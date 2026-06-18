"""Виджет сообщения с Markdown, кнопками и анимацией."""

import re
import html
from pathlib import Path

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QTextEdit, QSizePolicy

from core.utils import get_app_data_dir

# Попытка импортировать pygments для подсветки
try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import HtmlFormatter
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False


class MessageWidget(QWidget):
    """Отображение одного сообщения."""
    regenerate_requested = Signal()
    edit_requested = Signal()
    copy_requested = Signal()
    delete_requested = Signal()

    def __init__(self, role: str, content: str, timestamp: str = "", parent=None):
        super().__init__(parent)
        self.role = role  # 'user', 'assistant', 'system', 'tool'
        self.content = content
        self.timestamp = timestamp
        self.is_streaming = False
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        # Основной контейнер (бubble)
        self.bubble = QFrame()
        self.bubble.setObjectName("messageBubble")
        self.bubble.setStyleSheet(self._bubble_style())
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)

        # Заголовок (роль + время)
        header = QHBoxLayout()
        role_label = QLabel(self._role_display())
        role_label.setStyleSheet("font-weight: 600; font-size: 11px; opacity: 0.7;")
        header.addWidget(role_label)
        header.addStretch()
        if self.timestamp:
            time_label = QLabel(self.timestamp)
            time_label.setStyleSheet("font-size: 10px; opacity: 0.5;")
            header.addWidget(time_label)
        bubble_layout.addLayout(header)

        # Содержимое (HTML + Markdown)
        self.content_label = QLabel()
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_label.setOpenExternalLinks(True)
        self.content_label.setStyleSheet(self._content_style())
        self.content_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.set_content(self.content)
        bubble_layout.addWidget(self.content_label)

        # Кнопки действий (появляются при наведении)
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(4)

        self.copy_btn = QPushButton("📋")
        self.copy_btn.setFixedSize(24, 24)
        self.copy_btn.setToolTip("Копировать")
        self.copy_btn.clicked.connect(self.copy_requested.emit)

        self.regenerate_btn = QPushButton("🔄")
        self.regenerate_btn.setFixedSize(24, 24)
        self.regenerate_btn.setToolTip("Регенерировать")
        self.regenerate_btn.clicked.connect(self.regenerate_requested.emit)

        self.edit_btn = QPushButton("✏️")
        self.edit_btn.setFixedSize(24, 24)
        self.edit_btn.setToolTip("Редактировать (ветка)")
        self.edit_btn.clicked.connect(self.edit_requested.emit)

        self.delete_btn = QPushButton("🗑️")
        self.delete_btn.setFixedSize(24, 24)
        self.delete_btn.setToolTip("Удалить")
        self.delete_btn.clicked.connect(self.delete_requested.emit)

        # Показываем кнопки только для ассистента и пользователя
        if self.role in ('assistant', 'user'):
            for btn in (self.copy_btn, self.regenerate_btn if self.role == 'assistant' else self.edit_btn,
                        self.delete_btn):
                self.actions_layout.addWidget(btn)

        self.actions_layout.addStretch()
        self.actions_widget = QWidget()
        self.actions_widget.setLayout(self.actions_layout)
        self.actions_widget.setVisible(False)
        bubble_layout.addWidget(self.actions_widget)

        # Добавляем бабл в основной layout
        if self.role == 'user':
            # Выравнивание вправо
            wrapper = QHBoxLayout()
            wrapper.addStretch()
            wrapper.addWidget(self.bubble)
            layout.addLayout(wrapper)
        else:
            # Выравнивание влево
            wrapper = QHBoxLayout()
            wrapper.addWidget(self.bubble)
            wrapper.addStretch()
            layout.addLayout(wrapper)

        # Анимация появления
        self.animation = QPropertyAnimation(self, b"opacity")
        self.animation.setDuration(200)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.start()

        # Обработка наведения для показа кнопок
        self.bubble.enterEvent = self._on_enter
        self.bubble.leaveEvent = self._on_leave

    def _bubble_style(self):
        if self.role == 'user':
            return """
                QFrame#messageBubble {
                    background-color: #0D47A1;
                    border-radius: 18px 18px 4px 18px;
                    border: none;
                }
            """
        elif self.role == 'assistant':
            return """
                QFrame#messageBubble {
                    background-color: #2D2D2D;
                    border-radius: 18px 18px 18px 4px;
                    border: 1px solid #3D3D3D;
                }
            """
        elif self.role == 'system':
            return """
                QFrame#messageBubble {
                    background-color: transparent;
                    border: none;
                }
            """
        else:  # tool
            return """
                QFrame#messageBubble {
                    background-color: #1E1E1E;
                    border-radius: 8px;
                    border: 1px solid #444;
                }
            """

    def _content_style(self):
        if self.role == 'system':
            return "color: #888; font-size: 12px; font-style: italic;"
        elif self.role == 'tool':
            return "color: #aaa; font-size: 12px; font-family: monospace;"
        return "color: #EEE; font-size: 14px;"

    def _role_display(self):
        if self.role == 'user':
            return "Вы"
        elif self.role == 'assistant':
            return "Ассистент"
        elif self.role == 'system':
            return "Система"
        else:
            return "Инструмент"

    def set_content(self, text: str):
        """Устанавливает и рендерит Markdown в HTML."""
        self.content = text
        html_content = self._render_markdown(text)
        self.content_label.setText(html_content)

    def _render_markdown(self, text: str) -> str:
        """Преобразует Markdown в HTML с подсветкой кода."""
        if not text:
            return ""

        # Экранируем HTML-сущности, но сохраняем Markdown-разметку
        # Сначала обрабатываем блоки кода
        def code_block_repl(match):
            lang = match.group(1).strip()
            code = match.group(2)
            escaped_code = html.escape(code)
            if HAS_PYGMENTS and lang:
                try:
                    lexer = get_lexer_by_name(lang, stripall=True)
                    formatter = HtmlFormatter(style="monokai", nowrap=True, noclasses=True)
                    highlighted = highlight(code, lexer, formatter)
                    return f'<div class="code-block" style="background:#1A1A1A; border:1px solid #333; border-radius:6px; padding:8px; margin:6px 0; overflow-x:auto;"><pre style="margin:0; font-family:Cascadia Code, monospace; font-size:13px; white-space:pre-wrap; word-wrap:break-word;"><code>{highlighted}</code></pre></div>'
                except:
                    pass
            # fallback
            return f'<pre style="background:#1A1A1A; border:1px solid #333; border-radius:6px; padding:8px; margin:6px 0; overflow-x:auto;"><code>{escaped_code}</code></pre>'

        # Обработка ```lang\ncode```
        text = re.sub(r'```(\w*)\s*\n(.*?)```', code_block_repl, text, flags=re.DOTALL)

        # Inline code
        text = re.sub(r'`([^`]+)`', r'<code style="background:#333; padding:1px 6px; border-radius:3px; font-size:13px;">\1</code>', text)

        # Жирный, курсив, ссылки
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#60CDFF;">\1</a>', text)

        # Заголовки
        text = re.sub(r'^### (.+)$', r'<h3 style="font-size:1.1em; margin:6px 0;">\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<h2 style="font-size:1.2em; margin:8px 0;">\1</h2>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<h1 style="font-size:1.3em; margin:10px 0;">\1</h1>', text, flags=re.MULTILINE)

        # Списки
        text = re.sub(r'^\- (.+)$', r'• \1', text, flags=re.MULTILINE)
        text = re.sub(r'^\* (.+)$', r'• \1', text, flags=re.MULTILINE)
        text = re.sub(r'^(\d+)\. (.+)$', r'\1. \2', text, flags=re.MULTILINE)

        # Переносы строк в <br>
        text = text.replace('\n', '<br>')
        # Схлопываем множественные <br>
        text = re.sub(r'(<br>\s*){2,}', '<br>', text)

        return f'<div style="line-height:1.4; word-wrap:break-word;">{text}</div>'

    def _on_enter(self, event):
        self.actions_widget.setVisible(True)

    def _on_leave(self, event):
        self.actions_widget.setVisible(False)

    def set_streaming(self, active: bool):
        self.is_streaming = active
        if active:
            # добавить курсор-каретку (можно через QLabel с анимацией)
            pass

    # Переопределяем opacity для анимации
    def get_opacity(self):
        return self.windowOpacity()

    def set_opacity(self, value):
        self.setWindowOpacity(value)
