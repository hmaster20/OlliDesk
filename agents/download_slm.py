"""Скачивание и управление локальными SLM-моделями (GGUF, llama.cpp)."""

import re
import shutil
from pathlib import Path

from loguru import logger

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"

# Embedding-модель общая, не участвует в выборе chat-модели.
EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

_LEGACY_CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


# ---------------------------------------------------------------------------
# Ручной override + кэш автоматического определения частей
# ---------------------------------------------------------------------------

# filename → количество частей. Нужен только как fallback, если HF-запрос
# недоступен (offline). В норме количество частей определяется автоматически
# через HfApi().list_repo_files(repo_id).
_MANUAL_PARTS_OVERRIDE: dict[str, int] = {}

# (repo_id, filename) → количество частей (кэш на сессию)
_HF_PARTS_CACHE: dict[tuple[str, str], int] = {}


def _split_part_name(stem: str, index: int, total: int) -> str:
    """Имя части разбитой GGUF-модели (как в HF-репозитории)."""
    return f"{stem}-{index:05d}-of-{total:05d}.gguf"


def _detect_parts_via_hf(repo_id: str, filename: str) -> int | None:
    """Возвращает число частей модели, обращаясь к HuggingFace.

    Возможные результаты:
        1     — в репозитории лежит единый файл <filename>
        N > 1 — в репозитории лежат части <stem>-NNNNN-of-MMMMM.gguf
        None  — не удалось определить (нет сети / HfApi упал)
    """
    key = (repo_id, filename)
    if key in _HF_PARTS_CACHE:
        return _HF_PARTS_CACHE[key]

    try:
        from huggingface_hub import HfApi

        api = HfApi()
        files = api.list_repo_files(repo_id=repo_id)
    except Exception as e:
        logger.warning(f"Не удалось получить список файлов {repo_id}: {e}")
        return None

    # Вариант 1: цельный файл
    if filename in files:
        _HF_PARTS_CACHE[key] = 1
        logger.debug(f"HF: {repo_id}/{filename} — цельный файл")
        return 1

    # Вариант 2: разбитый — ищем все <stem>-NNNNN-of-MMMMM.gguf
    stem = filename.removesuffix(".gguf")
    pattern = re.compile(
        rf"^{re.escape(stem)}-(\d{{5}})-of-(\d{{5}})\.gguf$"
    )
    max_total = 0
    for f in files:
        m = pattern.match(f)
        if m:
            total = int(m.group(2))
            if total > max_total:
                max_total = total

    if max_total > 0:
        _HF_PARTS_CACHE[key] = max_total
        logger.info(
            f"HF: {repo_id}/{filename} разбит на {max_total} частей "
            f"(определено автоматически)"
        )
        return max_total

    logger.debug(f"HF: {repo_id}/{filename} — не найден")
    return None


def get_model_parts(repo_id: str, filename: str) -> int:
    """Определяет число частей модели.

    Приоритет:
      1. Ручной override в _MANUAL_PARTS_OVERRIDE.
      2. Автопроверка через HF (кэшируется).
      3. Fallback = 1 (считаем модель цельной).
    """
    if filename in _MANUAL_PARTS_OVERRIDE:
        return _MANUAL_PARTS_OVERRIDE[filename]

    hf_parts = _detect_parts_via_hf(repo_id, filename)
    if hf_parts is not None:
        return hf_parts

    return 1


def is_model_split(repo_id: str, filename: str) -> bool:
    """True, если модель разбита на части."""
    return get_model_parts(repo_id, filename) > 1


# ---------------------------------------------------------------------------
# Пути и базовые утилиты
# ---------------------------------------------------------------------------

