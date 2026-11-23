"""Утилиты для работы с путями, совместимые с PyInstaller."""

from __future__ import annotations

import os
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
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        # В режиме разработки используем корень проекта
        base_path = Path(__file__).resolve().parent.parent

    return Path(base_path) / relative_path


def _get_pointer_file() -> Path:
    """Фиксированный файл-указатель каталога данных (Вариант А).

    Хранится в постоянном месте, НЕ зависящем от самого каталога данных:
    на Windows — %APPDATA%/OlliDesk/data_dir.txt, на остальных платформах —
    ~/.config/OlliDesk/data_dir.txt. Это «якорь», который переживает перенос данных.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "OlliDesk" / "data_dir.txt"
    return Path.home() / ".config" / "OlliDesk" / "data_dir.txt"


def get_app_data_dir() -> Path:
    """Возвращает путь к папке данных приложения.
    Сначала читает файл-указатель. Если там валидный существующий путь — использует его,
    иначе дефолт ~/.ollidesk.
    """
    pointer = _get_pointer_file()
    if pointer.is_file():
        try:
            candidate = Path(pointer.read_text(encoding="utf-8").strip())
            if candidate.is_absolute() and candidate.exists():
                return candidate
        except (OSError, ValueError):
            pass
    return Path.home() / ".ollidesk"


def set_app_data_dir(path: Path) -> None:
    """Записывает файл-указатель каталога данных и создаёт сам каталог."""
    resolved = Path(path).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    pointer = _get_pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(resolved), encoding="utf-8")


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
