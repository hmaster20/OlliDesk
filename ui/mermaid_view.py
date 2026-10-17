"""Mermaid-виджет (опциональная альтернатива ASCII-схеме).

Базово OlliDesk отдаёт ASCII (работает офлайн, в любом терминале/виджете).
Mermaid — по переключателю: рендер через QWebEngineView + локальный mermaid.min.js
(бандлится в приложение, НЕ тянется из сети → локальность соблюдена) либо
растрирование через mermaid-cli при наличии node. Если ни того ни другого —
UI тихо показывает ASCII (graceful degradation).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QStackedWidget

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None       # type: ignore
    _HAS_WEBENGINE = False

_MERmaid_JS_BUNDLE = Path(__file__).resolve().parent / "vendor" / "mermaid.min.js"

_HTML = """<!doctype html><html><head><meta charset="utf-8">
<script src="{mermaid_src}"></script></head>
<body style="background:#1e1e2e;color:#e6e6e6">
<div class="mermaid">{code}</div>
<script>mermaid.initialize({{startOnLoad:true,theme:'dark'}});</script>
</body></html>"""


class DiagramView(QStackedWidget):
    """Переключатель ASCII <-> Mermaid с деградацией в ASCII."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ascii = QLabel(); self._ascii.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ascii.setStyleSheet("font-family:Consolas,monospace;font-size:12px;color:#cdd6f4;background:#181825;padding:8px;")
        self._web = None
        self.addWidget(self._ascii)        # индекс 0 = база
        self.set_current_ascii("")

    def enable_mermaid(self) -> bool:
        if not _HAS_WEBENGINE or not _MERMAID_JS_BUNDLE.exists():
            return False
        self._web = QWebEngineView()
        self.addWidget(self._web)          # индекс 1
        return True

    def show_ascii(self, text: str) -> None:
        self._ascii.setText(text)
        self.setCurrentIndex(0)

    def show_mermaid(self, code: str) -> None:
        if self._web is None:
            return
        src = _MERMAID_JS_BUNDLE.as_uri() if _MERMAID_JS_BUNDLE.exists() else ""
        html = _HTML.format(mermaid_src=src, code=code.replace("</", "<\\/"))
        self._web.setHtml(html, QUrl("file://"))
        self.setCurrentIndex(1)

    def set_current_ascii(self, text: str) -> None:
        self._ascii.setText(text)