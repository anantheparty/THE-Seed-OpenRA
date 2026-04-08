"""Tests for Task Agent agentic loop — using MockProvider + mock tool handlers."""

from __future__ import annotations

import asyncio
import json
import time
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging_system
from llm import LLMResponse, MockProvider, ToolCall
from models import (
    EconomyJobConfig,
    ExpertSignal,
    Event,
    EventType,
    Job,
    JobStatus,
    ReconJobConfig,
    SignalKind,
    Task,
    TaskKind,
    TaskStatus,
)
from task_agent import (
    AgentConfig,
    TaskAgent,
    ToolExecutor,
    WorldSummary,
    build_context_packet,
    context_to_message,
)
from task_agent.agent import CAPABILITY_SYSTEM_PROMPT, SYSTEM_PROMPT, _trim_conversation, _dedup_signals, _truncate_tool_result, _CONTEXT_MARKER


# --- Helpers ---

def make_task(task_id: str = "t1", raw_text: str = "侦察东北方向") -> Task:
    return Task(task_id=task_id, raw_text=raw_text, kind=TaskKind.MANAGED, priority=50)


def make_job(job_id: str = "j1", task_id: str = "t1") -> Job:
    return Job(
        job_id=job_id,
        task_id=task_id,
        expert_type="ReconExpert",
        config=ReconJobConfig(search_region="northeast", target_type="base", target_owner="enemy"),
        resources=["actor:57"],
    )


def make_world() -> WorldSummary:
    return WorldSummary(
        economy={"cash": 5000, "income": 200},
        military={"units": 15, "combat_value": 2500},
        map={"explored_pct": 0.35},
        known_enemy={"bases": 0, "units_spotted": 3},
    )


def noop_jobs_provider(task_id: str) -> list[Job]:
    return []


def noop_world_provider() -> WorldSummary:
    return make_world()


async def mock_tool_handler(name: str, args: dict) -> dict:
    """Mock tool handler that returns success for any tool."""
    if name == "start_job":
        return {"job_id": "j_new_1", "status": "running"}
    if name == "complete_task":
        return {"ok": True}
    if name == "query_world":
        return {"actors": [{"actor_id": 57, "name": "2tnk", "position": [100, 200]}]}
    return {"ok": True}


def make_executor() -> ToolExecutor:
    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, mock_tool_handler)
    # start_job is no longer in TOOL_DEFINITIONS (not LLM-facing) but is
    # still needed for bootstrap paths in agent.py (internal use).
    executor.register("start_job", mock_tool_handler)
    return executor


# --- Tests ---

def test_context_packet_construction():
    """Context packet contains all required fields with timestamps."""
    task = make_task()
    jobs = [make_job()]
    world = make_world()
    signal = ExpertSignal(
        task_id="t1", job_id="j1", kind=SignalKind.PROGRESS,
        summary="Scouting in progress", expert_state={"progress_pct": 0.4},
    )
    decision = ExpertSignal(
        task_id="t1", job_id="j1", kind=SignalKind.DECISION_REQUEST,
        summary="Scout lost, what to do?",
        decision={"options": ["wait", "use_infantry", "abort"], "default_if_timeout": "wait"},
    )

    packet = build_context_packet(
        task=task, jobs=jobs, world_summary=world,
        recent_signals=[signal], open_decisions=[decision],
    )

    assert packet.task["task_id"] == "t1"
    assert packet.task["raw_text"] == "侦察东北方向"
    assert packet.task["timestamp"] > 0
    assert len(packet.jobs) == 1
    assert packet.jobs[0]["expert_type"] == "ReconExpert"
    assert packet.jobs[0]["resources"] == ["actor:57"]
    assert packet.world_summary["economy"]["cash"] == 5000
    assert len(packet.recent_signals) == 1
    assert packet.recent_signals[0]["kind"] == "progress"
    assert len(packet.open_decisions) == 1
    assert packet.open_decisions[0]["decision"]["default_if_timeout"] == "wait"
    assert packet.recent_events == []  # No events passed
    assert packet.timestamp > 0
    print("  PASS: context_packet_construction")


def test_context_to_message():
    """Context packet converts to a compact structured text LLM user message."""
    packet = build_context_packet(task=make_task(), jobs=[make_job()])
    msg = context_to_message(packet)
    assert msg["role"] == "user"
    assert "[任务]" in msg["content"]
    assert "[Job]" in msg["content"]
    assert "[世界]" in msg["content"]
    # Verify compact format — no raw JSON dump of is_explored
    assert "is_explored" not in msg["content"]
    assert len(msg["content"]) < 5000  # Was ~120K with old format
    print("  PASS: context_to_message")


def test_single_turn_text_response():
    """Agent wakes, LLM returns text only → turn ends."""
    mock = MockProvider(responses=[
        LLMResponse(text="Task understood. Monitoring.", model="mock"),
    ])
    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_turns=5),
    )

    async def run():
        # Run one wake cycle only (init), then stop
        agent._task_completed = True  # Stop after init
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())
    assert len(mock.call_log) == 1
    assert agent._total_llm_calls == 1
    print("  PASS: single_turn_text_response")


def test_llm_reasoning_is_logged():
    """Non-tool LLM text is emitted as structured task_agent reasoning logs."""
    logging_system.clear()
    mock = MockProvider(responses=[
        LLMResponse(text="先观察敌情，再决定是否扩张。", model="mock"),
    ])

    agent = TaskAgent(
        task=make_task(),
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_turns=5),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    logs = logging_system.query(component="task_agent", event="llm_reasoning")
    assert len(logs) >= 1
    assert any("先观察敌情" in record.message for record in logs)
    context_logs = logging_system.query(component="task_agent", event="context_snapshot")
    assert len(context_logs) >= 1
    assert context_logs[-1].data["packet"]["task"]["task_id"] == "t1"
    input_logs = logging_system.query(component="task_agent", event="llm_input")
    assert len(input_logs) >= 1
    assert input_logs[-1].data["messages"][-1]["role"] == "user"
    assert "[任务]" in input_logs[-1].data["messages"][-1]["content"]
    print("  PASS: llm_reasoning_is_logged")


def test_multi_turn_tool_use():
    """Agent wakes, LLM calls tools → continues → text → ends."""
    mock = MockProvider(responses=[
        # Turn 1: LLM calls scout_map (new Expert-as-tool API)
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="scout_map", arguments='{"search_region":"northeast","target_type":"base"}')],
            model="mock",
        ),
        # Turn 2: LLM calls query_world
        LLMResponse(
            tool_calls=[ToolCall(id="tc2", name="query_world", arguments='{"query_type":"my_actors"}')],
            model="mock",
        ),
        # Turn 3: LLM returns text
        LLMResponse(text="Job started, monitoring.", model="mock"),
    ])

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_turns=10),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())
    assert agent._total_llm_calls == 3
    # Conversation should have: context, assistant+tool, tool_result, assistant+tool, tool_result, assistant text
    assert len(agent._conversation) >= 5
    print("  PASS: multi_turn_tool_use")


def test_complete_task_stops_loop():
    """complete_task tool call sets _task_completed flag."""
    mock = MockProvider(responses=[
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="complete_task", arguments='{"result":"succeeded","summary":"Scouted enemy base"}')],
            model="mock",
        ),
    ])

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())
    assert agent._task_completed is True
    assert agent._total_llm_calls == 1
    print("  PASS: complete_task_stops_loop")


def test_max_turns_limit():
    """max_turns prevents infinite tool use loops."""
    # LLM always returns a tool call — should be capped at max_turns
    responses = [
        LLMResponse(
            tool_calls=[ToolCall(id=f"tc{i}", name="query_world", arguments='{"query_type":"my_actors"}')],
            model="mock",
        )
        for i in range(20)
    ]
    mock = MockProvider(responses=responses)

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_turns=3),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())
    assert agent._total_llm_calls == 3  # Capped at max_turns
    print("  PASS: max_turns_limit")


def test_signal_queue_wakes_agent():
    """Signals pushed to queue trigger wake."""
    queue = __import__("task_agent").AgentQueue()

    signal = ExpertSignal(
        task_id="t1", job_id="j1", kind=SignalKind.PROGRESS,
        summary="50% done",
    )
    queue.push(signal)

    items = queue.drain()
    assert len(items) == 1
    assert isinstance(items[0], ExpertSignal)
    assert items[0].summary == "50% done"
    print("  PASS: signal_queue_wakes_agent")


def test_event_queue():
    """Events are buffered and drained correctly."""
    queue = __import__("task_agent").AgentQueue()

    queue.push(Event(type=EventType.ENEMY_DISCOVERED, position=(300, 400)))
    queue.push(Event(type=EventType.UNIT_DIED, actor_id=57))

    items = queue.drain()
    assert len(items) == 2
    assert items[0].type == EventType.ENEMY_DISCOVERED
    assert items[1].type == EventType.UNIT_DIED
    print("  PASS: event_queue")


