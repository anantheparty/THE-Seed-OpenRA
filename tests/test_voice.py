"""Tests for voice ASR/TTS service layer (T14).

Covers:
  - voice.asr.transcribe_sync: success path (mocked SDK), missing key error, SDK error
  - voice.tts.synthesize_sync: success path (mocked SDK), missing key error, SDK error
  - WSServer /api/asr HTTP handler: happy path, no audio, ASR failure
  - WSServer /api/tts HTTP handler: happy path, missing text, TTS failure
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import types
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===== voice.asr tests =====

def test_asr_transcribe_sync_success(monkeypatch=None):
    """transcribe_sync returns joined sentence text on success."""
    import importlib

    # Patch dashscope api_key assignment and Recognition
    fake_result = mock.MagicMock()
    fake_result.status_code = 200
    fake_result.get_sentence.return_value = [
        {"text": "你好"},
        {"text": "世界"},
    ]

    fake_recognition_instance = mock.MagicMock()
    fake_recognition_instance.call.return_value = fake_result

    with mock.patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}):
        with mock.patch("voice.asr.Recognition", return_value=fake_recognition_instance) as MockRec:
            with mock.patch("voice.asr.os.unlink"):
                with mock.patch("builtins.open", mock.mock_open()):
                    # Need a real temp file for the "exists" check inside Recognition.call
                    # We mock the whole Recognition instance so call() is intercepted
                    import tempfile
                    import voice.asr as asr_mod

                    # Write real temp file so call() arg validation passes
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        tmp = f.name
                        f.write(b"\x00" * 100)

                    try:
                        # Override tempfile.NamedTemporaryFile to return our known file
                        with mock.patch("voice.asr.tempfile.NamedTemporaryFile") as mock_ntf:
                            mock_ntf.return_value.__enter__ = lambda s: s
                            mock_ntf.return_value.__exit__ = mock.MagicMock(return_value=False)
                            mock_ntf.return_value.name = tmp
                            mock_ntf.return_value.write = mock.MagicMock()

                            text = asr_mod.transcribe_sync(b"\x00" * 100, audio_format="wav")
                            assert text == "你好 世界", f"Expected '你好 世界', got {text!r}"
                    finally:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass

    print("  PASS: asr_transcribe_sync_success")


def test_asr_transcribe_sync_no_key():
    """transcribe_sync raises RuntimeError when no API key is set."""
    import voice.asr as asr_mod

    env_backup = {}
    for k in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        env_backup[k] = os.environ.pop(k, None)

    try:
        try:
            asr_mod.transcribe_sync(b"\x00", audio_format="wav")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "API key" in str(e)
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v

    print("  PASS: asr_transcribe_sync_no_key")


def test_asr_transcribe_sync_api_error():
    """transcribe_sync raises RuntimeError when SDK returns non-200."""
    import tempfile
    import voice.asr as asr_mod

    fake_result = mock.MagicMock()
    fake_result.status_code = 400
    fake_result.message = "bad request"

    fake_rec = mock.MagicMock()
    fake_rec.call.return_value = fake_result

    with mock.patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}):
        with mock.patch("voice.asr.Recognition", return_value=fake_rec):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
                f.write(b"\x00" * 100)
            try:
                with mock.patch("voice.asr.tempfile.NamedTemporaryFile") as mock_ntf:
                    mock_ntf.return_value.__enter__ = lambda s: s
                    mock_ntf.return_value.__exit__ = mock.MagicMock(return_value=False)
                    mock_ntf.return_value.name = tmp
                    mock_ntf.return_value.write = mock.MagicMock()

                    try:
                        asr_mod.transcribe_sync(b"\x00" * 100, audio_format="wav")
                        assert False, "Expected RuntimeError"
                    except RuntimeError as e:
                        assert "400" in str(e)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    print("  PASS: asr_transcribe_sync_api_error")


# ===== voice.tts tests =====

def test_tts_synthesize_sync_success():
    """synthesize_sync returns audio bytes on success."""
    import voice.tts as tts_mod

    fake_result = mock.MagicMock()
    fake_result.get_audio_data.return_value = b"\xff\xfb\x00" * 100  # fake mp3 header

    with mock.patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}):
        with mock.patch("voice.tts.SpeechSynthesizer.call", return_value=fake_result):
            data = tts_mod.synthesize_sync("你好")
            assert isinstance(data, bytes) and len(data) > 0

    print("  PASS: tts_synthesize_sync_success")


def test_tts_synthesize_sync_no_key():
    """synthesize_sync raises RuntimeError when no API key is set."""
    import voice.tts as tts_mod

    env_backup = {}
    for k in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        env_backup[k] = os.environ.pop(k, None)

    try:
        try:
            tts_mod.synthesize_sync("hello")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "API key" in str(e)
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v

    print("  PASS: tts_synthesize_sync_no_key")


def test_tts_synthesize_sync_no_audio():
    """synthesize_sync raises RuntimeError when SDK returns None audio."""
    import voice.tts as tts_mod

    fake_result = mock.MagicMock()
    fake_result.get_audio_data.return_value = None

    with mock.patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}):
        with mock.patch("voice.tts.SpeechSynthesizer.call", return_value=fake_result):
            try:
                tts_mod.synthesize_sync("hello")
                assert False, "Expected RuntimeError"
            except RuntimeError as e:
                assert "no audio data" in str(e).lower()

    print("  PASS: tts_synthesize_sync_no_audio")


# ===== WSServer HTTP handler tests =====

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_ws_server():
    from ws_server.server import WSServer, WSServerConfig
    return WSServer(config=WSServerConfig(host="127.0.0.1", port=0, voice_enabled=True))


class _FakeRouter:
    def add_get(self, *args, **kwargs) -> None:
        del args, kwargs

    def add_post(self, *args, **kwargs) -> None:
        del args, kwargs


class _FakeApplication:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.router = _FakeRouter()


class _FakeAppRunner:
    def __init__(self, app) -> None:
        self.app = app

    async def setup(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None


class _FakeTCPSite:
    def __init__(self, runner, host, port) -> None:
        del runner, host, port

    async def start(self) -> None:
        return None


def test_ws_voice_disabled_rejects_requests():
    from ws_server.server import WSServer, WSServerConfig

    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=0, voice_enabled=False))

    async def run():
        asr_resp = await server._asr_handler(FakeRequest(body=b"\x00" * 16))
        tts_resp = await server._tts_handler(FakeRequest(json_body={"text": "hello"}))
        assert asr_resp.status == 503
        assert tts_resp.status == 503
        assert "disabled" in json.loads(asr_resp.body)["error"].lower()
        assert "disabled" in json.loads(tts_resp.body)["error"].lower()

    _run(run())
    print("  PASS: ws_voice_disabled_rejects_requests")


def test_ws_start_skips_voice_probe_by_default():
    from ws_server.server import WSServer, WSServerConfig
    import ws_server.server as server_mod

    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=0, voice_enabled=False))
    probe = mock.MagicMock()

    async def run():
        with mock.patch.object(server_mod.web, "Application", _FakeApplication):
            with mock.patch.object(server_mod.web, "AppRunner", _FakeAppRunner):
                with mock.patch.object(server_mod.web, "TCPSite", _FakeTCPSite):
                    with mock.patch.object(server, "_check_voice_availability", probe):
                        await server.start()
                        await server.stop()

    _run(run())
    assert probe.call_count == 0
    print("  PASS: ws_start_skips_voice_probe_by_default")


def test_ws_start_invokes_voice_probe_when_enabled():
    from ws_server.server import WSServer, WSServerConfig
    import ws_server.server as server_mod

    server = WSServer(config=WSServerConfig(host="127.0.0.1", port=0, voice_enabled=True))
    probe = mock.MagicMock()

    async def run():
        with mock.patch.object(server_mod.web, "Application", _FakeApplication):
            with mock.patch.object(server_mod.web, "AppRunner", _FakeAppRunner):
                with mock.patch.object(server_mod.web, "TCPSite", _FakeTCPSite):
                    with mock.patch.object(server, "_check_voice_availability", probe):
                        await server.start()
                        await server.stop()

    _run(run())
    assert probe.call_count == 1
    print("  PASS: ws_start_invokes_voice_probe_when_enabled")


class FakeRequest:
    """Minimal mock of aiohttp Request for handler unit tests."""

    def __init__(self, body: bytes = b"", json_body=None, query: dict = None, content_type: str = "application/octet-stream"):
        self._body = body
        self._json = json_body
        self.headers = {"Content-Type": content_type}
        self.rel_url = mock.MagicMock()
        self.rel_url.query = query or {}

    async def read(self):
        return self._body

    async def json(self):
        return self._json or {}

    async def multipart(self):
        raise AssertionError("multipart not expected in this test")


def test_ws_asr_handler_success():
    """_asr_handler returns JSON with ok=True and transcript text."""
    server = _make_ws_server()

    async def _fake_transcribe(audio_bytes, *, audio_format="wav", sample_rate=16000):
        return "开始游戏"

    async def run():
        with mock.patch("voice.asr.transcribe", _fake_transcribe):
            req = FakeRequest(body=b"\x00" * 100, query={"format": "wav", "sample_rate": "16000"})
            resp = await server._asr_handler(req)
            body = json.loads(resp.body)
            assert body["ok"] is True
            assert body["text"] == "开始游戏"

    _run(run())
    print("  PASS: ws_asr_handler_success")


def test_ws_asr_handler_no_audio():
    """_asr_handler returns 400 when body is empty."""
    server = _make_ws_server()

    async def run():
        req = FakeRequest(body=b"", query={})
        resp = await server._asr_handler(req)
        assert resp.status == 400

    _run(run())
    print("  PASS: ws_asr_handler_no_audio")


def test_ws_asr_handler_asr_error():
    """_asr_handler returns 500 when transcribe raises."""
    server = _make_ws_server()

    async def _fail(audio_bytes, *, audio_format="wav", sample_rate=16000):
        raise RuntimeError("DashScope down")

    async def run():
        with mock.patch("voice.asr.transcribe", _fail):
            req = FakeRequest(body=b"\x00" * 100, query={})
            resp = await server._asr_handler(req)
            assert resp.status == 500

    _run(run())
    print("  PASS: ws_asr_handler_asr_error")


def test_ws_tts_handler_success():
    """_tts_handler returns audio bytes with audio/mpeg content type."""
    server = _make_ws_server()

    async def _fake_synth(text, *, voice="longxiaochun", fmt="mp3", sample_rate=22050):
        return b"\xff\xfb" * 50

    async def run():
        with mock.patch("voice.tts.synthesize", _fake_synth):
            req = FakeRequest(json_body={"text": "你好", "format": "mp3"})
            resp = await server._tts_handler(req)
            assert resp.status == 200
            assert "audio" in resp.content_type

    _run(run())
    print("  PASS: ws_tts_handler_success")


def test_ws_tts_handler_missing_text():
    """_tts_handler returns 400 when text is empty."""
    server = _make_ws_server()

    async def run():
        req = FakeRequest(json_body={"text": ""})
        resp = await server._tts_handler(req)
        assert resp.status == 400

    _run(run())
    print("  PASS: ws_tts_handler_missing_text")


def test_ws_tts_handler_tts_error():
    """_tts_handler returns 500 when synthesize raises."""
    server = _make_ws_server()

    async def _fail(text, *, voice="longxiaochun", fmt="mp3", sample_rate=22050):
        raise RuntimeError("TTS quota exceeded")

    async def run():
        with mock.patch("voice.tts.synthesize", _fail):
            req = FakeRequest(json_body={"text": "hello"})
            resp = await server._tts_handler(req)
            assert resp.status == 500

    _run(run())
    print("  PASS: ws_tts_handler_tts_error")


# --- Run all ---

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
