"""WebSocket/dashboard publish helpers extracted from RuntimeBridge."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional

import benchmark

from adjutant import NotificationManager
from logging_system import get_logger, records_from as log_records_from
from models import TaskMessage, TaskMessageType
from ws_server import WSServer

slog = get_logger("dashboard_publish")


class DashboardPublisher:
    """Owns dashboard publish state and websocket fanout helpers."""

    def __init__(
        self,
        *,
        kernel: Any,
        task_message_callback: Optional[Callable[[TaskMessage], None]] = None,
        ws_server: Optional[WSServer] = None,
        dashboard_payload_builder: Callable[[], dict[str, Any]],
        task_payload_builder: Callable[..., dict[str, Any]],
    ) -> None:
        self.kernel = kernel
        self._task_message_callback = task_message_callback
        self.ws_server = ws_server
        self._dashboard_payload_builder = dashboard_payload_builder
        self._task_payload_builder = task_payload_builder

        self.task_fingerprints: dict[str, str] = {}
        self.task_message_offset = 0
        self.notification_manager: Optional[NotificationManager] = None
        self.log_offset = 0
        self.benchmark_offset = 0
        self.log_publish_batch_size = 200
        self.recent_responses: list[dict[str, Any]] = []
        self.publish_lock = asyncio.Lock()
        self.publish_task: Optional[asyncio.Task[Any]] = None
        self._runtime_fault_state: dict[str, Any] = {}

    def attach_ws_server(self, ws_server: Optional[WSServer]) -> None:
        self.ws_server = ws_server

    def schedule_publish(self) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        if self.publish_task is not None and not self.publish_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.publish_all())
        task.add_done_callback(self._handle_publish_task_done)
        self.publish_task = task

    def _handle_publish_task_done(self, task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._record_runtime_fault(source="dashboard_publish", stage="task", error=repr(exc))
            slog.error(
                "Dashboard publish task failed",
                event="dashboard_publish_task_failed",
                error=repr(exc),
            )
        finally:
            if self.publish_task is task:
                self.publish_task = None

    def clear_runtime_state(self) -> None:
        if self.publish_task is not None and not self.publish_task.done():
            self.publish_task.cancel()
        self.publish_task = None
        self.task_fingerprints.clear()
        self.task_message_offset = 0
        self.notification_manager = None
        self.log_offset = 0
        self.benchmark_offset = 0
        self.recent_responses.clear()
        self._runtime_fault_state = {}

    async def publish_all(self) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        async with self.publish_lock:
            had_fault = False
            had_fault |= not await self._run_publish_stage("dashboard", self.broadcast_current_dashboard)
            had_fault |= not await self._run_publish_stage("task_updates", self.publish_task_updates)
            had_fault |= not await self._run_publish_stage("task_messages", self.publish_task_messages)
            had_fault |= not await self._run_publish_stage("notifications", self.publish_notifications)
            had_fault |= not await self._run_publish_stage("logs", self.publish_logs)
            had_fault |= not await self._run_publish_stage("benchmarks", self.publish_benchmarks)
            if not had_fault:
                self._runtime_fault_state = {}

    async def _run_publish_stage(
        self,
        stage: str,
        publish: Callable[[], Awaitable[None]],
    ) -> bool:
        try:
            await publish()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_runtime_fault(source="dashboard_publish", stage=stage, error=repr(exc))
            slog.error(
                "Dashboard publish stage failed",
                event="dashboard_publish_stage_failed",
                stage=stage,
                error=repr(exc),
            )
            return False

    def _record_runtime_fault(self, *, source: str, stage: str, error: str) -> None:
        now = time.time()
        previous_count = int(self._runtime_fault_state.get("count", 0) or 0)
        previous_first_at = float(self._runtime_fault_state.get("first_at", 0.0) or 0.0)
        self._runtime_fault_state = {
            "degraded": True,
            "source": str(source or ""),
            "stage": str(stage or ""),
            "error": str(error or ""),
            "count": previous_count + 1,
            "first_at": previous_first_at or now,
            "updated_at": now,
        }

    def runtime_fault_state(self) -> dict[str, Any]:
        return dict(self._runtime_fault_state)

    async def publish_task_updates(self) -> None:
        assert self.ws_server is not None
        runtime_state = self.kernel.runtime_state()
        for task in self.kernel.list_tasks():
            payload = self._task_payload_builder(
                task,
                self.kernel.jobs_for_task(task.task_id),
                runtime_state=runtime_state,
            )
            fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if self.task_fingerprints.get(task.task_id) == fingerprint:
                continue
            self.task_fingerprints[task.task_id] = fingerprint
            await self.ws_server.send_task_update(payload)

    async def publish_task_messages(self) -> None:
        assert self.ws_server is not None
        task_messages = self.kernel.list_task_messages()
        new_messages = task_messages[self.task_message_offset :]
        self.task_message_offset = len(task_messages)
        for message in new_messages:
            payload = self._task_message_payload(message)
            await self.ws_server.send_task_message(payload)
            if message.type in {TaskMessageType.TASK_INFO, TaskMessageType.TASK_WARNING}:
                slog.info(
                    "Task message published",
                    event=message.type.value,
                    task_id=message.task_id,
                    message_id=message.message_id,
                    message_type=message.type.value,
                    content=message.content,
                )
            if self._task_message_callback is not None:
                self._task_message_callback(message)

    async def publish_notifications(self) -> None:
        assert self.ws_server is not None
        if self.notification_manager is None:
            self.notification_manager = NotificationManager(
                kernel=self.kernel,
                sink=self.ws_server.send_player_notification,
            )
        await self.notification_manager.poll_and_push()

    async def publish_logs(self) -> None:
        assert self.ws_server is not None
        new_records = log_records_from(self.log_offset, limit=self.log_publish_batch_size)
        self.log_offset += len(new_records)
        for record in new_records:
            if record.component == "benchmark":
                continue
            await self.ws_server.send_log_entry(record.to_dict())

    async def publish_benchmarks(self) -> None:
        assert self.ws_server is not None
        new_records = benchmark.records_from(self.benchmark_offset)
        if not new_records:
            return
        self.benchmark_offset += len(new_records)
        await self.ws_server.send_benchmark(
            {
                "records": [record.to_dict() for record in new_records],
                "replace": False,
            }
        )

    async def broadcast_current_dashboard(self) -> None:
        assert self.ws_server is not None
        dashboard = self._dashboard_payload_builder()
        await self.ws_server.send_world_snapshot(dashboard["world_snapshot"])
        await self.ws_server.send_task_list(
            dashboard["tasks"],
            pending_questions=dashboard["pending_questions"],
        )

    async def send_current_dashboard_to_client(self, client_id: str) -> None:
        assert self.ws_server is not None
        dashboard = self._dashboard_payload_builder()
        await self.ws_server.send_world_snapshot_to_client(
            client_id,
            dashboard["world_snapshot"],
        )
        await self.ws_server.send_task_list_to_client(
            client_id,
            dashboard["tasks"],
            pending_questions=dashboard["pending_questions"],
        )

    async def send_session_cleared(self) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        await self.ws_server.send_session_cleared()

    async def send_session_catalog_to_client(self, client_id: str, payload: dict[str, Any]) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        await self.ws_server.send_session_catalog_to_client(client_id, payload)

    async def send_session_task_catalog_to_client(self, client_id: str, payload: dict[str, Any]) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        await self.ws_server.send_session_task_catalog_to_client(client_id, payload)

    async def send_task_replay_to_client(self, client_id: str, payload: dict[str, Any]) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return
        await self.ws_server.send_task_replay_to_client(client_id, payload)

    async def emit_notification(
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

    async def emit_adjutant_response(
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
        self.recent_responses.append(dict(payload))
        if len(self.recent_responses) > 100:
            self.recent_responses = self.recent_responses[-100:]
        await self.ws_server.send_query_response(payload)

    async def replay_history(self, client_id: str) -> None:
        if self.ws_server is None or not self.ws_server.is_running:
            return

        history_logs = [
            record.to_dict()
            for record in log_records_from(0, limit=self.log_offset)
            if record.component != "benchmark"
        ][-500:]
        for entry in history_logs:
            await self.ws_server.send_to_client(client_id, "log_entry", entry)

        benchmark_history = [record.to_dict() for record in benchmark.records_from(0)]
        if benchmark_history:
            await self.ws_server.send_to_client(
                client_id,
                "benchmark",
                {
                    "records": benchmark_history,
                    "replace": True,
                },
            )

        for message in self.kernel.list_task_messages()[-100:]:
            if message.type == TaskMessageType.TASK_QUESTION:
                continue
            await self.ws_server.send_to_client(
                client_id,
                "task_message",
                self._task_message_payload(message),
            )

        for notification in self.kernel.list_player_notifications()[-100:]:
            await self.ws_server.send_to_client(client_id, "player_notification", notification)

        for response in self.recent_responses[-100:]:
            await self.ws_server.send_to_client(client_id, "query_response", response)

    def _task_message_payload(self, message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": message.type.value,
            "content": message.content,
            "task_id": message.task_id,
            "message_id": message.message_id,
            "timestamp": message.timestamp,
        }
        if message.options is not None:
            payload["options"] = message.options
        if message.timeout_s is not None:
            payload["timeout_s"] = message.timeout_s
        if message.default_option is not None:
            payload["default_option"] = message.default_option
        return payload
