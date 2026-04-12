"""Helpers for persisted session browsing and task replay payload assembly."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any, Optional

import benchmark
from logging_system import (
    current_session_dir,
    latest_session_dir,
    list_persistence_sessions,
    list_session_tasks,
    records_from,
    read_persistence_session,
    read_session_log_records,
    read_task_replay_records,
)
from logging_system.task_rollup import summarize_task_rollup


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
    disconnected = bool(current_world_health.get("disconnected"))
    consecutive_failures = int(current_world_health.get("consecutive_failures", 0) or 0)
    total_failures = int(current_world_health.get("total_failures", 0) or 0)
    failure_threshold = int(current_world_health.get("failure_threshold", 0) or 0)
    last_error = str(current_world_health.get("last_error") or "")
    last_error_detail = str(current_world_health.get("last_error_detail") or "")
    if not any([stale, disconnected, consecutive_failures, total_failures, failure_threshold, last_error, last_error_detail]):
        return {}
    normalized = {
        "stale_seen": stale or total_failures > 0 or consecutive_failures > 0 or bool(last_error),
        "ended_stale": stale,
        "disconnect_seen": disconnected,
        "ended_disconnected": stale and disconnected,
        "failure_threshold": failure_threshold,
        "last_error": last_error,
    }
    if last_error_detail:
        normalized["last_error_detail"] = last_error_detail
    return normalized


def _normalize_live_runtime_fault(current_runtime_fault_state: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(current_runtime_fault_state, dict):
        return {}
    degraded = bool(current_runtime_fault_state.get("degraded"))
    source = str(current_runtime_fault_state.get("source") or "")
    stage = str(current_runtime_fault_state.get("stage") or "")
    error = str(current_runtime_fault_state.get("error") or "")
    count = int(current_runtime_fault_state.get("count", 0) or 0)
    first_at = float(current_runtime_fault_state.get("first_at", 0.0) or 0.0)
    updated_at = float(current_runtime_fault_state.get("updated_at", 0.0) or 0.0)
    has_fault_marker = any([degraded, source, stage, error, updated_at])
    if has_fault_marker and count <= 0:
        count = 1
    if has_fault_marker and not first_at:
        first_at = updated_at
    if not any([degraded, source, stage, error, count, first_at, updated_at]):
        return {}
    normalized = {
        "degraded": degraded,
        "source": source,
        "stage": stage,
        "error": error,
        "count": count,
        "first_at": first_at,
        "updated_at": updated_at,
    }
    raw_breakdown = current_runtime_fault_state.get("breakdown")
    if isinstance(raw_breakdown, list):
        breakdown = []
        for item in raw_breakdown:
            if not isinstance(item, dict):
                continue
            item_source = str(item.get("source") or "")
            item_stage = str(item.get("stage") or "")
            item_count = int(item.get("count", 0) or 0)
            if item_count <= 0 or not (item_source or item_stage):
                continue
            breakdown.append(
                {
                    "source": item_source,
                    "stage": item_stage,
                    "count": item_count,
                }
            )
        breakdown.sort(key=lambda item: (-item["count"], item["source"], item["stage"]))
        if breakdown:
            normalized["breakdown"] = breakdown[:4]
    elif count > 0 and (source or stage):
        normalized["breakdown"] = [
            {
                "source": source,
                "stage": stage,
                "count": count,
            }
        ]
    return normalized


def _merge_runtime_fault_breakdown(
    left: Optional[list[dict[str, Any]]],
    right: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], int] = {}
    for items in (left, right):
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "")
            stage = str(item.get("stage") or "")
            count = int(item.get("count", 0) or 0)
            if count <= 0 or not (source or stage):
                continue
            key = (source, stage)
            buckets[key] = max(buckets.get(key, 0), count)
    merged = [
        {
            "source": source,
            "stage": stage,
            "count": count,
        }
        for (source, stage), count in buckets.items()
    ]
    merged.sort(key=lambda item: (-item["count"], item["source"], item["stage"]))
    return merged[:4]


def _merge_runtime_fault_summary(
    persisted: Optional[dict[str, Any]],
    live: Optional[dict[str, Any]],
) -> dict[str, Any]:
    left = _normalize_live_runtime_fault(persisted)
    right = _normalize_live_runtime_fault(live)
    if not left:
        return right
    if not right:
        return left
    left_updated_at = float(left.get("updated_at", 0.0) or 0.0)
    right_updated_at = float(right.get("updated_at", 0.0) or 0.0)
    latest = right if right_updated_at >= left_updated_at else left
    first_candidates = [
        float(item.get("first_at") or item.get("updated_at") or 0.0)
        for item in (left, right)
        if float(item.get("first_at") or item.get("updated_at") or 0.0) > 0.0
    ]
    merged = {
        **latest,
        "degraded": bool(left.get("degraded") or right.get("degraded")),
        "count": max(int(left.get("count", 0) or 0), int(right.get("count", 0) or 0)),
        "first_at": min(first_candidates) if first_candidates else float(latest.get("updated_at") or 0.0),
    }
    breakdown = _merge_runtime_fault_breakdown(left.get("breakdown"), right.get("breakdown"))
    if breakdown:
        merged["breakdown"] = breakdown
    return merged


def _summarize_live_task_rollup(current_tasks: Optional[list[dict[str, Any]]]) -> dict[str, Any]:
    if not isinstance(current_tasks, list):
        return {}
    return summarize_task_rollup(current_tasks)


def build_session_catalog_payload(
    log_session_root: str,
    *,
    selected_session_dir: Optional[Path],
    current_world_health: Optional[dict[str, Any]] = None,
    current_runtime_fault_state: Optional[dict[str, Any]] = None,
    current_tasks: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Assemble the session catalog payload for the diagnostics UI."""
    sessions = list_persistence_sessions(log_session_root, limit=30)
    live_world_health = _normalize_live_world_health(current_world_health)
    live_runtime_fault = _normalize_live_runtime_fault(current_runtime_fault_state)
    live_task_rollup = _summarize_live_task_rollup(current_tasks)
    if live_world_health:
        for item in sessions:
            if not item.get("is_current"):
                continue
            merged_world_health = dict(item.get("world_health") or {})
            persisted_last_error = str(merged_world_health.get("last_error") or "")
            merged_world_health.update(live_world_health)
            live_last_error = str(live_world_health.get("last_error") or "")
            live_last_error_detail = str(live_world_health.get("last_error_detail") or "")
            if live_last_error and live_last_error != persisted_last_error and not live_last_error_detail:
                merged_world_health["last_error_detail"] = ""
            item["world_health"] = merged_world_health
            break
    if live_runtime_fault:
        for item in sessions:
            if not item.get("is_current"):
                continue
            item["runtime_fault_summary"] = _merge_runtime_fault_summary(
                item.get("runtime_fault_summary") if isinstance(item.get("runtime_fault_summary"), dict) else {},
                live_runtime_fault,
            )
            break
    if live_task_rollup:
        for item in sessions:
            if item.get("is_current"):
                item["task_rollup"] = live_task_rollup
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


