"""Basic tests for WorldModel v1."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
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


def main() -> None:
    test_refresh_layers_and_summary()
    test_layered_refresh_respects_intervals()
    test_event_detection_and_queries()
    test_unit_death_runtime_state_and_constraints()
    test_refresh_failure_marks_stale_and_recovers()
    test_base_under_attack_requires_nearby_enemy_combat_and_meaningful_damage()
    print("OK: 6 WorldModel tests passed")


if __name__ == "__main__":
    main()
