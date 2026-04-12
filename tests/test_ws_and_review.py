"""Tests for WebSocket server (1.6) and review_interval scheduling (1.8)."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

import aiohttp
import benchmark
import dashboard_publish as dashboard_publish_module
import logging_system
import pytest
from logging_system import start_persistence_session, stop_persistence_session

from models import Event, EventType, TaskMessage, TaskMessageType, TaskStatus
from main import RuntimeBridge
from session_browser import build_session_catalog_payload
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

        async def on_diagnostics_sync_request(self, client_id):
            received_commands.append(f"diag-sync:{client_id}")

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

                await ws.send_str(json.dumps({"type": "diagnostics_sync_request"}))
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
    assert any(item.startswith("diag-sync:client_") for item in received_commands)
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

        async def on_diagnostics_sync_request(self, client_id):
            received_commands.append(f"diag-sync:{client_id}")

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


def test_ws_query_response_can_target_single_client():
    """Direct adjutant responses should only reach the requesting client when targeted."""
    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=18771))

    async def run():
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect("http://127.0.0.1:18771/ws") as ws1:
                    async with session.ws_connect("http://127.0.0.1:18771/ws") as ws2:
                        await asyncio.sleep(0.05)
                        await server.send_query_response(
                            {
                                "answer": "只发给 client_1",
                                "response_type": "command",
                                "ok": True,
                            },
                            client_id="client_1",
                        )
                        msg1 = await asyncio.wait_for(ws1.receive(), timeout=1.0)
                        payload1 = json.loads(msg1.data)
                        assert payload1["type"] == "query_response"
                        assert payload1["data"]["answer"] == "只发给 client_1"
                        try:
                            await asyncio.wait_for(ws2.receive(), timeout=0.2)
                            raise AssertionError("second client unexpectedly received targeted query_response")
                        except asyncio.TimeoutError:
                            pass
        finally:
            await server.stop()

    asyncio.run(run())
    print("  PASS: ws_query_response_can_target_single_client")


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


def test_runtime_bridge_handle_published_task_message_tolerates_minimal_adjutant_stub():
    class FakeTask:
        def __init__(self):
            self.task_id = "t1"
            self.raw_text = "探索地图"
            self.label = "001"
            self.status = TaskStatus.RUNNING

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask()]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

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

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )

    class MinimalAdjutant:
        async def handle_player_input(self, text: str) -> dict[str, Any]:
            return {"response_text": text}

    bridge.adjutant = MinimalAdjutant()

    bridge._handle_published_task_message(
        TaskMessage(
            task_id="t1",
            message_id="m_info",
            type=TaskMessageType.TASK_INFO,
            content="侦察进行中",
        )
    )
    bridge.kernel._tasks[0].status = TaskStatus.SUCCEEDED
    bridge._handle_published_task_message(
        TaskMessage(
            task_id="t1",
            message_id="m_done",
            type=TaskMessageType.TASK_COMPLETE_REPORT,
            content="任务完成",
        )
    )
    print("  PASS: runtime_bridge_handle_published_task_message_tolerates_minimal_adjutant_stub")


def test_diagnostics_sync_request_refreshes_current_state_without_replaying_generic_history():
    """diagnostics_sync_request should refresh diagnostics surfaces without duplicating chat/task history."""

    logging_system.clear()
    benchmark.clear()

    class FakeTask:
        def __init__(self, task_id: str, raw_text: str, status: str = "running"):
            self.task_id = task_id
            self.raw_text = raw_text
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("Status", (), {"value": status})()
            self.timestamp = 123.0
            self.created_at = 100.0

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask("t1", "探索地图")]
            self._task_messages = [
                TaskMessage(
                    task_id="t1",
                    message_id="msg-info",
                    type=TaskMessageType.TASK_INFO,
                    content="任务进行中",
                    timestamp=124.0,
                )
            ]
            self._notifications = [{"type": "command", "content": "已创建任务", "task_id": "t1"}]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return list(self._task_messages)

        def list_player_notifications(self):
            return list(self._notifications)

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {"economy": {"cash": 1200}, "military": {"units": 3}}

        def refresh_health(self):
            return {
                "stale": False,
                "consecutive_failures": 0,
                "failure_threshold": 3,
                "last_error": "",
            }

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

        async def send_session_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_task_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_history_to_client(self, client_id, payload):
            self.sent.append(("session_history", {"client_id": client_id, "payload": payload}))

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)
    bridge._publisher.recent_responses = [
        {
            "response_type": "command",
            "answer": "副官回复",
            "task_id": "t1",
            "timestamp": 125.0,
        }
    ]
    logging_system.get_logger("kernel").info("历史日志", event="history_log")
    with benchmark.span("tool_exec", name="history_bench"):
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        start_persistence_session(tmpdir, session_name="diag-sync")
        try:
            async def run():
                await bridge.on_diagnostics_sync_request("client_diag")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    sent_types = [msg_type for msg_type, _ in ws.sent]
    assert sent_types[:5] == ["world_snapshot", "task_list", "session_catalog", "session_task_catalog", "session_history"]
    assert sent_types.count("world_snapshot") == 1
    history_payload = next(item for item in ws.sent if item[0] == "session_history")[1]["payload"]
    assert history_payload["is_live"] is True
    assert [entry["message"] for entry in history_payload["log_entries"]] == ["历史日志"]
    assert [entry["name"] for entry in history_payload["benchmark_records"]] == ["history_bench"]
    assert "log_entry" not in sent_types
    assert "benchmark" not in sent_types
    assert "task_message" not in sent_types
    assert "player_notification" not in sent_types
    assert "query_response" not in sent_types
    print("  PASS: diagnostics_sync_request_refreshes_current_state_without_replaying_generic_history")


def test_sync_request_propagates_world_stale_truth_consistently():
    """sync_request should keep top-level world health and task triage in sync."""

    class FakeTask:
        def __init__(self, task_id: str, raw_text: str):
            self.task_id = task_id
            self.raw_text = raw_text
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 123.0
            self.created_at = 100.0

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask("t_sync", "展开基地车")]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

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
            return {
                "economy": {"cash": 1200},
                "military": {"units": 1},
                "stale": True,
                "consecutive_refresh_failures": 4,
                "failure_threshold": 3,
                "last_refresh_error": "actors:CONNECTION_ERROR: connection refused",
            }

        def refresh_health(self):
            return {
                "stale": True,
                "disconnected": True,
                "consecutive_failures": 4,
                "failure_threshold": 3,
                "last_error": "actors:CONNECTION_ERROR: connection refused",
            }

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
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
        await bridge.on_sync_request("client_sync")

    asyncio.run(run())

    snapshot = ws.sent[0][1]["snapshot"]
    triage = ws.sent[1][1]["tasks"][0]["triage"]
    assert snapshot["stale"] is True
    assert snapshot["disconnected"] is True
    assert snapshot["consecutive_refresh_failures"] == 4
    assert snapshot["failure_threshold"] == 3
    assert snapshot["last_refresh_error"] == "actors:CONNECTION_ERROR: connection refused"
    assert triage["state"] == "degraded"
    assert triage["world_stale"] is True
    assert triage["world_sync_failures"] == 4
    assert triage["world_sync_failure_threshold"] == 3
    assert triage["world_sync_error"] == "actors:CONNECTION_ERROR: connection refused"
    print("  PASS: sync_request_propagates_world_stale_truth_consistently")


def test_sync_request_overlays_live_world_health_into_session_catalog():
    class FakeTask:
        def __init__(self, task_id: str, status: str) -> None:
            self.task_id = task_id
            self.raw_text = task_id
            self.kind = type("TaskKindValue", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("TaskStatusValue", (), {"value": status})()
            self.timestamp = 100.0
            self.created_at = 100.0
            self.label = ""
            self.is_capability = False

    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return [
                FakeTask("t_live_running", "running"),
                FakeTask("t_live_partial", "partial"),
            ]

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

        def refresh_health(self):
            return {
                "stale": True,
                "disconnected": True,
                "consecutive_failures": 4,
                "total_failures": 9,
                "failure_threshold": 3,
                "last_error": "actors:CONNECTION_ERROR: connection refused",
            }

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

        async def send_session_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.sent.append(("session_task_catalog", {"client_id": client_id, "payload": payload}))

        async def send_session_history_to_client(self, client_id, payload):
            self.sent.append(("session_history", {"client_id": client_id, "payload": payload}))

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)
    bridge._publisher._runtime_fault_state = {
        "degraded": True,
        "source": "dashboard_publish",
        "stage": "task_messages",
        "error": "RuntimeError('publish-boom')",
        "count": 3,
        "first_at": 40.0,
        "updated_at": 42.0,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        start_persistence_session(tmpdir, session_name="live-session")
        try:
            async def run():
                await bridge.on_sync_request("client_live")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    session_catalog = next(item for item in ws.sent if item[0] == "session_catalog")[1]["payload"]["sessions"]
    assert len(session_catalog) == 1
    world_health = session_catalog[0]["world_health"]
    assert world_health["ended_stale"] is True
    assert world_health["disconnect_seen"] is True
    assert world_health["ended_disconnected"] is True
    assert world_health["failure_threshold"] == 3
    assert world_health["last_error"] == "actors:CONNECTION_ERROR: connection refused"
    assert "stale_refreshes" not in world_health
    assert "max_consecutive_failures" not in world_health
    assert session_catalog[0]["runtime_fault_summary"] == {
        "degraded": True,
        "source": "dashboard_publish",
        "stage": "task_messages",
        "error": "RuntimeError('publish-boom')",
        "count": 3,
        "first_at": 40.0,
        "updated_at": 42.0,
        "breakdown": [
            {
                "source": "dashboard_publish",
                "stage": "task_messages",
                "count": 3,
            }
        ],
    }
    assert session_catalog[0]["task_rollup"] == {
        "total": 2,
        "non_terminal": 1,
        "terminal": 1,
        "by_status": {
            "running": 1,
            "partial": 1,
        },
    }
    assert list(session_catalog[0]["task_rollup"]["by_status"].keys()) == ["running", "partial"]


def test_sync_request_tolerates_runtime_fact_and_world_health_failures():
    class FakeTask:
        def __init__(self):
            self.task_id = "t_cap"
            self.raw_text = "发展科技"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 70
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
                "active_tasks": {
                    "t_cap": {
                        "is_capability": True,
                        "label": "001",
                        "status": "running",
                    }
                },
                "capability_status": {
                    "task_id": "t_cap",
                    "label": "001",
                    "phase": "idle",
                },
            }

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert include_buildable is False
            raise RuntimeError(f"boom:{task_id}")

        def refresh_health(self):
            raise RuntimeError("health-boom")

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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

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

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir

        async def run():
            await bridge.on_sync_request("client_resilient")

        asyncio.run(run())

    world_snapshot = next(item for item in ws.sent if item[0] == "world_snapshot")[1]["snapshot"]
    task_payload = next(item for item in ws.sent if item[0] == "task_list")[1]["tasks"][0]
    session_catalog = next(item for item in ws.sent if item[0] == "session_catalog")[1]["payload"]

    assert world_snapshot["player_faction"] == ""
    assert world_snapshot["capability_truth_blocker"] == ""
    assert world_snapshot["runtime_fault_state"] == {
        "degraded": True,
        "source": "world_sync_probe",
        "stage": "",
        "error": "RuntimeError('health-boom')",
        "count": 1,
        "first_at": world_snapshot["runtime_fault_state"]["updated_at"],
        "updated_at": world_snapshot["runtime_fault_state"]["updated_at"],
    }
    assert isinstance(world_snapshot["runtime_fault_state"]["updated_at"], float)
    assert task_payload["task_id"] == "t_cap"
    assert task_payload["triage"]["phase"] == "idle"
    assert task_payload["triage"]["blocking_reason"] == ""
    assert session_catalog["sessions"] == []


def test_dashboard_publish_fault_is_reflected_in_world_snapshot_runtime_fault_state():
    class FakeKernel:
        def list_pending_questions(self):
            return []

        def runtime_state(self):
            return {}

        def list_tasks(self):
            return []

        def jobs_for_task(self, _task_id):
            return []

        def get_task_agent(self, _task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self, task_id=None):
            del task_id
            return []

        def list_player_notifications(self):
            return []

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def compute_runtime_facts(self, *_args, **_kwargs):
            return {}

        def refresh_health(self):
            return {"stale": False}

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

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(FakeWS())

    async def _noop():
        return None

    async def _boom():
        raise RuntimeError("publish-boom")

    bridge._publisher.broadcast_current_dashboard = _noop
    bridge._publisher.publish_task_updates = _noop
    bridge._publisher.publish_task_messages = _boom
    bridge._publisher.publish_notifications = _noop
    bridge._publisher.publish_logs = _noop
    bridge._publisher.publish_benchmarks = _noop

    asyncio.run(bridge._publisher.publish_all())

    world_snapshot = bridge._build_dashboard_payload()["world_snapshot"]
    assert world_snapshot["runtime_fault_state"] == {
        "degraded": True,
        "source": "dashboard_publish",
        "stage": "task_messages",
        "error": "RuntimeError('publish-boom')",
        "count": 1,
        "first_at": world_snapshot["runtime_fault_state"]["updated_at"],
        "updated_at": world_snapshot["runtime_fault_state"]["updated_at"],
    }
    assert isinstance(world_snapshot["runtime_fault_state"]["updated_at"], float)


def test_build_session_catalog_clears_persisted_error_detail_when_live_overlay_changes_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = start_persistence_session(tmpdir, session_name="live-session")
        session_meta_path = session_dir / "session.json"
        session_meta = json.loads(session_meta_path.read_text(encoding="utf-8"))
        session_meta["world_health"] = {
            "stale_seen": True,
            "ended_stale": False,
            "stale_refreshes": 2,
            "max_consecutive_failures": 6,
            "failure_threshold": 3,
            "last_error": "actors:OLD_ERROR",
            "last_error_detail": "Attempted to get trait from destroyed object",
        }
        session_meta_path.write_text(json.dumps(session_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            payload = build_session_catalog_payload(
                tmpdir,
                selected_session_dir=session_dir,
                current_world_health={
                    "stale": True,
                    "consecutive_failures": 4,
                    "total_failures": 9,
                    "failure_threshold": 3,
                    "last_error": "actors:COMMAND_EXECUTION_ERROR",
                },
            )
        finally:
            stop_persistence_session()

    session_catalog = payload["sessions"]
    assert len(session_catalog) == 1
    world_health = session_catalog[0]["world_health"]
    assert world_health["ended_stale"] is True
    assert world_health["stale_seen"] is True
    assert world_health["stale_refreshes"] == 2
    assert world_health["max_consecutive_failures"] == 6
    assert world_health["failure_threshold"] == 3
    assert world_health["last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert world_health["last_error_detail"] == ""


def test_build_session_catalog_preserves_persisted_error_detail_when_live_overlay_keeps_same_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = start_persistence_session(tmpdir, session_name="live-session")
        session_meta_path = session_dir / "session.json"
        session_meta = json.loads(session_meta_path.read_text(encoding="utf-8"))
        session_meta["world_health"] = {
            "stale_seen": True,
            "ended_stale": False,
            "stale_refreshes": 2,
            "max_consecutive_failures": 6,
            "failure_threshold": 3,
            "last_error": "actors:COMMAND_EXECUTION_ERROR",
            "last_error_detail": "Attempted to get trait from destroyed object",
        }
        session_meta_path.write_text(json.dumps(session_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            payload = build_session_catalog_payload(
                tmpdir,
                selected_session_dir=session_dir,
                current_world_health={
                    "stale": True,
                    "consecutive_failures": 4,
                    "total_failures": 9,
                    "failure_threshold": 3,
                    "last_error": "actors:COMMAND_EXECUTION_ERROR",
                },
            )
        finally:
            stop_persistence_session()

    session_catalog = payload["sessions"]
    assert len(session_catalog) == 1
    world_health = session_catalog[0]["world_health"]
    assert world_health["last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert world_health["last_error_detail"] == "Attempted to get trait from destroyed object"


def test_sync_request_preserves_persisted_session_health_when_refresh_health_fails():
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

        def refresh_health(self):
            raise RuntimeError("health-boom")

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

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

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = start_persistence_session(tmpdir, session_name="persisted-health")
        session_meta_path = session_dir / "session.json"
        session_meta = json.loads(session_meta_path.read_text(encoding="utf-8"))
        session_meta["world_health"] = {
            "stale_seen": True,
            "ended_stale": False,
            "stale_refreshes": 2,
            "max_consecutive_failures": 6,
            "failure_threshold": 3,
            "last_error": "actors:OLD_ERROR",
            "last_error_detail": "Attempted to get trait from destroyed object",
        }
        session_meta_path.write_text(json.dumps(session_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        bridge.log_session_root = tmpdir
        try:
            async def run():
                await bridge.on_sync_request("client_persisted_health")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    session_catalog = next(item for item in ws.sent if item[0] == "session_catalog")[1]["payload"]["sessions"]
    assert len(session_catalog) == 1
    world_health = session_catalog[0]["world_health"]
    assert world_health["stale_seen"] is True
    assert world_health["ended_stale"] is False
    assert world_health["stale_refreshes"] == 2
    assert world_health["max_consecutive_failures"] == 6
    assert world_health["failure_threshold"] == 3
    assert world_health["last_error"] == "actors:OLD_ERROR"
    assert world_health["last_error_detail"] == "Attempted to get trait from destroyed object"


def test_sync_request_surfaces_unit_pipeline_preview_in_world_snapshot():
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
            return {
                "unfulfilled_requests": [
                    {
                        "request_id": "req_1",
                        "task_id": "t_recon",
                        "task_label": "002",
                        "category": "infantry",
                        "unit_type": "e1",
                        "count": 1,
                        "fulfilled": 0,
                        "remaining_count": 1,
                        "hint": "步兵",
                        "reason": "waiting_dispatch",
                    }
                ],
                "unit_reservations": [
                    {
                        "reservation_id": "res_1",
                        "request_id": "req_1",
                        "task_id": "t_recon",
                        "task_label": "002",
                        "unit_type": "e1",
                        "count": 1,
                        "status": "pending",
                        "remaining_count": 1,
                        "reason": "waiting_dispatch",
                    }
                ],
            }

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def refresh_health(self):
            return {"stale": False}

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            assert task_id == "__dashboard__"
            assert include_buildable is False
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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

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
        await bridge.on_sync_request("client_preview")

    asyncio.run(run())

    snapshot = next(item for item in ws.sent if item[0] == "world_snapshot")[1]["snapshot"]
    assert snapshot["unit_pipeline_preview"] == "步兵 × 1 · 待分发"
    assert snapshot["unit_pipeline_focus"] == {
        "preview": "步兵 × 1 · 待分发",
        "detail": "步兵 × 1 <- 待分发",
        "reason": "waiting_dispatch",
        "reason_text": "待分发",
        "task_id": "t_recon",
        "task_label": "002",
        "request_count": 1,
        "reservation_count": 1,
        "reservation_status": "pending",
        "remaining_count": 1,
        "assigned_count": 0,
        "produced_count": 0,
        "start_released": False,
        "bootstrap_job_id": "",
    }


def test_sync_request_prefers_highest_severity_unit_pipeline_focus():
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

        def list_task_messages(self, task_id=None):
            del task_id
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {
                "unfulfilled_requests": [
                    {
                        "request_id": "req_wait",
                        "task_id": "t_wait",
                        "task_label": "002",
                        "category": "vehicle",
                        "count": 1,
                        "fulfilled": 0,
                        "remaining_count": 1,
                        "hint": "步兵",
                        "blocking": True,
                        "reason": "waiting_dispatch",
                    },
                    {
                        "request_id": "req_stale",
                        "task_id": "t_stale",
                        "task_label": "003",
                        "category": "vehicle",
                        "count": 1,
                        "fulfilled": 0,
                        "remaining_count": 1,
                        "hint": "重坦",
                        "blocking": True,
                        "reason": "world_sync_stale",
                        "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                        "world_sync_consecutive_failures": 4,
                        "world_sync_failure_threshold": 3,
                    },
                ],
                "unit_reservations": [
                    {
                        "reservation_id": "res_wait",
                        "request_id": "req_wait",
                        "task_id": "t_wait",
                        "task_label": "002",
                        "unit_type": "e1",
                        "count": 1,
                        "remaining_count": 1,
                        "status": "pending",
                        "blocking": True,
                        "reason": "waiting_dispatch",
                        "updated_at": 10.0,
                    },
                    {
                        "reservation_id": "res_stale",
                        "request_id": "req_stale",
                        "task_id": "t_stale",
                        "task_label": "003",
                        "unit_type": "3tnk",
                        "count": 1,
                        "remaining_count": 1,
                        "status": "pending",
                        "blocking": True,
                        "reason": "world_sync_stale",
                        "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                        "world_sync_consecutive_failures": 4,
                        "world_sync_failure_threshold": 3,
                        "updated_at": 20.0,
                    },
                ],
                "timestamp": time.time(),
            }

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def refresh_health(self):
            return {"stale": False}

        def compute_runtime_facts(self, task_id, include_buildable=False):
            del task_id, include_buildable
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

        async def send_world_snapshot_to_client(self, client_id, snapshot):
            self.sent.append(("world_snapshot", {"client_id": client_id, "snapshot": snapshot}))

        async def send_task_list_to_client(self, client_id, tasks, pending_questions=None):
            self.sent.append(("task_list", {"client_id": client_id, "tasks": tasks, "pending_questions": pending_questions}))

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
        await bridge.on_sync_request("client_severity")

    asyncio.run(run())

    snapshot = next(item for item in ws.sent if item[0] == "world_snapshot")[1]["snapshot"]
    assert snapshot["unit_pipeline_preview"] == "重坦 × 1 · 等待世界同步恢复"
    assert snapshot["unit_pipeline_focus"] == {
        "preview": "重坦 × 1 · 等待世界同步恢复",
        "detail": "重坦 × 1 <- 等待世界同步恢复 failures=4/3 | economy:COMMAND_EXECUTION_ERROR",
        "reason": "world_sync_stale",
        "reason_text": "等待世界同步恢复",
        "task_id": "t_stale",
        "task_label": "003",
        "request_count": 2,
        "reservation_count": 2,
        "reservation_status": "pending",
        "remaining_count": 1,
        "assigned_count": 0,
        "produced_count": 0,
        "start_released": False,
        "bootstrap_job_id": "",
    }


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


def test_dashboard_publisher_logs_player_visible_task_messages(monkeypatch):
    logged: list[dict[str, Any]] = []

    class FakeLogger:
        def info(self, _message, **kwargs):
            logged.append(kwargs)

    monkeypatch.setattr(dashboard_publish_module, "slog", FakeLogger())

    class FakeKernel:
        def list_task_messages(self):
            return [
                TaskMessage(
                    message_id="m_info",
                    task_id="t_demo",
                    type=TaskMessageType.TASK_INFO,
                    content="缺少战车工厂，等待能力层补前置",
                ),
                TaskMessage(
                    message_id="m_warn",
                    task_id="t_demo",
                    type=TaskMessageType.TASK_WARNING,
                    content="世界状态同步异常，暂停动作等待恢复",
                ),
                TaskMessage(
                    message_id="m_question",
                    task_id="t_demo",
                    type=TaskMessageType.TASK_QUESTION,
                    content="是否切换目标？",
                    options=["是", "否"],
                ),
            ]

    class FakeWS:
        def __init__(self):
            self.sent: list[dict[str, Any]] = []

        async def send_task_message(self, payload):
            self.sent.append(dict(payload))

    publisher = dashboard_publish_module.DashboardPublisher(
        kernel=FakeKernel(),
        ws_server=FakeWS(),
        dashboard_payload_builder=lambda: {},
        task_payload_builder=lambda *args, **kwargs: {},
    )

    async def run():
        await publisher.publish_task_messages()

    asyncio.run(run())

    assert [payload["type"] for payload in publisher.ws_server.sent] == [
        TaskMessageType.TASK_INFO.value,
        TaskMessageType.TASK_WARNING.value,
        TaskMessageType.TASK_QUESTION.value,
    ]
    assert logged == [
        {
            "event": TaskMessageType.TASK_INFO.value,
            "task_id": "t_demo",
            "message_id": "m_info",
            "message_type": TaskMessageType.TASK_INFO.value,
            "content": "缺少战车工厂，等待能力层补前置",
        },
        {
            "event": TaskMessageType.TASK_WARNING.value,
            "task_id": "t_demo",
            "message_id": "m_warn",
            "message_type": TaskMessageType.TASK_WARNING.value,
            "content": "世界状态同步异常，暂停动作等待恢复",
        },
    ]
    print("  PASS: dashboard_publisher_logs_player_visible_task_messages")


def test_dashboard_publisher_schedule_publish_logs_background_failures_without_unhandled_task(monkeypatch):
    logged_errors: list[dict[str, Any]] = []

    class FakeLogger:
        def info(self, _message, **kwargs):
            del _message, kwargs

        def error(self, _message, **kwargs):
            logged_errors.append(kwargs)

    monkeypatch.setattr(dashboard_publish_module, "slog", FakeLogger())

    class FakeKernel:
        def list_task_messages(self):
            return [
                TaskMessage(
                    message_id="m_info",
                    task_id="t_demo",
                    type=TaskMessageType.TASK_INFO,
                    content="后台 publish 触发任务消息回调",
                )
            ]

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[dict[str, Any]] = []

        async def send_task_message(self, payload):
            self.sent.append(dict(payload))

    async def _noop():
        return None

    def _crash(_message):
        raise RuntimeError("boom")

    async def run() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        background_errors: list[dict[str, Any]] = []

        def _capture_loop_exception(loop, context) -> None:
            del loop
            background_errors.append(dict(context))

        loop.set_exception_handler(_capture_loop_exception)
        try:
            publisher = dashboard_publish_module.DashboardPublisher(
                kernel=FakeKernel(),
                ws_server=FakeWS(),
                dashboard_payload_builder=lambda: {},
                task_payload_builder=lambda *args, **kwargs: {},
                task_message_callback=_crash,
            )
            publisher.broadcast_current_dashboard = _noop
            publisher.publish_task_updates = _noop
            publisher.publish_notifications = _noop
            publisher.publish_logs = _noop
            publisher.publish_benchmarks = _noop

            publisher.schedule_publish()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert publisher.publish_task is None
            assert background_errors == [], background_errors
            assert len(logged_errors) == 1
            assert logged_errors[0]["event"] == "dashboard_publish_stage_failed"
            assert logged_errors[0]["stage"] == "task_messages"
            assert logged_errors[0]["error"] == "RuntimeError('boom')"
            assert float(logged_errors[0]["timestamp"]) > 0
            assert len(publisher.ws_server.sent) == 1
            assert publisher.ws_server.sent[0]["message_id"] == "m_info"
            assert publisher.ws_server.sent[0]["task_id"] == "t_demo"
            assert publisher.ws_server.sent[0]["type"] == TaskMessageType.TASK_INFO.value
            assert publisher.ws_server.sent[0]["content"] == "后台 publish 触发任务消息回调"
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: dashboard_publisher_schedule_publish_logs_background_failures_without_unhandled_task")


def test_dashboard_publisher_publish_all_continues_after_stage_failure(monkeypatch):
    logged_errors: list[dict[str, Any]] = []

    class FakeLogger:
        def error(self, _message, **kwargs):
            logged_errors.append(kwargs)

    monkeypatch.setattr(dashboard_publish_module, "slog", FakeLogger())

    class FakeWS:
        def __init__(self):
            self.is_running = True

    publisher = dashboard_publish_module.DashboardPublisher(
        kernel=object(),
        ws_server=FakeWS(),
        dashboard_payload_builder=lambda: {},
        task_payload_builder=lambda *args, **kwargs: {},
    )

    visited: list[str] = []

    async def _dashboard():
        visited.append("dashboard")

    async def _task_updates():
        visited.append("task_updates")

    async def _task_messages():
        visited.append("task_messages")
        raise RuntimeError("boom")

    async def _notifications():
        visited.append("notifications")

    async def _logs():
        visited.append("logs")

    async def _benchmarks():
        visited.append("benchmarks")

    publisher.broadcast_current_dashboard = _dashboard
    publisher.publish_task_updates = _task_updates
    publisher.publish_task_messages = _task_messages
    publisher.publish_notifications = _notifications
    publisher.publish_logs = _logs
    publisher.publish_benchmarks = _benchmarks

    asyncio.run(publisher.publish_all())

    assert visited == [
        "dashboard",
        "task_updates",
        "task_messages",
        "notifications",
        "logs",
        "benchmarks",
    ]
    assert len(logged_errors) == 1
    assert logged_errors[0]["event"] == "dashboard_publish_stage_failed"
    assert logged_errors[0]["stage"] == "task_messages"
    assert logged_errors[0]["error"] == "RuntimeError('boom')"
    assert float(logged_errors[0]["timestamp"]) > 0
    print("  PASS: dashboard_publisher_publish_all_continues_after_stage_failure")


def test_dashboard_publisher_runtime_fault_state_clears_after_clean_publish():
    class FakeWS:
        def __init__(self):
            self.is_running = True

    publisher = dashboard_publish_module.DashboardPublisher(
        kernel=object(),
        ws_server=FakeWS(),
        dashboard_payload_builder=lambda: {},
        task_payload_builder=lambda *args, **kwargs: {},
    )

    async def _noop():
        return None

    async def _boom():
        raise RuntimeError("boom")

    publisher.broadcast_current_dashboard = _noop
    publisher.publish_task_updates = _noop
    publisher.publish_task_messages = _boom
    publisher.publish_notifications = _noop
    publisher.publish_logs = _noop
    publisher.publish_benchmarks = _noop

    asyncio.run(publisher.publish_all())
    fault_state = publisher.runtime_fault_state()
    assert fault_state["degraded"] is True
    assert fault_state["count"] == 1
    assert fault_state["source"] == "dashboard_publish"
    assert fault_state["stage"] == "task_messages"
    assert fault_state["error"] == "RuntimeError('boom')"
    assert fault_state["first_at"] == fault_state["updated_at"]
    first_fault_at = fault_state["first_at"]

    asyncio.run(publisher.publish_all())
    fault_state = publisher.runtime_fault_state()
    assert fault_state["count"] == 2
    assert fault_state["first_at"] == first_fault_at
    assert fault_state["updated_at"] >= first_fault_at

    publisher.publish_task_messages = _noop
    asyncio.run(publisher.publish_all())

    assert publisher.runtime_fault_state() == {}


def test_dashboard_publisher_schedule_publish_logs_task_level_failure(monkeypatch):
    logged_errors: list[dict[str, Any]] = []

    class FakeLogger:
        def error(self, _message, **kwargs):
            logged_errors.append(kwargs)

    monkeypatch.setattr(dashboard_publish_module, "slog", FakeLogger())

    class FakeWS:
        def __init__(self):
            self.is_running = True

    async def run():
        publisher = dashboard_publish_module.DashboardPublisher(
            kernel=object(),
            ws_server=FakeWS(),
            dashboard_payload_builder=lambda: {},
            task_payload_builder=lambda *args, **kwargs: {},
        )

        async def _boom():
            raise RuntimeError("top-level boom")

        publisher.publish_all = _boom
        publisher.schedule_publish()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert publisher.publish_task is None
        assert publisher.runtime_fault_state()["degraded"] is True
        assert publisher.runtime_fault_state()["count"] == 1
        assert publisher.runtime_fault_state()["source"] == "dashboard_publish"
        assert publisher.runtime_fault_state()["stage"] == "task"
        assert publisher.runtime_fault_state()["error"] == "RuntimeError('top-level boom')"
        assert publisher.runtime_fault_state()["first_at"] == publisher.runtime_fault_state()["updated_at"]

    asyncio.run(run())

    assert len(logged_errors) == 1
    assert logged_errors[0]["event"] == "dashboard_publish_task_failed"
    assert logged_errors[0]["error"] == "RuntimeError('top-level boom')"
    assert float(logged_errors[0]["timestamp"]) > 0


def test_session_clear_rotates_persisted_log_session():
    class FakeTask:
        def __init__(self, task_id: str, label: str):
            self.task_id = task_id
            self.raw_text = "推进前线"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = TaskStatus.RUNNING
            self.timestamp = 123.0
            self.created_at = 120.0
            self.label = label
            self.is_capability = False

    class FakeKernel:
        def __init__(self):
            self.reset_calls = 0
            self.tasks = [FakeTask("t_old", "001")]

        def reset_session(self):
            self.reset_calls += 1
            self.tasks = [FakeTask("t_new", "002")]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self.tasks)

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
            self.task_lists: list[dict[str, Any]] = []

        async def send_session_cleared(self):
            self.cleared += 1

        async def send_session_catalog_to_client(self, client_id, payload):
            self.catalogs.append({"client_id": client_id, "payload": payload})

        async def send_session_task_catalog_to_client(self, client_id, payload):
            self.task_catalogs.append({"client_id": client_id, "payload": payload})

        async def send_world_snapshot(self, payload):
            self.world_snapshots.append(dict(payload))

        async def send_task_list(self, tasks, pending_questions=None):
            self.task_lists.append({
                "tasks": list(tasks),
                "pending_questions": list(pending_questions or []),
            })

        async def send_task_update(self, payload):
            del payload

        async def send_task_message(self, payload):
            del payload

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
            logging_system.get_logger("kernel").info(
                "pre-clear marker",
                event="pre_clear_marker",
                task_id="t_old",
            )
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
            bridge._probe_fault_state = {
                "degraded": True,
                "source": "world_sync_probe",
                "stage": "",
                "error": "RuntimeError('old probe fault')",
                "updated_at": 123.0,
            }
            bridge._publisher._runtime_fault_state = {
                "degraded": True,
                "source": "dashboard_publish",
                "stage": "task_messages",
                "error": "RuntimeError('old publish fault')",
                "updated_at": 124.0,
            }

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
            assert ws.world_snapshots[-1]["runtime_fault_state"] == {}
            assert ws.task_lists[-1]["tasks"][0]["task_id"] == "t_new"
            assert ws.task_lists[-1]["tasks"][0]["log_path"] == str(new_session_dir / "tasks" / "t_new.jsonl")
            assert str(old_session_dir) not in str(ws.task_lists[-1]["tasks"][0]["log_path"])
            assert bridge._probe_fault_state == {}
            assert bridge._publisher.runtime_fault_state() == {}

            old_meta = json.loads((old_session_dir / "session.json").read_text(encoding="utf-8"))
            new_meta = json.loads((new_session_dir / "session.json").read_text(encoding="utf-8"))
            assert old_meta["ended_at"]
            assert new_meta["metadata"]["reason"] == "session_clear"
            assert "ended_at" not in new_meta

            old_records = (old_session_dir / "all.jsonl").read_text(encoding="utf-8")
            new_records = (new_session_dir / "all.jsonl").read_text(encoding="utf-8")
            assert "pre_clear_marker" in old_records
            assert "log_session_rotated" not in old_records
            assert "log_session_rotated" in new_records
    finally:
        stop_persistence_session()

    assert old_session_dir is not None
    assert new_session_dir is not None
    print("  PASS: session_clear_rotates_persisted_log_session")


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


def test_build_live_task_payload_uses_last_refresh_error_fallback_for_world_sync_detail():
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
            "consecutive_failures": 5,
            "failure_threshold": 3,
            "last_refresh_error": "economy:COMMAND_EXECUTION_ERROR",
        },
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "degraded"
    assert payload["triage"]["world_stale"] is True
    assert payload["triage"]["world_sync_failures"] == 5
    assert payload["triage"]["world_sync_failure_threshold"] == 3
    assert payload["triage"]["world_sync_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert "failures=5/3" in payload["triage"]["status_line"]
    assert "economy:COMMAND_EXECUTION_ERROR" in payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_uses_last_refresh_error_fallback_for_world_sync_detail")


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
    assert triage["reservation_status"] == "pending"
    assert triage["remaining_count"] == 2
    assert triage["assigned_count"] == 0
    assert triage["produced_count"] == 0
    assert triage["start_released"] is False
    assert "等待能力模块补前置：重坦 × 2" in triage["status_line"]
    assert "重坦 × 2 <- 维修厂 + 战车工厂" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_task_specific_reservation_blocker_detail")


def test_build_live_task_payload_surfaces_unit_pipeline_world_sync_detail():
    class FakeTask:
        task_id = "t_sync_req"
        raw_text = "整点步兵"
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
            "active_tasks": {"t_sync_req": {"label": "005"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_sync_req",
                    "task_label": "005",
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
                    "task_id": "t_sync_req",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "remaining_count": 1,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "economy:IGNORED_SHOULD_NOT_WIN",
                    "world_sync_consecutive_failures": 9,
                    "world_sync_failure_threshold": 7,
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "degraded"
    assert triage["phase"] == "world_sync"
    assert triage["waiting_reason"] == "world_sync_stale"
    assert triage["blocking_reason"] == "world_sync_stale"
    assert triage["reservation_preview"] == "步兵 × 1 · 等待世界同步恢复"
    assert triage["reservation_status"] == "pending"
    assert triage["remaining_count"] == 1
    assert triage["world_stale"] is True
    assert triage["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert triage["world_sync_failures"] == 4
    assert triage["world_sync_failure_threshold"] == 3
    assert "等待能力模块恢复世界同步：步兵 × 1" in triage["status_line"]
    assert "failures=4/3" in triage["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in triage["status_line"]
    assert "economy:IGNORED_SHOULD_NOT_WIN" not in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_unit_pipeline_world_sync_detail")


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


def test_runtime_bridge_task_payload_builder_uses_runtime_state_capability_flag():
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
            self.is_capability = False

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
    print("  PASS: runtime_bridge_task_payload_builder_uses_runtime_state_capability_flag")


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
            self.tick_interval = 1.0
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

    class TrackingGameLoop(GameLoop):
        def __init__(self, world_model, kernel):
            super().__init__(world_model, kernel)
            self.reset_runtime_calls = 0

        def reset_runtime_state(self):
            self.reset_runtime_calls += 1
            super().reset_runtime_state()

    kernel = FakeKernel()
    world_model = FakeWorldModel()
    game_loop = TrackingGameLoop(world_model, kernel)
    bridge = RuntimeBridge(
        kernel=kernel,
        world_model=world_model,
        game_loop=game_loop,
    )
    bridge.sync_runtime()

    assert set(game_loop._agents) == {"t1"}
    assert set(game_loop._jobs) == {"j1"}
    assert bridge._registered_agents == {"t1"}
    assert bridge._registered_jobs == {"j1"}

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
    assert game_loop._agents == {}
    assert game_loop._jobs == {}
    assert bridge._registered_agents == set()
    assert bridge._registered_jobs == set()
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
