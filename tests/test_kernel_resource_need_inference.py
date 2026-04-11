"""Tests for kernel resource-need inference helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.resource_need_inference import build_resource_needs, infer_resource_needs
from models import CombatJobConfig, EngagementMode, MovementJobConfig, ResourceKind, ResourceNeed


def test_build_resource_needs_prefers_get_resource_needs_and_normalizes() -> None:
    predicates = {"owner": "self"}
    raw_need = ResourceNeed(
        job_id="other_job",
        kind=ResourceKind.ACTOR,
        count=2,
        predicates=predicates,
        timestamp=123.0,
    )
    controller = SimpleNamespace(
        job_id="j_1",
        expert_type="ReconExpert",
        get_resource_needs=lambda: [raw_need],
    )

    needs = build_resource_needs(controller, SimpleNamespace())

    assert len(needs) == 1
    assert needs[0].job_id == "j_1"
    assert needs[0].count == 2
    assert needs[0].timestamp == 123.0
    assert needs[0].predicates == {"owner": "self"}
    assert needs[0].predicates is not predicates
    print("  PASS: build_resource_needs_prefers_get_resource_needs_and_normalizes")


def test_build_resource_needs_prefers_resource_needs_attribute() -> None:
    controller = SimpleNamespace(
        job_id="j_1",
        expert_type="CombatExpert",
        resource_needs=[
            ResourceNeed(
                job_id="x",
                kind=ResourceKind.PRODUCTION_QUEUE,
                count=1,
                predicates={"queue_type": "Vehicle"},
            )
        ],
    )

    needs = build_resource_needs(controller, SimpleNamespace())

    assert len(needs) == 1
    assert needs[0].job_id == "j_1"
    assert needs[0].kind == ResourceKind.PRODUCTION_QUEUE
    print("  PASS: build_resource_needs_prefers_resource_needs_attribute")


def test_infer_resource_needs_covers_recon_combat_movement() -> None:
    recon_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_r", expert_type="ReconExpert"),
        SimpleNamespace(actor_ids=[57, 58]),
    )
    combat_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_c", expert_type="CombatExpert"),
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.ASSAULT),
    )
    move_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_m", expert_type="MovementExpert"),
        MovementJobConfig(target_position=(10, 10), unit_count=4),
    )

    assert [need.predicates for need in recon_needs] == [
        {"actor_id": "57", "owner": "self"},
        {"actor_id": "58", "owner": "self"},
    ]
    assert combat_needs[0].count == 3
    assert combat_needs[0].predicates == {"can_attack": "true", "owner": "self"}
    assert move_needs[0].count == 4
    assert move_needs[0].predicates == {"owner": "self"}
    print("  PASS: infer_resource_needs_covers_recon_combat_movement")


def test_infer_resource_needs_covers_deploy_economy_and_unknown() -> None:
    deploy_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_d", expert_type="DeployExpert"),
        SimpleNamespace(actor_id=99),
    )
    economy_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_e", expert_type="EconomyExpert"),
        SimpleNamespace(queue_type="Building"),
    )
    unknown_needs = infer_resource_needs(
        SimpleNamespace(job_id="j_x", expert_type="UnknownExpert"),
        SimpleNamespace(),
    )

    assert deploy_needs[0].predicates == {"actor_id": "99", "owner": "self"}
    assert economy_needs[0].kind == ResourceKind.PRODUCTION_QUEUE
    assert economy_needs[0].predicates == {"queue_type": "Building"}
    assert unknown_needs == []
    print("  PASS: infer_resource_needs_covers_deploy_economy_and_unknown")
