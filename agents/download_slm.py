"""Скачивание и управление локальными SLM-моделями (GGUF, llama.cpp)."""

import shutil
import sys
from pathlib import Path

from loguru import logger

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"

# Embedding-модель общая, не участвует в выборе.
EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

_LEGACY_CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def get_models_dir() -> Path:
    """Возвращает каталог моделей, создавая его при необходимости."""
    d = DEFAULT_MODELS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_model_installed(filename: str, target_dir: Path | None = None) -> bool:
    """Проверяет, скачан ли итоговый файл модели."""
    target_dir = target_dir or get_models_dir()
    return (target_dir / filename).exists()


def get_model_path(filename: str) -> Path | None:
    """Возвращает путь к файлу модели, если он есть."""
    path = get_models_dir() / filename
    return path if path.exists() else None


# ---------------------------------------------------------------------------
# Скачивание разбитых моделей
# ---------------------------------------------------------------------------

def _download_split_model(
    repo_id: str,
    filename: str,
    parts: int,
    target_dir: Path,
) -> Path:
    """Скачивает разбитую на N частей модель и склеивает в один файл.

    Args:
        repo_id: HuggingFace репозиторий
        filename: Итоговое имя файла (например, "qwen2.5-7b-instruct-q4_k_m.gguf")
        parts: Количество частей (например, 2)
        target_dir: Каталог назначения

    Returns:
        Path к склеенному файлу.
    """
    from huggingface_hub import snapshot_download

    stem = filename.removesuffix(".gguf")
    # Шаблон: qwen2.5-7b-instruct-q4_k_m-*-of-00002.gguf
    pattern = f"{stem}-*-of-{parts:05d}.gguf"

    logger.info(
        f"Скачивание разбитой модели {filename} ({parts} частей) из {repo_id}..."
    )

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        allow_patterns=[pattern],
        resume_download=True,
    )

    # Собираем скачанные части в порядке номеров
    downloaded_parts: list[Path] = []
    for i in range(1, parts + 1):
        part_name = f"{stem}-{i:05d}-of-{parts:05d}.gguf"
        part_path = target_dir / part_name
        if not part_path.exists():
            raise FileNotFoundError(
                f"Часть модели не найдена после скачивания: {part_name}"
            )
        downloaded_parts.append(part_path)
        logger.info(f"  ✓ Часть {i}/{parts}: {part_name}")

    # Склеиваем побитово
    merged_path = target_dir / filename
    logger.info(f"Слияние {parts} частей в {filename}...")
    _merge_gguf_parts(downloaded_parts, merged_path)

    # Удаляем части, чтобы не занимать место (опционально)
    for p in downloaded_parts:
        try:
            p.unlink()
        except Exception as e:
            logger.warning(f"Не удалось удалить часть {p.name}: {e}")

    logger.info(f"Модель собрана: {merged_path}")
    return merged_path


def _merge_gguf_parts(parts: list[Path], output: Path) -> None:
    """Склеивает части GGUF в один файл побитовым копированием.

    Аналог `copy /b part1+part2 output` (Windows) или
    `cat part1 part2 > output` (Linux/macOS), но на чистом Python.
    """
    with open(output, "wb") as out_f:
        for part in parts:
            with open(part, "rb") as in_f:
                shutil.copyfileobj(in_f, out_f, length=1024 * 1024)


# ---------------------------------------------------------------------------
# Основная функция скачивания
# ---------------------------------------------------------------------------

def download_model(
    repo_id: str,
    filename: str,
    target_dir: Path | None = None,
    is_split: bool = False,
    parts: int = 1,
) -> Path:
    """Скачивает модель из Hugging Face.

    Для разбитых моделей (is_split=True) скачивает все части через
    snapshot_download и склеивает их в один GGUF-файл.

    Args:
        repo_id: Идентификатор репозитория
        filename: Имя файла (итоговое, если разбитая)
        target_dir: Каталог назначения
        is_split: True, если модель разбита на части
        parts: Количество частей (используется при is_split=True)

    Returns:
        Path к скачанному (или склеенному) файлу.
    """
    target_dir = target_dir or get_models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    if target_path.exists():
        logger.info(f"Модель уже скачана: {target_path}")
        return target_path

    if is_split:
        return _download_split_model(repo_id, filename, parts, target_dir)

    # Одиночный файл — обычный путь
    from huggingface_hub import hf_hub_download

    logger.info(f"Скачивание {filename} из {repo_id}...")
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(target_dir),
    )
    logger.info(f"Модель сохранена: {downloaded}")
    return Path(downloaded)


def delete_model(filename: str, target_dir: Path | None = None) -> bool:
    """Удаляет локальный файл модели."""
    target_dir = target_dir or get_models_dir()
    path = target_dir / filename
    if not path.exists():
        return False
    try:
        path.unlink()
        logger.info(f"Модель удалена: {path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления {path}: {e}")
        return False


def download_embed_model(target_dir: Path | None = None) -> Path | None:
    """Скачивает embedding-модель (общую)."""
    try:
        return download_model(EMBED_MODEL_REPO, EMBED_MODEL_FILE, target_dir)
    except Exception as e:
        logger.warning(f"Не удалось скачать embedding-модель: {e}")
        return None


def get_model_paths() -> tuple[Path | None, Path | None]:
    """Возвращает (chat_path, embed_path) для активной SLM-модели."""
    chat_path: Path | None = None
    embed_path: Path | None = None

    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info:
            p = get_models_dir() / info.file
            if p.exists():
                chat_path = p
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


def main() -> None:
    """CLI-точка входа: скачать активную chat-модель + embedding."""
    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info:
            path = download_model(
                info.repo,
                info.file,
                is_split=info.is_split,
                parts=info.parts,
            )
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