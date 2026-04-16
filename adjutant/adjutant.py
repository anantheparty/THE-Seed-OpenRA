"""Adjutant — player's sole dialogue interface (design.md §6).

Routes player input to the correct handler:
  1. Reply to pending question → Kernel.submit_player_response
  2. New command → Kernel.create_task
  3. Query → LLM + WorldModel direct answer

Formats all outbound TaskMessages for player consumption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol

from benchmark import span as bm_span
from logging_system import get_logger
from llm import LLMProvider, LLMResponse
from models import (
    CombatJobConfig,
    DeployJobConfig,
    EngagementMode,
    EconomyJobConfig,
    MovementJobConfig,
    MoveMode,
    OccupyJobConfig,
    PlayerResponse,
    RepairJobConfig,
    ReconJobConfig,
    TaskMessage,
    TaskMessageType,
)
from openra_api.models import Actor as GameActor
from openra_api.production_names import (
    normalize_production_name,
    production_name_matches,
    production_name_variants,
)
from runtime_views import (
    BattlefieldSnapshot,
    CapabilityStatusSnapshot,
    RuntimeStateSnapshot,
    TaskTriageInputs,
    TaskTriageSnapshot,
    normalize_base_progression,
)
from task_triage import (
    build_runtime_unit_pipeline_focus,
    build_runtime_unit_pipeline_preview,
    build_runtime_unit_pipeline_preview_items,
    build_task_triage_from_artifacts,
    capability_blocker_status_text,
    capability_coordinator_alert,
    capability_phase_status_text,
    collect_task_triage_inputs,
)
from task_agent.workflows import PRODUCE_UNITS_THEN_ATTACK, PRODUCE_UNITS_THEN_RECON
from unit_registry import UnitRegistry, get_default_registry, normalize_registry_name
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

_REPAIR_KEYWORDS = (
    "回修",
    "回去修",
    "去修",
    "拉去修",
)

_REPAIR_VERBS = (
    "修理",
    "维修",
)

_REPAIR_FACILITY_NOUNS = (
    "维修厂",
    "修理厂",
    "维修站",
    "修理站",
    "维修中心",
    "修理中心",
)

_OCCUPY_KEYWORDS = (
    "占领",
    "占下",
    "夺取",
    "夺下",
    "拿下",
    "接管",
    "占点",
)

_ATTACK_KEYWORDS = (
    "攻击",
    "进攻",
    "出击",
    "总攻",
    "冲锋",
    "打",
    "突袭",
    "消灭",
    "集火",
    "点杀",
    "优先打",
)
_RETREAT_KEYWORDS = (
    "撤退",
    "后撤",
    "回撤",
    "撤回",
    "撤军",
    "退兵",
    "退回去",
    "退回来",
    "回基地",
    "回家",
)
_RETREAT_BASE_HINTS = (
    "基地",
    "家里",
    "回家",
    "回来",
    "撤回来",
    "退回来",
    "后方",
    "本部",
)

# Question patterns that should bypass NLU and go to LLM classification
_QUESTION_RE = re.compile(r"(为什么|怎么|怎样|吗\s*[？?。！\s]?$|呢\s*[？?。！\s]?$|什么时候|如何|why|how\b)", re.IGNORECASE)
_MULTI_REPLY_SPLIT_RE = re.compile(r"[，,；;/、\n]+")

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

_INFO_ECONOMY_HINTS = frozenset({"电", "矿", "资源", "经济", "生产", "建", "造", "科技", "发展", "补给", "扩张", "前置", "补链", "单位请求", "请求"})
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
    def register_task_message(self, message: TaskMessage) -> bool: ...
    def record_capability_note(self, text: str) -> bool: ...
    def submit_player_response(self, response: PlayerResponse, *, now: Optional[float] = None) -> dict[str, Any]: ...
    def list_pending_questions(self) -> list[dict[str, Any]]: ...
    def list_task_messages(self, task_id: Optional[str] = None) -> list[Any]: ...
    def list_tasks(self) -> list[Any]: ...
    def jobs_for_task(self, task_id: str) -> list[Any]: ...
    def cancel_task(self, task_id: str) -> bool: ...
    def is_direct_managed(self, task_id: str) -> bool: ...
    def inject_player_message(self, task_id: str, text: str) -> bool: ...
    def runtime_state(self) -> dict[str, Any]: ...
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
    "OccupyExpert":    ["threat"],
    "RepairExpert":    ["base_state", "threat"],
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
    task_messages: list[Any] = field(default_factory=list)
    jobs_by_task: dict[str, list[Any]] = field(default_factory=dict)
    runtime_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
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
If `task_focus` is present, prefer it over coarse battlefield summary when answering task-specific questions.
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

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _trusted_query_actors(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        actors = payload.get("actors")
        if not isinstance(actors, list):
            return []
        trusted: list[dict[str, Any]] = []
        for item in actors:
            if not isinstance(item, dict):
                continue
            actor_id = self._coerce_int(item.get("actor_id"))
            if actor_id is None:
                continue
            actor = dict(item)
            actor["actor_id"] = actor_id
            position = actor.get("position")
            if isinstance(position, (list, tuple)) and len(position) >= 2:
                x = self._coerce_int(position[0])
                y = self._coerce_int(position[1])
                if x is not None and y is not None:
                    actor["position"] = [x, y]
            trusted.append(actor)
        return trusted

    def _query_self_actor_snapshot(self) -> list[dict[str, Any]]:
        payload = self.world_model.query("my_actors")
        if isinstance(payload, dict) and isinstance(payload.get("actors"), list):
            return self._trusted_query_actors(payload)

        merged: dict[int, dict[str, Any]] = {}
        for query_payload in (
            self.world_model.query("my_actors", {"category": "mcv"}),
            self.world_model.query("my_actors", {"type": "建造厂"}),
        ):
            for actor in self._trusted_query_actors(query_payload):
                merged[int(actor["actor_id"])] = dict(actor)
        return list(merged.values())

    @staticmethod
    def _is_construction_yard_actor(actor: dict[str, Any]) -> bool:
        return production_name_matches(
            "建造厂",
            str(actor.get("type") or ""),
            str(actor.get("name") or ""),
            str(actor.get("display_name") or ""),
        )

    @classmethod
    def _is_live_actor(cls, actor: dict[str, Any]) -> bool:
        is_alive = actor.get("is_alive")
        if is_alive is not None:
            return bool(is_alive)
        hp = cls._coerce_float(actor.get("hp"))
        if hp is not None:
            return hp > 0
        hp_percent = cls._coerce_float(actor.get("hppercent"))
        if hp_percent is not None:
            return hp_percent > 0
        return True

    @staticmethod
    def _is_mcv_actor(actor: dict[str, Any]) -> bool:
        category = str(actor.get("category") or "")
        if category == "mcv":
            return True
        return production_name_matches(
            "基地车",
            str(actor.get("type") or ""),
            str(actor.get("name") or ""),
            str(actor.get("display_name") or ""),
        )

    def _battlefield_snapshot(
        self,
        world_summary: Optional[dict[str, Any]] = None,
        *,
        runtime_state: Optional[dict[str, Any]] = None,
        runtime_facts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        query_snapshot = self._safe_world_query("battlefield_snapshot")
        if query_snapshot:
            return BattlefieldSnapshot.from_mapping(query_snapshot).to_dict()

        summary = world_summary if isinstance(world_summary, dict) else self._get_world_summary()
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(runtime_state)
        runtime_facts = dict(runtime_facts or {})
        capability_status = runtime_snapshot.capability_status
        active_tasks = dict(runtime_snapshot.active_tasks)
        economy = summary.get("economy", {}) if isinstance(summary, dict) else {}
        military = summary.get("military", {}) if isinstance(summary, dict) else {}
        game_map = summary.get("map", {}) if isinstance(summary, dict) else {}
        known_enemy = summary.get("known_enemy", {}) if isinstance(summary, dict) else {}
        info_experts = dict(runtime_facts.get("info_experts") or {})

        self_units = int(self._coerce_float(military.get("self_units")) or 0)
        enemy_units = int(self._coerce_float(military.get("enemy_units")) or 0)
        self_combat_value = self._coerce_float(military.get("self_combat_value"))
        enemy_combat_value = self._coerce_float(military.get("enemy_combat_value"))
        idle_self_units = int(self._coerce_float(military.get("idle_self_units")) or 0)
        low_power = bool(economy.get("low_power"))
        queue_blocked = bool(economy.get("queue_blocked"))
        queue_blocked_reason = str(economy.get("queue_blocked_reason", "") or "")
        queue_blocked_queue_types = [str(item) for item in list(economy.get("queue_blocked_queue_types", []) or []) if item]
        queue_blocked_items = [
            dict(item)
            for item in list((economy.get("queue_blocked_items") or runtime_facts.get("queue_blocked_items") or []) or [])
            if isinstance(item, dict)
        ]
        disabled_structure_count = int(self._coerce_float(economy.get("disabled_structure_count")) or 0)
        powered_down_structure_count = int(self._coerce_float(economy.get("powered_down_structure_count")) or 0)
        low_power_disabled_structure_count = int(self._coerce_float(economy.get("low_power_disabled_structure_count")) or 0)
        power_outage_structure_count = int(self._coerce_float(economy.get("power_outage_structure_count")) or 0)
        disabled_structures = [str(item) for item in list(economy.get("disabled_structures", []) or []) if item]
        explored_pct = self._coerce_float(game_map.get("explored_pct"))
        enemy_bases = int(self._coerce_float(known_enemy.get("bases")) or self._coerce_float(known_enemy.get("structures")) or 0)
        enemy_spotted = int(self._coerce_float(known_enemy.get("units_spotted")) or 0)
        frozen_count = int(self._coerce_float(known_enemy.get("frozen_count")) or 0)
        threat_level = str(info_experts.get("threat_level") or "unknown")
        threat_direction = str(info_experts.get("threat_direction") or "unknown")
        base_under_attack = bool(info_experts.get("base_under_attack"))
        base_health_summary = str(info_experts.get("base_health_summary") or "")
        total_combat_units = int(runtime_facts.get("combat_unit_count", 0) or 0)
        committed_combat_units = sum(
            int(task.get("active_group_size", 0) or 0)
            for task in active_tasks.values()
            if isinstance(task, dict) and not bool(task.get("is_capability"))
        )
        committed_combat_units = max(committed_combat_units, 0)
        free_combat_units = max(total_combat_units - committed_combat_units, 0)
        pending_request_count = int(capability_status.pending_request_count or 0)
        bootstrapping_request_count = int(capability_status.bootstrapping_request_count or 0)
        reservation_count = len(runtime_snapshot.unit_reservations)
        unit_pipeline_preview = build_runtime_unit_pipeline_preview(runtime_snapshot.to_dict())
        has_production = any(
            int(runtime_facts.get(field, 0) or 0) > 0
            for field in ("barracks_count", "war_factory_count", "airfield_count")
        )

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
        elif low_power or queue_blocked or pending_request_count:
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
            if queue_blocked_reason == "ready_not_placed":
                summary_text += "，生产队列有已完成未放置条目"
            elif queue_blocked_reason == "paused":
                summary_text += "，生产队列被暂停"
            else:
                summary_text += "，生产队列阻塞"
            if queue_blocked_items:
                preview = "、".join(
                    str(item.get("display_name") or item.get("unit_type") or "?")
                    for item in queue_blocked_items[:2]
                )
                summary_text += f"({preview})"
        if disabled_structure_count:
            summary_text += f"，离线建筑 {disabled_structure_count}"
        if pending_request_count:
            summary_text += f"，待处理请求 {pending_request_count}"
        if reservation_count:
            summary_text += f"，预留 {reservation_count}"
        if unit_pipeline_preview:
            summary_text += f"，在途 {unit_pipeline_preview}"
        if total_combat_units:
            summary_text += f"，可自由调度战斗单位 {free_combat_units}/{total_combat_units}"

        if low_power:
            recommended_posture = "stabilize_power"
        elif queue_blocked:
            recommended_posture = "unblock_queue"
        elif pending_request_count or reservation_count:
            recommended_posture = "satisfy_requests"
        elif base_under_attack or disposition == "under_pressure":
            recommended_posture = "defend_base"
        elif not enemy_bases and not enemy_spotted and frozen_count <= 0:
            recommended_posture = "expand_recon"
        elif disposition == "advantage":
            recommended_posture = "press_advantage"
        else:
            recommended_posture = "maintain_posture"

        return BattlefieldSnapshot(
            summary=summary_text,
            disposition=disposition,
            focus=focus,
            self_units=self_units,
            enemy_units=enemy_units,
            self_combat_value=round(self_score, 2),
            enemy_combat_value=round(enemy_score, 2),
            idle_self_units=idle_self_units,
            self_combat_units=total_combat_units,
            committed_combat_units=committed_combat_units,
            free_combat_units=free_combat_units,
            low_power=low_power,
            queue_blocked=queue_blocked,
            queue_blocked_reason=queue_blocked_reason,
            queue_blocked_queue_types=queue_blocked_queue_types,
            queue_blocked_items=queue_blocked_items,
            disabled_structure_count=disabled_structure_count,
            powered_down_structure_count=powered_down_structure_count,
            low_power_disabled_structure_count=low_power_disabled_structure_count,
            power_outage_structure_count=power_outage_structure_count,
            disabled_structures=disabled_structures,
            recommended_posture=recommended_posture,
            threat_level=threat_level,
            threat_direction=threat_direction,
            base_under_attack=base_under_attack,
            base_health_summary=base_health_summary,
            has_production=has_production,
            explored_pct=explored_pct,
            enemy_bases=enemy_bases,
            enemy_spotted=enemy_spotted,
            frozen_enemy_count=frozen_count,
            pending_request_count=pending_request_count,
            bootstrapping_request_count=bootstrapping_request_count,
            reservation_count=reservation_count,
            unit_pipeline_preview=unit_pipeline_preview,
            stale=bool(runtime_facts.get("world_sync_stale", False)),
            capability_status=capability_status,
        ).to_dict()

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

    @staticmethod
    def _workflow_task_domain(task: dict[str, Any]) -> str:
        workflow_template = str(task.get("workflow_template", "") or "")
        if workflow_template == PRODUCE_UNITS_THEN_RECON:
            return "recon"
        if workflow_template == PRODUCE_UNITS_THEN_ATTACK:
            return "combat"
        return "general"

    @staticmethod
    def _workflow_continuation_score(
        task: dict[str, Any],
        *,
        text_domain: str,
        is_follow_up: bool,
    ) -> int:
        workflow_template = str(task.get("workflow_template", "") or "")
        workflow_phase = str(task.get("workflow_phase", "") or "")
        if workflow_template == PRODUCE_UNITS_THEN_RECON:
            if text_domain not in {"general", "recon"}:
                return 0

            if not is_follow_up and workflow_phase not in {"ready_to_recon", "recon_running"}:
                return 0

            score = {
                "request_units_first": 2,
                "waiting_for_units": 3,
                "ready_to_recon": 5,
                "recon_running": 6,
            }.get(workflow_phase, 0)
            if score <= 0:
                return 0
            if int(task.get("active_group_size", 0) or 0) > 0:
                score += 1
            return score
        if workflow_template != PRODUCE_UNITS_THEN_ATTACK:
            return 0
        if text_domain not in {"general", "combat"}:
            return 0

        if not is_follow_up and workflow_phase not in {"ready_to_attack", "attack_running"}:
            return 0

        score = {
            "request_units_first": 2,
            "waiting_for_units": 3,
            "ready_to_attack": 5,
            "attack_running": 6,
        }.get(workflow_phase, 0)
        if score <= 0:
            return 0
        if int(task.get("active_group_size", 0) or 0) > 0:
            score += 1
        return score

    def _infer_task_domain(
        self,
        task_text: str,
        runtime_task: Optional[dict[str, Any]] = None,
        triage: Optional[dict[str, Any]] = None,
    ) -> str:
        runtime_task = dict(runtime_task or {})
        triage_snapshot = TaskTriageSnapshot.from_mapping(triage)

        if bool(runtime_task.get("is_capability")):
            return "economy"

        active_expert = str(triage_snapshot.active_expert or runtime_task.get("active_expert", "") or "")
        expert_domain = {
            "EconomyExpert": "economy",
            "DeployExpert": "economy",
            "ReconExpert": "recon",
            "CombatExpert": "combat",
        }.get(active_expert)
        if expert_domain:
            return expert_domain

        workflow_domain = self._workflow_task_domain(
            {
                "workflow_template": str(
                    triage_snapshot.workflow_template or runtime_task.get("workflow_template", "") or ""
                ),
            }
        )
        if workflow_domain != "general":
            return workflow_domain

        phase = str(triage_snapshot.phase or runtime_task.get("phase", "") or "")
        if phase in {"dispatch", "bootstrapping", "fulfilling"}:
            return "economy"

        active_group_size = int(triage_snapshot.active_group_size or runtime_task.get("active_group_size", 0) or 0)
        if active_group_size > 0:
            blocking_reason = triage_snapshot.blocking_reason
            waiting_reason = triage_snapshot.waiting_reason
            if waiting_reason == "unit_reservation":
                if any(hint in task_text for hint in _INFO_RECON_HINTS):
                    return "recon"
                if any(hint in task_text for hint in _INFO_COMBAT_HINTS):
                    return "combat"
            if blocking_reason == "task_warning" and any(hint in task_text for hint in _INFO_COMBAT_HINTS):
                return "combat"

        return self._task_domain(task_text)

    def _score_info_target(
        self,
        text: str,
        task: Any,
        battlefield_snapshot: BattlefieldSnapshot | dict[str, Any],
    ) -> int:
        task_text = self._task_text(task)
        if not task_text:
            return 0

        snapshot = BattlefieldSnapshot.from_mapping(battlefield_snapshot)
        text_domain = self._classify_text_domain(text)
        task_domain = self._task_domain(task_text)
        score = 0

        if text_domain != "general":
            score += 3 if text_domain == task_domain else -1
        focus = snapshot.focus
        disposition = snapshot.disposition
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
        battlefield_snapshot: BattlefieldSnapshot | dict[str, Any],
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
        return BattlefieldSnapshot.from_mapping(battlefield_snapshot).to_dict()

    def _select_query_focus_task_entry(self, text: str, context: AdjutantContext) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized:
            return None

        label_matches = {
            match.group(1)
            for match in re.finditer(r"#?(\d{1,3})", normalized)
            if match.group(1)
        }
        if label_matches:
            for task in context.active_tasks:
                label = str(task.get("label", "") or "").lstrip("0") or "0"
                if label in {item.lstrip("0") or "0" for item in label_matches}:
                    return task

        if re.search(r"(为什么只有|为什么才|怎么只有|怎么才|为什么没|几个上了|只有\d+个上了)", normalized):
            battle_groups = list(context.coordinator_snapshot.get("battle_groups") or [])
            if battle_groups:
                battle_groups.sort(
                    key=lambda item: (
                        int(item.get("active_group_size", 0) or 0),
                        1 if str(item.get("domain", "") or "") == "combat" else 0,
                    ),
                    reverse=True,
                )
                target_task_id = str(battle_groups[0].get("task_id", "") or "")
                for task in context.active_tasks:
                    if str(task.get("task_id", "") or "") == target_task_id:
                        return task

        text_domain = self._classify_text_domain(normalized)
        candidates = [
            task
            for task in context.active_tasks
            if not bool(task.get("is_capability"))
        ]
        if not candidates:
            return None

        scored: list[tuple[int, dict[str, Any]]] = []
        for task in candidates:
            score = 0
            task_domain = str(task.get("domain", "") or "general")
            if text_domain != "general":
                score += 4 if task_domain == text_domain else -1
            score += min(int(task.get("active_group_size", 0) or 0), 6)
            state = str(task.get("state", "") or "")
            if state == "running":
                score += 2
            elif state in {"waiting", "waiting_units"}:
                score += 1
            if str(task.get("active_expert", "") or "") in {"CombatExpert", "ReconExpert", "MovementExpert"}:
                score += 1
            if score > 0:
                scored.append((score, task))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def _build_query_task_focus(self, text: str, context: AdjutantContext) -> dict[str, Any]:
        task_entry = self._select_query_focus_task_entry(text, context)
        if task_entry is None:
            return {}
        task_id = str(task_entry.get("task_id", "") or "")
        runtime_task = dict((context.runtime_tasks or {}).get(task_id, {}) or {})
        task_jobs = list((context.jobs_by_task or {}).get(task_id, []) or [])
        recent_messages = []
        for message in list(context.task_messages or []):
            if str(getattr(message, "task_id", "") or "") != task_id:
                continue
            message_type = getattr(getattr(message, "type", None), "value", getattr(message, "type", None))
            recent_messages.append(
                {
                    "type": str(message_type or ""),
                    "content": str(getattr(message, "content", "") or ""),
                }
            )
        focus = {
            "task_id": task_id,
            "label": str(task_entry.get("label", "") or ""),
            "raw_text": str(task_entry.get("raw_text", "") or ""),
            "domain": str(task_entry.get("domain", "") or ""),
            "state": str(runtime_task.get("state", "") or task_entry.get("state", "") or ""),
            "phase": str(runtime_task.get("phase", "") or task_entry.get("phase", "") or ""),
            "active_expert": str(runtime_task.get("active_expert", "") or task_entry.get("active_expert", "") or ""),
            "status_line": str(runtime_task.get("status_line", "") or task_entry.get("status_line", "") or ""),
            "waiting_reason": str(runtime_task.get("waiting_reason", "") or task_entry.get("waiting_reason", "") or ""),
            "blocking_reason": str(runtime_task.get("blocking_reason", "") or task_entry.get("blocking_reason", "") or ""),
            "triage_waiting_reason": str(task_entry.get("waiting_reason", "") or ""),
            "triage_blocking_reason": str(task_entry.get("blocking_reason", "") or ""),
            "active_group_size": int(task_entry.get("active_group_size", 0) or 0),
            "active_actor_ids": [int(actor_id) for actor_id in list(task_entry.get("active_actor_ids", []) or []) if actor_id is not None][:12],
            "unit_mix": list(task_entry.get("unit_mix", []) or []),
            "jobs": [
                {
                    "job_id": str(getattr(job, "job_id", "") or ""),
                    "expert_type": str(getattr(job, "expert_type", "") or ""),
                    "status": str(getattr(getattr(job, "status", None), "value", getattr(job, "status", "")) or ""),
                    "config": str(getattr(job, "config", "") or ""),
                }
                for job in task_jobs
            ],
            "recent_messages": recent_messages[-5:],
        }
        return focus

    @staticmethod
    def _format_group_mix(actors: list[GameActor]) -> list[str]:
        counts: dict[str, int] = {}
        for actor in actors:
            label = str(
                getattr(actor, "display_name", "")
                or getattr(actor, "name", "")
                or getattr(actor, "type", "")
                or ""
            ).strip()
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
        return [
            f"{label}×{count}"
            for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]
        ]

    def _summarize_group_actor_ids(self, actor_ids: list[int]) -> dict[str, Any]:
        if not actor_ids:
            return {"known_count": 0, "combat_count": 0, "unit_mix": []}
        actor_map = getattr(getattr(self.world_model, "state", None), "actors", {}) or {}
        actors: list[GameActor] = []
        for actor_id in actor_ids:
            actor = actor_map.get(int(actor_id)) if isinstance(actor_map, dict) else None
            if actor is None:
                continue
            owner_value = getattr(getattr(actor, "owner", None), "value", getattr(actor, "owner", None))
            faction_value = str(getattr(actor, "faction", "") or "")
            if owner_value not in {None, "", "self"} and faction_value not in {"自己", "self"}:
                continue
            if not bool(getattr(actor, "is_alive", True)):
                continue
            actors.append(actor)
        return {
            "known_count": len(actors),
            "combat_count": sum(
                1
                for actor in actors
                if bool(getattr(actor, "can_attack", True))
                or str(getattr(actor, "type", "") or "")
            ),
            "unit_mix": self._format_group_mix(actors),
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

    def _build_battle_groups(self, active_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for task in active_tasks:
            domain = str(task.get("domain", "") or "")
            if domain not in {"combat", "recon"}:
                continue
            if int(task.get("active_group_size", 0) or 0) <= 0 and task.get("state") not in {"waiting_units", "running"}:
                continue
            active_actor_ids = [int(actor_id) for actor_id in list(task.get("active_actor_ids", []) or []) if actor_id is not None]
            group_summary = self._summarize_group_actor_ids(active_actor_ids)
            groups.append({
                "label": str(task.get("label", "") or ""),
                "task_id": str(task.get("task_id", "") or ""),
                "domain": domain,
                "state": str(task.get("state", "") or "unknown"),
                "phase": str(task.get("phase", "") or ""),
                "active_expert": str(task.get("active_expert", "") or ""),
                "active_group_size": int(task.get("active_group_size", 0) or 0),
                "active_actor_ids": active_actor_ids[:12],
                "group_known_count": int(group_summary.get("known_count", 0) or 0),
                "group_combat_count": int(group_summary.get("combat_count", 0) or 0),
                "unit_mix": list(group_summary.get("unit_mix", []) or []),
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

    def _runtime_state_snapshot(self) -> dict[str, Any]:
        runtime_state = getattr(self.kernel, "runtime_state", None)
        if callable(runtime_state):
            try:
                state = runtime_state()
                return state if isinstance(state, dict) else {}
            except Exception:
                logger.exception("Adjutant failed to read kernel runtime state")
        return self._safe_world_query("runtime_state")

    def _collect_coordinator_inputs(self) -> dict[str, Any]:
        world_summary = self._get_world_summary()
        runtime_state = self._runtime_state_snapshot()
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(runtime_state)
        runtime_facts: dict[str, Any] = {}
        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if callable(compute_runtime_facts):
            try:
                runtime_facts = compute_runtime_facts("__adjutant__", include_buildable=True) or {}
            except Exception:
                logger.exception("Adjutant failed to compute coordinator runtime facts")
                runtime_facts = {}
        battlefield = self._format_query_snapshot(
            self._battlefield_snapshot(
                world_summary,
                runtime_state=runtime_state,
                runtime_facts=runtime_facts,
            )
        )
        return {
            "world_summary": world_summary,
            "battlefield": battlefield,
            "runtime_state": runtime_snapshot.to_dict(),
            "runtime_facts": runtime_facts,
            "world_sync": self.world_model.refresh_health(),
        }

    def _build_context_snapshot(self) -> dict[str, Any]:
        tasks = list(self.kernel.list_tasks())
        pending_questions = list(self.kernel.list_pending_questions())
        list_task_messages = getattr(self.kernel, "list_task_messages", None)
        task_messages = list_task_messages() if callable(list_task_messages) else []
        jobs_for_task = getattr(self.kernel, "jobs_for_task", None)
        jobs_by_task: dict[str, list[Any]] = {}
        for task in tasks:
            if getattr(getattr(task, "status", None), "value", "") not in {"pending", "running", "waiting"}:
                continue
            jobs_by_task[str(getattr(task, "task_id", "") or "")] = (
                list(jobs_for_task(task.task_id)) if callable(jobs_for_task) else []
            )
        return {
            "tasks": tasks,
            "pending_questions": pending_questions,
            "task_messages": task_messages,
            "jobs_by_task": jobs_by_task,
            "coordinator_inputs": self._collect_coordinator_inputs(),
        }

    def _coordinator_snapshot(self, collected_inputs: dict[str, Any]) -> dict[str, Any]:
        inputs = dict(collected_inputs or {})
        battlefield = dict(inputs.get("battlefield") or {})
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(inputs.get("runtime_state"))
        runtime_state = runtime_snapshot.to_dict()
        capability_status = runtime_snapshot.capability_status
        runtime_facts = dict(inputs.get("runtime_facts") or {})
        ready_queue_items = []
        for item in list(runtime_facts.get("ready_queue_items", []) or [])[:3]:
            if not isinstance(item, dict):
                continue
            ready_queue_items.append({
                "queue_type": str(item.get("queue_type", "") or ""),
                "unit_type": str(item.get("unit_type", "") or ""),
                "display_name": str(item.get("display_name", "") or item.get("unit_type", "") or ""),
                "owner_actor_id": item.get("owner_actor_id"),
            })
        base_state = {
            "faction": str(runtime_facts.get("faction") or ""),
            "capability_truth_blocker": str(runtime_facts.get("capability_truth_blocker") or ""),
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
            "buildable": dict(runtime_facts.get("buildable") or {}),
            "buildable_now": dict(runtime_facts.get("buildable_now") or {}),
            "buildable_blocked": dict(runtime_facts.get("buildable_blocked") or {}),
            "base_progression": dict(runtime_facts.get("base_progression") or {}),
            "low_power": battlefield.get("low_power", False),
            "queue_blocked": battlefield.get("queue_blocked", False),
        }
        base_readiness = self._coordinator_base_readiness(base_state)
        info_experts = dict(runtime_facts.get("info_experts") or {})
        unit_pipeline_focus = build_runtime_unit_pipeline_focus(runtime_state)
        unit_pipeline_preview_items = build_runtime_unit_pipeline_preview_items(runtime_state, limit=3)
        return {
            "battlefield": battlefield,
            "base_state": base_state,
            "base_readiness": base_readiness,
            "capability": {
                "task_id": capability_status.task_id,
                "label": capability_status.task_label,
                "status": capability_status.status,
                "phase": capability_status.phase,
                "blocker": capability_status.blocker,
                "truth_blocker": str(runtime_facts.get("capability_truth_blocker") or ""),
                "faction": str(runtime_facts.get("faction") or ""),
                "active_job_types": list(capability_status.active_job_types),
                "pending_request_count": capability_status.pending_request_count,
                "dispatch_request_count": capability_status.dispatch_request_count,
                "bootstrapping_request_count": capability_status.bootstrapping_request_count,
                "start_released_request_count": capability_status.start_released_request_count,
                "reinforcement_request_count": capability_status.reinforcement_request_count,
                "blocking_request_count": capability_status.blocking_request_count,
                "inference_pending_count": capability_status.inference_pending_count,
                "prerequisite_gap_count": capability_status.prerequisite_gap_count,
                "world_sync_stale_count": capability_status.world_sync_stale_count,
                "deploy_required_count": capability_status.deploy_required_count,
                "disabled_prerequisite_count": capability_status.disabled_prerequisite_count,
                "low_power_count": capability_status.low_power_count,
                "producer_disabled_count": capability_status.producer_disabled_count,
                "queue_blocked_count": capability_status.queue_blocked_count,
                "insufficient_funds_count": capability_status.insufficient_funds_count,
                "recent_directives": list(capability_status.recent_directives),
                "ready_queue_items": ready_queue_items,
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
            "world_sync": dict(inputs.get("world_sync") or {}),
            "active_task_count": len(runtime_snapshot.active_tasks),
            "reservation_count": battlefield.get("reservation_count", len(runtime_snapshot.unit_reservations)),
            "unit_pipeline_focus": unit_pipeline_focus,
            "unit_pipeline_preview_items": unit_pipeline_preview_items,
        }

    @staticmethod
    def _coordinator_base_readiness(base_state: dict[str, Any]) -> dict[str, Any]:
        return normalize_base_progression(base_state)

    @staticmethod
    def _secondary_unit_pipeline_preview_text(
        snapshot: dict[str, Any],
        *,
        limit: int = 2,
    ) -> str:
        if limit <= 0:
            return ""

        focus = dict(snapshot.get("unit_pipeline_focus") or {})
        items = [
            dict(item)
            for item in list(snapshot.get("unit_pipeline_preview_items", []) or [])
            if isinstance(item, dict)
        ]
        if not items:
            return ""

        focus_preview = str(focus.get("preview") or "").strip()
        focus_task_id = str(focus.get("task_id") or "").strip()
        focus_task_label = str(focus.get("task_label") or "").strip()
        focus_reason = str(focus.get("reason") or "").strip()
        focus_skipped = False
        rendered: list[str] = []

        for item in items:
            preview = str(item.get("preview") or "").strip()
            if not preview:
                continue

            if not focus_skipped and (
                preview == focus_preview
                and str(item.get("task_id") or "").strip() == focus_task_id
                and str(item.get("task_label") or "").strip() == focus_task_label
                and str(item.get("reason") or "").strip() == focus_reason
            ):
                focus_skipped = True
                continue

            task_label = str(item.get("task_label") or "").strip()
            rendered.append(f"#{task_label} {preview}" if task_label else preview)
            if len(rendered) >= limit:
                break

        return "；".join(rendered)

    @staticmethod
    def _coordinator_alerts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        battlefield = dict(snapshot.get("battlefield") or {})
        capability = CapabilityStatusSnapshot.from_mapping(snapshot.get("capability") or {})
        task_overview = dict(snapshot.get("task_overview") or {})
        world_sync = dict(snapshot.get("world_sync") or {})
        unit_pipeline_focus = dict(snapshot.get("unit_pipeline_focus") or {})
        alerts: list[dict[str, Any]] = []

        def add_alert(code: str, severity: str, text: str, *, target_label: str = "") -> None:
            if not text:
                return
            alerts.append({
                "code": code,
                "severity": severity,
                "text": text,
                "target_label": target_label,
            })

        if world_sync.get("stale"):
            detail = Adjutant._world_sync_detail_text(world_sync)
            text = "世界状态同步异常，当前判断可能滞后"
            if detail:
                text += f"（{detail}）"
            add_alert("world_stale", "warning", text)
        if battlefield.get("base_under_attack"):
            direction = str(battlefield.get("threat_direction", "") or "")
            suffix = f"（方向：{direction}）" if direction and direction != "unknown" else ""
            add_alert("base_under_attack", "urgent", f"基地正受攻击{suffix}")
        truth_blocker = str((snapshot.get("capability") or {}).get("truth_blocker") or "")
        truth_faction = str((snapshot.get("capability") or {}).get("faction") or "")
        if truth_blocker == "faction_roster_unsupported":
            add_alert(
                "capability_truth_blocked",
                "warning",
                f"能力层当前因阵营限制暂停（{truth_faction or 'unknown'}）",
                target_label=str((snapshot.get("capability") or {}).get("label") or ""),
            )
        if battlefield.get("low_power"):
            add_alert("low_power", "warning", "当前低电，部分生产与建筑能力会受影响")
        capability_alert = capability_coordinator_alert(capability)
        if capability_alert and not (
            (capability_alert["code"] == "capability_low_power" and battlefield.get("low_power"))
            or (capability_alert["code"] == "capability_queue_blocked" and battlefield.get("queue_blocked"))
        ):
            add_alert(
                capability_alert["code"],
                capability_alert["severity"],
                capability_alert["text"],
                target_label=capability_alert.get("target_label", ""),
            )
        ready_items = list((snapshot.get("capability") or {}).get("ready_queue_items", []) or [])
        if ready_items:
            ready_names = "、".join(str(item.get("display_name", "") or item.get("unit_type", "") or "?") for item in ready_items[:2])
            add_alert("queue_ready_items", "warning", f"队列里有待处理成品：{ready_names}")
        elif battlefield.get("queue_blocked"):
            queue_reason = str(battlefield.get("queue_blocked_reason", "") or "")
            queue_types = [str(item) for item in list(battlefield.get("queue_blocked_queue_types", []) or []) if item]
            queue_items = [
                dict(item)
                for item in list(battlefield.get("queue_blocked_items", []) or [])
                if isinstance(item, dict)
            ]
            queue_suffix = f"（{','.join(queue_types)}）" if queue_types else ""
            queue_items_suffix = ""
            if queue_items:
                queue_items_suffix = "：" + "、".join(
                    str(item.get("display_name") or item.get("unit_type") or "?")
                    for item in queue_items[:2]
                )
            if queue_reason == "paused":
                add_alert("queue_blocked", "warning", f"生产队列被暂停{queue_suffix}{queue_items_suffix}")
            elif queue_reason == "ready_not_placed":
                add_alert("queue_blocked", "warning", f"生产队列有已完成未放置条目{queue_suffix}{queue_items_suffix}")
            else:
                add_alert("queue_blocked", "warning", f"生产队列存在阻塞{queue_suffix}{queue_items_suffix}")
        disabled_structures = [str(item) for item in list(battlefield.get("disabled_structures", []) or []) if item]
        if disabled_structures:
            preview = "、".join(disabled_structures[:2])
            more = f" 等{len(disabled_structures)} 个" if len(disabled_structures) > 2 else ""
            add_alert("disabled_structures", "warning", f"存在离线建筑：{preview}{more}")
        reservation_wait = int(task_overview.get("reservation_wait_count", 0) or 0)
        focus_detail = str(unit_pipeline_focus.get("detail") or unit_pipeline_focus.get("preview") or "")
        focus_label = str(unit_pipeline_focus.get("task_label") or "")
        if focus_detail:
            prefix = f"任务 #{focus_label} " if focus_label else ""
            add_alert("reservation_waiting", "info", f"{prefix}正在等待补位：{focus_detail}")
        elif reservation_wait:
            add_alert("reservation_waiting", "info", f"{reservation_wait} 个任务正在等待补位")
        return alerts[:5]

    @staticmethod
    def _coordinator_status_line(snapshot: dict[str, Any]) -> str:
        alerts = list(snapshot.get("alerts", []) or [])
        battlefield = dict(snapshot.get("battlefield") or {})
        capability = dict(snapshot.get("capability") or {})
        base_readiness = dict(snapshot.get("base_readiness") or {})
        task_overview = dict(snapshot.get("task_overview") or {})
        unit_pipeline_focus = dict(snapshot.get("unit_pipeline_focus") or {})

        parts: list[str] = []
        if alerts:
            parts.append(str(alerts[0].get("text", "") or ""))
        elif base_readiness.get("status"):
            parts.append(str(base_readiness.get("status", "") or ""))
        elif battlefield.get("summary"):
            parts.append(str(battlefield.get("summary", "") or ""))

        combat_groups = int(task_overview.get("combat_group_count", 0) or 0)
        recon_groups = int(task_overview.get("recon_group_count", 0) or 0)
        if combat_groups:
            parts.append(f"作战组 {combat_groups}")
        if recon_groups:
            parts.append(f"侦察组 {recon_groups}")

        phase_text = capability_phase_status_text(capability, prefix="能力层")
        if phase_text:
            parts.append(phase_text)

        unit_pipeline_focus_detail = str(unit_pipeline_focus.get("detail") or "").strip()
        unit_pipeline_focus_label = str(unit_pipeline_focus.get("task_label") or "").strip()
        if unit_pipeline_focus_detail:
            focus_text = unit_pipeline_focus_detail
            if unit_pipeline_focus_label:
                focus_text = f"任务 #{unit_pipeline_focus_label} {focus_text}"
            if all(unit_pipeline_focus_detail not in part for part in parts):
                parts.append(f"补位 {focus_text}")
        else:
            unit_pipeline_preview = str(battlefield.get("unit_pipeline_preview") or "")
            if unit_pipeline_preview and all(unit_pipeline_preview not in part for part in parts):
                parts.append(f"在途 {unit_pipeline_preview}")

        secondary_pipeline_preview = Adjutant._secondary_unit_pipeline_preview_text(snapshot)
        if secondary_pipeline_preview:
            parts.append(f"其他在途 {secondary_pipeline_preview}")

        return "；".join(part for part in parts if part)

    @staticmethod
    def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
        normalized = text.lower()
        return any(token in normalized for token in tokens)

    def _coordinator_hints(self, player_input: str, active_tasks: list[dict[str, Any]], battlefield: dict[str, Any]) -> dict[str, Any]:
        text = player_input.strip()
        if not text or not active_tasks:
            return {}

        text_domain = self._classify_text_domain(text)
        free_combat_units = int(battlefield.get("free_combat_units", 0) or 0)
        committed_combat_units = int(battlefield.get("committed_combat_units", 0) or 0)
        continuation_tokens = ("继续", "再", "顺便", "然后", "接着", "补", "优先", "先")
        override_tokens = ("改", "换", "别", "不要", "停止", "改成", "转去", "转向", "撤", "退")
        interrupt_tokens = ("立刻", "马上", "紧急", "火速")
        is_follow_up = self._has_any_token(text, continuation_tokens + override_tokens + interrupt_tokens)

        scored: list[tuple[int, dict[str, Any]]] = []
        for task in active_tasks:
            task_domain = str(task.get("domain", "general") or "general")
            workflow_domain = self._workflow_task_domain(task)
            candidate_domains = {task_domain}
            if workflow_domain != "general":
                candidate_domains.add(workflow_domain)
            if text_domain != "general" and text_domain not in candidate_domains:
                continue
            score = 0
            if text_domain != "general" and task_domain == text_domain:
                score += 3
            elif text_domain != "general" and workflow_domain == text_domain:
                score += 3
            if task.get("state") in {"waiting_units", "waiting", "running"}:
                score += 2
            if task.get("is_capability") and text_domain == "economy":
                score += 4
            if int(task.get("active_group_size", 0) or 0) > 0 and text_domain in {"combat", "recon"}:
                score += 3
            if task.get("state") == "waiting_units" and text_domain in {"combat", "recon"}:
                score -= 1
            score += self._workflow_continuation_score(
                task,
                text_domain=text_domain,
                is_follow_up=is_follow_up,
            )
            if score > 0:
                scored.append((score, task))

        best_task: Optional[dict[str, Any]] = None
        if scored:
            def _sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
                score, task = item
                state = str(task.get("state", "") or "")
                state_rank = {
                    "running": 3,
                    "waiting_units": 2,
                    "waiting": 1,
                    "blocked": 0,
                }.get(state, 0)
                return (
                    score,
                    int(task.get("active_group_size", 0) or 0),
                    state_rank,
                    1 if task.get("is_capability") else 0,
                )

            scored.sort(key=_sort_key, reverse=True)
            best_task = scored[0][1]

        suggested_disposition: Optional[str] = None
        reason = ""
        if self._has_any_token(text, interrupt_tokens) and text_domain == "combat" and battlefield.get("base_under_attack"):
            suggested_disposition = "interrupt"
            reason = "urgent_combat_under_pressure"
        elif best_task is not None:
            task_blocking_reason = str(best_task.get("blocking_reason", "") or "")
            task_phase = str(best_task.get("phase", "") or "")
            workflow_template = str(best_task.get("workflow_template", "") or "")
            workflow_phase = str(best_task.get("workflow_phase", "") or "")
            capability_followup = bool(best_task.get("is_capability")) and task_blocking_reason in {
                "missing_prerequisite",
                "request_inference_pending",
            }
            capability_phase_followup = bool(best_task.get("is_capability")) and task_phase in {
                "dispatch",
                "bootstrapping",
                "fulfilling",
            }
            workflow_followup = (
                workflow_template in {PRODUCE_UNITS_THEN_RECON, PRODUCE_UNITS_THEN_ATTACK}
                and text_domain in {"general", "recon", "combat"}
                and workflow_phase in {
                    "request_units_first",
                    "waiting_for_units",
                    "ready_to_recon",
                    "recon_running",
                    "ready_to_attack",
                    "attack_running",
                }
                and (
                    is_follow_up
                    or workflow_phase in {"ready_to_recon", "recon_running", "ready_to_attack", "attack_running"}
                )
            )
            if self._has_any_token(text, override_tokens):
                suggested_disposition = "override"
                reason = "followup_override"
            elif capability_followup and (is_follow_up or text_domain == "economy"):
                suggested_disposition = "merge"
                reason = f"capability_followup_{task_blocking_reason}"
            elif capability_phase_followup and (is_follow_up or text_domain == "economy"):
                suggested_disposition = "merge"
                reason = f"capability_phase_{task_phase}"
            elif workflow_followup:
                suggested_disposition = "merge"
                reason = f"workflow_continue_{workflow_phase}"
            elif text_domain in {"combat", "recon"} and int(best_task.get("active_group_size", 0) or 0) > 0 and free_combat_units <= 0:
                suggested_disposition = "merge"
                reason = "reuse_active_group_no_free_combat"
            elif text_domain in {"combat", "recon"} and int(best_task.get("active_group_size", 0) or 0) > 0 and free_combat_units > 0 and not is_follow_up:
                suggested_disposition = None
                reason = "free_combat_units_available"
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
            "free_combat_units": free_combat_units,
            "committed_combat_units": committed_combat_units,
            "has_free_combat_capacity": free_combat_units > 0,
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
    def _context_battlefield_snapshot(context: AdjutantContext) -> dict[str, Any]:
        snapshot = dict((context.coordinator_snapshot or {}).get("battlefield") or {})
        return snapshot if snapshot else {}

    @staticmethod
    def _derive_task_triage(
        task: Any,
        runtime_task: dict[str, Any],
        runtime_state: dict[str, Any],
        inputs: TaskTriageInputs,
        task_messages: list[Any],
        pending_questions: list[dict[str, Any]],
        jobs: list[Any],
    ) -> dict[str, Any]:
        return build_task_triage_from_artifacts(
            task=task,
            runtime_task=runtime_task,
            runtime_state=dict(runtime_state or {}),
            task_id=str(getattr(task, "task_id", "") or ""),
            jobs=jobs,
            world_sync=dict(inputs.world_sync or {}),
            pending_questions=pending_questions,
            task_messages=task_messages,
            unit_mix=list(inputs.unit_mix or []),
        ).to_dict()

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
            repair_feedback = self._maybe_handle_repair_feedback(text)
            if repair_feedback is not None:
                slog.info(
                    "Repair feedback short-circuit",
                    event="repair_feedback_shortcircuit",
                    ok=repair_feedback.get("ok"),
                    reason=repair_feedback.get("reason"),
                )
                self._record_dialogue("player", text)
                if repair_feedback.get("response_text"):
                    self._record_dialogue("adjutant", repair_feedback["response_text"])
                repair_feedback["timestamp"] = time.time()
                return repair_feedback
            occupy_feedback = self._maybe_handle_occupy_feedback(text)
            if occupy_feedback is not None:
                slog.info(
                    "Occupy feedback short-circuit",
                    event="occupy_feedback_shortcircuit",
                    ok=occupy_feedback.get("ok"),
                    reason=occupy_feedback.get("reason"),
                )
                self._record_dialogue("player", text)
                if occupy_feedback.get("response_text"):
                    self._record_dialogue("adjutant", occupy_feedback["response_text"])
                occupy_feedback["timestamp"] = time.time()
                return occupy_feedback
            explicit_operator_move = self._match_operator_move(re.sub(r"\s+", "", text.strip()))
            if explicit_operator_move is not None:
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="explicit_operator_move_rule",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_rule_command(text, explicit_operator_move)
                slog.info(
                    "Explicit operator move rule result",
                    event="route_result",
                    routing="rule",
                    ok=result.get("ok"),
                    expert_type=explicit_operator_move.expert_type,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            attack_feedback = self._maybe_handle_attack_feedback(text)
            if attack_feedback is not None:
                slog.info(
                    "Attack feedback short-circuit",
                    event="attack_feedback_shortcircuit",
                    ok=attack_feedback.get("ok"),
                    reason=attack_feedback.get("reason"),
                )
                self._record_dialogue("player", text)
                if attack_feedback.get("response_text"):
                    self._record_dialogue("adjutant", attack_feedback["response_text"])
                attack_feedback["timestamp"] = time.time()
                return attack_feedback
            explicit_repair_match = self._match_repair(re.sub(r"\s+", "", text.strip()))
            if explicit_repair_match is not None:
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="explicit_repair_rule",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_rule_command(text, explicit_repair_match)
                slog.info(
                    "Explicit repair rule result",
                    event="route_result",
                    routing="rule",
                    ok=result.get("ok"),
                    expert_type=explicit_repair_match.expert_type,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            explicit_attack_match = self._match_attack(re.sub(r"\s+", "", text.strip()))
            if explicit_attack_match is not None:
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="explicit_attack_rule",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_rule_command(text, explicit_attack_match)
                slog.info(
                    "Explicit attack rule result",
                    event="route_result",
                    routing="rule",
                    ok=result.get("ok"),
                    expert_type=explicit_attack_match.expert_type,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
            explicit_retreat_match = self._match_retreat(re.sub(r"\s+", "", text.strip()))
            if explicit_retreat_match is not None:
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="explicit_retreat_rule",
                    )
                    self._record_dialogue("player", text)
                    if result.get("response_text"):
                        self._record_dialogue("adjutant", result["response_text"])
                    result["timestamp"] = time.time()
                    return result
                result = await self._handle_rule_command(text, explicit_retreat_match)
                slog.info(
                    "Explicit retreat rule result",
                    event="route_result",
                    routing="rule",
                    ok=result.get("ok"),
                    expert_type=explicit_retreat_match.expert_type,
                )
                self._record_dialogue("player", text)
                if result.get("response_text"):
                    self._record_dialogue("adjutant", result["response_text"])
                result["timestamp"] = time.time()
                return result
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
            multi_reply_result = self._try_route_multi_reply(text)
            if multi_reply_result is not None:
                slog.info(
                    "Explicit multi-reply route result",
                    event="route_result",
                    routing="multi_reply",
                    ok=multi_reply_result.get("ok"),
                    answered_count=multi_reply_result.get("answered_count"),
                    question_count=multi_reply_result.get("question_count"),
                )
                self._record_dialogue("player", text)
                if multi_reply_result.get("response_text"):
                    self._record_dialogue("adjutant", multi_reply_result["response_text"])
                multi_reply_result["timestamp"] = time.time()
                return multi_reply_result
            single_reply_result = self._try_route_single_reply(text)
            if single_reply_result is not None:
                slog.info(
                    "Explicit single-reply route result",
                    event="route_result",
                    routing="single_reply",
                    ok=single_reply_result.get("ok"),
                )
                self._record_dialogue("player", text)
                if single_reply_result.get("response_text"):
                    self._record_dialogue("adjutant", single_reply_result["response_text"])
                single_reply_result["timestamp"] = time.time()
                return single_reply_result
            vague_combat_result = await self._maybe_handle_vague_combat_command(text)
            if vague_combat_result is not None:
                slog.info(
                    "Vague combat route result",
                    event="route_result",
                    routing=vague_combat_result.get("routing"),
                    ok=vague_combat_result.get("ok"),
                    target_task_id=vague_combat_result.get("target_task_id"),
                )
                self._record_dialogue("player", text)
                if vague_combat_result.get("response_text"):
                    self._record_dialogue("adjutant", vague_combat_result["response_text"])
                vague_combat_result["timestamp"] = time.time()
                return vague_combat_result
            continuation_result = await self._maybe_route_active_task_followup(text)
            if continuation_result is not None:
                slog.info(
                    "Continuation route result",
                    event="route_result",
                    routing="continuation",
                    ok=continuation_result.get("ok"),
                    target_task_id=continuation_result.get("target_task_id"),
                )
                self._record_dialogue("player", text)
                if continuation_result.get("response_text"):
                    self._record_dialogue("adjutant", continuation_result["response_text"])
                continuation_result["timestamp"] = time.time()
                return continuation_result
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
            # Economy commands → merge to EconomyCapability only after runtime NLU
            # gets the first chance to decompose safe direct/composite production
            # text into stable current-runtime steps.
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

        repair = self._match_repair(normalized)
        if repair is not None:
            return repair

        occupy = self._match_occupy(normalized)
        if occupy is not None:
            return occupy

        operator_move = self._match_operator_move(normalized)
        if operator_move is not None:
            return operator_move

        attack = self._match_attack(normalized)
        if attack is not None:
            return attack

        retreat = self._match_retreat(normalized)
        if retreat is not None:
            return retreat

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
        if self._looks_like_query(text) and decision.route_intent != "query_actor":
            return None
        if decision.route_intent == "attack" and self._looks_like_attack_preparation_command(text):
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
        normalized = re.sub(r"\s+", "", str(text or ""))
        if any(keyword in normalized for keyword in query_keywords):
            return True
        if any(
            marker in normalized
            for marker in ("能不能", "可不可以", "能否", "行不行", "要不要", "该不该", "是不是该", "适不适合")
        ):
            return True
        return normalized.endswith(("吗", "呢", "么"))

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

        deploy_truth = self._deploy_truth_snapshot()
        if deploy_truth["ambiguous"]:
            return {
                "type": "command",
                "ok": False,
                "response_text": "基地车状态同步中，请稍后重试",
                "routing": "rule",
                "reason": "deploy_truth_ambiguous",
            }
        if deploy_truth["mcv_actors"]:
            return None
        if deploy_truth["has_construction_yard"]:
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

    def _maybe_handle_repair_feedback(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", text.strip())
        if not self._looks_like_repair_command(normalized):
            return None
        if self._looks_like_query(normalized):
            return None
        if self._looks_like_complex_command(normalized):
            return None
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        targets = self._resolve_repair_targets(normalized)
        requires_facility = self._repair_requires_facility(normalized, targets=targets)
        if requires_facility and not self._has_repair_facility():
            return {
                "type": "command",
                "ok": False,
                "response_text": "当前没有维修厂，无法执行回修",
                "routing": "rule",
                "expert_type": "RepairExpert",
                "reason": "rule_repair_missing_facility",
            }
        if targets:
            return None
        entry = self.unit_registry.match_in_text(normalized, queue_types=("Vehicle", "Building"))
        target_name = entry.display_name if entry is not None else "单位"
        return {
            "type": "command",
            "ok": True,
            "response_text": f"当前没有需要回修的受损{target_name}",
            "routing": "rule",
            "expert_type": "RepairExpert",
            "reason": "rule_repair_no_damaged_target",
        }

    def _maybe_handle_occupy_feedback(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", text.strip())
        if not self._looks_like_occupy_command(normalized):
            return None
        if self._looks_like_query(normalized):
            return None
        if self._looks_like_complex_command(normalized):
            return None
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        if not self._resolve_occupy_actor_ids(normalized):
            return {
                "type": "command",
                "ok": False,
                "response_text": "当前没有可用工程师，无法执行占领",
                "routing": "rule",
                "reason": "rule_occupy_missing_engineer",
            }
        if self._resolve_occupy_target(normalized) is not None:
            return None
        return {
            "type": "command",
            "ok": False,
            "response_text": "当前没有可见的可占领目标，请先侦察或明确目标",
            "routing": "rule",
            "reason": "rule_occupy_missing_target",
        }

    def _maybe_handle_attack_feedback(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", text.strip())
        if not self._looks_like_attack_command(normalized):
            return None
        if self._looks_like_query(normalized):
            return None
        if self._is_economy_command(normalized):
            return None
        if self._looks_like_complex_command(normalized):
            return None
        if self._looks_like_attack_preparation_command(normalized):
            return None
        if self._looks_like_generic_enemy_base_attack(normalized):
            return None
        if self._looks_like_force_then_generic_enemy_attack(normalized):
            return None
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        target_entry = self.unit_registry.match_in_text(
            normalized,
            queue_types=("Building", "Defense", "Infantry", "Vehicle", "Aircraft", "Ship"),
        )
        if target_entry is None:
            return None
        if self._resolve_attack_target(normalized) is not None:
            return None
        return {
            "type": "command",
            "ok": False,
            "response_text": f"当前没有可见的{target_entry.display_name}目标，请先侦察或重新指定目标",
            "routing": "rule",
            "reason": "rule_attack_missing_target",
        }

    def _looks_like_attack_preparation_command(self, normalized: str) -> bool:
        if not self._looks_like_attack_command(normalized):
            return False
        if not re.search(r"(准备|备战|整一大批|整一批|整点|来点|补点|爆兵|拉一波|凑一波|攒一波|集结)", normalized):
            return False
        if re.search(r"(立刻|马上|立即|现在就|直接)", normalized):
            return False
        if re.search(r"(整一大批|整一批|整点|来点|补点|爆兵|拉一波|凑一波|攒一波)", normalized):
            return self.unit_registry.match_in_text(
                normalized,
                queue_types=("Infantry", "Vehicle", "Aircraft", "Ship"),
            ) is not None
        return True

    @staticmethod
    def _looks_like_generic_enemy_base_attack(normalized: str) -> bool:
        enemy_base_tokens = (
            "敌方基地",
            "敌军基地",
            "敌人基地",
            "基地残留位置",
            "敌方残留位置",
            "敌军残留位置",
            "敌人残留位置",
            "残留位置",
        )
        return any(token in normalized for token in enemy_base_tokens)

    def _looks_like_force_then_generic_enemy_attack(self, normalized: str) -> bool:
        generic_enemy_target_tokens = (
            "敌方目标",
            "敌军目标",
            "敌人目标",
        )
        if not any(token in normalized for token in generic_enemy_target_tokens):
            return False
        return self.unit_registry.match_in_text(
            normalized,
            queue_types=("Infantry", "Vehicle", "Aircraft", "Ship"),
        ) is not None

    @staticmethod
    def _looks_like_complex_command(normalized_text: str) -> bool:
        return any(token in normalized_text for token in ("然后", "之后", "并且", "同时", "别", "不要", "如果", "优先"))

    def _match_deploy(self, normalized: str) -> Optional[RuleMatchResult]:
        if "基地车" not in normalized:
            return None
        if not self._looks_like_deploy_command(normalized):
            return None
        deploy_truth = self._deploy_truth_snapshot()
        if deploy_truth["ambiguous"]:
            return None
        actors = list(deploy_truth["mcv_actors"])
        if not actors:
            return None
        actor = actors[0]
        position = tuple(actor.get("position") or [0, 0])
        return RuleMatchResult(
            expert_type="DeployExpert",
            config=DeployJobConfig(actor_id=int(actor["actor_id"]), target_position=position),
            reason="rule_deploy_mcv",
        )

    def _match_repair(self, normalized: str) -> Optional[RuleMatchResult]:
        if self._looks_like_query(normalized):
            return None
        if not self._looks_like_repair_command(normalized):
            return None
        actor_ids = self._resolve_repair_actor_ids(normalized)
        if not actor_ids:
            return None
        return RuleMatchResult(
            expert_type="RepairExpert",
            config=RepairJobConfig(actor_ids=actor_ids),
            reason="rule_repair_units",
        )

    def _match_occupy(self, normalized: str) -> Optional[RuleMatchResult]:
        if not self._looks_like_occupy_command(normalized):
            return None
        actor_ids = self._resolve_occupy_actor_ids(normalized)
        if not actor_ids:
            return None
        target = self._resolve_occupy_target(normalized)
        if target is None or target.get("actor_id") is None:
            return None
        return RuleMatchResult(
            expert_type="OccupyExpert",
            config=OccupyJobConfig(actor_ids=actor_ids, target_actor_id=int(target["actor_id"])),
            reason="rule_occupy_target",
        )

    def _match_attack(self, normalized: str) -> Optional[RuleMatchResult]:
        if self._looks_like_query(normalized):
            return None
        if self._looks_like_retreat_command(normalized):
            return None
        if not self._looks_like_attack_command(normalized):
            return None
        if self._looks_like_attack_preparation_command(normalized):
            return None
        if self._looks_like_operator_wide_attack_command(normalized):
            actor_ids = self._resolve_operator_force_actor_ids(combat_only=True)
            target_position = self._best_enemy_attack_position()
            if actor_ids and target_position is not None:
                return RuleMatchResult(
                    expert_type="CombatExpert",
                    config=CombatJobConfig(
                        target_position=target_position,
                        engagement_mode=EngagementMode.ASSAULT,
                        wait_for_full_group=False,
                        actor_ids=actor_ids,
                        unit_count=0,
                    ),
                    reason="rule_attack_all_force",
                )
        target = self._resolve_attack_target(normalized)
        if target is None or target.get("actor_id") is None:
            if not self._looks_like_generic_enemy_base_attack(normalized):
                return None
            target_position = self._best_enemy_attack_position()
            if target_position is None:
                return None
            config = self._normalize_attack_config(
                normalized,
                CombatJobConfig(
                    target_position=target_position,
                    engagement_mode=EngagementMode.ASSAULT,
                    wait_for_full_group=False,
                    unit_count=0,
                ),
            )
            return RuleMatchResult(
                expert_type="CombatExpert",
                config=config,
                reason="rule_attack_position",
            )
        position = tuple(target.get("position") or [0, 0])
        if len(position) != 2:
            return None
        config = self._normalize_attack_config(
            normalized,
            CombatJobConfig(
                target_position=(int(position[0]), int(position[1])),
                engagement_mode=EngagementMode.ASSAULT,
                target_actor_id=int(target["actor_id"]),
                wait_for_full_group=False,
                unit_count=0,
            ),
        )
        return RuleMatchResult(
            expert_type="CombatExpert",
            config=config,
            reason="rule_attack_actor",
        )

    def _match_retreat(self, normalized: str) -> Optional[RuleMatchResult]:
        if self._looks_like_query(normalized):
            return None
        if not (
            self._looks_like_retreat_command(normalized)
            or self._looks_like_pullback_correction_command(normalized)
        ):
            return None
        actor_ids = self._resolve_retreat_actor_ids()
        target_position = self._best_retreat_position()
        if not actor_ids or target_position is None:
            return None
        return RuleMatchResult(
            expert_type="MovementExpert",
            config=MovementJobConfig(
                target_position=target_position,
                move_mode=MoveMode.RETREAT,
                arrival_radius=5,
                wait_for_full_group=False,
                actor_ids=actor_ids,
            ),
            reason="rule_retreat_to_base",
        )

    def _match_operator_move(self, normalized: str) -> Optional[RuleMatchResult]:
        if self._looks_like_query(normalized):
            return None
        if not self._looks_like_operator_wide_move_command(normalized):
            return None
        actor_ids = self._resolve_operator_force_actor_ids(combat_only=False)
        target_position = self._best_operator_move_target(normalized)
        if not actor_ids or target_position is None:
            return None
        return RuleMatchResult(
            expert_type="MovementExpert",
            config=MovementJobConfig(
                target_position=target_position,
                move_mode=MoveMode.MOVE,
                arrival_radius=10,
                wait_for_full_group=False,
                actor_ids=actor_ids,
                unit_count=0,
            ),
            reason="rule_move_all_force",
        )

    @staticmethod
    def _looks_like_deploy_command(normalized: str) -> bool:
        lowered = normalized.lower()
        return any(keyword in normalized or keyword in lowered for keyword in _DEPLOY_KEYWORDS)

    @staticmethod
    def _looks_like_repair_command(normalized: str) -> bool:
        lowered = normalized.lower()
        if any(keyword in normalized or keyword in lowered for keyword in _REPAIR_KEYWORDS):
            return True
        if any(noun in normalized or noun in lowered for noun in _REPAIR_FACILITY_NOUNS):
            return False
        return any(verb in normalized or verb in lowered for verb in _REPAIR_VERBS)

    @staticmethod
    def _looks_like_occupy_command(normalized: str) -> bool:
        lowered = normalized.lower()
        return any(keyword in normalized or keyword in lowered for keyword in _OCCUPY_KEYWORDS)

    @staticmethod
    def _looks_like_attack_command(normalized: str) -> bool:
        lowered = normalized.lower()
        return any(keyword in normalized or keyword in lowered for keyword in _ATTACK_KEYWORDS)

    @staticmethod
    def _looks_like_vague_combat_command(normalized: str) -> bool:
        return bool(re.fullmatch(r"(你)?(打|上|开打|出击|进攻)[啊呀吧呗啦了!！。]*", normalized))

    @staticmethod
    def _looks_like_operator_scope(normalized: str) -> bool:
        return bool(
            re.search(
                r"(全军|全员|全部|所有(?:部队|兵力|单位)?|现有单位|现有部队|家里的兵都|我方所有|我军所有)",
                normalized,
            )
        )

    @classmethod
    def _looks_like_operator_wide_attack_command(cls, normalized: str) -> bool:
        return cls._looks_like_attack_command(normalized) and cls._looks_like_operator_scope(normalized)

    @classmethod
    def _looks_like_operator_wide_move_command(cls, normalized: str) -> bool:
        if not cls._looks_like_operator_scope(normalized):
            return False
        if not re.search(r"(移动|拉到|拉去|集结|集合|过去|前往|去到|开到)", normalized):
            return False
        return bool(re.search(r"(地图中间|地图中央|中间|中央)", normalized))

    @staticmethod
    def _looks_like_retreat_command(normalized: str) -> bool:
        lowered = normalized.lower()
        if not any(keyword in normalized or keyword in lowered for keyword in _RETREAT_KEYWORDS):
            return False
        if any(keyword in normalized or keyword in lowered for keyword in _RETREAT_BASE_HINTS):
            return True
        # Repeated retreat shouts like "撤退撤退撤退" or "全军撤退全军撤退..."
        # should be handled as direct retreat commands instead of falling back
        # to a generic managed task that may request fresh units.
        if normalized.count("撤退") >= 2:
            return True
        return bool(
            re.fullmatch(
                r"(全军|全部|所有|都|快|立即|马上|立刻|先)?"
                r"(撤退|后撤|回撤|撤回|撤军|退兵|退回去|退回来)+"
                r"[了啊吧呀嘛吗！!。]*",
                normalized,
            )
        )

    @staticmethod
    def _looks_like_pullback_correction_command(normalized: str) -> bool:
        if any(token in normalized for token in ("拉回来", "都回来", "拉回基地")):
            return True
        return bool(re.search(r"(别去那(?:里|边)?了?|别往那边走|不要往那边走|别再往那边走)", normalized))

    def _resolve_retreat_actor_ids(self) -> list[int]:
        actor_ids = self._active_task_actor_ids()
        if actor_ids:
            return actor_ids
        try:
            payload = self.world_model.query("my_actors")
        except Exception:
            logger.exception("Failed to inspect retreat actor candidates")
            return []
        actors = self._trusted_query_actors(payload)
        return [
            int(actor["actor_id"])
            for actor in actors
            if self._is_mobile_combat_actor(actor)
        ]

    def _resolve_operator_force_actor_ids(self, *, combat_only: bool) -> list[int]:
        try:
            payload = self.world_model.query("my_actors")
        except Exception:
            logger.exception("Failed to inspect operator force actor candidates")
            return []
        actors = self._trusted_query_actors(payload)
        actor_ids: list[int] = []
        for actor in actors:
            if not self._is_live_actor(actor):
                continue
            if combat_only:
                if not self._is_mobile_combat_actor(actor):
                    continue
            else:
                if not self._is_mobile_combat_actor(actor):
                    continue
            actor_ids.append(int(actor["actor_id"]))
        return actor_ids

    def _best_operator_move_target(self, normalized: str) -> Optional[tuple[int, int]]:
        del normalized
        try:
            payload = self.world_model.query("map")
        except Exception:
            payload = None
        if isinstance(payload, dict):
            width = self._coerce_int(payload.get("width"))
            height = self._coerce_int(payload.get("height"))
            if width is not None and height is not None and width > 100 and height > 100:
                return (width // 2, height // 2)
        summary = self._get_world_summary()
        game_map = dict((summary or {}).get("map") or {})
        width = self._coerce_int(game_map.get("width"))
        height = self._coerce_int(game_map.get("height"))
        if width is not None and height is not None and width > 100 and height > 100:
            return (width // 2, height // 2)
        return (50, 50)

    def _active_task_actor_ids(self) -> list[int]:
        try:
            runtime_state = self.kernel.runtime_state() or {}
        except Exception:
            logger.exception("Failed to inspect runtime state for retreat actors")
            return []
        active_tasks = runtime_state.get("active_tasks") or {}
        actor_ids: list[int] = []
        seen: set[int] = set()
        for task in active_tasks.values():
            if bool(task.get("is_capability")):
                continue
            for raw_actor_id in list(task.get("active_actor_ids") or []):
                actor_id = self._coerce_int(raw_actor_id)
                if actor_id is None or actor_id in seen:
                    continue
                seen.add(actor_id)
                actor_ids.append(actor_id)
        return actor_ids

    def _is_mobile_combat_actor(self, actor: dict[str, Any]) -> bool:
        category = str(actor.get("category") or "").lower()
        if category == "building":
            return False
        text = " ".join(
            str(actor.get(key) or "")
            for key in ("display_name", "name", "type", "unit_type", "category")
        )
        if re.search(r"(harv|mcv|矿车|基地车)", text, re.IGNORECASE):
            return False
        if actor.get("can_attack") is False:
            return False
        if category in {"infantry", "vehicle", "aircraft", "ship"}:
            return True
        entry = self.unit_registry.match_in_text(text, queue_types=("Infantry", "Vehicle", "Aircraft", "Ship"))
        if entry is None:
            return False
        return normalize_production_name(entry.unit_id) not in {"harv", "mcv"}

    def _best_retreat_position(self) -> Optional[tuple[int, int]]:
        construction_yards = [
            actor
            for actor in self._query_self_actor_snapshot()
            if self._is_construction_yard_actor(actor) and actor.get("position")
        ]
        if not construction_yards:
            try:
                payload = self.world_model.query("my_actors", {"type": "建造厂"})
            except Exception:
                logger.exception("Failed to inspect construction yard for retreat target")
                return None
            construction_yards = [
                actor
                for actor in self._trusted_query_actors(payload)
                if actor.get("position")
            ]
        if not construction_yards:
            return None
        base_position = construction_yards[0].get("position") or [0, 0]
        if len(base_position) != 2:
            return None
        bx = self._coerce_int(base_position[0])
        by = self._coerce_int(base_position[1])
        if bx is None or by is None:
            return None
        candidates = [
            (bx - 2, by + 2),
            (bx + 2, by + 2),
            (bx - 2, by - 2),
            (bx + 2, by - 2),
            (bx, by + 4),
            (bx + 4, by),
            (bx - 4, by),
            (bx, by - 4),
        ]
        for candidate in candidates:
            if candidate != (bx, by):
                return candidate
        return None

    def _deploy_truth_snapshot(self) -> dict[str, Any]:
        actor_snapshot = self._query_self_actor_snapshot()
        mcv_actors = [
            dict(actor)
            for actor in actor_snapshot
            if self._is_mcv_actor(actor) and self._is_live_actor(actor)
        ]
        construction_yard_actors = [
            dict(actor)
            for actor in actor_snapshot
            if self._is_construction_yard_actor(actor)
        ]
        live_construction_yards = [
            dict(actor)
            for actor in construction_yard_actors
            if self._is_live_actor(actor)
        ]

        facts_mcv_count: Optional[int] = None
        facts_has_construction_yard: Optional[bool] = None
        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if callable(compute_runtime_facts):
            try:
                runtime_facts = compute_runtime_facts("__adjutant__", include_buildable=False) or {}
            except Exception:
                logger.exception("Failed to compute deploy runtime facts")
            else:
                if "mcv_count" in runtime_facts:
                    try:
                        facts_mcv_count = int(runtime_facts.get("mcv_count", 0) or 0)
                    except (TypeError, ValueError):
                        facts_mcv_count = 0
                if "has_construction_yard" in runtime_facts:
                    facts_has_construction_yard = bool(runtime_facts.get("has_construction_yard", False))

        query_mcv_count = len(mcv_actors)
        query_has_construction_yard = bool(live_construction_yards)
        # Escalate whenever runtime facts and the actor snapshot disagree on MCV
        # count, or when world sync has not produced an initialized snapshot yet.
        ambiguous = False
        refresh_health = getattr(self.world_model, "refresh_health", None)
        if callable(refresh_health):
            try:
                health = refresh_health() or {}
            except Exception:
                logger.exception("Failed to read world refresh health")
                ambiguous = True
            else:
                try:
                    if float(health.get("timestamp") or 0.0) <= 0.0:
                        ambiguous = True
                except (TypeError, ValueError):
                    ambiguous = True
        if not ambiguous and facts_mcv_count is not None and facts_mcv_count != query_mcv_count:
            ambiguous = True
        if construction_yard_actors and not live_construction_yards:
            has_construction_yard = False
        else:
            has_construction_yard = (
                facts_has_construction_yard
                if facts_has_construction_yard is not None
                else query_has_construction_yard
            )
        return {
            "mcv_actors": mcv_actors,
            "mcv_count": facts_mcv_count if facts_mcv_count is not None else query_mcv_count,
            "has_construction_yard": bool(has_construction_yard),
            "ambiguous": ambiguous,
        }

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
    def _world_sync_detail_text(world_sync: dict[str, Any]) -> str:
        try:
            failure_count = max(int(world_sync.get("consecutive_failures", 0) or 0), 0)
        except Exception:
            failure_count = 0
        try:
            failure_threshold = max(int(world_sync.get("failure_threshold", 0) or 0), 0)
        except Exception:
            failure_threshold = 0
        error = str(
            world_sync.get("last_error")
            or world_sync.get("last_refresh_error")
            or ""
        ).strip()
        if error:
            compact = " ".join(error.split())
            error = f"{compact[:93]}..." if len(compact) > 96 else compact

        parts: list[str] = []
        if failure_count:
            if failure_threshold:
                parts.append(f"连续失败 {failure_count}/{failure_threshold}")
            else:
                parts.append(f"连续失败 {failure_count}")
        if error:
            parts.append(f"最近错误：{error}")
        return "；".join(parts)

    @classmethod
    def _stale_world_response_text(cls, kind: str, world_sync: Optional[dict[str, Any]] = None) -> str:
        detail = cls._world_sync_detail_text(dict(world_sync or {}))
        if kind == "query":
            base = "当前游戏状态同步异常，暂时无法可靠回答，请稍后重试"
        else:
            base = "当前游戏状态同步异常，已暂停执行以避免基于旧状态误操作，请稍后重试"
        return f"{base}（{detail}）" if detail else base

    def _stale_world_guard(self, kind: str) -> dict[str, Any]:
        world_sync = {}
        refresh_health = getattr(self.world_model, "refresh_health", None)
        if callable(refresh_health):
            try:
                health = refresh_health() or {}
                if isinstance(health, dict):
                    world_sync = dict(health)
            except Exception:
                logger.exception("Failed to read world refresh health")
        try:
            world_sync_failures = int(world_sync.get("consecutive_failures", 0) or 0)
        except Exception:
            world_sync_failures = 0
        try:
            world_sync_failure_threshold = int(world_sync.get("failure_threshold", 0) or 0)
        except Exception:
            world_sync_failure_threshold = 0
        world_sync_error = str(
            world_sync.get("last_error")
            or world_sync.get("last_refresh_error")
            or ""
        )
        return {
            "type": kind,
            "ok": False,
            "response_text": self._stale_world_response_text(kind, world_sync),
            "routing": "stale_guard",
            "reason": "world_sync_stale",
            "world_sync_error": world_sync_error,
            "world_sync_failures": world_sync_failures,
            "world_sync_failure_threshold": world_sync_failure_threshold,
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

    def _resolve_attack_target(self, normalized_text: str) -> Optional[dict[str, Any]]:
        payload = self.world_model.query("enemy_actors")
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        target = self._match_explicit_enemy_target(normalized_text, actors)
        if target and target.get("position"):
            return target
        return None

    def _best_enemy_attack_position(self) -> Optional[tuple[int, int]]:
        payload = self.world_model.query("enemy_actors", {"category": "building"})
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        if not actors:
            payload = self.world_model.query("enemy_actors")
            actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        targets = [actor for actor in actors if actor.get("position")]
        if not targets:
            summary = self._get_world_summary()
            frozen = (summary or {}).get("known_enemy", {}).get("frozen_positions", [])
            targets = [target for target in frozen if target.get("position")]
        if not targets:
            return None
        my_base = self.world_model.query("my_actors", {"type": "建造厂"})
        base_actors = list((my_base or {}).get("actors", [])) if isinstance(my_base, dict) else []
        if base_actors:
            bx, by = base_actors[0].get("position", [0, 0])
            targets.sort(
                key=lambda actor: sum(
                    (c1 - c2) ** 2 for c1, c2 in zip(actor.get("position", [0, 0]), [bx, by])
                )
            )
        pos = tuple(targets[0].get("position", [0, 0]))
        if len(pos) != 2:
            return None
        return (int(pos[0]), int(pos[1]))

    def _match_build(self, normalized: str) -> Optional[RuleMatchResult]:
        if not normalized.startswith(("建造", "修建", "造")):
            return None
        # Rule-based build fast-path is intentionally narrow: it only owns
        # single-target, count=1 structure commands. Multi-target or counted
        # phrases must fail closed to NLU/Capability so we do not silently
        # drop quantity or start the wrong building from one alias match.
        if self._extract_requested_count(normalized) > 1:
            return None
        if self._has_multiple_production_targets(
            normalized,
            queue_types=("Building", "Defense", "Infantry", "Vehicle", "Aircraft", "Ship"),
        ):
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
            scout_count = self._extract_requested_scout_count(normalized)
            return RuleMatchResult(
                expert_type="ReconExpert",
                config=ReconJobConfig(
                    search_region="enemy_half",
                    target_type="base",
                    target_owner="enemy",
                    retreat_hp_pct=0.3,
                    avoid_combat=True,
                    scout_count=scout_count,
                ),
                reason="rule_recon",
            )
        return None

    @staticmethod
    def _extract_requested_scout_count(normalized: str) -> int:
        count = Adjutant._extract_requested_count(normalized)
        if count <= 1 and re.search(r"(所有|全部|全军|都去|都派|全体|全员|家里的兵都)", normalized):
            return 10
        return count

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

    def _has_multiple_production_targets(
        self,
        normalized: str,
        *,
        queue_types: Iterable[str],
    ) -> bool:
        allowed = {str(queue).lower() for queue in queue_types}
        mentioned: set[str] = set()
        for entry in self.unit_registry.entries():
            if entry.queue_type.lower() not in allowed:
                continue
            for alias in [entry.display_name, entry.unit_id, entry.unit_id.lower(), *entry.aliases]:
                alias_text = normalize_registry_name(alias)
                if alias_text and alias_text in normalized:
                    mentioned.add(entry.unit_id.upper())
                    break
            if len(mentioned) >= 2:
                return True
        return False

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

    def _has_repair_facility(self) -> bool:
        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if callable(compute_runtime_facts):
            try:
                facts = compute_runtime_facts("__adjutant__", include_buildable=False) or {}
                if int(facts.get("repair_facility_count") or 0) > 0:
                    return True
            except Exception:
                logger.exception("Failed to inspect repair facility count")
        try:
            payload = self.world_model.query("my_actors", {"type": "维修厂"})
            actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
            return bool(actors)
        except Exception:
            return False

    @staticmethod
    def _repair_target_is_building(actor: dict[str, Any]) -> bool:
        return str(actor.get("category") or "").lower() == "building"

    def _resolve_repair_targets(self, normalized: str) -> list[dict[str, Any]]:
        entry = self.unit_registry.match_in_text(normalized, queue_types=("Vehicle", "Building"))
        name_candidates: list[str] = []
        if entry is not None:
            for candidate in [entry.display_name, *entry.aliases]:
                if candidate and candidate not in name_candidates:
                    name_candidates.append(candidate)

        query_candidates: list[dict[str, Any]] = [{"name": name} for name in name_candidates]
        query_candidates.append({})

        for params in query_candidates:
            try:
                payload = self.world_model.query("my_actors", params)
            except Exception:
                logger.exception("Failed to inspect repair targets")
                return []
            actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
            damaged_targets: list[dict[str, Any]] = []
            for actor in actors:
                if str(actor.get("category") or "").lower() == "infantry":
                    continue
                hp = self._coerce_float(actor.get("hp"))
                hp_max = self._coerce_float(actor.get("hp_max"))
                if hp is None or hp_max is None or hp_max <= 0:
                    continue
                if hp < hp_max:
                    actor_id = actor.get("actor_id")
                    if actor_id is not None:
                        damaged_targets.append(dict(actor, actor_id=int(actor_id)))
            if damaged_targets:
                return damaged_targets
        return []

    def _resolve_repair_actor_ids(self, normalized: str) -> list[int]:
        return [int(actor["actor_id"]) for actor in self._resolve_repair_targets(normalized)]

    def _repair_requires_facility(
        self,
        normalized: str,
        *,
        targets: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        if targets:
            return any(not self._repair_target_is_building(actor) for actor in targets)
        entry = self.unit_registry.match_in_text(normalized, queue_types=("Vehicle", "Building"))
        if entry is not None and entry.queue_type.lower() == "building":
            return False
        return True

    def _resolve_occupy_actor_ids(self, normalized: str) -> list[int]:
        del normalized
        try:
            payload = self.world_model.query("my_actors", {"name": "工程师"})
        except Exception:
            logger.exception("Failed to inspect occupy engineers")
            return []
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        actor_ids: list[int] = []
        for actor in actors:
            actor_id = actor.get("actor_id")
            if actor_id is not None:
                actor_ids.append(int(actor_id))
        return actor_ids

    def _resolve_occupy_target(self, normalized: str) -> Optional[dict[str, Any]]:
        try:
            payload = self.world_model.query("enemy_actors", {"category": "building"})
        except Exception:
            logger.exception("Failed to inspect occupy target")
            return None
        actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
        return self._match_explicit_enemy_target(normalized, actors)

    def _record_capability_nlu_note(self, text: str, expert_type: str) -> None:
        """Record NLU fast-path history on EconomyCapability without waking it as a player command."""
        if expert_type != "EconomyExpert":
            return
        cap_id = getattr(self.kernel, "capability_task_id", None)
        if not cap_id:
            return
        note_text = f"[NLU直达] 玩家命令已执行: {text}"
        self.kernel.record_capability_note(note_text)
        self.kernel.register_task_message(
            TaskMessage(
                message_id=f"info_nlu_{int(time.time() * 1000)}",
                task_id=cap_id,
                type=TaskMessageType.TASK_INFO,
                content=note_text,
                priority=80,
            )
        )

    def _start_capability_economy_job(self, raw_text: str, config: Any) -> tuple[Any, Any] | None:
        """Start a concrete EconomyExpert job under EconomyCapability instead of a standalone task."""
        cap_id = getattr(self.kernel, "capability_task_id", None)
        if not cap_id:
            return None
        task = next((item for item in self.kernel.list_tasks() if getattr(item, "task_id", None) == cap_id), None)
        if task is None:
            return None
        job = self.kernel.start_job(cap_id, "EconomyExpert", config)
        self._record_capability_nlu_note(raw_text, "EconomyExpert")
        return task, job

    def _is_economy_command(self, text: str) -> bool:
        """Check if text is an economy/production command that should merge to Capability."""
        normalized = re.sub(r"\s+", "", text.strip())
        if _QUESTION_RE.search(normalized):
            return False  # Don't intercept questions like "经济怎么样"
        if _ECONOMY_COMMAND_RE.search(normalized):
            return True
        if normalized.startswith(("建造", "修建", "造", "生产", "训练", "补")):
            if self._has_multiple_production_targets(
                normalized,
                queue_types=("Building", "Defense", "Infantry", "Vehicle", "Aircraft", "Ship"),
            ):
                return True
            if self.unit_registry.match_in_text(
                normalized,
                queue_types=("Building", "Defense", "Infantry", "Vehicle", "Aircraft", "Ship"),
            ) is not None:
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
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(self._runtime_state_snapshot())
        capability_status = runtime_snapshot.capability_status
        recent_directives = list(capability_status.recent_directives)
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
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(self._runtime_state_snapshot())
        capability_status = runtime_snapshot.capability_status
        slog.info("Merged economy command to Capability", event="capability_merge",
                  capability_task_id=cap_id, text=text)
        phase = capability_status.phase
        blocker = capability_status.blocker
        blocking_request_count = capability_status.blocking_request_count
        start_released_request_count = capability_status.start_released_request_count
        reinforcement_request_count = capability_status.reinforcement_request_count
        pending_request_count = capability_status.pending_request_count
        phase_text = {
            "bootstrapping": "正在补齐前置",
            "dispatch": "正在分发请求",
            "fulfilling": "已满足启动条件，正在补强",
            "executing": "正在执行生产",
            "directive_pending": "收到持续目标，待推进",
            "idle": "待命中",
        }.get(phase, "")
        blocker_text = {
            "world_sync_stale": "世界状态同步陈旧",
            "request_inference_pending": "存在待解析的单位请求",
            "deploy_required": "需先展开基地车",
            "missing_prerequisite": "部分请求缺少前置建筑",
            "low_power": "当前低电，优先恢复供电",
            "producer_disabled": "对应生产建筑离线/停用",
            "queue_blocked": "生产队列存在阻塞",
            "insufficient_funds": "当前资金不足",
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
        unit_pipeline_preview = build_runtime_unit_pipeline_preview(runtime_snapshot.to_dict())
        if unit_pipeline_preview:
            summary_parts.append(f"在途 {unit_pipeline_preview}")
        unit_pipeline_snapshot = {
            "unit_pipeline_focus": build_runtime_unit_pipeline_focus(runtime_snapshot.to_dict()),
            "unit_pipeline_preview_items": build_runtime_unit_pipeline_preview_items(runtime_snapshot.to_dict(), limit=3),
        }
        secondary_pipeline_preview = self._secondary_unit_pipeline_preview_text(unit_pipeline_snapshot)
        if secondary_pipeline_preview:
            summary_parts.append(f"其他在途 {secondary_pipeline_preview}")
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
        preempted_labels: list[str] = []
        if self._rule_requires_retreat_preemption(match):
            preempted_labels = self._cancel_conflicting_tasks_for_retreat(text)
        elif self._rule_requires_operator_preemption(match):
            preempted_labels = self._cancel_conflicting_tasks_for_operator_override(text)
        world_warning = self._check_rule_preconditions(match)
        try:
            if match.expert_type == "EconomyExpert":
                started = self._start_capability_economy_job(text, match.config)
                if started is not None:
                    task, job = started
                else:
                    task, job = self._start_direct_job(text, match.expert_type, match.config)
            else:
                task, job = self._start_direct_job(text, match.expert_type, match.config)
            slog.info(
                "Adjutant rule matched",
                event="rule_routed_command",
                raw_text=text,
                task_id=task.task_id,
                job_id=job.job_id,
                expert_type=match.expert_type,
                reason=match.reason,
                preempted_task_labels=preempted_labels,
                world_warning=world_warning,
            )
            if match.expert_type == "EconomyExpert" and getattr(task, "is_capability", False):
                response_text = "收到指令，已交给经济规划直接执行"
            else:
                response_text = f"收到指令，已直接执行并创建任务 {task.task_id}"
            if preempted_labels:
                response_text = f"已取消任务 #{'、#'.join(preempted_labels)}，并{response_text}"
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
                "preempted_task_labels": preempted_labels,
                "world_warning": world_warning,
            }
        except Exception as e:
            logger.exception("Rule-routed command failed: %r", text)
            return {
                "type": "command",
                "ok": False,
                "response_text": f"规则执行失败: {e}",
                "routing": "rule",
                "preempted_task_labels": preempted_labels,
            }

    @staticmethod
    def _rule_requires_retreat_preemption(match: RuleMatchResult) -> bool:
        if match.expert_type != "MovementExpert":
            return False
        return getattr(match.config, "move_mode", None) == MoveMode.RETREAT

    @staticmethod
    def _rule_requires_operator_preemption(match: RuleMatchResult) -> bool:
        return match.reason in {"rule_attack_all_force", "rule_move_all_force"}

    def _cancel_conflicting_tasks_for_retreat(self, text: str) -> list[str]:
        context = self._build_context(text)
        preempted_labels: list[str] = []
        seen_task_ids: set[str] = set()
        for task_entry in context.active_tasks:
            task_id = str(task_entry.get("task_id", "") or "")
            if not task_id or task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
            if bool(task_entry.get("is_capability")):
                continue
            if not self._task_conflicts_with_retreat(task_entry):
                continue
            if not self.kernel.cancel_task(task_id):
                continue
            label = str(task_entry.get("label", "") or task_id)
            preempted_labels.append(label)
        return preempted_labels

    def _cancel_conflicting_tasks_for_operator_override(self, text: str) -> list[str]:
        context = self._build_context(text)
        preempted_labels: list[str] = []
        seen_task_ids: set[str] = set()
        for task_entry in context.active_tasks:
            task_id = str(task_entry.get("task_id", "") or "")
            if not task_id or task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
            if bool(task_entry.get("is_capability")):
                continue
            if not self._task_conflicts_with_operator_override(task_entry):
                continue
            if not self.kernel.cancel_task(task_id):
                continue
            label = str(task_entry.get("label", "") or task_id)
            preempted_labels.append(label)
        return preempted_labels

    @staticmethod
    def _task_conflicts_with_retreat(task_entry: dict[str, Any]) -> bool:
        active_expert = str(task_entry.get("active_expert", "") or "")
        workflow_template = str(task_entry.get("workflow_template", "") or "")
        domain = str(task_entry.get("domain", "") or "")
        active_group_size = int(task_entry.get("active_group_size", 0) or 0)
        if active_expert in {"CombatExpert", "ReconExpert"}:
            return True
        if active_expert == "MovementExpert" and active_group_size > 0:
            return True
        if workflow_template in {PRODUCE_UNITS_THEN_ATTACK, PRODUCE_UNITS_THEN_RECON}:
            return True
        return domain in {"combat", "recon"}

    @staticmethod
    def _task_conflicts_with_operator_override(task_entry: dict[str, Any]) -> bool:
        active_expert = str(task_entry.get("active_expert", "") or "")
        workflow_template = str(task_entry.get("workflow_template", "") or "")
        domain = str(task_entry.get("domain", "") or "")
        active_group_size = int(task_entry.get("active_group_size", 0) or 0)
        if active_group_size > 0:
            return True
        if active_expert in {"CombatExpert", "ReconExpert", "MovementExpert", "OccupyExpert", "RepairExpert"}:
            return True
        if workflow_template in {PRODUCE_UNITS_THEN_ATTACK, PRODUCE_UNITS_THEN_RECON}:
            return True
        return domain in {"combat", "recon", "movement", "defense"}

    async def _handle_runtime_nlu(self, text: str, decision: RuntimeNLUDecision) -> dict[str, Any]:
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        if self._runtime_nlu_economy_steps_require_capability_merge(decision):
            fallback = self._try_merge_to_capability(text)
            if fallback is not None:
                fallback["routing"] = "capability_merge"
                fallback.update(self._nlu_result_meta(decision))
                self._record_nlu_decision(text, decision, execution_success=bool(fallback.get("ok", False)))
                return fallback
            result = {
                "type": "command",
                "ok": False,
                "response_text": "该生产指令包含当前不可立即下单的项目，已拒绝 NLU 直达执行",
                "routing": "nlu",
            }
            result.update(self._nlu_result_meta(decision))
            self._record_nlu_decision(text, decision, execution_success=False)
            return result
        created: list[dict[str, str]] = []
        preempted_labels: list[str] = []
        is_sequence = decision.route_intent == "composite_sequence"
        try:
            for step_idx, step in enumerate(decision.steps):
                if step.expert_type == "__QUERY_ACTOR__":
                    result = self._handle_runtime_nlu_query_actor(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                if step.expert_type == "__MINE__":
                    result = await self._handle_runtime_nlu_mine(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                if step.expert_type == "__STOP_ATTACK__":
                    result = await self._handle_runtime_nlu_stop_attack(text, decision, step)
                    self._record_nlu_decision(text, decision, execution_success=bool(result.get("ok", False)))
                    return result
                match = self._resolve_runtime_nlu_step(step)
                task_text = step.source_text or text
                normalized_task_text = re.sub(r"\s+", "", task_text.strip())
                if match.expert_type == "CombatExpert" and self._looks_like_operator_wide_attack_command(normalized_task_text):
                    actor_ids = list(getattr(match.config, "actor_ids", None) or [])
                    if not actor_ids:
                        result = {
                            "type": "command",
                            "ok": False,
                            "response_text": "当前没有可统一调度的作战单位可执行全员出击",
                            "routing": "nlu",
                        }
                        result.update(self._nlu_result_meta(decision))
                        self._record_nlu_decision(text, decision, execution_success=False)
                        return result
                    if not preempted_labels:
                        preempted_labels = self._cancel_conflicting_tasks_for_operator_override(task_text)
                if match.expert_type == "EconomyExpert" and not is_sequence:
                    started = self._start_capability_economy_job(task_text, match.config)
                    if started is not None:
                        task, job = started
                    else:
                        task, job = self._start_direct_job(task_text, match.expert_type, match.config)
                else:
                    task, job = self._start_direct_job(task_text, match.expert_type, match.config)
                    self._record_capability_nlu_note(task_text, match.expert_type)
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
                    if preempted_labels:
                        result["response_text"] = f"已取消任务 #{'、#'.join(preempted_labels)}，并{result['response_text']}"
                        result["preempted_task_labels"] = preempted_labels
                    result.update(self._nlu_result_meta(decision))
                    self._record_nlu_decision(text, decision, execution_success=True)
                    return result
            if len(created) == 1:
                task = created[0]
                response_text = (
                    "收到指令，已交给经济规划直接执行"
                    if task["expert_type"] == "EconomyExpert"
                    and str(task["task_id"] or "") == str(getattr(self.kernel, "capability_task_id", "") or "")
                    else f"收到指令，已直接执行并创建任务 {task['task_id']}"
                )
                if preempted_labels:
                    response_text = f"已取消任务 #{'、#'.join(preempted_labels)}，并{response_text}"
                result = {
                    "type": "command",
                    "ok": True,
                    "task_id": task["task_id"],
                    "job_id": task["job_id"],
                    "response_text": response_text,
                    "routing": "nlu",
                    "expert_type": task["expert_type"],
                }
                if preempted_labels:
                    result["preempted_task_labels"] = preempted_labels
                result.update(self._nlu_result_meta(decision))
                self._record_nlu_decision(text, decision, execution_success=True)
                return result
            task_ids = [item["task_id"] for item in created]
            response_text = f"收到指令，已拆解并直接执行 {len(created)} 个任务：{'、'.join(task_ids)}"
            if preempted_labels:
                response_text = f"已取消任务 #{'、#'.join(preempted_labels)}，并{response_text}"
            result = {
                "type": "command",
                "ok": True,
                "task_ids": task_ids,
                "steps": created,
                "response_text": response_text,
                "routing": "nlu",
            }
            if preempted_labels:
                result["preempted_task_labels"] = preempted_labels
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

    async def _handle_runtime_nlu_mine(
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
        await asyncio.to_thread(
            self.game_api.deploy_units,
            [GameActor(int(actor["actor_id"])) for actor in actors],
        )
        return {
            "type": "command",
            "ok": True,
            "response_text": f"收到指令，已让 {len(actors)} 辆采矿车恢复采矿",
            "routing": "nlu",
            **self._nlu_result_meta(decision),
        }

    async def _handle_runtime_nlu_stop_attack(
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
        await asyncio.to_thread(
            self.game_api.stop,
            [GameActor(int(actor["actor_id"])) for actor in actors],
        )
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
        deploy_truth = self._deploy_truth_snapshot()
        if deploy_truth["ambiguous"]:
            raise RuntimeError("基地车状态同步中，请稍后重试")
        actors = list(deploy_truth["mcv_actors"])
        if not actors:
            if deploy_truth["has_construction_yard"]:
                raise RuntimeError("建造厂已存在，当前无基地车可部署")
            raise RuntimeError("当前没有可部署的基地车")
        actor = actors[0]
        position = tuple(actor.get("position") or [0, 0])
        return RuleMatchResult(
            expert_type="DeployExpert",
            config=DeployJobConfig(actor_id=int(actor["actor_id"]), target_position=position),
            reason=step.reason,
        )

    def _runtime_nlu_economy_steps_require_capability_merge(self, decision: RuntimeNLUDecision) -> bool:
        produce_steps = [
            step
            for step in decision.steps
            if step.expert_type == "EconomyExpert" and isinstance(step.config, EconomyJobConfig)
        ]
        if not produce_steps:
            return False
        if len(produce_steps) != len(decision.steps):
            return False

        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if not callable(compute_runtime_facts):
            return False
        try:
            runtime_facts = compute_runtime_facts("__adjutant__", include_buildable=True) or {}
        except Exception:
            return False

        buildable_now = runtime_facts.get("buildable_now")
        if not isinstance(buildable_now, dict):
            return False

        available_by_queue: dict[str, set[str]] = {}
        for raw_queue_type, raw_items in buildable_now.items():
            queue_type = str(raw_queue_type or "").strip()
            if not queue_type:
                continue
            available = available_by_queue.setdefault(queue_type, set())
            for raw_item in list(raw_items or []):
                if isinstance(raw_item, dict):
                    unit_value = raw_item.get("unit_type") or raw_item.get("unit")
                else:
                    unit_value = raw_item
                unit_type = normalize_production_name(unit_value)
                if unit_type:
                    available.add(unit_type)

        for step in produce_steps:
            queue_type = str(getattr(step.config, "queue_type", "") or "").strip()
            unit_type = normalize_production_name(getattr(step.config, "unit_type", ""))
            if not queue_type or not unit_type:
                return True
            if unit_type not in available_by_queue.get(queue_type, set()):
                return True
        return False

    def _resolve_attack_step(self, step: DirectNLUStep) -> RuleMatchResult:
        """Resolve attack target_position from world model when NLU sets (0,0)."""
        config = self._normalize_attack_config(step.source_text, step.config)
        if config.target_position != (0, 0):
            return RuleMatchResult(expert_type=step.expert_type, config=config, reason=step.reason)
        visible_payload = self.world_model.query("enemy_actors")
        visible_actors = list((visible_payload or {}).get("actors", [])) if isinstance(visible_payload, dict) else []
        explicit_target = self._match_explicit_enemy_target(step.source_text, visible_actors)
        if explicit_target and explicit_target.get("position"):
            pos = tuple(explicit_target.get("position", [0, 0]))
            config = CombatJobConfig(
                target_position=pos,
                engagement_mode=config.engagement_mode,
                max_chase_distance=config.max_chase_distance,
                retreat_threshold=config.retreat_threshold,
                target_actor_id=int(explicit_target["actor_id"]),
                wait_for_full_group=config.wait_for_full_group,
                min_ready_count=config.min_ready_count,
                actor_ids=config.actor_ids,
                unit_count=config.unit_count,
            )
            return RuleMatchResult(expert_type=step.expert_type, config=config, reason=step.reason)
        # Auto-target: find nearest enemy building, fall back to any enemy actor, then frozen
        pos = self._best_enemy_attack_position()
        if pos is not None:
            config = CombatJobConfig(
                target_position=pos,
                engagement_mode=config.engagement_mode,
                max_chase_distance=config.max_chase_distance,
                retreat_threshold=config.retreat_threshold,
                wait_for_full_group=config.wait_for_full_group,
                min_ready_count=config.min_ready_count,
                actor_ids=config.actor_ids,
                unit_count=config.unit_count,
            )
        # If no enemies found, keep (0,0) — CombatExpert will handle "no visible enemy"
        return RuleMatchResult(expert_type=step.expert_type, config=config, reason=step.reason)

    def _normalize_attack_config(self, source_text: str, config: CombatJobConfig) -> CombatJobConfig:
        actor_ids = list(config.actor_ids or []) or None
        unit_count = int(getattr(config, "unit_count", 0) or 0)
        if unit_count <= 1 and not self._has_explicit_attack_unit_count(source_text):
            unit_count = 0
        if self._looks_like_operator_wide_attack_command(re.sub(r"\s+", "", str(source_text or ""))):
            operator_force_actor_ids = self._resolve_operator_force_actor_ids(combat_only=True)
            if operator_force_actor_ids:
                actor_ids = operator_force_actor_ids
                unit_count = 0
        preferred_actor_ids = self._resolve_preferred_attack_actor_ids(source_text)
        if preferred_actor_ids:
            actor_ids = preferred_actor_ids
            unit_count = 0
        if actor_ids == getattr(config, "actor_ids", None) and unit_count == int(getattr(config, "unit_count", 0) or 0):
            return config
        return CombatJobConfig(
            target_position=config.target_position,
            engagement_mode=config.engagement_mode,
            max_chase_distance=config.max_chase_distance,
            retreat_threshold=config.retreat_threshold,
            target_actor_id=config.target_actor_id,
            wait_for_full_group=False if actor_ids else config.wait_for_full_group,
            min_ready_count=config.min_ready_count,
            actor_ids=actor_ids,
            unit_count=unit_count,
        )

    def _resolve_preferred_attack_actor_ids(self, source_text: str) -> Optional[list[int]]:
        if not self._looks_like_air_attack_command(source_text):
            return None
        seen: set[int] = set()
        actor_ids: list[int] = []
        for params in ({"category": "vehicle"}, {"category": "aircraft"}):
            try:
                payload = self.world_model.query("my_actors", params)
            except Exception:
                logger.exception("Failed to inspect preferred attack actors")
                continue
            actors = list((payload or {}).get("actors", [])) if isinstance(payload, dict) else []
            for actor in actors:
                if not bool(actor.get("can_attack", True)):
                    continue
                if not self._actor_is_aircraft(actor):
                    continue
                actor_id = actor.get("actor_id")
                if actor_id is None:
                    continue
                actor_id_int = int(actor_id)
                if actor_id_int in seen:
                    continue
                seen.add(actor_id_int)
                actor_ids.append(actor_id_int)
        return actor_ids or None

    def _actor_is_aircraft(self, actor: dict[str, Any]) -> bool:
        category = str(actor.get("category") or "").lower()
        if category == "aircraft":
            return True
        text = " ".join(
            str(actor.get(key) or "")
            for key in ("name", "display_name", "type", "unit_type")
        )
        if re.search(r"(飞机|米格|雅克|mig|yak)", text, re.IGNORECASE):
            return True
        return self.unit_registry.match_in_text(text, queue_types=("Aircraft",)) is not None

    @staticmethod
    def _has_explicit_attack_unit_count(text: str) -> bool:
        normalized = re.sub(r"\s+", "", str(text or ""))
        return bool(re.search(r"([0-9]+|[一二两三四五六七八九十百几]+)(个|架|辆|台|名|队|组)", normalized))

    @staticmethod
    def _looks_like_air_attack_command(text: str) -> bool:
        return bool(re.search(r"(飞机|米格|雅克|mig|yak|空袭|空军)", str(text or ""), re.IGNORECASE))

    @staticmethod
    def _match_explicit_enemy_target(text: str, actors: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        raw_text = str(text or "").strip().lower()
        if not raw_text:
            return None
        best_actor: Optional[dict[str, Any]] = None
        best_score = 0
        for actor in actors:
            score = Adjutant._explicit_enemy_target_score(raw_text, actor)
            if score <= 0:
                continue
            actor_id = int(actor.get("actor_id") or 0)
            if score > best_score or (score == best_score and best_actor is not None and actor_id < int(best_actor.get("actor_id") or 0)):
                best_score = score
                best_actor = actor
        return best_actor

    @staticmethod
    def _explicit_enemy_target_score(text: str, actor: dict[str, Any]) -> int:
        best = 0
        seen: set[str] = set()
        for raw_name in (actor.get("display_name"), actor.get("name")):
            for variant in production_name_variants(raw_name):
                token = str(variant or "").strip()
                lowered = token.lower()
                if not token or lowered in seen:
                    continue
                seen.add(lowered)
                if lowered not in text:
                    continue
                score = len(token)
                if any("\u4e00" <= ch <= "\u9fff" for ch in token):
                    score += 10
                elif len(token) >= 3:
                    score += 3
                best = max(best, score)
        return best

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

    _AFFIRMATIVE_WORDS: frozenset[str] = frozenset({"继续", "是", "好", "确认", "ok", "OK", "需要", "要", "可以", "行"})
    _NEGATIVE_WORDS: frozenset[str] = frozenset({"放弃", "否", "不", "取消", "cancel"})

    @staticmethod
    def _normalize_reply_token(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).strip().lower()

    def _split_multi_reply_segments(self, text: str) -> list[str]:
        return [segment.strip() for segment in _MULTI_REPLY_SPLIT_RE.split(text) if segment.strip()]

    def _match_reply_segment_to_option(self, segment: str, options: list[str]) -> Optional[str]:
        normalized_segment = self._normalize_reply_token(segment)
        if not normalized_segment or not options:
            return None

        exact_matches = [option for option in options if self._normalize_reply_token(option) == normalized_segment]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return None

        affirmative_tokens = {self._normalize_reply_token(word) for word in self._AFFIRMATIVE_WORDS}
        negative_tokens = {self._normalize_reply_token(word) for word in self._NEGATIVE_WORDS}
        if normalized_segment in affirmative_tokens:
            candidates = [
                option
                for option in options
                if any(token in self._normalize_reply_token(option) for token in affirmative_tokens)
            ]
            if len(candidates) == 1:
                return candidates[0]
        if normalized_segment in negative_tokens:
            candidates = [
                option
                for option in options
                if any(token in self._normalize_reply_token(option) for token in negative_tokens)
            ]
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _collect_multi_reply_matches(
        self,
        text: str,
        pending_questions: list[dict[str, Any]],
    ) -> list[tuple[str, str, str]]:
        segments = self._split_multi_reply_segments(text)
        if len(segments) < 2 or len(pending_questions) < 2 or len(segments) > len(pending_questions):
            return []

        matches: list[tuple[str, str, str]] = []
        for segment, question in zip(segments, pending_questions):
            message_id = str(question.get("message_id") or "")
            task_id = str(question.get("task_id") or "")
            options = [str(option) for option in (question.get("options") or []) if str(option).strip()]
            matched_option = self._match_reply_segment_to_option(segment, options)
            if not message_id or not task_id or not matched_option:
                return []
            matches.append((message_id, task_id, matched_option))
        return matches

    def _try_route_multi_reply(self, text: str) -> Optional[dict[str, Any]]:
        pending = self.kernel.list_pending_questions()
        matches = self._collect_multi_reply_matches(text, pending)
        if len(matches) < 2:
            return None

        delivered = 0
        first_error = ""
        for message_id, task_id, answer in matches:
            result = self.kernel.submit_player_response(
                PlayerResponse(
                    message_id=message_id,
                    task_id=task_id,
                    answer=answer,
                )
            )
            if result.get("ok", False):
                delivered += 1
            elif not first_error:
                first_error = str(result.get("message") or "")

        ok = delivered == len(matches)
        return {
            "type": "reply",
            "ok": ok,
            "status": "delivered_multi" if ok else "partial",
            "response_text": first_error or (f"已回复 {delivered} 个问题" if ok else f"已回复 {delivered} / {len(matches)} 个问题"),
            "answered_count": delivered,
            "question_count": len(matches),
        }

    def _try_route_single_reply(self, text: str) -> Optional[dict[str, Any]]:
        pending = self.kernel.list_pending_questions()
        if len(pending) != 1:
            return None

        question = pending[0]
        message_id = str(question.get("message_id") or "")
        task_id = str(question.get("task_id") or "")
        if not message_id or not task_id:
            return None

        options = [str(option) for option in (question.get("options") or []) if str(option).strip()]
        normalized = self._normalize_reply_token(text)
        if not normalized:
            return None

        matched_option = self._match_reply_segment_to_option(text, options) if options else None
        generic_reply_tokens = {
            self._normalize_reply_token(word)
            for word in (self._AFFIRMATIVE_WORDS | self._NEGATIVE_WORDS)
            if self._normalize_reply_token(word) not in {"取消", "cancel"}
        }
        if matched_option is None:
            if normalized not in generic_reply_tokens:
                return None
        answer = text.strip()

        result = self.kernel.submit_player_response(
            PlayerResponse(
                message_id=message_id,
                task_id=task_id,
                answer=answer,
            )
        )
        return {
            "type": "reply",
            "ok": result.get("ok", False),
            "status": result.get("status"),
            "response_text": result.get("message", "已回复"),
        }

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
            if len(self._collect_multi_reply_matches(text, pending)) >= 2:
                return ClassificationResult(
                    input_type=InputType.REPLY,
                    confidence=0.95,
                    raw_text=text,
                )
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
        """
        if not classification.target_message_id:
            multi_reply_result = self._try_route_multi_reply(classification.raw_text)
            if multi_reply_result is not None:
                return multi_reply_result

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
        battlefield_snapshot = self._context_battlefield_snapshot(context) or self._battlefield_snapshot()
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
        battlefield_snapshot = self._context_battlefield_snapshot(context) or self._battlefield_snapshot()
        target_task = self._select_info_target_task(text, classification, context, battlefield_snapshot)
        disposition = (classification.disposition or "").lower()
        target_is_capability = bool(getattr(target_task, "is_capability", False)) if target_task is not None else False

        if target_is_capability:
            slog.info(
                "Command disposition blocked for capability target",
                event="command_disposition_blocked_capability",
                disposition=disposition or "none",
                task_id=getattr(target_task, "task_id", ""),
                label=getattr(target_task, "label", ""),
            )
            if disposition == "merge" and self._is_economy_command(text):
                merged = self._try_merge_to_capability(text)
                if merged is not None:
                    merged["routing"] = "capability_merge"
                    return merged
            return await self._handle_command(text)

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
            result = await self._try_execute_direct_command(text)
            if result is None:
                result = await self._handle_command(text)
            if result.get("ok"):
                label = getattr(target_task, "label", target_task.task_id)
                result["overridden_task_label"] = label
                if result.get("routing") == "command":
                    result["routing"] = "command_override"
                    result["response_text"] = f"已取代任务 #{label}，新指令已创建"
                else:
                    response_text = str(result.get("response_text") or "").strip()
                    prefix = f"已取消任务 #{label}，并"
                    if response_text:
                        result["response_text"] = f"{prefix}{response_text}"
            return result

        if disposition == "interrupt":
            direct_result = await self._try_execute_direct_command(text)
            if direct_result is not None:
                return direct_result
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

    async def _try_execute_direct_command(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized:
            return None
        explicit_operator_move = self._match_operator_move(normalized)
        if explicit_operator_move is not None:
            return await self._handle_rule_command(text, explicit_operator_move)
        rule_match = self._try_rule_match(text)
        if rule_match is not None:
            return await self._handle_rule_command(text, rule_match)
        runtime_nlu = self._try_runtime_nlu(text)
        if runtime_nlu is not None:
            return await self._handle_runtime_nlu(text, runtime_nlu)
        return None

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
        "攻击", "进攻", "打", "突袭", "消灭", "骚扰",
        "建", "造", "生产", "发展", "扩张",
        "防守", "防御", "守",
        "敌方", "敌人", "敌军", "基地",
    }
    _OVERLAP_OBJECT_KEYWORDS = frozenset({"敌方", "敌人", "敌军", "基地"})
    _OVERLAP_RECON_ACTION_KEYWORDS = frozenset({"探索", "侦察", "侦查", "找", "搜索", "发现", "探路"})
    _OVERLAP_COMBAT_ACTION_KEYWORDS = frozenset({"攻击", "进攻", "打", "突袭", "消灭", "骚扰"})
    _OVERLAP_ECONOMY_ACTION_KEYWORDS = frozenset({"建", "造", "生产", "发展", "扩张"})
    _OVERLAP_DEFENSE_ACTION_KEYWORDS = frozenset({"防守", "防御", "守"})

    def _overlap_action_domain(self, text: str) -> str:
        normalized = str(text or "")
        if any(keyword in normalized for keyword in self._OVERLAP_RECON_ACTION_KEYWORDS):
            return "recon"
        if any(keyword in normalized for keyword in self._OVERLAP_COMBAT_ACTION_KEYWORDS):
            return "combat"
        if any(keyword in normalized for keyword in self._OVERLAP_ECONOMY_ACTION_KEYWORDS):
            return "economy"
        if any(keyword in normalized for keyword in self._OVERLAP_DEFENSE_ACTION_KEYWORDS):
            return "defense"
        return "general"

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
        text_domain = self._overlap_action_domain(text)

        for t in active:
            raw = t.raw_text or ""
            task_kw = {w for w in self._OVERLAP_KEYWORDS if w in raw}
            if not task_kw:
                continue
            task_domain = self._overlap_action_domain(raw)
            if (
                text_domain != "general"
                and task_domain != "general"
                and text_domain != task_domain
            ):
                continue
            shared = text_kw & task_kw
            if not shared:
                continue
            shared_actions = shared - self._OVERLAP_OBJECT_KEYWORDS
            shared_objects = shared & self._OVERLAP_OBJECT_KEYWORDS
            # Recon goals often vary by verb ("探索"/"找到"/"发现"), so allow same-domain
            # recon overlap when the shared target nouns are the same. Combat/economy
            # remain stricter and require at least one shared action keyword.
            if text_domain == task_domain == "recon" and shared_objects:
                return t
            if shared_actions and len(shared) >= 2:
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

    def _find_task_by_id(self, task_id: str) -> Optional[Any]:
        normalized = str(task_id or "")
        if not normalized:
            return None
        for task in self.kernel.list_tasks():
            if str(getattr(task, "task_id", "") or "") == normalized:
                return task
        return None

    async def _maybe_route_active_task_followup(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized or self._looks_like_query(normalized):
            return None
        if self._is_economy_command(normalized):
            return None

        text_domain = self._classify_text_domain(normalized)
        if text_domain not in {"recon", "combat"}:
            return None

        context = self._build_context(text)
        hints = context.coordinator_hints or {}
        target_task: Optional[Any] = None

        suggested_disposition = str(hints.get("suggested_disposition", "") or "").lower()
        target_domain = str(hints.get("likely_target_domain", "") or "").lower()
        if suggested_disposition == "merge" and target_domain in {"recon", "combat"}:
            target_task = self._find_task_by_label(str(hints.get("likely_target_label", "") or ""))
            if target_task is None:
                target_task = self._find_task_by_id(str(hints.get("likely_target_task_id", "") or ""))

        if target_task is None and text_domain == "recon":
            active_recon = [
                task
                for task in context.active_tasks
                if str(task.get("domain", "") or "") == "recon"
                and not bool(task.get("is_capability"))
                and str(task.get("state", "") or "") in {"running", "waiting", "waiting_units"}
            ]
            if len(active_recon) == 1:
                target = active_recon[0]
                target_task = self._find_task_by_label(str(target.get("label", "") or ""))
                if target_task is None:
                    target_task = self._find_task_by_id(str(target.get("task_id", "") or ""))

        if target_task is None or self.kernel.is_direct_managed(target_task.task_id):
            return None

        ok = self.kernel.inject_player_message(target_task.task_id, text)
        if not ok:
            return None

        battlefield_snapshot = self._context_battlefield_snapshot(context) or self._battlefield_snapshot()
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

    async def _maybe_handle_vague_combat_command(self, text: str) -> Optional[dict[str, Any]]:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized or self._looks_like_query(normalized):
            return None
        if self._is_economy_command(normalized) or self._looks_like_complex_command(normalized):
            return None
        if not self._looks_like_vague_combat_command(normalized):
            return None

        context = self._build_context(text)
        active_combat = [
            task
            for task in context.active_tasks
            if str(task.get("domain", "") or "") == "combat"
            and not bool(task.get("is_capability"))
            and str(task.get("state", "") or "") in {"running", "waiting", "waiting_units"}
        ]
        if len(active_combat) == 1:
            target = active_combat[0]
            target_task = self._find_task_by_label(str(target.get("label", "") or ""))
            if target_task is None:
                target_task = self._find_task_by_id(str(target.get("task_id", "") or ""))
            if target_task is not None and not self.kernel.is_direct_managed(target_task.task_id):
                if self.kernel.inject_player_message(target_task.task_id, text):
                    label = getattr(target_task, "label", target_task.task_id)
                    battlefield_snapshot = self._context_battlefield_snapshot(context) or self._battlefield_snapshot()
                    return {
                        "type": "command",
                        "ok": True,
                        "merged": True,
                        "existing_task_id": target_task.task_id,
                        "response_text": f"收到指令，已转发给任务 #{label}",
                        "routing": "vague_combat_merge",
                        "target_task_id": label,
                        "battlefield_disposition": battlefield_snapshot.get("disposition", "unknown"),
                        "battlefield_focus": battlefield_snapshot.get("focus", "general"),
                    }

        if len(active_combat) > 1:
            return {
                "type": "command",
                "ok": False,
                "routing": "clarify_vague_combat",
                "response_text": "当前有多个攻击任务在执行，请指定目标或任务，例如：继续任务 #007、进攻敌方基地、全军出击。",
            }
        return {
            "type": "command",
            "ok": False,
            "routing": "clarify_vague_combat",
            "response_text": "请先说明要怎么打，例如：进攻敌方基地、全军出击、骚扰右路，或先集结兵力。",
        }

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
        task_focus = self._build_query_task_focus(text, context)
        query_context = json.dumps({
            "world_summary": world_summary,
            "battlefield_snapshot": battlefield_snapshot,
            "world_sync_health": self.world_model.refresh_health(),
            "active_tasks": context.active_tasks,
            "task_focus": task_focus,
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
        snapshot = self._build_context_snapshot()
        tasks = list(snapshot.get("tasks") or [])
        pending_questions = list(snapshot.get("pending_questions") or [])
        task_messages = list(snapshot.get("task_messages") or [])
        jobs_by_task = dict(snapshot.get("jobs_by_task") or {})
        collected_inputs = dict(snapshot.get("coordinator_inputs") or {})
        runtime_snapshot = RuntimeStateSnapshot.from_mapping(collected_inputs.get("runtime_state"))
        runtime_state = runtime_snapshot.to_dict()
        runtime_tasks = dict(runtime_snapshot.active_tasks)
        coordinator_snapshot = self._coordinator_snapshot(collected_inputs)
        world_sync = dict((coordinator_snapshot.get("world_sync") or {}))
        active_tasks = []
        for t in tasks:
            if t.status.value not in ("pending", "running", "waiting"):
                continue
            runtime_task = dict(runtime_tasks.get(t.task_id) or {})
            active_actor_ids = [int(actor_id) for actor_id in list(runtime_task.get("active_actor_ids", []) or []) if actor_id is not None]
            group_summary = self._summarize_group_actor_ids(active_actor_ids)
            task_entry = {
                "task_id": t.task_id,
                "label": getattr(t, "label", ""),
                "raw_text": t.raw_text,
                "status": t.status.value,
                "is_capability": bool(runtime_task.get("is_capability", getattr(t, "is_capability", False))),
                "state": str(runtime_task.get("state", "") or ""),
                "phase": str(runtime_task.get("phase", "") or ""),
                "active_group_size": int(runtime_task.get("active_group_size", 0) or 0),
                "active_actor_ids": active_actor_ids,
                "active_expert": str(runtime_task.get("active_expert", "") or ""),
                "waiting_reason": str(runtime_task.get("waiting_reason", "") or ""),
                "blocking_reason": str(runtime_task.get("blocking_reason", "") or ""),
                "status_line": str(runtime_task.get("status_line", "") or ""),
                "group_known_count": int(group_summary.get("known_count", 0) or 0),
                "group_combat_count": int(group_summary.get("combat_count", 0) or 0),
                "unit_mix": list(group_summary.get("unit_mix", []) or []),
            }
            jobs = list(jobs_by_task.get(str(t.task_id or ""), []) or [])
            triage_inputs = collect_task_triage_inputs(
                task_id=str(t.task_id or ""),
                jobs=jobs,
                world_sync=world_sync,
                pending_questions=pending_questions,
                task_messages=task_messages,
                unit_mix=list(group_summary.get("unit_mix", []) or []),
            )
            triage = self._derive_task_triage(
                t,
                runtime_task,
                runtime_state,
                triage_inputs,
                task_messages,
                pending_questions,
                jobs,
            )
            task_entry.update(triage)
            for field_name in ("state", "phase", "active_expert", "waiting_reason", "blocking_reason", "status_line"):
                if not str(task_entry.get(field_name, "") or "").strip():
                    task_entry[field_name] = str(runtime_task.get(field_name, "") or "")
            task_entry["domain"] = self._infer_task_domain(
                str(getattr(t, "raw_text", "") or "").lower(),
                runtime_task,
                task_entry,
            )
            task_entry["status_line"] = str(triage.get("status_line") or "")
            active_tasks.append(task_entry)

        coordinator_snapshot["task_overview"] = self._build_task_overview(active_tasks)
        coordinator_snapshot["battle_groups"] = self._build_battle_groups(active_tasks)
        coordinator_snapshot["alerts"] = self._coordinator_alerts(coordinator_snapshot)
        coordinator_snapshot["status_line"] = self._coordinator_status_line(coordinator_snapshot)
        coordinator_hints = self._coordinator_hints(
            player_input,
            active_tasks,
            coordinator_snapshot.get("battlefield") or {},
        )
        return AdjutantContext(
            active_tasks=active_tasks,
            pending_questions=pending_questions,
            recent_dialogue=self._dialogue_history[-self.config.max_dialogue_history:],
            player_input=player_input,
            recent_completed_tasks=list(self._recent_completed),
            coordinator_snapshot=coordinator_snapshot,
            coordinator_hints=coordinator_hints,
            task_messages=task_messages,
            jobs_by_task=jobs_by_task,
            runtime_tasks=runtime_tasks,
        )

    def _record_dialogue(self, speaker: str, text: str) -> None:
        """Record a dialogue entry."""
        self._dialogue_history.append({
            "from": speaker,
            "content": text,
            "timestamp": time.time(),
        })
        if len(self._dialogue_history) > self.config.max_dialogue_history:
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
            self._record_capability_nlu_note(task_text, match.expert_type)
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
