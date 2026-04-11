"""Small schema guards for mock fixtures used across tests."""

from __future__ import annotations


def assert_mapping_superset(mapping: dict, required_keys: set[str], *, label: str) -> None:
    missing = sorted(required_keys - set(mapping.keys()))
    assert not missing, f"{label} missing keys: {missing}"


def assert_object_surface(
    obj: object,
    *,
    required_callables: set[str],
    required_attrs: set[str] | None = None,
    label: str,
) -> None:
    missing_callables = sorted(
        name
        for name in required_callables
        if not callable(getattr(obj, name, None))
    )
    missing_attrs = sorted(
        name
        for name in (required_attrs or set())
        if not hasattr(obj, name)
    )
    assert not missing_callables and not missing_attrs, (
        f"{label} missing surface: callables={missing_callables}, attrs={missing_attrs}"
    )
