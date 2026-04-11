"""OccupyExpert unit tests."""

from __future__ import annotations

import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.occupy import OccupyExpert
from models import JobStatus, OccupyJobConfig, SignalKind


class FakeGameAPI:
    def __init__(self) -> None:
        self.occupy_calls: list[dict[str, list[int]]] = []

    def occupy_units(self, occupiers, targets) -> None:
        self.occupy_calls.append({
            "occupiers": [actor.actor_id for actor in occupiers],
            "targets": [actor.actor_id for actor in targets],
        })


class FakeWorldModel:
    def __init__(self) -> None:
        self.owner = "enemy"

    def query(self, query_type, params=None):
        if query_type == "actor_by_id":
            return {"actor": {"actor_id": params["actor_id"], "owner": self.owner}}
        return {}


def test_occupy_job_captures_target_after_owner_switch() -> None:
    api = FakeGameAPI()
    world = FakeWorldModel()
    signals = []
    expert = OccupyExpert(game_api=api, world_model=world)
    job = expert.create_job(
        task_id="t1",
        config=OccupyJobConfig(actor_ids=[701], target_actor_id=9001),
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:701"])

    job.do_tick()
    assert api.occupy_calls == [{"occupiers": [701], "targets": [9001]}]
    assert job.status == JobStatus.RUNNING

    world.owner = "self"
    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "succeeded"
    assert signals[-1].data["target_actor_id"] == 9001
    print("  PASS: occupy_job_captures_target_after_owner_switch")


def test_occupy_job_times_out_when_target_not_captured() -> None:
    api = FakeGameAPI()
    world = FakeWorldModel()
    signals = []
    expert = OccupyExpert(game_api=api, world_model=world)
    job = expert.create_job(
        task_id="t1",
        config=OccupyJobConfig(actor_ids=[701], target_actor_id=9001),
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:701"])

    with patch("experts.occupy.time.time", return_value=10.0):
        job.do_tick()
    job._issued_at = 10.0
    with patch("experts.occupy.time.time", return_value=16.0):
        job.do_tick()

    assert job.status == JobStatus.FAILED
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "failed"
    assert signals[-1].data["reason"] == "occupy_command_sent_but_target_not_captured"
    print("  PASS: occupy_job_times_out_when_target_not_captured")


def test_occupy_job_keeps_verifying_after_resource_revoked() -> None:
    api = FakeGameAPI()
    world = FakeWorldModel()
    signals = []
    expert = OccupyExpert(game_api=api, world_model=world)
    job = expert.create_job(
        task_id="t1",
        config=OccupyJobConfig(actor_ids=[701], target_actor_id=9001),
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:701"])

    job.do_tick()
    job.on_resource_revoked(["actor:701"])
    assert job.status == JobStatus.RUNNING

    world.owner = "self"
    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].result == "succeeded"
    print("  PASS: occupy_job_keeps_verifying_after_resource_revoked")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
