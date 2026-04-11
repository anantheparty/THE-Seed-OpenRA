"""Tests for GameLoop — mock WorldModel, Kernel, and Jobs."""

from __future__ import annotations

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

import pytest

from adjutant.adjutant import Adjutant
from experts.base import BaseJob
from llm import LLMResponse
from models import (
    EconomyJobConfig,
    Event,
    EventType,
    ExpertSignal,
    JobStatus,
    ReconJobConfig,
    CombatJobConfig,
    EngagementMode,
    SignalKind,
)
from game_loop import GameLoop, GameLoopConfig


# --- Mocks ---

class MockWorldModel:
    def __init__(self, events_per_refresh: Optional[list[Event]] = None, health_sequence: Optional[list[dict[str, Any]]] = None):
        self._events = events_per_refresh or []
        self.refresh_count = 0
        self._buffered: list[Event] = []
        self._health_sequence = list(health_sequence or [])
        self._health = {
            "stale": False,
            "consecutive_failures": 0,
            "total_failures": 0,
            "last_error": None,
            "failure_threshold": 3,
            "timestamp": 0.0,
        }

    def refresh(self, *, now=None, force=False) -> list[Event]:
        self.refresh_count += 1
        # Buffer events internally (like real WorldModel does)
        self._buffered = list(self._events)
        if self._health_sequence:
            self._health = dict(self._health_sequence.pop(0))
        if now is not None:
            self._health["timestamp"] = now
        return list(self._events)

    def detect_events(self, *, clear=True) -> list[Event]:
        events = list(self._buffered)
        if clear:
            self._buffered = []
        return events

    def refresh_health(self) -> dict[str, Any]:
        return dict(self._health)


class MockKernel:
    def __init__(self):
        self.routed_events: list[Event] = []
        self.tick_calls = 0
        self.player_notifications: list[dict[str, Any]] = []
        self.tasks: list[Any] = []

    def route_events(self, events: list[Event]) -> None:
        self.routed_events.extend(events)

    def tick(self, *, now=None) -> int:
        self.tick_calls += 1
        return 0

    def push_player_notification(self, notification_type: str, content: str, *, data=None, timestamp=None) -> None:
        self.player_notifications.append(
            {
                "type": notification_type,
                "content": content,
                "data": dict(data or {}),
                "timestamp": timestamp,
            }
        )

    def create_task(self, raw_text: str, kind: str, priority: int) -> Any:
        task = type("Task", (), {"task_id": "t1", "raw_text": raw_text, "kind": kind, "priority": priority})()
        self.tasks.append(task)
        return task

    def submit_player_response(self, response, *, now=None) -> dict[str, Any]:
        del response, now
        return {"ok": True, "message": "ok"}

    def list_pending_questions(self) -> list[dict[str, Any]]:
        return []

    def list_tasks(self) -> list[Any]:
        return list(self.tasks)


class MockTickJob(BaseJob):
    tick_interval = 0.05  # 50ms — ticks every other loop tick at 10Hz

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tick_count = 0

    @property
    def expert_type(self) -> str:
        return "MockExpert"

    def tick(self) -> None:
        self.tick_count += 1


class SlowTickJob(BaseJob):
    tick_interval = 0.2  # 200ms

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tick_count = 0

    @property
    def expert_type(self) -> str:
        return "SlowExpert"

    def tick(self) -> None:
        self.tick_count += 1


class FaultyJob(BaseJob):
    tick_interval = 0.0

    @property
    def expert_type(self) -> str:
        return "FaultyExpert"

    def tick(self) -> None:
        raise RuntimeError("boom")


class TerminatingJob(BaseJob):
    """Job that succeeds on its first tick."""
    tick_interval = 0.0

    @property
    def expert_type(self) -> str:
        return "TerminatingExpert"

    def tick(self) -> None:
        self.status = JobStatus.SUCCEEDED
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary="done",
            result="succeeded",
        )


