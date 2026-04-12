"""Helpers for persisted session browsing and task replay payload assembly."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from logging_system import (
    current_session_dir,
    latest_session_dir,
    list_persistence_sessions,
    list_session_tasks,
    read_task_replay_records,
)


def default_session_dir(log_session_root: str) -> Optional[Path]:
    """Return the current session, or the latest persisted one as fallback."""
    root = Path(log_session_root).resolve()
    current = current_session_dir()
    if current is not None:
        try:
            current.relative_to(root)
        except ValueError:
            current = None
    return current or latest_session_dir(root)


def resolve_session_dir(log_session_root: str, session_dir: Optional[str]) -> Optional[Path]:
    """Resolve a browser session path relative to the log session root."""
    if not session_dir:
        return None
    candidate = Path(session_dir)
    if not candidate.is_absolute():
        candidate = (Path(log_session_root) / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate if candidate.exists() else None


def _normalize_live_world_health(current_world_health: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(current_world_health, dict):
        return {}
    stale = bool(current_world_health.get("stale"))
    consecutive_failures = int(current_world_health.get("consecutive_failures", 0) or 0)
    total_failures = int(current_world_health.get("total_failures", 0) or 0)
    failure_threshold = int(current_world_health.get("failure_threshold", 0) or 0)
    last_error = str(current_world_health.get("last_error") or "")
    if not any([stale, consecutive_failures, total_failures, failure_threshold, last_error]):
        return {}
    return {
        "stale_seen": stale or total_failures > 0 or consecutive_failures > 0 or bool(last_error),
        "ended_stale": stale,
        "failure_threshold": failure_threshold,
        "last_error": last_error,
    }


def build_session_catalog_payload(
    log_session_root: str,
    *,
    selected_session_dir: Optional[Path],
    current_world_health: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the session catalog payload for the diagnostics UI."""
    sessions = list_persistence_sessions(log_session_root, limit=30)
    live_world_health = _normalize_live_world_health(current_world_health)
    if live_world_health:
        for item in sessions:
            if not item.get("is_current"):
                continue
            merged_world_health = dict(item.get("world_health") or {})
            merged_world_health.update(live_world_health)
            item["world_health"] = merged_world_health
            break
    return {
        "sessions": sessions,
        "selected_session_dir": str(selected_session_dir) if selected_session_dir is not None else None,
    }


def build_session_task_catalog_payload(
    log_session_root: str,
    *,
    session_dir: Optional[Path],
) -> dict[str, Any]:
    """Assemble the persisted task catalog payload for one session."""
    resolved = session_dir or default_session_dir(log_session_root)
    return {
        "session_dir": str(resolved) if resolved is not None else None,
        "tasks": list_session_tasks(resolved, limit=300) if resolved is not None else [],
    }


def build_task_replay_payload(
    task_id: str,
    *,
    requested_session_dir: Optional[str],
    log_session_root: str,
    raw_entry_limit: int,
    include_entries: bool = True,
    bundle_builder: Callable[[list[dict[str, Any]], Optional[Path]], dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a task replay response payload from persisted logs."""
    resolved_session_dir = resolve_session_dir(log_session_root, requested_session_dir) or default_session_dir(
        log_session_root
    )
    entries = read_task_replay_records(
        task_id,
        session_dir=resolved_session_dir,
        latest_base_dir=log_session_root,
    )
    raw_entries = entries[-raw_entry_limit:]
    included_entries = raw_entries if include_entries else []
    log_path = str(resolved_session_dir / "tasks" / f"{task_id}.jsonl") if resolved_session_dir else None
    return {
        "task_id": task_id,
        "session_dir": str(resolved_session_dir) if resolved_session_dir else None,
        "log_path": log_path,
        "entry_count": len(entries),
        "raw_entry_count": len(raw_entries),
        "raw_entries_truncated": len(raw_entries) < len(entries),
        "raw_entries_included": bool(include_entries),
        "bundle": bundle_builder(entries, resolved_session_dir),
        "entries": included_entries,
    }
