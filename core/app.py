"""Инициализация приложения."""

import sys

from loguru import logger
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from core._version import __version__
from core.config import AppConfig, load_config
from core.exceptions import ConfigError
from core.logger import setup_logger
from core.utils import get_app_data_dir, get_app_icon
from ui.dialogs.wizard import SetupWizard
from ui.main_window import MainWindow

def _run_wizard() -> AppConfig | None:
    """Показывает мастер настройки и загружает конфиг после его завершения."""
    wizard = SetupWizard()
    if wizard.exec():
        try:
            return load_config()
        except ConfigError as e:
            logger.error(f"Ошибка загрузки конфигурации после визарда: {e}")
            return None
    logger.warning("Визард отменён пользователем")
    return None

def _load_config_or_wizard() -> AppConfig | None:
    """Загружает конфиг или показывает визард первого запуска."""
    config_path = get_app_data_dir() / "config.yaml"

    # Явная проверка: файла нет → первый запуск, показываем мастер.
    if not config_path.exists():
        logger.info("Файл конфигурации не найден — первый запуск, показываю визард")
        return _run_wizard()

    try:
        config = load_config()
        logger.info(f"Конфигурация загружена: {config.version}")
        return config
    except ConfigError as e:
        logger.warning(f"Конфигурация невалидна: {e}")
        return _run_wizard()
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

        from PySide6.QtCore import QLibraryInfo, QTranslator

        # Русификация встроенных диалогов Qt (QFileDialog, кнопки QMessageBox и т.п.).
        # PySide6 поставляется с переводами в PySide6/translations.
        self._translator = QTranslator(self.app)
        if self._translator.load(
            "qtbase_ru",
            QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
        ):
            self.app.installTranslator(self._translator)
        else:
            logger.debug("Перевод qtbase_ru не найден — стандартные диалоги Qt останутся английскими")

        # Иконка приложения — до создания любых окон, иначе SetupWizard (в _load_config_or_wizard) откроется без неё.
        # QApplication.setWindowIcon распространяется на все QDialog (QMessageBox, QFileDialog, *Dialog) автоматически.
        icon = get_app_icon()
        if icon is not None:
            self.app.setWindowIcon(icon)

        self.app.setApplicationVersion(__version__)
        self.app.setApplicationName("OlliDesk")
        self.app.setOrganizationName("OlliDesk")

        initial_data_dir = get_app_data_dir()
        self.config = _load_config_or_wizard()
        if self.config is None:
            logger.error("Визард отменён пользователем")
            return 1

        # Визард мог сменить каталог данных — переинициализируем
        # логгер, чтобы логи писались уже в новый каталог.
        if get_app_data_dir() != initial_data_dir:
            logger.remove()
            setup_logger(get_app_data_dir() / "logs", level="INFO")
            logger.info(f"Каталог данных изменён визардом: {get_app_data_dir()}")

        self._apply_theme(self.config.theme)

        self.main_window = MainWindow(self.config)

        ollama_enabled = "ollama" in self.config.backends.enabled

        if self.config.last_model and ollama_enabled:
            logger.info(f"Применяю last_model={self.config.last_model}")
            self.main_window.chat_panel.set_model(self.config.last_model)
        elif self.config.last_model:
            logger.info(
                f"last_model={self.config.last_model!r} не применяется: "
                f"Ollama выключена (backends.enabled={self.config.backends.enabled}). "
                f"SLM использует selected_model={self.config.slm.selected_model!r}."
            )

        if self.config.last_mode:
            logger.info(f"Применяю last_mode={self.config.last_mode}")
            self.main_window.chat_panel.set_mode_by_name(self.config.last_mode)

        # Сигнал из BackendsMixin._detect_and_load_slm: первый запуск / нет конфига
        self.main_window.chat_panel.wizard_requested.connect(self._run_wizard)

        self.main_window.theme_changed.connect(self._on_theme_changed)
        self.main_window.showMaximized()
        logger.info("Приложение готово к работе")

        return self.app.exec()

    def _apply_theme(self, theme: str) -> None:
        """Применяет тему из дизайн-системы (ui.theme).

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


    def _run_wizard(self) -> None:
        """Показывает мастер настройки по запросу из ChatPanel (первый запуск, нет моделей)."""
        logger.info("Запуск визарда по запросу из ChatPanel...")
        wizard = SetupWizard()
        if wizard.exec():
            try:
                config = load_config()
                logger.info(f"Конфигурация обновлена после визарда: {config.version}")
                # Перезагружаем конфиг в MainWindow/ChatPanel, чтобы они подхватили
                # новые модели/бэкенды без перезапуска приложения.
                if self.main_window is not None:
                    self.main_window.chat_panel.reload_slm()
            except Exception as e:
                logger.error(f"Ошибка загрузки конфигурации после визарда: {e}")
        else:
            logger.warning("Визард отменён пользователем")


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
