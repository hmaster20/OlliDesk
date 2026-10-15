"""Автовыбор chat-модели SLM под профиль железа (по tier + fit)."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from agents.hardware_profile import HardwareProfile, get_hardware_profile
from agents.slm_optimizer import model_total_size_mb
from core.config import SLMModelInfo, load_config

# Резервы (синхронизированы с optimizer).
_RAM_RESERVE_MB = 3000
# Грубая оценка KV + compute на средний контекст (4K–8K).
_KV_HEADROOM_MB = 600


@dataclass(frozen=True)
class ModelFit:
    info: SLMModelInfo
    fits_vram: bool
    fits_ram: bool
    score: float
    reason: str


def _resolve_size_mb(info: SLMModelInfo) -> int:
    """Реальный размер файла (если скачан) → declared size_mb → 4000."""
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
    ram_avail = max(0, hw.ram.available_mb - _RAM_RESERVE_MB)

    size = _resolve_size_mb(info)
    need = size + _KV_HEADROOM_MB

    fits_vram = need <= vram
    fits_ram = need <= ram_avail

    # Tier — главный сигнал качества. Fit определяет, можно ли использовать.
    score = float(info.tier) * 10.0
    if fits_vram:
        score += 5.0        # полностью на GPU — быстро
    elif fits_ram:
        score += 1.0        # CPU-only — медленно, но работает
    else:
        score -= 100.0      # не влезает вообще — отбраковка

    reason = (
        "влезает целиком в VRAM" if fits_vram
        else ("влезает в RAM (CPU)" if fits_ram else "не влезает ни в VRAM, ни в RAM")
    )
    return ModelFit(info=info, fits_vram=fits_vram, fits_ram=fits_ram,
                    score=score, reason=reason)


def select_best_model(
    hw: HardwareProfile | None = None,
    *,
    prefer_vram: bool = True,
) -> SLMModelInfo | None:
    """Возвращает лучшую chat-модель под железо.

    Логика:
      1. Отбрасываем модели, которые не влезают ни в VRAM, ни в RAM.
      2. prefer_vram=True → сначала все, влезающие в VRAM, среди них максимум tier.
      3. Иначе → все, влезающие в RAM, среди них максимум tier.
      4. Финальный фоллбэк — самая маленькая модель из каталога.
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
                f"Автовыбор SLM (VRAM): {best.info.display_name} "
                f"[tier={best.info.tier}, score={best.score:.1f}, {best.reason}]"
            )
            return best.info

    ram_fits = [f for f in fits if f.fits_ram]
    if ram_fits:
        best = ram_fits[0]
        logger.info(
            f"Автовыбор SLM (RAM): {best.info.display_name} "
            f"[tier={best.info.tier}, score={best.score:.1f}, {best.reason}]"
        )
        return best.info

    smallest = min(candidates, key=lambda m: _resolve_size_mb(m))
    logger.warning(
        f"Автовыбор SLM: ничего не влезает, беру {smallest.display_name}"
    )
    return smallest