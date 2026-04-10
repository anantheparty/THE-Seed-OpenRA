"""Tests for WebSocket server (1.6) and review_interval scheduling (1.8)."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

import aiohttp
import benchmark
import logging_system
from logging_system import start_persistence_session, stop_persistence_session

from models import Event, EventType, TaskStatus
from main import RuntimeBridge
from task_agent.queue import AgentQueue
from game_loop import GameLoop, GameLoopConfig
from ws_server import WSServer, WSServerConfig
from ws_server.server import _THROTTLE_INTERVAL


# --- Mocks for GameLoop ---

class MockWorldModel:
    def __init__(self):
        self.refresh_count = 0
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
        if now is not None:
            self._health["timestamp"] = now
        return []

    def detect_events(self, *, clear=True) -> list[Event]:
        return []

    def refresh_health(self) -> dict[str, Any]:
        return dict(self._health)


class MockKernel:
    def __init__(self):
        self.tick_count = 0

    def route_events(self, events: list[Event]) -> None:
        pass

    def tick(self, *, now=None) -> int:
        self.tick_count += 1
        return 0

    def push_player_notification(self, notification_type: str, content: str, *, data=None, timestamp=None) -> None:
        return None


# --- 1.8 Tests: review_interval scheduling ---

def test_review_interval_triggers_wake():
    """GameLoop wakes Task Agent queue when review_interval elapses."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    queue = AgentQueue()
    loop.register_agent("t1", queue, review_interval=0.1)  # 100ms

    wake_count = 0

    async def run():
        nonlocal wake_count
        task = asyncio.create_task(loop.start())

        # Check wake events over 350ms
        for _ in range(5):
            woken = await queue.wait_for_wake(timeout=0.15)
            if woken:
                wake_count += 1

        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert wake_count >= 2  # Should have woken at least 2 times in 350ms at 100ms interval
    print(f"  PASS: review_interval_triggers_wake (wakes={wake_count})")


def test_register_unregister_agent():
    """Agent registration/unregistration works."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    queue = AgentQueue()
    loop.register_agent("t1", queue, review_interval=0.05)

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.1)

        # Unregister — should stop waking
        loop.unregister_agent("t1")
        queue._wake_event.clear()
        await asyncio.sleep(0.1)

        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())
    # After unregister, the queue should not have been woken
    assert not queue._wake_event.is_set()
    print("  PASS: register_unregister_agent")


def test_multiple_agents_different_intervals():
    """Multiple agents with different review_intervals are scheduled independently."""
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    fast_queue = AgentQueue()
    slow_queue = AgentQueue()
    loop.register_agent("t_fast", fast_queue, review_interval=0.05)  # 50ms
    loop.register_agent("t_slow", slow_queue, review_interval=0.5)   # 500ms

    fast_wakes = 0
    slow_wakes = 0

    async def run():
        nonlocal fast_wakes, slow_wakes
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.35)  # 350ms — fast should fire ~6x, slow 0x
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

        # Count how many times each wake_event was set
        # Use pending_count as proxy — each set() wakes the queue
        # Simpler: just check that fast fired and slow didn't
        # Fast: 350ms / 50ms = ~7 fires
        # Slow: 350ms / 500ms = 0 fires
        fast_wakes = fast_queue._wake_event.is_set()
        slow_wakes = slow_queue._wake_event.is_set()

    asyncio.run(run())

    # Fast should have been triggered, slow should not (500ms > 350ms)
    assert fast_wakes, "Fast agent should have been woken"
    assert not slow_wakes, "Slow agent should not have been woken yet"
    print(f"  PASS: multiple_agents_different_intervals")


def test_suspended_agent_skips_periodic_review():
    """Periodic review must not enqueue wakes for agents parked on unit requests.

    Otherwise the review sentinel remains queued, wait_for_wake() returns
    immediately forever, and the backend spins at 100% CPU.
    """
    wm = MockWorldModel()
    kernel = MockKernel()
    loop = GameLoop(wm, kernel, config=GameLoopConfig(tick_hz=100))

    queue = AgentQueue()
    suspended = True
    loop.register_agent(
        "t_waiting",
        queue,
        review_interval=0.05,
        is_suspended=lambda: suspended,
    )

    async def run():
        task = asyncio.create_task(loop.start())
        await asyncio.sleep(0.15)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(run())

    assert queue.pending_count == 0
    assert not queue._wake_event.is_set()
    print("  PASS: suspended_agent_skips_periodic_review")


# --- 1.6 Tests: WebSocket server ---

def test_ws_server_start_stop():
    """WS server starts and stops cleanly."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18765))

    async def run():
        await server.start()
        assert server.is_running
        assert server.client_count == 0
        await server.stop()
        assert not server.is_running

    asyncio.run(run())
    print("  PASS: ws_server_start_stop")


