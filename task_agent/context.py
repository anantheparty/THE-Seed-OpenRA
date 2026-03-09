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
    open_decisions: list[dict[str, Any]]
    timestamp: float = field(default_factory=time.time)


def build_context_packet(
    task: Task,
    jobs: list[Job],
    world_summary: Optional[WorldSummary] = None,
    recent_signals: Optional[list[ExpertSignal]] = None,
    open_decisions: Optional[list[ExpertSignal]] = None,
) -> ContextPacket:
    """Build a context packet from current state.

    Args:
        task: The Task this agent is managing.
        jobs: Active Jobs belonging to this Task.
        world_summary: Current world state snapshot.
        recent_signals: Signals received since last wake (or all for initial).
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
        job_dict: dict[str, Any] = {
            "job_id": job.job_id,
            "expert_type": job.expert_type,
            "status": job.status.value,
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

    return ContextPacket(
        task=task_dict,
        jobs=jobs_list,
        world_summary=ws_dict,
        recent_signals=signals_list,
        open_decisions=decisions_list,
    )


def context_to_message(packet: ContextPacket) -> dict[str, str]:
    """Convert a context packet to an LLM user message."""
    content = json.dumps(
        {
            "context_packet": {
                "task": packet.task,
                "jobs": packet.jobs,
                "world_summary": packet.world_summary,
                "recent_signals": packet.recent_signals,
                "open_decisions": packet.open_decisions,
                "timestamp": packet.timestamp,
            }
        },
        ensure_ascii=False,
        indent=None,
    )
    return {"role": "user", "content": f"[CONTEXT UPDATE]\n{content}"}
