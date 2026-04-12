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
from openra_api.models import Actor, Location


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
                "runtime_fault_state": {
                    "degraded": True,
                    "source": "dashboard_publish",
                    "stage": "task_messages",
                    "error": "RuntimeError('publish-boom')",
                },
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
    assert "'runtime_fault_degraded': True" in debug
    assert "'runtime_fault_source': 'dashboard_publish'" in debug
    assert "'runtime_fault_stage': 'task_messages'" in debug
    assert "'runtime_fault_error': \"RuntimeError('publish-boom')\"" in debug
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
            fake_ws.feed({"type": "world_snapshot", "data": {"stale": False, "runtime_fault_state": {}}})
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


def test_live_runner_connect_fails_closed_when_world_snapshot_baseline_is_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    fake_ws = _AsyncFakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr(live_e2e.websockets, "connect", fake_connect)
    runner = live_e2e.LiveTestRunner()

    original_wait = runner.wait_for_ws_state

    async def short_wait(predicate, timeout=5.0):
        return await original_wait(predicate, timeout=0.05)

    runner.wait_for_ws_state = short_wait  # type: ignore[method-assign]

    async def run() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(0.01)
            fake_ws.feed({"type": "world_snapshot", "data": {"stale": False}})
            await asyncio.sleep(0.01)
            fake_ws.feed({"type": "task_list", "data": {"tasks": [{"task_id": "t_1"}], "pending_questions": []}})
            await asyncio.sleep(0.01)
            fake_ws.feed({"type": "session_catalog", "data": {"sessions": [{"session_dir": "s_1"}]}})

        asyncio.create_task(_deliver())
        with pytest.raises(RuntimeError, match="websocket baseline incomplete after sync_request"):
            await runner.connect()

        assert "'stale': False" in runner.recent_debug_context()
        assert "'runtime_fault_degraded': False" in runner.recent_debug_context()
        await runner.close()
        assert fake_ws.closed is True

    asyncio.run(run())


