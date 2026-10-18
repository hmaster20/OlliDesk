"""Автоподбор параметров llama.cpp под железо и конкретную модель.

Двухуровневая стратегия:

  1. VRAM-first: пробуем уместить модель ЦЕЛИКОМ в GPU. Если получается
     даже с n_ctx ≥ MIN_USEFUL_CTX — берём этот вариант (n_gpu_layers=-1),
     это самый быстрый режим.
  2. Fallback: модель не влезает в VRAM целиком. Тогда используем
     VRAM+RAM, максимизируем n_ctx и делаем частичный offload.

Тип KV-кэша подбирается автоматически: сначала f16 (качество), при
нехватке — q8_0 (~2× компактнее, качество почти без потерь).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

from agents.gguf_reader import GGUFMeta, read_gguf_meta
from agents.hardware_profile import HardwareProfile, get_hardware_profile

# Резерв VRAM на compute-буферы llama.cpp (граф + активации).
COMPUTE_BUFFER_MB = 350
# Резерв RAM на ОС и приложение (не считая модель).
RAM_RESERVE_MB = 3000
# Минимальный n_ctx, при котором агент ещё осмысленно работает с инструментами.
MIN_USEFUL_CTX = 2048
# Абсолютный минимум (технический).
MIN_N_CTX = 512
# Порог «хорошего» контекста: ниже него f16 не даёт осмысленного выигрыша.
GOOD_CTX = 4096


@dataclass(frozen=True)
class OptimizedParams:
    n_ctx: int
    n_gpu_layers: int
    n_threads: int
    n_batch: int
    flash_attn: bool
    use_mmap: bool
    use_mlock: bool
    kv_cache_type: str
    vram_used_mb: int
    ram_used_mb: int
    notes: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def model_total_size_mb(path: Path) -> int:
    """Размер модели: для split — сумма всех частей."""
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


def _round_512(n: int) -> int:
    """Округляет n_ctx к ближайшему кратному 512 (вниз)."""
    if n <= 0:
        return MIN_N_CTX
    return max(MIN_N_CTX, (n // 512) * 512)


def _kv_mb(meta: GGUFMeta, kv_type: str, n_ctx: int) -> int:
    """Размер KV-кэша в МБ для заданного типа и n_ctx."""
    per_tok = meta.kv_bytes_per_token_fp16 if kv_type == "f16" else meta.kv_bytes_per_token_q8
    if per_tok <= 0:
        return 0
    return (n_ctx * per_tok) // (1024 * 1024)


def _max_ctx_in_budget(meta: GGUFMeta, kv_budget_mb: int, kv_type: str) -> int:
    """Сколько токенов KV влезет в kv_budget_mb при выбранном типе KV."""
    per_tok = meta.kv_bytes_per_token_fp16 if kv_type == "f16" else meta.kv_bytes_per_token_q8
    if per_tok <= 0 or kv_budget_mb <= 0:
        return 0
    return (kv_budget_mb * 1024 * 1024) // per_tok


def _target_ctx_upper_bound(meta: GGUFMeta, target_ctx: int) -> int:
    """Верхняя граница n_ctx: min(target_ctx, n_ctx_train)."""
    caps = [target_ctx]
    if meta.n_ctx_train > 0:
        caps.append(meta.n_ctx_train)
    upper = min(caps)
    return upper if upper > 0 else MIN_USEFUL_CTX


# ---------------------------------------------------------------------------
# Стратегия 1: всё на GPU
# ---------------------------------------------------------------------------


def _try_fit_all_on_gpu(
    meta: GGUFMeta,
    model_size_mb: int,
    usable_vram_mb: int,
    target_ctx: int,
) -> tuple[int, str] | None:
    """Пробует уместить модель + KV целиком в VRAM.

    Логика:
      1. Считаем n_ctx для f16 (качество) и q8_0 (компактность).
      2. Если f16 даёт ≥ GOOD_CTX — берём f16.
      3. Иначе — если q8_0 даёт заметно больше (≥1.5×) и ≥ MIN_USEFUL_CTX — берём q8_0.
      4. Иначе — то, что дотягивает до MIN_USEFUL_CTX.

    Возвращает (n_ctx, kv_type) или None, если даже при MIN_USEFUL_CTX
    ничего не влезает.
    """
    if usable_vram_mb <= 0:
        return None

    upper = _target_ctx_upper_bound(meta, target_ctx)
    weights_budget = usable_vram_mb - COMPUTE_BUFFER_MB - model_size_mb
    if weights_budget <= 0:
        return None

    def _ctx_for(kv_type: str) -> int:
        return _round_512(min(upper, _max_ctx_in_budget(meta, weights_budget, kv_type)))

    f16_ctx = _ctx_for("f16")
    q8_ctx = _ctx_for("q8_0")

    # Случай 1: f16 даёт хороший контекст — берём его (лучшее качество).
    if f16_ctx >= GOOD_CTX:
        return f16_ctx, "f16"

    # Случай 2: f16 не дотягивает, но q8_0 заметно лучше — экономим VRAM на KV.
    if q8_ctx >= MIN_USEFUL_CTX and q8_ctx > int(f16_ctx * 1.5):
        return q8_ctx, "q8_0"

    # Случай 3: оба малы, но один дотягивает до полезного минимума.
    if f16_ctx >= MIN_USEFUL_CTX:
        return f16_ctx, "f16"
    if q8_ctx >= MIN_USEFUL_CTX:
        return q8_ctx, "q8_0"

    return None


# ---------------------------------------------------------------------------
# Стратегия 2: частичный offload (VRAM + RAM)
# ---------------------------------------------------------------------------


def _pick_n_ctx_with_ram(
    meta: GGUFMeta,
    total_budget_mb: int,
    model_size_mb: int,
    target_ctx: int,
) -> tuple[int, str]:
    """Подбирает n_ctx исходя из общего бюджета (VRAM + RAM)."""
    upper = _target_ctx_upper_bound(meta, target_ctx)
    free_mb = max(0, total_budget_mb - model_size_mb)

    best_q8: tuple[int, str] | None = None
    for kv_type in ("f16", "q8_0"):
        max_ctx = _max_ctx_in_budget(meta, free_mb, kv_type)
        n = min(upper, max_ctx)
        n = _round_512(n)
        if kv_type == "f16" and n >= MIN_USEFUL_CTX:
            return n, "f16"
        if kv_type == "q8_0":
            best_q8 = (n, "q8_0")
            if n >= MIN_USEFUL_CTX:
                return n, "q8_0"

    if best_q8 is not None:
        return best_q8
    return MIN_N_CTX, "q8_0"


def _place_layers(
    model_size_mb: int,
    kv_mb: int,
    meta: GGUFMeta,
    usable_vram_mb: int,
) -> tuple[int, int, int]:
    """Решает, сколько слоёв положить на GPU.

    Returns:
        (n_gpu_layers, vram_used_mb, ram_used_mb)
        n_gpu_layers = -1 → все слои на GPU
    """
    if usable_vram_mb <= 0:
        return 0, 0, model_size_mb + kv_mb

    budget = usable_vram_mb - COMPUTE_BUFFER_MB
    if budget <= 0:
        return 0, 0, model_size_mb + kv_mb

    # Всё влезает целиком — забираем все слои на GPU.
    if model_size_mb + kv_mb + COMPUTE_BUFFER_MB <= usable_vram_mb:
        vram_used = model_size_mb + kv_mb + COMPUTE_BUFFER_MB
        return -1, vram_used, 200

    n_layer = max(meta.n_layer, 1)
    per_layer_weight = model_size_mb / n_layer
    per_layer_kv = kv_mb / n_layer
    per_layer_total = per_layer_weight + per_layer_kv

    if per_layer_total <= 0:
        return 0, 0, model_size_mb + kv_mb

    layers = int(budget / per_layer_total)
    layers = max(0, min(layers, n_layer))

    # КРИТИЧНО: частичный offload хуже 50% — контрпродуктивен.
    #
    # Каждый токен гоняет данные CPU↔GPU на каждом слое: выход CPU-слоя
    # отправляется на GPU, вход следующего CPU-слоя возвращается. При
    # 9/28 слоёв на GPU накладные расходы на синхронизацию съедают весь
    # выигрыш. Замерено на T1200 (4 ГБ): 0.4 ток/с при 9/28 vs ~2.5 ток/с
    # при 0/28 (чистый CPU). Если на GPU влезает меньше половины слоёв —
    # отдаём всё на CPU.
    if 0 < layers < n_layer * 0.5:
        logger.info(
            f"Partial offload {layers}/{n_layer} < 50% — отдаём модель "
            f"целиком на CPU (partial offload медленнее из-за CPU↔GPU sync)"
        )
        return 0, 0, model_size_mb + kv_mb

    # Не оставляем 1–2 слоя на CPU — они замедляют GPU-часть.
    if 0 < layers < n_layer and (n_layer - layers) < 2:
        layers = max(0, n_layer - 2)

    vram_used = int(layers * per_layer_total + COMPUTE_BUFFER_MB)
    ram_used = int((model_size_mb - layers * per_layer_weight) + (kv_mb - layers * per_layer_kv))
    ram_used = max(200, ram_used)
    return layers, vram_used, ram_used


def _optimal_batch(usable_vram_mb: int, n_gpu_layers: int) -> int:
    if n_gpu_layers == 0:
        return 512
    if usable_vram_mb >= 16000:
        return 2048
    if usable_vram_mb >= 8000:
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
    return major >= 7


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------


def optimize(
    model_path: Path,
    *,
    hw: HardwareProfile | None = None,
    target_n_ctx: int = 8192,  # было 32768 — провоцировало 32k на CPU
    model_size_mb: int | None = None,
    device: str | None = None,
) -> OptimizedParams:
    """Считает оптимальные параметры для модели на данном железе.

    Args:
        model_path: путь к GGUF-файлу (для split — к первой части).
        hw: профиль железа (None = собрать).
        target_n_ctx: верхняя граница n_ctx.
        model_size_mb: переопределить размер (None = посчитать).
        device: принудительное устройство для двухмодельного пайплайна.
            ``"CPU"`` — пропустить GPU-fit и жёстко обнулить n_gpu_layers
            (planner и analyst не должны делить VRAM).
            ``"GPU"`` или ``None`` — обычное поведение (пытаемся уместить
            всё на GPU, иначе VRAM+RAM).
    """
    hw = hw or get_hardware_profile()
    meta = read_gguf_meta(model_path)

    if model_size_mb is None:
        model_size_mb = model_total_size_mb(model_path)
    if model_size_mb <= 0:
        model_size_mb = 4000

    notes: list[str] = []
    gpu = hw.best_gpu
    usable_vram = gpu.usable_vram_mb if gpu else 0
    usable_ram = max(0, hw.ram.available_mb - RAM_RESERVE_MB)

    n_threads = hw.cpu.threads_for_llm
    n_batch = _optimal_batch(usable_vram, -1)
    flash = _optimal_flash_attn(hw, -1)

    # --- Стратегия 1: всё на GPU (пропускаем при device="CPU") ---
    if device != "CPU":
        fit = _try_fit_all_on_gpu(meta, model_size_mb, usable_vram, target_n_ctx)
        if fit is not None:
            n_ctx, kv_type = fit
            kv_mb = _kv_mb(meta, kv_type, n_ctx)
            vram_used = model_size_mb + kv_mb + COMPUTE_BUFFER_MB
            notes.append(f"Модель целиком на GPU (VRAM ≈ {vram_used} МБ)")
            if flash:
                notes.append("FlashAttention включён")

            params = OptimizedParams(
                n_ctx=n_ctx,
                n_gpu_layers=-1,
                n_threads=n_threads,
                n_batch=n_batch,
                flash_attn=flash,
                use_mmap=True,
                use_mlock=False,
                kv_cache_type=kv_type,
                vram_used_mb=int(vram_used),
                ram_used_mb=200,
                notes=tuple(notes),
            )
            logger.info(
                f"SLM оптимизация {model_path.name} [GPU-only]: "
                f"n_ctx={n_ctx}, n_gpu_layers=-1, n_threads={n_threads}, "
                f"n_batch={n_batch}, flash={flash}, kv={kv_type}, "
                f"VRAM≈{vram_used} МБ"
            )
            for note in notes:
                logger.info(f"  • {note}")
            return params

    # ------------------------------------------------------------------
    # Стратегия 2: VRAM + RAM, частичный offload.
    # ------------------------------------------------------------------
    total_budget = usable_vram + usable_ram - COMPUTE_BUFFER_MB
    if total_budget <= 0:
        notes.append("Недостаточно памяти: минимальный режим")
        total_budget = max(model_size_mb + 512, 4096)

    n_ctx, kv_type = _pick_n_ctx_with_ram(meta, total_budget, model_size_mb, target_n_ctx)
    if n_ctx < target_n_ctx:
        notes.append(f"n_ctx={n_ctx} (запрошено {target_n_ctx}): память под KV ограничена")

    kv_mb = _kv_mb(meta, kv_type, n_ctx)
    n_gpu_layers, vram_used, ram_used = _place_layers(
        model_size_mb,
        kv_mb,
        meta,
        usable_vram,
    )

    # device="CPU": жёстко обнуляем GPU-слои, даже если VRAM хватает.
    if device == "CPU" and n_gpu_layers != 0:
        notes.append(f"device=CPU: принудительно n_gpu_layers=0 (было {n_gpu_layers})")
        n_gpu_layers = 0
        vram_used = 0
        ram_used = model_size_mb + kv_mb

    # === [D1] CPU-only: жёсткий потолок контекста ради TTFT/throughput ===
    if n_gpu_layers == 0:
        if n_ctx > 8192:
            notes.append(f"[D1] CPU-only: n_ctx {n_ctx} -> 8192 (TTFT)")
            n_ctx = 8192
        # Квантизованный V-кэш требует Flash Attention, на CPU его нет.
        if kv_type != "f16":
            notes.append(
                f"CPU-only: kv {kv_type} -> f16 (квантизованный KV требует flash_attn, недоступного на CPU)"
            )
            kv_type = "f16"
        if n_batch > 512:
            n_batch = 512
            notes.append("[D1] CPU-only: n_batch -> 512")
        kv_mb = _kv_mb(meta, kv_type, n_ctx)
        vram_used, ram_used = 0, model_size_mb + kv_mb

    if n_ctx < MIN_USEFUL_CTX:
        notes.append(f"⚠️ n_ctx={n_ctx} < {MIN_USEFUL_CTX}: агент может работать нестабильно")

    if n_gpu_layers == -1:
        notes.append(f"Модель целиком на GPU (VRAM ≈ {vram_used} МБ)")
    elif n_gpu_layers == 0:
        notes.append(f"CPU-only (RAM ≈ {ram_used} МБ): модель не влезает в VRAM ({usable_vram} МБ)")
    else:
        notes.append(f"Частичный offload: {n_gpu_layers}/{meta.n_layer} слоёв на GPU")

    flash = _optimal_flash_attn(hw, n_gpu_layers)
    if flash:
        notes.append("FlashAttention включён")

    use_mmap = True
    use_mlock = n_gpu_layers == 0 and hw.ram.available_mb > (model_size_mb + RAM_RESERVE_MB + 4000)
    if use_mlock:
        notes.append("mlock включён (модель закреплена в RAM)")

    params = OptimizedParams(
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        n_threads=n_threads,
        n_batch=n_batch,
        flash_attn=flash,
        use_mmap=use_mmap,
        use_mlock=use_mlock,
        kv_cache_type=kv_type,
        vram_used_mb=int(vram_used),
        ram_used_mb=int(ram_used),
        notes=tuple(notes),
    )
    logger.info(
        f"SLM оптимизация {model_path.name} [VRAM+RAM]: "
        f"n_ctx={n_ctx}, n_gpu_layers={n_gpu_layers}, n_threads={n_threads}, "
        f"n_batch={n_batch}, flash={flash}, kv={kv_type}, "
        f"VRAM≈{vram_used} МБ, RAM≈{ram_used} МБ"
    )
    for note in notes:
        logger.info(f"  • {note}")
    return params


#
