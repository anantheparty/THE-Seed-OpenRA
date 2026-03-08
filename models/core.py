"""Core data models — Task, Job, ResourceNeed, Constraint, ExpertSignal, Event, NormalizedActor, TaskMessage, PlayerResponse."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .configs import ExpertConfig
from .enums import (
    ActorCategory,
    ActorOwner,
    ConstraintEnforcement,
    EventType,
    JobStatus,
    Mobility,
    ResourceKind,
    SignalKind,
    TaskKind,
    TaskMessageType,
    TaskStatus,
)


def _gen_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _now() -> float:
    return time.time()


# --- Task & Job ---


@dataclass
class Task:
    task_id: str
    raw_text: str
    kind: TaskKind
    priority: int  # 0-100
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=_now)
    timestamp: float = field(default_factory=_now)


@dataclass
class Job:
    job_id: str
    task_id: str
    expert_type: str  # ReconExpert, CombatExpert, etc.
    config: ExpertConfig
    resources: list[str] = field(default_factory=list)
    status: JobStatus = JobStatus.RUNNING
    timestamp: float = field(default_factory=_now)


# --- Resource ---


@dataclass
class ResourceNeed:
    job_id: str
    kind: ResourceKind  # actor / production_queue
    count: int = 1
    predicates: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=_now)


# --- Constraint ---


@dataclass
class Constraint:
    constraint_id: str
    kind: str  # do_not_chase / economy_first / defend_base
    scope: str  # global / expert_type:CombatExpert / task_id:xxx
    params: dict[str, Any] = field(default_factory=dict)
    enforcement: ConstraintEnforcement = ConstraintEnforcement.CLAMP
    active: bool = True
    timestamp: float = field(default_factory=_now)


# --- Signals & Events ---


@dataclass
class ExpertSignal:
    task_id: str
    job_id: str
    kind: SignalKind
    summary: str
    world_delta: dict[str, Any] = field(default_factory=dict)
    expert_state: dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None  # task_complete: succeeded / failed / aborted
    data: Optional[dict[str, Any]] = None
    decision: Optional[dict[str, Any]] = None  # decision_request: options + default_if_timeout
    timestamp: float = field(default_factory=_now)


@dataclass
class Event:
    type: EventType
    actor_id: Optional[int] = None
    position: Optional[tuple[int, int]] = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=_now)


# --- WorldModel Actor ---


@dataclass
class NormalizedActor:
    actor_id: int
    name: str  # e.g. "2tnk"
    display_name: str  # e.g. "重型坦克"
    owner: ActorOwner
    category: ActorCategory
    position: tuple[int, int]
    hp: int
    hp_max: int
    is_alive: bool = True
    is_idle: bool = True
    mobility: Mobility = Mobility.MEDIUM
    combat_value: float = 0.0
    can_attack: bool = False
    can_harvest: bool = False
    weapon_range: int = 0
    timestamp: float = field(default_factory=_now)


# --- Player Interaction (Adjutant) ---


@dataclass
class TaskMessage:
    message_id: str
    task_id: str
    type: TaskMessageType
    content: str
    options: Optional[list[str]] = None  # task_question
    timeout_s: Optional[float] = None  # task_question
    default_option: Optional[str] = None  # task_question timeout default
    priority: int = 50
    timestamp: float = field(default_factory=_now)


@dataclass
class PlayerResponse:
    message_id: str  # reply to TaskMessage.message_id
    task_id: str
    answer: str
    timestamp: float = field(default_factory=_now)
