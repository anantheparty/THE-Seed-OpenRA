"""Focused tests for extracted resource-assignment helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.resource_assignment import (
    actor_matches_need,
    notify_resource_loss,
    resource_matches_need,
    resources_for_need,
)
from models import ResourceKind, ResourceNeed


class _Controller:
    def __init__(self, job_id: str, resources: list[str]) -> None:
        self.job_id = job_id
        self.resources = resources
        self.signals = []

    def emit_signal(self, **payload) -> None:
        self.signals.append(payload)


def _actor(
    actor_id: int,
    *,
    category: str,
    mobility: str,
    owner: str = "self",
    can_attack: bool = False,
    can_harvest: bool = False,
    name: str = "unit",
):
    return SimpleNamespace(
        actor_id=actor_id,
        category=category,
        mobility=mobility,
        owner=owner,
        can_attack=can_attack,
        can_harvest=can_harvest,
        name=name,
    )


def test_actor_matches_need_respects_static_building_guard() -> None:
    building = _actor(20, category="building", mobility="static", name="矿场")
    soft_need = ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self"})
    explicit_need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=1,
        predicates={"category": "building", "owner": "self"},
    )

    assert actor_matches_need(building, soft_need) is False
    assert actor_matches_need(building, explicit_need) is True


def test_resource_matches_need_and_resources_for_need_cover_actor_and_queue() -> None:
    vehicle = _actor(10, category="vehicle", mobility="fast", owner="self", name="吉普")
    actors_by_id = {10: vehicle}
    actor_need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=1,
        predicates={"mobility": "fast", "owner": "self"},
    )
    queue_need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.PRODUCTION_QUEUE,
        count=1,
        predicates={"queue_type": "Vehicle"},
    )
    controller = _Controller("job_1", ["actor:10", "queue:Vehicle", "queue:Infantry"])

    assert resource_matches_need("actor:10", actor_need, actors_by_id=actors_by_id) is True
    assert resource_matches_need("queue:Vehicle", queue_need, actors_by_id=actors_by_id) is True
    assert resource_matches_need("queue:Infantry", queue_need, actors_by_id=actors_by_id) is False
    assert resources_for_need(controller, actor_need, actors_by_id=actors_by_id) == ["actor:10"]
    assert resources_for_need(controller, queue_need, actors_by_id=actors_by_id) == ["queue:Vehicle"]


def test_notify_resource_loss_deduplicates_signals() -> None:
    controller = _Controller("job_1", [])
    need = ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=2, predicates={"owner": "self"})
    notified = set()

    notify_resource_loss(controller, need, 2, resource_loss_notified=notified)
    notify_resource_loss(controller, need, 2, resource_loss_notified=notified)

    assert len(controller.signals) == 1
    assert controller.signals[0]["summary"] == "Missing 2 actor resource(s); waiting for replacement"
    assert "job_1" in notified
