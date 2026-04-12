"""Structured JSON logging store, query, and export helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
from threading import RLock
import time
from typing import Any, Dict, Iterable, Literal, Optional, Union

from .task_rollup import compact_task_rollup, summarize_task_rollup
ComponentName = Literal["kernel", "task_agent", "expert", "world_model", "adjutant", "game_loop", "benchmark"]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]

_LEVEL_TO_STD = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return cleaned or "unknown"


class PersistentLogSession:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.tasks_dir = session_dir / "tasks"
        self.components_dir = session_dir / "components"
        self.all_path = session_dir / "all.jsonl"
        self.metadata_path = session_dir / "session.json"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.components_dir.mkdir(parents=True, exist_ok=True)
        self.record_count = 0
        self.task_counts: dict[str, int] = {}
        self.component_counts: dict[str, int] = {}
        self.world_health_summary = _empty_world_health_summary()
        self.runtime_fault_summary = _empty_runtime_fault_summary()

    def append(self, record: "LogRecord") -> None:
        payload = record.to_json() + "\n"
        self.all_path.parent.mkdir(parents=True, exist_ok=True)
        with self.all_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
        self.record_count += 1

        component_name = _safe_filename(record.component)
        component_path = self.components_dir / f"{component_name}.jsonl"
        with component_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
        self.component_counts[record.component] = self.component_counts.get(record.component, 0) + 1

        task_id = record.data.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_path = self.tasks_dir / f"{_safe_filename(task_id)}.jsonl"
            with task_path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            self.task_counts[task_id] = self.task_counts.get(task_id, 0) + 1
        if record.component == "world_model":
            _update_world_health_summary_from_event(
                self.world_health_summary,
                str(record.event or ""),
                record.data if isinstance(record.data, dict) else {},
            )
        _update_runtime_fault_summary_from_event(
            self.runtime_fault_summary,
            component=str(record.component or ""),
            event=str(record.event or ""),
            data=record.data if isinstance(record.data, dict) else {},
            timestamp=record.timestamp,
        )

    def finalize(self) -> None:
        if not self.metadata_path.exists():
            return
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        payload["ended_at"] = datetime.now(timezone.utc).isoformat()
        payload["record_count"] = self.record_count
        payload["component_counts"] = dict(sorted(self.component_counts.items()))
        payload["task_counts"] = dict(sorted(self.task_counts.items()))
        payload["task_file_count"] = len(self.task_counts)
        world_health = _compact_world_health_summary(self.world_health_summary)
        if world_health:
            payload["world_health"] = world_health
        runtime_fault_summary = _compact_runtime_fault_summary(self.runtime_fault_summary)
        if runtime_fault_summary:
            payload["runtime_fault_summary"] = runtime_fault_summary
        task_rollup = _derive_task_rollup(self.session_dir)
        if task_rollup:
            payload["task_rollup"] = task_rollup
        self.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_time(value: Optional[Union[datetime, float, int]]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).timestamp()
        return value.astimezone(timezone.utc).timestamp()
    return float(value)


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(item) for item in value]
    if hasattr(value, "__dict__"):
        payload = {}
        for key, item in vars(value).items():
            if key.startswith("_"):
                continue
            payload[key] = _serialize(item)
        if payload:
            return payload
    return repr(value)


@dataclass(frozen=True)
class LogRecord:
    timestamp: float
    component: str
    level: str
    message: str
    event: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "iso_time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "component": self.component,
            "level": self.level,
            "message": self.message,
            "event": self.event,
            "data": _serialize(self.data),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class LogStore:
    def __init__(self) -> None:
        self._records: list[LogRecord] = []
        self._lock = RLock()
        self._persistent_session: Optional[PersistentLogSession] = None

    def add(
        self,
        *,
        component: str,
        level: str,
        message: str,
        event: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> LogRecord:
        record = LogRecord(
            timestamp=time.time() if timestamp is None else float(timestamp),
            component=component,
            level=level,
            message=message,
            event=event,
            data=dict(_serialize(data or {})),
        )
        with self._lock:
            self._records.append(record)
            session = self._persistent_session
        if session is not None:
            session.append(record)
        return record

    def query(
        self,
        *,
        component: Optional[str] = None,
        level: Optional[LogLevel] = None,
        start_time: Optional[Union[datetime, float, int]] = None,
        end_time: Optional[Union[datetime, float, int]] = None,
        event: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[LogRecord]:
        start_ts = _normalize_time(start_time)
        end_ts = _normalize_time(end_time)
        with self._lock:
            records = list(self._records)
        if component is not None:
            records = [record for record in records if record.component == component]
        if level is not None:
            records = [record for record in records if record.level == level]
        if start_ts is not None:
            records = [record for record in records if record.timestamp >= start_ts]
        if end_ts is not None:
            records = [record for record in records if record.timestamp <= end_ts]
        if event is not None:
            records = [record for record in records if record.event == event]
        records.sort(key=lambda record: record.timestamp)
        if limit is not None:
            records = records[-limit:]
        return records

    def records_from(self, offset: int, *, limit: Optional[int] = None) -> list[LogRecord]:
        start = max(0, int(offset))
        with self._lock:
            if start >= len(self._records):
                return []
            records = self._records[start:] if limit is None else self._records[start : start + max(0, int(limit))]
            return list(records)

    def tail(
        self,
        *,
        component: Optional[str] = None,
        level: Optional[LogLevel] = None,
        event: Optional[str] = None,
        limit: int = 100,
    ) -> list[LogRecord]:
        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        with self._lock:
            source = self._records
            results: list[LogRecord] = []
            for record in reversed(source):
                if component is not None and record.component != component:
                    continue
                if level is not None and record.level != level:
                    continue
                if event is not None and record.event != event:
                    continue
                results.append(record)
                if len(results) >= remaining:
                    break
        results.reverse()
        return results

    def export_json(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        component: Optional[str] = None,
        level: Optional[LogLevel] = None,
        start_time: Optional[Union[datetime, float, int]] = None,
        end_time: Optional[Union[datetime, float, int]] = None,
        event: Optional[str] = None,
        limit: Optional[int] = None,
        indent: int = 2,
    ) -> str:
        payload = [
            record.to_dict()
            for record in self.query(
                component=component,
                level=level,
                start_time=start_time,
                end_time=end_time,
                event=event,
                limit=limit,
            )
        ]
        serialized = json.dumps(payload, ensure_ascii=False, indent=indent)
        if path is not None:
            Path(path).write_text(serialized + "\n", encoding="utf-8")
        return serialized

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def start_persistence_session(
        self,
        base_dir: Union[str, Path],
        *,
        session_name: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Path:
        base = Path(base_dir).resolve()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = session_name or f"session-{timestamp}"
        session_dir = (base / _safe_filename(name)).resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = session_dir / "session.json"
        payload = {
            "session_name": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "metadata": _serialize(metadata or {}),
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (base / "latest.txt").write_text(str(session_dir) + "\n", encoding="utf-8")
        with self._lock:
            self._persistent_session = PersistentLogSession(session_dir)
        return session_dir

    def stop_persistence_session(self) -> None:
        with self._lock:
            session = self._persistent_session
            self._persistent_session = None
        if session is not None:
            session.finalize()

    def current_session_dir(self) -> Optional[Path]:
        with self._lock:
            session = self._persistent_session
        return None if session is None else session.session_dir

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


_DEFAULT_STORE = LogStore()


class StructuredLogger:
    def __init__(self, component: str, *, store: Optional[LogStore] = None) -> None:
        self.component = component
        self.store = store or _DEFAULT_STORE
        self._logger = logging.getLogger(component)

    def log(
        self,
        level: LogLevel,
        message: str,
        *,
        event: Optional[str] = None,
        timestamp: Optional[float] = None,
        **data: Any,
    ) -> LogRecord:
        record = self.store.add(
            component=self.component,
            level=level,
            message=message,
            event=event,
            data=data,
            timestamp=timestamp,
        )
        self._logger.log(_LEVEL_TO_STD[level], record.to_json())
        return record

    def debug(self, message: str, *, event: Optional[str] = None, **data: Any) -> LogRecord:
        return self.log("DEBUG", message, event=event, **data)

    def info(self, message: str, *, event: Optional[str] = None, **data: Any) -> LogRecord:
        return self.log("INFO", message, event=event, **data)

    def warn(self, message: str, *, event: Optional[str] = None, **data: Any) -> LogRecord:
        return self.log("WARN", message, event=event, **data)

    def error(self, message: str, *, event: Optional[str] = None, **data: Any) -> LogRecord:
        return self.log("ERROR", message, event=event, **data)


def get_logger(component: str) -> StructuredLogger:
    return StructuredLogger(component)


def query(
    *,
    component: Optional[str] = None,
    level: Optional[LogLevel] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
    event: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[LogRecord]:
    return _DEFAULT_STORE.query(
        component=component,
        level=level,
        start_time=start_time,
        end_time=end_time,
        event=event,
        limit=limit,
    )


def replay(**filters: Any) -> list[LogRecord]:
    return query(**filters)


def export_json(
    path: Optional[Union[str, Path]] = None,
    *,
    component: Optional[str] = None,
    level: Optional[LogLevel] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
    event: Optional[str] = None,
    limit: Optional[int] = None,
    indent: int = 2,
) -> str:
    return _DEFAULT_STORE.export_json(
        path,
        component=component,
        level=level,
        start_time=start_time,
        end_time=end_time,
        event=event,
        limit=limit,
        indent=indent,
    )


def clear() -> None:
    _DEFAULT_STORE.clear()


def records() -> list[LogRecord]:
    return list(query())


def records_from(offset: int, *, limit: Optional[int] = None) -> list[LogRecord]:
    return _DEFAULT_STORE.records_from(offset, limit=limit)


def tail_records(
    *,
    component: Optional[str] = None,
    level: Optional[LogLevel] = None,
    event: Optional[str] = None,
    limit: int = 100,
) -> list[LogRecord]:
    return _DEFAULT_STORE.tail(component=component, level=level, event=event, limit=limit)


def start_persistence_session(
    base_dir: Union[str, Path],
    *,
    session_name: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Path:
    return _DEFAULT_STORE.start_persistence_session(base_dir, session_name=session_name, metadata=metadata)


def stop_persistence_session() -> None:
    _DEFAULT_STORE.stop_persistence_session()


def current_session_dir() -> Optional[Path]:
    return _DEFAULT_STORE.current_session_dir()


def latest_session_dir(
    base_dir: Union[str, Path] = "Logs/runtime",
) -> Optional[Path]:
    """Resolve the latest persisted log session from ``latest.txt`` if present."""
    latest_path = Path(base_dir) / "latest.txt"
    if not latest_path.exists():
        return None
    try:
        session_path = Path(latest_path.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    if not session_path.is_absolute():
        session_path = (latest_path.parent / session_path).resolve()
    if not session_path.exists():
        return None
    return session_path


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_jsonl_dicts(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def _empty_world_health_summary() -> dict[str, Any]:
    return {
        "stale_seen": False,
        "ended_stale": False,
        "stale_refreshes": 0,
        "max_consecutive_failures": 0,
        "failure_threshold": 0,
        "last_error": "",
        "last_error_detail": "",
        "last_failure_layer": "",
        "slow_events": 0,
        "max_total_ms": 0.0,
    }


def _compact_world_health_summary(summary: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "stale_seen": bool(summary.get("stale_seen")),
        "ended_stale": bool(summary.get("ended_stale")),
        "stale_refreshes": int(summary.get("stale_refreshes", 0) or 0),
        "max_consecutive_failures": int(summary.get("max_consecutive_failures", 0) or 0),
        "failure_threshold": int(summary.get("failure_threshold", 0) or 0),
        "last_error": str(summary.get("last_error") or ""),
        "last_error_detail": str(summary.get("last_error_detail") or ""),
        "last_failure_layer": str(summary.get("last_failure_layer") or ""),
        "slow_events": int(summary.get("slow_events", 0) or 0),
        "max_total_ms": round(float(summary.get("max_total_ms", 0.0) or 0.0), 1),
    }
    if not any(
        [
            normalized["stale_seen"],
            normalized["ended_stale"],
            normalized["stale_refreshes"],
            normalized["max_consecutive_failures"],
            normalized["failure_threshold"],
            normalized["last_error"],
            normalized["last_error_detail"],
            normalized["last_failure_layer"],
            normalized["slow_events"],
            normalized["max_total_ms"],
        ]
    ):
        return {}
    return normalized


def _empty_runtime_fault_summary() -> dict[str, Any]:
    return {
        "degraded": False,
        "source": "",
        "stage": "",
        "error": "",
        "updated_at": 0.0,
    }


def _compact_runtime_fault_summary(summary: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "degraded": bool(summary.get("degraded")),
        "source": str(summary.get("source") or ""),
        "stage": str(summary.get("stage") or ""),
        "error": str(summary.get("error") or ""),
        "updated_at": float(summary.get("updated_at", 0.0) or 0.0),
    }
    if not any(
        [
            normalized["degraded"],
            normalized["source"],
            normalized["stage"],
            normalized["error"],
            normalized["updated_at"],
        ]
    ):
        return {}
    return normalized


def _update_world_health_summary_from_event(summary: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    if event == "world_refresh_completed":
        stale = bool(data.get("stale"))
        consecutive_failures = int(data.get("consecutive_failures", 0) or 0)
        failure_threshold = int(data.get("failure_threshold", 0) or 0)
        if stale:
            summary["stale_seen"] = True
            summary["stale_refreshes"] = int(summary.get("stale_refreshes", 0) or 0) + 1
        summary["ended_stale"] = stale
        summary["max_consecutive_failures"] = max(
            int(summary.get("max_consecutive_failures", 0) or 0),
            consecutive_failures,
        )
        if failure_threshold:
            summary["failure_threshold"] = max(
                int(summary.get("failure_threshold", 0) or 0),
                failure_threshold,
            )
        return

    if event == "world_refresh_failed":
        error = str(data.get("error") or "")
        error_detail = str(data.get("error_detail") or "")
        failure_layer = str(data.get("layer") or "")
        failure_threshold = int(data.get("failure_threshold", 0) or 0)
        if error:
            summary["last_error"] = error
        if error_detail:
            summary["last_error_detail"] = error_detail
        if failure_layer:
            summary["last_failure_layer"] = failure_layer
        if failure_threshold:
            summary["failure_threshold"] = max(
                int(summary.get("failure_threshold", 0) or 0),
                failure_threshold,
            )
        return

    if event == "world_refresh_slow":
        total_ms = float(data.get("total_ms", 0.0) or 0.0)
        summary["slow_events"] = int(summary.get("slow_events", 0) or 0) + 1
        summary["max_total_ms"] = max(float(summary.get("max_total_ms", 0.0) or 0.0), total_ms)


def _update_runtime_fault_summary_from_event(
    summary: dict[str, Any],
    *,
    component: str,
    event: str,
    data: dict[str, Any],
    timestamp: float,
) -> None:
    source = ""
    stage = ""
    error = ""

    if event == "runtime_probe_fault":
        source = str(data.get("source") or "")
        error = str(data.get("error") or "")
    elif event == "dashboard_publish_stage_failed":
        source = "dashboard_publish"
        stage = str(data.get("stage") or "")
        error = str(data.get("error") or "")
    elif event == "dashboard_publish_task_failed":
        source = "dashboard_publish"
        stage = "task"
        error = str(data.get("error") or "")
    else:
        return

    if not source and not error:
        return

    summary["degraded"] = True
    summary["source"] = source
    summary["stage"] = stage
    summary["error"] = error
    summary["updated_at"] = float(timestamp or 0.0)


def _derive_world_health_summary(session_dir: Path) -> dict[str, Any]:
    component_path = session_dir / "components" / "world_model.jsonl"
    if not component_path.exists():
        return {}
    summary = _empty_world_health_summary()
    for payload in _iter_jsonl_dicts(component_path):
        event = str(payload.get("event") or "")
        data = payload.get("data")
        _update_world_health_summary_from_event(summary, event, data if isinstance(data, dict) else {})
    return _compact_world_health_summary(summary)


def _derive_runtime_fault_summary(session_dir: Path) -> dict[str, Any]:
    components_dir = session_dir / "components"
    if not components_dir.exists():
        return {}
    summary = _empty_runtime_fault_summary()
    for component_name in ("main", "dashboard_publish"):
        component_path = components_dir / f"{component_name}.jsonl"
        if not component_path.exists():
            continue
        for payload in _iter_jsonl_dicts(component_path):
            event = str(payload.get("event") or "")
            data = payload.get("data")
            timestamp = float(payload.get("timestamp") or 0.0)
            _update_runtime_fault_summary_from_event(
                summary,
                component=component_name,
                event=event,
                data=data if isinstance(data, dict) else {},
                timestamp=timestamp,
            )
    return _compact_runtime_fault_summary(summary)


def _derive_task_rollup(session_dir: Path) -> dict[str, Any]:
    return summarize_task_rollup(list_session_tasks(session_dir, limit=0))


def list_persistence_sessions(
    base_dir: Union[str, Path] = "Logs/runtime",
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List persisted runtime sessions with lightweight metadata."""
    base = Path(base_dir).resolve()
    if not base.exists():
        return []

    latest = latest_session_dir(base)
    current = current_session_dir()
    sessions: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        metadata_path = child / "session.json"
        if not metadata_path.exists():
            continue
        payload = _load_json_dict(metadata_path)
        metadata_dirty = False
        world_health = _compact_world_health_summary(
            payload.get("world_health") if isinstance(payload.get("world_health"), dict) else {}
        )
        if not world_health:
            world_health = _derive_world_health_summary(child)
            if world_health:
                payload["world_health"] = world_health
                metadata_dirty = True
        runtime_fault_summary = _compact_runtime_fault_summary(
            payload.get("runtime_fault_summary") if isinstance(payload.get("runtime_fault_summary"), dict) else {}
        )
        if not runtime_fault_summary:
            runtime_fault_summary = _derive_runtime_fault_summary(child)
            if runtime_fault_summary:
                payload["runtime_fault_summary"] = runtime_fault_summary
                metadata_dirty = True
        task_rollup = compact_task_rollup(
            payload.get("task_rollup") if isinstance(payload.get("task_rollup"), dict) else {}
        )
        if not task_rollup:
            task_rollup = _derive_task_rollup(child)
            if task_rollup:
                payload["task_rollup"] = task_rollup
                metadata_dirty = True
        if metadata_dirty:
            try:
                metadata_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        task_counts = payload.get("task_counts")
        task_count = len(task_counts) if isinstance(task_counts, dict) else int(payload.get("task_file_count") or 0)
        started_at = str(payload.get("started_at") or "")
        sessions.append(
            {
                "session_name": str(payload.get("session_name") or child.name),
                "session_dir": str(child.resolve()),
                "started_at": started_at,
                "ended_at": str(payload.get("ended_at") or ""),
                "record_count": int(payload.get("record_count") or 0),
                "task_count": task_count,
                "task_file_count": int(payload.get("task_file_count") or task_count),
                "pid": int(payload.get("pid") or 0),
                "cwd": str(payload.get("cwd") or ""),
                "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                "world_health": world_health,
                "runtime_fault_summary": runtime_fault_summary,
                "task_rollup": task_rollup,
                "is_latest": latest is not None and child.resolve() == latest.resolve(),
                "is_current": current is not None and child.resolve() == current.resolve(),
                "mtime": child.stat().st_mtime,
            }
        )
    sessions.sort(
        key=lambda item: (
            str(item.get("started_at") or ""),
            float(item.get("mtime") or 0.0),
            str(item.get("session_name") or ""),
        ),
        reverse=True,
    )
    if limit > 0:
        sessions = sessions[:limit]
    for item in sessions:
        item.pop("mtime", None)
    return sessions