def test_tool_executor_error_handling():
    """Tool executor handles missing handlers and bad JSON."""
    executor = ToolExecutor()

    async def run():
        # No handler registered
        result = await executor.execute("tc1", "nonexistent_tool", "{}")
        assert result.error is not None
        assert "No handler" in result.error

        # Bad JSON
        executor.register("start_job", mock_tool_handler)
        result = await executor.execute("tc2", "start_job", "not json")
        assert result.error is not None
        assert "Invalid JSON" in result.error

        # Good call
        result = await executor.execute("tc3", "start_job", '{"expert_type":"ReconExpert","config":{}}')
        assert result.error is None
        assert result.result["job_id"] == "j_new_1"

    asyncio.run(run())
    print("  PASS: tool_executor_error_handling")


def test_full_lifecycle_with_signal():
    """Full lifecycle: init → signal arrives → wake → complete."""
    call_count = 0

    mock = MockProvider(responses=[
        # Init wake: start a recon job (new Expert-as-tool API)
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="scout_map", arguments='{"search_region":"northeast","target_type":"base"}')],
            model="mock",
        ),
        LLMResponse(text="Job started, waiting for results.", model="mock"),
        # Signal wake: complete the task
        LLMResponse(
            tool_calls=[ToolCall(id="tc2", name="complete_task", arguments='{"result":"succeeded","summary":"Found enemy base"}')],
            model="mock",
        ),
    ])

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=lambda tid: [make_job()],
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=60.0),  # Long interval — won't trigger
    )

    async def run():
        # Start agent in background
        agent_task = asyncio.create_task(agent.run())

        # Wait for init wake to complete
        await asyncio.sleep(0.1)

        # Push a signal to trigger second wake
        agent.push_signal(ExpertSignal(
            task_id="t1", job_id="j1", kind=SignalKind.TARGET_FOUND,
            summary="Enemy base found at (500, 600)",
            data={"position": [500, 600]},
        ))

        # Wait for completion
        await asyncio.wait_for(agent_task, timeout=5.0)

    asyncio.run(run())
    assert agent._task_completed is True
    assert agent._wake_count == 2  # init + signal
    print("  PASS: full_lifecycle_with_signal")


def test_review_interval_timer():
    """Agent wakes on review_interval timeout even without signals."""
    mock = MockProvider(responses=[
        LLMResponse(text="Init done.", model="mock"),
        LLMResponse(text="Periodic check.", model="mock"),
        # Third wake completes the task
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="complete_task", arguments='{"result":"succeeded","summary":"done"}')],
            model="mock",
        ),
    ])

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1),  # 100ms interval
    )

    async def run():
        await asyncio.wait_for(agent.run(), timeout=5.0)

    asyncio.run(run())
    assert agent._wake_count == 3  # init + timer + timer(complete)
    assert agent._task_completed is True
    print("  PASS: review_interval_timer")


def test_event_in_context_packet():
    """Events routed to agent appear in context packet for LLM."""
    mock = MockProvider(responses=[
        LLMResponse(text="Noted the enemy discovery.", model="mock"),
    ])

    task = make_task()
    captured_conversations = []

    class CaptureMock(MockProvider):
        async def chat(self, messages, **kwargs):
            captured_conversations.append(messages)
            return await super().chat(messages, **kwargs)

    capture_mock = CaptureMock(responses=[
        LLMResponse(text="Noted the enemy discovery.", model="mock"),
    ])

    agent = TaskAgent(
        task=task,
        llm=capture_mock,
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1),
    )

    # Push an event into the queue before wake
    agent.queue.push(Event(type=EventType.ENEMY_DISCOVERED, actor_id=201, position=(1800, 420)))

    async def run():
        await agent._wake_cycle(trigger="event")

    asyncio.run(run())

    # Verify the event appears in the context message sent to LLM
    assert len(captured_conversations) == 1
    messages = captured_conversations[0]
    context_msg = [m for m in messages if m.get("role") == "user" and "[任务]" in m.get("content", "")]
    assert len(context_msg) >= 1
    ctx_content = context_msg[-1]["content"]
    assert "[事件]" in ctx_content
    assert "ENEMY_DISCOVERED" in ctx_content
    print("  PASS: event_in_context_packet")


def test_default_if_timeout_applied():
    """When LLM fails, default_if_timeout calls patch_job handler."""
    applied_defaults = []

    async def tracking_handler(name: str, args: dict) -> dict:
        applied_defaults.append({"name": name, "args": args})
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for tn in get_tool_names():
        executor.register(tn, tracking_handler)

    # MockProvider that always fails (no responses → returns fallback text)
    class FailingProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            raise TimeoutError("LLM timeout simulated")

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=FailingProvider(),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_retries=0, llm_timeout=1.0),
    )

    # Push a decision_request signal
    agent.queue.push(ExpertSignal(
        task_id="t1", job_id="j1", kind=SignalKind.DECISION_REQUEST,
        summary="Scout lost, what to do?",
        decision={
            "options": ["wait", "use_infantry", "abort"],
            "default_if_timeout": "wait",
        },
    ))

    async def run():
        await agent._wake_cycle(trigger="event")

    asyncio.run(run())

    # Verify patch_job was called with the default
    assert len(applied_defaults) == 1
    assert applied_defaults[0]["name"] == "patch_job"
    assert applied_defaults[0]["args"]["job_id"] == "j1"
    assert applied_defaults[0]["args"]["params"]["decision_response"] == "wait"
    print("  PASS: default_if_timeout_applied")


def test_consecutive_failures_auto_terminate():
    """Agent auto-terminates after max_consecutive_failures LLM failures,
    and sends task_warning to player via message_callback."""
    completed_calls = []
    player_warnings = []

    async def tracking_handler(name: str, args: dict) -> dict:
        if name == "complete_task":
            completed_calls.append(args)
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for tn in get_tool_names():
        executor.register(tn, tracking_handler)

    def capture_message(msg):
        player_warnings.append(msg)

    # LLM always fails
    class AlwaysFailProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            raise TimeoutError("LLM always fails")

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=AlwaysFailProvider(),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(
            review_interval=0.05,
            max_retries=0,
            llm_timeout=0.5,
            max_consecutive_failures=3,
        ),
        message_callback=capture_message,
    )

    async def run():
        await asyncio.wait_for(agent.run(), timeout=5.0)

    asyncio.run(run())

    assert agent._task_completed is True
    assert agent._consecutive_failures >= 3
    # complete_task should have been called with result=failed
    assert len(completed_calls) >= 1
    fail_call = completed_calls[-1]
    assert fail_call["result"] == "failed"
    assert "连续失败" in fail_call["summary"]
    # Player warning should have been sent via message_callback
    warning_msgs = [w for w in player_warnings if w.type.value == "task_warning"]
    assert len(warning_msgs) >= 1
    assert "连续失败" in warning_msgs[0].content
    print("  PASS: consecutive_failures_auto_terminate")


def test_failure_counter_resets_on_success():
    """Consecutive failure counter resets when LLM succeeds."""
    mock = MockProvider(responses=[
        # Wake 1: fail (no responses left → fallback text)
        LLMResponse(text="recovered", model="mock"),
    ])

    # First make a provider that fails once then succeeds
    call_count = 0

    class FailOnceProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("temporary failure")
            return LLMResponse(text="recovered", model="mock")

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=FailOnceProvider(),
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.05, max_retries=0, max_consecutive_failures=3),
    )

    async def run():
        # Wake 1: LLM fails → consecutive_failures = 1
        await agent._wake_cycle(trigger="init")
        assert agent._consecutive_failures == 1

        # Wake 2: LLM succeeds → consecutive_failures resets to 0
        await agent._wake_cycle(trigger="timer")
        assert agent._consecutive_failures == 0

    asyncio.run(run())
    print("  PASS: failure_counter_resets_on_success")


def test_single_agent_error_isolation():
    """Exception outside _call_llm (in world_summary_provider) is caught
    by _safe_wake_cycle, doesn't crash the agent permanently."""
    provider_call_count = 0

    def flaky_world_provider():
        nonlocal provider_call_count
        provider_call_count += 1
        if provider_call_count == 1:
            raise RuntimeError("world_summary_provider crashed")
        return make_world()

    llm_call_count = 0

    class CountingProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            nonlocal llm_call_count
            llm_call_count += 1
            if llm_call_count >= 2:
                return LLMResponse(
                    tool_calls=[ToolCall(id="tc1", name="complete_task",
                                        arguments='{"result":"succeeded","summary":"done"}')],
                    model="mock",
                )
            return LLMResponse(text="ok", model="mock")

    task = make_task()
    agent = TaskAgent(
        task=task,
        llm=CountingProvider(),
        tool_executor=make_executor(),
        jobs_provider=noop_jobs_provider,
        world_summary_provider=flaky_world_provider,
        config=AgentConfig(review_interval=0.05, max_retries=0, max_consecutive_failures=5),
    )

    async def run():
        await asyncio.wait_for(agent.run(), timeout=5.0)

    asyncio.run(run())

    # Agent should have recovered: first wake crashes in provider,
    # _safe_wake_cycle catches it, next wake succeeds and completes
    assert agent._task_completed is True
    assert agent._wake_count >= 2
    assert provider_call_count >= 2  # Called at least twice (crash + recovery)
    print("  PASS: single_agent_error_isolation")


