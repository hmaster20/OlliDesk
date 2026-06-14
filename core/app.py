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

        try:
            self.config = load_config()
            logger.info(f"Конфигурация загружена: {self.config.version}")
        except ConfigError as e:
            logger.warning(f"Конфигурация не найдена или невалидна: {e}")
            logger.info("Запуск визарда первого запуска...")
            if not self._run_wizard():
                logger.error("Визард отменен пользователем")
                return 1

        assert self.config is not None
        self.main_window = MainWindow(self.config)
        self.main_window.show()
        logger.info("Приложение готово к работе")

        return self.app.exec()

    def _run_wizard(self) -> bool:
        """
        Запускает визард первого запуска.

        Returns:
            bool: True, если визард успешно завершен
        """
        wizard = SetupWizard()
        if wizard.exec():
            try:
                self.config = load_config()
                logger.info("Конфигурация создана и загружена")
                return True
            except ConfigError as e:
                logger.error(f"Ошибка загрузки конфигурации после визарда: {e}")
                return False
        return False


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
