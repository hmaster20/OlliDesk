"""Инициализация приложения OlliDesk."""

import sys

from loguru import logger
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from core._version import __version__
from core.config import AppConfig, load_config
from core.exceptions import ConfigError
from core.logger import setup_logger
from core.utils import get_app_data_dir
from ui.dialogs.wizard import SetupWizard
from ui.main_window import MainWindow


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

        logger.info("Запуск визарда для первого запуска...")
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
        self.app.setApplicationVersion(__version__)
        self.app.setApplicationName("OlliDesk")
        self.app.setOrganizationName("OlliDesk")

        self.config = _load_config_or_wizard()
        if self.config is None:
            logger.error("Визард отменён пользователем")
            return 1

        self._apply_theme(self.config.theme)

        self.main_window = MainWindow(self.config)

        # Применяем глобальные настройки
        if self.config.last_model:
            logger.info(f"Применяю last_model={self.config.last_model}")
            self.main_window.chat_panel.set_model(self.config.last_model)
        if self.config.last_mode:
            logger.info(f"Применяю last_mode={self.config.last_mode}")
            # 2.4: публичный API вместо прямого доступа к mode_combo.
            self.main_window.chat_panel.set_mode_by_name(self.config.last_mode)

        self.main_window.theme_changed.connect(self._on_theme_changed)
        self.main_window.showMaximized()
        logger.info("Приложение готово к работе")

        return self.app.exec()

    def _apply_theme(self, theme: str) -> None:
        """[9.1] Применяет тему из дизайн-системы (ui.theme).

        Палитра и QSS приходят из одного источника — `get_colors` и
        `get_stylesheet`. Дублирование цветов из QSS в палитре убрано.
        """
        if self.app is None:
            return
        from ui.theme import get_colors, get_stylesheet

        self.app.setStyleSheet(get_stylesheet(theme))
        c = get_colors(theme)
        palette = self.app.palette()
        palette.setColor(palette.ColorRole.Window, QColor(c.bg))
        palette.setColor(palette.ColorRole.WindowText, QColor(c.text))
        palette.setColor(palette.ColorRole.Base, QColor(c.surface))
        palette.setColor(palette.ColorRole.Text, QColor(c.text))
        palette.setColor(palette.ColorRole.Highlight, QColor(c.accent))
        palette.setColor(palette.ColorRole.HighlightedText, QColor(c.accent_text))
        palette.setColor(palette.ColorRole.Button, QColor(c.surface))
        palette.setColor(palette.ColorRole.ButtonText, QColor(c.text))
        self.app.setPalette(palette)

    def _on_theme_changed(self, theme: str) -> None:
        """Обрабатывает смену темы от MainWindow.
        Args:
            theme: 'light' или 'dark'
        """
        if self.config is None:
            return
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
