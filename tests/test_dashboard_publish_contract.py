"""DashboardPublisher replay and benchmark contract tests."""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import benchmark
import dashboard_publish as dashboard_publish_module

from main import RuntimeBridge
from models import TaskMessage, TaskMessageType


def test_dashboard_publisher_emit_adjutant_response_ignores_reserved_extra_field_collisions():
    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.query_responses: list[dict[str, Any]] = []
            self.query_response_targets: list[str | None] = []

        async def send_query_response(self, payload, client_id=None):
            self.query_responses.append(payload)
            self.query_response_targets.append(client_id)

    logged_info: list[dict[str, Any]] = []

    class FakeLogger:
        def info(self, _message, **kwargs):
            logged_info.append(kwargs)

    original_logger = dashboard_publish_module.slog
    dashboard_publish_module.slog = FakeLogger()
    try:
        publisher = dashboard_publish_module.DashboardPublisher(
            kernel=object(),
            ws_server=FakeWS(),
            dashboard_payload_builder=lambda: {},
            task_payload_builder=lambda *args, **kwargs: {},
        )

        async def run():
            await publisher.emit_adjutant_response(
                "收到指令",
                response_type="command",
                ok=True,
                extra={
                    "ok": True,
                    "timestamp": 123.0,
                    "task_id": "t_cmd",
                    "type": "command",
                },
            )

        asyncio.run(run())
    finally:
        dashboard_publish_module.slog = original_logger

    assert publisher.ws_server.query_responses[0]["answer"] == "收到指令"
    assert publisher.ws_server.query_responses[0]["ok"] is True
    assert publisher.ws_server.query_responses[0]["response_type"] == "command"
    assert publisher.ws_server.query_responses[0]["task_id"] == "t_cmd"
    assert publisher.ws_server.query_response_targets == [None]
    assert logged_info == [
        {
            "event": "adjutant_response_sent",
            "content": "收到指令",
            "response_type": "command",
            "ok": True,
            "task_id": "t_cmd",
            "type": "command",
        }
    ]
    print("  PASS: dashboard_publisher_emit_adjutant_response_ignores_reserved_extra_field_collisions")


def test_dashboard_publisher_replay_history_filters_client_scoped_query_responses():
    class FakeKernel:
        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_to_client(self, client_id, msg_type, data):
            self.sent.append((msg_type, {"client_id": client_id, "data": data}))

    publisher = dashboard_publish_module.DashboardPublisher(
        kernel=FakeKernel(),
        ws_server=FakeWS(),
        dashboard_payload_builder=lambda: {},
        task_payload_builder=lambda *args, **kwargs: {},
    )
    publisher.recent_responses = [
        {"answer": "global", "response_type": "info", "ok": True, "timestamp": 1.0},
        {"answer": "to-a", "response_type": "command", "ok": True, "timestamp": 2.0, "_client_id": "client_a"},
        {"answer": "to-b", "response_type": "reply", "ok": True, "timestamp": 3.0, "_client_id": "client_b"},
    ]

    asyncio.run(publisher.replay_history("client_b"))

    query_responses = [
        payload["data"]
        for msg_type, payload in publisher.ws_server.sent
        if msg_type == "query_response" and payload["client_id"] == "client_b"
    ]
    assert query_responses == [
        {"answer": "global", "response_type": "info", "ok": True, "timestamp": 1.0},
        {"answer": "to-b", "response_type": "reply", "ok": True, "timestamp": 3.0},
    ]
    assert all("_client_id" not in entry for entry in query_responses)
    print("  PASS: dashboard_publisher_replay_history_filters_client_scoped_query_responses")


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
