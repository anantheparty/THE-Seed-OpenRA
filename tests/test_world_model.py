"""Basic tests for WorldModel v1."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import logging_system
import pytest
from models import Constraint, ConstraintEnforcement, EventType
from openra_api.game_api import GameAPIError
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from world_model import WorldModel
from tests.schema_assertions import assert_mapping_superset


@dataclass
class Frame:
    self_actors: list[Actor]
    enemy_actors: list[Actor]
    economy: PlayerBaseInfo
    map_info: MapQueryResult
    queues: dict[str, dict]


class MockWorldSource:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames
        self.index = 0
        self.actor_fetches = 0
        self.economy_fetches = 0
        self.map_fetches = 0

    def set_frame(self, index: int) -> None:
        self.index = index

    def _frame(self) -> Frame:
        return self.frames[self.index]

    def fetch_self_actors(self) -> list[Actor]:
        self.actor_fetches += 1
        return self._frame().self_actors

    def fetch_enemy_actors(self) -> list[Actor]:
        return self._frame().enemy_actors

    def fetch_frozen_enemies(self):
        return []

    def fetch_economy(self) -> PlayerBaseInfo:
        self.economy_fetches += 1
        return self._frame().economy

    def fetch_map(self, fields=None) -> MapQueryResult:
        self.map_fetches += 1
        return self._frame().map_info

    def fetch_production_queues(self) -> dict[str, dict]:
        return self._frame().queues


class FailingWorldSource(MockWorldSource):
    def __init__(self, frames: list[Frame]) -> None:
        super().__init__(frames)
        self.fail = False

    def _maybe_fail(self, layer: str) -> None:
        if self.fail:
            raise RuntimeError(f"{layer} disconnected")

    def fetch_self_actors(self) -> list[Actor]:
        self._maybe_fail("actors")
        return super().fetch_self_actors()

    def fetch_enemy_actors(self) -> list[Actor]:
        self._maybe_fail("actors")
        return super().fetch_enemy_actors()

    def fetch_economy(self) -> PlayerBaseInfo:
        self._maybe_fail("economy")
        return super().fetch_economy()

    def fetch_map(self, fields=None) -> MapQueryResult:
        self._maybe_fail("map")
        return super().fetch_map(fields=fields)

    def fetch_production_queues(self) -> dict[str, dict]:
        self._maybe_fail("queues")
        return super().fetch_production_queues()


class DetailedFailure(RuntimeError):
    def __init__(self, message: str, details: dict) -> None:
        super().__init__(message)
        self.details = details


class DetailedFailingWorldSource(MockWorldSource):
    def fetch_self_actors(self) -> list[Actor]:
        raise DetailedFailure(
            "COMMAND_EXECUTION_ERROR: 查询执行失败",
            {"message": "Actor query failed in server", "type": "System.InvalidOperationException", "data": {"command": "query_actor"}},
        )

    def fetch_enemy_actors(self) -> list[Actor]:
        raise AssertionError("enemy fetch should not run after self actor failure")

    def fetch_economy(self) -> PlayerBaseInfo:
        return super().fetch_economy()

    def fetch_map(self, fields=None) -> MapQueryResult:
        return super().fetch_map(fields=fields)

    def fetch_production_queues(self) -> dict[str, dict]:
        return super().fetch_production_queues()


class ConnectionFailingWorldSource(MockWorldSource):
    def __init__(self, frames: list[Frame]) -> None:
        super().__init__(frames)
        self.actor_fail = True

    def fetch_self_actors(self) -> list[Actor]:
        self.actor_fetches += 1
        if self.actor_fail:
            raise GameAPIError("CONNECTION_ERROR", "连接服务器失败: connection refused")
        return super().fetch_self_actors()

    def fetch_enemy_actors(self) -> list[Actor]:
        if self.actor_fail:
            raise AssertionError("enemy fetch should not run after self actor connection failure")
        return super().fetch_enemy_actors()


def make_map(explored: float, visible: float) -> MapQueryResult:
    size = 4
    total = size * size
    explored_cells = round(total * explored)
    visible_cells = round(total * visible)

    def grid(true_count: int) -> list[list[bool]]:
        values = [True] * true_count + [False] * (total - true_count)
        return [values[index : index + size] for index in range(0, total, size)]

    return MapQueryResult(
        MapWidth=size,
        MapHeight=size,
        Height=[[0] * size for _ in range(size)],
        IsVisible=grid(visible_cells),
        IsExplored=grid(explored_cells),
        Terrain=[["clear"] * size for _ in range(size)],
        ResourcesType=[["ore"] * size for _ in range(size)],
        Resources=[[50] * size for _ in range(size)],
    )


def make_frames() -> list[Frame]:
    frames = [
        Frame(
            self_actors=[
                Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=2, type="重坦", faction="自己", position=Location(20, 20), hppercent=100, activity="Idle"),
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[
                Actor(actor_id=100, type="矿场", faction="敌人", position=Location(300, 300), hppercent=100, activity="Idle"),
                Actor(actor_id=101, type="重坦", faction="敌人", position=Location(100, 100), hppercent=100, activity="Idle"),
            ],
            economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={"Vehicle": {"queue_type": "Vehicle", "items": [{"name": "重坦", "display_name": "重型坦克", "owner_actor_id": 30, "done": False}], "has_ready_item": False}},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=2, type="重坦", faction="自己", position=Location(22, 20), hppercent=60, activity="AttackMove"),
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=80, activity="Idle"),
            ],
            enemy_actors=[
                Actor(actor_id=100, type="矿场", faction="敌人", position=Location(300, 300), hppercent=100, activity="Idle"),
                Actor(actor_id=101, type="重坦", faction="敌人", position=Location(70, 60), hppercent=100, activity="Idle"),
                Actor(actor_id=102, type="矿场", faction="敌人", position=Location(700, 680), hppercent=100, activity="Idle"),
            ],
            economy=PlayerBaseInfo(Cash=5200, Resources=700, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.75, visible=0.5),
            queues={"Vehicle": {"queue_type": "Vehicle", "items": [{"name": "重坦", "display_name": "重型坦克", "owner_actor_id": 30, "done": True}], "has_ready_item": True}},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=80, activity="Idle"),
            ],
            enemy_actors=[
                Actor(actor_id=100, type="矿场", faction="敌人", position=Location(300, 300), hppercent=100, activity="Idle"),
                Actor(actor_id=101, type="重坦", faction="敌人", position=Location(100, 100), hppercent=100, activity="Idle"),
                Actor(actor_id=102, type="矿场", faction="敌人", position=Location(700, 680), hppercent=100, activity="Idle"),
            ],
            economy=PlayerBaseInfo(Cash=4800, Resources=1000, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.8, visible=0.4),
            queues={"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}},
        ),
    ]

    for index, frame in enumerate(frames):
        for actor_kind, actors in (("self", frame.self_actors), ("enemy", frame.enemy_actors)):
            for actor in actors:
                for field in ("is_disabled", "is_powered_down", "has_low_power", "has_power_outage", "disabled_reason"):
                    assert hasattr(actor, field), f"make_frames[{index}] {actor_kind}_actor missing field: {field}"
        for queue_name, queue in frame.queues.items():
            assert_mapping_superset(
                queue,
                {"queue_type", "items", "has_ready_item"},
                label=f"make_frames[{index}].queues[{queue_name}]",
            )
            for item_index, item in enumerate(queue.get("items", [])):
                assert_mapping_superset(
                    item,
                    {"name", "display_name", "owner_actor_id", "done"},
                    label=f"make_frames[{index}].queues[{queue_name}].items[{item_index}]",
                )
    return frames


def test_refresh_layers_and_summary() -> None:
    benchmark.clear()
    source = MockWorldSource(make_frames())
    world = WorldModel(source)

    events = world.refresh(now=100.0, force=True)
    summary = world.world_summary()

    assert events == []
    assert source.actor_fetches == 1
    assert source.economy_fetches == 1
    assert source.map_fetches == 1
    assert summary["economy"]["cash"] == 2500
    assert summary["military"]["self_units"] == 3
    assert summary["map"]["explored_pct"] == 0.5
    assert len(benchmark.query(tag="world_refresh")) == 1
    print("  PASS: refresh_layers_and_summary")


def test_layered_refresh_respects_intervals() -> None:
    source = MockWorldSource(make_frames())
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    source.set_frame(1)
    world.refresh(now=100.2)

    assert source.actor_fetches == 2
    assert source.economy_fetches == 1
    assert source.map_fetches == 1
    assert world.last_refresh_layers() == ["actors"]
    print("  PASS: layered_refresh_respects_intervals")


def test_category_inference_marks_buildings_correctly() -> None:
    frame = Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(12, 12), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="兵营", faction="自己", position=Location(14, 14), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="重坦", faction="自己", position=Location(20, 20), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="基地车", faction="自己", position=Location(24, 24), hppercent=100, activity="Idle"),
            Actor(actor_id=6, type="矿车", faction="自己", position=Location(28, 28), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
        map_info=make_map(explored=0.5, visible=0.25),
        queues={},
    )
    source = MockWorldSource([frame])
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    actors = {item["actor_id"]: item for item in world.query("my_actors")["actors"]}

    assert actors[1]["category"] == "building"
    assert actors[2]["category"] == "building"
    assert actors[3]["category"] == "building"
    assert actors[4]["category"] == "vehicle"
    assert actors[5]["category"] == "mcv"
    assert actors[6]["category"] == "harvester"
    print("  PASS: category_inference_marks_buildings_correctly")


def test_actor_queries_honor_type_aliases() -> None:
    frame = Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(12, 12), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="基地车", faction="自己", position=Location(14, 14), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
        map_info=make_map(explored=0.5, visible=0.25),
        queues={},
    )
    source = MockWorldSource([frame])
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)

    mcv = world.query("my_actors", {"type": "mcv"})["actors"]
    power = world.query("my_actors", {"type": "powr"})["actors"]
    yard = world.query("my_actors", {"type": "建造厂"})["actors"]

    assert [item["actor_id"] for item in mcv] == [3]
    assert [item["actor_id"] for item in power] == [2]
    assert [item["actor_id"] for item in yard] == [1]
    print("  PASS: actor_queries_honor_type_aliases")


def test_event_detection_and_queries() -> None:
    source = MockWorldSource(make_frames())
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)

    source.set_frame(1)
    events = world.refresh(now=101.0)
    event_types = {event.type for event in events}

    assert EventType.UNIT_DAMAGED in event_types
    assert EventType.ENEMY_DISCOVERED in event_types
    assert EventType.BASE_UNDER_ATTACK in event_types
    assert EventType.PRODUCTION_COMPLETE in event_types
    assert EventType.ENEMY_EXPANSION in event_types
    assert EventType.ECONOMY_SURPLUS in event_types
    assert world.query("my_actors", {"category": "vehicle"})["actors"][0]["actor_id"] == 2
    assert world.query("find_actors", {"owner": "self", "idle_only": True})["actors"][0]["actor_id"] == 1
    print("  PASS: event_detection_and_queries")


def test_unit_death_runtime_state_and_constraints() -> None:
    source = MockWorldSource(make_frames())
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)
    world.bind_resource("actor:1", "job-1")
    world.set_constraint(
        Constraint(
            constraint_id="c1",
            kind="do_not_chase",
            scope="task_id:t1",
            params={"max_distance": 10},
            enforcement=ConstraintEnforcement.CLAMP,
        )
    )
    world.set_runtime_state(active_tasks={"t1": {"priority": 50}}, active_jobs={"j1": {"expert_type": "ReconExpert"}})

    source.set_frame(2)
    events = world.refresh(now=102.0)
    runtime = world.query("runtime_state")
    free_actors = world.query("find_actors", {"owner": "self", "unbound_only": True})["actors"]

    assert EventType.UNIT_DIED in {event.type for event in events}
    assert runtime["resource_bindings"]["actor:1"] == "job-1"
    assert runtime["constraints"][0]["constraint_id"] == "c1"
    assert [actor["actor_id"] for actor in free_actors] == [3]
    print("  PASS: unit_death_runtime_state_and_constraints")


def test_runtime_state_exposes_capability_status_and_battlefield_snapshot() -> None:
    source = MockWorldSource(make_frames())
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)
    world.set_runtime_state(
        active_tasks={"t1": {"priority": 50, "status": "running", "active_actor_ids": [2]}},
        active_jobs={"j1": {"task_id": "cap-1", "expert_type": "EconomyExpert", "status": "running"}},
        capability_status={
            "task_id": "cap-1",
            "task_label": "001",
            "status": "running",
            "active_job_count": 1,
            "active_job_types": ["EconomyExpert"],
            "pending_request_count": 2,
            "bootstrapping_request_count": 1,
        },
        unit_reservations=[
            {
                "reservation_id": "res_1",
                "request_id": "req_1",
                "task_id": "t1",
                "task_label": "001",
                "unit_type": "e1",
                "count": 2,
                "status": "pending",
            }
        ],
    )

    runtime = world.query("runtime_state")
    snapshot = world.query("battlefield_snapshot")
    capability = world.query("capability_status")
    facts = world.compute_runtime_facts("t1")

    assert runtime["capability_status"]["task_id"] == "cap-1"
    assert capability["pending_request_count"] == 2
    assert facts["capability_status"]["bootstrapping_request_count"] == 1
    assert snapshot["focus"] == "economy", snapshot
    assert snapshot["pending_request_count"] == 2, snapshot
    assert snapshot["bootstrapping_request_count"] == 1, snapshot
    assert snapshot["reservation_count"] == 1, snapshot
    assert snapshot["self_combat_units"] == 1, snapshot
    assert snapshot["committed_combat_units"] == 1, snapshot
    assert snapshot["free_combat_units"] == 0, snapshot
    assert snapshot["recommended_posture"] == "satisfy_requests", snapshot
    assert snapshot["threat_level"] == "unknown", snapshot
    assert snapshot["capability_status"]["active_job_count"] == 1, snapshot
    print("  PASS: runtime_state_exposes_capability_status_and_battlefield_snapshot")


def test_refresh_failure_marks_stale_and_recovers() -> None:
    logging_system.clear()
    source = FailingWorldSource(make_frames())
    world = WorldModel(source, stale_failure_threshold=3)
    world.refresh(now=100.0, force=True)

    source.fail = True
    for now in (101.0, 102.0, 103.0):
        world.refresh(now=now, force=True)

    health = world.refresh_health()
    summary = world.world_summary()

    assert summary["stale"] is True
    assert summary["military"]["self_units"] == 3  # previous snapshot still usable
    assert summary["disconnected"] is True
    assert "actors disconnected" in summary["last_refresh_error"]
    assert summary["consecutive_refresh_failures"] == 3
    assert summary["total_refresh_failures"] == 3
    assert summary["failure_threshold"] == 3
    facts = world.compute_runtime_facts("task-stale")
    assert facts["world_sync_stale"] is True
    assert facts["world_sync_consecutive_failures"] == 3
    assert facts["world_sync_failure_threshold"] == 3
    assert "actors disconnected" in facts["world_sync_last_error"]
    assert health["disconnected"] is True
    assert health["consecutive_failures"] == 3
    assert health["failure_threshold"] == 3
    assert "actors disconnected" in health["last_error"]

    source.fail = False
    world.refresh(now=104.0, force=True)
    recovered = world.refresh_health()

    assert recovered["stale"] is False
    assert recovered["disconnected"] is False
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_error"] is None
    print("  PASS: refresh_failure_marks_stale_and_recovers")


@pytest.mark.runtime_invariants
def test_connection_failure_skips_remaining_layers_and_respects_retry_backoff() -> None:
    source = ConnectionFailingWorldSource([make_frames()[0]])
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    assert source.actor_fetches == 1
    assert source.economy_fetches == 0
    assert source.map_fetches == 0

    world.refresh(now=101.0, force=False)
    assert source.actor_fetches == 1
    assert source.economy_fetches == 0
    assert source.map_fetches == 0

    world.refresh(now=102.1, force=False)
    assert source.actor_fetches == 2
    assert source.economy_fetches == 0
    assert source.map_fetches == 0
    print("  PASS: connection_failure_skips_remaining_layers_and_respects_retry_backoff")


def test_refresh_failure_logging_is_throttled_per_layer_until_recovery() -> None:
    logging_system.clear()
    source = FailingWorldSource(make_frames())
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)

    source.fail = True
    world.refresh(now=101.0, force=True)
    world.refresh(now=101.1, force=True)
    world.refresh(now=101.2, force=True)

    fail_logs = logging_system.query(component="world_model", event="world_refresh_failed")
    actor_logs = [record for record in fail_logs if record.data.get("layer") == "actors"]
    economy_logs = [record for record in fail_logs if record.data.get("layer") == "economy"]
    map_logs = [record for record in fail_logs if record.data.get("layer") == "map"]

    assert len(actor_logs) == 1
    assert len(economy_logs) == 1
    assert len(map_logs) == 1
    assert actor_logs[0].data.get("suppressed_count") == 0

    source.fail = False
    world.refresh(now=102.0, force=True)

    source.fail = True
    world.refresh(now=103.0, force=True)

    fail_logs = logging_system.query(component="world_model", event="world_refresh_failed")
    actor_logs = [record for record in fail_logs if record.data.get("layer") == "actors"]
    economy_logs = [record for record in fail_logs if record.data.get("layer") == "economy"]
    map_logs = [record for record in fail_logs if record.data.get("layer") == "map"]

    assert len(actor_logs) == 2
    assert len(economy_logs) == 2
    assert len(map_logs) == 2
    print("  PASS: refresh_failure_logging_is_throttled_per_layer_until_recovery")


def test_refresh_failure_logs_include_exception_detail_when_available() -> None:
    logging_system.clear()
    source = DetailedFailingWorldSource([make_frames()[0]])
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)

    fail_logs = logging_system.query(component="world_model", event="world_refresh_failed")
    actor_logs = [record for record in fail_logs if record.data.get("layer") == "actors"]

    assert len(actor_logs) == 1
    assert actor_logs[0].data.get("error_detail") == "Actor query failed in server"
    assert actor_logs[0].data.get("error_meta", {}).get("type") == "System.InvalidOperationException"
    print("  PASS: refresh_failure_logs_include_exception_detail_when_available")


def test_slow_refresh_logging_is_throttled_with_suppressed_count() -> None:
    logging_system.clear()

    class SlowMapWorldSource(MockWorldSource):
        def fetch_map(self, fields=None) -> MapQueryResult:
            time.sleep(0.11)
            return super().fetch_map(fields=fields)

    source = SlowMapWorldSource([make_frames()[0]])
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    world.refresh(now=101.0, force=True)
    world.refresh(now=111.0, force=True)

    slow_logs = logging_system.query(component="world_model", event="world_refresh_slow")
    assert len(slow_logs) == 2
    assert slow_logs[0].data.get("suppressed_count") == 0
    assert slow_logs[1].data.get("suppressed_count") == 1
    assert slow_logs[1].data.get("layer_ms", {}).get("map", 0) >= 100
    print("  PASS: slow_refresh_logging_is_throttled_with_suppressed_count")


def test_base_under_attack_requires_nearby_enemy_combat_and_meaningful_damage() -> None:
    frames = [
        Frame(
            self_actors=[
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=97, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=80, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(15, 15), hppercent=60, activity="Idle"),
            ],
            enemy_actors=[
                Actor(actor_id=201, type="重坦", faction="敌人", position=Location(60, 55), hppercent=100, activity="AttackMove"),
            ],
            economy=PlayerBaseInfo(Cash=2500, Resources=300, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
    ]
    source = MockWorldSource(frames)
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)

    source.set_frame(1)
    events = world.refresh(now=101.0, force=True)
    assert EventType.UNIT_DAMAGED in {event.type for event in events}
    assert EventType.BASE_UNDER_ATTACK not in {event.type for event in events}

    source.set_frame(2)
    events = world.refresh(now=102.0, force=True)
    assert EventType.BASE_UNDER_ATTACK not in {event.type for event in events}

    source.set_frame(3)
    events = world.refresh(now=103.0, force=True)
    assert EventType.BASE_UNDER_ATTACK in {event.type for event in events}
    print("  PASS: base_under_attack_requires_nearby_enemy_combat_and_meaningful_damage")


def test_mcv_deploy_is_not_reported_as_structure_loss_or_base_attack() -> None:
    frames = [
        Frame(
            self_actors=[
                Actor(actor_id=129, type="基地车", faction="自己", position=Location(90, 12), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=136, type="建造厂", faction="自己", position=Location(89, 11), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={},
        ),
    ]
    source = MockWorldSource(frames)
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    source.set_frame(1)
    events = world.refresh(now=101.0, force=True)
    types = {event.type for event in events}

    assert EventType.STRUCTURE_LOST not in types
    assert EventType.BASE_UNDER_ATTACK not in types
    print("  PASS: mcv_deploy_is_not_reported_as_structure_loss_or_base_attack")


def test_match_reset_emits_game_reset_event() -> None:
    frames = [
        Frame(
            self_actors=[
                Actor(actor_id=130, type="建造厂", faction="自己", position=Location(15, 112), hppercent=100, activity="Idle"),
                Actor(actor_id=131, type="发电厂", faction="自己", position=Location(16, 116), hppercent=100, activity="Idle"),
                Actor(actor_id=152, type="矿场", faction="自己", position=Location(10, 116), hppercent=100, activity="Idle"),
                Actor(actor_id=156, type="步兵", faction="自己", position=Location(78, 65), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=2950, Resources=65075, Power=240, PowerDrained=260, PowerProvided=500),
            map_info=make_map(explored=0.25, visible=0.05),
            queues={},
        ),
        Frame(
            self_actors=[
                Actor(actor_id=129, type="基地车", faction="自己", position=Location(113, 16), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
            map_info=make_map(explored=0.0, visible=0.0),
            queues={},
        ),
    ]
    source = MockWorldSource(frames)
    world = WorldModel(source)

    world.refresh(now=100.0, force=True)
    source.set_frame(1)
    events = world.refresh(now=101.0, force=True)

    assert [event.type for event in events] == [EventType.GAME_RESET]
    print("  PASS: match_reset_emits_game_reset_event")


def test_compute_runtime_facts_no_base() -> None:
    """When there are no buildings, tech_level=0 and mcv_count matches."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="基地车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=1000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1")
    assert facts["mcv_count"] == 1, facts
    assert facts["mcv_idle"] is True, facts
    assert facts["has_construction_yard"] is False, facts
    assert facts["tech_level"] == 0, facts
    assert facts["active_task_count"] == 0, facts
    assert facts["this_task_jobs"] == [], facts
    assert facts["failed_job_count"] == 0, facts


