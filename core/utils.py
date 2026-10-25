"""Утилиты для работы с путями, совместимые с PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


def get_resource_path(relative_path: str) -> Path:
    """
    Получает абсолютный путь к ресурсу.
    Работает как в режиме разработки, так и в скомпилированном PyInstaller приложении.

    Args:
        relative_path: Относительный путь к ресурсу (например, "ui/web_editor/editor.html")

    Returns:
        Path: Абсолютный путь к ресурсу
    """
    try:
        # PyInstaller создает временную папку и сохраняет ее путь в _MEIPASS
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        # В режиме разработки используем корень проекта
        base_path = Path(__file__).resolve().parent.parent

    return Path(base_path) / relative_path


def get_app_data_dir() -> Path:
    """
    Возвращает путь к папке данных приложения в профиле пользователя.
    Используется для хранения конфигов, логов и БД ChromaDB.

    Returns:
        Path: Путь к ~/.ollidesk
    """
    return Path.home() / ".ollidesk"


def get_app_icon() -> QIcon | None:
    """Возвращает QIcon приложения или None, если файла нет.

    Ищет ``icon.ico`` в корне проекта (режим разработки) или в
    ``sys._MEIPASS`` (PyInstaller-сборка). Возвращаемое значение
    устанавливается через ``QApplication.setWindowIcon(...)`` — Qt
    автоматически распространяет его на все окна приложения
    (QMainWindow, QDialog, QMessageBox, QFileDialog).

    Ленивые импорты PySide6/loguru: ``core.utils`` импортируется очень
    рано (из ``core.config``), а тянуть ``PySide6.QtGui`` до создания
    ``QApplication`` не хочется. ``from __future__ import annotations``
    (в шапке модуля) позволяет писать ``-> QIcon | None`` без runtime-
    импорта: аннотация остаётся строкой, вычисляется mypy/линтерами,
    а не интерпретатором.

    Returns:
        QIcon или ``None`` (файла нет / не читается / Qt не загрузил).
    """
    from loguru import logger
    from PySide6.QtGui import QIcon

    icon_path = get_resource_path("icon.ico")
    if not icon_path.is_file():
        logger.debug(f"Иконка приложения не найдена: {icon_path}")
        return None

    icon = QIcon(str(icon_path))
    if icon.isNull():
        logger.warning(f"Не удалось загрузить иконку: {icon_path}")
        return None

    logger.debug(f"Иконка приложения загружена: {icon_path} (sizes={icon.availableSizes()})")
    return icon
