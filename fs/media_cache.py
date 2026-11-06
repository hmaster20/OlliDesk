"""Кэш извлечённого текста из медиафайлов (Фаза 5).

Хэш содержимого файла → извлечённый текст. Повторное прикрепление
того же файла (например, пользователь дважды перетащил PDF) не
пересчитывает OCR/STT. Кэш на диске (`~/.ollidesk/media_cache/`) —
переживает перезапуск.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from loguru import logger

from core.utils import get_app_data_dir


def _cache_dir() -> Path:
    d = get_app_data_dir() / "media_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_sha256(path: Path) -> str:
    """SHA-256 файла (для ключа кэша)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_cached(path: Path, kind: str) -> str | None:
    """Возвращает закэшированный текст или ``None``.

    Args:
        path: Медиафайл.
        kind: ``"image"`` | ``"audio"`` | ``"video"``.
    """
    try:
        sha = file_sha256(path)
    except OSError:
        return None
    entry = _cache_dir() / f"{kind}_{sha}.json"
    if not entry.exists():
        return None
    try:
        data = json.loads(entry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    text = data.get("text", "")
    if not isinstance(text, str):
        return None
    logger.debug(f"media_cache: hit {path.name} ({kind})")
    return text


def set_cached(path: Path, kind: str, text: str) -> None:
    """Сохраняет извлечённый текст в кэш."""
    try:
        sha = file_sha256(path)
    except OSError:
        return
    entry = _cache_dir() / f"{kind}_{sha}.json"
    try:
        entry.write_text(
            json.dumps(
                {"text": text, "source": str(path)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug(f"media_cache: не удалось сохранить {path.name}: {e}")


__all__ = ["file_sha256", "get_cached", "set_cached"]