def test_managed_task_does_not_self_bootstrap_structure_build() -> None:
    provider = MockProvider([LLMResponse(text="monitoring")])
    captured: list[dict] = []

    async def start_job_handler(_name: str, args: dict) -> dict:
        captured.append(args)
        return {"job_id": "j_proc", "status": "running"}

    async def noop_handler(_name: str, _args: dict) -> dict:
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, noop_handler)
    executor.register("start_job", start_job_handler)

    agent = TaskAgent(
        task=make_task(raw_text="建造矿场"),
        llm=provider,
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(max_turns=1),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert captured == []
    assert agent._total_llm_calls == 1
    print("  PASS: managed_task_does_not_self_bootstrap_structure_build")


def test_capability_structure_build_completes_with_llm_running() -> None:
    """Bootstrap pre-creates job AND LLM runs; completion via job-status finalize path."""
    captured_start_jobs: list[dict] = []
    captured_completions: list[dict] = []
    bootstrap_jobs: list[Job] = []

    async def start_job_handler(_name: str, args: dict) -> dict:
        captured_start_jobs.append(args)
        return {"job_id": "j_bootstrap", "status": "running"}

    async def complete_task_handler(_name: str, args: dict) -> dict:
        captured_completions.append(args)
        return {"ok": True}

    async def noop_handler(_name: str, _args: dict) -> dict:
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, noop_handler)
    executor.register("start_job", start_job_handler)
    executor.register("complete_task", complete_task_handler)

    def dynamic_jobs_provider(task_id: str) -> list[Job]:
        return list(bootstrap_jobs)

    task = make_task(raw_text="建造兵营")
    task.is_capability = True
    agent = TaskAgent(
        task=task,
        llm=MockProvider([LLMResponse(text="正在监控兵营建造", model="mock")]),
        tool_executor=executor,
        jobs_provider=dynamic_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        # Wake 1: bootstrap pre-creates job, LLM also runs
        await agent._wake_cycle(trigger="init")
        # Job completes: update jobs registry to SUCCEEDED and push signal
        bootstrap_jobs.append(Job(
            job_id="j_bootstrap", task_id="t1", expert_type="EconomyExpert",
            config=EconomyJobConfig(unit_type="barr", count=1, queue_type="Building"),
            status=JobStatus.SUCCEEDED,
        ))
        agent.push_signal(
            ExpertSignal(
                task_id="t1",
                job_id="j_bootstrap",
                kind=SignalKind.TASK_COMPLETE,
                summary="生产完成 1/1: barr",
                result="succeeded",
            )
        )
        # Wake 2: _maybe_finalize_bootstrap_task checks job status → completes
        await agent._wake_cycle(trigger="event")

    asyncio.run(run())

    assert captured_start_jobs == [
        {
            "expert_type": "EconomyExpert",
            "config": {
                "unit_type": "barr",
                "count": 1,
                "queue_type": "Building",
                "repeat": False,
            },
        }
    ]
    assert len(captured_completions) == 1
    assert captured_completions[0]["result"] == "succeeded"
    assert "建造兵营" in captured_completions[0]["summary"]
    assert agent._task_completed is True
    # LLM runs on wake 1 (bootstrap no longer blocks LLM)
    assert agent._total_llm_calls >= 1
    print("  PASS: capability_structure_build_completes_with_llm_running")


def test_bootstrap_finalizes_on_job_status_without_signal() -> None:
    """BUG-A: bootstrap finalizes by job status even when terminal signal was already consumed.

    Scenario: wake 1 has a TASK_COMPLETE signal but LLM returns text (not complete_task).
    On wake 2 (no signal), _maybe_finalize_bootstrap_task must still finalize by
    checking bootstrap job status directly — not waiting for another signal.
    """
    captured_completions: list[dict] = []

    async def complete_task_handler(_name: str, args: dict) -> dict:
        captured_completions.append(args)
        return {"ok": True}

    async def noop_handler(_name: str, _args: dict) -> dict:
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, noop_handler)
    executor.register("complete_task", complete_task_handler)

    # Job is already SUCCEEDED in the provider (simulates state after job terminal)
    succeeded_job = Job(
        job_id="j_bootstrap", task_id="t1", expert_type="EconomyExpert",
        config=EconomyJobConfig(unit_type="barr", count=1, queue_type="Building"),
        status=JobStatus.SUCCEEDED,
    )

    agent = TaskAgent(
        task=make_task(raw_text="建造兵营"),
        llm=MockProvider([]),
        tool_executor=executor,
        jobs_provider=lambda task_id: [succeeded_job],
        world_summary_provider=noop_world_provider,
    )
    # Simulate: bootstrap was set up in a prior wake
    agent._bootstrap_job_id = "j_bootstrap"
    agent._bootstrap_raw_text = "建造兵营"

    async def run():
        # Wake with NO signal — signal was already consumed by a prior wake
        await agent._wake_cycle(trigger="review")

    asyncio.run(run())

    assert len(captured_completions) == 1, "Should finalize without a signal"
    assert captured_completions[0]["result"] == "succeeded"
    assert "建造兵营" in captured_completions[0]["summary"]
    assert agent._task_completed is True
    # LLM was NOT called — finalized before reaching LLM
    assert agent._total_llm_calls == 0
    print("  PASS: bootstrap_finalizes_on_job_status_without_signal")


