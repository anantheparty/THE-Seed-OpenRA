"""Basic tests for WorldModel v1."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import logging_system
from models import Constraint, ConstraintEnforcement, EventType
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from world_model import WorldModel


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

    def fetch_economy(self) -> PlayerBaseInfo:
        self.economy_fetches += 1
        return self._frame().economy

    def fetch_map(self) -> MapQueryResult:
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

    def fetch_map(self) -> MapQueryResult:
        self._maybe_fail("map")
        return super().fetch_map()

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

    def fetch_map(self) -> MapQueryResult:
        return super().fetch_map()

    def fetch_production_queues(self) -> dict[str, dict]:
        return super().fetch_production_queues()


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
    return [
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
    assert health["consecutive_failures"] == 3
    assert health["failure_threshold"] == 3
    assert "actors disconnected" in health["last_error"]

    source.fail = False
    world.refresh(now=104.0, force=True)
    recovered = world.refresh_health()

    assert recovered["stale"] is False
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_error"] is None
    print("  PASS: refresh_failure_marks_stale_and_recovers")


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
    assert facts["has_power"] is True, facts
    assert facts["has_barracks"] is True, facts
    assert facts["has_refinery"] is True, facts
    assert facts["has_war_factory"] is True, facts
    assert facts["has_radar"] is True, facts
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
    assert facts["has_power"] is True, facts
    assert facts["has_barracks"] is False, facts
    assert facts["has_refinery"] is False, facts
    assert facts["tech_level"] == 1, facts
    assert facts["can_afford_refinery"] is False, facts


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
    parsed = __import__("json").loads(content.replace("[CONTEXT UPDATE]\n", ""))
    assert parsed["context_packet"]["runtime_facts"]["mcv_count"] == 1, parsed


def main() -> None:
    test_refresh_layers_and_summary()
    test_layered_refresh_respects_intervals()
    test_category_inference_marks_buildings_correctly()
    test_actor_queries_honor_type_aliases()
    test_event_detection_and_queries()
    test_unit_death_runtime_state_and_constraints()
    test_refresh_failure_marks_stale_and_recovers()
    test_refresh_failure_logging_is_throttled_per_layer_until_recovery()
    test_refresh_failure_logs_include_exception_detail_when_available()
    test_base_under_attack_requires_nearby_enemy_combat_and_meaningful_damage()
    test_mcv_deploy_is_not_reported_as_structure_loss_or_base_attack()
    test_match_reset_emits_game_reset_event()
    test_compute_runtime_facts_no_base()
    test_compute_runtime_facts_full_base()
    test_compute_runtime_facts_partial_base()
    test_compute_runtime_facts_this_task_jobs()
    test_runtime_facts_injected_in_context_packet()
    print("OK: 17 WorldModel tests passed")


if __name__ == "__main__":
    main()
