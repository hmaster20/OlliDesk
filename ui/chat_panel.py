"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import asyncio
import html as html_mod
import json
import re
import threading

from loguru import logger
from typing import Any
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agents.agent_loop import AgentContext, AgentIterationLimitError, AgentLoop
from agents.ollama_client import ChatMessage, OllamaClient
from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from core.exceptions import ModelNotFoundError, OllamaConnectionError
from core.model_registry import ModelRegistry, ModelInfo
from core.roles import RoleManager, RoleDefinition
from core.system_prompts import SystemPromptManager

class OllamaChatThread(QThread):
    """Поток для асинхронного общения с Ollama (режим Chat)."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        model: str,
        messages: list[ChatMessage],
        base_url: str = "http://localhost:11434",
    ):
        super().__init__()
        self.model = model
        self.messages = messages
        self.base_url = base_url

    def cancel(self):
        """Отменяет текущий запрос к Ollama."""
        if hasattr(self, '_client') and self._client:
            self._client.cancel_request()

    def run(self):
        """Запускает асинхронный чат в цикле событий."""
        asyncio.run(self._run_chat())

    async def _run_chat(self):
        """Выполняет асинхронный запрос к Ollama."""
        try:
            self._client = OllamaClient(base_url=self.base_url)
            async with self._client as client:
                async for chunk in client.chat(
                    model=self.model,
                    messages=self.messages,
                    stream=True,
                ):
                    if chunk.reasoning_content:
                        self.thinking_received.emit(chunk.reasoning_content)
                    if chunk.content:
                        self.chunk_received.emit(chunk.content)
                    if chunk.done:
                        self.finish_received.emit()
        except OllamaConnectionError as e:
            self.error_occurred.emit(str(e))
        except ModelNotFoundError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Неизвестная ошибка: {e}")


class AgentChatThread(QThread):
    """Поток для выполнения цикла агента (режимы Plan / Agent)."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    tool_call_detected = Signal(str, str, str)
    tool_executed = Signal(str, str, bool)
    tool_started = Signal(str, str)  # name, arguments_json
    finish_received = Signal(str)
    error_occurred = Signal(str)
    iteration_changed = Signal(int, int)
    tools_status_changed = Signal(bool)  # True = tools active, False = disabled

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        model: str,
        context: AgentContext,
        messages_history: list[ChatMessage],
        agent_mode: AgentMode,
        max_iterations: int = 10,
    ):
        super().__init__()
        self.client = client
        self.registry = registry
        self.model = model
        self.context = context
        self.messages_history = messages_history
        self.agent_mode = agent_mode
        self.max_iterations = max_iterations

        self._tool_response: tuple[bool, dict] | None = None
        self._tool_event = threading.Event()

    def respond_to_tool(self, approved: bool, arguments: dict | None = None):
        """Отвечает на запрос подтверждения инструмента (вызывается из UI-потока).

        Args:
            approved: True если пользователь одобрил
            arguments: Возможно изменённые аргументы
        """
        self._tool_response = (approved, arguments)
        self._tool_event.set()

    async def _on_tool_request(self, name: str, description: str, arguments: dict) -> bool:
        """Колбэк, вызываемый AgentLoop при запросе подтверждения."""
        args_json = json.dumps(arguments, ensure_ascii=False)
        self.tool_call_detected.emit(name, description, args_json)

        self._tool_event.clear()
        await asyncio.get_event_loop().run_in_executor(None, self._tool_event.wait)

        approved, modified_args = self._tool_response or (False, None)
        if modified_args:
            arguments.clear()
            arguments.update(modified_args)
        return approved

    def run(self):
        """Запускает цикл агента."""
        asyncio.run(self._run_agent())

    async def _run_agent(self):
        """Выполняет цикл ReAct агента."""
        self.tools_status_changed.emit(True)

        async def _notify_tools_disabled():
            self.tools_status_changed.emit(False)

        async def _on_tool_start(name: str, args: dict):
            args_json = json.dumps(args, ensure_ascii=False)
            self.tool_started.emit(name, args_json)

        try:
            async with self.client as client:
                loop = AgentLoop(
                    client=client,
                    registry=self.registry,
                    model=self.model,
                )
                result = await loop.run(
                    context=self.context,
                    messages_history=self.messages_history,
                    on_token=self._on_token,
                    on_tool_request=self._on_tool_request,
                    max_iterations=self.max_iterations,
                    on_thinking=self._on_thinking,
                    on_tools_disabled=_notify_tools_disabled,
                    on_tool_start=_on_tool_start,
                )
            self.finish_received.emit(result)
        except AgentIterationLimitError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Ошибка агента: {e}")

    async def _on_token(self, token: str):
        """Колбэк для каждого токена (вызывается из asyncio, пробрасывает в Qt)."""
        self.chunk_received.emit(token)

    async def _on_thinking(self, token: str):
        """Колбэк для токенов рассуждения (вызывается из asyncio)."""
        self.thinking_received.emit(token)