def get_models_dir() -> Path:
    """Каталог моделей (~/.ollidesk/models), создаёт при необходимости."""
    d = DEFAULT_MODELS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_model_path(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,
) -> Path | None:
    """Возвращает путь к модели: либо цельный файл, либо первую часть разбитой.

    Для разбитых приоритет отдаём частям, а не «merged»-файлу:
    llama.cpp нативно работает со split GGUF, если передать первую часть.
    """
    target_dir = target_dir or get_models_dir()

    parts = 1
    if repo_id is not None:
        parts = get_model_parts(repo_id, filename)
    elif filename in _MANUAL_PARTS_OVERRIDE:
        parts = _MANUAL_PARTS_OVERRIDE[filename]

    if parts > 1:
        stem = filename.removesuffix(".gguf")
        first = target_dir / _split_part_name(stem, 1, parts)
        if first.exists():
            return first

    merged = target_dir / filename
    if merged.exists():
        return merged
    return None


def is_model_installed(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,
) -> bool:
    """Проверяет, скачана ли модель (единым файлом или частями)."""
    return _resolve_model_path(filename, target_dir, repo_id) is not None


def get_model_path(
    filename: str,
    repo_id: str | None = None,
) -> Path | None:
    """Путь к модели (для split — к первой части)."""
    return _resolve_model_path(filename, None, repo_id)


# ---------------------------------------------------------------------------
# Скачивание разбитой модели
# ---------------------------------------------------------------------------

def _download_split_model(
    repo_id: str,
    filename: str,
    parts: int,
    target_dir: Path,
) -> Path:
    """Скачивает разбитую на N частей модель и возвращает путь к первой части.

    ВАЖНО: части НЕ склеиваются. llama.cpp читает split GGUF сам —
    достаточно передать ему путь к первой части, остальные он найдёт
    в той же папке по имени. Побайтовая склейка оставляет в заголовке
    метаданные ``split.count = N``, из-за чего загрузчик уходит искать
    отсутствующих соседей и падает с «Failed to load model from file».
    """
    from huggingface_hub import snapshot_download

    stem = filename.removesuffix(".gguf")
    pattern = f"{stem}-*-of-{parts:05d}.gguf"

    logger.info(
        f"Скачивание разбитой модели {filename} ({parts} частей) из {repo_id}..."
    )

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        allow_patterns=[pattern],
    )

    first_part: Path | None = None
    for i in range(1, parts + 1):
        name = _split_part_name(stem, i, parts)
        part_path = target_dir / name
        if not part_path.exists():
            raise FileNotFoundError(
                f"Часть модели не найдена после скачивания: {name}"
            )
        if i == 1:
            first_part = part_path
        logger.info(f"  ✓ Часть {i}/{parts}: {name}")

    assert first_part is not None
    logger.info(
        f"Разбитая модель готова: {first_part.name} "
        f"(+ ещё {parts - 1} частей в той же папке)"
    )
    return first_part


# ---------------------------------------------------------------------------
# Публичная функция скачивания
# ---------------------------------------------------------------------------

def download_model(
    repo_id: str,
    filename: str,
    target_dir: Path | None = None,
) -> Path:
    """Скачивает модель из Hugging Face.

    Для разбитых моделей возвращает путь к ПЕРВОЙ части; остальные
    llama.cpp подтянет сам.

    Args:
        repo_id: HF-репозиторий (например, "Qwen/Qwen2.5-7B-Instruct-GGUF")
        filename: итоговое имя (например, "qwen2.5-7b-instruct-q4_k_m.gguf")
        target_dir: каталог назначения (~/.ollidesk/models по умолчанию)

    Returns:
        Путь к скачанному файлу (для split — к первой части).
    """
    target_dir = target_dir or get_models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    parts = get_model_parts(repo_id, filename)

    if parts > 1:
        stem = filename.removesuffix(".gguf")
        first = target_dir / _split_part_name(stem, 1, parts)
        if first.exists():
            logger.info(f"Разбитая модель уже скачана: {first}")
            return first

        # Удаляем старый «склеенный» файл от предыдущих версий приложения,
        # чтобы он не сбивал с толку.
        stale = target_dir / filename
        if stale.exists():
            logger.info(
                f"Удаляю устаревший объединённый файл: {stale.name} "
                f"(будет заменён split-частями)"
            )
            try:
                stale.unlink()
            except OSError as e:
                logger.warning(f"Не удалось удалить {stale}: {e}")

        return _download_split_model(repo_id, filename, parts, target_dir)

    # Цельный файл
    target_path = target_dir / filename
    if target_path.exists():
        logger.info(f"Модель уже скачана: {target_path}")
        return target_path

    from huggingface_hub import hf_hub_download

    logger.info(f"Скачивание {filename} из {repo_id}...")
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(target_dir),
    )
    logger.info(f"Модель сохранена: {downloaded}")
    return Path(downloaded)


