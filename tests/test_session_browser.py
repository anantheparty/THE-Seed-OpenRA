"""Session-browser and diagnostics-history tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import benchmark
import logging_system
from logging_system import start_persistence_session, stop_persistence_session

from main import RuntimeBridge
from session_browser import build_session_history_payload, default_session_dir, resolve_session_dir


def test_session_history_payload_includes_logged_adjutant_responses_and_notifications():
    logging_system.clear()
    benchmark.clear()

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.query_responses: list[dict[str, Any]] = []
            self.player_notifications: list[dict[str, Any]] = []
            self.query_response_targets: list[str | None] = []

        async def send_query_response(self, payload, client_id=None):
            self.query_responses.append(payload)
            self.query_response_targets.append(client_id)

        async def send_player_notification(self, payload):
            self.player_notifications.append(payload)

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

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(FakeWS())

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        session_dir = start_persistence_session(tmpdir, session_name="publisher-history")
        try:
            async def run():
                await bridge._publisher.emit_adjutant_response(
                    "副官收到指令",
                    response_type="command",
                    ok=True,
                    extra={"task_id": "t_resp"},
                )
                await bridge._publisher.emit_notification(
                    "command_cancel",
                    "任务已取消",
                    data={"task_id": "t_resp"},
                )

            asyncio.run(run())
            payload = build_session_history_payload(tmpdir, session_dir=session_dir)
        finally:
            stop_persistence_session()

    events = {(entry.get("event"), entry.get("message")) for entry in payload["log_entries"]}
    assert ("adjutant_response_sent", "副官收到指令") in events
    assert ("player_notification_sent", "任务已取消") in events
    assert any(
        entry.get("event") == "adjutant_response_sent" and entry.get("data", {}).get("task_id") == "t_resp"
        for entry in payload["log_entries"]
    )
    assert any(
        entry.get("event") == "player_notification_sent"
        and entry.get("data", {}).get("data", {}).get("task_id") == "t_resp"
        for entry in payload["log_entries"]
    )
    assert payload["player_visible_entries"] == [
        {
            "kind": "adjutant",
            "timestamp": payload["player_visible_entries"][0]["timestamp"],
            "task_id": "t_resp",
            "content": "副官收到指令",
        },
        {
            "kind": "notification",
            "timestamp": payload["player_visible_entries"][1]["timestamp"],
            "task_id": "t_resp",
            "content": "任务已取消",
        },
    ]
    print("  PASS: session_history_payload_includes_logged_adjutant_responses_and_notifications")

def test_session_history_payload_includes_logged_error_entries():
    logging_system.clear()
    benchmark.clear()

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = start_persistence_session(tmpdir, session_name="error-history")
        try:
            logging_system.get_logger("dashboard_publish").error(
                "Dashboard publish stage failed",
                event="dashboard_publish_stage_failed",
                task_id="t_err",
                error="RuntimeError('publish-boom')",
            )
            payload = build_session_history_payload(tmpdir, session_dir=session_dir)
        finally:
            stop_persistence_session()

    assert payload["error_entries"] == [
        {
            "timestamp": payload["error_entries"][0]["timestamp"],
            "task_id": "t_err",
            "content": "Dashboard publish stage failed | RuntimeError('publish-boom')",
            "component": "dashboard_publish",
            "event": "dashboard_publish_stage_failed",
            "level": "ERROR",
        },
    ]
    print("  PASS: session_history_payload_includes_logged_error_entries")

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

        async def send_session_history_to_client(self, client_id, payload):
            self.sent.append(("session_history", {"client_id": client_id, "payload": payload}))

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
            (Path(session_dir) / "all.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": 11.0,
                        "component": "kernel",
                        "level": "INFO",
                        "message": "历史日志",
                        "event": "task_info",
                        "data": {"task_id": "t_select"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (Path(session_dir) / "benchmark_records.json").write_text(
                json.dumps(
                    [
                        {
                            "tag": "tool_exec",
                            "name": "history_bench",
                            "started_at": "2026-04-12T00:00:00+00:00",
                            "ended_at": "2026-04-12T00:00:01+00:00",
                            "duration_ms": 1.0,
                            "metadata": {},
                        }
                    ],
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            bridge.log_session_root = tmpdir
            stop_persistence_session()

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
    assert ws.sent[2][0] == "session_history"
    assert ws.sent[2][1]["payload"]["session_dir"] == str(session_dir)
    assert ws.sent[2][1]["payload"]["is_live"] is False
    assert [entry["message"] for entry in ws.sent[2][1]["payload"]["log_entries"]] == ["历史日志"]
    assert [entry["name"] for entry in ws.sent[2][1]["payload"]["benchmark_records"]] == ["history_bench"]
    print("  PASS: session_select_returns_catalog_and_task_catalog")

def test_session_select_rejects_requested_session_outside_root():
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

        async def send_session_history_to_client(self, client_id, payload):
            self.sent.append(("session_history", {"client_id": client_id, "payload": payload}))

        async def send_error_to_client(self, client_id, message, *, code="INVALID_MESSAGE", inbound_type=None, extra=None):
            self.sent.append(
                (
                    "error",
                    {
                        "client_id": client_id,
                        "message": message,
                        "code": code,
                        "inbound_type": inbound_type,
                        "extra": dict(extra or {}),
                    },
                )
            )

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
        root = Path(tmpdir) / "logs"
        outside_root = Path(tmpdir) / "outside"
        inside_session = start_persistence_session(root, session_name="inside-session")
        try:
            inside_task_path = inside_session / "tasks" / "t_select.jsonl"
            inside_task_path.parent.mkdir(parents=True, exist_ok=True)
            inside_task_path.write_text(
                json.dumps(
                    {
                        "timestamp": 10.0,
                        "component": "kernel",
                        "level": "INFO",
                        "message": "Task created",
                        "event": "task_created",
                        "data": {"task_id": "t_select", "raw_text": "根目录会话", "priority": 40},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            outside_session = start_persistence_session(outside_root, session_name="outside-session")
            outside_task_path = outside_session / "tasks" / "t_select.jsonl"
            outside_task_path.parent.mkdir(parents=True, exist_ok=True)
            outside_task_path.write_text(
                json.dumps(
                    {
                        "timestamp": 11.0,
                        "component": "kernel",
                        "level": "INFO",
                        "message": "Task created",
                        "event": "task_created",
                        "data": {"task_id": "t_select", "raw_text": "越界会话", "priority": 50},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            bridge.log_session_root = str(root)
            stop_persistence_session()

            async def run():
                await bridge.on_session_select(str(outside_session), "client_10")

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent == [
        (
            "error",
            {
                "client_id": "client_10",
                "message": f"Invalid session_select: unknown session_dir {outside_session}",
                "code": "INVALID_SESSION",
                "inbound_type": "session_select",
                "extra": {},
            },
        )
    ]
    print("  PASS: session_select_rejects_requested_session_outside_root")

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

def test_resolve_session_dir_rejects_existing_path_outside_root():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "logs"
        outside_root = Path(tmpdir) / "outside"
        inside_session = start_persistence_session(root, session_name="inside")
        try:
            outside_session = start_persistence_session(outside_root, session_name="outside")
            assert resolve_session_dir(str(root), str(inside_session)) == inside_session
            assert resolve_session_dir(str(root), str(outside_session)) is None
            assert resolve_session_dir(str(root), str(Path("..") / "outside" / outside_session.name)) is None
        finally:
            stop_persistence_session()

    print("  PASS: resolve_session_dir_rejects_existing_path_outside_root")
