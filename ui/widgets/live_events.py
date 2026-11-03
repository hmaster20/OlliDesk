"""«Живая» лента событий под чатом (2.5 / 9.5.3).

`QtEventBus` — QObject-мост из `core.event_bus.EventBus` (plain-Python)
в Qt-сигнал. Подписка на EventBus идёт в GUI-потоке (ChatPanel),
эмит из любого потока; Qt выбирает QueuedConnection автоматически
(sender и receiver — один QObject, но thread() — GUI; emit из worker'а).

`LiveEventsPanel` — сворачиваемый QTextBrowser под `status_label`.
Свёрнут по умолчанию — видна строка «📡 События · N · последнее: …».
Развёрнут — фиксированная высота 200 px, авто-скролл, чекбокс
«Только важное» (level ≥ WARNING + `tool.error` + `slm.failed`).

Используется в `ChatPanel`: создаётся в `_setup_ui`, подключается
к `QtEventBus.event_emitted`. Тест-сценарий: `pytest tests/ui/test_live_events.py`.
"""

from __future__ import annotations

import contextlib
from typing import Final

from loguru import logger
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import subscribe
from core.events import EventKind

# События, которые «важны всегда» (не зависят от чекбокса).
# Остальные log-события — только если level >= WARNING.
_IMPORTANT_KINDS: Final[frozenset[str]] = frozenset(
    {
        EventKind.TOOL_ERROR.value,
        EventKind.SLM_FAILED.value,
        # 2.5 + 2.10: бюджет и завершение прогона — всегда видны.
        EventKind.BUDGET_EXCEEDED.value,
        EventKind.AGENT_FINISHED.value,
    }
)

# Максимальная высота тела в развёрнутом состоянии (px).
_BODY_MAX_HEIGHT = 200

# Длина превью «последнего события» в свёрнутом заголовке.
_PREVIEW_CHARS = 90

# Цвета по уровню/виду (в стиле «серого лога»).
_C_LOG_INFO = "#888"
_C_LOG_WARNING = "#e5c07b"
_C_LOG_ERROR = "#e06c75"
_C_TOOL = "#7a9ac8"
_C_RAG = "#9a9ac8"
_C_SLM = "#c8a878"
_C_BUDGET = "#e5c07b"
_C_OTHER = "#a8a8b8"

# 2.5: фильтр по домену — первый сегмент `kind` до точки.
# Порядок важен для UX: «все» первым, остальные — по алфавиту.
_DOMAIN_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "Все"),
    ("log", "log"),
    ("tool", "tool"),
    ("llm", "llm"),
    ("rag", "rag"),
    ("slm", "slm"),
    ("index", "index"),
    ("budget", "budget"),
    ("agent", "agent"),
    ("memory", "memory"),
    ("orchestrator", "orchestrator"),
    ("policy", "policy"),
    ("mcp", "mcp"),
    ("browser", "browser"),
)


