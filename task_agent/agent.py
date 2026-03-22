"""Task Agent — per-Task LLM brain instance.

Implements the agentic loop: wake → context → multi-turn LLM tool use → sleep.
~250 lines of core logic on raw SDK, no framework.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from benchmark import span as bm_span
from logging_system import get_logger
from llm import LLMProvider, LLMResponse
from models import Event, ExpertSignal, Job, SignalKind, Task, TaskMessage, TaskMessageType, TaskStatus

from .context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
)
from .queue import AgentQueue, QueueItem
from .tools import TOOL_DEFINITIONS, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)
slog = get_logger("task_agent")


class _AgentFatalError(Exception):
    """Raised when agent reaches max consecutive failures and must stop."""

# Type for a callback that fetches current Jobs for this Task
JobsProvider = Callable[[str], list[Job]]
# Type for a callback that fetches current WorldSummary
WorldSummaryProvider = Callable[[], WorldSummary]
# Type for a callback that sends TaskMessage to Kernel (for player notification)
MessageCallback = Callable[[TaskMessage], None]

SYSTEM_PROMPT = """\
You are a Task Agent in a real-time strategy game (OpenRA). You manage one player task by creating and coordinating Jobs (executed by Experts — traditional AI).

Your role:
- Understand the player's intent from the task description and context
- Create Jobs (start_job) to accomplish the task using appropriate Experts
- Monitor Job progress via Signals and adjust as needed (patch_job, abort_job)
- Respond to decision requests from Jobs
- Mark the task complete (complete_task) when done or when it cannot be fulfilled

Rules:
- You receive a context packet each time you wake, with current task state, active jobs, world summary, recent signals, and pending decisions
- You can call multiple tools in one turn (e.g., start 3 jobs simultaneously)
- When you have nothing more to do this cycle, respond with a brief text summary (no tool calls) to end the turn
- Timestamps in the context packet are Unix epoch seconds; use them to judge recency
- For decision requests with deadlines, respond promptly or the default option will be used

Expert types and their config schemas (use EXACT field names in start_job):

ReconExpert — scout the map to find targets:
  config: {search_region: "northeast"|"enemy_half"|"full_map", target_type: "base"|"army"|"expansion", target_owner: "enemy", retreat_hp_pct: 0.3, avoid_combat: true}

CombatExpert — engage enemies at a position:
  config: {target_position: [x, y], engagement_mode: "assault"|"harass"|"hold"|"surround", max_chase_distance: 20, retreat_threshold: 0.3}

MovementExpert — move units to a position:
  config: {target_position: [x, y], move_mode: "move"|"attack_move"|"retreat", arrival_radius: 5}

DeployExpert — deploy a unit (e.g. MCV):
  config: {actor_id: <int>, target_position: [x, y]}

EconomyExpert — produce units:
  config: {unit_type: "<unit_code>", count: <int>, queue_type: "Vehicle"|"Infantry"|"Building", repeat: false}

Common command → Expert mapping (IMPORTANT — choose the right Expert):
- "部署基地车" / "deploy MCV" → DeployExpert (first query_world to find MCV actor_id)
- "探索地图" / "找敌人基地" → ReconExpert
- "生产N辆坦克" / "造兵" → EconomyExpert
- "进攻" / "包围" / "防守" → CombatExpert
- "撤退" / "移动到" → MovementExpert
- "别追太远" / constraints → create_constraint tool (not start_job)
- "修理后进攻" → MovementExpert (move to repair) then CombatExpert (sequential)

CRITICAL: "部署" means DEPLOY (DeployExpert), NOT scout/recon. Always match the player's intent to the correct Expert.

