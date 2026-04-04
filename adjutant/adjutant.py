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


# --- Protocol interfaces ---

class KernelLike(Protocol):
    def create_task(self, raw_text: str, kind: str, priority: int) -> Any: ...
    def start_job(self, task_id: str, expert_type: str, config: Any) -> Any: ...
    def submit_player_response(self, response: PlayerResponse, *, now: Optional[float] = None) -> dict[str, Any]: ...
    def list_pending_questions(self) -> list[dict[str, Any]]: ...
    def list_tasks(self) -> list[Any]: ...


class WorldModelLike(Protocol):
    def world_summary(self) -> dict[str, Any]: ...
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...
    def refresh_health(self) -> dict[str, Any]: ...


# --- Classification result ---

class InputType:
    COMMAND = "command"
    REPLY = "reply"
    QUERY = "query"


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
    timestamp: float = field(default_factory=time.time)


CLASSIFICATION_SYSTEM_PROMPT = """\
You are the Adjutant (副官) in a real-time strategy game. Your job is to classify player input.

Given the current context (active tasks, pending questions, recent dialogue), classify the input as ONE of:
1. "reply" — the player is answering a pending question from a task
2. "command" — the player is giving a new order/instruction
3. "query" — the player is asking for information (战况, 建议, etc.)

Respond with a JSON object:
{"type": "reply"|"command"|"query", "target_message_id": "<id or null>", "target_task_id": "<id or null>", "confidence": 0.0-1.0}

Rules:
- If there are pending questions and the input looks like a response, classify as "reply" with the matching message_id
- If ambiguous between reply and command, match to the highest-priority pending question
- Queries ask about game state or advice without commanding action
- Commands are instructions to execute (attack, build, produce, explore, retreat, etc.)
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
    classification_timeout: float = 10.0
    query_timeout: float = 15.0


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
        self._runtime_nlu = RuntimeNLURouter(unit_registry=self.unit_registry)

    # --- Main entry point ---

    async def handle_player_input(self, text: str) -> dict[str, Any]:
        """Process player input and return a response dict.

        Returns:
            {"type": "command"|"reply"|"query", "response": ..., "timestamp": ...}
        """
        with bm_span("llm_call", name="adjutant:handle_input"):
            slog.info("Handling player input", event="player_input", text=text)
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
            runtime_nlu = self._try_runtime_nlu(text)
            if runtime_nlu is not None:
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
            slog.info(
                "Classified player input",
                event="input_classified",
                input_type=classification.input_type,
                confidence=classification.confidence,
                target_message_id=classification.target_message_id,
                target_task_id=classification.target_task_id,
            )

            # Route based on classification
            if classification.input_type == InputType.REPLY:
                slog.info(
                    "Routing to reply handler",
                    event="route_decision",
                    input_type=InputType.REPLY,
                    message_id=classification.target_message_id,
                    task_id=classification.target_task_id,
                )
                result = await self._handle_reply(classification)
            elif classification.input_type == InputType.QUERY:
                slog.info("Routing to query handler", event="route_decision", input_type=InputType.QUERY)
                result = await self._handle_query(text, context)
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

    async def _handle_rule_command(self, text: str, match: RuleMatchResult) -> dict[str, Any]:
        try:
            task, job = self._start_direct_job(text, match.expert_type, match.config)
            slog.info(
                "Adjutant rule matched",
                event="rule_routed_command",
                raw_text=text,
                task_id=task.task_id,
                job_id=job.job_id,
                expert_type=match.expert_type,
                reason=match.reason,
            )
            return {
                "type": "command",
                "ok": True,
                "task_id": task.task_id,
                "job_id": job.job_id,
                "response_text": f"收到指令，已直接执行并创建任务 {task.task_id}",
                "routing": "rule",
                "expert_type": match.expert_type,
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
        created: list[dict[str, str]] = []
        try:
            for step in decision.steps:
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
        task = self.kernel.create_task(
            raw_text=raw_text,
            kind=self.config.default_task_kind,
            priority=self.config.default_task_priority,
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
            "recent_dialogue": context.recent_dialogue[-5:],
            "player_input": context.player_input,
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
            if input_type not in (InputType.COMMAND, InputType.REPLY, InputType.QUERY):
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
        """Rule-based fallback classification when LLM is unavailable.

        Checks pending questions first so player replies in degraded mode
        are routed correctly instead of being misclassified as new commands.
        """
        text = context.player_input
        normalized = text.strip()

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

    async def _handle_command(self, text: str) -> dict[str, Any]:
        """Create a new Task via Kernel."""
        try:
            task = self.kernel.create_task(
                raw_text=text,
                kind=self.config.default_task_kind,
                priority=self.config.default_task_priority,
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
        world_summary = self.world_model.world_summary()
        query_context = json.dumps({
            "world_summary": world_summary,
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
            answer = f"LLM 响应超时，请稍后再试"
        except Exception:
            logger.exception("Query LLM failed")
            answer = "LLM 不可用，请稍后再试"

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

    def clear_dialogue_history(self) -> None:
        self._dialogue_history = []

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
