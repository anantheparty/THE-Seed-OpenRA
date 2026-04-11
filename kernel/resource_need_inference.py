"""Kernel-side ResourceNeed normalization and fallback inference."""

from __future__ import annotations

from typing import Any, Protocol

from models import ExpertConfig, ResourceKind, ResourceNeed


class ControllerLike(Protocol):
    job_id: str
    expert_type: str


def infer_resource_needs(controller: ControllerLike, config: ExpertConfig) -> list[ResourceNeed]:
    if controller.expert_type == "ReconExpert":
        actor_ids = getattr(config, "actor_ids", None)
        if actor_ids:
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(actor_id), "owner": "self"},
                )
                for actor_id in actor_ids
            ]
        return [
            ResourceNeed(
                job_id=controller.job_id,
                kind=ResourceKind.ACTOR,
                count=1,
                predicates={"owner": "self"},
            )
        ]
    if controller.expert_type == "CombatExpert":
        actor_ids = getattr(config, "actor_ids", None)
        if actor_ids:
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(actor_id), "owner": "self"},
                )
                for actor_id in actor_ids
            ]
        return [
            ResourceNeed(
                job_id=controller.job_id,
                kind=ResourceKind.ACTOR,
                count=3,
                predicates={"can_attack": "true", "owner": "self"},
            )
        ]
    if controller.expert_type == "MovementExpert":
        actor_ids = getattr(config, "actor_ids", None)
        if actor_ids:
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(actor_id), "owner": "self"},
                )
                for actor_id in actor_ids
            ]
        unit_count = getattr(config, "unit_count", 0)
        return [
            ResourceNeed(
                job_id=controller.job_id,
                kind=ResourceKind.ACTOR,
                count=unit_count if unit_count > 0 else 999,
                predicates={"owner": "self"},
            )
        ]
    if controller.expert_type == "DeployExpert":
        return [
            ResourceNeed(
                job_id=controller.job_id,
                kind=ResourceKind.ACTOR,
                count=1,
                predicates={"actor_id": str(getattr(config, "actor_id")), "owner": "self"},
            )
        ]
    if controller.expert_type == "EconomyExpert":
        return [
            ResourceNeed(
                job_id=controller.job_id,
                kind=ResourceKind.PRODUCTION_QUEUE,
                count=1,
                predicates={"queue_type": str(getattr(config, "queue_type"))},
            )
        ]
    return []


def build_resource_needs(controller: ControllerLike, config: ExpertConfig) -> list[ResourceNeed]:
    if hasattr(controller, "get_resource_needs"):
        needs = list(controller.get_resource_needs())  # type: ignore[call-arg]
    elif hasattr(controller, "resource_needs"):
        needs = list(getattr(controller, "resource_needs"))
    else:
        needs = infer_resource_needs(controller, config)

    normalized: list[ResourceNeed] = []
    for need in needs:
        normalized.append(
            ResourceNeed(
                job_id=controller.job_id,
                kind=need.kind,
                count=need.count,
                predicates=dict(need.predicates),
                timestamp=need.timestamp,
            )
        )
    return normalized
