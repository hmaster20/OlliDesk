"""Настройка логирования через loguru."""

import sys
from pathlib import Path

from loguru import logger


def _reconfigure_streams() -> None:
    """Принудительно включает UTF-8 для sys.stdout/sys.stderr.

    На русской Windows sys.stderr по умолчанию имеет encoding='cp1251'.
    Python 3.11+ в traceback использует box-drawing символы ``│`` (U+2502),
    ``└`` (U+2514) и т. п., которых нет в cp1251 → loguru падает с
    ``UnicodeEncodeError: 'charmap' codec can't encode characters`` при
    попытке залогировать ЛЮБОЕ исключение (включая транзитные).

    ``reconfigure(encoding="utf-8", errors="replace")`` решает проблему
    целиком: при невозможности закодировать символ он будет заменён на
    ``?``, а не выбросит исключение (UTF-8 покрывает весь Unicode, так что
    ``replace`` — страховка на случай экзотических code points).
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                # Если stream не поддерживает reconfigure (custom wrapper
                # от IDE / тестового фреймворка) — молча продолжаем:
                # catch=True в logger.add защитит от падения.
                pass


def setup_logger(log_dir: Path, level: str = "INFO") -> None:
    """Настраивает loguru с ротацией 10MB, хранение 7 дней.

    Args:
        log_dir: Папка для логов
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
    """
    # 1. Принудительно utf-8 для консоли (иначе UnicodeEncodeError на cp1251).
    _reconfigure_streams()

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ollidesk_{time:YYYY-MM-DD}.log"

    # Удаляем дефолтный handler.
    logger.remove()

    fmt = (
        "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level>"
        " | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
        " - <level>{message}</level>"
    )
    # catch=True: ошибка логирования не должна валить приложение. Если sink
    # временно недоступен (закрыта консоль, диск заполнен, encoding не
    # поддерживает символ) — loguru напечатает предупреждение в дефолтный
    # handler, а не выбросит исключение наружу.
    logger.add(sys.stderr, level=level, format=fmt, catch=True)

    logger.add(
        log_file,
        rotation="10 MB",
        retention="7 days",
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        catch=True,
    )