class RagSearchThread(QThread):
    """Поток для асинхронного RAG-поиска."""

    results_ready = Signal(str, str)
    search_error = Signal(str)

    def __init__(
        self,
        vector_store,
        query: str,
        n_results: int = 5,
    ):
        super().__init__()
        self.vector_store = vector_store
        self.query = query
        self.n_results = n_results

    def run(self):
        """Запускает асинхронный RAG-поиск."""
        try:
            asyncio.run(self._search())
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                self.search_error.emit(str(e))
            else:
                raise

    async def _search(self):
        """Выполняет поиск и формирует контекст."""
        try:
            results = await self.vector_store.search(self.query, n_results=self.n_results)
            if results:
                context_parts = []
                source_parts = []
                for r in results[:self.n_results]:
                    context_parts.append(
                        f"### Файл: {r.file_path} "
                        f"(строки {r.chunk.start_line}-{r.chunk.end_line})\n"
                        f"```\n{r.chunk.content}\n```"
                    )
                    source_parts.append(f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}")

                context = "\n\n".join(context_parts)
                sources = ", ".join(source_parts[:3])
                self.results_ready.emit(context, sources)
            else:
                self.results_ready.emit("", "")
        except Exception as e:
            self.search_error.emit(str(e))

class ChatMessageItem(QWidget):
    """Виджет одного сообщения в чате (пузырёк с QLabel)."""

    def __init__(self, role: str, content: str, thinking: str = "",
                 mode_name: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking
        self.think_label = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("_bubble")
        is_user = role == "user"
        is_assistant = role == "assistant"
        is_tool = role in ("tool", "system")

        # Настройка цветов и стилей
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

        # Заголовок
        # Заголовок (роль) – показываем только для ассистента и системных, не для пользователя
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
        # Для пользователя заголовок не выводим

        # if not is_tool:
        #     header = "Вы" if is_user else f"Ассистент ({mode_name})" if is_assistant else role
        #     role_label = QLabel(header)
        #     role_label.setStyleSheet(f"""
        #         background-color: {bg};
        #         color: {text_color};
        #         font-weight: 600;
        #         font-size: 11px;
        #         opacity: 0.7;
        #         padding: 2px 0 4px 0;
        #         border-bottom: 1px solid #666;
        #         margin-bottom: 2px;
        #     """)
        #     bubble_layout.addWidget(role_label)

        # Блок рассуждений (если есть)
        # if thinking:
        #     think_label = QLabel(thinking)
        #     think_label.setStyleSheet(
        #         f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
        #     )
        #     think_label.setWordWrap(True)
        #     bubble_layout.addWidget(think_label)


        if thinking:
            think_label = QLabel(thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            bubble_layout.addWidget(think_label)
            self.think_label = think_label

        # Основной контент
        self.content_label = QLabel()
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_label.setOpenExternalLinks(True)
        self.content_label.setStyleSheet(
            f"font-family: 'Segoe UI', Roboto, sans-serif; font-size: 14px;"
            f" color: {text_color}; background: transparent;"
        )
        self._set_content(content)
        bubble_layout.addWidget(self.content_label)

        # Выравнивание облаков
        inner = QHBoxLayout()
        inner.setContentsMargins(6, 0, 6, 0)
        if is_user:
            inner.addStretch()
            inner.addWidget(bubble, 1)   # растягиваем, но ограничим maximumWidth
            inner.addStretch()
        else:
            inner.addWidget(bubble, 1)   # для ассистента – на всю ширину с ограничением 95%

        # inner = QHBoxLayout()
        # inner.setContentsMargins(6, 0, 6, 0)
        # if is_user:
        #     inner.addStretch()
        #     inner.addWidget(bubble, 1)   # растягиваем
        # else:
        #     inner.addWidget(bubble, 1)   # растягиваем для ассистента
        #     # inner.addStretch()         # убираем, чтобы занимал всю ширину

        outer.addLayout(inner)
        self.bubble = bubble

    def _set_content(self, text: str):
        """Рендерит Markdown в HTML и устанавливает в content_label."""
        html = self._render_markdown_to_html(text)
        self.content_label.setText(html)

    def _render_markdown_to_html(self, text: str) -> str:
        """Преобразует Markdown в HTML (упрощённо, с подсветкой кода)."""
        import re
        import html as html_mod

        try:
            from pygments import highlight as pyg_highlight
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.formatters import HtmlFormatter

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
                    return (f'<div style="background:#2d2d2d; padding:4px 8px; border-radius:4px; '
                            f'margin:4px 0; font-size:13px; overflow-x:auto;">'
                            f'<pre style="margin:0; font-family:\'Consolas\',\'Courier New\',monospace;">'
                            f'<code>{highlighted}</code></pre></div>')
                except Exception:
                    escaped = html_mod.escape(code)
                    return (f'<pre style="background:#2d2d2d; padding:4px 8px; border-radius:4px; '
                            f'margin:4px 0; font-size:13px; overflow-x:auto;"><code>{escaped}</code></pre>')

            pattern = r"```(\w*)\s*\n(.*?)```"
            body = re.sub(pattern, _code_block, text, flags=re.DOTALL)

            # Inline code
            body = re.sub(
                r"`([^`]+)`",
                lambda m: f'<code style="background:#3a3a3a; padding:2px 6px; border-radius:3px; font-size:13px;">{html_mod.escape(m.group(1))}</code>',
                body,
            )
            # Bold, italic, links, headings...
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            body = re.sub(r"\*(.+?)\*", r"<i>\1</i>", body)
            body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" style="color:#42a5f5;">\1</a>', body)
            body = re.sub(r"^### (.+)$", r"<h3>\1</h3>", body, flags=re.MULTILINE)
            body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", body, flags=re.MULTILINE)
            body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", body, flags=re.MULTILINE)

            # Newlines to <br>, collapse multiples
            body = body.replace("\n", "<br>")
            body = re.sub(r"(<br>\s*){2,}", "<br>", body)

            return f"<div style='line-height:1.3;'>{body}</div>"
        except ImportError:
            # Fallback: просто экранируем и заменяем \n на <br>
            return "<br>".join(html_mod.escape(line) for line in text.split("\n"))

    def update_content(self, new_content: str):
        """Обновляет содержимое сообщения (для стриминга)."""
        self.content = new_content
        self._set_content(new_content)

    # def update_thinking(self, new_thinking: str):
    #     """Обновляет блок рассуждений (динамически)."""
    #     self.thinking = new_thinking
    #     # Найти существующий QLabel с thinking и обновить, либо создать
    #     # Для простоты мы не поддерживаем динамическое обновление thinking
    #     # (можно пересоздать виджет, но сейчас проще пересоздать всё сообщение)
    #     # Однако в текущей архитектуре мы обычно обновляем thinking через _insert_thinking_edit
    #     # В новом дизайне мы можем просто пересоздать виджет, но это сложнее.
    #     # Я предлагаю оставить thinking как статичный блок, который не обновляется после создания.
    #     # Если нужно динамическое обновление, лучше использовать QTextEdit, но мы от него отказались.
    #     # Поэтому в _on_thinking мы будем пересоздавать виджет, если он ещё не создан.
    #     # В текущем коде мы не будем динамически обновлять thinking.
    #     pass

    def update_thinking(self, new_thinking: str):
        """Обновляет блок рассуждений."""
        self.thinking = new_thinking
        if self.think_label is None:
            # Создаём новый QLabel для thinking и вставляем перед content_label
            think_label = QLabel(new_thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            # Вставляем перед content_label (индекс 0, так как content_label последний)
            # self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, think_label)
            #  Надёжнее вставлять перед content_label
            index = self.bubble_layout.indexOf(self.content_label)
            self.bubble_layout.insertWidget(index, think_label)
            self.think_label = think_label
        else:
            self.think_label.setText(new_thinking)

class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    tool_requested = Signal(str, str, str)
    mode_changed = Signal(str)  # "chat", "plan", "agent"
    agent_panel_toggle = Signal(bool)  # показать/скрыть панель агента
    refresh_models_requested = Signal()

    def __init__(
        self,
        session_store,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        vector_store=None,
        project_root: str = "",
    ):
        super().__init__()
        self.session_store = session_store
        self.base_url = base_url
        self.current_model = model
        self.vector_store = vector_store
        self.project_root = project_root

        self._session_id: str | None = None
        self._messages: list[ChatMessage] = []
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._ollama_thread: OllamaChatThread | None = None
        self._agent_thread: AgentChatThread | None = None
        self._rag_thread: RagSearchThread | None = None
        self._rag_context: str | None = None
        self._generating = False
        self._last_assistant_widget: ChatMessageItem | None = None
        self.tool_policies: dict[str, ToolPolicy] = {}
        self.registry: ModelRegistry | None = None

        self.role_manager: RoleManager | None = None
        self.current_role_id: str = "default"

        self.prompt_manager: SystemPromptManager | None

        self._setup_ui()

    def _add_message(self, role: str, content: str, mode_name: str = "") -> ChatMessageItem:
        """Добавляет сообщение в список."""
        widget = ChatMessageItem(role, content, mode_name=mode_name)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        self._update_bubble_width(widget)   # установим максимальную ширину
        QTimer.singleShot(0, self._scroll_to_bottom)
        return widget

    def _update_bubble_width(self, widget: ChatMessageItem = None):
        """Устанавливает максимальную ширину бабла в зависимости от роли."""
        from PySide6.QtWidgets import QSizePolicy

        scroll_width = self.scroll_area.viewport().width()
        if scroll_width <= 0:
            return

        # Коэффициенты для разных ролей
        ratios = {
            "user": 0.80,       # запрос – 80%
            "assistant": 0.95,  # ответ – 95%
            "tool": 0.90,       # системные – по умолчанию
            "system": 0.90,
        }

        def apply_policy(bubble: QFrame, role: str):
            max_width = int(scroll_width * ratios.get(role, 0.90))
            bubble.setMaximumWidth(max_width)
            bubble.setMinimumWidth(0)
            bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        if widget is not None:
            if widget.bubble:
                apply_policy(widget.bubble, widget.role)
        else:
            for i in range(self.scroll_layout.count()):
                item = self.scroll_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), ChatMessageItem):
                    if item.widget().bubble:
                        apply_policy(item.widget().bubble, item.widget().role)

    def _relayout_all_items(self) -> None:
        """Пересчитывает ширину баблов при ресайзе."""
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessageItem):
                self._update_bubble_width(item.widget())

    # @Slot(str)
    # def _on_thinking(self, token: str):
    #     """Накапливает токены рассуждения (виджет пока не создаётся)."""
    #     self._current_thinking_content += token

    @Slot(str)
    def _on_thinking(self, token: str):
        self._current_thinking_content += token
        if self._last_assistant_widget is None:
            # Создаём виджет с пустым контентом и текущим thinking
            widget = ChatMessageItem(
                "assistant",
                "",  # контент пока пуст
                thinking=self._current_thinking_content,
                mode_name=self._current_mode_name(),
            )
            self._last_assistant_widget = widget
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
            self._update_bubble_width(widget)
        else:
            # Обновляем thinking у существующего виджета
            self._last_assistant_widget.update_thinking(self._current_thinking_content)
        QTimer.singleShot(0, self._scroll_to_bottom)


    # @Slot(str)
    # def _on_chunk(self, content: str):
    #     """Обрабатывает полученный токен."""
    #     self._current_assistant_content += content

    #     if self._last_assistant_widget is None:
    #         # Создаём виджет с накопленным thinking и первым контентом
    #         thinking = self._current_thinking_content or ""
    #         widget = ChatMessageItem(
    #             "assistant",
    #             self._current_assistant_content,
    #             thinking=thinking,
    #             mode_name=self._current_mode_name(),
    #         )
    #         self._last_assistant_widget = widget
    #         self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
    #         self._update_bubble_width(widget)
    #     else:
    #         # Обновляем контент существующего виджета
    #         self._last_assistant_widget.update_content(self._current_assistant_content)

    #     QTimer.singleShot(0, self._scroll_to_bottom)


    @Slot(str)
    def _on_chunk(self, content: str):
        self._current_assistant_content += content

        if self._last_assistant_widget is None:
            # Если виджет ещё не создан (не было thinking), создаём его
            widget = ChatMessageItem(
                "assistant",
                self._current_assistant_content,
                thinking=self._current_thinking_content,
                mode_name=self._current_mode_name(),
            )
            self._last_assistant_widget = widget
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
            self._update_bubble_width(widget)
        else:
            # Обновляем контент у существующего виджета
            self._last_assistant_widget.update_content(self._current_assistant_content)

        QTimer.singleShot(0, self._scroll_to_bottom)


    def _setup_ui(self):
        """Настраивает UI компоненты."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()

        # Селектор ролей
        role_label = QLabel("Роль:")
        role_label.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(role_label)

        self.role_combo = QComboBox()
        self.role_combo.setMinimumWidth(180)
        self.role_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)
        top_bar.addWidget(self.role_combo)

        model_label = QLabel("Модель:")
        model_label.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItem(self.current_model)
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setStyleSheet("font-size: 13px; padding: 4px;")

        # Кнопка проверки моделей
        self.refresh_models_btn = QPushButton("🔄")
        self.refresh_models_btn.setToolTip("Проверить поддержку инструментов моделями")
        self.refresh_models_btn.setStyleSheet("font-size: 14px; padding: 2px 8px;")
        self.refresh_models_btn.clicked.connect(self.refresh_models_requested.emit)
        top_bar.addWidget(self.refresh_models_btn)

        # Статус текущей модели
        self.model_status_label = QLabel("")
        self.model_status_label.setStyleSheet("font-size: 12px; padding: 4px 8px; color: gray;")
        top_bar.addWidget(self.model_status_label)

        # Подключаем сигнал смены модели
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

        top_bar.addWidget(self.model_combo)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        self.mode_combo.setItemData(0, "Chat — Обычный диалог с LLM. Без инструментов, только текст.", Qt.ToolTipRole)
        self.mode_combo.setItemData(1, "Plan — Чтение файлов и поиск по коду (read-only). Подготовка к изменениям.", Qt.ToolTipRole)
        self.mode_combo.setItemData(2, "Agent — Полный доступ: чтение, запись, Git, поиск. Автономные изменения кода.", Qt.ToolTipRole)
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)

        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setToolTip("Настройки агента")
        self.gear_btn.setStyleSheet(
            "QPushButton { font-size: 16px; padding: 2px 8px; border: none; background: transparent; }"
            " QPushButton:hover { background: #555; border-radius: 4px; }"
        )
        self.gear_btn.clicked.connect(self._toggle_agent_panel)
        top_bar.addWidget(self.gear_btn)

        top_bar.addStretch()

        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setStyleSheet("font-size: 13px; padding: 4px 12px;")
        self.clear_btn.clicked.connect(self._clear_chat)
        top_bar.addWidget(self.clear_btn)

        layout.addLayout(top_bar)

        self.tool_status_label = QLabel("🔧 Инструменты активны")
        self.tool_status_label.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; background: #e8f5e9; color: #2e7d32;"
            " border: 1px solid #a5d6a7; border-radius: 4px; margin: 2px 0;"
        )
        self.tool_status_label.setVisible(False)
        layout.addWidget(self.tool_status_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: 1px solid #ccc; border-radius: 4px; background: transparent; }"
        )

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("_scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(0)
        self.scroll_layout.addStretch()

        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area, stretch=1)

        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-size: 12px; color: gray; padding: 4px 8px; background: #f5f5f5; border-radius: 4px;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Введите сообщение... (CTRL + Enter — отправить)")
        self.input_edit.setFixedHeight(100)
        self.input_edit.setStyleSheet("font-size: 14px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        self._enter_pressed = False
        input_layout.addWidget(self.input_edit, stretch=1)

        self.scroll_area.viewport().installEventFilter(self)

        self.send_btn = QPushButton("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.send_btn.clicked.connect(self._toggle_send_stop)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

    def eventFilter(self, obj: QWidget, event) -> bool:
        """Обрабатывает Ctrl+Enter и ресайз панели чата."""
        if obj is self.input_edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self._toggle_send_stop()
                return True
        if obj is self.scroll_area.viewport() and event.type() == event.Type.Resize:
            self._update_bubble_width()   # обновить все
        return super().eventFilter(obj, event)

    def _current_mode_name(self) -> str:
        """Возвращает отображаемое имя текущего режима."""
        mapping = {0: "Чат", 1: "План", 2: "Агент"}
        return mapping.get(self.mode_combo.currentIndex(), "Чат")

    def _current_mode(self) -> AgentMode:
        """Возвращает текущий режим из комбобокса."""
        mapping = {
            0: AgentMode.CHAT,
            1: AgentMode.PLAN,
            2: AgentMode.AGENT,
        }
        return mapping.get(self.mode_combo.currentIndex(), AgentMode.CHAT)

    def _add_tool_message(self, name: str, content: str, is_error: bool = False):
        """Добавляет сообщение о выполнении инструмента."""
        prefix = "❌" if is_error else "🔧"
        display_text = f"{prefix} Инструмент: {name}\n{content[:500]}"
        if len(content) > 500:
            display_text += "\n... (результат обрезан)"
        self._add_message("tool", display_text)

    def _on_mode_changed(self, index: int):
        """Оповещает MainWindow об изменении режима."""
        mapping = {0: "chat", 1: "plan", 2: "agent"}
        mode = mapping.get(index, "chat")

        # Жесткая блокировка: не даем выбрать Agent/Plan, если модель не поддерживает tools
        if index > 0 and self.registry:
            model_name = self.get_current_model()
            cap = self.registry.get(model_name)

            if cap and not cap.supports_tools:
                # Возвращаем комбобокс на Chat, блокируя сигналы чтобы не зациклить
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(0)
                self.mode_combo.blockSignals(False)

                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "Режим недоступен",
                    f"Модель '{model_name}' не поддерживает инструменты.\n"
                    "Используйте модели с пометкой ✅ Agent/Plan."
                )
                return  # Прерываем выполнение, не эммитим сигнал смены режима

        self.mode_changed.emit(mode)
        self.gear_btn.setVisible(True)

    def _toggle_agent_panel(self):
        """Показывает/скрывает панель настроек агента."""
        self.agent_panel_toggle.emit(True)

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_chat(self):
        """Очищает чат."""
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._messages.clear()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._rag_context = None
        self._session_id = None
        self._last_assistant_widget = None
        self.status_label.setText("Чат очищен")

    def _create_session(self):
        """Создаёт новую сессию если её нет."""
        if not self._session_id:
            self._session_id = self.session_store.create_session(
                project_path=str(Path.cwd())
            )

    def _toggle_send_stop(self):
        """Переключает между отправкой и остановкой генерации."""
        if self._generating:
            self._stop_generation()
        else:
            self._send_message()

    def _stop_generation(self):
        """Останавливает текущую генерацию."""
        self._generating = False
        # Сначала отменяем HTTP-запрос, чтобы не грузить процессор
        if self._ollama_thread and self._ollama_thread.isRunning():
            self._ollama_thread.cancel()
        if self._agent_thread and self._agent_thread.isRunning() and self._agent_thread.client:
            self._agent_thread.client.cancel_request()
        if self._rag_thread and self._rag_thread.isRunning():
            self._rag_thread.quit()
            self._rag_thread.wait(1000)
            if self._rag_thread.isRunning():
                self._rag_thread.terminate()
                self._rag_thread.wait(500)
        # Ждём остановки потоков
        if self._ollama_thread and self._ollama_thread.isRunning():
            self._ollama_thread.quit()
            self._ollama_thread.wait(1000)
            if self._ollama_thread.isRunning():
                self._ollama_thread.terminate()
                self._ollama_thread.wait(500)
        if self._agent_thread and self._agent_thread.isRunning():
            self._agent_thread.quit()
            self._agent_thread.wait(1000)
            if self._agent_thread.isRunning():
                self._agent_thread.terminate()
                self._agent_thread.wait(500)
        self._reset_send_button()
        self.status_label.setText("⏹ Остановлено пользователем")
        self._add_message("system", "⏹ Генерация остановлена пользователем")
        self._current_assistant_content = ""
        self._current_thinking_content = ""

    def _send_message(self):
        """Отправляет сообщение в чат (в зависимости от режима)."""
        if self._generating:
            return   # или показать уведомление
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self._create_session()

        user_message = ChatMessage(role="user", content=text)
        self._messages.append(user_message)
        self.session_store.save_message(self._session_id, user_message)
        self._add_message("user", text)

        self.input_edit.clear()
        self.status_label.setText("Обработка...")

        mode = self._current_mode()
        if mode == AgentMode.CHAT:
            self._start_chat(text)
        else:
            self._start_agent(text, mode)

    def _is_busy(self) -> bool:
        """Проверяет, выполняется ли сейчас генерация."""
        return (
            (self._ollama_thread and self._ollama_thread.isRunning())
            or (self._agent_thread and self._agent_thread.isRunning())
            or (self._rag_thread and self._rag_thread.isRunning())
        )

    def _start_chat(self, text: str):
        """Запускает обычный чат (режим Chat)."""
        self._generating = True
        self._set_button_stop()
        # self.input_edit.setEnabled(False) # Блокировка поля ввода
        self.status_label.setText("Поиск в кодовой базе...")
        self._rag_context = None

        if self.vector_store:
            self._rag_thread = RagSearchThread(self.vector_store, text, n_results=5)
            self._rag_thread.results_ready.connect(
                lambda ctx, src: self._on_rag_results(ctx, src)
            )
            self._rag_thread.search_error.connect(self._on_rag_error)
            self._rag_thread.finished.connect(lambda: self._do_chat(text))
            self._rag_thread.start()
        else:
            self._do_chat(text)

    @Slot(str, str)
    def _on_rag_results(self, context: str, sources: str):
        """Обрабатывает результаты RAG-поиска."""
        self._rag_context = context
        if sources:
            self.status_label.setText(f"📚 Источники: {sources}")

    @Slot(str)
    def _on_rag_error(self, error_msg: str):
        """Обрабатывает ошибку RAG-поиска."""
        import loguru
        loguru.logger.warning(f"Ошибка RAG: {error_msg}; продолжаем без контекста")

    def set_prompt_manager(self, manager):
        """Устанавливает менеджер системных промтов."""
        self.prompt_manager = manager
        # Обновить тултипы для режимов
        self._update_mode_tooltips()

    def _update_mode_tooltips(self):
        """Обновляет тултипы для режимов на основе текущих системных промтов."""
        if not self.prompt_manager:
            return
        for index, mode in enumerate(["chat", "plan", "agent"]):
            prompt = self.prompt_manager.get_prompt(mode)  # без контекста
            if prompt:
                # Обрезаем до 100 символов для компактности
                short = prompt[:100] + "..." if len(prompt) > 100 else prompt
                self.mode_combo.setItemData(index, f"Системный промт:\n{short}", Qt.ToolTipRole)

    def _do_chat(self, text: str):
        """Запускает LLM-генерацию (режим Chat)."""
        self.status_label.setText("Генерация ответа...")

        chat_messages: list[ChatMessage] = list(self._messages)

        # Формируем единый системный промт
        system_prompt_parts = []
        if self.prompt_manager:
            # sys_prompt = self.prompt_manager.get_prompt("chat")
            sys_prompt = self.prompt_manager.get_prompt("chat", project_root=self.project_root)
            if sys_prompt:
                system_prompt_parts.append(sys_prompt)
        if self._rag_context:
            system_prompt_parts.append(f"Контекст из кодовой базы:\n{self._rag_context}")

        if system_prompt_parts:
            combined = "\n\n".join(system_prompt_parts)
            chat_messages.insert(0, ChatMessage(role="system", content=combined))

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()
        self._ollama_thread = OllamaChatThread(
            model=model_name,
            messages=chat_messages,
            base_url=self.base_url,
        )

        self._ollama_thread.chunk_received.connect(self._on_chunk)
        self._ollama_thread.thinking_received.connect(self._on_thinking)
        self._ollama_thread.finish_received.connect(self._on_finish)
        self._ollama_thread.error_occurred.connect(self._on_chat_error)
        self._ollama_thread.start()

    def _start_agent(self, text: str, mode: AgentMode):
        """Запускает цикл агента (режимы Plan / Agent)."""
        self._generating = True
        self._set_button_stop()
        # self.input_edit.setEnabled(False) # Блокировка поля ввода
        self.status_label.setText("🤖 Агент думает...")

        if self.vector_store:
            self._rag_thread = RagSearchThread(self.vector_store, text, n_results=5)
            self._rag_thread.results_ready.connect(
                lambda ctx, src: self._on_agent_rag_ready(ctx, src, text, mode)
            )
            self._rag_thread.search_error.connect(
                lambda err: self._do_agent(text, mode, "")
            )
            self._rag_thread.start()
        else:
            self._do_agent(text, mode, "")

    def _on_agent_rag_ready(self, context: str, sources: str, text: str, mode: AgentMode):
        """RAG-контекст получен для агента."""
        if sources:
            self.status_label.setText(f"📚 {sources}")
        self._do_agent(text, mode, context)

    def _do_agent(self, text: str, mode: AgentMode, rag_context: str):
        """Запускает AgentLoop."""
        role_prompt = self.get_current_role_prompt()
        # mode_prompt = self.prompt_manager.get_prompt(mode.value) if self.prompt_manager else ""
        mode_prompt = self.prompt_manager.get_prompt(mode.value, project_root=self.project_root) if self.prompt_manager else ""
        combined = "\n\n".join(filter(None, [role_prompt, mode_prompt]))
        context = AgentContext(
            # system_prompt=self.get_current_role_prompt(),
            system_prompt=combined,
            rag_context=rag_context,
            project_root=self.project_root,
            vector_store=self.vector_store,
            mode=mode,
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()
        self._agent_thread = AgentChatThread(
            client=OllamaClient(base_url=self.base_url),
            registry=self._build_registry(),
            model=model_name,
            context=context,
            messages_history=list(self._messages),
            agent_mode=mode,
        )

        self._agent_thread.chunk_received.connect(self._on_chunk)
        self._agent_thread.thinking_received.connect(self._on_thinking)
        self._agent_thread.tool_call_detected.connect(self._on_tool_call_detected)
        self._agent_thread.tool_executed.connect(self._on_tool_executed)
        self._agent_thread.tool_started.connect(self._on_tool_started)
        self._agent_thread.finish_received.connect(self._on_agent_finish)
        self._agent_thread.error_occurred.connect(self._on_agent_error)
        self._agent_thread.tools_status_changed.connect(self._on_tools_status_changed)
        self._agent_thread.start()

    def _build_registry(self) -> ToolRegistry:
        """Строит реестр инструментов для агента."""
        from tools.codebase_search import search_codebase
        from tools.file_ops import list_directory, read_file, write_file
        from tools.git_ops_tool import tool_create_snapshot, tool_git_status, tool_undo_snapshot
        from tools.web_search import web_search

        registry = ToolRegistry()
        registry.register(read_file)
        registry.register(list_directory)
        registry.register(write_file)
        registry.register(search_codebase)
        registry.register(web_search)
        registry.register(tool_git_status)
        registry.register(tool_create_snapshot)
        registry.register(tool_undo_snapshot)

        for tool_name, policy in self.tool_policies.items():
            registry.set_policy(tool_name, policy)

        return registry

    @Slot(str, str, str)
    def _on_tool_call_detected(self, name: str, description: str, arguments_json: str):
        """Обрабатывает запрос подтверждения инструмента."""
        from ui.dialogs.tool_confirmation_dialog import ToolConfirmationDialog

        arguments = json.loads(arguments_json)
        tool_labels = {
            "read_file": "Чтение файла",
            "write_file": "Запись файла",
            "list_directory": "Просмотр директории",
            "search_codebase": "Поиск в кодовой базе",
            "web_search": "Поиск в интернете",
            "get_git_status": "Статус Git",
            "create_snapshot": "Создание снапшота Git",
            "undo_last_snapshot": "Откат снапшота Git",
        }
        display_name = tool_labels.get(name, name)
        dialog = ToolConfirmationDialog(display_name, description, arguments, self)
        if dialog.exec() == ToolConfirmationDialog.Accepted:
            approved = dialog.is_approved()
            new_args = dialog.get_arguments()
            if self._agent_thread:
                self._agent_thread.respond_to_tool(approved, new_args)
        else:
            if self._agent_thread:
                self._agent_thread.respond_to_tool(False)

    @Slot(str, str, bool)
    def _on_tool_executed(self, name: str, content: str, is_error: bool):
        """Обрабатывает результат выполнения инструмента."""
        self._add_tool_message(name, content, is_error)

    @Slot(str, str)
    def _on_tool_started(self, name: str, arguments_json: str):
        """Обрабатывает начало выполнения инструмента."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            args = {}
        arg_summary = ", ".join(f"{k}={v}" for k, v in args.items())
        tool_labels = {
            "read_file": "📖 Чтение файла",
            "write_file": "✏️ Запись файла",
            "list_directory": "📁 Просмотр директории",
            "search_codebase": "🔍 Поиск в кодовой базе",
            "web_search": "🌐 Поиск в интернете",
            "get_git_status": "🔎 Проверка Git-статуса",
            "create_snapshot": "📸 Создание снапшота Git",
            "undo_last_snapshot": "⏪ Откат снапшота Git",
        }
        label = tool_labels.get(name, f"🛠️ {name}")
        self.status_label.setText(f"{label}: {arg_summary}")

    @Slot()
    def _on_finish(self):
        """Обрабатывает завершение чат-генерации."""
        self._send_finished()

    @Slot(str)
    def _on_chat_error(self, error_msg: str):
        """Обрабатывает ошибку чата."""
        self._add_message("system", f"❌ Ошибка: {error_msg}")
        self._send_finished()

    @Slot(str)
    def _on_agent_finish(self, result: str):
        """Обрабатывает завершение цикла агента."""
        if result and result != "Отменено":
            assistant_message = ChatMessage(role="assistant", content=result)
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None

    def _reset_send_button(self):
        """Сбрасывает кнопку отправки в исходное состояние."""
        self._generating = False
        self.input_edit.setEnabled(True)
        self.send_btn.setText("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.status_label.setText("Готов к работе")

    def _set_button_stop(self) -> None:
        """Переключает кнопку в режим остановки."""
        self.send_btn.setText("■")
        self.send_btn.setStyleSheet(
            "font-size: 18px; padding: 8px 20px; background: #d32f2f; color: white;"
            " border: none; border-radius: 4px;"
        )

    def _send_finished(self):
        """Завершает отправку сообщения (режим Chat)."""
        if self._current_assistant_content:
            assistant_message = ChatMessage(
                role="assistant", content=self._current_assistant_content
            )
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None

    @Slot(str)
    def _on_agent_error(self, error_msg: str):
        """Обрабатывает ошибку агента."""
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка")

    @Slot(bool)
    def _on_tools_status_changed(self, active: bool):
        """Обновляет индикатор статуса инструментов."""
        if active:
            self.tool_status_label.setText("🔧 Инструменты активны")
            self.tool_status_label.setStyleSheet(
                "font-size: 12px; padding: 4px 8px; background: #e8f5e9; color: #2e7d32;"
                " border: 1px solid #a5d6a7; border-radius: 4px; margin: 2px 0;"
            )
        else:
            self.tool_status_label.setText(
                "⚠️ Инструменты отключены — модель не поддерживает вызов инструментов. "
                "Доступен только текстовый режим."
            )
            self.tool_status_label.setStyleSheet(
                "font-size: 12px; padding: 4px 8px; background: #fff3e0; color: #e65100;"
                " border: 1px solid #ffcc80; border-radius: 4px; margin: 2px 0;"
            )
        self.tool_status_label.setVisible(True)

    def set_model(self, model: str):
        """Устанавливает модель в комбобоксе."""
        idx = self.model_combo.findData(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            # Если модель не найдена, добавляем её временно
            self.model_combo.addItem(model, model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    def set_tool_policy(self, tool_name: str, policy: Any) -> None:
        """Устанавливает политику для инструмента."""
        self.tool_policies[tool_name] = policy

    def set_vector_store(self, vector_store):
        """Устанавливает векторное хранилище для RAG."""
        self.vector_store = vector_store

    def set_project_root(self, project_root: str):
        """Устанавливает корень проекта."""
        self.project_root = project_root

    def get_settings(self) -> dict:
        """Возвращает текущие настройки панели."""
        mode_map = {0: "chat", 1: "plan", 2: "agent"}
        model_name = self.get_current_model()
        return {
            "model": model_name,
            "mode": mode_map.get(self.mode_combo.currentIndex(), "chat"),
        }


    def apply_settings(self, settings: dict) -> None:
        """Применяет сохранённые настройки к панели."""
        model = settings.get("model", "")
        if model:
            self.set_model(model)
        mode = settings.get("mode", "chat")
        mode_map = {"chat": 0, "plan": 1, "agent": 2}
        idx = mode_map.get(mode, 0)
        if 0 <= idx < self.mode_combo.count():
            self.mode_combo.setCurrentIndex(idx)

    def set_project_open(self, is_open: bool):
        """Блокирует/разблокирует ввод при отсутствии открытого проекта."""
        self.input_edit.setEnabled(is_open)
        self.send_btn.setVisible(is_open)
        if not is_open:
            self.input_edit.setPlaceholderText("Сначала откройте проект (File → Open Project...)")
            self.status_label.setText("Проект не открыт")
        else:
            self.input_edit.setPlaceholderText("Введите сообщение... (CTRL + Enter — отправить)")
            self._reset_send_button()

    def set_registry(self, registry):
        """Устанавливает реестр моделей и обновляет список."""
        self.registry = registry
        self._update_model_list()

    def update_models(self, models: list[str]) -> None:
        """Обновляет список моделей (вызывается из MainWindow)."""
        self._update_model_list()

    def _update_model_list(self):
        """Обновляет выпадающий список моделей из реестра."""
        if not self.registry:
            return
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        all_models = self.registry.get_all_models()
        # Сортировка: сначала локальные, потом остальные
        sorted_names = sorted(
            all_models.keys(),
            key=lambda x: (not all_models[x].is_local, x.lower())
        )
        for model_name in sorted_names:
            info = all_models[model_name]
            # Префикс для визуального отличия локальных моделей
            display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
            self.model_combo.addItem(display, model_name)
        # Восстанавливаем выбор
        if current and self.model_combo.findData(current) >= 0:
            self.model_combo.setCurrentIndex(self.model_combo.findData(current))
        else:
            # Выбираем первую локальную или первую вообще
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) and self.registry.get(self.model_combo.itemData(i)).is_local:
                    self.model_combo.setCurrentIndex(i)
                    break
            else:
                if self.model_combo.count() > 0:
                    self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._update_model_status(self.model_combo.currentData())

    def _update_model_status(self, model_name: str):
        """Обновляет компактный статус модели (эмодзи + тултип)."""
        if not self.registry:
            self.model_status_label.setText("❓")
            self.model_status_label.setToolTip("Реестр моделей не загружен")
            return
        info = self.registry.get(model_name)
        if not info:
            self.model_status_label.setText("❓")
            self.model_status_label.setToolTip("Модель не найдена в реестре")
            return
        if info.is_local:
            if info.supports_tools is True:
                status_icon = "✅"
                tooltip = "Поддерживает инструменты"
            elif info.supports_tools is False:
                status_icon = "⚠️"
                tooltip = "Только чат (нет поддержки tools)"
            else:
                status_icon = "❓"
                tooltip = "Поддержка tools не проверена"
        else:
            status_icon = "⬇️"
            tooltip = "Модель не скачана локально"
        if info.description:
            tooltip += f"\n{info.description}"
        self.model_status_label.setText(status_icon)
        self.model_status_label.setToolTip(tooltip)

    def _on_model_changed(self, index: int):
        """Вызывается при смене модели в комбобоксе."""
        model_name = self.get_current_model()
        if model_name:
            self._update_model_status(model_name)
            self._check_mode_compatibility()

    def set_checking_state(self, is_checking: bool):
        """Меняет иконку кнопки во время проверки (кнопка остаётся активной)."""
        self.refresh_models_btn.setText("⏳" if is_checking else "🔄")

    def _check_mode_compatibility(self):
        """Проверяет, подходит ли выбранный режим для модели."""
        if not self.registry:
            return
        model_name = self.get_current_model()
        cap = self.registry.get(model_name)

        if cap and not cap.supports_tools:
            current_mode = self._current_mode()
            if current_mode in (AgentMode.PLAN, AgentMode.AGENT):
                # Автоматически переключаем на Chat
                self.mode_combo.setCurrentIndex(0)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "Режим недоступен",
                    f"Модель '{model_name}' не поддерживает инструменты.\n"
                    "Режим автоматически переключен на 'Chat'."
                )

    def set_role_manager(self, manager: RoleManager):
        """Устанавливает менеджер ролей и заполняет комбобокс."""
        self.role_manager = manager
        self.role_combo.blockSignals(True)
        self.role_combo.clear()

        for role in manager.get_all_roles():
            self.role_combo.addItem(f"{role.icon} {role.name}", role.id)

        # Выбираем дефолтную
        idx = self.role_combo.findData("default")
        if idx >= 0:
            self.role_combo.setCurrentIndex(idx)
        self.role_combo.blockSignals(False)

    def _on_role_changed(self, index: int):
        """Обработчик смены роли."""
        if self.role_manager:
            role_id = self.role_combo.itemData(index)
            self.current_role_id = role_id
            logger.info(f"Смена роли на: {role_id}")

    def get_current_role_prompt(self) -> str:
        """Возвращает системный промт текущей роли."""
        if self.role_manager:
            return self.role_manager.get_role(self.current_role_id).system_prompt
        return ""

    def update_model_status(self, model_name: str):
        """
        Обновляет отображение для указанной модели в комбобоксе,
        используя текущие данные из реестра.
        """
        if not self.registry:
            return
        info = self.registry.get(model_name)
        if not info:
            return
        # Обновляем текст в комбобоксе
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model_name:
                display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
                self.model_combo.setItemText(i, display)
                break
        # Если это текущая модель, обновляем статусную метку
        if self.model_combo.currentData() == model_name:
            self._update_model_status(model_name)

    def get_current_model(self) -> str:
        """Возвращает чистое имя текущей модели (без префиксов)."""
        model = self.model_combo.currentData()
        if model is not None:
            return model
        # fallback: убираем возможные префиксы из отображаемого текста
        text = self.model_combo.currentText()
        for prefix in ("✅ ", "⬇️ "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return text
