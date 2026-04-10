"""Tests for EconomyExpert and EconomyJob."""

from __future__ import annotations

import os
import sys
import time

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
        self.produce_return_value: int | None = 1
        self.place_building_error: GameAPIError | None = None

    def can_produce(self, unit_type: str) -> bool:
        return self.can_produce_value

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> int | None:
        self.produce_calls.append(
            {
                "unit_type": unit_type,
                "quantity": quantity,
                "auto_place_building": auto_place_building,
            }
        )
        return self.produce_return_value if self.produce_return_value is not None else None

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

    now = time.time()
    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": now + 100,
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
            "timestamp": now + 200,
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


def test_economy_job_blocks_when_produce_command_fails() -> None:
    api = MockGameAPI()
    api.produce_return_value = None
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(count=1),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    assert len(api.produce_calls) == 1
    assert job.issued_count == 0
    assert job.status == JobStatus.WAITING
    assert signals[-1].kind == SignalKind.BLOCKED
    assert signals[-1].data["reason"] == "produce_command_failed"
    assert signals[-1].data["impact"]["kind"] == "command_failure"
    print("  PASS: economy_job_blocks_when_produce_command_fails")


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
    assert [item["unit_type"] for item in signals[-1].data["recommendation"]["options"]] == ["powr"]
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


