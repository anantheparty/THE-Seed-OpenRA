"""Tests for EconomyExpert and EconomyJob."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.economy import EconomyExpert, EconomyJob
from models import EconomyJobConfig, JobStatus, SignalKind
from openra_api.game_api import GameAPIError


class MockGameAPI:
    def __init__(self) -> None:
        self.produce_calls: list[dict] = []
        self.place_building_calls: list[dict] = []
        self.manage_production_calls: list[dict] = []
        self.can_produce_value = True
        self.place_building_error: GameAPIError | None = None

    def can_produce(self, unit_type: str) -> bool:
        return self.can_produce_value

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> int:
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
        if self.place_building_error is not None:
            raise self.place_building_error

    def manage_production(
        self,
        queue_type: str,
        action: str,
        *,
        owner_actor_id=None,
        item_name=None,
        count=1,
    ) -> None:
        self.manage_production_calls.append(
            {
                "queue_type": queue_type,
                "action": action,
                "owner_actor_id": owner_actor_id,
                "item_name": item_name,
                "count": count,
            }
        )


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
        self.actors: list[dict] = []

    def query(self, query_type: str, params: dict | None = None):
        if query_type == "economy":
            return dict(self.economy)
        if query_type == "production_queues":
            return {name: dict(queue) for name, queue in self.queues.items()}
        if query_type == "events":
            return {"events": [dict(event) for event in self.events], "timestamp": 1.0}
        if query_type == "my_actors":
            params = params or {}
            category = params.get("category")
            actors = [dict(actor) for actor in self.actors if not category or actor.get("category") == category]
            return {"actors": actors, "timestamp": 1.0}
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
    assert needs == [], f"EconomyJob must not declare PRODUCTION_QUEUE resource: {needs}"
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

    job.tick()
    assert len(api.produce_calls) == 1
    # Vehicle queue: batched — single produce call with quantity=2
    assert api.produce_calls[0]["quantity"] == 2

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
    # No additional produce call — all units already issued in batch
    assert len(api.produce_calls) == 1

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

    job.do_tick()
    assert job.status == JobStatus.WAITING
    assert signals[-1].kind.value == SignalKind.BLOCKED.value
    assert signals[-1].data["reason"] == "low_power"
    assert signals[-1].data["impact"]["kind"] == "power_state"
    assert signals[-1].data["impact"]["effects"] == ["queue_slowdown", "structure_disable_possible"]
    assert signals[-1].data["recommendation"]["kind"] == "power_recovery"
    assert signals[-1].data["recommendation"]["queue_scope"] == "player_shared"
    assert signals[-1].data["knowledge"]["queue_scope"] == "player_shared"
    assert [item["unit_type"] for item in signals[-1].data["recommendation"]["options"]] == ["powr", "apwr"]
    assert "power_recovery" not in signals[-1].data["knowledge"]["roles"]
    assert "建议补建" in signals[-1].summary
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

    job.do_tick()

    assert job.status == JobStatus.WAITING
    assert job.phase == "waiting"
    assert signals[-1].kind.value == SignalKind.BLOCKED.value
    assert signals[-1].data["reason"] == "queue_missing"
    print("  PASS: economy_job_waits_when_queue_missing")


def test_economy_job_can_build_power_while_low_power() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.economy["low_power"] = True
    world.queues = {"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}}
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="PowerPlant", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    assert job.status == JobStatus.RUNNING
    assert api.produce_calls == [
        {"unit_type": "PowerPlant", "quantity": 1, "auto_place_building": True}
    ]
    assert signals == []
    print("  PASS: economy_job_can_build_power_while_low_power")


def test_economy_job_abort_does_not_cancel_shared_queue() -> None:
    """Aborting an EconomyJob must NOT cancel queue items — the queue is shared."""
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [
                {"name": "barr", "display_name": "兵营", "done": True, "paused": False},
                {"name": "proc", "display_name": "矿场", "done": False, "paused": False},
            ],
            "has_ready_item": True,
        }
    }
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="barr", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.abort()

    assert api.manage_production_calls == [], "abort must not cancel queue items in shared queue"
    assert job.status == JobStatus.ABORTED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "aborted"
    print("  PASS: economy_job_abort_does_not_cancel_shared_queue")


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
    world.queues["Building"] = {
        "queue_type": "Building",
        "items": [{"name": "powr", "display_name": "发电厂", "done": True, "paused": False}],
        "has_ready_item": True,
    }
    job.tick()

    assert job.status == JobStatus.SUCCEEDED
    assert api.place_building_calls == [{"queue_type": "Building", "location": None}]
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    print("  PASS: economy_job_matches_aliases_in_queue_and_completion_events")


def test_economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="PowerPlant", count=2, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [{"name": "powr", "display_name": "发电厂", "done": True, "paused": False}],
            "has_ready_item": True,
        }
    }
    job.tick()
    assert api.place_building_calls == [{"queue_type": "Building", "location": None}]
    assert api.produce_calls == [{"unit_type": "PowerPlant", "quantity": 1, "auto_place_building": True}]
    assert job.status == JobStatus.RUNNING

    world.queues["Building"] = {
        "queue_type": "Building",
        "items": [{"name": "tent", "display_name": "兵营", "done": True, "paused": False}],
        "has_ready_item": True,
    }
    job.tick()
    blocked = [
        signal for signal in signals
        if signal.kind.value == SignalKind.BLOCKED.value
        and signal.data
        and signal.data.get("reason") == "queue_ready_item_pending"
    ]
    assert blocked
    assert blocked[-1].data["recommendation"]["kind"] == "clear_ready_building"
    assert blocked[-1].data["recommendation"]["queue_scope"] == "player_shared"
    print("  PASS: economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items")


def test_economy_job_counts_preexisting_ready_building_toward_completion() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [{"name": "barr", "display_name": "兵营", "done": True, "paused": False}],
            "has_ready_item": True,
        }
    }
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="兵营", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    assert api.place_building_calls == [{"queue_type": "Building", "location": None}]
    assert api.produce_calls == []
    assert job.produced_count == 1
    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    print("  PASS: economy_job_counts_preexisting_ready_building_toward_completion")


def test_economy_job_waits_when_ready_building_cannot_be_placed() -> None:
    api = MockGameAPI()
    api.place_building_error = GameAPIError("COMMAND_EXECUTION_ERROR", "无法自动放置建筑: 兵营")
    world = MockWorldModel()
    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [{"name": "barr", "display_name": "兵营", "done": True, "paused": False}],
            "has_ready_item": True,
        }
    }
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="兵营", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    assert job.status == JobStatus.WAITING
    assert signals[-1].kind == SignalKind.BLOCKED
    assert signals[-1].data["reason"] == "ready_item_not_placeable"
    print("  PASS: economy_job_waits_when_ready_building_cannot_be_placed")


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

    job.tick()

    assert api.produce_calls == [
        {"unit_type": "PowerPlant", "quantity": 1, "auto_place_building": True}
    ]
    print("  PASS: economy_job_enables_auto_place_for_buildings")


def test_economy_job_counts_direct_auto_placed_buildings_without_queue_done_event() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}}
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="powr", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    assert api.produce_calls == [
        {"unit_type": "powr", "quantity": 1, "auto_place_building": True}
    ]
    assert job.status == JobStatus.RUNNING

    world.actors = [
        {
            "actor_id": 137,
            "name": "发电厂",
            "display_name": "发电厂",
            "category": "building",
        }
    ]
    job.tick()

    assert job.produced_count == 1
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert job.status == JobStatus.SUCCEEDED
    print("  PASS: economy_job_counts_direct_auto_placed_buildings_without_queue_done_event")


def test_economy_job_completes_before_low_power_after_building_lands() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}}
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="dome", count=1, queue_type="Building"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()
    assert job.status == JobStatus.RUNNING

    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": 10.0,
            "data": {"queue_type": "Building", "name": "dome", "display_name": "雷达站"},
        }
    ]
    world.actors = [
        {
            "actor_id": 235,
            "name": "雷达站",
            "display_name": "雷达站",
            "category": "building",
        }
    ]
    world.economy["low_power"] = True

    job.tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].data["knowledge"]["roles"] == ["awareness_gateway", "tech_gateway"]
    assert "atek" in signals[-1].data["knowledge"]["downstream_unlocks"]
    assert signals[-1].result == "succeeded"
    assert all(
        not (signal.kind == SignalKind.BLOCKED and signal.data and signal.data.get("reason") == "low_power")
        for signal in signals
    )
    print("  PASS: economy_job_completes_before_low_power_after_building_lands")


def test_economy_job_cannot_produce_signal_includes_prerequisite() -> None:
    """cannot_produce BLOCKED signal includes specific missing prerequisite building names."""
    api = MockGameAPI()
    api.can_produce_value = False
    world = MockWorldModel()
    world.queues["Infantry"] = {"queue_type": "Infantry", "items": [], "has_ready_item": False}
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(unit_type="e1", count=3, queue_type="Infantry"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.do_tick()
    assert job.status == JobStatus.WAITING
    blocked = [s for s in signals if s.kind == SignalKind.BLOCKED]
    assert blocked, "Expected a BLOCKED signal"
    sig = blocked[-1]
    assert sig.data["reason"] == "cannot_produce"
    # Signal summary should mention the missing prerequisite building (兵营)
    assert "兵营" in sig.summary, f"Expected '兵营' in summary: {sig.summary!r}"
    print("  PASS: economy_job_cannot_produce_signal_includes_prerequisite")


if __name__ == "__main__":
    print("Running EconomyExpert tests...\n")
    test_economy_expert_creates_queue_job()
    test_economy_job_emits_progress_and_finishes()
    test_economy_job_waits_on_low_power_and_recovers()
    test_economy_job_waits_when_queue_missing()
    test_economy_job_can_build_power_while_low_power()
    test_economy_job_matches_aliases_in_queue_and_completion_events()
    test_economy_job_auto_places_ready_buildings_and_blocks_foreign_ready_items()
    test_economy_job_counts_preexisting_ready_building_toward_completion()
    test_economy_job_waits_when_ready_building_cannot_be_placed()
    test_economy_job_enables_auto_place_for_buildings()
    test_economy_job_counts_direct_auto_placed_buildings_without_queue_done_event()
    test_economy_job_completes_before_low_power_after_building_lands()
    test_economy_job_abort_does_not_cancel_shared_queue()
    test_economy_job_cannot_produce_signal_includes_prerequisite()
    print("\nAll 14 EconomyExpert tests passed!")