class DirectCompletionEconomyJob(BaseJob):
    tick_interval = 0.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.produced_count = 0

    @property
    def expert_type(self) -> str:
        return "EconomyExpert"

    def tick(self) -> None:
        self.produced_count += 1
        self.status = JobStatus.SUCCEEDED


class BlockingWorldModel(MockWorldModel):
    def __init__(self, block_s: float = 0.3):
        super().__init__()
        self.block_s = block_s

    def refresh(self, *, now=None, force=False) -> list[Event]:
        time.sleep(self.block_s)
        return super().refresh(now=now, force=force)

    def world_summary(self) -> dict[str, Any]:
        return {
            "economy": {},
            "military": {},
            "map": {},
            "known_enemy": {},
            "timestamp": time.time(),
        }

    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        del query_type, params
        return {}


class BlockingQueueManager:
    def __init__(self, block_s: float = 0.3) -> None:
        self.block_s = block_s
        self.tick_calls = 0

    def tick(self, *, now: float) -> None:
        del now
        self.tick_calls += 1
        time.sleep(self.block_s)


class SleepLLM:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> LLMResponse:
        del messages, tools, max_tokens, temperature
        await asyncio.sleep(0.05)
        return LLMResponse(text='{"type":"query","confidence":1.0}')


# --- Tests ---

def test_loop_starts_and_stops():
    """GameLoop starts, runs a few ticks, and stops cleanly."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.15)  # ~15 ticks at 100Hz
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert not loop.is_running
    assert loop.tick_count >= 5  # At least a few ticks
    assert wm.refresh_count == loop.tick_count
    print(f"  PASS: loop_starts_and_stops (ticks={loop.tick_count})")


def test_events_routed_to_kernel():
    """Events from WorldModel refresh are forwarded to Kernel."""
    events = [
        Event(type=EventType.ENEMY_DISCOVERED, actor_id=201, position=(500, 600)),
        Event(type=EventType.UNIT_DAMAGED, actor_id=57, data={"old_hp": 100, "new_hp": 85}),
    ]
    wm = MockWorldModel(events_per_refresh=events)
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.05)  # A few ticks
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert len(kernel.routed_events) >= 2  # At least one tick's worth
    types = {e.type for e in kernel.routed_events}
    assert EventType.ENEMY_DISCOVERED in types
    assert EventType.UNIT_DAMAGED in types
    print(f"  PASS: events_routed_to_kernel (events={len(kernel.routed_events)})")


def test_job_tick_scheduling():
    """Jobs are ticked according to their tick_interval."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    signals: list = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    fast_job = MockTickJob(
        job_id="j_fast", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    fast_job.on_resource_granted(["actor:57"])

    slow_job = SlowTickJob(
        job_id="j_slow", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    slow_job.on_resource_granted(["actor:58"])

    loop.register_job(fast_job)
    loop.register_job(slow_job)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.3)  # 300ms
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    # Fast job (50ms interval over 300ms) should have more ticks than slow (200ms)
    assert fast_job.tick_count > slow_job.tick_count
    assert fast_job.tick_count >= 3  # At least 3 ticks in 300ms at 50ms interval
    assert slow_job.tick_count >= 1  # At least 1 tick in 300ms at 200ms interval
    print(f"  PASS: job_tick_scheduling (fast={fast_job.tick_count}, slow={slow_job.tick_count})")