def test_live_runner_matching_actor_positions_only_keeps_matching_mobile_actors(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    runner.query_actors = lambda faction="己方": [  # type: ignore[method-assign]
        Actor(actor_id=1, type="e1", position=Location(10, 10)),
        Actor(actor_id=2, type="powr", position=Location(20, 20)),
        Actor(actor_id=3, type="e3", position=Location(30, 30)),
    ]

    positions = runner.matching_actor_positions(["e1", "e3"])

    assert positions == {1: (10, 10), 3: (30, 30)}


def test_live_runner_detects_matching_actor_movement_from_baseline(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    before_positions = {1: (10, 10), 3: (30, 30)}
    actors = [
        Actor(actor_id=1, type="e1", position=Location(12, 11)),
        Actor(actor_id=2, type="powr", position=Location(50, 50)),
        Actor(actor_id=4, type="e1", position=Location(99, 99)),
    ]

    assert runner.any_matching_actor_moved(
        actors,
        before_positions,
        ["e1", "e3"],
        min_manhattan_distance=2,
    ) is True
    assert runner.any_matching_actor_moved(
        actors,
        before_positions,
        ["e1", "e3"],
        min_manhattan_distance=5,
    ) is False


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


def test_live_runner_send_player_input_response_accepts_query_payload(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    runner.ws = _FakeWS()

    async def run() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(0.01)
            runner._handle_message(
                {
                    "type": "query_response",
                    "data": {
                        "response_type": "query",
                        "echo_text": "战况如何？",
                        "answer": "当前现金3200，兵力稳定，地图左侧未明。",
                    },
                }
            )

        asyncio.create_task(_deliver())
        payload = await runner.send_player_input_response("战况如何？", timeout=0.5)
        assert payload["response_type"] == "query"
        assert "当前现金3200" in payload["answer"]

    asyncio.run(run())


def test_live_runner_send_command_timeout_includes_runtime_fault_context(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    runner = live_e2e.LiveTestRunner()
    runner.ws = _FakeWS()
    runner._handle_message(
        {
            "type": "world_snapshot",
            "data": {
                "stale": False,
                "runtime_fault_state": {
                    "degraded": True,
                    "source": "dashboard_publish",
                    "stage": "task_messages",
                    "error": "RuntimeError('publish-boom')",
                },
            },
        }
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="command reply timed out: 推进前线") as excinfo:
            await runner.send_command("推进前线", timeout=0.01)

        message = str(excinfo.value)
        assert "'runtime_fault_degraded': True" in message
        assert "'runtime_fault_source': 'dashboard_publish'" in message
        assert "'runtime_fault_stage': 'task_messages'" in message
        assert "'runtime_fault_error': \"RuntimeError('publish-boom')\"" in message

    asyncio.run(run())


class _StructureSuiteRunner:
    def __init__(
        self,
        *,
        counts: list[int],
        task: dict[str, Any] | None = None,
        task_snapshots: list[dict[str, Any]] | None = None,
        has_surface: bool = True,
    ) -> None:
        self._counts = list(counts)
        self._count_index = 0
        self._task = dict(task) if isinstance(task, dict) else task
        self._task_snapshots = [
            dict(item) for item in list(task_snapshots or []) if isinstance(item, dict)
        ]
        self._task_snapshot_index = 0
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
        if self._task_snapshots:
            if self._task_snapshot_index < len(self._task_snapshots):
                value = dict(self._task_snapshots[self._task_snapshot_index])
                self._task_snapshot_index += 1
                return value
            return dict(self._task_snapshots[-1])
        return dict(self._task)

    def recent_debug_context(self) -> str:
        return "debug-context"


class _QuerySuiteRunner:
    def __init__(
        self,
        response: dict[str, Any],
        *,
        task_lists: list[list[dict[str, Any]]] | None = None,
        world_snapshots: list[dict[str, Any]] | None = None,
    ) -> None:
        self._response = dict(response)
        self._task_lists = [
            [dict(item) for item in list(snapshot or []) if isinstance(item, dict)]
            for snapshot in list(task_lists or [[]])
        ]
        self._task_list_index = 0
        self._world_snapshots = [
            dict(snapshot) for snapshot in list(world_snapshots or [{"runtime_state": {"active_tasks": {}}}])
            if isinstance(snapshot, dict)
        ]
        self._world_snapshot_index = 0

    async def send_player_input_response(
        self,
        text: str,
        timeout: float = 30.0,
        *,
        response_types: set[str] | None = None,
    ) -> dict[str, Any]:
        del text, timeout
        if response_types is not None and str(self._response.get("response_type") or "") not in response_types:
            raise RuntimeError("unexpected response_type for query suite runner")
        return dict(self._response)

    def extract_task_id(self, reply: str) -> str | None:
        return live_e2e.LiveTestRunner.extract_task_id(reply)

    def latest_task_list(self) -> list[dict[str, Any]]:
        if self._task_list_index < len(self._task_lists):
            value = [dict(item) for item in self._task_lists[self._task_list_index]]
            self._task_list_index += 1
            return value
        return [dict(item) for item in self._task_lists[-1]]

    def latest_world_snapshot(self) -> dict[str, Any]:
        if self._world_snapshot_index < len(self._world_snapshots):
            value = dict(self._world_snapshots[self._world_snapshot_index])
            self._world_snapshot_index += 1
            return value
        return dict(self._world_snapshots[-1])

    def recent_debug_context(self) -> str:
        return "debug-context"


class _ReconSuiteRunner:
    actor_matches_expected = staticmethod(live_e2e.LiveTestRunner.actor_matches_expected)
    actor_positions = staticmethod(live_e2e.LiveTestRunner.actor_positions)

    def __init__(
        self,
        *,
        actor_snapshots: list[list[Actor]],
        task: dict[str, Any] | None = None,
        has_surface: bool = True,
        active_jobs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._actor_snapshots = [list(snapshot) for snapshot in actor_snapshots]
        self._actor_index = 0
        self._task = dict(task) if isinstance(task, dict) else task
        self._has_surface = has_surface
        self._active_jobs = dict(active_jobs or {})

    def extract_task_id(self, reply: str) -> str | None:
        return "t_recon" if "t_recon" in reply else None

    async def wait_for_ws_state(self, predicate, timeout=30.0, *, interval=0.2) -> bool:
        del timeout, interval
        return bool(predicate())

    def has_task_surface(self, task_id: str) -> bool:
        return self._has_surface and task_id == "t_recon"

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        if task_id != "t_recon" or self._task is None:
            return None
        return dict(self._task)

    def latest_world_snapshot(self) -> dict[str, Any]:
        return {"runtime_state": {"active_jobs": dict(self._active_jobs)}}

    def query_actors(self, faction: str = "己方") -> list[Actor]:
        del faction
        if self._actor_index < len(self._actor_snapshots):
            value = list(self._actor_snapshots[self._actor_index])
            self._actor_index += 1
            return value
        return list(self._actor_snapshots[-1]) if self._actor_snapshots else []

    def matching_actor_positions(self, expected: str | list[str], *, faction: str = "己方") -> dict[int, tuple[int, int]]:
        actors = [
            actor
            for actor in self.query_actors(faction=faction)
            if self.actor_matches_expected(actor, expected)
        ]
        return self.actor_positions(actors)

    def any_matching_actor_moved(
        self,
        actors: list[Actor],
        before_positions: dict[int, tuple[int, int]],
        expected: str | list[str],
        *,
        min_manhattan_distance: int = 2,
    ) -> bool:
        return live_e2e.LiveTestRunner.any_matching_actor_moved(
            self,
            actors,
            before_positions,
            expected,
            min_manhattan_distance=min_manhattan_distance,
        )

    def recent_debug_context(self) -> str:
        return "debug-context"


def test_live_suite_wait_for_actor_count_increase_result_requires_real_count_increase(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1, 1, 2],
            task={"task_id": "t_build", "status": "running"},
            task_snapshots=[
                {"task_id": "t_build", "status": "running"},
                {"task_id": "t_build", "status": "running"},
                {"task_id": "t_build", "status": "succeeded"},
            ],
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
        result = await suite._wait_for_actor_count_increase_result(
            expected="powr",
            before=1,
            reply="收到指令，已创建任务 t_build",
            timeout=5.0,
            min_delta=1,
            label="structure count",
        )
        assert "before=1" in result
        assert "after=2" in result
        assert "settle=task_succeeded" in result

    asyncio.run(run())


def test_live_suite_wait_for_actor_count_increase_result_fails_on_terminal_task_without_count_increase(monkeypatch) -> None:
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
            await suite._wait_for_actor_count_increase_result(
                expected="powr",
                before=1,
                reply="收到指令，已创建任务 t_build",
                timeout=5.0,
                min_delta=1,
                label="structure count",
            )

    asyncio.run(run())


def test_live_suite_wait_for_actor_count_increase_result_fails_when_non_capability_task_never_settles_after_count_increase(
    monkeypatch,
) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1, 1, 2],
            task={"task_id": "t_build", "status": "running"},
            task_snapshots=[
                {"task_id": "t_build", "status": "running"},
                {"task_id": "t_build", "status": "running"},
                {"task_id": "t_build", "status": "running"},
                {"task_id": "t_build", "status": "running"},
            ],
        )
    )

    fake_now = {"value": 220.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="did not settle after structure count changed"):
            await suite._wait_for_actor_count_increase_result(
                expected="powr",
                before=1,
                reply="收到指令，已创建任务 t_build",
                timeout=5.0,
                min_delta=1,
                label="structure count",
            )

    asyncio.run(run())


def test_live_suite_wait_for_actor_count_increase_result_allows_capability_task_to_keep_running_after_count_increase(
    monkeypatch,
) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[1, 1, 2],
            task={"task_id": "t_build", "status": "running", "is_capability": True},
            task_snapshots=[
                {"task_id": "t_build", "status": "running", "is_capability": True},
                {"task_id": "t_build", "status": "running", "is_capability": True},
                {"task_id": "t_build", "status": "running", "is_capability": True},
                {"task_id": "t_build", "status": "running", "is_capability": True},
            ],
        )
    )

    fake_now = {"value": 240.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        result = await suite._wait_for_actor_count_increase_result(
            expected="powr",
            before=1,
            reply="收到指令，已创建任务 t_build",
            timeout=5.0,
            min_delta=1,
            label="structure count",
        )
        assert "settle=task_running_capability" in result

    asyncio.run(run())


def test_live_suite_wait_for_actor_count_increase_result_requires_task_surface(monkeypatch) -> None:
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
            await suite._wait_for_actor_count_increase_result(
                expected="powr",
                before=1,
                reply="收到指令，已创建任务 t_build",
                timeout=5.0,
                min_delta=1,
                label="structure count",
            )

    asyncio.run(run())


def test_live_suite_phase_c_produce_infantry_fails_on_terminal_task_before_count_increase(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[5],
            task={"task_id": "t_build", "status": "succeeded"},
        )
    )

    async def _fake_send_command(text: str, timeout: float = 30.0) -> str:
        del timeout
        assert text == "生产3个步兵"
        return "收到指令，已创建任务 t_build"

    suite.runner.send_command = _fake_send_command  # type: ignore[method-assign]

    fake_now = {"value": 300.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="before infantry count increased by 3"):
            await suite.test_phase_c_produce_infantry()

    asyncio.run(run())


