"""Автоподбор параметров llama.cpp под железо и конкретную модель.

Заменяет naive-эвристику из gpu_utils.detect_optimal_gpu_layers.
Учитывает: точный размер весов, KV-кэш, compute-буферы, GQA.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

from agents.gguf_reader import GGUFMeta, read_gguf_meta
from agents.hardware_profile import HardwareProfile, get_hardware_profile

# Резерв VRAM на compute-буферы llama.cpp (граф + активации).
COMPUTE_BUFFER_MB = 350
# Резерв RAM на нужды ОС и приложения (не считая модель).
RAM_RESERVE_MB = 2000
MIN_N_CTX = 512


@dataclass(frozen=True)
class OptimizedParams:
    n_ctx: int
    n_gpu_layers: int
    n_threads: int
    n_batch: int
    flash_attn: bool
    use_mmap: bool
    use_mlock: bool
    kv_cache_type: str      # "f16" | "q8_0"
    vram_used_mb: int
    ram_used_mb: int
    notes: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def model_total_size_mb(path: Path) -> int:
    """Размер модели: для split — сумма всех частей."""
    import re
    path = Path(path)
    m = re.match(r"^(.+)-(\d{5})-of-(\d{5})\.gguf$", path.name, re.IGNORECASE)
    if m:
        stem, _, total_str = m.groups()
        total = int(total_str)
        s = 0
        for i in range(1, total + 1):
            p = path.parent / f"{stem}-{i:05d}-of-{total:05d}.gguf"
            if p.exists():
                s += p.stat().st_size
        if s > 0:
            return s // (1024 * 1024)
    try:
        return path.stat().st_size // (1024 * 1024)
    except OSError:
        return 0


def _layer_sizes_mb(model_size_mb: int, meta: GGUFMeta) -> tuple[float, float]:
    """(размер одного слоя, embed+output) — эмпирика: embed ≈ 12% от модели."""
    embed_mb = model_size_mb * 0.12
    per_layer = (model_size_mb - embed_mb) / max(meta.n_layer, 1)
    return per_layer, embed_mb


def _optimal_n_ctx(
    meta: GGUFMeta, vram_budget_mb: int, ram_budget_mb: int, target: int,
) -> tuple[int, str]:
    """Подбирает n_ctx и тип KV-кэша."""
    caps = [target]
    if meta.n_ctx_train > 0:
        caps.append(meta.n_ctx_train)
    upper = min(caps)

    for kv_type, per_tok in (
        ("f16", meta.kv_bytes_per_token_fp16),
        ("q8_0", meta.kv_bytes_per_token_q8),
    ):
        for budget_mb in (vram_budget_mb, ram_budget_mb):
            if budget_mb <= 0 or per_tok <= 0:
                continue
            max_ctx = (budget_mb * 1024 * 1024) // per_tok
            if max_ctx < MIN_N_CTX:
                continue
            n = min(upper, max_ctx)
            n = max(MIN_N_CTX, (n // 512) * 512)
            return n, kv_type
    return MIN_N_CTX, "q8_0"


def _optimal_gpu_layers(
    model_size_mb: int, meta: GGUFMeta, usable_vram_mb: int, kv_mb: int,
) -> tuple[int, int]:
    """Сколько слоёв положить на GPU. Возвращает (n_gpu_layers, used_mb)."""
    if usable_vram_mb <= 0:
        return 0, 0

    budget = usable_vram_mb - COMPUTE_BUFFER_MB - kv_mb
    if budget <= 0:
        return 0, 0

    per_layer_mb, embed_mb = _layer_sizes_mb(model_size_mb, meta)

    # Всё на GPU?
    if model_size_mb + COMPUTE_BUFFER_MB + kv_mb <= usable_vram_mb:
        return -1, int(model_size_mb + kv_mb + COMPUTE_BUFFER_MB)

    if embed_mb > budget:
        return 0, 0

    free_after_embed = budget - embed_mb
    layers = int(free_after_embed // per_layer_mb)
    layers = max(0, min(layers, meta.n_layer))

    # Оставляем CPU-слоям хотя бы 2 слоя на CPU (иначе GPU перегружен и всё равно медленно).
    if 0 < layers < meta.n_layer and (meta.n_layer - layers) < 2:
        layers = max(0, meta.n_layer - 2)

    used = int(embed_mb + layers * per_layer_mb + kv_mb + COMPUTE_BUFFER_MB)
    return layers, used


def _optimal_batch(usable_vram_mb: int, n_gpu_layers: int) -> int:
    if n_gpu_layers == 0:
        return 512
    if usable_vram_mb >= 8000:
        return 2048
    if usable_vram_mb >= 4000:
        return 1024
    return 512


def _optimal_flash_attn(hw: HardwareProfile, n_gpu_layers: int) -> bool:
    if n_gpu_layers == 0:
        return False
    gpu = hw.best_gpu
    if gpu is None:
        return False
    try:
        major = int(gpu.compute_capability.split(".")[0])
    except (ValueError, AttributeError):
        return False
    return major >= 7  # Turing+


def optimize(
    model_path: Path,
    *,
    hw: HardwareProfile | None = None,
    target_n_ctx: int = 8192,
    model_size_mb: int | None = None,
) -> OptimizedParams:
    """Главная точка входа. Считает оптимальные параметры для модели на железе."""
    hw = hw or get_hardware_profile()
    meta = read_gguf_meta(model_path)

    if model_size_mb is None:
        model_size_mb = model_total_size_mb(model_path)
    if model_size_mb <= 0:
        model_size_mb = 4000  # fallback

    notes: list[str] = []
    gpu = hw.best_gpu
    usable_vram = gpu.usable_vram_mb if gpu else 0

    # Бюджеты KV.
    ram_kv_budget = max(0, (hw.ram.available_mb - RAM_RESERVE_MB) // 3)
    vram_kv_budget = max(0, usable_vram - COMPUTE_BUFFER_MB - model_size_mb) // 2

    n_ctx, kv_type = _optimal_n_ctx(meta, vram_kv_budget, ram_kv_budget, target_n_ctx)
    if n_ctx < target_n_ctx:
        notes.append(f"n_ctx урезан до {n_ctx} (память под KV ограничена)")

    per_tok = (meta.kv_bytes_per_token_fp16 if kv_type == "f16"
               else meta.kv_bytes_per_token_q8)
    kv_mb = (n_ctx * per_tok) // (1024 * 1024)

    n_gpu_layers, vram_used = _optimal_gpu_layers(model_size_mb, meta, usable_vram, kv_mb)
    if n_gpu_layers == -1:
        notes.append(f"Модель целиком на GPU (VRAM ≈ {vram_used} МБ)")
    elif n_gpu_layers == 0:
        notes.append("GPU не задействован — модель не влезает в VRAM")
    else:
        notes.append(f"Частичный offload: {n_gpu_layers}/{meta.n_layer} слоёв на GPU")

    n_threads = hw.cpu.threads_for_llm
    n_batch = _optimal_batch(usable_vram, n_gpu_layers)
    flash = _optimal_flash_attn(hw, n_gpu_layers)
    if flash:
        notes.append("FlashAttention включён")

    use_mmap = True
    use_mlock = (
        n_gpu_layers == 0
        and hw.ram.available_mb > (model_size_mb + RAM_RESERVE_MB + 4000)
    )
    if use_mlock:
        notes.append("mlock включён (модель закреплена в RAM)")

    if n_gpu_layers == -1:
        ram_used = 200  # только процесс приложения
    else:
        frac_cpu = 1 - max(0, n_gpu_layers) / max(1, meta.n_layer)
        ram_used = int(model_size_mb * frac_cpu)

    params = OptimizedParams(
        n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, n_threads=n_threads,
        n_batch=n_batch, flash_attn=flash, use_mmap=use_mmap, use_mlock=use_mlock,
        kv_cache_type=kv_type, vram_used_mb=int(vram_used),
        ram_used_mb=ram_used, notes=tuple(notes),
    )
    logger.info(
        f"SLM оптимизация {model_path.name}: n_ctx={n_ctx}, "
        f"n_gpu_layers={n_gpu_layers}, n_threads={n_threads}, "
        f"n_batch={n_batch}, flash={flash}, kv={kv_type}, "
        f"VRAM≈{vram_used} МБ, RAM≈{ram_used} МБ"
    )
    for note in notes:
        logger.info(f"  • {note}")
    return params