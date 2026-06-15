"""Точка входа в OlliDesk."""

import os
import sys

# Отключаем телеметрию ChromaDB (posthog)
os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"
os.environ["POSTHOG_DISABLED"] = "1"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.app import main

if __name__ == "__main__":
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    sys.exit(main())