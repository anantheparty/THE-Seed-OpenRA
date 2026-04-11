"""RallyExpert unit tests."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.rally import RallyExpert
from models import RallyJobConfig, SignalKind
from openra_api.models import Actor, Location


class FakeGameAPI:
    def __init__(self) -> None:
        self.actors = {
            201: Actor(actor_id=201, type="barr"),
            202: Actor(actor_id=202, type="weap"),
            203: Actor(actor_id=203, type="e1"),
        }
        self.rally_calls: list[dict[str, object]] = []

    def get_actor_by_id(self, actor_id: int):
        return self.actors.get(actor_id)

    def set_rally_point(self, actors, target_location: Location) -> None:
        self.rally_calls.append({
            "actor_ids": [actor.actor_id for actor in actors],
            "target": (target_location.x, target_location.y),
        })


class FakeWorldModel:
    def query(self, query_type, params=None):
        return {}


def test_rally_job_sets_rally_for_eligible_production_buildings() -> None:
    api = FakeGameAPI()
    signals = []
    expert = RallyExpert(game_api=api, world_model=FakeWorldModel())
    job = expert.create_job(
        task_id="t_rally",
        config=RallyJobConfig(actor_ids=[201, 202, 203], target_position=(55, 88)),
        signal_callback=signals.append,
    )
    job.resources = ["actor:201", "actor:202", "actor:203"]

    job.tick()

    assert api.rally_calls == [{"actor_ids": [201, 202], "target": (55, 88)}]
    assert job.status.value == "succeeded"
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].data["actor_ids"] == [201, 202]
    assert signals[-1].data["ignored_actor_ids"] == [203]
    print("  PASS: rally_job_sets_rally_for_eligible_production_buildings")


def test_rally_job_fails_when_no_eligible_production_buildings() -> None:
    api = FakeGameAPI()
    signals = []
    expert = RallyExpert(game_api=api, world_model=FakeWorldModel())
    job = expert.create_job(
        task_id="t_rally",
        config=RallyJobConfig(actor_ids=[203], target_position=(11, 22)),
        signal_callback=signals.append,
    )
    job.resources = ["actor:203"]

    job.tick()

    assert api.rally_calls == []
    assert job.status.value == "failed"
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "failed"
    assert signals[-1].data["ignored_actor_ids"] == [203]
    print("  PASS: rally_job_fails_when_no_eligible_production_buildings")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
