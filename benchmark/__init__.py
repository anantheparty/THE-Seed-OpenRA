from __future__ import annotations

from contextlib import ContextDecorator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Union


BenchmarkTag = Literal[
    "llm_call",
    "tool_exec",
    "gameapi_call",
    "job_tick",
    "world_refresh",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: Optional[Union[datetime, float, int]]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


@dataclass(frozen=True)
class BenchmarkRecord:
    tag: BenchmarkTag
    name: str
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["ended_at"] = self.ended_at.isoformat()
        return payload


class BenchmarkStore:
    def __init__(self) -> None:
        self._records: List[BenchmarkRecord] = []
        self._subscribers: List[Callable[[BenchmarkRecord], None]] = []
        self._lock = RLock()

    def add(
        self,
        *,
        tag: BenchmarkTag,
        name: str,
        started_at: datetime,
        ended_at: datetime,
        duration_ms: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BenchmarkRecord:
        record = BenchmarkRecord(
            tag=tag,
            name=name,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._records.append(record)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(record)
            except Exception:
                continue
        return record

    def query(
        self,
        *,
        tag: Optional[BenchmarkTag] = None,
        start_time: Optional[Union[datetime, float, int]] = None,
        end_time: Optional[Union[datetime, float, int]] = None,
        top_n: Optional[int] = None,
        slowest_first: bool = True,
    ) -> List[BenchmarkRecord]:
        start_dt = _normalize_time(start_time)
        end_dt = _normalize_time(end_time)
        with self._lock:
            results = list(self._records)

        if tag is not None:
            results = [record for record in results if record.tag == tag]
        if start_dt is not None:
            results = [record for record in results if record.started_at >= start_dt]
        if end_dt is not None:
            results = [record for record in results if record.ended_at <= end_dt]

        results.sort(key=lambda record: record.duration_ms, reverse=slowest_first)
        if top_n is not None:
            results = results[:top_n]
        return results

    def export_json(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        tag: Optional[BenchmarkTag] = None,
        start_time: Optional[Union[datetime, float, int]] = None,
        end_time: Optional[Union[datetime, float, int]] = None,
        top_n: Optional[int] = None,
        slowest_first: bool = True,
        indent: int = 2,
    ) -> str:
        payload = [
            record.to_dict()
            for record in self.query(
                tag=tag,
                start_time=start_time,
                end_time=end_time,
                top_n=top_n,
                slowest_first=slowest_first,
            )
        ]
        serialized = json.dumps(payload, ensure_ascii=True, indent=indent)
        if path is not None:
            Path(path).write_text(serialized + "\n", encoding="utf-8")
        return serialized

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def subscribe(self, callback: Callable[[BenchmarkRecord], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[BenchmarkRecord], None]) -> None:
        with self._lock:
            self._subscribers = [subscriber for subscriber in self._subscribers if subscriber != callback]

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class Timer(ContextDecorator):
    def __init__(
        self,
        tag: BenchmarkTag,
        *,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        store: Optional[BenchmarkStore] = None,
    ) -> None:
        self.tag = tag
        self.name = name or tag
        self.metadata = dict(metadata or {})
        self.store = store or _DEFAULT_STORE
        self._started_at: Optional[datetime] = None
        self._started_perf: Optional[float] = None
        self.record: Optional[BenchmarkRecord] = None

    def __enter__(self) -> "Timer":
        self._started_at = _utc_now()
        self._started_perf = perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._started_at is None or self._started_perf is None:
            raise RuntimeError("Timer must be entered before it can exit.")
        ended_at = _utc_now()
        duration_ms = (perf_counter() - self._started_perf) * 1000.0
        metadata = dict(self.metadata)
        if exc_type is not None:
            metadata.setdefault("error", getattr(exc_type, "__name__", str(exc_type)))
        self.record = self.store.add(
            tag=self.tag,
            name=self.name,
            started_at=self._started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        return False


_DEFAULT_STORE = BenchmarkStore()


def timed(
    tag: BenchmarkTag,
    *,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    store: Optional[BenchmarkStore] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        metric_name = name or func.__qualname__

        if hasattr(func, "__code__") and func.__code__.co_flags & 0x80:
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with Timer(tag, name=metric_name, metadata=metadata, store=store):
                    return await func(*args, **kwargs)

            return async_wrapper

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with Timer(tag, name=metric_name, metadata=metadata, store=store):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def span(
    tag: BenchmarkTag,
    *,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    store: Optional[BenchmarkStore] = None,
) -> Timer:
    return Timer(tag, name=name, metadata=metadata, store=store)


def record(
    tag: BenchmarkTag,
    *,
    name: str,
    started_at: datetime,
    ended_at: datetime,
    metadata: Optional[Dict[str, Any]] = None,
    store: Optional[BenchmarkStore] = None,
) -> BenchmarkRecord:
    started = _normalize_time(started_at)
    ended = _normalize_time(ended_at)
    if started is None or ended is None:
        raise ValueError("started_at and ended_at are required")
    duration_ms = (ended - started).total_seconds() * 1000.0
    return (store or _DEFAULT_STORE).add(
        tag=tag,
        name=name,
        started_at=started,
        ended_at=ended,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def query(
    *,
    tag: Optional[BenchmarkTag] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
    top_n: Optional[int] = None,
    slowest_first: bool = True,
) -> List[BenchmarkRecord]:
    return _DEFAULT_STORE.query(
        tag=tag,
        start_time=start_time,
        end_time=end_time,
        top_n=top_n,
        slowest_first=slowest_first,
    )


def export_json(
    path: Optional[Union[str, Path]] = None,
    *,
    tag: Optional[BenchmarkTag] = None,
    start_time: Optional[Union[datetime, float, int]] = None,
    end_time: Optional[Union[datetime, float, int]] = None,
    top_n: Optional[int] = None,
    slowest_first: bool = True,
    indent: int = 2,
) -> str:
    return _DEFAULT_STORE.export_json(
        path,
        tag=tag,
        start_time=start_time,
        end_time=end_time,
        top_n=top_n,
        slowest_first=slowest_first,
        indent=indent,
    )


def clear() -> None:
    _DEFAULT_STORE.clear()


def records() -> List[BenchmarkRecord]:
    return list(query())


def subscribe(callback: Callable[[BenchmarkRecord], None]) -> None:
    _DEFAULT_STORE.subscribe(callback)


def unsubscribe(callback: Callable[[BenchmarkRecord], None]) -> None:
    _DEFAULT_STORE.unsubscribe(callback)


__all__ = [
    "BenchmarkRecord",
    "BenchmarkStore",
    "BenchmarkTag",
    "Timer",
    "clear",
    "export_json",
    "query",
    "record",
    "records",
    "span",
    "subscribe",
    "timed",
    "unsubscribe",
]
