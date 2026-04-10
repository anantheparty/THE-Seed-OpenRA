"""Typed runtime projection helpers shared across coordinator surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CapabilityStatusSnapshot:
    """Normalized read model for capability task runtime status."""

    task_id: str = ""
    task_label: str = ""
    status: str = ""
    phase: str = ""
    blocker: str = ""
    active_job_count: int = 0
    active_job_types: list[str] = field(default_factory=list)
    pending_request_count: int = 0
    blocking_request_count: int = 0
    dispatch_request_count: int = 0
    bootstrapping_request_count: int = 0
    start_released_request_count: int = 0
    reinforcement_request_count: int = 0
    inference_pending_count: int = 0
    prerequisite_gap_count: int = 0
    recent_directives: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: Any) -> "CapabilityStatusSnapshot":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            return cls()

        def _to_int(key: str) -> int:
            try:
                return int(raw.get(key, 0) or 0)
            except Exception:
                return 0

        active_job_types = [
            str(item)
            for item in list(raw.get("active_job_types", []) or [])
            if item is not None and str(item)
        ]
        recent_directives = [
            str(item)
            for item in list(raw.get("recent_directives", []) or [])
            if item is not None and str(item).strip()
        ]
        return cls(
            task_id=str(raw.get("task_id") or raw.get("taskId") or ""),
            task_label=str(raw.get("label") or raw.get("task_label") or ""),
            status=str(raw.get("status") or ""),
            phase=str(raw.get("phase") or ""),
            blocker=str(raw.get("blocker") or ""),
            active_job_count=_to_int("active_job_count"),
            active_job_types=active_job_types,
            pending_request_count=_to_int("pending_request_count"),
            blocking_request_count=_to_int("blocking_request_count"),
            dispatch_request_count=_to_int("dispatch_request_count"),
            bootstrapping_request_count=_to_int("bootstrapping_request_count"),
            start_released_request_count=_to_int("start_released_request_count"),
            reinforcement_request_count=_to_int("reinforcement_request_count"),
            inference_pending_count=_to_int("inference_pending_count"),
            prerequisite_gap_count=_to_int("prerequisite_gap_count"),
            recent_directives=recent_directives,
        )

    def matches_task(self, task_id: str, task_label: str, *, is_capability: bool) -> bool:
        if not is_capability:
            return False
        if not self.task_id:
            return True
        if self.task_id == str(task_id or ""):
            return True
        return bool(task_label and self.task_label and self.task_label == task_label)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_label": self.task_label,
            "label": self.task_label,
            "status": self.status,
            "phase": self.phase,
            "blocker": self.blocker,
            "active_job_count": self.active_job_count,
            "active_job_types": list(self.active_job_types),
            "pending_request_count": self.pending_request_count,
            "blocking_request_count": self.blocking_request_count,
            "dispatch_request_count": self.dispatch_request_count,
            "bootstrapping_request_count": self.bootstrapping_request_count,
            "start_released_request_count": self.start_released_request_count,
            "reinforcement_request_count": self.reinforcement_request_count,
            "inference_pending_count": self.inference_pending_count,
            "prerequisite_gap_count": self.prerequisite_gap_count,
            "recent_directives": list(self.recent_directives),
        }


@dataclass(slots=True)
class RuntimeStateSnapshot:
    """Normalized runtime-state projection shared by coordinator surfaces."""

    active_tasks: dict[str, Any] = field(default_factory=dict)
    active_jobs: dict[str, Any] = field(default_factory=dict)
    resource_bindings: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    capability_status: CapabilityStatusSnapshot = field(default_factory=CapabilityStatusSnapshot)
    unit_reservations: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_tasks": dict(self.active_tasks),
            "active_jobs": dict(self.active_jobs),
            "resource_bindings": dict(self.resource_bindings),
            "constraints": [dict(item) for item in self.constraints],
            "capability_status": self.capability_status.to_dict(),
            "unit_reservations": [dict(item) for item in self.unit_reservations],
            "timestamp": self.timestamp,
        }


def build_runtime_state_snapshot(
    *,
    active_tasks: dict[str, Any],
    active_jobs: dict[str, Any],
    resource_bindings: dict[str, Any],
    constraints: list[dict[str, Any]],
    capability_status: CapabilityStatusSnapshot | dict[str, Any],
    unit_reservations: list[dict[str, Any]],
    timestamp: float,
) -> RuntimeStateSnapshot:
    """Build a normalized runtime-state snapshot for exports and queries."""
    return RuntimeStateSnapshot(
        active_tasks=dict(active_tasks),
        active_jobs=dict(active_jobs),
        resource_bindings=dict(resource_bindings),
        constraints=[dict(item) for item in constraints],
        capability_status=CapabilityStatusSnapshot.from_mapping(capability_status),
        unit_reservations=[dict(item) for item in unit_reservations],
        timestamp=float(timestamp or 0.0),
    )


@dataclass(slots=True)
class TaskTriageSnapshot:
    """Normalized read model for task runtime triage."""

    state: str = ""
    phase: str = ""
    status_line: str = ""
    waiting_reason: str = ""
    blocking_reason: str = ""
    active_expert: str = ""
    active_job_id: str = ""
    reservation_ids: list[str] = field(default_factory=list)
    world_stale: bool = False
    active_group_size: int = 0

    @classmethod
    def from_mapping(cls, raw: Any) -> "TaskTriageSnapshot":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            return cls()
        return cls(
            state=str(raw.get("state") or ""),
            phase=str(raw.get("phase") or ""),
            status_line=str(raw.get("status_line") or ""),
            waiting_reason=str(raw.get("waiting_reason") or ""),
            blocking_reason=str(raw.get("blocking_reason") or ""),
            active_expert=str(raw.get("active_expert") or ""),
            active_job_id=str(raw.get("active_job_id") or ""),
            reservation_ids=[
                str(item)
                for item in list(raw.get("reservation_ids", []) or [])
                if item is not None and str(item)
            ],
            world_stale=bool(raw.get("world_stale", False)),
            active_group_size=int(raw.get("active_group_size", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "phase": self.phase,
            "status_line": self.status_line,
            "waiting_reason": self.waiting_reason,
            "blocking_reason": self.blocking_reason,
            "active_expert": self.active_expert,
            "active_job_id": self.active_job_id,
            "reservation_ids": list(self.reservation_ids),
            "world_stale": self.world_stale,
            "active_group_size": self.active_group_size,
        }


@dataclass(slots=True)
class TaskTriageInputs:
    """Shared side inputs used to synthesize task triage."""

    world_sync: dict[str, Any] = field(default_factory=dict)
    pending_question: dict[str, Any] | None = None
    latest_warning: str = ""
    primary_summary: str = ""
    unit_mix: list[str] = field(default_factory=list)