def test_managed_task_does_not_self_bootstrap_simple_production() -> None:
    provider = MockProvider([LLMResponse(text="monitoring")])
    captured: list[dict] = []

    async def start_job_handler(_name: str, args: dict) -> dict:
        captured.append(args)
        return {"job_id": "j_e1", "status": "running"}

    async def noop_handler(_name: str, _args: dict) -> dict:
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, noop_handler)
    executor.register("start_job", start_job_handler)

    agent = TaskAgent(
        task=make_task(raw_text="生产3个步兵"),
        llm=provider,
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(max_turns=1),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert captured == []
    assert agent._total_llm_calls == 1
    print("  PASS: managed_task_does_not_self_bootstrap_simple_production")


def test_capability_simple_production_completes_with_llm_running() -> None:
    """Bootstrap pre-creates production job AND LLM runs; completion via finalize path."""
    captured_start_jobs: list[dict] = []
    captured_completions: list[dict] = []

    async def start_job_handler(_name: str, args: dict) -> dict:
        captured_start_jobs.append(args)
        return {"job_id": "j_bootstrap_prod", "status": "running"}

    async def complete_task_handler(_name: str, args: dict) -> dict:
        captured_completions.append(args)
        return {"ok": True}

    async def noop_handler(_name: str, _args: dict) -> dict:
        return {"ok": True}

    executor = ToolExecutor()
    from task_agent.tools import get_tool_names
    for name in get_tool_names():
        executor.register(name, noop_handler)
    executor.register("start_job", start_job_handler)
    executor.register("complete_task", complete_task_handler)

    # Stateful jobs provider: returns running job initially, then succeeded after signal
    from models import JobStatus
    job_status = [JobStatus.RUNNING]

    def stateful_jobs_provider(task_id):
        return [Job(
            job_id="j_bootstrap_prod",
            task_id=task_id,
            expert_type="EconomyExpert",
            config=None,
            status=job_status[0],
        )]

    task = make_task(raw_text="生产3个步兵")
    task.is_capability = True
    agent = TaskAgent(
        task=task,
        llm=MockProvider([LLMResponse(text="正在监控步兵生产", model="mock")]),
        tool_executor=executor,
        jobs_provider=stateful_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        # Wake 1: bootstrap pre-creates production job, LLM also runs
        await agent._wake_cycle(trigger="init")
        # Simulate job completing
        job_status[0] = JobStatus.SUCCEEDED
        agent.push_signal(
            ExpertSignal(
                task_id="t1",
                job_id="j_bootstrap_prod",
                kind=SignalKind.TASK_COMPLETE,
                summary="生产完成 3/3: e1",
                result="succeeded",
            )
        )
        # Wake 2: _maybe_finalize_bootstrap_task handles completion via job status check
        await agent._wake_cycle(trigger="event")

    asyncio.run(run())

    # Bootstrap finalization via job status check should have called complete_task
    assert len(captured_completions) == 1
    assert captured_completions[0]["result"] == "succeeded"
    assert "生产3个步兵" in captured_completions[0]["summary"]
    assert agent._task_completed is True
    # LLM runs on wake 1 (bootstrap no longer blocks LLM)
    assert agent._total_llm_calls >= 1
    print("  PASS: capability_simple_production_completes_with_llm_running")


def test_existing_rule_routed_recon_job_attaches_and_llm_runs() -> None:
    """Rule-routed recon job is attached to bootstrap tracker AND LLM runs."""
    task = make_task(raw_text="探索地图")
    job = make_job(job_id="j_rule_recon", task_id=task.task_id)

    agent = TaskAgent(
        task=task,
        llm=MockProvider([LLMResponse(text="正在监控侦察任务", model="mock")]),
        tool_executor=make_executor(),
        jobs_provider=lambda _tid: [job],
        world_summary_provider=noop_world_provider,
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    # Job is still attached for finalization tracking
    assert agent._bootstrap_job_id == "j_rule_recon"
    # LLM runs so it can handle signals, fix compound commands, etc.
    assert agent._total_llm_calls >= 1
    print("  PASS: existing_rule_routed_recon_job_attaches_and_llm_runs")


def test_bootstrap_job_decision_request_reaches_llm() -> None:
    """DECISION_REQUEST signal must reach LLM even when bootstrap job is active."""
    decision_in_context = []

    class CapturingProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            # Record whether a decision_request appeared in context
            for msg in messages:
                content = msg.get("content", "")
                if "决策请求" in content or "DECISION_REQUEST" in content or "decision_request" in content:
                    decision_in_context.append(content)
            return await super().chat(messages, **kwargs)

    task = make_task(raw_text="探索地图")
    job = make_job(job_id="j_rule_recon", task_id=task.task_id)

    agent = TaskAgent(
        task=task,
        llm=CapturingProvider([
            LLMResponse(text="正在处理侦察任务", model="mock"),  # wake 1: attach + LLM
            LLMResponse(text="侦察队遇险，继续等待", model="mock"),  # wake 2: decision
        ]),
        tool_executor=make_executor(),
        jobs_provider=lambda _tid: [job],
        world_summary_provider=noop_world_provider,
    )

    async def run():
        # Wake 1: attach bootstrap job, LLM runs
        await agent._wake_cycle(trigger="init")

        # Push a DECISION_REQUEST from the running recon job
        agent.push_signal(ExpertSignal(
            task_id="t1",
            job_id="j_rule_recon",
            kind=SignalKind.DECISION_REQUEST,
            summary="侦察队失联，是否等待？",
            decision={
                "options": ["wait", "abort"],
                "default_if_timeout": "wait",
            },
        ))
        # Wake 2: DECISION_REQUEST must reach LLM
        await agent._wake_cycle(trigger="signal")

    asyncio.run(run())

    assert agent._total_llm_calls >= 2
    # The second wake's context must have contained the decision_request
    assert len(decision_in_context) >= 1
    print("  PASS: bootstrap_job_decision_request_reaches_llm")


def test_system_prompt_pins_structure_build_commands_to_economy() -> None:
    assert 'proc=' in SYSTEM_PROMPT  # unit type mapping
    assert 'Building' in SYSTEM_PROMPT
    assert 'powr=' in SYSTEM_PROMPT
    assert 'barr=' in SYSTEM_PROMPT
    print("  PASS: system_prompt_pins_structure_build_commands_to_economy")


def test_normal_context_redacts_capability_planning_hints() -> None:
    """Ordinary managed tasks should not see capability planning hints in context."""
    runtime_facts = {
        "has_construction_yard": True,
        "power_plant_count": 1,
        "buildable": {"Building": ["powr", "weap"], "Infantry": ["e1"]},
        "feasibility": {"deploy_mcv": True, "produce_units": True},
        "production_queues": {
            "Building": [{"unit_type": "powr", "count": 1, "source": "Kernel fast-path"}],
        },
        "unfulfilled_requests": [
            {
                "request_id": "r1",
                "task_label": "003",
                "category": "vehicle",
                "count": 2,
                "fulfilled": 0,
                "hint": "3tnk",
                "reason": "无车厂",
            }
        ],
        "can_afford_power_plant": True,
    }
    packet = build_context_packet(
        task=make_task(),
        jobs=[make_job()],
        world_summary=make_world(),
        runtime_facts=runtime_facts,
    )
    msg = context_to_message(packet, is_capability=False)
    assert "[可造]" not in msg["content"]
    assert "[生产队列]" not in msg["content"]
    assert "[待处理请求]" not in msg["content"]
    assert "buildable" not in msg["content"]
    assert "production_queues" not in msg["content"]
    assert "unfulfilled_requests" not in msg["content"]

    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    rf_out = header["context_packet"]["runtime_facts"]
    assert "buildable" not in rf_out
    assert "production_queues" not in rf_out
    assert "unfulfilled_requests" not in rf_out
    assert "feasibility" not in rf_out
    assert not any(k.startswith("can_afford_") for k in rf_out)
    print("  PASS: normal_context_redacts_capability_planning_hints")


def test_capability_context_exposes_phase_and_blocker_blocks() -> None:
    """Capability context should make phase and blockers explicit."""
    packet = build_context_packet(
        task=make_task(),
        jobs=[],
        world_summary=make_world(),
        recent_signals=[
            ExpertSignal(
                task_id="t1",
                job_id="j1",
                kind=SignalKind.PROGRESS,
                summary="进入摆放阶段",
                expert_state={"phase": "producing"},
            ),
            ExpertSignal(
                task_id="t1",
                job_id="j1",
                kind=SignalKind.BLOCKED,
                summary="缺少兵营",
            ),
        ],
        runtime_facts={
            "this_task_jobs": [
                {"job_id": "j1", "expert_type": "EconomyExpert", "status": "running", "phase": "placing"},
            ],
            "unfulfilled_requests": [
                {
                    "request_id": "r1",
                    "task_label": "003",
                    "category": "infantry",
                    "count": 2,
                    "fulfilled": 0,
                    "hint": "e1",
                    "reason": "无兵营",
                }
            ],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[阶段]" in msg["content"]
    assert "placing" in msg["content"] or "producing" in msg["content"]
    assert "[阻塞]" in msg["content"]
    assert "REQ-r1" in msg["content"]
    assert "无兵营" in msg["content"]
    assert "缺少兵营" in msg["content"]
    assert "阶段受限" in CAPABILITY_SYSTEM_PROMPT
    assert "阻塞" in CAPABILITY_SYSTEM_PROMPT
    assert "[阶段]" in CAPABILITY_SYSTEM_PROMPT
    print("  PASS: capability_context_exposes_phase_and_blocker_blocks")


# --- Expert-as-tool handler tests ---

def _make_handlers_executor(captured_jobs: list) -> ToolExecutor:
    """Build a ToolExecutor backed by TaskToolHandlers with a tracking kernel."""
    from task_agent.handlers import TaskToolHandlers

    class TrackingKernel:
        def start_job(self, task_id, expert_type, config):
            from models import Job, JobStatus
            from models.configs import ReconJobConfig
            captured_jobs.append({"expert_type": expert_type, "config": config, "task_id": task_id})
            return Job(
                job_id=f"j_{expert_type.lower()}",
                task_id=task_id,
                expert_type=expert_type,
                config=config or ReconJobConfig("northeast", "base", "enemy"),
            )
        def complete_task(self, *a, **kw): return True
        def patch_job(self, *a, **kw): return True
        def pause_job(self, *a, **kw): return True
        def resume_job(self, *a, **kw): return True
        def abort_job(self, *a, **kw): return True
        def cancel_tasks(self, *a, **kw): return 0
        def register_task_message(self, *a, **kw): return True
        def jobs_for_task(self, task_id): return []

    class StubWorldModel:
        def query(self, *a, **kw): return {}
        def set_constraint(self, *a, **kw): pass
        def remove_constraint(self, *a, **kw): pass

    from models import Task
    from models.enums import TaskKind
    stub_task = Task(task_id="t_test", raw_text="test", kind=TaskKind.MANAGED, priority=50)
    executor = ToolExecutor()
    handlers = TaskToolHandlers(stub_task, TrackingKernel(), StubWorldModel())
    handlers.register_all(executor)
    return executor


def test_scout_map_handler_creates_recon_job() -> None:
    """scout_map tool creates a ReconExpert job with correct config."""
    captured = []
    executor = _make_handlers_executor(captured)

    async def run():
        from models.configs import ReconJobConfig
        result = await executor.execute("tc1", "scout_map",
            '{"search_region": "enemy_half", "target_type": "base", "target_owner": "enemy", "avoid_combat": false}')
        assert result.error is None
        assert result.result["job_id"] == "j_reconexpert"
        assert len(captured) == 1
        assert captured[0]["expert_type"] == "ReconExpert"
        cfg = captured[0]["config"]
        assert isinstance(cfg, ReconJobConfig)
        assert cfg.search_region == "enemy_half"
        assert cfg.avoid_combat is False

    asyncio.run(run())
    print("  PASS: scout_map_handler_creates_recon_job")


def test_produce_units_handler_creates_economy_job() -> None:
    """produce_units tool creates an EconomyExpert job with correct config."""
    captured = []
    executor = _make_handlers_executor(captured)

    async def run():
        from models.configs import EconomyJobConfig
        result = await executor.execute("tc1", "produce_units",
            '{"unit_type": "e1", "count": 5, "queue_type": "Infantry"}')
        assert result.error is None
        assert len(captured) == 1
        assert captured[0]["expert_type"] == "EconomyExpert"
        cfg = captured[0]["config"]
        assert isinstance(cfg, EconomyJobConfig)
        assert cfg.unit_type == "e1"
        assert cfg.count == 5
        assert cfg.queue_type == "Infantry"
        assert cfg.repeat is False

    asyncio.run(run())
    print("  PASS: produce_units_handler_creates_economy_job")


def test_attack_handler_creates_combat_job() -> None:
    """attack tool creates a CombatExpert job with correct config."""
    captured = []
    executor = _make_handlers_executor(captured)

    async def run():
        from models.configs import CombatJobConfig
        from models.enums import EngagementMode
        result = await executor.execute("tc1", "attack",
            '{"target_position": [1200, 800], "engagement_mode": "harass", "max_chase_distance": 10}')
        assert result.error is None
        assert len(captured) == 1
        assert captured[0]["expert_type"] == "CombatExpert"
        cfg = captured[0]["config"]
        assert isinstance(cfg, CombatJobConfig)
        assert cfg.target_position == (1200, 800)
        assert cfg.engagement_mode == EngagementMode.HARASS
        assert cfg.max_chase_distance == 10

    asyncio.run(run())
    print("  PASS: attack_handler_creates_combat_job")


def test_move_units_handler_creates_movement_job() -> None:
    """move_units tool creates a MovementExpert job with correct config."""
    captured = []
    executor = _make_handlers_executor(captured)

    async def run():
        from models.configs import MovementJobConfig
        from models.enums import MoveMode
        result = await executor.execute("tc1", "move_units",
            '{"target_position": [500, 300], "move_mode": "retreat", "arrival_radius": 8}')
        assert result.error is None
        assert len(captured) == 1
        assert captured[0]["expert_type"] == "MovementExpert"
        cfg = captured[0]["config"]
        assert isinstance(cfg, MovementJobConfig)
        assert cfg.target_position == (500, 300)
        assert cfg.move_mode == MoveMode.RETREAT
        assert cfg.arrival_radius == 8

    asyncio.run(run())
    print("  PASS: move_units_handler_creates_movement_job")


def test_deploy_mcv_handler_creates_deploy_job() -> None:
    """deploy_mcv tool creates a DeployExpert job with correct config."""
    captured = []
    executor = _make_handlers_executor(captured)

    async def run():
        from models.configs import DeployJobConfig
        result = await executor.execute("tc1", "deploy_mcv",
            '{"actor_id": 42, "target_position": [100, 200]}')
        assert result.error is None
        assert len(captured) == 1
        assert captured[0]["expert_type"] == "DeployExpert"
        cfg = captured[0]["config"]
        assert isinstance(cfg, DeployJobConfig)
        assert cfg.actor_id == 42
        assert cfg.target_position == (100, 200)

    asyncio.run(run())
    print("  PASS: deploy_mcv_handler_creates_deploy_job")


def test_start_job_removed_from_tool_definitions() -> None:
    """start_job must NOT appear in TOOL_DEFINITIONS (LLM sees only Expert tools)."""
    from task_agent.tools import TOOL_DEFINITIONS, get_tool_names
    names = get_tool_names()
    assert "start_job" not in names, "start_job should not be exposed to LLM"
    assert "scout_map" in names
    assert "produce_units" in names
    assert "attack" in names
    assert "move_units" in names
    assert "deploy_mcv" in names
    print("  PASS: start_job_removed_from_tool_definitions")


def test_execute_tools_parallel() -> None:
    """_execute_tools runs all tool calls concurrently, not serially."""
    import time as _time

    call_order: list[str] = []
    start_times: dict[str, float] = {}

    async def slow_handler(name: str, args: dict) -> dict:
        start_times[name] = _time.monotonic()
        await asyncio.sleep(0.05)  # 50 ms simulated I/O
        call_order.append(name)
        return {"job_id": f"j_{name}", "status": "running", "timestamp": _time.time()}

    executor = ToolExecutor()
    executor.register("scout_map", slow_handler)
    executor.register("produce_units", slow_handler)

    agent = TaskAgent(
        task=make_task(),
        llm=MockProvider([LLMResponse(text="done", model="mock")]),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        response = LLMResponse(
            tool_calls=[
                ToolCall(id="tc1", name="scout_map",
                         arguments='{"search_region":"enemy_half","target_type":"base"}'),
                ToolCall(id="tc2", name="produce_units",
                         arguments='{"unit_type":"e1","count":3,"queue_type":"Infantry"}'),
            ],
            model="mock",
        )
        t0 = _time.monotonic()
        results = await agent._execute_tools(response)
        elapsed = _time.monotonic() - t0

        assert len(results) == 2
        assert all(r.error is None for r in results), f"unexpected errors: {[r.error for r in results]}"
        # Both tools started before either finished (parallel, not serial)
        gap = abs(start_times["scout_map"] - start_times["produce_units"])
        assert gap < 0.03, f"tools started {gap:.3f}s apart — likely serial, not parallel"
        # Total elapsed should be ~50ms, not ~100ms (serial would take ≥100ms)
        assert elapsed < 0.09, f"elapsed {elapsed:.3f}s is too slow for parallel execution"

    asyncio.run(run())
    print("  PASS: execute_tools_parallel")


def test_execute_tools_exception_isolation() -> None:
    """An exception in one tool must not cancel or corrupt sibling tool results."""

    async def failing_handler(name: str, args: dict) -> dict:
        raise RuntimeError("tool crashed!")

    async def ok_handler(name: str, args: dict) -> dict:
        return {"job_id": "j_ok", "status": "running", "timestamp": 0.0}

    executor = ToolExecutor()
    executor.register("scout_map", failing_handler)
    executor.register("produce_units", ok_handler)

    agent = TaskAgent(
        task=make_task(),
        llm=MockProvider([LLMResponse(text="done", model="mock")]),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        response = LLMResponse(
            tool_calls=[
                ToolCall(id="tc1", name="scout_map",
                         arguments='{"search_region":"enemy_half","target_type":"base"}'),
                ToolCall(id="tc2", name="produce_units",
                         arguments='{"unit_type":"e1","count":2,"queue_type":"Infantry"}'),
            ],
            model="mock",
        )
        results = await agent._execute_tools(response)

        assert len(results) == 2
        # First tool failed
        assert results[0].error is not None
        assert "tool crashed" in results[0].error
        # Second tool succeeded despite first failing
        assert results[1].error is None
        assert results[1].result["job_id"] == "j_ok"

    asyncio.run(run())
    print("  PASS: execute_tools_exception_isolation")


# --- Mid-wake context refresh test ---

def test_multi_turn_context_refresh() -> None:
    """After each tool call turn, a fresh context is injected so next LLM turn sees updated state."""
    import json as _json

    # jobs_provider returns a new job after the first call (simulating start_job effect)
    job_calls: list[int] = [0]
    new_job = make_job(job_id="j_new", task_id="t1")

    def dynamic_jobs_provider(task_id: str) -> list:
        job_calls[0] += 1
        # First call (initial context build) returns empty; subsequent calls return the new job
        if job_calls[0] <= 1:
            return []
        return [new_job]

    mock = MockProvider(responses=[
        # Turn 1: tool call
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="scout_map",
                                 arguments='{"search_region":"northeast","target_type":"base"}')],
            model="mock",
        ),
        # Turn 2: text only (ends loop)
        LLMResponse(text="Scouting started.", model="mock"),
    ])

    agent = TaskAgent(
        task=make_task(),
        llm=mock,
        tool_executor=make_executor(),
        jobs_provider=dynamic_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.1, max_turns=5),
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert mock._call_count == 2, f"Expected 2 LLM calls, got {mock._call_count}"

    # Turn 2 messages should contain a fresh context after the tool results
    turn2_messages = mock.call_log[1]["messages"]
    # Last user message before the final assistant turn should be a CONTEXT UPDATE
    ctx_msgs = [m for m in turn2_messages if m.get("role") == "user" and "[任务]" in m.get("content", "")]
    assert len(ctx_msgs) >= 2, f"Expected at least 2 context messages (initial + refresh), got {len(ctx_msgs)}"

    # The last context message should contain the new job (j_new)
    last_ctx = ctx_msgs[-1]
    assert "j_new" in last_ctx["content"], "Fresh context after tool call must include newly created job"

    print("  PASS: multi_turn_context_refresh")


def test_complete_task_warns_when_no_jobs_succeeded() -> None:
    """complete_task handler adds job_status_warning when no jobs reached succeeded."""
    from models import Job, JobStatus, Task
    from models.enums import TaskKind
    from models.configs import ReconJobConfig
    from task_agent.handlers import TaskToolHandlers
    from task_agent.tools import ToolExecutor

    task = Task(task_id="t_warn", raw_text="test", kind=TaskKind.MANAGED, priority=50)

    waiting_job = Job(
        job_id="j_waiting",
        task_id="t_warn",
        expert_type="EconomyExpert",
        config=ReconJobConfig("northeast", "base", "enemy"),
        status=JobStatus.WAITING,
    )

    class StubKernel:
        def complete_task(self, *a, **kw): return True
        def start_job(self, *a, **kw): ...
        def patch_job(self, *a, **kw): return True
        def pause_job(self, *a, **kw): return True
        def resume_job(self, *a, **kw): return True
        def abort_job(self, *a, **kw): return True
        def cancel_tasks(self, *a, **kw): return 0
        def register_task_message(self, *a, **kw): return True
        def jobs_for_task(self, task_id): return [waiting_job]

    class StubWM:
        def query(self, *a, **kw): return {}
        def set_constraint(self, *a, **kw): pass
        def remove_constraint(self, *a, **kw): pass

    executor = ToolExecutor()
    handlers = TaskToolHandlers(task, StubKernel(), StubWM())
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1", "complete_task", '{"result":"succeeded","summary":"done"}'
        )
        assert result.error is None
        assert result.result["ok"] is True
        assert "job_status_warning" in result.result, "should warn when job is still waiting"
        assert "j_waiting" in result.result["job_status_warning"]
        assert "waiting" in result.result["job_status_warning"]

        # No warning when job has succeeded
        waiting_job.status = JobStatus.SUCCEEDED
        result2 = await executor.execute(
            "tc2", "complete_task", '{"result":"succeeded","summary":"done"}'
        )
        assert "job_status_warning" not in result2.result

    asyncio.run(run())
    print("  PASS: complete_task_warns_when_no_jobs_succeeded")


def test_context_packet_includes_job_status_zh() -> None:
    """build_context_packet includes status_zh Chinese label on each job."""
    from models import Job, JobStatus
    from models.configs import ReconJobConfig
    from task_agent.context import build_context_packet

    job = Job(
        job_id="j1", task_id="t1", expert_type="EconomyExpert",
        config=ReconJobConfig("northeast", "base", "enemy"),
        status=JobStatus.WAITING,
    )
    packet = build_context_packet(make_task(), [job])
    assert packet.jobs[0]["status"] == "waiting"
    assert packet.jobs[0]["status_zh"] == "等待中（尚未生效）"

    job.status = JobStatus.SUCCEEDED
    packet2 = build_context_packet(make_task(), [job])
    assert packet2.jobs[0]["status_zh"] == "已成功完成"

    job.status = JobStatus.ABORTED
    packet3 = build_context_packet(make_task(), [job])
    assert packet3.jobs[0]["status_zh"] == "已中止（未完成目标）"
    print("  PASS: context_packet_includes_job_status_zh")


def test_system_prompt_has_completion_judgment_rules() -> None:
    """SYSTEM_PROMPT includes guidance to base complete_task on Job status."""
    assert "complete_task" in SYSTEM_PROMPT
    assert "Job" in SYSTEM_PROMPT
    assert "succeeded" in SYSTEM_PROMPT
    assert "partial" in SYSTEM_PROMPT
    # Key rule: don't rely on world observation alone
    assert "world" in SYSTEM_PROMPT.lower() or "另一个" in SYSTEM_PROMPT or "other" in SYSTEM_PROMPT.lower()
    print("  PASS: system_prompt_has_completion_judgment_rules")


# --- Subscription tests ---

def test_subscription_filters_info_experts_in_context() -> None:
    """build_context_packet only includes info_experts keys matching task.info_subscriptions."""
    from models import Task
    from models.enums import TaskKind
    from task_agent.context import build_context_packet

    all_ie = {
        "threat_level": "high",
        "threat_direction": "northeast",
        "enemy_count": 5,
        "enemy_composition_summary": "tanks",
        "base_under_attack": False,
        "base_established": True,
        "base_health_summary": "established",
        "has_production": True,
    }
    runtime_facts = {"credits": 500, "info_experts": all_ie}

    # No subscriptions → info_experts is empty
    task_none = Task(task_id="t1", raw_text="t", kind=TaskKind.MANAGED, priority=50, info_subscriptions=[])
    pkt = build_context_packet(task_none, [], runtime_facts=runtime_facts)
    assert pkt.runtime_facts.get("info_experts") == {}

    # threat subscription → only threat keys
    task_threat = Task(task_id="t2", raw_text="t", kind=TaskKind.MANAGED, priority=50, info_subscriptions=["threat"])
    pkt = build_context_packet(task_threat, [], runtime_facts=runtime_facts)
    ie = pkt.runtime_facts["info_experts"]
    assert "threat_level" in ie
    assert "enemy_count" in ie
    assert "base_established" not in ie

    # base_state subscription → only base_state keys
    task_base = Task(task_id="t3", raw_text="t", kind=TaskKind.MANAGED, priority=50, info_subscriptions=["base_state"])
    pkt = build_context_packet(task_base, [], runtime_facts=runtime_facts)
    ie = pkt.runtime_facts["info_experts"]
    assert "base_established" in ie
    assert "base_health_summary" in ie
    assert "threat_level" not in ie

    # both subscriptions → all keys
    task_both = Task(task_id="t4", raw_text="t", kind=TaskKind.MANAGED, priority=50, info_subscriptions=["threat", "base_state"])
    pkt = build_context_packet(task_both, [], runtime_facts=runtime_facts)
    ie = pkt.runtime_facts["info_experts"]
    assert "threat_level" in ie
    assert "base_established" in ie

    print("  PASS: subscription_filters_info_experts_in_context")


def test_update_subscriptions_handler() -> None:
    """update_subscriptions tool modifies task.info_subscriptions in-place."""
    from models import Task
    from models.enums import TaskKind
    from task_agent.handlers import TaskToolHandlers
    from task_agent.tools import ToolExecutor

    task = Task(task_id="t_sub", raw_text="test", kind=TaskKind.MANAGED, priority=50,
                info_subscriptions=["threat"])

    class StubKernel:
        def start_job(self, *a, **kw): ...
        def complete_task(self, *a, **kw): return True
        def patch_job(self, *a, **kw): return True
        def pause_job(self, *a, **kw): return True
        def resume_job(self, *a, **kw): return True
        def abort_job(self, *a, **kw): return True
        def cancel_tasks(self, *a, **kw): return 0
        def register_task_message(self, *a, **kw): return True
        def jobs_for_task(self, task_id): return []

    class StubWM:
        def query(self, *a, **kw): return {}
        def set_constraint(self, *a, **kw): pass
        def remove_constraint(self, *a, **kw): pass

    executor = ToolExecutor()
    handlers = TaskToolHandlers(task, StubKernel(), StubWM())
    handlers.register_all(executor)

    async def run():
        # Add base_state
        r1 = await executor.execute("tc1", "update_subscriptions", '{"add": ["base_state"]}')
        assert r1.error is None
        assert "base_state" in r1.result["subscriptions"]
        assert "threat" in r1.result["subscriptions"]
        assert sorted(task.info_subscriptions) == ["base_state", "threat"]

        # Remove threat
        r2 = await executor.execute("tc2", "update_subscriptions", '{"remove": ["threat"]}')
        assert r2.error is None
        assert task.info_subscriptions == ["base_state"]

        # Invalid keys are silently dropped
        r3 = await executor.execute("tc3", "update_subscriptions", '{"add": ["invalid_key"]}')
        assert r3.error is None
        assert task.info_subscriptions == ["base_state"]

    asyncio.run(run())
    print("  PASS: update_subscriptions_handler")


def test_update_subscriptions_in_tool_definitions() -> None:
    """update_subscriptions must appear in TOOL_DEFINITIONS."""
    from task_agent.tools import get_tool_names
    assert "update_subscriptions" in get_tool_names()
    print("  PASS: update_subscriptions_in_tool_definitions")


def test_adjutant_sets_subscriptions_from_expert_type() -> None:
    """Adjutant _start_direct_job sets info_subscriptions based on expert_type."""
    from adjutant.adjutant import _EXPERT_SUBSCRIPTIONS

    assert _EXPERT_SUBSCRIPTIONS["CombatExpert"] == ["threat"]
    assert _EXPERT_SUBSCRIPTIONS["ReconExpert"] == ["threat"]
    assert _EXPERT_SUBSCRIPTIONS["MovementExpert"] == ["threat"]
    assert set(_EXPERT_SUBSCRIPTIONS["EconomyExpert"]) == {"base_state", "production"}
    assert _EXPERT_SUBSCRIPTIONS["DeployExpert"] == ["base_state"]
    print("  PASS: adjutant_sets_subscriptions_from_expert_type")


# --- Conversation compression tests ---

def _make_ctx_msg(n: int) -> dict:
    return {"role": "user", "content": f"{_CONTEXT_MARKER}\n{{\"cycle\": {n}}}"}


def _make_assistant_msg(n: int) -> dict:
    return {"role": "assistant", "content": f"response {n}"}


def _make_turn(n: int) -> list[dict]:
    return [_make_ctx_msg(n), _make_assistant_msg(n)]


def test_trim_conversation_keeps_last_n_turns() -> None:
    """_trim_conversation drops turns older than max_turns."""
    conv = []
    for i in range(10):
        conv.extend(_make_turn(i))

    trimmed = _trim_conversation(conv, max_turns=3)

    # Should start at context message for turn 7 (last 3 of 0..9)
    assert trimmed[0]["role"] == "user"
    assert '"cycle": 7' in trimmed[0]["content"]
    assert len(trimmed) == 6  # 3 turns × 2 messages each
    print("  PASS: trim_conversation_keeps_last_n_turns")


def test_trim_conversation_no_op_when_within_window() -> None:
    """_trim_conversation returns full list when turns ≤ max_turns."""
    conv = []
    for i in range(4):
        conv.extend(_make_turn(i))

    trimmed = _trim_conversation(conv, max_turns=6)
    assert trimmed == conv
    print("  PASS: trim_conversation_no_op_when_within_window")


def test_dedup_signals_collapses_consecutive_same_kind() -> None:
    """Consecutive resource_lost signals collapse to one with ×N annotation."""
    def _sig(kind, summary):
        return ExpertSignal(
            task_id="t1", job_id="j1", kind=kind,
            summary=summary, timestamp=1.0,
        )

    signals = [
        _sig(SignalKind.RESOURCE_LOST, "missing actor"),
        _sig(SignalKind.RESOURCE_LOST, "still missing"),
        _sig(SignalKind.RESOURCE_LOST, "missing again"),
    ]
    result = _dedup_signals(signals)

    assert len(result) == 1
    assert "×3" in result[0].summary
    assert result[0].summary.startswith("missing again")  # last in run preserved
    print("  PASS: dedup_signals_collapses_consecutive_same_kind")


def test_dedup_signals_preserves_last_summary() -> None:
    """The last signal in a run (most recent data) is the one kept."""
    def _sig(kind, summary):
        return ExpertSignal(task_id="t1", job_id="j1", kind=kind, summary=summary, timestamp=1.0)

    signals = [
        _sig(SignalKind.RESOURCE_LOST, "first"),
        _sig(SignalKind.RESOURCE_LOST, "second"),
        _sig(SignalKind.TASK_COMPLETE, "done"),
    ]
    result = _dedup_signals(signals)

    assert len(result) == 2
    assert "second" in result[0].summary and "×2" in result[0].summary
    assert result[1].kind == SignalKind.TASK_COMPLETE
    print("  PASS: dedup_signals_preserves_last_summary")


def test_dedup_signals_mixed_kinds_not_collapsed() -> None:
    """Non-consecutive same-kind signals are NOT collapsed."""
    def _sig(kind):
        return ExpertSignal(task_id="t1", job_id="j1", kind=kind, summary="s", timestamp=1.0)

    signals = [
        _sig(SignalKind.RESOURCE_LOST),
        _sig(SignalKind.TASK_COMPLETE),
        _sig(SignalKind.RESOURCE_LOST),
    ]
    result = _dedup_signals(signals)
    assert len(result) == 3  # alternating — no collapse
    print("  PASS: dedup_signals_mixed_kinds_not_collapsed")


def test_truncate_tool_result_passthrough_small() -> None:
    """Small results pass through unchanged."""
    result = {"ok": True, "job_id": "j1", "timestamp": 1.0}
    content = _truncate_tool_result(result)
    assert "j1" in content
    assert "truncated" not in content
    print("  PASS: truncate_tool_result_passthrough_small")


def test_truncate_tool_result_summarises_large_data_list() -> None:
    """query_world result with large data list is summarised to 3 items + count."""
    actors = [{"actor_id": i, "name": f"unit{i}"} for i in range(20)]
    result = {"data": actors, "timestamp": 1.0}
    content = _truncate_tool_result(result)
    parsed = json.loads(content)
    assert parsed["data_count"] == 20
    assert len(parsed["data"]) == 3  # first 3 only
    assert "data_truncated" in parsed
    print("  PASS: truncate_tool_result_summarises_large_data_list")


def test_truncate_tool_result_hard_truncates_large_non_list() -> None:
    """Non-list result exceeding limit is hard-truncated."""
    from task_agent.agent import _MAX_TOOL_RESULT_CHARS
    big_text = "x" * (_MAX_TOOL_RESULT_CHARS + 500)
    result = {"text": big_text}
    content = _truncate_tool_result(result)
    assert len(content) <= _MAX_TOOL_RESULT_CHARS + 30  # small overhead for truncation marker
    assert "truncated" in content
    print("  PASS: truncate_tool_result_hard_truncates_large_non_list")


def test_conversation_window_bounds_message_size() -> None:
    """After many wake cycles the total conversation char count stays bounded."""
    task = make_task()
    provider = MockProvider()

    # Fill provider with enough responses to run 15 wake cycles
    for _ in range(30):
        provider.add_response(LLMResponse(text="monitoring", model="mock"))

    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=ToolExecutor(),
        jobs_provider=lambda _: [],
        world_summary_provider=lambda: WorldSummary(),
        config=AgentConfig(
            review_interval=0.001,
            max_turns=1,
            conversation_window=4,
        ),
    )

    # Simulate 12 wake cycles by calling _build_messages directly
    for cycle in range(12):
        ctx_msg = {"role": "user", "content": f"{_CONTEXT_MARKER}\n{{\"cycle\": {cycle}, \"data\": \"x\"*200}}"}
        msgs = agent._build_messages(ctx_msg)
        # Simulate assistant response appended
        asst = {"role": "assistant", "content": f"cycle {cycle} done"}
        agent._conversation.append(asst)

    # Total conversation chars should be bounded (window=4 → at most 4 ctx turns retained)
    total_chars = sum(len(json.dumps(m)) for m in agent._conversation)
    assert total_chars < 20_000, f"Conversation too large: {total_chars} chars"

    # Verify _build_messages only passes windowed history
    ctx_msg = {"role": "user", "content": f"{_CONTEXT_MARKER}\n{{\"final\": true}}"}
    msgs = agent._build_messages(ctx_msg)
    ctx_msgs_in_output = [m for m in msgs if _CONTEXT_MARKER in str(m.get("content", ""))]
    # system prompt + last 4 retained turns + new one = at most 5 ctx messages
    assert len(ctx_msgs_in_output) <= 5
    print("  PASS: conversation_window_bounds_message_size")


# --- Smart wake tests ---

def test_smart_wake_skips_llm_when_no_new_info() -> None:
    """Timer wake with no new signals and unchanged job statuses skips LLM."""
    task = make_task()
    provider = MockProvider()
    # Only one LLM response — second wake should be skipped
    provider.add_response(LLMResponse(text="monitoring, no action", model="mock"))

    job = make_job()
    calls_to_jobs_provider = [0]

    def jobs_provider(task_id):
        calls_to_jobs_provider[0] += 1
        return [job]

    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=make_executor(),
        jobs_provider=jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )

    async def run():
        # Wake 1: has job, no snapshot yet → runs LLM, sets snapshot
        await agent._wake_cycle(trigger="init")
        assert provider._call_count == 1

        # Wake 2: same job, no signals → smart skip
        await agent._wake_cycle(trigger="timer")
        assert provider._call_count == 1  # LLM NOT called again

    asyncio.run(run())
    print("  PASS: smart_wake_skips_llm_when_no_new_info")


def test_smart_wake_runs_llm_when_signal_arrives() -> None:
    """Wake with a new signal must NOT be skipped."""
    task = make_task()
    provider = MockProvider()
    provider.add_response(LLMResponse(text="processing signal", model="mock"))
    provider.add_response(LLMResponse(text="processing signal 2", model="mock"))

    job = make_job()

    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=make_executor(),
        jobs_provider=lambda _: [job],
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )

    async def run():
        # Wake 1: first wake, runs LLM
        await agent._wake_cycle(trigger="init")
        assert provider._call_count == 1

        # Push a signal before wake 2
        agent.queue.push(ExpertSignal(
            task_id=task.task_id, job_id=job.job_id,
            kind=SignalKind.RESOURCE_LOST, summary="actor lost",
        ))

        # Wake 2: has signal → must NOT skip
        await agent._wake_cycle(trigger="event_or_review")
        assert provider._call_count == 2

    asyncio.run(run())
    print("  PASS: smart_wake_runs_llm_when_signal_arrives")


