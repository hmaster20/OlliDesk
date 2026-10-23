"""Тесты дизайн-системы и контраста (9.11).

Без pytest-qt: чистые функции + строковый анализ QSS. Проверяем:
  * контраст WCAG AA ключевых пар (текст на фоне, кнопки, выделение);
  * валидность hex-токенов;
  * наличие QSS-правил для всех обещанных интерактивных элементов;
  * что QSS собирается из токенов (правка токена меняет QSS).
"""

from __future__ import annotations

import re

import pytest

from ui.theme import (
    DARK_COLORS,
    DARK_STYLESHEET,
    LIGHT_COLORS,
    LIGHT_STYLESHEET,
    ColorTokens,
    get_colors,
    get_stylesheet,
)


# ============================================================================
# Утилиты: цвет + контраст (WCAG 2.1)
# ============================================================================


def _parse_hex(value: str) -> tuple[int, int, int]:
    """Разбирает '#rrggbb' (опционально с '#' или без) в (r, g, b)."""
    s = value.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Невалидный hex: {value!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Относительная яркость по WCAG 2.1."""
    def _channel(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    """Контраст по WCAG: (lighter + 0.05) / (darker + 0.05)."""
    l1 = _relative_luminance(_parse_hex(fg))
    l2 = _relative_luminance(_parse_hex(bg))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ============================================================================
# Валидность токенов
# ============================================================================


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@pytest.mark.parametrize("colors,name", [
    (DARK_COLORS, "DARK"),
    (LIGHT_COLORS, "LIGHT"),
])
def test_all_tokens_are_valid_hex(colors: ColorTokens, name: str) -> None:
    """Все цветовые поля — валидный '#rrggbb'."""
    for field_name, value in colors.__dict__.items():
        assert _HEX_RE.match(value), (
            f"{name}.{field_name}={value!r} не #rrggbb"
        )


# ============================================================================
# WCAG AA: контраст
# ============================================================================


# Пары «текст на фоне» с порогами. AA — 4.5:1 для обычного текста,
# 3:1 для крупного (18pt+) и элементов UI. muted/disabled — исключения.
_CONTRAST_PAIRS = [
    # (foreground, background, threshold, description)
    ("text", "bg", 4.5, "основной текст на фоне окна"),
    ("text", "surface", 4.5, "текст на панелях/полях"),
    ("text", "row_alt", 4.5, "текст на чётных строках таблиц"),
    ("text", "surface_hover", 4.5, "текст на hover-строках"),
    ("header_text", "header_bg", 4.5, "заголовок на header таблицы"),
    ("header_text", "surface", 4.5, "заголовок группы на surface"),
    ("accent_text", "accent", 4.5, "текст на accent (кнопки)"),
    ("accent_text", "accent_hover", 3.0, "текст на accent hover (кратк.)"),
    ("accent_text", "accent_pressed", 4.5, "текст на accent pressed"),
    ("selection_text", "selection_bg", 4.5, "текст в выделенной строке"),
    ("text_muted", "bg", 3.0, "muted-текст на фоне (AA Large)"),
    ("text_muted", "surface", 3.0, "muted-текст на панелях (AA Large)"),
    ("danger", "bg", 3.0, "danger-индикатор на фоне (AA Large)"),
    ("success", "bg", 3.0, "success-индикатор на фоне (AA Large)"),
    ("warning", "bg", 3.0, "warning-индикатор на фоне (AA Large)"),
]


@pytest.mark.parametrize("colors,name", [
    (DARK_COLORS, "DARK"),
    (LIGHT_COLORS, "LIGHT"),
])
@pytest.mark.parametrize("fg_key,bg_key,threshold,description", _CONTRAST_PAIRS)
def test_wcag_contrast(
    colors: ColorTokens,
    name: str,
    fg_key: str,
    bg_key: str,
    threshold: float,
    description: str,
) -> None:
    """Контраст пары >= threshold (WCAG AA)."""
    fg = getattr(colors, fg_key)
    bg = getattr(colors, bg_key)
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= threshold, (
        f"[{name}] {description}: {fg_key}={fg} on {bg_key}={bg} "
        f"даёт {ratio:.2f}:1 < {threshold}:1"
    )


def test_contrast_ratio_function_sanity() -> None:
    """Самопроверка _contrast_ratio: white/black = 21:1."""
    assert _contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert _contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    # Одинаковые цвета = 1:1.
    assert _contrast_ratio("#2b2b2b", "#2b2b2b") == pytest.approx(1.0, abs=0.01)


# ============================================================================
# QSS: наличие правил для интерактивных элементов (9.1)
# ============================================================================


# Минимальный набор селекторов, которые план 9.1 явно требует.
# Отсутствие любого — регрессия (элемент «без стиля» = «болят глаза»).
_REQUIRED_SELECTORS = [
    # Базовые
    "QMainWindow, QWidget",
    "QDialog",
    "QLabel",
    "QMenuBar",
    "QMenuBar::item",
    "QMenu",
    "QMenu::item:selected",
    "QMenu::separator",
    # Кнопки
    "QPushButton",
    "QPushButton:hover",
    "QPushButton:pressed",
    "QPushButton:disabled",
    "QPushButton:default",
    "QPushButton[class=\"primary\"]",
    "QPushButton[class=\"danger\"]",
    # Поля ввода
    "QLineEdit, QPlainTextEdit, QTextEdit",
    "QLineEdit:focus",
    # ComboBox
    "QComboBox",
    "QComboBox::down-arrow",
    "QComboBox QAbstractItemView",
    # Списки
    "QListWidget, QListView",
    "QListWidget::item:selected",
    # Дерево
    "QTreeView, QTreeWidget",
    "QTreeView::item:selected",
    # Таблицы (приоритет №1 — 9.1)
    "QTableWidget, QTableView",
    "QTableWidget::item",
    "QTableWidget::item:selected",
    "QHeaderView",
    "QHeaderView::section",
    "QTableCornerButton::section",
    # Вкладки
    "QTabWidget::pane",
    "QTabBar::tab",
    "QTabBar::tab:selected",
    # Скроллбар
    "QScrollBar:vertical",
    "QScrollBar::handle:vertical",
    "QScrollBar:horizontal",
    "QScrollBar::handle:horizontal",
    # Splitter
    "QSplitter::handle",
    # GroupBox
    "QGroupBox",
    "QGroupBox::title",
    # CheckBox / RadioButton
    "QCheckBox",
    "QCheckBox::indicator",
    "QRadioButton",
    "QRadioButton::indicator",
    # SpinBox
    "QSpinBox, QDoubleSpinBox",
    "QSpinBox::up-arrow",
    "QSpinBox::down-arrow",
    # Прогресс
    "QProgressBar",
    "QProgressBar::chunk",
    # StatusBar
    "QStatusBar",
    # Tooltip
    "QToolTip",
    # MessageBox
    "QMessageBox",
]


@pytest.mark.parametrize("theme,qss", [
    ("dark", DARK_STYLESHEET),
    ("light", LIGHT_STYLESHEET),
])
@pytest.mark.parametrize("selector", _REQUIRED_SELECTORS)
def test_qss_has_selector(theme: str, qss: str, selector: str) -> None:
    """QSS содержит правило для селектора (9.1 — полное покрытие)."""
    assert selector in qss, (
        f"[{theme}] QSS не содержит селектор {selector!r}. "
        f"Элемент будет выглядеть «без стиля»."
    )


# ============================================================================
# QSS собирается из токенов (а не хардкодится)
# ============================================================================


def test_qss_reflects_token_changes() -> None:
    """QSS генерируется из токенов: правка поля меняет QSS.

    Реализация через `dataclasses.replace` — надёжнее, чем мокать
    модуль. Если `_build_qss` начнёт хардкодить цвета, тест упадёт.
    """
    from dataclasses import replace

    from ui.theme import RADIUS, SPACE, TYPO, _build_qss

    modified = replace(DARK_COLORS, text="#ff0000")
    qss = _build_qss(modified, TYPO, SPACE, RADIUS)
    assert "#ff0000" in qss, "QSS не отразил изменение DARK_COLORS.text"
    # И в дефолтном QSS красного нет.
    assert "#ff0000" not in DARK_STYLESHEET


def test_get_stylesheet_dispatch() -> None:
    """get_stylesheet/get_colors возвращают правильный набор по имени темы."""
    assert get_stylesheet("dark") is DARK_STYLESHEET
    assert get_stylesheet("light") is LIGHT_STYLESHEET
    assert get_colors("dark") is DARK_COLORS
    assert get_colors("light") is LIGHT_COLORS
    # Неизвестная тема → light (соответствует поведению в коде).
    assert get_stylesheet("unknown") is LIGHT_STYLESHEET


# ============================================================================
# Никаких «сырых» цветов в QSS вне токенов
# ============================================================================


def test_no_hardcoded_hex_in_qss() -> None:
    """Все hex-цвета в QSS приходят из токенов (не хардкодятся).

    Проверяем: каждый hex, встречающийся в QSS, есть в наборе значений
    DARK_COLORS. `rgba(...)` тоже допустим — там hex не используется.
    """
    # Собираем все hex-значения из токенов (в нижнем регистре).
    token_hexes = {v.lower() for v in DARK_COLORS.__dict__.values()}
    # Плюс маска прозрачности и прозрачный фон.
    allowed_extra = {"#ffffff", "#000000"}  # для контраста в специфичных местах

    # Ищем все #rrggbb в QSS.
    hexes_in_qss = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}", DARK_STYLESHEET)}
    unknown = hexes_in_qss - token_hexes - allowed_extra
    # Разрешаем небольшой список «фоновых» — для прозрачности в QSS
    # (см. rgba(), которые не считаются hex'ами).
    assert not unknown, (
        f"В DARK_STYLESHEET есть hex-цвета не из токенов: {sorted(unknown)}. "
        f"Либо добавь их в ColorTokens, либо используй существующий токен."
    )