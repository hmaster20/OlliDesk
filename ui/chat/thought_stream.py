"""Серый лог рассуждений агента в реальном времени.

Виджет `ChatThoughtStream` создаётся при старте research-прогона и живёт
до конца запроса. Он собирает:

* 💭 мысли модели (свободный текст, стримится в реальном времени);
* 🔧 вызовы инструментов;
* ✓/✗ результаты;
* 💡 созданные гипотезы;
* 🔬 обновления гипотез;
* 🔁 нуджи и статусы фаз.

По умолчанию **свёрнут** — видна только строка «💭 Рассуждение · N действий
· …превью последней мысли». Кнопка «▲ Развернуть» открывает тело с
ограниченной высотой (280px) и внутренним скроллом. При отмене прогона
виджет получает `mark_interrupted()` и показывает курсивный комментарий.

Заголовок обновляется **при каждом токене мысли** — иначе свёрнутый
виджет выглядит «застывшим», и пользователь не видит, что модель работает.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Цвета сегментов (в стиле «серого лога»)
_C_THOUGHT = "#9a9a9a"
_C_ACTION = "#7a9ac8"
_C_RESULT_OK = "#8aa87a"
_C_RESULT_ERR = "#c88a7a"
_C_HYPOTHESIS = "#c8a878"
_C_NUDGE = "#a8a8b8"
_C_INTERRUPT = "#d8a878"

# Максимальная высота тела виджета в развёрнутом состоянии (пикселей).
_BODY_MAX_HEIGHT = 280

# Длина превью последней мысли в заголовке (символов).
_HEADER_PREVIEW_CHARS = 80


class ChatThoughtStream(QWidget):
    """Свёрнутый по умолчанию серый лог рассуждений агента."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._actions_count = 0
        self._hypotheses_count = 0
        self._expanded = False

        # Буфер текущей незакрытой мысли (для превью в заголовке).
        self._current_thought_buffer = ""
        self._current_thought_open = False

        container = QFrame(self)
        container.setObjectName("_thought_stream")
        container.setStyleSheet(
            "QFrame#_thought_stream {"
            " background: #1b1b22; border: 1px solid #2f2f3c;"
            " border-radius: 8px; padding: 8px 10px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.addWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # --- Заголовок ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._header_label = QLabel()
        self._header_label.setTextFormat(Qt.TextFormat.RichText)
        self._header_label.setWordWrap(False)
        header.addWidget(self._header_label, stretch=1)

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

        root.addLayout(header)

        # --- Тело ---
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
            " font-size: 12px; padding: 2px 0; }"
        )
        self._body.setVisible(False)
        root.addWidget(self._body)

        self._refresh_header()

    # ---------------------------------------------------------------
    # Заголовок
    # ---------------------------------------------------------------

    def _refresh_header(self) -> None:
        parts = ['<span style="color: #888; font-size: 12px; font-weight: 600;">💭 Рассуждение</span>']
        if self._actions_count:
            parts.append(
                f'<span style="color: #7a7a8a; font-size: 11px;">· {self._actions_count} действий</span>'
            )
        if self._hypotheses_count:
            parts.append(
                f'<span style="color: #7a7a8a; font-size: 11px;">· {self._hypotheses_count} гипотез</span>'
            )

        # Превью последней мысли — только когда виджет свёрнут
        # (в развёрнутом состоянии текст и так виден в теле).
        if not self._expanded and self._current_thought_buffer:
            preview = self._current_thought_buffer.strip().replace("\n", " ")
            if len(preview) > _HEADER_PREVIEW_CHARS:
                preview = "…" + preview[-_HEADER_PREVIEW_CHARS:]
            safe = _escape(preview)
            parts.append(
                f'<span style="color: #6a6a7a; font-size: 11px; font-style: italic;">· {safe}</span>'
            )

        self._header_label.setText(" ".join(parts))

    # ---------------------------------------------------------------
    # Сворачивание / разворот
    # ---------------------------------------------------------------

    def _toggle_body(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText("▲ Свернуть" if self._expanded else "▼ Развернуть")
        self._refresh_header()
        if self._expanded:
            QTimer.singleShot(0, self._scroll_body_to_end)

    def _scroll_body_to_end(self) -> None:
        sb = self._body.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------------------------------------------------------------
    # Запись сегментов
    # ---------------------------------------------------------------

    def _cursor_to_end(self) -> QTextCursor:
        cursor = self._body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        return cursor

    def _insert_html(self, html: str) -> None:
        cursor = self._cursor_to_end()
        self._body.setTextCursor(cursor)
        self._body.insertHtml(html)
        if self._expanded:
            QTimer.singleShot(0, self._scroll_body_to_end)

    def _insert_newline(self) -> None:
        cursor = self._cursor_to_end()
        self._body.setTextCursor(cursor)
        self._body.insertPlainText("\n")

    def append_thought_token(self, token: str) -> None:
        """Добавляет токен мысли — продолжает текущий блок."""
        if not token:
            return
        if not self._current_thought_open:
            self._insert_html('<span style="color: #7a7a8a;">💭 </span>')
            self._current_thought_open = True

        safe = token.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        self._insert_html(f'<span style="color: {_C_THOUGHT}; font-style: italic;">{safe}</span>')

        # Копим буфер для превью в заголовке (ограничиваем 500 символами —
        # хватит для последних 80 в заголовке с запасом).
        self._current_thought_buffer += token
        if len(self._current_thought_buffer) > 500:
            self._current_thought_buffer = self._current_thought_buffer[-500:]

        # Заголовок обновляем при каждом токене — пользователь видит,
        # что модель работает, даже если плашка свёрнута.
        self._refresh_header()

    def append_thought_block(self, text: str) -> None:
        """Добавляет целый блок мыслей (если стриминга не было)."""
        if not text or not text.strip():
            return
        self._close_thought_block()
        safe = (
            text.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        )
        self._insert_html(
            f'<span style="color: #7a7a8a;">💭 </span>'
            f'<span style="color: {_C_THOUGHT}; font-style: italic;">{safe}</span>'
        )
        self._insert_newline()

    def _close_thought_block(self) -> None:
        """Закрывает открытый блок мысли (перед следующим сегментом)."""
        if self._current_thought_open:
            self._insert_newline()
            self._current_thought_open = False
        self._current_thought_buffer = ""

    def append_action(self, name: str, arguments: dict | None = None) -> None:
        """Записывает вызов инструмента."""
        self._close_thought_block()
        args_summary = ""
        if arguments:
            args_summary = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        line = f'<span style="color: {_C_ACTION};">🔧 {name}</span>'
        if args_summary:
            line += f'<span style="color: #6a7a8a;">({_escape(args_summary)})</span>'
        self._insert_html(line)
        self._insert_newline()
        self._actions_count += 1
        self._refresh_header()

    def append_result(self, name: str, ok: bool, preview: str = "") -> None:
        """Записывает результат инструмента (краткая сводка)."""
        self._close_thought_block()
        icon = "✓" if ok else "✗"
        color = _C_RESULT_OK if ok else _C_RESULT_ERR
        preview_clean = (preview or "").strip().replace("\n", " ")
        if len(preview_clean) > 120:
            preview_clean = preview_clean[:117] + "…"
        line = f'<span style="color: {color};">  {icon} {name}</span>'
        if preview_clean:
            line += f' <span style="color: #6a6a7a;">— {_escape(preview_clean)}</span>'
        self._insert_html(line)
        self._insert_newline()

    def append_hypothesis(
        self,
        hypothesis_id: str,
        status: str,
        statement: str,
        evidence: str = "",
    ) -> None:
        """Записывает создание/обновление гипотезы."""
        self._close_thought_block()
        icons = {
            "open": ("🟦", "#7a9ac8"),
            "verified": ("✅", "#8aa87a"),
            "refuted": ("❌", "#c88a7a"),
            "partial": ("🟨", "#c8a878"),
            "inconclusive": ("⚪", "#7a7a8a"),
        }
        icon, color = icons.get(status, ("💡", _C_HYPOTHESIS))
        stmt = _escape(statement.strip())
        if len(stmt) > 140:
            stmt = stmt[:137] + "…"
        line = f'<span style="color: {color};">{icon} [{hypothesis_id}] {stmt}</span>'
        if status != "open" and evidence:
            ev = _escape(evidence.strip().replace("\n", " "))
            if len(ev) > 100:
                ev = ev[:97] + "…"
            line += f' <span style="color: #6a6a7a;">— {ev}</span>'
        self._insert_html(line)
        self._insert_newline()
        if status == "open":
            self._hypotheses_count += 1
            self._refresh_header()

    def append_nudge(self, label: str) -> None:
        """Записывает срабатывание нуджа (переход фазы)."""
        self._close_thought_block()
        self._insert_html(f'<span style="color: {_C_NUDGE}; font-style: italic;">🔁 {_escape(label)}</span>')
        self._insert_newline()

    def append_phase(self, label: str) -> None:
        """Записывает фазу исследования."""
        self._close_thought_block()
        self._insert_html(f'<span style="color: {_C_NUDGE}; font-style: italic;">📌 {_escape(label)}</span>')
        self._insert_newline()

    def append_status(self, label: str) -> None:
        """Произвольный статус агента («🧠 Размышляю…», «⏳ Подготовка…»).

        В отличие от `append_phase` (📌) и `append_nudge` (🔁) — без
        собственной иконки, эмодзи приходит в тексте.
        """
        if not label:
            return
        self._close_thought_block()
        self._insert_html(
            f'<span style="color: {_C_NUDGE}; font-style: italic;">'
            f"{_escape(label)}</span>"
        )
        self._insert_newline()

    def mark_interrupted(self, reason: str = "") -> None:
        """Помечает виджет как прерванный — курсивный комментарий в конце."""
        self._close_thought_block()
        self._insert_newline()
        text = reason or "Рассуждение прервано пользователем."
        self._insert_html(
            f'<span style="color: {_C_INTERRUPT}; font-style: italic;">⏹ {_escape(text)}</span>'
        )
        self._insert_newline()
        # Разворачиваем, чтобы пользователь увидел, где встал агент.
        if not self._expanded:
            self._toggle_body()

    @property
    def actions_count(self) -> int:
        return self._actions_count

    @property
    def hypotheses_count(self) -> int:
        return self._hypotheses_count


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short(value: object, limit: int = 60) -> str:
    """Короткое строковое представление аргумента."""
    s = str(value).replace("\n", " ")
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s
