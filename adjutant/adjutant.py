"""Adjutant — player's sole dialogue interface (design.md §6).

Routes player input to the correct handler:
  1. Reply to pending question → Kernel.submit_player_response
  2. New command → Kernel.create_task
  3. Query → LLM + WorldModel direct answer

Formats all outbound TaskMessages for player consumption.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from benchmark import span as bm_span
from logging_system import get_logger
from llm import LLMProvider, LLMResponse
from models import (
    CombatJobConfig,
    DeployJobConfig,
    EconomyJobConfig,
    PlayerResponse,
    ReconJobConfig,
    TaskMessage,
    TaskMessageType,
)
from openra_api.models import Actor as GameActor
from openra_api.production_names import normalize_production_name
from task_triage import build_task_triage, capability_blocker_status_text
from unit_registry import UnitRegistry, get_default_registry
from .runtime_nlu import DirectNLUStep, RuntimeNLUDecision, RuntimeNLURouter

logger = logging.getLogger(__name__)
slog = get_logger("adjutant")

_DEPLOY_KEYWORDS = (
    "部署",
    "展开",
    "下基地",
    "开基地",
    "放下mcv",
    "deploy",
)

# Question patterns that should bypass NLU and go to LLM classification
_QUESTION_RE = re.compile(r"(为什么|怎么|怎样|吗\s*[？?。！\s]?$|呢\s*[？?。！\s]?$|什么时候|如何|why|how\b)", re.IGNORECASE)

# Economy/production regex — commands matching merge to EconomyCapability.
# Uses regex instead of keyword set to handle patterns like "爆各种兵".
_ECONOMY_COMMAND_RE = re.compile(
    r"(爆.*兵|扩军|全力生产|停止生产|暂停生产|多造|多建"
    r"|发展|经济|科技|升级"
    r"|没电|缺电|断电|电力不足|电不够|停电|补电"
    r"|造矿车|多挖矿|造矿场|造兵营|造车间|造雷达|造科技|造电厂|建电厂"
    r"|核电|大电|高级电厂|维修厂|修理厂|维修站|修理站"
    r")"
)
# Bare building names as implicit produce (short commands only, not inside queries)
_BARE_BUILDING_NAMES = frozenset({
    "电厂", "兵营", "车间", "矿场", "雷达", "科技中心", "维修厂", "修理厂",
    "核电站", "大电", "狗屋",
})

_INFO_ECONOMY_HINTS = frozenset({"电", "矿", "资源", "经济", "生产", "建", "造", "科技", "补给", "扩张", "前置", "补链", "单位请求", "请求"})
_INFO_COMBAT_HINTS = frozenset({"敌", "打", "攻", "防", "战", "袭", "守", "包围", "前线", "被打", "来袭"})
_INFO_RECON_HINTS = frozenset({"探", "侦", "看", "发现", "位置", "坐标", "左上", "右上", "左下", "右下", "地图"})
_TASK_DOMAIN_HINTS: dict[str, frozenset[str]] = {
    "economy": _INFO_ECONOMY_HINTS,
    "combat": _INFO_COMBAT_HINTS,
    "recon": _INFO_RECON_HINTS,
}


# --- Protocol interfaces ---

class KernelLike(Protocol):
    def create_task(self, raw_text: str, kind: str, priority: int, info_subscriptions: Optional[list] = None) -> Any: ...
    def start_job(self, task_id: str, expert_type: str, config: Any) -> Any: ...
    def submit_player_response(self, response: PlayerResponse, *, now: Optional[float] = None) -> dict[str, Any]: ...
    def list_pending_questions(self) -> list[dict[str, Any]]: ...
    def list_tasks(self) -> list[Any]: ...
    def cancel_task(self, task_id: str) -> bool: ...
    def is_direct_managed(self, task_id: str) -> bool: ...
    def inject_player_message(self, task_id: str, text: str) -> bool: ...
    @property
    def capability_task_id(self) -> Optional[str]: ...


class WorldModelLike(Protocol):
    def world_summary(self) -> dict[str, Any]: ...
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...
    def refresh_health(self) -> dict[str, Any]: ...


# Maps expert type → initial info_subscriptions for the created Task.
_EXPERT_SUBSCRIPTIONS: dict[str, list] = {
    "CombatExpert":    ["threat"],
    "ReconExpert":     ["threat"],
    "MovementExpert":  ["threat"],
    "EconomyExpert":   ["base_state", "production"],
    "DeployExpert":    ["base_state"],
}

# --- Classification result ---

class InputType:
    COMMAND = "command"
    REPLY = "reply"
    QUERY = "query"
    CANCEL = "cancel"
    ACK = "ack"
    INFO = "info"


_ACKNOWLEDGMENT_WORDS: frozenset[str] = frozenset({
    "ok", "好", "好的", "收到", "知道了", "嗯", "行", "明白", "了解",
    "好吧", "是的", "对", "嗯嗯", "哦", "哦哦", "好好", "懂了", "明白了",
    "ok.", "ok!", "好！", "好。",
})


@dataclass
class ClassificationResult:
    input_type: str  # command / reply / query
    confidence: float = 1.0
    target_message_id: Optional[str] = None  # for reply
    target_task_id: Optional[str] = None  # for reply
    disposition: Optional[str] = None  # merge / override / interrupt / new
    raw_text: str = ""


@dataclass
class RuleMatchResult:
    expert_type: str
    config: Any
    reason: str


# --- Adjutant context ---

@dataclass
class AdjutantContext:
    """Minimal context for Adjutant LLM classification (~500-1000 tokens)."""
    active_tasks: list[dict[str, Any]]
    pending_questions: list[dict[str, Any]]
    recent_dialogue: list[dict[str, Any]]
    player_input: str
    recent_completed_tasks: list[dict[str, Any]] = field(default_factory=list)
    coordinator_snapshot: dict[str, Any] = field(default_factory=dict)
    coordinator_hints: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


CLASSIFICATION_SYSTEM_PROMPT = """\
You are the Adjutant (副官) in a real-time strategy game. Your job is to classify player input.

Given the current context (active tasks with triage, pending questions, recent dialogue, recent completed tasks, battlefield snapshot/disposition, battle_groups), classify the input as ONE of:
1. "reply" — the player is answering a pending question from a task
2. "command" — the player is giving a new order/instruction
3. "query" — the player is asking for information (战况, 建议, etc.)
4. "cancel" — the player wants to cancel/stop a currently running task (e.g. "取消任务002", "停止#001", "cancel task 003")
5. "info" — the player is providing intelligence, feedback, or situational updates (e.g. "敌人在左下角", "发现敌人基地了", "被打了", "就在剩下的14%里啊")

Respond with a JSON object:
{"type": "reply"|"command"|"query"|"cancel"|"info", "disposition": "new"|"merge"|"override"|"interrupt"|null, "target_message_id": "<id or null>", "target_task_id": "<label or task_id or null>", "confidence": 0.0-1.0}

Rules:
- If there are pending questions and the input looks like a response, classify as "reply" with the matching message_id
- If ambiguous between reply and command, match to the highest-priority pending question
- Queries ask about game state or advice WITHOUT providing new facts — pure questions ("战况如何?", "电力够吗?")
- Commands are instructions to execute (attack, build, produce, explore, retreat, etc.)
- "info" is for inputs that provide new facts, intelligence, corrections, or situational awareness to the AI — NOT a question and NOT a direct order. E.g.: "敌人基地在左下角", "发现敌人，被打了", "那个方向没有敌人"
- If the input describes an urgent situation (被攻击, 被打了, 发现敌人) but has no explicit action verb, classify as "info" NOT "query"
- "cancel" applies when the player explicitly wants to stop an existing task; set target_task_id to the task label or id mentioned (e.g. "001", "002")
- Active tasks are listed in the context with state/phase/waiting_reason/blocking_reason/active_expert — use this information when deciding whether the player is continuing, interrupting, or redirecting an existing task.
- `coordinator_hints` contains deterministic top-level suggestions derived from current task triage. Use them as strong hints when the input is short, follow-up-like, or ambiguous.
- Use task labels to resolve "取消001" → target_task_id="001"
- For "info" type: set target_task_id to the label of the most relevant active task if one is clearly related

Dialogue context awareness:
- Check recent_completed_tasks for context when the player's input is short or vague.
- If a task recently failed and the player's input seems to be a reaction to that failure (e.g., "那你就建需要的", "你根据需求建造啊"), classify as "command" and understand it as a follow-up to that specific failed task.
- Short ambiguous phrases (e.g., "雷达呢？") that look like queries may actually be commands ("建雷达") when recent context involves building or the player seems to be following up on a task — use recent_completed_tasks and recent_dialogue to decide.
- When input contains both frustration and a command (e.g., "怎么一个都没来？发展科技"), extract and classify by the command portion.
- If recent_completed_tasks shows a "failed" task, lean toward "command" for vague follow-up inputs rather than "query".
"""

QUERY_SYSTEM_PROMPT = """\
You are a game advisor in a real-time strategy game (OpenRA). Answer the player's question about the current game state.

