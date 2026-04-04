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
    # Core building counts
    parts: list[str] = []
    for key in ("has_construction_yard", "power_plant_count", "barracks_count",
                "refinery_count", "war_factory_count", "radar_count",
                "tech_level", "mcv_count", "mcv_idle", "harvester_count"):
        if key in rf:
            parts.append(f"{key}={rf[key]}")
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
    # Info experts (compact)
    ie = rf.get("info_experts", {})
    if ie:
        ie_compact = json.dumps(ie, ensure_ascii=False, separators=(",", ":"))
        if len(ie_compact) < 500:
            parts.append(f"info={ie_compact}")
    return " | ".join(parts)


def context_to_message(packet: ContextPacket) -> dict[str, str]:
    """Convert a context packet to a compact LLM user message.

    Uses structured text instead of raw JSON to minimize token usage.
    Target: <2000 chars for a typical context (was ~120K with is_explored grid).
    """
    lines: list[str] = []

    # Task
    t = packet.task
    lines.append(f"[任务] {t.get('raw_text','')} | 状态:{t.get('status','')} | id:{t.get('task_id','')}")

    # Jobs
    for j in packet.jobs:
        status_zh = j.get("status_zh", j.get("status", "?"))
        cfg = j.get("config", {})
        cfg_brief = ""
        if isinstance(cfg, dict):
            # Only show essential config fields
            cfg_parts = []
            for k in ("unit_type", "count", "queue_type", "target_type", "target_position",
                       "engagement_mode", "scout_count"):
                if k in cfg:
                    cfg_parts.append(f"{k}={cfg[k]}")
            cfg_brief = " ".join(cfg_parts)
        lines.append(f"[Job] {j.get('job_id','')} {j.get('expert_type','')} → {status_zh} {cfg_brief}")

    # Signals
    for sig in packet.recent_signals:
        lines.append(f"[信号] {sig.get('kind','')} job={sig.get('job_id','')}: {sig.get('summary','')}")

    # Events
    for evt in packet.recent_events:
        lines.append(f"[事件] {evt.get('type','')} {evt.get('data','')}")

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

    # Runtime facts (compact)
    rf_line = _compact_runtime_facts(packet.runtime_facts)
    if rf_line:
        lines.append(f"[状态] {rf_line}")

    # Other active tasks (compact)
    if packet.other_active_tasks:
        others = []
        for ot in packet.other_active_tasks:
            others.append(f"{ot.get('raw_text','')}({ot.get('status','')})")
        lines.append(f"[并行] {', '.join(others)}")

    return {"role": "user", "content": "\n".join(lines)}
