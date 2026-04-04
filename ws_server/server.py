"""WebSocket backend server (design.md §7).

Inbound: command_submit, command_cancel, mode_switch, question_reply, game_restart, session_clear
Outbound: world_snapshot, task_update, task_list, log_entry,
          player_notification, query_response, session_cleared

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
    async def on_game_restart(self, save_path: Optional[str], client_id: str) -> None: ...
    async def on_sync_request(self, client_id: str) -> None: ...
    async def on_session_clear(self, client_id: str) -> None: ...


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

    async def on_game_restart(self, save_path: Optional[str], client_id: str) -> None:
        logger.info("game_restart: save=%r from %s", save_path, client_id)

    async def on_sync_request(self, client_id: str) -> None:
        logger.info("sync_request from %s", client_id)

    async def on_session_clear(self, client_id: str) -> None:
        logger.info("session_clear from %s", client_id)


@dataclass
class WSServerConfig:
    """WebSocket server configuration."""

    host: str = "0.0.0.0"
    port: int = 8765


_THROTTLE_INTERVAL: float = 1.0  # seconds — world_snapshot and task_list max rate


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
        self._last_world_snapshot_at: float = 0.0
        self._last_task_list_at: float = 0.0

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._app = web.Application(client_max_size=10 * 1024 * 1024)
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_post("/api/asr", self._asr_handler)
        self._app.router.add_post("/api/tts", self._tts_handler)
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
        ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
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
        elif msg_type == "game_restart":
            await self.inbound_handler.on_game_restart(message.get("save_path"), client_id)
        elif msg_type == "sync_request":
            await self.inbound_handler.on_sync_request(client_id)
        elif msg_type == "session_clear":
            await self.inbound_handler.on_session_clear(client_id)
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

    async def send_to_client(self, client_id: str, msg_type: str, data: dict[str, Any]) -> None:
        """Send a typed outbound message to a specific client."""
        await self._send_to(
            client_id,
            {
                "type": msg_type,
                "data": data,
                "timestamp": time.time(),
            },
        )

    async def send_world_snapshot(self, snapshot: dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_world_snapshot_at < _THROTTLE_INTERVAL:
            return
        self._last_world_snapshot_at = now
        await self.broadcast("world_snapshot", snapshot)

    async def send_benchmark(self, benchmark_data: list[dict[str, Any]]) -> None:
        await self.broadcast("benchmark", {"records": benchmark_data})

    async def send_session_cleared(self) -> None:
        await self.broadcast("session_cleared", {"ok": True})

    async def send_task_update(self, task_data: dict[str, Any]) -> None:
        await self.broadcast("task_update", task_data)

    async def send_task_list(
        self,
        tasks: list[dict[str, Any]],
        pending_questions: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        now = time.time()
        if now - self._last_task_list_at < _THROTTLE_INTERVAL:
            return
        self._last_task_list_at = now
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

    async def send_task_message(self, message: dict[str, Any]) -> None:
        await self.broadcast("task_message", message)

    # --- HTTP endpoints ---

    async def _asr_handler(self, request: web.Request) -> web.Response:
        """POST /api/asr — receive audio, return transcript JSON.

        Accepts multipart/form-data with field "audio" (audio bytes)
        or raw binary body. Query param "format" (default wav) and
        "sample_rate" (default 16000) are honoured.
        """
        try:
            from voice.asr import transcribe as asr_transcribe
        except ImportError as e:
            return web.json_response({"ok": False, "error": f"ASR module unavailable: {e}"}, status=503)

        try:
            audio_format = request.rel_url.query.get("format", "wav")
            sample_rate = int(request.rel_url.query.get("sample_rate", "16000"))

            content_type = request.headers.get("Content-Type", "")
            if "multipart" in content_type:
                reader = await request.multipart()
                audio_bytes = b""
                async for field in reader:
                    if field.name in ("audio", "file"):
                        audio_bytes = await field.read()
                        break
            else:
                audio_bytes = await request.read()

            if not audio_bytes:
                return web.json_response({"ok": False, "error": "No audio data received"}, status=400)

            text = await asr_transcribe(audio_bytes, audio_format=audio_format, sample_rate=sample_rate)
            return web.json_response({"ok": True, "text": text or ""})
        except Exception as e:
            logger.exception("ASR handler error")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _tts_handler(self, request: web.Request) -> web.Response:
        """POST /api/tts — receive JSON {"text", "voice"?, "format"?}, return audio bytes.

        Response Content-Type is audio/mpeg (mp3) by default.
        """
        try:
            from voice.tts import synthesize as tts_synthesize, AUDIO_MIME
        except ImportError as e:
            return web.json_response({"ok": False, "error": f"TTS module unavailable: {e}"}, status=503)

        try:
            body = await request.json()
            text = body.get("text", "")
            if not text:
                return web.json_response({"ok": False, "error": "Missing text"}, status=400)
            voice = body.get("voice", "longxiaochun")
            fmt = body.get("format", "mp3")
            audio_bytes = await tts_synthesize(text, voice=voice, fmt=fmt)
            mime = AUDIO_MIME.get(fmt, "audio/mpeg")
            return web.Response(body=audio_bytes, content_type=mime)
        except Exception as e:
            logger.exception("TTS handler error")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

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
