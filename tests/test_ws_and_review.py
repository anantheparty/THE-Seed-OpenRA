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
import pytest
from logging_system import start_persistence_session, stop_persistence_session

from models import Event, EventType, TaskMessage, TaskMessageType, TaskStatus
from main import RuntimeBridge, TASK_REPLAY_RAW_ENTRY_LIMIT
from session_browser import default_session_dir
from task_replay import build_live_task_replay_bundle, build_task_replay_bundle
from task_triage import build_live_task_payload
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

@pytest.mark.contract
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

        async def on_session_select(self, session_dir, client_id):
            received_commands.append(f"session:{session_dir}:{client_id}")

        async def on_task_replay_request(self, task_id, client_id, session_dir=None, include_entries=True):
            received_commands.append(f"replay:{task_id}:{session_dir}:{include_entries}:{client_id}")

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

                await ws.send_str(json.dumps({"type": "session_select", "session_dir": "/tmp/demo-session"}))
                await asyncio.sleep(0.05)

                await ws.send_str(json.dumps({"type": "task_replay_request", "task_id": "t9", "include_entries": False}))
                await asyncio.sleep(0.05)

        await server.stop()

    asyncio.run(run())

    assert "探索地图" in received_commands
    assert "cancel:t1" in received_commands
    assert "mode:debug" in received_commands
    assert "restart:baseline.orasav" in received_commands
    assert any(item.startswith("clear:client_") for item in received_commands)
    assert any(item.startswith("session:/tmp/demo-session:client_") for item in received_commands)
    assert any(item.startswith("replay:t9:None:False:client_") for item in received_commands)
    assert handler.session_clears == 1
    print("  PASS: ws_client_connect_and_inbound")


def test_ws_rejects_invalid_inbound_payloads():
    received_commands: list[str] = []

    class TestHandler:
        async def on_command_submit(self, text, client_id):
            received_commands.append(f"submit:{text}:{client_id}")

        async def on_command_cancel(self, task_id, client_id):
            received_commands.append(f"cancel:{task_id}:{client_id}")

        async def on_mode_switch(self, mode, client_id):
            received_commands.append(f"mode:{mode}:{client_id}")

        async def on_question_reply(self, message_id, task_id, answer, client_id):
            received_commands.append(f"reply:{message_id}:{task_id}:{answer}:{client_id}")

        async def on_game_restart(self, save_path, client_id):
            received_commands.append(f"restart:{save_path}:{client_id}")

        async def on_sync_request(self, client_id):
            received_commands.append(f"sync:{client_id}")

        async def on_session_clear(self, client_id):
            received_commands.append(f"clear:{client_id}")

        async def on_session_select(self, session_dir, client_id):
            received_commands.append(f"session:{session_dir}:{client_id}")

        async def on_task_replay_request(self, task_id, client_id, session_dir=None, include_entries=True):
            received_commands.append(f"replay:{task_id}:{session_dir}:{include_entries}:{client_id}")

    server = WSServer(
        config=WSServerConfig(host="127.0.0.1", port=18769),
        inbound_handler=TestHandler(),
    )
    responses: list[dict[str, Any]] = []

    async def run():
        await server.start()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:18769/ws") as ws:
                await ws.send_str(json.dumps({"type": "command_cancel", "task_id": ""}))
                responses.append(json.loads((await asyncio.wait_for(ws.receive(), timeout=1.0)).data))
                await ws.send_str(json.dumps({"type": "task_replay_request"}))
                responses.append(json.loads((await asyncio.wait_for(ws.receive(), timeout=1.0)).data))
        await server.stop()

    asyncio.run(run())

    assert received_commands == []
    assert responses[0]["type"] == "error"
    assert responses[0]["message"] == "Invalid command_cancel: missing task_id"
    assert responses[0]["code"] == "INVALID_MESSAGE"
    assert responses[1]["message"] == "Invalid task_replay_request: missing task_id"
    print("  PASS: ws_rejects_invalid_inbound_payloads")


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


@pytest.mark.contract
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

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
            return {
                "faction": "allied",
                "capability_truth_blocker": "faction_roster_unsupported",
            }

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

        async def send_session_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_task_catalog", {"client_id": client_id, "payload": payload}))

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
    assert ws.sent[0][1]["snapshot"]["player_faction"] == "allied"
    assert ws.sent[0][1]["snapshot"]["capability_truth_blocker"] == "faction_roster_unsupported"
    assert ws.sent[1][0] == "task_list"
    assert ws.sent[1][1]["client_id"] == "client_42"
    assert ws.sent[1][1]["tasks"][0]["task_id"] == "t1"
    assert ws.sent[1][1]["tasks"][0]["triage"]["state"] == "waiting_player"
    assert "等待玩家回复" in ws.sent[1][1]["tasks"][0]["triage"]["status_line"]
    assert ws.sent[1][1]["pending_questions"][0]["message_id"] == "msg_1"
    assert ws.sent[2][0] == "session_catalog"
    assert ws.sent[3][0] == "session_task_catalog"
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
    bridge._publisher.log_publish_batch_size = 2
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    logger = logging_system.get_logger("kernel")
    logger.info("one", event="e1")
    logger.info("two", event="e2")
    logger.info("three", event="e3")

    async def run():
        await bridge._publisher.publish_logs()
        assert [entry["message"] for entry in ws.log_entries] == ["one", "two"]
        await bridge._publisher.publish_logs()

    try:
        asyncio.run(run())
    finally:
        logging_system.clear()

    assert [entry["message"] for entry in ws.log_entries] == ["one", "two", "three"]
    assert bridge._publisher.log_offset == 3
    print("  PASS: runtime_bridge_publish_logs_batches_incrementally")