Before creating a Job, use query_world to check available units (my_actors) so you know actor_ids and positions.
"""


@dataclass
class AgentConfig:
    """Configuration for a Task Agent instance."""

    review_interval: float = 10.0  # seconds between periodic wakes
    max_turns: int = 10  # max LLM call rounds per wake cycle
    llm_timeout: float = 30.0  # seconds before LLM call times out
    max_retries: int = 1  # LLM call retries on failure
    max_consecutive_failures: int = 3  # consecutive LLM failures before auto-terminate


class TaskAgent:
    """Per-Task LLM brain instance.

    Lifecycle: created by Kernel when a Task is created, destroyed when Task ends.
    """

    def __init__(
        self,
        task: Task,
        llm: LLMProvider,
        tool_executor: ToolExecutor,
        jobs_provider: JobsProvider,
        world_summary_provider: WorldSummaryProvider,
        config: Optional[AgentConfig] = None,
        message_callback: Optional[MessageCallback] = None,
    ) -> None:
        self.task = task
        self.llm = llm
        self.tool_executor = tool_executor
        self._jobs_provider = jobs_provider
        self._world_provider = world_summary_provider
        self.config = config or AgentConfig()
        self._message_callback = message_callback

        self.queue = AgentQueue()
        self._conversation: list[dict[str, Any]] = []
        self._running = False
        self._task_completed = False
        self._wake_count = 0
        self._total_llm_calls = 0
        self._consecutive_failures = 0
        self._last_llm_error: str = ""

    # --- Public interface (called by Kernel) ---

    def push_signal(self, signal: ExpertSignal) -> None:
        """Deliver a Signal to this agent (called from Kernel/Job thread)."""
        self.queue.push(signal)

    def push_event(self, event: Event) -> None:
        """Deliver a WorldModel Event to this agent."""
        self.queue.push(event)

    def push_player_response(self, response: Any) -> None:
        """Deliver a PlayerResponse through the normal event intake path."""
        self.push_event(
            Event(
                type="player_response",  # type: ignore[arg-type]
                data={
                    "message_id": response.message_id,
                    "task_id": response.task_id,
                    "answer": response.answer,
                },
                timestamp=response.timestamp,
            )
        )

    async def run(self) -> None:
        """Main loop — runs until task is completed or cancelled."""
        self._running = True
        logger.info("TaskAgent started: task_id=%s raw_text=%r", self.task.task_id, self.task.raw_text)
        slog.info("TaskAgent started", event="agent_started", task_id=self.task.task_id, raw_text=self.task.raw_text)

        try:
            # Initial wake: process the task for the first time
            await self._safe_wake_cycle(trigger="init")

            while self._running and not self._task_completed:
                # Wait for signal/event or review_interval timeout
                woken_by_event = await self.queue.wait_for_wake(
                    timeout=self.config.review_interval
                )
                if not self._running:
                    break
                trigger = "event" if woken_by_event else "timer"
                await self._safe_wake_cycle(trigger=trigger)
        except asyncio.CancelledError:
            logger.info("TaskAgent cancelled: task_id=%s", self.task.task_id)
            raise
        except _AgentFatalError:
            # Raised by _safe_wake_cycle when max consecutive failures reached
            pass
        finally:
            self._running = False
            logger.info(
                "TaskAgent stopped: task_id=%s wakes=%d llm_calls=%d",
                self.task.task_id,
                self._wake_count,
                self._total_llm_calls,
            )
            slog.info("TaskAgent stopped", event="agent_stopped", task_id=self.task.task_id, wakes=self._wake_count, llm_calls=self._total_llm_calls)

    def stop(self) -> None:
        """Signal the agent to stop after the current cycle."""
        self._running = False
        self.queue.push(Event(type="SHUTDOWN"))  # type: ignore[arg-type] — wake the queue

    # --- Core agentic loop ---

    async def _safe_wake_cycle(self, trigger: str) -> None:
        """Error-isolated wake cycle. Catches unexpected exceptions so one
        bad cycle doesn't crash the entire agent. LLM failures are handled
        inside _wake_cycle; this catches everything else (tool errors, etc.)."""
        try:
            await self._wake_cycle(trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Unexpected wake cycle error: task_id=%s",
                self.task.task_id,
            )
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.max_consecutive_failures:
                await self._auto_terminate_on_failure()
                raise _AgentFatalError()

    async def _wake_cycle(self, trigger: str) -> None:
        """One wake cycle: drain queue → context → multi-turn LLM loop."""
        self._wake_count += 1
        logger.debug("Wake #%d trigger=%s task_id=%s", self._wake_count, trigger, self.task.task_id)
        slog.debug("TaskAgent wake", event="agent_wake", task_id=self.task.task_id, wake=self._wake_count, trigger=trigger)

        # Drain pending signals and events
        items = self.queue.drain()
        signals = [i for i in items if isinstance(i, ExpertSignal)]
        events = [i for i in items if isinstance(i, Event)]

        # Separate open decisions (decision_request signals)
        open_decisions = [s for s in signals if s.kind == SignalKind.DECISION_REQUEST]
        recent_signals = [s for s in signals if s.kind != SignalKind.DECISION_REQUEST]

        # Build context packet
        jobs = self._jobs_provider(self.task.task_id)
        world = self._world_provider()
        packet = build_context_packet(
            task=self.task,
            jobs=jobs,
            world_summary=world,
            recent_signals=recent_signals,
            recent_events=events,
            open_decisions=open_decisions,
        )

        # Inject context as user message
        ctx_msg = context_to_message(packet)

        # Build messages for this cycle
        messages = self._build_messages(ctx_msg)

        # Multi-turn tool use loop
        for turn in range(self.config.max_turns):
            response = await self._call_llm(messages)
            if response is None:
                # LLM failure — track and handle
                self._consecutive_failures += 1
                logger.warning(
                    "LLM failure %d/%d for task_id=%s",
                    self._consecutive_failures,
                    self.config.max_consecutive_failures,
                    self.task.task_id,
                )
                slog.warn("TaskAgent LLM call failed", event="llm_failed", task_id=self.task.task_id, consecutive_failures=self._consecutive_failures)
                # Apply defaults for any open decisions
                await self._apply_defaults(open_decisions)
                # Notify player on repeated failures
                if self._consecutive_failures >= 2:
                    await self._notify_player_llm_failure()
                # Auto-terminate on max consecutive failures
                if self._consecutive_failures >= self.config.max_consecutive_failures:
                    await self._auto_terminate_on_failure()
                break

            # LLM succeeded — reset failure counter
            self._consecutive_failures = 0
            self._total_llm_calls += 1
            slog.info(
                "TaskAgent LLM call succeeded",
                event="llm_succeeded",
                task_id=self.task.task_id,
                tool_calls=len(response.tool_calls),
                has_text=bool(response.text),
            )

            # If LLM returns tool calls, execute them and continue
            if response.tool_calls:
                # Append assistant message with tool calls
                assistant_msg = self._response_to_assistant_msg(response)
                messages.append(assistant_msg)
                self._conversation.append(assistant_msg)

                # Execute all tool calls
                results = await self._execute_tools(response)

                # Append tool results as messages
                for result in results:
                    tool_msg = self._tool_result_to_msg(result)
                    messages.append(tool_msg)
                    self._conversation.append(tool_msg)

                # Check if complete_task was called
                for result in results:
                    if result.name == "complete_task" and result.error is None:
                        self._task_completed = True

                if self._task_completed:
                    break
                continue

            # LLM returned text only — turn ends
            if response.text:
                assistant_msg = {"role": "assistant", "content": response.text}
                messages.append(assistant_msg)
                self._conversation.append(assistant_msg)
            break
        else:
            # max_turns exceeded
            logger.warning(
                "Max turns (%d) exceeded: task_id=%s wake=%d",
                self.config.max_turns,
                self.task.task_id,
                self._wake_count,
            )

    def _build_messages(self, context_msg: dict[str, str]) -> list[dict[str, Any]]:
        """Build the message list for an LLM call."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        # Include conversation history (trimmed to keep system prompt cacheable)
        messages.extend(self._conversation)
        # Append new context
        messages.append(context_msg)
        self._conversation.append(context_msg)
        return messages

    async def _call_llm(self, messages: list[dict[str, Any]]) -> Optional[LLMResponse]:
        """Call the LLM with retry and timeout."""
        for attempt in range(1 + self.config.max_retries):
            try:
                with bm_span("llm_call", name=f"task_agent:{self.task.task_id}"):
                    response = await asyncio.wait_for(
                        self.llm.chat(messages, tools=TOOL_DEFINITIONS),
                        timeout=self.config.llm_timeout,
                    )
                return response
            except asyncio.TimeoutError:
                self._last_llm_error = f"timeout ({self.config.llm_timeout}s)"
                logger.warning(
                    "LLM timeout (attempt %d/%d): task_id=%s",
                    attempt + 1,
                    1 + self.config.max_retries,
                    self.task.task_id,
                )
            except Exception as e:
                self._last_llm_error = str(e)[:100]
                logger.exception(
                    "LLM error (attempt %d/%d): task_id=%s",
                    attempt + 1,
                    1 + self.config.max_retries,
                    self.task.task_id,
                )
        return None

    async def _execute_tools(self, response: LLMResponse) -> list[ToolResult]:
        """Execute all tool calls from an LLM response."""
        results = []
        for tc in response.tool_calls:
            result = await self.tool_executor.execute(tc.id, tc.name, tc.arguments)
            results.append(result)
        return results

    async def _apply_defaults(self, open_decisions: list[ExpertSignal]) -> None:
        """When LLM fails, apply default_if_timeout for open decisions.

        Executes the default option by calling the appropriate tool handler,
        so the decision has real side effects (not just logging).
        """
        for dec in open_decisions:
            if not (dec.decision and "default_if_timeout" in dec.decision):
                continue
            default = dec.decision["default_if_timeout"]
            logger.info(
                "Applying default_if_timeout=%r for decision in job=%s",
                default,
                dec.job_id,
            )
            # Execute the default by calling patch_job with the chosen option
            result = await self.tool_executor.execute(
                tool_call_id=f"default_{dec.job_id}",
                name="patch_job",
                arguments_json=json.dumps({
                    "job_id": dec.job_id,
                    "params": {"decision_response": default},
                }),
            )
            if result.error:
                logger.warning(
                    "Failed to apply default for job=%s: %s",
                    dec.job_id,
                    result.error,
                )

    # --- Error recovery ---

    async def _notify_player_llm_failure(self) -> None:
        """Notify player that LLM is experiencing failures via TaskMessage(task_warning).

        Uses message_callback to send TaskMessage to Kernel, which holds it
        for Adjutant/dashboard delivery. This is the correct outbound path —
        messages leave the agent and reach the player.
        """
        logger.warning(
            "LLM repeated failure — notifying player: task_id=%s failures=%d",
            self.task.task_id,
            self._consecutive_failures,
        )
        if self._message_callback is None:
            return
        import uuid
        error_detail = f" ({self._last_llm_error})" if self._last_llm_error else ""
        message = TaskMessage(
            message_id=f"warn_{uuid.uuid4().hex[:8]}",
            task_id=self.task.task_id,
            type=TaskMessageType.TASK_WARNING,
            content=f"LLM 连续失败 {self._consecutive_failures} 次{error_detail}，任务可能受影响",
            priority=self.task.priority,
        )
        try:
            self._message_callback(message)
        except Exception:
            logger.exception("Failed to send LLM failure warning to Kernel")

    async def _auto_terminate_on_failure(self) -> None:
        """Auto-terminate task after max consecutive LLM failures."""
        logger.error(
            "Auto-terminating task due to %d consecutive LLM failures: task_id=%s",
            self._consecutive_failures,
            self.task.task_id,
        )
        try:
            await self.tool_executor.execute(
                tool_call_id="auto_fail",
                name="complete_task",
                arguments_json=json.dumps({
                    "result": "failed",
                    "summary": f"LLM连续失败{self._consecutive_failures}次，自动终止",
                }),
            )
        except Exception:
            logger.exception("Failed to auto-terminate task: %s", self.task.task_id)
        self._task_completed = True

    # --- Message format helpers ---

    @staticmethod
    def _response_to_assistant_msg(response: LLMResponse) -> dict[str, Any]:
        """Convert LLMResponse to an assistant message with tool_calls."""
        tool_calls = []
        for tc in response.tool_calls:
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            })
        msg: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
        if response.text:
            msg["content"] = response.text
        else:
            msg["content"] = None
        return msg

    @staticmethod
    def _tool_result_to_msg(result: ToolResult) -> dict[str, Any]:
        """Convert a ToolResult to a tool message for the LLM."""
        if result.error:
            content = json.dumps({"error": result.error})
        else:
            content = json.dumps(result.result, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": content,
        }