def test_register_unregister_job():
    """Jobs can be registered and unregistered dynamically."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    signals: list = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    job = MockTickJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:57"])

    loop.register_job(job)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.1)

        # Unregister mid-run
        ticks_before = job.tick_count
        loop.unregister_job("j1")
        await asyncio.sleep(0.1)

        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)
        return ticks_before

    ticks_before = asyncio.run(run())

    # After unregister, tick count should not have increased (much)
    assert job.tick_count <= ticks_before + 1  # At most 1 extra from race
    print(f"  PASS: register_unregister_job (before={ticks_before}, after={job.tick_count})")


def test_reset_runtime_state_clears_stale_bookkeeping():
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel)

    loop._world_stale_active = True
    loop._world_stale_notified = True
    loop._world_stale_since = 123.0
    loop._world_stale_escalated = True
    loop._paused_for_recovery = {"j1", "j2"}

    loop.reset_runtime_state()

    assert loop._world_stale_active is False
    assert loop._world_stale_notified is False
    assert loop._world_stale_since is None
    assert loop._world_stale_escalated is False
    assert loop._paused_for_recovery == set()
    print("  PASS: reset_runtime_state_clears_stale_bookkeeping")


def test_direct_completion_economy_job_routes_synthetic_production_event():
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    job = DirectCompletionEconomyJob(
        job_id="j_econ",
        task_id="t1",
        config=EconomyJobConfig(unit_type="3tnk", count=1, queue_type="Vehicle"),
        signal_callback=lambda _signal: None,
    )
    loop.register_job(job)

    async def run():
        await loop._tick_jobs(time.time())

    asyncio.run(run())

    production_events = [event for event in kernel.routed_events if event.type == EventType.PRODUCTION_COMPLETE]
    assert len(production_events) == 1
    assert production_events[0].data["source"] == "job_direct_completion"
    assert production_events[0].data["job_id"] == "j_econ"
    assert production_events[0].data["queue_type"] == "Vehicle"
    assert production_events[0].data["name"] == "3tnk"


def test_terminated_jobs_skipped():
    """Jobs in terminal states are not ticked."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    signals: list = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    job = MockTickJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:57"])
    job.status = JobStatus.SUCCEEDED  # Already terminated

    loop.register_job(job)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.1)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert job.tick_count == 0  # Never ticked
    print("  PASS: terminated_jobs_skipped")


def test_dashboard_callback():
    """Dashboard callback is called each tick."""
    wm = MockWorldModel()
    kernel = MockKernel()

    dashboard_calls: list[tuple[int, float]] = []

    def dashboard_cb(tick_num: int, ts: float) -> None:
        dashboard_calls.append((tick_num, ts))

    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100), dashboard_callback=dashboard_cb)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.1)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert len(dashboard_calls) >= 5
    # Tick numbers should be sequential
    for i, (tick_num, _) in enumerate(dashboard_calls):
        assert tick_num == i + 1
    print(f"  PASS: dashboard_callback (calls={len(dashboard_calls)})")


def test_configurable_tick_rate():
    """GameLoop respects custom tick rate."""
    wm = MockWorldModel()
    kernel = MockKernel()

    config = GameLoopConfig(tick_hz=50)  # 50Hz = 20ms interval
    assert config.tick_interval == 0.02

    loop = GameLoop(wm, kernel, config=config)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.15)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    # 50Hz over 150ms ≈ 7-8 ticks
    assert loop.tick_count >= 5
    print(f"  PASS: configurable_tick_rate (50Hz, ticks={loop.tick_count})")


@pytest.mark.runtime_invariants
def test_worldmodel_stale_pauses_jobs_notifies_and_recovers():
    health_sequence = [
        {"stale": True, "consecutive_failures": 1, "total_failures": 1, "last_error": "disconnect", "failure_threshold": 3, "timestamp": 101.0},
        {"stale": True, "consecutive_failures": 2, "total_failures": 2, "last_error": "disconnect", "failure_threshold": 3, "timestamp": 102.0},
        {"stale": True, "consecutive_failures": 3, "total_failures": 3, "last_error": "disconnect", "failure_threshold": 3, "timestamp": 103.0},
        {"stale": False, "consecutive_failures": 0, "total_failures": 3, "last_error": None, "failure_threshold": 3, "timestamp": 104.0},
    ]
    wm = MockWorldModel(health_sequence=health_sequence)
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    job = MockTickJob(job_id="j_recover", task_id="t1", config=config, signal_callback=signals.append)
    job.tick_interval = 0.0
    job.on_resource_granted(["actor:57"])
    loop.register_job(job)

    async def run():
        for _ in range(4):
            await loop._tick()

    asyncio.run(run())

    assert kernel.tick_calls == 4
    assert len(kernel.player_notifications) == 1
    assert kernel.player_notifications[0]["type"] == "world_model_stale"
    assert job.status == JobStatus.RUNNING
    assert job.tick_count == 1
    print("  PASS: worldmodel_stale_pauses_jobs_notifies_and_recovers")


