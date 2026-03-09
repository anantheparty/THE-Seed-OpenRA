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
from llm import LLMProvider, LLMResponse
from models import Event, ExpertSignal, Job, SignalKind, Task, TaskStatus

from .context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
)
from .queue import AgentQueue, QueueItem
from .tools import TOOL_DEFINITIONS, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)

# Type for a callback that fetches current Jobs for this Task
JobsProvider = Callable[[str], list[Job]]
# Type for a callback that fetches current WorldSummary
WorldSummaryProvider = Callable[[], WorldSummary]

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
"""


@dataclass
class AgentConfig:
    """Configuration for a Task Agent instance."""

    review_interval: float = 10.0  # seconds between periodic wakes
    max_turns: int = 10  # max LLM call rounds per wake cycle
    llm_timeout: float = 30.0  # seconds before LLM call times out
    max_retries: int = 1  # LLM call retries on failure


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
    ) -> None:
        self.task = task
        self.llm = llm
        self.tool_executor = tool_executor
        self._jobs_provider = jobs_provider
        self._world_provider = world_summary_provider
        self.config = config or AgentConfig()

        self.queue = AgentQueue()
        self._conversation: list[dict[str, Any]] = []
        self._running = False
        self._task_completed = False
        self._wake_count = 0
        self._total_llm_calls = 0

    # --- Public interface (called by Kernel) ---

    def push_signal(self, signal: ExpertSignal) -> None:
        """Deliver a Signal to this agent (called from Kernel/Job thread)."""
        self.queue.push(signal)

    def push_event(self, event: Event) -> None:
        """Deliver a WorldModel Event to this agent."""
        self.queue.push(event)

    async def run(self) -> None:
        """Main loop — runs until task is completed or cancelled."""
        self._running = True
        logger.info("TaskAgent started: task_id=%s raw_text=%r", self.task.task_id, self.task.raw_text)

        try:
            # Initial wake: process the task for the first time
            await self._wake_cycle(trigger="init")

            while self._running and not self._task_completed:
                # Wait for signal/event or review_interval timeout
                woken_by_event = await self.queue.wait_for_wake(
                    timeout=self.config.review_interval
                )
                if not self._running:
                    break
                trigger = "event" if woken_by_event else "timer"
                await self._wake_cycle(trigger=trigger)
        except asyncio.CancelledError:
            logger.info("TaskAgent cancelled: task_id=%s", self.task.task_id)
            raise
        except Exception:
            logger.exception("TaskAgent error: task_id=%s", self.task.task_id)
            raise
        finally:
            self._running = False
            logger.info(
                "TaskAgent stopped: task_id=%s wakes=%d llm_calls=%d",
                self.task.task_id,
                self._wake_count,
                self._total_llm_calls,
            )

    def stop(self) -> None:
        """Signal the agent to stop after the current cycle."""
        self._running = False
        self.queue.push(Event(type="SHUTDOWN"))  # type: ignore[arg-type] — wake the queue

    # --- Core agentic loop ---

    async def _wake_cycle(self, trigger: str) -> None:
        """One wake cycle: drain queue → context → multi-turn LLM loop."""
        self._wake_count += 1
        logger.debug("Wake #%d trigger=%s task_id=%s", self._wake_count, trigger, self.task.task_id)

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
                # LLM failure — apply defaults for any open decisions
                self._apply_defaults(open_decisions)
                break

            self._total_llm_calls += 1

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
                logger.warning(
                    "LLM timeout (attempt %d/%d): task_id=%s",
                    attempt + 1,
                    1 + self.config.max_retries,
                    self.task.task_id,
                )
            except Exception:
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

    def _apply_defaults(self, open_decisions: list[ExpertSignal]) -> None:
        """When LLM fails, apply default_if_timeout for open decisions."""
        for dec in open_decisions:
            if dec.decision and "default_if_timeout" in dec.decision:
                default = dec.decision["default_if_timeout"]
                logger.info(
                    "Applying default_if_timeout=%r for decision in job=%s",
                    default,
                    dec.job_id,
                )
                # The actual application is handled by Kernel/Job (task 1.5).
                # Here we log it so the next wake cycle can see the decision was made.

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
