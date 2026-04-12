"""Typed runtime projection helpers shared across coordinator surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openra_state.data.dataset import (
    demo_base_progression,
    demo_prompt_display_name_for,
    demo_queue_type_for,
)


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
    world_sync_stale_count: int = 0
    deploy_required_count: int = 0
    disabled_prerequisite_count: int = 0
    low_power_count: int = 0
    producer_disabled_count: int = 0
    queue_blocked_count: int = 0
    insufficient_funds_count: int = 0
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
            world_sync_stale_count=_to_int("world_sync_stale_count"),
            deploy_required_count=_to_int("deploy_required_count"),
            disabled_prerequisite_count=_to_int("disabled_prerequisite_count"),
            low_power_count=_to_int("low_power_count"),
            producer_disabled_count=_to_int("producer_disabled_count"),
            queue_blocked_count=_to_int("queue_blocked_count"),
            insufficient_funds_count=_to_int("insufficient_funds_count"),
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
            "world_sync_stale_count": self.world_sync_stale_count,
            "deploy_required_count": self.deploy_required_count,
            "disabled_prerequisite_count": self.disabled_prerequisite_count,
            "low_power_count": self.low_power_count,
            "producer_disabled_count": self.producer_disabled_count,
            "queue_blocked_count": self.queue_blocked_count,
            "insufficient_funds_count": self.insufficient_funds_count,
            "recent_directives": list(self.recent_directives),
        }


@dataclass(slots=True)
class RuntimeStateSnapshot:
    """Normalized runtime-state projection shared by coordinator surfaces."""

    active_tasks: dict[str, Any] = field(default_factory=dict)
    active_jobs: dict[str, Any] = field(default_factory=dict)
    resource_bindings: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    unfulfilled_requests: list[dict[str, Any]] = field(default_factory=list)
    capability_status: CapabilityStatusSnapshot = field(default_factory=CapabilityStatusSnapshot)
    unit_reservations: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Any) -> "RuntimeStateSnapshot":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            return cls()

        def _to_dict_map(key: str) -> dict[str, Any]:
            source = raw.get(key)
            if not isinstance(source, dict):
                return {}
            return {
                str(item_key): dict(item_value) if isinstance(item_value, dict) else item_value
                for item_key, item_value in source.items()
            }

        try:
            timestamp = float(raw.get("timestamp", 0.0) or 0.0)
        except Exception:
            timestamp = 0.0

        return cls(
            active_tasks=_to_dict_map("active_tasks"),
            active_jobs=_to_dict_map("active_jobs"),
            resource_bindings=_to_dict_map("resource_bindings"),
            constraints=[
                dict(item)
                for item in list(raw.get("constraints", []) or [])
                if isinstance(item, dict)
            ],
            unfulfilled_requests=[
                dict(item)
                for item in list(raw.get("unfulfilled_requests", []) or [])
                if isinstance(item, dict)
            ],
            capability_status=CapabilityStatusSnapshot.from_mapping(raw.get("capability_status")),
            unit_reservations=[
                dict(item)
                for item in list(raw.get("unit_reservations", []) or [])
                if isinstance(item, dict)
            ],
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_tasks": dict(self.active_tasks),
            "active_jobs": dict(self.active_jobs),
            "resource_bindings": dict(self.resource_bindings),
            "constraints": [dict(item) for item in self.constraints],
            "unfulfilled_requests": [dict(item) for item in self.unfulfilled_requests],
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
    unfulfilled_requests: list[dict[str, Any]],
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
        unfulfilled_requests=[dict(item) for item in unfulfilled_requests],
        capability_status=CapabilityStatusSnapshot.from_mapping(capability_status),
        unit_reservations=[dict(item) for item in unit_reservations],
        timestamp=float(timestamp or 0.0),
    )


def normalize_base_progression(base_state: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize base progression against issue-now truth for shared surfaces.

    `base_progression` is the shared coarse tech-progress hint emitted by
    `WorldModel.compute_runtime_facts()`. Some surfaces need to refine it with
    current issue-now truth (`buildable_now`, `buildable_blocked`) so they do
    not overclaim "can progress now" when the next step is currently blocked.
    """
    state = dict(base_state or {})
    truth_blocker = str(state.get("capability_truth_blocker") or "").strip()
    faction = str(state.get("faction") or "").strip()
    if truth_blocker == "faction_roster_unsupported":
        return {
            "status": f"能力层当前因阵营限制暂停（{faction or 'unknown'}）",
            "blocking_reason": truth_blocker,
            "faction": faction,
            "buildable_now": False,
        }

    progression = dict(state.get("base_progression") or {})
    buildable_now = {
        str(queue_type): [str(unit_type) for unit_type in list(units or []) if unit_type]
        for queue_type, units in dict(state.get("buildable_now") or {}).items()
    }
    if not progression:
        progression = demo_base_progression(
            has_construction_yard=bool(state.get("has_construction_yard")),
            mcv_count=int(state.get("mcv_count", 0) or 0),
            power_plant_count=int(state.get("power_plant_count", 0) or 0),
            refinery_count=int(state.get("refinery_count", 0) or 0),
            barracks_count=int(state.get("barracks_count", 0) or 0),
            war_factory_count=int(state.get("war_factory_count", 0) or 0),
            buildable=buildable_now or {
                str(queue_type): [str(unit_type) for unit_type in list(units or []) if unit_type]
                for queue_type, units in dict(state.get("buildable") or {}).items()
            },
        )

    next_unit_type = str(progression.get("next_unit_type") or "")
    if not next_unit_type:
        return progression

    next_queue_type = str(
        progression.get("next_queue_type") or demo_queue_type_for(next_unit_type) or "Building"
    )
    if next_unit_type in list(buildable_now.get(next_queue_type, []) or []):
        progression["buildable_now"] = True
        return progression

    blocked_items = [
        dict(item)
        for item in list((state.get("buildable_blocked") or {}).get(next_queue_type, []) or [])
        if isinstance(item, dict) and str(item.get("unit_type") or "") == next_unit_type
    ]
    if not blocked_items:
        progression["buildable_now"] = bool(progression.get("buildable_now", False))
        return progression

    blocked = blocked_items[0]
    display_name = demo_prompt_display_name_for(next_unit_type)
    reason = str(blocked.get("reason") or "").strip()
    reason_text = {
        "low_power": "低电",
        "queue_blocked": "队列阻塞",
        "disabled_prerequisite": "前置离线",
        "producer_disabled": "生产建筑离线",
        "insufficient_funds": "资金不足",
    }.get(reason, reason or "当前受阻")
    progression["buildable_now"] = False
    progression["blocking_reason"] = reason
    progression["status"] = f"当前受阻：{display_name}（{reason_text}）"
    return progression


