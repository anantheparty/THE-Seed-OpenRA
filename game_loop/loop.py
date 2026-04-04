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
from logging_system import get_logger
from models import Event, JobStatus, SignalKind
from task_agent.queue import AgentQueue

logger = logging.getLogger(__name__)
slog = get_logger("game_loop")


class WorldModelInterface(Protocol):
    """Minimal WorldModel interface needed by GameLoop."""

    def refresh(self, *, now: Optional[float] = None, force: bool = False) -> list[Event]: ...
    def detect_events(self, *, clear: bool = True) -> list[Event]: ...
    def refresh_health(self) -> dict[str, Any]: ...


class KernelInterface(Protocol):
    """Minimal Kernel interface needed by GameLoop."""

    def route_events(self, events: list[Event]) -> None: ...
    def tick(self, *, now: Optional[float] = None) -> int: ...
    def push_player_notification(
        self,
        notification_type: str,
        content: str,
        *,
        data: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None: ...


class QueueManagerInterface(Protocol):
    def tick(self, *, now: float) -> None: ...


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
    last_status: str = ""  # last known status before the tick; used to detect terminal transitions


@dataclass
class _RegisteredAgent:
    """Internal tracking of a Task Agent and its review_interval schedule."""

    agent_queue: AgentQueue
    review_interval: float  # seconds
    last_review_at: float = 0.0


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
        queue_manager: Optional[QueueManagerInterface] = None,
    ) -> None:
        self.world_model = world_model
        self.kernel = kernel
        self.config = config or GameLoopConfig()
        self._dashboard_callback = dashboard_callback
        self._queue_manager = queue_manager

        self._jobs: dict[str, _RegisteredJob] = {}
        self._agents: dict[str, _RegisteredAgent] = {}
        self._running = False
        self._tick_count = 0
        self._started_at: Optional[float] = None
        self._world_stale_active = False
        self._world_stale_notified = False
        self._world_stale_since: Optional[float] = None   # timestamp when staleness began
        self._world_stale_escalated = False               # escalation (>30s) sent once
        self._paused_for_recovery: set[str] = set()

    # --- Job registration ---

    def register_job(self, job: BaseJob) -> None:
        """Register a Job to be ticked by the GameLoop."""
        self._jobs[job.job_id] = _RegisteredJob(job=job)
        if self._world_stale_active and job.status == JobStatus.RUNNING and not job.is_paused:
            job.pause()
            self._paused_for_recovery.add(job.job_id)
        logger.debug("Job registered: %s (interval=%.2fs)", job.job_id, job.tick_interval)

    def unregister_job(self, job_id: str) -> None:
        """Remove a Job from the tick schedule."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._paused_for_recovery.discard(job_id)
            logger.debug("Job unregistered: %s", job_id)

    # --- Agent registration (1.8 review_interval) ---

    def register_agent(self, task_id: str, agent_queue: AgentQueue, review_interval: float = 10.0) -> None:
        """Register a Task Agent for periodic review_interval wake."""
        self._agents[task_id] = _RegisteredAgent(
            agent_queue=agent_queue,
            review_interval=review_interval,
            last_review_at=time.time(),  # Don't trigger immediately on first tick
        )
        logger.debug("Agent registered: %s (review_interval=%.1fs)", task_id, review_interval)

    def unregister_agent(self, task_id: str) -> None:
        """Remove a Task Agent from review scheduling."""
        if task_id in self._agents:
            del self._agents[task_id]
            logger.debug("Agent unregistered: %s", task_id)

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the main loop. Runs until stop() is called."""
        self._running = True
        self._started_at = time.time()
        self._tick_count = 0
        logger.info("GameLoop started at %.1f Hz", self.config.tick_hz)
        slog.info("GameLoop started", event="game_loop_started", tick_hz=self.config.tick_hz)

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
            slog.info("GameLoop stopped", event="game_loop_stopped", tick_count=self._tick_count)

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
            # 1. WorldModel refresh (layered refresh + internal event detection)
            await asyncio.to_thread(self.world_model.refresh, now=now)

            # 2. Collect events (single source — avoids double-counting)
            events = self.world_model.detect_events(clear=True)

            # 3. Forward events to Kernel
            if events:
                self.kernel.route_events(events)
                slog.debug("Forwarded WorldModel events to Kernel", event="events_forwarded", tick=self._tick_count, event_count=len(events))

            # 3b. Kernel tick (pending question timeout scan)
            self.kernel.tick(now=now)

            # 3c. Recovery / stale handling
            self._handle_world_model_health(now)

            # 4. Tick due Jobs
            await self._tick_jobs(now)

            # 5. Check review_interval for Task Agents (1.8)
            self._check_agent_reviews(now)

            # 6. Shared queue manager
            if self._queue_manager is not None:
                self._queue_manager.tick(now=now)

            # 7. Dashboard push (placeholder)
            if self._dashboard_callback:
                self._dashboard_callback(self._tick_count, now)

    _TERMINAL_STATUSES: frozenset = frozenset({"succeeded", "failed", "aborted"})

    async def _tick_jobs(self, now: float) -> None:
        """Tick all registered Jobs that are due."""
        for reg in list(self._jobs.values()):
            job = reg.job
            # Skip if not enough time has passed since last tick
            if now - reg.last_tick_at < job.tick_interval:
                continue
            # Skip terminated jobs
            if job.status.value in self._TERMINAL_STATUSES:
                continue

            prev_status = reg.last_status
            reg.last_tick_at = now
            try:
                await asyncio.to_thread(job.do_tick)
            except Exception as exc:
                logger.exception("Job tick error: %s", job.job_id)
                slog.error("Job tick raised exception", event="job_tick_failed", job_id=job.job_id, error=str(exc))
                job.status = JobStatus.FAILED
                self._paused_for_recovery.discard(job.job_id)
                job.emit_signal(
                    kind=SignalKind.TASK_COMPLETE,
                    summary=f"Job {job.job_id} failed: {exc}",
                    result="failed",
                    data={"error": str(exc), "error_type": type(exc).__name__},
                )

            new_status = job.status.value
            reg.last_status = new_status

            # If the job just became terminal, immediately wake its Task Agent
            # from the event-loop thread. asyncio.Event.set() from a to_thread
            # callback is not reliably thread-safe; firing trigger_review() here
            # (after the thread returns) ensures the agent wakes promptly instead
            # of waiting for the next review_interval.
            if new_status in self._TERMINAL_STATUSES and prev_status not in self._TERMINAL_STATUSES:
                reg_agent = self._agents.get(job.task_id)
                if reg_agent is not None:
                    reg_agent.agent_queue.trigger_review()
                    slog.debug(
                        "Job terminal — immediate agent wake",
                        event="job_terminal_wake",
                        job_id=job.job_id,
                        task_id=job.task_id,
                        status=new_status,
                    )

    def _check_agent_reviews(self, now: float) -> None:
        """Wake Task Agents whose review_interval has elapsed.

        Pushes a lightweight review Event into the AgentQueue. This is
        race-free because push() enqueues an item (persistent) AND sets
        the wake event — even if the agent re-clears the event before
        waiting, the queued item ensures drain() returns non-empty.
        """
        for task_id, reg in list(self._agents.items()):
            if now - reg.last_review_at >= reg.review_interval:
                reg.last_review_at = now
                reg.agent_queue.trigger_review()
                logger.debug("Review wake for agent %s", task_id)
                slog.debug("Triggered periodic task-agent review", event="agent_review_wake", task_id=task_id)

    _STALE_ESCALATION_TIMEOUT: float = 30.0  # seconds before escalated disconnect notification

    def _handle_world_model_health(self, now: float) -> None:
        health = self.world_model.refresh_health()
        if not health.get("stale", False):
            if self._world_stale_active:
                self._resume_jobs_after_recovery()
            self._world_stale_active = False
            self._world_stale_notified = False
            self._world_stale_since = None
            self._world_stale_escalated = False
            return

        if not self._world_stale_active:
            self._pause_jobs_for_recovery()
            self._world_stale_active = True
            self._world_stale_since = now

        if (
            health.get("consecutive_failures", 0) >= health.get("failure_threshold", 3)
            and not self._world_stale_notified
        ):
            self.kernel.push_player_notification(
                "world_model_stale",
                "WorldModel refresh failed repeatedly; using stale snapshot.",
                data={
                    "consecutive_failures": health.get("consecutive_failures", 0),
                    "last_error": health.get("last_error"),
                },
                timestamp=now,
            )
            self._world_stale_notified = True

        # Escalation: if disconnected for >30s, send an urgent follow-up notification
        stale_since = self._world_stale_since or now
        if (
            not self._world_stale_escalated
            and (now - stale_since) >= self._STALE_ESCALATION_TIMEOUT
        ):
            self.kernel.push_player_notification(
                "world_model_stale_critical",
                f"GameAPI 已持续断连 {int(now - stale_since)}s，请检查游戏进程是否正常运行。",
                data={
                    "disconnected_for_s": round(now - stale_since, 1),
                    "last_error": health.get("last_error"),
                },
                timestamp=now,
            )
            self._world_stale_escalated = True

    def _pause_jobs_for_recovery(self) -> None:
        for reg in self._jobs.values():
            job = reg.job
            if job.status != JobStatus.RUNNING or job.is_paused:
                continue
            job.pause()
            self._paused_for_recovery.add(job.job_id)

    def _resume_jobs_after_recovery(self) -> None:
        for job_id in list(self._paused_for_recovery):
            reg = self._jobs.get(job_id)
            if reg is None:
                self._paused_for_recovery.discard(job_id)
                continue
            reg.job.resume()
            self._paused_for_recovery.discard(job_id)
