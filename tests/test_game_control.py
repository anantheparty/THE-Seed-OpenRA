"""Tests for game lifecycle control and runtime restart wiring."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_control
import main as main_module
from llm import MockProvider
from main import ApplicationRuntime, RuntimeBridge, RuntimeConfig
from models import Event
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
        self.benchmarks: list[list[dict[str, Any]]] = []
        self.client_messages: list[tuple[str, str, dict[str, Any]]] = []
        self.session_cleared = 0

    async def send_log_entry(self, payload: dict[str, Any]) -> None:
        self.log_entries.append(payload)

    async def send_benchmark(self, payload: list[dict[str, Any]]) -> None:
        self.benchmarks.append(payload)

    async def send_to_client(self, client_id: str, msg_type: str, payload: dict[str, Any]) -> None:
        self.client_messages.append((client_id, msg_type, payload))

    async def send_session_cleared(self) -> None:
        self.session_cleared += 1


class _BridgeAdjutant:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = dict(result)

    async def handle_player_input(self, text: str) -> dict[str, Any]:
        result = dict(self.result)
        result.setdefault("echo_text", text)
        return result


class _BridgeLoop:
    def register_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def register_job(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_job(self, *args, **kwargs) -> None:
        del args, kwargs


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

        await bridge._publish_logs()
        await bridge._publish_benchmarks()

        assert [entry["message"] for entry in ws.log_entries] == ["one", "two"]
        assert [entry["name"] for entry in ws.benchmarks[0]] == ["a", "b"]

        logger.info("three", event="e3")
        with benchmark.span("tool_exec", name="c"):
            pass

        await bridge._publish_logs()
        await bridge._publish_benchmarks()

        assert [entry["message"] for entry in ws.log_entries] == ["one", "two", "three"]
        assert len(ws.benchmarks) == 2
        assert [entry["name"] for entry in ws.benchmarks[1]] == ["a", "b", "c"]

        logger.info("four", event="e4")
        logger.info("five", event="e5")
        await bridge._replay_history("client-1")
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
    print("  PASS: runtime_bridge_publishes_logs_and_benchmarks_incrementally")


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
        await bridge._publish_benchmarks()
        assert bridge._benchmark_offset == 1
        assert len(ws.benchmarks[-1]) == 1

        await bridge.on_session_clear("client-1")
        assert kernel.reset_calls == 1
        assert bridge._benchmark_offset == 0
        assert ws.session_cleared == 1
        assert benchmark.records() == []
        assert logging_system.records() == []

        with benchmark.span("tool_exec", name="after-clear"):
            pass
        await bridge._publish_benchmarks()
        assert bridge._benchmark_offset == 1
        assert [entry["name"] for entry in ws.benchmarks[-1]] == ["after-clear"]

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
        await bridge._publish_logs()

        for idx in range(505):
            with benchmark.span("tool_exec", name=f"b{idx}"):
                pass

        await bridge._publish_benchmarks()
        await bridge._replay_history("client-1")

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