@dataclass(slots=True)
class BattlefieldSnapshot:
    """Normalized battlefield read model for coordinator/query surfaces."""

    summary: str = ""
    disposition: str = "unknown"
    focus: str = "general"
    self_units: int = 0
    enemy_units: int = 0
    self_combat_value: float = 0.0
    enemy_combat_value: float = 0.0
    idle_self_units: int = 0
    self_combat_units: int = 0
    committed_combat_units: int = 0
    free_combat_units: int = 0
    low_power: bool = False
    queue_blocked: bool = False
    queue_blocked_reason: str = ""
    queue_blocked_queue_types: list[str] = field(default_factory=list)
    queue_blocked_items: list[dict[str, Any]] = field(default_factory=list)
    disabled_structure_count: int = 0
    powered_down_structure_count: int = 0
    low_power_disabled_structure_count: int = 0
    power_outage_structure_count: int = 0
    disabled_structures: list[str] = field(default_factory=list)
    recommended_posture: str = "maintain_posture"
    threat_level: str = "unknown"
    threat_direction: str = "unknown"
    base_under_attack: bool = False
    base_health_summary: str = ""
    has_production: bool = False
    explored_pct: float | None = None
    enemy_bases: int = 0
    enemy_spotted: int = 0
    frozen_enemy_count: int = 0
    pending_request_count: int = 0
    bootstrapping_request_count: int = 0
    reservation_count: int = 0
    unit_pipeline_preview: str = ""
    stale: bool = False
    capability_status: CapabilityStatusSnapshot = field(default_factory=CapabilityStatusSnapshot)

    @classmethod
    def from_mapping(cls, raw: Any) -> "BattlefieldSnapshot":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            return cls()

        def _to_int(key: str) -> int:
            try:
                return int(raw.get(key, 0) or 0)
            except Exception:
                return 0

        def _to_float(key: str) -> float | None:
            try:
                value = raw.get(key)
                if value is None or value == "":
                    return None
                return float(value)
            except Exception:
                return None

        return cls(
            summary=str(raw.get("summary") or ""),
            disposition=str(raw.get("disposition") or "unknown"),
            focus=str(raw.get("focus") or "general"),
            self_units=_to_int("self_units"),
            enemy_units=_to_int("enemy_units"),
            self_combat_value=round(_to_float("self_combat_value") or 0.0, 2),
            enemy_combat_value=round(_to_float("enemy_combat_value") or 0.0, 2),
            idle_self_units=_to_int("idle_self_units"),
            self_combat_units=_to_int("self_combat_units"),
            committed_combat_units=_to_int("committed_combat_units"),
            free_combat_units=_to_int("free_combat_units"),
            low_power=bool(raw.get("low_power")),
            queue_blocked=bool(raw.get("queue_blocked")),
            queue_blocked_reason=str(raw.get("queue_blocked_reason") or ""),
            queue_blocked_queue_types=[
                str(item)
                for item in list(raw.get("queue_blocked_queue_types", []) or [])
                if item is not None and str(item)
            ],
            queue_blocked_items=[
                dict(item)
                for item in list(raw.get("queue_blocked_items", []) or [])
                if isinstance(item, dict)
            ],
            disabled_structure_count=_to_int("disabled_structure_count"),
            powered_down_structure_count=_to_int("powered_down_structure_count"),
            low_power_disabled_structure_count=_to_int("low_power_disabled_structure_count"),
            power_outage_structure_count=_to_int("power_outage_structure_count"),
            disabled_structures=[
                str(item)
                for item in list(raw.get("disabled_structures", []) or [])
                if item is not None and str(item)
            ],
            recommended_posture=str(raw.get("recommended_posture") or "maintain_posture"),
            threat_level=str(raw.get("threat_level") or "unknown"),
            threat_direction=str(raw.get("threat_direction") or "unknown"),
            base_under_attack=bool(raw.get("base_under_attack")),
            base_health_summary=str(raw.get("base_health_summary") or ""),
            has_production=bool(raw.get("has_production")),
            explored_pct=_to_float("explored_pct"),
            enemy_bases=_to_int("enemy_bases"),
            enemy_spotted=_to_int("enemy_spotted"),
            frozen_enemy_count=_to_int("frozen_enemy_count"),
            pending_request_count=_to_int("pending_request_count"),
            bootstrapping_request_count=_to_int("bootstrapping_request_count"),
            reservation_count=_to_int("reservation_count"),
            unit_pipeline_preview=str(raw.get("unit_pipeline_preview") or ""),
            stale=bool(raw.get("stale")),
            capability_status=CapabilityStatusSnapshot.from_mapping(raw.get("capability_status")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "disposition": self.disposition,
            "focus": self.focus,
            "self_units": self.self_units,
            "enemy_units": self.enemy_units,
            "self_combat_value": self.self_combat_value,
            "enemy_combat_value": self.enemy_combat_value,
            "idle_self_units": self.idle_self_units,
            "self_combat_units": self.self_combat_units,
            "committed_combat_units": self.committed_combat_units,
            "free_combat_units": self.free_combat_units,
            "low_power": self.low_power,
            "queue_blocked": self.queue_blocked,
            "queue_blocked_reason": self.queue_blocked_reason,
            "queue_blocked_queue_types": list(self.queue_blocked_queue_types),
            "queue_blocked_items": [dict(item) for item in self.queue_blocked_items],
            "disabled_structure_count": self.disabled_structure_count,
            "powered_down_structure_count": self.powered_down_structure_count,
            "low_power_disabled_structure_count": self.low_power_disabled_structure_count,
            "power_outage_structure_count": self.power_outage_structure_count,
            "disabled_structures": list(self.disabled_structures),
            "recommended_posture": self.recommended_posture,
            "threat_level": self.threat_level,
            "threat_direction": self.threat_direction,
            "base_under_attack": self.base_under_attack,
            "base_health_summary": self.base_health_summary,
            "has_production": self.has_production,
            "explored_pct": self.explored_pct,
            "enemy_bases": self.enemy_bases,
            "enemy_spotted": self.enemy_spotted,
            "frozen_enemy_count": self.frozen_enemy_count,
            "pending_request_count": self.pending_request_count,
            "bootstrapping_request_count": self.bootstrapping_request_count,
            "reservation_count": self.reservation_count,
            "unit_pipeline_preview": self.unit_pipeline_preview,
            "stale": self.stale,
            "capability_status": self.capability_status.to_dict(),
        }


def build_battlefield_snapshot(
    *,
    summary: str,
    disposition: str,
    focus: str,
    self_units: int,
    enemy_units: int,
    self_combat_value: float,
    enemy_combat_value: float,
    idle_self_units: int,
    self_combat_units: int,
    committed_combat_units: int,
    free_combat_units: int,
    low_power: bool,
    queue_blocked: bool,
    queue_blocked_reason: str = "",
    queue_blocked_queue_types: list[str] | None = None,
    queue_blocked_items: list[dict[str, Any]] | None = None,
    disabled_structure_count: int = 0,
    powered_down_structure_count: int = 0,
    low_power_disabled_structure_count: int = 0,
    power_outage_structure_count: int = 0,
    disabled_structures: list[str] | None = None,
    recommended_posture: str,
    threat_level: str,
    threat_direction: str,
    base_under_attack: bool,
    base_health_summary: str,
    has_production: bool,
    explored_pct: float | None,
    enemy_bases: int,
    enemy_spotted: int,
    frozen_enemy_count: int,
    pending_request_count: int,
    bootstrapping_request_count: int,
    reservation_count: int,
    stale: bool,
    unit_pipeline_preview: str = "",
    capability_status: CapabilityStatusSnapshot | dict[str, Any],
) -> BattlefieldSnapshot:
    """Build the normalized battlefield snapshot used by world queries."""
    return BattlefieldSnapshot(
        summary=str(summary or ""),
        disposition=str(disposition or "unknown"),
        focus=str(focus or "general"),
        self_units=int(self_units or 0),
        enemy_units=int(enemy_units or 0),
        self_combat_value=round(float(self_combat_value or 0.0), 2),
        enemy_combat_value=round(float(enemy_combat_value or 0.0), 2),
        idle_self_units=int(idle_self_units or 0),
        self_combat_units=int(self_combat_units or 0),
        committed_combat_units=int(committed_combat_units or 0),
        free_combat_units=int(free_combat_units or 0),
        low_power=bool(low_power),
        queue_blocked=bool(queue_blocked),
        queue_blocked_reason=str(queue_blocked_reason or ""),
        queue_blocked_queue_types=[str(item) for item in list(queue_blocked_queue_types or []) if item],
        queue_blocked_items=[dict(item) for item in list(queue_blocked_items or []) if isinstance(item, dict)],
        disabled_structure_count=int(disabled_structure_count or 0),
        powered_down_structure_count=int(powered_down_structure_count or 0),
        low_power_disabled_structure_count=int(low_power_disabled_structure_count or 0),
        power_outage_structure_count=int(power_outage_structure_count or 0),
        disabled_structures=[str(item) for item in list(disabled_structures or []) if item],
        recommended_posture=str(recommended_posture or "maintain_posture"),
        threat_level=str(threat_level or "unknown"),
        threat_direction=str(threat_direction or "unknown"),
        base_under_attack=bool(base_under_attack),
        base_health_summary=str(base_health_summary or ""),
        has_production=bool(has_production),
        explored_pct=explored_pct,
        enemy_bases=int(enemy_bases or 0),
        enemy_spotted=int(enemy_spotted or 0),
        frozen_enemy_count=int(frozen_enemy_count or 0),
        pending_request_count=int(pending_request_count or 0),
        bootstrapping_request_count=int(bootstrapping_request_count or 0),
        reservation_count=int(reservation_count or 0),
        unit_pipeline_preview=str(unit_pipeline_preview or ""),
        stale=bool(stale),
        capability_status=CapabilityStatusSnapshot.from_mapping(capability_status),
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
    reservation_preview: str = ""
    reservation_status: str = ""
    remaining_count: int = 0
    assigned_count: int = 0
    produced_count: int = 0
    start_released: bool = False
    bootstrap_job_id: str = ""
    world_stale: bool = False
    world_sync_error: str = ""
    world_sync_failures: int = 0
    world_sync_failure_threshold: int = 0
    active_group_size: int = 0

    @classmethod
    def from_mapping(cls, raw: Any) -> "TaskTriageSnapshot":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            return cls()

        def _to_int(key: str) -> int:
            try:
                return int(raw.get(key, 0) or 0)
            except Exception:
                return 0

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
            reservation_preview=str(raw.get("reservation_preview") or ""),
            reservation_status=str(raw.get("reservation_status") or ""),
            remaining_count=_to_int("remaining_count"),
            assigned_count=_to_int("assigned_count"),
            produced_count=_to_int("produced_count"),
            start_released=bool(raw.get("start_released", False)),
            bootstrap_job_id=str(raw.get("bootstrap_job_id") or ""),
            world_stale=bool(raw.get("world_stale", False)),
            world_sync_error=str(raw.get("world_sync_error") or ""),
            world_sync_failures=_to_int("world_sync_failures"),
            world_sync_failure_threshold=_to_int("world_sync_failure_threshold"),
            active_group_size=_to_int("active_group_size"),
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
            "reservation_preview": self.reservation_preview,
            "reservation_status": self.reservation_status,
            "remaining_count": self.remaining_count,
            "assigned_count": self.assigned_count,
            "produced_count": self.produced_count,
            "start_released": self.start_released,
            "bootstrap_job_id": self.bootstrap_job_id,
            "world_stale": self.world_stale,
            "world_sync_error": self.world_sync_error,
            "world_sync_failures": self.world_sync_failures,
            "world_sync_failure_threshold": self.world_sync_failure_threshold,
            "active_group_size": self.active_group_size,
        }


@dataclass(slots=True)
class TaskTriageInputs:
    """Shared side inputs used to synthesize task triage."""

    world_sync: dict[str, Any] = field(default_factory=dict)
    runtime_facts: dict[str, Any] = field(default_factory=dict)
    pending_question: dict[str, Any] | None = None
    latest_warning: str = ""
    latest_info: str = ""
    primary_summary: str = ""
    unit_mix: list[str] = field(default_factory=list)
