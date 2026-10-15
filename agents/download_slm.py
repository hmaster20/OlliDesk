"""Скачивание и управление локальными SLM-моделями (GGUF, llama.cpp).

Принцип: локальные проверки (установлена ли модель, сколько частей) НЕ
обращаются к HuggingFace. HF используется только в момент скачивания.
Это устраняет фризы UI при открытии диалога SLM, где раньше каждый
показ таблицы дёргал HF для каждой из ~30 моделей.
"""

import re
from pathlib import Path

from loguru import logger

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"

# Embedding-модель общая, не участвует в выборе chat-модели.
EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

_LEGACY_CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

# filename → количество частей. Fallback только при скачивании, когда HF
# недоступен. В нормальной работе число частей определяется по имени файла
# первой части (см. _detect_parts_from_local).
_MANUAL_PARTS_OVERRIDE: dict[str, int] = {}

# (repo_id, filename) → количество частей (кэш на сессию). Заполняется
# только во время скачивания, не при UI-проверках.
_HF_PARTS_CACHE: dict[tuple[str, str], int] = {}


# ---------------------------------------------------------------------------
# Имена split-частей
# ---------------------------------------------------------------------------

_SPLIT_PART_RE = re.compile(
    r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$",
    re.IGNORECASE,
)


def _split_part_name(stem: str, index: int, total: int) -> str:
    """Имя части разбитой GGUF-модели (как в HF-репозитории)."""
    return f"{stem}-{index:05d}-of-{total:05d}.gguf"


# ---------------------------------------------------------------------------
# Локальное определение частей (без сети!)
# ---------------------------------------------------------------------------


def _detect_parts_from_local(filename: str, target_dir: Path) -> int:
    """Сколько частей модели лежит локально.

    Возможные результаты:
        0 — ни цельного файла, ни частей
        1 — цельный файл <filename>
        N — N частей разбитой модели

    Не обращается в сеть.
    """
    if (target_dir / filename).exists():
        return 1

    stem = filename.removesuffix(".gguf")
    total = 0
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            total = max(total, int(m.group("total")))
    return total


def _detect_parts_via_hf(repo_id: str, filename: str) -> int | None:
    """Возвращает число частей модели, обращаясь к HuggingFace.

    Используется ТОЛЬКО при скачивании. Возможные результаты:
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

    if filename in files:
        _HF_PARTS_CACHE[key] = 1
        logger.debug(f"HF: {repo_id}/{filename} — цельный файл")
        return 1

    stem = filename.removesuffix(".gguf")
    pattern = re.compile(rf"^{re.escape(stem)}-(\d{{5}})-of-(\d{{5}})\.gguf$")
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
            "(определено автоматически)"
        )
        return max_total

    logger.debug(f"HF: {repo_id}/{filename} — не найден")
    return None


def get_model_parts(repo_id: str, filename: str) -> int:
    """Число частей модели. Обращается к HF только если локально ничего нет.

    Приоритет:
      1. Ручной override в _MANUAL_PARTS_OVERRIDE.
      2. Локальная ФС (если модель уже скачана).
      3. HF API (при скачивании).
      4. Fallback = 1.
    """
    if filename in _MANUAL_PARTS_OVERRIDE:
        return _MANUAL_PARTS_OVERRIDE[filename]

    # Локальная проверка — мгновенная.
    local = _detect_parts_from_local(filename, get_models_dir())
    if local > 0:
        return local

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


def _resolve_model_path(filename: str, target_dir: Path | None = None) -> Path | None:
    """Путь к модели: цельный файл или первая часть разбитой.

    Работает только с локальной ФС. Для split-моделей приоритет отдаём
    частям, а не «merged»-файлу: llama.cpp нативно работает со split GGUF,
    если передать первую часть.
    """
    target_dir = target_dir or get_models_dir()

    # 1. Split-модель: ищем первую часть.
    stem = filename.removesuffix(".gguf")
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem and m.group("idx") == "00001":
            return entry

    # 2. Цельный файл.
    merged = target_dir / filename
    if merged.exists():
        return merged
    return None


def is_model_installed(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,  # оставлен для совместимости, не используется
) -> bool:
    """Проверяет, скачана ли модель (единым файлом или частями).

    ВАЖНО: не обращается к HuggingFace. Проверка чисто локальная —
    наличие <filename> ИЛИ любой из частей <stem>-*-of-*.gguf.
    """
    target_dir = target_dir or get_models_dir()
    return _resolve_model_path(filename, target_dir) is not None


def get_model_path(
    filename: str,
    repo_id: str | None = None,  # оставлен для совместимости
) -> Path | None:
    """Путь к модели (для split — к первой части). Не ходит в сеть."""
    return _resolve_model_path(filename, None)


def get_local_size_mb(filename: str, target_dir: Path | None = None) -> int:
    """Размер модели на диске в МБ (сумма всех частей). 0, если нет."""
    target_dir = target_dir or get_models_dir()
    merged = target_dir / filename
    if merged.exists():
        try:
            return merged.stat().st_size // (1024 * 1024)
        except OSError:
            return 0

    stem = filename.removesuffix(".gguf")
    total = 0
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total // (1024 * 1024)


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

    Части НЕ склеиваются. llama.cpp читает split GGUF сам — достаточно
    передать ему путь к первой части, остальные он найдёт в той же папке.
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
            raise FileNotFoundError(f"Часть модели не найдена после скачивания: {name}")
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

        stale = target_dir / filename
        if stale.exists():
            logger.info(
                f"Удаляю устаревший объединённый файл: {stale.name} "
                "(будет заменён split-частями)"
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
    repo_id: str | None = None,  # совместимость
) -> bool:
    """Удаляет модель (цельный файл и/или все части разбитой). Не ходит в сеть."""
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

    stem = filename.removesuffix(".gguf")
    for entry in list(target_dir.iterdir()):
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            try:
                entry.unlink()
                logger.info(f"Удалена часть: {entry.name}")
                deleted_any = True
            except OSError as e:
                logger.error(f"Ошибка удаления части {entry}: {e}")

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
    """Возвращает (chat_path, embed_path) для активной SLM."""
    chat_path: Path | None = None
    embed_path: Path | None = None

    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info is not None:
            chat_path = _resolve_model_path(info.file)
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