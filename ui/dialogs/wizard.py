"""Визард первого запуска OlliDesk.

Полностью тёмная (или светлая) тема без нативных светлых полос:
ClassicStyle + QSS из токенов ui.theme + палитра QPalette.
Свободное место показывается отдельно для диска каталога данных и
диска каталога моделей («облачко» на строке поля). При свободном
месте < MODELS_MIN_FREE_GB для моделей — жёлтое предупреждение.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import override

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
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

# Минимум свободного места на диске каталога моделей, ГБ.
MODELS_MIN_FREE_GB = 20.0


# ============================================================================
# Диски: свободное место и имя диска для конкретного пути
# ============================================================================
def _disk_anchor(path: Path) -> Path:
    """Ближайший существующий предок пути (для shutil.disk_usage)."""
    anchor = path
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    return anchor


def _disk_free_gb(path: Path) -> float | None:
    try:
        return shutil.disk_usage(_disk_anchor(path)).free / (1024**3)
    except OSError:
        return None


def _disk_label(path: Path) -> str:
    """Имя диска: 'C:' в Windows, точка монтирования в POSIX."""
    if path.drive:
        return path.drive
    anchor = _disk_anchor(path)
    while anchor.parent != anchor and not os.path.ismount(anchor):
        anchor = anchor.parent
    return str(anchor)


def _rgba(hex_color: str, alpha: float) -> str:
    """#rrggbb -> rgba(r, g, b, a) для QSS."""
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


