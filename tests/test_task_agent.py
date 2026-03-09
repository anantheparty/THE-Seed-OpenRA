"""Tests for Task Agent agentic loop — using MockProvider + mock tool handlers."""

from __future__ import annotations

import asyncio
import json
import time
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Task Agent tests...\n")

    test_context_packet_construction()
    test_context_to_message()
    test_single_turn_text_response()
    test_multi_turn_tool_use()
    test_complete_task_stops_loop()
    test_max_turns_limit()
    test_signal_queue_wakes_agent()
    test_event_queue()
    test_tool_executor_error_handling()
    test_full_lifecycle_with_signal()
    test_review_interval_timer()

    print(f"\nAll 11 tests passed!")
