"""Транскрипция видео (Фаза 5): ffmpeg → WAV → faster-whisper.

Извлекаем только аудиодорожку (видео не нужно), конвертируем и
отдаём в `fs.audio_extractors.transcribe_audio`.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from fs.audio_extractors import transcribe_audio

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".m4v", ".flv"}
)


@dataclass
class VideoResult:
    """Результат транскрипции видео."""

    text: str
    duration_s: float = 0.0
    kind: str = "video"
    error: str = ""


def is_video(path: Path | str) -> bool:
    """True, если расширение — видео."""
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def _extract_audio(src: Path) -> Path | None:
    """Извлекает аудио из видео в 16 kHz WAV."""
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg не найден в PATH — STT видео недоступен")
        return None
    tmp = Path(tempfile.mkstemp(prefix="ollidesk_vstt_", suffix=".wav")[1])
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
            creationflags=flags,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"ffmpeg {src.name}: {e}")
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    if result.returncode != 0:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    return tmp


def extract_transcript(path: Path) -> VideoResult:
    """Транскрибирует видео (только аудиодорожку)."""
    wav = _extract_audio(path)
    if wav is None:
        return VideoResult(text="", error="ffmpeg недоступен или ошибка извлечения")
    try:
        result = transcribe_audio(wav)
    finally:
        with contextlib.suppress(OSError):
            wav.unlink()
    if result.error:
        return VideoResult(text="", error=result.error)
    logger.info(f"Video STT {path.name}: {len(result.text)} символов")
    return VideoResult(text=result.text, duration_s=result.duration_s)


__all__ = [
    "VIDEO_EXTENSIONS",
    "VideoResult",
    "extract_transcript",
    "is_video",
]
