"""Инициализация приложения OlliDesk."""

import sys

from loguru import logger
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, load_config
from core.exceptions import ConfigError
from core.logger import setup_logger
from core.utils import get_app_data_dir
from ui.dialogs.wizard import SetupWizard
from ui.main_window import MainWindow
from ui.theme import DARK_STYLESHEET, LIGHT_STYLESHEET


def _check_config_exists() -> bool:
    """Проверяет, существует ли файл конфигурации."""
    return (get_app_data_dir() / "config.yaml").exists()


def _load_config_or_wizard() -> AppConfig | None:
    """Загружает конфиг или показывает визард.

    Returns:
        AppConfig или None, если визард не был завершён
    """
    config_path = get_app_data_dir() / "config.yaml"

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
        log_dir = get_app_data_dir() / "logs"
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

        self._apply_theme(self.config.theme)

        self.main_window = MainWindow(self.config)

        # Применяем глобальные настройки
        if self.config.last_model:
            self.main_window.chat_panel.set_model(self.config.last_model)
        if self.config.last_mode:
            mode_map = {"chat": 0, "plan": 1, "agent": 2}
            idx = mode_map.get(self.config.last_mode, 0)
            self.main_window.chat_panel.mode_combo.setCurrentIndex(idx)

        self.main_window.theme_changed.connect(self._on_theme_changed)
        self.main_window.showMaximized()
        # self.main_window.show()
        logger.info("Приложение готово к работе")

        return self.app.exec()

    def _apply_theme(self, theme: str) -> None:
        """Применяет тему к приложению.

        Args:
            theme: 'light' или 'dark'
        """
        stylesheet = DARK_STYLESHEET if theme == "dark" else LIGHT_STYLESHEET
        self.app.setStyleSheet(stylesheet)
        # Для заголовка
        palette = self.app.palette()
        if theme == "dark":
            palette.setColor(palette.ColorRole.Window, QColor("#2d2d2d"))
            palette.setColor(palette.ColorRole.WindowText, QColor("#d4d4d4"))
            palette.setColor(palette.ColorRole.Base, QColor("#1e1e1e"))
            palette.setColor(palette.ColorRole.Text, QColor("#d4d4d4"))
        else:
            palette.setColor(palette.ColorRole.Window, QColor("#f0f0f0"))
            palette.setColor(palette.ColorRole.WindowText, QColor("#000000"))
            palette.setColor(palette.ColorRole.Base, QColor("#ffffff"))
            palette.setColor(palette.ColorRole.Text, QColor("#000000"))
        self.app.setPalette(palette)

    def _on_theme_changed(self, theme: str) -> None:
        """Обрабатывает смену темы от MainWindow.

        Args:
            theme: 'light' или 'dark'
        """
        self.config.theme = theme
        self._apply_theme(theme)
        from core.config import save_config
        save_config(self.config)


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
