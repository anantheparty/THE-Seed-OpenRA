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
from task_agent.queue import AgentQueue
from game_loop import GameLoop, GameLoopConfig
from ws_server import WSServer, WSServerConfig


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

    server = WSServer(
        config=WSServerConfig(host="127.0.0.1", port=18766),
        inbound_handler=TestHandler(),
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

        await server.stop()

    asyncio.run(run())

    assert "探索地图" in received_commands
    assert "cancel:t1" in received_commands
    assert "mode:debug" in received_commands
    assert "restart:baseline.orasav" in received_commands
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

    print(f"\nAll 8 tests passed!")