Use the provided world summary and battlefield snapshot to give accurate, concise answers in Chinese.
Focus on actionable information: economy, military strength, map control, enemy activity.
Do not execute any actions — only provide information and suggestions.
"""


@dataclass
class AdjutantConfig:
    default_task_priority: int = 50
    default_task_kind: str = "managed"
    max_dialogue_history: int = 20
    classification_timeout: float = 20.0
    query_timeout: float = 20.0


class Adjutant:
    """Player's sole dialogue interface — routes input, formats output."""

    def __init__(
        self,
        llm: LLMProvider,
        kernel: KernelLike,
        world_model: WorldModelLike,
        game_api: Optional[Any] = None,
        unit_registry: Optional[UnitRegistry] = None,
        config: Optional[AdjutantConfig] = None,
    ) -> None:
        self.llm = llm
        self.kernel = kernel
        self.world_model = world_model
        self.game_api = game_api
        self.unit_registry = unit_registry or get_default_registry()
        self.config = config or AdjutantConfig()
        self._dialogue_history: list[dict[str, Any]] = []
        self._recent_completed: list[dict[str, Any]] = []
        self._pending_sequence: list[Any] = []  # DirectNLUStep items queued for sequential execution
        self._sequence_task_id: str | None = None  # task_id of the currently running sequence step
        self._runtime_nlu = RuntimeNLURouter(unit_registry=self.unit_registry)

    def _get_world_summary(self) -> dict[str, Any]:
        try:
            summary = self.world_model.world_summary()
        except Exception:
            logger.exception("Failed to read world summary")
            return {}
        return summary if isinstance(summary, dict) else {}

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _battlefield_snapshot(self, world_summary: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        query_snapshot = self._safe_world_query("battlefield_snapshot")
        if query_snapshot:
            return {
                "summary": str(query_snapshot.get("summary", "")),
                "disposition": query_snapshot.get("disposition", "unknown"),
                "focus": query_snapshot.get("focus", "general"),
                "self_units": int(self._coerce_float(query_snapshot.get("self_units")) or 0),
                "enemy_units": int(self._coerce_float(query_snapshot.get("enemy_units")) or 0),
                "self_combat_value": round(self._coerce_float(query_snapshot.get("self_combat_value")) or 0.0, 2),
                "enemy_combat_value": round(self._coerce_float(query_snapshot.get("enemy_combat_value")) or 0.0, 2),
                "idle_self_units": int(self._coerce_float(query_snapshot.get("idle_self_units")) or 0),
                "low_power": bool(query_snapshot.get("low_power")),
                "queue_blocked": bool(query_snapshot.get("queue_blocked")),
                "recommended_posture": str(query_snapshot.get("recommended_posture", "maintain_posture")),
                "threat_level": str(query_snapshot.get("threat_level", "unknown")),
                "threat_direction": str(query_snapshot.get("threat_direction", "unknown")),
                "base_under_attack": bool(query_snapshot.get("base_under_attack")),
                "base_health_summary": str(query_snapshot.get("base_health_summary", "")),
                "has_production": bool(query_snapshot.get("has_production")),
                "explored_pct": self._coerce_float(query_snapshot.get("explored_pct")),
                "enemy_bases": int(self._coerce_float(query_snapshot.get("enemy_bases")) or 0),
                "enemy_spotted": int(self._coerce_float(query_snapshot.get("enemy_spotted")) or 0),
                "frozen_enemy_count": int(self._coerce_float(query_snapshot.get("frozen_enemy_count")) or 0),
                "pending_request_count": int(self._coerce_float(query_snapshot.get("pending_request_count")) or 0),
                "bootstrapping_request_count": int(self._coerce_float(query_snapshot.get("bootstrapping_request_count")) or 0),
                "reservation_count": int(self._coerce_float(query_snapshot.get("reservation_count")) or 0),
                "stale": bool(query_snapshot.get("stale")),
            }

        summary = world_summary if isinstance(world_summary, dict) else self._get_world_summary()
        economy = summary.get("economy", {}) if isinstance(summary, dict) else {}
        military = summary.get("military", {}) if isinstance(summary, dict) else {}
        game_map = summary.get("map", {}) if isinstance(summary, dict) else {}
        known_enemy = summary.get("known_enemy", {}) if isinstance(summary, dict) else {}

        self_units = int(self._coerce_float(military.get("self_units")) or 0)
        enemy_units = int(self._coerce_float(military.get("enemy_units")) or 0)
        self_combat_value = self._coerce_float(military.get("self_combat_value"))
        enemy_combat_value = self._coerce_float(military.get("enemy_combat_value"))
        idle_self_units = int(self._coerce_float(military.get("idle_self_units")) or 0)
        low_power = bool(economy.get("low_power"))
        queue_blocked = bool(economy.get("queue_blocked"))
        explored_pct = self._coerce_float(game_map.get("explored_pct"))
        enemy_bases = int(self._coerce_float(known_enemy.get("bases")) or self._coerce_float(known_enemy.get("structures")) or 0)
        enemy_spotted = int(self._coerce_float(known_enemy.get("units_spotted")) or 0)
        frozen_count = int(self._coerce_float(known_enemy.get("frozen_count")) or 0)

        combat_known = self_combat_value is not None or enemy_combat_value is not None
        if combat_known:
            self_score = self_combat_value or 0.0
            enemy_score = enemy_combat_value or 0.0
        else:
            self_score = float(self_units)
            enemy_score = float(enemy_units)

        if self_score == 0 and enemy_score == 0 and self_units == 0 and enemy_units == 0:
            disposition = "unknown"
        elif (enemy_score >= max(self_score * 1.2, self_score + 1)) or (enemy_units >= max(self_units * 1.2, self_units + 1)):
            disposition = "under_pressure"
        elif (self_score >= max(enemy_score * 1.2, enemy_score + 1)) or (self_units >= max(enemy_units * 1.2, enemy_units + 1)):
            disposition = "advantage"
        elif low_power or queue_blocked:
            disposition = "stalled"
        else:
            disposition = "stable"

        if disposition == "under_pressure":
            focus = "defense"
        elif disposition == "advantage":
            focus = "attack"
        elif low_power or queue_blocked:
            focus = "economy"
        elif enemy_bases or enemy_spotted or frozen_count:
            focus = "recon"
        else:
            focus = "general"

        summary_text = (
            f"我方 {self_units} 单位 / 敌方 {enemy_units} 单位，"
            f"战斗值 {self_score:.0f} / {enemy_score:.0f}，"
            f"探索 {explored_pct * 100:.1f}%"
            if explored_pct is not None
            else f"我方 {self_units} 单位 / 敌方 {enemy_units} 单位，战斗值 {self_score:.0f} / {enemy_score:.0f}"
        )
        if low_power:
            summary_text += "，当前低电"
        if queue_blocked:
            summary_text += "，生产队列阻塞"

        return {
            "summary": summary_text,
            "disposition": disposition,
            "focus": focus,
            "self_units": self_units,
            "enemy_units": enemy_units,
            "self_combat_value": round(self_score, 2),
            "enemy_combat_value": round(enemy_score, 2),
            "idle_self_units": idle_self_units,
            "low_power": low_power,
            "queue_blocked": queue_blocked,
            "recommended_posture": "stabilize_power" if low_power else ("unblock_queue" if queue_blocked else "maintain_posture"),
            "threat_level": "unknown",
            "threat_direction": "unknown",
            "base_under_attack": False,
            "base_health_summary": "",
            "has_production": False,
            "explored_pct": explored_pct,
            "enemy_bases": enemy_bases,
            "enemy_spotted": enemy_spotted,
            "frozen_enemy_count": frozen_count,
            "pending_request_count": 0,
            "bootstrapping_request_count": 0,
            "reservation_count": 0,
            "stale": False,
        }

    @staticmethod
    def _task_text(task: Any) -> str:
        return str(getattr(task, "raw_text", "") or "").strip().lower()

    @staticmethod
    def _task_label(task: Any) -> str:
        return str(getattr(task, "label", "") or "")

    def _classify_text_domain(self, text: str) -> str:
        normalized = text.lower()
        if any(hint in normalized for hint in _INFO_COMBAT_HINTS):
            return "combat"
        if any(hint in normalized for hint in _INFO_ECONOMY_HINTS):
            return "economy"
        if any(hint in normalized for hint in _INFO_RECON_HINTS):
            return "recon"
        return "general"

    def _task_domain(self, task_text: str) -> str:
        if any(hint in task_text for hint in _INFO_COMBAT_HINTS):
            return "combat"
        if any(hint in task_text for hint in _INFO_ECONOMY_HINTS):
            return "economy"
        if any(hint in task_text for hint in _INFO_RECON_HINTS):
            return "recon"
        return "general"

    def _score_info_target(self, text: str, task: Any, battlefield_snapshot: dict[str, Any]) -> int:
        task_text = self._task_text(task)
        if not task_text:
            return 0

        text_domain = self._classify_text_domain(text)
        task_domain = self._task_domain(task_text)
        score = 0

        if text_domain != "general":
            score += 3 if text_domain == task_domain else -1
        focus = battlefield_snapshot.get("focus", "general")
        disposition = battlefield_snapshot.get("disposition", "unknown")
        if focus == task_domain:
            score += 2
        elif focus != "general" and task_domain != "general":
            score += 1
        if disposition == "under_pressure" and task_domain == "combat":
            score += 2
        if disposition in {"advantage", "stable"} and task_domain == "combat" and text_domain == "combat":
            score += 1
        if disposition in {"stalled", "under_pressure"} and task_domain == "economy" and text_domain == "economy":
            score += 1
        if task_domain == "general" and text_domain != "general":
            score -= 1

        overlap = sum(1 for hint in _TASK_DOMAIN_HINTS.get(text_domain, frozenset()) if hint in task_text)
        score += min(overlap, 3)
        return score

    def _select_info_target_task(
        self,
        text: str,
        classification: ClassificationResult,
        context: AdjutantContext,
        battlefield_snapshot: dict[str, Any],
    ) -> Optional[Any]:
        if classification.target_task_id:
            target = self._find_task_by_label(classification.target_task_id)
            if target is not None:
                return target

        tasks = self.kernel.list_tasks()
        terminal = {"succeeded", "failed", "aborted", "partial"}
        active_tasks = [
            task for task in tasks
            if getattr(task, "status", None) is not None and getattr(task.status, "value", "") not in terminal
        ]
        if not active_tasks:
            return None

        scored: list[tuple[int, int, Any]] = []
        for index, task in enumerate(active_tasks):
            if getattr(task, "task_id", None) is None:
                continue
            score = self._score_info_target(text, task, battlefield_snapshot)
            if score <= 0:
                continue
            scored.append((score, -index, task))

        if not scored:
            recent_task = self._find_overlapping_task(text)
            return recent_task

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return scored[0][2]

    def _format_query_snapshot(self, battlefield_snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": battlefield_snapshot.get("summary", ""),
            "disposition": battlefield_snapshot.get("disposition", "unknown"),
            "focus": battlefield_snapshot.get("focus", "general"),
            "self_units": battlefield_snapshot.get("self_units", 0),
            "enemy_units": battlefield_snapshot.get("enemy_units", 0),
            "self_combat_value": battlefield_snapshot.get("self_combat_value", 0),
            "enemy_combat_value": battlefield_snapshot.get("enemy_combat_value", 0),
            "idle_self_units": battlefield_snapshot.get("idle_self_units", 0),
            "low_power": battlefield_snapshot.get("low_power", False),
            "queue_blocked": battlefield_snapshot.get("queue_blocked", False),
            "recommended_posture": battlefield_snapshot.get("recommended_posture", "maintain_posture"),
            "threat_level": battlefield_snapshot.get("threat_level", "unknown"),
            "threat_direction": battlefield_snapshot.get("threat_direction", "unknown"),
            "base_under_attack": battlefield_snapshot.get("base_under_attack", False),
            "base_health_summary": battlefield_snapshot.get("base_health_summary", ""),
            "has_production": battlefield_snapshot.get("has_production", False),
            "explored_pct": battlefield_snapshot.get("explored_pct"),
            "enemy_bases": battlefield_snapshot.get("enemy_bases", 0),
            "enemy_spotted": battlefield_snapshot.get("enemy_spotted", 0),
            "frozen_enemy_count": battlefield_snapshot.get("frozen_enemy_count", 0),
            "pending_request_count": battlefield_snapshot.get("pending_request_count", 0),
            "bootstrapping_request_count": battlefield_snapshot.get("bootstrapping_request_count", 0),
            "reservation_count": battlefield_snapshot.get("reservation_count", 0),
        }

    @staticmethod
    def _build_task_overview(active_tasks: list[dict[str, Any]]) -> dict[str, Any]:
        counts_by_state: dict[str, int] = {}
        counts_by_domain: dict[str, int] = {}
        running_labels: list[str] = []
        waiting_labels: list[str] = []
        reservation_wait_labels: list[str] = []
        combat_groups = 0
        recon_groups = 0
        busiest_label = ""
        busiest_group_size = 0

        for task in active_tasks:
            state = str(task.get("state", "") or "unknown")
            domain = str(task.get("domain", "") or "general")
            label = str(task.get("label", "") or "")
            active_group_size = int(task.get("active_group_size", 0) or 0)

            counts_by_state[state] = counts_by_state.get(state, 0) + 1
            counts_by_domain[domain] = counts_by_domain.get(domain, 0) + 1

            if state == "running" and label:
                running_labels.append(label)
            if state == "waiting" and label:
                waiting_labels.append(label)
            if state == "waiting_units" and label:
                reservation_wait_labels.append(label)
            if domain == "combat" and active_group_size > 0:
                combat_groups += 1
            if domain == "recon" and active_group_size > 0:
                recon_groups += 1
            if active_group_size > busiest_group_size and label:
                busiest_group_size = active_group_size
                busiest_label = label

        return {
            "active_count": len(active_tasks),
            "running_count": counts_by_state.get("running", 0),
            "waiting_count": counts_by_state.get("waiting", 0),
            "reservation_wait_count": counts_by_state.get("waiting_units", 0),
            "blocked_count": counts_by_state.get("blocked", 0),
            "degraded_count": counts_by_state.get("degraded", 0),
            "counts_by_state": counts_by_state,
            "counts_by_domain": counts_by_domain,
            "combat_group_count": combat_groups,
            "recon_group_count": recon_groups,
            "running_labels": running_labels[:5],
            "waiting_labels": waiting_labels[:5],
            "reservation_wait_labels": reservation_wait_labels[:5],
            "largest_group_label": busiest_label,
            "largest_group_size": busiest_group_size,
        }

    @staticmethod
    def _build_battle_groups(active_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for task in active_tasks:
            domain = str(task.get("domain", "") or "")
            if domain not in {"combat", "recon"}:
                continue
            if int(task.get("active_group_size", 0) or 0) <= 0 and task.get("state") not in {"waiting_units", "running"}:
                continue
            groups.append({
                "label": str(task.get("label", "") or ""),
                "task_id": str(task.get("task_id", "") or ""),
                "domain": domain,
                "state": str(task.get("state", "") or "unknown"),
                "phase": str(task.get("phase", "") or ""),
                "active_expert": str(task.get("active_expert", "") or ""),
                "active_group_size": int(task.get("active_group_size", 0) or 0),
                "waiting_reason": str(task.get("waiting_reason", "") or ""),
                "blocking_reason": str(task.get("blocking_reason", "") or ""),
                "status_line": str(task.get("status_line", "") or ""),
            })
        groups.sort(key=lambda item: (-item["active_group_size"], item["domain"], item["label"]))
        return groups[:6]

    def _safe_world_query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            result = self.world_model.query(query_type, params)
        except Exception:
            logger.exception("Adjutant failed world query: %s", query_type)
            return {}
        return result if isinstance(result, dict) else {}

    def _coordinator_snapshot(self) -> dict[str, Any]:
        battlefield = self._format_query_snapshot(self._battlefield_snapshot())
        runtime_state = self._safe_world_query("runtime_state")
        capability_status = dict(runtime_state.get("capability_status") or {})
        runtime_facts: dict[str, Any] = {}
        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if callable(compute_runtime_facts):
            try:
                runtime_facts = compute_runtime_facts("__adjutant__", include_buildable=False) or {}
            except Exception:
                logger.exception("Adjutant failed to compute coordinator runtime facts")
                runtime_facts = {}
        base_state = {
            "has_construction_yard": runtime_facts.get("has_construction_yard", False),
            "mcv_count": runtime_facts.get("mcv_count", 0),
            "mcv_idle": runtime_facts.get("mcv_idle", False),
            "power_plant_count": runtime_facts.get("power_plant_count", 0),
            "refinery_count": runtime_facts.get("refinery_count", 0),
            "barracks_count": runtime_facts.get("barracks_count", 0),
            "war_factory_count": runtime_facts.get("war_factory_count", 0),
            "radar_count": runtime_facts.get("radar_count", 0),
            "repair_facility_count": runtime_facts.get("repair_facility_count", 0),
            "airfield_count": runtime_facts.get("airfield_count", 0),
            "tech_center_count": runtime_facts.get("tech_center_count", 0),
            "harvester_count": runtime_facts.get("harvester_count", 0),
            "low_power": battlefield.get("low_power", False),
            "queue_blocked": battlefield.get("queue_blocked", False),
        }
        info_experts = dict(runtime_facts.get("info_experts") or {})
        return {
            "battlefield": battlefield,
            "base_state": base_state,
            "capability": {
                "task_id": capability_status.get("task_id"),
                "label": capability_status.get("label") or capability_status.get("task_label"),
                "status": capability_status.get("status"),
                "phase": capability_status.get("phase"),
                "blocker": capability_status.get("blocker"),
                "active_job_types": list(capability_status.get("active_job_types", []) or []),
                "pending_request_count": int(capability_status.get("pending_request_count", 0) or 0),
                "dispatch_request_count": int(capability_status.get("dispatch_request_count", 0) or 0),
                "bootstrapping_request_count": int(capability_status.get("bootstrapping_request_count", 0) or 0),
                "start_released_request_count": int(capability_status.get("start_released_request_count", 0) or 0),
                "reinforcement_request_count": int(capability_status.get("reinforcement_request_count", 0) or 0),
                "blocking_request_count": int(capability_status.get("blocking_request_count", 0) or 0),
                "inference_pending_count": int(capability_status.get("inference_pending_count", 0) or 0),
                "prerequisite_gap_count": int(capability_status.get("prerequisite_gap_count", 0) or 0),
                "recent_directives": list(capability_status.get("recent_directives", []) or []),
            },
            "info_experts": {
                "threat_level": battlefield.get("threat_level") or info_experts.get("threat_level"),
                "threat_direction": battlefield.get("threat_direction") or info_experts.get("threat_direction"),
                "enemy_count": info_experts.get("enemy_count"),
                "base_under_attack": battlefield.get("base_under_attack"),
                "base_health_summary": battlefield.get("base_health_summary") or info_experts.get("base_health_summary"),
                "has_production": battlefield.get("has_production"),
            },
            "recommended_posture": battlefield.get("recommended_posture", "maintain_posture"),
            "world_sync": self.world_model.refresh_health(),
            "active_task_count": len(runtime_state.get("active_tasks", {}) or {}),
            "reservation_count": battlefield.get("reservation_count", len(runtime_state.get("unit_reservations", []) or [])),
        }

    @staticmethod
    def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
        normalized = text.lower()
        return any(token in normalized for token in tokens)

    def _coordinator_hints(self, player_input: str, active_tasks: list[dict[str, Any]], battlefield: dict[str, Any]) -> dict[str, Any]:
        text = player_input.strip()
        if not text or not active_tasks:
            return {}

        text_domain = self._classify_text_domain(text)
        continuation_tokens = ("继续", "再", "顺便", "然后", "接着", "补", "优先", "先")
        override_tokens = ("改", "换", "别", "不要", "停止", "改成", "转去", "转向", "撤", "退")
        interrupt_tokens = ("立刻", "马上", "紧急", "火速")
        is_follow_up = self._has_any_token(text, continuation_tokens + override_tokens + interrupt_tokens)

        scored: list[tuple[int, dict[str, Any]]] = []
        for task in active_tasks:
            task_domain = str(task.get("domain", "general") or "general")
            if text_domain != "general" and task_domain != text_domain:
                continue
            score = 0
            if text_domain != "general" and task_domain == text_domain:
                score += 3
            if task.get("state") in {"waiting_units", "waiting", "running"}:
                score += 2
            if task.get("is_capability") and text_domain == "economy":
                score += 2
            if int(task.get("active_group_size", 0) or 0) > 0 and text_domain in {"combat", "recon"}:
                score += 1
            if score > 0:
                scored.append((score, task))

        best_task: Optional[dict[str, Any]] = None
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            best_task = scored[0][1]

        suggested_disposition: Optional[str] = None
        reason = ""
        if self._has_any_token(text, interrupt_tokens) and text_domain == "combat" and battlefield.get("base_under_attack"):
            suggested_disposition = "interrupt"
            reason = "urgent_combat_under_pressure"
        elif best_task is not None:
            task_blocking_reason = str(best_task.get("blocking_reason", "") or "")
            task_phase = str(best_task.get("phase", "") or "")
            capability_followup = bool(best_task.get("is_capability")) and task_blocking_reason in {
                "missing_prerequisite",
                "request_inference_pending",
            }
            capability_phase_followup = bool(best_task.get("is_capability")) and task_phase in {
                "dispatch",
                "bootstrapping",
                "fulfilling",
            }
            if self._has_any_token(text, override_tokens):
                suggested_disposition = "override"
                reason = "followup_override"
            elif capability_followup and (is_follow_up or text_domain == "economy"):
                suggested_disposition = "merge"
                reason = f"capability_followup_{task_blocking_reason}"
            elif capability_phase_followup and (is_follow_up or text_domain == "economy"):
                suggested_disposition = "merge"
                reason = f"capability_phase_{task_phase}"
            elif is_follow_up or text_domain != "general":
                suggested_disposition = "merge"
                reason = "followup_merge"

        if best_task is None and suggested_disposition != "interrupt":
            return {}

        return {
            "text_domain": text_domain,
            "suggested_disposition": suggested_disposition,
            "likely_target_task_id": str(best_task.get("task_id", "")) if best_task is not None else "",
            "likely_target_label": str(best_task.get("label", "")) if best_task is not None else "",
            "likely_target_domain": str(best_task.get("domain", "")) if best_task is not None else "",
            "likely_target_state": str(best_task.get("state", "")) if best_task is not None else "",
            "reason": reason,
        }

    def _apply_coordinator_hints(self, classification: ClassificationResult, context: AdjutantContext) -> ClassificationResult:
        hints = context.coordinator_hints or {}
        if not hints:
            return classification

        target_label = str(hints.get("likely_target_label", "") or "")
        suggested_disposition = str(hints.get("suggested_disposition", "") or "").lower()

        if classification.input_type == InputType.INFO and not classification.target_task_id and target_label:
            classification.target_task_id = target_label
            return classification

        if classification.input_type != InputType.COMMAND:
            return classification

        if not classification.target_task_id and target_label:
            classification.target_task_id = target_label

        if not classification.disposition and suggested_disposition in {"merge", "override", "interrupt"}:
            classification.disposition = suggested_disposition

        return classification

    @staticmethod
    def _task_status_line(task_entry: dict[str, Any], capability_status: dict[str, Any]) -> str:
        if task_entry.get("is_capability"):
            active_job_types = list(capability_status.get("active_job_types", []) or [])
            pending_request_count = int(capability_status.get("pending_request_count", 0) or 0)
            blocking_request_count = int(capability_status.get("blocking_request_count", 0) or 0)
            start_released_request_count = int(capability_status.get("start_released_request_count", 0) or 0)
            reinforcement_request_count = int(capability_status.get("reinforcement_request_count", 0) or 0)
            phase = str(capability_status.get("phase", "") or "")
            blocker = str(capability_status.get("blocker", "") or "")
            status = task_entry.get("status", "running")
            parts = [f"能力任务 {status}"]
            if phase:
                parts.append(f"phase={phase}")
            if active_job_types:
                parts.append(f"job={','.join(active_job_types[:3])}")
            if pending_request_count:
                parts.append(f"pending={pending_request_count}")
            if blocking_request_count:
                parts.append(f"blocking={blocking_request_count}")
            if start_released_request_count:
                parts.append(f"ready={start_released_request_count}")
            if reinforcement_request_count:
                parts.append(f"reinforce={reinforcement_request_count}")
            if blocker:
                parts.append(f"blocker={blocker}")
            return " | ".join(parts)
        active_group_size = int(task_entry.get("active_group_size", 0) or 0)
        status = str(task_entry.get("status", "running"))
        if active_group_size > 0:
            return f"{status} | group={active_group_size}"
        return status

    @staticmethod
    def _derive_task_triage(
        task: Any,
        runtime_task: dict[str, Any],
        runtime_state: dict[str, Any],
        capability_status: dict[str, Any],
        world_sync: dict[str, Any],
    ) -> dict[str, Any]:
        runtime_state = dict(runtime_state or {})
        if capability_status:
            runtime_state["capability_status"] = dict(capability_status)
        return build_task_triage(
            task=task,
            runtime_task=runtime_task,
            runtime_state=runtime_state,
            world_sync=world_sync,
        )

    # --- Main entry point ---

    async def handle_player_input(self, text: str) -> dict[str, Any]:
        """Process player input and return a response dict.

        Returns:
            {"type": "command"|"reply"|"query", "response": ..., "timestamp": ...}
        """
        with bm_span("llm_call", name="adjutant:handle_input"):
            slog.info("Handling player input", event="player_input", text=text)
            if text.strip().lower().rstrip(".,！。") in _ACKNOWLEDGMENT_WORDS:
                # If there are pending questions, the ack is likely a reply — let normal flow handle it
                if not self.kernel.list_pending_questions():
                    self._record_dialogue("player", text)
                    self._record_dialogue("adjutant", "收到")
                    return {"type": InputType.ACK, "ok": True, "response_text": "收到", "timestamp": time.time()}
            deploy_feedback = self._maybe_handle_deploy_feedback(text)
            if deploy_feedback is not None:
                slog.info(
                    "Deploy feedback short-circuit",
                    event="deploy_feedback_shortcircuit",
                    ok=deploy_feedback.get("ok"),
                    reason=deploy_feedback.get("reason"),
                )
                self._record_dialogue("player", text)
                if deploy_feedback.get("response_text"):
                    self._record_dialogue("adjutant", deploy_feedback["response_text"])
                deploy_feedback["timestamp"] = time.time()
                return deploy_feedback
            if self._world_sync_is_stale() and self._looks_like_query(text) and not self.kernel.list_pending_questions():
                result = self._stale_world_guard("query")
                slog.info(
                    "Stale world guard short-circuit",
                    event="stale_world_guard",
                    input_type="query",
                    raw_text=text,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            # Economy commands → merge to EconomyCapability (before NLU, so "爆兵" etc. go to Capability)
            if self._is_economy_command(text):
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="capability_early",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                cap_result = self._try_merge_to_capability(text)
                if cap_result is not None:
                    self._record_dialogue("player", text)
                    if cap_result.get("response_text"):
                        self._record_dialogue("adjutant", cap_result["response_text"])
                    cap_result["timestamp"] = time.time()
                    return cap_result

            runtime_nlu = self._try_runtime_nlu(text)
            if runtime_nlu is not None:
                if self._world_sync_is_stale():
                    response_kind = "query" if runtime_nlu.route_intent == "query_actor" else "command"
                    result = self._stale_world_guard(response_kind)
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type=response_kind,
                        raw_text=text,
                        source="runtime_nlu",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_runtime_nlu(text, runtime_nlu)
                slog.info(
                    "NLU route result",
                    event="route_result",
                    routing="nlu",
                    ok=result.get("ok"),
                    steps=len(runtime_nlu.steps),
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            rule_match = self._try_rule_match(text)
            if rule_match is not None:
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="rule",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_rule_command(text, rule_match)
                slog.info(
                    "Rule route result",
                    event="route_result",
                    routing="rule",
                    ok=result.get("ok"),
                    expert_type=rule_match.expert_type,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            # Build context
            context = self._build_context(text)

            # Classify input
            classification = await self._classify_input(context)
            classification = self._apply_coordinator_hints(classification, context)
            slog.info(
                "Classified player input",
                event="input_classified",
                input_type=classification.input_type,
                confidence=classification.confidence,
                target_message_id=classification.target_message_id,
                target_task_id=classification.target_task_id,
            )

            # Route based on classification
            if classification.input_type == InputType.CANCEL:
                slog.info("Routing to cancel handler", event="route_decision", input_type=InputType.CANCEL,
                          target_label=classification.target_task_id)
                result = await self._handle_cancel(classification)
            elif classification.input_type == InputType.REPLY:
                slog.info(
                    "Routing to reply handler",
                    event="route_decision",
                    input_type=InputType.REPLY,
                    message_id=classification.target_message_id,
                    task_id=classification.target_task_id,
                )
                result = await self._handle_reply(classification)
                # Fallback: if reply had no target (no pending question), treat as command
                if not result.get("ok") and result.get("response_text") == "没有待回答的问题":
                    slog.info("Reply had no target, falling back to command", event="reply_fallback_to_command")
                    result = await self._handle_command(text)
            elif classification.input_type == InputType.INFO:
                slog.info("Routing to info handler", event="route_decision", input_type=InputType.INFO,
                          target_task_id=classification.target_task_id)
                result = await self._handle_info(text, classification, context)
            elif classification.input_type == InputType.QUERY:
                if self._world_sync_is_stale():
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="query",
                        raw_text=text,
                        source="classification",
                    )
                    result = self._stale_world_guard("query")
                else:
                    slog.info("Routing to query handler", event="route_decision", input_type=InputType.QUERY)
                    result = await self._handle_query(text, context)
            else:
                if self._world_sync_is_stale():
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="classification",
                    )
                    result = self._stale_world_guard("command")
                else:
                    slog.info("Routing to command handler", event="route_decision", input_type=InputType.COMMAND)
                    if classification.disposition in {"merge", "override", "interrupt"}:
                        result = await self._handle_command_with_disposition(text, classification, context)
                    else:
                        result = await self._handle_command(text)

            # Record in dialogue history
            self._record_dialogue("player", text)
            if result.get("response_text"):
                self._record_dialogue("adjutant", result["response_text"])

            result["timestamp"] = time.time()
            return result

    def _try_rule_match(self, text: str) -> Optional[RuleMatchResult]:
        normalized = re.sub(r"\s+", "", text.strip())
        if not normalized:
            return None
        if self._looks_like_query(normalized):
            return None
        if any(token in normalized for token in ("然后", "之后", "并且", "同时", "别", "不要", "如果", "优先")):
            return None

        deploy = self._match_deploy(normalized)
        if deploy is not None:
            return deploy

        build = self._match_build(normalized)
        if build is not None:
            return build

        production = self._match_production(normalized)
        if production is not None:
            return production

        recon = self._match_recon(normalized)
        if recon is not None:
            return recon

        return None

    def _try_runtime_nlu(self, text: str) -> Optional[RuntimeNLUDecision]:
        # Questions should not be routed as commands regardless of NLU confidence
        if _QUESTION_RE.search(text.strip()):
            return None
        try:
            decision = self._runtime_nlu.route(text)
        except Exception:
            logger.exception("Runtime NLU routing failed: %r", text)
            return None
        if decision is None:
            return None
        slog.info(
            "Adjutant runtime NLU matched",
            event="nlu_routed_command",
            raw_text=text,
            source=decision.source,
            route_intent=decision.route_intent,
            intent=decision.intent,
            confidence=decision.confidence,
            risk_level=decision.risk_level,
            step_count=len(decision.steps),
            reason=decision.reason,
        )
        return decision

    @staticmethod
    def _looks_like_query(text: str) -> bool:
        query_keywords = ("？", "?", "如何", "怎么", "为什么", "战况", "建议", "分析", "多少", "几个", "哪里", "什么")
        return any(keyword in text for keyword in query_keywords)

    def _maybe_handle_deploy_feedback(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", text.strip())
        if "基地车" not in normalized:
            return None
        if not self._looks_like_deploy_command(normalized):
            return None
        if self._looks_like_query(normalized):
            return None
        if self._looks_like_complex_command(normalized):
            return None
        if self._world_sync_is_stale():
            return {
                "type": "command",
                "ok": False,
                "response_text": "当前游戏状态同步异常，请稍后重试",
                "routing": "rule",
                "reason": "world_sync_stale",
            }

        payload = self.world_model.query("my_actors", {"category": "mcv"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if actors:
            return None

        base_payload = self.world_model.query("my_actors", {"type": "建造厂"})
        bases = list((base_payload or {}).get("actors", [])) if isinstance(base_payload, dict) else []
        if bases:
            return {
                "type": "command",
                "ok": True,
                "response_text": "建造厂已存在，当前无基地车可部署",
                "routing": "rule",
                "reason": "rule_deploy_already_deployed",
            }

        return {
            "type": "command",
            "ok": False,
            "response_text": "当前没有可部署的基地车",
            "routing": "rule",
            "reason": "rule_deploy_missing_mcv",
        }

    @staticmethod
    def _looks_like_complex_command(normalized_text: str) -> bool:
        return any(token in normalized_text for token in ("然后", "之后", "并且", "同时", "别", "不要", "如果", "优先"))

    def _match_deploy(self, normalized: str) -> Optional[RuleMatchResult]:
        if "基地车" not in normalized:
            return None
        if not self._looks_like_deploy_command(normalized):
            return None
        payload = self.world_model.query("my_actors", {"category": "mcv"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            return None
        actor = actors[0]
        position = tuple(actor.get("position") or [0, 0])
        return RuleMatchResult(
            expert_type="DeployExpert",
            config=DeployJobConfig(actor_id=int(actor["actor_id"]), target_position=position),
            reason="rule_deploy_mcv",
        )

    @staticmethod
    def _looks_like_deploy_command(normalized: str) -> bool:
        lowered = normalized.lower()
        return any(keyword in normalized or keyword in lowered for keyword in _DEPLOY_KEYWORDS)

    def _world_sync_is_stale(self) -> bool:
        refresh_health = getattr(self.world_model, "refresh_health", None)
        if not callable(refresh_health):
            return False
        try:
            health = refresh_health() or {}
        except Exception:
            logger.exception("Failed to read world refresh health")
            return False
        return bool(health.get("stale"))

    @staticmethod
    def _stale_world_response_text(kind: str) -> str:
        if kind == "query":
            return "当前游戏状态同步异常，暂时无法可靠回答，请稍后重试"
        return "当前游戏状态同步异常，已暂停执行以避免基于旧状态误操作，请稍后重试"

    def _stale_world_guard(self, kind: str) -> dict[str, Any]:
        return {
            "type": kind,
            "ok": False,
            "response_text": self._stale_world_response_text(kind),
            "routing": "stale_guard",
            "reason": "world_sync_stale",
        }

    def _fallback_query_answer(self, world_summary: dict[str, Any]) -> str:
        battlefield_snapshot = self._battlefield_snapshot(world_summary)
        economy = world_summary.get("economy", {}) if isinstance(world_summary, dict) else {}
        military = world_summary.get("military", {}) if isinstance(world_summary, dict) else {}
        game_map = world_summary.get("map", {}) if isinstance(world_summary, dict) else {}
        known_enemy = world_summary.get("known_enemy", {}) if isinstance(world_summary, dict) else {}

        cash = economy.get("cash", economy.get("total_credits", "?"))
        low_power = bool(economy.get("low_power"))
        self_units = military.get("self_units", "?")
        enemy_units = military.get("enemy_units", "?")
        enemy_bases = known_enemy.get("bases", known_enemy.get("structures", 0))
        explored = game_map.get("explored_pct", 0.0)
        try:
            explored_pct = f"{float(explored) * 100:.1f}%"
        except (TypeError, ValueError):
            explored_pct = "未知"

        low_power_note = "，当前低电" if low_power else ""
        return (
            f"当前缓存战况：资金 {cash}{low_power_note}；"
            f"我方单位 {self_units}，敌方单位 {enemy_units}；"
            f"已探索 {explored_pct}，已知敌方基地 {enemy_bases}。"
            f"战场态势 {battlefield_snapshot.get('disposition', 'unknown')}，"
            f"当前重点 {battlefield_snapshot.get('focus', 'general')}。"
            "LLM 当前超时，这是基于最新缓存世界状态的摘要。"
        )

    def _match_build(self, normalized: str) -> Optional[RuleMatchResult]:
        if not normalized.startswith(("建造", "修建", "造")):
            return None
        entry = self.unit_registry.match_in_text(normalized, queue_types=("Building", "Defense"))
        if entry is not None:
            return RuleMatchResult(
                expert_type="EconomyExpert",
                config=EconomyJobConfig(
                    unit_type=normalize_production_name(entry.unit_id),
                    count=1,
                    queue_type=entry.queue_type,
                    repeat=False,
                ),
                reason="rule_build_structure",
            )
        return None

    def _match_production(self, normalized: str) -> Optional[RuleMatchResult]:
        if normalized.startswith(("建造", "修建")):
            return None
        if not any(token in normalized for token in ("生产", "造", "训练", "补")):
            return None
        canonical = self._resolve_production_target(normalized)
        if canonical is None:
            return None
        unit_type, queue_type = canonical
        count = self._extract_requested_count(normalized)
        return RuleMatchResult(
            expert_type="EconomyExpert",
            config=EconomyJobConfig(unit_type=unit_type, count=count, queue_type=queue_type, repeat=False),
            reason="rule_production",
        )

    def _match_recon(self, normalized: str) -> Optional[RuleMatchResult]:
        if any(token in normalized for token in ("探索", "侦察", "找敌人", "找基地")):
            return RuleMatchResult(
                expert_type="ReconExpert",
                config=ReconJobConfig(
                    search_region="enemy_half",
                    target_type="base",
                    target_owner="enemy",
                    retreat_hp_pct=0.3,
                    avoid_combat=True,
                ),
                reason="rule_recon",
            )
        return None

    @staticmethod
    def _extract_requested_count(normalized: str) -> int:
        match = re.search(r"(\d+)", normalized)
        if match:
            return max(1, int(match.group(1)))
        chinese_digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if "十" in normalized:
            left, _, right = normalized.partition("十")
            tens = chinese_digits.get(left, 1 if left == "" else 0)
            ones = chinese_digits.get(right[:1], 0)
            value = tens * 10 + ones
            if value > 0:
                return value
        for char in normalized:
            if char in chinese_digits and chinese_digits[char] > 0:
                return chinese_digits[char]
        return 1

    def _resolve_production_target(self, normalized: str) -> Optional[tuple[str, str]]:
        entry = self.unit_registry.match_in_text(normalized, queue_types=("Infantry", "Vehicle", "Aircraft", "Ship"))
        if entry is not None:
            return (normalize_production_name(entry.unit_id), entry.queue_type)
        normalized_text = normalize_production_name(normalized)
        entry = self.unit_registry.match_in_text(normalized_text, queue_types=("Infantry", "Vehicle", "Aircraft", "Ship"))
        if entry is not None:
            return (normalize_production_name(entry.unit_id), entry.queue_type)
        if "坦克" in normalized or "tank" in normalized_text:
            fallback = self.unit_registry.resolve_name("重坦")
            if fallback is not None:
                return (normalize_production_name(fallback.unit_id), fallback.queue_type)
        return None

    def _check_rule_preconditions(self, match: RuleMatchResult) -> Optional[str]:
        """Return a player-facing warning if world state makes the action likely to fail.

        The task and job are still created — the LLM will see the world summary
        and decide how to handle the resource gap (e.g. produce units first).
        Returns None when no warning is needed.
        """
        if match.expert_type != "ReconExpert":
            return None
        try:
            infantry = self.world_model.query("my_actors", {"category": "infantry"})
            vehicles = self.world_model.query("my_actors", {"category": "vehicle"})
            infantry_count = len(list((infantry or {}).get("actors", []))) if isinstance(infantry, dict) else 0
            vehicle_count = len(list((vehicles or {}).get("actors", []))) if isinstance(vehicles, dict) else 0
            if infantry_count + vehicle_count == 0:
                return "目前没有可用的侦察单位，建议先生产步兵或载具"
        except Exception:
            pass
        return None

    def _notify_capability_of_nlu(self, text: str, expert_type: str) -> None:
        """Notify EconomyCapability when NLU/rule handles a production command directly."""
        if expert_type != "EconomyExpert":
            return
        cap_id = getattr(self.kernel, "capability_task_id", None)
        if not cap_id:
            return
        self.kernel.inject_player_message(cap_id, f"[NLU直达] 玩家命令已执行: {text}")

    def _is_economy_command(self, text: str) -> bool:
        """Check if text is an economy/production command that should merge to Capability."""
        normalized = re.sub(r"\s+", "", text.strip())
        if _QUESTION_RE.search(normalized):
            return False  # Don't intercept questions like "经济怎么样"
        if _ECONOMY_COMMAND_RE.search(normalized):
            return True
        # Bare building name (short input) = implicit produce
        stripped = normalized.rstrip("了啊吧呢嘛吗！!。")
        if stripped in _BARE_BUILDING_NAMES:
            return True
        return False

    def _try_merge_to_capability(self, text: str) -> Optional[dict[str, Any]]:
        """Try to merge an economy command to the EconomyCapability task."""
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        cap_id = getattr(self.kernel, "capability_task_id", None)
        if not cap_id:
            return None
        runtime_state = self._safe_world_query("runtime_state")
        capability_status = dict(runtime_state.get("capability_status") or {})
        recent_directives = list(capability_status.get("recent_directives", []) or [])
        normalized_text = re.sub(r"\s+", "", text.strip())
        if recent_directives:
            last_directive = re.sub(r"\s+", "", str(recent_directives[-1] or "").strip())
            if last_directive and last_directive == normalized_text:
                return {
                    "type": "command",
                    "ok": True,
                    "merged": True,
                    "deduplicated": True,
                    "existing_task_id": cap_id,
                    "response_text": "同类经济指令已在处理中，保持当前规划",
                }
        ok = self.kernel.inject_player_message(cap_id, text)
        if not ok:
            return None
        runtime_state = self._safe_world_query("runtime_state")
        capability_status = dict(runtime_state.get("capability_status") or {})
        slog.info("Merged economy command to Capability", event="capability_merge",
                  capability_task_id=cap_id, text=text)
        phase = str(capability_status.get("phase", "") or "")
        blocker = str(capability_status.get("blocker", "") or "")
        blocking_request_count = int(capability_status.get("blocking_request_count", 0) or 0)
        start_released_request_count = int(capability_status.get("start_released_request_count", 0) or 0)
        reinforcement_request_count = int(capability_status.get("reinforcement_request_count", 0) or 0)
        pending_request_count = int(capability_status.get("pending_request_count", 0) or 0)
        phase_text = {
            "bootstrapping": "正在补齐前置",
            "dispatch": "正在分发请求",
            "fulfilling": "已满足启动条件，正在补强",
            "executing": "正在执行生产",
            "idle": "待命中",
        }.get(phase, "")
        blocker_text = {
            "request_inference_pending": "存在待解析的单位请求",
            "missing_prerequisite": "部分请求缺少前置建筑",
            "pending_requests_waiting_dispatch": "仍有请求等待分发",
            "bootstrap_in_progress": "已有前置生产在进行",
        }.get(blocker, "")
        summary_parts: list[str] = []
        if phase_text:
            summary_parts.append(phase_text)
        if pending_request_count:
            summary_parts.append(f"待处理请求 {pending_request_count}")
        if blocking_request_count:
            summary_parts.append(f"阻塞请求 {blocking_request_count}")
        if start_released_request_count:
            summary_parts.append(f"已可启动 {start_released_request_count}")
        if reinforcement_request_count:
            summary_parts.append(f"增援请求 {reinforcement_request_count}")
        if blocker_text:
            summary_parts.append(blocker_text)
        response_text = "收到经济指令，已转发给经济规划"
        if summary_parts:
            response_text += "（" + "；".join(summary_parts) + "）"
        return {
            "type": "command",
            "ok": True,
            "merged": True,
            "existing_task_id": cap_id,
            "response_text": response_text,
        }

    async def _handle_rule_command(self, text: str, match: RuleMatchResult) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        world_warning = self._check_rule_preconditions(match)
        try:
            task, job = self._start_direct_job(text, match.expert_type, match.config)
            self._notify_capability_of_nlu(text, match.expert_type)
            slog.info(
                "Adjutant rule matched",
                event="rule_routed_command",
                raw_text=text,
                task_id=task.task_id,
                job_id=job.job_id,
                expert_type=match.expert_type,
                reason=match.reason,
                world_warning=world_warning,
            )
            response_text = f"收到指令，已直接执行并创建任务 {task.task_id}"
            if world_warning:
                response_text += f"。⚠ {world_warning}"
            return {
                "type": "command",
                "ok": True,
                "task_id": task.task_id,
                "job_id": job.job_id,
                "response_text": response_text,
                "routing": "rule",
                "expert_type": match.expert_type,
                "world_warning": world_warning,
            }
        except Exception as e:
            logger.exception("Rule-routed command failed: %r", text)
            return {
                "type": "command",
                "ok": False,
                "response_text": f"规则执行失败: {e}",
                "routing": "rule",
            }

    async def _handle_runtime_nlu(self, text: str, decision: RuntimeNLUDecision) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        created: list[dict[str, str]] = []
        is_sequence = decision.route_intent == "composite_sequence"
        try:
            for step_idx, step in enumerate(decision.steps):
                if step.expert_type == "__QUERY_ACTOR__":
                    result = self._handle_runtime_nlu_query_actor(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                if step.expert_type == "__MINE__":
                    result = self._handle_runtime_nlu_mine(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                if step.expert_type == "__STOP_ATTACK__":
                    result = self._handle_runtime_nlu_stop_attack(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                match = self._resolve_runtime_nlu_step(step)
                task_text = step.source_text or text
                task, job = self._start_direct_job(task_text, match.expert_type, match.config)
                created.append(
                    {
                        "task_id": task.task_id,
                        "job_id": job.job_id,
                        "expert_type": match.expert_type,
                        "intent": step.intent,
                        "source_text": task_text,
                    }
                )
                # For composite_sequence: start only the first task; queue the rest
                if is_sequence and step_idx < len(decision.steps) - 1:
                    remaining = list(decision.steps[step_idx + 1:])
                    self._pending_sequence = remaining
                    self._sequence_task_id = task.task_id
                    total = len(decision.steps)
                    result = {
                        "type": "command",
                        "ok": True,
                        "task_id": task.task_id,
                        "job_id": job.job_id,
                        "pending_steps": len(remaining),
                        "response_text": (
                            f"收到指令，已启动第1步（共{total}步），后续步骤将依序执行"
                        ),
                        "routing": "nlu",
                        "expert_type": match.expert_type,
                    }
                    result.update(self._nlu_result_meta(decision))
                    self._record_nlu_decision(text, decision, execution_success=True)
                    return result
            if len(created) == 1:
                task = created[0]
                result = {
                    "type": "command",
                    "ok": True,
                    "task_id": task["task_id"],
                    "job_id": task["job_id"],
                    "response_text": f"收到指令，已直接执行并创建任务 {task['task_id']}",
                    "routing": "nlu",
                    "expert_type": task["expert_type"],
                }
                result.update(self._nlu_result_meta(decision))
                self._record_nlu_decision(text, decision, execution_success=True)
                return result
            task_ids = [item["task_id"] for item in created]
            result = {
                "type": "command",
                "ok": True,
                "task_ids": task_ids,
                "steps": created,
                "response_text": f"收到指令，已拆解并直接执行 {len(created)} 个任务：{'、'.join(task_ids)}",
                "routing": "nlu",
            }
            result.update(self._nlu_result_meta(decision))
            self._record_nlu_decision(text, decision, execution_success=True)
            return result
        except Exception as exc:
            logger.exception("Runtime NLU command failed: %r", text)
            if created:
                created_ids = "、".join(item["task_id"] for item in created)
                result = {
                    "type": "command",
                    "ok": False,
                    "task_ids": [item["task_id"] for item in created],
                    "response_text": f"NLU 执行中断：已启动 {created_ids}，后续步骤失败: {exc}",
                    "routing": "nlu",
                }
                result.update(self._nlu_result_meta(decision))
                self._record_nlu_decision(text, decision, execution_success=False)
                return result
            result = {
                "type": "command",
                "ok": False,
                "response_text": f"NLU 执行失败: {exc}",
                "routing": "nlu",
            }
            result.update(self._nlu_result_meta(decision))
            self._record_nlu_decision(text, decision, execution_success=False)
            return result

    def _handle_runtime_nlu_query_actor(
        self,
        text: str,
        decision: RuntimeNLUDecision,
        step: DirectNLUStep,
    ) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("query")
        del text
        entities = dict(step.config or {})
        owner = None
        faction = str(entities.get("faction") or "").strip()
        if faction in {"己方", "自己", "我方", "友军"}:
            owner = "self"
        elif faction in {"敌方", "敌人", "对面"}:
            owner = "enemy"
        payload = self.world_model.query(
            "find_actors",
            {
                "owner": owner,
                "name": entities.get("unit"),
            },
        )
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        unit_name = str(entities.get("unit") or "单位")
        faction_text = faction or "当前"
        answer = f"{faction_text}{unit_name}共 {len(actors)} 个"
        if actors:
            ids = "、".join(str(actor.get("actor_id")) for actor in actors[:8] if actor.get("actor_id") is not None)
            if ids:
                answer += f"，ID: {ids}"
        result = {
            "type": "query",
            "ok": True,
            "response_text": answer,
            "routing": "nlu",
        }
        result.update(self._nlu_result_meta(decision))
        return result

    def _handle_runtime_nlu_mine(
        self,
        text: str,
        decision: RuntimeNLUDecision,
        step: DirectNLUStep,
    ) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        del text, step
        if self.game_api is None:
            raise RuntimeError("当前运行时未挂载 GameAPI，无法直接执行采矿命令")
        payload = self.world_model.query("my_actors", {"category": "harvester"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            raise RuntimeError("当前没有可用的采矿车")
        self.game_api.deploy_units([GameActor(int(actor["actor_id"])) for actor in actors])
        return {
            "type": "command",
            "ok": True,
            "response_text": f"收到指令，已让 {len(actors)} 辆采矿车恢复采矿",
            "routing": "nlu",
            **self._nlu_result_meta(decision),
        }

    def _handle_runtime_nlu_stop_attack(
        self,
        text: str,
        decision: RuntimeNLUDecision,
        step: DirectNLUStep,
    ) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        del text
        if self.game_api is None:
            raise RuntimeError("当前运行时未挂载 GameAPI，无法直接停止攻击")
        entities = dict(step.config or {})
        payload = self.world_model.query(
            "my_actors",
            {
                "name": entities.get("attacker_type") or entities.get("unit"),
                "can_attack": True,
            },
        )
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            raise RuntimeError("当前没有符合条件的己方作战单位")
        self.game_api.stop([GameActor(int(actor["actor_id"])) for actor in actors])
        return {
            "type": "command",
            "ok": True,
            "response_text": f"收到指令，已停止 {len(actors)} 个单位的当前攻击行动",
            "routing": "nlu",
            **self._nlu_result_meta(decision),
        }

    def _resolve_runtime_nlu_step(self, step: DirectNLUStep) -> RuleMatchResult:
        if step.intent == "attack":
            return self._resolve_attack_step(step)
        if step.intent != "deploy_mcv":
            return RuleMatchResult(expert_type=step.expert_type, config=step.config, reason=step.reason)
        if self._world_sync_is_stale():
            raise RuntimeError("当前游戏状态同步异常，请稍后重试")
        payload = self.world_model.query("my_actors", {"category": "mcv"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            base_payload = self.world_model.query("my_actors", {"type": "建造厂"})
            bases = list((base_payload or {}).get("actors", [])) if isinstance(base_payload, dict) else []
            if bases:
                raise RuntimeError("建造厂已存在，当前无基地车可部署")
            raise RuntimeError("当前没有可部署的基地车")
        actor = actors[0]
        position = tuple(actor.get("position") or [0, 0])
        return RuleMatchResult(
            expert_type="DeployExpert",
            config=DeployJobConfig(actor_id=int(actor["actor_id"]), target_position=position),
            reason=step.reason,
        )

    def _resolve_attack_step(self, step: DirectNLUStep) -> RuleMatchResult:
        """Resolve attack target_position from world model when NLU sets (0,0)."""
        config: CombatJobConfig = step.config
        if config.target_position != (0, 0):
            return RuleMatchResult(expert_type=step.expert_type, config=config, reason=step.reason)
        # Auto-target: find nearest enemy building, fall back to any enemy actor, then frozen
        payload = self.world_model.query("enemy_actors", {"category": "building"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            payload = self.world_model.query("enemy_actors")
            actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        # Fall back to frozen enemies (last-seen positions in fog)
        targets = [{"position": a.get("position", [0, 0])} for a in actors]
        if not targets:
            summary = self.world_model.query("world_summary")
            frozen = (summary or {}).get("known_enemy", {}).get("frozen_positions", [])
            targets = [{"position": f["position"]} for f in frozen if f.get("position")]
        if targets:
            # Pick closest to our base
            my_base = self.world_model.query("my_actors", {"type": "建造厂"})
            base_actors = list((my_base or {}).get("actors", [])) if isinstance(my_base, dict) else []
            if base_actors:
                bx, by = base_actors[0].get("position", [0, 0])
                targets.sort(key=lambda a: sum((c1 - c2) ** 2 for c1, c2 in zip(a.get("position", [0, 0]), [bx, by])))
            pos = tuple(targets[0].get("position", [0, 0]))
            config = CombatJobConfig(
                target_position=pos,
                engagement_mode=config.engagement_mode,
                max_chase_distance=config.max_chase_distance,
                retreat_threshold=config.retreat_threshold,
                unit_count=config.unit_count,
            )
        # If no enemies found, keep (0,0) — CombatExpert will handle "no visible enemy"
        return RuleMatchResult(expert_type=step.expert_type, config=config, reason=step.reason)

    def _start_direct_job(self, raw_text: str, expert_type: str, config: Any) -> tuple[Any, Any]:
        subscriptions = _EXPERT_SUBSCRIPTIONS.get(expert_type, ["threat", "base_state"])
        task = self.kernel.create_task(
            raw_text=raw_text,
            kind=self.config.default_task_kind,
            priority=self.config.default_task_priority,
            info_subscriptions=subscriptions,
            skip_agent=True,
        )
        job = self.kernel.start_job(task.task_id, expert_type, config)
        return task, job

    def _nlu_result_meta(self, decision: RuntimeNLUDecision) -> dict[str, Any]:
        return {
            "nlu_source": decision.source,
            "nlu_reason": decision.reason,
            "nlu_intent": decision.intent,
            "nlu_route_intent": decision.route_intent,
            "nlu_confidence": decision.confidence,
            "nlu_matched": decision.matched,
            "nlu_risk_level": decision.risk_level,
            "nlu_rollout_allowed": decision.rollout_allowed,
            "nlu_rollout_reason": decision.rollout_reason,
        }

    def _record_nlu_decision(self, command: str, decision: RuntimeNLUDecision, *, execution_success: bool) -> None:
        payload = {
            "source": decision.source,
            "reason": decision.reason,
            "intent": decision.intent,
            "confidence": decision.confidence,
            "route_intent": decision.route_intent,
            "matched": decision.matched,
            "risk_level": decision.risk_level,
            "rollout_allowed": decision.rollout_allowed,
            "rollout_reason": decision.rollout_reason,
            "execution_success": execution_success,
            "step_count": len(decision.steps),
            "steps": [
                {
                    "intent": step.intent,
                    "expert_type": step.expert_type,
                    "reason": step.reason,
                    "source_text": step.source_text,
                }
                for step in decision.steps
            ],
        }
        slog.info("NLU decision", event="nlu_decision", command=command, **payload)
        self._runtime_nlu.append_decision_log(command, payload)

    # --- Classification ---

    async def _classify_input(self, context: AdjutantContext) -> ClassificationResult:
        """Use LLM to classify player input."""
        coordinator_snapshot = context.coordinator_snapshot or {}
        context_json = json.dumps({
            "active_tasks": context.active_tasks,
            "pending_questions": context.pending_questions,
            "recent_dialogue": context.recent_dialogue[-10:],
            "recent_completed_tasks": context.recent_completed_tasks,
            "player_input": context.player_input,
            "coordinator_hints": context.coordinator_hints,
            "battlefield_snapshot": coordinator_snapshot.get("battlefield") or self._format_query_snapshot(self._battlefield_snapshot()),
            "coordinator_snapshot": coordinator_snapshot,
            "world_sync_health": coordinator_snapshot.get("world_sync") or self.world_model.refresh_health(),
        }, ensure_ascii=False)

        messages = [
            {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": context_json},
        ]

        try:
            import asyncio
            response = await asyncio.wait_for(
                self.llm.chat(messages, max_tokens=200, temperature=0.1),
                timeout=self.config.classification_timeout,
            )
            return self._parse_classification(response, context)
        except Exception:
            logger.exception("Classification LLM failed, using rule-based fallback")
            slog.error("Classification LLM failed", event="classification_failed")
            return self._rule_based_classify(context)

    def _parse_classification(self, response: LLMResponse, context: AdjutantContext) -> ClassificationResult:
        """Parse LLM classification response."""
        text = (response.text or "").strip()

        # Try to parse JSON from response
        try:
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            input_type = data.get("type", "command")
            if input_type not in (InputType.COMMAND, InputType.REPLY, InputType.QUERY, InputType.CANCEL, InputType.INFO):
                input_type = InputType.COMMAND

            return ClassificationResult(
                input_type=input_type,
                confidence=float(data.get("confidence", 0.8)),
                target_message_id=data.get("target_message_id"),
                target_task_id=data.get("target_task_id"),
                disposition=data.get("disposition"),
                raw_text=context.player_input,
            )
        except (json.JSONDecodeError, KeyError, IndexError):
            logger.warning("Failed to parse classification, defaulting to command")
            slog.warn("Classification parse failed", event="classification_parse_failed", raw_response=text)
            return ClassificationResult(
                input_type=InputType.COMMAND,
                raw_text=context.player_input,
                confidence=0.5,
            )

    _AFFIRMATIVE_WORDS: frozenset[str] = frozenset({"继续", "是", "好", "确认", "ok", "OK"})
    _NEGATIVE_WORDS: frozenset[str] = frozenset({"放弃", "否", "不", "取消", "cancel"})

    def _rule_based_classify(self, context: AdjutantContext) -> ClassificationResult:
        """Rule-based fallback classification — used only when LLM is unavailable.

        Primary classification path is _classify_input() (LLM) which has full
        context (active task labels) to correctly identify cancel intent.
        This fallback handles the most obvious patterns so degraded mode still works.
        """
        import re as _re
        text = context.player_input
        normalized = text.strip()

        # Cancel detection (degraded-mode fallback):
        # "取消任务001", "取消#002", "停止任务003", "cancel task 004"
        # Primary path: LLM classifies with active_tasks context (sees labels).
        _cancel_pattern = _re.compile(
            r"(?:取消|停止|cancel)(?:\s*任务|task)?\s*[#＃]?\s*(\d+)",
            _re.IGNORECASE,
        )
        _cancel_match = _cancel_pattern.search(normalized)
        if _cancel_match:
            return ClassificationResult(
                input_type=InputType.CANCEL,
                confidence=0.95,
                target_task_id=_cancel_match.group(1),  # label number, e.g. "001"
                raw_text=text,
            )

        # Reply detection: check pending questions
        pending = context.pending_questions
        if pending:
            top = pending[0]  # Highest priority (list is pre-sorted)
            # Exact match against any option in the highest-priority question
            for opt in top.get("options", []):
                if normalized == opt:
                    return ClassificationResult(
                        input_type=InputType.REPLY,
                        confidence=0.9,
                        target_message_id=top["message_id"],
                        target_task_id=top["task_id"],
                        raw_text=text,
                    )
            # Fuzzy match common yes/no/continue/abort words
            if normalized in self._AFFIRMATIVE_WORDS | self._NEGATIVE_WORDS:
                return ClassificationResult(
                    input_type=InputType.REPLY,
                    confidence=0.6,
                    target_message_id=top["message_id"],
                    target_task_id=top["task_id"],
                    raw_text=text,
                )

        # Query detection
        query_keywords = {"？", "?", "如何", "怎么", "战况", "多少", "几个", "哪里", "什么", "建议", "分析"}
        if any(kw in text for kw in query_keywords):
            return ClassificationResult(
                input_type=InputType.QUERY,
                confidence=0.4,
                raw_text=text,
            )

        info_keywords = {"敌人", "被打", "发现", "左下", "右下", "左上", "右上", "前线", "情报", "坐标", "基地在", "进攻中", "被围", "骚扰"}
        if any(kw in normalized for kw in info_keywords):
            return ClassificationResult(
                input_type=InputType.INFO,
                confidence=0.4,
                raw_text=text,
            )

        return ClassificationResult(
            input_type=InputType.COMMAND,
            confidence=0.4,
            raw_text=text,
        )

    # --- Route handlers ---

    async def _handle_reply(self, classification: ClassificationResult) -> dict[str, Any]:
        """Route player reply to the correct pending question.

        # TODO(14d): When a player reply addresses multiple pending questions at once
        # (e.g. "继续, 优先生产" could answer two separate questions), this handler
        # only routes to the highest-priority question.  A proper implementation would
        # split the reply text and dispatch to each matched question in priority order.
        # Current fallback (single-question routing) is acceptable for now.
        """
        message_id = classification.target_message_id
        task_id = classification.target_task_id

        # If no specific target, match highest-priority pending question
        if not message_id:
            pending = self.kernel.list_pending_questions()
            if pending:
                top = pending[0]  # Already sorted by priority
                message_id = top["message_id"]
                task_id = top["task_id"]

        if not message_id or not task_id:
            return {
                "type": "reply",
                "ok": False,
                "response_text": "没有待回答的问题",
            }

        response = PlayerResponse(
            message_id=message_id,
            task_id=task_id,
            answer=classification.raw_text,
        )
        result = self.kernel.submit_player_response(response)
        return {
            "type": "reply",
            "ok": result.get("ok", False),
            "status": result.get("status"),
            "response_text": result.get("message", "已回复"),
        }

    async def _handle_info(self, text: str, classification: ClassificationResult, context: AdjutantContext) -> dict[str, Any]:
        """Route player intelligence/feedback to the most relevant active task.

        If a matching task is found, creates a supplementary command task that
        captures the player's intel. Otherwise falls back to _handle_command.
        """
        battlefield_snapshot = self._battlefield_snapshot()
        best_task = self._select_info_target_task(text, classification, context, battlefield_snapshot)

        if best_task is not None and not self.kernel.is_direct_managed(best_task.task_id):
            ok = self.kernel.inject_player_message(best_task.task_id, text)
            if ok:
                label = getattr(best_task, "label", best_task.task_id)
                return {
                    "type": "info",
                    "ok": True,
                    "task_id": best_task.task_id,
                    "routing": "info_merge",
                    "response_text": f"收到情报，已转发给任务 #{label}",
                    "target_task_id": label,
                    "battlefield_disposition": battlefield_snapshot.get("disposition", "unknown"),
                    "battlefield_focus": battlefield_snapshot.get("focus", "general"),
                }

        # No viable target task: fall back to a normal command task so the intel stays visible.
        result = await self._handle_command(text)
        task_ref = f"（相关任务: {self._task_label(best_task)}）" if best_task else ""
        if result.get("ok"):
            result["type"] = "info"
            result["response_text"] = f"收到情报{task_ref}，已记录"
            result["routing"] = "info_fallback"
            result["battlefield_disposition"] = battlefield_snapshot.get("disposition", "unknown")
            result["battlefield_focus"] = battlefield_snapshot.get("focus", "general")
        return result

    async def _handle_command_with_disposition(
        self,
        text: str,
        classification: ClassificationResult,
        context: AdjutantContext,
    ) -> dict[str, Any]:
        battlefield_snapshot = self._battlefield_snapshot()
        target_task = self._select_info_target_task(text, classification, context, battlefield_snapshot)
        disposition = (classification.disposition or "").lower()

        if disposition == "merge" and target_task is not None and not self.kernel.is_direct_managed(target_task.task_id):
            ok = self.kernel.inject_player_message(target_task.task_id, text)
            if ok:
                label = getattr(target_task, "label", target_task.task_id)
                return {
                    "type": "command",
                    "ok": True,
                    "merged": True,
                    "existing_task_id": target_task.task_id,
                    "response_text": f"收到指令，已转发给任务 #{label}",
                    "routing": "command_merge",
                    "target_task_id": label,
                    "battlefield_disposition": battlefield_snapshot.get("disposition", "unknown"),
                    "battlefield_focus": battlefield_snapshot.get("focus", "general"),
                }

        if disposition == "override" and target_task is not None and not self.kernel.is_direct_managed(target_task.task_id):
            self.kernel.cancel_task(target_task.task_id)
            result = await self._handle_command(text)
            if result.get("ok"):
                label = getattr(target_task, "label", target_task.task_id)
                result["overridden_task_label"] = label
                result["routing"] = "command_override"
                result["response_text"] = f"已取代任务 #{label}，新指令已创建"
            return result

        if disposition == "interrupt":
            try:
                task = self.kernel.create_task(
                    raw_text=text,
                    kind=self.config.default_task_kind,
                    priority=self.config.default_task_priority + 20,
                    info_subscriptions=["threat", "base_state"],
                )
                return {
                    "type": "command",
                    "ok": True,
                    "task_id": task.task_id,
                    "routing": "command_interrupt",
                    "battlefield_disposition": battlefield_snapshot.get("disposition", "unknown"),
                    "battlefield_focus": battlefield_snapshot.get("focus", "general"),
                    "response_text": f"收到紧急指令，已创建高优先级任务 {task.task_id}",
                }
            except Exception as e:
                logger.exception("Failed to create interrupt task for command: %r", text)
                return {
                    "type": "command",
                    "ok": False,
                    "response_text": f"指令处理失败: {e}",
                    "routing": "command_interrupt",
                }

        return await self._handle_command(text)

    async def _handle_cancel(self, classification: ClassificationResult) -> dict[str, Any]:
        """Cancel a task by its label (e.g. '001') via Kernel."""
        label = (classification.target_task_id or "").lstrip("0") or "0"
        # Find task by label (reverse lookup: label "001" → task_id "t_xxx")
        tasks = self.kernel.list_tasks()
        target = next(
            (t for t in tasks if getattr(t, "label", "").lstrip("0") == label or getattr(t, "label", "") == classification.target_task_id),
            None,
        )
        if target is None:
            return {
                "type": "cancel",
                "ok": False,
                "response_text": f"找不到任务 #{classification.target_task_id}，请确认任务编号",
            }
        ok = self.kernel.cancel_task(target.task_id)
        label_display = f"#{getattr(target, 'label', classification.target_task_id)}"
        return {
            "type": "cancel",
            "ok": ok,
            "task_id": target.task_id,
            "response_text": f"已取消任务 {label_display}" if ok else f"任务 {label_display} 无法取消（已完成或已中止）",
        }

    _OVERLAP_KEYWORDS = {
        "探索", "侦察", "侦查", "找", "搜索", "发现", "探路",
        "攻击", "进攻", "打", "突袭", "消灭",
        "建", "造", "生产", "发展", "扩张",
        "防守", "防御", "守",
        "敌方", "敌人", "敌军", "基地",
    }

    def _find_overlapping_task(self, text: str) -> Optional[Any]:
        """Find an active task with semantically overlapping intent."""
        tasks = self.kernel.list_tasks()
        terminal = {"succeeded", "failed", "aborted", "partial"}
        active = [t for t in tasks if t.status.value not in terminal]
        if not active:
            return None

        text_kw = {w for w in self._OVERLAP_KEYWORDS if w in text}
        if not text_kw:
            return None

        for t in active:
            raw = t.raw_text or ""
            task_kw = {w for w in self._OVERLAP_KEYWORDS if w in raw}
            shared = text_kw & task_kw
            # Require at least 2 shared keywords for overlap detection
            if len(shared) >= 2:
                return t
        return None

    def _find_task_by_label(self, label: str) -> Optional[Any]:
        """Find a task by its human-readable label (e.g. '001' or '1')."""
        normalized = label.lstrip("0") or "0"
        for t in self.kernel.list_tasks():
            t_label = getattr(t, "label", "")
            if t_label == label or t_label.lstrip("0") == normalized:
                return t
        return None

    async def _handle_override(self, text: str, target_label: str) -> dict[str, Any]:
        """Cancel an existing task and create a new one to replace it."""
        target = self._find_task_by_label(target_label)
        cancelled_label = None
        if target is not None:
            if getattr(target, "is_capability", False):
                slog.info("Override blocked: target is capability task", event="override_blocked_capability",
                          task_id=target.task_id, label=target_label)
                return await self._handle_command(text)
            self.kernel.cancel_task(target.task_id)
            cancelled_label = getattr(target, "label", target_label)
            slog.info("Override: cancelled old task", event="task_overridden",
                      old_task_id=target.task_id, old_label=cancelled_label)

        result = await self._handle_command(text)
        if cancelled_label and result.get("ok"):
            result["overridden_task_label"] = cancelled_label
            result["response_text"] = f"已取代任务 #{cancelled_label}，新指令已创建"
        return result

    @staticmethod
    def _find_oldest_agent_task(context: Any) -> Optional[str]:
        """Find the oldest non-NLU, non-capability active task label for override."""
        oldest_label = None
        oldest_age = -1
        for at in context.active_tasks:
            if at.get("is_nlu") or at.get("is_capability"):
                continue
            age = at.get("age_seconds", 0)
            if age > oldest_age:
                oldest_age = age
                oldest_label = at.get("label")
        return oldest_label

    async def _handle_command(self, text: str) -> dict[str, Any]:
        """Create a new Task via Kernel, with semantic overlap detection."""
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        # Check for overlapping active tasks
        overlap = self._find_overlapping_task(text)
        if overlap is not None:
            slog.info(
                "Semantic overlap detected with active task",
                event="task_overlap_detected",
                new_text=text,
                existing_label=overlap.label,
                existing_text=overlap.raw_text,
            )
            return {
                "type": "command",
                "ok": True,
                "merged": True,
                "existing_task_id": overlap.task_id,
                "response_text": f"已有类似任务在执行（#{overlap.label}: {overlap.raw_text}），不重复创建",
            }
        try:
            task = self.kernel.create_task(
                raw_text=text,
                kind=self.config.default_task_kind,
                priority=self.config.default_task_priority,
                info_subscriptions=["threat", "base_state"],
            )
            return {
                "type": "command",
                "ok": True,
                "task_id": task.task_id,
                "response_text": f"收到指令，已创建任务 {task.task_id}",
            }
        except Exception as e:
            logger.exception("Failed to create task for command: %r", text)
            return {
                "type": "command",
                "ok": False,
                "response_text": f"指令处理失败: {e}",
            }

    async def _handle_query(self, text: str, context: AdjutantContext) -> dict[str, Any]:
        """Answer a query using LLM + WorldModel context."""
        if self._world_sync_is_stale():
            return self._stale_world_guard("query")
        world_summary = self._get_world_summary()
        battlefield_snapshot = self._format_query_snapshot(self._battlefield_snapshot(world_summary))
        query_context = json.dumps({
            "world_summary": world_summary,
            "battlefield_snapshot": battlefield_snapshot,
            "world_sync_health": self.world_model.refresh_health(),
            "active_tasks": context.active_tasks,
            "question": text,
        }, ensure_ascii=False)

        messages = [
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": query_context},
        ]

        try:
            import asyncio
            with bm_span("llm_call", name="adjutant:query"):
                response = await asyncio.wait_for(
                    self.llm.chat(messages, max_tokens=500, temperature=0.7),
                    timeout=self.config.query_timeout,
                )
            answer = response.text or "无法回答"
        except asyncio.TimeoutError:
            logger.warning("Query LLM timed out after %.0fs", self.config.query_timeout)
            answer = self._fallback_query_answer(world_summary)
        except Exception:
            logger.exception("Query LLM failed")
            answer = self._fallback_query_answer(world_summary)

        return {
            "type": "query",
            "ok": True,
            "response_text": answer,
        }

    # --- Context building ---

    def _build_context(self, player_input: str) -> AdjutantContext:
        """Build the minimal Adjutant context (~500-1000 tokens)."""
        tasks = self.kernel.list_tasks()
        runtime_state = self._safe_world_query("runtime_state")
        runtime_tasks = dict(runtime_state.get("active_tasks") or {})
        capability_status = dict(runtime_state.get("capability_status") or {})
        coordinator_snapshot = self._coordinator_snapshot()
        world_sync = dict((coordinator_snapshot.get("world_sync") or {}))
        active_tasks = []
        for t in tasks:
            if t.status.value not in ("pending", "running", "waiting"):
                continue
            runtime_task = dict(runtime_tasks.get(t.task_id) or {})
            task_entry = {
                "task_id": t.task_id,
                "label": getattr(t, "label", ""),
                "raw_text": t.raw_text,
                "status": t.status.value,
                "is_capability": bool(runtime_task.get("is_capability", getattr(t, "is_capability", False))),
                "active_group_size": int(runtime_task.get("active_group_size", 0) or 0),
                "domain": self._task_domain(str(getattr(t, "raw_text", "") or "").lower()),
            }
            triage = self._derive_task_triage(t, runtime_task, runtime_state, capability_status, world_sync)
            task_entry.update(triage)
            task_entry["status_line"] = triage.get("status_line") or self._task_status_line(task_entry, capability_status)
            active_tasks.append(task_entry)

        pending_questions = self.kernel.list_pending_questions()
        coordinator_hints = self._coordinator_hints(
            player_input,
            active_tasks,
            coordinator_snapshot.get("battlefield") or {},
        )
        coordinator_snapshot["task_overview"] = self._build_task_overview(active_tasks)
        coordinator_snapshot["battle_groups"] = self._build_battle_groups(active_tasks)
        return AdjutantContext(
            active_tasks=active_tasks,
            pending_questions=pending_questions,
            recent_dialogue=self._dialogue_history[-self.config.max_dialogue_history:],
            player_input=player_input,
            recent_completed_tasks=list(self._recent_completed),
            coordinator_snapshot=coordinator_snapshot,
            coordinator_hints=coordinator_hints,
        )

    def _record_dialogue(self, speaker: str, text: str) -> None:
        """Record a dialogue entry."""
        self._dialogue_history.append({
            "from": speaker,
            "content": text,
            "timestamp": time.time(),
        })
        # Trim history
        if len(self._dialogue_history) > self.config.max_dialogue_history * 2:
            self._dialogue_history = self._dialogue_history[-self.config.max_dialogue_history:]

    def notify_task_message(self, task_id: str, message_type: str, content: str) -> None:
        """Record a task WARNING or INFO message into dialogue history.

        Called by the Bridge for TASK_WARNING and TASK_INFO so the Adjutant
        LLM sees ongoing task updates when classifying the next player input.
        """
        prefix = "⚠" if message_type == "task_warning" else "ℹ"
        self._record_dialogue("system", f"{prefix} 任务 {task_id}: {content}")

    def notify_task_completed(
        self,
        label: str,
        raw_text: str,
        result: str,
        summary: str,
        task_id: str | None = None,
    ) -> None:
        """Record a task completion into dialogue history and recent_completed buffer.

        Called by the Bridge when a TASK_COMPLETE_REPORT message is published,
        so the next LLM classification can see recent task outcomes in context.
        """
        entry = {"label": label, "raw_text": raw_text, "result": result, "summary": summary}
        self._recent_completed.append(entry)
        if len(self._recent_completed) > 5:
            self._recent_completed = self._recent_completed[-5:]
        self._record_dialogue("system", f"任务 #{label}（{raw_text}）{result}: {summary}")
        # Advance composite_sequence if this task was the current sequence step
        _tid = task_id or label
        if self._sequence_task_id and self._sequence_task_id == _tid:
            self._advance_sequence(result)

    def _advance_sequence(self, completed_result: str) -> None:
        """Start the next pending sequence step, or cancel on failure."""
        if not self._pending_sequence:
            self._sequence_task_id = None
            return
        if self._world_sync_is_stale():
            cancelled = len(self._pending_sequence)
            self._pending_sequence = []
            self._sequence_task_id = None
            self._record_dialogue(
                "system",
                f"当前游戏状态同步异常，已暂停序列并取消剩余 {cancelled} 步",
            )
            return
        if completed_result not in ("succeeded", "partial"):
            cancelled = len(self._pending_sequence)
            self._pending_sequence = []
            self._sequence_task_id = None
            self._record_dialogue(
                "system",
                f"序列步骤失败（{completed_result}），已取消剩余 {cancelled} 步",
            )
            return
        next_step = self._pending_sequence.pop(0)
        try:
            match = self._resolve_runtime_nlu_step(next_step)
            task_text = next_step.source_text or ""
            task, job = self._start_direct_job(task_text, match.expert_type, match.config)
            self._sequence_task_id = task.task_id
            remaining = len(self._pending_sequence)
            self._record_dialogue(
                "system",
                f"序列下一步已启动（任务 {task.task_id}），剩余 {remaining} 步",
            )
        except Exception as exc:
            logger.warning("Sequence advance failed: %s", exc)
            self._pending_sequence = []
            self._sequence_task_id = None
            self._record_dialogue("system", f"序列推进失败: {exc}")

    def clear_dialogue_history(self) -> None:
        self._dialogue_history = []
        self._recent_completed = []
        self._pending_sequence = []
        self._sequence_task_id = None

    # --- TaskMessage formatting ---
    # NOTE: format_task_message() is a utility retained for tests and external callers.
    # The primary message delivery path (implemented in T2) routes TaskMessages directly
    # via ws_server.send_task_message() — this formatter is NOT called on that path.

    @staticmethod
    def format_task_message(message: TaskMessage, mode: str = "text") -> str:
        """Format a TaskMessage for player consumption.

        Args:
            message: The TaskMessage to format.
            mode: "text" for chat mode, "card" for dashboard card mode.
        """
        task_label = f"[任务 {message.task_id}]"

        if mode == "text":
            if message.type == TaskMessageType.TASK_INFO:
                return f"{task_label} {message.content}"
            elif message.type == TaskMessageType.TASK_WARNING:
                return f"⚠ {task_label} {message.content}"
            elif message.type == TaskMessageType.TASK_QUESTION:
                options_str = ""
                if message.options:
                    options_str = " (" + " / ".join(message.options) + ")"
                return f"❓ {task_label} {message.content}{options_str}"
            elif message.type == TaskMessageType.TASK_COMPLETE_REPORT:
                return f"✓ {task_label} {message.content}"
            return f"{task_label} {message.content}"

        # Card mode — structured dict for frontend
        return json.dumps({
            "task_id": message.task_id,
            "message_id": message.message_id,
            "type": message.type.value,
            "content": message.content,
            "options": message.options,
            "timeout_s": message.timeout_s,
            "default_option": message.default_option,
            "priority": message.priority,
            "timestamp": message.timestamp,
        }, ensure_ascii=False)