def _read_benchmark_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _player_visible_entry(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    event = str(record.get("event") or "")
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    timestamp = float(record.get("timestamp", 0.0) or 0.0)
    if event in {"adjutant_response_sent", "query_response_sent"}:
        content = str(record.get("message") or data.get("answer") or data.get("response_text") or "")
        if not content:
            return None
        return {
            "kind": "adjutant",
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or ""),
            "content": content,
        }
    if event == "player_notification_sent":
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        content = str(record.get("message") or data.get("content") or "")
        if not content:
            return None
        return {
            "kind": "notification",
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or nested.get("task_id") or ""),
            "content": content,
        }
    if event in {"task_info", "task_warning"}:
        content = str(record.get("message") or data.get("content") or "")
        if not content:
            return None
        return {
            "kind": "task_message",
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or ""),
            "content": content,
            "message_type": event,
        }
    if event == "task_message_registered" and str(data.get("message_type") or "") in {"task_info", "task_warning"}:
        content = str(data.get("content") or "")
        if not content:
            return None
        return {
            "kind": "task_message",
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or ""),
            "content": content,
            "message_type": str(data.get("message_type") or ""),
        }
    return None


def _build_player_visible_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [entry for entry in (_player_visible_entry(record) for record in records) if entry is not None]
    return entries[-80:]