def test_compute_runtime_facts_full_base() -> None:
    """All major buildings present → correct flags and tech_level=3."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="兵营", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="矿场", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="战车工厂", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=6, type="雷达站", faction="自己", position=Location(15, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=7, type="矿车", faction="自己", position=Location(20, 20), hppercent=100, activity="Move"),
            Actor(actor_id=8, type="矿车", faction="自己", position=Location(21, 20), hppercent=100, activity="Move"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=3000, Resources=500, Power=120, PowerDrained=80, PowerProvided=200),
        map_info=make_map(0.5, 0.2),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1")
    assert facts["has_construction_yard"] is True, facts
    assert facts["power_plant_count"] == 1, facts
    assert facts["barracks_count"] == 1, facts
    assert facts["refinery_count"] == 1, facts
    assert facts["war_factory_count"] == 1, facts
    assert facts["radar_count"] == 1, facts
    assert facts["tech_level"] == 3, facts
    assert facts["mcv_count"] == 0, facts
    assert facts["harvester_count"] == 2, facts
    assert facts["can_afford_power_plant"] is True, facts
    assert facts["can_afford_refinery"] is True, facts


def test_compute_runtime_facts_partial_base() -> None:
    """yard + power only → tech_level=1, cannot afford refinery with low credits."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=500, Resources=0, Power=50, PowerDrained=30, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1")
    assert facts["has_construction_yard"] is True, facts
    assert facts["power_plant_count"] >= 1, facts
    assert facts["barracks_count"] == 0, facts
    assert facts["refinery_count"] == 0, facts
    assert facts["tech_level"] == 1, facts
    assert facts["can_afford_refinery"] is False, facts
    assert facts["base_progression"]["next_unit_type"] == "proc", facts
    assert facts["base_progression"]["buildable_now"] is True, facts