def test_smart_wake_runs_llm_when_job_status_changes() -> None:
    """Wake with changed job status must NOT be skipped."""
    task = make_task()
    provider = MockProvider()
    provider.add_response(LLMResponse(text="ok", model="mock"))
    provider.add_response(LLMResponse(text="job changed", model="mock"))

    from models import JobStatus
    job = make_job()

    job_status = ["running"]

    def jobs_provider(_):
        j = make_job()
        j.status = JobStatus(job_status[0])
        return [j]

    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=make_executor(),
        jobs_provider=jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )

    async def run():
        # Wake 1: sets snapshot to {j1: running}
        await agent._wake_cycle(trigger="init")
        assert provider._call_count == 1

        # Job status changes to waiting
        job_status[0] = "waiting"

        # Wake 2: snapshot differs → must NOT skip
        await agent._wake_cycle(trigger="timer")
        assert provider._call_count == 2

    asyncio.run(run())
    print("  PASS: smart_wake_runs_llm_when_job_status_changes")


def test_smart_wake_no_skip_when_no_jobs() -> None:
    """Wake with no jobs (agent needs to start some) is never skipped."""
    task = make_task()
    provider = MockProvider()
    provider.add_response(LLMResponse(text="will start job", model="mock"))
    provider.add_response(LLMResponse(text="still no job", model="mock"))

    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=make_executor(),
        jobs_provider=lambda _: [],  # never any jobs
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )

    async def run():
        await agent._wake_cycle(trigger="init")
        assert provider._call_count == 1

        # Wake 2: still no jobs → should NOT skip (agent may need to retry)
        await agent._wake_cycle(trigger="timer")
        assert provider._call_count == 2

    asyncio.run(run())
    print("  PASS: smart_wake_no_skip_when_no_jobs")


