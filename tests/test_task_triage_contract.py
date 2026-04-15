"""Task-triage builder contract tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models import TaskMessageType
from task_triage import build_live_task_payload


def test_build_live_task_payload_uses_task_specific_message_lookup():
    class FakeTask:
        task_id = "t_demo"
        raw_text = "test"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = False

    class FakeMessage:
        def __init__(self, task_id: str, content: str):
            self.task_id = task_id
            self.content = content
            self.type = TaskMessageType.TASK_WARNING

    calls: list[tuple[str, ...]] = []

    def list_pending_questions():
        return []

    def list_task_messages(task_id: str):
        calls.append((task_id,))
        return [FakeMessage(task_id, "warn")]

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=list_pending_questions,
        list_task_messages=list_task_messages,
        world_stale=False,
        log_session_dir=None,
    )

    assert calls == [("t_demo",)]
    assert payload["task_id"] == "t_demo"
    assert payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_uses_task_specific_message_lookup")


def test_build_live_task_payload_uses_latest_info_when_no_other_triage_signal():
    class FakeTask:
        task_id = "t_info"
        raw_text = "test"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "002"
        is_capability = False

    class FakeMessage:
        def __init__(self, task_id: str, content: str):
            self.task_id = task_id
            self.content = content
            self.type = TaskMessageType.TASK_INFO

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [FakeMessage(task_id, "缺少战车工厂，等待能力层补前置")],
        world_stale=False,
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "running"
    assert payload["triage"]["status_line"] == "缺少战车工厂，等待能力层补前置"
    assert payload["triage"]["waiting_reason"] == ""
    print("  PASS: build_live_task_payload_uses_latest_info_when_no_other_triage_signal")


def test_build_live_task_payload_surfaces_workflow_request_units_first() -> None:
    class FakeTask:
        task_id = "t_flow"
        raw_text = "整点步兵，探索一下地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["workflow_template"] == "produce_units_then_recon"
    assert triage["workflow_phase"] == "request_units_first"
    assert "先请求执行单位" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_workflow_request_units_first")


def test_build_live_task_payload_surfaces_attack_workflow_request_units_first() -> None:
    class FakeTask:
        task_id = "t_flow"
        raw_text = "整一大批步兵和防空车，准备一轮进攻"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["workflow_template"] == "produce_units_then_attack"
    assert triage["workflow_phase"] == "request_units_first"
    assert "先请求执行单位" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_attack_workflow_request_units_first")


def test_build_live_task_payload_keeps_unit_pipeline_truth_while_exposing_workflow_waiting_for_units() -> None:
    class FakeTask:
        task_id = "t_flow"
        raw_text = "整点步兵，探索一下地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_flow",
                    "task_label": "004",
                    "category": "infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "步兵",
                    "reason": "waiting_dispatch",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["workflow_template"] == "produce_units_then_recon"
    assert triage["workflow_phase"] == "waiting_for_units"
    assert "等待能力模块分发单位：步兵 × 1" in triage["status_line"]
    assert "工作流：" not in triage["status_line"]
    print("  PASS: build_live_task_payload_keeps_unit_pipeline_truth_while_exposing_workflow_waiting_for_units")


def test_build_live_task_payload_surfaces_workflow_ready_to_recon() -> None:
    class FakeTask:
        task_id = "t_flow"
        raw_text = "整点步兵，探索一下地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {
                "t_flow": {
                    "active_actor_ids": [11, 12],
                    "active_group_size": 2,
                }
            }
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["workflow_template"] == "produce_units_then_recon"
    assert triage["workflow_phase"] == "ready_to_recon"
    assert triage["has_active_group"] is True
    assert triage["active_group_size"] == 2
    assert "执行单位已到位，可开始侦察" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_workflow_ready_to_recon")


def test_build_live_task_payload_surfaces_task_owned_group_continuity() -> None:
    class FakeTask:
        task_id = "t_group"
        raw_text = "推进前线"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "007"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {
                "t_group": {
                    "active_actor_ids": [31, 32],
                    "active_group_size": 2,
                }
            }
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "running"
    assert triage["phase"] == "task_active"
    assert triage["has_active_group"] is True
    assert triage["active_group_size"] == 2
    assert "group=2" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_task_owned_group_continuity")


def test_build_live_task_payload_surfaces_workflow_recon_running() -> None:
    class FakeTask:
        task_id = "t_flow"
        raw_text = "整点步兵，探索一下地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_jobs": {
                "j_recon": {
                    "job_id": "j_recon",
                    "task_id": "t_flow",
                    "expert_type": "ReconExpert",
                    "status": "running",
                }
            }
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["workflow_template"] == "produce_units_then_recon"
    assert triage["workflow_phase"] == "recon_running"
    assert "侦察执行中" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_workflow_recon_running")


def test_build_live_task_payload_surfaces_world_sync_failure_detail():
    class FakeTask:
        task_id = "t_sync"
        raw_text = "展开基地车"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "003"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_sync={
            "stale": True,
            "consecutive_failures": 4,
            "failure_threshold": 3,
            "last_error": "actors:COMMAND_EXECUTION_ERROR",
        },
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "degraded"
    assert payload["triage"]["world_stale"] is True
    assert payload["triage"]["world_sync_failures"] == 4
    assert payload["triage"]["world_sync_failure_threshold"] == 3
    assert payload["triage"]["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert "failures=4/3" in payload["triage"]["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_surfaces_world_sync_failure_detail")


def test_build_live_task_payload_uses_last_refresh_error_fallback_for_world_sync_detail():
    class FakeTask:
        task_id = "t_sync"
        raw_text = "展开基地车"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 50
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "003"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={},
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_sync={
            "stale": True,
            "consecutive_failures": 5,
            "failure_threshold": 3,
            "last_refresh_error": "economy:COMMAND_EXECUTION_ERROR",
        },
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "degraded"
    assert payload["triage"]["world_stale"] is True
    assert payload["triage"]["world_sync_failures"] == 5
    assert payload["triage"]["world_sync_failure_threshold"] == 3
    assert payload["triage"]["world_sync_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert "failures=5/3" in payload["triage"]["status_line"]
    assert "economy:COMMAND_EXECUTION_ERROR" in payload["triage"]["status_line"]
    print("  PASS: build_live_task_payload_uses_last_refresh_error_fallback_for_world_sync_detail")


def test_build_live_task_payload_capability_triage_surfaces_blocker_detail():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "发展科技"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_other",
                    "task_label": "008",
                    "category": "vehicle",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "猛犸坦克",
                    "reason": "missing_prerequisite",
                    "prerequisites": ["fix", "stek", "weap"],
                }
            ],
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "dispatch",
                "blocker": "missing_prerequisite",
                "pending_request_count": 1,
                "blocking_request_count": 1,
                "prerequisite_gap_count": 1,
            },
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    status_line = payload["triage"]["status_line"]
    assert payload["triage"]["state"] == "running"
    assert "blocker=缺少前置建筑" in status_line
    assert "猛犸坦克 <- 维修厂 + 科技中心 + 战车工厂" in status_line
    assert payload["triage"]["blocking_reason"] == "missing_prerequisite"
    print("  PASS: build_live_task_payload_capability_triage_surfaces_blocker_detail")


def test_build_live_task_payload_capability_triage_surfaces_fulfilling_detail():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "补兵"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "fulfilling",
                "start_released_request_count": 1,
                "reinforcement_request_count": 1,
            },
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "task_id": "t_cap",
                    "unit_type": "3tnk",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "partial",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    status_line = payload["triage"]["status_line"]
    assert "ready=1" in status_line
    assert "reinforce=1" in status_line
    assert "重坦×2 (partial)" in status_line
    print("  PASS: build_live_task_payload_capability_triage_surfaces_fulfilling_detail")


def test_build_live_task_payload_capability_triage_surfaces_runtime_truth_blocker():
    class FakeTask:
        task_id = "t_cap"
        raw_text = "发展科技"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "idle",
            },
        },
        runtime_facts={
            "faction": "allied",
            "capability_truth_blocker": "faction_roster_unsupported",
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "blocked"
    assert triage["blocking_reason"] == "faction_roster_unsupported"
    assert triage["waiting_reason"] == "faction_roster_unsupported"
    assert "能力处理中：真值受限" in triage["status_line"]
    assert "blocker=阵营能力真值未覆盖" in triage["status_line"]
    assert "faction=allied demo capability roster 未覆盖" in triage["status_line"]
    print("  PASS: build_live_task_payload_capability_triage_surfaces_runtime_truth_blocker")


@pytest.mark.parametrize(
    ("blocker", "count_field", "expected"),
    [
        ("world_sync_stale", "world_sync_stale_count", "等待世界同步恢复"),
        ("deploy_required", "deploy_required_count", "等待展开基地车"),
        ("disabled_prerequisite", "disabled_prerequisite_count", "前置建筑离线"),
        ("low_power", "low_power_count", "低电受阻"),
        ("queue_blocked", "queue_blocked_count", "队列阻塞"),
        ("insufficient_funds", "insufficient_funds_count", "资金不足"),
    ],
)
def test_build_live_task_payload_capability_triage_humanizes_additional_blockers(
    blocker: str,
    count_field: str,
    expected: str,
):
    class FakeTask:
        task_id = "t_cap"
        raw_text = "能力任务"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 80
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "001"
        is_capability = True

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_cap": {"is_capability": True, "label": "001"}},
            "capability_status": {
                "task_id": "t_cap",
                "label": "001",
                "phase": "dispatch",
                "blocker": blocker,
                count_field: 1,
            },
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    assert payload["triage"]["state"] == "running"
    assert expected in payload["triage"]["status_line"]
    assert payload["triage"]["blocking_reason"] == blocker
    print(f"  PASS: build_live_task_payload_capability_triage_humanizes_additional_blockers[{blocker}]")


def test_build_live_task_payload_surfaces_task_specific_reservation_blocker_detail():
    class FakeTask:
        task_id = "t_recon"
        raw_text = "探索地图"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 60
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "004"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_recon": {"label": "004"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_recon",
                    "task_label": "004",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "fulfilled": 0,
                    "remaining_count": 2,
                    "reason": "missing_prerequisite",
                    "prerequisites": ["fix", "weap"],
                }
            ],
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "request_id": "req_1",
                    "task_id": "t_recon",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "pending",
                    "reason": "missing_prerequisite",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "blocked"
    assert triage["phase"] == "blocked"
    assert triage["waiting_reason"] == "missing_prerequisite"
    assert triage["blocking_reason"] == "missing_prerequisite"
    assert triage["reservation_preview"] == "重坦 × 2 · 缺少前置"
    assert triage["reservation_status"] == "pending"
    assert triage["remaining_count"] == 2
    assert triage["assigned_count"] == 0
    assert triage["produced_count"] == 0
    assert triage["start_released"] is False
    assert "等待能力模块补前置：重坦 × 2" in triage["status_line"]
    assert "重坦 × 2 <- 维修厂 + 战车工厂" in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_task_specific_reservation_blocker_detail")


def test_build_live_task_payload_surfaces_unit_pipeline_world_sync_detail():
    class FakeTask:
        task_id = "t_sync_req"
        raw_text = "整点步兵"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 60
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "005"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_sync_req": {"label": "005"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_1",
                    "task_id": "t_sync_req",
                    "task_label": "005",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "remaining_count": 1,
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 4,
                    "world_sync_failure_threshold": 3,
                }
            ],
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "request_id": "req_1",
                    "task_id": "t_sync_req",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "remaining_count": 1,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "economy:IGNORED_SHOULD_NOT_WIN",
                    "world_sync_consecutive_failures": 9,
                    "world_sync_failure_threshold": 7,
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "degraded"
    assert triage["phase"] == "world_sync"
    assert triage["waiting_reason"] == "world_sync_stale"
    assert triage["blocking_reason"] == "world_sync_stale"
    assert triage["reservation_preview"] == "步兵 × 1 · 等待世界同步恢复"
    assert triage["reservation_status"] == "pending"
    assert triage["remaining_count"] == 1
    assert triage["world_stale"] is True
    assert triage["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert triage["world_sync_failures"] == 4
    assert triage["world_sync_failure_threshold"] == 3
    assert "等待能力模块恢复世界同步：步兵 × 1" in triage["status_line"]
    assert "failures=4/3" in triage["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in triage["status_line"]
    assert "economy:IGNORED_SHOULD_NOT_WIN" not in triage["status_line"]
    print("  PASS: build_live_task_payload_surfaces_unit_pipeline_world_sync_detail")


def test_build_live_task_payload_marks_request_dispatch_without_fake_blocker():
    class FakeTask:
        task_id = "t_attack"
        raw_text = "进攻"
        kind = type("Kind", (), {"value": "managed"})()
        priority = 60
        status = type("Status", (), {"value": "running"})()
        timestamp = 123.0
        created_at = 120.0
        label = "005"
        is_capability = False

    payload = build_live_task_payload(
        FakeTask(),
        [],
        runtime_state={
            "active_tasks": {"t_attack": {"label": "005"}},
            "unfulfilled_requests": [
                {
                    "request_id": "req_2",
                    "task_id": "t_attack",
                    "task_label": "005",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "remaining_count": 1,
                    "reason": "waiting_dispatch",
                }
            ],
        },
        list_pending_questions=lambda: [],
        list_task_messages=lambda task_id: [],
        world_stale=False,
        log_session_dir=None,
    )

    triage = payload["triage"]
    assert triage["state"] == "running"
    assert triage["phase"] == "dispatch"
    assert triage["waiting_reason"] == "waiting_dispatch"
    assert triage["blocking_reason"] == ""
    assert triage["reservation_preview"] == "步兵 × 1 · 待分发"
    assert "等待能力模块分发单位：步兵 × 1" in triage["status_line"]
    print("  PASS: build_live_task_payload_marks_request_dispatch_without_fake_blocker")
