"""Инициализация приложения OlliDesk."""

import sys
from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, load_config
from core.exceptions import ConfigError
from core.logger import setup_logger
from ui.dialogs.wizard import SetupWizard
from ui.main_window import MainWindow


def _check_config_exists() -> bool:
    """Проверяет, существует ли файл конфигурации."""
    return (Path.home() / ".ollidesk" / "config.yaml").exists()


def _load_config_or_wizard() -> AppConfig | None:
    """Загружает конфиг или показывает визард.

    Returns:
        AppConfig или None, если визард не был завершён
    """
    config_path = Path.home() / ".ollidesk" / "config.yaml"

    try:
        config = load_config()
        logger.info(f"Конфигурация загружена: {config.version}")
        return config
    except ConfigError as e:
        logger.warning(f"Конфигурация не найдена или невалидна: {e}")

        if _check_config_exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    import yaml
                    data = yaml.safe_load(f) or {}
                if data.get("wizard_dismissed"):
                    logger.info("Визард отключён пользователем, использую конфиг по умолчанию")
                    return AppConfig(
                        default_model="qwen2.5-coder:7b",
                        embed_model="nomic-embed-text",
                        wizard_dismissed=True,
                    )
            except Exception:
                pass

        logger.info("Запуск визарда первого запуска...")
        wizard = SetupWizard()
        if wizard.exec():
            try:
                return load_config()
            except ConfigError as e2:
                logger.error(f"Ошибка загрузки конфигурации после визарда: {e2}")
                return None
        return None


class OlliDeskApp:
    """Главный класс приложения."""

    def __init__(self) -> None:
        """Инициализация приложения."""
        self.app: QApplication | None = None
        self.config: AppConfig | None = None
        self.main_window: MainWindow | None = None

    def run(self) -> int:
        """
        Запускает приложение.

        Returns:
            int: Код выхода (0 — успех)
        """
        log_dir = Path.home() / ".ollidesk" / "logs"
        setup_logger(log_dir, level="INFO")
        logger.info("Запуск OlliDesk...")

        self.app = QApplication(sys.argv)
        self.app.setApplicationName("OlliDesk")
        self.app.setApplicationVersion("1.0.0")
        self.app.setOrganizationName("OlliDesk")

        self.config = _load_config_or_wizard()
        if self.config is None:
            logger.error("Визард отменён пользователем")
            return 1

        self.main_window = MainWindow(self.config)
        self.main_window.show()
        logger.info("Приложение готово к работе")

        return self.app.exec()


def main() -> int:
    """
    Главная функция приложения.

    Returns:
        int: Код выхода
    """
    app = OlliDeskApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
