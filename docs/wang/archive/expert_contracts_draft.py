"""Draft expert contracts for the roadmap redesign.

This file is intentionally documentation-grade only.
It is not wired into runtime code.

Design intent:
- extract the common shape behind CombatAgent-style execution experts
- also leave room for EconomyEngine-style policy/execution experts
- keep InformationExpert read-only against a shared WorldModel
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class ExpertPhase(str, Enum):
    """High-level lifecycle state for a running expert instance."""

    IDLE = "idle"
    BINDING = "binding"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RELEASED = "released"


@dataclass(frozen=True)
class TaskSpec:
    """Kernel-normalized task description passed into experts."""

    job_id: str
    kind: str
    target: Optional[Dict[str, Any]] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceClaim:
    """A request to bind runtime resources such as actors, squads, or queues."""

    kind: str
    resource_ids: Sequence[str]
    exclusive: bool = True
    note: str = ""


@dataclass(frozen=True)
class ActionProposal:
    """An executable recommendation produced by an expert tick."""

    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    resource_ids: Sequence[str] = field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class ExpertStatus:
    """Small status object suitable for kernel polling and telemetry."""

    phase: ExpertPhase
    summary: str = ""
    progress: Optional[float] = None
    blocking_reason: Optional[str] = None
    error: Optional[str] = None
    outputs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InfoReport:
    """Read-only output from an InformationExpert."""

    topic: str
    estimates: Dict[str, Any] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    alerts: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)


class WorldModel(ABC):
    """Minimal facade exposed to experts.

    The real implementation may internally compose current IntelService,
    current IntelligenceService, job state, and resource bindings.
    """

    @abstractmethod
    def now(self) -> float:
        """Return current model time."""

    @abstractmethod
    def snapshot(self) -> Dict[str, Any]:
        """Return raw or near-raw game snapshot data."""

    @abstractmethod
    def intel(self) -> Dict[str, Any]:
        """Return derived/shared intel suitable for cross-domain reasoning."""

    @abstractmethod
    def get_resource_bindings(self) -> Dict[str, Any]:
        """Return current runtime ownership such as actor->job or queue->job."""

    @abstractmethod
    def get_task_record(self, job_id: str) -> Dict[str, Any]:
        """Return current task/job record visible to experts."""


class BaseExpert(ABC):
    """Common metadata contract for all experts."""

    @property
    @abstractmethod
    def expert_id(self) -> str:
        """Stable expert identifier."""

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain label such as combat, economy, recon, defense."""

    @abstractmethod
    def supports(self, task: TaskSpec) -> bool:
        """Return whether this expert can handle the given task kind."""


class ExecutionExpert(BaseExpert, ABC):
    """Runtime execution expert.

    This is the shared shape behind:
    - CombatAgent company order + company_states
    - future Job-backed executors such as ExploreExecutor or AttackExecutor
    - a wrapped economy executor if macro production becomes a managed job
    """

    @abstractmethod
    def bind(self, task: TaskSpec, world: WorldModel) -> Sequence[ResourceClaim]:
        """Declare which resources this expert wants to own for the task.

        Kernel is expected to arbitrate these claims before execution starts.
        """

    @abstractmethod
    def start(self, task: TaskSpec, world: WorldModel) -> None:
        """Initialize runtime state after resource claims are granted."""

    @abstractmethod
    def tick(self, world: WorldModel) -> Sequence[ActionProposal]:
        """Advance execution once and return proposed executable actions."""

    @abstractmethod
    def status(self) -> ExpertStatus:
        """Report current phase, summary, progress, and any error/blocking state."""

    @abstractmethod
    def bound_resources(self) -> Sequence[str]:
        """Return currently owned resource identifiers."""

    @abstractmethod
    def release(self, world: WorldModel, reason: str = "") -> None:
        """Release owned resources and finalize local runtime state."""


class InformationExpert(BaseExpert, ABC):
    """Read-only expert that enriches the shared world model."""

    @abstractmethod
    def evaluate(self, world: WorldModel) -> InfoReport:
        """Read world state and emit estimates, scores, alerts, or hypotheses."""

    @abstractmethod
    def report_topic(self) -> str:
        """Return the namespace/topic under which outputs should be stored."""


class PlannerExpert(BaseExpert, ABC):
    """Optional draft for completeness.

    Not requested directly, but useful because EconomyEngine sits between
    information and execution in several cases.
    """

    @abstractmethod
    def propose(self, task: TaskSpec, world: WorldModel) -> Sequence[ActionProposal]:
        """Return candidate plans or actions without directly owning resources."""


"""
Mapping notes
-------------

CombatAgent today:
- bind(...) ~= company/squad ownership determined outside agent by SquadManager
- start(...) ~= set_company_order + company_states initialization
- tick(...) ~= _execute_company_cycle returning attack/move proposals
- status(...) ~= company_states[company_id]
- release(...) ~= clearing state / stopping company control

EconomyEngine today:
- supports(...) = macro/economy task kinds
- tick(...) can be modeled as reading world economic state and returning ActionProposal list
- status(...) is currently implicit; would need a small wrapper state object
- bind(...) may claim production queues instead of actors

The key unification is:
- execution experts consume TaskSpec
- execution experts expose explicit status instead of private dicts or bare action lists
- resource binding is first-class whether the resource is actors, squads, or queues
"""

