"""Context packet construction for Task Agent LLM calls.

The context packet is injected as the first user message each wake cycle,
giving the LLM a complete picture of the current task state.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from models import ExpertSignal, Event, Job, Task
from openra_state.data.dataset import (
    demo_capability_buildable_lines,
    demo_capability_queue_types,
    demo_prompt_display_name_for,
    demo_capability_unit_type_for,
    demo_queue_type_for,
    filter_demo_capability_buildable,
    filter_demo_capability_production_queues,
    filter_demo_capability_ready_items,
    filter_demo_capability_reservations,
)
from runtime_views import CapabilityStatusSnapshot
from runtime_views import normalize_base_progression
from .workflows import classify_managed_workflow, workflow_block, workflow_phase

# Chinese labels for Job status values — makes completion judgment clearer for LLM.
_JOB_STATUS_ZH: dict[str, str] = {
    "succeeded": "已成功完成",
    "failed":    "已失败",
    "aborted":   "已中止（未完成目标）",
    "waiting":   "等待中（尚未生效）",
    "running":   "运行中",
}

# Maps subscription key → frozenset of info_experts dict keys produced by that expert.
_SUBSCRIPTION_KEYS: dict[str, frozenset] = {
    "threat": frozenset({
        "threat_level", "threat_direction", "enemy_count",
        "enemy_composition_summary", "base_under_attack",
    }),
    "base_state": frozenset({
        "base_established", "base_health_summary", "has_production",
    }),
    "production": frozenset(),  # placeholder — no production InfoExpert yet
}

_ORDINARY_RUNTIME_FACTS_HIDDEN_KEYS = {
    "buildable",
    "buildable_now",
    "buildable_blocked",
    "feasibility",
    "production_queues",
    "ready_queue_items",
    "unfulfilled_requests",
    "unit_reservations",
    "capability_status",
}
_ORDINARY_RUNTIME_FACTS_HIDDEN_PREFIXES = ("can_afford_",)


@dataclass
class WorldSummary:
    """Snapshot of relevant world state for the Task Agent."""

    economy: dict[str, Any] = field(default_factory=dict)
    military: dict[str, Any] = field(default_factory=dict)
    map: dict[str, Any] = field(default_factory=dict)
    known_enemy: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContextPacket:
    """All information the Task Agent needs for one wake cycle."""

    task: dict[str, Any]
    jobs: list[dict[str, Any]]
    world_summary: dict[str, Any]
    recent_signals: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    open_decisions: list[dict[str, Any]]
    runtime_facts: dict[str, Any] = field(default_factory=dict)
    other_active_tasks: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


def build_context_packet(
    task: Task,
    jobs: list[Job],
    world_summary: Optional[WorldSummary] = None,
    recent_signals: Optional[list[ExpertSignal]] = None,
    recent_events: Optional[list[Event]] = None,
    open_decisions: Optional[list[ExpertSignal]] = None,
    runtime_facts: Optional[dict[str, Any]] = None,
    other_active_tasks: Optional[list[dict[str, Any]]] = None,
    bootstrap_job_id: Optional[str] = None,
) -> ContextPacket:
    """Build a context packet from current state.

    Args:
        task: The Task this agent is managing.
        jobs: Active Jobs belonging to this Task.
        world_summary: Current world state snapshot.
        recent_signals: Signals received since last wake (or all for initial).
        recent_events: WorldModel Events routed to this agent since last wake.
        open_decisions: Pending decision_request signals awaiting response.
    """
    task_dict = {
        "task_id": task.task_id,
        "raw_text": task.raw_text,
        "kind": task.kind.value,
        "priority": task.priority,
        "status": task.status.value,
        "created_at": task.created_at,
        "timestamp": task.timestamp,
    }
    ordinary_workflow = None
    if not getattr(task, "is_capability", False):
        ordinary_workflow = classify_managed_workflow(task.raw_text)
        if ordinary_workflow:
            task_dict["workflow_template"] = ordinary_workflow

    jobs_list = []
    for job in jobs:
        status_val = job.status.value
        job_dict: dict[str, Any] = {
            "job_id": job.job_id,
            "expert_type": job.expert_type,
            "status": status_val,
            "status_zh": _JOB_STATUS_ZH.get(status_val, status_val),
            "resources": job.resources,
            "timestamp": job.timestamp,
        }
        # Include config as dict for LLM readability
        if hasattr(job.config, "__dataclass_fields__"):
            job_dict["config"] = asdict(job.config)
        else:
            job_dict["config"] = str(job.config)
        # Mark bootstrap-created jobs so LLM knows not to create duplicates.
        if bootstrap_job_id and job.job_id == bootstrap_job_id:
            job_dict["source"] = "bootstrap"
        jobs_list.append(job_dict)

    if ordinary_workflow:
        ordinary_workflow_phase = workflow_phase(
            ordinary_workflow,
            runtime_facts=runtime_facts or {},
            active_actor_ids=list((runtime_facts or {}).get("active_actor_ids") or []),
            jobs=jobs_list,
        )
        if ordinary_workflow_phase:
            task_dict["workflow_phase"] = ordinary_workflow_phase

    ws = world_summary or WorldSummary()
    ws_dict = {
        "economy": ws.economy,
        "military": ws.military,
        "map": ws.map,
        "known_enemy": ws.known_enemy,
        "timestamp": ws.timestamp,
    }

    signals_list = []
    for sig in (recent_signals or []):
        sig_dict: dict[str, Any] = {
            "task_id": sig.task_id,
            "job_id": sig.job_id,
            "kind": sig.kind.value,
            "summary": sig.summary,
            "timestamp": sig.timestamp,
        }
        if sig.world_delta:
            sig_dict["world_delta"] = sig.world_delta
        if sig.expert_state:
            sig_dict["expert_state"] = sig.expert_state
        if sig.result:
            sig_dict["result"] = sig.result
        if sig.data:
            sig_dict["data"] = sig.data
        signals_list.append(sig_dict)

    decisions_list = []
    for dec in (open_decisions or []):
        dec_dict: dict[str, Any] = {
            "task_id": dec.task_id,
            "job_id": dec.job_id,
            "kind": dec.kind.value,
            "summary": dec.summary,
            "timestamp": dec.timestamp,
        }
        if dec.decision:
            dec_dict["decision"] = dec.decision
        decisions_list.append(dec_dict)

    events_list = []
    for evt in (recent_events or []):
        evt_dict: dict[str, Any] = {
            "type": evt.type.value if hasattr(evt.type, "value") else str(evt.type),
            "timestamp": evt.timestamp,
        }
        if evt.actor_id is not None:
            evt_dict["actor_id"] = evt.actor_id
        if evt.position is not None:
            evt_dict["position"] = list(evt.position)
        if evt.data:
            evt_dict["data"] = evt.data
        events_list.append(evt_dict)

    # Filter info_experts in runtime_facts based on task.info_subscriptions.
    # Only keys belonging to subscribed experts are included; unsubscribed data is dropped.
    final_runtime_facts = dict(runtime_facts or {})
    subscriptions = getattr(task, "info_subscriptions", None)
    if subscriptions is not None and "info_experts" in final_runtime_facts:
        all_ie: dict = final_runtime_facts["info_experts"]
        filtered_ie: dict = {}
        for sub in subscriptions:
            for k in _SUBSCRIPTION_KEYS.get(sub, frozenset()):
                if k in all_ie:
                    filtered_ie[k] = all_ie[k]
        final_runtime_facts["info_experts"] = filtered_ie

    return ContextPacket(
        task=task_dict,
        jobs=jobs_list,
        world_summary=ws_dict,
        recent_signals=signals_list,
        recent_events=events_list,
        open_decisions=decisions_list,
        runtime_facts=final_runtime_facts,
        other_active_tasks=list(other_active_tasks) if other_active_tasks else [],
    )


def _compact_economy(eco: dict[str, Any]) -> str:
    """One-line economy summary."""
    cash = eco.get("cash", 0)
    res = eco.get("resources", 0)
    pwr = eco.get("power_provided", 0)
    drain = eco.get("power_drained", 0)
    low = " ⚡低电力" if eco.get("low_power") else ""
    disabled_count = int(eco.get("disabled_structure_count", 0) or 0)
    disabled = f" 离线建筑{disabled_count}" if disabled_count else ""
    return f"资金{cash} 资源{res} 电力{pwr}/{drain}{low}{disabled}"


def _compact_military(mil: dict[str, Any]) -> str:
    """One-line military summary."""
    su = mil.get("self_units", 0)
    eu = mil.get("enemy_units", 0)
    idle = mil.get("idle_self_units", 0)
    return f"我军{su}(闲置{idle}) 敌军{eu}"


def _compact_map(m: dict[str, Any]) -> str:
    """One-line map summary — never include is_explored grid."""
    ep = m.get("explored_pct", 0)
    return f"探索{ep:.1%}"


def _compact_runtime_facts(rf: dict[str, Any]) -> str:
    """Compact runtime facts as key=value pairs."""
    if not rf:
        return ""
    # Faction
    parts: list[str] = []
    if rf.get("faction"):
        parts.append(f"阵营={rf['faction']}")
    # Core building counts
    for key in ("has_construction_yard", "power_plant_count", "barracks_count",
                "refinery_count", "war_factory_count", "radar_count",
                "tech_center_count", "repair_facility_count", "airfield_count",
                "tech_level", "mcv_count", "mcv_idle", "harvester_count",
                "active_group_size"):
        if key in rf:
            parts.append(f"{key}={rf[key]}")
    if rf.get("active_actor_ids"):
        parts.append(f"active_actor_ids={rf['active_actor_ids']}")
    if "disabled_structure_count" in rf:
        parts.append(f"disabled_structure_count={rf['disabled_structure_count']}")
    if "powered_down_structure_count" in rf:
        parts.append(f"powered_down_structure_count={rf['powered_down_structure_count']}")
    if "low_power_disabled_structure_count" in rf:
        parts.append(f"low_power_disabled_structure_count={rf['low_power_disabled_structure_count']}")
    if "power_outage_structure_count" in rf:
        parts.append(f"power_outage_structure_count={rf['power_outage_structure_count']}")
    if rf.get("disabled_structures"):
        parts.append(f"disabled_structures={rf['disabled_structures']}")
    # Affordability
    afford = [k.replace("can_afford_", "") for k in rf if k.startswith("can_afford_") and rf[k]]
    if afford:
        parts.append(f"can_afford=[{','.join(afford)}]")
    # Feasibility
    feas = rf.get("feasibility", {})
    if feas:
        ok_tools = [k for k, v in feas.items() if v]
        no_tools = [k for k, v in feas.items() if not v]
        if ok_tools:
            parts.append(f"可行=[{','.join(ok_tools)}]")
        if no_tools:
            parts.append(f"不可行=[{','.join(no_tools)}]")
    # Buildable units per queue
    buildable = rf.get("buildable", {})
    if buildable:
        for queue_type in demo_capability_queue_types():
            units = buildable.get(queue_type)
            if units:
                parts.append(f"可造{queue_type}=[{','.join(units)}]")
    # Info experts (compact)
    ie = rf.get("info_experts", {})
    if ie:
        ie_compact = json.dumps(ie, ensure_ascii=False, separators=(",", ":"))
        if len(ie_compact) < 500:
            parts.append(f"info={ie_compact}")
    return " | ".join(parts)


def _ordinary_runtime_facts_view(rf: dict[str, Any]) -> dict[str, Any]:
    """Redact capability planning hints from ordinary task runtime facts."""
    if not rf:
        return {}
    filtered = dict(rf)
    for key in list(filtered):
        if key in _ORDINARY_RUNTIME_FACTS_HIDDEN_KEYS or key.startswith(_ORDINARY_RUNTIME_FACTS_HIDDEN_PREFIXES):
            filtered.pop(key, None)
    return filtered


def _capability_world_summary_view(ws: dict[str, Any]) -> dict[str, Any]:
    """Filter weak-reference world_summary fields for Capability header JSON."""
    if not ws:
        return {}
    filtered = dict(ws)
    economy = dict(filtered.get("economy") or {})
    queue_blocked_items = economy.get("queue_blocked_items")
    if isinstance(queue_blocked_items, list):
        economy["queue_blocked_items"] = _filter_capability_queue_blocked_items(queue_blocked_items)
    filtered["economy"] = economy
    return filtered


def _capability_recent_signals_view(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep capability header signals compact and free of raw expert payloads."""
    compact: list[dict[str, Any]] = []
    for signal in list(signals or [])[-6:]:
        if not isinstance(signal, dict):
            continue
        entry: dict[str, Any] = {
            "kind": str(signal.get("kind") or ""),
            "job_id": str(signal.get("job_id") or ""),
            "summary": str(signal.get("summary") or ""),
        }
        result = signal.get("result")
        if result is not None:
            entry["result"] = result
        compact.append(entry)
    return compact


