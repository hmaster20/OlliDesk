"""Утилиты для работы с путями, совместимые с PyInstaller."""

import sys
from pathlib import Path


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
        base_path = sys._MEIPASS  # type: ignore
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
