"""Распознавание изображений (Фаза 5): OCR + миниатюра для превью.

Публичный API:
  * `extract_image(path, use_ocr=True) -> ImageResult` — текст (OCR).
  * `image_thumbnail_base64(path, max_size=200) -> str` — миниатюра для
    превью в user-пузыре.
  * `is_image(path) -> bool` — расширение поддерживается.

Зависимости (lazy):
  * `rapidocr_onnxruntime` — OCR (в основных зависимостях).
  * `Pillow` — миниатюра (опционально, если нет — превью пропускается).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
)

MAX_OCR_CHARS = 20_000


@dataclass
class ImageResult:
    """Результат OCR изображения."""

    text: str
    kind: str = "image"
    error: str = ""


def is_image(path: Path | str) -> bool:
    """True, если расширение — изображение."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def extract_image(path: Path, *, use_ocr: bool = True) -> ImageResult:
    """Извлекает текст из изображения (OCR).

    Args:
        path: Путь к изображению.
        use_ocr: ``False`` — вернуть пустой результат (для тестов).

    Returns:
        `ImageResult`. При ошибке — `error` заполнен, `text` пуст.
    """
    if not use_ocr:
        return ImageResult(text="")
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        return ImageResult(
            text="",
            error=f"rapidocr-onnxruntime не установлен: {e}",
        )
    try:
        engine = RapidOCR()
        result, _ = engine(str(path))
    except Exception as e:
        logger.warning(f"OCR {path.name} упал: {e}")
        return ImageResult(text="", error=str(e))
    if not result:
        return ImageResult(text="")
    lines = [str(item[1]) for item in result if item and len(item) > 1]
    text = "\n".join(lines).strip()
    if len(text) > MAX_OCR_CHARS:
        text = text[:MAX_OCR_CHARS] + "\n… [OCR обрезан]"
    logger.info(f"OCR {path.name}: {len(text)} символов")
    return ImageResult(text=text)


def image_thumbnail_base64(path: Path, max_size: int = 200) -> str:
    """Возвращает base64 (PNG) миниатюры изображения.

    Используется для превью в user-пузыре. Если Pillow недоступен —
    возвращает пустую строку (превью не рендерится).

    Args:
        path: Путь к изображению.
        max_size: Максимальная сторона миниатюры (пиксели).

    Returns:
        Строка ``data:image/png;base64,…`` или пустая.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow не установлен — превью отключено")
        return ""
    try:
        img = Image.open(path)
        img.thumbnail((max_size, max_size))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.debug(f"thumbnail {path.name}: {e}")
        return ""


__all__ = [
    "IMAGE_EXTENSIONS",
    "ImageResult",
    "extract_image",
    "image_thumbnail_base64",
    "is_image",
]