def test_ws_client_connect_and_inbound():
    """Client connects and sends inbound messages."""
    received_commands: list[str] = []

    class TestHandler:
        def __init__(self):
            self.session_clears = 0

        async def on_command_submit(self, text, client_id):
            received_commands.append(text)

        async def on_command_cancel(self, task_id, client_id):
            received_commands.append(f"cancel:{task_id}")

        async def on_mode_switch(self, mode, client_id):
            received_commands.append(f"mode:{mode}")

        async def on_question_reply(self, message_id, task_id, answer, client_id):
            received_commands.append(f"reply:{message_id}:{task_id}:{answer}")

        async def on_game_restart(self, save_path, client_id):
            received_commands.append(f"restart:{save_path}")

        async def on_sync_request(self, client_id):
            received_commands.append(f"sync:{client_id}")

        async def on_session_clear(self, client_id):
            self.session_clears += 1
            received_commands.append(f"clear:{client_id}")

        async def on_task_replay_request(self, task_id, client_id):
            received_commands.append(f"replay:{task_id}:{client_id}")

    handler = TestHandler()
    server = WSServer(
        config=WSServerConfig(host="127.0.0.1", port=18766),
        inbound_handler=handler,
    )

    async def run():
        await server.start()

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:18766/ws") as ws:
                assert server.client_count == 1

                await ws.send_str(json.dumps({"type": "command_submit", "text": "探索地图"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "command_cancel", "task_id": "t1"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "mode_switch", "mode": "debug"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "game_restart", "save_path": "baseline.orasav"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "session_clear"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "task_replay_request", "task_id": "t9"}))
                await asyncio.sleep(0.05)

        await server.stop()

    asyncio.run(run())

    assert "探索地图" in received_commands
    assert "cancel:t1" in received_commands
    assert "mode:debug" in received_commands
    assert "restart:baseline.orasav" in received_commands
    assert any(item.startswith("clear:client_") for item in received_commands)
    assert any(item.startswith("replay:t9:client_") for item in received_commands)
    assert handler.session_clears == 1
    print("  PASS: ws_client_connect_and_inbound")


def test_ws_broadcast_outbound():
    """Server broadcasts outbound messages to all clients."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18767))

    received: list[dict] = []

    async def run():
        await server.start()

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:18767/ws") as ws:
                await asyncio.sleep(0.05)

                await server.send_world_snapshot({"economy": {"cash": 5000}, "military": {"units": 10}})
                await server.send_player_notification({"content": "敌人在扩张", "type": "info"})

                for _ in range(2):
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    received.append(json.loads(msg.data))

        await server.stop()

    asyncio.run(run())

    assert len(received) == 2
    types = {m["type"] for m in received}
    assert "world_snapshot" in types
    assert "player_notification" in types
    for msg in received:
        assert "timestamp" in msg
        assert msg["timestamp"] > 0
    print("  PASS: ws_broadcast_outbound")


def test_ws_multi_client():
    """Multiple clients receive broadcasts."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18768))

    client_messages: dict[str, list] = {"c1": [], "c2": []}

    async def run():
        await server.start()

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:18768/ws") as ws1:
                async with session.ws_connect("http://127.0.0.1:18768/ws") as ws2:
                    await asyncio.sleep(0.05)
                    assert server.client_count == 2

                    await server.send_task_list([{"task_id": "t1", "status": "running"}])

                    msg1 = await asyncio.wait_for(ws1.receive(), timeout=1.0)
                    msg2 = await asyncio.wait_for(ws2.receive(), timeout=1.0)
                    client_messages["c1"].append(json.loads(msg1.data))
                    client_messages["c2"].append(json.loads(msg2.data))

        await server.stop()

    asyncio.run(run())

    assert len(client_messages["c1"]) == 1
    assert len(client_messages["c2"]) == 1
    assert client_messages["c1"][0]["type"] == "task_list"
    assert client_messages["c2"][0]["type"] == "task_list"
    print("  PASS: ws_multi_client")


