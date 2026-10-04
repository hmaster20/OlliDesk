"""Определение свободной VRAM и выбор количества GPU-слоёв для llama.cpp.

Работает на системах с NVIDIA-картами через nvidia-smi. На системах без
NVIDIA/драйвера возвращает None, и вызывающий код откатывается к CPU-only.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger


def _run(cmd: list[str], timeout: float = 4.0) -> str | None:
    """Запускает команду и возвращает stdout или None при ошибке."""
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def get_free_vram_mb() -> int | None:
    """Свободная VRAM первой NVIDIA-карты в МБ, или None.

    Использует nvidia-smi. Не падает, если GPU/драйвера нет — просто None.
    """
    if shutil.which("nvidia-smi") is None:
        return None

    out = _run([
        "nvidia-smi",
        "--query-gpu=memory.free",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None

    first = out.strip().splitlines()[0].strip()
    try:
        return int(first)
    except ValueError:
        logger.debug(f"nvidia-smi вернул неожиданное значение: {first!r}")
        return None


def get_total_vram_mb() -> int | None:
    """Общая VRAM первой NVIDIA-карты в МБ, или None."""
    if shutil.which("nvidia-smi") is None:
        return None
    out = _run([
        "nvidia-smi",
        "--query-gpu=memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    try:
        return int(out.strip().splitlines()[0].strip())
    except ValueError:
        return None


def detect_optimal_gpu_layers(
    model_path: Path,
    preferred: int = 0,
    auto: bool = True,
) -> int:
    """Решает, сколько слоёв модели положить на GPU.

    Args:
        model_path: путь к GGUF-файлу (для оценки размера)
        preferred: значение из конфига (используется при auto=False)
        auto: True — считаем сами; False — отдаём preferred как есть

    Returns:
        Число слоёв: -1 = все слои, 0 = CPU-only, N>0 = первые N слоёв.
    """
    if not auto:
        logger.debug(f"auto_gpu_layers=False → n_gpu_layers={preferred}")
        return preferred

    free_mb = get_free_vram_mb()
    if free_mb is None:
        logger.info("GPU не обнаружен (nvidia-smi недоступен) → n_gpu_layers=0")
        return 0

    try:
        model_mb = model_path.stat().st_size // (1024 * 1024)
    except OSError as e:
        logger.warning(f"Не удалось определить размер модели: {e}")
        return 0

    # Резерв под KV-кэш, compute-буферы, фрагментацию драйвера.
    # Без него 4-гиговая карта уйдёт в OOM при offload.
    RESERVE_MB = 700
    available_mb = max(0, free_mb - RESERVE_MB)

    logger.info(
        f"GPU: free={free_mb} МБ, модель={model_mb} МБ, "
        f"доступно после резерва={available_mb} МБ"
    )

    if available_mb <= 0:
        logger.info("Недостаточно свободной VRAM → n_gpu_layers=0")
        return 0

    # Если модель влезает целиком с запасом 10% — грузим всё
    if model_mb * 1.1 <= available_mb:
        logger.info("Модель влезает в VRAM целиком → n_gpu_layers=-1")
        return -1

    # Иначе — пропорциональный offload.
    # Типичная LLM имеет 24–40 слоёв; берём 32 как безопасную оценку.
    APPROX_LAYERS = 32
    ratio = available_mb / model_mb
    layers = max(0, min(APPROX_LAYERS, int(APPROX_LAYERS * ratio)))
    logger.info(
        f"Частичный offload: {layers} из ~{APPROX_LAYERS} слоёв "
        f"(ratio={ratio:.2f})"
    )
    return layers