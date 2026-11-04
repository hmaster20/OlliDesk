"""Дизайн-система OlliDesk: токены + QSS (9.1).

Единая точка правды для цветов, шрифтов, отступов и радиусов.
QSS генерируется из токенов: правка палитры автоматически меняет все
стили. Покрытие — все интерактивные элементы UI, приоритет №1 —
QTableWidget/QHeaderView/QTableView (в SLMModelsDialog и
ModelManagerDialog «болели глаза» без них).

Палитра: Darcula-основа + тёплые акценты (янтарный #d4a373,
терракотовый #cd7a5b) вместо стандартного синего. Светлая тема —
нейтральная с тёплым акцентом. Контраст — WCAG AA.
"""

from __future__ import annotations

from dataclasses import dataclass

# ============================================================================
# Токены: цвета
# ============================================================================


@dataclass(frozen=True)
class ColorTokens:
    """Цветовые токены темы."""

    # Фоны
    bg: str  # основной фон окна
    surface: str  # фон панелей, полей
    surface_alt: str  # альтернативные строки таблиц, hover
    surface_hover: str  # hover-фон
    surface_pressed: str  # pressed-фон
    header_bg: str  # заголовки таблиц

    # Текст
    text: str
    text_muted: str
    text_disabled: str
    header_text: str

    # Границы
    border: str
    border_strong: str

    # Акценты (тёплая палитра)
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_muted: str

    # Семантика
    success: str
    warning: str
    danger: str
    info: str

    # Выделение
    selection_bg: str
    selection_text: str

    # Скроллбар
    scrollbar: str
    scrollbar_hover: str
    scrollbar_bg: str

    # Строки таблиц
    row_alt: str

    # [9.5.1] Пузыри чата.
    bubble_bg: str = ""  # фон пузыря assistant
    bubble_user_bg: str = ""  # фон пузыря user
    bubble_border: str = ""  # граница пузыря (тонкая)
    # [9.5.1] Код-блоки (pygments + header).
    code_bg: str = ""
    code_text: str = ""
    code_header_bg: str = ""
    code_header_text: str = ""
    # [9.5.1] Reasoning-виджет.
    reasoning_bg: str = ""
    reasoning_text: str = ""
    # [9.5.1] Tool-карточки.
    tool_card_bg: str = ""
    tool_card_border: str = ""
    tool_preview_text: str = ""

DARK_COLORS = ColorTokens(
    bg="#2b2b2b",
    surface="#3c3f41",
    surface_alt="#45494a",
    surface_hover="#4c5052",
    surface_pressed="#555555",
    header_bg="#3c3f41",
    text="#c4c4c4",
    text_muted="#909090",
    text_disabled="#555555",
    header_text="#d4d4d4",
    border="#555555",
    border_strong="#6a6a6a",
    accent="#d4a373",
    accent_hover="#e0b88a",
    accent_pressed="#b8885a",
    accent_text="#1e1e1e",
    accent_muted="#8a6a4a",
    success="#98c379",
    warning="#e5c07b",
    danger="#e06c75",
    info="#61afef",
    selection_bg="#4a6741",
    selection_text="#ffffff",
    scrollbar="#4c5052",
    scrollbar_hover="#5c6062",
    scrollbar_bg="#2b2b2b",
    row_alt="#333637",
    # [9.5.1] Пузыри чата.
    bubble_bg="#2f2f38",
    bubble_user_bg="#3d3d48",
    bubble_border="#3a3a48",
    # [9.5.1] Код-блоки.
    code_bg="#23232c",
    code_text="#e0e0e0",
    code_header_bg="#2f2f3c",
    code_header_text="#9a9ab0",
    # [9.5.1] Reasoning.
    reasoning_bg="#262630",
    reasoning_text="#a8a8b8",
    # [9.5.1] Tool-карточки.
    tool_card_bg="rgba(120,120,140,0.06)",
    tool_card_border="#3a3a48",
    tool_preview_text="#a8a8b8",
)

