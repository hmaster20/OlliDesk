"""Финальный «паспорт исследования» под ответом ассистента.

Показывается один раз в конце research-прогона. Содержит:

1. **Выводы агента** — финальный текст модели (markdown → HTML).
2. KPI: сколько файлов прочитано, сколько поисков.
3. Список прочитанных файлов.
4. Все гипотезы с их финальными статусами и доказательствами.

Кнопка «📋 Скопировать в Markdown» кладёт готовый блок в буфер обмена —
вставить в commit message, PR-описание или `AGENTS.md`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.chat.message_item import render_markdown

_HYPOTHESIS_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "open": ("#4a90e2", "🟦"),
    "verified": ("#4caf50", "✅"),
    "refuted": ("#e57373", "❌"),
    "partial": ("#ffb74d", "🟨"),
    "inconclusive": ("#888888", "⚪"),
}


def _fmt_count(n: int, singular: str, plural2: str, plural5: str) -> str:
    """Русская форма множественного числа."""
    n_abs = abs(n) % 100
    last = n_abs % 10
    if 11 <= n_abs <= 14:
        return f"{n} {plural5}"
    if last == 1:
        return f"{n} {singular}"
    if 2 <= last <= 4:
        return f"{n} {plural2}"
    return f"{n} {plural5}"


def _fmt_chars(total: int) -> str:
    if total < 1000:
        return f"{total} символов"
    if total < 1_000_000:
        return f"~{total // 1000}к символов"
    return f"~{total / 1_000_000:.1f}М символов"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ChatResearchPassport(QWidget):
    """Финальный «паспорт исследования» под ответом ассистента."""

    def __init__(self, summary: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._summary = summary or {}

        container = QFrame(self)
        container.setObjectName("_research_passport")
        container.setStyleSheet(
            "QFrame#_research_passport {"
            " background: #23232c; border: 1px solid #3a3a48;"
            " border-radius: 8px; padding: 10px 12px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(container)

        body_layout = QVBoxLayout(container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)

        # --- Заголовок + кнопки ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        title = QLabel("🔎 Паспорт исследования")
        title.setStyleSheet("color: #d4d4d4; font-size: 13px; font-weight: 700; padding: 0;")
        header.addWidget(title)
        header.addStretch()

        copy_btn = QPushButton("📋 Скопировать в Markdown")
        copy_btn.setToolTip("Скопировать сводку исследования в буфер обмена")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #555;"
            " border-radius: 10px; color: #bbb; font-size: 11px; padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(128,128,128,0.2); color: #fff; }"
        )
        copy_btn.clicked.connect(self._copy_markdown)
        header.addWidget(copy_btn)

        self._toggle_btn = QPushButton("▲ Свернуть")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #555;"
            " border-radius: 10px; color: #bbb; font-size: 11px; padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(128,128,128,0.2); color: #fff; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_body)
        header.addWidget(self._toggle_btn)

        body_layout.addLayout(header)

        # --- Тело (сворачиваемая часть) ---
        self._body = QWidget()
        body_inner = QVBoxLayout(self._body)
        body_inner.setContentsMargins(0, 0, 0, 0)
        body_inner.setSpacing(6)

        # 1. ВЫВОДЫ АГЕНТА (финальный текст модели)
        final_answer = str(self._summary.get("final_answer") or "").strip()
        if final_answer:
            header_conv = QLabel("Выводы агента")
            header_conv.setStyleSheet(
                "color: #d4d4d4; font-size: 12px; font-weight: 600; padding: 4px 0 0 0;"
            )
            body_inner.addWidget(header_conv)

            conv = QTextBrowser()
            conv.setOpenExternalLinks(True)
            conv.setReadOnly(True)
            conv.setFrameShape(QFrame.Shape.NoFrame)
            conv.setStyleSheet(
                "QTextBrowser {"
                " background: transparent; border: none; color: #d4d4d4;"
                " font-size: 13px; padding: 0; }"
            )
            conv.setHtml(render_markdown(final_answer))
            # Автовысота под контент (в разумных пределах)
            conv.document().setTextWidth(760)
            content_height = int(conv.document().size().height()) + 8
            conv.setFixedHeight(min(max(content_height, 80), 600))
            body_inner.addWidget(conv)

        # 2. KPI
        files_read: list[str] = list(self._summary.get("files_read") or [])
        searches: list[str] = list(self._summary.get("searches") or [])
        total_chars = int(self._summary.get("total_chars_read") or 0)

        kpi_parts = [
            f"📖 Прочитано: {_fmt_count(len(files_read), 'файл', 'файла', 'файлов')} ({_fmt_chars(total_chars)})"
        ]
        if searches:
            kpi_parts.append(f"🔍 Поисков по коду: {_fmt_count(len(searches), 'поиск', 'поиска', 'поисков')}")

        checks: list[str] = []
        checks.append("✓ Точка входа" if self._summary.get("read_entry_point") else "✗ Точка входа")
        checks.append("✓ Конфиг/README" if self._summary.get("read_config") else "✗ Конфиг/README")
        kpi_parts.append(" | ".join(checks))

        kpi = QLabel(" · ".join(kpi_parts))
        kpi.setWordWrap(True)
        kpi.setStyleSheet("color: #a8a8b8; font-size: 12px; padding: 4px 0 0 0;")
        body_inner.addWidget(kpi)

        # 3. Список файлов
        if files_read:
            files_line = ", ".join(files_read[:12])
            if len(files_read) > 12:
                files_line += f", … и ещё {len(files_read) - 12}"
            files_label = QLabel(files_line)
            files_label.setWordWrap(True)
            files_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            files_label.setStyleSheet(
                "color: #7a7a8a; font-size: 11px;"
                " font-family: 'Consolas','Cascadia Mono',monospace;"
                " padding: 0;"
            )
            body_inner.addWidget(files_label)

        # 4. Гипотезы
        hypotheses: list[dict] = list(self._summary.get("hypotheses") or [])
        if hypotheses:
            sep = QLabel("Гипотезы:")
            sep.setStyleSheet("color: #d4d4d4; font-size: 12px; font-weight: 600; padding: 6px 0 0 0;")
            body_inner.addWidget(sep)

            for h in hypotheses:
                body_inner.addWidget(self._make_hypothesis_row(h))

            counts: dict[str, int] = {}
            for h in hypotheses:
                status = str(h.get("status") or "open")
                counts[status] = counts.get(status, 0) + 1
            summary_parts: list[str] = []
            for key in ("verified", "partial", "refuted", "inconclusive", "open"):
                if counts.get(key):
                    icon = _HYPOTHESIS_STATUS_STYLES.get(key, ("#888", "•"))[1]
                    summary_parts.append(f"{icon} {counts[key]}")
            if summary_parts:
                total_label = QLabel(" · ".join(summary_parts))
                total_label.setStyleSheet("color: #a8a8b8; font-size: 11px; padding: 2px 0 0 0;")
                body_inner.addWidget(total_label)
        else:
            no_h = QLabel("Гипотезы не формулировались.")
            no_h.setStyleSheet("color: #7a7a8a; font-size: 11px; font-style: italic;")
            body_inner.addWidget(no_h)

        body_layout.addWidget(self._body)

    # ---------------------------------------------------------------
    # Построение строки гипотезы
    # ---------------------------------------------------------------

    @staticmethod
    def _make_hypothesis_row(h: dict) -> QWidget:
        status = str(h.get("status") or "open")
        color, icon = _HYPOTHESIS_STATUS_STYLES.get(status, ("#888888", "•"))

        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(1)

        head = QLabel(
            f'<span style="color: {color}; font-weight: 700;">{icon}</span> '
            f'<span style="color: #888; font-size: 11px;">[{h.get("id", "?")}]</span> '
            f'<span style="color: #d4d4d4; font-weight: 600;">'
            f'{_escape_html(str(h.get("statement") or ""))}</span>'
        )
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head.setStyleSheet("padding: 0;")
        row_layout.addWidget(head)

        evidence = str(h.get("evidence") or "").strip()
        verification = str(h.get("verification_method") or "").strip()
        if status == "open" and verification:
            detail_text = f"Проверить: {verification}"
            detail_color = "#9a9a9a"
        elif evidence:
            detail_text = f"Доказательство: {evidence}"
            detail_color = "#9a9a9a"
        else:
            detail_text = ""
            detail_color = "#7a7a8a"

        if detail_text:
            if len(detail_text) > 220:
                detail_text = detail_text[:217] + "…"
            detail = QLabel(detail_text)
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail.setStyleSheet(
                f"color: {detail_color}; font-size: 11px;"
                " font-family: 'Consolas','Cascadia Mono',monospace; padding: 0;"
            )
            row_layout.addWidget(detail)

        return row

    # ---------------------------------------------------------------
    # Копирование / сворачивание
    # ---------------------------------------------------------------

    def _toggle_body(self) -> None:
        visible = self._body.isVisible()
        self._body.setVisible(not visible)
        self._toggle_btn.setText("▼ Развернуть" if visible else "▲ Свернуть")

    def _copy_markdown(self) -> None:
        QApplication.clipboard().setText(self._to_markdown())

    def _to_markdown(self) -> str:
        s = self._summary
        files_read: list[str] = list(s.get("files_read") or [])
        searches: list[str] = list(s.get("searches") or [])
        hypotheses: list[dict] = list(s.get("hypotheses") or [])
        total_chars = int(s.get("total_chars_read") or 0)
        final_answer = str(s.get("final_answer") or "").strip()

        lines: list[str] = ["# Паспорт исследования", ""]

        if final_answer:
            lines.append("## Выводы агента")
            lines.append("")
            lines.append(final_answer)
            lines.append("")

        lines.append("## Доказательная база")
        lines.append("")
        lines.append(
            f"**Прочитано:** {_fmt_count(len(files_read), 'файл', 'файла', 'файлов')} "
            f"({_fmt_chars(total_chars)})"
        )
        if searches:
            lines.append(f"**Поисков по коду:** {_fmt_count(len(searches), 'поиск', 'поиска', 'поисков')}")
        lines.append(f"**Точка входа:** {'открыта' if s.get('read_entry_point') else 'не найдена'}")
        lines.append(f"**Конфиг/README:** {'открыт' if s.get('read_config') else 'не найден'}")
        lines.append("")

        if files_read:
            lines.append("### Прочитанные файлы")
            lines.append("")
            for f in files_read:
                lines.append(f"- `{f}`")
            lines.append("")

        if hypotheses:
            lines.append("### Гипотезы")
            lines.append("")
            status_icons = {
                "verified": "✅",
                "refuted": "❌",
                "partial": "🟨",
                "inconclusive": "⚪",
                "open": "🟦",
            }
            for h in hypotheses:
                icon = status_icons.get(str(h.get("status") or "open"), "•")
                hid = h.get("id", "?")
                statement = str(h.get("statement") or "").strip()
                lines.append(f"- {icon} **[{hid}]** {statement}")
                evidence = str(h.get("evidence") or "").strip()
                verification = str(h.get("verification_method") or "").strip()
                if str(h.get("status") or "") == "open" and verification:
                    lines.append(f"  - Проверить: {verification}")
                elif evidence:
                    lines.append(f"  - Доказательство: `{evidence}`")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
