from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import pytest
from kernel.unit_request_bootstrap import (
    active_bootstrap_job_id,
    build_bootstrap_config,
    build_bootstrap_player_message,
    BootstrapStartOutcome,
    compute_bootstrap_reconcile_target,
    decide_bootstrap_start,
    reconcile_request_bootstrap,
    record_bootstrap_started,
)
from models import JobStatus, ReservationStatus, UnitReservation, UnitRequest


class _Controller:
    def __init__(
        self,
        *,
        expert_type: str = "EconomyExpert",
        count: int = 5,
        issued_count: int = 0,
        produced_count: int = 0,
    ) -> None:
        self.expert_type = expert_type
        self.config = type("Config", (), {"count": count})()
        self.issued_count = issued_count
        self.produced_count = produced_count


def test_reconcile_request_bootstrap_keeps_succeeded_refs_until_request_is_credited() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=1,
        urgency="high",
        hint="tank",
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        unit_type="3tnk",
        count=1,
        status=ReservationStatus.PENDING,
    )
    record_bootstrap_started(
        req,
        reservation,
        job_id="j_boot",
        task_id="t_cap",
        now=lambda: 100.0,
    )
    controller = _Controller(count=1)
    controller.status = JobStatus.SUCCEEDED
    clear_calls: list[tuple[str, str]] = []

    reconcile_request_bootstrap(
        req,
        reservation_for_request=lambda current: reservation if current.request_id == req.request_id else None,
        jobs={"j_boot": controller},
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        clear_request_bootstrap_refs=lambda current, current_reservation: clear_calls.append(
            (current.request_id, current_reservation.reservation_id if current_reservation is not None else "")
        ),
        release_job_resources=lambda controller: None,
        resource_loss_notified=set(),
        now=lambda: 101.0,
    )

    assert clear_calls == []
    assert req.bootstrap_job_id == "j_boot"
    assert reservation.bootstrap_job_id == "j_boot"


def test_record_bootstrap_started_updates_request_and_reservation() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=4,
        urgency="high",
        hint="tank",
        fulfilled=1,
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        unit_type="3tnk",
        count=4,
        status=ReservationStatus.PENDING,
    )

    record_bootstrap_started(
        req,
        reservation,
        job_id="j_boot",
        task_id="t_cap",
        now=lambda: 123.0,
    )

    assert req.bootstrap_job_id == "j_boot"
    assert req.bootstrap_task_id == "t_cap"
    assert reservation.bootstrap_job_id == "j_boot"
    assert reservation.bootstrap_task_id == "t_cap"
    assert reservation.status == ReservationStatus.PARTIAL
    assert reservation.updated_at == 123.0
    assert active_bootstrap_job_id(req, reservation) == "j_boot"


def test_build_bootstrap_config_and_message_reflect_remaining_count() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="007",
        task_summary="attack",
        category="vehicle",
        count=5,
        urgency="high",
        hint="重坦",
        fulfilled=2,
    )

    config = build_bootstrap_config(
        req,
        unit_type="3tnk",
        queue_type="Vehicle",
        reservation_id="res_1",
    )

    assert config.unit_type == "3tnk"
    assert config.count == 3
    assert config.queue_type == "Vehicle"
    assert config.request_id == "req_1"
    assert config.reservation_id == "res_1"
    assert build_bootstrap_player_message(req, unit_type="3tnk") == (
        "[Kernel fast-path] 已为 Task#007 启动生产: 3tnk×3 (REQ-req_1)"
    )


def test_decide_bootstrap_start_tracks_inference_and_readiness() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="007",
        task_summary="attack",
        category="vehicle",
        count=5,
        urgency="high",
        hint="重坦",
        fulfilled=2,
    )

    decision = decide_bootstrap_start(
        req,
        infer_unit_type=lambda category, hint: ("3tnk", "Vehicle"),
        production_readiness_for=lambda unit_type, queue_type: {"can_issue_now": True},
    )

    assert decision.remaining == 3
    assert decision.unit_type == "3tnk"
    assert decision.queue_type == "Vehicle"
    assert decision.can_issue_now is True
    assert BootstrapStartOutcome(decision=decision, started=True).notify_capability is False
    assert BootstrapStartOutcome(decision=decision, started=False).notify_capability is True


def test_decide_bootstrap_start_handles_unknown_or_unready_units() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="007",
        task_summary="attack",
        category="aircraft",
        count=2,
        urgency="high",
        hint="对地攻击机",
    )

    unknown = decide_bootstrap_start(
        req,
        infer_unit_type=lambda category, hint: (None, None),
        production_readiness_for=lambda unit_type, queue_type: {"can_issue_now": True},
    )
    assert unknown.remaining == 2
    assert unknown.unit_type is None
    assert unknown.queue_type is None
    assert unknown.can_issue_now is False

    req.category = "vehicle"
    unready = decide_bootstrap_start(
        req,
        infer_unit_type=lambda category, hint: ("3tnk", "Vehicle"),
        production_readiness_for=lambda unit_type, queue_type: {"can_issue_now": False},
    )
    assert unready.unit_type == "3tnk"
    assert unready.queue_type == "Vehicle"
    assert unready.can_issue_now is False


def test_compute_bootstrap_reconcile_target_shrinks_and_clears() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=5,
        urgency="high",
        hint="tank",
        fulfilled=1,
    )

    shrink = compute_bootstrap_reconcile_target(
        req,
        _Controller(count=5, issued_count=0, produced_count=0),
    )
    assert shrink is not None
    assert shrink.current_target == 5
    assert shrink.desired_remaining == 4
    assert shrink.new_target == 4
    assert shrink.clear_job is False

    req.fulfilled = 5
    clear = compute_bootstrap_reconcile_target(
        req,
        _Controller(count=1, issued_count=0, produced_count=0),
    )
    assert clear is not None
    assert clear.new_target == 0
    assert clear.clear_job is True

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
