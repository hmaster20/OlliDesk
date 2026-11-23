"""Распознавание речи (Фаза 5): STT через faster-whisper (CPU).

Принципы:
  * ffmpeg конвертирует любой формат (mp3/m4a/ogg/flac) в 16 kHz WAV,
    потому что faster-whisper ожидает именно этот формат.
  * Модель `base` / `small` / `medium` — выбирается по доступной RAM.
    По умолчанию `base` (~140 MB) — быстро на CPU.
  * Тяжёлая зависимость импортируется лениво внутри функции.
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

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".wma"}
)

DEFAULT_WHISPER_MODEL = "base"
MAX_TRANSCRIPT_CHARS = 30_000


@dataclass
class AudioResult:
    """Результат транскрипции."""

    text: str
    duration_s: float = 0.0
    kind: str = "audio"
    error: str = ""


def is_audio(path: Path | str) -> bool:
    """True, если расширение — аудио."""
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _convert_to_wav(src: Path) -> Path | None:
    """Конвертирует любой аудиофайл в 16 kHz WAV через ffmpeg.

    Returns:
        Путь к временному WAV или ``None`` (ffmpeg недоступен / ошибка).
    """
    if not _have_ffmpeg():
        logger.warning("ffmpeg не найден в PATH — STT недоступен")
        return None
    tmp = Path(tempfile.mkstemp(prefix="ollidesk_stt_", suffix=".wav")[1])
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
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
            timeout=120,
            creationflags=flags,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"ffmpeg {src.name}: {e}")
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    if result.returncode != 0:
        logger.warning(f"ffmpeg {src.name} вернул {result.returncode}: {(result.stderr or '')[:200]}")
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    return tmp


def transcribe_audio(
    path: Path,
    *,
    model_name: str = DEFAULT_WHISPER_MODEL,
) -> AudioResult:
    """Транскрибирует аудиофайл через faster-whisper (CPU).

    Args:
        path: Путь к аудио.
        model_name: Размер модели (`tiny`/`base`/`small`/`medium`).

    Returns:
        `AudioResult`. При ошибке — `error`, пустой `text`.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        return AudioResult(
            text="",
            error=f"faster-whisper не установлен: {e}",
        )

    wav = _convert_to_wav(path)
    if wav is None:
        return AudioResult(text="", error="ffmpeg недоступен или ошибка конвертации")

    try:
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segments, info = model.transcribe(str(wav), language=None)
            parts: list[str] = []
            for seg in segments:
                parts.append(seg.text.strip())
            text = " ".join(p for p in parts if p).strip()
            duration = float(getattr(info, "duration", 0.0) or 0.0)
        except Exception as e:
            logger.exception(f"whisper {path.name} упал")
            return AudioResult(text="", error=str(e))
    finally:
        with contextlib.suppress(OSError):
            wav.unlink()

    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n… [транскрипт обрезан]"
    logger.info(f"STT {path.name}: {len(text)} символов, {duration:.1f}с (модель {model_name})")
    return AudioResult(text=text, duration_s=duration)


__all__ = [
    "AUDIO_EXTENSIONS",
    "AudioResult",
    "DEFAULT_WHISPER_MODEL",
    "is_audio",
    "transcribe_audio",
]
