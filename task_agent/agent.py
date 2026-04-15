"""Task Agent — per-Task LLM brain instance.

Implements the agentic loop: wake → context → multi-turn LLM tool use → sleep.
~250 lines of core logic on raw SDK, no framework.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
from dataclasses import asdict, dataclass, field, replace as dc_replace
from typing import Any, Callable, Optional

from benchmark import span as bm_span
from logging_system import get_logger
from llm import LLMProvider, LLMResponse
from models import Event, EventType, ExpertSignal, Job, JobStatus, SignalKind, Task, TaskMessage, TaskMessageType, TaskStatus

from .context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
)
from .policy import (
    build_capability_system_prompt,
    build_system_prompt,
    capability_tools,
    ordinary_tools,
)
from .queue import AgentQueue, QueueItem
from .tools import CAPABILITY_TOOL_NAMES, TOOL_DEFINITIONS, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)
slog = get_logger("task_agent")

# Tools for normal agents: capability-exclusive production posture tools stay hidden.
_NORMAL_TOOLS = ordinary_tools(TOOL_DEFINITIONS)
# Tools for EconomyCapability: only CAPABILITY_TOOL_NAMES
_CAPABILITY_TOOLS = capability_tools(TOOL_DEFINITIONS, CAPABILITY_TOOL_NAMES)


class _AgentFatalError(Exception):
    """Raised when agent reaches max consecutive failures and must stop."""

# Type for a callback that fetches current Jobs for this Task
JobsProvider = Callable[[str], list[Job]]
# Type for a callback that fetches current WorldSummary
WorldSummaryProvider = Callable[[], WorldSummary]
# Type for a callback that fetches structured runtime facts for a given task_id
RuntimeFactsProvider = Callable[[str], dict]
# Type for a callback that fetches other currently-active tasks (excluding this task)
# Returns list of dicts with keys: label, raw_text, status
ActiveTasksProvider = Callable[[str], list[dict]]
# Type for a callback that sends TaskMessage to Kernel (for player notification)
MessageCallback = Callable[[TaskMessage], None]


SYSTEM_PROMPT = build_system_prompt()
CAPABILITY_SYSTEM_PROMPT = build_capability_system_prompt()


_CONTEXT_MARKER = "[CONTEXT UPDATE]"
_MAX_TOOL_RESULT_CHARS = 2000
_MAX_HISTORY_CONTEXT_LINES = 6
_HISTORY_CONTEXT_PREFIXES = (
    "[任务]",
    "[世界]",
    "[状态]",
    "[阶段]",
    "[阻塞]",
    "[能力近期指令]",
    "[待处理请求]",
    "[单位预留]",
    "[玩家消息]",
    "[其他活跃任务]",
)


def _trim_conversation(
    conversation: list[dict[str, Any]],
    max_turns: int,
) -> list[dict[str, Any]]:
    """Return a slice of conversation keeping the last max_turns context turns.

    A "turn" starts at each user message whose content begins with the context
    marker.  Everything from the (len-max_turns)th such marker onwards is kept;
    older messages are discarded.
    """
    ctx_indices = [
        i for i, msg in enumerate(conversation)
        if msg.get("role") == "user"
        and _CONTEXT_MARKER in str(msg.get("content", ""))
    ]
    if len(ctx_indices) <= max_turns:
        return list(conversation)
    start = ctx_indices[-max_turns]
    return conversation[start:]


def _dedup_signals(signals: list[ExpertSignal]) -> list[ExpertSignal]:
    """Collapse consecutive signals of the same kind into one with ×N annotation.

    Keeps the last occurrence of each run so the most recent data/summary is
    preserved.  Decision-request signals are excluded before this is called.
    """
    if not signals:
        return signals
    result: list[ExpertSignal] = []
    i = 0
    while i < len(signals):
        run_kind = signals[i].kind
        j = i
        while j < len(signals) and signals[j].kind == run_kind:
            j += 1
        run_len = j - i
        last = signals[j - 1]  # keep last (most recent) in the run
        if run_len > 1:
            last = dc_replace(last, summary=f"{last.summary} (×{run_len})")
        result.append(last)
        i = j
    return result


def _truncate_tool_result(result: Any) -> str:
    """Serialise a tool result, summarising large payloads to save context space.

    - query_world results with a data list → replaced with count + first item
    - Any other result > _MAX_TOOL_RESULT_CHARS → hard-truncated with note
    """
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list) and len(data) > 5:
            summary = dict(result)
            summary["data"] = data[:3]
            summary["data_count"] = len(data)
            summary["data_truncated"] = f"showing 3 of {len(data)} items"
            content = json.dumps(summary, ensure_ascii=False)
            if len(content) <= _MAX_TOOL_RESULT_CHARS:
                return content
    content = json.dumps(result, ensure_ascii=False)
    if len(content) > _MAX_TOOL_RESULT_CHARS:
        content = content[:_MAX_TOOL_RESULT_CHARS] + ' "[...truncated]"}'
    return content


def _compact_history_context_message(message: dict[str, Any]) -> dict[str, Any]:
    """Store prior context turns in compact form to bound long-task prompt growth."""
    if message.get("role") != "user":
        return dict(message)
    content = str(message.get("content", "") or "")
    if _CONTEXT_MARKER not in content:
        return dict(message)
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) <= 2:
        return dict(message)
    compact_lines = [_CONTEXT_MARKER]
    selected: list[str] = []
    seen_prefixes: set[str] = set()
    for line in lines[2:]:
        prefix = next((item for item in _HISTORY_CONTEXT_PREFIXES if line.startswith(item)), "")
        if prefix:
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
        if not prefix and selected:
            continue
        selected.append(line)
        if len(selected) >= _MAX_HISTORY_CONTEXT_LINES:
            break
    if not selected:
        selected.extend(lines[2:2 + _MAX_HISTORY_CONTEXT_LINES])
    compact_lines.extend(selected)
    return {
        **message,
        "content": "\n".join(compact_lines),
    }


def _replace_fresh_context_message(
    messages: list[dict[str, Any]],
    *,
    current_wake_context_index: int,
    context_msg: dict[str, Any],
) -> None:
    """Keep the initial wake context plus only the latest refreshed context.

    During a multi-turn tool-use wake, each tool round may refresh context so
    the next LLM call sees updated jobs/runtime facts. Older refreshed context
    packets in the same wake are redundant and cause prompt growth. Preserve:
    - all history context turns from previous wakes
    - the initial context packet for the current wake
    - only the newest refreshed context packet after that
    """
    keep: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if index <= current_wake_context_index:
            keep.append(message)
            continue
        if (
            message.get("role") == "user"
            and _CONTEXT_MARKER in str(message.get("content", ""))
        ):
            continue
        keep.append(message)
    keep.append(context_msg)
    messages[:] = keep


@dataclass
class AgentConfig:
    """Configuration for a Task Agent instance."""

    review_interval: float = 10.0  # seconds between periodic wakes
    max_turns: int = 10  # max LLM call rounds per wake cycle
    llm_timeout: float = 60.0  # seconds before LLM call times out
    max_retries: int = 1  # LLM call retries on failure
    max_consecutive_failures: int = 5  # consecutive LLM failures before auto-terminate
    conversation_window: int = 6  # max context-update turns to retain in history


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
        runtime_facts_provider: Optional[RuntimeFactsProvider] = None,
        active_tasks_provider: Optional[ActiveTasksProvider] = None,
    ) -> None:
        self.task = task
        self.llm = llm
        self.tool_executor = tool_executor
        self._jobs_provider = jobs_provider
        self._world_provider = world_summary_provider
        self._runtime_facts_provider: Optional[RuntimeFactsProvider] = runtime_facts_provider
        self._active_tasks_provider: Optional[ActiveTasksProvider] = active_tasks_provider
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
        self._bootstrap_job_id: Optional[str] = None
        self._bootstrap_raw_text: Optional[str] = None
        # Smart wake: skip LLM when there is no new information
        self._last_job_snapshot: Optional[dict[str, str]] = None
        # Unit request suspension: skip LLM wake cycles while waiting
        self._suspended = False

    def set_runtime_facts_provider(self, provider: RuntimeFactsProvider) -> None:
        """Wire the runtime facts provider after construction (called by Kernel)."""
        self._runtime_facts_provider = provider

    def set_active_tasks_provider(self, provider: ActiveTasksProvider) -> None:
        """Wire the active tasks provider after construction (called by Kernel)."""
        self._active_tasks_provider = provider

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

    def suspend(self) -> None:
        """Suspend LLM wake cycles (waiting for unit requests)."""
        self._suspended = True

    def resume_with_event(self, event: Event) -> None:
        """Resume suspended agent and deliver a wake event."""
        self._suspended = False
        self.push_event(event)

    @property
    def is_suspended(self) -> bool:
        """Whether the agent is intentionally parked waiting for unit fulfillment."""
        return self._suspended

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
                # Trigger label resolved after drain in _wake_cycle;
                # pass raw woken_by_event flag so the cycle can distinguish
                # review-sentinel wakes from real event wakes.
                trigger = "event_or_review" if woken_by_event else "timer"
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

    def _capability_has_actionable_demand(
        self,
        *,
        events: list[Event],
        runtime_facts: dict[str, Any],
    ) -> bool:
        """Return True when the persistent capability is explicitly allowed to act."""
        if not getattr(self.task, "is_capability", False):
            return True
        if not str(getattr(self.task, "raw_text", "") or "").startswith("EconomyCapability"):
            return True
        if any(getattr(evt, "type", None) == EventType.PLAYER_MESSAGE for evt in events):
            return True
        requests = runtime_facts.get("unfulfilled_requests", []) if isinstance(runtime_facts, dict) else []
        if isinstance(requests, list) and requests:
            return True
        capability_status = runtime_facts.get("capability_status", {}) if isinstance(runtime_facts, dict) else {}
        if not isinstance(capability_status, dict):
            capability_status = {}
        for key in (
            "pending_request_count",
            "blocking_request_count",
            "dispatch_request_count",
            "bootstrapping_request_count",
            "start_released_request_count",
            "reinforcement_request_count",
            "inference_pending_count",
        ):
            try:
                if int(capability_status.get(key, 0) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    # --- Core agentic loop ---

    async def _safe_wake_cycle(self, trigger: str) -> None:
        """Error-isolated wake cycle. Catches unexpected exceptions so one
        bad cycle doesn't crash the entire agent. LLM failures are handled
        inside _wake_cycle; this catches everything else (tool errors, etc.)."""
        try:
            await self._wake_cycle(trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception(
                "Unexpected wake cycle error: task_id=%s",
                self.task.task_id,
            )
            self._last_llm_error = f"wake_cycle_crash ({type(exc).__name__}): {str(exc)[:200]}"
            slog.error(
                "Unexpected wake cycle error",
                event="wake_cycle_error",
                task_id=self.task.task_id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                traceback=tb,
            )
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.max_consecutive_failures:
                await self._auto_terminate_on_failure()
                raise _AgentFatalError()

    async def _wake_cycle(self, trigger: str) -> None:
        """One wake cycle: drain queue → context → multi-turn LLM loop."""
        if self._suspended:
            # Drain queue to prevent busy-spin (undrained items cause
            # wait_for_wake to return immediately on next iteration).
            self.queue.drain()
            return  # Skip LLM while waiting for unit request fulfillment
        self._wake_count += 1

        # Drain pending signals and events
        items = self.queue.drain()
        signals = [i for i in items if isinstance(i, ExpertSignal)]
        events = [i for i in items if isinstance(i, Event)]

        # Refine trigger label now that we know what arrived
        if signals or events:
            effective_trigger = "event"
        elif trigger == "event_or_review":
            effective_trigger = "review"
        else:
            effective_trigger = "timer"

        logger.debug("Wake #%d trigger=%s task_id=%s", self._wake_count, effective_trigger, self.task.task_id)
        slog.debug("TaskAgent wake", event="agent_wake", task_id=self.task.task_id, wake=self._wake_count, trigger=effective_trigger)

        # Separate open decisions (decision_request signals)
        open_decisions = [s for s in signals if s.kind == SignalKind.DECISION_REQUEST]
        recent_signals = _dedup_signals([s for s in signals if s.kind != SignalKind.DECISION_REQUEST])

        if await self._maybe_finalize_bootstrap_task(recent_signals):
            return

        # Bootstrap functions run for their side effects (job pre-creation,
        # _bootstrap_job_id assignment) but never block the LLM.  The LLM
        # must run every wake so it can handle DECISION_REQUEST signals,
        # correct misfired bootstraps, and complete compound commands.
        jobs = self._jobs_provider(self.task.task_id)
        self._maybe_attach_existing_rule_job(jobs)
        await self._maybe_bootstrap_structure_build(jobs)
        await self._maybe_bootstrap_simple_production(jobs)
        # Re-fetch jobs after bootstrap so newly created jobs appear in context.
        jobs = self._jobs_provider(self.task.task_id)

        world = self._world_provider()
        facts = self._runtime_facts_provider(self.task.task_id) if self._runtime_facts_provider else {}
        other_tasks = self._active_tasks_provider(self.task.task_id) if self._active_tasks_provider else []

        if not self._capability_has_actionable_demand(events=events, runtime_facts=facts):
            slog.info(
                "Capability wake idled: no actionable demand",
                event="capability_idle_skipped",
                task_id=self.task.task_id,
                wake=self._wake_count,
                trigger=effective_trigger,
            )
            return

        # Send "analyzing" progress message on first wake so the player knows work has started
        if self._wake_count == 1:
            self._send_info_message("正在分析任务...")

        # Smart wake: skip LLM when there is no new information.
        # Only applies once at least one job exists (first wake must always
        # reach the LLM so it can start jobs).
        if not signals and not events and jobs:
            current_snapshot = {j.job_id: j.status.value for j in jobs}
            if current_snapshot == self._last_job_snapshot:
                slog.debug(
                    "TaskAgent wake skipped: no new signals/events, job statuses unchanged",
                    event="wake_skipped",
                    task_id=self.task.task_id,
                    wake=self._wake_count,
                    trigger=effective_trigger,
                )
                return

        # Update job snapshot — recorded just before we commit to an LLM call
        self._last_job_snapshot = {j.job_id: j.status.value for j in jobs}
        packet = build_context_packet(
            task=self.task,
            jobs=jobs,
            world_summary=world,
            recent_signals=recent_signals,
            recent_events=events,
            open_decisions=open_decisions,
            runtime_facts=facts,
            other_active_tasks=other_tasks,
            bootstrap_job_id=self._bootstrap_job_id,
        )
        slog.info(
            "TaskAgent context snapshot",
            event="context_snapshot",
            task_id=self.task.task_id,
            wake=self._wake_count,
            packet=asdict(packet),
        )

        # Inject context as user message
        ctx_msg = context_to_message(
            packet,
            is_capability=getattr(self.task, "is_capability", False),
        )

        # Build messages for this cycle
        messages = self._build_messages(ctx_msg)
        current_wake_context_index = len(messages) - 1

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
                slog.warn(
                    "TaskAgent LLM call failed",
                    event="llm_failed",
                    task_id=self.task.task_id,
                    consecutive_failures=self._consecutive_failures,
                    last_error=self._last_llm_error,
                )
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
            # Extract reasoning_content from raw provider response (Qwen3 thinking mode)
            _raw = response.raw
            _reasoning = None
            if _raw is not None:
                # Qwen3: choices[0].message.reasoning_content
                try:
                    _reasoning = _raw.choices[0].message.reasoning_content or None
                except Exception:
                    pass
                if not _reasoning:
                    try:
                        _reasoning = _raw.get("reasoning_content") or None
                    except Exception:
                        pass
            slog.info(
                "TaskAgent LLM call succeeded",
                event="llm_succeeded",
                task_id=self.task.task_id,
                model=response.model,
                usage=response.usage,
                tool_calls=len(response.tool_calls),
                response_text=response.text or None,
                reasoning_content=_reasoning,
                tool_calls_detail=[
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            )
            self._log_reasoning_text(response.text, turn=turn + 1)

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

                # Inject fresh context for next turn — job/world state may have changed
                # (e.g. start_job just created a job). Appended to messages only, NOT to
                # self._conversation; next wake builds its own fresh context anyway.
                _fresh_jobs = self._jobs_provider(self.task.task_id)
                _fresh_world = self._world_provider()
                _fresh_facts = (
                    self._runtime_facts_provider(self.task.task_id)
                    if self._runtime_facts_provider
                    else {}
                )
                _fresh_other = (
                    self._active_tasks_provider(self.task.task_id)
                    if self._active_tasks_provider
                    else []
                )
                _fresh_packet = build_context_packet(
                    task=self.task,
                    jobs=_fresh_jobs,
                    world_summary=_fresh_world,
                    runtime_facts=_fresh_facts,
                    other_active_tasks=_fresh_other,
                )
                _replace_fresh_context_message(
                    messages,
                    current_wake_context_index=current_wake_context_index,
                    context_msg=context_to_message(
                        _fresh_packet,
                        is_capability=getattr(self.task, "is_capability", False),
                    ),
                )
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

    def _log_reasoning_text(self, text: Optional[str], *, turn: int) -> None:
        """Persist non-tool LLM text so diagnostics can show the reasoning path."""
        reasoning = (text or "").strip()
        if not reasoning:
            return
        slog.info(
            reasoning,
            event="llm_reasoning",
            task_id=self.task.task_id,
            wake=self._wake_count,
            turn=turn,
        )

    async def _maybe_bootstrap_structure_build(self, jobs: list[Job]) -> bool:
        """Deterministically pin common Chinese build-structure commands.

        Live testing showed that prompt-only guidance is not enough for
        commands such as "建造矿场": the LLM can still reinterpret them as
        recon/expansion or unit production. For simple, first-turn structure
        build commands we bootstrap the correct EconomyExpert job directly.
        This is reserved for capability tasks; ordinary managed tasks should
        request_units and wait instead of self-supplementing prerequisites.
        """
        if not getattr(self.task, "is_capability", False):
            return False
        if self._bootstrap_job_id is not None:
            return False  # already bootstrapped; do not create a duplicate
        if jobs:
            return False

        normalized = re.sub(r"\s+", "", self.task.raw_text)
        if not normalized.startswith(("建造", "修建", "造")):
            return False

        if "矿场" in normalized or "精炼厂" in normalized:
            unit_type = "proc"
        elif "电厂" in normalized or "发电厂" in normalized:
            unit_type = "powr"
        elif "兵营" in normalized:
            unit_type = "barr"
        else:
            return False

        result = await self.tool_executor.execute(
            tool_call_id=f"bootstrap_{self.task.task_id}",
            name="start_job",
            arguments_json=json.dumps(
                {
                    "expert_type": "EconomyExpert",
                    "config": {
                        "unit_type": unit_type,
                        "count": 1,
                        "queue_type": "Building",
                        "repeat": False,
                    },
                },
                ensure_ascii=False,
            ),
        )
        if result.error:
            logger.warning(
                "Bootstrap structure-build failed: task_id=%s raw_text=%r error=%s",
                self.task.task_id,
                self.task.raw_text,
                result.error,
            )
            return False

        job_id = None
        if isinstance(result.result, dict):
            job_id = result.result.get("job_id")
        if isinstance(job_id, str):
            self._bootstrap_job_id = job_id
            self._bootstrap_raw_text = self.task.raw_text

        slog.info(
            "Bootstrapped structure build job",
            event="bootstrap_structure_build",
            task_id=self.task.task_id,
            raw_text=self.task.raw_text,
            unit_type=unit_type,
        )
        return True

    async def _maybe_bootstrap_simple_production(self, jobs: list[Job]) -> bool:
        """Deterministically pin common one-shot production commands.

        Live testing showed that simple infantry commands like "生产3个步兵"
        can still drift through the LLM into non-canonical unit ids such as
        `rifl`, even though the live RA ruleset expects `e1`. For the common
        "生产/造/训练 + <unit>" path, bootstrap directly into the correct
        EconomyExpert config instead of spending LLM turns guessing ids.
        This is reserved for capability tasks; ordinary managed tasks should
        request_units and wait instead of self-supplementing production.
        """
        if not getattr(self.task, "is_capability", False):
            return False
        if self._bootstrap_job_id is not None:
            return False  # already bootstrapped; do not create a duplicate
        if jobs:
            return False

        normalized = re.sub(r"\s+", "", self.task.raw_text)
        if normalized.startswith(("建造", "修建")):
            return False
        if not any(token in normalized for token in ("生产", "造", "训练", "补")):
            return False

        unit_type = None
        queue_type = None
        for aliases, canonical, queue in (
            (("步兵", "枪兵", "步枪兵", "普通步兵"), "e1", "Infantry"),
            (("火箭兵", "火箭筒兵", "导弹兵"), "e3", "Infantry"),
            (("工程师", "维修工程师"), "e6", "Infantry"),
        ):
            if any(alias in normalized for alias in aliases):
                unit_type = canonical
                queue_type = queue
                break
        if unit_type is None or queue_type is None:
            return False

        count = self._extract_requested_count(normalized)
        result = await self.tool_executor.execute(
            tool_call_id=f"bootstrap_{self.task.task_id}",
            name="start_job",
            arguments_json=json.dumps(
                {
                    "expert_type": "EconomyExpert",
                    "config": {
                        "unit_type": unit_type,
                        "count": count,
                        "queue_type": queue_type,
                        "repeat": False,
                    },
                },
                ensure_ascii=False,
            ),
        )
        if result.error:
            logger.warning(
                "Bootstrap simple production failed: task_id=%s raw_text=%r error=%s",
                self.task.task_id,
                self.task.raw_text,
                result.error,
            )
            return False

        job_id = None
        if isinstance(result.result, dict):
            job_id = result.result.get("job_id")
        if isinstance(job_id, str):
            self._bootstrap_job_id = job_id
            self._bootstrap_raw_text = self.task.raw_text

        slog.info(
            "Bootstrapped simple production job",
            event="bootstrap_simple_production",
            task_id=self.task.task_id,
            raw_text=self.task.raw_text,
            unit_type=unit_type,
            count=count,
            queue_type=queue_type,
        )
        return True

    def _maybe_attach_existing_rule_job(self, jobs: list[Job]) -> bool:
        """Treat simple rule-routed tasks as monitor-only once a single job exists.

        Live testing showed that simple Adjutant rule-routed commands such as
        "探索地图" could still wake the TaskAgent and let it re-plan into
        unrelated cross-domain work (e.g. building factories and extra scouts).
        When the task text is a single-intent command and it already has one
        matching job, the agent should only monitor that job and wait for its
        terminal signal instead of continuing to re-plan through the LLM.
        """
        if self._bootstrap_job_id is not None:
            return True
        if len(jobs) != 1:
            return False

        normalized = re.sub(r"\s+", "", self.task.raw_text)
        if not normalized:
            return False
        if self._looks_like_complex_command(normalized):
            return False

        job = jobs[0]
        if self._is_simple_rule_job(normalized, job):
            self._bootstrap_job_id = job.job_id
            self._bootstrap_raw_text = self.task.raw_text
            slog.info(
                "Attached existing rule-routed job for monitor-only task",
                event="bootstrap_existing_rule_job",
                task_id=self.task.task_id,
                job_id=job.job_id,
                expert_type=job.expert_type,
                raw_text=self.task.raw_text,
            )
            return True
        return False

    @staticmethod
    def _looks_like_complex_command(normalized_text: str) -> bool:
        return any(token in normalized_text for token in ("然后", "之后", "并且", "同时", "别", "不要", "如果", "优先"))

    @staticmethod
    def _is_simple_rule_job(normalized_text: str, job: Job) -> bool:
        if "基地车" in normalized_text and ("部署" in normalized_text or normalized_text.lower().startswith("deploy")):
            return job.expert_type == "DeployExpert"
        if any(token in normalized_text for token in ("探索地图", "找敌人", "找基地")):
            return job.expert_type == "ReconExpert"
        if normalized_text.startswith(("建造", "修建", "造")):
            return job.expert_type == "EconomyExpert"
        if any(token in normalized_text for token in ("生产", "训练", "补")):
            return job.expert_type == "EconomyExpert"
        return False

    def _extract_requested_count(self, normalized_text: str) -> int:
        match = re.search(r"(\d+)", normalized_text)
        if match:
            return max(1, int(match.group(1)))

        chinese_digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if "十" in normalized_text:
            left, _, right = normalized_text.partition("十")
            tens = chinese_digits.get(left, 1 if left == "" else 0)
            ones = chinese_digits.get(right[:1], 0)
            value = tens * 10 + ones
            if value > 0:
                return value
        for char in normalized_text:
            if char in chinese_digits and chinese_digits[char] > 0:
                return chinese_digits[char]
        return 1

    async def _maybe_finalize_bootstrap_task(self, recent_signals: list[ExpertSignal]) -> bool:
        """Close deterministic bootstrap build tasks without another LLM turn.

        Checks bootstrap job status directly (not signals) so it fires even when
        the terminal signal was already consumed by a previous wake that failed to
        call complete_task (e.g. LLM returned text instead of a tool call).
        """
        if self._bootstrap_job_id is None:
            return False

        jobs = self._jobs_provider(self.task.task_id)
        bootstrap_job = next((j for j in jobs if j.job_id == self._bootstrap_job_id), None)
        if bootstrap_job is None:
            return False

        if bootstrap_job.status == JobStatus.SUCCEEDED:
            result = "succeeded"
        elif bootstrap_job.status in (JobStatus.FAILED, JobStatus.ABORTED):
            result = "failed"
        else:
            return False  # Still running or waiting — not ready to close

        prefix = {
            "succeeded": "已完成",
            "failed": "未完成",
            "aborted": "已中止",
        }.get(result, "已结束")
        raw_text = self._bootstrap_raw_text or self.task.raw_text
        summary = f"{prefix}：{raw_text}"
        # Best-effort: enrich summary from matching signal if available this wake
        for signal in recent_signals:
            if signal.job_id == self._bootstrap_job_id and signal.kind == SignalKind.TASK_COMPLETE:
                if signal.summary:
                    summary = f"{summary}。{signal.summary}"
                break

        complete = await self.tool_executor.execute(
            tool_call_id=f"bootstrap_complete_{self.task.task_id}",
            name="complete_task",
            arguments_json=json.dumps(
                {
                    "result": result,
                    "summary": summary,
                },
                ensure_ascii=False,
            ),
        )
        if complete.error is None:
            self._task_completed = True
            return True

        logger.warning(
            "Bootstrap auto-complete failed: task_id=%s job_id=%s error=%s",
            self.task.task_id,
            self._bootstrap_job_id,
            complete.error,
        )
        return False

    def _build_messages(self, context_msg: dict[str, str]) -> list[dict[str, Any]]:
        """Build the message list for an LLM call.

        Applies a sliding window over conversation history: retains the last
        `conversation_window` context-update turns plus their assistant/tool
        responses. Older turns are dropped silently — no LLM summarization.
        """
        prompt = CAPABILITY_SYSTEM_PROMPT if getattr(self.task, "is_capability", False) else SYSTEM_PROMPT
        trimmed_history = _trim_conversation(self._conversation, self.config.conversation_window)
        if len(trimmed_history) != len(self._conversation):
            self._conversation = trimmed_history
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
        ]
        messages.extend(self._conversation)
        messages.append(context_msg)
        self._conversation.append(_compact_history_context_message(context_msg))
        return messages

    @staticmethod
    def _classify_llm_error(exc: Exception) -> str:
        """Classify an LLM exception into a diagnostic category."""
        exc_name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if status is not None:
            return f"provider_error (HTTP {status})"
        msg = str(exc).lower()
        if "json" in msg or "parse" in msg or "decode" in msg:
            return "parse_error"
        if "connect" in msg or "network" in msg or "socket" in msg or "dns" in msg:
            return "network_error"
        return f"unknown_error ({exc_name})"

    async def _call_llm(self, messages: list[dict[str, Any]]) -> Optional[LLMResponse]:
        """Call the LLM with retry and timeout."""
        tools = _CAPABILITY_TOOLS if getattr(self.task, "is_capability", False) else _NORMAL_TOOLS
        for attempt in range(1 + self.config.max_retries):
            try:
                slog.info(
                    "TaskAgent LLM input",
                    event="llm_input",
                    task_id=self.task.task_id,
                    wake=self._wake_count,
                    attempt=attempt + 1,
                    messages=messages,
                    tools=[tool["function"]["name"] for tool in tools],
                )
                with bm_span("llm_call", name=f"task_agent:{self.task.task_id}"):
                    response = await asyncio.wait_for(
                        self.llm.chat(messages, tools=tools),
                        timeout=self.config.llm_timeout,
                    )
                # Detect empty output (no text and no tool_calls)
                if not response.tool_calls and not (response.text or "").strip():
                    self._last_llm_error = "empty_output"
                    slog.warn(
                        "LLM returned empty output (no text, no tool_calls)",
                        event="llm_empty_output",
                        task_id=self.task.task_id,
                        wake=self._wake_count,
                        attempt=attempt + 1,
                        model=response.model,
                        usage=response.usage,
                    )
                    return None
                return response
            except asyncio.TimeoutError:
                error_type = "timeout"
                self._last_llm_error = f"timeout ({self.config.llm_timeout}s)"
                slog.warn(
                    f"LLM call failed: {error_type}",
                    event="llm_call_error",
                    task_id=self.task.task_id,
                    error_type=error_type,
                    error=self._last_llm_error,
                    attempt=attempt + 1,
                    max_attempts=1 + self.config.max_retries,
                )
                logger.warning(
                    "LLM timeout (attempt %d/%d): task_id=%s",
                    attempt + 1,
                    1 + self.config.max_retries,
                    self.task.task_id,
                )
            except Exception as e:
                error_type = self._classify_llm_error(e)
                self._last_llm_error = f"{error_type}: {str(e)[:200]}"
                slog.warn(
                    f"LLM call failed: {error_type}",
                    event="llm_call_error",
                    task_id=self.task.task_id,
                    error_type=error_type,
                    error=str(e)[:500],
                    attempt=attempt + 1,
                    max_attempts=1 + self.config.max_retries,
                )
                logger.exception(
                    "LLM error (attempt %d/%d): task_id=%s",
                    attempt + 1,
                    1 + self.config.max_retries,
                    self.task.task_id,
                )
        return None

    # Names of Expert action tools whose success warrants a progress message.
    _EXPERT_TOOL_NAMES = frozenset({"deploy_mcv", "scout_map", "produce_units", "move_units", "stop_units", "repair_units", "attack", "attack_actor"})

    async def _execute_tools(self, response: LLMResponse) -> list[ToolResult]:
        """Execute all tool calls in parallel and return results in call order.

        asyncio.gather runs all coroutines concurrently on the same event loop.
        kernel.start_job is synchronous so two simultaneous Expert tool calls
        (e.g. scout_map + produce_units) interleave safely.  Exceptions from
        individual tools are caught and wrapped as ToolResult(error=...) so
        they never cancel sibling executions.
        """
        coros = [
            self.tool_executor.execute(tc.id, tc.name, tc.arguments)
            for tc in response.tool_calls
        ]
        raw = await asyncio.gather(*coros, return_exceptions=True)

        results: list[ToolResult] = []
        for tc, outcome in zip(response.tool_calls, raw):
            if isinstance(outcome, ToolResult):
                results.append(outcome)
            else:
                # asyncio.gather caught an exception — wrap it
                results.append(ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    result={},
                    error=str(outcome),
                ))

        # Emit progress feedback for successful Expert tool calls
        for tc, result in zip(response.tool_calls, results):
            if tc.name in self._EXPERT_TOOL_NAMES and result.error is None:
                self._send_info_message(f"正在部署 {tc.name}...")

        return results

    def _send_info_message(self, content: str) -> None:
        """Send a TASK_INFO progress message to the player via message_callback."""
        if self._message_callback is None:
            return
        import uuid as _uuid
        try:
            msg = TaskMessage(
                message_id=f"info_{_uuid.uuid4().hex[:8]}",
                task_id=self.task.task_id,
                type=TaskMessageType.TASK_INFO,
                content=content,
                priority=self.task.priority,
            )
            self._message_callback(msg)
        except Exception:
            logger.debug("_send_info_message failed: %s", content)

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
            error_detail = f"（{self._last_llm_error}）" if self._last_llm_error else ""
            await self.tool_executor.execute(
                tool_call_id="auto_fail",
                name="complete_task",
                arguments_json=json.dumps({
                    "result": "failed",
                    "summary": f"LLM连续失败{self._consecutive_failures}次{error_detail}，自动终止",
                }, ensure_ascii=False),
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
        """Convert a ToolResult to a tool message for the LLM.

        Large payloads (e.g. full actor lists from query_world) are summarised
        so they don't dominate the context window.
        """
        if result.error:
            content = json.dumps({"error": result.error}, ensure_ascii=False)
        else:
            content = _truncate_tool_result(result.result)
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": content,
        }
