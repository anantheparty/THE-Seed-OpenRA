"""DashScope ASR (paraformer) — isolated API client.

Usage:
    from voice.asr import transcribe
    text = await transcribe(audio_bytes, audio_format="wav", sample_rate=16000)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback

_ASR_MODEL = "paraformer-realtime-v2"
_TRANSCODE_TO_WAV_FORMATS = {"webm", "ogg", "opus"}


def _api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY", "")


def _normalize_audio_input(
    audio_bytes: bytes,
    *,
    audio_format: str,
    sample_rate: int,
) -> tuple[bytes, str, int]:
    normalized_format = str(audio_format or "wav").strip().lower()
    if normalized_format not in _TRANSCODE_TO_WAV_FORMATS:
        return audio_bytes, normalized_format or "wav", sample_rate

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError(
            f"ASR input format '{normalized_format}' requires ffmpeg transcoding, but ffmpeg is not installed"
        )

    with tempfile.NamedTemporaryFile(suffix=f".{normalized_format}", delete=False) as src:
        src_path = src.name
        src.write(audio_bytes)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
        dst_path = dst.name

    try:
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                src_path,
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "wav",
                dst_path,
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RuntimeError(
                f"Failed to transcode ASR input '{normalized_format}' to wav via ffmpeg"
                + (f": {stderr}" if stderr else "")
            )
        with open(dst_path, "rb") as wav_file:
            return wav_file.read(), "wav", sample_rate
    finally:
        for path in (src_path, dst_path):
            try:
                os.unlink(path)
            except OSError:
                pass


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

    audio_bytes, audio_format, sample_rate = _normalize_audio_input(
        audio_bytes,
        audio_format=audio_format,
        sample_rate=sample_rate,
    )

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
