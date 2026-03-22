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
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from benchmark import span as bm_span
from logging_system import get_logger
from llm import LLMProvider, LLMResponse
from models import PlayerResponse, TaskMessage, TaskMessageType

logger = logging.getLogger(__name__)
slog = get_logger("adjutant")


# --- Protocol interfaces ---

class KernelLike(Protocol):
    def create_task(self, raw_text: str, kind: str, priority: int) -> Any: ...
    def submit_player_response(self, response: PlayerResponse, *, now: Optional[float] = None) -> dict[str, Any]: ...
    def list_pending_questions(self) -> list[dict[str, Any]]: ...
    def list_tasks(self) -> list[Any]: ...


class WorldModelLike(Protocol):
    def world_summary(self) -> dict[str, Any]: ...
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


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
        config: Optional[AdjutantConfig] = None,
    ) -> None:
        self.llm = llm
        self.kernel = kernel
        self.world_model = world_model
        self.config = config or AdjutantConfig()
        self._dialogue_history: list[dict[str, Any]] = []

    # --- Main entry point ---

    async def handle_player_input(self, text: str) -> dict[str, Any]:
        """Process player input and return a response dict.

        Returns:
            {"type": "command"|"reply"|"query", "response": ..., "timestamp": ...}
        """
        with bm_span("llm_call", name="adjutant:handle_input"):
            slog.info("Handling player input", event="player_input", text=text)
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
                result = await self._handle_reply(classification)
            elif classification.input_type == InputType.QUERY:
                result = await self._handle_query(text, context)
            else:
                result = await self._handle_command(text)

            # Record in dialogue history
            self._record_dialogue("player", text)
            if result.get("response_text"):
                self._record_dialogue("adjutant", result["response_text"])

            result["timestamp"] = time.time()
            return result

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
            # Rule-based fallback when LLM is unavailable
            fallback_type = self._rule_based_classify(context.player_input)
            return ClassificationResult(
                input_type=fallback_type,
                raw_text=context.player_input,
                confidence=0.4,
            )

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

    @staticmethod
    def _rule_based_classify(text: str) -> str:
        """Simple rule-based fallback when LLM classification is unavailable."""
        query_keywords = {"？", "?", "如何", "怎么", "战况", "多少", "几个", "哪里", "什么", "建议", "分析"}
        if any(kw in text for kw in query_keywords):
            return InputType.QUERY
        return InputType.COMMAND

    # --- Route handlers ---

    async def _handle_reply(self, classification: ClassificationResult) -> dict[str, Any]:
        """Route player reply to the correct pending question."""
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

    # --- TaskMessage formatting ---

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
