"""Скачивание и управление локальными SLM-моделями (GGUF, llama.cpp).

Модели скачиваются из Hugging Face через huggingface_hub в
~/.ollidesk/models/. Управление (выбор, скачивание, удаление) — через UI.
"""

from pathlib import Path

from loguru import logger

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"

# Embedding-модель общая, не участвует в выборе.
EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

# Legacy 1.5B — только как fallback, если конфиг недоступен.
_LEGACY_CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def get_models_dir() -> Path:
    """Возвращает каталог моделей, создавая его при необходимости."""
    d = DEFAULT_MODELS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_model_installed(filename: str, target_dir: Path | None = None) -> bool:
    """Проверяет, скачан ли файл модели."""
    target_dir = target_dir or get_models_dir()
    return (target_dir / filename).exists()


def get_model_path(filename: str) -> Path | None:
    """Возвращает путь к файлу модели, если он есть."""
    path = get_models_dir() / filename
    return path if path.exists() else None


def download_model(
    repo_id: str,
    filename: str,
    target_dir: Path | None = None,
) -> Path:
    """Скачивает один файл модели из Hugging Face.

    Args:
        repo_id: Идентификатор репозитория (например, "Qwen/Qwen2.5-7B-Instruct-GGUF")
        filename: Имя файла в репозитории
        target_dir: Каталог назначения (по умолчанию ~/.ollidesk/models)

    Returns:
        Path к скачанному файлу
    """
    from huggingface_hub import hf_hub_download

    target_dir = target_dir or get_models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    if target_path.exists():
        logger.info(f"Модель уже скачана: {target_path}")
        return target_path

    logger.info(f"Скачивание {filename} из {repo_id}...")
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(target_dir),
    )
    logger.info(f"Модель сохранена: {downloaded}")
    return Path(downloaded)


def delete_model(filename: str, target_dir: Path | None = None) -> bool:
    """Удаляет локальный файл модели.

    Returns:
        True если файл был удалён, False — если не было или ошибка.
    """
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
    """Возвращает (chat_path, embed_path) для активной SLM-модели.

    Chat-модель берётся из конфига (SLMConfig.selected_model), с fallback
    на legacy 1.5B, если конфиг недоступен или модель не найдена.
    """
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