def test_runtime_bridge_task_update_fingerprint_tracks_active_group_size():
    class FakeTask:
        def __init__(self):
            self.task_id = "t_group"
            self.raw_text = "推进前线"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 10.0
            self.created_at = 10.0

    class FakeKernel:
        def __init__(self):
            self.task = FakeTask()

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return [self.task]

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
            self.updates: list[dict[str, Any]] = []

        async def send_task_update(self, payload):
            self.updates.append(payload)

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    group_sizes = iter([1, 2])
    bridge._task_to_dict = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "task_id": "t_group",
        "status": "running",
        "priority": 50,
        "timestamp": 10.0,
        "raw_text": "推进前线",
        "jobs": [],
        "triage": {
            "state": "running",
            "phase": "task_active",
            "status_line": "执行中",
            "active_group_size": next(group_sizes),
        },
    }

    async def run():
        await bridge._publisher.publish_task_updates()
        await bridge._publisher.publish_task_updates()

    asyncio.run(run())

    assert len(ws.updates) == 2
    assert ws.updates[0]["triage"]["active_group_size"] == 1
    assert ws.updates[1]["triage"]["active_group_size"] == 2
    print("  PASS: runtime_bridge_task_update_fingerprint_tracks_active_group_size")


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
            self.benchmarks: list[dict[str, Any]] = []

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
        await bridge._publisher.publish_benchmarks()
        assert ws.benchmarks == []

        with benchmark.span("tool_exec", name="one"):
            time.sleep(0.001)
        await bridge._publisher.publish_benchmarks()
        assert len(ws.benchmarks) == 1
        assert ws.benchmarks[-1]["replace"] is False
        assert len(ws.benchmarks[-1]["records"]) == 1

        await bridge._publisher.publish_benchmarks()
        assert len(ws.benchmarks) == 1

        with benchmark.span("tool_exec", name="two"):
            time.sleep(0.001)
        await bridge._publisher.publish_benchmarks()

    try:
        asyncio.run(run())
    finally:
        benchmark.clear()

    assert len(ws.benchmarks) == 2
    assert len(ws.benchmarks[-1]["records"]) == 1
    assert ws.benchmarks[-1]["records"][0]["name"] == "two"
    assert bridge._publisher.benchmark_offset == 2
    print("  PASS: runtime_bridge_publish_benchmarks_sends_full_snapshot_only_when_changed")


def test_runtime_bridge_replay_history_sends_replace_benchmark_snapshot():
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
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with benchmark.span("tool_exec", name="one"):
        time.sleep(0.001)
    with benchmark.span("tool_exec", name="two"):
        time.sleep(0.001)

    try:
        asyncio.run(bridge._publisher.replay_history("client_bench"))
    finally:
        benchmark.clear()

    benchmark_msgs = [item for item in ws.sent if item[0] == "benchmark"]
    assert len(benchmark_msgs) == 1
    assert benchmark_msgs[0][1]["client_id"] == "client_bench"
    assert benchmark_msgs[0][1]["data"]["replace"] is True
    assert len(benchmark_msgs[0][1]["data"]["records"]) == 2
    print("  PASS: runtime_bridge_replay_history_sends_replace_benchmark_snapshot")


