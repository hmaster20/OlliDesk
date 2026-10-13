"""Определение свободной VRAM и выбор количества GPU-слоёв для llama.cpp.

Работает на системах с NVIDIA-картами через nvidia-smi. На системах без
NVIDIA/драйвера возвращает None, и вызывающий код откатывается к CPU-only.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

# Резерв под KV-кэш, compute-буферы и фрагментацию драйвера: без него
# 4-гиговая карта уходит в OOM при частичном offload.
VRAM_RESERVE_MB = 700

# Типичная LLM имеет 24–40 слоёв; 32 — безопасная оценка для расчёта offload.
APPROX_LAYERS = 32

# Имя split-части GGUF: <stem>-00001-of-00003.gguf
_SPLIT_RE = re.compile(r"^(.+)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


def _run(cmd: list[str], timeout: float = 4.0) -> str | None:
    """Запускает команду и возвращает stdout или None при ошибке."""
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
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
    """Свободная VRAM первой NVIDIA-карты в МБ, или None."""
    if shutil.which("nvidia-smi") is None:
        return None

    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ]
    )
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
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return None
    try:
        return int(out.strip().splitlines()[0].strip())
    except ValueError:
        return None


def model_total_size_mb(model_path: Path) -> int:
    """Полный размер модели в МБ: для split — сумма всех частей.

    Для разбитой модели llama.cpp передаётся путь к первой части
    (<stem>-00001-of-00003.gguf), и ``stat().st_size`` вернул бы размер
    только этой части — в 2-3 раза меньше реального. Из-за этого
    ``detect_optimal_gpu_layers`` завышал число слоёв под GPU, и модель
    уходила в OOM/своп. Здесь считаем сумму всех частей по имени первой.

    Args:
        model_path: Путь к GGUF-файлу (для split — к первой части).

    Returns:
        Полный размер в МБ или 0, если файл недоступен.
    """
    match = _SPLIT_RE.match(model_path.name)
    if match:
        stem, _, total_str = match.groups()
        total_parts = int(total_str)
        total_bytes = 0
        found = 0
        for i in range(1, total_parts + 1):
            part_name = f"{stem}-{i:05d}-of-{total_parts:05d}.gguf"
            part = model_path.parent / part_name
            if part.exists():
                try:
                    total_bytes += part.stat().st_size
                    found += 1
                except OSError:
                    continue
        if total_bytes > 0:
            if found != total_parts:
                logger.warning(
                    f"Split-модель {model_path.name}: найдено {found} из {total_parts} частей, "
                    f"использую оценку по найденным ({total_bytes // (1024 * 1024)} МБ)"
                )
            return total_bytes // (1024 * 1024)

    try:
        return model_path.stat().st_size // (1024 * 1024)
    except OSError as e:
        logger.warning(f"Не удалось определить размер модели {model_path}: {e}")
        return 0


def detect_optimal_threads() -> int:
    """Определяет число потоков для CPU-инференса llama.cpp.

    Берём число **физических** ядер: на гиперпоточном CPU два логических
    потока на ядро почти не добавляют скорости генерации, зато делят её между
    собой. Если psutil недоступен или не знает про ядра — падаем назад на
    ``os.cpu_count()`` и минимум 2 потока.

    Returns:
        Число потоков для ``Llama(n_threads=...)``, минимум 2.
    """
    physical: int | None = None
    try:
        import psutil

        physical = psutil.cpu_count(logical=False)
    except Exception as e:  # psutil опционален
        logger.debug(f"psutil недоступен для подсчёта ядер: {e}")

    if not physical:
        physical = os.cpu_count()

    threads = max(2, physical or 2)
    logger.info(f"CPU: физических ядер={physical}, n_threads={threads}")
    return threads


def detect_optimal_gpu_layers(
    model_path: Path,
    preferred: int = 0,
    auto: bool = True,
) -> int:
    """Решает, сколько слоёв модели положить на GPU.

    Args:
        model_path: путь к GGUF-файлу (для split — к первой части)
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

    model_mb = model_total_size_mb(model_path)
    if model_mb <= 0:
        logger.warning("Не удалось определить размер модели → n_gpu_layers=0")
        return 0

    # Резерв под KV-кэш, compute-буферы, фрагментацию драйвера.
    # Без него 4-гиговая карта уйдёт в OOM при offload.
    available_mb = max(0, free_mb - VRAM_RESERVE_MB)

    logger.info(
        f"GPU: free={free_mb} МБ, модель={model_mb} МБ (полный размер), "
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
    ratio = available_mb / model_mb
    layers = max(0, min(APPROX_LAYERS, int(APPROX_LAYERS * ratio)))
    logger.info(f"Частичный offload: {layers} из ~{APPROX_LAYERS} слоёв (ratio={ratio:.2f})")
    return layers