def _notification_entry(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    event = str(record.get("event") or "")
    if event != "player_notification_sent":
        return None
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    timestamp = float(record.get("timestamp", 0.0) or 0.0)
    content = str(record.get("message") or data.get("content") or "")
    if not content:
        return None
    return {
        "timestamp": timestamp,
        "task_id": str(data.get("task_id") or nested.get("task_id") or ""),
        "content": content,
    }


def _build_notification_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [entry for entry in (_notification_entry(record) for record in records) if entry is not None]
    return entries[-80:]


def _query_response_entry(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    event = str(record.get("event") or "")
    if event not in {"adjutant_response_sent", "query_response_sent"}:
        return None
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    timestamp = float(record.get("timestamp", 0.0) or 0.0)
    answer = str(record.get("message") or data.get("answer") or data.get("response_text") or "")
    if not answer:
        return None
    return {
        "timestamp": timestamp,
        "task_id": str(data.get("task_id") or ""),
        "answer": answer,
        "response_type": str(data.get("response_type") or ""),
        "ok": bool(data.get("ok", False)),
        "message_id": str(data.get("message_id") or ""),
        "existing_task_id": str(data.get("existing_task_id") or ""),
    }


def _build_query_response_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [entry for entry in (_query_response_entry(record) for record in records) if entry is not None]
    return entries[-80:]


def _error_entry(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    level = str(record.get("level") or "").upper()
    if level not in {"ERROR", "CRITICAL"}:
        return None
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    timestamp = float(record.get("timestamp", 0.0) or 0.0)
    task_id = str(
        data.get("task_id")
        or data.get("holder_task_id")
        or nested.get("task_id")
        or ""
    )
    message = str(record.get("message") or "")
    error = str(data.get("error") or "")
    content = message or error
    if not content:
        return None
    if error and error not in content:
        content = f"{content} | {error}"
    return {
        "timestamp": timestamp,
        "task_id": task_id,
        "content": content,
        "component": str(record.get("component") or ""),
        "event": str(record.get("event") or ""),
        "level": level,
    }


def _build_error_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [entry for entry in (_error_entry(record) for record in records) if entry is not None]
    return entries[-80:]


def _task_message_entry(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    event = str(record.get("event") or "")
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    timestamp = float(record.get("timestamp", 0.0) or 0.0)

    if event == "task_message_registered":
        message_type = str(data.get("message_type") or "")
        content = str(data.get("content") or "")
        if not message_type or not content:
            return None
        return {
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or ""),
            "message_id": str(data.get("message_id") or ""),
            "message_type": message_type,
            "content": content,
            "priority": int(data.get("priority", 50) or 50),
        }

    if event in {"task_info", "task_warning", "task_complete_report", "task_question"}:
        content = str(record.get("message") or data.get("content") or "")
        if not content:
            return None
        entry = {
            "timestamp": timestamp,
            "task_id": str(data.get("task_id") or ""),
            "message_id": str(data.get("message_id") or ""),
            "message_type": event,
            "content": content,
            "priority": int(data.get("priority", 50) or 50),
        }
        if event == "task_question":
            options = data.get("options")
            if isinstance(options, list):
                entry["options"] = [str(option) for option in options]
            timeout_s = data.get("timeout_s")
            if timeout_s is not None:
                entry["timeout_s"] = float(timeout_s)
            default_option = data.get("default_option")
            if default_option is not None:
                entry["default_option"] = str(default_option)
        return entry

    return None


def _build_task_message_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [entry for entry in (_task_message_entry(record) for record in records) if entry is not None]
    return entries[-120:]


def build_session_history_payload(
    log_session_root: str,
    *,
    session_dir: Optional[Path],
) -> dict[str, Any]:
    """Assemble a session-scoped diagnostics history payload."""
    resolved = session_dir or default_session_dir(log_session_root)
    if resolved is None:
        return {
            "session_dir": None,
            "is_live": False,
            "log_entries": [],
            "benchmark_records": [],
            "player_visible_entries": [],
            "notification_entries": [],
            "query_response_entries": [],
            "error_entries": [],
            "task_message_entries": [],
        }
    current = current_session_dir()
    is_live = current is not None and resolved.resolve() == current.resolve()
    if is_live:
        log_entries = [record.to_dict() for record in _live_log_records()]
        benchmark_records = [record.to_dict() for record in benchmark.records_from(0)]
    else:
        log_entries = read_session_log_records(resolved, limit=500)
        benchmark_records = _read_benchmark_records(resolved / "benchmark_records.json")
    return {
        "session_dir": str(resolved),
        "is_live": is_live,
        "log_entries": log_entries,
        "benchmark_records": benchmark_records,
        "player_visible_entries": _build_player_visible_entries(log_entries),
        "notification_entries": _build_notification_entries(log_entries),
        "query_response_entries": _build_query_response_entries(log_entries),
        "error_entries": _build_error_entries(log_entries),
        "task_message_entries": _build_task_message_entries(log_entries),
    }


def _live_log_records() -> list[Any]:
    return [record for record in records_from(0) if record.component != "benchmark"][-500:]


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
    bundle = bundle_builder(entries, resolved_session_dir)
    session_summary = read_persistence_session(resolved_session_dir) if resolved_session_dir is not None else {}
    world_health = session_summary.get("world_health") if isinstance(session_summary.get("world_health"), dict) else {}
    runtime_fault_summary = (
        session_summary.get("runtime_fault_summary") if isinstance(session_summary.get("runtime_fault_summary"), dict) else {}
    )
    if isinstance(bundle, dict) and (world_health or runtime_fault_summary):
        session_context = bundle.get("session_context")
        if not isinstance(session_context, dict):
            session_context = {}
        if world_health:
            session_context["world_health"] = dict(world_health)
        session_context["runtime_fault_summary"] = dict(runtime_fault_summary)
        bundle["session_context"] = session_context
    return {
        "task_id": task_id,
        "session_dir": str(resolved_session_dir) if resolved_session_dir else None,
        "log_path": log_path,
        "entry_count": len(entries),
        "raw_entry_count": len(raw_entries),
        "raw_entries_truncated": len(raw_entries) < len(entries),
        "raw_entries_included": bool(include_entries),
        "bundle": bundle,
        "entries": included_entries,
    }
