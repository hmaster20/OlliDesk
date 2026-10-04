"""Виджет одного сообщения в чате."""

import html as html_mod
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ChatMessageItem(QWidget):
    """Виджет одного сообщения в чате.

    Текст рендерится в QTextBrowser (выделение мышью, Ctrl+C, кликабельные
    ссылки, встроенное контекстное меню). Кнопка 📋 под текстом сообщения
    копирует исходный markdown в буфер обмена.
    """

    def __init__(
        self,
        role: str,
        content: str,
        thinking: str = "",
        mode_name: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking
        self.think_label: QLabel | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("_bubble")
        is_user = role == "user"
        is_assistant = role == "assistant"
        is_tool = role in ("tool", "system")

        if is_user:
            bg = "#3d3d3d"
            text_color = "#e0e0e0"
            border = "1px solid #555"
        elif is_assistant:
            bg = "transparent"
            text_color = "#e0e0e0"
            border = "none"
        else:
            bg = "transparent"
            text_color = "#999"
            border = "none"

        bubble.setStyleSheet(f"""
            QFrame#_bubble {{
                background-color: {bg};
                border-radius: 12px;
                padding: 6px 12px;
                border: {border};
            }}
        """)

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(2)
        self.bubble_layout = bubble_layout
        self.text_color = text_color

        # --- Заголовок: имя роли (для assistant/tool) ---
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        if not is_tool and not is_user:
            header = f"Ассистент ({mode_name})" if is_assistant else role
            role_label = QLabel(header)
            role_label.setStyleSheet(f"""
                background-color: {bg};
                color: {text_color};
                font-weight: 600;
                font-size: 11px;
                padding: 2px 0 4px 0;
            """)
            header_row.addWidget(role_label)

        header_row.addStretch()

        bubble_layout.addLayout(header_row)

        if thinking:
            think_label = QLabel(thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            bubble_layout.addWidget(think_label)
            self.think_label = think_label

        # --- Текст сообщения ---
        self.content_browser = QTextBrowser()
        self.content_browser.setOpenExternalLinks(True)
        self.content_browser.setReadOnly(True)
        self.content_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.content_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.content_browser.document().setDocumentMargin(0)
        self.content_browser.setStyleSheet(
            f"QTextBrowser {{"
            f" font-family: 'Segoe UI', Roboto, sans-serif;"
            f" font-size: 14px; color: {text_color};"
            f" background: transparent; border: none; padding: 0;"
            f" }}"
        )
        bubble_layout.addWidget(self.content_browser)

        # --- Кнопка «копировать» под текстом сообщения ---
        if is_user or is_assistant:
            copy_row = QHBoxLayout()
            copy_row.setContentsMargins(0, 2, 0, 0)
            copy_row.setSpacing(4)
            copy_row.addStretch()

            copy_btn = QPushButton("Копировать")
            copy_btn.setToolTip("Копировать сообщение (markdown)")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            copy_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #555;"
                " border-radius: 10px; color: #888; font-size: 11px; padding: 1px 10px; }"
                " QPushButton:hover { background: rgba(128,128,128,0.2); color: #ddd; }"
                " QPushButton:pressed { background: rgba(128,128,128,0.35); }"
            )
            copy_btn.clicked.connect(self._copy_to_clipboard)
            copy_row.addWidget(copy_btn)
            self.copy_button = copy_btn
            bubble_layout.addLayout(copy_row)

        self._set_content(content)

        inner = QHBoxLayout()
        inner.setContentsMargins(6, 0, 6, 0)
        if is_user:
            inner.addStretch()
            inner.addWidget(bubble, 1)
            inner.addStretch()
        else:
            inner.addWidget(bubble, 1)

        outer.addLayout(inner)
        self.bubble = bubble

    # ---------------------------------------------------------
    # Контент и высота
    # ---------------------------------------------------------

    def _set_content(self, text: str):
        html = self._render_markdown_to_html(text)
        self.content_browser.setHtml(html)
        # Обновляем высоту после смены контента.
        self._adjust_browser_height()

    def _adjust_browser_height(self):
        """Подгоняет высоту QTextBrowser под содержимое при текущей ширине."""
        doc: QTextDocument = self.content_browser.document()
        viewport_width = self.content_browser.viewport().width()
        if viewport_width <= 0:
            # Виджет ещё не отрисован — выйдем, resizeEvent вызовет повторно.
            return
        doc.setTextWidth(viewport_width)
        height = int(doc.size().height()) + 4
        self.content_browser.setFixedHeight(max(height, 22))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_browser_height()

    # ---------------------------------------------------------
    # Копирование
    # ---------------------------------------------------------

    def _copy_to_clipboard(self):
        QApplication.clipboard().setText(self.content)

    # ---------------------------------------------------------
    # Обновление во время стриминга
    # ---------------------------------------------------------

    def update_content(self, new_content: str):
        self.content = new_content
        self._set_content(new_content)

    def update_thinking(self, new_thinking: str):
        self.thinking = new_thinking
        if self.think_label is None:
            think_label = QLabel(new_thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {self.text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            index = self.bubble_layout.indexOf(self.content_browser)
            self.bubble_layout.insertWidget(index, think_label)
            self.think_label = think_label
        else:
            self.think_label.setText(new_thinking)

    # ---------------------------------------------------------
    # Markdown → HTML
    # ---------------------------------------------------------

    def _render_markdown_to_html(self, text: str) -> str:
        try:
            from pygments import highlight as pyg_highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name, guess_lexer

            formatter = HtmlFormatter(style="monokai", nowrap=True, noclasses=True)

            def _code_block(m):
                lang = m.group(1).strip() or ""
                code = m.group(2)
                try:
                    if lang:
                        lexer = get_lexer_by_name(lang, stripall=True)
                    else:
                        lexer = guess_lexer(code)
                    highlighted = pyg_highlight(code, lexer, formatter)
                    return (
                        f'<div style="background:#2d2d2d; padding:4px 8px; border-radius:4px; '
                        f'margin:4px 0; font-size:13px; overflow-x:auto;">'
                        f'<pre style="margin:0; font-family:\'Consolas\',\'Courier New\',monospace;">'
                        f'<code>{highlighted}</code></pre></div>'
                    )
                except Exception:
                    escaped = html_mod.escape(code)
                    return (
                        f'<pre style="background:#2d2d2d; padding:4px 8px; border-radius:4px; '
                        f'margin:4px 0; font-size:13px; overflow-x:auto;"><code>{escaped}</code></pre>'
                    )

            pattern = r"```(\w*)\s*\n(.*?)```"
            body = re.sub(pattern, _code_block, text, flags=re.DOTALL)

            body = re.sub(
                r"`([^`]+)`",
                lambda m: f'<code style="background:#3a3a3a; padding:2px 6px; border-radius:3px; font-size:13px;">{html_mod.escape(m.group(1))}</code>',
                body,
            )
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            body = re.sub(r"\*(.+?)\*", r"<i>\1</i>", body)
            body = re.sub(
                r"\[([^\]]+)\]\(([^)]+)\)",
                r'<a href="\2" style="color:#42a5f5;">\1</a>',
                body,
            )
            body = re.sub(r"^### (.+)$", r"<h3>\1</h3>", body, flags=re.MULTILINE)
            body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", body, flags=re.MULTILINE)
            body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", body, flags=re.MULTILINE)

            body = body.replace("\n", "<br>")
            body = re.sub(r"(<br>\s*){2,}", "<br>", body)

            return f"<div style='line-height:1.3;'>{body}</div>"
        except ImportError:
            return "<br>".join(html_mod.escape(line) for line in text.split("\n"))