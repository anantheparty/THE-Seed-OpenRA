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

from models import Event, EventType
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

        await server.stop()

    asyncio.run(run())

    assert "探索地图" in received_commands
    assert "cancel:t1" in received_commands
    assert "mode:debug" in received_commands
    assert "restart:baseline.orasav" in received_commands
    assert any(item.startswith("clear:client_") for item in received_commands)
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
            self._task_runtimes = {}
            self._jobs = {}

        def list_pending_questions(self):
            return [{"message_id": "msg_1", "task_id": "t1", "options": ["是", "否"]}]

        def list_tasks(self):
            return [FakeTask("t1", "建造电厂")]

        def jobs_for_task(self, task_id):
            return [FakeJob("j1", "EconomyExpert")] if task_id == "t1" else []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

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
    assert ws.sent[1][1]["pending_questions"][0]["message_id"] == "msg_1"
    print("  PASS: sync_request_pushes_current_state_directly")


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


# --- Run all tests ---

if __name__ == "__main__":
    print("Running WS + review_interval tests...\n")

    # 1.8
    test_review_interval_triggers_wake()
    test_register_unregister_agent()
    test_multiple_agents_different_intervals()

    # 1.6
    test_ws_server_start_stop()
    test_ws_client_connect_and_inbound()
    test_ws_broadcast_outbound()
    test_ws_multi_client()
    test_ws_query_response_envelope()
    test_ws_send_to_client_targets_single_client()
    test_sync_request_pushes_current_state_directly()
    test_world_snapshot_throttled()
    test_task_list_throttled()
    test_world_snapshot_passes_after_interval()
    test_other_messages_not_throttled()

    print(f"\nAll 14 tests passed!")
