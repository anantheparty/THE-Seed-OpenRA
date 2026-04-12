"""Shared helpers for session/task rollup summaries."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TERMINAL_TASK_STATUSES = {"succeeded", "failed", "partial", "aborted"}
DISPLAY_TASK_STATUSES = ("running", "queued", "paused", "succeeded", "failed", "partial", "aborted")


def normalize_task_status(value: Any) -> str:
    status = str(value or "running").strip().lower()
    return status or "running"


def compact_task_rollup(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    total = int(summary.get("total") or 0)
    non_terminal = int(summary.get("non_terminal") or 0)
    terminal = int(summary.get("terminal") or 0)
    by_status_raw = summary.get("by_status") if isinstance(summary.get("by_status"), dict) else {}
    by_status = {
        str(key): int(value or 0)
        for key, value in by_status_raw.items()
        if int(value or 0) > 0
    }
    if total <= 0 and non_terminal <= 0 and terminal <= 0 and not by_status:
        return {}
    ordered_status: dict[str, int] = {
        status: by_status[status]
        for status in DISPLAY_TASK_STATUSES
        if by_status.get(status)
    }
    for key in sorted(by_status):
        if key not in ordered_status:
            ordered_status[key] = by_status[key]
    return {
        "total": total,
        "non_terminal": non_terminal,
        "terminal": terminal,
        "by_status": ordered_status,
    }


def summarize_task_rollup(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "total": 0,
        "non_terminal": 0,
        "terminal": 0,
        "by_status": {},
    }
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = normalize_task_status(task.get("status"))
        summary["total"] += 1
        if status in TERMINAL_TASK_STATUSES:
            summary["terminal"] += 1
        else:
            summary["non_terminal"] += 1
        by_status = summary["by_status"]
        by_status[status] = int(by_status.get(status) or 0) + 1
    return compact_task_rollup(summary)