def test_live_suite_phase_b_deploy_mcv_fails_on_terminal_task_before_construction_yard_appears(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _StructureSuiteRunner(
            counts=[0, 1, 0],
            task={"task_id": "t_build", "status": "succeeded"},
        )
    )

    async def _fake_send_command(text: str, timeout: float = 30.0) -> str:
        del timeout
        assert text == "部署基地车"
        return "收到指令，已创建任务 t_build"

    suite.runner.send_command = _fake_send_command  # type: ignore[method-assign]

    fake_now = {"value": 250.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="before construction yard count increased by 1"):
            await suite.test_phase_b_deploy_mcv()

    asyncio.run(run())


def test_live_suite_phase_d_recon_fails_on_terminal_task_before_scout_movement(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _ReconSuiteRunner(
            actor_snapshots=[
                [Actor(actor_id=11, type="e1", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle")],
                [Actor(actor_id=11, type="e1", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle")],
                [Actor(actor_id=11, type="e1", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle")],
            ],
            task={"task_id": "t_recon", "status": "succeeded"},
            active_jobs={"j_recon": {"task_id": "t_recon", "expert_type": "ReconExpert"}},
        )
    )

    async def _fake_send_command(text: str, timeout: float = 30.0) -> str:
        del timeout
        assert text == "探索地图"
        return "收到指令，已创建任务 t_recon"

    suite.runner.send_command = _fake_send_command  # type: ignore[method-assign]

    fake_now = {"value": 350.0}

    def _fake_time() -> float:
        return fake_now["value"]

    async def _fake_sleep(delay: float) -> None:
        fake_now["value"] += delay

    monkeypatch.setattr(live_e2e.time, "time", _fake_time)
    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="before scout movement"):
            await suite.test_phase_d_recon()

    asyncio.run(run())


def test_live_suite_phase_e_query_requires_pure_query_contract(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _QuerySuiteRunner(
            {
                "response_type": "query",
                "answer": "当前现金3200，经济稳定，己方单位正在推进，地图左侧仍有未知区域，敌军单位暂未接触，战况总体可控，建议继续侦察并关注矿区。",
            },
            task_lists=[
                [{"task_id": "t_cap", "status": "running", "raw_text": "发展经济"}],
                [{"task_id": "t_cap", "status": "running", "raw_text": "发展经济"}],
            ],
            world_snapshots=[
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}}},
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}}},
            ],
        )
    )

    async def _fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        reply = await suite.test_phase_e_query()
        assert "当前现金3200" in reply

    asyncio.run(run())


