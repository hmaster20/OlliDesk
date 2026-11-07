"""Mermaid-виджет (опциональная альтернатива ASCII-схеме).

Базово OlliDesk отдаёт ASCII (работает офлайн, в любом терминале/виджете).
Mermaid — по переключателю: рендер через QWebEngineView + локальный
mermaid.min.js (бандлится в приложение, НЕ тянется из сети → локальность
соблюдена). Если WebEngine или vendor-файла нет — UI тихо показывает ASCII
(graceful degradation).

Архитектура:
  * ``ui/web_editor/mermaid_view.html`` — статический шаблон; подключает
    ``vendor/mermaid.min.js`` относительным путём. Резолвится только при
    ``load(QUrl.fromLocalFile)`` — поэтому ``setHtml`` не используется.
  * ``enable_mermaid()`` грузит HTML из файла и ждёт ``loadFinished``;
    код диаграммы кладётся в ``_pending_mermaid_code`` и рендерится либо
    сразу (если страница уже загружена), либо в ``_on_web_loaded``. Это
    устраняет гонку «show_mermaid вызван до загрузки QWebEngine».
"""

from __future__ import annotations

import json

from loguru import logger
from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

from core.utils import get_resource_path

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None  # type: ignore[assignment,misc]
    _HAS_WEBENGINE = False

# ВАЖНО: mermaid.min.js качается `make download-mermaid` в
# `ui/web_editor/vendor/mermaid.min.js`. Раньше путь указывал на
# `ui/vendor/mermaid.min.js` — файла там не было, кнопка Mermaid всегда
# была disabled.
_MERMAID_JS = get_resource_path("ui/web_editor/vendor/mermaid.min.js")
_MERMAID_HTML = get_resource_path("ui/web_editor/mermaid_view.html")


class DiagramView(QStackedWidget):
    """Переключатель ASCII ↔ Mermaid с деградацией в ASCII."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ascii = QLabel()
        self._ascii.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._ascii.setStyleSheet(
            "font-family:Consolas,monospace;font-size:12px;color:#cdd6f4;background:#181825;padding:8px;"
        )
        self._web: QWebEngineView | None = None
        self._mermaid_loaded = False
        self._pending_mermaid_code = ""
        self._theme = "dark"

        self.addWidget(self._ascii)  # индекс 0
        self.set_current_ascii("")

    # ------------------------------------------------------------------
    # ASCII
    # ------------------------------------------------------------------

    def show_ascii(self, text: str) -> None:
        self._ascii.setText(text)
        self.setCurrentIndex(0)

    def set_current_ascii(self, text: str) -> None:
        self._ascii.setText(text)

    # ------------------------------------------------------------------
    # Mermaid
    # ------------------------------------------------------------------

    def enable_mermaid(self) -> bool:
        """Создаёт QWebEngineView и грузит HTML-шаблон.

        Returns:
            True, если WebEngine доступен и шаблон + vendor/mermaid.min.js
            существуют. False — кнопка Mermaid остаётся disabled, в лог
            пишется причина.
        """
        if not _HAS_WEBENGINE:
            logger.info("Mermaid недоступен: PySide6.QtWebEngineWidgets не импортируется")
            return False
        if not _MERMAID_HTML.exists():
            logger.warning(f"Mermaid недоступен: нет HTML-шаблона {_MERMAID_HTML}")
            return False
        if not _MERMAID_JS.exists():
            logger.warning(
                f"Mermaid недоступен: нет {_MERMAID_JS}. "
                "Запустите `make download-mermaid` "
                "(или `python -m ui.web_editor.download_mermaid`)."
            )
            return False

        self._web = QWebEngineView()
        # file-based load: относительный `vendor/mermaid.min.js` из HTML
        # резолвится корректно. setHtml этого не гарантирует.
        self._web.loadFinished.connect(self._on_web_loaded)
        self._web.load(QUrl.fromLocalFile(str(_MERMAID_HTML)))
        self.addWidget(self._web)  # индекс 1
        return True

    @Slot(bool)
    def _on_web_loaded(self, ok: bool) -> None:
        self._mermaid_loaded = bool(ok)
        if not ok:
            logger.warning("Mermaid: QWebEngineView не смог загрузить HTML-шаблон")
            return
        if self._pending_mermaid_code:
            self._render_mermaid(self._pending_mermaid_code)

    def show_mermaid(self, code: str) -> None:
        """Показывает mermaid-код. Если страница ещё грузится — рендер отложится."""
        if self._web is None:
            return
        self._pending_mermaid_code = code or ""
        if self._mermaid_loaded:
            self._render_mermaid(self._pending_mermaid_code)
        self.setCurrentIndex(1)

    def _render_mermaid(self, code: str) -> None:
        if self._web is None:
            return
        # json.dumps экранирует кавычки/переводы строк — безопасно для JS.
        # НЕ используем .format(): mermaid-код содержит {} (например, A{Decision}).
        escaped = json.dumps(code)
        theme_js = json.dumps("default" if self._theme == "light" else "dark")
        try:
            self._web.page().runJavaScript(f"renderMermaid({escaped}, {theme_js})")
        except Exception:
            logger.debug("Mermaid: runJavaScript failed", exc_info=True)

    def set_theme(self, theme: str) -> None:
        """Переключает тему рендера ('light' / 'dark')."""
        self._theme = theme or "dark"
        if self._mermaid_loaded and self._pending_mermaid_code:
            self._render_mermaid(self._pending_mermaid_code)
