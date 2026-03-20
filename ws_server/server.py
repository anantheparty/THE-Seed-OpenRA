"""WebSocket backend server (design.md §7).

Inbound: command_submit, command_cancel, mode_switch
Outbound: world_snapshot, task_update, task_list, log_entry,
          player_notification, query_response

All payloads carry timestamp. JSON serialization. Built on aiohttp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)


# --- Inbound message handler protocol ---

class InboundHandler(Protocol):
    """Handles inbound WS messages from the frontend."""

    async def on_command_submit(self, text: str, client_id: str) -> None: ...
    async def on_command_cancel(self, task_id: str, client_id: str) -> None: ...
    async def on_mode_switch(self, mode: str, client_id: str) -> None: ...
    async def on_question_reply(self, message_id: str, task_id: str, answer: str, client_id: str) -> None: ...
    async def on_game_control(self, action: str, client_id: str) -> None: ...


class NoOpInboundHandler:
    """Default no-op handler for inbound messages."""

    async def on_command_submit(self, text: str, client_id: str) -> None:
        logger.info("command_submit: %r from %s", text, client_id)

    async def on_command_cancel(self, task_id: str, client_id: str) -> None:
        logger.info("command_cancel: %s from %s", task_id, client_id)

    async def on_mode_switch(self, mode: str, client_id: str) -> None:
        logger.info("mode_switch: %s from %s", mode, client_id)

    async def on_question_reply(self, message_id: str, task_id: str, answer: str, client_id: str) -> None:
        logger.info("question_reply: msg=%s task=%s answer=%r from %s", message_id, task_id, answer, client_id)

    async def on_game_control(self, action: str, client_id: str) -> None:
        logger.info("game_control: %s from %s", action, client_id)


@dataclass
class WSServerConfig:
    """WebSocket server configuration."""

    host: str = "0.0.0.0"
    port: int = 8765


class WSServer:
    """WebSocket server — manages clients and message routing."""

    def __init__(
        self,
        config: Optional[WSServerConfig] = None,
        inbound_handler: Optional[InboundHandler] = None,
    ) -> None:
        self.config = config or WSServerConfig()
        self.inbound_handler = inbound_handler or NoOpInboundHandler()
        self._clients: dict[str, web.WebSocketResponse] = {}
        self._client_counter = 0
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._running = False

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._app = web.Application()
        self._app.router.add_get("/ws", self._ws_handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        self._running = True
        logger.info("WS server started on %s:%d", self.config.host, self.config.port)

    async def stop(self) -> None:
        """Stop the server and disconnect all clients."""
        self._running = False
        # Close all WS connections
        for ws in list(self._clients.values()):
            await ws.close()
        self._clients.clear()
        if self._runner:
            await self._runner.cleanup()
        logger.info("WS server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # --- Client handling ---

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a single client WebSocket connection."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._client_counter += 1
        client_id = f"client_{self._client_counter}"
        self._clients[client_id] = ws
        logger.info("Client connected: %s (total: %d)", client_id, len(self._clients))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        message = json.loads(msg.data)
                        await self._handle_inbound(message, client_id)
                    except json.JSONDecodeError:
                        await self._send_to(client_id, {
                            "type": "error",
                            "message": "Invalid JSON",
                            "timestamp": time.time(),
                        })
                    except Exception as e:
                        logger.exception("Error handling message from %s", client_id)
                        await self._send_to(client_id, {
                            "type": "error",
                            "message": str(e),
                            "timestamp": time.time(),
                        })
                elif msg.type == WSMsgType.ERROR:
                    logger.warning("WS error from %s: %s", client_id, ws.exception())
        finally:
            self._clients.pop(client_id, None)
            logger.info("Client disconnected: %s (total: %d)", client_id, len(self._clients))

        return ws

    async def _handle_inbound(self, message: dict[str, Any], client_id: str) -> None:
        """Route an inbound message to the appropriate handler."""
        msg_type = message.get("type")
        if msg_type == "command_submit":
            await self.inbound_handler.on_command_submit(
                message.get("text", ""), client_id
            )
        elif msg_type == "command_cancel":
            await self.inbound_handler.on_command_cancel(
                message.get("task_id", ""), client_id
            )
        elif msg_type == "mode_switch":
            await self.inbound_handler.on_mode_switch(
                message.get("mode", ""), client_id
            )
        elif msg_type == "question_reply":
            await self.inbound_handler.on_question_reply(
                message.get("message_id", ""),
                message.get("task_id", ""),
                message.get("answer", ""),
                client_id,
            )
        elif msg_type in ("game_start", "game_stop", "game_restart"):
            await self.inbound_handler.on_game_control(msg_type, client_id)
        else:
            await self._send_to(client_id, {
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
                "timestamp": time.time(),
            })

    # --- Outbound broadcasting ---

    async def broadcast(self, msg_type: str, data: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        payload = json.dumps({
            "type": msg_type,
            "data": data,
            "timestamp": time.time(),
        }, ensure_ascii=False)
        disconnected = []
        for client_id, ws in list(self._clients.items()):
            try:
                await ws.send_str(payload)
            except Exception:
                disconnected.append(client_id)
        for client_id in disconnected:
            self._clients.pop(client_id, None)

    async def send_world_snapshot(self, snapshot: dict[str, Any]) -> None:
        await self.broadcast("world_snapshot", snapshot)

    async def send_benchmark(self, benchmark_data: list[dict[str, Any]]) -> None:
        await self.broadcast("benchmark", {"records": benchmark_data})

    async def send_task_update(self, task_data: dict[str, Any]) -> None:
        await self.broadcast("task_update", task_data)

    async def send_task_list(
        self,
        tasks: list[dict[str, Any]],
        pending_questions: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        payload: dict[str, Any] = {"tasks": tasks}
        if pending_questions is not None:
            payload["pending_questions"] = pending_questions
        await self.broadcast("task_list", payload)

    async def send_log_entry(self, entry: dict[str, Any]) -> None:
        await self.broadcast("log_entry", entry)

    async def send_player_notification(self, notification: dict[str, Any]) -> None:
        await self.broadcast("player_notification", notification)

    async def send_query_response(self, response: dict[str, Any]) -> None:
        await self.broadcast("query_response", response)

    # --- Internal ---

    async def _send_to(self, client_id: str, payload: dict[str, Any]) -> None:
        """Send a message to a specific client."""
        ws = self._clients.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_str(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self._clients.pop(client_id, None)
