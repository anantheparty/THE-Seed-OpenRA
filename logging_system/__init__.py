"""Structured logging system with benchmark integration."""

from .benchmark_integration import install_benchmark_logging
from .benchmark_tools import export_benchmark_report_json, summarize_benchmarks
from .core import (
    LogLevel,
    LogRecord,
    LogStore,
    StructuredLogger,
    clear,
    export_json,
    get_logger,
    query,
    records,
    replay,
)

install_benchmark_logging()

__all__ = [
    "LogLevel",
    "LogRecord",
    "LogStore",
    "StructuredLogger",
    "clear",
    "export_benchmark_report_json",
    "export_json",
    "get_logger",
    "query",
    "records",
    "replay",
    "summarize_benchmarks",
]
