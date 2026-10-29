"""Настройка логирования через loguru."""

import contextlib
import sys
from pathlib import Path
from typing import Any, TextIO, cast

from loguru import logger

from core import redaction


class _SafeStream:
    """Обёртка над sys.stderr, которая не падает при записи.

    Проблема, которую решает:

    В GUI-запуске на Windows sys.stderr — это ``nul`` с кодировкой
    ``cp1251``. Первая же запись цветного лога падает с
    ``OSError: [WinError 1] Неверная функция``. Loguru с ``catch=True``
    пытается напечатать error-record в тот же сломанный stderr, а его
    error-interceptor использует box-drawing символы (│, └, ┌),
    которых нет в cp1251 → ``UnicodeEncodeError`` прямо в обработчике
    ошибок → ``sys.excepthook`` и трейсбек в консоль.

    Эта обёртка гасит ЛЮБЫЕ ошибки ``write``/``flush``: после первого
    сбоя стрим помечается ``_broken`` и дальнейшие записи — no-op.
    Основной канал логов — файловый sink (всегда рабочий), stderr —
    вспомогательный (на случай ``python main.py`` из консоли).

    Note:
        Type checker не считает ``_SafeStream`` полноценным ``TextIO``
        (protocol требует явных ``read``/``readline``/``encoding``/…),
        поэтому в ``setup_logger`` применяется ``cast(TextIO, ...)``.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._broken = False

    def write(self, data: str) -> int:
        """Пишет в нижележащий поток; никогда не бросает.

        Returns:
            Число «принятых» символов. При первом сбое стрим
            помечается ``_broken``, а возвращается ``len(data)`` — так
            loguru думает, что запись прошла (иначе он попытается
            напечатать error-record в этот же сломанный stderr).
        """
        if self._broken or self._stream is None:
            return len(data)
        try:
            return self._stream.write(data)
        except Exception:
            self._broken = True
            return len(data)

    def flush(self) -> None:
        """Flush нижележащего потока; никогда не бросает."""
        if self._broken or self._stream is None:
            return
        try:
            self._stream.flush()
        except Exception:
            self._broken = True

    def __getattr__(self, name: str) -> Any:
        """Делегирует ``encoding``/``isatty``/``fileno``/``closed``/… оригиналу."""
        return getattr(self._stream, name)


def _reconfigure_streams() -> None:
    """UTF-8 для stdout/stderr, если это возможно. Ошибки — молча."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def _is_null_stream(stream: object) -> bool:
    """True, если поток — nul/None/закрыт (бесполезен для логов)."""
    if stream is None:
        return True
    try:
        if getattr(stream, "closed", False):
            return True
        name = str(getattr(stream, "name", "") or "").strip().lower()
        return name in ("nul", "nul:", "/dev/null")
    except Exception:
        return True

def _redact_filter(record: dict) -> bool:
    """Loguru-filter: вырезает секреты из сообщения ДО форматирования.

    Мутирует ``record["message"]`` in-place. Возвращает ``True`` всегда —
    строку не отбрасываем, только чистим. Loguru применяет ``format``
    ПОСЛЕ фильтра, поэтому ``{message}`` в format-строке получит уже
    очищенный текст.

    Покрывает ``logger.info(f"…{secret}…")``, ``logger.opt(exception=…)``
    (только сообщение; сам трейсбек не чистим — см. §12 «Подводные камни»),
    ``logger.bind(...)`` (тоже message).
    """
    try:
        msg = record.get("message")
        if isinstance(msg, str) and msg:
            cleaned, hits = redaction.redact(msg)
            if cleaned != msg:
                record["message"] = cleaned
                # Не логируем в тот же sink (рекурсия): пишем в debug-уровень
                # напрямую в файл нельзя — sink ещё не отформатирован.
                # Просто оставляем пометку в самом сообщении.
                record["message"] = f"{cleaned}  [redacted:{','.join(hits)}]"
    except Exception:
        # Фильтр не должен ронять логирование ни при каких условиях.
        pass
    return True


def setup_logger(log_dir: Path, level: str = "INFO") -> None:
    """Настраивает loguru с ротацией 10MB, хранение 7 дней.

    ВАЖНО: stderr-канал оборачивается в ``_SafeStream``, поэтому сбой
    записи в stderr (nul/cp1251/закрытый поток) больше не может уронить
    приложение. Файл — основной и всегда рабочий канал.

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

    # 2. stderr — только если это не nul/None. Оборачиваем в _SafeStream:
    #    cast(TextIO, ...) нужен ТОЛЬКО для type checker'а — Pylance/mypy
    #    не выводят _SafeStream как TextIO (у него нет явных read/readline/
    #    encoding — они приходят через __getattr__). Runtime не меняется.
    if not _is_null_stream(sys.stderr):
        try:
            safe_stream = _SafeStream(sys.stderr)
            sys.stderr = cast(TextIO, safe_stream)
            logger.add(
                cast(TextIO, safe_stream),
                level=level,
                format=fmt,
                catch=True,
                filter=_redact_filter,  # 0.1.2
            )
        except Exception:
            # stderr сломан окончательно — файл всё равно останется.
            pass

    # 3. Файл — основной канал. Всегда работает.
    logger.add(
        log_file,
        rotation="10 MB",
        retention="7 days",
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        catch=True,
        filter=_redact_filter,  # 0.1.2
    )