def test_ws_query_response_envelope():
    """`query_response` keeps the payload under the WS `data` envelope."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18769))

    async def run():
        await server.start()

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:18769/ws") as ws:
                await asyncio.sleep(0.05)
                await server.send_query_response(
                    {
                        "answer": "收到指令，已创建任务 t_demo",
                        "response_type": "command",
                        "ok": True,
                        "task_id": "t_demo",
                    }
                )
                msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                payload = json.loads(msg.data)
                assert payload["type"] == "query_response"
                assert payload["data"]["answer"] == "收到指令，已创建任务 t_demo"
                assert payload["data"]["response_type"] == "command"
                assert payload["data"]["task_id"] == "t_demo"
                assert "answer" not in payload

        await server.stop()

    asyncio.run(run())
    print("  PASS: ws_query_response_envelope")


def test_ws_send_to_client_targets_single_client():
    """History replay helper only targets the requesting client."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18770))

    async def run():
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect("http://127.0.0.1:18770/ws") as ws1:
                    async with session.ws_connect("http://127.0.0.1:18770/ws") as ws2:
                        await asyncio.sleep(0.05)
                        await server.send_to_client("client_1", "log_entry", {"message": "only-one"})
                        msg1 = await asyncio.wait_for(ws1.receive(), timeout=1.0)
                        payload1 = json.loads(msg1.data)
                        assert payload1["type"] == "log_entry"
                        assert payload1["data"]["message"] == "only-one"
                        try:
                            await asyncio.wait_for(ws2.receive(), timeout=0.2)
                            raise AssertionError("second client unexpectedly received targeted message")
                        except asyncio.TimeoutError:
                            pass
        finally:
            await server.stop()

    asyncio.run(run())
    print("  PASS: ws_send_to_client_targets_single_client")


def test_sync_request_pushes_current_state_directly():
    """sync_request should deliver current snapshot/task list directly to the requesting client."""

    class FakeTask:
        def __init__(self, task_id: str, raw_text: str, status: str = "running"):
            self.task_id = task_id
            self.raw_text = raw_text
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("Status", (), {"value": status})()
            self.timestamp = 123.0
            self.created_at = 100.0

    class FakeJob:
        def __init__(self, job_id: str, expert_type: str):
            self.job_id = job_id
            self.expert_type = expert_type
            self.status = type("Status", (), {"value": "running"})()
            self.resources = []
            self.timestamp = 124.0
            self.config = {}

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask("t1", "建造电厂")]

        def list_pending_questions(self):
            return [{"message_id": "msg_1", "task_id": "t1", "options": ["是", "否"]}]

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            return [FakeJob("j1", "EconomyExpert")] if task_id == "t1" else []

        def get_task_agent(self, task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

        def runtime_state(self):
            return {}

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {"economy": {"cash": 1200}, "military": {"units": 3}}

        def runtime_state(self):
            return {"active_tasks": 1}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict]] = []

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(
                (
                    "task_list",
                    {
                        "client_id": client_id,
                        "tasks": tasks,
                        "pending_questions": pending_questions,
                    },
                )
            )

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    async def run():
        await bridge.on_sync_request("client_42")

    asyncio.run(run())

    assert ws.sent[0][0] == "world_snapshot"
    assert ws.sent[0][1]["client_id"] == "client_42"
    assert ws.sent[0][1]["snapshot"]["economy"]["cash"] == 1200
    assert ws.sent[1][0] == "task_list"
    assert ws.sent[1][1]["client_id"] == "client_42"
    assert ws.sent[1][1]["tasks"][0]["task_id"] == "t1"
    assert ws.sent[1][1]["tasks"][0]["triage"]["state"] == "waiting_player"
    assert "等待玩家回复" in ws.sent[1][1]["tasks"][0]["triage"]["status_line"]
    assert ws.sent[1][1]["pending_questions"][0]["message_id"] == "msg_1"
    print("  PASS: sync_request_pushes_current_state_directly")


