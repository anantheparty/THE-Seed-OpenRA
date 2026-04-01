"""Phase 7 application entrypoint and runtime assembly.

Start order (design.md §2):
    GameAPI -> UnitRegistry -> WorldModel -> Kernel -> Dashboard(WS) -> GameLoop
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from dataclasses import dataclass
import importlib.util
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Optional

import benchmark

from adjutant import Adjutant, AdjutantConfig
from experts.base import ExecutionExpert
from experts.combat import CombatExpert
from experts.deploy import DeployExpert
from experts.economy import EconomyExpert
from experts.movement import MovementExpert
from experts.recon import ReconExpert
import game_control
from game_loop import GameLoop, GameLoopConfig
from kernel import Kernel, KernelConfig, TaskAgentFactory
from llm import AnthropicProvider, LLMProvider, MockProvider, QwenProvider
from logging_system import (
    clear as clear_logs,
    current_session_dir,
    export_benchmark_report_json,
    export_json as export_log_json,
    get_logger,
    records as log_records,
    start_persistence_session,
    stop_persistence_session,
)
from models import PlayerResponse, TaskMessageType, TaskStatus
from openra_api.game_api import GameAPI
from queue_manager import QueueManager, QueueManagerConfig
from task_agent import AgentConfig
from unit_registry import UnitRegistry, set_default_registry
from world_model import GameAPIWorldSource, RefreshPolicy, WorldModel, WorldModelSource
from ws_server import InboundHandler, WSServer, WSServerConfig


slog = get_logger("main")


@dataclass(slots=True)
class RuntimeConfig:
    game_host: str = "localhost"
    game_port: int = 7445
    game_language: str = "zh"
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    tick_hz: float = 10.0
    actors_refresh_s: float = 0.1
    economy_refresh_s: float = 0.5
    map_refresh_s: float = 1.0
    review_interval: float = 10.0
    queue_manager_mode: str = "auto_place"
    queue_ready_timeout_s: float = 5.0
    llm_provider: str = "qwen"
    llm_model: str = "qwen-plus"
    adjutant_llm_provider: Optional[str] = None
    adjutant_llm_model: Optional[str] = None
    benchmark_records_path: str = "docs/wang/phase7_e2e_benchmark_records.json"
    benchmark_summary_path: str = "docs/wang/phase7_e2e_benchmark_summary.json"
    log_export_path: str = "docs/wang/phase7_runtime_logs.json"
    log_session_root: str = "Logs/runtime"
    enable_ws: bool = True
    verify_game_api: bool = True
    log_level: str = "WARNING"


def _load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_provider(provider_name: str, model: str) -> LLMProvider:
    normalized = provider_name.strip().lower()
    if normalized == "qwen":
        if importlib.util.find_spec("openai") is None:
            raise RuntimeError(
                "LLM provider 'qwen' requires Python package 'openai' in the backend runtime environment. "
                "Install it before starting main.py."
            )
        proxy_keys = ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
        if any(os.environ.get(key, "").strip().lower().startswith("socks") for key in proxy_keys):
            if importlib.util.find_spec("socksio") is None:
                raise RuntimeError(
                    "LLM provider 'qwen' is configured to use a SOCKS proxy via proxy environment variables, "
                    "but Python package 'socksio' is not installed in the backend runtime environment. "
                    "Install it before starting main.py."
                )
        return QwenProvider(model=model)
    if normalized == "anthropic":
        if importlib.util.find_spec("anthropic") is None:
            raise RuntimeError(
                "LLM provider 'anthropic' requires Python package 'anthropic' in the backend runtime environment. "
                "Install it before starting main.py."
            )
        return AnthropicProvider(model=model)
    if normalized == "mock":
        return MockProvider([])
    raise ValueError(f"Unsupported LLM provider: {provider_name}")


def build_default_expert_registry(game_api: Any, world_model: WorldModel) -> dict[str, ExecutionExpert]:
    return {
        "ReconExpert": ReconExpert(game_api=game_api, world_model=world_model),
        "MovementExpert": MovementExpert(game_api=game_api, world_model=world_model),
        "DeployExpert": DeployExpert(game_api=game_api),
        "CombatExpert": CombatExpert(game_api=game_api, world_model=world_model),
        "EconomyExpert": EconomyExpert(game_api=game_api, world_model=world_model),
    }


class RuntimeBridge(InboundHandler):
    """Thin integration bridge for GameLoop registration and dashboard fanout."""

    def __init__(
        self,
        *,
        kernel: Kernel,
        world_model: WorldModel,
        game_loop: GameLoop,
        adjutant: Optional[Adjutant] = None,
    ) -> None:
        self.kernel = kernel
        self.world_model = world_model
        self.game_loop = game_loop
        self.adjutant = adjutant
        self.runtime: Optional[ApplicationRuntime] = None
        self.ws_server: Optional[WSServer] = None
        self.mode = "user"

        self._registered_agents: set[str] = set()
        self._registered_jobs: set[str] = set()
        self._task_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._task_message_offset = 0
        self._notification_offset = 0
        self._log_offset = 0
        self._recent_responses: list[dict[str, Any]] = []
        self._publish_lock = asyncio.Lock()
        self._publish_task: Optional[asyncio.Task[Any]] = None

    def attach_ws_server(self, ws_server: Optional[WSServer]) -> None:
        self.ws_server = ws_server

    def attach_runtime(self, runtime: ApplicationRuntime) -> None:
        self.runtime = runtime

    def sync_runtime(self) -> None:
        active_agent_ids: set[str] = set()
        for task_id, runtime in self.kernel._task_runtimes.items():  # type: ignore[attr-defined]
            task = runtime.task
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                continue
            active_agent_ids.add(task_id)
            if task_id not in self._registered_agents:
                review_interval = getattr(getattr(runtime.agent, "config", None), "review_interval", 10.0)
                self.game_loop.register_agent(task_id, runtime.agent.queue, review_interval=review_interval)
                self._registered_agents.add(task_id)

        for task_id in list(self._registered_agents):
            if task_id not in active_agent_ids:
                self.game_loop.unregister_agent(task_id)
                self._registered_agents.discard(task_id)

        active_job_ids: set[str] = set()
        for job_id, controller in self.kernel._jobs.items():  # type: ignore[attr-defined]
            if controller.status.value in {"succeeded", "failed", "aborted"}:
                continue
            active_job_ids.add(job_id)
            if job_id not in self._registered_jobs:
                self.game_loop.register_job(controller)
                self._registered_jobs.add(job_id)

        for job_id in list(self._registered_jobs):
            if job_id not in active_job_ids:
                self.game_loop.unregister_job(job_id)
                self._registered_jobs.discard(job_id)

    async def publish_dashboard(self) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        async with self._publish_lock:
            pending_questions = self.kernel.list_pending_questions()
            await self.ws_server.send_world_snapshot(
                {
                    **self.world_model.world_summary(),
                    "runtime_state": self.world_model.runtime_state(),
                    "pending_questions": pending_questions,
                    "mode": self.mode,
                }
            )
            await self.ws_server.send_task_list(
                [self._task_to_dict(task, self.kernel.jobs_for_task(task.task_id)) for task in self.kernel.list_tasks()],
                pending_questions=pending_questions,
            )
            await self._publish_task_updates()
            await self._publish_task_messages()
            await self._publish_notifications()
            await self._publish_logs()
            await self._publish_benchmarks()

    def on_tick(self, tick_number: int, now: float) -> None:
        self.sync_runtime()
        if self.ws_server is None or not self.ws_server.is_running:
            return
        if self._publish_task is not None and not self._publish_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._publish_task = loop.create_task(self.publish_dashboard())

    async def on_command_submit(self, text: str, client_id: str) -> None:
        del client_id
        try:
            if self.adjutant is None:
                task = self.kernel.create_task(text, kind="managed", priority=50)
                await self._emit_adjutant_response(
                    f"收到指令，已创建任务 {task.task_id}",
                    response_type="command",
                )
                self.sync_runtime()
                await self.publish_dashboard()
                return

            result = await self.adjutant.handle_player_input(text)
            response_text = result.get("response_text")
            if response_text:
                await self._emit_adjutant_response(
                    response_text,
                    response_type=result.get("type", "info"),
                    ok=result.get("ok", True),
                    extra={
                        key: value
                        for key, value in result.items()
                        if key not in {"response_text", "timestamp"}
                    },
                )
            self.sync_runtime()
            await self.publish_dashboard()
        except Exception:
            slog.error("on_command_submit failed", event="command_submit_error", text=text)
            await self._emit_adjutant_response(f"指令处理失败: {text[:50]}", response_type="error", ok=False)

    async def on_command_cancel(self, task_id: str, client_id: str) -> None:
        del client_id
        ok = self.kernel.cancel_task(task_id)
        content = "任务已取消" if ok else "取消失败：任务不存在或已结束"
        await self._emit_notification("command_cancel", content, data={"task_id": task_id, "ok": ok})
        self.sync_runtime()
        await self.publish_dashboard()

    async def on_mode_switch(self, mode: str, client_id: str) -> None:
        del client_id
        if mode:
            self.mode = mode
        await self.publish_dashboard()

    async def on_question_reply(self, message_id: str, task_id: str, answer: str, client_id: str) -> None:
        del client_id
        result = self.kernel.submit_player_response(
            PlayerResponse(message_id=message_id, task_id=task_id, answer=answer)
        )
        await self._emit_adjutant_response(
            result.get("message", "已回复" if result.get("ok", False) else "回复失败"),
            response_type="reply",
            ok=result.get("ok", False),
            extra={
                "task_id": task_id,
                "message_id": message_id,
                "status": result.get("status"),
            },
        )
        self.sync_runtime()
        await self.publish_dashboard()

    async def on_sync_request(self, client_id: str) -> None:
        """Client connected/reconnected — push full state immediately."""
        self.sync_runtime()
        await self.publish_dashboard()
        await self._replay_history(client_id)

    async def on_session_clear(self, client_id: str) -> None:
        del client_id
        self.kernel.reset_session()
        if self.adjutant is not None:
            self.adjutant.clear_dialogue_history()
        self._recent_responses.clear()
        self._task_fingerprints.clear()
        self._task_message_offset = 0
        self._notification_offset = 0
        self._log_offset = 0
        clear_logs()
        benchmark.clear()
        self.sync_runtime()
        if self.ws_server is not None and self.ws_server.is_running:
            await self.ws_server.send_session_cleared()
        await self.publish_dashboard()

    async def on_game_restart(self, save_path: Optional[str], client_id: str) -> None:
        del client_id
        if self.runtime is None:
            await self._emit_notification("error", "游戏重启失败：runtime 未挂载")
            return
        await self.runtime.restart_game(save_path=save_path)

    async def _publish_task_updates(self) -> None:
        assert self.ws_server is not None
        for task in self.kernel.list_tasks():
            payload = self._task_to_dict(task, self.kernel.jobs_for_task(task.task_id))
            fingerprint = (
                payload.get("task_id"),
                payload.get("status"),
                payload.get("priority"),
                payload.get("timestamp"),
                payload.get("raw_text"),
                tuple(
                    (
                        job.get("job_id"),
                        job.get("expert_type"),
                        job.get("status"),
                        job.get("summary"),
                    )
                    for job in payload.get("jobs", [])
                ),
            )
            if self._task_fingerprints.get(task.task_id) == fingerprint:
                continue
            self._task_fingerprints[task.task_id] = fingerprint
            await self.ws_server.send_task_update(payload)

    async def _publish_task_messages(self) -> None:
        assert self.ws_server is not None
        task_messages = self.kernel.list_task_messages()
        new_messages = task_messages[self._task_message_offset :]
        self._task_message_offset = len(task_messages)
        for message in new_messages:
            if message.type == TaskMessageType.TASK_QUESTION:
                continue
            icon = {
                TaskMessageType.TASK_INFO: "ℹ",
                TaskMessageType.TASK_WARNING: "⚠",
                TaskMessageType.TASK_COMPLETE_REPORT: "✓",
            }.get(message.type, "ℹ")
            await self.ws_server.send_player_notification(
                {
                    "type": message.type.value,
                    "content": message.content,
                    "icon": icon,
                    "task_id": message.task_id,
                    "message_id": message.message_id,
                    "timestamp": message.timestamp,
                }
            )

    async def _publish_notifications(self) -> None:
        assert self.ws_server is not None
        notifications = self.kernel.list_player_notifications()
        new_notifications = notifications[self._notification_offset :]
        self._notification_offset = len(notifications)
        for notification in new_notifications:
            await self.ws_server.send_player_notification(notification)

    async def _publish_logs(self) -> None:
        assert self.ws_server is not None
        _LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
        new_records = log_records()[self._log_offset :]
        self._log_offset += len(new_records)
        for record in new_records:
            # Only push INFO+ to frontend; skip benchmark noise
            if _LEVEL_ORDER.get(record.level, 0) < 1:
                continue
            if record.component == "benchmark":
                continue
            await self.ws_server.send_log_entry(record.to_dict())

    async def _publish_benchmarks(self) -> None:
        assert self.ws_server is not None
        benchmark_records = [
            record.to_dict()
            for record in benchmark.query(slowest_first=False)
        ]
        await self.ws_server.send_benchmark(benchmark_records)

    async def _emit_notification(
        self,
        notification_type: str,
        content: str,
        *,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        await self.ws_server.send_player_notification(
            {
                "type": notification_type,
                "content": content,
                "icon": "ℹ",
                "data": dict(data or {}),
            }
        )

    async def _emit_adjutant_response(
        self,
        answer: str,
        *,
        response_type: str,
        ok: bool = True,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        payload = {
            "answer": answer,
            "response_type": response_type,
            "ok": ok,
        }
        if extra:
            payload.update(extra)
        payload["timestamp"] = time.time()
        self._recent_responses.append(dict(payload))
        if len(self._recent_responses) > 100:
            self._recent_responses = self._recent_responses[-100:]
        await self.ws_server.send_query_response(payload)

    async def _replay_history(self, client_id: str) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return

        history_logs = [
            record.to_dict()
            for record in log_records()
            if record.level in {"INFO", "WARN", "ERROR"} and record.component != "benchmark"
        ][-300:]
        for entry in history_logs:
            await self.ws_server.send_to_client(client_id, "log_entry", entry)

        for message in self.kernel.list_task_messages()[-100:]:
            if message.type == TaskMessageType.TASK_QUESTION:
                continue
            icon = {
                TaskMessageType.TASK_INFO: "ℹ",
                TaskMessageType.TASK_WARNING: "⚠",
                TaskMessageType.TASK_COMPLETE_REPORT: "✓",
            }.get(message.type, "ℹ")
            await self.ws_server.send_to_client(
                client_id,
                "player_notification",
                {
                    "type": message.type.value,
                    "content": message.content,
                    "icon": icon,
                    "task_id": message.task_id,
                    "message_id": message.message_id,
                    "timestamp": message.timestamp,
                },
            )

        for notification in self.kernel.list_player_notifications()[-100:]:
            await self.ws_server.send_to_client(client_id, "player_notification", notification)

        for response in self._recent_responses[-100:]:
            await self.ws_server.send_to_client(client_id, "query_response", response)

    @staticmethod
    def _task_to_dict(task: Any, jobs: Optional[list[Any]] = None) -> dict[str, Any]:
        task_jobs = jobs or []
        return {
            "task_id": task.task_id,
            "raw_text": task.raw_text,
            "kind": task.kind.value,
            "priority": task.priority,
            "status": task.status.value,
            "timestamp": task.timestamp,
            "created_at": task.created_at,
            "jobs": [RuntimeBridge._job_to_dict(job) for job in task_jobs],
            "job_count": len(task_jobs),
        }

    @staticmethod
    def _job_to_dict(job: Any) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "expert_type": job.expert_type,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "resources": list(getattr(job, "resources", []) or []),
            "timestamp": getattr(job, "timestamp", None),
            "summary": RuntimeBridge._describe_job(job),
        }

    @staticmethod
    def _describe_job(job: Any) -> str:
        config = getattr(job, "config", None)
        if config is None:
            return ""
        if is_dataclass(config):
            config_data = asdict(config)
        elif isinstance(config, dict):
            config_data = dict(config)
        else:
            return str(config)

        expert_type = getattr(job, "expert_type", "")
        if expert_type == "EconomyExpert":
            unit_type = config_data.get("unit_type")
            count = config_data.get("count")
            queue_type = config_data.get("queue_type")
            return f"{queue_type} · {unit_type} × {count}"
        if expert_type in {"ReconExpert", "CombatExpert", "MovementExpert", "DeployExpert"}:
            parts: list[str] = []
            if "target_position" in config_data and config_data["target_position"] is not None:
                parts.append(f"目标 {tuple(config_data['target_position'])}")
            if "search_region" in config_data:
                parts.append(f"区域 {config_data['search_region']}")
            if "target_type" in config_data:
                parts.append(f"目标类型 {config_data['target_type']}")
            if "engagement_mode" in config_data:
                parts.append(f"模式 {config_data['engagement_mode']}")
            if "move_mode" in config_data:
                parts.append(f"模式 {config_data['move_mode']}")
            if "actor_id" in config_data and config_data["actor_id"] is not None:
                parts.append(f"actor {config_data['actor_id']}")
            return " · ".join(parts)
        return ", ".join(f"{key}={value}" for key, value in config_data.items())


class ApplicationRuntime:
    """Owns and runs the assembled Phase 7 runtime."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        task_llm: Optional[LLMProvider] = None,
        adjutant_llm: Optional[LLMProvider] = None,
        api: Optional[Any] = None,
        world_source: Optional[WorldModelSource] = None,
        expert_registry: Optional[dict[str, ExecutionExpert]] = None,
        kernel_config: Optional[KernelConfig] = None,
        task_agent_factory: Optional[TaskAgentFactory] = None,
    ) -> None:
        self.config = config
        self.api = api or GameAPI(config.game_host, port=config.game_port, language=config.game_language)
        self.unit_registry = UnitRegistry.load()
        set_default_registry(self.unit_registry)
        self.world_source = world_source or GameAPIWorldSource(self.api)

        refresh_policy = RefreshPolicy(
            actors_s=config.actors_refresh_s,
            economy_s=config.economy_refresh_s,
            map_s=config.map_refresh_s,
        )
        self.world_model = WorldModel(
            self.world_source,
            refresh_policy=refresh_policy,
            unit_registry=self.unit_registry,
        )
        self.world_model.refresh(force=True)

        self.task_llm = task_llm or _build_provider(config.llm_provider, config.llm_model)
        adjutant_provider = config.adjutant_llm_provider or config.llm_provider
        adjutant_model = config.adjutant_llm_model or config.llm_model
        self.adjutant_llm = adjutant_llm or _build_provider(adjutant_provider, adjutant_model)

        kernel_cfg = kernel_config or KernelConfig(
            auto_start_agents=True,
            default_agent_config=AgentConfig(review_interval=config.review_interval),
        )
        self.kernel = Kernel(
            world_model=self.world_model,
            llm=self.task_llm,
            expert_registry=expert_registry or build_default_expert_registry(self.api, self.world_model),
            task_agent_factory=task_agent_factory,
            config=kernel_cfg,
        )
        self.adjutant = Adjutant(
            llm=self.adjutant_llm,
            kernel=self.kernel,
            world_model=self.world_model,
            game_api=self.api,
            unit_registry=self.unit_registry,
            config=AdjutantConfig(default_task_kind="managed", default_task_priority=50),
        )
        self.queue_manager = QueueManager(
            world_model=self.world_model,
            game_api=self.api,
            notify=self.kernel.push_player_notification,
            config=QueueManagerConfig(
                mode=config.queue_manager_mode,  # type: ignore[arg-type]
                ready_timeout_s=config.queue_ready_timeout_s,
            ),
        )
        self.game_loop = GameLoop(
            self.world_model,
            self.kernel,
            config=GameLoopConfig(tick_hz=config.tick_hz),
            queue_manager=self.queue_manager,
        )
        self.bridge = RuntimeBridge(
            kernel=self.kernel,
            world_model=self.world_model,
            game_loop=self.game_loop,
            adjutant=self.adjutant,
        )
        self.bridge.attach_runtime(self)
        self.game_loop._dashboard_callback = self.bridge.on_tick  # type: ignore[attr-defined]
        self.ws_server = (
            WSServer(
                config=WSServerConfig(host=config.ws_host, port=config.ws_port),
                inbound_handler=self.bridge,
            )
            if config.enable_ws
            else None
        )
        self.bridge.attach_ws_server(self.ws_server)
        self._loop_task: Optional[asyncio.Task[Any]] = None
        self._restart_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        if self.ws_server is not None:
            await self.ws_server.start()
        self.bridge.sync_runtime()
        if self.ws_server is not None:
            await self.bridge.publish_dashboard()
        self._loop_task = asyncio.create_task(self.game_loop.start())
        slog.info("ApplicationRuntime started", event="runtime_started", ws_enabled=bool(self.ws_server))

    async def stop(self) -> None:
        await self._stop_loop_task()
        api_close = getattr(self.api, "close", None)
        if callable(api_close):
            api_close()
        if self.ws_server is not None and self.ws_server.is_running:
            await self.ws_server.stop()
        self.export_runtime_reports()
        self._shutdown_event.set()
        slog.info("ApplicationRuntime stopped", event="runtime_stopped")

    async def wait_until_stopped(self) -> None:
        await self._shutdown_event.wait()

    def request_shutdown(self) -> None:
        if not self._shutdown_event.is_set():
            asyncio.create_task(self.stop())

    async def restart_game(self, save_path: Optional[str] = None) -> dict[str, Any]:
        async with self._restart_lock:
            self.kernel.push_player_notification(
                "game_restart",
                "正在重启 OpenRA 对局",
                data={"save_path": save_path},
            )
            await self._stop_loop_task()
            api_close = getattr(self.api, "close", None)
            if callable(api_close):
                api_close()
            cancelled = self.kernel.cancel_tasks({})
            self.bridge.sync_runtime()
            if self.ws_server is not None and self.ws_server.is_running:
                await self.bridge.publish_dashboard()

            control_config = game_control.GameControlConfig(
                host=self.config.game_host,
                port=self.config.game_port,
                language=self.config.game_language,
            )
            try:
                await asyncio.to_thread(
                    game_control.restart_game,
                    save_path,
                    control_config,
                )
                ready = await asyncio.to_thread(
                    game_control.wait_for_api,
                    30.0,
                    host=self.config.game_host,
                    port=self.config.game_port,
                    language=self.config.game_language,
                )
            except Exception as exc:
                self.kernel.push_player_notification(
                    "game_restart_failed",
                    f"游戏重启失败: {exc}",
                    data={"save_path": save_path, "cancelled_tasks": cancelled},
                )
                if self.ws_server is not None and self.ws_server.is_running:
                    await self.bridge.publish_dashboard()
                return {"ok": False, "message": str(exc), "cancelled_tasks": cancelled}

            if not ready:
                self.kernel.push_player_notification(
                    "game_restart_failed",
                    "游戏已重启，但 Copilot API 未在超时内恢复",
                    data={"save_path": save_path, "cancelled_tasks": cancelled},
                )
                if self.ws_server is not None and self.ws_server.is_running:
                    await self.bridge.publish_dashboard()
                return {
                    "ok": False,
                    "message": "Game API did not recover in time.",
                    "cancelled_tasks": cancelled,
                }

            self.world_model.reset_snapshot()
            self.world_model.refresh(force=True)
            self.bridge.sync_runtime()
            await self._start_loop_task()
            self.kernel.push_player_notification(
                "game_restart_complete",
                "OpenRA 对局已重启并完成重新连接",
                data={"save_path": save_path, "cancelled_tasks": cancelled},
            )
            if self.ws_server is not None and self.ws_server.is_running:
                await self.bridge.publish_dashboard()
            return {"ok": True, "cancelled_tasks": cancelled, "save_path": save_path}

    async def _start_loop_task(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self.game_loop.start())

    async def _stop_loop_task(self) -> None:
        self.game_loop.stop()
        if self._loop_task is None:
            return
        try:
            await asyncio.wait_for(self._loop_task, timeout=2.0)
        except asyncio.TimeoutError:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._loop_task = None

    def export_runtime_reports(
        self,
        *,
        benchmark_records_path: Optional[str] = None,
        benchmark_summary_path: Optional[str] = None,
        log_export_path: Optional[str] = None,
    ) -> None:
        records_path = benchmark_records_path or self.config.benchmark_records_path
        summary_path = benchmark_summary_path or self.config.benchmark_summary_path
        logs_path = log_export_path or self.config.log_export_path
        Path(records_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(logs_path).parent.mkdir(parents=True, exist_ok=True)
        benchmark.export_json(records_path, slowest_first=False)
        export_benchmark_report_json(summary_path)
        export_log_json(logs_path)


def parse_args(argv: Optional[list[str]] = None) -> RuntimeConfig:
    _load_env_file()
    parser = argparse.ArgumentParser(description="THE Seed OpenRA runtime")
    parser.add_argument("--game-host", default=os.environ.get("OPENRA_HOST", "localhost"))
    parser.add_argument("--game-port", type=int, default=int(os.environ.get("OPENRA_PORT", "7445")))
    parser.add_argument("--game-language", default=os.environ.get("OPENRA_LANGUAGE", "zh"))
    parser.add_argument("--ws-host", default=os.environ.get("WS_HOST", "0.0.0.0"))
    parser.add_argument("--ws-port", type=int, default=int(os.environ.get("WS_PORT", "8765")))
    parser.add_argument("--tick-hz", type=float, default=float(os.environ.get("TICK_HZ", "10.0")))
    parser.add_argument("--actors-refresh-s", type=float, default=float(os.environ.get("WORLD_ACTORS_REFRESH_S", "0.1")))
    parser.add_argument("--economy-refresh-s", type=float, default=float(os.environ.get("WORLD_ECONOMY_REFRESH_S", "0.5")))
    parser.add_argument("--map-refresh-s", type=float, default=float(os.environ.get("WORLD_MAP_REFRESH_S", "1.0")))
    parser.add_argument("--review-interval", type=float, default=float(os.environ.get("TASK_REVIEW_INTERVAL", "10.0")))
    parser.add_argument("--queue-manager-mode", default=os.environ.get("QUEUE_MANAGER_MODE", "auto_place"))
    parser.add_argument("--queue-ready-timeout-s", type=float, default=float(os.environ.get("QUEUE_READY_TIMEOUT_S", "5.0")))
    parser.add_argument("--llm-provider", default=os.environ.get("LLM_PROVIDER", "qwen"))
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "qwen-plus"))
    parser.add_argument("--adjutant-llm-provider", default=os.environ.get("ADJUTANT_LLM_PROVIDER"))
    parser.add_argument("--adjutant-llm-model", default=os.environ.get("ADJUTANT_LLM_MODEL"))
    parser.add_argument("--benchmark-records-path", default=os.environ.get("BENCHMARK_RECORDS_PATH", "docs/wang/phase7_e2e_benchmark_records.json"))
    parser.add_argument("--benchmark-summary-path", default=os.environ.get("BENCHMARK_SUMMARY_PATH", "docs/wang/phase7_e2e_benchmark_summary.json"))
    parser.add_argument("--log-export-path", default=os.environ.get("LOG_EXPORT_PATH", "docs/wang/phase7_runtime_logs.json"))
    parser.add_argument("--log-session-root", default=os.environ.get("LOG_SESSION_ROOT", "Logs/runtime"))
    parser.add_argument("--disable-ws", action="store_true")
    parser.add_argument("--skip-game-api-check", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "WARNING"), help="Logging level (DEBUG/INFO/WARNING/ERROR)")
    args = parser.parse_args(argv)
    return RuntimeConfig(
        game_host=args.game_host,
        game_port=args.game_port,
        game_language=args.game_language,
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        tick_hz=args.tick_hz,
        actors_refresh_s=args.actors_refresh_s,
        economy_refresh_s=args.economy_refresh_s,
        map_refresh_s=args.map_refresh_s,
        review_interval=args.review_interval,
        queue_manager_mode=args.queue_manager_mode,
        queue_ready_timeout_s=args.queue_ready_timeout_s,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        adjutant_llm_provider=args.adjutant_llm_provider,
        adjutant_llm_model=args.adjutant_llm_model,
        benchmark_records_path=args.benchmark_records_path,
        benchmark_summary_path=args.benchmark_summary_path,
        log_export_path=args.log_export_path,
        log_session_root=args.log_session_root,
        enable_ws=not args.disable_ws,
        verify_game_api=not args.skip_game_api_check,
        log_level=args.log_level,
    )


