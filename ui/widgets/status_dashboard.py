"""Единая приборная панель для статус-бара (9.7).

Заменяет `ollama_status_label` + `backend_status_label` +
`pipeline_status_label` из MainWindow. Все статусы — в одном месте,
с tooltip'ами. Элементы:

  * `backend` — SLM / Ollama / Резерв (кликабельно для переключения);
  * `planner` — id модели + device;
  * `analyst` — id модели + device;
  * `pipeline` — ⏳/⚡/⚠️/💤;
  * `tokens_per_sec` — из `last_stats` после генерации;
  * `rag` — количество чанков / статус;
  * `tools` — активность инструментов текущего запроса.

Встраивается в `MainWindow._setup_status_bar` через
`self.status_bar.addPermanentWidget(self.dashboard)`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

# Цвета семантических индикаторов (в стиле «приборной панели»).
_C_OK = "#98c379"
_C_WARN = "#e5c07b"
_C_ERR = "#e06c75"
_C_OFF = "#7a7a7a"
_C_INFO = "#d4a373"


class _PillLabel(QLabel):
    """Кликабельный статус-индикатор в стиле «pill»."""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QLabel {"
            " background-color: rgba(80, 80, 80, 0.4);"
            " color: #bbbbbb;"
            " border-radius: 10px;"
            " padding: 2px 8px;"
            " font-size: 11px;"
            "}"
            "QLabel:hover {"
            " background-color: rgba(212, 163, 115, 0.25);"
            " color: #d4a373;"
            "}"
        )

    def set_state(self, text: str, color: str, tooltip: str = "") -> None:
        """Обновляет текст и цвет индикатора (цвет — как маркер)."""
        self.setText(f'<span style="color:{color};">●</span> {text}')
        self.setTextFormat(Qt.TextFormat.RichText)
        if tooltip:
            self.setToolTip(tooltip)
        self._current_color = color

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatusDashboard(QWidget):
    """Приборная панель статусов: backend, planner, analyst, pipeline, tok/s, RAG, tools."""

    backend_toggle_requested = Signal()
    slm_models_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Бэкенд: SLM / Ollama / Резерв.
        self.backend = _PillLabel("backend")
        self.backend.setToolTip("Активный LLM-бэкенд")
        self.backend.clicked.connect(self.backend_toggle_requested.emit)
        layout.addWidget(self.backend)

        # Пайплайн: planner + analyst.
        self.pipeline = _PillLabel("pipeline")
        self.pipeline.setToolTip("Двухмодельный пайплайн (planner + analyst)")
        self.pipeline.clicked.connect(self.slm_models_requested.emit)
        layout.addWidget(self.pipeline)

        # tok/s.
        self.tok_per_sec = _PillLabel("tok/s")
        self.tok_per_sec.setToolTip("Скорость генерации (обновляется после каждого ответа)")
        layout.addWidget(self.tok_per_sec)

        # RAG.
        self.rag = _PillLabel("RAG")
        self.rag.setToolTip("Индекс RAG: количество чанков / статус")
        layout.addWidget(self.rag)

        # Tools.
        self.tools = _PillLabel("tools")
        self.tools.setToolTip("Активность инструментов текущего запроса")
        layout.addWidget(self.tools)

        # Начальное состояние.
        self.set_backend("slm", "SLM", "—")
        self.set_pipeline("💤", "не активирован")
        self.set_tok_per_sec(0.0)
        self.set_rag(0, "не проиндексирован")
        self.set_tools_active(False)

    # ------------------------------------------------------------------
    # Публичный API (вызывается из MainWindow/ChatPanel)
    # ------------------------------------------------------------------

    def set_backend(self, kind: str, display: str, model: str) -> None:
        """kind ∈ {'slm','ollama','rule','none'}."""
        color = {
            "slm": _C_OK,
            "ollama": _C_INFO,
            "rule": _C_WARN,
            "none": _C_ERR,
        }.get(kind, _C_OFF)
        tooltip = f"Бэкенд: {kind}\nМодель: {model}\nКлик — переключить"
        self.backend.set_state(display, color, tooltip)

    def set_pipeline(self, icon: str, description: str, tooltip: str = "") -> None:
        color = _C_OK if icon == "⚡" else _C_WARN if icon in ("⚠️", "⏳") else _C_OFF
        self.pipeline.set_state(f"{icon} {description}", color, tooltip or description)

    def set_pipeline_loading(self, message: str) -> None:
        self.pipeline.set_state(f"⏳ {message}", _C_WARN, message)

    def set_tok_per_sec(self, value: float) -> None:
        if value <= 0:
            self.tok_per_sec.set_state("— tok/s", _C_OFF, "Скорость ещё не измерена")
        else:
            self.tok_per_sec.set_state(f"{value:.1f} tok/s", _C_OK, f"Скорость генерации: {value:.1f} tok/s")

    def set_rag(self, chunks: int, status: str) -> None:
        if chunks > 0:
            color = _C_OK
            text = f"RAG: {chunks} чанков"
        elif status.startswith("индекс"):
            color = _C_WARN
            text = f"RAG: {status}"
        else:
            color = _C_OFF
            text = "RAG: —"
        self.rag.set_state(text, color, f"RAG-индекс: {status}, чанков: {chunks}")

    def set_tools_active(self, active: bool, count: int = 0) -> None:
        if active:
            self.tools.set_state(f"🔧 tools ({count})", _C_OK, "Инструменты активны")
        else:
            self.tools.set_state("🔧 tools", _C_OFF, "Инструменты не используются")

    def set_tools_from_label(self, label: str) -> None:
        """Совместимость с текущим `tool_status_label.setText(...)`."""
        self.tools.set_state(label, _C_OK if "актив" in label.lower() else _C_WARN, label)


__all__ = ["StatusDashboard"]
