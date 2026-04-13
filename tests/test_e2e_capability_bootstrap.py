"""Focused E2E smoke for ordinary-task request -> capability bootstrap -> delivery."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
import pytest

from llm import MockProvider
from main import ApplicationRuntime, RuntimeConfig
from models import TaskKind, TaskStatus
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from task_agent import AgentConfig
from kernel import KernelConfig

pytestmark = pytest.mark.mock_integration


class CapabilityBootstrapGameAPI:
    def __init__(self) -> None:
        self.produce_requests = 0
        self.last_produce: dict[str, Any] | None = None

    def can_produce(self, unit_type: str) -> bool:
        return unit_type == "3tnk"

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> int:
        self.produce_requests += quantity
        self.last_produce = {
            "unit_type": unit_type,
            "quantity": quantity,
            "auto_place_building": auto_place_building,
        }
        return self.produce_requests

    def place_building(self, queue_type: str, location: Any = None) -> None:
        raise AssertionError(f"place_building should not be called in vehicle bootstrap smoke: {queue_type=} {location=}")

    def manage_production(
        self,
        queue_type: str,
        action: str,
        *,
        owner_actor_id: int | None = None,
        item_name: str | None = None,
        count: int = 1,
    ) -> None:
        raise AssertionError(
            f"manage_production should not be called in vehicle bootstrap smoke: "
            f"{queue_type=} {action=} {owner_actor_id=} {item_name=} {count=}"
        )


class CapabilityBootstrapWorldSource:
    def __init__(self, game_api: CapabilityBootstrapGameAPI) -> None:
        self.game_api = game_api
        self.queue_refreshes = 0

    def fetch_self_actors(self) -> list[Actor]:
        actors = [
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(22, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(28, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="维修厂", faction="自己", position=Location(34, 10), hppercent=100, activity="Idle"),
        ]
        if self.game_api.produce_requests and self.queue_refreshes >= 3:
            actors.append(
                Actor(actor_id=20, type="重坦", faction="自己", position=Location(32, 12), hppercent=100, activity="Idle")
            )
        return actors

    def fetch_enemy_actors(self) -> list[Actor]:
        return []

    def fetch_frozen_enemies(self) -> list[Actor]:
        return []

    def fetch_economy(self) -> PlayerBaseInfo:
        return PlayerBaseInfo(Cash=5000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100)

    def fetch_map(self, fields=None) -> MapQueryResult:
        size = 4
        return MapQueryResult(
            MapWidth=size,
            MapHeight=size,
            Height=[[0] * size for _ in range(size)],
            IsVisible=[[True] * size for _ in range(size)],
            IsExplored=[[True] * size for _ in range(size)],
            Terrain=[["clear"] * size for _ in range(size)],
            ResourcesType=[["ore"] * size for _ in range(size)],
            Resources=[[0] * size for _ in range(size)],
        )

    def fetch_production_queues(self) -> dict[str, dict]:
        self.queue_refreshes += 1
        items: list[dict[str, Any]] = []
        if self.game_api.produce_requests:
            done = self.queue_refreshes >= 3
            items = [
                {
                    "queue_type": "Vehicle",
                    "name": "3tnk",
                    "display_name": "重型坦克",
                    "progress": 100 if done else 50,
                    "status": "Done" if done else "Building",
                    "paused": False,
                    "owner_actor_id": 4,
                    "remaining_time": 0 if done else 1,
                    "total_time": 1,
                    "done": done,
                }
            ]
        return {"Vehicle": {"queue_type": "Vehicle", "items": items, "has_ready_item": False}}


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition")


async def _tick_until(runtime: ApplicationRuntime, predicate, *, max_ticks: int = 40, step_s: float = 0.25) -> None:
    now = time.time()
    for _ in range(max_ticks):
        now += step_s
        await asyncio.to_thread(runtime.world_model.refresh, now=now, force=True)
        events = runtime.world_model.detect_events(clear=True)
        if events:
            runtime.kernel.route_events(events)
        runtime.kernel.tick(now=now)
        await runtime.game_loop._tick_jobs(now)
        runtime.bridge.sync_runtime()
        if predicate():
            return
    raise AssertionError("Timed out waiting for predicate across manual game-loop ticks")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime_config(
    root: str,
    *,
    enable_ws: bool = False,
    ws_port: int = 0,
) -> RuntimeConfig:
    return RuntimeConfig(
        tick_hz=30.0,
        review_interval=10.0,
        enable_ws=enable_ws,
        ws_host="127.0.0.1",
        ws_port=ws_port,
        verify_game_api=False,
        llm_provider="mock",
        llm_model="mock",
        log_session_root=os.path.join(root, "logs"),
        benchmark_records_path=os.path.join(root, "capability_bootstrap_records.json"),
        benchmark_summary_path=os.path.join(root, "capability_bootstrap_summary.json"),
        log_export_path=os.path.join(root, "capability_bootstrap_logs.json"),
    )


@pytest.mark.mock_integration
def test_capability_bootstrap_request_smoke() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            game_api = CapabilityBootstrapGameAPI()
            runtime = ApplicationRuntime(
                config=_runtime_config(tmpdir),
                task_llm=MockProvider([]),
                adjutant_llm=MockProvider([]),
                api=game_api,
                world_source=CapabilityBootstrapWorldSource(game_api),
                kernel_config=KernelConfig(
                    auto_start_agents=False,
                    default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
                ),
            )
            try:
                await asyncio.to_thread(runtime.world_model.refresh, force=True)
                runtime.bridge.sync_runtime()
                assert runtime.kernel.capability_task_id is None
                assert not any(task.is_capability for task in runtime.kernel.list_tasks())
                task = runtime.kernel.create_task("前线补一辆重坦", TaskKind.MANAGED, 60)
                agent = runtime.kernel.get_task_agent(task.task_id)
                assert agent is not None

                result = await agent.tool_executor.execute(
                    "tc_cap_bootstrap",
                    "request_units",
                    '{"category":"vehicle","count":1,"urgency":"high","hint":"重坦"}',
                )
                assert result.error is None
                payload = result.result
                assert payload["status"] == "waiting"
                assert payload["unit_type"] == "3tnk"
                assert payload["queue_type"] == "Vehicle"
                assert payload["bootstrap_job_id"] is not None
                capability_task_id = runtime.kernel.capability_task_id
                assert capability_task_id is not None
                assert payload["bootstrap_task_id"] == capability_task_id

                capability_task = runtime.kernel.tasks[capability_task_id]
                assert capability_task.is_capability is True
                assert capability_task.raw_text == "EconomyCapability — 持久经济规划"
                assert runtime.kernel.get_task_agent(capability_task_id) is not None

                runtime.kernel._jobs[payload["bootstrap_job_id"]].tick_interval = 0.2  # type: ignore[attr-defined]
                runtime.bridge.sync_runtime()

                await _tick_until(runtime, lambda: game_api.produce_requests == 1, max_ticks=12)
                await _tick_until(
                    runtime,
                    lambda: runtime.kernel._unit_requests[payload["request_id"]].status == "fulfilled",
                    max_ticks=24,
                )

                request = runtime.kernel._unit_requests[payload["request_id"]]
                reservation = runtime.kernel.list_unit_reservations()[0]

                assert game_api.last_produce == {
                    "unit_type": "3tnk",
                    "quantity": 1,
                    "auto_place_building": False,
                }
                assert request.assigned_actor_ids == [20]
                assert request.bootstrap_job_id is None
                assert request.bootstrap_task_id is None
                assert reservation.assigned_actor_ids == [20]
                assert reservation.bootstrap_job_id is None
                assert reservation.bootstrap_task_id is None
                assert runtime.kernel.task_active_actor_ids(task.task_id) == [20]
                runtime_state = runtime.kernel.world_model.query("runtime_state")
                assert runtime_state["active_tasks"][task.task_id]["active_actor_ids"] == [20]
                assert runtime_state["capability_status"]["task_id"] == capability_task_id
            finally:
                api_close = getattr(runtime.api, "close", None)
                if callable(api_close):
                    await asyncio.to_thread(api_close)

    asyncio.run(run())


@pytest.mark.mock_integration
def test_capability_bootstrap_command_submit_live_surfaces_reservation_and_completion() -> None:
    class _LiveBootstrapAdjutant:
        def __init__(self, runtime: ApplicationRuntime) -> None:
            self.runtime = runtime

        async def handle_player_input(self, text: str) -> dict[str, Any]:
            task = self.runtime.kernel.create_task(text, TaskKind.MANAGED, 60)
            agent = self.runtime.kernel.get_task_agent(task.task_id)
            assert agent is not None
            result = await agent.tool_executor.execute(
                "tc_live_cap_bootstrap",
                "request_units",
                '{"category":"vehicle","count":1,"urgency":"high","hint":"重坦"}',
            )
            assert result.error is None
            return {
                "response_text": f"收到指令，已创建任务 {task.task_id}",
                "type": "command",
                "ok": True,
                "task_id": task.task_id,
            }

        def notify_task_completed(self, **kwargs: Any) -> None:
            del kwargs

        def notify_task_message(self, **kwargs: Any) -> None:
            del kwargs

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_port = _free_tcp_port()
            game_api = CapabilityBootstrapGameAPI()
            runtime = ApplicationRuntime(
                config=_runtime_config(tmpdir, enable_ws=True, ws_port=ws_port),
                task_llm=MockProvider([]),
                adjutant_llm=MockProvider([]),
                api=game_api,
                world_source=CapabilityBootstrapWorldSource(game_api),
                kernel_config=KernelConfig(
                    auto_start_agents=False,
                    default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
                ),
            )
            try:
                await runtime.start()
                runtime.bridge.adjutant = _LiveBootstrapAdjutant(runtime)

                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                        buffered_payloads: list[dict[str, Any]] = []

                        async def _recv_json(
                            *,
                            predicate,
                            timeout_s: float = 3.0,
                            max_messages: int = 80,
                        ) -> dict[str, Any]:
                            loop = asyncio.get_running_loop()
                            for index, payload in enumerate(list(buffered_payloads)):
                                if predicate(payload):
                                    return buffered_payloads.pop(index)
                            deadline = loop.time() + timeout_s
                            seen = 0
                            while seen < max_messages and loop.time() < deadline:
                                msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                                assert msg.type == aiohttp.WSMsgType.TEXT
                                payload = json.loads(msg.data)
                                if predicate(payload):
                                    return payload
                                buffered_payloads.append(payload)
                                seen += 1
                            raise AssertionError("Timed out waiting for websocket payload")

                        async def _drain_ws(*, idle_s: float = 0.4) -> None:
                            loop = asyncio.get_running_loop()
                            deadline = loop.time() + idle_s
                            while loop.time() < deadline:
                                try:
                                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                                except asyncio.TimeoutError:
                                    break
                                if msg.type != aiohttp.WSMsgType.TEXT:
                                    continue
                                buffered_payloads.append(json.loads(msg.data))

                        await ws.send_json({"type": "sync_request"})
                        seen_types: set[str] = set()
                        while {"world_snapshot", "task_list", "session_catalog"} - seen_types:
                            payload = await _recv_json(
                                predicate=lambda message: message.get("type") in {
                                    "world_snapshot",
                                    "task_list",
                                    "session_catalog",
                                }
                            )
                            seen_types.add(str(payload.get("type") or ""))
                        await _drain_ws()

                        await ws.send_json({"type": "command_submit", "text": "前线补一辆重坦"})
                        query_response = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "query_response"
                                and payload.get("data", {}).get("response_type") == "command"
                                and payload.get("data", {}).get("task_id")
                            )
                        )
                        task_id = str(query_response["data"]["task_id"])
                        assert query_response["data"]["ok"] is True
                        assert query_response["data"]["answer"] == f"收到指令，已创建任务 {task_id}"

                        task_update = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "task_update"
                                and payload.get("data", {}).get("task_id") == task_id
                            )
                        )
                        assert task_update["data"]["raw_text"] == "前线补一辆重坦"
                        assert task_update["data"]["status"] == TaskStatus.RUNNING.value

                        reservations = runtime.kernel.list_unit_reservations()
                        assert len(reservations) == 1
                        assert reservations[0].task_id == task_id
                        assert reservations[0].unit_type == "3tnk"
                        request_id = reservations[0].request_id

                        await ws.send_json({"type": "sync_request"})
                        world_snapshot = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "world_snapshot"
                                and payload.get("data", {}).get("unit_pipeline_focus", {}).get("task_id") == task_id
                            )
                        )
                        focus = world_snapshot["data"]["unit_pipeline_focus"]
                        assert focus["task_id"] == task_id
                        assert focus["reason"] in {"bootstrap_in_progress", "waiting_dispatch", "start_package_released"}
                        runtime_state = world_snapshot["data"]["runtime_state"]
                        assert any(item.get("task_id") == task_id for item in runtime_state["unfulfilled_requests"])
                        assert any(item.get("task_id") == task_id for item in runtime_state["unit_reservations"])

                        live_task_list = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "task_list"
                                and any(
                                    item.get("task_id") == task_id
                                    for item in list(payload.get("data", {}).get("tasks", []) or [])
                                    if isinstance(item, dict)
                                )
                            )
                        )
                        task_payload = next(
                            item
                            for item in list(live_task_list["data"]["tasks"] or [])
                            if isinstance(item, dict) and item.get("task_id") == task_id
                        )
                        assert task_payload["triage"]["waiting_reason"] in {
                            "bootstrap_in_progress",
                            "waiting_dispatch",
                            "start_package_released",
                        }
                        assert task_payload["status"] == TaskStatus.RUNNING.value

                        await _tick_until(runtime, lambda: game_api.produce_requests == 1, max_ticks=12)
                        await _tick_until(
                            runtime,
                            lambda: runtime.kernel.task_active_actor_ids(task_id) == [20],
                            max_ticks=24,
                        )
                        await _tick_until(
                            runtime,
                            lambda: runtime.kernel._unit_requests[request_id].status == "fulfilled",
                            max_ticks=24,
                        )
                        reservation = runtime.kernel.list_unit_reservations()[0]
                        assert reservation.assigned_actor_ids == [20]

                        runtime.kernel.complete_task(task_id, "succeeded", "重坦已到位")
                        runtime.bridge.sync_runtime()
                        await runtime.bridge._publisher.publish_all()
                        await _drain_ws()
                        buffered_payloads.clear()

                        await ws.send_json({"type": "sync_request"})
                        final_world_snapshot = await _recv_json(
                            predicate=lambda payload: payload.get("type") == "world_snapshot"
                        )
                        final_runtime_state = final_world_snapshot["data"]["runtime_state"]
                        assert not any(
                            item.get("task_id") == task_id for item in final_runtime_state["unfulfilled_requests"]
                        )
                        assert not any(
                            item.get("task_id") == task_id for item in final_runtime_state["unit_reservations"]
                        )

                        final_task_list = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "task_list"
                                and any(
                                    item.get("task_id") == task_id
                                    and item.get("status") == TaskStatus.SUCCEEDED.value
                                    for item in list(payload.get("data", {}).get("tasks", []) or [])
                                    if isinstance(item, dict)
                                )
                            )
                        )
                        final_task = next(
                            item
                            for item in list(final_task_list["data"]["tasks"] or [])
                            if isinstance(item, dict) and item.get("task_id") == task_id
                        )
                        assert final_task["status"] == TaskStatus.SUCCEEDED.value

            finally:
                await runtime.stop()

    asyncio.run(run())


@pytest.mark.mock_integration
def test_capability_bootstrap_direct_abort_live_surfaces_exit_bootstrap_state() -> None:
    class _LiveBootstrapAdjutant:
        def __init__(self, runtime: ApplicationRuntime) -> None:
            self.runtime = runtime

        async def handle_player_input(self, text: str) -> dict[str, Any]:
            task = self.runtime.kernel.create_task(text, TaskKind.MANAGED, 60)
            agent = self.runtime.kernel.get_task_agent(task.task_id)
            assert agent is not None
            result = await agent.tool_executor.execute(
                "tc_live_cap_bootstrap_abort",
                "request_units",
                '{"category":"vehicle","count":1,"urgency":"high","hint":"重坦"}',
            )
            assert result.error is None
            return {
                "response_text": f"收到指令，已创建任务 {task.task_id}",
                "type": "command",
                "ok": True,
                "task_id": task.task_id,
            }

        def notify_task_completed(self, **kwargs: Any) -> None:
            del kwargs

        def notify_task_message(self, **kwargs: Any) -> None:
            del kwargs

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_port = _free_tcp_port()
            game_api = CapabilityBootstrapGameAPI()
            runtime = ApplicationRuntime(
                config=_runtime_config(tmpdir, enable_ws=True, ws_port=ws_port),
                task_llm=MockProvider([]),
                adjutant_llm=MockProvider([]),
                api=game_api,
                world_source=CapabilityBootstrapWorldSource(game_api),
                kernel_config=KernelConfig(
                    auto_start_agents=False,
                    default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
                ),
            )
            try:
                await runtime.start()
                runtime.bridge.adjutant = _LiveBootstrapAdjutant(runtime)

                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                        buffered_payloads: list[dict[str, Any]] = []

                        async def _recv_json(
                            *,
                            predicate,
                            timeout_s: float = 3.0,
                            max_messages: int = 80,
                        ) -> dict[str, Any]:
                            loop = asyncio.get_running_loop()
                            for index, payload in enumerate(list(buffered_payloads)):
                                if predicate(payload):
                                    return buffered_payloads.pop(index)
                            deadline = loop.time() + timeout_s
                            seen = 0
                            while seen < max_messages and loop.time() < deadline:
                                msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                                assert msg.type == aiohttp.WSMsgType.TEXT
                                payload = json.loads(msg.data)
                                if predicate(payload):
                                    return payload
                                buffered_payloads.append(payload)
                                seen += 1
                            raise AssertionError("Timed out waiting for websocket payload")

                        async def _drain_ws(*, idle_s: float = 0.4) -> None:
                            loop = asyncio.get_running_loop()
                            deadline = loop.time() + idle_s
                            while loop.time() < deadline:
                                try:
                                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                                except asyncio.TimeoutError:
                                    break
                                if msg.type != aiohttp.WSMsgType.TEXT:
                                    continue
                                buffered_payloads.append(json.loads(msg.data))

                        await ws.send_json({"type": "sync_request"})
                        seen_types: set[str] = set()
                        while {"world_snapshot", "task_list", "session_catalog"} - seen_types:
                            payload = await _recv_json(
                                predicate=lambda message: message.get("type") in {
                                    "world_snapshot",
                                    "task_list",
                                    "session_catalog",
                                }
                            )
                            seen_types.add(str(payload.get("type") or ""))
                        await _drain_ws()

                        await ws.send_json({"type": "command_submit", "text": "前线补一辆重坦"})
                        query_response = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "query_response"
                                and payload.get("data", {}).get("response_type") == "command"
                                and payload.get("data", {}).get("task_id")
                            )
                        )
                        task_id = str(query_response["data"]["task_id"])
                        assert query_response["data"]["ok"] is True

                        await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "task_update"
                                and payload.get("data", {}).get("task_id") == task_id
                            )
                        )

                        reservations = runtime.kernel.list_unit_reservations()
                        assert len(reservations) == 1
                        request_id = reservations[0].request_id
                        bootstrap_job_id = runtime.kernel._unit_requests[request_id].bootstrap_job_id
                        assert bootstrap_job_id is not None

                        await ws.send_json({"type": "sync_request"})
                        world_snapshot = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "world_snapshot"
                                and payload.get("data", {}).get("unit_pipeline_focus", {}).get("task_id") == task_id
                            )
                        )
                        assert world_snapshot["data"]["unit_pipeline_focus"]["reason"] in {
                            "bootstrap_in_progress",
                            "waiting_dispatch",
                            "start_package_released",
                        }

                        assert runtime.kernel.abort_job(bootstrap_job_id) is True
                        req = runtime.kernel._unit_requests[request_id]
                        reservation = runtime.kernel.list_unit_reservations()[0]
                        assert req.bootstrap_job_id is None
                        assert req.bootstrap_task_id is None
                        assert reservation.bootstrap_job_id is None
                        assert reservation.bootstrap_task_id is None

                        runtime.bridge.sync_runtime()
                        await runtime.bridge._publisher.publish_all()
                        await _drain_ws()

                        await ws.send_json({"type": "sync_request"})
                        refreshed_world = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "world_snapshot"
                                and payload.get("data", {}).get("unit_pipeline_focus", {}).get("task_id") == task_id
                            )
                        )
                        focus = refreshed_world["data"]["unit_pipeline_focus"]
                        assert focus["task_id"] == task_id
                        assert focus["reason"] != "bootstrap_in_progress"
                        runtime_state = refreshed_world["data"]["runtime_state"]
                        request_payload = next(
                            item for item in runtime_state["unfulfilled_requests"] if item.get("task_id") == task_id
                        )
                        reservation_payload = next(
                            item for item in runtime_state["unit_reservations"] if item.get("task_id") == task_id
                        )
                        assert not request_payload["bootstrap_job_id"]
                        assert not request_payload["bootstrap_task_id"]
                        assert request_payload["reason"] != "bootstrap_in_progress"
                        assert not reservation_payload["bootstrap_job_id"]
                        assert not reservation_payload["bootstrap_task_id"]
                        assert reservation_payload["reason"] != "bootstrap_in_progress"

                        refreshed_task_list = await _recv_json(
                            predicate=lambda payload: (
                                payload.get("type") == "task_list"
                                and any(
                                    item.get("task_id") == task_id
                                    and item.get("triage", {}).get("waiting_reason") != "bootstrap_in_progress"
                                    for item in list(payload.get("data", {}).get("tasks", []) or [])
                                    if isinstance(item, dict)
                                )
                            )
                        )
                        task_payload = next(
                            item
                            for item in list(refreshed_task_list["data"]["tasks"] or [])
                            if isinstance(item, dict) and item.get("task_id") == task_id
                        )
                        assert task_payload["status"] == TaskStatus.RUNNING.value
                        assert task_payload["triage"]["waiting_reason"] != "bootstrap_in_progress"

            finally:
                await runtime.stop()

    asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