def test_live_suite_phase_e_query_fails_if_task_metadata_leaks_into_reply(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _QuerySuiteRunner(
            {
                "response_type": "query",
                "task_id": "t_query",
                "answer": "当前现金3200，经济稳定，己方单位正在推进，地图左侧仍有未知区域，敌军单位暂未接触，战况总体可控，建议继续侦察并关注矿区。",
            }
        )
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="unexpectedly attached task metadata"):
            await suite.test_phase_e_query()

    asyncio.run(run())


def test_live_suite_phase_e_query_fails_if_new_task_ids_appear_after_reply(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _QuerySuiteRunner(
            {
                "response_type": "query",
                "answer": "当前现金3200，经济稳定，己方单位正在推进，地图左侧仍有未知区域，敌军单位暂未接触，战况总体可控，建议继续侦察并关注矿区。",
            },
            task_lists=[
                [{"task_id": "t_cap", "status": "running", "raw_text": "发展经济"}],
                [
                    {"task_id": "t_cap", "status": "running", "raw_text": "发展经济"},
                    {"task_id": "t_query", "status": "running", "raw_text": "战况如何？"},
                ],
            ],
            world_snapshots=[
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}}},
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}}},
            ],
        )
    )

    async def _fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="unexpectedly created visible task ids"):
            await suite.test_phase_e_query()

    asyncio.run(run())


def test_live_suite_phase_e_query_fails_if_runtime_active_tasks_grow_after_reply(monkeypatch) -> None:
    monkeypatch.setattr(live_e2e, "GameAPI", _FakeGameAPI)
    suite = live_e2e.LiveTestSuite(
        _QuerySuiteRunner(
            {
                "response_type": "query",
                "answer": "当前现金3200，经济稳定，己方单位正在推进，地图左侧仍有未知区域，敌军单位暂未接触，战况总体可控，建议继续侦察并关注矿区。",
            },
            task_lists=[
                [{"task_id": "t_cap", "status": "running", "raw_text": "发展经济"}],
                [{"task_id": "t_cap", "status": "running", "raw_text": "发展经济"}],
            ],
            world_snapshots=[
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}}}},
                {"runtime_state": {"active_tasks": {"t_cap": {"label": "001"}, "t_query": {"label": "004"}}}},
            ],
        )
    )

    async def _fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(live_e2e.asyncio, "sleep", _fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="unexpectedly added runtime active tasks"):
            await suite.test_phase_e_query()

    asyncio.run(run())

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
