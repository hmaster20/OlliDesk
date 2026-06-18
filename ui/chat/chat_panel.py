"""Современная панель чата с виртуализированным списком сообщений."""

import asyncio
import json
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot, QModelIndex, QAbstractListModel, QSize, QEvent
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListView, QTextEdit, QPushButton,
    QLabel, QSplitter, QStyledItemDelegate, QStyleOptionViewItem
)

from agents.agent_loop import AgentContext, AgentLoop
from agents.ollama_client import ChatMessage, OllamaClient
from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from core.model_registry import ModelRegistry
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager
from state.session_store import SessionStore
from ui.chat.message_widget import MessageWidget
from ui.chat.chat_header import ChatHeader
from ui.chat.right_panel import RightSettingsPanel
from ui.dialogs.tool_confirmation_dialog import ToolConfirmationDialog
from ui.chat.chat_threads import OllamaChatThread, AgentChatThread


# Модель данных для списка сообщений (для QListView)
class MessageModel(QAbstractListModel):
    """Модель для списка сообщений с поддержкой добавления и обновления."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []  # list of dicts: {role, content, timestamp, id}

    def rowCount(self, parent=QModelIndex()):
        return len(self._messages)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        msg = self._messages[index.row()]
        if role == Qt.DisplayRole:
            return msg.get("content", "")
        elif role == Qt.UserRole:
            return msg
        return None

    def add_message(self, role: str, content: str, timestamp: str = ""):
        self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
        self._messages.append({"role": role, "content": content, "timestamp": timestamp, "id": len(self._messages)})
        self.endInsertRows()

    def update_last_message(self, content: str):
        """Обновляет последнее сообщение (для стриминга)."""
        if self.rowCount() == 0:
            return
        row = self.rowCount() - 1
        self._messages[row]["content"] = content
        self.dataChanged.emit(self.index(row), self.index(row), [Qt.DisplayRole])

    def clear(self):
        self.beginResetModel()
        self._messages.clear()
        self.endResetModel()

    def get_last_message(self):
        if self.rowCount() > 0:
            return self._messages[-1]
        return None


# Делегат для отображения MessageWidget в QListView
class MessageDelegate(QStyledItemDelegate):
    """Делегат для рендеринга сообщений в QListView."""
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        data = index.data(Qt.UserRole)
        if not data:
            return
        widget = MessageWidget(data["role"], data["content"], data.get("timestamp", ""))
        widget.setGeometry(option.rect)
        painter.save()
        widget.render(painter, option.rect.topLeft(), sourceRegion=option.rect, renderFlags=QWidget.DrawChildren)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        data = index.data(Qt.UserRole)
        if not data:
            return QSize(option.rect.width(), 50)
        lines = data["content"].count('\n') + 1
        height = max(50, lines * 20 + 30)
        return QSize(option.rect.width(), height)


class ChatPanel(QWidget):
    """Главная панель чата с новым дизайном."""

    mode_changed = Signal(str)  # 'chat', 'plan', 'agent'
    agent_panel_toggle = Signal(bool)  # для совместимости со старым интерфейсом
    refresh_models_requested = Signal()  # сигнал для обновления моделей

    def __init__(self, session_store: SessionStore, base_url: str = "http://localhost:11434",
                 model_registry: ModelRegistry = None, role_manager: RoleManager = None,
                 prompt_manager: SystemPromptManager = None, parent=None):
        super().__init__(parent)
        self.session_store = session_store
        self.base_url = base_url
        self.model_registry = model_registry
        self.role_manager = role_manager
        self.prompt_manager = prompt_manager

        self.current_model = "qwen2.5-coder:7b"
        self.current_agent_id = "default"
        self.vector_store = None
        self.project_root = ""
        self.tool_policies = {}

        self._messages_history = []  # список ChatMessage для бэкенда
        self._session_id = None
        self._generating = False
        self._current_assistant_content = ""
        self._ollama_thread = None
        self._agent_thread = None

        self.setup_ui()

        # Для совместимости со старым main_window: даём доступ к кнопке обновления
        self.refresh_models_btn = self.header.refresh_btn

        # Подключаем сигнал обновления от header
        self.header.refresh_models_requested.connect(self.refresh_models_requested.emit)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Заголовок
        self.header = ChatHeader()
        self.header.model_changed.connect(self._on_model_changed)
        self.header.new_chat_requested.connect(self._new_chat)
        self.header.stop_generation_requested.connect(self._stop_generation)
        self.header.settings_requested.connect(self._toggle_right_panel)
        main_layout.addWidget(self.header)

        # Сплиттер: список сообщений (слева) и правая панель (справа)
        self.splitter = QSplitter(Qt.Horizontal)

        # Контейнер для списка сообщений
        self.messages_container = QWidget()
        messages_layout = QVBoxLayout(self.messages_container)
        messages_layout.setContentsMargins(0, 0, 0, 0)

        # Список сообщений с делегатом
        self.message_view = QListView()
        self.message_view.setUniformItemSizes(False)
        self.message_view.setResizeMode(QListView.Adjust)
        self.message_view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.message_view.setSpacing(4)
        self.message_view.setStyleSheet("""
            QListView {
                background: transparent;
                border: none;
                outline: none;
            }
            QListView::item {
                border: none;
                padding: 0;
            }
        """)
        self.message_delegate = MessageDelegate(self.message_view)
        self.message_view.setItemDelegate(self.message_delegate)
        self.message_model = MessageModel()
        self.message_view.setModel(self.message_model)
        messages_layout.addWidget(self.message_view)

        # Поле ввода (нижняя часть)
        input_layout = QHBoxLayout()
        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Введите сообщение... (Ctrl+Enter отправка)")
        self.input_edit.setMaximumHeight(120)
        self.input_edit.setMinimumHeight(60)
        self.input_edit.setStyleSheet("""
            QTextEdit {
                background: #1E1E1E;
                color: #EEE;
                border: 1px solid #3D3D3D;
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
            }
            QTextEdit:focus {
                border: 1px solid #60CDFF;
            }
        """)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit)

        self.send_btn = QPushButton("▶")
        self.send_btn.setFixedSize(48, 48)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: #60CDFF;
                color: #1E1E1E;
                border: none;
                border-radius: 24px;
                font-size: 20px;
            }
            QPushButton:hover {
                background: #4DB8E8;
            }
            QPushButton:disabled {
                background: #555;
                color: #888;
            }
        """)
        self.send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(self.send_btn)

        messages_layout.addLayout(input_layout)

        self.splitter.addWidget(self.messages_container)

        # Правая панель (изначально скрыта)
        self.right_panel = RightSettingsPanel()
        self.right_panel.settings_changed.connect(self._apply_settings)
        self.right_panel.setVisible(False)
        self.splitter.addWidget(self.right_panel)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        main_layout.addWidget(self.splitter)

        # Загрузка моделей
        self._update_model_list()

    def _toggle_right_panel(self):
        visible = self.right_panel.isVisible()
        self.right_panel.setVisible(not visible)
        self.agent_panel_toggle.emit(visible)  # для совместимости

    def _on_model_changed(self, model: str):
        self.current_model = model

    def _new_chat(self):
        self.message_model.clear()
        self._messages_history.clear()
        self._session_id = None
        self.header.set_tokens(0, 0)

    def _stop_generation(self):
        if self._ollama_thread and self._ollama_thread.isRunning():
            self._ollama_thread.cancel()
        if self._agent_thread and self._agent_thread.isRunning():
            self._agent_thread.client.cancel_request()
        self._generating = False
        self.send_btn.setEnabled(True)
        self.header.show_stop_button(False)

    def _send_message(self):
        if self._generating:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self._generating = True
        self.send_btn.setEnabled(False)
        self.header.show_stop_button(True)

        self.message_model.add_message("user", text)
        self._messages_history.append(ChatMessage(role="user", content=text))

        self.message_model.add_message("assistant", "")
        self._current_assistant_content = ""

        mode = AgentMode.CHAT  # по умолчанию
        if mode == AgentMode.CHAT:
            self._start_chat(text)
        else:
            self._start_agent(text, mode)

    def _start_chat(self, text: str):
        chat_messages = self._build_chat_messages()
        self._ollama_thread = OllamaChatThread(
            model=self.current_model,
            messages=chat_messages,
            base_url=self.base_url
        )
        self._ollama_thread.chunk_received.connect(self._on_chunk)
        self._ollama_thread.finish_received.connect(self._on_finish)
        self._ollama_thread.error_occurred.connect(self._on_error)
        self._ollama_thread.start()

    def _start_agent(self, text: str, mode: AgentMode):
        registry = self._build_registry()
        context = AgentContext(
            system_prompt=self._get_system_prompt(mode),
            rag_context="",
            project_root=self.project_root,
            vector_store=self.vector_store,
            mode=mode,
        )
        self._agent_thread = AgentChatThread(
            client=OllamaClient(base_url=self.base_url),
            registry=registry,
            model=self.current_model,
            context=context,
            messages_history=self._messages_history,
            agent_mode=mode,
        )
        self._agent_thread.chunk_received.connect(self._on_chunk)
        self._agent_thread.tool_call_detected.connect(self._on_tool_call)
        self._agent_thread.finish_received.connect(self._on_finish)
        self._agent_thread.error_occurred.connect(self._on_error)
        self._agent_thread.start()

    def _build_chat_messages(self) -> List[ChatMessage]:
        msgs = []
        sys_prompt = self._get_system_prompt(AgentMode.CHAT)
        if sys_prompt:
            msgs.append(ChatMessage(role="system", content=sys_prompt))
        msgs.extend(self._messages_history)
        return msgs

    def _get_system_prompt(self, mode: AgentMode) -> str:
        if self.prompt_manager:
            prompt = self.prompt_manager.get_prompt(mode.value, project_root=self.project_root)
        else:
            prompt = ""
        if self.role_manager and self.current_agent_id:
            role = self.role_manager.get_role(self.current_agent_id)
            if role:
                prompt = role.system_prompt + "\n\n" + prompt
        return prompt

    def _build_registry(self) -> ToolRegistry:
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
        for name, policy in self.tool_policies.items():
            registry.set_policy(name, policy)
        return registry

    @Slot(str)
    def _on_chunk(self, content: str):
        self._current_assistant_content += content
        self.message_model.update_last_message(self._current_assistant_content)
        self.message_view.scrollTo(self.message_model.index(self.message_model.rowCount() - 1))

    @Slot()
    def _on_finish(self):
        self._generating = False
        self.send_btn.setEnabled(True)
        self.header.show_stop_button(False)
        last = self.message_model.get_last_message()
        if last and last["role"] == "assistant":
            self._messages_history.append(ChatMessage(role="assistant", content=last["content"]))

    @Slot(str)
    def _on_error(self, error_msg: str):
        self.message_model.update_last_message(f"❌ Ошибка: {error_msg}")
        self._on_finish()

    @Slot(str, str, str)
    def _on_tool_call(self, name: str, description: str, args_json: str):
        args = json.loads(args_json)
        dialog = ToolConfirmationDialog(name, description, args, self)
        if dialog.exec() == ToolConfirmationDialog.Accepted:
            approved = dialog.is_approved()
            if self._agent_thread:
                self._agent_thread.respond_to_tool(approved, dialog.get_arguments())

    def _apply_settings(self, settings: dict):
        # Применяем настройки (сохраняем, но пока заглушка)
        pass

    def _update_model_list(self):
        if self.model_registry:
            models = self.model_registry.get_all_models()
            names = list(models.keys())
            self.header.set_models(names, self.current_model)

    # --- Методы для совместимости с main_window ---

    def set_vector_store(self, store):
        self.vector_store = store

    def set_project_root(self, root: str):
        self.project_root = root

    def set_project_open(self, is_open: bool):
        """Блокирует/разблокирует ввод при открытии/закрытии проекта."""
        self.input_edit.setEnabled(is_open)
        self.send_btn.setEnabled(is_open)
        if not is_open:
            self.input_edit.setPlaceholderText("Сначала откройте проект (File → Open Project...)")
        else:
            self.input_edit.setPlaceholderText("Введите сообщение... (Ctrl+Enter отправка)")

    def apply_settings(self, settings: dict):
        """Применяет сохранённые настройки (модель, режим)."""
        model = settings.get("model")
        if model:
            self.set_model(model)
        mode = settings.get("mode", "chat")
        mode_map = {"chat": 0, "plan": 1, "agent": 2}
        # В новом интерфейсе мы не используем комбобокс режима в ChatPanel,
        # но для совместимости можно установить режим через сигнал.
        # Пока просто сохраняем.
        self._current_mode = mode

    def get_settings(self) -> dict:
        """Возвращает текущие настройки (модель, режим)."""
        return {
            "model": self.current_model,
            "mode": "chat",  # по умолчанию, можно расширить
        }

    def set_model(self, model: str):
        """Устанавливает модель в комбобоксе."""
        self.header.model_combo.blockSignals(True)
        idx = self.header.model_combo.findText(model)
        if idx >= 0:
            self.header.model_combo.setCurrentIndex(idx)
        else:
            # Добавляем, если нет
            self.header.model_combo.addItem(model)
            self.header.model_combo.setCurrentIndex(self.header.model_combo.count() - 1)
        self.header.model_combo.blockSignals(False)
        self.current_model = model

    def set_checking_state(self, is_checking: bool):
        """Меняет иконку кнопки обновления во время проверки."""
        self.header.set_checking_state(is_checking)

    def update_models(self, models: list[str]):
        """Обновляет список моделей (вызывается из MainWindow)."""
        self.header.set_models(models, self.current_model)

    def update_model_status(self, model_name: str):
        """Обновляет статус модели (заглушка)."""
        pass

    def _update_model_status(self, model_name: str):
        """Внутренний метод для обновления статуса (заглушка)."""
        pass

    def _update_mode_tooltips(self):
        """Обновляет подсказки для режимов (заглушка)."""
        pass

    def set_role_manager(self, manager: RoleManager):
        self.role_manager = manager

    def set_prompt_manager(self, manager: SystemPromptManager):
        self.prompt_manager = manager

    def set_registry(self, registry: ModelRegistry):
        self.model_registry = registry
        self._update_model_list()

    # --- Event filter ---
    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
                self._send_message()
                return True
        return super().eventFilter(obj, event)
