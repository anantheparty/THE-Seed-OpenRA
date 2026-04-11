"""Focused tests for StopExpert / StopJob."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.stop import StopExpert
from models import JobStatus, SignalKind, StopJobConfig


class MockGameAPI:
    def __init__(self) -> None:
        self.stop_calls: list[list[int]] = []

    def stop(self, actors) -> None:
        self.stop_calls.append([actor.actor_id for actor in actors])


class MockWorldModel:
    def query(self, query_type: str, params=None):
        return {}


def test_stop_expert_stops_owned_actors_and_completes():
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    expert = StopExpert(game_api=api, world_model=world)
    job = expert.create_job(
        task_id="t_stop",
        config=StopJobConfig(actor_ids=[101, 102]),
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:101", "actor:102"])

    job.do_tick()

    assert api.stop_calls == [[101, 102]]
    assert job.status == JobStatus.SUCCEEDED
    assert len(signals) == 1
    assert signals[0].kind == SignalKind.TASK_COMPLETE
    assert signals[0].result == "succeeded"
    assert signals[0].data == {"actor_ids": [101, 102]}
    print("  PASS: stop_expert_stops_owned_actors_and_completes")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
