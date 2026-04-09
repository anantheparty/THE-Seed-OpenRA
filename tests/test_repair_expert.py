"""RepairExpert unit tests."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.repair import RepairExpert
from models import RepairJobConfig, SignalKind
from openra_api.models import Actor


class FakeGameAPI:
    def __init__(self) -> None:
        self.actors = {
            101: Actor(actor_id=101, type="3tnk", hppercent=75),
            102: Actor(actor_id=102, type="3tnk", hppercent=100),
            103: Actor(actor_id=103, type="harv", hppercent=40),
        }
        self.repair_calls: list[list[int]] = []

    def repair_units(self, actors):
        self.repair_calls.append([actor.actor_id for actor in actors])

    def get_actor_by_id(self, actor_id):
        return self.actors.get(actor_id)


class FakeWorldModel:
    def query(self, query_type, params=None):
        return []


def test_repair_job_repairs_only_damaged_units() -> None:
    game_api = FakeGameAPI()
    signals = []
    expert = RepairExpert(game_api=game_api, world_model=FakeWorldModel())
    job = expert.create_job(
        task_id="t1",
        config=RepairJobConfig(actor_ids=[101, 102, 103]),
        signal_callback=signals.append,
    )
    job.resources = ["actor:101", "actor:102", "actor:103"]

    job.tick()

    assert game_api.repair_calls == [[101, 103]]
    assert job.status.value == "succeeded"
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].data["actor_ids"] == [101, 103]
    print("  PASS: repair_job_repairs_only_damaged_units")


def test_repair_job_succeeds_without_call_when_all_healthy() -> None:
    game_api = FakeGameAPI()
    game_api.actors[101].hppercent = 100
    game_api.actors[103].hppercent = 100
    signals = []
    expert = RepairExpert(game_api=game_api, world_model=FakeWorldModel())
    job = expert.create_job(
        task_id="t1",
        config=RepairJobConfig(actor_ids=[101, 102]),
        signal_callback=signals.append,
    )
    job.resources = ["actor:101", "actor:102"]

    job.tick()

    assert game_api.repair_calls == []
    assert job.status.value == "succeeded"
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].data["damaged_count"] == 0
    print("  PASS: repair_job_succeeds_without_call_when_all_healthy")


if __name__ == "__main__":
    print("Running RepairExpert tests...\n")
    test_repair_job_repairs_only_damaged_units()
    test_repair_job_succeeds_without_call_when_all_healthy()
    print("\nAll 2 tests passed!")
