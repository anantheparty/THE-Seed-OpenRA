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
