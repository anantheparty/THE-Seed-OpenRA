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


def context_to_message(packet: ContextPacket) -> dict[str, str]:
    """Convert a context packet to an LLM user message."""
    content = json.dumps(
        {
            "context_packet": {
                "task": packet.task,
                "jobs": packet.jobs,
                "world_summary": packet.world_summary,
                "runtime_facts": packet.runtime_facts,
                "other_active_tasks": packet.other_active_tasks,
                "recent_signals": packet.recent_signals,
                "recent_events": packet.recent_events,
                "open_decisions": packet.open_decisions,
                "timestamp": packet.timestamp,
            }
        },
        ensure_ascii=False,
        indent=None,
    )
    return {"role": "user", "content": f"[CONTEXT UPDATE]\n{content}"}