def test_compute_runtime_facts_infers_player_faction_from_specific_units() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="基地车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="米格战机", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=500, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1")
    assert facts["faction"] == "soviet", facts


def test_compute_runtime_facts_leaves_player_faction_empty_when_ambiguous() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=500, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1")
    assert facts["faction"] is None, facts


def test_compute_runtime_facts_fails_closed_for_unsupported_allied_capability_roster() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="tent", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=6, type="2tnk", faction="自己", position=Location(15, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=200, PowerDrained=120, PowerProvided=200),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    assert facts["faction"] == "allied", facts
    assert facts["capability_truth_blocker"] == "faction_roster_unsupported", facts
    assert facts["buildable"] == {}, facts
    assert facts["buildable_now"] == {}, facts
    assert facts["buildable_blocked"] == {}, facts
    assert facts["base_progression"] == {}, facts
    assert facts["feasibility"]["produce_units"] is False, facts


def test_compute_runtime_facts_surfaces_truth_blocker_without_buildable_snapshot() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="tent", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=200, PowerDrained=120, PowerProvided=200),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t_cap", include_buildable=False)
    assert facts["faction"] == "allied", facts
    assert facts["capability_truth_blocker"] == "faction_roster_unsupported", facts
    assert "buildable" not in facts, facts
    assert "buildable_now" not in facts, facts
    assert "buildable_blocked" not in facts, facts
    assert "base_progression" not in facts, facts


