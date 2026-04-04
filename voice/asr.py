"""DashScope ASR (paraformer) — isolated API client.

Usage:
    from voice.asr import transcribe
    text = await transcribe(audio_bytes, audio_format="wav", sample_rate=16000)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback

_ASR_MODEL = "paraformer-realtime-v2"


def _api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY", "")


def transcribe_sync(
    audio_bytes: bytes,
    *,
    audio_format: str = "wav",
    sample_rate: int = 16000,
) -> Optional[str]:
    """Transcribe audio bytes to text synchronously.

    Returns the transcript string, or None if result is empty.
    Raises RuntimeError on API error.
    """
    key = _api_key()
    if not key:
        raise RuntimeError("No DashScope API key (set DASHSCOPE_API_KEY or QWEN_API_KEY)")

    dashscope.api_key = key

    suffix = f".{audio_format}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp_path = f.name
        f.write(audio_bytes)

    try:
        rec = Recognition(
            model=_ASR_MODEL,
            format=audio_format,
            sample_rate=sample_rate,
            callback=RecognitionCallback(),
        )
        result = rec.call(tmp_path)
        if result.status_code != 200:
            raise RuntimeError(f"ASR HTTP {result.status_code}: {result.message}")

        sentences = result.get_sentence()
        if not sentences:
            return ""
        if isinstance(sentences, list):
            return " ".join(s.get("text", "") for s in sentences if s.get("text"))
        if isinstance(sentences, dict):
            return sentences.get("text", "")
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def transcribe(
    audio_bytes: bytes,
    *,
    audio_format: str = "wav",
    sample_rate: int = 16000,
) -> Optional[str]:
    """Async wrapper for transcribe_sync — runs in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: transcribe_sync(audio_bytes, audio_format=audio_format, sample_rate=sample_rate),
    )