def test_smart_wake_trigger_label_refined() -> None:
    """Trigger is refined to 'event'/'review'/'timer' based on drained items."""
    import logging_system
    logging_system.clear()

    task = make_task()
    provider = MockProvider()
    provider.add_response(LLMResponse(text="ok", model="mock"))

    job = make_job()
    agent = TaskAgent(
        task=task,
        llm=provider,
        tool_executor=make_executor(),
        jobs_provider=lambda _: [job],
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )

    async def run():
        # First wake sets snapshot
        await agent._wake_cycle(trigger="init")

        # Review wake (sentinel) with no real items → trigger should be "review"
        # Since job snapshot matches, this will be skipped after refinement
        agent.queue.trigger_review()
        await agent._wake_cycle(trigger="event_or_review")

        # Check slog for wake_skipped with trigger=review
        records = logging_system.records()
        skipped = [r for r in records if getattr(r, "event", None) == "wake_skipped"]
        assert skipped, "Expected wake_skipped log entry"
        assert skipped[-1].data.get("trigger") == "review"

    asyncio.run(run())
    print("  PASS: smart_wake_trigger_label_refined")


# --- BUG4: other_active_tasks scope awareness ---

def test_other_active_tasks_in_context_packet() -> None:
    """build_context_packet includes other_active_tasks when provided."""
    task = make_task()
    other = [
        {"label": "001", "raw_text": "建造电厂", "status": "running"},
        {"label": "002", "raw_text": "侦察西北", "status": "running"},
    ]
    pkt = build_context_packet(task, [], other_active_tasks=other)
    assert pkt.other_active_tasks == other
    print("  PASS: other_active_tasks_in_context_packet")


