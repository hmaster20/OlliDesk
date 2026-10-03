"""Виджет одного сообщения в чате."""

import html as html_mod
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ChatMessageItem(QWidget):
    """Виджет одного сообщения в чате (пузырёк с QLabel)."""

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

        if not is_tool and not is_user:
            header = f"Ассистент ({mode_name})" if is_assistant else role
            role_label = QLabel(header)
            role_label.setStyleSheet(f"""
                background-color: {bg};
                color: {text_color};
                font-weight: 600;
                font-size: 11px;
                opacity: 0.7;
                padding: 2px 0 4px 0;
                border-bottom: 1px solid #666;
                margin-bottom: 2px;
            """)
            bubble_layout.addWidget(role_label)

        if thinking:
            think_label = QLabel(thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            bubble_layout.addWidget(think_label)
            self.think_label = think_label

        self.content_label = QLabel()
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.content_label.setOpenExternalLinks(True)
        self.content_label.setStyleSheet(
            f"font-family: 'Segoe UI', Roboto, sans-serif; font-size: 14px;"
            f" color: {text_color}; background: transparent;"
        )
        self._set_content(content)
        bubble_layout.addWidget(self.content_label)

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

    def _set_content(self, text: str):
        html = self._render_markdown_to_html(text)
        self.content_label.setText(html)

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
            index = self.bubble_layout.indexOf(self.content_label)
            self.bubble_layout.insertWidget(index, think_label)
            self.think_label = think_label
        else:
            self.think_label.setText(new_thinking)