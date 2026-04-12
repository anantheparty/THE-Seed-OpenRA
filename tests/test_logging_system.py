from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import logging_system
from kernel import Kernel, KernelConfig
from models import Event, Task, TaskKind
from openra_api.models import MapQueryResult, PlayerBaseInfo
from task_agent import ToolExecutor
from world_model import RefreshPolicy, WorldModel


class DummyAgent:
    def __init__(self, task, tool_executor, jobs_provider, world_summary_provider):
        self.task = task

    async def run(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def push_signal(self, signal) -> None:
        return None

    def push_event(self, event) -> None:
        return None


class SimpleWorldSource:
    def fetch_self_actors(self):
        return []

    def fetch_enemy_actors(self):
        return []

    def fetch_frozen_enemies(self):
        return []

    def fetch_economy(self):
        return PlayerBaseInfo(Cash=1000, Resources=500, Power=100, PowerDrained=30, PowerProvided=130)

    def fetch_map(self, fields=None):
        size = 2
        return MapQueryResult(
            MapWidth=size,
            MapHeight=size,
            Height=[[0] * size for _ in range(size)],
            IsVisible=[[True] * size for _ in range(size)],
            IsExplored=[[True] * size for _ in range(size)],
            Terrain=[["clear"] * size for _ in range(size)],
            ResourcesType=[["ore"] * size for _ in range(size)],
            Resources=[[10] * size for _ in range(size)],
        )

    def fetch_production_queues(self):
        return {"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}}


def setup_function() -> None:
    benchmark.clear()
    logging_system.uninstall_benchmark_logging()
    logging_system.clear()
    logging_system.stop_persistence_session()


def test_import_logging_system_has_no_benchmark_side_effect() -> None:
    original_subscribe = benchmark.subscribe
    calls: list[object] = []

    def fake_subscribe(callback):
        calls.append(callback)
        return original_subscribe(callback)

    benchmark.subscribe = fake_subscribe  # type: ignore[assignment]
    try:
        importlib.reload(logging_system)
        assert calls == []
    finally:
        benchmark.subscribe = original_subscribe  # type: ignore[assignment]


def test_log_query_and_export() -> None:
    logger = logging_system.get_logger("kernel")
    logger.info("Task created", event="task_created", task_id="t_1")
    time.sleep(0.001)
    logger.warn("Task completed with warning", event="task_completed", task_id="t_1")

    records = logging_system.query(component="kernel")
    assert len(records) == 2
    assert records[0].event == "task_created"
    assert records[1].level == "WARN"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "logs.json"
        payload = logging_system.export_json(path, component="kernel")
        written = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.loads(payload)

    assert written == serialized
    assert written[0]["component"] == "kernel"


def test_log_records_from_and_tail_records() -> None:
    logger = logging_system.get_logger("kernel")
    for idx in range(5):
        logger.info(f"event-{idx}", event=f"e_{idx}", task_id=f"t_{idx}")

    sliced = logging_system.records_from(2, limit=2)
    tail = logging_system.tail_records(limit=2)

    assert [record.message for record in sliced] == ["event-2", "event-3"]
    assert [record.message for record in tail] == ["event-3", "event-4"]
    assert logging_system.tail_records(component="kernel", limit=1)[0].message == "event-4"


def test_persistent_log_session_writes_all_and_task_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(
            tmpdir,
            session_name="test-session",
            metadata={"source": "unit-test"},
        )
        logger = logging_system.get_logger("kernel")
        logger.info("Task created", event="task_created", task_id="t_1", job_id="j_1")
        logger.warn("Runtime warning", event="runtime_warning")
        logging_system.stop_persistence_session()

        session_meta = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        latest = Path(tmpdir, "latest.txt").read_text(encoding="utf-8").strip()
        all_lines = (session_dir / "all.jsonl").read_text(encoding="utf-8").strip().splitlines()
        task_lines = (session_dir / "tasks" / "t_1.jsonl").read_text(encoding="utf-8").strip().splitlines()
        component_lines = (session_dir / "components" / "kernel.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert session_meta["metadata"]["source"] == "unit-test"
    assert session_meta["record_count"] == 2
    assert session_meta["task_counts"]["t_1"] == 1
    assert session_meta["component_counts"]["kernel"] == 2
    assert "ended_at" in session_meta
    assert latest == str(session_dir)
    assert len(all_lines) == 2
    assert len(task_lines) == 1
    assert len(component_lines) == 2
    assert json.loads(task_lines[0])["data"]["task_id"] == "t_1"


def test_persistent_log_session_persists_world_health_summary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(tmpdir, session_name="health-session")
        world_logger = logging_system.get_logger("world_model")
        world_logger.warn(
            "WorldModel actors refresh failed",
            event="world_refresh_failed",
            layer="actors",
            error="COMMAND_EXECUTION_ERROR",
            error_detail="Attempted to get trait from destroyed object",
            failure_threshold=3,
        )
        world_logger.debug(
            "WorldModel refresh completed",
            event="world_refresh_completed",
            stale=True,
            consecutive_failures=2,
            failure_threshold=3,
        )
        world_logger.warn(
            "Slow world refresh",
            event="world_refresh_slow",
            total_ms=154.2,
        )
        world_logger.debug(
            "WorldModel refresh completed",
            event="world_refresh_completed",
            stale=False,
            consecutive_failures=0,
            failure_threshold=3,
        )
        logging_system.stop_persistence_session()

        session_meta = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))

    world_health = session_meta["world_health"]
    assert world_health["stale_seen"] is True
    assert world_health["ended_stale"] is False
    assert world_health["stale_refreshes"] == 1
    assert world_health["max_consecutive_failures"] == 2
    assert world_health["failure_threshold"] == 3
    assert world_health["last_error"] == "COMMAND_EXECUTION_ERROR"
    assert world_health["last_error_detail"] == "Attempted to get trait from destroyed object"
    assert world_health["last_failure_layer"] == "actors"
    assert world_health["slow_events"] == 1
    assert world_health["max_total_ms"] == 154.2


def test_read_task_replay_records_falls_back_to_latest_session() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = logging_system.start_persistence_session(
            tmpdir,
            session_name="replay-session",
        )
        task_path = session_dir / "tasks" / "t_replay.jsonl"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            json.dumps(
                {
                    "timestamp": 1.0,
                    "component": "kernel",
                    "level": "INFO",
                    "message": "Task created",
                    "event": "task_created",
                    "data": {"task_id": "t_replay"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        logging_system.stop_persistence_session()

        assert logging_system.current_session_dir() is None
        assert logging_system.latest_session_dir(tmpdir) == session_dir
        records = logging_system.read_task_replay_records("t_replay", latest_base_dir=tmpdir)

    assert len(records) == 1
    assert records[0]["data"]["task_id"] == "t_replay"


def test_latest_session_dir_resolves_relative_latest_txt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-relative"
        session_dir.mkdir(parents=True, exist_ok=True)
        (base / "latest.txt").write_text("session-relative\n", encoding="utf-8")

        resolved = logging_system.latest_session_dir(base)

    assert resolved == session_dir.resolve()


def test_list_persistence_sessions_and_session_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = logging_system.start_persistence_session(
            base,
            session_name="session-a",
            metadata={"source": "unit-test"},
        )
        task_path = session_dir / "tasks" / "t_demo.jsonl"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task created",
                            "event": "task_created",
                            "data": {
                                "task_id": "t_demo",
                                "task_label": "004",
                                "raw_text": "发展科技",
                                "kind": "managed",
                                "priority": 60,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 12.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task completed",
                            "event": "task_completed",
                            "data": {
                                "task_id": "t_demo",
                                "result": "partial",
                                "summary": "缺少前置，部分完成",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        logging_system.stop_persistence_session()

        sessions = logging_system.list_persistence_sessions(base)
        tasks = logging_system.list_session_tasks(session_dir)

    assert len(sessions) == 1
    assert sessions[0]["session_name"] == "session-a"
    assert sessions[0]["metadata"]["source"] == "unit-test"
    assert sessions[0]["is_latest"] is True
    assert tasks == [
        {
            "task_id": "t_demo",
            "raw_text": "发展科技",
            "label": "004",
            "kind": "managed",
            "priority": 60,
            "status": "partial",
            "timestamp": 10.0,
            "created_at": 10.0,
            "entry_count": 2,
            "summary": "缺少前置，部分完成",
            "log_path": str((session_dir / "tasks" / "t_demo.jsonl").resolve()),
        }
    ]


def test_list_session_tasks_falls_back_to_latest_task_message_summary_when_no_terminal_summary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-b"
        tasks_dir = session_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_name": "session-b",
                    "started_at": "2026-04-12T00:00:00+00:00",
                    "metadata": {"source": "unit-test"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tasks_dir / "t_demo.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task created",
                            "event": "task_created",
                            "data": {
                                "task_id": "t_demo",
                                "task_label": "004",
                                "raw_text": "发展科技",
                                "kind": "managed",
                                "priority": 60,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 11.0,
                            "component": "dashboard_publish",
                            "level": "INFO",
                            "message": "Task message published",
                            "event": "task_info",
                            "data": {
                                "task_id": "t_demo",
                                "content": "缺少战车工厂，等待能力层补前置",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 12.0,
                            "component": "dashboard_publish",
                            "level": "INFO",
                            "message": "Task message published",
                            "event": "task_warning",
                            "data": {
                                "task_id": "t_demo",
                                "content": "世界状态同步异常，暂停动作等待恢复",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        tasks = logging_system.list_session_tasks(session_dir)

    assert tasks == [
        {
            "task_id": "t_demo",
            "raw_text": "发展科技",
            "label": "004",
            "kind": "managed",
            "priority": 60,
            "status": "running",
            "timestamp": 10.0,
            "created_at": 10.0,
            "entry_count": 3,
            "summary": "世界状态同步异常，暂停动作等待恢复",
            "log_path": str((session_dir / "tasks" / "t_demo.jsonl").resolve()),
        }
    ]


def test_list_session_tasks_ignores_task_message_registered_without_content() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-c"
        tasks_dir = session_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_name": "session-c",
                    "started_at": "2026-04-12T00:00:00+00:00",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tasks_dir / "t_demo.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task created",
                            "event": "task_created",
                            "data": {
                                "task_id": "t_demo",
                                "task_label": "004",
                                "raw_text": "发展科技",
                                "kind": "managed",
                                "priority": 60,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 11.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task message registered",
                            "event": "task_message_registered",
                            "data": {
                                "task_id": "t_demo",
                                "message_type": "task_warning",
                                "message_id": "m_warn",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        tasks = logging_system.list_session_tasks(session_dir)

    assert tasks[0]["summary"] == ""


def test_list_session_tasks_keeps_terminal_summary_over_later_task_message() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-d"
        tasks_dir = session_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_name": "session-d",
                    "started_at": "2026-04-12T00:00:00+00:00",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tasks_dir / "t_demo.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task created",
                            "event": "task_created",
                            "data": {
                                "task_id": "t_demo",
                                "task_label": "004",
                                "raw_text": "发展科技",
                                "kind": "managed",
                                "priority": 60,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 12.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task completed",
                            "event": "task_completed",
                            "data": {
                                "task_id": "t_demo",
                                "result": "partial",
                                "summary": "缺少前置，部分完成",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 13.0,
                            "component": "dashboard_publish",
                            "level": "INFO",
                            "message": "Task message published",
                            "event": "task_warning",
                            "data": {
                                "task_id": "t_demo",
                                "summary": "后续 warning 不应覆盖终态",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        tasks = logging_system.list_session_tasks(session_dir)

    assert tasks[0]["status"] == "partial"
    assert tasks[0]["summary"] == "缺少前置，部分完成"


def test_list_session_tasks_falls_back_to_latest_expert_signal_summary_for_older_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-e"
        tasks_dir = session_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_name": "session-e",
                    "started_at": "2026-04-12T00:00:00+00:00",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tasks_dir / "t_demo.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "kernel",
                            "level": "INFO",
                            "message": "Task created",
                            "event": "task_created",
                            "data": {
                                "task_id": "t_demo",
                                "task_label": "004",
                                "raw_text": "发展科技",
                                "kind": "managed",
                                "priority": 60,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 11.0,
                            "component": "expert",
                            "level": "INFO",
                            "message": "Expert signal emitted",
                            "event": "expert_signal",
                            "data": {
                                "task_id": "t_demo",
                                "signal_kind": "blocked",
                                "summary": "缺少战车工厂，无法继续生产坦克",
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        tasks = logging_system.list_session_tasks(session_dir)

    assert tasks[0]["summary"] == "缺少战车工厂，无法继续生产坦克"


def test_list_persistence_sessions_backfills_world_health_from_component_logs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        session_dir = base / "session-backfill"
        (session_dir / "components").mkdir(parents=True, exist_ok=True)
        (base / "latest.txt").write_text("session-backfill\n", encoding="utf-8")
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_name": "session-backfill",
                    "started_at": "2026-04-12T00:00:00+00:00",
                    "metadata": {"source": "unit-test"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (session_dir / "components" / "world_model.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 10.0,
                            "component": "world_model",
                            "level": "WARN",
                            "message": "WorldModel actors refresh failed",
                            "event": "world_refresh_failed",
                            "data": {
                                "layer": "actors",
                                "error": "COMMAND_EXECUTION_ERROR",
                                "error_detail": "actor destroyed",
                                "failure_threshold": 3,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 11.0,
                            "component": "world_model",
                            "level": "DEBUG",
                            "message": "WorldModel refresh completed",
                            "event": "world_refresh_completed",
                            "data": {
                                "stale": True,
                                "consecutive_failures": 3,
                                "failure_threshold": 3,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": 12.0,
                            "component": "world_model",
                            "level": "WARN",
                            "message": "Slow world refresh",
                            "event": "world_refresh_slow",
                            "data": {
                                "total_ms": 167.4,
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        sessions = logging_system.list_persistence_sessions(base)
        session_meta = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))

    assert sessions[0]["world_health"]["stale_seen"] is True
    assert sessions[0]["world_health"]["ended_stale"] is True
    assert sessions[0]["world_health"]["stale_refreshes"] == 1
    assert sessions[0]["world_health"]["max_consecutive_failures"] == 3
    assert sessions[0]["world_health"]["failure_threshold"] == 3
    assert sessions[0]["world_health"]["last_failure_layer"] == "actors"
    assert sessions[0]["world_health"]["slow_events"] == 1
    assert sessions[0]["world_health"]["max_total_ms"] == 167.4
    assert session_meta["world_health"] == sessions[0]["world_health"]


def test_benchmark_summary_and_logging_integration() -> None:
    logging_system.install_benchmark_logging()
    try:
        with benchmark.span("tool_exec", name="fast"):
            time.sleep(0.001)
        with benchmark.span("tool_exec", name="slow"):
            time.sleep(0.002)
        with benchmark.span("llm_call", name="chat"):
            time.sleep(0.001)

        summary = logging_system.summarize_benchmarks()
        by_tag = {item["tag"]: item for item in summary}
        assert by_tag["tool_exec"]["count"] == 2
        assert by_tag["tool_exec"]["max_ms"] >= by_tag["tool_exec"]["avg_ms"]
        assert by_tag["llm_call"]["count"] == 1

        bench_logs = logging_system.query(component="benchmark", event="benchmark_recorded")
        assert len(bench_logs) >= 3
    finally:
        logging_system.uninstall_benchmark_logging()


def test_runtime_components_emit_structured_logs() -> None:
    world = WorldModel(SimpleWorldSource(), refresh_policy=RefreshPolicy(actors_s=0.01, economy_s=0.01, map_s=0.01))
    world.refresh(force=True)

    kernel = Kernel(
        world_model=world,
        task_agent_factory=lambda task, te, jp, wsp: DummyAgent(task, te, jp, wsp),
        config=KernelConfig(auto_start_agents=False),
    )
    task = kernel.create_task("侦察地图", TaskKind.MANAGED, 60)
    assert kernel.complete_task(task.task_id, "succeeded", "done")

    world_logs = logging_system.query(component="world_model", event="world_refresh_completed")
    kernel_logs = logging_system.query(component="kernel")

    assert len(world_logs) >= 1
    assert any(record.event == "task_created" for record in kernel_logs)
    assert any(record.event == "task_completed" for record in kernel_logs)


def test_tool_executor_emits_structured_logs() -> None:
    executor = ToolExecutor()

    async def echo(_name: str, args: dict[str, object]) -> dict[str, object]:
        return {"echo": args["value"]}

    executor.register("echo", echo)
    result = asyncio.run(executor.execute("tc_1", "echo", '{"value":"ok"}'))

    assert result.error is None
    logs = logging_system.query(component="task_agent")
    assert any(record.event == "tool_execute" for record in logs)
    assert any(record.event == "tool_execute_completed" for record in logs)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