def configure_logging(level: str = "WARNING") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run_runtime(config: RuntimeConfig) -> int:
    configure_logging(config.log_level)
    session_dir = start_persistence_session(
        config.log_session_root,
        metadata={
            "game_host": config.game_host,
            "game_port": config.game_port,
            "ws_port": config.ws_port,
            "llm_provider": config.llm_provider,
            "llm_model": config.llm_model,
        },
    )
    slog.info("Persistent log session started", event="log_session_started", session_dir=str(session_dir))
    if config.verify_game_api and not GameAPI.is_server_running(config.game_host, config.game_port):
        print(
            f"OpenRA server is not reachable at {config.game_host}:{config.game_port}. "
            "Use --skip-game-api-check to bypass the preflight.",
            file=sys.stderr,
        )
        stop_persistence_session()
        return 2

    runtime = ApplicationRuntime(config=config)
    await runtime.start()

    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        slog.warn("Shutdown requested", event="runtime_shutdown_requested")
        runtime.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_shutdown())

    try:
        await runtime.wait_until_stopped()
    finally:
        if not runtime._shutdown_event.is_set():
            await runtime.stop()
        session_dir_now = current_session_dir()
        if session_dir_now is not None:
            benchmark.export_json(session_dir_now / "benchmark_records.json", slowest_first=False)
            export_benchmark_report_json(session_dir_now / "benchmark_summary.json")
            export_log_json(session_dir_now / "all.pretty.json")
        slog.info(
            "Persistent log session stopped",
            event="log_session_stopped",
            session_dir=str(session_dir_now) if session_dir_now is not None else None,
        )
        stop_persistence_session()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    config = parse_args(argv)
    return asyncio.run(run_runtime(config))


if __name__ == "__main__":
    raise SystemExit(main())
