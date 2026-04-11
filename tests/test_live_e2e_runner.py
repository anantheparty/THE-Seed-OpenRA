"""Unit tests for the live E2E runner's local message plumbing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import tests.test_live_e2e as live_e2e


class _FakeGameAPI:
    def __init__(self, host: str, *, port: int, language: str) -> None:
        self.host = host
        self.port = port
        self.language = language
        self.closed = False

    def query_actor(self, _param: Any) -> list[Any]:
        return []

    def close(self) -> None:
        self.closed = True


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def test_live_runner_merges_task_updates_into_latest_task_view(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()

    runner._handle_message(
        {
            "type": "task_list",
            "data": {
                "tasks": [{"task_id": "t_1", "status": "pending", "raw_text": "建造电厂"}],
                "pending_questions": [{"message_id": "q_1"}],
            },
        }
    )
    runner._handle_message(
        {
            "type": "task_update",
            "data": {"task_id": "t_1", "status": "running", "job_count": 1},
        }
    )
    runner._handle_message(
        {
            "type": "task_update",
            "data": {"task_id": "t_2", "status": "pending", "raw_text": "探索地图"},
        }
    )

    assert runner.get_task("t_1")["status"] == "running"
    assert runner.get_task("t_1")["job_count"] == 1
    assert runner.get_task("t_2")["raw_text"] == "探索地图"
    assert runner.latest_task_list()[0]["task_id"] == "t_1"
    assert runner._pending_questions[0]["message_id"] == "q_1"


def test_live_runner_extract_task_id_accepts_readable_non_hex_ids(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()

    assert runner.extract_task_id("副官收到指令，已创建任务 t_1") == "t_1"
    assert runner.extract_task_id("副官收到指令，已创建任务 t_f22aa872") == "t_f22aa872"
    assert runner.extract_task_id("副官收到指令，已创建任务 t_seq-007") == "t_seq-007"


def test_live_runner_captures_diagnostics_payloads_and_task_replay(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()

    runner._handle_message({"type": "session_catalog", "data": {"sessions": [{"session_dir": "s_1"}]}})
    runner._handle_message({"type": "session_task_catalog", "data": {"tasks": [{"task_id": "t_1"}]}})
    runner._handle_message({"type": "benchmark", "data": {"records": [{"name": "tool_exec"}], "replace": False}})
    runner._handle_message({"type": "task_message", "data": {"task_id": "t_1", "content": "正在执行"}})
    runner._handle_message({"type": "task_replay", "data": {"task_id": "t_1", "summary": "done"}})
    runner._handle_message({"type": "error", "message": "Unknown message type: game_start", "code": "INVALID_MESSAGE"})
    runner._handle_message({"type": "log_entry", "data": {"message": "log-1"}})
    runner._handle_message({"type": "player_notification", "data": {"content": "notify-1"}})

    assert runner.latest_session_catalog()["sessions"][0]["session_dir"] == "s_1"
    assert runner.latest_session_task_catalog()["tasks"][0]["task_id"] == "t_1"
    assert runner.latest_benchmarks()[-1]["records"][0]["name"] == "tool_exec"
    assert runner.latest_task_messages()[-1]["content"] == "正在执行"
    assert runner.latest_task_replay("t_1")["summary"] == "done"
    assert runner.latest_errors()[-1]["message"] == "Unknown message type: game_start"
    debug = runner.recent_debug_context()
    assert "Unknown message type: game_start" in debug
    assert "log-1" in debug
    assert "正在执行" in debug


def test_live_runner_has_task_surface_from_update_or_message(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()

    assert runner.has_task_surface("t_1") is False

    runner._handle_message({"type": "task_message", "data": {"task_id": "t_1", "content": "正在执行"}})
    assert runner.has_task_surface("t_1") is True

    runner._handle_message({"type": "task_update", "data": {"task_id": "t_2", "status": "running"}})
    assert runner.has_task_surface("t_2") is True


def test_live_runner_request_task_replay_waits_for_matching_payload(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    runner.ws = _FakeWS()

    async def run() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(0.01)
            runner._handle_message({"type": "task_replay", "data": {"task_id": "t_9", "summary": "ready"}})

        asyncio.create_task(_deliver())
        payload = await runner.request_task_replay("t_9", include_entries=False, timeout=0.5)
        assert payload == {"task_id": "t_9", "summary": "ready"}

    asyncio.run(run())

    sent = json.loads(runner.ws.sent[0])
    assert sent["type"] == "task_replay_request"
    assert sent["task_id"] == "t_9"
    assert sent["include_entries"] is False
