"""Helpers for normalizing production names across LLM/GameAPI layers."""

from __future__ import annotations

from typing import Iterable

from unit_registry import get_default_registry, normalize_registry_name


def normalize_production_name(name: str | None) -> str:
    return normalize_registry_name(name)


def production_name_variants(name: str | None) -> list[str]:
    """Return unique lookup variants in preferred order."""

    raw = (name or "").strip()
    if not raw:
        return []

    variants: list[str] = []
    for candidate in (raw, normalize_production_name(raw)):
        if candidate and candidate not in variants:
            variants.append(candidate)

    registry = get_default_registry()
    normalized_inputs = {normalize_production_name(candidate) for candidate in variants if candidate}
    for normalized in list(normalized_inputs):
        for entry in registry.find_matches(normalized):
            for alias in [entry.unit_id, entry.unit_id.lower(), entry.display_name, *entry.aliases]:
                if alias and alias not in variants:
                    variants.append(alias)
    return variants


def production_name_matches(expected: str | None, *observed: str | None) -> bool:
    """Return True when any observed name matches the expected alias."""

    expected_variants = set(production_name_variants(expected))
    if not expected_variants:
        return False
    for name in observed:
        if set(production_name_variants(name)) & expected_variants:
            return True
    return False


def first_matching_production_name(
    expected: str | None,
    candidates: Iterable[str | None],
) -> str | None:
    """Return the first candidate that matches the expected alias."""

    for candidate in candidates:
        if production_name_matches(expected, candidate):
            return candidate
    return None
