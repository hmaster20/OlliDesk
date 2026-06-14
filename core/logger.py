"""Настройка логирования через loguru."""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_dir: Path, level: str = "INFO") -> None:
    """
    Настраивает loguru с ротацией 10MB, хранение 7 дней.

    Args:
        log_dir: Папка для логов
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ollidesk_{time:YYYY-MM-DD}.log"

    # Удаляем дефолтный handler
    logger.remove()

    # Консольный вывод
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # Файловый вывод с ротацией
    logger.add(
        log_file,
        rotation="10 MB",
        retention="7 days",
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