# ---------------------------------------------------------------------------
# Удаление
# ---------------------------------------------------------------------------

def delete_model(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,
) -> bool:
    """Удаляет модель (цельный файл и/или все части разбитой)."""
    target_dir = target_dir or get_models_dir()
    deleted_any = False

    merged = target_dir / filename
    if merged.exists():
        try:
            merged.unlink()
            logger.info(f"Модель удалена: {merged}")
            deleted_any = True
        except OSError as e:
            logger.error(f"Ошибка удаления {merged}: {e}")

    parts = 1
    if repo_id is not None:
        parts = get_model_parts(repo_id, filename)
    elif filename in _MANUAL_PARTS_OVERRIDE:
        parts = _MANUAL_PARTS_OVERRIDE[filename]

    if parts > 1:
        stem = filename.removesuffix(".gguf")
        for i in range(1, parts + 1):
            p = target_dir / _split_part_name(stem, i, parts)
            if not p.exists():
                continue
            try:
                p.unlink()
                logger.info(f"Удалена часть: {p.name}")
                deleted_any = True
            except OSError as e:
                logger.error(f"Ошибка удаления части {p}: {e}")

    return deleted_any


# ---------------------------------------------------------------------------
# Embedding-модель
# ---------------------------------------------------------------------------

def download_embed_model(target_dir: Path | None = None) -> Path | None:
    """Скачивает embedding-модель (общую для RAG)."""
    try:
        return download_model(EMBED_MODEL_REPO, EMBED_MODEL_FILE, target_dir)
    except Exception as e:
        logger.warning(f"Не удалось скачать embedding-модель: {e}")
        return None


# ---------------------------------------------------------------------------
# Получение путей для текущей активной SLM
# ---------------------------------------------------------------------------

def get_model_paths() -> tuple[Path | None, Path | None]:
    """Возвращает (chat_path, embed_path) для активной SLM.

    Chat-модель берётся из конфига (SLMConfig.selected_model) с fallback
    на legacy 1.5B, если конфиг недоступен или модель не найдена.
    """
    chat_path: Path | None = None
    embed_path: Path | None = None

    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info is not None:
            chat_path = _resolve_model_path(info.file, repo_id=info.repo)
    except Exception as e:
        logger.debug(f"Не удалось получить конфиг для SLM: {e}")

    if chat_path is None:
        legacy = get_models_dir() / _LEGACY_CHAT_MODEL_FILE
        if legacy.exists():
            logger.info(f"Использую legacy SLM: {legacy.name}")
            chat_path = legacy

    embed = get_models_dir() / EMBED_MODEL_FILE
    if embed.exists():
        embed_path = embed

    return chat_path, embed_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: скачать активную chat-модель + embedding."""
    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info is not None:
            path = download_model(info.repo, info.file)
            print(f"Chat-модель ({info.display_name}): {path}")
        else:
            print("Активная SLM-модель не выбрана в конфиге.")
    except Exception as e:
        print(f"Ошибка загрузки chat-модели: {e}")

    embed = download_embed_model()
    if embed:
        print(f"Embedding-модель: {embed}")
    else:
        print("Embedding-модель не скачана (опционально).")


if __name__ == "__main__":
    main()