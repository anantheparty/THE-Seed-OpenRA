"""Phase 7 application entrypoint and runtime assembly.

Start order (design.md §2):
    GameAPI -> UnitRegistry -> WorldModel -> Kernel -> Dashboard(WS) -> GameLoop
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
from dataclasses import dataclass
import importlib.util
import logging
import os
from pathlib import Path
import signal
import sys
import time

# Enable faulthandler: SIGUSR1 dumps all thread tracebacks to stderr
faulthandler.enable()
if hasattr(faulthandler, "register") and hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)
from typing import Any, Optional

import benchmark

from adjutant import Adjutant, AdjutantConfig
from dashboard_publish import DashboardPublisher
from experts.base import ExecutionExpert
from experts.combat import CombatExpert
from experts.deploy import DeployExpert
from experts.economy import EconomyExpert
from experts.info_base_state import BaseStateExpert
from experts.info_threat import ThreatAssessor
from experts.movement import MovementExpert
from experts.occupy import OccupyExpert
from experts.rally import RallyExpert
from experts.repair import RepairExpert
from experts.recon import ReconExpert
from experts.stop import StopExpert
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
    install_benchmark_logging,
    start_persistence_session,
    stop_persistence_session,
)
from models import PlayerResponse, TaskMessage, TaskMessageType, TaskStatus
from openra_api.game_api import GameAPI
from queue_manager import QueueManager, QueueManagerConfig
from session_browser import (
    build_session_history_payload,
    build_session_catalog_payload,
    build_session_task_catalog_payload,
    build_task_replay_payload,
    default_session_dir,
    resolve_session_dir,
)
from task_agent import AgentConfig
from task_replay import build_live_task_replay_bundle, build_task_replay_bundle
from task_triage import (
    build_live_task_payload,
    build_runtime_unit_pipeline_focus,
    build_runtime_unit_pipeline_preview,
    build_runtime_unit_pipeline_preview_items,
)
from unit_registry import UnitRegistry, set_default_registry
from world_model import GameAPIWorldSource, RefreshPolicy, WorldModel, WorldModelSource
from ws_server import InboundHandler, WSServer, WSServerConfig


slog = get_logger("main")
TASK_REPLAY_RAW_ENTRY_LIMIT = 80


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
    map_refresh_s: float = 5.0
    review_interval: float = 10.0
    queue_manager_mode: str = "auto_place"
    queue_ready_timeout_s: float = 5.0
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    adjutant_llm_provider: Optional[str] = None
    adjutant_llm_model: Optional[str] = None
    benchmark_records_path: str = "docs/wang/phase7_e2e_benchmark_records.json"
    benchmark_summary_path: str = "docs/wang/phase7_e2e_benchmark_summary.json"
    log_export_path: str = "docs/wang/phase7_runtime_logs.json"
    log_session_root: str = "Logs/runtime"
    enable_ws: bool = True
    enable_voice: bool = False
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
    if normalized == "deepseek":
        if importlib.util.find_spec("openai") is None:
            raise RuntimeError(
                "LLM provider 'deepseek' requires Python package 'openai' in the backend runtime environment. "
                "Install it before starting main.py."
            )
        from llm import DeepSeekProvider
        return DeepSeekProvider(model=model)
    if normalized == "mock":
        return MockProvider([])
    raise ValueError(f"Unsupported LLM provider: {provider_name}")


def build_default_expert_registry(game_api: Any, world_model: WorldModel) -> dict[str, ExecutionExpert]:
    return {
        "ReconExpert": ReconExpert(game_api=game_api, world_model=world_model),
        "MovementExpert": MovementExpert(game_api=game_api, world_model=world_model),
        "StopExpert": StopExpert(game_api=game_api, world_model=world_model),
        "RepairExpert": RepairExpert(game_api=game_api, world_model=world_model),
        "OccupyExpert": OccupyExpert(game_api=game_api, world_model=world_model),
        "RallyExpert": RallyExpert(game_api=game_api, world_model=world_model),
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
        self.log_session_root = "Logs/runtime"
        self._probe_fault_state: dict[str, Any] = {}

        def _task_payload_builder(task: Any, jobs: Optional[list[Any]] = None, **kwargs: Any) -> dict[str, Any]:
            runtime_state = kwargs.get("runtime_state")
            if runtime_state is None:
                runtime_state = self.kernel.runtime_state()
            runtime_facts = kwargs.get("runtime_facts")
            runtime_task = {}
            if isinstance(runtime_state, dict):
                active_tasks = runtime_state.get("active_tasks", {})
                if isinstance(active_tasks, dict):
                    runtime_task = dict(active_tasks.get(getattr(task, "task_id", ""), {}) or {})
            if runtime_facts is None and bool(
                getattr(task, "is_capability", False) or runtime_task.get("is_capability")
            ):
                compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
                if callable(compute_runtime_facts):
                    try:
                        runtime_facts = compute_runtime_facts(
                            getattr(task, "task_id", ""),
                            include_buildable=False,
                        ) or {}
                    except Exception:
                        runtime_facts = {}
            world_sync = kwargs.get("world_sync")
            if not isinstance(world_sync, dict):
                world_sync = self._world_sync_health()
            return build_live_task_payload(
                task,
                jobs or [],
                runtime_state=runtime_state,
                runtime_facts=runtime_facts,
                list_pending_questions=self.kernel.list_pending_questions,
                list_task_messages=self.kernel.list_task_messages,
                world_sync=world_sync,
                world_stale=bool(world_sync.get("stale", False)),
                log_session_dir=current_session_dir(),
            )

        self._task_payload_builder = _task_payload_builder
        self._publisher = DashboardPublisher(
            kernel=self.kernel,
            task_message_callback=self._handle_published_task_message,
            dashboard_payload_builder=lambda: self._build_dashboard_payload(),
            task_payload_builder=lambda task, jobs, **kwargs: self._task_to_dict(task, jobs, **kwargs),
        )

    def attach_ws_server(self, ws_server: Optional[WSServer]) -> None:
        self.ws_server = ws_server
        self._publisher.attach_ws_server(ws_server)

    def attach_runtime(self, runtime: ApplicationRuntime) -> None:
        self.runtime = runtime

    def _handle_published_task_message(self, message: TaskMessage) -> None:
        if self.adjutant is None:
            return
        notify_task_completed = getattr(self.adjutant, "notify_task_completed", None)
        notify_task_message = getattr(self.adjutant, "notify_task_message", None)
        if message.type == TaskMessageType.TASK_COMPLETE_REPORT:
            task_obj = next((t for t in self.kernel.list_tasks() if t.task_id == message.task_id), None)
            if task_obj is None:
                return
            if not callable(notify_task_completed):
                return
            notify_task_completed(
                label=getattr(task_obj, "label", message.task_id),
                raw_text=task_obj.raw_text,
                result=task_obj.status.value,
                summary=message.content,
                task_id=message.task_id,
            )
            return
        if message.type in (TaskMessageType.TASK_WARNING, TaskMessageType.TASK_INFO) and callable(notify_task_message):
            notify_task_message(
                task_id=message.task_id,
                message_type=message.type.value,
                content=message.content,
            )

    def sync_runtime(self) -> None:
        active_agent_ids: set[str] = set()
        for task in self.kernel.list_tasks():
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                continue
            agent = self.kernel.get_task_agent(task.task_id)
            if agent is None:
                continue
            active_agent_ids.add(task.task_id)
            if task.task_id not in self._registered_agents:
                review_interval = getattr(getattr(agent, "config", None), "review_interval", 10.0)
                self.game_loop.register_agent(
                    task.task_id,
                    agent.queue,
                    review_interval=review_interval,
                    is_suspended=lambda agent=agent: agent.is_suspended,
                )
                self._registered_agents.add(task.task_id)

        for task_id in list(self._registered_agents):
            if task_id not in active_agent_ids:
                self.game_loop.unregister_agent(task_id)
                self._registered_agents.discard(task_id)

        active_job_ids: set[str] = set()
        for controller in self.kernel.active_jobs():
            active_job_ids.add(controller.job_id)
            if controller.job_id not in self._registered_jobs:
                self.game_loop.register_job(controller)
                self._registered_jobs.add(controller.job_id)

        for job_id in list(self._registered_jobs):
            if job_id not in active_job_ids:
                self.game_loop.unregister_job(job_id)
                self._registered_jobs.discard(job_id)

    async def publish_dashboard(self) -> None:
        await self._publisher.publish_all()

    def on_tick(self, tick_number: int, now: float) -> None:
        self.sync_runtime()
        self._publisher.schedule_publish()

    async def on_command_submit(self, text: str, client_id: str) -> None:
        try:
            if self.adjutant is None:
                # Design intent: when Adjutant is unavailable (no LLM configured,
                # headless/test mode), commands bypass NLU and are submitted directly
                # to the Kernel as unclassified "managed" tasks.  This is a deliberate
                # degraded-mode fallback — not a routing bug.
                task = self.kernel.create_task(text, kind="managed", priority=50)
                await self._publisher.emit_adjutant_response(
                    f"收到指令，已创建任务 {task.task_id}",
                    response_type="command",
                    client_id=client_id,
                )
                self.sync_runtime()
                await self.publish_dashboard()
                return

            result = await self.adjutant.handle_player_input(text)
            response_text = result.get("response_text")
            if response_text:
                await self._publisher.emit_adjutant_response(
                    response_text,
                    response_type=result.get("type", "info"),
                    ok=result.get("ok", True),
                    client_id=client_id,
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
            await self._publisher.emit_adjutant_response(
                f"指令处理失败: {text[:50]}",
                response_type="error",
                ok=False,
                client_id=client_id,
            )

    async def on_command_cancel(self, task_id: str, client_id: str) -> None:
        del client_id
        ok = self.kernel.cancel_task(task_id)
        content = "任务已取消" if ok else "取消失败：任务不存在或已结束"
        await self._publisher.emit_notification("command_cancel", content, data={"task_id": task_id, "ok": ok})
        self.sync_runtime()
        await self.publish_dashboard()

    async def on_mode_switch(self, mode: str, client_id: str) -> None:
        del client_id
        if mode:
            self.mode = mode
        await self.publish_dashboard()

    async def on_question_reply(self, message_id: str, task_id: str, answer: str, client_id: str) -> None:
        result = self.kernel.submit_player_response(
            PlayerResponse(message_id=message_id, task_id=task_id, answer=answer)
        )
        await self._publisher.emit_adjutant_response(
            result.get("message", "已回复" if result.get("ok", False) else "回复失败"),
            response_type="reply",
            ok=result.get("ok", False),
            client_id=client_id,
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
        await self._send_sync_baseline(client_id, include_history=True)

    async def on_diagnostics_sync_request(self, client_id: str) -> None:
        """Diagnostics opened on a warm client — refresh state without replaying chat/task history."""
        await self._send_sync_baseline(client_id, include_history=False)
        await self._send_session_history_to_client(client_id, session_dir=default_session_dir(self.log_session_root))

    async def _send_sync_baseline(self, client_id: str, *, include_history: bool) -> None:
        self.sync_runtime()
        world_sync = self._world_sync_health()
        runtime_fault_state = self._runtime_fault_state()
        dashboard = self._build_dashboard_payload(
            current_world_health=world_sync,
            current_runtime_fault_state=runtime_fault_state,
        )
        await self._publisher.send_current_dashboard_to_client(client_id, dashboard=dashboard)
        selected_session_dir = default_session_dir(self.log_session_root)
        await self._send_session_catalog_to_client(
            client_id,
            selected_session_dir=selected_session_dir,
            current_world_health=world_sync,
            current_runtime_fault_state=runtime_fault_state,
        )
        await self._send_session_tasks_to_client(client_id, session_dir=selected_session_dir)
        if include_history:
            await self._publisher.replay_history(client_id)

    async def on_session_clear(self, client_id: str) -> None:
        previous_session_dir = current_session_dir()
        stop_persistence_session()
        clear_logs()
        benchmark.clear()
        new_session_dir = start_persistence_session(
            self.log_session_root,
            metadata={"reason": "session_clear"},
        )
        slog.info(
            "Persistent log session rotated",
            event="log_session_rotated",
            previous_session_dir=str(previous_session_dir) if previous_session_dir is not None else None,
            session_dir=str(new_session_dir),
        )
        self.kernel.reset_session()
        if self.adjutant is not None:
            self.adjutant.clear_dialogue_history()
        reset_snapshot = getattr(self.world_model, "reset_snapshot", None)
        if callable(reset_snapshot):
            reset_snapshot()
        reset_loop_runtime = getattr(self.game_loop, "reset_runtime_state", None)
        if callable(reset_loop_runtime):
            reset_loop_runtime()
        self._publisher.clear_runtime_state()
        self._probe_fault_state = {}
        self.sync_runtime()
        if self.ws_server is not None and self.ws_server.is_running:
            await self._publisher.send_session_cleared()
            await self._send_session_catalog_to_client(client_id, selected_session_dir=new_session_dir)
            await self._send_session_tasks_to_client(client_id, session_dir=new_session_dir)
        await self.publish_dashboard()

    async def on_session_select(self, session_dir: str, client_id: str) -> None:
        selected_session_dir = resolve_session_dir(self.log_session_root, session_dir)
        if selected_session_dir is None:
            if self.ws_server is not None and self.ws_server.is_running:
                await self.ws_server.send_error_to_client(
                    client_id,
                    f"Invalid session_select: unknown session_dir {session_dir}",
                    inbound_type="session_select",
                    code="INVALID_SESSION",
                )
            return
        await self._send_session_catalog_to_client(client_id, selected_session_dir=selected_session_dir)
        await self._send_session_tasks_to_client(client_id, session_dir=selected_session_dir)
        await self._send_session_history_to_client(client_id, session_dir=selected_session_dir)

    async def on_task_replay_request(
        self,
        task_id: str,
        client_id: str,
        session_dir: Optional[str] = None,
        include_entries: bool = True,
    ) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        resolved_session_dir = resolve_session_dir(self.log_session_root, session_dir)
        if session_dir and resolved_session_dir is None:
            await self.ws_server.send_error_to_client(
                client_id,
                f"Invalid task_replay_request: unknown session_dir {session_dir}",
                inbound_type="task_replay_request",
                code="INVALID_SESSION",
            )
            return
        resolved_session_dir = resolved_session_dir or default_session_dir(self.log_session_root)
        live_session_dir = current_session_dir()
        use_live_bundle = (
            resolved_session_dir is not None
            and live_session_dir is not None
            and resolved_session_dir.resolve() == live_session_dir.resolve()
        )
        runtime_state = self.kernel.runtime_state()
        replay_runtime_state = runtime_state if use_live_bundle else {}
        await self._publisher.send_task_replay_to_client(
            client_id,
            build_task_replay_payload(
                task_id,
                requested_session_dir=str(resolved_session_dir) if resolved_session_dir is not None else session_dir,
                log_session_root=self.log_session_root,
                raw_entry_limit=TASK_REPLAY_RAW_ENTRY_LIMIT,
                include_entries=include_entries,
                bundle_builder=(
                    (
                        lambda entries, _resolved_session_dir: build_live_task_replay_bundle(
                            task_id,
                            entries,
                            runtime_state=runtime_state,
                            tasks=self.kernel.list_tasks(),
                            jobs_for_task=self.kernel.jobs_for_task,
                            task_payload_builder=self._task_to_dict,
                            compute_runtime_facts=getattr(self.world_model, "compute_runtime_facts", None),
                        )
                    )
                    if use_live_bundle
                    else (
                        lambda entries, _resolved_session_dir: build_task_replay_bundle(
                            task_id,
                            entries,
                            runtime_state=replay_runtime_state,
                        )
                    )
                ),
            ),
        )

    def _task_to_dict(
        self,
        task: Any,
        jobs: Optional[list[Any]] = None,
        *,
        runtime_state: Optional[dict[str, Any]] = None,
        world_sync: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._task_payload_builder(
            task,
            jobs,
            runtime_state=runtime_state,
            world_sync=world_sync,
        )

    async def on_game_restart(self, save_path: Optional[str], client_id: str) -> None:
        del client_id
        if self.runtime is None:
            await self._publisher.emit_notification("error", "游戏重启失败：runtime 未挂载")
            return
        await self.runtime.restart_game(save_path=save_path)

    async def _send_session_catalog_to_client(
        self,
        client_id: str,
        *,
        selected_session_dir: Optional[Path],
        current_world_health: Optional[dict[str, Any]] = None,
        current_runtime_fault_state: Optional[dict[str, Any]] = None,
    ) -> None:
        await self._publisher.send_session_catalog_to_client(
            client_id,
            build_session_catalog_payload(
                self.log_session_root,
                selected_session_dir=selected_session_dir,
                current_world_health=current_world_health if isinstance(current_world_health, dict) else self._world_sync_health(),
                current_runtime_fault_state=(
                    current_runtime_fault_state
                    if isinstance(current_runtime_fault_state, dict)
                    else self._runtime_fault_state()
                ),
                current_tasks=[
                    {
                        "task_id": task.task_id,
                        "status": str(task.status.value),
                    }
                    for task in self.kernel.list_tasks()
                ],
            ),
        )

    async def _send_session_tasks_to_client(
        self,
        client_id: str,
        *,
        session_dir: Optional[Path],
    ) -> None:
        await self._publisher.send_session_task_catalog_to_client(
            client_id,
            build_session_task_catalog_payload(
                self.log_session_root,
                session_dir=session_dir,
            ),
        )

    async def _send_session_history_to_client(
        self,
        client_id: str,
        *,
        session_dir: Optional[Path],
    ) -> None:
        await self._publisher.send_session_history_to_client(
            client_id,
            build_session_history_payload(
                self.log_session_root,
                session_dir=session_dir,
            ),
        )

    def _build_dashboard_payload(
        self,
        *,
        current_world_health: Optional[dict[str, Any]] = None,
        current_runtime_fault_state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        pending_questions = self.kernel.list_pending_questions()
        runtime_state = self.kernel.runtime_state()
        dashboard_runtime_facts = self._dashboard_runtime_facts()
        world_sync = current_world_health if isinstance(current_world_health, dict) else self._world_sync_health()
        runtime_fault_state = (
            dict(current_runtime_fault_state)
            if isinstance(current_runtime_fault_state, dict)
            else self._runtime_fault_state()
        )
        world_snapshot = {
            **self.world_model.world_summary(),
            "disconnected": bool(world_sync.get("disconnected", False)),
            "runtime_state": runtime_state,
            "pending_questions": pending_questions,
            "mode": self.mode,
            "player_faction": str(dashboard_runtime_facts.get("faction") or ""),
            "capability_truth_blocker": str(dashboard_runtime_facts.get("capability_truth_blocker") or ""),
            "unit_pipeline_preview": build_runtime_unit_pipeline_preview(runtime_state),
            "unit_pipeline_focus": build_runtime_unit_pipeline_focus(runtime_state),
            "unit_pipeline_preview_items": build_runtime_unit_pipeline_preview_items(runtime_state),
            "runtime_fault_state": runtime_fault_state,
        }
        tasks = [
            self._task_to_dict(
                task,
                self.kernel.jobs_for_task(task.task_id),
                runtime_state=runtime_state,
                world_sync=world_sync,
            )
            for task in self.kernel.list_tasks()
        ]
        return {
            "world_snapshot": world_snapshot,
            "tasks": tasks,
            "pending_questions": pending_questions,
        }

    def _world_is_stale(self) -> bool:
        return bool(self._world_sync_health().get("stale", False))

    def _dashboard_runtime_facts(self) -> dict[str, Any]:
        compute_runtime_facts = getattr(self.world_model, "compute_runtime_facts", None)
        if not callable(compute_runtime_facts):
            return {}
        try:
            facts = compute_runtime_facts("__dashboard__", include_buildable=False) or {}
            self._clear_probe_fault("dashboard_runtime_facts")
            return dict(facts) if isinstance(facts, dict) else {}
        except Exception as exc:
            self._record_probe_fault(source="dashboard_runtime_facts", error=repr(exc))
            return {}

    def _world_sync_health(self) -> dict[str, Any]:
        refresh_health = getattr(self.world_model, "refresh_health", None)
        if not callable(refresh_health):
            return {"stale": False}
        try:
            health = refresh_health() or {}
            if isinstance(health, dict):
                self._clear_probe_fault("world_sync_probe")
                return dict(health)
            return {"stale": False}
        except Exception as exc:
            self._record_probe_fault(source="world_sync_probe", error=repr(exc))
            return {"stale": False}

    def _record_probe_fault(self, *, source: str, error: str) -> None:
        now = time.time()
        previous_count = int(self._probe_fault_state.get("count", 0) or 0)
        previous_first_at = float(self._probe_fault_state.get("first_at", 0.0) or 0.0)
        next_fault_state = {
            "degraded": True,
            "source": str(source or ""),
            "stage": "",
            "error": str(error or ""),
            "count": previous_count + 1,
            "first_at": previous_first_at or now,
            "updated_at": now,
        }
        previous_source = str(self._probe_fault_state.get("source") or "")
        previous_error = str(self._probe_fault_state.get("error") or "")
        self._probe_fault_state = next_fault_state
        if previous_source != next_fault_state["source"] or previous_error != next_fault_state["error"]:
            slog.warn(
                "Runtime probe fault",
                event="runtime_probe_fault",
                timestamp=now,
                source=next_fault_state["source"],
                error=next_fault_state["error"],
            )

    def _clear_probe_fault(self, source: str) -> None:
        if str(self._probe_fault_state.get("source") or "") == str(source or ""):
            self._probe_fault_state = {}

    def _runtime_fault_state(self) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        if self._probe_fault_state.get("degraded"):
            candidates.append(dict(self._probe_fault_state))
        publisher_fault = self._publisher.runtime_fault_state()
        if publisher_fault.get("degraded"):
            candidates.append(dict(publisher_fault))
        if not candidates:
            return {}
        latest = max(candidates, key=lambda item: float(item.get("updated_at") or 0.0))
        first_candidates = [
            float(item.get("first_at") or item.get("updated_at") or 0.0)
            for item in candidates
            if float(item.get("first_at") or item.get("updated_at") or 0.0) > 0.0
        ]
        return {
            **latest,
            "count": sum(max(int(item.get("count", 0) or 0), 1) for item in candidates),
            "first_at": min(first_candidates) if first_candidates else float(latest.get("updated_at") or 0.0),
        }


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
        self.world_model.register_info_expert(BaseStateExpert())
        self.world_model.register_info_expert(ThreatAssessor())

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
        self.bridge.log_session_root = config.log_session_root
        self.bridge.attach_runtime(self)
        self.game_loop._dashboard_callback = self.bridge.on_tick  # type: ignore[attr-defined]
        self.ws_server = (
            WSServer(
                config=WSServerConfig(host=config.ws_host, port=config.ws_port, voice_enabled=config.enable_voice),
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
        install_benchmark_logging()
        if self.ws_server is not None:
            await self.ws_server.start()
        await asyncio.to_thread(self.world_model.refresh, force=True)
        self.bridge.sync_runtime()
        if self.ws_server is not None:
            await self.bridge.publish_dashboard()
        self.kernel.ensure_capability_task()
        self._loop_task = asyncio.create_task(self.game_loop.start())
        slog.info("ApplicationRuntime started", event="runtime_started", ws_enabled=bool(self.ws_server))

    async def stop(self) -> None:
        await self._stop_loop_task()
        api_close = getattr(self.api, "close", None)
        if callable(api_close):
            await asyncio.to_thread(api_close)
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
                await asyncio.to_thread(api_close)
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
            reset_loop_runtime = getattr(self.game_loop, "reset_runtime_state", None)
            if callable(reset_loop_runtime):
                reset_loop_runtime()
            await asyncio.to_thread(self.world_model.refresh, force=True)
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
    parser.add_argument("--map-refresh-s", type=float, default=float(os.environ.get("WORLD_MAP_REFRESH_S", "5.0")))
    parser.add_argument("--review-interval", type=float, default=float(os.environ.get("TASK_REVIEW_INTERVAL", "10.0")))
    parser.add_argument("--queue-manager-mode", default=os.environ.get("QUEUE_MANAGER_MODE", "auto_place"))
    parser.add_argument("--queue-ready-timeout-s", type=float, default=float(os.environ.get("QUEUE_READY_TIMEOUT_S", "5.0")))
    parser.add_argument("--llm-provider", default=os.environ.get("LLM_PROVIDER", "deepseek"))
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "deepseek-chat"))
    parser.add_argument("--adjutant-llm-provider", default=os.environ.get("ADJUTANT_LLM_PROVIDER"))
    parser.add_argument("--adjutant-llm-model", default=os.environ.get("ADJUTANT_LLM_MODEL"))
    parser.add_argument("--benchmark-records-path", default=os.environ.get("BENCHMARK_RECORDS_PATH", "docs/wang/phase7_e2e_benchmark_records.json"))
    parser.add_argument("--benchmark-summary-path", default=os.environ.get("BENCHMARK_SUMMARY_PATH", "docs/wang/phase7_e2e_benchmark_summary.json"))
    parser.add_argument("--log-export-path", default=os.environ.get("LOG_EXPORT_PATH", "docs/wang/phase7_runtime_logs.json"))
    parser.add_argument("--log-session-root", default=os.environ.get("LOG_SESSION_ROOT", "Logs/runtime"))
    parser.add_argument("--disable-ws", action="store_true")
    parser.add_argument(
        "--enable-voice",
        action="store_true",
        default=_env_bool("ENABLE_VOICE", False),
        help="Enable optional voice ASR/TTS endpoints and startup dependency checks",
    )
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
        enable_voice=args.enable_voice,
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

    runtime: Optional[ApplicationRuntime] = None
    try:
        runtime = ApplicationRuntime(config=config)
        await runtime.start()

        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            slog.warn("Shutdown requested", event="runtime_shutdown_requested")
            assert runtime is not None
            runtime.request_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda *_: _request_shutdown())

        await runtime.wait_until_stopped()
        return 0
    finally:
        try:
            if runtime is not None:
                shutdown_event = getattr(runtime, "_shutdown_event", None)
                is_stopped = bool(shutdown_event.is_set()) if callable(getattr(shutdown_event, "is_set", None)) else False
                if not is_stopped:
                    stop_runtime = getattr(runtime, "stop", None)
                    if callable(stop_runtime):
                        await stop_runtime()
        finally:
            session_dir_now = current_session_dir()
            try:
                if session_dir_now is not None:
                    benchmark.export_json(session_dir_now / "benchmark_records.json", slowest_first=False)
                    export_benchmark_report_json(session_dir_now / "benchmark_summary.json")
                    export_log_json(session_dir_now / "all.pretty.json")
            finally:
                slog.info(
                    "Persistent log session stopped",
                    event="log_session_stopped",
                    session_dir=str(session_dir_now) if session_dir_now is not None else None,
                )
                stop_persistence_session()


def main(argv: Optional[list[str]] = None) -> int:
    config = parse_args(argv)
    return asyncio.run(run_runtime(config))


if __name__ == "__main__":
    raise SystemExit(main())