def test_job_exception_emits_failed_signal():
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    job = FaultyJob(job_id="j_fail", task_id="t1", config=config, signal_callback=signals.append)
    job.on_resource_granted(["actor:57"])
    loop.register_job(job)

    async def run():
        await loop._tick()

    asyncio.run(run())

    assert job.status == JobStatus.FAILED
    assert len(signals) == 1
    assert signals[0].kind == SignalKind.TASK_COMPLETE
    assert signals[0].result == "failed"
    assert signals[0].data["error_type"] == "RuntimeError"
    print("  PASS: job_exception_emits_failed_signal")


def test_job_terminal_status_immediately_wakes_agent():
    """When a Job transitions to terminal status, the agent queue is triggered
    from the event-loop thread — not just via the (unreliable) thread-side push.

    This test verifies that after one tick of a TerminatingJob, the corresponding
    agent's queue has at least one pending item (the trigger_review sentinel),
    which means the agent will wake promptly rather than waiting review_interval.
    """
    from task_agent.queue import AgentQueue

    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    # Register an agent queue for task t_finish
    agent_queue = AgentQueue()
    loop.register_agent("t_finish", agent_queue, review_interval=10.0)

    # Register a job that will succeed on first tick
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")
    job = TerminatingJob(job_id="j_finish", task_id="t_finish", config=config, signal_callback=signals.append)
    job.on_resource_granted(["actor:57"])
    loop.register_job(job)

    async def run():
        # One tick: job runs, succeeds, terminal transition detected
        await loop._tick_jobs(time.time())

    asyncio.run(run())

    assert job.status == JobStatus.SUCCEEDED
    # Agent queue must have pending items (the terminal-wake trigger_review sentinel)
    assert agent_queue.pending_count > 0, "Expected agent queue to have a pending wake after job completion"
    print("  PASS: job_terminal_status_immediately_wakes_agent")


def test_blocking_world_refresh_does_not_starve_adjutant_llm():
    wm = BlockingWorldModel(block_s=0.3)
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=10))
    adjutant = Adjutant(llm=SleepLLM(), kernel=kernel, world_model=wm)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(adjutant.handle_player_input("战况如何？"), timeout=0.5)
            elapsed = time.perf_counter() - start
            return result, elapsed
        finally:
            loop.stop()
            await asyncio.wait_for(task, timeout=2.0)

    result, elapsed = asyncio.run(run())

    assert result["type"] == "query"
    assert elapsed < 0.25
    print(f"  PASS: blocking_world_refresh_does_not_starve_adjutant_llm (elapsed={elapsed:.3f}s)")


def test_blocking_queue_manager_does_not_starve_adjutant_llm():
    wm = MockWorldModel()
    kernel = MockKernel()
    queue_manager = BlockingQueueManager(block_s=0.3)
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=10), queue_manager=queue_manager)
    adjutant = Adjutant(llm=SleepLLM(), kernel=kernel, world_model=wm)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(adjutant.handle_player_input("战况如何？"), timeout=0.5)
            elapsed = time.perf_counter() - start
            return result, elapsed
        finally:
            loop.stop()
            await asyncio.wait_for(task, timeout=2.0)

    result, elapsed = asyncio.run(run())

    assert result["type"] == "query"
    assert elapsed < 0.25
    assert queue_manager.tick_calls >= 1
    print(f"  PASS: blocking_queue_manager_does_not_starve_adjutant_llm (elapsed={elapsed:.3f}s)")


# --- Run all tests ---

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