def test_runtime_bridge_replay_history_preserves_task_message_type():
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
            return [
                TaskMessage(
                    message_id="m_info",
                    task_id="t1",
                    type=TaskMessageType.TASK_INFO,
                    content="正在补前置",
                    timestamp=10.0,
                ),
                TaskMessage(
                    message_id="m_question",
                    task_id="t1",
                    type=TaskMessageType.TASK_QUESTION,
                    content="是否继续？",
                    options=["是", "否"],
                    timestamp=11.0,
                ),
            ]

        def list_player_notifications(self):
            return [{"type": "info", "content": "普通通知", "timestamp": 12.0}]

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
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    asyncio.run(bridge._publisher.replay_history("client_msg"))

    task_messages = [item for item in ws.sent if item[0] == "task_message"]
    notifications = [item for item in ws.sent if item[0] == "player_notification"]

    assert len(task_messages) == 1
    assert task_messages[0][1]["client_id"] == "client_msg"
    assert task_messages[0][1]["data"]["type"] == "task_info"
    assert task_messages[0][1]["data"]["content"] == "正在补前置"
    assert len(notifications) == 1
    assert notifications[0][1]["data"]["content"] == "普通通知"
    assert all(item[1]["data"].get("message_id") != "m_question" for item in ws.sent)
    print("  PASS: runtime_bridge_replay_history_preserves_task_message_type")


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
                                        "runtime_facts": {
                                            "cash": 5000,
                                            "power_drained": 100,
                                            "unfulfilled_requests": [
                                                {
                                                    "request_id": "req_1",
                                                    "reservation_id": "res_1",
                                                    "task_id": "t_demo",
                                                    "task_label": "007",
                                                    "unit_type": "3tnk",
                                                    "queue_type": "Vehicle",
                                                    "count": 2,
                                                    "fulfilled": 1,
                                                    "remaining_count": 1,
                                                    "blocking": True,
                                                    "min_start_package": 1,
                                                    "bootstrap_job_id": "j_boot",
                                                    "bootstrap_task_id": "t_cap",
                                                    "reservation_status": "partial",
                                                    "reason": "bootstrap_in_progress",
                                                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 4,
                                                    "world_sync_failure_threshold": 3,
                                                    "disabled_producers": ["weap"],
                                                }
                                            ],
                                            "unit_reservations": [
                                                {
                                                    "reservation_id": "res_1",
                                                    "request_id": "req_1",
                                                    "task_id": "t_demo",
                                                    "task_label": "007",
                                                    "unit_type": "3tnk",
                                                    "queue_type": "Vehicle",
                                                    "count": 2,
                                                    "remaining_count": 1,
                                                    "status": "partial",
                                                    "blocking": True,
                                                    "min_start_package": 1,
                                                    "start_released": False,
                                                    "bootstrap_job_id": "j_boot",
                                                    "bootstrap_task_id": "t_cap",
                                                    "reason": "bootstrap_in_progress",
                                                    "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 5,
                                                    "world_sync_failure_threshold": 3,
                                                    "assigned_actor_ids": [10],
                                                    "produced_actor_ids": [11],
                                                }
                                            ],
                                        },
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
                                    "model": "demo-model",
                                    "response_text": "先查询世界状态",
                                    "reasoning_content": "需要先确认当前侦察态势",
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
                await bridge.on_task_replay_request("t_demo", "client_7", session_dir=str(session_dir))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent[0][0] == "task_replay"
    assert ws.sent[0][1]["client_id"] == "client_7"
    payload = ws.sent[0][1]["payload"]
    assert payload["task_id"] == "t_demo"
    assert payload["session_dir"] == str(session_dir)
    assert payload["entry_count"] == 7
    assert payload["raw_entry_count"] == 7
    assert payload["raw_entries_truncated"] is False
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
    assert payload["bundle"]["debug"]["latest_context"]["runtime_fact_keys"] == [
        "cash",
        "power_drained",
        "unfulfilled_requests",
        "unit_reservations",
    ]
    assert payload["bundle"]["debug"]["latest_llm_input"]["message_count"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["tool_count"] == 1
    assert payload["bundle"]["debug"]["latest_llm_input"]["attempt"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["wake"] == 7
    assert "packet" not in payload["bundle"]["debug"]["latest_context"]
    assert "messages" not in payload["bundle"]["debug"]["latest_llm_input"]
    assert "tools" not in payload["bundle"]["debug"]["latest_llm_input"]
    assert len(payload["bundle"]["lifecycle_events"]) == 7
    assert payload["bundle"]["lifecycle_events"][1]["job_id"] == "j_1"
    assert payload["bundle"]["expert_runs"][0]["job_id"] == "j_1"
    assert payload["bundle"]["expert_runs"][0]["latest_signal"]["label"] == "expert:risk_alert"
    assert payload["bundle"]["llm_turns"][0]["wake"] == 7
    assert payload["bundle"]["llm_turns"][0]["attempt"] == 2
    assert payload["bundle"]["llm_turns"][0]["response_text"] == "先查询世界状态"
    assert payload["bundle"]["llm_turns"][0]["reasoning_content"] == "需要先确认当前侦察态势"
    assert payload["bundle"]["llm_turns"][0]["input_messages"][0]["role"] == "system"
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["request_id"] == "req_1"
    assert (
        payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_last_error"]
        == "actors:COMMAND_EXECUTION_ERROR"
    )
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_consecutive_failures"] == 4
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_failure_threshold"] == 3
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["reservation_id"] == "res_1"
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["assigned_count"] == 1
    assert (
        payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_last_error"]
        == "economy:COMMAND_EXECUTION_ERROR"
    )
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_consecutive_failures"] == 5
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_request_returns_persisted_task_log")


def test_task_replay_request_limits_raw_entries_payload():
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
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import asyncio
    import json
    import tempfile
    from pathlib import Path

    ws = FakeWS()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmp_dir:
        session_dir = Path(tmp_dir) / "session-20260411T021500Z"
        task_dir = session_dir / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        start_persistence_session(tmp_dir)
        try:
            records = [
                {
                    "timestamp": float(100 + index),
                    "component": "kernel",
                    "level": "INFO",
                    "message": f"event-{index}",
                    "event": "task_info",
                    "data": {"task_id": "t_demo", "index": index},
                }
                for index in range(TASK_REPLAY_RAW_ENTRY_LIMIT + 25)
            ]
            (task_dir / "t_demo.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request("t_demo", "client_7", session_dir=str(session_dir))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    assert payload["entry_count"] == TASK_REPLAY_RAW_ENTRY_LIMIT + 25
    assert payload["raw_entry_count"] == TASK_REPLAY_RAW_ENTRY_LIMIT
    assert payload["raw_entries_truncated"] is True
    assert len(payload["entries"]) == TASK_REPLAY_RAW_ENTRY_LIMIT
    assert payload["entries"][0]["data"]["index"] == 25
    assert payload["entries"][-1]["data"]["index"] == TASK_REPLAY_RAW_ENTRY_LIMIT + 24
    print("  PASS: task_replay_request_limits_raw_entries_payload")


def test_task_replay_request_can_skip_raw_entries_until_expanded():
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
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import asyncio
    import json
    import tempfile
    from pathlib import Path

    ws = FakeWS()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmp_dir:
        session_dir = Path(tmp_dir) / "session-20260411T021500Z"
        task_dir = session_dir / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        start_persistence_session(tmp_dir)
        try:
            records = [
                {
                    "timestamp": float(100 + index),
                    "component": "kernel",
                    "level": "INFO",
                    "message": f"event-{index}",
                    "event": "task_info",
                    "data": {"task_id": "t_demo", "index": index},
                }
                for index in range(6)
            ]
            (task_dir / "t_demo.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request(
                    "t_demo",
                    "client_7",
                    session_dir=str(session_dir),
                    include_entries=False,
                )

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    assert payload["entry_count"] == 6
    assert payload["raw_entry_count"] == 6
    assert payload["raw_entries_truncated"] is False
    assert payload["raw_entries_included"] is False
    assert payload["entries"] == []
    assert payload["bundle"]["entry_count"] == 6
    print("  PASS: task_replay_request_can_skip_raw_entries_until_expanded")


def test_session_select_returns_catalog_and_task_catalog():
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
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_session_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_task_catalog", {"client_id": client_id, "payload": payload}))

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
        session_dir = start_persistence_session(tmpdir, session_name="session-select")
        try:
            task_path = Path(session_dir) / "tasks" / "t_select.jsonl"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                json.dumps(
                    {
                        "timestamp": 10.0,
                        "component": "kernel",
                        "level": "INFO",
                        "message": "Task created",
                        "event": "task_created",
                        "data": {"task_id": "t_select", "raw_text": "探索地图", "priority": 40},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            bridge.log_session_root = tmpdir

            async def run():
                await bridge.on_session_select(str(session_dir), "client_9")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent[0][0] == "session_catalog"
    assert ws.sent[0][1]["payload"]["selected_session_dir"] == str(session_dir)
    assert ws.sent[1][0] == "session_task_catalog"
    assert ws.sent[1][1]["payload"]["tasks"][0]["task_id"] == "t_select"
    assert ws.sent[1][1]["payload"]["tasks"][0]["raw_text"] == "探索地图"
    print("  PASS: session_select_returns_catalog_and_task_catalog")


def test_session_clear_rotates_persisted_log_session():
    class FakeKernel:
        def __init__(self):
            self.reset_calls = 0

        def reset_session(self):
            self.reset_calls += 1

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
        def __init__(self):
            self.stale = True
            self.reset_calls = 0

        def world_summary(self):
            return {"stale": self.stale}

        def reset_snapshot(self):
            self.reset_calls += 1
            self.stale = False

    class FakeGameLoop:
        def __init__(self):
            self.reset_runtime_calls = 0

        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

        def reset_runtime_state(self):
            self.reset_runtime_calls += 1

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.cleared = 0
            self.catalogs: list[dict[str, Any]] = []
            self.task_catalogs: list[dict[str, Any]] = []
            self.world_snapshots: list[dict[str, Any]] = []

        async def send_session_cleared(self):
            self.cleared += 1

        async def send_session_catalog_to_client(self, client_id, payload):
            self.catalogs.append({"client_id": client_id, "payload": payload})

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.task_catalogs.append({"client_id": client_id, "payload": payload})

        async def send_world_snapshot(self, payload):
            self.world_snapshots.append(dict(payload))

        async def send_task_list(self, tasks, pending_questions=None):
            del tasks, pending_questions

        async def send_log_entry(self, payload):
            del payload

        async def send_player_notification(self, payload):
            del payload

    import tempfile
    old_session_dir = None
    new_session_dir = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_session_dir = start_persistence_session(tmpdir, session_name="before-clear")
            world_model = FakeWorldModel()
            game_loop = FakeGameLoop()
            bridge = RuntimeBridge(
                kernel=FakeKernel(),
                world_model=world_model,
                game_loop=game_loop,
            )
            bridge.log_session_root = tmpdir
            ws = FakeWS()
            bridge.attach_ws_server(ws)

            asyncio.run(bridge.on_session_clear("client_clear"))

            new_session_dir = logging_system.current_session_dir()
            assert new_session_dir is not None
            assert new_session_dir != old_session_dir
            assert logging_system.latest_session_dir(tmpdir) == new_session_dir
            assert bridge.kernel.reset_calls == 1
            assert world_model.reset_calls == 1
            assert game_loop.reset_runtime_calls == 1
            assert ws.cleared == 1
            assert ws.catalogs[0]["client_id"] == "client_clear"
            assert ws.catalogs[0]["payload"]["selected_session_dir"] == str(new_session_dir)
            assert ws.task_catalogs[0]["payload"]["session_dir"] == str(new_session_dir)
            assert ws.world_snapshots[-1]["stale"] is False
    finally:
        stop_persistence_session()

    assert old_session_dir is not None
    assert new_session_dir is not None
    print("  PASS: session_clear_rotates_persisted_log_session")


def test_default_session_dir_ignores_current_session_from_other_root():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        root_a = Path(tmpdir) / "logs-a"
        root_b = Path(tmpdir) / "logs-b"
        session_a = start_persistence_session(root_a, session_name="root-a")
        try:
            session_b = start_persistence_session(root_b, session_name="root-b")
            assert logging_system.current_session_dir() == session_b
            assert default_session_dir(str(root_a)) == session_a
        finally:
            stop_persistence_session()

    print("  PASS: default_session_dir_ignores_current_session_from_other_root")


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
    bundle = build_live_task_replay_bundle(
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
        tasks=bridge.kernel.list_tasks(),
        jobs_for_task=bridge.kernel.jobs_for_task,
        task_payload_builder=bridge._task_to_dict,
        compute_runtime_facts=getattr(bridge.world_model, "compute_runtime_facts", None),
    )
    assert bundle["summary"] == "等待能力层补前置：电厂"
    assert bundle["status_line"] == "等待能力层补前置：电厂"
    assert bundle["timeline"][0]["label"] == "task_created"
    print("  PASS: task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks")


def test_build_live_task_payload_uses_task_specific_message_lookup():
    class FakeTask:
        task_id = "t_demo"
        raw_text = "test"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = False

    class FakeMessage:
        def __init__(self, task_id: str, content: str):
            self.task_id = task_id
            self.content = content
            self.type = TaskMessageType.TASK_WARNING

    calls: list[tuple[str, ...]] = []

    def list_pending_questions():
        return []

    def list_task_messages(task_id: str):
        calls.append((task_id,))
        return [FakeMessage(task_id, "warn")]

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=list_pending_questions,
        list_task_messages=list_task_messages,
        world_stale=False,
        log_session_dir=None,
    )

    assert calls == [("t_demo",)]
    assert payload["task_id"] == "t_demo"
    assert payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_uses_task_specific_message_lookup")


def test_build_live_task_payload_uses_latest_info_when_no_other_triage_signal():
    class FakeTask:
        task_id = "t_info"
        raw_text = "test"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "002"
        is_capability = False

    class FakeMessage:
        def __init__(self, task_id: str, content: str):
            self.task_id = task_id
            self.content = content
            self.type = TaskMessageType.TASK_INFO

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [FakeMessage(task_id, "缺少战车工厂，等待能力层补前置")],
        world_stale=False,
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "running"
    assert payload["triage"]["status_line"] == "缺少战车工厂，等待能力层补前置"
    assert payload["triage"]["waiting_reason"] == ""
    print("  PASS: build_live_task_payload_uses_latest_info_when_no_other_triage_signal")


def test_build_live_task_payload_surfaces_world_sync_failure_detail():
    class FakeTask:
        task_id = "t_sync"
        raw_text = "展开基地车"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "003"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_sync={
            "stale": True,
            "consecutive_failures": 4,
            "failure_threshold": 3,
            "last_error": "actors:COMMAND_EXECUTION_ERROR",
        },
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "degraded"
    assert payload["triage"]["world_stale"] is True
    assert payload["triage"]["world_sync_failures"] == 4
    assert payload["triage"]["world_sync_failure_threshold"] == 3
    assert payload["triage"]["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert "failures=4/3" in payload["triage"]["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_surfaces_world_sync_failure_detail")


def test_build_live_task_payload_capability_triage_surfaces_blocker_detail():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "发展科技"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_other",
                    "task_label": "008",
                    "category": "vehicle",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "猛犸坦克",
                    "reason": "missing_prerequisite",
                    "prerequisites": ["fix", "stek", "weap"],
                }
            ],
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "dispatch",
                "blocker": "missing_prerequisite",
                "pending_request_count": 1,
                "blocking_request_count": 1,
                "prerequisite_gap_count": 1,
            },
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    status_line = payload["triage"]["status_line"]
    assert payload["triage"]["state"] == "running"
    assert "blocker=缺少前置建筑" in status_line
    assert "猛犸坦克 <- 维修厂 + 科技中心 + 战车工厂" in status_line
    assert payload["triage"]["blocking_reason"] == "missing_prerequisite"
    print("  PASS: build_live_task_payload_capability_triage_surfaces_blocker_detail")


def test_build_live_task_payload_capability_triage_surfaces_fulfilling_detail():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "补兵"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "fulfilling",
                "start_released_request_count": 1,
                "reinforcement_request_count": 1,
            },
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "task_id": "t_cap",
                    "unit_type": "3tnk",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "partial",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    status_line = payload["triage"]["status_line"]
    assert "ready=1" in status_line
    assert "reinforce=1" in status_line
    assert "重坦×2 (partial)" in status_line
    print("  PASS: build_live_task_payload_capability_triage_surfaces_fulfilling_detail")


def test_build_live_task_payload_capability_triage_surfaces_runtime_truth_blocker():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "发展科技"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "idle",
            },
        },
        runtime_facts={
            "faction": "allied",
            "capability_truth_blocker": "faction_roster_unsupported",
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "blocked"
    assert triage["blocking_reason"] == "faction_roster_unsupported"
    assert triage["waiting_reason"] == "faction_roster_unsupported"
    assert "能力处理中：真值受限" in triage["status_line"]
    assert "blocker=阵营能力真值未覆盖" in triage["status_line"]
    assert "faction=allied demo capability roster 未覆盖" in triage["status_line"]
    print("  PASS: build_live_task_payload_capability_triage_surfaces_runtime_truth_blocker")


@pytest.mark.parametrize(
    ("blocker", "count_field", "expected"),
    [
        ("world_sync_stale", "world_sync_stale_count", "等待世界同步恢复"),
        ("deploy_required", "deploy_required_count", "等待展开基地车"),
        ("disabled_prerequisite", "disabled_prerequisite_count", "前置建筑离线"),
        ("low_power", "low_power_count", "低电受阻"),
        ("queue_blocked", "queue_blocked_count", "队列阻塞"),
        ("insufficient_funds", "insufficient_funds_count", "资金不足"),
    ],
)
def test_build_live_task_payload_capability_triage_humanizes_additional_blockers(
    blocker: str,
    count_field: str,
    expected: str,
):
    class FakeTask:
        task_id = "t_cap"
        raw_text = "能力任务"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "dispatch",
                "blocker": blocker,
                count_field: 1,
            },
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "running"
    assert expected in payload["triage"]["status_line"]
    assert payload["triage"]["blocking_reason"] == blocker
    print(f"  PASS: build_live_task_payload_capability_triage_humanizes_additional_blockers[{blocker}]")


