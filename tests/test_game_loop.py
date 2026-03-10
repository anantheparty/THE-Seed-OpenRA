"""Tests for GameLoop — mock WorldModel, Kernel, and Jobs."""

from __future__ import annotations

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from experts.base import BaseJob
from models import (
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
    def __init__(self, events_per_refresh: Optional[list[Event]] = None):
        self._events = events_per_refresh or []
        self.refresh_count = 0
        self._buffered: list[Event] = []

    def refresh(self, *, now=None, force=False) -> list[Event]:
        self.refresh_count += 1
        return list(self._events)

    def detect_events(self, *, clear=True) -> list[Event]:
        events = list(self._buffered)
        if clear:
            self._buffered = []
        return events


class MockKernel:
    def __init__(self):
        self.routed_events: list[Event] = []

    def route_events(self, events: list[Event]) -> None:
        self.routed_events.extend(events)


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


# --- Run all tests ---

if __name__ == "__main__":
    print("Running GameLoop tests...\n")

    test_loop_starts_and_stops()
    test_events_routed_to_kernel()
    test_job_tick_scheduling()
    test_register_unregister_job()
    test_terminated_jobs_skipped()
    test_dashboard_callback()
    test_configurable_tick_rate()

    print(f"\nAll 7 tests passed!")
