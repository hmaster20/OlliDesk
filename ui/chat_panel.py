"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import asyncio
import json
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
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
from core.config import AgentMode
from core.exceptions import ModelNotFoundError, OllamaConnectionError


class OllamaChatThread(QThread):
    """Поток для асинхронного общения с Ollama (режим Chat)."""

    chunk_received = Signal(str)
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

    def run(self):
        """Запускает асинхронный чат в цикле событий."""
        asyncio.run(self._run_chat())

    async def _run_chat(self):
        """Выполняет асинхронный запрос к Ollama."""
        try:
            async with OllamaClient(base_url=self.base_url) as client:
                async for chunk in client.chat(
                    model=self.model,
                    messages=self.messages,
                    stream=True,
                ):
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
    tool_call_detected = Signal(str, str, str)
    tool_executed = Signal(str, str, bool)
    finish_received = Signal(str)
    error_occurred = Signal(str)
    iteration_changed = Signal(int, int)

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        model: str,
        user_message: str,
        context: AgentContext,
        messages_history: list[ChatMessage],
        agent_mode: AgentMode,
        max_iterations: int = 10,
    ):
        super().__init__()
        self.client = client
        self.registry = registry
        self.model = model
        self.user_message = user_message
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
        loop = AgentLoop(
            client=self.client,
            registry=self.registry,
            model=self.model,
        )

        try:
            result = await loop.run(
                user_message=self.user_message,
                context=self.context,
                messages_history=self.messages_history,
                on_token=self._on_token,
                on_tool_request=self._on_tool_request,
                max_iterations=self.max_iterations,
            )
            self.finish_received.emit(result)
        except AgentIterationLimitError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Ошибка агента: {e}")

    async def _on_token(self, token: str):
        """Колбэк для каждого токена (вызывается из asyncio, пробрасывает в Qt)."""
        self.chunk_received.emit(token)


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
    """Виджет одного сообщения в чате."""

    def __init__(self, role: str, content: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.content = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        role_label = QLabel(
            "🧑 Вы" if role == "user"
            else "🤖 Ассистент" if role == "assistant"
            else "🔧 Система" if role == "system"
            else f"🛠 {role}"
        )
        role_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #555;")
        layout.addWidget(role_label)

        content_label = QLabel(content)
        content_label.setWordWrap(True)
        content_label.setTextFormat(1)
        content_label.setStyleSheet("font-size: 14px; padding: 4px 0; line-height: 1.4;")
        content_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(content_label)

        self.setStyleSheet(
            "background: #f0f0f0; border-radius: 6px; margin: 2px 0;"
            if role == "user"
            else "background: #e3f2fd; border-radius: 6px; margin: 2px 0;"
        )


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    tool_requested = Signal(str, str, str)

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
        self._ollama_thread: OllamaChatThread | None = None
        self._agent_thread: AgentChatThread | None = None
        self._rag_context: str | None = None

        self._setup_ui()

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
        top_bar.addWidget(self.mode_combo)

        top_bar.addStretch()

        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setStyleSheet("font-size: 13px; padding: 4px 12px;")
        self.clear_btn.clicked.connect(self._clear_chat)
        top_bar.addWidget(self.clear_btn)

        layout.addLayout(top_bar)

        self.message_list = QListWidget()
        self.message_list.setStyleSheet(
            "font-size: 14px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.message_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        layout.addWidget(self.message_list, stretch=1)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Введите сообщение... (Enter — отправить)")
        self.input_edit.setMaximumHeight(100)
        self.input_edit.setStyleSheet(
            "font-size: 14px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, stretch=1)

        self.send_btn = QPushButton("Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-size: 12px; color: gray; padding: 4px;")
        layout.addWidget(self.status_label)

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
        item.setSizeHint(widget.sizeHint())
        self.message_list.setItemWidget(item, widget)
        self.message_list.scrollToBottom()

    def _add_tool_message(self, name: str, content: str, is_error: bool = False):
        """Добавляет сообщение о выполнении инструмента."""
        prefix = "❌" if is_error else "🔧"
        display_text = f"{prefix} Инструмент: {name}\n{content[:500]}"
        if len(content) > 500:
            display_text += "\n... (результат обрезан)"
        self._add_message("tool", display_text)

    def _clear_chat(self):
        """Очищает чат."""
        self.message_list.clear()
        self._messages.clear()
        self._current_assistant_content = ""
        self._rag_context = None
        self._session_id = None
        self.status_label.setText("Чат очищен")

    def _create_session(self):
        """Создаёт новую сессию если её нет."""
        if not self._session_id:
            self._session_id = self.session_store.create_session(
                project_path=str(Path.cwd())
            )

    def _send_message(self):
        """Отправляет сообщение в чат (в зависимости от режима)."""
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        if self._is_busy():
            self.status_label.setText("Ожидание завершения...")
            return

        self._create_session()

        user_message = ChatMessage(role="user", content=text)
        self._messages.append(user_message)
        self.session_store.save_message(self._session_id, user_message)
        self._add_message("user", text)

        self.input_edit.clear()
        self.send_btn.setEnabled(False)
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
        )

    def _start_chat(self, text: str):
        """Запускает обычный чат (режим Chat)."""
        self.send_btn.setText("Поиск...")
        self.status_label.setText("Поиск в кодовой базе...")
        self._rag_context = None

        if self.vector_store:
            rag_thread = RagSearchThread(self.vector_store, text, n_results=5)
            rag_thread.results_ready.connect(
                lambda ctx, src: self._on_rag_results(ctx, src)
            )
            rag_thread.search_error.connect(self._on_rag_error)
            rag_thread.finished.connect(lambda: self._do_chat(text))
            rag_thread.start()
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
        self.send_btn.setText("Ожидание...")
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
        self._ollama_thread = OllamaChatThread(
            model=self.model_combo.currentText(),
            messages=chat_messages,
            base_url=self.base_url,
        )
        self._ollama_thread.chunk_received.connect(self._on_chunk)
        self._ollama_thread.finish_received.connect(self._on_finish)
        self._ollama_thread.error_occurred.connect(self._on_chat_error)
        self._ollama_thread.start()

    def _start_agent(self, text: str, mode: AgentMode):
        """Запускает цикл агента (режимы Plan / Agent)."""
        self.send_btn.setText("Агент...")
        self.status_label.setText("🤖 Агент думает...")

        if self.vector_store:
            rag_thread = RagSearchThread(self.vector_store, text, n_results=5)
            rag_thread.results_ready.connect(
                lambda ctx, src: self._on_agent_rag_ready(ctx, src, text, mode)
            )
            rag_thread.search_error.connect(
                lambda err: self._do_agent(text, mode, "")
            )
            rag_thread.start()
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
        self._agent_thread = AgentChatThread(
            client=OllamaClient(base_url=self.base_url),
            registry=self._build_registry(),
            model=self.model_combo.currentText(),
            user_message=text,
            context=context,
            messages_history=list(self._messages),
            agent_mode=mode,
        )

        self._agent_thread.chunk_received.connect(self._on_chunk)
        self._agent_thread.tool_call_detected.connect(self._on_tool_call_detected)
        self._agent_thread.tool_executed.connect(self._on_tool_executed)
        self._agent_thread.finish_received.connect(self._on_agent_finish)
        self._agent_thread.error_occurred.connect(self._on_agent_error)
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

    @Slot(str)
    def _on_chunk(self, content: str):
        """Обрабатывает полученный токен."""
        self._current_assistant_content += content

        last_item = self.message_list.item(self.message_list.count() - 1)
        if last_item and hasattr(last_item, "_is_assistant"):
            widget = self.message_list.itemWidget(last_item)
            if widget:
                widget.content = self._current_assistant_content
                widget.findChild(QLabel).setText(self._current_assistant_content)
        else:
            item = QListWidgetItem(self.message_list)
            widget = ChatMessageItem("assistant", content)
            item.setSizeHint(widget.sizeHint())
            self.message_list.setItemWidget(item, widget)
            item._is_assistant = True

        self.message_list.scrollToBottom()

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

    @Slot(str)
    def _on_agent_error(self, error_msg: str):
        """Обрабатывает ошибку агента."""
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()

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

    def _reset_send_button(self):
        """Сбрасывает кнопку отправки в исходное состояние."""
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Отправить")
        self.status_label.setText("Готов к работе")

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
        """Обновляет список доступных моделей."""
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        index = self.model_combo.findText(current)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)

    def set_vector_store(self, vector_store):
        """Устанавливает векторное хранилище для RAG."""
        self.vector_store = vector_store

    def set_project_root(self, project_root: str):
        """Устанавливает корень проекта."""
        self.project_root = project_root