def test_build_live_task_payload_surfaces_task_specific_reservation_blocker_detail():
    class FakeTask:
        task_id = "t_recon"
        raw_text = "探索地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 60
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_recon": {"label": "004"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_recon",
                    "task_label": "004",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "fulfilled": 0,
                    "remaining_count": 2,
                    "reason": "missing_prerequisite",
                    "prerequisites": ["fix", "weap"],
                }
            ],
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "request_id": "req_1",
                    "task_id": "t_recon",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "pending",
                    "reason": "missing_prerequisite",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "blocked"
    assert triage["phase"] == "blocked"
    assert triage["waiting_reason"] == "missing_prerequisite"
    assert triage["blocking_reason"] == "missing_prerequisite"
    assert triage["reservation_preview"] == "重坦 × 2 · 缺少前置"
    assert "等待能力模块补前置：重坦 × 2" in triage["status_line"]
    assert "重坦 × 2 <- 维修厂 + 战车工厂" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_task_specific_reservation_blocker_detail")


def test_build_live_task_payload_marks_request_dispatch_without_fake_blocker():
    class FakeTask:
        task_id = "t_attack"
        raw_text = "进攻"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 60
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "005"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_attack": {"label": "005"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_2",
                    "task_id": "t_attack",
                    "task_label": "005",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "remaining_count": 1,
                    "reason": "waiting_dispatch",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "running"
    assert triage["phase"] == "dispatch"
    assert triage["waiting_reason"] == "waiting_dispatch"
    assert triage["blocking_reason"] == ""
    assert triage["reservation_preview"] == "步兵 × 1 · 待分发"
    assert "等待能力模块分发单位：步兵 × 1" in triage["status_line"]
    print("  PASS: build_live_task_payload_marks_request_dispatch_without_fake_blocker")