LIGHT_COLORS = ColorTokens(
    bg="#f5f5f5",
    surface="#ffffff",
    surface_alt="#f0f0f0",
    surface_hover="#e8e8e8",
    surface_pressed="#dcdcdc",
    header_bg="#e8e8e8",
    text="#1e1e1e",
    text_muted="#666666",
    text_disabled="#aaaaaa",
    header_text="#2b2b2b",
    border="#d0d0d0",
    border_strong="#b0b0b0",
    accent="#9e5318",
    accent_hover="#b8651f",
    accent_pressed="#7a3f12",
    accent_text="#ffffff",
    accent_muted="#d4a373",
    success="#4e9a06",
    warning="#b08000",
    danger="#c0392b",
    info="#1976d2",
    selection_bg="#9e5318",
    selection_text="#ffffff",
    scrollbar="#c0c0c0",
    scrollbar_hover="#a8a8a8",
    scrollbar_bg="#e8e8e8",
    row_alt="#f7f7f7",
    # [9.5.1] Пузыри чата.
    bubble_bg="#ffffff",
    bubble_user_bg="#e8eef5",
    bubble_border="#e0e0e0",
    # [9.5.1] Код-блоки.
    code_bg="#f5f5f5",
    code_text="#1e1e1e",
    code_header_bg="#e8e8e8",
    code_header_text="#666666",
    # [9.5.1] Reasoning.
    reasoning_bg="#f8f8f8",
    reasoning_text="#555555",
    # [9.5.1] Tool-карточки.
    tool_card_bg="rgba(120,120,140,0.04)",
    tool_card_border="#d0d0d0",
    tool_preview_text="#666666",
)

# ============================================================================
# Токены: типографика, отступы, радиусы
# ============================================================================


@dataclass(frozen=True)
class TypographyTokens:
    """Шрифты и размеры."""

    font_family_ui: str = "'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
    font_family_mono: str = "'Cascadia Mono', 'Consolas', 'Courier New', monospace"
    size_small: int = 11
    size_base: int = 13
    size_medium: int = 14
    size_large: int = 16
    size_heading: int = 18
    weight_normal: int = 400
    weight_bold: int = 600


@dataclass(frozen=True)
class SpacingTokens:
    """Отступы (px)."""

    xs: int = 2
    sm: int = 4
    md: int = 8
    lg: int = 12
    xl: int = 16
    xxl: int = 24


@dataclass(frozen=True)
class RadiusTokens:
    """Радиусы скругления (px)."""

    sm: int = 3
    md: int = 4
    lg: int = 6
    xl: int = 8


TYPO = TypographyTokens()
SPACE = SpacingTokens()
RADIUS = RadiusTokens()


# ============================================================================
# Сборка QSS из токенов
# ============================================================================


