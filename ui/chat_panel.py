"""Панель чата для общения с LLM."""

import asyncio
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

from agents.ollama_client import ChatMessage, OllamaClient
from core.exceptions import ModelNotFoundError, OllamaConnectionError
from state.session_store import SessionStore


class OllamaChatThread(QThread):
    """Поток для асинхронного общения с Ollama."""

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


class ChatMessageItem(QWidget):
    """Виджет одного сообщения в чате."""

    def __init__(self, role: str, content: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.content = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        role_label = QLabel(
            "🧑 Вы" if role == "user" else "🤖 Ассистент" if role == "assistant" else f"🔧 {role}"
        )
        role_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #555;"
        )
        layout.addWidget(role_label)

        content_label = QLabel(content)
        content_label.setWordWrap(True)
        content_label.setTextFormat(1)  # Qt.PlainText
        content_label.setStyleSheet(
            "font-size: 14px; padding: 4px 0; line-height: 1.4;"
        )
        content_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(content_label)

        self.setStyleSheet(
            "background: #f0f0f0; border-radius: 6px; margin: 2px 0;"
            if role == "user"
            else "background: #e3f2fd; border-radius: 6px; margin: 2px 0;"
        )


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    def __init__(
        self,
        session_store: SessionStore,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
    ):
        super().__init__()
        self.session_store = session_store
        self.base_url = base_url
        self.current_model = model

        self._session_id: str | None = None
        self._messages: list[ChatMessage] = []
        self._current_assistant_content = ""
        self._ollama_thread: OllamaChatThread | None = None

        self._setup_ui()

    def _setup_ui(self):
        """Настраивает UI компоненты."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Верхняя панель с выбором модели и кнопками
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

        top_bar.addStretch()

        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setStyleSheet("font-size: 13px; padding: 4px 12px;")
        self.clear_btn.clicked.connect(self._clear_chat)
        top_bar.addWidget(self.clear_btn)

        layout.addLayout(top_bar)

        # Список сообщений
        self.message_list = QListWidget()
        self.message_list.setStyleSheet(
            "font-size: 14px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.message_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        layout.addWidget(self.message_list, stretch=1)

        # Нижняя панель: ввод + кнопка отправки
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

        # Статусная строка
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-size: 12px; color: gray; padding: 4px;")
        layout.addWidget(self.status_label)

    def _add_message(self, role: str, content: str):
        """Добавляет сообщение в список."""
        item = QListWidgetItem(self.message_list)
        widget = ChatMessageItem(role, content)
        item.setSizeHint(widget.sizeHint())
        self.message_list.setItemWidget(item, widget)
        self.message_list.scrollToBottom()

    def _clear_chat(self):
        """Очищает чат."""
        self.message_list.clear()
        self._messages.clear()
        self._current_assistant_content = ""
        self._session_id = None
        self.status_label.setText("Чат очищен")

    def _create_session(self):
        """Создает новую сессию если её нет."""
        if not self._session_id:
            self._session_id = self.session_store.create_session(
                project_path=str(Path.cwd())
            )

    def _send_message(self):
        """Отправляет сообщение в чат."""
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        if self._ollama_thread and self._ollama_thread.isRunning():
            self.status_label.setText("Ожидание ответа...")
            return

        self._create_session()

        # Добавляем сообщение пользователя
        user_message = ChatMessage(role="user", content=text)
        self._messages.append(user_message)
        self.session_store.save_message(self._session_id, user_message)
        self._add_message("user", text)

        self.input_edit.clear()
        self.send_btn.setEnabled(False)
        self.send_btn.setText("Ожидание...")
        self.status_label.setText("Генерация ответа...")

        # Подготовка сообщений для LLM
        chat_messages = list(self._messages)

        # Запуск потока
        self._current_assistant_content = ""
        self._ollama_thread = OllamaChatThread(
            model=self.model_combo.currentText(),
            messages=chat_messages,
            base_url=self.base_url,
        )
        self._ollama_thread.chunk_received.connect(self._on_chunk)
        self._ollama_thread.finish_received.connect(self._on_finish)
        self._ollama_thread.error_occurred.connect(self._on_error)
        self._ollama_thread.start()

    @Slot(str)
    def _on_chunk(self, content: str):
        """Обрабатывает полученный токен."""
        self._current_assistant_content += content

        # Обновляем последнее сообщение ассистента
        last_item = self.message_list.item(self.message_list.count() - 1)
        if last_item and hasattr(last_item, "_is_assistant"):
            widget = self.message_list.itemWidget(last_item)
            if widget:
                widget.content = self._current_assistant_content
                widget.findChild(QLabel).setText(self._current_assistant_content)
        else:
            # Первый токен — создаем новое сообщение
            item = QListWidgetItem(self.message_list)
            widget = ChatMessageItem("assistant", content)
            item.setSizeHint(widget.sizeHint())
            self.message_list.setItemWidget(item, widget)
            item._is_assistant = True

        self.message_list.scrollToBottom()

    @Slot()
    def _on_finish(self):
        """Обрабатывает завершение генерации."""
        self._send_finished()

    @Slot(str)
    def _on_error(self, error_msg: str):
        """Обрабатывает ошибку."""
        self._add_message("system", f"❌ Ошибка: {error_msg}")
        self._send_finished()

    def _send_finished(self):
        """Завершает отправку сообщения."""
        if self._current_assistant_content:
            assistant_message = ChatMessage(
                role="assistant", content=self._current_assistant_content
            )
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self.send_btn.setEnabled(True)
        self.send_btn.setText("Отправить")
        self.status_label.setText("Готов к работе")
        self._current_assistant_content = ""

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