def _capability_recent_events_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep capability header events compact and focused on player/system cues."""
    compact: list[dict[str, Any]] = []
    for event in list(events or [])[-6:]:
        if not isinstance(event, dict):
            continue
        entry: dict[str, Any] = {"type": str(event.get("type") or "")}
        timestamp = event.get("timestamp")
        if timestamp is not None:
            entry["timestamp"] = timestamp
        data = event.get("data")
        if isinstance(data, dict):
            filtered_data = {
                key: data[key]
                for key in ("text", "power_provided", "power_drained")
                if key in data
            }
            if filtered_data:
                entry["data"] = filtered_data
        compact.append(entry)
    return compact


def _build_player_messages(events: list[dict[str, Any]]) -> str:
    """Build [player_messages] block from PLAYER_MESSAGE and LOW_POWER events, newest first."""
    now = time.time()
    player_msgs = [
        evt for evt in events
        if evt.get("type") == "PLAYER_MESSAGE" and isinstance(evt.get("data"), dict)
    ]
    low_power_events = [
        evt for evt in events
        if evt.get("type") == "LOW_POWER"
    ]
    if not player_msgs and not low_power_events:
        return ""
    parts = ["[玩家追加指令]"]
    for evt in low_power_events:
        data = evt.get("data") or {}
        parts.append(f"⚡系统事件: 电力不足（供电{data.get('power_provided', '?')}/耗电{data.get('power_drained', '?')}），请建电厂")
    for evt in reversed(player_msgs):
        text = evt["data"].get("text", "")
        ts = evt.get("timestamp") or evt.get("data", {}).get("timestamp")
        if ts:
            ago = int(now - ts)
            parts.append(f"{ago}s前: \"{text}\"")
        else:
            parts.append(f"最近: \"{text}\"")
    return "\n".join(parts)


def _capability_status_snapshot(rf: dict[str, Any]) -> CapabilityStatusSnapshot:
    return CapabilityStatusSnapshot.from_mapping(
        rf.get("capability_status", {}) if isinstance(rf, dict) else {}
    )


def _build_capability_directives(rf: dict[str, Any]) -> str:
    """Build a compact directive-memory block from capability runtime state."""
    capability_status = _capability_status_snapshot(rf)
    directives = list(capability_status.recent_directives)
    if not directives:
        return ""
    parts = ["[能力近期指令]"]
    for text in directives[-5:]:
        parts.append(f"- {text}")
    return "\n".join(parts)


def _build_unfulfilled_requests(rf: dict[str, Any]) -> str:
    """Build [unfulfilled_requests] block for Capability context."""
    reason_labels = {
        "waiting_dispatch": "等待 Capability 分发",
        "bootstrap_in_progress": "Kernel fast-path 生产中",
        "start_package_released": "已达到启动包，剩余补强中",
        "reinforcement_waiting_dispatch": "增援待分发",
        "reinforcement_bootstrapping": "增援生产中",
        "reinforcement_after_start": "增援补强中",
        "inference_pending": "等待解析具体单位",
        "world_sync_stale": "世界状态陈旧，暂停下单",
        "deploy_required": "需先展开基地车",
        "missing_prerequisite": "缺少前置建筑",
        "disabled_prerequisite": "前置建筑离线/停用，需先恢复",
        "low_power": "当前低电，先恢复供电",
        "producer_disabled": "生产建筑离线/停用，需先恢复",
        "queue_blocked": "生产队列阻塞，需先解阻",
        "insufficient_funds": "资金不足",
    }
    reqs = rf.get("unfulfilled_requests", [])
    if not reqs:
        return ""
    parts = ["[待处理请求]"]
    for r in reqs:
        rid = r.get("request_id", "?")
        task_label = r.get("task_label", "?")
        cat = r.get("category", "?")
        count = r.get("count", 0)
        fulfilled = r.get("fulfilled", 0)
        urgency = r.get("urgency", "medium")
        hint = r.get("hint", "")
        unit_type = str(r.get("unit_type", "") or "")
        queue_type = str(r.get("queue_type", "") or "")
        blocking = bool(r.get("blocking", True))
        min_start_package = int(r.get("min_start_package", 1) or 1)
        reason = r.get("reason", "")
        remaining = count - fulfilled
        line = f"REQ-{rid} #{task_label} {cat}x{remaining} {urgency} \"{hint}\""
        if unit_type:
            line += f" => {unit_type}"
            if queue_type:
                line += f"/{queue_type}"
        line += " blocking" if blocking else " reinforcement"
        if min_start_package > 1:
            line += f" start>={min_start_package}"
        if reason:
            line += f" 原因:{reason_labels.get(reason, reason)}"
        if reason == "world_sync_stale":
            failures = int(r.get("world_sync_consecutive_failures", 0) or 0)
            threshold = int(r.get("world_sync_failure_threshold", 0) or 0)
            error = str(r.get("world_sync_last_error", "") or "")
            if failures:
                line += f" 同步失败:{failures}"
                if threshold:
                    line += f"/{threshold}"
            if error:
                line += f" 最近错误:{error}"
        queue_blocked_reason = str(r.get("queue_blocked_reason", "") or "")
        queue_blocked_queue_types = [
            str(item)
            for item in list(r.get("queue_blocked_queue_types", []) or [])
            if item
        ]
        queue_blocked_items = [
            dict(item)
            for item in list(r.get("queue_blocked_items", []) or [])
            if isinstance(item, dict)
        ]
        if reason == "queue_blocked":
            if queue_blocked_queue_types:
                line += f" 队列:{','.join(queue_blocked_queue_types)}"
            if queue_blocked_reason:
                line += f" 细因:{_readiness_reason_label('queue_blocked', queue_blocked_reason=queue_blocked_reason)}"
            if queue_blocked_items:
                names = "、".join(
                    str(item.get("display_name") or item.get("unit_type") or "?")
                    for item in queue_blocked_items[:2]
                )
                line += f" 成品:{names}"
        prerequisites = [
            demo_prompt_display_name_for(item)
            for item in list(r.get("prerequisites", []) or [])
            if item
        ]
        if reason == "missing_prerequisite" and prerequisites:
            line += f" 前置:{' + '.join(prerequisites)}"
        disabled_prerequisites = [str(item) for item in list(r.get("disabled_prerequisites", []) or []) if item]
        if reason == "disabled_prerequisite" and disabled_prerequisites:
            line += f" 前置离线:{' + '.join(disabled_prerequisites)}"
        disabled_producers = [str(item) for item in list(r.get("disabled_producers", []) or []) if item]
        if reason == "producer_disabled" and disabled_producers:
            line += f" 生产点:{' + '.join(disabled_producers)}"
        parts.append(line)
    return "\n".join(parts)


def _build_active_production(rf: dict[str, Any]) -> str:
    """Build [active_production] block for Capability context."""
    queues = rf.get("production_queues", {})
    ready_items = rf.get("ready_queue_items", [])
    if not queues:
        if not ready_items:
            return ""
        queues = {}
    parts = ["[生产队列]"]
    for queue_type, items in queues.items():
        if not items:
            parts.append(f"{queue_type}: 空闲")
        else:
            for item in items:
                unit = item.get("unit_type", "?")
                count = item.get("count", 1)
                source = item.get("source", "")
                source_tag = f" ({source})" if source else ""
                parts.append(f"{queue_type}: {unit}x{count}{source_tag}")
    if ready_items:
        parts.append("[待处理已就绪条目]")
        for item in ready_items[:6]:
            queue_type = item.get("queue_type", "?")
            display_name = item.get("display_name", item.get("unit_type", "?"))
            owner_actor_id = item.get("owner_actor_id")
            owner_tag = f" owner={owner_actor_id}" if owner_actor_id is not None else ""
            parts.append(f"{queue_type}: {display_name}{owner_tag}")
    return "\n".join(parts)


def _build_capability_queue_block_state(rf: dict[str, Any]) -> str:
    """Render a compact queue-blocking summary for capability decisions."""
    if not rf or not rf.get("queue_blocked"):
        return ""
    reason = str(rf.get("queue_blocked_reason", "") or "")
    queues = [str(item) for item in list(rf.get("queue_blocked_queue_types", []) or []) if item]
    reason_text = {
        "ready_not_placed": "有已完成未放置条目",
        "paused": "队列被暂停",
        "mixed": "同时存在未放置和暂停条目",
    }.get(reason, "队列阻塞")
    line = f"[队列阻塞] {reason_text}"
    if queues:
        line += f" | queues={','.join(queues)}"
    blocked_items = [
        dict(item)
        for item in list(rf.get("queue_blocked_items", []) or [])
        if isinstance(item, dict)
    ]
    if blocked_items:
        names = "、".join(
            str(item.get("display_name") or item.get("unit_type") or "?")
            for item in blocked_items[:3]
        )
        line += f" | items={names}"
    return line


def _filter_capability_queue_blocked_items(
    items: list[Any],
    *,
    faction: str | None = None,
) -> list[dict[str, Any]]:
    normalized = [
        dict(item)
        for item in list(items or [])
        if isinstance(item, dict)
    ]
    return filter_demo_capability_ready_items(normalized, faction=faction)


def _has_ready_power_recovery(rf: dict[str, Any]) -> bool:
    for item in list(rf.get("ready_queue_items", []) or []) + list(rf.get("queue_blocked_items", []) or []):
        if not isinstance(item, dict):
            continue
        canonical = demo_capability_unit_type_for(item.get("unit_type") or item.get("display_name"))
        if canonical in {"powr", "apwr"}:
            return True
    return False


def _build_capability_truth_guard(rf: dict[str, Any]) -> str:
    blocker = str(rf.get("capability_truth_blocker") or "").strip()
    if blocker != "faction_roster_unsupported":
        return ""
    faction = str(rf.get("faction") or "unknown").strip() or "unknown"
    return (
        f"[能力真值] faction={faction} 当前 demo capability roster 未覆盖该阵营；"
        "已隐藏 buildable/buildable_now/buildable_blocked，禁止补链，直接 wait"
    )


def _build_unit_reservations(rf: dict[str, Any]) -> str:
    """Build [Reservations] block for Capability context."""
    reservations = rf.get("unit_reservations", [])
    if not reservations:
        return ""
    parts = ["[预留]"]
    for reservation in reservations[:8]:
        if not isinstance(reservation, dict):
            continue
        reservation_id = reservation.get("reservation_id", "?")
        request_id = reservation.get("request_id", "?")
        task_label = reservation.get("task_label", "?")
        unit_type = reservation.get("unit_type", "?")
        queue_type = reservation.get("queue_type", "") or demo_queue_type_for(str(unit_type))
        count = int(reservation.get("count", 0) or 0)
        assigned = len(reservation.get("assigned_actor_ids", []) or [])
        produced = len(reservation.get("produced_actor_ids", []) or [])
        remaining = int(reservation.get("remaining_count", max(0, count - assigned - produced)))
        status = reservation.get("status", "?")
        blocking = bool(reservation.get("blocking", True))
        min_start_package = int(reservation.get("min_start_package", 1) or 1)
        bootstrap_job_id = reservation.get("bootstrap_job_id", "")
        bootstrap_task_id = reservation.get("bootstrap_task_id", "")
        line = f"{reservation_id} REQ-{request_id} #{task_label} {unit_type}"
        if queue_type:
            line += f"/{queue_type}"
        line += f" remaining={remaining} assigned={assigned} produced={produced} status={status}"
        line += " blocking" if blocking else " reinforcement"
        if min_start_package > 1:
            line += f" start>={min_start_package}"
        if bootstrap_job_id:
            line += f" bootstrap={bootstrap_job_id}"
        if bootstrap_task_id:
            line += f" owner={bootstrap_task_id}"
        if reservation.get("reason") == "world_sync_stale":
            failures = int(reservation.get("world_sync_consecutive_failures", 0) or 0)
            threshold = int(reservation.get("world_sync_failure_threshold", 0) or 0)
            error = str(reservation.get("world_sync_last_error", "") or "")
            if failures:
                line += f" sync_fail={failures}"
                if threshold:
                    line += f"/{threshold}"
            if error:
                line += f" sync_error={error}"
        parts.append(line)
    return "\n".join(parts)


def _build_capability_phase_block(rf: dict[str, Any], signals: list[dict[str, Any]]) -> str:
    """Build a phase block for Capability context."""
    entries: list[str] = []
    capability_status = _capability_status_snapshot(rf)
    current_phase = rf.get("task_phase") or capability_status.phase or rf.get("phase")
    if current_phase:
        entries.append(f"task={current_phase}")

    for job in rf.get("this_task_jobs", []):
        if not isinstance(job, dict):
            continue
        phase = job.get("phase")
        if not phase:
            continue
        label = job.get("job_id") or job.get("expert_type") or "job"
        entries.append(f"{label}={phase}")

    for sig in reversed(signals):
        state = sig.get("expert_state") or {}
        if not isinstance(state, dict):
            state = {}
        phase = state.get("phase") or (sig.get("data") or {}).get("phase")
        if not phase:
            continue
        label = sig.get("job_id") or sig.get("kind") or "signal"
        entries.append(f"{label}={phase}")
        if len(entries) >= 4:
            break

    if not entries:
        return ""

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)
    return "[阶段] " + " | ".join(deduped[:4])


def _build_capability_blocker_block(rf: dict[str, Any], signals: list[dict[str, Any]]) -> str:
    """Build a blocker block for Capability context."""
    entries: list[str] = []

    capability_status = _capability_status_snapshot(rf)
    capability_blocker = str(rf.get("capability_blocker", "") or capability_status.blocker)
    if capability_blocker == "request_inference_pending":
        inference_count = capability_status.inference_pending_count
        line = "存在待解析的单位请求，等待 Capability 先确定具体生产目标"
        if inference_count:
            line += f"（inference={inference_count}）"
        entries.append(line)
    elif capability_blocker == "world_sync_stale":
        stale_count = capability_status.world_sync_stale_count
        line = "世界状态同步陈旧，暂停新生产/补链"
        if stale_count:
            line += f"（stale={stale_count}）"
        entries.append(line)
    elif capability_blocker == "deploy_required":
        deploy_count = capability_status.deploy_required_count
        line = "当前仅有基地车，需先展开建造厂"
        if deploy_count:
            line += f"（deploy={deploy_count}）"
        entries.append(line)
    elif capability_blocker == "missing_prerequisite":
        prerequisite_count = capability_status.prerequisite_gap_count
        line = "存在缺前置建筑的请求，需先补链后再分发"
        if prerequisite_count:
            line += f"（prereq_gap={prerequisite_count}）"
        entries.append(line)
    elif capability_blocker == "disabled_prerequisite":
        disabled_prerequisite_count = capability_status.disabled_prerequisite_count
        line = "存在离线/停用的前置建筑，需先恢复前置再继续分发"
        if disabled_prerequisite_count:
            line += f"（prereq_disabled={disabled_prerequisite_count}）"
        entries.append(line)
    elif capability_blocker == "low_power":
        low_power_count = capability_status.low_power_count
        line = "当前低电，先恢复供电再继续产能扩张"
        if low_power_count:
            line += f"（low_power={low_power_count}）"
        entries.append(line)
    elif capability_blocker == "producer_disabled":
        producer_disabled_count = capability_status.producer_disabled_count
        line = "对应生产建筑离线/停用，需先恢复生产建筑再继续分发"
        if producer_disabled_count:
            line += f"（producer_disabled={producer_disabled_count}）"
        entries.append(line)
    elif capability_blocker == "queue_blocked":
        queue_blocked_count = capability_status.queue_blocked_count
        line = "生产队列阻塞，需先处理已就绪/暂停条目"
        if queue_blocked_count:
            line += f"（queue_blocked={queue_blocked_count}）"
        entries.append(line)
    elif capability_blocker == "insufficient_funds":
        insufficient_funds_count = capability_status.insufficient_funds_count
        line = "当前资金不足，暂不继续新下单"
        if insufficient_funds_count:
            line += f"（funds={insufficient_funds_count}）"
        entries.append(line)
    elif capability_blocker == "pending_requests_waiting_dispatch":
        blocking_count = (
            capability_status.dispatch_request_count
            or capability_status.blocking_request_count
            or int(rf.get("dispatch_request_count", 0) or 0)
            or int(rf.get("blocking_request_count", 0) or 0)
        )
        line = "能力层有待分发请求"
        if blocking_count:
            line += f"（blocking={blocking_count}）"
        entries.append(line)
    elif capability_blocker == "bootstrap_in_progress":
        bootstrap_count = capability_status.bootstrapping_request_count
        line = "Kernel fast-path 生产进行中，等待 Capability 接手与收口"
        if bootstrap_count:
            line += f"（bootstrapping={bootstrap_count}）"
        entries.append(line)

    for req in rf.get("unfulfilled_requests", []):
        if not isinstance(req, dict):
            continue
        rid = req.get("request_id", "?")
        task_label = req.get("task_label", "?")
        cat = req.get("category", "?")
        count = int(req.get("count", 0) or 0)
        fulfilled = int(req.get("fulfilled", 0) or 0)
        remaining = max(0, count - fulfilled)
        hint = req.get("hint", "")
        reason = req.get("reason", "")
        line = f"REQ-{rid} #{task_label} {cat}x{remaining}"
        if hint:
            line += f' "{hint}"'
        if reason:
            line += f" 原因:{_readiness_reason_label(reason)}"
        queue_blocked_reason = str(req.get("queue_blocked_reason", "") or "")
        queue_blocked_queue_types = [
            str(item)
            for item in list(req.get("queue_blocked_queue_types", []) or [])
            if item
        ]
        queue_blocked_items = [
            dict(item)
            for item in list(req.get("queue_blocked_items", []) or [])
            if isinstance(item, dict)
        ]
        if reason == "queue_blocked":
            if queue_blocked_queue_types:
                line += f" 队列:{','.join(queue_blocked_queue_types)}"
            if queue_blocked_reason:
                line += f" 细因:{_readiness_reason_label('queue_blocked', queue_blocked_reason=queue_blocked_reason)}"
            if queue_blocked_items:
                names = "、".join(
                    str(item.get("display_name") or item.get("unit_type") or "?")
                    for item in queue_blocked_items[:2]
                )
                line += f" 成品:{names}"
        prerequisites = [
            demo_prompt_display_name_for(item)
            for item in list(req.get("prerequisites", []) or [])
            if item
        ]
        if reason == "missing_prerequisite" and prerequisites:
            line += f" 前置:{' + '.join(prerequisites)}"
        disabled_prerequisites = [str(item) for item in list(req.get("disabled_prerequisites", []) or []) if item]
        if reason == "disabled_prerequisite" and disabled_prerequisites:
            line += f" 前置离线:{' + '.join(disabled_prerequisites)}"
        disabled_producers = [str(item) for item in list(req.get("disabled_producers", []) or []) if item]
        if reason == "producer_disabled" and disabled_producers:
            line += f" 生产点:{' + '.join(disabled_producers)}"
        entries.append(line)

    for sig in reversed(signals):
        kind = sig.get("kind", "")
        if kind not in {"blocked", "constraint_violated", "decision_request"}:
            continue
        summary = sig.get("summary", "")
        label = sig.get("job_id") or kind
        if summary:
            entries.append(f"{label} {summary}")
        else:
            entries.append(str(label))

    if not entries:
        return ""

    return "[阻塞] " + " | ".join(entries[:4])


def _capability_runtime_facts_view(rf: dict[str, Any]) -> dict[str, Any]:
    """Filter capability runtime facts to the demo-safe subset shown to the LLM.

    The live OpenRA ruleset can expose a broader tech tree than this demo
    supports.  Capability should only see the simplified roster/buildings we
    intentionally allow it to reason about.
    """
    if not rf:
        return {}
    filtered = dict(rf)
    capability_faction = str(filtered.get("faction") or "").strip().lower() or None
    if "queue_blocked_items" in filtered and isinstance(filtered["queue_blocked_items"], list):
        filtered["queue_blocked_items"] = _filter_capability_queue_blocked_items(
            filtered["queue_blocked_items"],
            faction=capability_faction,
        )
    if "unfulfilled_requests" in filtered and isinstance(filtered["unfulfilled_requests"], list):
        compact_requests: list[dict[str, Any]] = []
        for request in filtered["unfulfilled_requests"]:
            if not isinstance(request, dict):
                continue
            normalized = dict(request)
            normalized["queue_blocked_items"] = _filter_capability_queue_blocked_items(
                list(request.get("queue_blocked_items", []) or []),
                faction=capability_faction,
            )
            compact_requests.append(normalized)
        filtered["unfulfilled_requests"] = compact_requests
    if "unit_reservations" in filtered and isinstance(filtered["unit_reservations"], list):
        reservations = filter_demo_capability_reservations(
            filtered["unit_reservations"],
            faction=capability_faction,
        )
        compact_reservations: list[dict[str, Any]] = []
        for reservation in reservations:
            if not isinstance(reservation, dict):
                continue
            compact_reservations.append({
                "reservation_id": reservation.get("reservation_id", "?"),
                "request_id": reservation.get("request_id", "?"),
                "task_id": reservation.get("task_id", "?"),
                "task_label": reservation.get("task_label", "?"),
                "unit_type": reservation.get("unit_type", "?"),
                "queue_type": reservation.get("queue_type", "") or demo_queue_type_for(str(reservation.get("unit_type", ""))),
                "count": int(reservation.get("count", 0) or 0),
                "remaining_count": int(reservation.get("remaining_count", max(0, int(reservation.get("count", 0) or 0) - len(reservation.get("assigned_actor_ids", []) or []) - len(reservation.get("produced_actor_ids", []) or [])))),
                "blocking": bool(reservation.get("blocking", True)),
                "min_start_package": int(reservation.get("min_start_package", 1) or 1),
                "assigned_actor_ids": list(reservation.get("assigned_actor_ids", []) or []),
                "produced_actor_ids": list(reservation.get("produced_actor_ids", []) or []),
                "status": reservation.get("status", "?"),
                "reason": str(reservation.get("reason") or ""),
                "bootstrap_job_id": reservation.get("bootstrap_job_id", ""),
                "bootstrap_task_id": reservation.get("bootstrap_task_id", ""),
                "world_sync_last_error": str(reservation.get("world_sync_last_error", "") or ""),
                "world_sync_consecutive_failures": int(reservation.get("world_sync_consecutive_failures", 0) or 0),
                "world_sync_failure_threshold": int(reservation.get("world_sync_failure_threshold", 0) or 0),
                "cancelled_at": reservation.get("cancelled_at"),
            })
        filtered["unit_reservations"] = compact_reservations
    production_queues = rf.get("production_queues", {})
    if isinstance(production_queues, dict):
        filtered["production_queues"] = filter_demo_capability_production_queues(
            production_queues,
            faction=capability_faction,
        )
    ready_queue_items = rf.get("ready_queue_items", [])
    if isinstance(ready_queue_items, list):
        filtered["ready_queue_items"] = filter_demo_capability_ready_items(
            ready_queue_items,
            faction=capability_faction,
        )
    buildable = rf.get("buildable", {})
    if isinstance(buildable, dict):
        filtered["buildable"] = filter_demo_capability_buildable(buildable, faction=capability_faction)
    buildable_now = rf.get("buildable_now")
    if isinstance(buildable_now, dict):
        filtered["buildable_now"] = filter_demo_capability_buildable(
            buildable_now,
            faction=capability_faction,
        )
    buildable_blocked = rf.get("buildable_blocked")
    if isinstance(buildable_blocked, dict):
        filtered_blocked: dict[str, list[dict[str, Any]]] = {}
        for queue_type in demo_capability_queue_types():
            items = list(buildable_blocked.get(queue_type, []) or [])
            keep: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                canonical = str(item.get("unit_type") or "").lower()
                if demo_queue_type_for(canonical) != queue_type:
                    continue
                keep.append({
                    "unit_type": canonical,
                    "queue_type": queue_type,
                    "reason": str(item.get("reason") or ""),
                    "queue_blocked_reason": str(item.get("queue_blocked_reason") or ""),
                    "queue_blocked_items": _filter_capability_queue_blocked_items(
                        list(item.get("queue_blocked_items", []) or [])
                    ),
                    "disabled_prerequisites": [str(value) for value in list(item.get("disabled_prerequisites", []) or []) if value],
                    "disabled_producers": [str(value) for value in list(item.get("disabled_producers", []) or []) if value],
                })
            if keep:
                filtered_blocked[queue_type] = keep
        filtered["buildable_blocked"] = filtered_blocked
    if "base_progression" in filtered:
        filtered["base_progression"] = normalize_base_progression(filtered)
    if str(filtered.get("capability_truth_blocker") or "") == "faction_roster_unsupported":
        filtered["buildable"] = {}
        filtered["buildable_now"] = {}
        filtered["buildable_blocked"] = {}
        filtered["base_progression"] = {}
    return filtered


def _build_capability_base_state(rf: dict[str, Any]) -> str:
    """Build a compact capability-facing base state line."""
    if not rf:
        return ""
    fields = [
        f"建造厂={'有' if rf.get('has_construction_yard') else '无'}",
        f"基地车={rf.get('mcv_count', 0)}",
        f"电厂={rf.get('power_plant_count', 0)}",
        f"矿场={rf.get('refinery_count', 0)}",
        f"兵营={rf.get('barracks_count', 0)}",
        f"车厂={rf.get('war_factory_count', 0)}",
        f"雷达={rf.get('radar_count', 0)}",
        f"维修厂={rf.get('repair_facility_count', 0)}",
        f"空军基地={rf.get('airfield_count', 0)}",
        f"科技中心={rf.get('tech_center_count', 0)}",
        f"矿车={rf.get('harvester_count', 0)}",
    ]
    disabled_count = int(rf.get("disabled_structure_count", 0) or 0)
    low_power_disabled_count = int(rf.get("low_power_disabled_structure_count", 0) or 0)
    if disabled_count:
        fields.append(f"离线建筑={disabled_count}")
    if low_power_disabled_count:
        fields.append(f"低电离线={low_power_disabled_count}")
    disabled_structures = [str(item) for item in list(rf.get("disabled_structures", []) or []) if item]
    if disabled_structures:
        preview = "、".join(disabled_structures[:2])
        if len(disabled_structures) > 2:
            preview += f" 等{len(disabled_structures)}个"
        fields.append(f"离线明细={preview}")
    return "[基地状态] " + " ".join(fields)


def _build_capability_world_sync(rf: dict[str, Any]) -> str:
    """Expose stale-world status explicitly for capability fail-closed behavior."""
    if not rf:
        return ""
    stale = bool(rf.get("world_sync_stale", False))
    failures = int(rf.get("world_sync_consecutive_failures", 0) or 0)
    total = int(rf.get("world_sync_total_failures", 0) or 0)
    error = str(rf.get("world_sync_last_error", "") or "")
    if not stale and failures <= 0 and not error:
        return ""
    line = f"[世界同步] stale={'true' if stale else 'false'} failures={failures}/{total}"
    if error:
        line += f" error={error}"
    return line


def _build_ordinary_world_sync(rf: dict[str, Any]) -> str:
    """Expose stale-world status for ordinary tasks in human-readable form."""
    if not rf:
        return ""
    stale = bool(rf.get("world_sync_stale", False))
    failures = int(rf.get("world_sync_consecutive_failures", 0) or 0)
    total = int(rf.get("world_sync_total_failures", 0) or 0)
    error = str(rf.get("world_sync_last_error", "") or "")
    if not stale and failures <= 0 and not error:
        return ""
    line = f"[世界同步] stale={'true' if stale else 'false'} failures={failures}/{total}"
    if error:
        line += f" error={error}"
    return line


def _readiness_reason_label(reason: str, *, queue_blocked_reason: str = "") -> str:
    key = str(reason or "").strip().lower()
    if key == "world_sync_stale":
        return "世界同步陈旧"
    if key == "deploy_required":
        return "需先展开基地车"
    if key == "queue_blocked":
        if queue_blocked_reason == "ready_not_placed":
            return "队列有已完成未放置条目"
        if queue_blocked_reason == "paused":
            return "队列被暂停"
        return "队列阻塞"
    if key == "producer_disabled":
        return "生产点离线"
    if key == "disabled_prerequisite":
        return "前置建筑离线"
    if key == "low_power":
        return "低电"
    if key == "insufficient_funds":
        return "资金不足"
    if key == "missing_prerequisite":
        return "缺少前置"
    return key or "暂不可下单"


def _build_capability_issue_now(rf: dict[str, Any]) -> str:
    """Render units/buildings that are safe to issue right now."""
    if not rf:
        return ""
    if str(rf.get("capability_truth_blocker") or "") == "faction_roster_unsupported":
        return ""
    buildable_now = rf.get("buildable_now", {})
    if not isinstance(buildable_now, dict):
        return ""
    lines = list(
        demo_capability_buildable_lines(
            buildable_now,
            faction=str(rf.get("faction") or "").strip().lower() or None,
        )
    )
    if not lines:
        return ""
    return f"[可立即下单] {' | '.join(lines)}"


def _build_capability_prereq_reference(rf: dict[str, Any]) -> str:
    """Render low-priority prereq truth not already covered by stronger lanes."""
    if not rf:
        return ""
    if str(rf.get("capability_truth_blocker") or "") == "faction_roster_unsupported":
        return ""
    buildable = rf.get("buildable", {})
    if not isinstance(buildable, dict):
        return ""

    covered: set[str] = set()
    buildable_now = rf.get("buildable_now", {})
    if isinstance(buildable_now, dict):
        for units in buildable_now.values():
            for unit_type in list(units or []):
                canonical = str(unit_type or "").strip().lower()
                if canonical:
                    covered.add(canonical)

    blocked = rf.get("buildable_blocked", {})
    if isinstance(blocked, dict):
        for items in blocked.values():
            for item in list(items or []):
                if not isinstance(item, dict):
                    continue
                canonical = str(item.get("unit_type") or "").strip().lower()
                if canonical:
                    covered.add(canonical)

    reference_only: dict[str, list[str]] = {}
    for queue_type in demo_capability_queue_types():
        keep: list[str] = []
        for unit_type in list(buildable.get(queue_type, []) or []):
            canonical = str(unit_type or "").strip().lower()
            if not canonical:
                continue
            if canonical in covered:
                continue
            keep.append(canonical)
        if keep:
            reference_only[queue_type] = keep

    if not reference_only:
        return ""
    parts = list(
        demo_capability_buildable_lines(
            reference_only,
            faction=str(rf.get("faction") or "").strip().lower() or None,
        )
    )
    if not parts:
        return ""
    return f"[前置已满足] 仅参考: {' | '.join(parts)}"


def _build_capability_blocked_buildable(rf: dict[str, Any]) -> str:
    """Render prereq-satisfied demo items that are still blocked right now."""
    if not rf:
        return ""
    if str(rf.get("capability_truth_blocker") or "") == "faction_roster_unsupported":
        return ""
    blocked = rf.get("buildable_blocked", {})
    if not isinstance(blocked, dict):
        return ""
    entries: list[str] = []
    for queue_type in demo_capability_queue_types():
        for item in list(blocked.get(queue_type, []) or []):
            if not isinstance(item, dict):
                continue
            unit_type = str(item.get("unit_type") or "").lower()
            if not unit_type:
                continue
            label = demo_prompt_display_name_for(unit_type)
            reason = _readiness_reason_label(
                str(item.get("reason") or ""),
                queue_blocked_reason=str(item.get("queue_blocked_reason") or ""),
            )
            line = f"{unit_type}({label})={reason}"
            blocked_items = [
                dict(value)
                for value in list(item.get("queue_blocked_items", []) or [])
                if isinstance(value, dict)
            ]
            if blocked_items and str(item.get("reason") or "") == "queue_blocked":
                names = "、".join(
                    str(value.get("display_name") or value.get("unit_type") or "?")
                    for value in blocked_items[:2]
                )
                line += f" 成品:{names}"
            disabled_producers = [str(value) for value in list(item.get("disabled_producers", []) or []) if value]
            if disabled_producers and str(item.get("reason") or "") == "producer_disabled":
                line += f" 生产点:{' + '.join(disabled_producers[:2])}"
            disabled_prerequisites = [str(value) for value in list(item.get("disabled_prerequisites", []) or []) if value]
            if disabled_prerequisites and str(item.get("reason") or "") == "disabled_prerequisite":
                line += f" 前置离线:{' + '.join(disabled_prerequisites[:2])}"
            entries.append(line)
    if not entries:
        return ""
    return "[前置已满足但当前受阻] " + " | ".join(entries[:8])


def _build_capability_base_progression(rf: dict[str, Any]) -> str:
    """Build the shared demo base-progression hint for Capability context."""
    if not rf:
        return ""
    if str(rf.get("capability_truth_blocker") or "") == "faction_roster_unsupported":
        return ""
    progression = normalize_base_progression(rf)
    status = str(progression.get("status", "") or "")
    if not status:
        return ""
    line = f"[基地推进] {status}"
    action_required = str(progression.get("action_required", "") or "")
    if action_required:
        line += f" | action={action_required}"
        if action_required == "deploy_mcv":
            line += " | 先query_world(my_actors, category=mcv)获取actor_id"
    next_unit_type = str(progression.get("next_unit_type", "") or "")
    next_queue_type = str(progression.get("next_queue_type", "") or demo_queue_type_for(next_unit_type) or "")
    if next_unit_type:
        line += f" | next={next_unit_type}"
    runtime_buildable_now = {
        str(item).lower()
        for item in list((rf.get("buildable_now", {}) or {}).get(next_queue_type, []) or [])
        if item is not None
    }
    blocked_entry = None
    for item in list((rf.get("buildable_blocked", {}) or {}).get(next_queue_type, []) or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("unit_type") or "").lower() == next_unit_type:
            blocked_entry = item
            break
    has_runtime_buildable_now = isinstance(rf.get("buildable_now"), dict)
    can_progress_now = bool(next_unit_type and next_unit_type in runtime_buildable_now)
    if next_unit_type and not has_runtime_buildable_now and bool(progression.get("buildable_now")):
        can_progress_now = True
    if can_progress_now:
        line += " | 可直接推进"
    elif blocked_entry is not None:
        blocked_label = _readiness_reason_label(
            str(blocked_entry.get("reason") or ""),
            queue_blocked_reason=str(blocked_entry.get("queue_blocked_reason") or ""),
        )
        line += f" | 当前受阻:{blocked_label}"
        blocked_items = [
            dict(value)
            for value in list(blocked_entry.get("queue_blocked_items", []) or [])
            if isinstance(value, dict)
        ]
        if blocked_items and str(blocked_entry.get("reason") or "") == "queue_blocked":
            names = "、".join(
                str(value.get("display_name") or value.get("unit_type") or "?")
                for value in blocked_items[:2]
            )
            line += f"({names})"
        disabled_prerequisites = [
            str(value)
            for value in list(blocked_entry.get("disabled_prerequisites", []) or [])
            if value
        ]
        if disabled_prerequisites and str(blocked_entry.get("reason") or "") == "disabled_prerequisite":
            line += f"({' + '.join(disabled_prerequisites[:2])})"
    return line


def _build_capability_recent_signals(signals: list[dict[str, Any]]) -> str:
    """Build a compact recent-signals block for capability decisions."""
    if not signals:
        return ""
    parts = ["[最近信号]"]
    for sig in signals[-6:]:
        kind = sig.get("kind", "?")
        summary = sig.get("summary", "")
        data = sig.get("data") or {}
        unit_type = data.get("unit_type", "")
        result = sig.get("result")
        label = f"{kind}"
        if unit_type:
            label += f" {unit_type}"
        if result:
            label += f" result={result}"
        if summary:
            label += f" — {summary}"
        parts.append(label)
    return "\n".join(parts)


def _build_capability_runtime_status(rf: dict[str, Any], other_active_tasks: list[dict[str, Any]]) -> str:
    """Build a compact status line so Capability sees live workload at a glance."""
    capability_status = _capability_status_snapshot(rf)
    parts: list[str] = []

    if capability_status.active_job_types:
        counts: dict[str, int] = {}
        for job_type in capability_status.active_job_types:
            counts[job_type] = counts.get(job_type, 0) + 1
        jobs = ",".join(
            f"{job_type}x{count}" if count > 1 else job_type
            for job_type, count in sorted(counts.items())
        )
        parts.append(f"jobs={jobs}")
    elif capability_status.active_job_count:
        parts.append(f"jobs={capability_status.active_job_count}")

    if capability_status.pending_request_count:
        parts.append(f"pending={capability_status.pending_request_count}")
    if capability_status.blocking_request_count:
        parts.append(f"blocking={capability_status.blocking_request_count}")
    if capability_status.dispatch_request_count:
        parts.append(f"dispatch={capability_status.dispatch_request_count}")
    if capability_status.bootstrapping_request_count:
        parts.append(f"boot={capability_status.bootstrapping_request_count}")
    if capability_status.start_released_request_count:
        parts.append(f"start_released={capability_status.start_released_request_count}")
    if capability_status.reinforcement_request_count:
        parts.append(f"reinforcement={capability_status.reinforcement_request_count}")

    if other_active_tasks:
        summaries: list[str] = []
        for task in other_active_tasks[:4]:
            label = str(task.get("label") or "?")
            raw_text = str(task.get("raw_text") or "")
            status = str(task.get("status") or "")
            if raw_text:
                summaries.append(f"{label}:{raw_text}({status})")
            else:
                summaries.append(f"{label}({status})")
        if summaries:
            parts.append(f"parallel={'; '.join(summaries)}")

    if not parts:
        return ""
    return "[能力态势] " + " | ".join(parts)


def context_to_message(packet: ContextPacket, *, is_capability: bool = False) -> dict[str, str]:
    """Convert a context packet to a compact LLM user message.

    Uses structured text instead of raw JSON to minimize token usage.
    Target: <2000 chars for a typical context.

    Args:
        packet: The context packet with all current state.
        is_capability: If True, render capability-specific blocks instead of
            normal task blocks.
    """
    lines: list[str] = []

    # JSON header for programmatic consumers (tests, tooling).
    header_rf = packet.runtime_facts or {}
    header_ws = None
    header_signals = packet.recent_signals
    header_events = packet.recent_events
    if is_capability:
        header_rf = _capability_runtime_facts_view(header_rf)
        header_ws = _capability_world_summary_view(packet.world_summary or {})
        header_signals = _capability_recent_signals_view(packet.recent_signals)
        header_events = _capability_recent_events_view(packet.recent_events)

    header = {
        "context_packet": {
            "task": packet.task,
            "jobs": packet.jobs,
            "recent_signals": header_signals,
            "recent_events": header_events,
            "open_decisions": packet.open_decisions,
            "other_active_tasks": packet.other_active_tasks,
            "runtime_facts": header_rf,
        }
    }
    if header_ws is not None:
        header["context_packet"]["world_summary"] = header_ws
    if not is_capability:
        header["context_packet"]["runtime_facts"] = _ordinary_runtime_facts_view(header_rf)
    lines.append("[CONTEXT UPDATE]")
    lines.append(json.dumps(header, ensure_ascii=False, default=str))

    if is_capability:
        # Capability-specific: economy, production queues, unfulfilled requests, player messages
        ws = packet.world_summary or {}
        eco = ws.get("economy", {})
        rf = _capability_runtime_facts_view(packet.runtime_facts or {})
        world_sync_block = _build_capability_world_sync(rf)
        if world_sync_block:
            lines.append(world_sync_block)
        truth_guard_block = _build_capability_truth_guard(rf)
        if truth_guard_block:
            lines.append(truth_guard_block)
        base_block = _build_capability_base_state(rf)
        if base_block:
            lines.append(base_block)
        progression_block = _build_capability_base_progression(rf)
        if progression_block:
            lines.append(progression_block)
        if eco:
            cash = eco.get("cash", 0)
            pwr = eco.get("power_provided", 0)
            drain = eco.get("power_drained", 0)
            # Suppress ⚡低电力 if power recovery is already queued or ready to place.
            is_low = eco.get("low_power", False)
            if is_low:
                queues = rf.get("production_queues", {})
                for _qn, items in queues.items():
                    if any(it.get("unit_type", "").lower() in ("powr", "apwr") for it in (items or [])):
                        is_low = False
                        break
            if is_low and _has_ready_power_recovery(rf):
                is_low = False
            low = " ⚡低电力" if is_low else ""
            harv = rf.get("harvester_count", eco.get("harvester_count", "?"))
            lines.append(f"[经济] 资金:{cash} 电力:{pwr}/{drain}{low} 矿车:{harv}")

        phase_block = _build_capability_phase_block(rf, packet.recent_signals)
        if phase_block:
            lines.append(phase_block)

        blocker_block = _build_capability_blocker_block(rf, packet.recent_signals)
        if blocker_block:
            lines.append(blocker_block)

        runtime_status_block = _build_capability_runtime_status(rf, packet.other_active_tasks)
        if runtime_status_block:
            lines.append(runtime_status_block)

        prod_block = _build_active_production(rf)
        if prod_block:
            lines.append(prod_block)
        queue_block_state = _build_capability_queue_block_state(rf)
        if queue_block_state:
            lines.append(queue_block_state)

        req_block = _build_unfulfilled_requests(rf)
        if req_block:
            lines.append(req_block)

        reservation_block = _build_unit_reservations(rf)
        if reservation_block:
            lines.append(reservation_block)

        issue_now_block = _build_capability_issue_now(rf)
        if issue_now_block:
            lines.append(issue_now_block)
        blocked_buildable_block = _build_capability_blocked_buildable(rf)
        if blocked_buildable_block:
            lines.append(blocked_buildable_block)
        prereq_reference_block = _build_capability_prereq_reference(rf)
        if prereq_reference_block:
            lines.append(prereq_reference_block)

        sig_block = _build_capability_recent_signals(packet.recent_signals)
        if sig_block:
            lines.append(sig_block)

        directive_block = _build_capability_directives(rf)
        if directive_block:
            lines.append(directive_block)

        pm_block = _build_player_messages(packet.recent_events)
        if pm_block:
            lines.append(pm_block)
        else:
            lines.append("[玩家追加指令] 无")
    else:
        # Normal task context
        # Task
        t = packet.task
        lines.append(f"[任务] {t.get('raw_text','')} | 状态:{t.get('status','')} | id:{t.get('task_id','')}")

        # Jobs
        for j in packet.jobs:
            status_zh = j.get("status_zh", j.get("status", "?"))
            cfg = j.get("config", {})
            cfg_brief = ""
            if isinstance(cfg, dict):
                cfg_parts = []
                for k in ("unit_type", "count", "queue_type", "target_type", "target_position",
                           "engagement_mode", "scout_count"):
                    if k in cfg:
                        cfg_parts.append(f"{k}={cfg[k]}")
                cfg_brief = " ".join(cfg_parts)
            source_tag = " [自动创建]" if j.get("source") == "bootstrap" else ""
            lines.append(f"[Job] {j.get('job_id','')} {j.get('expert_type','')} → {status_zh}{source_tag} {cfg_brief}")

        # Signals
        for sig in packet.recent_signals:
            lines.append(f"[信号] {sig.get('kind','')} job={sig.get('job_id','')}: {sig.get('summary','')}")

        # Events (including player messages for normal tasks)
        for evt in packet.recent_events:
            lines.append(f"[事件] {evt.get('type','')} {evt.get('data','')}")

        # Player messages (highest priority for merge path)
        pm_block = _build_player_messages(packet.recent_events)
        if pm_block:
            lines.append(pm_block)

        # Decisions
        for dec in packet.open_decisions:
            lines.append(f"[决策请求] {dec.get('summary','')} job={dec.get('job_id','')}")

        workflow_name = str(packet.task.get("workflow_template") or "")
        workflow_phase_name = str(packet.task.get("workflow_phase") or "")
        wf_block = workflow_block(workflow_name, phase=workflow_phase_name)
        if wf_block:
            lines.append(wf_block)

        # World state (compact one-liners)
        ws = packet.world_summary
        if ws:
            eco_line = _compact_economy(ws.get("economy", {}))
            mil_line = _compact_military(ws.get("military", {}))
            map_line = _compact_map(ws.get("map", {}))
            lines.append(f"[世界] {eco_line} | {mil_line} | {map_line}")

        ordinary_world_sync_block = _build_ordinary_world_sync(packet.runtime_facts or {})
        if ordinary_world_sync_block:
            lines.append(ordinary_world_sync_block)

        # Enemy intel
        enemy_intel = packet.runtime_facts.get("enemy_intel", {}) if packet.runtime_facts else {}
        has_visible = enemy_intel and enemy_intel.get("total", 0) > 0
        has_frozen = enemy_intel and enemy_intel.get("frozen_count", 0) > 0
        if has_visible or has_frozen:
            enemy_parts = []
            buildings = enemy_intel.get("buildings", [])
            if buildings:
                positions = [f"[{b['position'][0]},{b['position'][1]}]" for b in buildings if b.get("position")]
                enemy_parts.append(f"建筑x{len(buildings)}({','.join(positions)})" if positions else f"建筑x{len(buildings)}")
            inf = enemy_intel.get("infantry_count", 0)
            veh = enemy_intel.get("vehicle_count", 0)
            if inf:
                enemy_parts.append(f"步兵x{inf}")
            if veh:
                enemy_parts.append(f"车辆x{veh}")
            # Frozen enemies (last-seen positions in fog-of-war)
            frozen = enemy_intel.get("frozen", [])
            if frozen:
                fpos = [f"{f.get('name','?')}[{f['position'][0]},{f['position'][1]}]" for f in frozen if f.get("position")]
                enemy_parts.append(f"残影x{len(frozen)}({','.join(fpos[:5])})" if fpos else f"残影x{len(frozen)}")
            lines.append(f"[敌军] 已发现: {' '.join(enemy_parts)}")
        else:
            lines.append("[敌军] 无情报")

        # Runtime facts (compact)
        rf_line = _compact_runtime_facts(_ordinary_runtime_facts_view(packet.runtime_facts))
        if rf_line:
            lines.append(f"[状态] {rf_line}")

        # Other active tasks (compact, with job details)
        if packet.other_active_tasks:
            others = []
            recent_reports = []
            for ot in packet.other_active_tasks:
                # Cross-task reports (memory from other tasks)
                if "_recent_reports" in ot:
                    recent_reports = ot["_recent_reports"]
                    continue
                task_str = f"{ot.get('raw_text','')}({ot.get('status','')})"
                jobs = ot.get("jobs", [])
                if jobs:
                    job_parts = []
                    for j in jobs:
                        parts = [j.get("expert", "")]
                        if "unit" in j:
                            parts.append(j["unit"])
                            if "count" in j:
                                parts[-1] += f"x{j['count']}"
                        if "region" in j:
                            parts.append(j["region"])
                        job_parts.append(":".join(parts))
                    task_str += f" [{', '.join(job_parts)}]"
                others.append(task_str)
            if others:
                lines.append(f"[并行] {', '.join(others)}")
            if recent_reports:
                report_strs = [f"#{r['task_label']} {r['content']}" for r in recent_reports]
                lines.append(f"[其他任务报告] {' | '.join(report_strs)}")

    return {"role": "user", "content": "\n".join(lines)}