def test_other_active_tasks_empty_by_default() -> None:
    """other_active_tasks defaults to empty list when not provided."""
    task = make_task()
    pkt = build_context_packet(task, [])
    assert pkt.other_active_tasks == []
    print("  PASS: other_active_tasks_empty_by_default")


def test_context_to_message_includes_other_active_tasks() -> None:
    """context_to_message includes other_active_tasks in compact format."""
    task = make_task()
    other = [{"label": "001", "raw_text": "建造电厂", "status": "running"}]
    pkt = build_context_packet(task, [], other_active_tasks=other)
    msg = context_to_message(pkt)
    assert "建造电厂" in msg["content"]
    assert "[并行]" in msg["content"]
    print("  PASS: context_to_message_includes_other_active_tasks")


def test_agent_uses_active_tasks_provider() -> None:
    """TaskAgent calls active_tasks_provider and includes result in context."""
    task = make_task()
    sibling_tasks = [{"label": "001", "raw_text": "建造矿场", "status": "running"}]
    provider_call_count = [0]

    def active_tasks_provider(task_id: str) -> list[dict]:
        provider_call_count[0] += 1
        return sibling_tasks

    cap_provider = MockProvider()
    executor = make_executor()
    agent = TaskAgent(
        task=task,
        llm=cap_provider,
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
        config=AgentConfig(review_interval=0.001, max_turns=1),
    )
    agent.set_active_tasks_provider(active_tasks_provider)

    async def run():
        await agent._wake_cycle(trigger="init")
        # Provider must have been called
        assert provider_call_count[0] >= 1
        # The sibling task must appear in the context message sent to LLM
        assert cap_provider.call_log, "Expected LLM to be called"
        messages = cap_provider.call_log[0]["messages"]
        context_msgs = [m for m in messages if "[任务]" in str(m.get("content", ""))]
        assert context_msgs, "Expected at least one context message"
        assert "建造矿场" in context_msgs[0]["content"]
        assert "[并行]" in context_msgs[0]["content"]

    asyncio.run(run())
    print("  PASS: agent_uses_active_tasks_provider")


