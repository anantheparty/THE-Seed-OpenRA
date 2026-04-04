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
    ExpertSignal,
    Event,
    EventType,
    Job,
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
from task_agent.agent import SYSTEM_PROMPT, _trim_conversation, _dedup_signals, _truncate_tool_result, _CONTEXT_MARKER


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
    """Context packet converts to a valid LLM user message."""
    packet = build_context_packet(task=make_task(), jobs=[make_job()])
    msg = context_to_message(packet)
    assert msg["role"] == "user"
    assert "[CONTEXT UPDATE]" in msg["content"]
    data = json.loads(msg["content"].split("\n", 1)[1])
    assert "context_packet" in data
    assert "task" in data["context_packet"]
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
    assert "context_packet" in input_logs[-1].data["messages"][-1]["content"]
    print("  PASS: llm_reasoning_is_logged")


def test_multi_turn_tool_use():
    """Agent wakes, LLM calls tools → continues → text → ends."""
    mock = MockProvider(responses=[
        # Turn 1: LLM calls start_job
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="start_job", arguments='{"expert_type":"ReconExpert","config":{"search_region":"northeast","target_type":"base","target_owner":"enemy"}}')],
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
        # Init wake: start a job
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="start_job", arguments='{"expert_type":"ReconExpert","config":{"search_region":"northeast","target_type":"base","target_owner":"enemy"}}')],
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
    context_msg = [m for m in messages if m.get("role") == "user" and "[CONTEXT UPDATE]" in m.get("content", "")]
    assert len(context_msg) >= 1
    import json as _json
    content = _json.loads(context_msg[-1]["content"].split("\n", 1)[1])
    events = content["context_packet"]["recent_events"]
    assert len(events) == 1
    assert events[0]["type"] == "ENEMY_DISCOVERED"
    assert events[0]["actor_id"] == 201
    assert events[0]["position"] == [1800, 420]
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
    assert len(player_warnings) >= 1
    assert player_warnings[0].type.value == "task_warning"
    assert "连续失败" in player_warnings[0].content
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


def test_bootstrap_structure_build_maps_refinery_to_proc() -> None:
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

    assert captured == [
        {
            "expert_type": "EconomyExpert",
            "config": {
                "unit_type": "proc",
                "count": 1,
                "queue_type": "Building",
                "repeat": False,
            },
        }
    ]
    print("  PASS: bootstrap_structure_build_maps_refinery_to_proc")


def test_bootstrap_structure_build_completes_without_llm_drift() -> None:
    captured_start_jobs: list[dict] = []
    captured_completions: list[dict] = []

    class NoLlmNeededProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            raise AssertionError("Bootstrap structure-build path should not call the LLM")

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

    agent = TaskAgent(
        task=make_task(raw_text="建造兵营"),
        llm=NoLlmNeededProvider(),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        await agent._wake_cycle(trigger="init")
        agent.push_signal(
            ExpertSignal(
                task_id="t1",
                job_id="j_bootstrap",
                kind=SignalKind.TASK_COMPLETE,
                summary="生产完成 1/1: barr",
                result="succeeded",
            )
        )
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
    assert agent._total_llm_calls == 0
    print("  PASS: bootstrap_structure_build_completes_without_llm_drift")


def test_bootstrap_simple_production_maps_basic_infantry_to_e1() -> None:
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

    assert captured == [
        {
            "expert_type": "EconomyExpert",
            "config": {
                "unit_type": "e1",
                "count": 3,
                "queue_type": "Infantry",
                "repeat": False,
            },
        }
    ]
    print("  PASS: bootstrap_simple_production_maps_basic_infantry_to_e1")


def test_bootstrap_simple_production_completes_without_llm_drift() -> None:
    captured_start_jobs: list[dict] = []
    captured_completions: list[dict] = []

    class NoLlmNeededProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            raise AssertionError("Bootstrap simple-production path should not call the LLM")

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

    agent = TaskAgent(
        task=make_task(raw_text="生产3个步兵"),
        llm=NoLlmNeededProvider(),
        tool_executor=executor,
        jobs_provider=noop_jobs_provider,
        world_summary_provider=noop_world_provider,
    )

    async def run():
        await agent._wake_cycle(trigger="init")
        agent.push_signal(
            ExpertSignal(
                task_id="t1",
                job_id="j_bootstrap_prod",
                kind=SignalKind.TASK_COMPLETE,
                summary="生产完成 3/3: e1",
                result="succeeded",
            )
        )
        await agent._wake_cycle(trigger="event")

    asyncio.run(run())

    assert captured_start_jobs == [
        {
            "expert_type": "EconomyExpert",
            "config": {
                "unit_type": "e1",
                "count": 3,
                "queue_type": "Infantry",
                "repeat": False,
            },
        }
    ]
    assert len(captured_completions) == 1
    assert captured_completions[0]["result"] == "succeeded"
    assert "生产3个步兵" in captured_completions[0]["summary"]
    assert agent._task_completed is True
    assert agent._total_llm_calls == 0
    print("  PASS: bootstrap_simple_production_completes_without_llm_drift")


def test_existing_rule_routed_recon_job_is_monitor_only() -> None:
    class NoLlmNeededProvider(MockProvider):
        async def chat(self, messages, **kwargs):
            raise AssertionError("Rule-routed recon monitor path should not call the LLM")

    task = make_task(raw_text="探索地图")
    job = make_job(job_id="j_rule_recon", task_id=task.task_id)

    agent = TaskAgent(
        task=task,
        llm=NoLlmNeededProvider(),
        tool_executor=make_executor(),
        jobs_provider=lambda _tid: [job],
        world_summary_provider=noop_world_provider,
    )

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._bootstrap_job_id == "j_rule_recon"
    assert agent._total_llm_calls == 0
    print("  PASS: existing_rule_routed_recon_job_is_monitor_only")


def test_system_prompt_pins_structure_build_commands_to_economy() -> None:
    assert '建造矿场' in SYSTEM_PROMPT
    assert 'unit_type "proc"' in SYSTEM_PROMPT
    assert 'Do NOT reinterpret "矿场" as expansion scouting or "矿车"' in SYSTEM_PROMPT
    print("  PASS: system_prompt_pins_structure_build_commands_to_economy")


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
    test_bootstrap_structure_build_maps_refinery_to_proc()
    test_bootstrap_structure_build_completes_without_llm_drift()
    test_bootstrap_simple_production_maps_basic_infantry_to_e1()
    test_bootstrap_simple_production_completes_without_llm_drift()
    test_existing_rule_routed_recon_job_is_monitor_only()
    test_system_prompt_pins_structure_build_commands_to_economy()
    test_trim_conversation_keeps_last_n_turns()
    test_trim_conversation_no_op_when_within_window()
    test_dedup_signals_collapses_consecutive_same_kind()
    test_dedup_signals_preserves_last_summary()
    test_dedup_signals_mixed_kinds_not_collapsed()
    test_truncate_tool_result_passthrough_small()
    test_truncate_tool_result_summarises_large_data_list()
    test_truncate_tool_result_hard_truncates_large_non_list()
    test_conversation_window_bounds_message_size()

    print(f"\nAll 30 tests passed!")
