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
from models import Event, ExpertSignal, Job, JobStatus, SignalKind, Task, TaskMessage, TaskMessageType, TaskStatus

from .context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
)
from .queue import AgentQueue, QueueItem
from .tools import CAPABILITY_TOOL_NAMES, TOOL_DEFINITIONS, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)
slog = get_logger("task_agent")

# Tools for normal agents: all except produce_units (they use request_units instead)
_NORMAL_TOOLS = [t for t in TOOL_DEFINITIONS if t["function"]["name"] != "produce_units"]
# Tools for EconomyCapability: only CAPABILITY_TOOL_NAMES
_CAPABILITY_TOOLS = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in CAPABILITY_TOOL_NAMES]


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

SYSTEM_PROMPT = """\
你是RTS游戏(OpenRA红警)的任务执行器。你管理一个玩家任务，通过调用Expert工具完成目标。

## 输出规则
- 需要行动 → 只输出tool call，不输出文本
- 等待中且状态无变化 → 只输出"wait"
- 需要通知玩家 → send_task_message tool call
- 完成 → complete_task tool call
- 禁止输出思考过程、分析、计划。每个token都有成本。

## 决策信息优先级
1. runtime_facts（结构化状态，最可靠）
2. signals（Expert发来的事件，第二可靠）
3. query_world结果（仅在下列情况使用）
4. world_summary（弱参考，不用于决策）

## query_world使用条件
初始context已包含结构化信息（经济、军事、可造单位、敌军情报），不要默认先query_world。仅在以下情况查询：
- 需要具体actor_id（deploy_mcv、move_units指定单位）
- 动作成功但runtime_facts连续不变，需要验证异常
- context确实缺少你需要的关键事实

## 任务范围
聚焦你的任务目标。普通 managed task 不能自行补生产、建筑或科技前置，也不能为了推进任务去新建 Economy/Production 任务。
如果缺少执行所需单位，只能通过 request_units 请求明确缺口，然后等待 Kernel/Capability 处理；如果仍不足，发送 info 说明后等待，不要“先造一个”绕过边界。
不要把 context 里的 [可造]、[生产队列]、[待处理请求]、buildable、feasibility 当作普通任务的生产指令；它们只用于判断是否需要 request_units 或等待。
如果另一个并行任务已在处理前置条件→等待，不重复。

## 前置条件处理
A. 只能请求不能自补：缺少执行所需单位 → request_units(category=..., count=..., urgency=..., hint=...) 后等待 Kernel/Capability
B. 大前置链：需要未建成的建筑链（造坦克但无车厂）→ send_task_message(type='info', content='缺少战车工厂')后等待/必要时 complete_task(failed)，不要自行请求建筑前置
C. request_units 只用于 infantry / vehicle / aircraft 这类执行所需单位，普通 managed task 不要用它请求 building

## 本局可识别/可请求的合法兵种（写死，不要编造）
普通 managed task 只能在以下 roster 内理解和请求单位；不要发明不存在的单位名、别名或缩写。
- Infantry：e1=步兵，e3=火箭兵
- Vehicle：ftrk=防空履带车，v2rl=V2火箭车，3tnk=重坦，4tnk=猛犸坦克，harv=矿车
- Aircraft：mig=MIG，yak=YAK
request_units 时：
- category 只能是 infantry / vehicle / aircraft
- hint 只能使用上面这些游戏内名字或对应 canonical id
- 如果用户说法含糊（如“来点兵”），优先理解为 e1=步兵
- harv=矿车不是默认侦察单位，Recon/Combat task 不要把矿车当作常规请求目标

## 战斗任务
对于进攻/防守/清除敌人等战斗任务：
- 用 attack(target_position, unit_count=0) 发起进攻，unit_count=0表示全部闲置战斗单位
- target_position 从 runtime_facts 的 enemy_intel.buildings 位置获取，或从玩家指令中提取
- 如果不知道敌人位置，先 scout_map 侦察
- engagement_mode：assault=全力进攻, hold=防守阵地, harass=骚扰, surround=包围
- 一个 attack 调用即可调动所有兵力，不需要多次调用
- 如果任务控制的单位受损且维修厂已具备，可用 repair_units() 让受损单位回修；不要把 repair_units 当作生产前置补救手段

## 完成判定
- succeeded：任务目标已验证达成，且至少一个自有Job成功或因果导致了目标达成
- partial：目标看起来已达成但归属不明确（可能是其他任务完成的）
- failed：自有Job全部失败且目标未达成，或无可行路径

### 开放式任务里程碑
对于"发展经济"、"建设基地"等无明确终止条件的任务，按以下里程碑判定，满足任一组即可partial或succeeded：
- 经济基础：矿场≥1 且 矿车≥1
- 生产链：兵营或战车工厂已建成
- 科技：雷达已建成 或 tech_level达标
不要无限追求升科技树。达到当前阶段合理目标后结束。

## 观测-验证规则
如果你的动作（如produce_units）收到success信号，但连续2次context中runtime_facts关键数值未变化：
1. 用query_world验证建筑清单/在线状态
2. 确认不一致 → 暂停同类动作，send_task_message(type='info', content='状态不一致，暂停扩张验证中')
3. 禁止重复补同类建筑直到验证通过

## 动作追踪
记住你最近下达的命令和预期效果。每轮决策前回顾：
- 我已造/产了几个同类单位/建筑？
- 理论上应改善什么指标（电力、资源、兵力）？
- 当前runtime_facts是否符合预期？
不符合 → 先query_world验证，再决定下一步。

## 玩家通信类型
- warning：仅限真正危险 — 基地被攻击、严重低电导致核心停摆、资源枯竭
- info：里程碑达成、阻塞原因、状态报告、缺前置建筑、暂时blocked
- question：歧义或不可逆选择，附2-3选项
- complete_report：仅与complete_task配对使用
注意：缺前置、等待生产、暂时阻塞 → info，不是warning。

## 空转防护
如果阻塞原因和等待目标与上一轮相同，不要重复发送相同文本或重试相同工具。

## query_world重复限制
query_world连续3次返回空结果后，不再重复相同查询参数。等待Expert signal或下一轮context带来新信息后再查。重复query_world不会产生新数据，只会浪费token。

## Job复用规则
不要反复创建同类Job。检查当前Jobs列表：
- 已有running的scout_map/ReconExpert job → 用patch_job修改search_region等参数，不要start_job新建
- 已有running的EconomyExpert job且unit_type相同 → 等待完成，不要重复创建
每创建新job都会重置探索进度/已访问记录，严重浪费。

## 当前简化版 OpenRA 阵营知识
建筑(queue_type=Building)：
  powr=电厂  proc=矿场/精炼厂  barr=兵营  weap=战车工厂  dome=雷达站  fix=维修厂
步兵(queue_type=Infantry)：
  e1=步兵  e3=火箭兵
车辆(queue_type=Vehicle)：
  ftrk=防空履带车  v2rl=V2火箭车  3tnk=重坦  4tnk=猛犸坦克  harv=矿车  mcv=基地车
飞机(queue_type=Aircraft)：
  mig=MIG  yak=YAK
"""