def test_runtime_bridge_publish_logs_batches_incrementally():
    logging_system.clear()

    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return []

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.log_entries: list[dict[str, Any]] = []

        async def send_log_entry(self, payload):
            self.log_entries.append(payload)

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge._log_publish_batch_size = 2
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    logger = logging_system.get_logger("kernel")
    logger.info("one", event="e1")
    logger.info("two", event="e2")
    logger.info("three", event="e3")

    async def run():
        await bridge._publish_logs()
        assert [entry["message"] for entry in ws.log_entries] == ["one", "two"]
        await bridge._publish_logs()

    try:
        asyncio.run(run())
    finally:
        logging_system.clear()

    assert [entry["message"] for entry in ws.log_entries] == ["one", "two", "three"]
    assert bridge._log_offset == 3
    print("  PASS: runtime_bridge_publish_logs_batches_incrementally")


def test_runtime_bridge_publish_benchmarks_sends_full_snapshot_only_when_changed():
    benchmark.clear()

    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return []

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.benchmarks: list[list[dict[str, Any]]] = []

        async def send_benchmark(self, payload):
            self.benchmarks.append(payload)

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    async def run():
        await bridge._publish_benchmarks()
        assert ws.benchmarks == []

        with benchmark.span("tool_exec", name="one"):
            time.sleep(0.001)
        await bridge._publish_benchmarks()
        assert len(ws.benchmarks) == 1
        assert len(ws.benchmarks[-1]) == 1

        await bridge._publish_benchmarks()
        assert len(ws.benchmarks) == 1

        with benchmark.span("tool_exec", name="two"):
            time.sleep(0.001)
        await bridge._publish_benchmarks()

    try:
        asyncio.run(run())
    finally:
        benchmark.clear()

    assert len(ws.benchmarks) == 2
    assert len(ws.benchmarks[-1]) == 2
    assert {record["name"] for record in ws.benchmarks[-1]} == {"one", "two"}
    assert bridge._benchmark_offset == 2
    print("  PASS: runtime_bridge_publish_benchmarks_sends_full_snapshot_only_when_changed")


