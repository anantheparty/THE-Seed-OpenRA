"""Bridge benchmark records into the structured logging store."""

from __future__ import annotations

from typing import Optional

import benchmark

from .core import StructuredLogger, get_logger


_INSTALLED = False
_LOGGER: Optional[StructuredLogger] = None


def _on_benchmark_record(record: benchmark.BenchmarkRecord) -> None:
    logger = _LOGGER or get_logger("benchmark")
    logger.debug(
        "Benchmark record captured",
        event="benchmark_recorded",
        benchmark_tag=record.tag,
        name=record.name,
        duration_ms=record.duration_ms,
        metadata=record.metadata,
        started_at=record.started_at.isoformat(),
        ended_at=record.ended_at.isoformat(),
    )


def install_benchmark_logging() -> None:
    global _INSTALLED, _LOGGER
    if _INSTALLED:
        return
    _LOGGER = get_logger("benchmark")
    benchmark.subscribe(_on_benchmark_record)
    _INSTALLED = True


def uninstall_benchmark_logging() -> None:
    global _INSTALLED
    if not _INSTALLED:
        return
    benchmark.unsubscribe(_on_benchmark_record)
    _INSTALLED = False
