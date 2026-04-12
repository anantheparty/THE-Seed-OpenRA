"""Unit tests for the live E2E runner's local message plumbing."""

from __future__ import annotations

import os
import sys
import pytest
import asyncio
import json
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class _AsyncFakeWS(_FakeWS):
    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)

    def feed(self, payload: dict[str, Any]) -> None:
        self._queue.put_nowait(json.dumps(payload, ensure_ascii=False))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


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


def test_live_runner_recent_debug_context_includes_world_truth(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()

    runner._handle_message(
        {
            "type": "world_snapshot",
            "data": {
                "stale": True,
                "consecutive_refresh_failures": 4,
                "failure_threshold": 3,
                "last_refresh_error": "actors:COMMAND_EXECUTION_ERROR",
                "player_faction": "allied",
                "capability_truth_blocker": "faction_roster_unsupported",
                "pending_questions": [{"message_id": "q_1"}],
                "runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}},
            },
        }
    )

    debug = runner.recent_debug_context()
    assert "'stale': True" in debug
    assert "'sync_failures': 4" in debug
    assert "'failure_threshold': 3" in debug
    assert "'last_refresh_error': 'actors:COMMAND_EXECUTION_ERROR'" in debug
    assert "'player_faction': 'allied'" in debug
    assert "'capability_truth_blocker': 'faction_roster_unsupported'" in debug
    assert "'active_tasks': 1" in debug
    assert "'pending_questions': 1" in debug


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


def test_live_runner_connect_waits_for_full_ws_baseline(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    fake_ws = _AsyncFakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr(live_e2e.websockets, "connect", fake_connect)
    runner = live_e2e.LiveTestRunner()

    async def run() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(0.01)
            fake_ws.feed({"type": "world_snapshot", "data": {"stale": False}})
            await asyncio.sleep(0.25)
            fake_ws.feed({"type": "task_list", "data": {"tasks": [{"task_id": "t_1"}], "pending_questions": []}})
            await asyncio.sleep(0.25)
            fake_ws.feed({"type": "session_catalog", "data": {"sessions": [{"session_dir": "s_1"}]}})

        asyncio.create_task(_deliver())
        await runner.connect()

        assert runner.latest_world_snapshot()["stale"] is False
        assert runner.latest_task_list()[0]["task_id"] == "t_1"
        assert runner.latest_session_catalog()["sessions"][0]["session_dir"] == "s_1"
        assert json.loads(fake_ws.sent[0])["type"] == "sync_request"

        await runner.close()
        assert fake_ws.closed is True

    asyncio.run(run())


def test_live_runner_send_command_ignores_non_command_or_mismatched_query_responses(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    runner.ws = _FakeWS()

    async def run() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(0.01)
            runner._handle_message(
                {
                    "type": "query_response",
                    "data": {"response_type": "reply", "answer": "这不是命令回执"},
                }
            )
            await asyncio.sleep(0.01)
            runner._handle_message(
                {
                    "type": "query_response",
                    "data": {"response_type": "command", "echo_text": "别的命令", "answer": "不应取这条"},
                }
            )
            await asyncio.sleep(0.01)
            runner._handle_message(
                {
                    "type": "query_response",
                    "data": {"response_type": "command", "echo_text": "推进前线", "answer": "收到指令"},
                }
            )

        asyncio.create_task(_deliver())
        reply = await runner.send_command("推进前线", timeout=0.5)
        assert reply == "收到指令"

    asyncio.run(run())

    sent = json.loads(runner.ws.sent[0])
    assert sent["type"] == "command_submit"
    assert sent["text"] == "推进前线"


class _StructureSuiteRunner:
    def __init__(
        self,
        *,
        counts: list[int],
        task: dict[str, Any] | None = None,
        has_surface: bool = True,
    ) -> None:
        self._counts = list(counts)
        self._count_index = 0
        self._task = dict(task) if isinstance(task, dict) else task
        self._has_surface = has_surface

    def extract_task_id(self, reply: str) -> str | None:
        return "t_build" if "t_build" in reply else None

    async def wait_for_ws_state(self, predicate, timeout=30.0, *, interval=0.2) -> bool:
        del timeout, interval
        return bool(predicate())

    def has_task_surface(self, task_id: str) -> bool:
        return self._has_surface and task_id == "t_build"

    def count_matching_actors(self, expected: str | list[str], *, faction: str = "己方") -> int:
        del expected, faction
        if self._count_index < len(self._counts):
            value = self._counts[self._count_index]
            self._count_index += 1
            return value
        return self._counts[-1] if self._counts else 0

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        if task_id != "t_build" or self._task is None:
            return None
        return dict(self._task)

    def recent_debug_context(self) -> str:
        return "debug-context"


def test_live_suite_wait_for_structure_result_requires_real_count_increase(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1, 1, 2],
            task={"task_id": "t_build", "status": "running"},
        )
    )

    fake_now = {"value": 100.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        result = await suite._wait_for_structure_result(
            expected="powr",
            before=1,
            reply="收到指令，已创建任务 t_build",
            timeout=5.0,
        )
        assert "(before=1, after=2)" in result

    asyncio.run(run())


def test_live_suite_wait_for_structure_result_fails_on_terminal_task_without_count_increase(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1],
            task={"task_id": "t_build", "status": "succeeded"},
        )
    )

    fake_now = {"value": 200.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="terminal status succeeded"):
            await suite._wait_for_structure_result(
                expected="powr",
                before=1,
                reply="收到指令，已创建任务 t_build",
                timeout=5.0,
            )

    asyncio.run(run())


def test_live_suite_wait_for_structure_result_requires_task_surface(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1, 2],
            task={"task_id": "t_build", "status": "running"},
            has_surface=False,
        )
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="never surfaced"):
            await suite._wait_for_structure_result(
                expected="powr",
                before=1,
                reply="收到指令，已创建任务 t_build",
                timeout=5.0,
            )

    asyncio.run(run())

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
