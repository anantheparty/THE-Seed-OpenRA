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
        """Drain all pending items from the queue (non-blocking)."""
        items: list[QueueItem] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    async def wait_for_wake(self, timeout: float) -> bool:
        """Wait for a wake signal or timeout.

        Args:
            timeout: Max seconds to wait (review_interval).

        Returns:
            True if woken by an event, False if timed out.
        """
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
