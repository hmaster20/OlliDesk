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

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

# Цвета семантических индикаторов (в стиле «приборной панели»).
_C_OK = "#98c379"
_C_WARN = "#e5c07b"
_C_ERR = "#e06c75"
_C_OFF = "#7a7a7a"
_C_INFO = "#d4a373"


class _PillLabel(QLabel):
    """Кликабельный статус-индикатор в стиле «pill».

    2.5: `set_pulse(True)` включает мигание точки (QTimer 500 мс,
    toggle между двумя оттенками) — визуальный признак «программа
    работает». Tooltip можно переопределить независимо от текста.
    """

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
        self._current_color: str = _C_OFF
        self._current_text: str = ""
        self._pulse_active: bool = False
        self._pulse_on: bool = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(500)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

    def set_state(self, text: str, color: str, tooltip: str = "") -> None:
        """Обновляет текст и цвет индикатора (цвет — как маркер)."""
        self._current_text = text
        self._current_color = color
        self._apply()

    def set_tooltip(self, tooltip: str) -> None:
        """Только tooltip (текст/цвет не трогаются)."""
        if tooltip:
            self.setToolTip(tooltip)

    def set_pulse(self, active: bool) -> None:
        """Включает/выключает мигание точки."""
        if active == self._pulse_active:
            return
        self._pulse_active = active
        if active:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse_on = False
            self._apply()

    def _on_pulse_tick(self) -> None:
        self._pulse_on = not self._pulse_on
        self._apply()

    def _apply(self) -> None:
        color = self._current_color
        if self._pulse_active and self._pulse_on:
            color = "#f0d494"  # осветлённый цвет — «вспышка»
        self.setText(f'<span style="color:{color};">●</span> {self._current_text}')
        self.setTextFormat(Qt.TextFormat.RichText)

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

        # Индикатор активности (2.5): пульсирует пока идёт работа,
        # tooltip — последнее событие из EventBus.
        self.activity = _PillLabel("💤 idle")
        self.activity.setToolTip("Активность: ожидание")
        layout.addWidget(self.activity)

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

        # [2.10] Бюджет + причина остановки.
        self.budget = _PillLabel("budget")
        self.budget.setToolTip("Мягкий бюджет агента (итерации · токены · время)")
        layout.addWidget(self.budget)

        # Начальное состояние.
        self.set_backend("slm", "SLM", "—")
        self.set_pipeline("💤", "не активирован")
        self.set_tok_per_sec(0.0)
        self.set_rag(0, "не проиндексирован")
        self.set_tools_active(False)
        self.set_budget(None)

    # ------------------------------------------------------------------
    # Публичный API (вызывается из MainWindow/ChatPanel)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 2.5: активность + последнее событие
    # ------------------------------------------------------------------

    _ACTIVITY_LABELS: dict[str, tuple[str, str]] = {
        "idle": ("💤 idle", _C_OFF),
        "thinking": ("🤔 думает", _C_WARN),
        "tool": ("🔧 tool", _C_INFO),
        "streaming": ("✍️ стрим", _C_OK),
        "error": ("❌ ошибка", _C_ERR),
        "cancelled": ("⏹ стоп", _C_OFF),
    }

    def set_activity_state(self, state: str) -> None:
        """Обновляет индикатор активности (2.5)."""
        label, color = self._ACTIVITY_LABELS.get(state, ("●", _C_OFF))
        self.activity.set_state(label, color)
        self.activity.set_pulse(state in ("thinking", "tool", "streaming"))
        if state == "idle":
            self.activity.set_tooltip("Активность: ожидание")
        elif state == "cancelled":
            self.activity.set_tooltip("Остановлено пользователем")

    def set_last_event_tooltip(self, kind: str, payload: dict) -> None:
        """Tooltip индикатора активности — последнее событие (2.5)."""
        text = f"{kind}"
        msg = payload.get("message")
        if isinstance(msg, str) and msg:
            text += f": {msg[:120]}"
        else:
            # Показываем ключевые поля payload (без private-ключей).
            for key in ("query", "name", "model_id", "model", "error"):
                if payload.get(key):
                    text += f": {str(payload[key])[:120]}"
                    break
        self.activity.set_tooltip(text)

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

    # ------------------------------------------------------------------
    # [2.10] Бюджет агента и причина остановки
    # ------------------------------------------------------------------

    def set_budget(self, snapshot: dict | None) -> None:
        """Обновляет индикатор бюджета.

        Args:
            snapshot: ``SoftBudget.snapshot()`` или ``None`` (сброс).
                Ключи: ``iterations``, ``iterations_limit``, ``tokens``,
                ``tokens_limit``, ``time_s``, ``time_limit_s``, ``status``.
        """
        if snapshot is None:
            self.budget.set_state("— budget", _C_OFF, "Бюджет не активен")
            return

        status = str(snapshot.get("status", "ok"))
        iterations = int(snapshot.get("iterations", 0))
        iterations_limit = int(snapshot.get("iterations_limit", 0))
        tokens = int(snapshot.get("tokens", 0))
        time_s = float(snapshot.get("time_s", 0.0))

        color = _C_OK if status == "ok" else _C_WARN if status == "warning" else _C_ERR
        icon = "" if status == "ok" else "⚠️ " if status == "warning" else "⛔ "
        text = f"{icon}{iterations}/{iterations_limit} ит."
        tooltip = (
            f"Итерации: {iterations}/{iterations_limit}\n"
            f"Токены: {tokens}/{int(snapshot.get('tokens_limit', 0))}\n"
            f"Время: {time_s:.0f}с/{int(snapshot.get('time_limit_s', 0))}с"
        )
        self.budget.set_state(text, color, tooltip)

    def set_finish_reason(self, reason: str | None) -> None:
        """Показывает причину остановки последнего прогона.

        Args:
            reason: ``"stop"`` | ``"length"`` | ``"budget.exceeded"`` |
                ``"user_cancel"`` | ``None``.
        """
        if not reason:
            return
        labels = {
            "stop": ("✅ завершено", _C_OK),
            "length": ("✂️ обрезано (max_tokens)", _C_WARN),
            "tool_calls": ("🔧 вызовы инструментов", _C_INFO),
            "budget.exceeded": ("⛔ бюджет исчерпан", _C_ERR),
            "user_cancel": ("⏹ отменено пользователем", _C_OFF),
        }
        text, color = labels.get(reason, (reason, _C_OFF))
        self.budget.set_state(text, color, f"finish_reason={reason}")

    def set_tools_from_label(self, label: str) -> None:
        """Совместимость с текущим `tool_status_label.setText(...)`."""
        self.tools.set_state(label, _C_OK if "актив" in label.lower() else _C_WARN, label)


__all__ = ["StatusDashboard"]
