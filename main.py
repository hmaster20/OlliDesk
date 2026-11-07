"""Точка входа в OlliDesk."""

import logging
import os
import sys
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from core.app import main

# Отключаем телеметрию ChromaDB (posthog)
os.environ.update(
    {
        "CHROMA_TELEMETRY_ENABLED": "false",
        "POSTHOG_DISABLED": "1",
        "CHROMA_API_TELEMETRY_ENABLED": "false",
    }
)

# Подавляем телеметрию posthog принудительно
logging.getLogger("posthog").setLevel(logging.CRITICAL)
logging.getLogger("chromadb").setLevel(logging.WARNING)

# ChromaDB пишет свой лог через дочерние логгеры, например
# "chromadb.segment.impl.metadata.sqlite" — глобальный setLevel на "chromadb"
# их не покрывает. Поэтому рекурсивно глушим всё, что начинается с "chromadb".
# Иначе консоль спамит строками вида "Add of existing embedding ID: …",
# которые выглядят как предупреждения, но не являются ошибками.
logging.getLogger("chromadb").setLevel(logging.ERROR)
for _logger_name in list(logging.root.manager.loggerDict.keys()):
    if _logger_name.startswith("chromadb"):
        logging.getLogger(_logger_name).setLevel(logging.ERROR)


# Создаем класс-заглушку с нужными атрибутами
class PosthogMock:
    def capture(*a, **kw):
        pass

    def identify(*a, **kw):
        pass

    def flush(*a, **kw):
        pass

    disabled = True


# Приводим к Any, чтобы Python разрешил подменить им целый модуль в sys.modules
_posthog_mock: Any = PosthogMock()
sys.modules["posthog"] = _posthog_mock


# Настройка обработчика сообщений Qt
def qt_message_handler(mode, context, message):
    if mode == QtMsgType.QtDebugMsg:
        logger.debug(f"Qt: {message}")
    elif mode == QtMsgType.QtInfoMsg:
        logger.info(f"Qt: {message}")
    elif mode == QtMsgType.QtWarningMsg:
        logger.warning(f"Qt: {message}")
    elif mode == QtMsgType.QtCriticalMsg:
        logger.critical(f"Qt: {message}")
    elif mode == QtMsgType.QtFatalMsg:
        logger.critical(f"Qt FATAL: {message}")


qInstallMessageHandler(qt_message_handler)


def global_exception_handler(exc_type, exc_value, exc_tb):
    """Единая точка необработанных исключений.

    Использует `logger.opt(exception=...)` вместо ручного
    `traceback.format_exception`: Loguru сам построит трейсбек и не упадёт
    на нестандартных объектах, которые PySide6 иногда передаёт в excepthook
    (например, когда слот Qt бросил исключение из фонового контекста).
    """
    if exc_type is None and exc_value is None and exc_tb is None:
        # PySide6 иногда вызывает excepthook "пустым" при завершении —
        # не засоряем лог шумом.
        logger.debug("global_exception_handler: получен пустой вызов (PySide6)")
        return
    logger.opt(exception=(exc_type, exc_value, exc_tb)).error("Необработанное исключение")


sys.excepthook = global_exception_handler

if __name__ == "__main__":
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        sys.exit(main())
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)
