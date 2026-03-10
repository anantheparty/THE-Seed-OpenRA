"""GameLoop — single-threaded 10Hz main loop (design.md §2).

Tick sequence:
  1. WorldModel.refresh() — layered refresh + event detection
  2. Collect events from WorldModel
  3. Forward events to Kernel (route_events)
  4. Tick due Jobs (per Job tick_interval)
  5. Push dashboard updates (placeholder)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from benchmark import span as bm_span
from experts.base import BaseJob
from models import Event

logger = logging.getLogger(__name__)


class WorldModelInterface(Protocol):
    """Minimal WorldModel interface needed by GameLoop."""

    def refresh(self, *, now: Optional[float] = None, force: bool = False) -> list[Event]: ...
    def detect_events(self, *, clear: bool = True) -> list[Event]: ...


class KernelInterface(Protocol):
    """Minimal Kernel interface needed by GameLoop."""

    def route_events(self, events: list[Event]) -> None: ...


# Dashboard push callback: (tick_number, timestamp) -> None
DashboardCallback = Callable[[int, float], None]


@dataclass
class GameLoopConfig:
    """Configuration for the GameLoop."""

    tick_hz: float = 10.0  # ticks per second (10Hz default)

    @property
    def tick_interval(self) -> float:
        """Seconds between ticks."""
        return 1.0 / self.tick_hz


@dataclass
class _RegisteredJob:
    """Internal tracking of a registered Job and its tick schedule."""

    job: BaseJob
    last_tick_at: float = 0.0


class GameLoop:
    """Single-threaded main loop that drives the entire game system.

    Start order: GameAPI → WorldModel → Kernel → GameLoop
    """

    def __init__(
        self,
        world_model: WorldModelInterface,
        kernel: KernelInterface,
        config: Optional[GameLoopConfig] = None,
        dashboard_callback: Optional[DashboardCallback] = None,
    ) -> None:
        self.world_model = world_model
        self.kernel = kernel
        self.config = config or GameLoopConfig()
        self._dashboard_callback = dashboard_callback

        self._jobs: dict[str, _RegisteredJob] = {}
        self._running = False
        self._tick_count = 0
        self._started_at: Optional[float] = None

    # --- Job registration ---

    def register_job(self, job: BaseJob) -> None:
        """Register a Job to be ticked by the GameLoop."""
        self._jobs[job.job_id] = _RegisteredJob(job=job)
        logger.debug("Job registered: %s (interval=%.2fs)", job.job_id, job.tick_interval)

    def unregister_job(self, job_id: str) -> None:
        """Remove a Job from the tick schedule."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            logger.debug("Job unregistered: %s", job_id)

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the main loop. Runs until stop() is called."""
        self._running = True
        self._started_at = time.time()
        self._tick_count = 0
        logger.info("GameLoop started at %.1f Hz", self.config.tick_hz)

        try:
            while self._running:
                tick_start = time.monotonic()

                await self._tick()

                # Sleep to maintain target tick rate
                elapsed = time.monotonic() - tick_start
                sleep_time = self.config.tick_interval - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                elif elapsed > self.config.tick_interval * 2:
                    logger.warning(
                        "Tick %d took %.1fms (budget %.1fms)",
                        self._tick_count,
                        elapsed * 1000,
                        self.config.tick_interval * 1000,
                    )
        except asyncio.CancelledError:
            logger.info("GameLoop cancelled")
            raise
        finally:
            self._running = False
            logger.info("GameLoop stopped after %d ticks", self._tick_count)

    def stop(self) -> None:
        """Signal the loop to stop after the current tick."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # --- Core tick ---

    async def _tick(self) -> None:
        """Execute one tick of the game loop."""
        self._tick_count += 1
        now = time.time()

        with bm_span("job_tick", name=f"game_loop:tick_{self._tick_count}"):
            # 1. WorldModel refresh (layered) + event detection
            events = self.world_model.refresh(now=now)

            # 2. Collect any additional buffered events
            buffered = self.world_model.detect_events(clear=True)
            all_events = events + buffered if buffered else events

            # 3. Forward events to Kernel
            if all_events:
                self.kernel.route_events(all_events)

            # 4. Tick due Jobs
            self._tick_jobs(now)

            # 5. Dashboard push (placeholder)
            if self._dashboard_callback:
                self._dashboard_callback(self._tick_count, now)

    def _tick_jobs(self, now: float) -> None:
        """Tick all registered Jobs that are due."""
        for reg in list(self._jobs.values()):
            job = reg.job
            # Skip if not enough time has passed since last tick
            if now - reg.last_tick_at < job.tick_interval:
                continue
            # Skip terminated jobs
            if job.status.value in ("succeeded", "failed", "aborted"):
                continue

            reg.last_tick_at = now
            try:
                job.do_tick()
            except Exception:
                logger.exception("Job tick error: %s", job.job_id)
