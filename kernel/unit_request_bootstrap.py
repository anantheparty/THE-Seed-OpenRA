"""Helpers for unit-request bootstrap production and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from models import EconomyJobConfig, UnitReservation, UnitRequest
from models.enums import ReservationStatus


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
