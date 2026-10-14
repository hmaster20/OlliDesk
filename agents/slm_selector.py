"""Автовыбор chat-модели SLM под профиль железа."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from agents.gguf_reader import read_gguf_meta
from agents.hardware_profile import HardwareProfile, get_hardware_profile
from agents.slm_optimizer import model_total_size_mb
from core.config import SLMModelInfo, load_config


# Резерв VRAM (такой же, как в optimizer.usable_vram_mb).
_VRAM_RESERVE_SHARE = 0.20
# Грубая оценка KV-кэша и compute-буфера при выборе модели.
_KV_HEADROOM_MB = 450
_COMPUTE_HEADROOM_MB = 400


@dataclass(frozen=True)
class ModelFit:
    info: SLMModelInfo
    fits_vram: bool
    fits_ram: bool
    score: float
    reason: str


def _resolve_size_mb(info: SLMModelInfo) -> int:
    """Размер модели: реальный файл (если скачан) → declared size_mb → 4000."""
    try:
        from agents.download_slm import get_model_path, is_model_installed
        if is_model_installed(info.file, repo_id=info.repo):
            path = get_model_path(info.file, repo_id=info.repo)
            if path is not None:
                real = model_total_size_mb(path)
                if real > 0:
                    return real
    except Exception:
        pass
    return info.size_mb or 4000


def _score_model(info: SLMModelInfo, hw: HardwareProfile) -> ModelFit:
    gpu = hw.best_gpu
    vram = gpu.usable_vram_mb if gpu else 0
    ram_avail = max(0, hw.ram.available_mb - 2000)

    size = _resolve_size_mb(info)
    need_vram = size + _KV_HEADROOM_MB + _COMPUTE_HEADROOM_MB
    fits_vram = need_vram <= vram
    fits_ram = size + _KV_HEADROOM_MB <= ram_avail

    # Чем крупнее — тем качественнее, но с жёсткими штрафами за невместимость.
    base = size / 1000.0
    if not fits_vram:
        base -= 10.0     # hard penalty — частичный offload сильно медленнее
    if not fits_ram:
        base -= 20.0

    reason = (
        "влезает целиком в VRAM" if fits_vram
        else ("влезает в RAM (CPU)" if fits_ram
              else "не влезает ни в VRAM, ни в RAM")
    )
    return ModelFit(info=info, fits_vram=fits_vram, fits_ram=fits_ram,
                    score=base, reason=reason)


def select_best_model(
    hw: HardwareProfile | None = None,
    *,
    prefer_vram: bool = True,
) -> SLMModelInfo | None:
    """Возвращает лучшую chat-модель под железо.

    prefer_vram=True → сначала все, влезающие в VRAM, среди них — максимум score.
    Иначе → все, влезающие в RAM.
    """
    hw = hw or get_hardware_profile()
    cfg = load_config()
    candidates = cfg.slm.get_chat_models()
    if not candidates:
        logger.warning("Автовыбор SLM: нет chat-моделей в конфиге")
        return None

    fits = [_score_model(m, hw) for m in candidates]
    fits.sort(key=lambda f: f.score, reverse=True)

    if prefer_vram:
        vram_fits = [f for f in fits if f.fits_vram]
        if vram_fits:
            best = vram_fits[0]
            logger.info(
                f"Автовыбор SLM: {best.info.display_name} "
                f"[{best.reason}, score={best.score:.2f}]"
            )
            return best.info

    ram_fits = [f for f in fits if f.fits_ram]
    if ram_fits:
        best = ram_fits[0]
        logger.info(
            f"Автовыбор SLM: {best.info.display_name} "
            f"[{best.reason}, score={best.score:.2f}]"
        )
        return best.info

    smallest = min(candidates, key=lambda m: _resolve_size_mb(m))
    logger.warning(
        f"Автовыбор SLM: ничего не влезает, беру {smallest.display_name}"
    )
    return smallest