"""Точка входа в OlliDesk."""

import sys
from pathlib import Path
from core.logger import setup_logger
from core.config import load_config, AppConfig
from core.exceptions import ConfigError


def main() -> int:
    """Главная функция приложения."""
    # Настройка логирования
    log_dir = Path.home() / ".ollidesk" / "logs"
    setup_logger(log_dir, level="INFO")

    from loguru import logger
    logger.info("Запуск OlliDesk...")

    # Загрузка конфигурации
    try:
        config = load_config()
        logger.info(f"Конфигурация загружена: {config.version}")
    except ConfigError as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        # TODO: Показать визард первого запуска
        return 1

    # TODO: Инициализация QApplication и главного окна
    logger.info("Приложение готово к работе")

    return 0


if __name__ == "__main__":
    sys.exit(main())
