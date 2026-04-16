"""Helpers for unit-request bootstrap production and reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableSet
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from logging_system import get_logger
from models import EconomyJobConfig, JobStatus, TaskStatus, UnitReservation, UnitRequest
from models.enums import ReservationStatus

slog = get_logger("kernel")


class ControllerLike(Protocol):
    job_id: str
    task_id: str
    expert_type: str
    status: JobStatus
    resources: list[str]

    def patch(self, params: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True, slots=True)
class BootstrapReconcileTarget:
    """Computed target for an existing bootstrap EconomyJob."""

    desired_remaining: int
    current_target: int
    new_target: int
    issued_count: int
    produced_count: int
    clear_job: bool = False


@dataclass(frozen=True, slots=True)
class BootstrapStartDecision:
    """Decision inputs for starting fast-path bootstrap production."""

    remaining: int
    unit_type: Optional[str]
    queue_type: Optional[str]
    can_issue_now: bool = False


@dataclass(frozen=True, slots=True)
class BootstrapStartOutcome:
    """Observed outcome after the kernel attempts fast-path bootstrap."""

    decision: BootstrapStartDecision
    started: bool = False

    @property
    def notify_capability(self) -> bool:
        return self.decision.remaining > 0 and not self.started


def active_bootstrap_job_id(
    req: UnitRequest,
    reservation: Optional[UnitReservation],
) -> Optional[str]:
    """Return the active bootstrap job id recorded on the request/reservation pair."""
    return req.bootstrap_job_id or (reservation.bootstrap_job_id if reservation is not None else None)


def decide_bootstrap_start(
    req: UnitRequest,
    *,
    infer_unit_type: Callable[[str, str], tuple[Optional[str], Optional[str]]],
    production_readiness_for: Callable[[str, str], dict[str, Any]],
) -> BootstrapStartDecision:
    """Decide whether a request is eligible for fast-path bootstrap production."""
    remaining = max(req.count - req.fulfilled, 0)
    if remaining <= 0:
        return BootstrapStartDecision(remaining=0, unit_type=None, queue_type=None, can_issue_now=False)
    unit_type, queue_type = infer_unit_type(req.category, req.hint)
    if unit_type is None or queue_type is None:
        return BootstrapStartDecision(
            remaining=remaining,
            unit_type=unit_type,
            queue_type=queue_type,
            can_issue_now=False,
        )
    readiness = production_readiness_for(unit_type, queue_type)
    return BootstrapStartDecision(
        remaining=remaining,
        unit_type=unit_type,
        queue_type=queue_type,
        can_issue_now=bool(readiness.get("can_issue_now")),
    )


def build_bootstrap_config(
    req: UnitRequest,
    *,
    unit_type: str,
    queue_type: str,
    reservation_id: str,
) -> EconomyJobConfig:
    """Build an EconomyJob config for fast-path request bootstrap."""
    return EconomyJobConfig(
        unit_type=unit_type,
        count=max(req.count - req.fulfilled, 0),
        queue_type=queue_type,
        request_id=req.request_id,
        reservation_id=reservation_id,
    )


def record_bootstrap_started(
    req: UnitRequest,
    reservation: UnitReservation,
    *,
    job_id: str,
    task_id: str,
    now: Callable[[], float],
) -> None:
    """Record bootstrap ownership on both request and reservation."""
    req.bootstrap_job_id = job_id
    req.bootstrap_task_id = task_id
    reservation.bootstrap_job_id = job_id
    reservation.bootstrap_task_id = task_id
    if reservation.status == ReservationStatus.PENDING and req.fulfilled > 0:
        reservation.status = ReservationStatus.PARTIAL
    reservation.updated_at = now()


def build_bootstrap_player_message(
    req: UnitRequest,
    *,
    unit_type: str,
) -> str:
    """Build the capability-facing player message for fast-path bootstrap."""
    remaining = max(req.count - req.fulfilled, 0)
    return (
        f"[Kernel fast-path] 已为 Task#{req.task_label} 启动生产: "
        f"{unit_type}×{remaining} (REQ-{req.request_id})"
    )


def compute_bootstrap_reconcile_target(
    req: UnitRequest,
    controller: Any,
) -> Optional[BootstrapReconcileTarget]:
    """Return a shrink/clear target for an active bootstrap EconomyJob."""
    if getattr(controller, "expert_type", None) != "EconomyExpert":
        return None
    config = getattr(controller, "config", None)
    issued_count = int(getattr(controller, "issued_count", 0) or 0)
    produced_count = int(getattr(controller, "produced_count", 0) or 0)
    if config is None or not hasattr(config, "count"):
        return None
    current_target = int(getattr(config, "count", 0) or 0)
    desired_remaining = max(req.count - req.fulfilled, 0)
    new_target = max(desired_remaining, issued_count, produced_count, 0)
    if new_target >= current_target:
        return None
    return BootstrapReconcileTarget(
        desired_remaining=desired_remaining,
        current_target=current_target,
        new_target=new_target,
        issued_count=issued_count,
        produced_count=produced_count,
        clear_job=(new_target == 0 and issued_count == 0),
    )


def reconcile_request_bootstrap(
    req: UnitRequest,
    *,
    reservation_for_request: Callable[[UnitRequest], Optional[UnitReservation]],
    jobs: Mapping[str, ControllerLike],
    is_terminal_status: Callable[[JobStatus], bool],
    clear_request_bootstrap_refs: Callable[[UnitRequest, Optional[UnitReservation]], None],
    release_job_resources: Callable[[ControllerLike], None],
    resource_loss_notified: MutableSet[str],
    now: Callable[[], float],
) -> None:
    """Shrink or clear bootstrap production after new idle assignments."""
    reservation = reservation_for_request(req)
    bootstrap_job_id = active_bootstrap_job_id(req, reservation)
    if not bootstrap_job_id:
        return
    controller = jobs.get(bootstrap_job_id)
    if controller is None:
        clear_request_bootstrap_refs(req, reservation)
        return
    if is_terminal_status(controller.status):
        remaining = max(req.count - req.fulfilled, 0)
        if controller.status == JobStatus.SUCCEEDED and remaining > 0:
            return
        clear_request_bootstrap_refs(req, reservation)
        return
    reconcile_target = compute_bootstrap_reconcile_target(req, controller)
    if reconcile_target is None:
        return

    if reconcile_target.clear_job:
        if controller.resources:
            release_job_resources(controller)
        controller.resources = []
        controller.status = JobStatus.ABORTED
        resource_loss_notified.discard(bootstrap_job_id)
        clear_request_bootstrap_refs(req, reservation)
        slog.info(
            "Bootstrap job cleared after idle fulfillment",
            event="bootstrap_reconciled",
            request_id=req.request_id,
            reservation_id=reservation.reservation_id if reservation is not None else "",
            job_id=bootstrap_job_id,
            desired_remaining=reconcile_target.desired_remaining,
            previous_target=reconcile_target.current_target,
            new_target=0,
            mode="clear",
        )
        return

    controller.patch({"count": reconcile_target.new_target})
    if reservation is not None:
        reservation.updated_at = now()
    slog.info(
        "Bootstrap job reconciled after idle fulfillment",
        event="bootstrap_reconciled",
        request_id=req.request_id,
        reservation_id=reservation.reservation_id if reservation is not None else "",
        job_id=bootstrap_job_id,
        desired_remaining=reconcile_target.desired_remaining,
        previous_target=reconcile_target.current_target,
        new_target=reconcile_target.new_target,
        issued_count=reconcile_target.issued_count,
        produced_count=reconcile_target.produced_count,
        mode="shrink",
    )


def bootstrap_production_for_request(
    req: UnitRequest,
    *,
    infer_unit_type: Callable[[str, str], tuple[Optional[str], Optional[str]]],
    production_readiness_for: Callable[[str, str], dict[str, Any]],
    ensure_reservation_for_request: Callable[[UnitRequest, str], UnitReservation],
    ensure_capability_task: Callable[[], Optional[str]],
    tasks: Mapping[str, Any],
    start_job: Callable[[str, str, EconomyJobConfig], Any],
    inject_player_message: Callable[[str, str], bool],
    now: Callable[[], float],
) -> BootstrapStartOutcome:
    """Start shared bootstrap production for an unfulfilled request."""
    decision = decide_bootstrap_start(
        req,
        infer_unit_type=infer_unit_type,
        production_readiness_for=production_readiness_for,
    )
    if decision.remaining <= 0:
        return BootstrapStartOutcome(decision=decision, started=False)
    if decision.unit_type is None or decision.queue_type is None:
        return BootstrapStartOutcome(decision=decision, started=False)
    unit_type = decision.unit_type
    queue_type = decision.queue_type
    reservation = ensure_reservation_for_request(req, unit_type)
    reservation.updated_at = now()
    if not decision.can_issue_now:
        return BootstrapStartOutcome(decision=decision, started=False)

    bootstrap_task_id = ensure_capability_task()
    capability_task = tasks.get(bootstrap_task_id)
    if capability_task is None or capability_task.status not in {
        TaskStatus.PENDING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING,
    }:
        return BootstrapStartOutcome(decision=decision, started=False)

    config = build_bootstrap_config(
        req,
        unit_type=unit_type,
        queue_type=queue_type,
        reservation_id=reservation.reservation_id,
    )
    try:
        job = start_job(bootstrap_task_id, "EconomyExpert", config)
        record_bootstrap_started(
            req,
            reservation,
            job_id=job.job_id,
            task_id=bootstrap_task_id,
            now=now,
        )
        slog.info(
            "Bootstrap production for request",
            event="bootstrap_production",
            request_id=req.request_id,
            unit_type=unit_type,
            count=decision.remaining,
            job_id=job.job_id,
            bootstrap_task_id=bootstrap_task_id,
        )
    except Exception as exc:
        slog.warning(
            "Bootstrap production failed",
            event="bootstrap_production_failed",
            request_id=req.request_id,
            error=str(exc),
        )
        return BootstrapStartOutcome(decision=decision, started=False)

    if bootstrap_task_id:
        inject_player_message(
            bootstrap_task_id,
            build_bootstrap_player_message(req, unit_type=unit_type),
        )
    return BootstrapStartOutcome(decision=decision, started=True)