def test_task_replay_request_returns_persisted_task_log():
    """Task replay should read persisted task logs and return them to one client."""

    class FakeKernel:
        def __init__(self):
            self._tasks = []

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            return []

        def get_task_agent(self, task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def runtime_state(self):
            raise AssertionError("bridge should use kernel.runtime_state()")

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import tempfile
    from pathlib import Path

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = start_persistence_session(tmpdir, session_name="unit-replay")
        try:
            task_path = Path(session_dir) / "tasks" / "t_demo.jsonl"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 123.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_demo"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.5,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Job started",
                                "event": "job_started",
                                "data": {"task_id": "t_demo", "job_id": "j_1", "expert_type": "ReconExpert"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.8,
                                "component": "task_agent",
                                "level": "DEBUG",
                                "message": "TaskAgent context snapshot",
                                "event": "context_snapshot",
                                "data": {
                                    "task_id": "t_demo",
                                    "packet": {
                                        "jobs": [{"job_id": "j_1"}],
                                        "recent_signals": [{"kind": "risk_alert"}],
                                        "recent_events": [{"event": "job_started"}],
                                        "other_active_tasks": [{"task_id": "t_other"}],
                                        "open_decisions": [{"kind": "need_target"}],
                                        "runtime_facts": {"cash": 5000, "power_drained": 100},
                                    },
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.9,
                                "component": "task_agent",
                                "level": "DEBUG",
                                "message": "TaskAgent llm input",
                                "event": "llm_input",
                                "data": {
                                    "task_id": "t_demo",
                                    "messages": [{"role": "system"}, {"role": "user"}],
                                    "tools": [{"name": "query_world"}],
                                    "attempt": 2,
                                    "wake": 7,
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 124.0,
                                "component": "task_agent",
                                "level": "INFO",
                                "message": "TaskAgent LLM call succeeded",
                                "event": "llm_succeeded",
                                "data": {
                                    "task_id": "t_demo",
                                    "tool_calls_detail": [{"name": "query_world", "arguments": "{}"}],
                                    "usage": {"prompt_tokens": 321, "completion_tokens": 45},
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 124.2,
                                "component": "expert",
                                "level": "WARN",
                                "message": "Expert signal emitted",
                                "event": "expert_signal",
                                "data": {
                                    "task_id": "t_demo",
                                    "job_id": "j_1",
                                    "signal_kind": "risk_alert",
                                    "summary": "电力不足",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 125.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task completed",
                                "event": "task_completed",
                                "data": {"task_id": "t_demo", "summary": "侦察完成，发现目标"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request("t_demo", "client_7")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent[0][0] == "task_replay"
    assert ws.sent[0][1]["client_id"] == "client_7"
    payload = ws.sent[0][1]["payload"]
    assert payload["task_id"] == "t_demo"
    assert payload["entry_count"] == 7
    assert payload["entries"][1]["data"]["job_id"] == "j_1"
    assert payload["bundle"]["summary"] == "侦察完成，发现目标"
    assert payload["bundle"]["last_transition"]["label"] == "task_completed"
    assert payload["bundle"]["timeline"][0]["label"] == "task_created"
    assert payload["bundle"]["timeline"][0]["elapsed_s"] == 0.0
    assert payload["bundle"]["timeline"][-1]["label"] == "task_completed"
    assert payload["bundle"]["blockers"][0]["message"] == "电力不足"
    assert payload["bundle"]["llm"]["rounds"] == 1
    assert payload["bundle"]["llm"]["prompt_tokens"] == 321
    assert payload["bundle"]["tools"][0]["name"] == "query_world"
    assert payload["bundle"]["experts"][0]["name"] == "ReconExpert"
    assert payload["bundle"]["signals"][0]["name"] == "risk_alert"
    assert payload["bundle"]["current_runtime"] is None
    assert payload["bundle"]["debug"]["latest_context"]["job_count"] == 1
    assert payload["bundle"]["debug"]["latest_context"]["signal_count"] == 1
    assert payload["bundle"]["debug"]["latest_context"]["runtime_fact_keys"] == ["cash", "power_drained"]
    assert payload["bundle"]["debug"]["latest_llm_input"]["message_count"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["tool_count"] == 1
    assert payload["bundle"]["debug"]["latest_llm_input"]["attempt"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["wake"] == 7
    print("  PASS: task_replay_request_returns_persisted_task_log")


def test_task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks():
    class FakeKernel:
        def __init__(self):
            self._task = type(
                "Task",
                (),
                {
                    "task_id": "t_live",
                    "raw_text": "发展经济",
                    "kind": type("K", (), {"value": "managed"})(),
                    "priority": 50,
                    "status": type("S", (), {"value": "running"})(),
                    "timestamp": 1.0,
                    "created_at": 1.0,
                    "label": "001",
                    "is_capability": True,
                },
            )()

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return [self._task]

        def jobs_for_task(self, task_id):
            return []

        def get_task_agent(self, task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {"active_tasks": {"t_live": {"status": "running"}}}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge._task_to_dict = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "task_id": "t_live",
        "triage": {"status_line": "等待能力层补前置：电厂"},
    }
    bundle = bridge._build_task_replay_bundle(
        "t_live",
        [
            {
                "timestamp": 100.0,
                "component": "kernel",
                "level": "INFO",
                "message": "Task created",
                "event": "task_created",
                "data": {"task_id": "t_live"},
            }
        ],
        runtime_state=bridge.kernel.runtime_state(),
    )
    assert bundle["summary"] == "等待能力层补前置：电厂"
    assert bundle["status_line"] == "等待能力层补前置：电厂"
    assert bundle["timeline"][0]["label"] == "task_created"
    print("  PASS: task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks")


def test_runtime_bridge_sync_runtime_uses_public_kernel_accessors():
    """Bridge sync should rely on public Kernel accessors, not private fields."""

    class FakeTask:
        def __init__(self, task_id: str, status=TaskStatus.RUNNING):
            self.task_id = task_id
            self.raw_text = "test"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = status
            self.timestamp = 123.0
            self.created_at = 100.0

    class FakeAgent:
        def __init__(self):
            self.queue = AgentQueue()
            self.config = type("Config", (), {"review_interval": 0.25})()
            self.is_suspended = False

    class FakeJob:
        def __init__(self, job_id: str):
            self.job_id = job_id
            self.task_id = "t1"
            self.expert_type = "CombatExpert"
            self.status = type("Status", (), {"value": "running"})()
            self.resources = []
            self.timestamp = 124.0
            self.config = {}

    class FakeKernel:
        def __init__(self):
            self.task = FakeTask("t1")
            self.agent = FakeAgent()
            self.job = FakeJob("j1")
            self.tasks = [self.task]
            self.jobs = [self.job]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self.tasks)

        def jobs_for_task(self, task_id):
            return [self.job] if task_id == "t1" else []

        def get_task_agent(self, task_id):
            return self.agent if task_id == "t1" else None

        def active_jobs(self):
            return list(self.jobs)

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def runtime_state(self):
            return {}

    class FakeGameLoop:
        def __init__(self):
            self.registered_agents: list[tuple[str, float]] = []
            self.unregistered_agents: list[str] = []
            self.registered_jobs: list[str] = []
            self.unregistered_jobs: list[str] = []

        def register_agent(self, task_id, queue, review_interval=10.0, *, is_suspended=None):
            del queue, is_suspended
            self.registered_agents.append((task_id, review_interval))

        def unregister_agent(self, task_id):
            self.unregistered_agents.append(task_id)

        def register_job(self, job):
            self.registered_jobs.append(job.job_id)

        def unregister_job(self, job_id):
            self.unregistered_jobs.append(job_id)

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.sync_runtime()

    assert bridge.game_loop.registered_agents == [("t1", 0.25)]
    assert bridge.game_loop.registered_jobs == ["j1"]

    bridge.kernel.task.status = TaskStatus.SUCCEEDED
    bridge.kernel.jobs = []
    bridge.sync_runtime()

    assert bridge.game_loop.unregistered_agents == ["t1"]
    assert bridge.game_loop.unregistered_jobs == ["j1"]
    print("  PASS: runtime_bridge_sync_runtime_uses_public_kernel_accessors")


# --- T12: WS throttle tests (no network needed) ---

class _TrackingWSServer(WSServer):
    """WSServer subclass that records broadcast calls instead of sending over network."""

    def __init__(self):
        super().__init__()
        self.broadcast_calls: list[tuple[str, dict]] = []

    async def broadcast(self, msg_type: str, data: dict[str, Any]) -> None:
        self.broadcast_calls.append((msg_type, data))


def test_world_snapshot_throttled():
    """Two rapid send_world_snapshot calls → only the first is broadcast."""
    server = _TrackingWSServer()

    async def run():
        await server.send_world_snapshot({"cash": 1000})
        await server.send_world_snapshot({"cash": 1001})  # within throttle window

    asyncio.run(run())
    ws_calls = [t for t, _ in server.broadcast_calls if t == "world_snapshot"]
    assert len(ws_calls) == 1, f"Expected 1 world_snapshot broadcast, got {len(ws_calls)}"
    print("  PASS: world_snapshot_throttled")


def test_task_list_throttled():
    """Two rapid send_task_list calls → only the first is broadcast."""
    server = _TrackingWSServer()

    async def run():
        await server.send_task_list([{"task_id": "t1"}])
        await server.send_task_list([{"task_id": "t1", "status": "done"}])  # within throttle window

    asyncio.run(run())
    tl_calls = [t for t, _ in server.broadcast_calls if t == "task_list"]
    assert len(tl_calls) == 1, f"Expected 1 task_list broadcast, got {len(tl_calls)}"
    print("  PASS: task_list_throttled")


def test_world_snapshot_passes_after_interval():
    """send_world_snapshot passes through again once throttle interval has elapsed."""
    server = _TrackingWSServer()

    async def run():
        await server.send_world_snapshot({"cash": 1000})
        # Simulate elapsed time by rewinding the timestamp
        server._last_world_snapshot_at -= _THROTTLE_INTERVAL
        await server.send_world_snapshot({"cash": 2000})

    asyncio.run(run())
    ws_calls = [t for t, _ in server.broadcast_calls if t == "world_snapshot"]
    assert len(ws_calls) == 2, f"Expected 2 world_snapshot broadcasts, got {len(ws_calls)}"
    print("  PASS: world_snapshot_passes_after_interval")


def test_other_messages_not_throttled():
    """send_log_entry and send_task_update are never throttled."""
    server = _TrackingWSServer()

    async def run():
        for _ in range(5):
            await server.send_log_entry({"msg": "tick"})
            await server.send_task_update({"task_id": "t1", "status": "running"})

    asyncio.run(run())
    log_calls = [t for t, _ in server.broadcast_calls if t == "log_entry"]
    task_calls = [t for t, _ in server.broadcast_calls if t == "task_update"]
    assert len(log_calls) == 5, f"Expected 5 log_entry, got {len(log_calls)}"
    assert len(task_calls) == 5, f"Expected 5 task_update, got {len(task_calls)}"
    print("  PASS: other_messages_not_throttled")


def test_broadcast_fanout_is_concurrent():
    """A slow client must not serialize broadcast fanout across other clients."""
    server = WSServer()
    starts: dict[str, float] = {}

    class _SlowWS:
        def __init__(self, name: str, delay_s: float) -> None:
            self.name = name
            self.delay_s = delay_s

        async def send_str(self, payload: str) -> None:
            del payload
            starts[self.name] = time.perf_counter()
            await asyncio.sleep(self.delay_s)

    async def run():
        server._clients = {
            "c1": _SlowWS("c1", 0.05),  # type: ignore[assignment]
            "c2": _SlowWS("c2", 0.05),  # type: ignore[assignment]
        }
        await server.broadcast("log_entry", {"msg": "tick"})

    asyncio.run(run())
    assert len(starts) == 2
    assert abs(starts["c1"] - starts["c2"]) < 0.02, starts
    print("  PASS: broadcast_fanout_is_concurrent")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running WS + review_interval tests...\n")

    # 1.8
    test_review_interval_triggers_wake()
    test_register_unregister_agent()
    test_multiple_agents_different_intervals()
    test_suspended_agent_skips_periodic_review()

    # 1.6
    test_ws_server_start_stop()
    test_ws_client_connect_and_inbound()
    test_ws_broadcast_outbound()
    test_ws_multi_client()
    test_ws_query_response_envelope()
    test_ws_send_to_client_targets_single_client()
    test_sync_request_pushes_current_state_directly()
    test_runtime_bridge_publish_logs_batches_incrementally()
    test_runtime_bridge_publish_benchmarks_sends_full_snapshot_only_when_changed()
    test_task_replay_request_returns_persisted_task_log()
    test_task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks()
    test_runtime_bridge_sync_runtime_uses_public_kernel_accessors()
    test_world_snapshot_throttled()
    test_task_list_throttled()
    test_world_snapshot_passes_after_interval()
    test_other_messages_not_throttled()
    test_broadcast_fanout_is_concurrent()

    print("\nAll WS + review_interval tests passed!")