def test_task_replay_bundle_counts_tools_once_and_keeps_separated_blockers():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.2,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "tool_calls_detail": [{"name": "query_world", "arguments": "{}"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        },
        {
            "timestamp": 10.3,
            "component": "task_agent",
            "level": "INFO",
            "message": "Executing tool call",
            "event": "tool_execute",
            "data": {
                "task_id": "t_demo",
                "tool": "query_world",
                "tool_call_id": "call_1",
            },
        },
        {
            "timestamp": 10.4,
            "component": "task_agent",
            "level": "INFO",
            "message": "Tool call completed",
            "event": "tool_execute_completed",
            "data": {
                "task_id": "t_demo",
                "tool": "query_world",
                "tool_call_id": "call_1",
            },
        },
        {
            "timestamp": 10.5,
            "component": "expert",
            "level": "WARN",
            "message": "Expert signal emitted",
            "event": "expert_signal",
            "data": {
                "task_id": "t_demo",
                "job_id": "j_1",
                "signal_kind": "risk_alert",
                "summary": "等待电厂",
            },
        },
        {
            "timestamp": 10.6,
            "component": "kernel",
            "level": "INFO",
            "message": "Task still running",
            "event": "task_info",
            "data": {"task_id": "t_demo", "summary": "继续检查前置条件"},
        },
        {
            "timestamp": 10.7,
            "component": "expert",
            "level": "WARN",
            "message": "Expert signal emitted",
            "event": "expert_signal",
            "data": {
                "task_id": "t_demo",
                "job_id": "j_1",
                "signal_kind": "risk_alert",
                "summary": "等待电厂",
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    assert bundle["llm"]["rounds"] == 1
    assert bundle["tools"] == [{"name": "query_world", "count": 1}]
    assert [item["message"] for item in bundle["blockers"]] == ["等待电厂", "等待电厂"]
    print("  PASS: task_replay_bundle_counts_tools_once_and_keeps_separated_blockers")


def test_task_replay_bundle_preserves_world_sync_detail_in_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 4,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                        "unit_reservations": [
                            {
                                "reservation_id": "res_1",
                                "request_id": "req_1",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "remaining_count": 1,
                                "status": "pending",
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 5,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    request = bundle["unit_pipeline"]["unfulfilled_requests"][0]
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert request["world_sync_last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert request["world_sync_consecutive_failures"] == 4
    assert request["world_sync_failure_threshold"] == 3
    assert reservation["world_sync_last_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 5
    assert reservation["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_bundle_preserves_world_sync_detail_in_unit_pipeline")


def test_task_replay_bundle_derives_replay_triage_from_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "reservation_id": "res_1",
                                "task_id": "t_demo",
                                "unit_type": "4tnk",
                                "queue_type": "Vehicle",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "missing_prerequisite",
                                "prerequisites": ["fix", "stek", "weap"],
                            }
                        ],
                        "unit_reservations": [
                            {
                                "reservation_id": "res_1",
                                "request_id": "req_1",
                                "task_id": "t_demo",
                                "unit_type": "4tnk",
                                "queue_type": "Vehicle",
                                "count": 1,
                                "remaining_count": 1,
                                "status": "pending",
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "blocked"
    assert triage["phase"] == "blocked"
    assert triage["waiting_reason"] == "missing_prerequisite"
    assert triage["blocking_reason"] == "missing_prerequisite"
    assert triage["reservation_ids"] == ["res_1"]
    assert triage["reservation_preview"] == "猛犸坦克 × 1 · 缺少前置"
    assert "猛犸坦克 × 1 · 缺少前置" in triage["status_line"]
    print("  PASS: task_replay_bundle_derives_replay_triage_from_unit_pipeline")


def test_task_replay_bundle_marks_waiting_dispatch_as_running_dispatch():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "reservation_id": "res_1",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "waiting_dispatch",
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "running"
    assert triage["phase"] == "dispatch"
    assert triage["waiting_reason"] == "waiting_dispatch"
    assert triage["blocking_reason"] == ""
    assert triage["reservation_preview"] == "步兵 × 1 · 待分发"
    assert triage["status_line"] == "历史推进：步兵 × 1 · 待分发"
    print("  PASS: task_replay_bundle_marks_waiting_dispatch_as_running_dispatch")


def test_task_replay_bundle_falls_back_to_live_runtime_facts_for_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "cash": 5000,
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_persisted",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "persisted:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 2,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle(
        "t_demo",
        entries,
        live_runtime_facts={
            "unfulfilled_requests": [
                {
                    "request_id": "req_live",
                    "task_id": "t_demo",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "remaining_count": 1,
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "live:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 4,
                    "world_sync_failure_threshold": 3,
                }
            ],
            "unit_reservations": [
                {
                    "reservation_id": "res_live",
                    "request_id": "req_live",
                    "task_id": "t_demo",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "remaining_count": 1,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 5,
                    "world_sync_failure_threshold": 3,
                }
            ],
        },
    )

    request = bundle["unit_pipeline"]["unfulfilled_requests"][0]
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert request["request_id"] == "req_persisted"
    assert request["world_sync_last_error"] == "persisted:COMMAND_EXECUTION_ERROR"
    assert request["world_sync_consecutive_failures"] == 2
    assert request["world_sync_failure_threshold"] == 3
    assert reservation["reservation_id"] == "res_live"
    assert reservation["world_sync_last_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 5
    assert reservation["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_bundle_falls_back_to_live_runtime_facts_for_unit_pipeline")


def test_task_replay_bundle_falls_back_to_runtime_state_reservations():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        }
    ]

    bundle = build_task_replay_bundle(
        "t_demo",
        entries,
        runtime_state={
            "unit_reservations": [
                {
                    "reservation_id": "res_runtime",
                    "request_id": "req_runtime",
                    "task_id": "t_demo",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 6,
                    "world_sync_failure_threshold": 3,
                }
            ]
        },
    )

    assert bundle["unit_pipeline"]["unfulfilled_requests"] == []
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert reservation["reservation_id"] == "res_runtime"
    assert reservation["world_sync_last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 6
    assert reservation["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_bundle_falls_back_to_runtime_state_reservations")


def test_task_replay_bundle_exposes_capability_truth_summary() -> None:
    entries = [
        {
            "timestamp": 10.0,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_cap",
                "packet": {
                    "runtime_facts": {
                        "faction": "soviet",
                        "base_progression": {
                            "status": "下一步：矿场",
                            "next_unit_type": "proc",
                            "next_queue_type": "Building",
                            "buildable_now": True,
                        },
                        "buildable_now": {"Building": ["powr", "proc"]},
                        "buildable_blocked": {
                            "Building": [
                                {"unit_type": "barr", "queue_type": "Building", "reason": "queue_blocked"},
                            ]
                        },
                        "ready_queue_items": [
                            {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂"},
                        ],
                    }
                },
            },
        }
    ]

    bundle = build_task_replay_bundle("t_cap", entries)

    capability_truth = bundle["capability_truth"]
    assert capability_truth["faction"] == "soviet"
    assert capability_truth["base_status"] == "下一步：矿场"
    assert capability_truth["next_unit_type"] == "proc"
    assert capability_truth["buildable_now"] is True
    assert "Building:powr" in capability_truth["issue_now"]
    assert "Building:proc" in capability_truth["issue_now"]
    assert "Building:barr:queue_blocked" in capability_truth["blocked_now"]
    assert "Building:发电厂" in capability_truth["ready_items"]
    print("  PASS: task_replay_bundle_exposes_capability_truth_summary")


def test_live_task_replay_bundle_fetches_buildable_truth_for_capability_tasks() -> None:
    class FakeTask:
        def __init__(self, task_id: str, *, is_capability: bool) -> None:
            self.task_id = task_id
            self.raw_text = "能力"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 80
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 123.0
            self.created_at = 120.0
            self.label = "cap"
            self.is_capability = is_capability

    calls: list[tuple[str, bool]] = []

    def compute_runtime_facts(task_id: str, *, include_buildable: bool = False):
        calls.append((task_id, include_buildable))
        return {
            "base_progression": {
                "status": "下一步：矿场",
                "next_unit_type": "proc",
                "next_queue_type": "Building",
                "buildable_now": True,
            },
            "buildable_now": {"Building": ["proc"]} if include_buildable else {},
        }

    bundle = build_live_task_replay_bundle(
        "t_cap",
        [
            {
                "timestamp": 10.0,
                "component": "kernel",
                "level": "INFO",
                "message": "Task created",
                "event": "task_created",
                "data": {"task_id": "t_cap"},
            }
        ],
        runtime_state={},
        tasks=[FakeTask("t_cap", is_capability=True)],
        jobs_for_task=lambda _task_id: [],
        task_payload_builder=lambda *_args, **_kwargs: {
            "task_id": "t_cap",
            "status": "running",
            "triage": {"status_line": "能力处理中：待机"},
        },
        compute_runtime_facts=compute_runtime_facts,
    )

    assert calls == [("t_cap", True)]
    assert bundle["capability_truth"]["base_status"] == "下一步：矿场"
    assert "Building:proc" in bundle["capability_truth"]["issue_now"]
    print("  PASS: live_task_replay_bundle_fetches_buildable_truth_for_capability_tasks")


def test_task_replay_bundle_keeps_distinct_llm_turns_when_wake_attempt_missing():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent llm input",
            "event": "llm_input",
            "data": {
                "task_id": "t_demo",
                "messages": [{"role": "system"}, {"role": "user", "content": "first"}],
                "tools": [{"name": "query_world"}],
            },
        },
        {
            "timestamp": 10.2,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "response_text": "first response",
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        },
        {
            "timestamp": 10.3,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent llm input",
            "event": "llm_input",
            "data": {
                "task_id": "t_demo",
                "messages": [{"role": "system"}, {"role": "user", "content": "second"}],
                "tools": [{"name": "query_world"}],
            },
        },
        {
            "timestamp": 10.4,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "response_text": "second response",
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    assert len(bundle["llm_turns"]) == 2
    assert bundle["llm_turns"][0]["response_text"] == "first response"
    assert bundle["llm_turns"][0]["input_messages"][1]["content"] == "first"
    assert bundle["llm_turns"][1]["response_text"] == "second response"
    assert bundle["llm_turns"][1]["input_messages"][1]["content"] == "second"
    print("  PASS: task_replay_bundle_keeps_distinct_llm_turns_when_wake_attempt_missing")


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


def test_runtime_bridge_task_payload_builder_fetches_capability_truth_blocker():
    class FakeTask:
        def __init__(self):
            self.task_id = "t_cap"
            self.raw_text = "发展科技"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 80
            self.status = TaskStatus.RUNNING
            self.timestamp = 123.0
            self.created_at = 120.0
            self.label = "001"
            self.is_capability = True

    class FakeKernel:
        def __init__(self):
            self.task = FakeTask()

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return [self.task]

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self, task_id=None):
            del task_id
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {
                "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
                "capability_status": {
                    "task_id": "t_cap",
                    "label": "001",
                    "phase": "idle",
                },
            }

    class FakeWorldModel:
        def __init__(self):
            self.calls: list[tuple[str, bool]] = []

        def world_summary(self):
            return {}

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            self.calls.append((task_id, include_buildable))
            return {
                "faction": "allied",
                "capability_truth_blocker": "faction_roster_unsupported",
            }

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    world_model = FakeWorldModel()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=world_model,
        game_loop=FakeGameLoop(),
    )

    payload = bridge._task_to_dict(bridge.kernel.task, [], runtime_state=bridge.kernel.runtime_state())

    assert world_model.calls == [("t_cap", False)]
    assert payload["triage"]["blocking_reason"] == "faction_roster_unsupported"
    assert "阵营能力真值未覆盖" in payload["triage"]["status_line"]
    print("  PASS: runtime_bridge_task_payload_builder_fetches_capability_truth_blocker")


def test_session_clear_unregisters_runtime_bindings():
    class FakeTask:
        def __init__(self, task_id: str):
            self.task_id = task_id
            self.raw_text = "推进前线"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = TaskStatus.RUNNING
            self.timestamp = 123.0
            self.created_at = 120.0
            self.label = "001"
            self.is_capability = False

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
            self.reset_calls = 0
            self.task = FakeTask("t1")
            self.agent = FakeAgent()
            self.job = FakeJob("j1")
            self.tasks = [self.task]
            self.jobs = [self.job]

        def reset_session(self):
            self.reset_calls += 1
            self.tasks = []
            self.jobs = []

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self.tasks)

        def jobs_for_task(self, task_id):
            return [job for job in self.jobs if job.task_id == task_id]

        def get_task_agent(self, task_id):
            return self.agent if self.tasks and task_id == "t1" else None

        def active_jobs(self):
            return list(self.jobs)

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def __init__(self):
            self.reset_calls = 0

        def world_summary(self):
            return {}

        def reset_snapshot(self):
            self.reset_calls += 1

    class FakeGameLoop:
        def __init__(self):
            self.registered_agents: list[tuple[str, float]] = []
            self.unregistered_agents: list[str] = []
            self.registered_jobs: list[str] = []
            self.unregistered_jobs: list[str] = []
            self.reset_runtime_calls = 0

        def register_agent(self, task_id, queue, review_interval=10.0, *, is_suspended=None):
            del queue, is_suspended
            self.registered_agents.append((task_id, review_interval))

        def unregister_agent(self, task_id):
            self.unregistered_agents.append(task_id)

        def register_job(self, job):
            self.registered_jobs.append(job.job_id)

        def unregister_job(self, job_id):
            self.unregistered_jobs.append(job_id)

        def reset_runtime_state(self):
            self.reset_runtime_calls += 1

    kernel = FakeKernel()
    world_model = FakeWorldModel()
    game_loop = FakeGameLoop()
    bridge = RuntimeBridge(
        kernel=kernel,
        world_model=world_model,
        game_loop=game_loop,
    )
    bridge.sync_runtime()

    assert game_loop.registered_agents == [("t1", 0.25)]
    assert game_loop.registered_jobs == ["j1"]

    async def _noop_publish_dashboard():
        return None

    bridge.publish_dashboard = _noop_publish_dashboard  # type: ignore[method-assign]

    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_persistence_session(tmpdir, session_name="runtime-clear")
            bridge.log_session_root = tmpdir
            asyncio.run(bridge.on_session_clear("client_clear"))
    finally:
        stop_persistence_session()

    assert kernel.reset_calls == 1
    assert world_model.reset_calls == 1
    assert game_loop.reset_runtime_calls == 1
    assert game_loop.unregistered_agents == ["t1"]
    assert game_loop.unregistered_jobs == ["j1"]
    print("  PASS: session_clear_unregisters_runtime_bindings")


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


def test_broadcast_drops_stalled_client_after_timeout():
    """A stalled client should be evicted instead of blocking broadcast indefinitely."""
    server = WSServer()
    server._broadcast_send_timeout_s = 0.01

    class _HangingWS:
        async def send_str(self, payload: str) -> None:
            del payload
            await asyncio.sleep(1.0)

    class _FastWS:
        def __init__(self) -> None:
            self.payloads: list[str] = []

        async def send_str(self, payload: str) -> None:
            self.payloads.append(payload)

    fast = _FastWS()

    async def run():
        server._clients = {
            "slow": _HangingWS(),  # type: ignore[assignment]
            "fast": fast,  # type: ignore[assignment]
        }
        await server.broadcast("log_entry", {"msg": "tick"})

    asyncio.run(run())
    assert "slow" not in server._clients
    assert "fast" in server._clients
    assert len(fast.payloads) == 1
    print("  PASS: broadcast_drops_stalled_client_after_timeout")


def test_send_to_client_drops_stalled_client_after_timeout():
    """Direct client sends should also time out and evict stalled sockets."""
    server = WSServer()
    server._broadcast_send_timeout_s = 0.01

    class _HangingWS:
        async def send_str(self, payload: str) -> None:
            del payload
            await asyncio.sleep(1.0)

    async def run():
        server._clients = {
            "slow": _HangingWS(),  # type: ignore[assignment]
        }
        await server.send_to_client("slow", "log_entry", {"msg": "tick"})

    asyncio.run(run())
    assert "slow" not in server._clients
    print("  PASS: send_to_client_drops_stalled_client_after_timeout")


# --- Run all tests ---

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
