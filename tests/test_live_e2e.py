"""Executable live E2E runner against a real game + backend.

Usage:
    python3 tests/test_live_e2e.py
    python3 tests/test_live_e2e.py phase_a
    python3 tests/test_live_e2e.py phase_b
    python3 tests/test_live_e2e.py phase_c
    python3 tests/test_live_e2e.py phase_d
    python3 tests/test_live_e2e.py phase_e

See also:
    docs/live_e2e_checklist.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import pytest
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api.game_api import GameAPI
from openra_api.models import Actor, TargetsQueryParam
from openra_api.production_names import production_name_matches
from unit_registry import normalize_registry_name

pytestmark = pytest.mark.live


MAX_SIZE = 10 * 1024 * 1024
SCOUT_CANDIDATE_TYPES = ["e1", "e3", "dog", "jeep", "ftrk", "1tnk", "2tnk", "3tnk", "4tnk", "yak", "mig"]


@dataclass
class CaseResult:
    name: str
    ok: bool
    elapsed_s: float
    detail: str


class LiveTestRunner:
    """Connects to a real backend WS and live GameAPI for automation."""

    def __init__(
        self,
        *,
        ws_url: str = "ws://127.0.0.1:8765/ws",
        game_host: str = "127.0.0.1",
        game_port: int = 7445,
        game_language: str = "zh",
    ) -> None:
        self.ws_url = ws_url
        self.game_host = game_host
        self.game_port = game_port
        self.api = GameAPI(game_host, port=game_port, language=game_language)
        self.ws: Any = None
        self._receiver_task: Optional[asyncio.Task[Any]] = None
        self._query_responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task_list: list[dict[str, Any]] = []
        self._task_index: dict[str, dict[str, Any]] = {}
        self._pending_questions: list[dict[str, Any]] = []
        self._world_snapshot: dict[str, Any] = {}
        self._notifications: deque[dict[str, Any]] = deque(maxlen=20)
        self._logs: deque[dict[str, Any]] = deque(maxlen=50)
        self._task_messages: deque[dict[str, Any]] = deque(maxlen=50)
        self._benchmarks: deque[dict[str, Any]] = deque(maxlen=20)
        self._errors: deque[dict[str, Any]] = deque(maxlen=20)
        self._session_catalog: dict[str, Any] = {}
        self._session_task_catalog: dict[str, Any] = {}
        self._task_replays: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        self.ws = await websockets.connect(
            self.ws_url,
            max_size=MAX_SIZE,
            proxy=None,
        )
        self._receiver_task = asyncio.create_task(self._recv_loop())
        await self._send({"type": "sync_request"})
        ok = await self.wait_for_ws_state(
            lambda: (
                bool(self._world_snapshot)
                and isinstance(self._world_snapshot.get("runtime_fault_state"), dict)
                and bool(self._task_list)
                and bool(self._session_catalog)
            ),
            timeout=5.0,
        )
        if not ok:
            raise RuntimeError(f"websocket baseline incomplete after sync_request; {self.recent_debug_context()}")

    async def close(self) -> None:
        if self._receiver_task is not None:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
        if self.ws is not None:
            await self.ws.close()
        self.api.close()

    async def _recv_loop(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self._handle_message(msg)

    def _apply_task_list(self, tasks: list[dict[str, Any]]) -> None:
        normalized: list[dict[str, Any]] = []
        index: dict[str, dict[str, Any]] = {}
        for item in list(tasks or []):
            if not isinstance(item, dict):
                continue
            task = dict(item)
            task_id = str(task.get("task_id") or "")
            if not task_id:
                continue
            normalized.append(task)
            index[task_id] = task
        self._task_list = normalized
        self._task_index = index

    def _apply_task_update(self, update: dict[str, Any]) -> None:
        if not isinstance(update, dict):
            return
        task_id = str(update.get("task_id") or "")
        if not task_id:
            return
        merged = dict(self._task_index.get(task_id) or {})
        merged.update(dict(update))
        self._task_index[task_id] = merged
        for index, existing in enumerate(self._task_list):
            if str(existing.get("task_id") or "") == task_id:
                self._task_list[index] = merged
                break
        else:
            self._task_list.append(merged)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        data = msg.get("data", {})
        if msg_type == "query_response":
            self._query_responses.put_nowait(dict(msg))
        elif msg_type == "task_list":
            self._apply_task_list(list(data.get("tasks", [])))
            self._pending_questions = list(data.get("pending_questions", []))
        elif msg_type == "task_update":
            self._apply_task_update(dict(data))
        elif msg_type == "world_snapshot":
            self._world_snapshot = dict(data)
            if data.get("pending_questions") is not None:
                self._pending_questions = list(data.get("pending_questions", []))
        elif msg_type == "player_notification":
            self._notifications.append(dict(msg))
        elif msg_type == "log_entry":
            self._logs.append(dict(msg))
        elif msg_type == "task_message":
            self._task_messages.append(dict(data))
        elif msg_type == "benchmark":
            self._benchmarks.append(dict(data))
        elif msg_type == "session_catalog":
            self._session_catalog = dict(data)
        elif msg_type == "session_task_catalog":
            self._session_task_catalog = dict(data)
        elif msg_type == "task_replay":
            replay = dict(data)
            task_id = str(replay.get("task_id") or "")
            if task_id:
                self._task_replays[task_id] = replay
        elif msg_type == "error":
            self._errors.append(dict(msg))

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self.ws is not None
        outgoing = dict(payload)
        outgoing.setdefault("timestamp", time.time())
        await self.ws.send(json.dumps(outgoing, ensure_ascii=False))

    async def request_task_replay(
        self,
        task_id: str,
        *,
        include_entries: bool = False,
        session_dir: str | None = None,
        timeout: float = 5.0,
    ) -> Optional[dict[str, Any]]:
        self._task_replays.pop(task_id, None)
        payload: dict[str, Any] = {
            "type": "task_replay_request",
            "task_id": task_id,
            "include_entries": include_entries,
        }
        if session_dir:
            payload["session_dir"] = session_dir
        await self._send(payload)
        ok = await self.wait_for_ws_state(lambda: task_id in self._task_replays, timeout=timeout)
        if not ok:
            return None
        return dict(self._task_replays.get(task_id) or {})

    async def send_player_input_response(
        self,
        text: str,
        timeout: float = 30.0,
        *,
        response_types: set[str] | None = None,
    ) -> dict[str, Any]:
        while not self._query_responses.empty():
            try:
                self._query_responses.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._send({"type": "command_submit", "text": text})
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError(
                    f"command reply timed out: {text}; {self.recent_debug_context()}"
                )
            try:
                msg = await asyncio.wait_for(self._query_responses.get(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"command reply timed out: {text}; {self.recent_debug_context()}"
                ) from exc
            data = msg.get("data", {})
            response_type = str(data.get("response_type") or "")
            if response_types is not None and response_type not in response_types:
                continue
            echo_text = str(data.get("echo_text") or "")
            if echo_text and echo_text != text:
                continue
            return dict(data)

    async def send_command_response(self, text: str, timeout: float = 30.0) -> dict[str, Any]:
        return await self.send_player_input_response(text, timeout=timeout, response_types={"command"})

    async def send_command(self, text: str, timeout: float = 30.0) -> str:
        data = await self.send_command_response(text, timeout=timeout)
        return str(
            data.get("answer")
            or data.get("response_text")
            or data.get("content")
            or json.dumps(data, ensure_ascii=False)
        )

    async def wait_for_game_state(
        self,
        predicate: Callable[[list[Actor]], bool],
        timeout: float = 60.0,
        *,
        faction: str = "己方",
        interval: float = 1.0,
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            actors = self.query_actors(faction=faction)
            if predicate(actors):
                return True
            await asyncio.sleep(interval)
        return False

    async def wait_for_ws_state(
        self,
        predicate: Callable[[], bool],
        timeout: float = 30.0,
        *,
        interval: float = 0.2,
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(interval)
        return False

    def query_actors(self, faction: str = "己方") -> list[Actor]:
        normalized = {"己方": "自己", "self": "自己", "敌方": "敌人", "enemy": "敌人"}.get(faction, faction)
        return self.api.query_actor(TargetsQueryParam(faction=normalized))

    def check_backend_running(self) -> bool:
        parsed = urlparse(self.ws_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            return False

    def latest_task_list(self) -> list[dict[str, Any]]:
        return list(self._task_list)

    def latest_world_snapshot(self) -> dict[str, Any]:
        return dict(self._world_snapshot)

    def latest_session_catalog(self) -> dict[str, Any]:
        return dict(self._session_catalog)

    def latest_session_task_catalog(self) -> dict[str, Any]:
        return dict(self._session_task_catalog)

    def latest_benchmarks(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self._benchmarks)]

    def latest_errors(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self._errors)]

    def latest_task_messages(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self._task_messages)]

    def latest_task_replay(self, task_id: str) -> Optional[dict[str, Any]]:
        replay = self._task_replays.get(task_id)
        return dict(replay) if isinstance(replay, dict) else None

    def has_task_surface(self, task_id: str) -> bool:
        if self.get_task(task_id) is not None:
            return True
        return any(str(item.get("task_id") or "") == task_id for item in self._task_messages)

    @staticmethod
    def extract_task_id(reply: str) -> Optional[str]:
        match = re.search(r"\b(t_[A-Za-z0-9][A-Za-z0-9_-]*)\b", reply)
        if match:
            return match.group(1)
        return None

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        task = self._task_index.get(task_id)
        return dict(task) if isinstance(task, dict) else None

    @staticmethod
    def actor_matches_expected(actor: Actor, expected: str | list[str]) -> bool:
        expected_names = [expected] if isinstance(expected, str) else list(expected)
        observed = getattr(actor, "type", None)
        normalized_observed = normalize_registry_name(observed)
        return any(
            production_name_matches(name, observed)
            or (
                normalized_observed
                and normalize_registry_name(name)
                and normalize_registry_name(name) in normalized_observed
            )
            for name in expected_names
        )

    def matching_actors(self, expected: str | list[str], *, faction: str = "己方") -> list[Actor]:
        return [
            actor
            for actor in self.query_actors(faction=faction)
            if self.actor_matches_expected(actor, expected)
        ]

    @staticmethod
    def actor_positions(actors: list[Actor]) -> dict[int, tuple[int, int]]:
        positions: dict[int, tuple[int, int]] = {}
        for actor in list(actors or []):
            actor_id = int(getattr(actor, "actor_id", getattr(actor, "id", 0)) or 0)
            position = getattr(actor, "position", None)
            if actor_id <= 0 or position is None:
                continue
            positions[actor_id] = (int(position.x), int(position.y))
        return positions

    def matching_actor_positions(self, expected: str | list[str], *, faction: str = "己方") -> dict[int, tuple[int, int]]:
        return self.actor_positions(self.matching_actors(expected, faction=faction))

    def any_matching_actor_moved(
        self,
        actors: list[Actor],
        before_positions: dict[int, tuple[int, int]],
        expected: str | list[str],
        *,
        min_manhattan_distance: int = 2,
    ) -> bool:
        for actor in list(actors or []):
            if not self.actor_matches_expected(actor, expected):
                continue
            actor_id = int(getattr(actor, "actor_id", getattr(actor, "id", 0)) or 0)
            if actor_id <= 0 or actor_id not in before_positions:
                continue
            position = getattr(actor, "position", None)
            if position is None:
                continue
            before_x, before_y = before_positions[actor_id]
            if abs(int(position.x) - before_x) + abs(int(position.y) - before_y) >= min_manhattan_distance:
                return True
        return False

    async def wait_for_task_status(
        self,
        task_id: str,
        statuses: set[str],
        timeout: float = 30.0,
        *,
        interval: float = 0.2,
    ) -> Optional[dict[str, Any]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get_task(task_id)
            if task is not None and task.get("status") in statuses:
                return task
            await asyncio.sleep(interval)
        return None

    def count_matching_actors(self, expected: str | list[str], *, faction: str = "己方") -> int:
        return len(self.matching_actors(expected, faction=faction))

    def recent_debug_context(self) -> str:
        notifications = [item.get("data", {}).get("content", "") for item in list(self._notifications)[-3:]]
        logs = [item.get("data", {}).get("message", "") for item in list(self._logs)[-5:]]
        errors = [str(item.get("message") or "") for item in list(self._errors)[-3:]]
        task_messages = [str(item.get("content") or "") for item in list(self._task_messages)[-3:]]
        world_snapshot = dict(self._world_snapshot or {})
        runtime_state = world_snapshot.get("runtime_state") if isinstance(world_snapshot, dict) else {}
        runtime_fault_state = world_snapshot.get("runtime_fault_state") if isinstance(world_snapshot, dict) else {}
        if not isinstance(runtime_fault_state, dict):
            runtime_fault_state = {}
        active_tasks = len(runtime_state.get("active_tasks", {}) or {}) if isinstance(runtime_state, dict) else 0
        world_debug = {
            "stale": bool(world_snapshot.get("stale", False)),
            "sync_failures": int(world_snapshot.get("consecutive_refresh_failures", 0) or 0),
            "failure_threshold": int(world_snapshot.get("failure_threshold", 0) or 0),
            "last_refresh_error": str(world_snapshot.get("last_refresh_error") or ""),
            "player_faction": str(world_snapshot.get("player_faction") or ""),
            "capability_truth_blocker": str(world_snapshot.get("capability_truth_blocker") or ""),
            "runtime_fault_degraded": bool(runtime_fault_state.get("degraded", False)),
            "runtime_fault_source": str(runtime_fault_state.get("source") or ""),
            "runtime_fault_stage": str(runtime_fault_state.get("stage") or ""),
            "runtime_fault_error": str(runtime_fault_state.get("error") or ""),
            "active_tasks": active_tasks,
            "pending_questions": len(world_snapshot.get("pending_questions", []) or []),
        }
        return (
            f"world={world_debug} notifications={notifications} logs={logs} "
            f"errors={errors} task_messages={task_messages}"
        )


class LiveTestSuite:
    def __init__(self, runner: LiveTestRunner) -> None:
        self.runner = runner

    async def _require_task_surface(self, reply: str, *, timeout: float = 10.0) -> Optional[str]:
        task_id = self.runner.extract_task_id(reply)
        if task_id is None:
            raise RuntimeError(
                f"command reply did not include task_id; reply={reply}; {self.runner.recent_debug_context()}"
            )
        ok = await self.runner.wait_for_ws_state(
            lambda: self.runner.has_task_surface(task_id),
            timeout=timeout,
        )
        if not ok:
            raise RuntimeError(
                f"task {task_id} never surfaced in task_update/task_message; reply={reply}; "
                f"{self.runner.recent_debug_context()}"
            )
        return task_id

    async def _wait_for_actor_count_increase_result(
        self,
        *,
        expected: str | list[str],
        before: int,
        reply: str,
        timeout: float,
        min_delta: int = 1,
        label: str = "actor count",
    ) -> str:
        task_id = await self._require_task_surface(reply, timeout=min(timeout, 10.0))
        target_count = before + max(1, int(min_delta))
        deadline = time.time() + timeout
        while time.time() < deadline:
            after = self.runner.count_matching_actors(expected, faction="己方")
            if after >= target_count:
                return f"{reply} (before={before}, after={after})"
            if task_id is not None:
                task = self.runner.get_task(task_id)
                status = str((task or {}).get("status") or "")
                if status in {"succeeded", "failed", "aborted", "partial"}:
                    raise RuntimeError(
                        f"task {task_id} reached terminal status {status} before {label} increased by {target_count - before}; "
                        f"before={before}, after={after}; reply={reply}; {self.runner.recent_debug_context()}"
                    )
            await asyncio.sleep(1.0)

        after = self.runner.count_matching_actors(expected, faction="己方")
        raise RuntimeError(
            f"{label} did not increase by {target_count - before}; before={before}, after={after}; "
            f"reply={reply}; {self.runner.recent_debug_context()}"
        )

    async def _wait_for_structure_result(
        self,
        *,
        expected: str | list[str],
        before: int,
        reply: str,
        timeout: float,
    ) -> str:
        return await self._wait_for_actor_count_increase_result(
            expected=expected,
            before=before,
            reply=reply,
            timeout=timeout,
            min_delta=1,
            label="structure count",
        )

    async def _wait_for_matching_actor_movement_result(
        self,
        *,
        expected: str | list[str],
        before_positions: dict[int, tuple[int, int]],
        reply: str,
        timeout: float,
        min_manhattan_distance: int = 2,
        label: str = "actor movement",
    ) -> str:
        task_id = await self._require_task_surface(reply, timeout=min(timeout, 10.0))
        deadline = time.time() + timeout
        while time.time() < deadline:
            actors = self.runner.query_actors(faction="己方")
            if self.runner.any_matching_actor_moved(
                actors,
                before_positions,
                expected,
                min_manhattan_distance=min_manhattan_distance,
            ):
                return f"{reply} (scouts={len(before_positions)})"
            if task_id is not None:
                task = self.runner.get_task(task_id)
                status = str((task or {}).get("status") or "")
                if status in {"succeeded", "failed", "aborted", "partial"}:
                    after_positions = self.runner.matching_actor_positions(expected, faction="己方")
                    raise RuntimeError(
                        f"task {task_id} reached terminal status {status} before {label}; "
                        f"before={before_positions}; after={after_positions}; reply={reply}; "
                        f"{self.runner.recent_debug_context()}"
                    )
            await asyncio.sleep(1.0)

        after_positions = self.runner.matching_actor_positions(expected, faction="己方")
        raise RuntimeError(
            f"{label} was not observed within timeout; before={before_positions}; after={after_positions}; "
            f"reply={reply}; {self.runner.recent_debug_context()}"
        )

    async def test_phase_a_connectivity(self) -> str:
        if not self.runner.check_backend_running():
            raise RuntimeError("backend WS port is not reachable")
        if not GameAPI.is_server_running(host=self.runner.game_host, port=self.runner.game_port):
            raise RuntimeError("GameAPI server is not reachable")
        actors = self.runner.query_actors("己方")
        if not isinstance(actors, list):
            raise RuntimeError("GameAPI query_actor did not return a list")
        return f"backend ok, game api ok, self actors={len(actors)}"

    async def test_phase_b_deploy_mcv(self) -> str:
        before = self.runner.count_matching_actors(["建造厂", "construction yard"], faction="己方")
        before_mcv = self.runner.count_matching_actors(["mcv", "基地车"], faction="己方")
        if before > 0 and before_mcv == 0:
            return "skip: construction yard already exists and no undeployed mcv is present"
        reply = await self.runner.send_command("部署基地车")
        return await self._wait_for_actor_count_increase_result(
            expected=["建造厂", "construction yard"],
            before=before,
            reply=reply,
            timeout=90.0,
            min_delta=1,
            label="construction yard count",
        )

    async def test_phase_b_build_power(self) -> str:
        before = self.runner.count_matching_actors("powr", faction="己方")
        reply = await self.runner.send_command("建造电厂")
        return await self._wait_for_structure_result(expected="powr", before=before, reply=reply, timeout=120.0)

    async def test_phase_b_build_barracks(self) -> str:
        before = self.runner.count_matching_actors(["barr", "tent"], faction="己方")
        reply = await self.runner.send_command("建造兵营")
        return await self._wait_for_structure_result(
            expected=["barr", "tent"],
            before=before,
            reply=reply,
            timeout=120.0,
        )

    async def test_phase_b_build_refinery(self) -> str:
        before = self.runner.count_matching_actors("proc", faction="己方")
        reply = await self.runner.send_command("建造矿场")
        return await self._wait_for_structure_result(expected="proc", before=before, reply=reply, timeout=120.0)

    async def test_phase_c_produce_infantry(self) -> str:
        before = self.runner.count_matching_actors("e1", faction="己方")
        reply = await self.runner.send_command("生产3个步兵")
        return await self._wait_for_actor_count_increase_result(
            expected="e1",
            before=before,
            reply=reply,
            timeout=120.0,
            min_delta=3,
            label="infantry count",
        )

    async def test_phase_d_recon(self) -> str:
        before_positions = self.runner.matching_actor_positions(SCOUT_CANDIDATE_TYPES, faction="己方")
        if not before_positions:
            raise RuntimeError(
                "phase_d_recon requires at least one mobile scout candidate before issuing recon; "
                f"{self.runner.recent_debug_context()}"
            )
        reply = await self.runner.send_command("探索地图")
        await self._require_task_surface(reply)
        ok = await self.runner.wait_for_ws_state(
            lambda: any(
                job.get("expert_type") == "ReconExpert"
                for job in self.runner.latest_world_snapshot().get("runtime_state", {}).get("active_jobs", {}).values()
            ),
            timeout=30.0,
        )
        if not ok:
            raise RuntimeError(f"ReconJob not visible in runtime_state.active_jobs; reply={reply}; snapshot={self.runner.latest_world_snapshot()}")
        return await self._wait_for_matching_actor_movement_result(
            expected=SCOUT_CANDIDATE_TYPES,
            before_positions=before_positions,
            reply=reply,
            timeout=30.0,
            min_manhattan_distance=2,
            label="scout movement",
        )

    async def test_phase_e_query(self) -> str:
        response = await self.runner.send_player_input_response("战况如何？", response_types={"query"})
        reply = str(
            response.get("answer")
            or response.get("response_text")
            or response.get("content")
            or json.dumps(response, ensure_ascii=False)
        )
        if str(response.get("response_type") or "") != "query":
            raise RuntimeError(f"query command returned non-query response_type: {response!r}")
        if str(response.get("task_id") or "") or str(response.get("existing_task_id") or ""):
            raise RuntimeError(f"query command unexpectedly attached task metadata: {response!r}")
        if self.runner.extract_task_id(reply) is not None:
            raise RuntimeError(f"query reply unexpectedly looks like task creation ack: {reply!r}")
        keywords = ("经济", "敌", "单位", "地图", "战况", "现金")
        if len(reply) <= 50 or not any(keyword in reply for keyword in keywords):
            raise RuntimeError(f"query response too weak: {reply!r}")
        return reply


async def run_case(name: str, coro: Callable[[], Awaitable[str]]) -> CaseResult:
    started = time.time()
    print(f"\n=== {name} ===", flush=True)
    try:
        detail = await coro()
        elapsed = time.time() - started
        print(f"PASS {name} ({elapsed:.1f}s)", flush=True)
        print(f"  detail: {detail}", flush=True)
        return CaseResult(name=name, ok=True, elapsed_s=elapsed, detail=detail)
    except Exception as exc:
        elapsed = time.time() - started
        print(f"FAIL {name} ({elapsed:.1f}s)", flush=True)
        print(f"  reason: {exc}", flush=True)
        return CaseResult(name=name, ok=False, elapsed_s=elapsed, detail=str(exc))


def _phase_cases(suite: LiveTestSuite) -> dict[str, list[tuple[str, Callable[[], Awaitable[str]]]]]:
    return {
        "phase_a": [("test_phase_a_connectivity", suite.test_phase_a_connectivity)],
        "phase_b": [
            ("test_phase_b_deploy_mcv", suite.test_phase_b_deploy_mcv),
            ("test_phase_b_build_power", suite.test_phase_b_build_power),
            ("test_phase_b_build_barracks", suite.test_phase_b_build_barracks),
            ("test_phase_b_build_refinery", suite.test_phase_b_build_refinery),
        ],
        "phase_c": [("test_phase_c_produce_infantry", suite.test_phase_c_produce_infantry)],
        "phase_d": [("test_phase_d_recon", suite.test_phase_d_recon)],
        "phase_e": [("test_phase_e_query", suite.test_phase_e_query)],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Live E2E runner against real game + backend")
    parser.add_argument("phase", nargs="?", default="all", help="all / phase_a / phase_b / phase_c / phase_d / phase_e")
    parser.add_argument("--ws-url", default=os.environ.get("LIVE_WS_URL", "ws://127.0.0.1:8765/ws"))
    parser.add_argument("--game-host", default=os.environ.get("LIVE_GAME_HOST", "127.0.0.1"))
    parser.add_argument("--game-port", type=int, default=int(os.environ.get("LIVE_GAME_PORT", "7445")))
    args = parser.parse_args()

    runner = LiveTestRunner(ws_url=args.ws_url, game_host=args.game_host, game_port=args.game_port)
    suite = LiveTestSuite(runner)
    phase_map = _phase_cases(suite)

    requested = args.phase.lower()
    if requested == "all":
        cases = [case for group in phase_map.values() for case in group]
    else:
        if requested not in phase_map:
            print(f"Unknown phase: {args.phase}", flush=True)
            return 2
        cases = phase_map[requested]

    try:
        await runner.connect()
    except Exception as exc:
        print(f"\nFAIL connect_to_backend (0.0s)", flush=True)
        print(f"  reason: {exc}", flush=True)
        print("\nSummary: 0 passed / 1 failed", flush=True)
        return 1

    try:
        results = []
        for name, coro in cases:
            results.append(await run_case(name, coro))
    finally:
        await runner.close()

    passed = sum(1 for item in results if item.ok)
    failed = len(results) - passed
    print(f"\nSummary: {passed} passed / {failed} failed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