def list_session_tasks(
    session_dir: Union[str, Path],
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Build a lightweight task catalog from persisted task JSONL files."""
    base = Path(session_dir)
    tasks_dir = base / "tasks"
    if not tasks_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for task_path in sorted(tasks_dir.glob("*.jsonl")):
        task_id = task_path.stem
        raw_text = ""
        task_label = ""
        status = "running"
        summary = ""
        latest_message_summary = ""
        latest_signal_summary = ""
        kind = ""
        priority = 0
        created_at = 0.0
        last_timestamp = 0.0
        entry_count = 0
        for payload in _iter_jsonl_dicts(task_path):
            entry_count += 1
            event = str(payload.get("event") or "")
            timestamp = float(payload.get("timestamp") or 0.0)
            if timestamp > 0:
                last_timestamp = timestamp
                if created_at <= 0:
                    created_at = timestamp
            data = payload.get("data")
            data = data if isinstance(data, dict) else {}
            if data.get("task_id"):
                task_id = str(data.get("task_id") or task_id)
            if data.get("task_label"):
                task_label = str(data.get("task_label") or task_label)
            if event == "task_created":
                raw_text = str(data.get("raw_text") or raw_text)
                kind = str(data.get("kind") or kind)
                priority = int(data.get("priority") or priority or 0)
                if timestamp > 0:
                    created_at = timestamp
            elif event == "task_completed":
                result = str(data.get("result") or "")
                if result:
                    status = result
                summary = str(data.get("summary") or summary)
            elif event == "expert_signal" and str(data.get("signal_kind") or "") == "task_complete":
                result = str(data.get("result") or "")
                if result:
                    status = result
                summary = str(data.get("summary") or summary)
            elif event == "expert_signal" and str(data.get("signal_kind") or "") in {
                "blocked",
                "constraint_violated",
                "risk_alert",
                "resource_lost",
                "progress",
                "target_found",
            }:
                latest_signal_summary = str(
                    data.get("summary")
                    or payload.get("summary")
                    or payload.get("message")
                    or latest_signal_summary
                )
            elif event in {"task_info", "task_warning"}:
                latest_message_summary = str(
                    data.get("summary")
                    or data.get("content")
                    or payload.get("message")
                    or latest_message_summary
                )
            elif event == "task_message_registered" and str(data.get("message_type") or "") in {
                "task_info",
                "task_warning",
            }:
                latest_message_summary = str(
                    data.get("summary")
                    or data.get("content")
                    or latest_message_summary
                )

        if not task_id:
            continue
        items.append(
            {
                "task_id": task_id,
                "raw_text": raw_text,
                "label": task_label,
                "kind": kind,
                "priority": priority,
                "status": status,
                "timestamp": created_at or last_timestamp,
                "created_at": created_at or last_timestamp,
                "entry_count": entry_count,
                "summary": summary or latest_message_summary or latest_signal_summary,
                "log_path": str(task_path.resolve()),
            }
        )
    items.sort(
        key=lambda item: (
            float(item.get("timestamp") or 0.0),
            str(item.get("task_id") or ""),
        ),
        reverse=True,
    )
    if limit > 0:
        items = items[:limit]
    return items


def read_task_replay_records(
    task_id: str,
    *,
    session_dir: Optional[Union[str, Path]] = None,
    latest_base_dir: Optional[Union[str, Path]] = "Logs/runtime",
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Read persisted task-scoped JSONL logs from the active/given or latest session."""
    if not task_id:
        return []
    candidates: list[Path] = []
    primary = Path(session_dir) if session_dir is not None else current_session_dir()
    if primary is not None:
        candidates.append(primary)
    if latest_base_dir is not None:
        latest = latest_session_dir(latest_base_dir)
        if latest is not None and latest not in candidates:
            candidates.append(latest)

    for base in candidates:
        task_path = Path(base) / "tasks" / f"{_safe_filename(task_id)}.jsonl"
        if not task_path.exists():
            continue
        items = list(_iter_jsonl_dicts(task_path))
        if limit is not None and limit > 0:
            return items[-limit:]
        return items
    return []
