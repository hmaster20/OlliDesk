"""Автовыбор chat-модели SLM под профиль железа (по tier + fit)."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from agents.hardware_profile import HardwareProfile, get_hardware_profile
from agents.model_pool import Role
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
        score += 5.0  # полностью на GPU — быстро
    elif fits_ram:
        score += 1.0  # CPU-only — медленно, но работает
    else:
        score -= 100.0  # не влезает вообще — отбраковка

    reason = (
        "влезает целиком в VRAM"
        if fits_vram
        else ("влезает в RAM (CPU)" if fits_ram else "не влезает ни в VRAM, ни в RAM")
    )
    return ModelFit(info=info, fits_vram=fits_vram, fits_ram=fits_ram, score=score, reason=reason)


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
    logger.warning(f"Автовыбор SLM: ничего не влезает, беру {smallest.display_name}")
    return smallest


def select_pair(
    hw: HardwareProfile | None = None,
) -> dict[str, SLMModelInfo | None]:
    """Возвращает ДВЕ модели по ролям: planner + analyst.

    Приоритет:
      1. Явный выбор из UI (``slm.planner_model_id`` / ``analyst_model_id``)
         — используется без каких-либо проверок на tier/размер. Даже если
         модель «не оптимальна» по железу, пользователь знает лучше.
      2. Автовыбор для недостающих ролей (tier 1 в VRAM для planner,
         tier 2 в RAM для analyst).

    Guard: если planner и analyst — одна и та же модель, возвращает
    ``analyst=None`` — вызывающий (`_ModelPoolLoadThread`) покажет внятную
    ошибку, а не уйдёт в OOM.

    Args:
        hw: Профиль железа (по умолчанию собирается один раз за процесс).

    Returns:
        ``{'planner': SLMModelInfo | None, 'analyst': SLMModelInfo | None}``.
    """
    hw = hw or get_hardware_profile()
    cfg = load_config()
    candidates = cfg.slm.get_chat_models()
    if not candidates:
        logger.warning("select_pair: нет chat-моделей в конфиге")
        return {"planner": None, "analyst": None}

    fits = [_score_model(m, hw) for m in candidates]
    gpu = hw.best_gpu
    vram = gpu.usable_vram_mb if gpu else 0

    # --- 1. Явный выбор из UI ---
    planner: SLMModelInfo | None = cfg.slm.get_planner_info()
    analyst: SLMModelInfo | None = cfg.slm.get_analyst_info()
    planner_source = "явно из конфига" if planner else "автовыбор"
    analyst_source = "явно из конфига" if analyst else "автовыбор"
    logger.debug(f"select_pair: planner_source={planner_source}, analyst_source={analyst_source}")

    # --- 2. Автовыбор planner ---
    if planner is None:
        planner_pool = [f for f in fits if f.info.tier == 1 and f.fits_vram and vram >= 2900]
        planner_pool.sort(key=lambda f: f.score, reverse=True)
        planner = planner_pool[0].info if planner_pool else None
        if planner is None:
            vram_fits = sorted(
                (f for f in fits if f.fits_vram),
                key=lambda f: f.score,
                reverse=True,
            )
            planner = vram_fits[0].info if vram_fits else None

    # --- 3. Автовыбор analyst ---
    if analyst is None:
        analyst_pool = [f for f in fits if f.info.tier == 2 and f.fits_ram and f.info.kind == "chat"]
        analyst_pool.sort(key=lambda f: f.score, reverse=True)
        analyst = analyst_pool[0].info if analyst_pool else None
        if analyst is None:
            ram_fits = sorted(
                (f for f in fits if f.fits_ram),
                key=lambda f: f.score,
                reverse=True,
            )
            if ram_fits:
                candidate = ram_fits[0].info
                # Не берём тот же файл, что planner — двухмодельный режим
                # с одной моделью бессмыслен (дважды съест память).
                if planner is None or candidate.id != planner.id:
                    analyst = candidate

    # --- 4. Одна модель на две роли: НЕ отбрасываем, работаем single-model ---
    if planner is not None and analyst is not None and planner.id == analyst.id:
        if cfg.slm.planner_model_id and cfg.slm.analyst_model_id:
            logger.info(
                f"select_pair: planner и analyst — одна модель "
                f"({planner.id}), пользователь выбрал явно. "
                f"Модель будет загружена ОДИН раз и обслужит обе роли "
                f"(ModelPool.add_role кэширует клиент по файлу)."
            )
        else:
            logger.warning(
                f"select_pair: planner и analyst — одна модель ({planner.id}) "
                f"после автовыбора (второй подходящей модели нет). "
                f"Работаем в single-model режиме."
            )
    return {"planner": planner, "analyst": analyst}


def select_park(
    hw: HardwareProfile | None = None,
    *,
    profile_park_roles: dict[str, str] | None = None,
) -> dict[Role, SLMModelInfo]:
    """Выбирает модели под все роли парка (4.1).

    Приоритет:
      1. Явные id из ``profile_park_roles`` (значения — ``SLMModelInfo.id``).
         Несуществующий id → ``warning``, роль пропускается.
      2. Автовыбор по тегам:
         * ``router`` — первая модель с тегом ``router``, tier ≤ 1, влезает в VRAM.
         * ``planner`` — как в :func:`select_pair` (tier 1, VRAM).
         * ``coder`` — модель с тегом ``coder``, максимальный tier, влезает в RAM.
         * ``verifier`` — если ``router`` и ``verifier`` — один файл, вернётся
           тот же ``SLMModelInfo``; иначе первая с тегом ``verifier``.
         * ``analyst`` — как в :func:`select_pair` (tier 2, RAM).
         * ``embed`` — не входит в парк (embed-модель отдельная).

    Args:
        hw: Профиль железа (по умолчанию собирается).
        profile_park_roles: ``ProfileConfig.park_roles`` — dict
            ``{"router": "qwen2.5-3b-...", "coder": "qwen3-coder-30b-..."}``.

    Returns:
        Словарь ``{Role: SLMModelInfo}``. Пустые роли не входят.
    """
    hw = hw or get_hardware_profile()
    cfg = load_config()
    candidates = cfg.slm.get_chat_models()
    if not candidates:
        logger.warning("select_park: нет chat-моделей в конфиге")
        return {}

    # Явный выбор по ролям из профиля.
    explicit: dict[Role, SLMModelInfo] = {}
    for role_value, model_id in (profile_park_roles or {}).items():
        try:
            role = Role(role_value)
        except ValueError:
            logger.warning(f"select_park: неизвестная роль {role_value!r}")
            continue
        info = cfg.slm.get_model_by_id(model_id)
        if info is None:
            logger.warning(
                f"select_park: профиль ссылается на {model_id!r} ({role.value}), "
                f"но такой модели нет в каталоге"
            )
            continue
        explicit[role] = info

    result: dict[Role, SLMModelInfo] = dict(explicit)

    # Автоподбор недостающих ролей по тегам.
    fits = [_score_model(m, hw) for m in candidates]
    gpu = hw.best_gpu
    vram = gpu.usable_vram_mb if gpu else 0

    def _pick(
        tag: str,
        *,
        prefer_gpu: bool,
        min_tier: int = 0,
        max_tier: int = 5,
    ) -> SLMModelInfo | None:
        pool = [
            f
            for f in fits
            if tag in f.info.tags
            and min_tier <= f.info.tier <= max_tier
            and (f.fits_vram if prefer_gpu else f.fits_ram)
        ]
        pool.sort(key=lambda f: f.score, reverse=True)
        return pool[0].info if pool else None

    # Router / verifier — GPU, tier ≤ 1.
    if Role.ROUTER not in result and vram > 0:
        picked = _pick("router", prefer_gpu=True, max_tier=1)
        if picked:
            result[Role.ROUTER] = picked

    # Planner — GPU, tier == 1.
    if Role.PLANNER not in result:
        picked = _pick("planner", prefer_gpu=True, min_tier=1, max_tier=1)
        if picked:
            result[Role.PLANNER] = picked

    # Verifier — GPU; если router уже выбран и его файл подходит, делим
    # файл (ModelPool закэширует по file).
    if Role.VERIFIER not in result:
        router_info = result.get(Role.ROUTER)
        if router_info is not None and "verifier" in router_info.tags:
            result[Role.VERIFIER] = router_info
        else:
            picked = _pick("verifier", prefer_gpu=True, max_tier=1)
            if picked:
                result[Role.VERIFIER] = picked

    # Coder — CPU (или GPU, если помещается), максимальный tier.
    if Role.CODER not in result:
        coder_pool = [f for f in fits if "coder" in f.info.tags]
        coder_pool.sort(
            key=lambda f: (f.fits_vram, f.fits_ram, f.info.tier),
            reverse=True,
        )
        if coder_pool:
            result[Role.CODER] = coder_pool[0].info

    # Analyst — как в select_pair (для research-флоу).
    if Role.ANALYST not in result:
        picked = _pick("analyst", prefer_gpu=False, min_tier=2, max_tier=2)
        if picked:
            result[Role.ANALYST] = picked

    logger.info("select_park: " + ", ".join(f"{role.value}={info.id}" for role, info in result.items()))
    return result


def select_pair_park(hw: HardwareProfile | None = None) -> dict[Role, SLMModelInfo]:
    """Fallback: только planner+analyst (совместимость с 4.7).

    Используется, если профиль не задаёт ``park_roles`` и мы хотим
    сохранить текущее двухмодельное поведение research-флоу. По сути
    оборачивает :func:`select_pair`.
    """
    pair = select_pair(hw)
    result: dict[Role, SLMModelInfo] = {}
    if pair.get("planner"):
        result[Role.PLANNER] = pair["planner"]
    if pair.get("analyst"):
        result[Role.ANALYST] = pair["analyst"]
    return result


# ---------------------------------------------------------------------------
# 10.6: coder-модель для coding-задач
# ---------------------------------------------------------------------------


def select_coder(
    hw: HardwareProfile | None = None,
    *,
    profile_coder_models: list[str] | None = None,
    allow_fallback: bool = True,
) -> SLMModelInfo | None:
    """Выбирает coder-модель для coding-задач (10.6).

    Приоритет:
      1. ``profile_coder_models`` (``ProfileConfig.coder_models`` — список id).
         Первая установленная и влезающая модель.
      2. Авто: первый тег ``coder``, влезающий в VRAM; иначе — тег ``coder``,
         влезающий в RAM; максимальный tier.
      3. Fallback (если ``allow_fallback=True``): обычная chat-модель
         (tier 2, влезает в RAM) — помечается как ``None`` coder, но
         вызывающий код может взять её для роли ``ANALYST``.

    Args:
        hw: Профиль железа (по умолчанию собирается один раз).
        profile_coder_models: Список id из ``ProfileConfig.coder_models``.
        allow_fallback: ``True`` — вернуть обычную chat-модель, если
            coder-модели нет. ``False`` — вернуть ``None``.

    Returns:
        ``SLMModelInfo`` coder-модели или ``None`` (если ничего нет).
    """
    from agents.download_slm import is_model_installed

    hw = hw or get_hardware_profile()
    cfg = load_config()
    candidates = cfg.slm.get_chat_models()
    if not candidates:
        return None

    fits = [_score_model(m, hw) for m in candidates]

    # 1. Явные id из профиля.
    if profile_coder_models:
        for model_id in profile_coder_models:
            info = cfg.slm.get_model_by_id(model_id)
            if info is None:
                logger.warning(f"select_coder: профиль ссылается на {model_id!r}, но в каталоге такой нет")
                continue
            if not is_model_installed(info.file):
                logger.info(f"select_coder: {model_id!r} не установлена — пропускаю")
                continue
            return info

    # 2. Авто: coder-тег, максимальный tier, влезает в VRAM/RAM.
    coder_fits = [f for f in fits if "coder" in f.info.tags]
    coder_fits.sort(
        key=lambda f: (f.fits_vram, f.fits_ram, f.info.tier),
        reverse=True,
    )
    if coder_fits:
        best = coder_fits[0]
        logger.info(
            f"select_coder: {best.info.id} (tier={best.info.tier}, {'VRAM' if best.fits_vram else 'RAM'})"
        )
        return best.info

    # 3. Fallback на обычную chat-модель.
    if allow_fallback:
        chat_fits = sorted(
            (f for f in fits if f.fits_ram),
            key=lambda f: f.info.tier,
            reverse=True,
        )
        if chat_fits:
            logger.info(f"select_coder: coder-модели нет, fallback на {chat_fits[0].info.id}")
            return chat_fits[0].info
    return None
