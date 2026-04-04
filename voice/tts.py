"""DashScope TTS (CosyVoice / sambert) — isolated API client.

Usage:
    from voice.tts import synthesize
    audio_bytes = await synthesize("你好世界", voice="longxiaochun", fmt="mp3")
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import dashscope
from dashscope.audio.tts import SpeechSynthesizer

_TTS_MODEL = "cosyvoice-v1"
_DEFAULT_VOICE = "longxiaochun"  # standard Mandarin female voice


def _api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY", "")


def synthesize_sync(
    text: str,
    *,
    voice: str = _DEFAULT_VOICE,
    fmt: str = "mp3",
    sample_rate: int = 22050,
) -> bytes:
    """Convert text to speech synchronously.

    Returns audio bytes.
    Raises RuntimeError on API error.
    """
    key = _api_key()
    if not key:
        raise RuntimeError("No DashScope API key (set DASHSCOPE_API_KEY or QWEN_API_KEY)")

    dashscope.api_key = key

    result = SpeechSynthesizer.call(
        model=_TTS_MODEL,
        text=text,
        voice=voice,
        format=fmt,
        sample_rate=sample_rate,
    )
    if result.get_audio_data() is None:
        raise RuntimeError(f"TTS returned no audio data (response: {getattr(result, '_response', None)})")
    return result.get_audio_data()


async def synthesize(
    text: str,
    *,
    voice: str = _DEFAULT_VOICE,
    fmt: str = "mp3",
    sample_rate: int = 22050,
) -> bytes:
    """Async wrapper for synthesize_sync — runs in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: synthesize_sync(text, voice=voice, fmt=fmt, sample_rate=sample_rate),
    )


# MIME types for supported formats
AUDIO_MIME: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "ogg": "audio/ogg",
}