def test_system_prompt_has_multi_task_scope_section() -> None:
    """SYSTEM_PROMPT contains task scope guidance."""
    assert "并行任务" in SYSTEM_PROMPT or "聚焦" in SYSTEM_PROMPT
    assert "任务目标" in SYSTEM_PROMPT or "目标" in SYSTEM_PROMPT
    print("  PASS: system_prompt_has_multi_task_scope_section")


# --- BUG5: prerequisite waiting discipline ---

def test_system_prompt_has_prerequisite_waiting_discipline() -> None:
    """SYSTEM_PROMPT has prerequisite handling guidance."""
    assert "前置" in SYSTEM_PROMPT
    assert "request_units" in SYSTEM_PROMPT
    assert "不能自行补" in SYSTEM_PROMPT or "只能通过 request_units" in SYSTEM_PROMPT
    print("  PASS: system_prompt_has_prerequisite_waiting_discipline")


def test_system_prompt_has_fixed_demo_unit_roster() -> None:
    """SYSTEM_PROMPT pins the simplified OpenRA roster to avoid made-up units."""
    assert "e1=步兵" in SYSTEM_PROMPT
    assert "e3=火箭兵" in SYSTEM_PROMPT
    assert "ftrk=防空履带车" in SYSTEM_PROMPT
    assert "v2rl=V2火箭车" in SYSTEM_PROMPT
    assert "3tnk=重坦" in SYSTEM_PROMPT
    assert "4tnk=猛犸坦克" in SYSTEM_PROMPT
    assert "harv=矿车" in SYSTEM_PROMPT
    assert "mig=MIG" in SYSTEM_PROMPT
    assert "yak=YAK" in SYSTEM_PROMPT
    assert "不要编造" in SYSTEM_PROMPT
    print("  PASS: system_prompt_has_fixed_demo_unit_roster")


def test_knowledge_tech_prerequisites_for_infantry() -> None:
    """tech_prerequisites_for returns barracks requirement for infantry units."""
    from experts.knowledge import tech_prerequisites_for, display_name_for
    prereqs = tech_prerequisites_for("e1")
    assert len(prereqs) == 1
    assert prereqs[0]["unit_type"] == "barr"
    assert display_name_for("barr") == "兵营"
    print("  PASS: knowledge_tech_prerequisites_for_infantry")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Task Agent tests...\n")

    test_context_packet_construction()
    test_context_to_message()
    test_single_turn_text_response()
    test_llm_reasoning_is_logged()
    test_multi_turn_tool_use()
    test_complete_task_stops_loop()
    test_max_turns_limit()
    test_signal_queue_wakes_agent()
    test_event_queue()
    test_tool_executor_error_handling()
    test_full_lifecycle_with_signal()
    test_review_interval_timer()
    test_event_in_context_packet()
    test_default_if_timeout_applied()
    test_consecutive_failures_auto_terminate()
    test_failure_counter_resets_on_success()
    test_single_agent_error_isolation()
    test_managed_task_does_not_self_bootstrap_structure_build()
    test_capability_structure_build_completes_with_llm_running()
    test_bootstrap_finalizes_on_job_status_without_signal()
    test_managed_task_does_not_self_bootstrap_simple_production()
    test_capability_simple_production_completes_with_llm_running()
    test_existing_rule_routed_recon_job_attaches_and_llm_runs()
    test_bootstrap_job_decision_request_reaches_llm()
    test_system_prompt_pins_structure_build_commands_to_economy()
    test_normal_context_redacts_capability_planning_hints()
    test_capability_context_exposes_phase_and_blocker_blocks()
    # Expert-as-tool handler tests
    test_scout_map_handler_creates_recon_job()
    test_produce_units_handler_creates_economy_job()
    test_attack_handler_creates_combat_job()
    test_move_units_handler_creates_movement_job()
    test_deploy_mcv_handler_creates_deploy_job()
    test_start_job_removed_from_tool_definitions()
    # Parallel tool execution tests
    test_execute_tools_parallel()
    test_execute_tools_exception_isolation()
    # Mid-wake context refresh
    test_multi_turn_context_refresh()
    # BUG3: task completion judgment
    test_complete_task_warns_when_no_jobs_succeeded()
    test_context_packet_includes_job_status_zh()
    test_system_prompt_has_completion_judgment_rules()
    # Subscription mechanism tests
    test_subscription_filters_info_experts_in_context()
    test_update_subscriptions_handler()
    test_update_subscriptions_in_tool_definitions()
    test_adjutant_sets_subscriptions_from_expert_type()
    # BUG4: other_active_tasks scope awareness tests
    test_other_active_tasks_in_context_packet()
    test_other_active_tasks_empty_by_default()
    test_context_to_message_includes_other_active_tasks()
    test_agent_uses_active_tasks_provider()
    test_system_prompt_has_multi_task_scope_section()
    # BUG5: prerequisite waiting discipline tests
    test_system_prompt_has_prerequisite_waiting_discipline()
    test_knowledge_tech_prerequisites_for_infantry()
    # Conversation compression tests
    test_trim_conversation_keeps_last_n_turns()
    test_trim_conversation_no_op_when_within_window()
    test_dedup_signals_collapses_consecutive_same_kind()
    test_dedup_signals_preserves_last_summary()
    test_dedup_signals_mixed_kinds_not_collapsed()
    test_truncate_tool_result_passthrough_small()
    test_truncate_tool_result_summarises_large_data_list()
    test_truncate_tool_result_hard_truncates_large_non_list()
    test_conversation_window_bounds_message_size()
    test_smart_wake_skips_llm_when_no_new_info()
    test_smart_wake_runs_llm_when_signal_arrives()
    test_smart_wake_runs_llm_when_job_status_changes()
    test_smart_wake_no_skip_when_no_jobs()
    test_smart_wake_trigger_label_refined()

    print(f"\nAll 55 tests passed!")