def _build_qss(c: ColorTokens, t: TypographyTokens, s: SpacingTokens, r: RadiusTokens) -> str:
    """Собирает QSS из токенов. Одна точка правды — этот шаблон."""
    return f"""
/* ===================== Базовые ===================== */
QMainWindow, QWidget {{
    background-color: {c.bg};
    color: {c.text};
    font-family: {t.font_family_ui};
    font-size: {t.size_base}px;
}}

QDialog {{
    background-color: {c.bg};
}}

QLabel {{
    color: {c.text};
    background: transparent;
}}

QLabel[class="muted"] {{
    color: {c.text_muted};
    font-size: {t.size_small}px;
}}

QLabel[class="heading"] {{
    color: {c.header_text};
    font-size: {t.size_heading}px;
    font-weight: {t.weight_bold};
}}

/* ===================== Меню ===================== */
QMenuBar {{
    background-color: {c.surface};
    color: {c.text};
    border-bottom: 1px solid {c.border};
}}
QMenuBar::item {{
    background: transparent;
    padding: {s.sm}px {s.lg}px;
    color: {c.text};
}}
QMenuBar::item:selected {{
    background-color: {c.surface_hover};
    color: {c.header_text};
}}
QMenuBar::item:pressed {{
    background-color: {c.accent};
    color: {c.accent_text};
}}

QMenu {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    padding: {s.sm}px 0;
}}
QMenu::item {{
    padding: {s.sm}px {s.xxl}px {s.sm}px {s.xl}px;
    background: transparent;
    color: {c.text};
}}
QMenu::item:selected {{
    background-color: {c.accent};
    color: {c.accent_text};
}}
QMenu::item:disabled {{
    color: {c.text_disabled};
}}
QMenu::separator {{
    height: 1px;
    background: {c.border};
    margin: {s.sm}px 0;
}}

/* ===================== Кнопки ===================== */
QPushButton {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    padding: {s.sm}px {s.xl}px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {c.surface_hover};
    border-color: {c.border_strong};
}}
QPushButton:pressed {{
    background-color: {c.surface_pressed};
}}
QPushButton:disabled {{
    background-color: {c.surface_alt};
    color: {c.text_disabled};
    border-color: {c.border};
}}
QPushButton:default {{
    background-color: {c.accent};
    color: {c.accent_text};
    border-color: {c.accent_pressed};
}}
QPushButton:default:hover {{
    background-color: {c.accent_hover};
}}
QPushButton[class="primary"] {{
    background-color: {c.accent};
    color: {c.accent_text};
    border: none;
    font-weight: {t.weight_bold};
    padding: {s.md}px {s.xl}px;
}}
QPushButton[class="primary"]:hover {{
    background-color: {c.accent_hover};
}}
QPushButton[class="primary"]:pressed {{
    background-color: {c.accent_pressed};
}}
QPushButton[class="danger"] {{
    background-color: {c.danger};
    color: #ffffff;
    border: none;
}}
QPushButton[class="flat"] {{
    background: transparent;
    border: none;
    padding: {s.xs}px {s.sm}px;
}}
QPushButton[class="flat"]:hover {{
    background-color: {c.surface_hover};
}}

/* ===================== Поля ввода ===================== */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    padding: {s.sm}px {s.md}px;
    selection-background-color: {c.selection_bg};
    selection-color: {c.selection_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {c.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    background-color: {c.surface_alt};
    color: {c.text_disabled};
}}

/* ===================== ComboBox ===================== */
QComboBox {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    padding: {s.sm}px {s.md}px;
    min-height: 22px;
}}
QComboBox:hover {{
    border-color: {c.border_strong};
}}
QComboBox:focus {{
    border-color: {c.accent};
}}
QComboBox:disabled {{
    background-color: {c.surface_alt};
    color: {c.text_disabled};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c.text_muted};
    margin-right: {s.md}px;
}}
QComboBox QAbstractItemView {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    selection-background-color: {c.accent};
    selection-color: {c.accent_text};
    outline: none;
    padding: {s.xs}px;
}}

/* ===================== Списки ===================== */
QListWidget, QListView {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    outline: none;
    alternate-background-color: {c.row_alt};
}}
QListWidget::item, QListView::item {{
    padding: {s.sm}px {s.md}px;
    border-radius: {r.sm}px;
}}
QListWidget::item:selected, QListView::item:selected {{
    background-color: {c.selection_bg};
    color: {c.selection_text};
}}
QListWidget::item:hover, QListView::item:hover {{
    background-color: {c.surface_hover};
}}

/* ===================== Дерево ===================== */
QTreeView, QTreeWidget {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    outline: none;
    alternate-background-color: {c.row_alt};
}}
QTreeView::item, QTreeWidget::item {{
    padding: {s.xs}px {s.sm}px;
    min-height: 20px;
}}
QTreeView::item:selected, QTreeWidget::item:selected {{
    background-color: {c.selection_bg};
    color: {c.selection_text};
}}
QTreeView::branch {{
    background: transparent;
}}

/* ===================== ТАБЛИЦЫ (приоритет №1 — 9.1) ===================== */
QTableWidget, QTableView {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    gridline-color: {c.border};
    outline: none;
    alternate-background-color: {c.row_alt};
    selection-background-color: {c.selection_bg};
    selection-color: {c.selection_text};
}}
QTableWidget::item, QTableView::item {{
    padding: {s.sm}px {s.md}px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {c.selection_bg};
    color: {c.selection_text};
}}
QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {c.surface_hover};
}}

QHeaderView {{
    background-color: {c.header_bg};
    border: none;
}}
QHeaderView::section {{
    background-color: {c.header_bg};
    color: {c.header_text};
    border: none;
    border-right: 1px solid {c.border};
    border-bottom: 1px solid {c.border};
    padding: {s.sm}px {s.md}px;
    font-weight: {t.weight_bold};
    font-size: {t.size_base}px;
}}
QHeaderView::section:last {{
    border-right: none;
}}
QHeaderView::section:hover {{
    background-color: {c.surface_hover};
}}
QTableCornerButton::section {{
    background-color: {c.header_bg};
    border: none;
    border-right: 1px solid {c.border};
    border-bottom: 1px solid {c.border};
}}

/* ===================== Вкладки ===================== */
QTabWidget::pane {{
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    background-color: {c.bg};
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background-color: {c.surface};
    color: {c.text_muted};
    border: 1px solid {c.border};
    border-bottom: none;
    border-top-left-radius: {r.md}px;
    border-top-right-radius: {r.md}px;
    padding: {s.sm}px {s.lg}px;
    margin-right: 1px;
}}
QTabBar::tab:hover {{
    background-color: {c.surface_hover};
    color: {c.text};
}}
QTabBar::tab:selected {{
    background-color: {c.bg};
    color: {c.header_text};
    border-bottom: 2px solid {c.accent};
}}
QTabBar::tab:disabled {{
    color: {c.text_disabled};
}}

/* ===================== Scrollbar ===================== */
QScrollBar:vertical {{
    background: {c.scrollbar_bg};
    width: 12px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c.scrollbar};
    min-height: 24px;
    border-radius: {r.sm}px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.scrollbar_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
    border: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: {c.scrollbar_bg};
    height: 12px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c.scrollbar};
    min-width: 24px;
    border-radius: {r.sm}px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c.scrollbar_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
    border: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ===================== Splitter ===================== */
QSplitter::handle {{
    background-color: {c.border};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QSplitter::handle:hover {{
    background-color: {c.accent};
}}

/* ===================== GroupBox ===================== */
QGroupBox {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    margin-top: {s.md}px;
    padding-top: {s.md}px;
    font-weight: {t.weight_bold};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {s.md}px;
    padding: 0 {s.sm}px;
    color: {c.header_text};
    background-color: {c.surface};
}}

/* ===================== CheckBox / RadioButton ===================== */
QCheckBox, QRadioButton {{
    color: {c.text};
    spacing: {s.md}px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {c.border_strong};
    background-color: {c.surface};
}}
QCheckBox::indicator {{
    border-radius: {r.sm}px;
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {c.accent};
}}
QCheckBox::indicator:checked {{
    background-color: {c.accent};
    border-color: {c.accent_pressed};
    image: none;
}}
QRadioButton::indicator:checked {{
    background-color: {c.accent};
    border-color: {c.accent_pressed};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {c.surface_alt};
    border-color: {c.border};
}}

/* ===================== SpinBox / DoubleSpinBox ===================== */
QSpinBox, QDoubleSpinBox {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    padding: {s.sm}px {s.md}px;
    min-height: 22px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c.accent};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: {c.surface_alt};
    border: 1px solid {c.border};
    width: 16px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {c.surface_hover};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {c.text_muted};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {c.text_muted};
}}

/* ===================== Прогресс ===================== */
QProgressBar {{
    background-color: {c.surface_alt};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.md}px;
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background-color: {c.accent};
    border-radius: {r.sm}px;
}}

/* ===================== StatusBar ===================== */
QStatusBar {{
    background-color: {c.surface};
    color: {c.text_muted};
    border-top: 1px solid {c.border};
}}
QStatusBar QLabel {{
    color: {c.text_muted};
    padding: 0 {s.sm}px;
}}
QStatusBar::item {{
    border: none;
}}

/* ===================== ToolTip ===================== */
QToolTip {{
    background-color: {c.surface_alt};
    color: {c.text};
    border: 1px solid {c.border_strong};
    border-radius: {r.md}px;
    padding: {s.sm}px {s.md}px;
}}

/* ===================== MessageBox ===================== */
QMessageBox {{
    background-color: {c.bg};
}}
QMessageBox QLabel {{
    color: {c.text};
}}
QMessageBox QPushButton {{
    min-width: 80px;
}}

/* ===================== Диалоги ===================== */
QDialogButtonBox QPushButton {{
    min-width: 80px;
}}

/* ===================== Формы ===================== */
QFormLayout {{
    spacing: {s.md}px;
}}

/* ===================== Специальные элементы чата ===================== */
QLabel[class="chat-status"] {{
    color: {c.text_muted};
    font-size: {t.size_small}px;
    padding: {s.sm}px;
}}

QFrame#_bubble {{
    border-radius: {r.lg}px;
    padding: {s.md}px;
}}

QFrame#_research_passport {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: {r.lg}px;
}}

QFrame#_thought_stream {{
    background-color: {c.bg};
    border: 1px solid {c.border};
    border-radius: {r.lg}px;
}}
"""


# ============================================================================
# Публичный API
# ============================================================================


DARK_STYLESHEET = _build_qss(DARK_COLORS, TYPO, SPACE, RADIUS)
LIGHT_STYLESHEET = _build_qss(LIGHT_COLORS, TYPO, SPACE, RADIUS)


def get_colors(theme: str) -> ColorTokens:
    """Возвращает набор токенов по имени темы (dark/light)."""
    return DARK_COLORS if theme == "dark" else LIGHT_COLORS


def get_stylesheet(theme: str) -> str:
    """Возвращает готовый QSS по имени темы."""
    return DARK_STYLESHEET if theme == "dark" else LIGHT_STYLESHEET


__all__ = [
    "ColorTokens",
    "DARK_COLORS",
    "DARK_STYLESHEET",
    "LIGHT_COLORS",
    "LIGHT_STYLESHEET",
    "RADIUS",
    "SPACE",
    "TYPO",
    "get_colors",
    "get_stylesheet",
]
