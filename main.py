"""Точка входа в OlliDesk."""

import logging
import os
import sys

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

if __name__ == "__main__":
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    sys.exit(main())