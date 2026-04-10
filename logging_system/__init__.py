"""Structured logging system with benchmark integration."""

from .benchmark_integration import install_benchmark_logging
from .benchmark_integration import uninstall_benchmark_logging
from .benchmark_tools import export_benchmark_report_json, summarize_benchmarks
from .core import (
    LogLevel,
    LogRecord,
    LogStore,
    StructuredLogger,
    clear,
    current_session_dir,
    export_json,
    get_logger,
    query,
    read_task_replay_records,
    records,
    replay,
    start_persistence_session,
    stop_persistence_session,
)

__all__ = [
    "LogLevel",
    "LogRecord",
    "LogStore",
    "StructuredLogger",
    "clear",
    "current_session_dir",
    "export_benchmark_report_json",
    "export_json",
    "get_logger",
    "install_benchmark_logging",
    "query",
    "read_task_replay_records",
    "records",
    "replay",
    "start_persistence_session",
    "stop_persistence_session",
    "summarize_benchmarks",
    "uninstall_benchmark_logging",
]
