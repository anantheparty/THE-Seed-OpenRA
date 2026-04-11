"""Tests for game lifecycle control and runtime restart wiring."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from typing import Any

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_control
import main as main_module
from llm import MockProvider
from main import ApplicationRuntime, RuntimeBridge, RuntimeConfig
from models import Event, Task, TaskKind, TaskMessage, TaskMessageType
from tests.test_world_model import MockWorldSource, make_frames


class _FakeCompletedProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _FakePopen:
    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = 43210


class _CloseTrackingAPI:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _BridgeKernel:
    def __init__(self) -> None:
        self.reset_calls = 0

    def submit_player_response(self, response, *, now=None):
        del response, now
        return {"ok": True, "status": "delivered", "message": "已收到回复"}

    def list_task_messages(self):
        return []

    def list_player_notifications(self):
        return []

    def reset_session(self) -> None:
        self.reset_calls += 1


class _BridgeWS:
    def __init__(self) -> None:
        self.is_running = True
        self.query_responses: list[dict[str, Any]] = []
        self.player_notifications: list[dict[str, Any]] = []

    async def send_query_response(self, payload: dict[str, Any]) -> None:
        self.query_responses.append(payload)

    async def send_player_notification(self, payload: dict[str, Any]) -> None:
        self.player_notifications.append(payload)


class _BridgePublishWS:
    def __init__(self) -> None:
        self.is_running = True
        self.log_entries: list[dict[str, Any]] = []
        self.benchmarks: list[dict[str, Any]] = []
        self.client_messages: list[tuple[str, str, dict[str, Any]]] = []
        self.session_cleared = 0

    async def send_log_entry(self, payload: dict[str, Any]) -> None:
        self.log_entries.append(payload)

    async def send_benchmark(self, payload: dict[str, Any]) -> None:
        self.benchmarks.append(payload)

    async def send_to_client(self, client_id: str, msg_type: str, payload: dict[str, Any]) -> None:
        self.client_messages.append((client_id, msg_type, payload))

    async def send_session_cleared(self) -> None:
        self.session_cleared += 1

    async def send_session_catalog_to_client(self, client_id: str, payload: dict[str, Any]) -> None:
        del client_id, payload

    async def send_session_task_catalog_to_client(self, client_id: str, payload: dict[str, Any]) -> None:
        del client_id, payload


class _BridgeAdjutant:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = dict(result)

    async def handle_player_input(self, text: str) -> dict[str, Any]:
        result = dict(self.result)
        result.setdefault("echo_text", text)
        return result


class _BridgeNotificationAdjutant:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def notify_task_completed(
        self,
        *,
        label: str,
        raw_text: str,
        result: str,
        summary: str,
        task_id: str,
    ) -> None:
        self.completed.append(
            {
                "label": label,
                "raw_text": raw_text,
                "result": result,
                "summary": summary,
                "task_id": task_id,
            }
        )

    def notify_task_message(
        self,
        *,
        task_id: str,
        message_type: str,
        content: str,
    ) -> None:
        self.messages.append(
            {
                "task_id": task_id,
                "message_type": message_type,
                "content": content,
            }
        )


class _BridgeLoop:
    def register_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def register_job(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_job(self, *args, **kwargs) -> None:
        del args, kwargs


class _BridgeTaskKernel(_BridgeKernel):
    def __init__(self, tasks: list[Task]) -> None:
        super().__init__()
        self._tasks = list(tasks)

    def list_tasks(self):
        return list(self._tasks)


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_game_passes_baseline_save() -> None:
    captured: dict[str, Any] = {}
    original_popen = game_control.subprocess.Popen
    try:
        def fake_popen(args, **kwargs):
            captured["args"] = list(args)
            captured["kwargs"] = dict(kwargs)
            return _FakePopen(list(args), **kwargs)

        game_control.subprocess.Popen = fake_popen  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = game_control.GameControlConfig(
                openra_dir=Path(tmpdir),
                log_path=Path(tmpdir) / "openra.log",
            )
            pid = game_control.start_game(save_path="baseline.orasav", config=cfg)

        assert pid == 43210
        assert "Game.LoadSave=baseline.orasav" in captured["args"]
        assert captured["kwargs"]["cwd"] == Path(tmpdir)
        assert captured["kwargs"]["env"]["DISPLAY"] == cfg.display
        print("  PASS: start_game_passes_baseline_save")
    finally:
        game_control.subprocess.Popen = original_popen  # type: ignore[assignment]


def test_wait_for_api_polls_until_ready() -> None:
    calls = {"count": 0}
    original_check = game_control.GameAPI.is_server_running
    original_sleep = game_control.time.sleep
    try:
        def fake_check(host="localhost", port=7445, timeout=2.0):
            del host, port, timeout
            calls["count"] += 1
            return calls["count"] >= 3

        game_control.GameAPI.is_server_running = staticmethod(fake_check)  # type: ignore[assignment]
        game_control.time.sleep = lambda _: None  # type: ignore[assignment]
        assert game_control.wait_for_api(timeout=0.1, poll_interval=0.01)
        assert calls["count"] == 3
        print("  PASS: wait_for_api_polls_until_ready")
    finally:
        game_control.GameAPI.is_server_running = original_check  # type: ignore[assignment]
        game_control.time.sleep = original_sleep  # type: ignore[assignment]


def test_cli_restart_forwards_save_path() -> None:
    captured: dict[str, Any] = {}
    original_restart = game_control.restart_game
    original_wait = game_control.wait_for_api
    try:
        def fake_restart(save_path=None, config=None):
            captured["save_path"] = save_path
            captured["config"] = config
            return 123

        def fake_wait(timeout=30.0, *, host=None, port=None, language="zh", poll_interval=0.5):
            captured["wait"] = {
                "timeout": timeout,
                "host": host,
                "port": port,
                "language": language,
                "poll_interval": poll_interval,
            }
            return True

        game_control.restart_game = fake_restart  # type: ignore[assignment]
        game_control.wait_for_api = fake_wait  # type: ignore[assignment]
        rc = game_control.main(["restart", "--save", "baseline.orasav", "--wait-timeout", "0"])
        assert rc == 0
        assert captured["save_path"] == "baseline.orasav"
        assert captured["config"].port == 7445
        print("  PASS: cli_restart_forwards_save_path")
    finally:
        game_control.restart_game = original_restart  # type: ignore[assignment]
        game_control.wait_for_api = original_wait  # type: ignore[assignment]


def test_application_runtime_restart_game() -> None:
    provider = MockProvider([])
    source = MockWorldSource(make_frames())
    api = _CloseTrackingAPI()
    original_restart = main_module.game_control.restart_game
    original_wait = main_module.game_control.wait_for_api
    original_is_running = main_module.game_control.GameAPI.is_server_running
    original_install = main_module.install_benchmark_logging
    calls: dict[str, Any] = {}

    async def run() -> None:
        runtime = ApplicationRuntime(
            config=RuntimeConfig(enable_ws=False, verify_game_api=False, llm_provider="mock", llm_model="mock"),
            task_llm=provider,
            adjutant_llm=provider,
            api=api,
            world_source=source,
            expert_registry={},
        )
        try:
            await runtime.start()
            await asyncio.sleep(0.05)
            assert calls["install_benchmark_logging"] == 1
            assert runtime.game_loop.is_running
            result = await runtime.restart_game(save_path="baseline.orasav")
            await asyncio.sleep(0.05)
            assert result["ok"] is True
            assert calls["save_path"] == "baseline.orasav"
            assert runtime.game_loop.is_running
            assert api.close_calls == 1
            assert source.actor_fetches >= 2
        finally:
            await runtime.stop()
        assert api.close_calls == 2

    try:
        def fake_install_benchmark_logging() -> None:
            calls["install_benchmark_logging"] = calls.get("install_benchmark_logging", 0) + 1

        def fake_restart(save_path=None, config=None):
            calls["save_path"] = save_path
            calls["config"] = config
            return 222

        def fake_wait(timeout=30.0, *, host=None, port=None, language="zh", poll_interval=0.5):
            calls["wait"] = (timeout, host, port, language, poll_interval)
            return True

        main_module.install_benchmark_logging = fake_install_benchmark_logging  # type: ignore[assignment]
        main_module.game_control.restart_game = fake_restart  # type: ignore[assignment]
        main_module.game_control.wait_for_api = fake_wait  # type: ignore[assignment]
        main_module.game_control.GameAPI.is_server_running = staticmethod(lambda *args, **kwargs: True)  # type: ignore[assignment]
        asyncio.run(run())
        print("  PASS: application_runtime_restart_game")
    finally:
        main_module.install_benchmark_logging = original_install  # type: ignore[assignment]
        main_module.game_control.restart_game = original_restart  # type: ignore[assignment]
        main_module.game_control.wait_for_api = original_wait  # type: ignore[assignment]
        main_module.game_control.GameAPI.is_server_running = original_is_running  # type: ignore[assignment]


def test_runtime_defaults_are_demo_friendly() -> None:
    cfg = RuntimeConfig()
    assert cfg.map_refresh_s == 5.0
    assert cfg.enable_voice is False
    print("  PASS: runtime_defaults_are_demo_friendly")


def test_parse_args_defaults_are_demo_friendly() -> None:
    original_loader = main_module._load_env_file
    original_world_map_refresh = os.environ.pop("WORLD_MAP_REFRESH_S", None)
    try:
        main_module._load_env_file = lambda path=".env": None  # type: ignore[assignment]
        cfg = main_module.parse_args([])
        assert cfg.map_refresh_s == 5.0
        assert cfg.enable_voice is False
        print("  PASS: parse_args_defaults_are_demo_friendly")
    finally:
        main_module._load_env_file = original_loader  # type: ignore[assignment]
        if original_world_map_refresh is None:
            os.environ.pop("WORLD_MAP_REFRESH_S", None)
        else:
            os.environ["WORLD_MAP_REFRESH_S"] = original_world_map_refresh


@pytest.mark.contract
def test_runtime_bridge_command_feedback_uses_query_response() -> None:
    async def run() -> None:
        bridge = RuntimeBridge(
            kernel=_BridgeKernel(),
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=_BridgeAdjutant({"type": "command", "ok": True, "response_text": "收到指令，已创建任务"}),
        )
        bridge.sync_runtime = lambda: None  # type: ignore[method-assign]

        async def _noop_publish() -> None:
            return None

        bridge.publish_dashboard = _noop_publish  # type: ignore[method-assign]
        ws = _BridgeWS()
        bridge.attach_ws_server(ws)
        await bridge.on_command_submit("生产5辆坦克", "client_1")

        assert len(ws.query_responses) == 1
        response = ws.query_responses[0]
        assert response["answer"] == "收到指令，已创建任务"
        assert response["response_type"] == "command"
        assert response["ok"] is True
        assert response["type"] == "command"
        assert response["echo_text"] == "生产5辆坦克"
        assert response["timestamp"] > 0
        assert ws.player_notifications == []

    asyncio.run(run())
    print("  PASS: runtime_bridge_command_feedback_uses_query_response")


def test_runtime_bridge_question_reply_success_is_visible() -> None:
    async def run() -> None:
        bridge = RuntimeBridge(
            kernel=_BridgeKernel(),
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=None,
        )
        bridge.sync_runtime = lambda: None  # type: ignore[method-assign]

        async def _noop_publish() -> None:
            return None

        bridge.publish_dashboard = _noop_publish  # type: ignore[method-assign]
        ws = _BridgeWS()
        bridge.attach_ws_server(ws)
        await bridge.on_question_reply("msg_1", "t_1", "继续", "client_1")

        assert len(ws.query_responses) == 1
        response = ws.query_responses[0]
        assert response["answer"] == "已收到回复"
        assert response["response_type"] == "reply"
        assert response["ok"] is True
        assert response["task_id"] == "t_1"
        assert response["message_id"] == "msg_1"
        assert response["status"] == "delivered"
        assert response["timestamp"] > 0
        assert ws.player_notifications == []

    asyncio.run(run())
    print("  PASS: runtime_bridge_question_reply_success_is_visible")


@pytest.mark.contract
def test_runtime_bridge_published_task_messages_notify_adjutant() -> None:
    task = Task(task_id="t_1", raw_text="建造电厂", kind=TaskKind.MANAGED, priority=50)
    task.label = "001"
    adjutant = _BridgeNotificationAdjutant()
    bridge = RuntimeBridge(
        kernel=_BridgeTaskKernel([task]),
        world_model=type("WM", (), {})(),
        game_loop=_BridgeLoop(),
        adjutant=adjutant,
    )

    bridge._handle_published_task_message(
        TaskMessage(
            message_id="m_info",
            task_id="t_1",
            type=TaskMessageType.TASK_INFO,
            content="电力不足，等待恢复",
        )
    )
    bridge._handle_published_task_message(
        TaskMessage(
            message_id="m_done",
            task_id="t_1",
            type=TaskMessageType.TASK_COMPLETE_REPORT,
            content="电厂已建成",
        )
    )

    assert adjutant.messages == [
        {
            "task_id": "t_1",
            "message_type": TaskMessageType.TASK_INFO.value,
            "content": "电力不足，等待恢复",
        }
    ]
    assert adjutant.completed == [
        {
            "label": "001",
            "raw_text": "建造电厂",
            "result": task.status.value,
            "summary": "电厂已建成",
            "task_id": "t_1",
        }
    ]
    print("  PASS: runtime_bridge_published_task_messages_notify_adjutant")


def test_runtime_bridge_publishes_logs_and_benchmarks_incrementally() -> None:
    import benchmark
    import logging_system

    async def run() -> None:
        bridge = RuntimeBridge(
            kernel=_BridgeKernel(),
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=None,
        )
        ws = _BridgePublishWS()
        bridge.attach_ws_server(ws)

        logger = logging_system.get_logger("kernel")
        logger.info("one", event="e1")
        logger.info("two", event="e2")
        with benchmark.span("tool_exec", name="a"):
            pass
        with benchmark.span("tool_exec", name="b"):
            pass

        await bridge._publisher.publish_logs()
        await bridge._publisher.publish_benchmarks()

        assert [entry["message"] for entry in ws.log_entries] == ["one", "two"]
        assert [entry["name"] for entry in ws.benchmarks[0]["records"]] == ["a", "b"]
        assert ws.benchmarks[0]["replace"] is False

        logger.info("three", event="e3")
        with benchmark.span("tool_exec", name="c"):
            pass

        await bridge._publisher.publish_logs()
        await bridge._publisher.publish_benchmarks()

        assert [entry["message"] for entry in ws.log_entries] == ["one", "two", "three"]
        assert len(ws.benchmarks) == 2
        assert [entry["name"] for entry in ws.benchmarks[1]["records"]] == ["c"]

        logger.info("four", event="e4")
        logger.info("five", event="e5")
        await bridge._publisher.replay_history("client-1")
        replay_logs = [
            payload["message"]
            for client_id, msg_type, payload in ws.client_messages
            if client_id == "client-1" and msg_type == "log_entry"
        ]
        assert replay_logs == ["one", "two", "three"]
        replay_benchmarks = [
            payload["records"]
            for client_id, msg_type, payload in ws.client_messages
            if client_id == "client-1" and msg_type == "benchmark"
        ]
        assert replay_benchmarks
        assert [entry["name"] for entry in replay_benchmarks[-1]] == ["a", "b", "c"]

    logging_system.clear()
    benchmark.clear()
    asyncio.run(run())


@pytest.mark.startup_smoke
def test_application_runtime_ws_startup_smoke_and_background_publish() -> None:
    provider = MockProvider([])
    source = MockWorldSource(make_frames())
    api = _CloseTrackingAPI()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        background_errors: list[dict[str, Any]] = []

        def _capture_loop_exception(loop, context) -> None:
            del loop
            background_errors.append(dict(context))

        loop.set_exception_handler(_capture_loop_exception)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                ws_port = _free_tcp_port()
                cfg = RuntimeConfig(
                    ws_host="127.0.0.1",
                    ws_port=ws_port,
                    enable_ws=True,
                    enable_voice=False,
                    log_session_root=str(Path(tmpdir) / "logs"),
                    benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
                    benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
                    log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
                )
                runtime = ApplicationRuntime(
                    config=cfg,
                    task_llm=provider,
                    adjutant_llm=provider,
                    api=api,
                    world_source=source,
                )
                try:
                    await runtime.start()

                    cap_id = runtime.kernel.capability_task_id
                    assert cap_id is not None
                    cap_agent = runtime.kernel.get_task_agent(cap_id)
                    assert cap_agent is not None
                    assert "produce_units" in cap_agent.tool_executor._handlers
                    assert "request_units" not in cap_agent.tool_executor._handlers

                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                            await ws.send_json({"type": "sync_request"})
                            seen_types: set[str] = set()
                            for _ in range(12):
                                msg = await ws.receive(timeout=2.0)
                                assert msg.type == aiohttp.WSMsgType.TEXT
                                payload = json.loads(msg.data)
                                seen_types.add(str(payload.get("type")))
                                if {"world_snapshot", "task_list", "session_catalog"} <= seen_types:
                                    break
                            assert "world_snapshot" in seen_types
                            assert "task_list" in seen_types
                            assert "session_catalog" in seen_types

                    runtime.bridge.on_tick(1, 0.0)
                    await asyncio.sleep(0.1)
                    assert background_errors == [], background_errors
                finally:
                    await runtime.stop()

                assert api.close_calls == 1
                assert runtime.ws_server is not None
                assert runtime.ws_server.is_running is False
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    assert probe.connect_ex(("127.0.0.1", ws_port)) != 0
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: application_runtime_ws_startup_smoke_and_background_publish")


def test_runtime_bridge_session_clear_resets_benchmark_publish_state() -> None:
    import benchmark
    import logging_system

    async def run() -> None:
        kernel = _BridgeKernel()
        bridge = RuntimeBridge(
            kernel=kernel,
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=None,
        )
        bridge.sync_runtime = lambda: None  # type: ignore[method-assign]

        async def _noop_publish() -> None:
            return None

        bridge.publish_dashboard = _noop_publish  # type: ignore[method-assign]
        ws = _BridgePublishWS()
        bridge.attach_ws_server(ws)

        with benchmark.span("tool_exec", name="before-clear"):
            pass
        await bridge._publisher.publish_benchmarks()
        assert bridge._publisher.benchmark_offset == 1
        assert len(ws.benchmarks[-1]["records"]) == 1

        await bridge.on_session_clear("client-1")
        assert kernel.reset_calls == 1
        assert bridge._publisher.benchmark_offset == 0
        assert ws.session_cleared == 1
        assert benchmark.records() == []
        records = logging_system.records()
        assert len(records) == 1
        assert records[0].event == "log_session_rotated"

        with benchmark.span("tool_exec", name="after-clear"):
            pass
        await bridge._publisher.publish_benchmarks()
        assert bridge._publisher.benchmark_offset == 1
        assert [entry["name"] for entry in ws.benchmarks[-1]["records"]] == ["after-clear"]

    logging_system.clear()
    benchmark.clear()
    asyncio.run(run())
    print("  PASS: runtime_bridge_session_clear_resets_benchmark_publish_state")


def test_runtime_bridge_replay_history_sends_full_benchmark_snapshot() -> None:
    import benchmark
    import logging_system

    async def run() -> None:
        bridge = RuntimeBridge(
            kernel=_BridgeKernel(),
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=None,
        )
        ws = _BridgePublishWS()
        bridge.attach_ws_server(ws)

        logger = logging_system.get_logger("kernel")
        logger.info("published", event="e1")
        await bridge._publisher.publish_logs()

        for idx in range(505):
            with benchmark.span("tool_exec", name=f"b{idx}"):
                pass

        await bridge._publisher.publish_benchmarks()
        await bridge._publisher.replay_history("client-1")

        replay_benchmarks = [
            payload["records"]
            for client_id, msg_type, payload in ws.client_messages
            if client_id == "client-1" and msg_type == "benchmark"
        ]
        assert replay_benchmarks
        assert len(replay_benchmarks[-1]) == 505
        assert replay_benchmarks[-1][0]["name"] == "b0"
        assert replay_benchmarks[-1][-1]["name"] == "b504"

    logging_system.clear()
    benchmark.clear()
    asyncio.run(run())
    print("  PASS: runtime_bridge_replay_history_sends_full_benchmark_snapshot")


def test_build_provider_fails_fast_when_qwen_dependency_missing() -> None:
    original_find_spec = main_module.importlib.util.find_spec
    try:
        def fake_find_spec(name: str):
            if name == "openai":
                return None
            return original_find_spec(name)

        main_module.importlib.util.find_spec = fake_find_spec  # type: ignore[assignment]
        try:
            main_module._build_provider("qwen", "qwen-plus")
            raise AssertionError("expected RuntimeError for missing openai dependency")
        except RuntimeError as exc:
            assert "requires Python package 'openai'" in str(exc)
        print("  PASS: build_provider_fails_fast_when_qwen_dependency_missing")
    finally:
        main_module.importlib.util.find_spec = original_find_spec  # type: ignore[assignment]


def test_build_provider_fails_fast_when_anthropic_dependency_missing() -> None:
    original_find_spec = main_module.importlib.util.find_spec
    try:
        def fake_find_spec(name: str):
            if name == "anthropic":
                return None
            return original_find_spec(name)

        main_module.importlib.util.find_spec = fake_find_spec  # type: ignore[assignment]
        try:
            main_module._build_provider("anthropic", "claude-sonnet-4-20250514")
            raise AssertionError("expected RuntimeError for missing anthropic dependency")
        except RuntimeError as exc:
            assert "requires Python package 'anthropic'" in str(exc)
        print("  PASS: build_provider_fails_fast_when_anthropic_dependency_missing")
    finally:
        main_module.importlib.util.find_spec = original_find_spec  # type: ignore[assignment]


def test_build_provider_fails_fast_when_socks_proxy_support_missing() -> None:
    original_find_spec = main_module.importlib.util.find_spec
    original_all_proxy = os.environ.get("ALL_PROXY")
    try:
        os.environ["ALL_PROXY"] = "socks5://127.0.0.1:7890"

        def fake_find_spec(name):
            if name == "openai":
                return object()
            if name == "socksio":
                return None
            return original_find_spec(name)

        main_module.importlib.util.find_spec = fake_find_spec  # type: ignore[assignment]

        try:
            main_module._build_provider("qwen", "qwen-plus")
            raise AssertionError("expected RuntimeError for missing socksio dependency")
        except RuntimeError as exc:
            assert "SOCKS proxy" in str(exc)
            assert "socksio" in str(exc)
        print("  PASS: build_provider_fails_fast_when_socks_proxy_support_missing")
    finally:
        main_module.importlib.util.find_spec = original_find_spec  # type: ignore[assignment]
        if original_all_proxy is None:
            os.environ.pop("ALL_PROXY", None)
        else:
            os.environ["ALL_PROXY"] = original_all_proxy


if __name__ == "__main__":
    print("Running game control tests...\n")
    test_start_game_passes_baseline_save()
    test_wait_for_api_polls_until_ready()
    test_cli_restart_forwards_save_path()
    test_application_runtime_restart_game()
    test_runtime_defaults_are_demo_friendly()
    test_parse_args_defaults_are_demo_friendly()
    test_runtime_bridge_command_feedback_uses_query_response()
    test_runtime_bridge_question_reply_success_is_visible()
    test_runtime_bridge_publishes_logs_and_benchmarks_incrementally()
    test_runtime_bridge_session_clear_resets_benchmark_publish_state()
    test_runtime_bridge_replay_history_sends_full_benchmark_snapshot()
    test_build_provider_fails_fast_when_qwen_dependency_missing()
    test_build_provider_fails_fast_when_anthropic_dependency_missing()
    test_build_provider_fails_fast_when_socks_proxy_support_missing()
    print("\nAll 13 tests passed!")
