"""Helpers for normalizing production names across LLM/GameAPI layers."""

from __future__ import annotations

from typing import Iterable

from unit_registry import UnitEntry, get_default_registry, normalize_registry_name


def normalize_production_name(name: str | None) -> str:
    return normalize_registry_name(name)


def production_name_entry(name: str | None) -> UnitEntry | None:
    """Resolve a unit/building name through the shared registry-backed alias table."""

    raw = (name or "").strip()
    if not raw:
        return None

    entries = production_name_entries(raw)
    return entries[0] if entries else None


def production_name_entries(name: str | None) -> list[UnitEntry]:
    """Return all registry matches for a production name in lookup order."""

    raw = (name or "").strip()
    if not raw:
        return []

    registry = get_default_registry()
    matches: list[UnitEntry] = []
    seen: set[str] = set()
    for candidate in (raw, normalize_production_name(raw)):
        if not candidate:
            continue
        for entry in registry.find_matches(candidate):
            if entry.unit_id in seen:
                continue
            seen.add(entry.unit_id)
            matches.append(entry)
    return matches


def production_name_unit_id(name: str | None) -> str | None:
    entry = production_name_entry(name)
    if entry is None:
        return None
    return entry.unit_id.lower()


def production_name_category(name: str | None) -> str | None:
    entry = production_name_entry(name)
    if entry is None:
        return None
    return entry.category.lower()


def production_name_variants(name: str | None) -> list[str]:
    """Return unique lookup variants in preferred order."""

    raw = (name or "").strip()
    if not raw:
        return []

    variants: list[str] = []
    for candidate in (raw, normalize_production_name(raw)):
        if candidate and candidate not in variants:
            variants.append(candidate)

    expanded_entries: list[UnitEntry] = []
    seen_entry_ids: set[str] = set()
    for entry in production_name_entries(raw):
        if entry.unit_id not in seen_entry_ids:
            seen_entry_ids.add(entry.unit_id)
            expanded_entries.append(entry)
        # Expand through human-facing aliases so canonical ids like "barr"
        # also pick up sibling variants such as TENT that share the same
        # common display name "兵营".
        for alias in [entry.display_name, *entry.aliases]:
            for sibling in production_name_entries(alias):
                if sibling.unit_id in seen_entry_ids:
                    continue
                seen_entry_ids.add(sibling.unit_id)
                expanded_entries.append(sibling)

    for entry in expanded_entries:
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
