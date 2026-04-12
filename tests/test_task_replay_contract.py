"""Task replay request and replay-bundle contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

from logging_system import start_persistence_session, stop_persistence_session

from main import RuntimeBridge, TASK_REPLAY_RAW_ENTRY_LIMIT
from task_replay import build_live_task_replay_bundle, build_task_replay_bundle


def test_task_replay_request_returns_persisted_task_log():
    """Task replay should read persisted task logs and return them to one client."""

    class FakeKernel:
        def __init__(self):
            self._tasks = []

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            return []

        def get_task_agent(self, task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def runtime_state(self):
            raise AssertionError("bridge should use kernel.runtime_state()")

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import tempfile
    from pathlib import Path

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        session_dir = start_persistence_session(tmpdir, session_name="unit-replay")
        try:
            task_path = Path(session_dir) / "tasks" / "t_demo.jsonl"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 123.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_demo"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.5,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Job started",
                                "event": "job_started",
                                "data": {"task_id": "t_demo", "job_id": "j_1", "expert_type": "ReconExpert"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.8,
                                "component": "task_agent",
                                "level": "DEBUG",
                                "message": "TaskAgent context snapshot",
                                "event": "context_snapshot",
                                "data": {
                                    "task_id": "t_demo",
                                    "packet": {
                                        "jobs": [{"job_id": "j_1"}],
                                        "recent_signals": [{"kind": "risk_alert"}],
                                        "recent_events": [{"event": "job_started"}],
                                        "other_active_tasks": [{"task_id": "t_other"}],
                                        "open_decisions": [{"kind": "need_target"}],
                                        "runtime_facts": {
                                            "cash": 5000,
                                            "power_drained": 100,
                                            "unfulfilled_requests": [
                                                {
                                                    "request_id": "req_1",
                                                    "reservation_id": "res_1",
                                                    "task_id": "t_demo",
                                                    "task_label": "007",
                                                    "unit_type": "3tnk",
                                                    "queue_type": "Vehicle",
                                                    "count": 2,
                                                    "fulfilled": 1,
                                                    "remaining_count": 1,
                                                    "blocking": True,
                                                    "min_start_package": 1,
                                                    "bootstrap_job_id": "j_boot",
                                                    "bootstrap_task_id": "t_cap",
                                                    "reservation_status": "partial",
                                                    "reason": "bootstrap_in_progress",
                                                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 4,
                                                    "world_sync_failure_threshold": 3,
                                                    "disabled_producers": ["weap"],
                                                }
                                            ],
                                            "unit_reservations": [
                                                {
                                                    "reservation_id": "res_1",
                                                    "request_id": "req_1",
                                                    "task_id": "t_demo",
                                                    "task_label": "007",
                                                    "unit_type": "3tnk",
                                                    "queue_type": "Vehicle",
                                                    "count": 2,
                                                    "remaining_count": 1,
                                                    "status": "partial",
                                                    "blocking": True,
                                                    "min_start_package": 1,
                                                    "start_released": False,
                                                    "bootstrap_job_id": "j_boot",
                                                    "bootstrap_task_id": "t_cap",
                                                    "reason": "bootstrap_in_progress",
                                                    "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 5,
                                                    "world_sync_failure_threshold": 3,
                                                    "assigned_actor_ids": [10],
                                                    "produced_actor_ids": [11],
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.9,
                                "component": "task_agent",
                                "level": "DEBUG",
                                "message": "TaskAgent llm input",
                                "event": "llm_input",
                                "data": {
                                    "task_id": "t_demo",
                                    "messages": [{"role": "system"}, {"role": "user"}],
                                    "tools": [{"name": "query_world"}],
                                    "attempt": 2,
                                    "wake": 7,
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 124.0,
                                "component": "task_agent",
                                "level": "INFO",
                                "message": "TaskAgent LLM call succeeded",
                                "event": "llm_succeeded",
                                "data": {
                                    "task_id": "t_demo",
                                    "model": "demo-model",
                                    "response_text": "先查询世界状态",
                                    "reasoning_content": "需要先确认当前侦察态势",
                                    "tool_calls_detail": [{"name": "query_world", "arguments": "{}"}],
                                    "usage": {"prompt_tokens": 321, "completion_tokens": 45},
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 124.2,
                                "component": "expert",
                                "level": "WARN",
                                "message": "Expert signal emitted",
                                "event": "expert_signal",
                                "data": {
                                    "task_id": "t_demo",
                                    "job_id": "j_1",
                                    "signal_kind": "risk_alert",
                                    "summary": "电力不足",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 125.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task completed",
                                "event": "task_completed",
                                "data": {"task_id": "t_demo", "summary": "侦察完成，发现目标"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            component_path = Path(session_dir) / "components" / "dashboard_publish.jsonl"
            component_path.parent.mkdir(parents=True, exist_ok=True)
            component_path.write_text(
                json.dumps(
                    {
                        "timestamp": 126.0,
                        "component": "dashboard_publish",
                        "level": "ERROR",
                        "message": "Dashboard publish stage failed",
                        "event": "dashboard_publish_stage_failed",
                        "data": {
                            "stage": "task_messages",
                            "error": "RuntimeError('publish-boom')",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            world_component_path = Path(session_dir) / "components" / "world_model.jsonl"
            world_component_path.parent.mkdir(parents=True, exist_ok=True)
            world_component_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 122.8,
                                "component": "world_model",
                                "level": "WARN",
                                "message": "WorldModel actors refresh failed",
                                "event": "world_refresh_failed",
                                "data": {
                                    "layer": "actors",
                                    "error": "COMMAND_EXECUTION_ERROR",
                                    "error_detail": "Attempted to get trait from destroyed object",
                                    "failure_threshold": 3,
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.0,
                                "component": "world_model",
                                "level": "WARN",
                                "message": "Slow world refresh",
                                "event": "world_refresh_slow",
                                "data": {
                                    "total_ms": 154.2,
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.1,
                                "component": "world_model",
                                "level": "INFO",
                                "message": "World refresh completed",
                                "event": "world_refresh_completed",
                                "data": {
                                    "stale": True,
                                    "consecutive_failures": 4,
                                    "failure_threshold": 3,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request("t_demo", "client_7", session_dir=str(session_dir))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent[0][0] == "task_replay"
    assert ws.sent[0][1]["client_id"] == "client_7"
    payload = ws.sent[0][1]["payload"]
    assert payload["task_id"] == "t_demo"
    assert payload["session_dir"] == str(session_dir)
    assert payload["entry_count"] == 7
    assert payload["raw_entry_count"] == 7
    assert payload["raw_entries_truncated"] is False
    assert payload["entries"][1]["data"]["job_id"] == "j_1"
    assert payload["bundle"]["summary"] == "侦察完成，发现目标"
    assert payload["bundle"]["last_transition"]["label"] == "task_completed"
    assert payload["bundle"]["timeline"][0]["label"] == "task_created"
    assert payload["bundle"]["timeline"][0]["elapsed_s"] == 0.0
    assert payload["bundle"]["timeline"][-1]["label"] == "task_completed"
    assert payload["bundle"]["blockers"][0]["message"] == "电力不足"
    assert payload["bundle"]["llm"]["rounds"] == 1
    assert payload["bundle"]["llm"]["prompt_tokens"] == 321
    assert payload["bundle"]["tools"][0]["name"] == "query_world"
    assert payload["bundle"]["experts"][0]["name"] == "ReconExpert"
    assert payload["bundle"]["signals"][0]["name"] == "risk_alert"
    assert payload["bundle"]["current_runtime"] is None
    assert payload["bundle"]["debug"]["latest_context"]["job_count"] == 1
    assert payload["bundle"]["debug"]["latest_context"]["signal_count"] == 1
    assert payload["bundle"]["debug"]["latest_context"]["runtime_fact_keys"] == [
        "cash",
        "power_drained",
        "unfulfilled_requests",
        "unit_reservations",
    ]
    assert payload["bundle"]["debug"]["latest_llm_input"]["message_count"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["tool_count"] == 1
    assert payload["bundle"]["debug"]["latest_llm_input"]["attempt"] == 2
    assert payload["bundle"]["debug"]["latest_llm_input"]["wake"] == 7
    assert "packet" not in payload["bundle"]["debug"]["latest_context"]
    assert "messages" not in payload["bundle"]["debug"]["latest_llm_input"]
    assert "tools" not in payload["bundle"]["debug"]["latest_llm_input"]
    assert len(payload["bundle"]["lifecycle_events"]) == 7
    assert payload["bundle"]["lifecycle_events"][1]["job_id"] == "j_1"
    assert payload["bundle"]["expert_runs"][0]["job_id"] == "j_1"
    assert payload["bundle"]["expert_runs"][0]["latest_signal"]["label"] == "expert:risk_alert"
    assert payload["bundle"]["llm_turns"][0]["wake"] == 7
    assert payload["bundle"]["llm_turns"][0]["attempt"] == 2
    assert payload["bundle"]["llm_turns"][0]["response_text"] == "先查询世界状态"
    assert payload["bundle"]["llm_turns"][0]["reasoning_content"] == "需要先确认当前侦察态势"
    assert payload["bundle"]["llm_turns"][0]["input_messages"][0]["role"] == "system"
    assert payload["bundle"]["session_context"]["world_health"] == {
        "stale_seen": True,
        "ended_stale": True,
        "disconnect_seen": False,
        "ended_disconnected": False,
        "stale_refreshes": 1,
        "max_consecutive_failures": 4,
        "failure_threshold": 3,
        "last_error": "COMMAND_EXECUTION_ERROR",
        "last_error_detail": "Attempted to get trait from destroyed object",
        "last_failure_layer": "actors",
        "slow_events": 1,
        "max_total_ms": 154.2,
    }
    assert payload["bundle"]["session_context"]["runtime_fault_summary"] == {
        "degraded": True,
        "source": "dashboard_publish",
        "stage": "task_messages",
        "error": "RuntimeError('publish-boom')",
        "count": 1,
        "first_at": 126.0,
        "updated_at": 126.0,
        "breakdown": [
            {
                "source": "dashboard_publish",
                "stage": "task_messages",
                "count": 1,
            }
        ],
    }
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["request_id"] == "req_1"
    assert (
        payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_last_error"]
        == "actors:COMMAND_EXECUTION_ERROR"
    )
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_consecutive_failures"] == 4
    assert payload["bundle"]["unit_pipeline"]["unfulfilled_requests"][0]["world_sync_failure_threshold"] == 3
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["reservation_id"] == "res_1"
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["assigned_count"] == 1
    assert (
        payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_last_error"]
        == "economy:COMMAND_EXECUTION_ERROR"
    )
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_consecutive_failures"] == 5
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"][0]["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_request_returns_persisted_task_log")

def test_task_replay_request_rejects_requested_session_outside_root():
    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return []

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

        async def send_error_to_client(self, client_id, message, *, code="INVALID_MESSAGE", inbound_type=None, extra=None):
            self.sent.append(
                (
                    "error",
                    {
                        "client_id": client_id,
                        "message": message,
                        "code": code,
                        "inbound_type": inbound_type,
                        "extra": dict(extra or {}),
                    },
                )
            )

    import tempfile
    from pathlib import Path

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "logs"
        outside_root = Path(tmpdir) / "outside"
        inside_session = start_persistence_session(root, session_name="inside-session")
        try:
            inside_task_path = inside_session / "tasks" / "t_demo.jsonl"
            inside_task_path.parent.mkdir(parents=True, exist_ok=True)
            inside_task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 10.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_demo"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 11.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task completed",
                                "event": "task_completed",
                                "data": {"task_id": "t_demo", "summary": "根目录回放"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            outside_session = start_persistence_session(outside_root, session_name="outside-session")
            outside_task_path = outside_session / "tasks" / "t_demo.jsonl"
            outside_task_path.parent.mkdir(parents=True, exist_ok=True)
            outside_task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 20.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_demo"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 21.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task completed",
                                "event": "task_completed",
                                "data": {"task_id": "t_demo", "summary": "越界回放"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            bridge.log_session_root = str(root)
            stop_persistence_session()

            async def run():
                await bridge.on_task_replay_request("t_demo", "client_scope", session_dir=str(outside_session))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    assert ws.sent == [
        (
            "error",
            {
                "client_id": "client_scope",
                "message": f"Invalid task_replay_request: unknown session_dir {outside_session}",
                "code": "INVALID_SESSION",
                "inbound_type": "task_replay_request",
                "extra": {},
            },
        )
    ]
    print("  PASS: task_replay_request_rejects_requested_session_outside_root")

def test_task_replay_request_prefers_live_truth_for_active_task_bundle():
    """Live replay should not keep stale persisted truth once live runtime provides current truth."""

    class FakeTask:
        def __init__(self):
            self.task_id = "t_live"
            self.raw_text = "发展科技"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 80
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 123.0
            self.created_at = 100.0
            self.label = "001"
            self.is_capability = True

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask()]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {
                "active_tasks": {
                    "t_live": {
                        "label": "001",
                        "is_capability": True,
                    }
                },
                "capability_status": {
                    "task_id": "t_live",
                    "label": "001",
                    "phase": "idle",
                },
                "unfulfilled_requests": [],
                "unit_reservations": [],
            }

    class FakeWorldModel:
        def __init__(self):
            self.calls: list[tuple[str, bool]] = []

        def world_summary(self):
            return {}

        def refresh_health(self):
            return {"stale": False}

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            self.calls.append((task_id, include_buildable))
            if task_id == "t_live":
                return {
                    "faction": "soviet",
                    "base_progression": {
                        "status": "下一步：矿场",
                        "next_unit_type": "proc",
                        "next_queue_type": "Building",
                        "buildable_now": True,
                    },
                    "buildable_now": {"Building": ["proc"]},
                    "buildable_blocked": {},
                    "ready_queue_items": [],
                    "unfulfilled_requests": [],
                    "unit_reservations": [],
                }
            raise AssertionError(f"unexpected task_id {task_id}")

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import tempfile
    from pathlib import Path

    world_model = FakeWorldModel()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=world_model,
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        session_dir = start_persistence_session(tmpdir, session_name="unit-replay-live")
        try:
            task_path = Path(session_dir) / "tasks" / "t_live.jsonl"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 123.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_live"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 123.5,
                                "component": "task_agent",
                                "level": "DEBUG",
                                "message": "TaskAgent context snapshot",
                                "event": "context_snapshot",
                                "data": {
                                    "task_id": "t_live",
                                    "packet": {
                                        "runtime_facts": {
                                            "faction": "soviet",
                                            "base_progression": {
                                                "status": "下一步：电厂",
                                                "next_unit_type": "powr",
                                                "next_queue_type": "Building",
                                                "buildable_now": True,
                                            },
                                            "buildable_now": {"Building": ["powr"]},
                                            "buildable_blocked": {},
                                            "ready_queue_items": [
                                                {
                                                    "queue_type": "Building",
                                                    "unit_type": "powr",
                                                    "display_name": "发电厂",
                                                }
                                            ],
                                            "unfulfilled_requests": [
                                                {
                                                    "request_id": "req_old",
                                                    "reservation_id": "res_old",
                                                    "task_id": "t_live",
                                                    "unit_type": "e1",
                                                    "queue_type": "Infantry",
                                                    "count": 1,
                                                    "fulfilled": 0,
                                                    "remaining_count": 1,
                                                    "reason": "world_sync_stale",
                                                    "world_sync_last_error": "persisted:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 2,
                                                    "world_sync_failure_threshold": 3,
                                                }
                                            ],
                                            "unit_reservations": [
                                                {
                                                    "reservation_id": "res_old",
                                                    "request_id": "req_old",
                                                    "task_id": "t_live",
                                                    "unit_type": "e1",
                                                    "queue_type": "Infantry",
                                                    "count": 1,
                                                    "remaining_count": 1,
                                                    "status": "pending",
                                                    "reason": "world_sync_stale",
                                                    "world_sync_last_error": "persisted:COMMAND_EXECUTION_ERROR",
                                                    "world_sync_consecutive_failures": 2,
                                                    "world_sync_failure_threshold": 3,
                                                }
                                            ],
                                        }
                                    },
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request("t_live", "client_live", session_dir=str(session_dir))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    bundle = payload["bundle"]
    assert bundle["current_runtime"] is not None
    assert bundle["replay_triage"]["status_line"] == bundle["current_runtime"]["triage"]["status_line"]
    assert bundle["capability_truth"]["next_unit_type"] == "proc"
    assert "Building:proc" in bundle["capability_truth"]["issue_now"]
    assert "Building:powr" not in bundle["capability_truth"]["issue_now"]
    assert bundle["unit_pipeline"]["unfulfilled_requests"] == []
    assert bundle["unit_pipeline"]["unit_reservations"] == []
    assert ("t_live", True) in world_model.calls
    print("  PASS: task_replay_request_prefers_live_truth_for_active_task_bundle")

def test_task_replay_request_keeps_historical_session_isolated_from_live_runtime():
    """Requesting an older persisted session must not be polluted by current live runtime."""

    class FakeTask:
        def __init__(self):
            self.task_id = "t_hist"
            self.raw_text = "历史任务"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 50
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 223.0
            self.created_at = 200.0
            self.label = "009"
            self.is_capability = False

    class FakeKernel:
        def __init__(self):
            self._tasks = [FakeTask()]

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return list(self._tasks)

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {
                "active_tasks": {"t_hist": {"label": "009"}},
                "unit_reservations": [
                    {
                        "reservation_id": "res_live_leak",
                        "task_id": "t_hist",
                        "unit_type": "3tnk",
                        "queue_type": "Vehicle",
                        "count": 1,
                        "remaining_count": 1,
                        "status": "pending",
                        "reason": "waiting_dispatch",
                    }
                ],
            }

    class FakeWorldModel:
        def world_summary(self):
            return {}

        def refresh_health(self):
            return {"stale": False}

        def compute_runtime_facts(self, task_id: str, *, include_buildable: bool = True):
            raise AssertionError(f"historical replay must not query live runtime facts: {task_id}/{include_buildable}")

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import tempfile
    from pathlib import Path

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    ws = FakeWS()
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge.log_session_root = tmpdir
        historical_session = start_persistence_session(tmpdir, session_name="older-session")
        try:
            task_path = Path(historical_session) / "tasks" / "t_hist.jsonl"
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": 100.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task created",
                                "event": "task_created",
                                "data": {"task_id": "t_hist"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": 101.0,
                                "component": "kernel",
                                "level": "INFO",
                                "message": "Task completed",
                                "event": "task_completed",
                                "data": {"task_id": "t_hist", "summary": "历史任务已完成"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            current_session = start_persistence_session(tmpdir, session_name="current-session")

            async def run():
                await bridge.on_task_replay_request(
                    "t_hist",
                    "client_hist",
                    session_dir=str(historical_session),
                )

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    assert payload["session_dir"] == str(historical_session)
    assert payload["session_dir"] != str(current_session)
    assert payload["bundle"]["current_runtime"] is None
    assert payload["bundle"]["status_line"] == ""
    assert payload["bundle"]["unit_pipeline"]["unit_reservations"] == []
    assert payload["bundle"]["replay_triage"]["state"] == "completed"
    assert payload["bundle"]["replay_triage"]["phase"] == "succeeded"
    assert payload["bundle"]["summary"] == "历史任务已完成"
    print("  PASS: task_replay_request_keeps_historical_session_isolated_from_live_runtime")

def test_task_replay_request_limits_raw_entries_payload():
    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return []

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import asyncio
    import json
    import tempfile
    from pathlib import Path

    ws = FakeWS()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge.log_session_root = tmp_dir
        session_dir = Path(tmp_dir) / "session-20260411T021500Z"
        task_dir = session_dir / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        start_persistence_session(tmp_dir)
        try:
            records = [
                {
                    "timestamp": float(100 + index),
                    "component": "kernel",
                    "level": "INFO",
                    "message": f"event-{index}",
                    "event": "task_info",
                    "data": {"task_id": "t_demo", "index": index},
                }
                for index in range(TASK_REPLAY_RAW_ENTRY_LIMIT + 25)
            ]
            (task_dir / "t_demo.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request("t_demo", "client_7", session_dir=str(session_dir))

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    assert payload["entry_count"] == TASK_REPLAY_RAW_ENTRY_LIMIT + 25
    assert payload["raw_entry_count"] == TASK_REPLAY_RAW_ENTRY_LIMIT
    assert payload["raw_entries_truncated"] is True
    assert len(payload["entries"]) == TASK_REPLAY_RAW_ENTRY_LIMIT
    assert payload["entries"][0]["data"]["index"] == 25
    assert payload["entries"][-1]["data"]["index"] == TASK_REPLAY_RAW_ENTRY_LIMIT + 24
    print("  PASS: task_replay_request_limits_raw_entries_payload")

def test_task_replay_request_can_skip_raw_entries_until_expanded():
    class FakeKernel:
        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return []

        def jobs_for_task(self, task_id):
            del task_id
            return []

        def get_task_agent(self, task_id):
            del task_id
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    class FakeWS:
        def __init__(self):
            self.is_running = True
            self.sent: list[tuple[str, dict[str, Any]]] = []

        async def send_task_replay_to_client(self, client_id, payload):
            self.sent.append(("task_replay", {"client_id": client_id, "payload": payload}))

    import asyncio
    import json
    import tempfile
    from pathlib import Path

    ws = FakeWS()
    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge.attach_ws_server(ws)

    with tempfile.TemporaryDirectory() as tmp_dir:
        bridge.log_session_root = tmp_dir
        session_dir = Path(tmp_dir) / "session-20260411T021500Z"
        task_dir = session_dir / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        start_persistence_session(tmp_dir)
        try:
            records = [
                {
                    "timestamp": float(100 + index),
                    "component": "kernel",
                    "level": "INFO",
                    "message": f"event-{index}",
                    "event": "task_info",
                    "data": {"task_id": "t_demo", "index": index},
                }
                for index in range(6)
            ]
            (task_dir / "t_demo.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            async def run():
                await bridge.on_task_replay_request(
                    "t_demo",
                    "client_7",
                    session_dir=str(session_dir),
                    include_entries=False,
                )

            asyncio.run(run())
        finally:
            stop_persistence_session()

    payload = ws.sent[0][1]["payload"]
    assert payload["entry_count"] == 6
    assert payload["raw_entry_count"] == 6
    assert payload["raw_entries_truncated"] is False
    assert payload["raw_entries_included"] is False
    assert payload["entries"] == []
    assert payload["bundle"]["entry_count"] == 6
    print("  PASS: task_replay_request_can_skip_raw_entries_until_expanded")

def test_task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks():
    class FakeKernel:
        def __init__(self):
            self._task = type(
                "Task",
                (),
                {
                    "task_id": "t_live",
                    "raw_text": "发展经济",
                    "kind": type("K", (), {"value": "managed"})(),
                    "priority": 50,
                    "status": type("S", (), {"value": "running"})(),
                    "timestamp": 1.0,
                    "created_at": 1.0,
                    "label": "001",
                    "is_capability": True,
                },
            )()

        def list_pending_questions(self):
            return []

        def list_tasks(self):
            return [self._task]

        def jobs_for_task(self, task_id):
            return []

        def get_task_agent(self, task_id):
            return None

        def active_jobs(self):
            return []

        def list_task_messages(self):
            return []

        def list_player_notifications(self):
            return []

        def runtime_state(self):
            return {"active_tasks": {"t_live": {"status": "running"}}}

    class FakeWorldModel:
        def world_summary(self):
            return {}

    class FakeGameLoop:
        def register_agent(self, *args, **kwargs):
            pass

        def unregister_agent(self, *args, **kwargs):
            pass

        def register_job(self, *args, **kwargs):
            pass

        def unregister_job(self, *args, **kwargs):
            pass

    bridge = RuntimeBridge(
        kernel=FakeKernel(),
        world_model=FakeWorldModel(),
        game_loop=FakeGameLoop(),
    )
    bridge._task_to_dict = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "task_id": "t_live",
        "triage": {"status_line": "等待能力层补前置：电厂"},
    }
    bundle = build_live_task_replay_bundle(
        "t_live",
        [
            {
                "timestamp": 100.0,
                "component": "kernel",
                "level": "INFO",
                "message": "Task created",
                "event": "task_created",
                "data": {"task_id": "t_live"},
            }
        ],
        runtime_state=bridge.kernel.runtime_state(),
        tasks=bridge.kernel.list_tasks(),
        jobs_for_task=bridge.kernel.jobs_for_task,
        task_payload_builder=bridge._task_to_dict,
        compute_runtime_facts=getattr(bridge.world_model, "compute_runtime_facts", None),
    )
    assert bundle["summary"] == "等待能力层补前置：电厂"
    assert bundle["status_line"] == "等待能力层补前置：电厂"
    assert bundle["timeline"][0]["label"] == "task_created"
    print("  PASS: task_replay_bundle_prefers_live_runtime_status_line_for_active_tasks")

def test_task_replay_bundle_counts_tools_once_and_keeps_separated_blockers():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.2,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "tool_calls_detail": [{"name": "query_world", "arguments": "{}"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        },
        {
            "timestamp": 10.3,
            "component": "task_agent",
            "level": "INFO",
            "message": "Executing tool call",
            "event": "tool_execute",
            "data": {
                "task_id": "t_demo",
                "tool": "query_world",
                "tool_call_id": "call_1",
            },
        },
        {
            "timestamp": 10.4,
            "component": "task_agent",
            "level": "INFO",
            "message": "Tool call completed",
            "event": "tool_execute_completed",
            "data": {
                "task_id": "t_demo",
                "tool": "query_world",
                "tool_call_id": "call_1",
            },
        },
        {
            "timestamp": 10.5,
            "component": "expert",
            "level": "WARN",
            "message": "Expert signal emitted",
            "event": "expert_signal",
            "data": {
                "task_id": "t_demo",
                "job_id": "j_1",
                "signal_kind": "risk_alert",
                "summary": "等待电厂",
            },
        },
        {
            "timestamp": 10.6,
            "component": "kernel",
            "level": "INFO",
            "message": "Task still running",
            "event": "task_info",
            "data": {"task_id": "t_demo", "summary": "继续检查前置条件"},
        },
        {
            "timestamp": 10.7,
            "component": "expert",
            "level": "WARN",
            "message": "Expert signal emitted",
            "event": "expert_signal",
            "data": {
                "task_id": "t_demo",
                "job_id": "j_1",
                "signal_kind": "risk_alert",
                "summary": "等待电厂",
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    assert bundle["llm"]["rounds"] == 1
    assert bundle["tools"] == [{"name": "query_world", "count": 1}]
    assert [item["message"] for item in bundle["blockers"]] == ["等待电厂", "等待电厂"]
    print("  PASS: task_replay_bundle_counts_tools_once_and_keeps_separated_blockers")

def test_task_replay_bundle_surfaces_unit_request_lifecycle_events():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "kernel",
            "level": "INFO",
            "message": "Unit request fulfilled from idle",
            "event": "unit_request_fulfilled",
            "data": {
                "task_id": "t_demo",
                "request_id": "req_idle",
                "reservation_id": "res_idle",
                "actor_ids": [10],
                "reservation_status": "assigned",
                "assigned_count": 1,
                "produced_count": 0,
            },
        },
        {
            "timestamp": 10.2,
            "component": "kernel",
            "level": "INFO",
            "message": "Unit request start released",
            "event": "unit_request_start_released",
            "data": {
                "task_id": "t_demo",
                "request_id": "req_release",
                "reservation_id": "res_release",
                "status": "partial",
                "start_released": True,
                "assigned_count": 2,
                "produced_count": 1,
                "remaining_count": 1,
            },
        },
        {
            "timestamp": 10.3,
            "component": "kernel",
            "level": "INFO",
            "message": "Unit request cancelled",
            "event": "unit_request_cancelled",
            "data": {
                "task_id": "t_demo",
                "request_id": "req_cancel",
                "reservation_id": "res_cancel",
                "remaining_count": 2,
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    timeline_labels = [item["label"] for item in bundle["timeline"]]
    highlight_labels = [item["label"] for item in bundle["highlights"]]
    assert timeline_labels == [
        "task_created",
        "unit_request_fulfilled",
        "unit_request_start_released",
        "unit_request_cancelled",
    ]
    assert highlight_labels == [
        "task_created",
        "unit_request_fulfilled",
        "unit_request_start_released",
        "unit_request_cancelled",
    ]
    assert bundle["summary"] == "Unit request cancelled"
    print("  PASS: task_replay_bundle_surfaces_unit_request_lifecycle_events")

def test_task_replay_bundle_preserves_world_sync_detail_in_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "task_id": "t_demo",
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
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "remaining_count": 1,
                                "status": "pending",
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 5,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    request = bundle["unit_pipeline"]["unfulfilled_requests"][0]
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert request["world_sync_last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert request["world_sync_consecutive_failures"] == 4
    assert request["world_sync_failure_threshold"] == 3
    assert reservation["world_sync_last_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 5
    assert reservation["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_bundle_preserves_world_sync_detail_in_unit_pipeline")

def test_task_replay_bundle_derives_world_sync_replay_triage_from_reservation_only_context():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unit_reservations": [
                            {
                                "reservation_id": "res_1",
                                "request_id": "req_1",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "remaining_count": 1,
                                "status": "pending",
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 5,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "degraded"
    assert triage["phase"] == "world_sync"
    assert triage["waiting_reason"] == "world_sync_stale"
    assert triage["blocking_reason"] == "world_sync_stale"
    assert triage["world_stale"] is True
    assert triage["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert triage["world_sync_failures"] == 5
    assert triage["world_sync_failure_threshold"] == 3
    assert "历史阻塞" in triage["status_line"]
    assert "failures=5/3" in triage["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in triage["status_line"]
    print("  PASS: task_replay_bundle_derives_world_sync_replay_triage_from_reservation_only_context")

def test_task_replay_bundle_derives_replay_triage_from_runtime_facts_world_sync_without_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "world_sync_stale": True,
                        "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                        "world_sync_consecutive_failures": 4,
                        "world_sync_failure_threshold": 3,
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "degraded"
    assert triage["phase"] == "world_sync"
    assert triage["waiting_reason"] == "world_sync_stale"
    assert triage["blocking_reason"] == "world_sync_stale"
    assert triage["world_stale"] is True
    assert triage["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert triage["world_sync_failures"] == 4
    assert triage["world_sync_failure_threshold"] == 3
    assert "历史世界同步异常，等待恢复" in triage["status_line"]
    assert "failures=4/3" in triage["status_line"]
    assert "actors:COMMAND_EXECUTION_ERROR" in triage["status_line"]
    print("  PASS: task_replay_bundle_derives_replay_triage_from_runtime_facts_world_sync_without_pipeline")

def test_task_replay_bundle_terminal_state_beats_runtime_facts_world_sync_fallback():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "world_sync_stale": True,
                        "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                        "world_sync_consecutive_failures": 4,
                        "world_sync_failure_threshold": 3,
                    }
                },
            },
        },
        {
            "timestamp": 10.2,
            "component": "kernel",
            "level": "INFO",
            "message": "Task completed",
            "event": "task_completed",
            "data": {"task_id": "t_demo", "summary": "任务已完成"},
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "completed"
    assert triage["phase"] == "succeeded"
    assert triage["waiting_reason"] == ""
    assert triage["blocking_reason"] == ""
    assert triage["world_stale"] is False
    assert triage["world_sync_error"] == ""
    assert triage["world_sync_failures"] == 0
    assert triage["world_sync_failure_threshold"] == 0
    print("  PASS: task_replay_bundle_terminal_state_beats_runtime_facts_world_sync_fallback")

def test_task_replay_bundle_derives_replay_triage_from_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "reservation_id": "res_1",
                                "task_id": "t_demo",
                                "unit_type": "4tnk",
                                "queue_type": "Vehicle",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "missing_prerequisite",
                                "prerequisites": ["fix", "stek", "weap"],
                            }
                        ],
                        "unit_reservations": [
                            {
                                "reservation_id": "res_1",
                                "request_id": "req_1",
                                "task_id": "t_demo",
                                "unit_type": "4tnk",
                                "queue_type": "Vehicle",
                                "count": 1,
                                "remaining_count": 1,
                                "status": "pending",
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "blocked"
    assert triage["phase"] == "blocked"
    assert triage["waiting_reason"] == "missing_prerequisite"
    assert triage["blocking_reason"] == "missing_prerequisite"
    assert triage["reservation_ids"] == ["res_1"]
    assert triage["reservation_preview"] == "猛犸坦克 × 1 · 缺少前置"
    assert triage["reservation_status"] == "pending"
    assert triage["remaining_count"] == 1
    assert "猛犸坦克 × 1 · 缺少前置" in triage["status_line"]
    print("  PASS: task_replay_bundle_derives_replay_triage_from_unit_pipeline")

def test_task_replay_bundle_marks_waiting_dispatch_as_running_dispatch():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_1",
                                "reservation_id": "res_1",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "waiting_dispatch",
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    triage = bundle["replay_triage"]
    assert triage["state"] == "running"
    assert triage["phase"] == "dispatch"
    assert triage["waiting_reason"] == "waiting_dispatch"
    assert triage["blocking_reason"] == ""
    assert triage["reservation_preview"] == "步兵 × 1 · 待分发"
    assert triage["remaining_count"] == 1
    assert triage["status_line"] == "历史推进：步兵 × 1 · 待分发"
    print("  PASS: task_replay_bundle_marks_waiting_dispatch_as_running_dispatch")

def test_task_replay_bundle_falls_back_to_live_runtime_facts_for_unit_pipeline():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_demo",
                "packet": {
                    "runtime_facts": {
                        "cash": 5000,
                        "unfulfilled_requests": [
                            {
                                "request_id": "req_persisted",
                                "task_id": "t_demo",
                                "unit_type": "e1",
                                "queue_type": "Infantry",
                                "count": 1,
                                "fulfilled": 0,
                                "remaining_count": 1,
                                "reason": "world_sync_stale",
                                "world_sync_last_error": "persisted:COMMAND_EXECUTION_ERROR",
                                "world_sync_consecutive_failures": 2,
                                "world_sync_failure_threshold": 3,
                            }
                        ],
                    }
                },
            },
        },
    ]

    bundle = build_task_replay_bundle(
        "t_demo",
        entries,
        live_runtime_facts={
            "unfulfilled_requests": [
                {
                    "request_id": "req_live",
                    "task_id": "t_demo",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "fulfilled": 0,
                    "remaining_count": 1,
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "live:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 4,
                    "world_sync_failure_threshold": 3,
                }
            ],
            "unit_reservations": [
                {
                    "reservation_id": "res_live",
                    "request_id": "req_live",
                    "task_id": "t_demo",
                    "unit_type": "e1",
                    "queue_type": "Infantry",
                    "count": 1,
                    "remaining_count": 1,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 5,
                    "world_sync_failure_threshold": 3,
                }
            ],
        },
    )

    request = bundle["unit_pipeline"]["unfulfilled_requests"][0]
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert request["request_id"] == "req_persisted"
    assert request["world_sync_last_error"] == "persisted:COMMAND_EXECUTION_ERROR"
    assert request["world_sync_consecutive_failures"] == 2
    assert request["world_sync_failure_threshold"] == 3
    assert reservation["reservation_id"] == "res_live"
    assert reservation["world_sync_last_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 5
    assert reservation["world_sync_failure_threshold"] == 3
    print("  PASS: task_replay_bundle_falls_back_to_live_runtime_facts_for_unit_pipeline")

def test_task_replay_bundle_falls_back_to_runtime_state_reservations():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        }
    ]

    bundle = build_task_replay_bundle(
        "t_demo",
        entries,
        runtime_state={
            "unit_reservations": [
                {
                    "reservation_id": "res_runtime",
                    "request_id": "req_runtime",
                    "task_id": "t_demo",
                    "unit_type": "3tnk",
                    "queue_type": "Vehicle",
                    "count": 2,
                    "remaining_count": 2,
                    "status": "pending",
                    "reason": "world_sync_stale",
                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                    "world_sync_consecutive_failures": 6,
                    "world_sync_failure_threshold": 3,
                }
            ]
        },
    )

    assert bundle["unit_pipeline"]["unfulfilled_requests"] == []
    reservation = bundle["unit_pipeline"]["unit_reservations"][0]
    assert reservation["reservation_id"] == "res_runtime"
    assert reservation["world_sync_last_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert reservation["world_sync_consecutive_failures"] == 6
    assert reservation["world_sync_failure_threshold"] == 3
    triage = bundle["replay_triage"]
    assert triage["state"] == "degraded"
    assert triage["phase"] == "world_sync"
    assert triage["world_stale"] is True
    assert triage["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"
    assert triage["world_sync_failures"] == 6
    assert triage["world_sync_failure_threshold"] == 3
    assert "历史阻塞" in triage["status_line"]
    print("  PASS: task_replay_bundle_falls_back_to_runtime_state_reservations")

def test_task_replay_bundle_exposes_capability_truth_summary() -> None:
    entries = [
        {
            "timestamp": 10.0,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent context snapshot",
            "event": "context_snapshot",
            "data": {
                "task_id": "t_cap",
                "packet": {
                    "runtime_facts": {
                        "faction": "soviet",
                        "base_progression": {
                            "status": "下一步：矿场",
                            "next_unit_type": "proc",
                            "next_queue_type": "Building",
                            "buildable_now": True,
                        },
                        "buildable_now": {"Building": ["powr", "proc"]},
                        "buildable_blocked": {
                            "Building": [
                                {"unit_type": "barr", "queue_type": "Building", "reason": "queue_blocked"},
                            ]
                        },
                        "ready_queue_items": [
                            {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂"},
                        ],
                    }
                },
            },
        }
    ]

    bundle = build_task_replay_bundle("t_cap", entries)

    capability_truth = bundle["capability_truth"]
    assert capability_truth["faction"] == "soviet"
    assert capability_truth["base_status"] == "下一步：矿场"
    assert capability_truth["next_unit_type"] == "proc"
    assert capability_truth["buildable_now"] is True
    assert "Building:powr" in capability_truth["issue_now"]
    assert "Building:proc" in capability_truth["issue_now"]
    assert "Building:barr:queue_blocked" in capability_truth["blocked_now"]
    assert "Building:发电厂" in capability_truth["ready_items"]
    print("  PASS: task_replay_bundle_exposes_capability_truth_summary")

def test_live_task_replay_bundle_fetches_buildable_truth_for_capability_tasks() -> None:
    class FakeTask:
        def __init__(self, task_id: str, *, is_capability: bool) -> None:
            self.task_id = task_id
            self.raw_text = "能力"
            self.kind = type("Kind", (), {"value": "managed"})()
            self.priority = 80
            self.status = type("Status", (), {"value": "running"})()
            self.timestamp = 123.0
            self.created_at = 120.0
            self.label = "cap"
            self.is_capability = is_capability

    calls: list[tuple[str, bool]] = []

    def compute_runtime_facts(task_id: str, *, include_buildable: bool = False):
        calls.append((task_id, include_buildable))
        return {
            "base_progression": {
                "status": "下一步：矿场",
                "next_unit_type": "proc",
                "next_queue_type": "Building",
                "buildable_now": True,
            },
            "buildable_now": {"Building": ["proc"]} if include_buildable else {},
        }

    bundle = build_live_task_replay_bundle(
        "t_cap",
        [
            {
                "timestamp": 10.0,
                "component": "kernel",
                "level": "INFO",
                "message": "Task created",
                "event": "task_created",
                "data": {"task_id": "t_cap"},
            }
        ],
        runtime_state={},
        tasks=[FakeTask("t_cap", is_capability=True)],
        jobs_for_task=lambda _task_id: [],
        task_payload_builder=lambda *_args, **_kwargs: {
            "task_id": "t_cap",
            "status": "running",
            "triage": {"status_line": "能力处理中：待机"},
        },
        compute_runtime_facts=compute_runtime_facts,
    )

    assert calls == [("t_cap", True)]
    assert bundle["capability_truth"]["base_status"] == "下一步：矿场"
    assert "Building:proc" in bundle["capability_truth"]["issue_now"]
    print("  PASS: live_task_replay_bundle_fetches_buildable_truth_for_capability_tasks")

def test_task_replay_bundle_keeps_distinct_llm_turns_when_wake_attempt_missing():
    entries = [
        {
            "timestamp": 10.0,
            "component": "kernel",
            "level": "INFO",
            "message": "Task created",
            "event": "task_created",
            "data": {"task_id": "t_demo"},
        },
        {
            "timestamp": 10.1,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent llm input",
            "event": "llm_input",
            "data": {
                "task_id": "t_demo",
                "messages": [{"role": "system"}, {"role": "user", "content": "first"}],
                "tools": [{"name": "query_world"}],
            },
        },
        {
            "timestamp": 10.2,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "response_text": "first response",
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        },
        {
            "timestamp": 10.3,
            "component": "task_agent",
            "level": "DEBUG",
            "message": "TaskAgent llm input",
            "event": "llm_input",
            "data": {
                "task_id": "t_demo",
                "messages": [{"role": "system"}, {"role": "user", "content": "second"}],
                "tools": [{"name": "query_world"}],
            },
        },
        {
            "timestamp": 10.4,
            "component": "task_agent",
            "level": "INFO",
            "message": "TaskAgent LLM call succeeded",
            "event": "llm_succeeded",
            "data": {
                "task_id": "t_demo",
                "response_text": "second response",
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        },
    ]

    bundle = build_task_replay_bundle("t_demo", entries)

    assert len(bundle["llm_turns"]) == 2
    assert bundle["llm_turns"][0]["response_text"] == "first response"
    assert bundle["llm_turns"][0]["input_messages"][1]["content"] == "first"
    assert bundle["llm_turns"][1]["response_text"] == "second response"
    assert bundle["llm_turns"][1]["input_messages"][1]["content"] == "second"
    print("  PASS: task_replay_bundle_keeps_distinct_llm_turns_when_wake_attempt_missing")
