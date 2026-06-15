"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import asyncio
import json
import threading
from typing import Any
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
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
        loop = AgentLoop(
            client=self.client,
            registry=self.registry,
            model=self.model,
        )

        try:
            async def _notify_tools_disabled():
                self.tools_status_changed.emit(False)

            async def _on_tool_start(name: str, args: dict):
                args_json = json.dumps(args, ensure_ascii=False)
                self.tool_started.emit(name, args_json)

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
        asyncio.run(self._search())

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
    """Виджет одного сообщения в чате (пузырёк как в Telegram)."""

    def __init__(self, role: str, content: str, thinking: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        bubble = QWidget()
        is_user = role == "user"
        is_assistant = role == "assistant"
        is_tool = role in ("tool", "system")

        # Цвета пузырька
        if is_user:
            bg = "#1976d2"
            text_color = "white"
            bubble_style = (
                f"background: {bg}; border-radius: 12px;"
                f" padding: 8px 14px; margin: 1px 0;"
            )
        elif is_assistant:
            bg = "#2d2d2d"
            text_color = "#e0e0e0"
            bubble_style = (
                f"background: {bg}; border-radius: 12px;"
                f" padding: 8px 14px; margin: 1px 0;"
            )
        else:
            bg = "transparent"
            text_color = "#999"
            bubble_style = "padding: 2px 14px; margin: 1px 0;"

        bubble.setStyleSheet(bubble_style)

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(2)

        if not is_tool:
            role_label = QLabel(
                "Вы" if is_user
                else "Ассистент" if is_assistant
                else role
            )
            role_label.setStyleSheet(
                f"font-weight: bold; font-size: 11px; color: {text_color};"
                f" opacity: 0.7;"
            )
            bubble_layout.addWidget(role_label)

        # Reasoning (thinking)
        self._thinking_edit: QTextEdit | None = None
        if thinking:
            self._thinking_edit = QTextEdit()
            self._thinking_edit.setReadOnly(True)
            self._thinking_edit.setPlainText(thinking)
            self._thinking_edit.setStyleSheet(
                "font-size: 12px; padding: 4px 0; border: none; background: transparent;"
                f" color: {'rgba(255,255,255,0.5)' if is_user else '#888'};"
                " font-style: italic; line-height: 1.3;"
            )
            self._thinking_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            self._thinking_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._thinking_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._thinking_edit.setMinimumHeight(16)
            bubble_layout.addWidget(self._thinking_edit)

        # Content
        self._content_edit = QTextEdit()
        self._content_edit.setReadOnly(True)
        ChatPanel._render_markdown(self._content_edit, content)
        self._content_edit.setStyleSheet(
            f"font-size: 14px; padding: 2px 0; border: none; background: transparent;"
            f" color: {text_color}; line-height: 1.4;"
        )
        self._content_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._content_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_edit.setMinimumHeight(16)
        bubble_layout.addWidget(self._content_edit)

        # Выравнивание: пользователь справа, ассистент слева
        inner = QHBoxLayout()
        inner.setContentsMargins(8, 1, 8, 1)
        if is_user:
            inner.addStretch()
            inner.addWidget(bubble)
            inner.setStretchFactor(bubble, 0)
        elif is_assistant:
            inner.addWidget(bubble)
            inner.addStretch()
            inner.setStretchFactor(bubble, 0)
        else:
            inner.addWidget(bubble)
            inner.addStretch()
            inner.setStretchFactor(bubble, 0)

        outer.addLayout(inner)


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    tool_requested = Signal(str, str, str)
    mode_changed = Signal(str)  # "chat", "plan", "agent"
    agent_panel_toggle = Signal(bool)  # показать/скрыть панель агента

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
        self.tool_policies: dict[str, ToolPolicy] = {}

        self._setup_ui()

    @staticmethod
    def _render_markdown(edit: QTextEdit, text: str) -> None:
        """Рендерит markdown в QTextEdit."""
        edit.setMarkdown(text)

    def _setup_ui(self):
        """Настраивает UI компоненты."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()

        model_label = QLabel("Модель:")
        model_label.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItem(self.current_model)
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(self.model_combo)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)

        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setToolTip("Настройки агента")
        self.gear_btn.setStyleSheet(
            "font-size: 16px; padding: 2px 8px; border: none; background: transparent;"
            " QPushButton:hover { background: #555; border-radius: 4px; }"
        )
        self.gear_btn.setVisible(False)
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

        self.message_list = QListWidget()
        self.message_list.setStyleSheet(
            "font-size: 14px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.message_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.message_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.message_list, stretch=1)

        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-size: 12px; color: gray; padding: 4px 8px; background: #f5f5f5; border-radius: 4px;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Введите сообщение... (CTRL + Enter — отправить)")
        self.input_edit.setFixedHeight(80)
        self.input_edit.setStyleSheet(
            "font-size: 14px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        self._enter_pressed = False
        input_layout.addWidget(self.input_edit, stretch=1)

        self.send_btn = QPushButton("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.send_btn.clicked.connect(self._toggle_send_stop)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

    def eventFilter(self, obj: QWidget, event) -> bool:
        """Обрабатывает Ctrl+Enter для отправки сообщения."""
        if obj is self.input_edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self._toggle_send_stop()
                return True
        return super().eventFilter(obj, event)

    def _current_mode(self) -> AgentMode:
        """Возвращает текущий режим из комбобокса."""
        mapping = {
            0: AgentMode.CHAT,
            1: AgentMode.PLAN,
            2: AgentMode.AGENT,
        }
        return mapping.get(self.mode_combo.currentIndex(), AgentMode.CHAT)

    def _add_message(self, role: str, content: str):
        """Добавляет сообщение в список."""
        item = QListWidgetItem(self.message_list)
        widget = ChatMessageItem(role, content)
        self._resize_message_item(item, widget)
        self.message_list.setItemWidget(item, widget)
        def _scroll():
            self.message_list.scrollToBottom()
            self.message_list.update()
        QTimer.singleShot(0, _scroll)

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
        self.mode_changed.emit(mode)
        self.gear_btn.setVisible(mode == "agent")
        if mode != "agent":
            self.agent_panel_toggle.emit(False)

    def _toggle_agent_panel(self):
        """Показывает/скрывает панель настроек агента."""
        self.agent_panel_toggle.emit(True)

    def _clear_chat(self):
        """Очищает чат."""
        self.message_list.clear()
        self._messages.clear()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._rag_context = None
        self._session_id = None
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
        self.input_edit.setEnabled(False)
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

    def _do_chat(self, text: str):
        """Запускает LLM-генерацию (режим Chat)."""
        self.status_label.setText("Генерация ответа...")

        chat_messages: list[ChatMessage] = list(self._messages)
        if self._rag_context:
            chat_messages.insert(
                0,
                ChatMessage(
                    role="system",
                    content=f"Контекст из кодовой базы:\n\n{self._rag_context}",
                ),
            )

        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._ollama_thread = OllamaChatThread(
            model=self.model_combo.currentText(),
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
        self.input_edit.setEnabled(False)
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
        context = AgentContext(
            rag_context=rag_context,
            project_root=self.project_root,
            vector_store=self.vector_store,
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._agent_thread = AgentChatThread(
            client=OllamaClient(base_url=self.base_url),
            registry=self._build_registry(),
            model=self.model_combo.currentText(),
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
        dialog = ToolConfirmationDialog(name, description, arguments, self)
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

    @Slot(str)
    def _on_thinking(self, token: str):
        """Обрабатывает токен рассуждения от LLM."""
        self._current_thinking_content += token

        if not self._current_thinking_content.strip():
            return

        last_item = self.message_list.item(self.message_list.count() - 1)
        if last_item and hasattr(last_item, "_is_assistant"):
            widget = self.message_list.itemWidget(last_item)
            if widget:
                widget.thinking = self._current_thinking_content
                if widget._thinking_edit:
                    widget._thinking_edit.setPlainText(self._current_thinking_content)
                else:
                    self._insert_thinking_edit(widget)
                self._resize_message_item(last_item, widget)
        else:
            item = QListWidgetItem(self.message_list)
            widget = ChatMessageItem("assistant", "", thinking=self._current_thinking_content)
            self._resize_message_item(item, widget)
            self.message_list.setItemWidget(item, widget)
            item._is_assistant = True

        self.status_label.setText("🧠 Анализирует...")
        QTimer.singleShot(0, self.message_list.scrollToBottom)

    def _insert_thinking_edit(self, widget: ChatMessageItem) -> None:
        """Вставляет QTextEdit для thinking в bubble."""
        thinking_edit = QTextEdit()
        thinking_edit.setReadOnly(True)
        thinking_edit.setPlainText(self._current_thinking_content)
        bubble_text_color = "rgba(255,255,255,0.5)" if widget.role == "user" else "#888"
        thinking_edit.setStyleSheet(
            "font-size: 12px; padding: 4px 0; border: none; background: transparent;"
            f" color: {bubble_text_color}; font-style: italic; line-height: 1.3;"
        )
        thinking_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        thinking_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        thinking_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        thinking_edit.setMinimumHeight(16)
        widget._thinking_edit = thinking_edit
        # Ищем bubble_layout внутри ChatMessageItem
        if widget._content_edit:
            bl = widget._content_edit.parentWidget()
            if bl and bl.layout():
                bl.layout().insertWidget(
                    bl.layout().count() - 1, thinking_edit
                )

    @Slot(str)
    def _on_chunk(self, content: str):
        """Обрабатывает полученный токен."""
        self._current_assistant_content += content

        # Если статус показывал инструмент — переключаем на генерацию
        status_text = self.status_label.text()
        if any(emoji in status_text for emoji in ("📖", "✏️", "📁", "🔍", "🌐", "🔎", "📸", "⏪", "🛠️")):
            self.status_label.setText("🤖 Генерация ответа...")

        last_item = self.message_list.item(self.message_list.count() - 1)
        if last_item and hasattr(last_item, "_is_assistant"):
            widget = self.message_list.itemWidget(last_item)
            if widget:
                widget.content = self._current_assistant_content
                self._render_markdown(widget._content_edit, self._current_assistant_content)
                self._resize_message_item(last_item, widget)
        else:
            thinking = self._current_thinking_content or ""
            item = QListWidgetItem(self.message_list)
            widget = ChatMessageItem("assistant", content, thinking=thinking)
            self._resize_message_item(item, widget)
            self.message_list.setItemWidget(item, widget)
            item._is_assistant = True

        def _scroll():
            self.message_list.scrollToBottom()
            self.message_list.update()
        QTimer.singleShot(0, _scroll)

    def _resize_message_item(self, item: QListWidgetItem, widget: QWidget) -> None:
        """Подгоняет высоту элемента под содержимое."""
        list_width = self.message_list.viewport().width()
        if list_width > 0:
            widget.setFixedWidth(list_width - 4)
        widget.adjustSize()
        hint = widget.sizeHint()
        item.setSizeHint(hint)

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
        if result:
            assistant_message = ChatMessage(role="assistant", content=result)
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self.status_label.setText("Готов к работе")


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
        self.current_model = model
        index = self.model_combo.findText(model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        else:
            self.model_combo.insertItem(0, model)
            self.model_combo.setCurrentIndex(0)

    def update_models(self, models: list[str]):
        """Обновляет список доступных моделей (в алфавитном порядке)."""
        current = self.model_combo.currentText()
        self.model_combo.clear()
        for m in sorted(models, key=str.lower):
            self.model_combo.addItem(m)
        index = self.model_combo.findText(current)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)

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
        return {
            "model": self.model_combo.currentText(),
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
