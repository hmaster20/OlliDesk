"""Скачивание встроенной SLM-модели через huggingface-hub.

Модель: Qwen2.5-1.5B-Instruct (GGUF, Q4_K_M, ~1.1 ГБ).
Оптимальна для CPU-инференса на машинах с 4 ГБ VRAM и большим объёмом RAM.
"""

from pathlib import Path

from loguru import logger

# CHAT_MODEL_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
# CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

CHAT_MODEL_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
CHAT_MODEL_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"

EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"


def download_chat_model(
    target_dir: Path | None = None,
    repo_id: str = CHAT_MODEL_REPO,
    filename: str = CHAT_MODEL_FILE,
) -> Path:
    """Скачивает chat-модель Qwen2.5-1.5B-Instruct (GGUF).

    Args:
        target_dir: Директория для сохранения (по умолчанию ~/.ollidesk/models)
        repo_id: HuggingFace репозиторий
        filename: Имя файла в репозитории

    Returns:
        Path: Путь к скачанному файлу
    """
    from huggingface_hub import hf_hub_download

    target_dir = target_dir or DEFAULT_MODELS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    if target_path.exists():
        logger.info(f"Chat-модель уже скачана: {target_path}")
        return target_path

    logger.info(f"Скачивание chat-модели {filename} ({repo_id})...")
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(target_dir),
    )
    logger.info(f"Chat-модель сохранена: {downloaded}")
    return Path(downloaded)


def download_embed_model(
    target_dir: Path | None = None,
    repo_id: str = EMBED_MODEL_REPO,
    filename: str = EMBED_MODEL_FILE,
) -> Path | None:
    """Скачивает embedding-модель (опционально).

    Returns:
        Path или None при ошибке
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        logger.warning("huggingface-hub не установлен, embedding-модель не будет скачана")
        return None

    target_dir = target_dir or DEFAULT_MODELS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = target_dir / filename
    if target_path.exists():
        logger.info(f"Embedding-модель уже скачана: {target_path}")
        return target_path

    try:
        logger.info(f"Скачивание embedding-модели {filename}...")
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(target_dir),
        )
        logger.info(f"Embedding-модель сохранена: {downloaded}")
        return Path(downloaded)
    except Exception as e:
        logger.warning(f"Не удалось скачать embedding-модель: {e}")
        return None


def get_model_paths() -> tuple[Path | None, Path | None]:
    """Возвращает пути к моделям, если они существуют."""
    chat_path = DEFAULT_MODELS_DIR / CHAT_MODEL_FILE
    embed_path = DEFAULT_MODELS_DIR / EMBED_MODEL_FILE

    chat = chat_path if chat_path.exists() else None
    embed = embed_path if embed_path.exists() else None
    return chat, embed


def main() -> None:
    """CLI-точка входа: скачать обе модели."""
    chat = download_chat_model()
    print(f"Chat-модель: {chat}")

    embed = download_embed_model()
    if embed:
        print(f"Embedding-модель: {embed}")
    else:
        print("Embedding-модель не скачана (опционально)")


if __name__ == "__main__":
    main()