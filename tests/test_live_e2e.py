"""Executable live E2E runner against a real game + backend.

Usage:
    python3 tests/test_live_e2e.py
    python3 tests/test_live_e2e.py phase_a
    python3 tests/test_live_e2e.py phase_b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api.game_api import GameAPI
from openra_api.models import Actor, TargetsQueryParam
from openra_api.production_names import production_name_matches
from unit_registry import normalize_registry_name


MAX_SIZE = 10 * 1024 * 1024


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
        ws_url: str = "ws://localhost:8765/ws",
        game_host: str = "localhost",
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
        self._pending_questions: list[dict[str, Any]] = []
        self._world_snapshot: dict[str, Any] = {}
        self._notifications: deque[dict[str, Any]] = deque(maxlen=20)
        self._logs: deque[dict[str, Any]] = deque(maxlen=50)

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.ws_url, max_size=MAX_SIZE)
        self._receiver_task = asyncio.create_task(self._recv_loop())
        await self._send({"type": "sync_request"})
        await self.wait_for_ws_state(lambda: bool(self._world_snapshot) or bool(self._task_list), timeout=5.0)

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
            msg_type = msg.get("type")
            data = msg.get("data", {})
            if msg_type == "query_response":
                await self._query_responses.put(msg)
            elif msg_type == "task_list":
                self._task_list = list(data.get("tasks", []))
                self._pending_questions = list(data.get("pending_questions", []))
            elif msg_type == "world_snapshot":
                self._world_snapshot = dict(data)
                if data.get("pending_questions") is not None:
                    self._pending_questions = list(data.get("pending_questions", []))
            elif msg_type == "player_notification":
                self._notifications.append(msg)
            elif msg_type == "log_entry":
                self._logs.append(msg)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self.ws is not None
        outgoing = dict(payload)
        outgoing.setdefault("timestamp", time.time())
        await self.ws.send(json.dumps(outgoing, ensure_ascii=False))

    async def send_command(self, text: str, timeout: float = 30.0) -> str:
        while not self._query_responses.empty():
            try:
                self._query_responses.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._send({"type": "command_submit", "text": text})
        msg = await asyncio.wait_for(self._query_responses.get(), timeout=timeout)
        data = msg.get("data", {})
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

    def count_matching_actors(self, expected: str | list[str], *, faction: str = "己方") -> int:
        expected_names = [expected] if isinstance(expected, str) else list(expected)
        count = 0
        for actor in self.query_actors(faction=faction):
            observed = getattr(actor, "type", None)
            normalized_observed = normalize_registry_name(observed)
            if any(
                production_name_matches(name, observed)
                or (
                    normalized_observed
                    and normalize_registry_name(name)
                    and normalize_registry_name(name) in normalized_observed
                )
                for name in expected_names
            ):
                count += 1
        return count

    def recent_debug_context(self) -> str:
        notifications = [item.get("data", {}).get("content", "") for item in list(self._notifications)[-3:]]
        logs = [item.get("data", {}).get("message", "") for item in list(self._logs)[-5:]]
        return f"notifications={notifications} logs={logs}"


class LiveTestSuite:
    def __init__(self, runner: LiveTestRunner) -> None:
        self.runner = runner

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
        reply = await self.runner.send_command("部署基地车")
        ok = await self.runner.wait_for_game_state(
            lambda actors: self.runner.count_matching_actors(["建造厂", "construction yard"], faction="己方") > before,
            timeout=90.0,
            faction="己方",
        )
        if not ok:
            raise RuntimeError(f"construction yard did not appear; reply={reply}; {self.runner.recent_debug_context()}")
        return reply

    async def test_phase_b_build_power(self) -> str:
        before = self.runner.count_matching_actors("powr", faction="己方")
        reply = await self.runner.send_command("建造电厂")
        ok = await self.runner.wait_for_game_state(
            lambda actors: self.runner.count_matching_actors("powr", faction="己方") > before,
            timeout=120.0,
            faction="己方",
        )
        if not ok:
            raise RuntimeError(f"POWR did not appear; reply={reply}; {self.runner.recent_debug_context()}")
        return reply

    async def test_phase_b_build_barracks(self) -> str:
        before = self.runner.count_matching_actors(["barr", "tent"], faction="己方")
        reply = await self.runner.send_command("建造兵营")
        ok = await self.runner.wait_for_game_state(
            lambda actors: self.runner.count_matching_actors(["barr", "tent"], faction="己方") > before,
            timeout=120.0,
            faction="己方",
        )
        if not ok:
            raise RuntimeError(f"BARR/TENT did not appear; reply={reply}; {self.runner.recent_debug_context()}")
        return reply

    async def test_phase_b_build_refinery(self) -> str:
        before = self.runner.count_matching_actors("proc", faction="己方")
        reply = await self.runner.send_command("建造矿场")
        ok = await self.runner.wait_for_game_state(
            lambda actors: self.runner.count_matching_actors("proc", faction="己方") > before,
            timeout=120.0,
            faction="己方",
        )
        if not ok:
            raise RuntimeError(f"PROC did not appear; reply={reply}; {self.runner.recent_debug_context()}")
        return reply

    async def test_phase_c_produce_infantry(self) -> str:
        before = self.runner.count_matching_actors("e1", faction="己方")
        reply = await self.runner.send_command("生产3个步兵")
        ok = await self.runner.wait_for_game_state(
            lambda actors: self.runner.count_matching_actors("e1", faction="己方") >= before + 3,
            timeout=120.0,
            faction="己方",
        )
        if not ok:
            raise RuntimeError(f"infantry count did not increase by 3; before={before}; reply={reply}; {self.runner.recent_debug_context()}")
        after = self.runner.count_matching_actors("e1", faction="己方")
        return f"{reply} (before={before}, after={after})"

    async def test_phase_d_recon(self) -> str:
        reply = await self.runner.send_command("探索地图")
        ok = await self.runner.wait_for_ws_state(
            lambda: any(
                job.get("expert_type") == "ReconExpert"
                for job in self.runner.latest_world_snapshot().get("runtime_state", {}).get("active_jobs", {}).values()
            ),
            timeout=30.0,
        )
        if not ok:
            raise RuntimeError(f"ReconJob not visible in runtime_state.active_jobs; reply={reply}; snapshot={self.runner.latest_world_snapshot()}")
        return reply

    async def test_phase_e_query(self) -> str:
        reply = await self.runner.send_command("战况如何？")
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
    parser.add_argument("--ws-url", default=os.environ.get("LIVE_WS_URL", "ws://localhost:8765/ws"))
    parser.add_argument("--game-host", default=os.environ.get("LIVE_GAME_HOST", "localhost"))
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
