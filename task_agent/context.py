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
    demo_capability_queue_types,
    demo_prompt_display_name_for,
    demo_queue_type_for,
    filter_demo_capability_buildable,
)

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
    "feasibility",
    "production_queues",
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
    return f"资金{cash} 资源{res} 电力{pwr}/{drain}{low}"


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


def _build_capability_directives(rf: dict[str, Any]) -> str:
    """Build a compact directive-memory block from capability runtime state."""
    capability_status = rf.get("capability_status", {}) if isinstance(rf, dict) else {}
    directives = list(capability_status.get("recent_directives", []) or [])
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
        "missing_prerequisite": "缺少前置建筑",
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
        blocking = bool(r.get("blocking", True))
        min_start_package = int(r.get("min_start_package", 1) or 1)
        reason = r.get("reason", "")
        remaining = count - fulfilled
        line = f"REQ-{rid} #{task_label} {cat}x{remaining} {urgency} \"{hint}\""
        line += " blocking" if blocking else " reinforcement"
        if min_start_package > 1:
            line += f" start>={min_start_package}"
        if reason:
            line += f" 原因:{reason_labels.get(reason, reason)}"
        parts.append(line)
    return "\n".join(parts)


def _build_active_production(rf: dict[str, Any]) -> str:
    """Build [active_production] block for Capability context."""
    queues = rf.get("production_queues", {})
    if not queues:
        return ""
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
    return "\n".join(parts)


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
        parts.append(line)
    return "\n".join(parts)


def _build_capability_phase_block(rf: dict[str, Any], signals: list[dict[str, Any]]) -> str:
    """Build a phase block for Capability context."""
    entries: list[str] = []
    current_phase = rf.get("task_phase") or rf.get("phase")
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

    capability_blocker = str(rf.get("capability_blocker", "") or "")
    if capability_blocker == "request_inference_pending":
        inference_count = int(rf.get("inference_pending_count", 0) or 0)
        line = "存在待解析的单位请求，等待 Capability 先确定具体生产目标"
        if inference_count:
            line += f"（inference={inference_count}）"
        entries.append(line)
    elif capability_blocker == "missing_prerequisite":
        prerequisite_count = int(rf.get("prerequisite_gap_count", 0) or 0)
        line = "存在缺前置建筑的请求，需先补链后再分发"
        if prerequisite_count:
            line += f"（prereq_gap={prerequisite_count}）"
        entries.append(line)
    elif capability_blocker == "pending_requests_waiting_dispatch":
        blocking_count = int(rf.get("dispatch_request_count", 0) or 0)
        line = "能力层有待分发请求"
        if blocking_count:
            line += f"（blocking={blocking_count}）"
        entries.append(line)
    elif capability_blocker == "bootstrap_in_progress":
        bootstrap_count = int(rf.get("bootstrapping_request_count", 0) or 0)
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
            line += f" 原因:{reason}"
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
    if "unit_reservations" in filtered and isinstance(filtered["unit_reservations"], list):
        compact_reservations: list[dict[str, Any]] = []
        for reservation in filtered["unit_reservations"]:
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
                "bootstrap_job_id": reservation.get("bootstrap_job_id", ""),
                "bootstrap_task_id": reservation.get("bootstrap_task_id", ""),
                "cancelled_at": reservation.get("cancelled_at"),
            })
        filtered["unit_reservations"] = compact_reservations
    buildable = rf.get("buildable", {})
    if isinstance(buildable, dict):
        filtered["buildable"] = filter_demo_capability_buildable(buildable)
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
    return "[基地状态] " + " ".join(fields)


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
    if is_capability:
        header_rf = _capability_runtime_facts_view(header_rf)
        header_ws = packet.world_summary

    header = {
        "context_packet": {
            "task": packet.task,
            "jobs": packet.jobs,
            "recent_signals": packet.recent_signals,
            "recent_events": packet.recent_events,
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
        base_block = _build_capability_base_state(rf)
        if base_block:
            lines.append(base_block)
        if eco:
            cash = eco.get("cash", 0)
            pwr = eco.get("power_provided", 0)
            drain = eco.get("power_drained", 0)
            # Suppress ⚡低电力 if powr is already in production queue
            is_low = eco.get("low_power", False)
            if is_low:
                queues = rf.get("production_queues", {})
                for _qn, items in queues.items():
                    if any(it.get("unit_type", "").lower() in ("powr", "apwr") for it in (items or [])):
                        is_low = False
                        break
            low = " ⚡低电力" if is_low else ""
            harv = rf.get("harvester_count", eco.get("harvester_count", "?"))
            lines.append(f"[经济] 资金:{cash} 电力:{pwr}/{drain}{low} 矿车:{harv}")

        phase_block = _build_capability_phase_block(rf, packet.recent_signals)
        if phase_block:
            lines.append(phase_block)

        blocker_block = _build_capability_blocker_block(rf, packet.recent_signals)
        if blocker_block:
            lines.append(blocker_block)

        prod_block = _build_active_production(rf)
        if prod_block:
            lines.append(prod_block)

        req_block = _build_unfulfilled_requests(rf)
        if req_block:
            lines.append(req_block)

        reservation_block = _build_unit_reservations(rf)
        if reservation_block:
            lines.append(reservation_block)

        # Buildable units (important for Capability to know what to produce)
        buildable = rf.get("buildable", {})
        if buildable:
            b_parts = []
            for queue_type in demo_capability_queue_types():
                units = buildable.get(queue_type)
                if units:
                    labeled = []
                    for u in units:
                        cn = demo_prompt_display_name_for(u)
                        labeled.append(f"{u}({cn})" if cn else u)
                    b_parts.append(f"{queue_type}=[{','.join(labeled)}]")
            if b_parts:
                lines.append(f"[可造] {' | '.join(b_parts)}")

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

        # World state (compact one-liners)
        ws = packet.world_summary
        if ws:
            eco_line = _compact_economy(ws.get("economy", {}))
            mil_line = _compact_military(ws.get("military", {}))
            map_line = _compact_map(ws.get("map", {}))
            lines.append(f"[世界] {eco_line} | {mil_line} | {map_line}")

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
