"""Inference and idle-matching helpers for unit requests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Optional

from models import UnitRequest


def hint_match_score(actor: Any, hint: str) -> int:
    """Score how well an actor matches a request hint."""
    if not hint:
        return 0
    normalized_hint = str(hint or "").strip().lower()
    name = str(getattr(actor, "name", "") or "").strip().lower()
    display = str(getattr(actor, "display_name", "") or "").strip().lower()
    haystacks = [value for value in (name, display) if value]
    if any(value and (value in normalized_hint or normalized_hint in value) for value in haystacks):
        return 2

    alias_groups = {
        "步兵": ("步枪兵", "步兵", "e1"),
        "火箭兵": ("火箭兵", "e3"),
        "重坦": ("重坦", "重型坦克", "3tnk"),
        "猛犸坦克": ("猛犸坦克", "4tnk"),
        "防空车": ("防空履带车", "防空车", "ftrk"),
        "火箭车": ("v2火箭车", "v2火箭发射车", "火箭车", "v2rl"),
        "矿车": ("矿车", "harv"),
    }
    for hint_key, aliases in alias_groups.items():
        if hint_key not in normalized_hint:
            continue
        if any(alias in value for alias in aliases for value in haystacks):
            return 2
    if "坦克" in normalized_hint and any(("坦克" in value or "重坦" in value or "tnk" in value) for value in haystacks):
        return 1
    return 0


def infer_unit_type(
    category: str,
    hint: str,
    *,
    hint_to_unit: Mapping[str, tuple[str, str]],
    category_defaults: Mapping[str, tuple[str, str]],
) -> tuple[Optional[str], Optional[str]]:
    """Infer concrete (unit_type, queue_type) from category + hint."""
    for keyword, (unit_type, queue_type) in hint_to_unit.items():
        if keyword in hint:
            return unit_type, queue_type
    default = category_defaults.get(category)
    if default:
        return default
    return None, None


def available_match_count(
    req: UnitRequest,
    idle_actors: Iterable[Any],
    *,
    category_to_actor_category: Mapping[str, str],
) -> int:
    """Count how many currently idle actors could satisfy a request by category."""
    actor_category = category_to_actor_category.get(req.category)
    matched = [
        actor
        for actor in idle_actors
        if actor_category is None or actor.category.value == actor_category
    ]
    return len(matched)


def sort_pending_requests(
    requests: Iterable[UnitRequest],
    idle_actors: list[Any],
    *,
    category_to_actor_category: Mapping[str, str],
    urgency_weight: Mapping[str, int],
    task_priority_for: Callable[[str], int],
    request_start_goal: Callable[[UnitRequest], int],
) -> list[UnitRequest]:
    """Sort pending requests by urgency, blocking-ness, start-package value, then task priority."""
    return sorted(
        requests,
        key=lambda req: (
            -urgency_weight.get(req.urgency, 1),
            -int(bool(req.blocking)),
            -int((req.fulfilled + available_match_count(
                req,
                idle_actors,
                category_to_actor_category=category_to_actor_category,
            )) >= request_start_goal(req)),
            -task_priority_for(req.task_id),
            req.created_at,
        ),
    )


def matching_idle_actors(
    req: UnitRequest,
    idle_actors: Iterable[Any],
    *,
    category_to_actor_category: Mapping[str, str],
) -> list[Any]:
    """Return idle actors matching the request category filter."""
    actor_category = category_to_actor_category.get(req.category)
    return [
        actor
        for actor in idle_actors
        if actor_category is None or actor.category.value == actor_category
    ]


def admissible_idle_actors(
    req: UnitRequest,
    idle_actors: Iterable[Any],
    *,
    category_to_actor_category: Mapping[str, str],
    hint_match_score_fn: Callable[[Any, str], int],
) -> list[Any]:
    """Return idle actors that pass both category and hint admission.

    When a request carries a concrete hint, matching is fail-closed: unrelated
    idle actors should not be consumed just because they share the same broad
    category (for example, `vehicle` should not absorb a V2 for a `重坦`
    request). The hint score is therefore an admission gate, not only a sort
    preference.
    """
    matched = matching_idle_actors(
        req,
        idle_actors,
        category_to_actor_category=category_to_actor_category,
    )
    if not req.hint:
        return matched
    admitted = [
        actor
        for actor in matched
        if hint_match_score_fn(actor, req.hint) > 0
    ]
    return admitted