def test_economy_job_abort_best_effort_clears_matching_shared_queue_items() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {
        "Building": {
            "queue_type": "Building",
            "items": [
                {"name": "barr", "display_name": "兵营", "done": True, "paused": False, "owner_actor_id": 11},
                {"name": "barr", "display_name": "兵营", "done": False, "paused": False, "owner_actor_id": 11},
                {"name": "proc", "display_name": "矿场", "done": False, "paused": False, "owner_actor_id": 11},
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
    job.issued_count = 1

    job.abort()

    assert api.manage_production_calls == [
        {
            "queue_type": "Building",
            "action": "cancel",
            "owner_actor_id": 11,
            "item_name": "barr",
            "count": 1,
        }
    ]
    assert job.status == JobStatus.ABORTED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "aborted"
    print("  PASS: economy_job_abort_best_effort_clears_matching_shared_queue_items")


def test_economy_job_signals_include_request_and_reservation_ids() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = EconomyJob(
        job_id="j1",
        task_id="t1",
        config=make_config(
            unit_type="2tnk",
            count=1,
            queue_type="Vehicle",
            request_id="req_1",
            reservation_id="res_1",
        ),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )

    job.tick()

    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": time.time() + 100,
            "data": {"queue_type": "Vehicle", "name": "2tnk", "display_name": "重坦"},
        }
    ]
    world.queues["Vehicle"]["items"] = []
    job.tick()

    progress = signals[-2]
    complete = signals[-1]
    assert progress.data["request_id"] == "req_1"
    assert progress.data["reservation_id"] == "res_1"
    assert complete.data["request_id"] == "req_1"
    assert complete.data["reservation_id"] == "res_1"
    print("  PASS: economy_job_signals_include_request_and_reservation_ids")


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
            "timestamp": time.time() + 100,
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
            "timestamp": time.time() + 100,
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
    assert signals[-1].data["knowledge"]["downstream_unlocks"] == ["afld", "stek"]
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


def test_economy_job_faction_restricted_fails_immediately() -> None:
    """Faction-restricted units from the wrong faction fail immediately.

    Player is Soviet (hardcoded). Allied-only unit '2tnk' should fail immediately.
    Soviet-only unit 'e2' should NOT fail (it waits for prerequisites instead).
    """
    api = MockGameAPI()
    api.can_produce_value = False
    world = MockWorldModel()

    # Allied unit on Soviet player → immediate FAILED
    world.queues["Vehicle"] = {"queue_type": "Vehicle", "items": [], "has_ready_item": False}
    signals_allied: list = []
    job_allied = EconomyJob(
        job_id="j_allied",
        task_id="t1",
        config=make_config(unit_type="2tnk", count=1, queue_type="Vehicle"),
        signal_callback=signals_allied.append,
        game_api=api,
        world_model=world,
    )
    job_allied.do_tick()
    assert job_allied.status == JobStatus.FAILED, f"Expected FAILED for Allied unit, got {job_allied.status}"
    blocked = [s for s in signals_allied if s.kind == SignalKind.BLOCKED]
    assert blocked, "Expected a BLOCKED signal before failure"
    assert "盟军专属" in blocked[-1].summary, f"Expected faction info: {blocked[-1].summary!r}"

    # Soviet unit on Soviet player → should NOT fail immediately (waits for prereqs)
    world.queues["Infantry"] = {"queue_type": "Infantry", "items": [], "has_ready_item": False}
    signals_soviet: list = []
    job_soviet = EconomyJob(
        job_id="j_soviet",
        task_id="t2",
        config=make_config(unit_type="e2", count=1, queue_type="Infantry"),
        signal_callback=signals_soviet.append,
        game_api=api,
        world_model=world,
    )
    job_soviet.do_tick()
    assert job_soviet.status == JobStatus.WAITING, f"Soviet unit should WAIT for prereqs, got {job_soviet.status}"
    print("  PASS: economy_job_faction_restricted_fails_immediately")


def test_economy_job_second_identical_building_does_not_see_first_completion() -> None:
    """Job 2 for the same building type must NOT count Job 1's completion events."""
    from unittest.mock import patch

    api = MockGameAPI()
    world = MockWorldModel()
    world.queues = {"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}}

    # Control timestamps: Job 1 inits at T=1000, event at T=1500, Job 2 inits at T=2000
    # Job 1 sees event (1500 > 1000). Job 2 must NOT see it (1500 < 2000).
    init_times = iter([1000.0, 2000.0])

    signals1: list = []
    with patch("experts.economy.time") as mock_time:
        mock_time.time.side_effect = lambda: next(init_times)
        job1 = EconomyJob(
            job_id="j1",
            task_id="t1",
            config=make_config(unit_type="powr", count=1, queue_type="Building"),
            signal_callback=signals1.append,
            game_api=api,
            world_model=world,
        )

    # Job 1 issues produce and completes
    job1.tick()
    assert len(api.produce_calls) == 1

    world.events = [
        {
            "type": "PRODUCTION_COMPLETE",
            "timestamp": 1500.0,
            "data": {"queue_type": "Building", "name": "powr", "display_name": "发电厂"},
        }
    ]
    world.actors = [{"actor_id": 50, "name": "发电厂", "display_name": "发电厂", "category": "building"}]
    job1.tick()
    assert job1.status == JobStatus.SUCCEEDED

    # Now create Job 2 for the same building type — Job 1's completion event is still in history
    signals2: list = []
    with patch("experts.economy.time") as mock_time:
        mock_time.time.side_effect = lambda: next(iter([2000.0]))
        job2 = EconomyJob(
            job_id="j2",
            task_id="t1",
            config=make_config(unit_type="powr", count=1, queue_type="Building"),
            signal_callback=signals2.append,
            game_api=api,
            world_model=world,
        )

    job2.tick()

    # Job 2 must NOT immediately succeed — it should issue its own produce call
    assert job2.status != JobStatus.SUCCEEDED, (
        f"Job 2 should not immediately succeed from Job 1's completion event; "
        f"produced_count={job2.produced_count}"
    )
    assert job2.status == JobStatus.RUNNING
    assert len(api.produce_calls) == 2  # Job 2 issued its own produce
    print("  PASS: economy_job_second_identical_building_does_not_see_first_completion")


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
    test_economy_job_abort_best_effort_clears_matching_shared_queue_items()
    test_economy_job_signals_include_request_and_reservation_ids()
    test_economy_job_cannot_produce_signal_includes_prerequisite()
    test_economy_job_faction_restricted_fails_immediately()
    test_economy_job_second_identical_building_does_not_see_first_completion()
    print("\nAll 18 EconomyExpert tests passed!")
