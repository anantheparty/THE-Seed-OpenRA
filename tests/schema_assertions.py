"""Small schema guards for mock fixtures used across tests."""

from __future__ import annotations


def assert_mapping_superset(mapping: dict, required_keys: set[str], *, label: str) -> None:
    missing = sorted(required_keys - set(mapping.keys()))
    assert not missing, f"{label} missing keys: {missing}"
