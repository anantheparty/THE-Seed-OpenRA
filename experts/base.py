"""Expert and Job base classes — three Expert types + Job lifecycle.

Expert types (design.md §4, not mergeable):
  - InformationExpert: read-only analysis, no resources, no actions
  - PlannerExpert: proposals/suggestions, no resources, no execution
  - ExecutionExpert: binds resources, ticks Jobs, produces ExpertSignals
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from benchmark import span as bm_span
from models import (
    Constraint,
    ExpertConfig,
    ExpertSignal,
    Job as JobModel,
    JobStatus,
    SignalKind,
)

logger = logging.getLogger(__name__)

# Callback type: Job sends Signal to Task Agent (via Kernel routing)
SignalCallback = Callable[[ExpertSignal], None]

# Callback type: read active constraints matching a scope
ConstraintProvider = Callable[[str], list[Constraint]]


# ---------------------------------------------------------------------------
# Information Expert — read-only analysis
# ---------------------------------------------------------------------------


class InformationExpert(ABC):
    """Read-only expert that analyzes WorldModel and produces derived info.

    No resource binding, no actions. Passively called by consumers
    (WorldModel, Jobs, Task Agents).
    """

    @abstractmethod
    def analyze(self, world_state: dict[str, Any]) -> dict[str, Any]:
        """Analyze world state and return derived information.

        Args:
            world_state: Current game state snapshot (from WorldModel).

        Returns:
            Dict of derived analysis (threat levels, economy trends, etc.)
        """


# ---------------------------------------------------------------------------
# Planner Expert — proposals and suggestions
# ---------------------------------------------------------------------------


class PlannerExpert(ABC):
    """Expert that produces tactical suggestions/proposals.

    No resource binding, no direct execution. Uses traditional AI
    (scoring, search, rules) — not LLM.
    """

    @abstractmethod
    def plan(
        self,
        query_type: str,
        params: dict[str, Any],
        world_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a proposal/suggestion.

        Args:
            query_type: Type of planning query.
            params: Query-specific parameters.
            world_state: Current game state snapshot.

        Returns:
            Proposal dict (routes, priorities, options, etc.)
        """


# ---------------------------------------------------------------------------
# Job — runtime instance of an Execution Expert
# ---------------------------------------------------------------------------


class BaseJob(ABC):
    """Base class for all Jobs (Execution Expert runtime instances).

    A Job autonomously ticks, calls GameAPI, and sends Signals to its
    Task Agent. It does not wait for the LLM between ticks.
    """

    # Subclasses MUST override this (seconds between ticks)
    tick_interval: float = 1.0

    def __init__(
        self,
        job_id: str,
        task_id: str,
        config: ExpertConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self.config = config
        self._signal_callback = signal_callback
        self._constraint_provider = constraint_provider

        self.resources: list[str] = []
        self.status: JobStatus = JobStatus.RUNNING
        self._paused = False
        self._created_at = time.time()

    # --- Lifecycle (called by Kernel / Execution Expert) ---

    def do_tick(self) -> None:
        """Execute one tick with benchmark instrumentation. Called by GameLoop."""
        if self._paused or self.status != JobStatus.RUNNING:
            return

        with bm_span("job_tick", name=f"{self.expert_type}:{self.job_id}"):
            self.tick()

    @abstractmethod
    def tick(self) -> None:
        """One tick of autonomous execution. Subclasses implement game logic here."""

    def patch(self, params: dict[str, Any]) -> None:
        """Update Job parameters mid-execution (called by Task Agent via Kernel)."""
        for key, value in params.items():
            if hasattr(self.config, key):
                object.__setattr__(self.config, key, value)
            else:
                logger.warning("patch: unknown param %r for %s", key, self.expert_type)

    def pause(self) -> None:
        """Pause this Job. Ticks will be skipped until resume."""
        self._paused = True
        logger.debug("Job paused: %s", self.job_id)

    def resume(self) -> None:
        """Resume a paused Job."""
        self._paused = False
        logger.debug("Job resumed: %s", self.job_id)

    def abort(self) -> None:
        """Terminate this Job immediately."""
        self.status = JobStatus.ABORTED
        logger.info("Job aborted: %s", self.job_id)
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"Job {self.job_id} aborted",
            result="aborted",
        )

    # --- Resource callbacks (called by Kernel) ---

    def on_resource_granted(self, actor_ids: list[str]) -> None:
        """Kernel assigned new resources to this Job."""
        self.resources.extend(actor_ids)
        if self.status == JobStatus.WAITING:
            self.status = JobStatus.RUNNING
        logger.debug("Resources granted to %s: %s", self.job_id, actor_ids)

    def on_resource_revoked(self, actor_ids: list[str]) -> None:
        """Kernel revoked resources from this Job (preemption)."""
        for aid in actor_ids:
            if aid in self.resources:
                self.resources.remove(aid)
        if not self.resources:
            self.status = JobStatus.WAITING
        logger.debug("Resources revoked from %s: %s (remaining: %s)", self.job_id, actor_ids, self.resources)

    # --- Signal emission ---

    def emit_signal(
        self,
        kind: SignalKind,
        summary: str,
        world_delta: Optional[dict[str, Any]] = None,
        expert_state: Optional[dict[str, Any]] = None,
        result: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        decision: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send an ExpertSignal to the Task Agent (via callback)."""
        signal = ExpertSignal(
            task_id=self.task_id,
            job_id=self.job_id,
            kind=kind,
            summary=summary,
            world_delta=world_delta or {},
            expert_state=expert_state or {},
            result=result,
            data=data,
            decision=decision,
        )
        self._signal_callback(signal)

    # --- Constraint reading ---

    def get_active_constraints(self) -> list[Constraint]:
        """Read constraints matching this Job's scope."""
        if self._constraint_provider is None:
            return []
        # Match: global, expert_type:<this_type>, or task_id:<this_task>
        constraints = []
        for scope in ["global", f"expert_type:{self.expert_type}", f"task_id:{self.task_id}"]:
            constraints.extend(self._constraint_provider(scope))
        return constraints

    # --- Properties ---

    @property
    @abstractmethod
    def expert_type(self) -> str:
        """Return the expert type string (e.g. 'ReconExpert')."""

    @property
    def is_paused(self) -> bool:
        return self._paused

    def to_model(self) -> JobModel:
        """Convert to the data model Job for context packets."""
        return JobModel(
            job_id=self.job_id,
            task_id=self.task_id,
            expert_type=self.expert_type,
            config=self.config,
            resources=list(self.resources),
            status=self.status,
        )


# ---------------------------------------------------------------------------
# Execution Expert — creates and manages Jobs
# ---------------------------------------------------------------------------


class ExecutionExpert(ABC):
    """Expert that binds resources, creates Jobs, and ticks them.

    Each Execution Expert type defines one Job subclass. The Expert is
    a factory + lifecycle manager; the Job does the actual work.
    """

    @abstractmethod
    def create_job(
        self,
        task_id: str,
        config: ExpertConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> BaseJob:
        """Create a new Job instance for this Expert type.

        Args:
            task_id: Parent Task ID.
            config: Expert-specific configuration.
            signal_callback: Callback to send Signals to Task Agent.
            constraint_provider: Callback to read active constraints.

        Returns:
            A new BaseJob subclass instance, ready to tick.
        """

    @property
    @abstractmethod
    def expert_type(self) -> str:
        """Return the expert type string (e.g. 'ReconExpert')."""

    @staticmethod
    def generate_job_id() -> str:
        """Generate a unique Job ID."""
        return f"j_{uuid.uuid4().hex[:8]}"
