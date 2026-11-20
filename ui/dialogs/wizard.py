"""Визард первого запуска OlliDesk.

Страницы:
  1. Приветствие и выбор темы (по умолчанию — тёмная).
  2. Расположение данных (Вариант А: каталог можно сменить; выбор
     сохраняется в файл-указатель %APPDATA%/OlliDesk/data_dir.txt).
  3. Поставщик моделей: локальные СЛМ (по умолчанию) / Оллама / ЛМ-студия.
  4. Подтверждение.
"""
import shutil
from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from core.config import AppConfig, save_config
from core.utils import get_app_data_dir, set_app_data_dir


def _disk_free_gb(path: Path) -> float | None:
    """Свободное место (ГБ) на томе, где лежит `path`; Нон — если недоступно."""
    try:
        anchor = path
        while not anchor.exists() and anchor.parent != anchor:
            anchor = anchor.parent
        return shutil.disk_usage(anchor).free / (1024**3)
    except OSError:
        return None


def _format_free_gb(path: Path) -> str:
    free = _disk_free_gb(path)
    return f"{free:.1f} ГБ" if free is not None else "?"

class SetupWizard(QWizard):
    """Визард первого запуска: тема + данные + бэкенд + подтверждение."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OlliDesk — Первоначальная настройка")
        self.setMinimumSize(640, 500)

        self._selected_theme: str = "dark"
        self._data_dir: Path = get_app_data_dir()
        self._models_dir: Path = get_app_data_dir() / "models"
        self._selected_backend: str = "local_slm"
        self._ollama_url: str = "http://localhost:11434"

        self._apply_theme(self._selected_theme)
        self._setup_pages()

    def _setup_pages(self) -> None:
        self.addPage(WelcomeThemePage(self))
        self.addPage(DataLocationPage(self))
        self.addPage(BackendSelectionPage(self))
        self.addPage(ConfirmationPage(self))

    def _apply_theme(self, theme: str) -> None:
        try:
            from PySide6.QtWidgets import QApplication

            from ui.theme import DARK_COLORS, get_stylesheet
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(get_stylesheet(theme))
            self._selected_theme = theme

            # Принудительная стилизация QWizard (обход белых нативных стилей Windows)
            if theme == "dark":
                self.setStyleSheet(f"""
                    QWizard {{
                        background-color: {DARK_COLORS.bg};
                        color: {DARK_COLORS.text};
                    }}
                    QWizard QLabel {{
                        color: {DARK_COLORS.text};
                        background: transparent;
                    }}
                    QWizard QLineEdit {{
                        background-color: {DARK_COLORS.surface};
                        color: {DARK_COLORS.text};
                        border: 1px solid {DARK_COLORS.border};
                        border-radius: 4px;
                        padding: 4px 8px;
                    }}
                    QWizard QPushButton {{
                        background-color: {DARK_COLORS.surface};
                        color: {DARK_COLORS.text};
                        border: 1px solid {DARK_COLORS.border};
                        border-radius: 4px;
                        padding: 6px 16px;
                        min-height: 22px;
                    }}
                    QWizard QPushButton:hover {{
                        background-color: {DARK_COLORS.surface_hover};
                        border-color: {DARK_COLORS.border_strong};
                    }}
                    QWizard QPushButton:pressed {{
                        background-color: {DARK_COLORS.surface_pressed};
                    }}
                    QWizard QRadioButton, QWizard QCheckBox {{
                        color: {DARK_COLORS.text};
                        spacing: 8px;
                        background: transparent;
                    }}
                    QWizard QTextBrowser {{
                        background-color: {DARK_COLORS.surface};
                        color: {DARK_COLORS.text};
                        border: 1px solid {DARK_COLORS.border};
                    }}
                """)
        except Exception as e:
            logger.debug(f"Не удалось применить тему в визарде: {e}", exc_info=True)

    def set_data_dir(self, path: Path) -> None:
        self._data_dir = path

    def accept(self) -> None:
        try:
            if self._data_dir.resolve() != get_app_data_dir().resolve():
                set_app_data_dir(self._data_dir)
                from core.config import config_loader
                config_loader.refresh_path()

            # Сохраняем кастомный каталог моделей, если он отличается от дефолтного
            default_models_dir = self._data_dir / "models"
            if self._models_dir.resolve() != default_models_dir.resolve():
                try:
                    pointer_file = self._data_dir / "models_dir.txt"
                    pointer_file.write_text(str(self._models_dir), encoding="utf-8")
                    logger.info(f"Указатель каталога моделей сохранен: {pointer_file}")
                except Exception as e:
                    logger.warning(f"Не удалось сохранить models_dir.txt: {e}")

            config = self._build_config()
            save_config(config)
            logger.info("Конфигурация создана визардом первого запуска")
            super().accept()
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфигурацию:\n{e}")

    def _build_config(self) -> AppConfig:
        if self._selected_backend == "ollama":
            enabled = ["ollama", "local_slm"]
            priority = ["ollama", "local_slm"]
        else:
            enabled = ["local_slm"]
            priority = ["local_slm", "ollama"]

        pages = self.findChildren(ConfirmationPage)
        dismissed = bool(pages[0].dismiss_checkbox.isChecked()) if pages else True
        url = self._ollama_url if self._selected_backend == "ollama" else "http://localhost:11434"

        return AppConfig(
            theme=self._selected_theme,
            default_model="qwen2.5-3b-instruct-q4_k_m",
            embed_model="nomic-embed-text",
            recent_projects=[],
            wizard_dismissed=dismissed,
            backends={"enabled": enabled, "priority": priority, "ollama_url": url},
        )

class WelcomeThemePage(QWizardPage):
    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Добро пожаловать в OlliDesk")
        self.setSubTitle("Локальный AI-ассистент. Давайте настроим его под вас.")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "OlliDesk работает на <b>локальных моделях</b>. Сейчас мы:\n"
            "  • выберем тему оформления;\n"
            "  • покажем, где будут храниться данные и модели;\n"
            "  • выберем поставщика моделей;\n"
            "  • сохраним конфигурацию.\n\n"
            "Загрузку моделей можно выполнить позже через «Вид → Локальные модели»."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(12)
        layout.addWidget(QLabel("<b>Тема оформления</b>"))

        self.dark_radio = QRadioButton("Тёмная (рекомендуется)")
        self.light_radio = QRadioButton("Светлая")
        self.dark_radio.setChecked(True)
        self.dark_radio.toggled.connect(self._on_theme_toggled)
        self.light_radio.toggled.connect(self._on_theme_toggled)

        layout.addWidget(self.dark_radio)
        layout.addWidget(self.light_radio)
        layout.addStretch()

    def _on_theme_toggled(self, _checked: bool) -> None:
        theme = "dark" if self.dark_radio.isChecked() else "light"
        self.wizard_ref._apply_theme(theme)

class DataLocationPage(QWizardPage):
    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Расположение данных")
        self.setSubTitle("Где OlliDesk хранит настройки, журналы и модели.")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Каталог данных (профиль)</b>"))
        row = QHBoxLayout()
        self.data_edit = QLineEdit(str(get_app_data_dir()))
        row.addWidget(self.data_edit)
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._browse_data_dir)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        layout.addWidget(QLabel("Настройки, журналы, сессии, память. <i>Можно изменить позже.</i>"))
        layout.addSpacing(10)

        # Каталог моделей SLM (с кнопкой Обзор)
        layout.addWidget(QLabel("<b>Каталог моделей SLM</b>"))
        models_row = QHBoxLayout()
        self.models_edit = QLineEdit(str(get_app_data_dir() / "models"))
        models_row.addWidget(self.models_edit)
        models_browse_btn = QPushButton("Обзор…")
        models_browse_btn.clicked.connect(self._browse_models_dir)
        models_row.addWidget(models_browse_btn)
        layout.addLayout(models_row)
        layout.addWidget(QLabel("Сюда скачиваются локальные модели (.gguf)."))
        layout.addSpacing(10)

        self.free_label = QLabel()
        layout.addWidget(self.free_label)

        warn = QLabel(
            "⚠️ <b>Важно про размер моделей.</b><br>"
            "Локальные модели занимают от ~1 до 20+ ГБ каждая.<br>"
            "Убедитесь, что на диске достаточно места."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #e5c07b;")
        layout.addWidget(warn)
        layout.addStretch()
        self._refresh_free()

    def _browse_data_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Каталог данных", str(self.data_edit.text()))
        if folder:
            self.data_edit.setText(folder)
            self._refresh_free()

    def _browse_models_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Каталог моделей SLM", str(self.models_edit.text()))
        if folder:
            self.models_edit.setText(folder)
            self.wizard_ref._models_dir = Path(folder)

    def _refresh_free(self) -> None:
        path = Path(self.data_edit.text())
        free = _format_free_gb(path)
        self.free_label.setText(f"Свободного места на диске: <b>{free}</b>")
        # Если каталог моделей не был изменен вручную, он следует за каталогом данных
        if self.wizard_ref._models_dir == self.wizard_ref._data_dir / "models":
            self.models_edit.setText(str(path / "models"))
            self.wizard_ref._models_dir = path / "models"

    def validatePage(self) -> bool:
        chosen = Path(self.data_edit.text()).expanduser()
        if not chosen.is_absolute():
            QMessageBox.warning(self, "Ошибка", "Укажите абсолютный путь к каталогу данных.")
            return False
        self.wizard_ref.set_data_dir(chosen)

        models_chosen = Path(self.models_edit.text()).expanduser()
        if not models_chosen.is_absolute():
            QMessageBox.warning(self, "Ошибка", "Укажите абсолютный путь к каталогу моделей.")
            return False
        self.wizard_ref._models_dir = models_chosen
        return True

class BackendSelectionPage(QWizardPage):
    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Поставщик моделей")
        self.setSubTitle("Какой бэкенд использовать для генерации ответов.")
        layout = QVBoxLayout(self)

        self.slm_radio = QRadioButton("Локальные модели SLM (рекомендуется)")
        self.ollama_radio = QRadioButton("Ollama")
        self.lmstudio_radio = QRadioButton("LM Studio")

        self.slm_radio.setChecked(True)
        self.slm_radio.toggled.connect(self._on_backend_toggled)
        self.ollama_radio.toggled.connect(self._on_backend_toggled)
        self.lmstudio_radio.toggled.connect(self._on_backend_toggled)

        layout.addWidget(self.slm_radio)
        layout.addWidget(self.ollama_radio)
        layout.addWidget(self.lmstudio_radio)
        layout.addSpacing(12)

        self.ollama_url_label = QLabel("Ollama URL:")
        self.ollama_url_edit = QLineEdit("http://localhost:11434")
        url_row = QHBoxLayout()
        url_row.addWidget(self.ollama_url_label)
        url_row.addWidget(self.ollama_url_edit)
        layout.addLayout(url_row)

        self.lmstudio_note = QLabel("⚠️ Поддержка LM Studio находится <b>в разработке</b>.")
        self.lmstudio_note.setWordWrap(True)
        self.lmstudio_note.setStyleSheet("color: #e5c07b;")
        layout.addWidget(self.lmstudio_note)
        layout.addStretch()
        self._sync_visibility()

    def _on_backend_toggled(self, _checked: bool) -> None:
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        ollama_on = self.ollama_radio.isChecked()
        self.ollama_url_label.setVisible(ollama_on)
        self.ollama_url_edit.setVisible(ollama_on)
        self.lmstudio_note.setVisible(self.lmstudio_radio.isChecked())

    def validatePage(self) -> bool:
        if self.lmstudio_radio.isChecked():
            QMessageBox.warning(self, "В разработке", "Поддержка LM Studio пока в разработке.")
            return False
        if self.ollama_radio.isChecked():
            self.wizard_ref._selected_backend = "ollama"
            self.wizard_ref._ollama_url = self.ollama_url_edit.text().strip()
        else:
            self.wizard_ref._selected_backend = "local_slm"
        return True

class ConfirmationPage(QWizardPage):
    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Подтверждение")
        self.setSubTitle("Проверьте настройки перед сохранением.")
        layout = QVBoxLayout(self)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.dismiss_checkbox = QCheckBox("Больше не показывать этот мастер при запуске")
        self.dismiss_checkbox.setChecked(True)
        layout.addWidget(self.dismiss_checkbox)
        layout.addStretch()

    def initializePage(self) -> None:
        theme_name = "Тёмная" if self.wizard_ref._selected_theme == "dark" else "Светлая"
        backend_names = {"local_slm": "Локальные модели SLM", "ollama": "Ollama", "lmstudio": "LM Studio"}
        backend_name = backend_names.get(self.wizard_ref._selected_backend, "Локальные модели SLM")
        data_dir = self.wizard_ref._data_dir
        models_dir = self.wizard_ref._models_dir

        lines = [
            f"<b>Тема оформления:</b> {theme_name}",
            f"<b>Каталог данных:</b> {data_dir}",
            f"<b>Каталог моделей:</b> {models_dir}",
            f"<b>Поставщик моделей:</b> {backend_name}",
        ]
        if self.wizard_ref._selected_backend == "ollama":
            lines.append(f"<b>Ollama URL:</b> {self.wizard_ref._ollama_url}")
        lines.append("<br>Конфигурация будет сохранена в каталог данных.")
        self.summary_label.setText("<br>".join(lines))