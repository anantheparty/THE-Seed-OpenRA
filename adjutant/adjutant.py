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
    DeployJobConfig,
    EconomyJobConfig,
    PlayerResponse,
    ReconJobConfig,
    TaskMessage,
    TaskMessageType,
)
from openra_api.models import Actor as GameActor
from openra_api.production_names import normalize_production_name
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

# Economy/production keywords — commands matching these merge to EconomyCapability
_ECONOMY_KEYWORDS = frozenset({
    "发展经济", "爆兵", "多建电厂", "补电", "建电厂", "造电厂",
    "发展", "扩张", "多造", "全力生产", "停止生产", "暂停生产",
    "多建", "科技", "升级科技", "经济", "多挖矿", "造矿车",
})


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
    timestamp: float = field(default_factory=time.time)


CLASSIFICATION_SYSTEM_PROMPT = """\
You are the Adjutant (副官) in a real-time strategy game. Your job is to classify player input.

Given the current context (active tasks, pending questions, recent dialogue, recent completed tasks), classify the input as ONE of:
1. "reply" — the player is answering a pending question from a task
2. "command" — the player is giving a new order/instruction
3. "query" — the player is asking for information (战况, 建议, etc.)
4. "cancel" — the player wants to cancel/stop a currently running task (e.g. "取消任务002", "停止#001", "cancel task 003")
5. "info" — the player is providing intelligence, feedback, or situational updates (e.g. "敌人在左下角", "发现敌人基地了", "被打了", "就在剩下的14%里啊")

Respond with a JSON object:
{"type": "reply"|"command"|"query"|"cancel"|"info", "target_message_id": "<id or null>", "target_task_id": "<label or task_id or null>", "confidence": 0.0-1.0}

Rules:
- If there are pending questions and the input looks like a response, classify as "reply" with the matching message_id
- If ambiguous between reply and command, match to the highest-priority pending question
- Queries ask about game state or advice WITHOUT providing new facts — pure questions ("战况如何?", "电力够吗?")
- Commands are instructions to execute (attack, build, produce, explore, retreat, etc.)
- "info" is for inputs that provide new facts, intelligence, corrections, or situational awareness to the AI — NOT a question and NOT a direct order. E.g.: "敌人基地在左下角", "发现敌人，被打了", "那个方向没有敌人"
- If the input describes an urgent situation (被攻击, 被打了, 发现敌人) but has no explicit action verb, classify as "info" NOT "query"
- "cancel" applies when the player explicitly wants to stop an existing task; set target_task_id to the task label or id mentioned (e.g. "001", "002")
- Active tasks are listed in the context — use their labels to resolve "取消001" → target_task_id="001"
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

Use the provided world summary to give accurate, concise answers in Chinese.
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
            # Economy commands → merge to EconomyCapability (before LLM classification)
            if self._is_economy_command(text):
                if self._world_sync_is_stale():
                    result = self._stale_world_guard("command")
                    slog.info(
                        "Stale world guard short-circuit",
                        event="stale_world_guard",
                        input_type="command",
                        raw_text=text,
                        source="capability",
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

            # Build context
            context = self._build_context(text)

            # Classify input
            classification = await self._classify_input(context)
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

    @staticmethod
    def _fallback_query_answer(world_summary: dict[str, Any]) -> str:
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
        return any(kw in normalized for kw in _ECONOMY_KEYWORDS)

    def _try_merge_to_capability(self, text: str) -> Optional[dict[str, Any]]:
        """Try to merge an economy command to the EconomyCapability task."""
        if self._world_sync_is_stale():
            return self._stale_world_guard("command")
        cap_id = getattr(self.kernel, "capability_task_id", None)
        if not cap_id:
            return None
        ok = self.kernel.inject_player_message(cap_id, text)
        if not ok:
            return None
        slog.info("Merged economy command to Capability", event="capability_merge",
                  capability_task_id=cap_id, text=text)
        return {
            "type": "command",
            "ok": True,
            "merged": True,
            "existing_task_id": cap_id,
            "response_text": f"收到经济指令，已转发给经济规划",
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
        context_json = json.dumps({
            "active_tasks": context.active_tasks,
            "pending_questions": context.pending_questions,
            "recent_dialogue": context.recent_dialogue[-10:],
            "recent_completed_tasks": context.recent_completed_tasks,
            "player_input": context.player_input,
            "world_sync_health": self.world_model.refresh_health(),
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
        # Find best matching active task by keyword overlap
        target_label = classification.target_task_id
        best_task = None
        if target_label:
            tasks = self.kernel.list_tasks()
            best_task = next(
                (t for t in tasks if getattr(t, "label", "") == target_label
                 or getattr(t, "label", "").lstrip("0") == target_label.lstrip("0")),
                None,
            )

        if best_task is None and context.active_tasks:
            # Match by keyword overlap between input and task raw_text
            text_lower = text.lower()
            scored = []
            for at in context.active_tasks:
                raw = str(at.get("raw_text", "")).lower()
                overlap = sum(1 for w in raw if len(w) > 1 and w in text_lower)
                scored.append((overlap, at))
            scored.sort(key=lambda x: -x[0])
            # Pick the best match, or highest priority active task
            if scored:
                best_label = scored[0][1].get("label")
                tasks = self.kernel.list_tasks()
                best_task = next(
                    (t for t in tasks if getattr(t, "label", "") == best_label),
                    None,
                )

        # Create a command task with the intel — it will be visible to other tasks via [并行]
        result = await self._handle_command(text)
        task_ref = f"（相关任务: {best_task.label}）" if best_task else ""
        if result.get("ok"):
            result["response_text"] = f"收到情报{task_ref}，已记录"
        return result

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
        world_summary = self.world_model.world_summary()
        query_context = json.dumps({
            "world_summary": world_summary,
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
        active_tasks = [
            {
                "task_id": t.task_id,
                "label": getattr(t, "label", ""),
                "raw_text": t.raw_text,
                "status": t.status.value,
            }
            for t in tasks
            if t.status.value in ("pending", "running", "waiting")
        ]

        pending_questions = self.kernel.list_pending_questions()

        return AdjutantContext(
            active_tasks=active_tasks,
            pending_questions=pending_questions,
            recent_dialogue=self._dialogue_history[-self.config.max_dialogue_history:],
            player_input=player_input,
            recent_completed_tasks=list(self._recent_completed),
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