def test_runtime_facts_buildable_requires_power_for_proc() -> None:
    """Buildability should not expose proc before a power plant exists."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    buildable = wm.runtime_facts_buildable()
    assert buildable["Building"] == ["powr"], buildable


def test_runtime_facts_buildable_exposes_airfield_and_top_tier_units() -> None:
    """Demo buildability should include afld/stek prerequisites for yak/mig/4tnk."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="兵营", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="战车工厂", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=6, type="雷达站", faction="自己", position=Location(15, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=7, type="维修厂", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=8, type="科技中心", faction="自己", position=Location(17, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=9, type="空军基地", faction="自己", position=Location(18, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=150, PowerDrained=120, PowerProvided=200),
        map_info=make_map(0.3, 0.1),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    buildable = wm.runtime_facts_buildable()
    assert "stek" in buildable["Building"], buildable
    assert "afld" in buildable["Building"], buildable
    assert "4tnk" in buildable["Vehicle"], buildable
    assert "mig" in buildable["Aircraft"], buildable
    assert "yak" in buildable["Aircraft"], buildable


def test_runtime_facts_buildable_hides_disabled_gateway_unlocks() -> None:
    """Disabled gateway buildings should not continue to unlock downstream buildables."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(
                actor_id=5,
                type="雷达站",
                faction="自己",
                position=Location(14, 10),
                hppercent=100,
                activity="Idle",
                is_disabled=True,
                is_powered_down=True,
                disabled_reason="powerdown",
            ),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=150, PowerDrained=80, PowerProvided=200),
        map_info=make_map(0.3, 0.1),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    buildable = wm.runtime_facts_buildable()
    assert "afld" not in buildable.get("Building", []), buildable
    assert "stek" not in buildable.get("Building", []), buildable
    assert "v2rl" not in buildable.get("Vehicle", []), buildable


def test_production_readiness_blocks_when_world_sync_is_stale() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="兵营", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=100, PowerDrained=40, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    wm.state.stale = True
    readiness = wm.production_readiness_for("e1")
    assert readiness["prereq_satisfied"] is True
    assert readiness["can_issue_now"] is False
    assert readiness["reason"] == "world_sync_stale"


def test_production_readiness_marks_deploy_required_when_only_mcv_exists() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="基地车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    readiness = wm.production_readiness_for("powr")
    assert readiness["deploy_required"] is True
    assert readiness["can_issue_now"] is False
    assert readiness["reason"] == "deploy_required"


def test_production_readiness_marks_queue_blocked_for_ready_item() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=100, PowerDrained=20, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={
            "Building": {
                "queue_type": "Building",
                "items": [{"name": "powr", "done": True, "paused": False, "status": "done", "owner_actor_id": 1}],
                "has_ready_item": True,
            }
        },
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    readiness = wm.production_readiness_for("powr")
    assert readiness["prereq_satisfied"] is True
    assert readiness["can_issue_now"] is False
    assert readiness["reason"] == "queue_blocked"
    assert readiness["queue_blocked_reason"] == "ready_not_placed"
    assert readiness["queue_blocked_queue_types"] == ["Building"]
    assert readiness["queue_blocked_items"] == [
        {
            "queue_type": "Building",
            "unit_type": "powr",
            "display_name": "发电厂",
            "owner_actor_id": 1,
        }
    ]


def test_production_readiness_marks_producer_disabled_when_factory_is_offline() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(
                actor_id=4,
                type="战车工厂",
                faction="自己",
                position=Location(13, 10),
                hppercent=100,
                activity="Idle",
                is_disabled=True,
                is_powered_down=True,
                disabled_reason="powerdown",
            ),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=100, PowerDrained=20, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    readiness = wm.production_readiness_for("ftrk")
    assert readiness["prereq_satisfied"] is True
    assert readiness["can_issue_now"] is False
    assert readiness["reason"] == "producer_disabled"
    assert readiness["producer_count"] == 1
    assert readiness["active_producer_count"] == 0
    assert readiness["disabled_producer_count"] == 1
    assert readiness["disabled_producers"] == ["战车工厂(powerdown)"]


def test_production_readiness_marks_disabled_prerequisite_when_gateway_is_offline() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(
                actor_id=5,
                type="雷达站",
                faction="自己",
                position=Location(14, 10),
                hppercent=100,
                activity="Idle",
                is_disabled=True,
                is_powered_down=True,
                disabled_reason="powerdown",
            ),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=120, PowerDrained=80, PowerProvided=200),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    readiness = wm.production_readiness_for("stek")
    assert readiness["prereq_satisfied"] is True
    assert readiness["active_prereq_satisfied"] is False
    assert readiness["can_issue_now"] is False
    assert readiness["reason"] == "disabled_prerequisite"
    assert readiness["disabled_prerequisites"] == ["雷达站(powerdown)"]

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    blocked = {
        item["unit_type"]: item
        for item in facts["buildable_blocked"]["Building"]
    }
    assert "stek" not in facts["buildable"].get("Building", []), facts
    assert blocked["stek"]["reason"] == "disabled_prerequisite", blocked
    assert blocked["stek"]["disabled_prerequisites"] == ["雷达站(powerdown)"], blocked


def test_world_summary_and_runtime_facts_expose_queue_block_reason() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=100, PowerDrained=20, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={
            "Building": {
                "queue_type": "Building",
                "items": [{"name": "powr", "paused": True, "status": "building"}],
                "has_ready_item": False,
            }
        },
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    summary = wm.world_summary()
    facts = wm.compute_runtime_facts("t1", include_buildable=True)
    assert summary["economy"]["queue_blocked"] is True
    assert summary["economy"]["queue_blocked_reason"] == "paused"
    assert summary["economy"]["queue_blocked_queue_types"] == ["Building"]
    assert summary["economy"]["queue_blocked_items"] == []
    assert facts["queue_blocked"] is True
    assert facts["queue_blocked_reason"] == "paused"
    assert facts["queue_blocked_queue_types"] == ["Building"]
    assert facts["queue_blocked_items"] == []


def test_production_readiness_allows_power_recovery_while_low_power() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=50, PowerDrained=80, PowerProvided=50),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    powr = wm.production_readiness_for("powr")
    proc = wm.production_readiness_for("proc")
    assert powr["can_issue_now"] is True
    assert powr["reason"] == ""
    assert proc["can_issue_now"] is False
    assert proc["reason"] == "low_power"


def test_runtime_facts_split_prereq_truth_from_issue_now_truth() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=50, PowerDrained=80, PowerProvided=50),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    assert facts["buildable"]["Building"] == ["powr", "proc", "barr"], facts
    assert facts["buildable_now"]["Building"] == ["powr"], facts
    blocked = {
        item["unit_type"]: item
        for item in facts["buildable_blocked"]["Building"]
    }
    assert blocked["proc"]["reason"] == "low_power", blocked
    assert blocked["barr"]["reason"] == "low_power", blocked


def test_runtime_facts_buildable_blocked_filters_off_roster_queue_items() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=100, PowerDrained=20, PowerProvided=100),
        map_info=make_map(0.1, 0.05),
        queues={
            "Building": {
                "queue_type": "Building",
                "items": [{"name": "防空炮", "display_name": "防空炮", "done": True, "paused": False, "status": "done", "owner_actor_id": 9}],
                "has_ready_item": True,
            }
        },
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    blocked_entries = list(facts.get("buildable_blocked", {}).get("Building", []) or [])
    assert blocked_entries, facts
    assert all(item.get("reason") == "queue_blocked" for item in blocked_entries), blocked_entries
    assert all(item.get("queue_blocked_items") == [] for item in blocked_entries), blocked_entries


def test_compute_runtime_facts_this_task_jobs() -> None:
    """this_task_jobs reflects active_jobs for the queried task_id."""
    source = MockWorldSource([Frame(
        self_actors=[],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=1000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    wm.set_runtime_state(
        active_jobs={
            "j1": {"task_id": "t1", "expert_type": "ReconExpert", "status": "running"},
            "j2": {"task_id": "t2", "expert_type": "CombatExpert", "status": "running"},
        },
        job_stats_by_task={
            "t1": {"failed_count": 1, "expert_attempts": {"ReconExpert": 2}},
        },
    )
    facts = wm.compute_runtime_facts("t1")
    assert len(facts["this_task_jobs"]) == 1, facts
    assert facts["this_task_jobs"][0]["expert_type"] == "ReconExpert", facts
    assert facts["failed_job_count"] == 1, facts
    assert facts["same_expert_retry_count"] == 1, facts  # 2 attempts - 1


def test_compute_runtime_facts_exposes_unit_reservations() -> None:
    """Capability-facing runtime facts should include reservation records."""
    source = MockWorldSource([Frame(
        self_actors=[],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=1000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    wm.set_runtime_state(
        unit_reservations=[
            {
                "reservation_id": "res_1",
                "request_id": "req_1",
                "task_id": "t1",
                "task_label": "003",
                "unit_type": "3tnk",
                "count": 2,
                "remaining_count": 1,
                "assigned_actor_ids": [11],
                "produced_actor_ids": [21],
                "status": "partial",
                "bootstrap_job_id": "j_boot",
            }
        ]
    )
    facts = wm.compute_runtime_facts("t1")
    assert len(facts["unit_reservations"]) == 1, facts
    assert facts["unit_reservations"][0]["reservation_id"] == "res_1", facts
    assert facts["unit_reservations"][0]["remaining_count"] == 1, facts


def test_compute_runtime_facts_ordinary_view_omits_buildability() -> None:
    """Ordinary task view should not expose buildable/feasibility/economy planning hints."""
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="兵营", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=3000, Resources=500, Power=120, PowerDrained=80, PowerProvided=200),
        map_info=make_map(0.5, 0.2),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)
    facts = wm.compute_runtime_facts("t1", include_buildable=False)
    assert "buildable" not in facts, facts
    assert "feasibility" not in facts, facts
    assert "can_afford_power_plant" not in facts, facts
    assert "can_afford_barracks" not in facts, facts
    assert "can_afford_refinery" not in facts, facts
    assert facts["has_construction_yard"] is True, facts


def test_runtime_facts_injected_in_context_packet() -> None:
    """build_context_packet carries runtime_facts through to context_to_message JSON."""
    from task_agent import build_context_packet, context_to_message, WorldSummary
    from models import Task, TaskKind

    task = Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50)
    facts = {"mcv_count": 1, "has_construction_yard": False, "tech_level": 0}
    packet = build_context_packet(task=task, jobs=[], runtime_facts=facts)
    assert packet.runtime_facts == facts

    msg = context_to_message(packet)
    content = msg["content"]
    # Compact format includes runtime facts as key=value pairs
    assert "mcv_count=1" in content
    assert "has_construction_yard=False" in content
    assert "tech_level=0" in content


def test_runtime_facts_exposes_ready_queue_items_and_capability_context_renders_them() -> None:
    from task_agent import WorldSummary, build_context_packet, context_to_message
    from models import Task, TaskKind

    source = MockWorldSource([make_frames()[1]])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap")
    assert facts["ready_queue_items"] == [
        {
            "queue_type": "Vehicle",
            "unit_type": "重坦",
            "display_name": "重型坦克",
            "owner_actor_id": 30,
        }
    ], facts

    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=80)
    task.is_capability = True
    summary = wm.world_summary()
    packet = build_context_packet(
        task=task,
        jobs=[],
        runtime_facts=facts,
        world_summary=WorldSummary(
            economy=summary.get("economy", {}),
            military=summary.get("military", {}),
            map=summary.get("map", {}),
            known_enemy=summary.get("known_enemy", {}),
        ),
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[待处理已就绪条目]" in msg["content"], msg["content"]
    assert "Vehicle: 重型坦克 owner=30" in msg["content"], msg["content"]


def test_capability_context_fails_closed_when_world_model_knows_allied_faction() -> None:
    from task_agent import WorldSummary, build_context_packet, context_to_message
    from models import Task, TaskKind

    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="tent", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=6, type="2tnk", faction="自己", position=Location(15, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=200, PowerDrained=120, PowerProvided=200),
        map_info=make_map(0.1, 0.05),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    summary = wm.world_summary()
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=80)
    task.is_capability = True
    packet = build_context_packet(
        task=task,
        jobs=[],
        runtime_facts=facts,
        world_summary=WorldSummary(
            economy=summary.get("economy", {}),
            military=summary.get("military", {}),
            map=summary.get("map", {}),
            known_enemy=summary.get("known_enemy", {}),
        ),
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[能力真值] faction=allied 当前 demo capability roster 未覆盖该阵营" in msg["content"], msg["content"]
    assert "[可立即下单]" not in msg["content"], msg["content"]
    assert "[基地推进]" not in msg["content"], msg["content"]


def test_capability_context_suppresses_low_power_nudge_with_ready_power_from_world_model() -> None:
    from task_agent import WorldSummary, build_context_packet, context_to_message
    from models import Task, TaskKind

    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=2000, Resources=0, Power=50, PowerDrained=80, PowerProvided=50),
        map_info=make_map(0.1, 0.05),
        queues={
            "Building": {
                "queue_type": "Building",
                "items": [{"name": "powr", "done": True, "paused": False, "status": "done", "owner_actor_id": 1}],
                "has_ready_item": True,
            }
        },
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)
    summary = wm.world_summary()
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=80)
    task.is_capability = True
    packet = build_context_packet(
        task=task,
        jobs=[],
        runtime_facts=facts,
        world_summary=WorldSummary(
            economy=summary.get("economy", {}),
            military=summary.get("military", {}),
            map=summary.get("map", {}),
            known_enemy=summary.get("known_enemy", {}),
        ),
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[待处理已就绪条目]" in msg["content"], msg["content"]
    assert "Building: 发电厂 owner=1" in msg["content"], msg["content"]
    assert "[队列阻塞] 有已完成未放置条目" in msg["content"], msg["content"]
    assert "⚡低电力" not in msg["content"], msg["content"]


def test_runtime_facts_feasibility_derive_from_buildable_truth() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(actor_id=1, type="空军基地", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=50, Resources=0, Power=100, PowerDrained=40, PowerProvided=140),
        map_info=make_map(0.3, 0.1),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    facts = wm.compute_runtime_facts("t_cap", include_buildable=True)

    assert facts["buildable"]["Aircraft"] == ["mig", "yak"], facts
    assert facts["feasibility"]["produce_units"] is True, facts


def test_world_summary_and_runtime_facts_expose_structure_power_states() -> None:
    source = MockWorldSource([Frame(
        self_actors=[
            Actor(
                actor_id=1,
                type="雷达站",
                faction="自己",
                position=Location(10, 10),
                hppercent=100,
                activity="Idle",
                is_disabled=True,
                has_low_power=True,
                disabled_reason="lowpower",
            ),
            Actor(
                actor_id=2,
                type="防空炮",
                faction="自己",
                position=Location(12, 10),
                hppercent=100,
                activity="Idle",
                is_disabled=True,
                is_powered_down=True,
                disabled_reason="powerdown",
            ),
            Actor(
                actor_id=3,
                type="电厂",
                faction="自己",
                position=Location(14, 10),
                hppercent=100,
                activity="Idle",
            ),
        ],
        enemy_actors=[],
        economy=PlayerBaseInfo(Cash=600, Resources=0, Power=20, PowerDrained=80, PowerProvided=60),
        map_info=make_map(0.3, 0.1),
        queues={},
    )])
    wm = WorldModel(source)
    wm.refresh(force=True)

    summary = wm.world_summary()
    facts = wm.compute_runtime_facts("t_cap")
    actors = wm.query("actors")["actors"]

    assert summary["economy"]["disabled_structure_count"] == 2, summary
    assert summary["economy"]["powered_down_structure_count"] == 1, summary
    assert summary["economy"]["low_power_disabled_structure_count"] == 1, summary
    assert summary["economy"]["disabled_structures"] == ["雷达站(lowpower)", "防空炮(powerdown)"], summary

    assert facts["disabled_structure_count"] == 2, facts
    assert facts["powered_down_structure_count"] == 1, facts
    assert facts["low_power_disabled_structure_count"] == 1, facts
    assert facts["disabled_structures"] == ["雷达站(lowpower)", "防空炮(powerdown)"], facts

    by_id = {int(actor["actor_id"]): actor for actor in actors}
    assert by_id[1]["is_disabled"] is True
    assert by_id[1]["has_low_power"] is True
    assert by_id[1]["disabled_reason"] == "lowpower"
    assert by_id[2]["is_powered_down"] is True
    assert by_id[2]["disabled_reason"] == "powerdown"


def main() -> None:
    test_refresh_layers_and_summary()
    test_layered_refresh_respects_intervals()
    test_category_inference_marks_buildings_correctly()
    test_actor_queries_honor_type_aliases()
    test_event_detection_and_queries()
    test_unit_death_runtime_state_and_constraints()
    test_runtime_state_exposes_capability_status_and_battlefield_snapshot()
    test_refresh_failure_marks_stale_and_recovers()
    test_refresh_failure_logging_is_throttled_per_layer_until_recovery()
    test_refresh_failure_logs_include_exception_detail_when_available()
    test_slow_refresh_logging_is_throttled_with_suppressed_count()
    test_base_under_attack_requires_nearby_enemy_combat_and_meaningful_damage()
    test_mcv_deploy_is_not_reported_as_structure_loss_or_base_attack()
    test_match_reset_emits_game_reset_event()
    test_compute_runtime_facts_no_base()
    test_compute_runtime_facts_full_base()
    test_compute_runtime_facts_partial_base()
    test_runtime_facts_buildable_requires_power_for_proc()
    test_runtime_facts_buildable_exposes_airfield_and_top_tier_units()
    test_production_readiness_marks_producer_disabled_when_factory_is_offline()
    test_compute_runtime_facts_this_task_jobs()
    test_compute_runtime_facts_exposes_unit_reservations()
    test_compute_runtime_facts_ordinary_view_omits_buildability()
    test_runtime_facts_injected_in_context_packet()
    test_runtime_facts_exposes_ready_queue_items_and_capability_context_renders_them()
    test_world_summary_and_runtime_facts_expose_structure_power_states()
    print("OK: WorldModel tests passed")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
