"""Tests for game lifecycle control and runtime restart wiring."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
from typing import Any

import aiohttp
import benchmark
import logging_system
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_control
import main as main_module
from llm import MockProvider
from main import ApplicationRuntime, RuntimeBridge, RuntimeConfig
from models import Event, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus
from openra_api.models import Actor, Location, PlayerBaseInfo
from tests.test_world_model import Frame, MockWorldSource, make_frames, make_map


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
        self.cancel_ok = True
        self.cancelled_task_id: str | None = None

    def submit_player_response(self, response, *, now=None):
        del response, now
        return {"ok": True, "status": "delivered", "message": "已收到回复"}

    def list_task_messages(self):
        return []

    def list_player_notifications(self):
        return []

    def reset_session(self) -> None:
        self.reset_calls += 1

    def cancel_task(self, task_id: str) -> bool:
        self.cancelled_task_id = task_id
        return self.cancel_ok


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
        self.world_snapshots: list[dict[str, Any]] = []
        self.task_lists: list[dict[str, Any]] = []
        self.task_updates: list[dict[str, Any]] = []
        self.task_messages: list[dict[str, Any]] = []
        self.player_notifications: list[dict[str, Any]] = []
        self.log_entries: list[dict[str, Any]] = []
        self.benchmarks: list[dict[str, Any]] = []
        self.client_messages: list[tuple[str, str, dict[str, Any]]] = []
        self.session_cleared = 0

    async def send_world_snapshot(self, payload: dict[str, Any]) -> None:
        self.world_snapshots.append(payload)

    async def send_task_list(self, tasks: list[dict[str, Any]], pending_questions=None) -> None:
        self.task_lists.append({
            "tasks": list(tasks),
            "pending_questions": list(pending_questions or []),
        })

    async def send_task_update(self, payload: dict[str, Any]) -> None:
        self.task_updates.append(payload)

    async def send_task_message(self, payload: dict[str, Any]) -> None:
        self.task_messages.append(payload)

    async def send_player_notification(self, payload: dict[str, Any]) -> None:
        self.player_notifications.append(payload)

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
    def __init__(self) -> None:
        self.reset_runtime_calls = 0

    def register_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_agent(self, *args, **kwargs) -> None:
        del args, kwargs

    def register_job(self, *args, **kwargs) -> None:
        del args, kwargs

    def unregister_job(self, *args, **kwargs) -> None:
        del args, kwargs

    def reset_runtime_state(self) -> None:
        self.reset_runtime_calls += 1


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


def test_parse_args_cli_flags_override_entry_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_VOICE", "0")
    cfg = main_module.parse_args([
        "--disable-ws",
        "--enable-voice",
        "--skip-game-api-check",
    ])

    assert cfg.enable_ws is False
    assert cfg.enable_voice is True
    assert cfg.verify_game_api is False


def test_run_runtime_preflight_failure_finalizes_persistence_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: False))

        exit_code = asyncio.run(main_module.run_runtime(cfg))

        latest_session = logging_system.latest_session_dir(log_root)
        assert exit_code == 2
        assert logging_system.current_session_dir() is None
        assert latest_session is not None
        session_meta = json.loads((latest_session / "session.json").read_text(encoding="utf-8"))
        assert session_meta["ended_at"]
        assert "OpenRA server is not reachable" in capsys.readouterr().err


def test_run_runtime_start_failure_stops_persistence_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomRuntime:
        created: list["_BoomRuntime"] = []

        def __init__(self, *, config: RuntimeConfig) -> None:
            self.config = config
            self.stop_calls = 0
            self._shutdown_event = asyncio.Event()
            type(self).created.append(self)

        async def start(self) -> None:
            raise RuntimeError("boom-start")

        async def stop(self) -> None:
            self.stop_calls += 1
            self._shutdown_event.set()

    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: True))
        monkeypatch.setattr(main_module, "ApplicationRuntime", _BoomRuntime)

        with pytest.raises(RuntimeError, match="boom-start"):
            asyncio.run(main_module.run_runtime(cfg))

        runtime = _BoomRuntime.created[-1]
        latest_session = logging_system.latest_session_dir(log_root)
        assert runtime.stop_calls == 1
        assert logging_system.current_session_dir() is None
        assert latest_session is not None
        session_meta = json.loads((latest_session / "session.json").read_text(encoding="utf-8"))
        assert session_meta["ended_at"]


def test_run_runtime_constructor_failure_stops_persistence_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomRuntime:
        def __init__(self, *, config: RuntimeConfig) -> None:
            del config
            raise RuntimeError("boom-init")

    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: True))
        monkeypatch.setattr(main_module, "ApplicationRuntime", _BoomRuntime)

        with pytest.raises(RuntimeError, match="boom-init"):
            asyncio.run(main_module.run_runtime(cfg))

        latest_session = logging_system.latest_session_dir(log_root)
        assert logging_system.current_session_dir() is None
        assert latest_session is not None
        session_meta = json.loads((latest_session / "session.json").read_text(encoding="utf-8"))
        assert session_meta["ended_at"]


def test_run_runtime_export_failure_still_stops_persistence_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _TinyRuntime:
        def __init__(self, *, config: RuntimeConfig) -> None:
            self.config = config
            self._shutdown_event = asyncio.Event()

        async def start(self) -> None:
            self._shutdown_event.set()

        async def wait_until_stopped(self) -> None:
            return None

        async def stop(self) -> None:
            self._shutdown_event.set()

    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: True))
        monkeypatch.setattr(main_module, "ApplicationRuntime", _TinyRuntime)
        monkeypatch.setattr(main_module.benchmark, "export_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("export-boom")))

        with pytest.raises(RuntimeError, match="export-boom"):
            asyncio.run(main_module.run_runtime(cfg))

        latest_session = logging_system.latest_session_dir(log_root)
        assert logging_system.current_session_dir() is None
        assert latest_session is not None
        session_meta = json.loads((latest_session / "session.json").read_text(encoding="utf-8"))
        assert session_meta["ended_at"]


def test_run_runtime_signal_handler_requests_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SignalRuntime:
        created: list["_SignalRuntime"] = []

        def __init__(self, *, config: RuntimeConfig) -> None:
            self.config = config
            self.request_shutdown_calls = 0
            self.stop_calls = 0
            self._shutdown_event = asyncio.Event()
            type(self).created.append(self)

        async def start(self) -> None:
            return None

        async def wait_until_stopped(self) -> None:
            await self._shutdown_event.wait()

        def request_shutdown(self) -> None:
            self.request_shutdown_calls += 1
            self._shutdown_event.set()

        async def stop(self) -> None:
            self.stop_calls += 1
            self._shutdown_event.set()

    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: True))
        monkeypatch.setattr(main_module, "ApplicationRuntime", _SignalRuntime)

        async def run() -> int:
            loop = asyncio.get_running_loop()
            registered: dict[Any, Any] = {}

            def fake_add_signal_handler(sig, callback) -> None:
                registered[sig] = callback
                if len(registered) == 2:
                    loop.call_soon(callback)

            monkeypatch.setattr(loop, "add_signal_handler", fake_add_signal_handler)
            return await main_module.run_runtime(cfg)

        exit_code = asyncio.run(run())

        runtime = _SignalRuntime.created[-1]
        assert exit_code == 0
        assert runtime.request_shutdown_calls == 1
        assert runtime.stop_calls == 0


def test_run_runtime_signal_fallback_requests_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SignalRuntime:
        created: list["_SignalRuntime"] = []

        def __init__(self, *, config: RuntimeConfig) -> None:
            self.config = config
            self.request_shutdown_calls = 0
            self.stop_calls = 0
            self._shutdown_event = asyncio.Event()
            type(self).created.append(self)

        async def start(self) -> None:
            return None

        async def wait_until_stopped(self) -> None:
            await self._shutdown_event.wait()

        def request_shutdown(self) -> None:
            self.request_shutdown_calls += 1
            self._shutdown_event.set()

        async def stop(self) -> None:
            self.stop_calls += 1
            self._shutdown_event.set()

    with tempfile.TemporaryDirectory() as tmpdir:
        log_root = Path(tmpdir) / "logs"
        cfg = RuntimeConfig(
            enable_ws=False,
            verify_game_api=True,
            llm_provider="mock",
            llm_model="mock",
            log_session_root=str(log_root),
            benchmark_records_path=str(Path(tmpdir) / "benchmark_records.json"),
            benchmark_summary_path=str(Path(tmpdir) / "benchmark_summary.json"),
            log_export_path=str(Path(tmpdir) / "runtime_logs.json"),
        )
        monkeypatch.setattr(main_module.game_control.GameAPI, "is_server_running", staticmethod(lambda *args, **kwargs: True))
        monkeypatch.setattr(main_module, "ApplicationRuntime", _SignalRuntime)

        async def run() -> int:
            loop = asyncio.get_running_loop()
            registered_fallbacks: dict[Any, Any] = {}

            def fake_add_signal_handler(sig, callback) -> None:
                del sig, callback
                raise NotImplementedError()

            def fake_signal(sig, handler):
                registered_fallbacks[sig] = handler
                if len(registered_fallbacks) == 2:
                    loop.call_soon(handler, sig, None)
                return None

            monkeypatch.setattr(loop, "add_signal_handler", fake_add_signal_handler)
            monkeypatch.setattr(main_module.signal, "signal", fake_signal)
            return await main_module.run_runtime(cfg)

        exit_code = asyncio.run(run())

        runtime = _SignalRuntime.created[-1]
        assert exit_code == 0
        assert runtime.request_shutdown_calls == 1
        assert runtime.stop_calls == 0


def test_main_py_dunder_main_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_asyncio_run(coro):
        observed["called"] = observed.get("called", 0) + 1
        observed["coro_name"] = getattr(getattr(coro, "cr_code", None), "co_name", type(coro).__name__)
        coro.close()
        return 123

    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(Path(main_module.__file__)),
            "--llm-provider",
            "mock",
            "--llm-model",
            "mock",
            "--adjutant-llm-provider",
            "mock",
            "--adjutant-llm-model",
            "mock",
            "--disable-ws",
            "--skip-game-api-check",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(Path(main_module.__file__)), run_name="__main__")

    assert exc_info.value.code == 123
    assert observed["called"] == 1
    assert observed["coro_name"] == "run_runtime"


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


def test_runtime_bridge_command_cancel_emits_notification_payload() -> None:
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
        ws = _BridgeWS()
        bridge.attach_ws_server(ws)
        await bridge.on_command_cancel("t_1", "client_1")

        assert kernel.cancelled_task_id == "t_1"
        assert ws.query_responses == []
        assert len(ws.player_notifications) == 1
        notification = ws.player_notifications[0]
        assert notification["type"] == "command_cancel"
        assert notification["content"] == "任务已取消"
        assert notification["icon"] == "ℹ"
        assert notification["data"] == {"task_id": "t_1", "ok": True}

    asyncio.run(run())
    print("  PASS: runtime_bridge_command_cancel_emits_notification_payload")


def test_runtime_bridge_command_cancel_failure_emits_notification_payload() -> None:
    async def run() -> None:
        kernel = _BridgeKernel()
        kernel.cancel_ok = False
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
        ws = _BridgeWS()
        bridge.attach_ws_server(ws)
        await bridge.on_command_cancel("missing_task", "client_1")

        assert kernel.cancelled_task_id == "missing_task"
        assert ws.query_responses == []
        assert len(ws.player_notifications) == 1
        notification = ws.player_notifications[0]
        assert notification["type"] == "command_cancel"
        assert notification["content"] == "取消失败：任务不存在或已结束"
        assert notification["icon"] == "ℹ"
        assert notification["data"] == {"task_id": "missing_task", "ok": False}

    asyncio.run(run())
    print("  PASS: runtime_bridge_command_cancel_failure_emits_notification_payload")


def test_runtime_bridge_game_restart_without_runtime_emits_error_notification() -> None:
    async def run() -> None:
        bridge = RuntimeBridge(
            kernel=_BridgeKernel(),
            world_model=type("WM", (), {})(),
            game_loop=_BridgeLoop(),
            adjutant=None,
        )
        ws = _BridgeWS()
        bridge.attach_ws_server(ws)
        await bridge.on_game_restart(None, "client_1")

        assert ws.query_responses == []
        assert len(ws.player_notifications) == 1
        notification = ws.player_notifications[0]
        assert notification["type"] == "error"
        assert notification["content"] == "游戏重启失败：runtime 未挂载"
        assert notification["icon"] == "ℹ"
        assert notification["data"] == {}

    asyncio.run(run())
    print("  PASS: runtime_bridge_game_restart_without_runtime_emits_error_notification")


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
        benchmark.clear()
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        background_errors: list[dict[str, Any]] = []

        def _capture_loop_exception(loop, context) -> None:
            del loop
            background_errors.append(dict(context))

        loop.set_exception_handler(_capture_loop_exception)
        try:
            async def _recv_json(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                predicate,
                timeout_s: float = 3.0,
                max_messages: int = 40,
            ) -> dict[str, Any]:
                deadline = loop.time() + timeout_s
                seen = 0
                while seen < max_messages and loop.time() < deadline:
                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    assert msg.type == aiohttp.WSMsgType.TEXT
                    payload = json.loads(msg.data)
                    if predicate(payload):
                        return payload
                    seen += 1
                raise AssertionError("expected websocket payload not received before timeout")

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

                            task = runtime.kernel.create_task("侦察前线", TaskKind.MANAGED, 50)
                            runtime.kernel.register_task_message(
                                TaskMessage(
                                    message_id="m_info_live",
                                    task_id=task.task_id,
                                    type=TaskMessageType.TASK_INFO,
                                    content="前线侦察进行中",
                                )
                            )
                            with benchmark.span("tool_exec", name="live-startup-smoke"):
                                pass
                            runtime.bridge.sync_runtime()
                            await runtime.bridge._publisher.publish_all()

                            task_message_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_message"
                                    and payload.get("data", {}).get("task_id") == task.task_id
                                ),
                            )
                            assert task_message_payload["data"]["type"] == TaskMessageType.TASK_INFO.value
                            assert task_message_payload["data"]["content"] == "前线侦察进行中"

                            benchmark_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "benchmark"
                                    and payload.get("data", {}).get("replace") is False
                                    and any(
                                        str(record.get("name") or "") == "live-startup-smoke"
                                        for record in list(payload.get("data", {}).get("records", []) or [])
                                        if isinstance(record, dict)
                                    )
                                ),
                            )
                            assert benchmark_payload["data"]["replace"] is False

                            task_update_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_update"
                                    and payload.get("data", {}).get("task_id") == task.task_id
                                ),
                            )
                            assert task_update_payload["data"]["task_id"] == task.task_id

                            async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as observer_ws:
                                await ws.send_json({
                                    "type": "task_replay_request",
                                    "task_id": task.task_id,
                                    "include_entries": False,
                                })
                                replay_payload = await _recv_json(
                                    ws,
                                    predicate=lambda payload: (
                                        payload.get("type") == "task_replay"
                                        and payload.get("data", {}).get("task_id") == task.task_id
                                    ),
                                )
                                assert replay_payload["data"]["task_id"] == task.task_id
                                assert replay_payload["data"]["raw_entries_included"] is False

                                observer_deadline = loop.time() + 0.3
                                while loop.time() < observer_deadline:
                                    try:
                                        observer_msg = await asyncio.wait_for(
                                            observer_ws.receive(),
                                            timeout=max(0.05, observer_deadline - loop.time()),
                                        )
                                    except asyncio.TimeoutError:
                                        break
                                    if observer_msg.type != aiohttp.WSMsgType.TEXT:
                                        continue
                                    observer_payload = json.loads(observer_msg.data)
                                    assert observer_payload.get("type") != "task_replay"

                            await ws.send_json({"type": "unknown_live_message"})
                            error_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "error"
                                    and payload.get("message") == "Unknown message type: unknown_live_message"
                                ),
                            )
                            assert error_payload["type"] == "error"
                            assert error_payload["message"] == "Unknown message type: unknown_live_message"

                            class _LiveCommandAdjutant:
                                def __init__(self, kernel) -> None:
                                    self.kernel = kernel

                                async def handle_player_input(self, text: str) -> dict[str, Any]:
                                    task = self.kernel.create_task(text, TaskKind.MANAGED, 55)
                                    return {
                                        "response_text": f"收到指令，已创建任务 {task.task_id}",
                                        "type": "command",
                                        "ok": True,
                                        "task_id": task.task_id,
                                    }

                            runtime.bridge.adjutant = _LiveCommandAdjutant(runtime.kernel)
                            await ws.send_json({"type": "command_submit", "text": "推进前线"})

                            query_response_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "query_response"
                                    and payload.get("data", {}).get("response_type") == "command"
                                    and payload.get("data", {}).get("task_id")
                                ),
                            )
                            command_task_id = query_response_payload["data"]["task_id"]
                            assert query_response_payload["data"]["ok"] is True
                            assert query_response_payload["data"]["answer"] == f"收到指令，已创建任务 {command_task_id}"

                            command_task_update = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_update"
                                    and payload.get("data", {}).get("task_id") == command_task_id
                                ),
                            )
                            assert command_task_update["data"]["raw_text"] == "推进前线"
                            assert command_task_update["data"]["kind"] == "managed"

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
            benchmark.clear()
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: application_runtime_ws_startup_smoke_and_background_publish")


@pytest.mark.startup_smoke
def test_application_runtime_ws_question_reply_round_trip_delivers_to_task_agent() -> None:
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
            async def _recv_json(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                predicate,
                timeout_s: float = 3.0,
                max_messages: int = 40,
            ) -> dict[str, Any]:
                deadline = loop.time() + timeout_s
                seen = 0
                while seen < max_messages and loop.time() < deadline:
                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    assert msg.type == aiohttp.WSMsgType.TEXT
                    payload = json.loads(msg.data)
                    if predicate(payload):
                        return payload
                    seen += 1
                raise AssertionError("expected websocket payload not received before timeout")

            async def _drain_ws(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                idle_s: float = 0.5,
            ) -> None:
                deadline = loop.time() + idle_s
                while loop.time() < deadline:
                    try:
                        msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue

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

                    task = runtime.kernel.create_task("是否继续推进？", TaskKind.MANAGED, 60)
                    agent = runtime.kernel.get_task_agent(task.task_id)
                    assert agent is not None

                    delivered: list[dict[str, Any]] = []
                    original_push = agent.push_player_response

                    def _spy_push_player_response(response) -> None:
                        delivered.append(
                            {
                                "message_id": response.message_id,
                                "task_id": response.task_id,
                                "answer": response.answer,
                            }
                        )
                        original_push(response)

                    agent.push_player_response = _spy_push_player_response  # type: ignore[method-assign]

                    message_id = "msg_live_reply"
                    assert runtime.kernel.register_task_message(
                        TaskMessage(
                            message_id=message_id,
                            task_id=task.task_id,
                            type=TaskMessageType.TASK_QUESTION,
                            content="继续推进还是等待？",
                            options=["继续", "等待"],
                            timeout_s=15.0,
                            default_option="等待",
                            priority=60,
                        )
                    )

                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                            await ws.send_json({"type": "sync_request"})

                            initial_task_list = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and any(
                                        item.get("message_id") == message_id
                                        for item in list(payload.get("data", {}).get("pending_questions", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            pending_questions = initial_task_list["data"]["pending_questions"]
                            assert any(item["message_id"] == message_id for item in pending_questions)
                            await _drain_ws(ws)

                            await ws.send_json(
                                {
                                    "type": "question_reply",
                                    "message_id": message_id,
                                    "task_id": task.task_id,
                                    "answer": "继续",
                                }
                            )

                            query_response_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "query_response"
                                    and payload.get("data", {}).get("response_type") == "reply"
                                    and payload.get("data", {}).get("message_id") == message_id
                                ),
                            )
                            assert query_response_payload["data"]["ok"] is True
                            assert query_response_payload["data"]["status"] == "delivered"
                            assert query_response_payload["data"]["task_id"] == task.task_id
                            assert query_response_payload["data"]["answer"] == "已回复"

                            cleared_task_list = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and not any(
                                        item.get("message_id") == message_id
                                        for item in list(payload.get("data", {}).get("pending_questions", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            assert cleared_task_list["type"] == "task_list"
                            assert runtime.kernel.list_pending_questions() == []
                            assert delivered == [
                                {
                                    "message_id": message_id,
                                    "task_id": task.task_id,
                                    "answer": "继续",
                                }
                            ]

                    runtime.bridge.on_tick(1, 0.0)
                    await asyncio.sleep(0.1)
                    assert background_errors == [], background_errors
                finally:
                    await runtime.stop()

                assert api.close_calls == 1
                assert runtime.ws_server is not None
                assert runtime.ws_server.is_running is False
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: application_runtime_ws_question_reply_round_trip_delivers_to_task_agent")


@pytest.mark.startup_smoke
def test_application_runtime_ws_question_reply_task_mismatch_preserves_pending_question() -> None:
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
            async def _recv_json(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                predicate,
                timeout_s: float = 3.0,
                max_messages: int = 40,
            ) -> dict[str, Any]:
                deadline = loop.time() + timeout_s
                seen = 0
                while seen < max_messages and loop.time() < deadline:
                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    assert msg.type == aiohttp.WSMsgType.TEXT
                    payload = json.loads(msg.data)
                    if predicate(payload):
                        return payload
                    seen += 1
                raise AssertionError("expected websocket payload not received before timeout")

            async def _drain_ws(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                idle_s: float = 0.5,
            ) -> None:
                deadline = loop.time() + idle_s
                while loop.time() < deadline:
                    try:
                        msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue

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

                    task = runtime.kernel.create_task("等待玩家确认", TaskKind.MANAGED, 60)
                    agent = runtime.kernel.get_task_agent(task.task_id)
                    assert agent is not None

                    delivered: list[dict[str, Any]] = []
                    original_push = agent.push_player_response

                    def _spy_push_player_response(response) -> None:
                        delivered.append(
                            {
                                "message_id": response.message_id,
                                "task_id": response.task_id,
                                "answer": response.answer,
                            }
                        )
                        original_push(response)

                    agent.push_player_response = _spy_push_player_response  # type: ignore[method-assign]

                    message_id = "msg_live_mismatch"
                    assert runtime.kernel.register_task_message(
                        TaskMessage(
                            message_id=message_id,
                            task_id=task.task_id,
                            type=TaskMessageType.TASK_QUESTION,
                            content="继续推进还是等待？",
                            options=["继续", "等待"],
                            timeout_s=15.0,
                            default_option="等待",
                            priority=60,
                        )
                    )

                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                            await ws.send_json({"type": "sync_request"})

                            initial_task_list = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and any(
                                        item.get("message_id") == message_id
                                        for item in list(payload.get("data", {}).get("pending_questions", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            pending_questions = initial_task_list["data"]["pending_questions"]
                            assert any(item["message_id"] == message_id for item in pending_questions)
                            await _drain_ws(ws)

                            await ws.send_json(
                                {
                                    "type": "question_reply",
                                    "message_id": message_id,
                                    "task_id": "t_wrong",
                                    "answer": "继续",
                                }
                            )

                            query_response_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "query_response"
                                    and payload.get("data", {}).get("response_type") == "reply"
                                    and payload.get("data", {}).get("message_id") == message_id
                                ),
                            )
                            assert query_response_payload["data"]["ok"] is False
                            assert query_response_payload["data"]["status"] == "task_mismatch"
                            assert query_response_payload["data"]["task_id"] == "t_wrong"
                            assert query_response_payload["data"]["answer"] == "回复与任务不匹配"

                            retained_task_list = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and any(
                                        item.get("message_id") == message_id
                                        for item in list(payload.get("data", {}).get("pending_questions", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            assert any(
                                item["message_id"] == message_id
                                for item in retained_task_list["data"]["pending_questions"]
                            )
                            assert runtime.kernel.list_pending_questions()[0]["message_id"] == message_id
                            assert delivered == []

                    runtime.bridge.on_tick(1, 0.0)
                    await asyncio.sleep(0.1)
                    assert background_errors == [], background_errors
                finally:
                    await runtime.stop()

                assert api.close_calls == 1
                assert runtime.ws_server is not None
                assert runtime.ws_server.is_running is False
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: application_runtime_ws_question_reply_task_mismatch_preserves_pending_question")


@pytest.mark.startup_smoke
def test_application_runtime_ws_command_cancel_round_trip_updates_runtime_truth() -> None:
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
            async def _recv_json(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                predicate,
                timeout_s: float = 3.0,
                max_messages: int = 60,
            ) -> dict[str, Any]:
                deadline = loop.time() + timeout_s
                seen = 0
                while seen < max_messages and loop.time() < deadline:
                    msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    assert msg.type == aiohttp.WSMsgType.TEXT
                    payload = json.loads(msg.data)
                    if predicate(payload):
                        return payload
                    seen += 1
                raise AssertionError("expected websocket payload not received before timeout")

            async def _drain_ws(
                ws: aiohttp.ClientWebSocketResponse,
                *,
                idle_s: float = 0.5,
            ) -> None:
                deadline = loop.time() + idle_s
                while loop.time() < deadline:
                    try:
                        msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue

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

                    task = runtime.kernel.create_task("测试取消路径", TaskKind.MANAGED, 55)

                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                            await ws.send_json({"type": "sync_request"})

                            initial_task_list = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and any(
                                        item.get("task_id") == task.task_id
                                        for item in list(payload.get("data", {}).get("tasks", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            assert any(
                                item["task_id"] == task.task_id
                                and item["status"] == TaskStatus.RUNNING.value
                                for item in initial_task_list["data"]["tasks"]
                            )
                            await _drain_ws(ws)

                            await ws.send_json({"type": "command_cancel", "task_id": task.task_id})

                            cancel_notification = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "player_notification"
                                    and payload.get("data", {}).get("type") == "command_cancel"
                                    and payload.get("data", {}).get("data", {}).get("task_id") == task.task_id
                                ),
                            )
                            assert cancel_notification["data"]["content"] == "任务已取消"
                            assert cancel_notification["data"]["data"] == {"task_id": task.task_id, "ok": True}

                            task_update_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_update"
                                    and payload.get("data", {}).get("task_id") == task.task_id
                                ),
                            )
                            assert task_update_payload["data"]["status"] == TaskStatus.ABORTED.value

                            task_list_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "task_list"
                                    and any(
                                        item.get("task_id") == task.task_id
                                        and item.get("status") == TaskStatus.ABORTED.value
                                        for item in list(payload.get("data", {}).get("tasks", []) or [])
                                        if isinstance(item, dict)
                                    )
                                ),
                            )
                            assert any(
                                item["task_id"] == task.task_id
                                and item["status"] == TaskStatus.ABORTED.value
                                for item in task_list_payload["data"]["tasks"]
                            )
                            assert runtime.kernel.tasks[task.task_id].status == TaskStatus.ABORTED

                    runtime.bridge.on_tick(1, 0.0)
                    await asyncio.sleep(0.1)
                    assert background_errors == [], background_errors
                finally:
                    await runtime.stop()

                assert api.close_calls == 1
                assert runtime.ws_server is not None
                assert runtime.ws_server.is_running is False
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())
    print("  PASS: application_runtime_ws_command_cancel_round_trip_updates_runtime_truth")


@pytest.mark.startup_smoke
def test_application_runtime_ws_session_clear_retargets_requesting_client_only() -> None:
    provider = MockProvider([])
    source = MockWorldSource(make_frames())
    api = _CloseTrackingAPI()

    async def run() -> None:
        loop = asyncio.get_running_loop()

        async def _recv_json(
            ws: aiohttp.ClientWebSocketResponse,
            *,
            predicate,
            timeout_s: float = 3.0,
            max_messages: int = 40,
        ) -> dict[str, Any]:
            deadline = loop.time() + timeout_s
            seen = 0
            while seen < max_messages and loop.time() < deadline:
                msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                assert msg.type == aiohttp.WSMsgType.TEXT
                payload = json.loads(msg.data)
                if predicate(payload):
                    return payload
                seen += 1
            raise AssertionError("expected websocket payload not received before timeout")

        with tempfile.TemporaryDirectory() as tmpdir:
            log_root = Path(tmpdir) / "logs"
            old_session_dir = logging_system.start_persistence_session(log_root, session_name="before-clear")
            ws_port = _free_tcp_port()
            cfg = RuntimeConfig(
                ws_host="127.0.0.1",
                ws_port=ws_port,
                enable_ws=True,
                enable_voice=False,
                log_session_root=str(log_root),
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
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                        async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as observer_ws:
                            await ws.send_json({"type": "sync_request"})
                            initial_catalog = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "session_catalog"
                                    and payload.get("data", {}).get("selected_session_dir")
                                ),
                            )
                            assert initial_catalog["data"]["selected_session_dir"] == str(old_session_dir)

                            await ws.send_json({"type": "session_clear"})

                            cleared_payload = await _recv_json(
                                ws,
                                predicate=lambda payload: payload.get("type") == "session_cleared",
                                max_messages=120,
                            )
                            assert cleared_payload["data"]["ok"] is True

                            observer_cleared = await _recv_json(
                                observer_ws,
                                predicate=lambda payload: payload.get("type") == "session_cleared",
                                max_messages=120,
                            )
                            assert observer_cleared["data"]["ok"] is True

                            next_catalog = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                        payload.get("type") == "session_catalog"
                                        and payload.get("data", {}).get("selected_session_dir")
                                        and payload.get("data", {}).get("selected_session_dir") != str(old_session_dir)
                                    ),
                                    max_messages=120,
                                )
                            new_session_dir = next_catalog["data"]["selected_session_dir"]
                            assert new_session_dir != str(old_session_dir)
                            current_session_dir = logging_system.current_session_dir()
                            assert current_session_dir is not None
                            assert str(current_session_dir) == new_session_dir

                            next_task_catalog = await _recv_json(
                                ws,
                                predicate=lambda payload: (
                                    payload.get("type") == "session_task_catalog"
                                    and payload.get("data", {}).get("session_dir") == new_session_dir
                                ),
                                max_messages=120,
                            )
                            assert next_task_catalog["data"]["session_dir"] == new_session_dir

                            observer_deadline = loop.time() + 0.4
                            while loop.time() < observer_deadline:
                                try:
                                    observer_msg = await asyncio.wait_for(
                                        observer_ws.receive(),
                                        timeout=max(0.05, observer_deadline - loop.time()),
                                    )
                                except asyncio.TimeoutError:
                                    break
                                if observer_msg.type != aiohttp.WSMsgType.TEXT:
                                    continue
                                observer_payload = json.loads(observer_msg.data)
                                assert observer_payload.get("type") not in {"session_catalog", "session_task_catalog"}
            finally:
                await runtime.stop()
                logging_system.stop_persistence_session()

            assert api.close_calls == 1

    asyncio.run(run())
    print("  PASS: application_runtime_ws_session_clear_retargets_requesting_client_only")


@pytest.mark.startup_smoke
def test_application_runtime_ws_game_restart_round_trip(monkeypatch) -> None:
    provider = MockProvider([])
    source = MockWorldSource(make_frames())
    api = _CloseTrackingAPI()
    calls: dict[str, Any] = {}

    monkeypatch.setattr(
        main_module.game_control,
        "restart_game",
        lambda save_path=None, config=None: calls.update({"save_path": save_path, "config": config}) or 222,
    )
    monkeypatch.setattr(
        main_module.game_control,
        "wait_for_api",
        lambda timeout=30.0, *, host=None, port=None, language="zh", poll_interval=0.5: calls.update(
            {
                "wait": (timeout, host, port, language, poll_interval),
            }
        ) or True,
    )
    monkeypatch.setattr(
        main_module.game_control.GameAPI,
        "is_server_running",
        staticmethod(lambda *args, **kwargs: True),
    )

    async def run() -> None:
        loop = asyncio.get_running_loop()

        async def _recv_json(
            ws: aiohttp.ClientWebSocketResponse,
            *,
            predicate,
            timeout_s: float = 3.0,
            max_messages: int = 80,
        ) -> dict[str, Any]:
            deadline = loop.time() + timeout_s
            seen = 0
            while seen < max_messages and loop.time() < deadline:
                msg = await ws.receive(timeout=max(0.05, deadline - loop.time()))
                assert msg.type == aiohttp.WSMsgType.TEXT
                payload = json.loads(msg.data)
                if predicate(payload):
                    return payload
                seen += 1
            raise AssertionError("expected websocket payload not received before timeout")

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_port = _free_tcp_port()
            cfg = RuntimeConfig(
                ws_host="127.0.0.1",
                ws_port=ws_port,
                enable_ws=True,
                enable_voice=False,
                verify_game_api=False,
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
                expert_registry={},
            )
            try:
                await runtime.start()
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(f"http://127.0.0.1:{ws_port}/ws") as ws:
                        await ws.send_json({"type": "game_restart", "save_path": "baseline.orasav"})

                        restarting_payload = await _recv_json(
                            ws,
                            predicate=lambda payload: (
                                payload.get("type") == "player_notification"
                                and payload.get("data", {}).get("type") == "game_restart"
                            ),
                        )
                        assert restarting_payload["data"]["content"] == "正在重启 OpenRA 对局"
                        assert restarting_payload["data"]["data"]["save_path"] == "baseline.orasav"

                        complete_payload = await _recv_json(
                            ws,
                            predicate=lambda payload: (
                                payload.get("type") == "player_notification"
                                and payload.get("data", {}).get("type") == "game_restart_complete"
                            ),
                        )
                        assert complete_payload["data"]["content"] == "OpenRA 对局已重启并完成重新连接"
                        assert complete_payload["data"]["data"]["save_path"] == "baseline.orasav"
                        assert calls["save_path"] == "baseline.orasav"
                        assert runtime.game_loop.is_running
            finally:
                await runtime.stop()

        assert calls["save_path"] == "baseline.orasav"
        assert api.close_calls == 2

    asyncio.run(run())
    print("  PASS: application_runtime_ws_game_restart_round_trip")


@pytest.mark.mock_integration
def test_application_runtime_publish_smoke_surfaces_truth_payloads() -> None:
    provider = MockProvider([])
    source = MockWorldSource([
        Frame(
            self_actors=[
                Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=2, type="发电厂", faction="自己", position=Location(11, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=3, type="矿场", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(13, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=5, type="tent", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=6, type="2tnk", faction="自己", position=Location(15, 10), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
            economy=PlayerBaseInfo(Cash=5000, Resources=0, Power=200, PowerDrained=120, PowerProvided=200),
            map_info=make_map(0.1, 0.05),
            queues={},
        )
    ])
    api = _CloseTrackingAPI()

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = RuntimeConfig(
                enable_ws=False,
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
                ws = _BridgePublishWS()
                runtime.bridge.attach_ws_server(ws)
                await runtime.bridge._publisher.publish_all()

                assert ws.world_snapshots
                world_snapshot = ws.world_snapshots[-1]
                assert world_snapshot["player_faction"] == "allied"
                assert world_snapshot["capability_truth_blocker"] == "faction_roster_unsupported"

                assert ws.task_lists
                tasks = list(ws.task_lists[-1]["tasks"] or [])
                capability_task = next(task for task in tasks if task.get("is_capability"))
                assert capability_task["triage"]["blocking_reason"] == "faction_roster_unsupported"
                assert "真值受限" in capability_task["triage"]["status_line"]

                capability_update = next(
                    (
                        payload
                        for payload in ws.task_updates
                        if str(payload.get("task_id") or "") == str(capability_task["task_id"] or "")
                    ),
                    None,
                )
                assert capability_update is not None
                assert capability_update["triage"]["blocking_reason"] == "faction_roster_unsupported"
                assert "阵营能力真值未覆盖" in capability_update["triage"]["status_line"]
            finally:
                await runtime.stop()

    asyncio.run(run())
    print("  PASS: application_runtime_publish_smoke_surfaces_truth_payloads")


@pytest.mark.startup_smoke
def test_main_entry_direct_start_smoke_covers_enable_voice_and_task_message_publish(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _DirectEntryRuntime(main_module.ApplicationRuntime):
        created: list["_DirectEntryRuntime"] = []

        def __init__(self, *, config: RuntimeConfig) -> None:
            super().__init__(
                config=config,
                task_llm=MockProvider([]),
                adjutant_llm=MockProvider([]),
                api=_CloseTrackingAPI(),
                world_source=MockWorldSource(make_frames()),
            )
            self.notification_spy = _BridgeNotificationAdjutant()
            self.bridge.adjutant = self.notification_spy
            self.captured_voice_enabled = self.ws_server.config.voice_enabled if self.ws_server is not None else None
            type(self).created.append(self)

        async def start(self) -> None:
            await super().start()
            task = self.kernel.create_task("建造电厂", TaskKind.MANAGED, 50)
            task.label = "001"
            self.kernel.register_task_message(
                TaskMessage(
                    message_id="m_info",
                    task_id=task.task_id,
                    type=TaskMessageType.TASK_INFO,
                    content="电力不足，等待恢复",
                )
            )
            self.kernel.register_task_message(
                TaskMessage(
                    message_id="m_done",
                    task_id=task.task_id,
                    type=TaskMessageType.TASK_COMPLETE_REPORT,
                    content="电厂已建成",
                )
            )
            task.status = TaskStatus.SUCCEEDED
            await self.bridge._publisher.publish_all()
            await self.stop()

    with tempfile.TemporaryDirectory() as tmpdir:
        ws_port = _free_tcp_port()
        monkeypatch.setattr(main_module, "ApplicationRuntime", _DirectEntryRuntime)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        caplog.set_level(logging.WARNING, logger="ws_server.server")

        exit_code = main_module.main(
            [
                "--llm-provider",
                "mock",
                "--llm-model",
                "mock",
                "--adjutant-llm-provider",
                "mock",
                "--adjutant-llm-model",
                "mock",
                "--skip-game-api-check",
                "--enable-voice",
                "--ws-host",
                "127.0.0.1",
                "--ws-port",
                str(ws_port),
                "--benchmark-records-path",
                str(Path(tmpdir) / "benchmark_records.json"),
                "--benchmark-summary-path",
                str(Path(tmpdir) / "benchmark_summary.json"),
                "--log-export-path",
                str(Path(tmpdir) / "runtime_logs.json"),
                "--log-session-root",
                str(Path(tmpdir) / "logs"),
            ]
        )

    assert exit_code == 0
    runtime = _DirectEntryRuntime.created[-1]
    assert runtime.captured_voice_enabled is True
    assert {
        "task_id": runtime.notification_spy.messages[0]["task_id"],
        "message_type": TaskMessageType.TASK_INFO.value,
        "content": "电力不足，等待恢复",
    } in runtime.notification_spy.messages
    assert {
        "label": "001",
        "raw_text": "建造电厂",
        "result": TaskStatus.SUCCEEDED.value,
        "summary": "电厂已建成",
        "task_id": runtime.notification_spy.completed[0]["task_id"],
    } in runtime.notification_spy.completed
    if importlib.util.find_spec("dashscope") is None:
        assert any("Voice subsystem:" in record.message for record in caplog.records), caplog.messages
    print("  PASS: main_entry_direct_start_smoke_covers_enable_voice_and_task_message_publish")


def test_runtime_bridge_session_clear_resets_benchmark_publish_state() -> None:
    import benchmark
    import logging_system

    async def run() -> None:
        kernel = _BridgeKernel()
        loop = _BridgeLoop()
        world_model = type(
            "WM",
            (),
            {
                "__init__": lambda self: setattr(self, "reset_calls", 0),
                "reset_snapshot": lambda self: setattr(self, "reset_calls", self.reset_calls + 1),
            },
        )()
        bridge = RuntimeBridge(
            kernel=kernel,
            world_model=world_model,
            game_loop=loop,
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
        assert world_model.reset_calls == 1
        assert loop.reset_runtime_calls == 1
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
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
