"""Benchmark reporting helpers built on the shared benchmark store."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Optional, Union

import benchmark


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_benchmarks(
    *,
    tag: Optional[str] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
) -> list[dict[str, Any]]:
    records = benchmark.query(tag=tag, start_time=start_time, end_time=end_time, slowest_first=False)
    grouped: dict[str, list[float]] = {}
    for record in records:
        grouped.setdefault(record.tag, []).append(record.duration_ms)

    summary: list[dict[str, Any]] = []
    for record_tag in sorted(grouped):
        durations = grouped[record_tag]
        summary.append(
            {
                "tag": record_tag,
                "count": len(durations),
                "avg_ms": sum(durations) / len(durations),
                "p95_ms": _percentile(durations, 0.95),
                "max_ms": max(durations),
                "total_ms": sum(durations),
            }
        )
    return summary


def export_benchmark_report_json(
    path: Optional[Union[str, Path]] = None,
    *,
    tag: Optional[str] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
    indent: int = 2,
) -> str:
    payload = summarize_benchmarks(tag=tag, start_time=start_time, end_time=end_time)
    serialized = json.dumps(payload, ensure_ascii=True, indent=indent)
    if path is not None:
        Path(path).write_text(serialized + "\n", encoding="utf-8")
    return serialized
