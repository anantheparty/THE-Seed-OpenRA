"""Lightweight workflow classification for ordinary managed tasks.

These helpers intentionally stay narrow and repo-grounded. They do not try to
invent a full planner; they only surface and enforce a few high-value bounded
workflow families for ordinary managed tasks.
"""

from __future__ import annotations

import re
from typing import Any, Optional


PRODUCE_UNITS_THEN_RECON = "produce_units_then_recon"
PRODUCE_UNITS_THEN_ATTACK = "produce_units_then_attack"

_RECON_RE = re.compile(r"(探索|探图|侦察|侦查|摸图|找敌人|找基地)")
_ATTACK_RE = re.compile(r"(进攻|攻击|出击|总攻|打一波|打一轮|袭扰|反击)")
_UNIT_RE = re.compile(
    r"(步兵|坦克|兵力|部队|整点兵|整点步兵|来点兵|来点步兵|补兵|补点兵|e1|e3|ftrk|v2rl|3tnk|4tnk|mig|yak|飞机)"
)


def classify_managed_workflow(raw_text: str) -> Optional[str]:
    """Return a bounded workflow family for an ordinary managed task."""
    text = str(raw_text or "").strip().lower()
    if not text:
        return None
    if _RECON_RE.search(text) and _UNIT_RE.search(text):
        return PRODUCE_UNITS_THEN_RECON
    if _ATTACK_RE.search(text) and _UNIT_RE.search(text):
        return PRODUCE_UNITS_THEN_ATTACK
    return None


def workflow_phase(
    workflow: Optional[str],
    *,
    runtime_facts: dict[str, Any] | None = None,
    active_actor_ids: list[int] | None = None,
    jobs: list[dict[str, Any]] | None = None,
) -> Optional[str]:
    """Return a compact current phase label for a bounded workflow."""
    if workflow not in {PRODUCE_UNITS_THEN_RECON, PRODUCE_UNITS_THEN_ATTACK}:
        return None

    actor_ids = list(active_actor_ids or [])
    job_items = list(jobs or [])
    if workflow == PRODUCE_UNITS_THEN_RECON:
        if any(str(job.get("expert_type") or "") == "ReconExpert" for job in job_items):
            return "recon_running"
    if workflow == PRODUCE_UNITS_THEN_ATTACK:
        if any(str(job.get("expert_type") or "") == "CombatExpert" for job in job_items):
            return "attack_running"
    if actor_ids:
        if workflow == PRODUCE_UNITS_THEN_RECON:
            return "ready_to_recon"
        return "ready_to_attack"

    rf = runtime_facts or {}
    requests = list(rf.get("unfulfilled_requests") or [])
    reservations = list(rf.get("unit_reservations") or [])
    if requests or reservations:
        return "waiting_for_units"
    return "request_units_first"


def workflow_block(
    workflow: Optional[str],
    *,
    phase: Optional[str],
) -> str:
    """Render a compact ordinary-task workflow block for the LLM."""
    if workflow == PRODUCE_UNITS_THEN_RECON:
        phase = str(phase or "request_units_first")
        phase_text = {
            "request_units_first": "先请求明确执行单位，不要先开侦察",
            "waiting_for_units": "单位请求/预留已在处理中，等待，不要重复补链或先开侦察",
            "ready_to_recon": "执行单位已到位，可以开始侦察",
            "recon_running": "侦察已开始，优先复用/推进现有侦察动作",
        }.get(phase, phase)

        return (
            "[工作流] template=produce_units_then_recon"
            f" phase={phase} — {phase_text}\n"
            "规则: 未拿到执行单位前，只允许 request_units / wait / send_task_message / query_world；"
            "不要先启动 ReconExpert，也不要补经济/建筑前置。"
        )
    if workflow == PRODUCE_UNITS_THEN_ATTACK:
        phase = str(phase or "request_units_first")
        phase_text = {
            "request_units_first": "先请求明确执行单位，不要先开攻击",
            "waiting_for_units": "单位请求/预留已在处理中，等待，不要重复补链或先开攻击",
            "ready_to_attack": "执行单位已到位，可以开始进攻",
            "attack_running": "进攻已开始，优先复用/推进现有攻击动作",
        }.get(phase, phase)

        return (
            "[工作流] template=produce_units_then_attack"
            f" phase={phase} — {phase_text}\n"
            "规则: 未拿到执行单位前，只允许 request_units / wait / send_task_message / query_world；"
            "不要先启动 CombatExpert，也不要补经济/建筑前置。"
        )
    if workflow is None:
        return ""
    return ""
