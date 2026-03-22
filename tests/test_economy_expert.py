"""Tests for EconomyExpert and EconomyJob."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.economy import EconomyExpert, EconomyJob
from models import EconomyJobConfig, JobStatus, ResourceKind, SignalKind


class MockGameAPI:
    def __init__(self) -> None:
        self.produce_calls: list[dict] = []
        self.place_building_calls: list[dict] = []
        self.can_produce_value = True

    def can_produce(self, unit_type: str) -> bool:
        return self.can_produce_value

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = False) -> int:
        self.produce_calls.append(
            {
                "unit_type": unit_type,
                "quantity": quantity,
                "auto_place_building": auto_place_building,
            }
        )
        return len(self.produce_calls)

    def place_building(self, queue_type: str, location=None) -> None:
        self.place_building_calls.append({"queue_type": queue_type, "location": location})


class MockWorldModel:
    def __init__(self) -> None:
        self.economy = {"total_credits": 3000, "low_power": False}
        self.queues = {
            "Vehicle": {
                "queue_type": "Vehicle",
                "items": [],
                "has_ready_item": False,
            }
        }
        self.events: list[dict] = []

    def query(self, query_type: str, params: dict | None = None):
        if query_type == "economy":
            return dict(self.economy)
        if query_type == "production_queues":
            return {name: dict(queue) for name, queue in self.queues.items()}
        if query_type == "events":
            return {"events": [dict(event) for event in self.events], "timestamp": 1.0}
        raise ValueError(f"Unsupported query_type: {query_type}")


def make_config(**overrides) -> EconomyJobConfig:
    base = {"unit_type": "2tnk", "count": 2, "queue_type": "Vehicle", "repeat": False}
    base.update(overrides)
    return EconomyJobConfig(**base)


def test_economy_expert_creates_queue_job() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    expert = EconomyExpert(game_api=api, world_model=world)
    signals = []

    job = expert.create_job("t1", make_config(), signals.append)

    assert isinstance(job, EconomyJob)
    assert job.expert_type == "EconomyExpert"
    assert job.tick_interval == 5.0
    needs = job.get_resource_needs()
    assert len(needs) == 1
    assert needs[0].kind == ResourceKind.PRODUCTION_QUEUE
    assert needs[0].predicates == {"queue_type": "Vehicle"}
    print("  PASS: economy_expert_creates_queue_job")


def test_economy_job_emits_progress_and_finishes() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(count=2),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["queue:Vehicle"])

    job.tick()
    assert len(api.produce_calls) == 1

    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": 10.0,
            "data": {"queue_type": "Vehicle", "name": "2tnk", "display_name": "重坦"},
        }
    ]
    world.queues["Vehicle"]["items"] = []
    job.tick()

    assert signals[0].kind == SignalKind.PROGRESS
    assert signals[0].data["produced_count"] == 1
    assert len(api.produce_calls) == 2

    world.events.append(
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": 20.0,
            "data": {"queue_type": "Vehicle", "name": "2tnk", "display_name": "重坦"},
        }
    )
    job.tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-2].kind == SignalKind.PROGRESS
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "succeeded"
    assert signals[-1].data["produced_count"] == 2
    print("  PASS: economy_job_emits_progress_and_finishes")


def test_economy_job_waits_on_low_power_and_recovers() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.economy["low_power"] = True
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(count=1),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["queue:Vehicle"])

    job.do_tick()
    assert job.status == JobStatus.WAITING
    assert signals[-1].kind == SignalKind.BLOCKED
    assert signals[-1].data["reason"] == "low_power"
    assert api.produce_calls == []

    world.economy["low_power"] = False
    job.do_tick()
    assert job.status == JobStatus.RUNNING
    assert len(api.produce_calls) == 1
    print("  PASS: economy_job_waits_on_low_power_and_recovers")


def test_economy_job_waits_when_queue_missing() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {}
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(count=1),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["queue:Vehicle"])

    job.do_tick()

    assert job.status == JobStatus.WAITING
    assert job.phase == "waiting"
    assert signals[-1].kind == SignalKind.BLOCKED
    assert signals[-1].data["reason"] == "queue_missing"
    print("  PASS: economy_job_waits_when_queue_missing")


def test_economy_job_matches_aliases_in_queue_and_completion_events() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="PowerPlant", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [{"name": "powr", "display_name": "发电厂", "done": False, "paused": False}],
            "has_ready_item": False,
        }
    }
    job.on_resource_granted(["queue:Building"])

    job.tick()
    assert api.produce_calls == []

    world.queues["Building"]["items"] = []
    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": 10.0,
            "data": {"queue_type": "Building", "name": "powr", "display_name": "发电厂"},
        }
    ]
    job.tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    print("  PASS: economy_job_matches_aliases_in_queue_and_completion_events")


def test_economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="PowerPlant", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["queue:Building"])

    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [{"name": "powr", "display_name": "发电厂", "done": True, "paused": False}],
            "has_ready_item": True,
        }
    }
    job.tick()
    assert api.place_building_calls == [{"queue_type": "Building", "location": None}]
    assert api.produce_calls == []

    world.queues["Building"] = {
        "queue_type": "Building",
        "items": [{"name": "tent", "display_name": "兵营", "done": True, "paused": False}],
        "has_ready_item": True,
    }
    job.tick()
    assert signals[-1].kind == SignalKind.BLOCKED
    assert signals[-1].data["reason"] == "queue_ready_item_pending"
    print("  PASS: economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items")


def test_economy_job_enables_auto_place_for_buildings() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}}
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="PowerPlant", count=1, queue_type="Building"),
        signal_callback=lambda _signal: None,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["queue:Building"])

    job.tick()

    assert api.produce_calls == [
        {"unit_type": "PowerPlant", "quantity": 1, "auto_place_building": True}
    ]
    print("  PASS: economy_job_enables_auto_place_for_buildings")


if __name__ == "__main__":
    print("Running EconomyExpert tests...\n")
    test_economy_expert_creates_queue_job()
    test_economy_job_emits_progress_and_finishes()
    test_economy_job_waits_on_low_power_and_recovers()
    test_economy_job_waits_when_queue_missing()
    test_economy_job_matches_aliases_in_queue_and_completion_events()
    test_economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items()
    test_economy_job_enables_auto_place_for_buildings()
    print("\nAll 7 EconomyExpert tests passed!")