class DiskFreePill(QLabel):
    """Капсула-«облачко»: диск и свободное место для конкретного пути.

    Стилизуется через QSS визарда: objectName=freePill + свойство state
    (ok / warn / unknown). Фон чуть темнее окна (токен code_bg).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("freePill")
        self.setProperty("state", "unknown")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(160)

    def refresh(self, path: Path, warn_below_gb: float | None = None) -> float | None:
        """Обновляет капсулу; возвращает свободное место в ГБ (или None)."""
        absolute = path.is_absolute()
        free = _disk_free_gb(path) if absolute else None
        label = _disk_label(path) if absolute else "?"
        if free is None:
            state = "unknown"
            text = f"{label} · место: ?"
            tip = "Не удалось определить свободное место на диске."
        elif warn_below_gb is not None and free < warn_below_gb:
            state = "warn"
            text = f"⚠ {label} · {free:.1f} ГБ"
            tip = (
                f"На диске {label} свободно {free:.1f} ГБ — меньше "
                f"рекомендуемых {warn_below_gb:.0f} ГБ. Выберите другой диск."
            )
        else:
            state = "ok"
            text = f"{label} · свободно {free:.1f} ГБ"
            tip = f"Свободно {free:.1f} ГБ на диске {label}."
        self.setText(text)
        self.setToolTip(tip)
        if self.property("state") != state:
            self.setProperty("state", state)
            # Переприменение QSS после смены динамического свойства.
            self.style().unpolish(self)
            self.style().polish(self)
        return free


# ============================================================================
# Визард
# ============================================================================
class SetupWizard(QWizard):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OlliDesk — Первоначальная настройка")
        self.setMinimumSize(700, 540)

        self._selected_theme: str = "dark"
        self._data_dir: Path = get_app_data_dir()
        self._models_dir: Path = self._data_dir / "models"
        self._selected_backend: str = "local_slm"
        self._ollama_url: str = "http://localhost:11434"

        # ClassicStyle: без нативных Aero-полос Windows (светлый низ/баннер),
        # вся отрисовка подчиняется QSS и палитре.
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.WizardButton.BackButton, "< Назад")
        self.setButtonText(QWizard.WizardButton.NextButton, "Далее >")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Готово")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Отмена")

        self._apply_theme(self._selected_theme)
        self._confirmation_page: ConfirmationPage | None = None
        self._setup_pages()

    def _setup_pages(self) -> None:
        self.addPage(WelcomeThemePage(self))
        self.addPage(DataLocationPage(self))
        self.addPage(BackendSelectionPage(self))
        self._confirmation_page = ConfirmationPage(self)
        self.addPage(self._confirmation_page)

    # ------------------------------------------------------------------ тема
    def _apply_theme(self, theme: str) -> None:
        try:
            from ui.theme import RADIUS, SPACE, TYPO, get_colors, get_stylesheet

            c = get_colors(theme)
            app = QApplication.instance()
            if isinstance(app, QApplication) and app is not None:
                app.setStyleSheet(get_stylesheet(theme))
            self._selected_theme = theme
            self.setPalette(self._build_palette(c))
            self.setStyleSheet(self._wizard_qss(c, TYPO, SPACE, RADIUS))
            self._apply_dark_titlebar(theme == "dark")
        except Exception as e:
            logger.debug(f"Не удалось применить тему в визарде: {e}", exc_info=True)

    @staticmethod
    def _build_palette(c) -> QPalette:
        """Палитра из токенов: закрывает нативную отрисовку (разделители и т.п.)."""
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c.bg))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c.text))
        pal.setColor(QPalette.ColorRole.Base, QColor(c.surface))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c.surface_alt))
        pal.setColor(QPalette.ColorRole.Text, QColor(c.text))
        pal.setColor(QPalette.ColorRole.Button, QColor(c.surface))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(c.text))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c.surface_alt))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c.text))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c.selection_bg))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c.selection_text))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c.text_muted))
        pal.setColor(QPalette.ColorRole.Link, QColor(c.accent))
        # Роли, из которых стиль рисует «etched»-линии: без них будут светлыми.
        pal.setColor(QPalette.ColorRole.Light, QColor(c.border_strong))
        pal.setColor(QPalette.ColorRole.Midlight, QColor(c.border))
        pal.setColor(QPalette.ColorRole.Mid, QColor(c.border))
        pal.setColor(QPalette.ColorRole.Dark, QColor(c.border))
        pal.setColor(QPalette.ColorRole.Shadow, QColor(c.bg))
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.text_disabled))
        return pal

    @staticmethod
    def _wizard_qss(c, t, s, r) -> str:
        """QSS визарда из токенов темы. Порядок правил важен: общее -> частное."""
        return f"""
            QWizard {{
                background-color: {c.bg};
                color: {c.text};
            }}
            /* Страховка от светлых полос: все дочерние виджеты на фоне темы. */
            QWizard QWidget {{
                background-color: {c.bg};
                color: {c.text};
            }}
            QWizard QLabel {{
                background: transparent;
                color: {c.text};
            }}
            QWizard QLabel#titleLabel {{
                color: {c.header_text};
                font-size: {t.size_heading}px;
                font-weight: {t.weight_bold};
            }}
            QWizard QLabel#subTitleLabel {{
                color: {c.text_muted};
            }}
            QWizard QLabel#warnNote {{
                color: {c.warning};
                background: transparent;
            }}

            /* «Облачко» свободного места: чуть темнее окна, капсула. */
            QWizard QLabel#freePill {{
                background-color: {c.code_bg};
                color: {c.text_muted};
                border: 1px solid {c.border};
                border-radius: {r.xl}px;
                padding: {s.xs + 1}px {s.lg}px;
            }}
            QWizard QLabel#freePill[state="ok"] {{
                color: {c.text};
            }}
            QWizard QLabel#freePill[state="warn"] {{
                background-color: {_rgba(c.warning, 0.14)};
                color: {c.warning};
                border-color: {c.warning};
            }}

            /* Поля ввода */
            QWizard QLineEdit {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
                padding: {s.sm}px {s.md}px;
                selection-background-color: {c.selection_bg};
                selection-color: {c.selection_text};
            }}
            QWizard QLineEdit:focus {{
                border-color: {c.accent};
            }}

            /* Кнопки, включая навигацию визарда и :disabled */
            QWizard QPushButton {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
                padding: {s.sm + 2}px {s.xl}px;
                min-height: 22px;
                min-width: 80px;
            }}
            QWizard QPushButton:hover {{
                background-color: {c.surface_hover};
                border-color: {c.border_strong};
            }}
            QWizard QPushButton:pressed {{
                background-color: {c.surface_pressed};
            }}
            QWizard QPushButton:disabled {{
                background-color: {c.surface_alt};
                color: {c.text_disabled};
                border-color: {c.border};
            }}
            QWizard QPushButton:default {{
                background-color: {c.accent};
                color: {c.accent_text};
                border-color: {c.accent_pressed};
            }}
            QWizard QPushButton:default:hover {{
                background-color: {c.accent_hover};
            }}

            /* CheckBox / RadioButton + индикаторы */
            QWizard QRadioButton, QWizard QCheckBox {{
                background: transparent;
                color: {c.text};
                spacing: {s.md}px;
            }}
            QWizard QCheckBox::indicator, QWizard QRadioButton::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {c.border_strong};
                background-color: {c.surface};
            }}
            QWizard QCheckBox::indicator {{
                border-radius: {r.sm}px;
            }}
            QWizard QRadioButton::indicator {{
                border-radius: 7px;
            }}
            QWizard QCheckBox::indicator:hover, QWizard QRadioButton::indicator:hover {{
                border-color: {c.accent};
            }}
            QWizard QCheckBox::indicator:checked, QWizard QRadioButton::indicator:checked {{
                background-color: {c.accent};
                border-color: {c.accent_pressed};
            }}
            QWizard QCheckBox::indicator:disabled, QWizard QRadioButton::indicator:disabled {{
                background-color: {c.surface_alt};
                border-color: {c.border};
            }}

            /* Немодальные QFileDialog (DontUseNativeOption) — тоже тёмные */
            QWizard QAbstractItemView {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                selection-background-color: {c.selection_bg};
                selection-color: {c.selection_text};
            }}
            QWizard QComboBox {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
                padding: {s.xs}px {s.sm}px;
            }}
            QWizard QHeaderView::section {{
                background-color: {c.header_bg};
                color: {c.header_text};
                border: none;
                border-right: 1px solid {c.border};
                border-bottom: 1px solid {c.border};
                padding: {s.xs}px {s.sm}px;
            }}
            QWizard QToolButton {{
                background-color: {c.surface};
                color: {c.text};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
                padding: {s.sm}px;
            }}
            QWizard QToolButton:hover {{
                background-color: {c.surface_hover};
            }}
        """

    def _apply_dark_titlebar(self, dark: bool) -> None:
        """Тёмный заголовок окна в Windows 10/11 (DWM) — без светлой полосы сверху."""
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            import ctypes

            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ]
            dwm.DwmSetWindowAttribute.restype = ctypes.c_int32
            hwnd = ctypes.c_void_p(int(self.winId()))
            value = ctypes.c_int(1 if dark else 0)
            # 20 — Win10 20H1+/Win11; 19 — Win10 1809–1909.
            for attr in (20, 19):
                if dwm.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
        except Exception as e:
            logger.debug(f"Не удалось применить тёмный заголовок окна: {e}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_dark_titlebar(self._selected_theme == "dark")

    # ------------------------------------------------------------- сохранение
    def set_data_dir(self, path: Path) -> None:
        self._data_dir = path

    def accept(self) -> None:
        try:
            if self._data_dir.resolve() != get_app_data_dir().resolve():
                set_app_data_dir(self._data_dir)
                from core.config import config_loader

                config_loader.refresh_path()
            self._data_dir.mkdir(parents=True, exist_ok=True)

            # Указатель кастомного каталога моделей; если выбран дефолтный —
            # убираем устаревший указатель от прошлых запусков.
            pointer_file = self._data_dir / "models_dir.txt"
            default_models_dir = self._data_dir / "models"
            if self._models_dir.resolve() != default_models_dir.resolve():
                pointer_file.write_text(str(self._models_dir), encoding="utf-8")
                logger.info(f"Указатель каталога моделей сохранён: {pointer_file}")
            else:
                pointer_file.unlink(missing_ok=True)

            config = self._build_config()
            save_config(config)
            logger.info("Конфигурация создана визардом первого запуска")
            super().accept()
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфигурацию:\n{e}")

    def _build_config(self) -> AppConfig:
        """Собирает AppConfig из выборов, сделанных на страницах визарда."""
        if self._selected_backend == "ollama":
            enabled = ["ollama", "local_slm"]
            priority = ["ollama", "local_slm"]
        else:
            enabled = ["local_slm"]
            priority = ["local_slm", "ollama"]

        # Ссылка на страницу сохраняется в _setup_pages() — без поиска по дереву.
        dismissed = (
            self._confirmation_page.dismiss_checkbox.isChecked()
            if self._confirmation_page is not None
            else True
        )

        url = self._ollama_url if self._selected_backend == "ollama" else "http://localhost:11434"
        return AppConfig(
            theme=self._selected_theme,
            default_model="qwen2.5-3b-instruct-q4_k_m",
            embed_model="nomic-embed-text",
            recent_projects=[],
            wizard_dismissed=dismissed,
            backends={"enabled": enabled, "priority": priority, "ollama_url": url},
        )


# ============================================================================
# Страницы
# ============================================================================
class WelcomeThemePage(QWizardPage):
    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Добро пожаловать в OlliDesk")
        self.setSubTitle("Локальный AI-ассистент. Давайте настроим его под вас.")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "OlliDesk работает на <b>локальных моделях</b>. Сейчас мы:<br>"
            "&nbsp;&nbsp;• выберем тему оформления;<br>"
            "&nbsp;&nbsp;• укажем, где хранить данные и модели;<br>"
            "&nbsp;&nbsp;• выберем поставщика моделей;<br>"
            "&nbsp;&nbsp;• сохраним конфигурацию.<br><br>"
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
        # Пока пользователь не трогал каталог моделей — он следует за каталогом данных.
        self._models_manual = False

        self.setTitle("Расположение данных")
        self.setSubTitle("Где OlliDesk хранит настройки, журналы, ChromaDB и модели.")
        layout = QVBoxLayout(self)

        # --- Каталог данных ---
        layout.addWidget(QLabel("<b>Расположение данных (профиль)</b>"))
        row = QHBoxLayout()
        self.data_edit = QLineEdit(str(wizard._data_dir))
        self.data_edit.textChanged.connect(self._on_data_changed)
        row.addWidget(self.data_edit, 1)
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._browse_data_dir)
        row.addWidget(browse_btn)
        self.data_pill = DiskFreePill()
        row.addWidget(self.data_pill)
        layout.addLayout(row)
        layout.addWidget(QLabel("Настройки, журналы, сессии, память. <i>Можно изменить позже.</i>"))
        layout.addSpacing(10)

        # --- Каталог моделей ---
        layout.addWidget(QLabel("<b>Расположение малых языковых моделей (SLM)</b>"))
        models_row = QHBoxLayout()
        self.models_edit = QLineEdit(str(wizard._models_dir))
        self.models_edit.textChanged.connect(self._on_models_changed)
        self.models_edit.textEdited.connect(self._on_models_edited)
        models_row.addWidget(self.models_edit, 1)
        models_browse_btn = QPushButton("Обзор…")
        models_browse_btn.clicked.connect(self._browse_models_dir)
        models_row.addWidget(models_browse_btn)
        self.models_pill = DiskFreePill()
        models_row.addWidget(self.models_pill)
        layout.addLayout(models_row)
        layout.addWidget(QLabel("Сюда скачиваются локальные модели (.gguf)."))

        # Динамическое предупреждение: < 20 ГБ на диске моделей.
        self.models_warn = QLabel()
        self.models_warn.setObjectName("warnNote")
        self.models_warn.setWordWrap(True)
        self.models_warn.hide()
        layout.addWidget(self.models_warn)
        layout.addSpacing(10)

        note = QLabel(
            "⚠️ <b>Важно про размер моделей.</b><br>"
            "Локальные модели занимают от ~1 до 20+ ГБ каждая.<br>"
            f"Для каталога моделей рекомендуется минимум {MODELS_MIN_FREE_GB:.0f} ГБ свободного места."
        )
        note.setWordWrap(True)
        note.setObjectName("warnNote")
        layout.addWidget(note)
        layout.addStretch()

        self._refresh_free()

    # -------------------------------------------------------------- обработчики
    def _on_data_changed(self, text: str) -> None:
        path = Path(text).expanduser()
        self.data_pill.refresh(path)
        if not self._models_manual:
            self.models_edit.setText(str(path / "models"))
        self._on_models_changed(self.models_edit.text())

    def _on_models_edited(self, _text: str) -> None:
        self._models_manual = True  # ручной ввод — перестаём следовать за данными

    def _on_models_changed(self, text: str) -> None:
        path = Path(text).expanduser()
        free = self.models_pill.refresh(path, warn_below_gb=MODELS_MIN_FREE_GB)
        low = free is not None and free < MODELS_MIN_FREE_GB
        if low:
            label = _disk_label(path)
            self.models_warn.setText(
                f"⚠ На диске {label} свободно {free:.1f} ГБ — меньше "
                f"{MODELS_MIN_FREE_GB:.0f} ГБ. Модели могут не поместиться: "
                "выберите другой диск для каталога моделей."
            )
        self.models_warn.setVisible(low)

    def _refresh_free(self) -> None:
        self._on_data_changed(self.data_edit.text())
        self._on_models_changed(self.models_edit.text())

    def _browse_data_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Каталог данных", self.data_edit.text(),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if folder:
            self.data_edit.setText(folder)

    def _browse_models_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Каталог моделей SLM", self.models_edit.text(),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if folder:
            self._models_manual = True
            self.models_edit.setText(folder)
            self._on_models_changed(folder)

    # ---------------------------------------------------------------- проверка

    @override
    def validatePage(self) -> bool:
        chosen = Path(self.data_edit.text()).expanduser()
        if not chosen.is_absolute():
            QMessageBox.warning(self, "Ошибка", "Укажите абсолютный путь к каталогу данных.")
            return False
        models_chosen = Path(self.models_edit.text()).expanduser()
        if not models_chosen.is_absolute():
            QMessageBox.warning(self, "Ошибка", "Укажите абсолютный путь к каталогу моделей.")
            return False

        free = _disk_free_gb(models_chosen)
        if free is not None and free < MODELS_MIN_FREE_GB:
            label = _disk_label(models_chosen)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Мало места для моделей")
            box.setText(
                f"На диске {label} свободно {free:.1f} ГБ — меньше "
                f"рекомендуемых {MODELS_MIN_FREE_GB:.0f} ГБ."
            )
            box.setInformativeText(
                "Локальные модели занимают от ~1 до 20+ ГБ каждая. "
                "Выберите другой диск для каталога моделей или продолжите на свой риск."
            )
            choose_btn = box.addButton("Выбрать другой диск…", QMessageBox.ButtonRole.AcceptRole)
            continue_btn = box.addButton("Всё равно продолжить", QMessageBox.ButtonRole.ActionRole)
            box.setDefaultButton(choose_btn)
            box.exec()
            if box.clickedButton() is not continue_btn:
                if box.clickedButton() is choose_btn:
                    self._browse_models_dir()
                return False  # остаёмся на странице (в т.ч. при закрытии диалога)
            logger.warning(
                f"Каталог моделей на диске {label}: свободно {free:.1f} ГБ "
                f"(< {MODELS_MIN_FREE_GB:.0f} ГБ), пользователь выбрал продолжить."
            )

        self.wizard_ref.set_data_dir(chosen)
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
        self.lmstudio_note.setObjectName("warnNote")
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

    @override
    def validatePage(self) -> bool:
        if self.lmstudio_radio.isChecked():
            QMessageBox.warning(self, "В разработке", "Поддержка LM Studio пока в разработке.")
            return False
        if self.ollama_radio.isChecked():
            url = self.ollama_url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "Ошибка", "Укажите URL сервера Ollama.")
                return False
            self.wizard_ref._selected_backend = "ollama"
            self.wizard_ref._ollama_url = url
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

    @override
    def initializePage(self) -> None:
        theme_name = "Тёмная" if self.wizard_ref._selected_theme == "dark" else "Светлая"
        backend_names = {"local_slm": "Локальные модели SLM", "ollama": "Ollama", "lmstudio": "LM Studio"}
        backend_name = backend_names.get(self.wizard_ref._selected_backend, "Локальные модели SLM")
        lines = [
            f"<b>Тема оформления:</b> {theme_name}",
            f"<b>Каталог данных:</b> {self.wizard_ref._data_dir}",
            f"<b>Каталог моделей:</b> {self.wizard_ref._models_dir}",
            f"<b>Поставщик моделей:</b> {backend_name}",
        ]
        if self.wizard_ref._selected_backend == "ollama":
            lines.append(f"<b>Ollama URL:</b> {self.wizard_ref._ollama_url}")
        lines.append("<br>Конфигурация будет сохранена в каталог данных.")
        self.summary_label.setText("<br>".join(lines))