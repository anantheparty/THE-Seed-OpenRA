"""Focused tests for extracted resource-assignment helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.resource_assignment import (
    actor_matches_need,
    find_unbound_resource,
    notify_resource_loss,
    rebalance_resources,
    resource_matches_need,
    resources_for_need,
)
from models import JobStatus, ResourceKind, ResourceNeed, Task, TaskKind, TaskStatus


class _Controller:
    def __init__(
        self,
        job_id: str,
        resources: list[str],
        *,
        task_id: str = "task_1",
        status: JobStatus = JobStatus.RUNNING,
        expert_type: str = "CombatExpert",
    ) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self.status = status
        self.resources = resources
        self.expert_type = expert_type
        self.signals = []

    def emit_signal(self, **payload) -> None:
        self.signals.append(payload)

    def on_resource_granted(self, resources: list[str]) -> None:
        self.resources.extend(resources)
        if self.status == JobStatus.WAITING:
            self.status = JobStatus.RUNNING

    def abort(self) -> None:
        self.status = JobStatus.ABORTED


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
        activity="Idle",
        can_attack=can_attack,
        can_harvest=can_harvest,
        name=name,
    )


class _World:
    def __init__(self, actors: list[SimpleNamespace]) -> None:
        self.state = SimpleNamespace(actors={actor.actor_id: actor for actor in actors})
        self.resource_bindings: dict[str, str] = {}

    def find_actors(
        self,
        *,
        owner: str | None = None,
        idle_only: bool = False,
        unbound_only: bool = False,
        category: str | None = None,
    ) -> list[SimpleNamespace]:
        results: list[SimpleNamespace] = []
        for actor in self.state.actors.values():
            if owner is not None and getattr(actor, "owner", None) != owner:
                continue
            if category is not None and getattr(actor, "category", None) != category:
                continue
            if idle_only and getattr(actor, "activity", "") != "Idle":
                continue
            if unbound_only and f"actor:{actor.actor_id}" in self.resource_bindings:
                continue
            results.append(actor)
        return results

    def bind_resource(self, resource_id: str, job_id: str) -> None:
        self.resource_bindings[resource_id] = job_id

    def unbind_resource(self, resource_id: str) -> None:
        self.resource_bindings.pop(resource_id, None)

    def query(self, query_type: str):
        if query_type == "production_queues":
            return {}
        raise AssertionError(f"unexpected query: {query_type}")


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


def test_actor_ids_any_matches_allowed_subset() -> None:
    vehicle = _actor(10, category="vehicle", mobility="fast", owner="self", name="吉普")
    outsider = _actor(11, category="vehicle", mobility="fast", owner="self", name="吉普")
    actors_by_id = {10: vehicle, 11: outsider}
    need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=2,
        predicates={"owner": "self", "actor_ids_any": "10,12,13"},
    )
    controller = _Controller("job_1", ["actor:10", "actor:11"])

    assert actor_matches_need(vehicle, need) is True
    assert actor_matches_need(outsider, need) is False
    assert resource_matches_need("actor:10", need, actors_by_id=actors_by_id) is True
    assert resource_matches_need("actor:11", need, actors_by_id=actors_by_id) is False
    assert resources_for_need(controller, need, actors_by_id=actors_by_id) == ["actor:10"]


def test_notify_resource_loss_deduplicates_signals() -> None:
    controller = _Controller("job_1", [])
    need = ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=2, predicates={"owner": "self"})
    notified = set()

    notify_resource_loss(controller, need, 2, resource_loss_notified=notified)
    notify_resource_loss(controller, need, 2, resource_loss_notified=notified)

    assert len(controller.signals) == 1
    assert controller.signals[0]["summary"] == "Missing 2 actor resource(s); waiting for replacement"
    assert "job_1" in notified


def test_notify_resource_loss_enriches_explicit_movement_group_truth() -> None:
    controller = _Controller("job_1", ["actor:101", "actor:102"], expert_type="MovementExpert")
    controller.config = SimpleNamespace(actor_ids=[101, 102, 103, 104, 105])
    need = ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=3, predicates={"owner": "self", "actor_ids_any": "101,102,103,104,105"})
    notified = set()

    notify_resource_loss(controller, need, 3, resource_loss_notified=notified)

    assert len(controller.signals) == 1
    assert "group=2/5" in controller.signals[0]["summary"]
    assert controller.signals[0]["data"]["source_expert"] == "MovementExpert"
    assert controller.signals[0]["data"]["explicit_group"] is True
    assert controller.signals[0]["data"]["requested_total"] == 5
    assert controller.signals[0]["data"]["bound_count"] == 2
    assert controller.signals[0]["data"]["missing_count"] == 3
    print("  PASS: notify_resource_loss_enriches_explicit_movement_group_truth")


def test_find_unbound_resource_keeps_generic_needs_idle_only() -> None:
    actors = [_actor(57, category="vehicle", mobility="fast", can_attack=True)]
    actors[0].activity = "Moving"
    world = _World(actors)
    need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=1,
        predicates={"owner": "self", "can_attack": "true"},
    )

    assert find_unbound_resource(need, world_model=world) is None


def test_find_unbound_resource_allows_busy_unbound_explicit_actor_selection_for_movement() -> None:
    actors = [_actor(57, category="vehicle", mobility="fast", can_attack=True)]
    actors[0].activity = "Moving"
    world = _World(actors)
    need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=1,
        predicates={"owner": "self", "actor_id": "57"},
    )

    assert find_unbound_resource(need, world_model=world, allow_busy_explicit=True) == "actor:57"


def test_find_unbound_resource_skips_foreign_task_owned_actor_but_allows_same_task() -> None:
    actors = [_actor(57, category="vehicle", mobility="fast", can_attack=True)]
    world = _World(actors)
    need = ResourceNeed(
        job_id="job_1",
        kind=ResourceKind.ACTOR,
        count=1,
        predicates={"owner": "self", "can_attack": "true"},
    )
    owner_lookup = lambda actor_id: "task_a" if actor_id == 57 else None

    assert (
        find_unbound_resource(
            need,
            world_model=world,
            controller_task_id="task_b",
            task_owner_for_actor=owner_lookup,
        )
        is None
    )
    assert (
        find_unbound_resource(
            need,
            world_model=world,
            controller_task_id="task_a",
            task_owner_for_actor=owner_lookup,
        )
        == "actor:57"
    )


def test_rebalance_keeps_explicit_group_waiting_until_start_package_ready() -> None:
    actors = [
        _actor(57, category="vehicle", mobility="fast", can_attack=True),
        _actor(58, category="vehicle", mobility="fast", can_attack=True),
        _actor(59, category="vehicle", mobility="fast", can_attack=True),
    ]
    actors[1].activity = "Moving"
    actors[2].activity = "Moving"
    world = _World(actors)
    controller = _Controller("job_1", [], status=JobStatus.RUNNING)
    task = Task(
        task_id="task_1",
        raw_text="operator attack",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        created_at=1.0,
        label="001",
    )
    needs = [
        ResourceNeed(
            job_id="job_1",
            kind=ResourceKind.ACTOR,
            count=2,
            predicates={"owner": "self", "can_attack": "true", "actor_ids_any": "57,58,59"},
        ),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "57"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "58"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "59"}),
    ]

    rebalance_resources(
        jobs={"job_1": controller},
        tasks={"task_1": task},
        resource_needs={"job_1": needs},
        world_model=world,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        release_job_resources=lambda _controller: None,
        set_task_actor_group=lambda _task_id, _actor_ids: None,
        resource_loss_notified=set(),
        sync_world_runtime=lambda: None,
    )

    assert controller.resources == ["actor:57"]
    assert controller.status == JobStatus.WAITING


def test_rebalance_rebinds_busy_unbound_explicit_group_after_preemption() -> None:
    actors = [
        _actor(57, category="vehicle", mobility="fast", can_attack=True),
        _actor(58, category="vehicle", mobility="fast", can_attack=True),
        _actor(59, category="vehicle", mobility="fast", can_attack=True),
    ]
    for actor in actors:
        actor.activity = "Moving"
    world = _World(actors)
    controller = _Controller("job_1", [], status=JobStatus.WAITING, expert_type="MovementExpert")
    task = Task(
        task_id="task_1",
        raw_text="operator retreat",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        created_at=1.0,
        label="001",
    )
    needs = [
        ResourceNeed(
            job_id="job_1",
            kind=ResourceKind.ACTOR,
            count=2,
            predicates={"owner": "self", "actor_ids_any": "57,58,59"},
        ),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "57"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "58"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "59"}),
    ]

    rebalance_resources(
        jobs={"job_1": controller},
        tasks={"task_1": task},
        resource_needs={"job_1": needs},
        world_model=world,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        release_job_resources=lambda _controller: None,
        set_task_actor_group=lambda _task_id, _actor_ids: None,
        resource_loss_notified=set(),
        sync_world_runtime=lambda: None,
    )

    assert controller.resources == ["actor:57", "actor:58", "actor:59"]
    assert controller.status == JobStatus.RUNNING


def test_rebalance_allows_partial_explicit_group_to_run_once_min_ready_count_met() -> None:
    actors = [
        _actor(57, category="vehicle", mobility="fast", can_attack=True),
        _actor(58, category="vehicle", mobility="fast", can_attack=True),
        _actor(59, category="vehicle", mobility="fast", can_attack=True),
    ]
    actors[2].activity = "Moving"
    world = _World(actors)
    controller = _Controller("job_1", [], status=JobStatus.RUNNING)
    task = Task(
        task_id="task_1",
        raw_text="operator attack",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        created_at=1.0,
        label="001",
    )
    needs = [
        ResourceNeed(
            job_id="job_1",
            kind=ResourceKind.ACTOR,
            count=2,
            predicates={"owner": "self", "can_attack": "true", "actor_ids_any": "57,58,59"},
        ),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "57"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "58"}),
        ResourceNeed(job_id="job_1", kind=ResourceKind.ACTOR, count=1, predicates={"owner": "self", "actor_id": "59"}),
    ]

    rebalance_resources(
        jobs={"job_1": controller},
        tasks={"task_1": task},
        resource_needs={"job_1": needs},
        world_model=world,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        release_job_resources=lambda _controller: None,
        set_task_actor_group=lambda _task_id, _actor_ids: None,
        resource_loss_notified=set(),
        sync_world_runtime=lambda: None,
    )

    assert controller.resources == ["actor:57", "actor:58"]
    assert controller.status == JobStatus.RUNNING

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
