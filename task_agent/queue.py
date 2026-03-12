"""Async queue for Signal and Event delivery to a Task Agent.

Buffers incoming Signals and Events while the agent sleeps. On wake,
the agent drains the queue and processes all pending items.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Union

from models import Event, ExpertSignal

# Items that can arrive in the queue
QueueItem = Union[ExpertSignal, Event]


class AgentQueue:
    """Async queue that buffers Signals/Events and provides a wake trigger."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._wake_event = asyncio.Event()

    def push(self, item: QueueItem) -> None:
        """Push a Signal or Event into the queue and trigger wake."""
        self._queue.put_nowait(item)
        self._wake_event.set()

    def drain(self) -> list[QueueItem]:
        """Drain all pending items from the queue (non-blocking).

        Filters out review sentinels so the agent only sees real items.
        """
        items: list[QueueItem] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if not isinstance(item, _ReviewSentinel):
                    items.append(item)
            except asyncio.QueueEmpty:
                break
        return items

    async def wait_for_wake(self, timeout: float) -> bool:
        """Wait for a wake signal or timeout.

        Args:
            timeout: Max seconds to wait (review_interval).

        Returns:
            True if woken by an event/item, False if timed out.
        """
        # Check queue first — if items are already pending (including
        # sentinels from trigger_review), return immediately. This
        # prevents the race where set() fires before we enter wait().
        if not self._queue.empty():
            return True
        self._wake_event.clear()
        # Double-check after clear — item may have arrived between
        # the empty() check and clear().
        if not self._queue.empty():
            return True
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def trigger_review(self) -> None:
        """Trigger a review wake without pushing a real item.

        Race-free: sets the wake event AND puts a sentinel in the queue.
        The sentinel is filtered out by drain() so the agent sees an
        empty queue — which it handles as a timer-triggered review.
        """
        self._queue.put_nowait(_REVIEW_SENTINEL)
        self._wake_event.set()

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()


class _ReviewSentinel:
    """Sentinel object for review wake — filtered out by drain()."""
    pass


_REVIEW_SENTINEL = _ReviewSentinel()
