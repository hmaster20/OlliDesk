"""Точка входа в OlliDesk."""

import logging
import os
import sys
import traceback
from loguru import logger


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
import types

_posthog_mock = types.ModuleType("posthog")
_posthog_mock.capture = lambda *a, **kw: None
_posthog_mock.identify = lambda *a, **kw: None
_posthog_mock.flush = lambda *a, **kw: None
_posthog_mock.disabled = True
sys.modules["posthog"] = _posthog_mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.app import main

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
