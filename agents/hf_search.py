"""Поиск моделей на HuggingFace (9.3.1).

Тонкая обёртка над `huggingface_hub.HfApi`:
  * `search_models(query, filter="gguf", sort="downloads", limit=50)`.
  * `model_info(repo_id)` — карточка (описание, теги, лицензия, siblings).
  * `list_gguf_files(repo_id)` — `.gguf`-файлы с размерами.
  * `detect_tags(card_data)` — эвристика тегов: coder / reasoning / moE /
    embed / chat.

Принципы:
  * **Оффлайн не блокирует.** Все сетевые вызовы в `asyncio.to_thread`
    (HfApi синхронный). `HFUnavailableError` — если сеть недоступна;
    вызывающий код показывает сообщение и не падает.
  * **Кэш на сессию.** `search_models` мемоизирует `(query, filter)` на
    10 минут — HF-лимит 30 req/min без токена.
  * **Токен.** Из `AppConfig.hf_token` (приоритет) или `HF_TOKEN` env.
  * **Lazy import.** `huggingface_hub` импортируется внутри — не тянем
    при старте приложения.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Rate limit: HF даёт 30 req/min без токена, 300 с токеном.
# Кэш на 10 минут — «дорогие» вызовы (list_models/model_info) не дублируются.
_CACHE_TTL_S = 600

# Сколько результатов отдавать по умолчанию.
DEFAULT_LIMIT = 50


class HFUnavailableError(Exception):
    """HF API недоступен (сеть, DNS, timeout)."""


@dataclass
class HFModel:
    """Результат `search_models`."""

    repo_id: str
    downloads: int = 0
    likes: int = 0
    tags: list[str] = field(default_factory=list)
    pipeline_tag: str = ""
    last_modified: str = ""


@dataclass
class HFGgufFile:
    """Один `.gguf`-файл в репозитории."""

    filename: str
    size_bytes: int = 0

    @property
    def size_mb(self) -> int:
        return self.size_bytes // (1024 * 1024)

    @property
    def size_human(self) -> str:
        if self.size_bytes <= 0:
            return "?"
        mb = self.size_bytes / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.1f} ГБ"
        return f"{mb:.0f} МБ"


@dataclass
class HFModelDetails:
    """Полная карточка модели (`model_info` + `list_gguf_files`)."""

    repo_id: str
    author: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    pipeline_tag: str = ""
    license: str = ""
    downloads: int = 0
    likes: int = 0
    last_modified: str = ""
    gguf_files: list[HFGgufFile] = field(default_factory=list)
    url: str = ""

    @property
    def is_gguf(self) -> bool:
        return any(f.filename.endswith(".gguf") for f in self.gguf_files)


# ---------------------------------------------------------------------------
# Кэш
# ---------------------------------------------------------------------------

_search_cache: dict[tuple[str, str, str], tuple[float, list[HFModel]]] = {}
_info_cache: dict[str, tuple[float, HFModelDetails]] = {}


def clear_caches() -> None:
    """Сбрасывает кэши (для тестов / принудительного refresh)."""
    _search_cache.clear()
    _info_cache.clear()


# ---------------------------------------------------------------------------
# HfApi-хелперы
# ---------------------------------------------------------------------------


def _hf_token() -> str | None:
    """Токен из конфига или env. ``None`` — без токена."""
    try:
        from core.config import load_config

        token = (load_config().hf_token or "").strip()
        if token:
            logger.debug("hf_search: используется токен из конфига")
            return token
    except Exception:
        pass
    env_token = os.environ.get("HF_TOKEN")
    if env_token:
        logger.debug("hf_search: используется токен из переменной окружения HF_TOKEN")
    return env_token or None


def _get_api():
    """Ленивый `HfApi` с токеном."""
    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        logger.error("hf_search: huggingface_hub не установлен")
        raise HFUnavailableError("huggingface_hub не установлен: `pip install huggingface_hub`") from e
    token = _hf_token()
    return HfApi(token=token) if token else HfApi()


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


async def search_models(
    query: str,
    *,
    filter_tag: str = "gguf",
    sort: str = "downloads",
    limit: int = DEFAULT_LIMIT,
) -> list[HFModel]:
    """Ищет модели на HF.

    Args:
        query: Поисковая строка (может быть пустой — тогда вернёт
            top по downloads с фильтром).
        filter_tag: ``"gguf"`` / ``"text-generation"`` / ``""`` (без фильтра).
        sort: ``"downloads"`` / ``"likes"`` / ``"lastModified"``.
        limit: Максимум результатов (1..100).

    Returns:
        Список `HFModel`.

    Raises:
        HFUnavailableError: Сеть/HF API недоступны.
    """
    query = (query or "").strip()
    n = max(1, min(int(limit), 100))

    valid_api_sorts = {"downloads", "likes", "lastModified", "createdAt", "id"}
    api_sort = sort if sort in valid_api_sorts else "downloads"

    cache_key = (query, filter_tag, api_sort)
    cached = _search_cache.get(cache_key)
    if cached is not None:
        ts, models = cached
        if time.time() - ts < _CACHE_TTL_S:
            logger.debug(f"hf_search: cache hit {cache_key!r} ({len(models)} моделей)")
            return models

    emit_event_safe("hf.search.started", {"query": query, "filter": filter_tag})
    started = time.perf_counter()

    def _do() -> list[HFModel]:
        api = _get_api()
        kwargs: dict[str, Any] = {"sort": api_sort, "limit": n, "full": False}
        if query:
            kwargs["search"] = query
        if filter_tag:
            kwargs["filter"] = filter_tag

        logger.info(f"hf_search: api.list_models({kwargs})")
        try:
            iterator = api.list_models(**kwargs)
            results: list[HFModel] = []
            for m in iterator:
                results.append(
                    HFModel(
                        repo_id=str(getattr(m, "id", "") or ""),
                        downloads=int(getattr(m, "downloads", 0) or 0),
                        likes=int(getattr(m, "likes", 0) or 0),
                        tags=list(getattr(m, "tags", []) or []),
                        pipeline_tag=str(getattr(m, "pipeline_tag", "") or ""),
                        last_modified=str(getattr(m, "last_modified", "") or ""),
                    )
                )
            return results
        except Exception as e:
            logger.error(f"hf_search: ошибка HF API: {e}")
            raise HFUnavailableError(f"HF list_models: {e}") from e

    try:
        models = await asyncio.wait_for(asyncio.to_thread(_do), timeout=30.0)
    except TimeoutError as e:
        logger.error("hf_search: timeout при запросе к HF API (30с)")
        raise HFUnavailableError("HF list_models: timeout 30s") from e

    _search_cache[cache_key] = (time.time(), models)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    if models:
        logger.info(f"hf_search: '{query}' → {len(models)} моделей за {duration_ms} ms")
        emit_event_safe("hf.search.found", {"query": query, "n_results": len(models), "duration_ms": duration_ms})
    else:
        logger.warning(f"hf_search: по запросу '{query}' ничего не найдено")
        emit_event_safe("hf.search.empty", {"query": query, "duration_ms": duration_ms})

    return models


async def model_info(repo_id: str) -> HFModelDetails:
    """Читает карточку модели + список `.gguf`-файлов.

    Args:
        repo_id: ``"Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"``.

    Returns:
        `HFModelDetails` (с `gguf_files`).

    Raises:
        HFUnavailableError: Сеть/HF API недоступны.
    """
    if not repo_id:
        raise ValueError("hf.model_info: пустой repo_id")

    cached = _info_cache.get(repo_id)
    if cached is not None:
        ts, info = cached
        if time.time() - ts < _CACHE_TTL_S:
            logger.debug(f"hf_info: cache hit {repo_id}")
            return info

    def _do() -> HFModelDetails:
        api = _get_api()
        logger.info(f"hf_info: запрос карточки {repo_id}")
        try:
            # ИСПРАВЛЕНИЕ: в новых версиях huggingface_hub аргумент cardData удален.
            # Данные карточки (description, tags) теперь всегда доступны через info.cardData.
            info = api.model_info(repo_id, files_metadata=True)
        except Exception as e:
            logger.error(f"hf_info: ошибка получения {repo_id}: {e}")
            raise HFUnavailableError(f"HF model_info: {e}") from e

        card = getattr(info, "cardData", None) or {}
        siblings = getattr(info, "siblings", None) or []
        gguf_files: list[HFGgufFile] = []
        for s in siblings:
            fname = str(getattr(s, "rfilename", "") or "")
            if not fname.lower().endswith(".gguf"):
                continue
            size = int(getattr(s, "size", 0) or 0)
            gguf_files.append(HFGgufFile(filename=fname, size_bytes=size))

        description = (
            str(card.get("model_name") or "")
            + ("\n\n" if card.get("model_name") else "")
            + str(card.get("short_description") or card.get("description") or "")
        ).strip()

        license_str = ""
        lic = card.get("license")
        if isinstance(lic, list):
            license_str = ", ".join(str(x) for x in lic)
        elif lic:
            license_str = str(lic)

        return HFModelDetails(
            repo_id=repo_id,
            author=str(getattr(info, "author", "") or ""),
            description=description,
            tags=list(getattr(info, "tags", []) or []),
            pipeline_tag=str(getattr(info, "pipeline_tag", "") or ""),
            license=license_str,
            downloads=int(getattr(info, "downloads", 0) or 0),
            likes=int(getattr(info, "likes", 0) or 0),
            last_modified=str(getattr(info, "lastModified", "") or ""),
            gguf_files=gguf_files,
            url=f"https://huggingface.co/{repo_id}",
        )

    try:
        details = await asyncio.wait_for(asyncio.to_thread(_do), timeout=30.0)
    except TimeoutError as e:
        logger.error(f"hf_info: timeout при запросе карточки {repo_id}")
        raise HFUnavailableError("HF model_info: timeout 30s") from e

    _info_cache[repo_id] = (time.time(), details)
    logger.info(f"hf_info: {repo_id} — {len(details.gguf_files)} .gguf файлов, downloads={details.downloads}")
    return details


def detect_tags(
    tags: list[str] | None,
    pipeline_tag: str = "",
    repo_id: str = "",
) -> list[str]:
    """Эвристика тегов модели по cardData.tags + pipeline_tag + repo_id.

    Args:
        tags: Теги из карточки HF (``cardData.tags`` или
            ``model_info.tags``).
        pipeline_tag: ``model_info.pipeline_tag``.
        repo_id: id репозитория (для случая, когда карточка пустая).

    Returns:
        Список тегов из нашего закрытого набора: ``chat`` / ``coder`` /
        ``reasoning`` / ``moE`` / ``embed`` / ``router`` / ``planner`` /
        ``verifier``.
    """
    combined = " ".join(str(x).lower() for x in (tags or []) + [pipeline_tag, repo_id])
    out: list[str] = []
    if any(k in combined for k in ("coder", "code-", "starcoder", "code_")):
        out.append("coder")
    if any(k in combined for k in ("reasoning", "r1", "-think", "qwq")):
        out.append("reasoning")
    if any(k in combined for k in ("moe", "a3b", "a2b", "a22b")):
        out.append("moE")
    if any(k in combined for k in ("embed", "bge", "nomic-embed", "e5-", "gte-")):
        out.append("embed")
    if "embed" not in out and (not out or "coder" not in out):
        out.append("chat")
    # Дедупликация с сохранением порядка.
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# ---------------------------------------------------------------------------
# Внутренняя обёртка emit_event (без падения при отсутствии шины)
# ---------------------------------------------------------------------------


def emit_event_safe(kind: str, payload: dict) -> None:
    """Обёртка: emit_event не должен валить поиск при ошибке шины."""
    try:
        from core.event_bus import emit_event

        emit_event(kind, payload)
    except Exception:
        logger.debug(f"hf_search: не удалось отправить событие {kind}", exc_info=True)


__all__ = [
    "DEFAULT_LIMIT",
    "HFGgufFile",
    "HFModel",
    "HFModelDetails",
    "HFUnavailableError",
    "clear_caches",
    "detect_tags",
    "model_info",
    "search_models",
]