class QtEventBus(QObject):
    """QObject-мост из `EventBus` (plain-Python) в Qt-сигнал.

    Создаётся в GUI-потоке (в `ChatPanel._setup_ui`), подписывается на
    `EventBus` единожды. `_on_event` вызывается синхронно в потоке-эмиттере;
    внутри — `event_emitted.emit(...)`, Qt перекладывает в очередь и
    доставляет в GUI-поток (AutoConnection → QueuedConnection при разных
    thread()).

    В `shutdown()` — отписка от EventBus (вызывается из `ChatPanel.shutdown`
    и `MainWindow.closeEvent`).
    """

    event_emitted = Signal(str, dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsubscribe = subscribe(None, self._on_event)

    @Slot(str, dict)
    def _on_event(self, kind: str, payload: dict) -> None:
        """Синхронный callback от EventBus (может быть в worker-потоке)."""
        try:
            self.event_emitted.emit(kind, payload)
        except Exception:
            # Мост не должен валить worker.
            logger.debug("QtEventBus: не удалось эмитнуть событие", exc_info=True)

    def shutdown(self) -> None:
        """Снимает подписку с EventBus (идемпотентно)."""
        if self._unsubscribe is not None:
            with contextlib.suppress(Exception):
                self._unsubscribe()
            self._unsubscribe = None


class LiveEventsPanel(QWidget):
    """Сворачиваемая лента событий (2.5 / 9.5.3)."""

    # Проброс «последнего события» наружу (для tooltip в StatusDashboard).
    latest_event = Signal(str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._count = 0
        self._important_only = False
        self._domain_filter: str = "all"
        self._last_kind: str = ""
        self._last_payload: dict = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        container = QFrame(self)
        container.setObjectName("_live_events")
        container.setStyleSheet(
            "QFrame#_live_events {"
            " background: rgba(120, 120, 140, 0.04);"
            " border: 1px solid #2f2f3c;"
            " border-radius: 6px;"
            " padding: 4px 8px;"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(container)

        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._header_label = QLabel()
        self._header_label.setTextFormat(Qt.TextFormat.RichText)
        self._header_label.setStyleSheet("font-size: 11px;")
        header.addWidget(self._header_label, stretch=1)

        self._domain_combo = QComboBox()
        self._domain_combo.setStyleSheet("font-size: 11px; color: #888;")
        self._domain_combo.setToolTip(
            "Фильтр по домену события (tool, llm, rag, budget, …)."
        )
        for key, label in _DOMAIN_FILTERS:
            self._domain_combo.addItem(label, key)
        self._domain_combo.currentIndexChanged.connect(self._on_domain_changed)
        header.addWidget(self._domain_combo)

        self._important_check = QCheckBox("Только важное")
        self._important_check.setStyleSheet("font-size: 11px; color: #888;")
        self._important_check.setToolTip(
            "Скрыть INFO-логи. Показывать только WARNING/ERROR/CRITICAL "
            "и ошибки инструментов."
        )
        self._important_check.toggled.connect(self._on_important_toggled)
        header.addWidget(self._important_check)

        self._clear_btn = QPushButton("Очистить")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: #6ba3d6; font-size: 11px; padding: 0 6px; }"
            " QPushButton:hover { color: #8bc0f0; text-decoration: underline; }"
        )
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)

        self._toggle_btn = QPushButton("▼ Развернуть")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444;"
            " border-radius: 10px; color: #aaa; font-size: 11px;"
            " padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(128,128,128,0.15);"
            " color: #fff; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_body)
        header.addWidget(self._toggle_btn)

        body.addLayout(header)

        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setReadOnly(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setMaximumHeight(_BODY_MAX_HEIGHT)
        self._body.setStyleSheet(
            "QTextBrowser {"
            " background: transparent; border: none; color: #9a9a9a;"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 2px 0;"
            "}"
        )
        self._body.setVisible(False)
        body.addWidget(self._body)

        self._refresh_header()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @Slot(str, dict)
    def append_event(self, kind: str, payload: dict) -> None:
        """Принимает событие из `QtEventBus.event_emitted` (GUI-поток)."""
        self._last_kind = kind
        self._last_payload = payload
        self.latest_event.emit(kind, payload)

        # 2.5: фильтр по домену (первый сегмент kind до точки).
        if self._domain_filter != "all":
            domain = kind.split(".", 1)[0]
            if domain != self._domain_filter:
                return
        if self._important_only and not self._is_important(kind, payload):
            return

        self._count += 1
        self._render_line(kind, payload)
        self._refresh_header()

    @Slot()
    def clear(self) -> None:
        """Полная очистка ленты."""
        self._body.clear()
        self._count = 0
        self._refresh_header()

    @property
    def event_count(self) -> int:
        """Сколько событий отрисовано (для тестов)."""
        return self._count

    @property
    def latest_kind(self) -> str:
        """Вид последнего события (для тестов)."""
        return self._last_kind

    # ------------------------------------------------------------------
    # Фильтр / рендер
    # ------------------------------------------------------------------

    def _is_important(self, kind: str, payload: dict) -> bool:
        if kind in _IMPORTANT_KINDS:
            return True
        if kind == EventKind.LOG.value:
            return str(payload.get("level", "")).upper() in (
                "WARNING",
                "ERROR",
                "CRITICAL",
            )
        return False

    @Slot(bool)
    def _on_important_toggled(self, _checked: bool) -> None:
        self._important_only = self._important_check.isChecked()

    @Slot(int)
    def _on_domain_changed(self, _index: int) -> None:
        """2.5: смена фильтра домена — обновляет текущий вид."""
        self._domain_filter = self._domain_combo.currentData() or "all"

    def _render_line(self, kind: str, payload: dict) -> None:
        text, color = self._format_line(kind, payload)
        safe = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        ts = str(payload.get("timestamp", ""))
        cursor = self._body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._body.setTextCursor(cursor)
        prefix = f'<span style="color:#555;">{ts} </span>' if ts else ""
        self._body.insertHtml(
            f'{prefix}<span style="color:{color};">{safe}</span>'
        )
        cursor = self._body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._body.setTextCursor(cursor)
        self._body.insertPlainText("\n")
        if self._expanded:
            QTimer.singleShot(0, self._scroll_to_end)

    @staticmethod
    def _format_line(kind: str, payload: dict) -> tuple[str, str]:
        """(текст, CSS-цвет) для строки ленты."""
        if kind == EventKind.LOG.value:
            level = str(payload.get("level", "INFO"))
            color = {
                "DEBUG": _C_LOG_INFO,
                "INFO": _C_LOG_INFO,
                "WARNING": _C_LOG_WARNING,
                "ERROR": _C_LOG_ERROR,
                "CRITICAL": _C_LOG_ERROR,
            }.get(level, _C_LOG_INFO)
            source = str(payload.get("source", ""))
            msg = str(payload.get("message", ""))
            label = f"[{level}] " + (f"{source} — " if source else "") + msg
            return label, color

        if kind == EventKind.TOOL_STARTED.value:
            return f"🔧 {payload.get('name', '?')}({payload.get('args_hash', '')})", _C_TOOL
        if kind == EventKind.TOOL_FINISHED.value:
            ok = "✓" if not payload.get("is_error") else "✗"
            dur = payload.get("duration_ms", 0)
            return f"   {ok} {payload.get('name', '?')} ({dur} ms)", _C_TOOL
        if kind == EventKind.TOOL_ERROR.value:
            return f"   ✗ ошибка: {payload.get('name', '?')}", _C_LOG_ERROR

        if kind == EventKind.RAG_SEARCH_STARTED.value:
            return f"🔍 RAG: «{payload.get('query', '')}»", _C_RAG
        if kind == EventKind.RAG_SEARCH_FOUND.value:
            dur = payload.get("duration_ms", 0)
            return f"   ↳ {payload.get('n_results', 0)} чанков ({dur} ms)", _C_RAG
        if kind == EventKind.RAG_SEARCH_EMPTY.value:
            return f"   ↳ пусто ({payload.get('duration_ms', 0)} ms)", _C_RAG

        if kind == EventKind.SLM_LOADING.value:
            return f"⏳ SLM: {payload.get('model_id', '?')}", _C_SLM
        if kind == EventKind.SLM_LOADED.value:
            return f"✅ SLM загружена ({payload.get('load_ms', 0)} ms)", _C_SLM
        if kind == EventKind.SLM_FAILED.value:
            return f"❌ SLM: {payload.get('error', '')[:80]}", _C_LOG_ERROR

        if kind == EventKind.INDEX_STARTED.value:
            return f"📁 Индексация: {payload.get('total_files', 0)} файлов", _C_OTHER
        if kind == EventKind.INDEX_FINISHED.value:
            dur = payload.get("duration_ms", 0)
            return f"✅ Индексация завершена ({dur} ms)", _C_OTHER

        if kind == EventKind.LLM_REQUEST_SENT.value:
            return f"🧠 → {payload.get('model', '?')}", _C_OTHER
        if kind == EventKind.LLM_FIRST_TOKEN.value:
            return f"⚡ TTFT: {payload.get('ttft_ms', 0)} ms", _C_OTHER
        if kind == EventKind.LLM_GENERATION_DONE.value:
            tps = payload.get("tokens_per_sec", 0)
            return f"✅ Готово ({tps:.1f} tok/s)", _C_OTHER

        # --- 2.5 + 2.10: бюджет и завершение агента ---
        if kind == EventKind.BUDGET_WARNING.value:
            return (
                f"⚠️ Бюджет: {payload.get('iterations', 0)}/{payload.get('iterations_limit', 0)} ит., "
                f"{payload.get('tokens', 0)}/{payload.get('tokens_limit', 0)} tok, "
                f"{payload.get('time_s', 0):.0f}с"
            ), _C_BUDGET
        if kind == EventKind.BUDGET_EXCEEDED.value:
            return (
                f"⛔ Бюджет исчерпан: {payload.get('iterations', 0)} ит., "
                f"{payload.get('tokens', 0)} tok, {payload.get('time_s', 0):.0f}с"
            ), _C_LOG_ERROR
        if kind == EventKind.AGENT_FINISHED.value:
            reason = str(payload.get("finish_reason") or "?")
            iterations = payload.get("iterations", 0)
            elapsed = payload.get("elapsed_s", 0.0)
            icon = {
                "stop": "✅",
                "length": "✂️",
                "budget.exceeded": "⛔",
                "user_cancel": "⏹",
            }.get(reason, "•")
            color = {
                "stop": _C_OTHER,
                "length": _C_BUDGET,
                "budget.exceeded": _C_LOG_ERROR,
                "user_cancel": _C_OTHER,
            }.get(reason, _C_OTHER)
            return (
                f"{icon} Прогон завершён: {reason} "
                f"({iterations} ит., {elapsed:.0f}с)"
            ), color

        if kind == EventKind.MEMORY_RECALLED.value:
            n = payload.get("n_results", 0)
            q = str(payload.get("query", ""))[:80]
            return f"🧠 Память: {n} рефлексий по «{q}»", _C_OTHER
        if kind == EventKind.MEMORY_REMEMBERED.value:
            task = payload.get("task_type", "?")
            outcome = payload.get("outcome", "?")
            mid = payload.get("id", "?")
            return f"🧠 Память: записано #{mid} ({task}/{outcome})", _C_OTHER

        if kind == EventKind.POLICY_OVERRIDE.value:
            tool = payload.get("tool", "?")
            policy = payload.get("policy", "?")
            scope = payload.get("scope", "?")
            return f"🔒 {tool}: {policy} ({scope})", _C_OTHER

        if kind == EventKind.SEARCH_PROVIDER_USED.value:
            provider = payload.get("provider", "?")
            n = payload.get("n_results", 0)
            dur = payload.get("duration_ms", 0)
            return f"🔍 search via {provider} ({n} результатов, {dur} ms)", _C_OTHER
        if kind == EventKind.SEARCH_CHAIN_EXHAUSTED.value:
            attempted = payload.get("attempted") or []
            return (
                f"🔍 search: все провайдеры упали "
                f"({', '.join(str(a) for a in attempted)})",
                _C_LOG_ERROR,
            )

        if kind == EventKind.MCP_CONNECTED.value:
            name = payload.get("server_name", "?")
            n = payload.get("n_tools", 0)
            return f"🔌 MCP: {name} ({n} tools)", _C_OTHER
        if kind == EventKind.MCP_DISCONNECTED.value:
            name = payload.get("server_name", "?")
            return f"🔌 MCP: {name} отключён", _C_OTHER
        if kind == EventKind.MCP_TOOL_CALLED.value:
            name = payload.get("server_name", "?")
            tool = payload.get("tool_name", "?")
            dur = payload.get("duration_ms", 0)
            ok = not payload.get("is_error", False)
            mark = "✓" if ok else "✗"
            return f"🔌 {name}/{tool} {mark} ({dur} ms)", _C_OTHER
        if kind == EventKind.MCP_ERROR.value:
            name = payload.get("server_name", "?")
            err = str(payload.get("error", ""))[:120]
            return f"🔌 MCP {name}: {err}", _C_LOG_ERROR

        if kind == EventKind.ORCHESTRATOR_EVENT.value:
            sub = str(payload.get("kind") or "?")
            data = payload.get("data") or {}
            short = ", ".join(f"{k}={v}" for k, v in list(data.items())[:3])
            return f"🎼 {sub}: {short}", _C_OTHER

        if kind == EventKind.BROWSER_ACTION.value:
            action = payload.get("action", "?")
            mode = payload.get("mode", "?")
            extra = ""
            if "url" in payload:
                extra = f" {str(payload['url'])[:80]}"
            elif "selector" in payload:
                extra = f" {str(payload['selector'])[:60]}"
            return f"🌐 browser.{action}{extra} ({mode})", _C_OTHER

        return f"{kind}: {payload}", _C_OTHER

    # ------------------------------------------------------------------
    # Заголовок / разворот
    # ------------------------------------------------------------------

    def _refresh_header(self) -> None:
        parts = ['<span style="color:#888; font-weight:600;">📡 События</span>']
        if self._count:
            parts.append(f'<span style="color:#7a7a8a;">· {self._count}</span>')

        preview = self._latest_preview()
        if preview:
            safe = (
                preview.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            parts.append(
                f'<span style="color:#6a6a7a; font-style:italic;">· {safe}</span>'
            )

        self._header_label.setText(" ".join(parts))

    def _latest_preview(self) -> str:
        if not self._last_kind:
            return ""
        text, _ = self._format_line(self._last_kind, self._last_payload)
        text = text.strip().replace("\n", " ")
        if len(text) > _PREVIEW_CHARS:
            text = text[: _PREVIEW_CHARS - 1] + "…"
        return text

    def _toggle_body(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText("▲ Свернуть" if self._expanded else "▼ Развернуть")
        self._refresh_header()
        if self._expanded:
            QTimer.singleShot(0, self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        sb = self._body.verticalScrollBar()
        sb.setValue(sb.maximum())


__all__ = ["LiveEventsPanel", "QtEventBus"]