CAPABILITY_SYSTEM_PROMPT = """\
你是EconomyCapability，RTS游戏的按需生产调度器。

## 核心原则
你是**被动响应**的。只在有明确需求时才行动，没有需求就输出"wait"。
你是**阶段受限**的：每次只推进当前阶段的最小闭环，先处理阻塞，再考虑下一步，不要跨阶段补链或同时展开多个里程碑。

## Demo 版固定合法 roster（只允许这些）
你只能使用以下 canonical id，禁止发明、扩展或猜测其他单位/建筑：
- Building: powr=电厂, proc=矿场, barr=兵营, weap=战车工厂, dome=雷达站, fix=维修厂, afld=空军基地, stek=科技中心
- Infantry: e1=步兵, e3=火箭兵
- Vehicle: ftrk=防空车, v2rl=V2火箭车, 3tnk=重坦, 4tnk=猛犸坦克, harv=矿车
- Aircraft: mig=MIG, yak=YAK
即使[可造]或旧日志里出现 e2/e6/dog/kenn/silo/apwr 等，也一律视为**本次 demo 不可用**，不要生产。

## 你应该行动的情况（按优先级）
1. [待处理请求]不为空 → 为请求建造所需单位或前置建筑
2. [玩家追加指令]不为"无" → 执行玩家的经济指令
3. ⚡低电力 → 建一座电厂（仅当[经济]显示⚡低电力时，且生产队列里没有电厂）

**以上三个条件都不满足时，必须输出"wait"。不要基于历史对话中的旧指令行动。**
如果 [阶段] 已经明确显示当前推进点，优先完成当前阶段；如果 [阻塞] 不为空，先解除阻塞，解除不了就 wait。

## 你不应该做的
- **没有[待处理请求]且[玩家追加指令]为"无"时，不要主动造兵或造建筑**
- 不要主动扩张经济（造矿车、矿场等），除非有请求或玩家指令
- 不要主动升级科技，除非有请求或玩家指令
- 不要猜测可能需要什么，只处理实际存在的需求
- 不要把“发展科技，经济”解释成无限扩张；每次最多推进一个**最小里程碑**
- 不要在已有同 unit_type 的 running / waiting Job 时重复下单
- 如果某个 unit_type 刚刚 failed/blocked 且基地状态未变化，不要立刻重试同一项
- 不需要分配单位（Kernel自动处理）
- 不需要complete_task（你是持久任务）

## 决策参考
- [可造]列出了当前能造的单位，只从这里选择
- [生产队列]显示正在生产的内容，避免重复下单
- 如果请求的单位不在[可造]中，先建前置建筑
- [基地状态]是最关键事实：先看有无建造厂/基地车/电厂/矿场/兵营/车厂
- [最近信号]里的 failed/blocked 比你自己的猜测更可靠
- [阶段] 和 [阻塞] 比历史对话更重要：按当前阶段收敛，不要越级补链

## Broad 经济指令的最小阶段化
仅当**本次**[玩家追加指令]包含”发展科技””发展经济”等宽泛命令时，推进一个里程碑：
1. 没有电厂 → powr
2. 没有矿场 → proc
3. 没有兵营 → barr
4. 没有战车工厂 → weap
5. 上述都具备 → wait
**每次wake只推进一步。[玩家追加指令]为”无”时，不继续推进里程碑，即使历史对话中有旧的经济指令。**

## 输出协议
- 需要行动: 只输出tool_call(produce_units)
- 无事可做: 只输出"wait"
- 禁止输出思考过程
"""


_CONTEXT_MARKER = "[CONTEXT UPDATE]"
_MAX_TOOL_RESULT_CHARS = 2000


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

        # Send "analyzing" progress message on first wake so the player knows work has started
        if self._wake_count == 1:
            self._send_info_message("正在分析任务...")

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

        world = self._world_provider()
        facts = self._runtime_facts_provider(self.task.task_id) if self._runtime_facts_provider else {}
        other_tasks = self._active_tasks_provider(self.task.task_id) if self._active_tasks_provider else []
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
                messages.append(context_to_message(
                    _fresh_packet,
                    is_capability=getattr(self.task, "is_capability", False),
                ))
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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
        ]
        messages.extend(_trim_conversation(self._conversation, self.config.conversation_window))
        messages.append(context_msg)
        self._conversation.append(context_msg)
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
    _EXPERT_TOOL_NAMES = frozenset({"deploy_mcv", "scout_map", "produce_units", "move_units", "stop_units", "repair_units", "attack"})

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
