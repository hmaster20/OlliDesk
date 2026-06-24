"""Точка входа в OlliDesk."""

import logging
import os
import sys
import traceback
import types
from loguru import logger
from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication
from core.app import main

# Отключаем телеметрию ChromaDB (posthog)
os.environ.update({
    "CHROMA_TELEMETRY_ENABLED": "false",
    "POSTHOG_DISABLED": "1",
    "CHROMA_API_TELEMETRY_ENABLED": "false",
})

# Подавляем телеметрию posthog принудительно
logging.getLogger("posthog").setLevel(logging.CRITICAL)
logging.getLogger("chromadb").setLevel(logging.WARNING)

# Мок для posthog, чтобы избежать ошибок capture()
_posthog_mock = types.ModuleType("posthog")
_posthog_mock.capture = lambda *a, **kw: None
_posthog_mock.identify = lambda *a, **kw: None
_posthog_mock.flush = lambda *a, **kw: None
_posthog_mock.disabled = True
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
    logger.error("Необработанное исключение:")
    logger.error("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    # Можно показать диалог с ошибкой

sys.excepthook = global_exception_handler

if __name__ == "__main__":
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        sys.exit(main())
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)
