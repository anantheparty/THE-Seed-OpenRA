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
        self.all_path = session_dir / "all.jsonl"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def append(self, record: "LogRecord") -> None:
        payload = record.to_json() + "\n"
        self.all_path.parent.mkdir(parents=True, exist_ok=True)
        with self.all_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)

        task_id = record.data.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_path = self.tasks_dir / f"{_safe_filename(task_id)}.jsonl"
            with task_path.open("a", encoding="utf-8") as handle:
                handle.write(payload)


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
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True)


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
        serialized = json.dumps(payload, ensure_ascii=True, indent=indent)
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
        base = Path(base_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = session_name or f"session-{timestamp}"
        session_dir = base / _safe_filename(name)
        session_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = session_dir / "session.json"
        payload = {
            "session_name": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "metadata": _serialize(metadata or {}),
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        with self._lock:
            self._persistent_session = PersistentLogSession(session_dir)
        return session_dir

    def stop_persistence_session(self) -> None:
        with self._lock:
            self._persistent_session = None

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
