"""Панель чата с поддержкой режимов Chat / Plan / Agent.

Тонкий координатор: UI, приём сигналов фоновых потоков и делегирование
тяжёлой логики специализированным менеджерам:

* `BackendManager` — Ollama, локальная SLM, `ModelRouter`;
* `ProjectContext` — карта проекта, правила, scope;
* `AttachedFilesHandler` — `@file`/`@dir`/`@path`;
* `ResearchController` — research-режим, гипотезы, паспорт;
* `SessionManager` — создание/загрузка сессий.
"""

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("LLAMA_LOG_LEVEL", "WARN")

from loguru import logger
from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agents.agent_loop import AgentContext
from agents.context_loader import load_agents_context
from agents.hypotheses import HypothesisStore
from agents.model_router import BackendType
from agents.ollama_client import ChatMessage
from core.config import AgentMode, ToolPolicy, load_config
from core.context_budget import rag_char_budget, truncate_to_chars
from core.model_registry import ModelRegistry
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager
from ui.chat.attached_files_handler import AttachedFilesHandler
from ui.chat.backend_manager import BackendManager
from ui.chat.message_item import (
    ChatActivityItem,
    ChatMessageItem,
    ChatSourcesItem,
)
from ui.chat.project_context import ProjectContext
from ui.chat.research_controller import ResearchController
from ui.chat.session_manager import SessionManager
from ui.chat.threads import (
    AgentChatThread,
    RagSearchThread,
    WebSearchThread,
)
from ui.chat.tool_labels import (
    MODE_DESCRIPTIONS,
    MODE_IDS,
    MODE_IDS_REVERSE,
    MODE_NAMES,
    PATH_COMMANDS,
    RAG_N_RESULTS,
    TOOL_ACTIVITY_LABELS,
    TOOL_DISPLAY_NAMES,
)
from ui.chat.tool_registry_builder import build_registry

# Regex для подсветки команд во вводе (собирается из PATH_COMMANDS).
_CMD_PATTERN = (
    r"^(@web|" + "|".join(re.escape(c) for c in PATH_COMMANDS) + r")(?=\s|$)"
)


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    # --- Сигналы наружу ---
    mode_changed = Signal(str)
    agent_panel_toggle = Signal(bool)
    refresh_models_requested = Signal()
    settings_changed = Signal(dict)
    backend_status_changed = Signal(str)
    directory_attach_requested = Signal(str)
    source_activated = Signal(str, int)

    def __init__(
        self,
        session_store,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        vector_store=None,
        project_root: str = "",
    ) -> None:
        super().__init__()

        # --- Публичное состояние (используется MainWindow и агентами) ---
        self.base_url = base_url
        self.current_model = model
        self.vector_store = vector_store
        self.project_root = project_root
        self.tool_policies: dict[str, ToolPolicy] = {}
        self.registry: ModelRegistry | None = None
        self.role_manager: RoleManager | None = None
        self.current_role_id: str = "default"
        self.prompt_manager: SystemPromptManager | None = None
        self.search_cache = None

        # --- Внутреннее состояние панели ---
        self._messages: list[ChatMessage] = []
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget: ChatMessageItem | None = None
        self._agent_thread: AgentChatThread | None = None
        self._rag_thread: RagSearchThread | None = None
        self._web_thread: WebSearchThread | None = None
        self._rag_context: str | None = None
        self._rag_scope: tuple[str, list[str]] | None = None
        self._generating: bool = False

        # Хранилище гипотез живёт на уровне сессии чата (передаётся в AgentContext).
        self._hypotheses = HypothesisStore()

        # --- Менеджеры ---
        self.session_manager = SessionManager(session_store)
        self.project_context = ProjectContext()
        if project_root:
            self.project_context.set_root(project_root)

        self.attached_files_handler = AttachedFilesHandler(self)
        self.backend_manager = BackendManager(base_url=base_url, parent=self)
        self.research_controller = ResearchController(self)

        # --- UI ---
        self._setup_ui()
        self._connect_manager_signals()

        QTimer.singleShot(100, self.backend_manager.detect_backends)

    # =============================================================
    # Подключение сигналов менеджеров
    # =============================================================

    def _connect_manager_signals(self) -> None:
        """Связывает сигналы менеджеров со слотами панели."""
        # --- BackendManager ---
        self.backend_manager.status_message.connect(self.status_label.setText)
        self.backend_manager.backend_status_changed.connect(
            self._on_backend_status_changed
        )

        # --- AttachedFilesHandler ---
        self.attached_files_handler.message_emitted.connect(
            lambda text: self._add_message("system", text)
        )
        self.attached_files_handler.error_emitted.connect(
            lambda text: self._add_message("system", text)
        )
        self.attached_files_handler.file_attached.connect(self._on_file_attached)
        self.attached_files_handler.directory_requested.connect(
            self.directory_attach_requested.emit
        )
        self.attached_files_handler.resend_requested.connect(
            self._on_resend_requested
        )

        # --- ResearchController ---
        self.research_controller.insert_widget.connect(self._insert_scroll_widget)
        self.research_controller.scroll_to_bottom.connect(self._scroll_to_bottom)

    # =============================================================
    # UI
    # =============================================================

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Верхняя панель ---
        top_bar = QHBoxLayout()

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
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_bar.addWidget(self.model_combo)

        self.refresh_models_btn = QPushButton("🔄")
        self.refresh_models_btn.setToolTip(
            "Проверить поддержку инструментов моделями"
        )
        self.refresh_models_btn.setStyleSheet("font-size: 14px; padding: 2px 8px;")
        self.refresh_models_btn.clicked.connect(self.refresh_models_requested.emit)
        top_bar.addWidget(self.refresh_models_btn)

        self.model_status_label = QLabel("")
        self.model_status_label.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; color: gray;"
        )
        top_bar.addWidget(self.model_status_label)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        for index, description in MODE_DESCRIPTIONS.items():
            self.mode_combo.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setToolTip(MODE_DESCRIPTIONS[0])
        self.mode_combo.setCurrentIndex(2)
        self.mode_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)

        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setToolTip("Настройки агента")
        self.gear_btn.setStyleSheet(
            "QPushButton { font-size: 16px; padding: 2px 8px; border: none;"
            " background: transparent; }"
            " QPushButton:hover { background: #555; border-radius: 4px; }"
        )
        self.gear_btn.clicked.connect(lambda: self.agent_panel_toggle.emit(True))
        top_bar.addWidget(self.gear_btn)

        top_bar.addStretch()

        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setStyleSheet("font-size: 13px; padding: 4px 12px;")
        self.clear_btn.clicked.connect(self._clear_chat)
        top_bar.addWidget(self.clear_btn)

        layout.addLayout(top_bar)

        # --- Строка статуса инструментов ---
        self.tool_status_label = QLabel("🔧 Инструменты активны")
        self.tool_status_label.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; background: #e8f5e9;"
            " color: #2e7d32; border: 1px solid #a5d6a7;"
            " border-radius: 4px; margin: 2px 0;"
        )
        self.tool_status_label.setVisible(False)
        layout.addWidget(self.tool_status_label)

        # --- Область сообщений ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: 1px solid #ccc; border-radius: 4px;"
            " background: transparent; }"
        )

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("_scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(0)
        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)

        self.scroll_area.viewport().installEventFilter(self)
        layout.addWidget(self.scroll_area, stretch=1)

        # --- Строка статуса ---
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet(
            "font-size: 12px; color: gray; padding: 4px 8px;"
            " background: #f5f5f5; border-radius: 4px;"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # --- Поле ввода ---
        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(
            "Введите сообщение... (CTRL+Enter — отправить, @web — поиск, "
            "@file — прикрепить файл, @dir — прикрепить папку)"
        )
        self.input_edit.setFixedHeight(100)
        self.input_edit.setStyleSheet(
            "font-size: 14px; padding: 8px; border: 1px solid #ccc;"
            " border-radius: 4px;"
        )
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        self.input_edit.textChanged.connect(self._highlight_commands)
        input_layout.addWidget(self.input_edit, stretch=1)

        self.send_btn = QPushButton("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2;"
            " color: white; border: none; border-radius: 4px;"
        )
        self.send_btn.clicked.connect(self._toggle_send_stop)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

    def _insert_scroll_widget(self, widget: QWidget) -> None:
        """Вставляет виджет перед stretch-спейсером в конце ленты."""
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)

    def _add_message(
        self, role: str, content: str, mode_name: str = ""
    ) -> ChatMessageItem:
        """Добавляет сообщение в ленту."""
        widget = ChatMessageItem(role, content, mode_name=mode_name)
        self._insert_scroll_widget(widget)
        self._update_bubble_width(widget)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return widget

    def _show_activity(self, text: str) -> None:
        """Показывает серую строку-событие в ленте."""
        if not text:
            return
        item = ChatActivityItem(text)
        self._insert_scroll_widget(item)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _show_sources(self, sources: list[tuple[str, int, int]]) -> None:
        """Показывает сворачиваемый блок источников RAG."""
        if not sources:
            return
        item = ChatSourcesItem(sources)
        item.source_activated.connect(self._on_source_activated)
        self._insert_scroll_widget(item)
        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot(str, int)
    def _on_source_activated(self, file_path: str, line: int) -> None:
        if self.project_root and not Path(file_path).is_absolute():
            file_path = str(Path(self.project_root) / file_path)
        self.source_activated.emit(file_path, line)

    # =============================================================
    # Ширины «пузырей» (адаптивная вёрстка)
    # =============================================================

    def _update_bubble_width(self, widget: QWidget | None = None) -> None:
        viewport = self.scroll_area.viewport()
        if not viewport:
            return
        scroll_width = viewport.width()
        if scroll_width <= 0:
            return

        ratios = {"user": 0.80, "assistant": 0.95, "tool": 0.90, "system": 0.90}

        def apply_policy(bubble, role: str) -> None:
            max_width = int(scroll_width * ratios.get(role, 0.90))
            bubble.setMaximumWidth(max_width)
            bubble.setMinimumWidth(0)
            bubble.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )

        if widget is not None and isinstance(widget, ChatMessageItem):
            apply_policy(widget.bubble, widget.role)
        else:
            for i in range(self.scroll_layout.count()):
                item = self.scroll_layout.itemAt(i)
                if item and item.widget():
                    w = item.widget()
                    if isinstance(w, ChatMessageItem):
                        apply_policy(w.bubble, w.role)

    def _relayout_all_items(self) -> None:
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessageItem):
                self._update_bubble_width(item.widget())

    # =============================================================
    # Подсветка команд во вводе + eventFilter
    # =============================================================

    def _highlight_commands(self) -> None:
        """Подсвечивает @web/@file/@dir/@path в поле ввода."""
        try:
            if not self.input_edit or not self.input_edit.document():
                return
            text = self.input_edit.toPlainText()
            if not text:
                return
            self.input_edit.blockSignals(True)
            try:
                cursor = self.input_edit.textCursor()
                if cursor.isNull():
                    return
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.setCharFormat(QTextCharFormat())
                cursor.clearSelection()

                for m in re.finditer(_CMD_PATTERN, text, re.MULTILINE):
                    start, end = m.start(), m.end()
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setForeground(QColor("#42a5f5"))
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setBackground(QColor("#e3f2fd"))
                    fmt.setUnderlineStyle(
                        QTextCharFormat.UnderlineStyle.SingleUnderline
                    )
                    fmt.setUnderlineColor(QColor("#42a5f5"))
                    cursor.mergeCharFormat(fmt)
            finally:
                self.input_edit.blockSignals(False)
        except Exception as e:
            logger.warning(f"Ошибка в _highlight_commands: {e}")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Перехват событий ввода и resize области прокрутки.

        ВАЖНО: `input_edit` может ещё не существовать, если eventFilter
        сработал во время построения UI — viewport QScrollArea получает
        resize при добавлении в layout раньше, чем создаётся поле ввода.
        Поэтому доступ к атрибуту идёт через `getattr`.
        """
        input_edit = getattr(self, "input_edit", None)
        if (
            input_edit is not None
            and obj is input_edit
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Return
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            self._toggle_send_stop()
            return True

        scroll_area = getattr(self, "scroll_area", None)
        if (
            scroll_area is not None
            and obj is scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._update_bubble_width()

        return super().eventFilter(obj, event)


    # =============================================================
    # Режим / роль / модель
    # =============================================================

    def _current_mode_name(self) -> str:
        return MODE_NAMES.get(self.mode_combo.currentIndex(), "Чат")

    def _current_mode(self) -> AgentMode:
        mapping = {0: AgentMode.CHAT, 1: AgentMode.PLAN, 2: AgentMode.AGENT}
        return mapping.get(self.mode_combo.currentIndex(), AgentMode.CHAT)

    def _on_mode_changed(self, index: int) -> None:
        mode = MODE_IDS.get(index, "chat")
        self.mode_combo.setToolTip(MODE_DESCRIPTIONS.get(index, ""))

        if index > 0 and self.registry:
            model_name = self.get_current_model()
            cap = self.registry.get(model_name)
            if cap and not cap.supports_tools:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(0)
                self.mode_combo.blockSignals(False)
                QMessageBox.warning(
                    self,
                    "Режим недоступен",
                    f"Модель '{model_name}' не поддерживает инструменты.\n"
                    "Используйте модели с пометкой ✅ Agent/Plan.",
                )
                return

        self.mode_changed.emit(mode)
        self._emit_settings_changed()

    def _emit_settings_changed(self) -> None:
        self.settings_changed.emit(
            {
                "model": self.get_current_model(),
                "mode": MODE_IDS.get(self.mode_combo.currentIndex(), "chat"),
            }
        )

    def _on_role_changed(self, index: int) -> None:
        if self.role_manager:
            role_id = self.role_combo.itemData(index)
            self.current_role_id = role_id
            logger.info(f"Смена роли на: {role_id}")

    def _on_model_changed(self, index: int) -> None:
        model_name = self.get_current_model()
        if model_name:
            self._update_model_status(model_name)
            self._check_mode_compatibility()
            self._save_current_settings()

    def _save_current_settings(self) -> None:
        try:
            self.settings_changed.emit(
                {
                    "model": self.get_current_model(),
                    "mode": MODE_IDS.get(self.mode_combo.currentIndex(), "chat"),
                }
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить настройки: {e}")

    # =============================================================
    # Отмена генерации
    # =============================================================

    @staticmethod
    def _stop_thread(thread: QThread | None, wait_ms: int = 1000) -> None:
        """Аккуратно останавливает QThread (quit → wait → terminate)."""
        if thread is None or not thread.isRunning():
            return
        thread.quit()
        thread.wait(wait_ms)
        if thread.isRunning():
            thread.terminate()
            thread.wait(500)

    def _toggle_send_stop(self) -> None:
        if self._generating:
            self._stop_generation()
        else:
            self._send_message()

    def _stop_generation(self) -> None:
        """Останавливает генерацию с сохранением визуального лога.

        Семантика:
        * серый поток рассуждений остаётся видимым (с пометкой «прервано»);
        * виджеты гипотез остаются в ленте как визуальный архив;
        * активный `HypothesisStore` очищается — в следующий запрос
          гипотезы НЕ утекают;
        * паспорт не строится.
        """
        self.research_controller.mark_cancelled()
        self._generating = False

        if (
            self._agent_thread
            and self._agent_thread.isRunning()
            and self._agent_thread.client
        ):
            self._agent_thread.client.cancel_request()

        self._stop_thread(self._web_thread)
        self._stop_thread(self._rag_thread)
        self._stop_thread(self._agent_thread)

        # Очищаем активный store — в следующий запрос гипотезы не утекут.
        # Визуальные виджеты (в research_controller) НЕ трогаем — это архив.
        self._hypotheses.clear()

        self._reset_send_button()
        self.status_label.setText("⏹ Остановлено пользователем")
        self._add_message(
            "system",
            "⏹ Генерация остановлена пользователем.\n"
            "Визуальный лог рассуждений сохранён выше для ознакомления, "
            "но НЕ будет учтён в следующем запросе.",
        )
        self._reset_generation_state()

    # =============================================================
    # Отправка сообщений
    # =============================================================

    def _send_message(self) -> None:
        if self._generating:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self.input_edit.clear()
        self.input_edit.setFocus()

        if text.startswith("@web"):
            query = text[4:].strip()
            if query:
                self._start_web_search(query)
            else:
                self._add_message("system", "⚠️ Укажите запрос после @web")
            return

        parsed = self.attached_files_handler.extract_path_command(text)
        if parsed is not None:
            self.attached_files_handler.handle_command(*parsed)
            return

        project_for_session = self.project_root if self.project_root else "global"
        self.session_manager.ensure_session(project_for_session)

        user_message = ChatMessage(role="user", content=text)
        self._messages.append(user_message)
        self.session_manager.save_message(user_message)
        self._add_message("user", text)
        self.status_label.setText("Обработка...")

        mode = self._current_mode()
        if mode == AgentMode.CHAT:
            self._start_chat(text)
        else:
            self._start_agent(text, mode)

    def _on_file_attached(self, name: str, size_chars: int, lines: int) -> None:
        self._add_message(
            "system",
            f"📎 Прикреплён файл: {name} "
            f"({lines} строк, {size_chars} символов). "
            "В контекст попадёт только начало и конец, остальное — "
            "через инструмент read_attached_file.",
        )

    @Slot(str)
    def _on_resend_requested(self, rest: str) -> None:
        self.input_edit.setPlainText(rest)
        self._send_message()

    # =============================================================
    # Web search
    # =============================================================

    def _start_web_search(self, query: str) -> None:
        if not query.strip():
            self._add_message("system", "⚠️ Укажите запрос после @web")
            return

        project_for_session = self.project_root if self.project_root else "global"
        self.session_manager.ensure_session(project_for_session)

        user_message = ChatMessage(role="user", content=f"@web {query}")
        self._messages.append(user_message)
        self.session_manager.save_message(user_message)
        self._add_message("user", f"🌐 Поиск в интернете: {query}")

        self._generating = True
        self._set_button_stop()
        self.input_edit.setEnabled(False)
        self.status_label.setText("🌐 Поиск в интернете...")
        self._show_activity(f"🌐 Ищу в интернете: {query}")

        self._web_thread = WebSearchThread(
            router=self.backend_manager.router,
            model=self.get_current_model(),
            query=query,
            messages_history=list(self._messages),
            search_cache=self.search_cache,
        )
        self._web_thread.chunk_received.connect(self._on_chunk)
        self._web_thread.thinking_received.connect(self._on_thinking)
        self._web_thread.finish_received.connect(self._on_web_finish)
        self._web_thread.error_occurred.connect(self._on_web_error)
        self._web_thread.start()

    # =============================================================
    # Chat / Agent: вход в ReAct-цикл
    # =============================================================

    def _start_chat(self, text: str) -> None:
        self._generating = True
        self._set_button_stop()
        self.status_label.setText("Поиск в кодовой базе...")
        self._rag_context = None
        self._rag_scope = self.project_context.query_scope(text)

        if self.vector_store:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
            )
            self._rag_thread.results_ready.connect(self._on_rag_results)
            self._rag_thread.search_error.connect(self._on_rag_error)
            self._rag_thread.finished.connect(lambda: self._do_chat(text))
            self._rag_thread.start()
        else:
            self._do_chat(text)

    def _start_agent(self, text: str, mode: AgentMode) -> None:
        self._generating = True
        self._set_button_stop()
        self.status_label.setText("🤖 Агент думает...")
        self._rag_scope = self.project_context.query_scope(text)

        if self.vector_store:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
            )
            self._rag_thread.results_ready.connect(
                lambda ctx, src: self._on_agent_rag_ready(ctx, src, text, mode)
            )
            self._rag_thread.search_error.connect(
                lambda err: self._do_agent(text, mode, "")
            )
            self._rag_thread.start()
        else:
            self._do_agent(text, mode, "")

    # --- RAG ---

    @Slot(str, list)
    def _on_rag_results(self, context: str, sources: list) -> None:
        merged = self.project_context.merge_scope_into_context(
            context, self._rag_scope
        )
        if merged:
            budget = rag_char_budget(self.backend_manager.router.num_ctx)
            self._rag_context = truncate_to_chars(merged, budget)
        else:
            self._rag_context = None
        if sources:
            self._show_sources(sources)
            summary = ", ".join(f"{p}:{s}" for p, s, _end in sources[:3])
            self.status_label.setText(f"📚 Источники: {summary}")

    @Slot(str)
    def _on_rag_error(self, error_msg: str) -> None:
        logger.warning(f"Ошибка RAG: {error_msg}; продолжаем без контекста")

    def _on_agent_rag_ready(
        self,
        context: str,
        sources: list,
        text: str,
        mode: AgentMode,
    ) -> None:
        merged = self.project_context.merge_scope_into_context(
            context, self._rag_scope
        )
        if merged:
            budget = rag_char_budget(self.backend_manager.router.num_ctx)
            merged = truncate_to_chars(merged, budget)
        if sources:
            self._show_sources(sources)
            summary = ", ".join(f"{p}:{s}" for p, s, _end in sources[:3])
            self.status_label.setText(f"📚 {summary}")
        self._do_agent(text, mode, merged)

    # --- Сборка AgentContext ---

    def _build_system_prompt(self, mode_name: str) -> str:
        """Склеивает роль + системный промт режима (с шаблонизацией)."""
        role_prompt = self.get_current_role_prompt()
        p_root = Path(self.project_root) if self.project_root else None
        mode_prompt = (
            self.prompt_manager.get_prompt(
                mode_name,
                project_root=p_root,
                context=self.project_context.template_context(),
            )
            if self.prompt_manager
            else ""
        )
        return "\n\n".join(filter(None, [role_prompt, mode_prompt]))

    def _build_agent_context(
        self,
        mode: AgentMode,
        rag_context: str,
    ) -> AgentContext:
        """Собирает AgentContext для AgentChatThread."""
        router = self.backend_manager.router
        num_ctx = router.num_ctx

        attached_text = self.attached_files_handler.render_context(
            num_ctx, rag_active=bool(rag_context)
        )
        merged_rag = "\n\n".join(filter(None, [attached_text, rag_context]))

        return AgentContext(
            system_prompt=self._build_system_prompt(mode.value),
            rag_context=merged_rag,
            agents_context=load_agents_context(self.project_root),
            project_root=self.project_root,
            project_name=self.project_context.project_name(),
            project_map=self.project_context.map_text(),
            vector_store=self.vector_store,
            num_ctx=num_ctx,
            mode=mode,
            search_cache=cast(Any, self.search_cache),
            attached_files=(
                self.attached_files_handler.store
                if self.attached_files_handler
                else None
            ),
            hypotheses=self._hypotheses,
            extra_rules=self.project_context.rules(),
        )

    # --- Chat ---

    def _do_chat(self, text: str) -> None:
        logger.debug("_do_chat: запуск через AgentChatThread (ReAct)")
        self.research_controller.reset()

        client = self.backend_manager.router.active_client
        if client is None:
            self._add_message("system", "⚠️ Нет доступного LLM-бэкенда.")
            self._reset_send_button()
            return

        context = self._build_agent_context(AgentMode.CHAT, self._rag_context or "")
        registry = build_registry(
            AgentMode.CHAT,
            attached_files=bool(self.attached_files_handler),
            tool_policies=self.tool_policies,
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        self._agent_thread = AgentChatThread(
            client=client,
            registry=registry,
            model=self.get_current_model(),
            context=context,
            messages_history=list(self._messages),
            agent_mode=AgentMode.CHAT,
            max_iterations=self._iteration_limit(AgentMode.CHAT),
        )
        self._connect_agent_thread_signals()

    # --- Agent ---

    def _do_agent(self, text: str, mode: AgentMode, rag_context: str) -> None:
        self.research_controller.reset()

        client = self.backend_manager.router.active_client
        if client is None:
            self._add_message("system", "⚠️ Нет доступного LLM-бэкенда для агента.")
            self._reset_send_button()
            return

        context = self._build_agent_context(mode, rag_context)
        registry = build_registry(
            mode,
            attached_files=bool(self.attached_files_handler),
            tool_policies=self.tool_policies,
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        self._agent_thread = AgentChatThread(
            client=client,
            registry=registry,
            model=self.get_current_model(),
            context=context,
            messages_history=list(self._messages),
            agent_mode=mode,
            max_iterations=self._iteration_limit(mode),
        )
        self._connect_agent_thread_signals()

    def _iteration_limit(self, mode: AgentMode) -> int:
        default = 30 if mode == AgentMode.AGENT else 7
        try:
            cfg = load_config()
            return (
                cfg.agent_max_iterations
                if mode == AgentMode.AGENT
                else cfg.chat_max_iterations
            )
        except Exception:
            return default

    def _connect_agent_thread_signals(self) -> None:
        """Подключает все сигналы AgentChatThread к слотам панели."""
        if self._agent_thread is None:
            return

        # --- Research-сигналы (делегируем в ResearchController) ---
        self._agent_thread.research_mode_detected.connect(
            self.research_controller.on_research_mode_detected
        )
        self._agent_thread.thought_chunk.connect(
            self.research_controller.on_thought_chunk
        )
        self._agent_thread.final_answer_started.connect(
            self.research_controller.on_final_answer_started
        )
        self._agent_thread.final_chunk.connect(
            self.research_controller.on_final_chunk
        )
        self._agent_thread.research_summary_ready.connect(
            self.research_controller.on_research_summary_ready
        )

        # --- Обычные сигналы ---
        self._agent_thread.chunk_received.connect(self._on_chunk)
        self._agent_thread.thinking_received.connect(self._on_thinking)
        self._agent_thread.tool_call_detected.connect(self._on_tool_call_detected)
        self._agent_thread.tool_executed.connect(self._on_tool_executed)
        self._agent_thread.tool_started.connect(self._on_tool_started)
        self._agent_thread.finish_received.connect(self._on_agent_finish)
        self._agent_thread.error_occurred.connect(self._on_agent_error)
        with contextlib.suppress(Exception):
            self._agent_thread.iteration_limit_exceeded.connect(
                self._on_iteration_limit_exceeded
            )
        self._agent_thread.tools_status_changed.connect(
            self._on_tools_status_changed
        )
        self._agent_thread.status_changed.connect(self._on_status_event)
        self._agent_thread.start()

    # =============================================================
    # Стриминг в UI
    # =============================================================

    @Slot(str)
    def _on_thinking(self, token: str) -> None:
        self._current_thinking_content += token
        if self._last_assistant_widget is None:
            widget = ChatMessageItem(
                "assistant",
                "",
                thinking=self._current_thinking_content,
                mode_name=self._current_mode_name(),
            )
            self._last_assistant_widget = widget
            self._insert_scroll_widget(widget)
            self._update_bubble_width(widget)
        else:
            self._last_assistant_widget.update_thinking(
                self._current_thinking_content
            )
        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot(str)
    def _on_chunk(self, content: str) -> None:
        """Обычная дорожка (не-research): стриминг в сообщение."""
        self._current_assistant_content += content
        if self._last_assistant_widget is None:
            widget = ChatMessageItem(
                "assistant",
                self._current_assistant_content,
                thinking=self._current_thinking_content,
                mode_name=self._current_mode_name(),
            )
            self._last_assistant_widget = widget
            self._insert_scroll_widget(widget)
            self._update_bubble_width(widget)
        else:
            self._last_assistant_widget.update_content(
                self._current_assistant_content
            )
        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot(str, str, str)
    def _on_tool_call_detected(
        self, name: str, description: str, arguments_json: str
    ) -> None:
        from ui.dialogs.tool_confirmation_dialog import ToolConfirmationDialog

        arguments = json.loads(arguments_json)
        display_name = TOOL_DISPLAY_NAMES.get(name, name)
        dialog = ToolConfirmationDialog(display_name, description, arguments, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            approved = dialog.is_approved()
            new_args = dialog.get_arguments()
            if self._agent_thread:
                self._agent_thread.respond_to_tool(approved, new_args)
        else:
            if self._agent_thread:
                self._agent_thread.respond_to_tool(False)

    @Slot(str, str, bool)
    def _on_tool_executed(self, name: str, content: str, is_error: bool) -> None:
        """Обрабатывает завершение инструмента.

        * В research-режиме — пишет в серый поток.
        * Для тулов гипотез — обновляет/создаёт виджет `ChatHypothesisItem`.
        * В не-research режиме — стандартное сообщение в ленту.
        """
        # Результат в серый поток (кроме гипотез — их обработаем отдельно).
        if name not in ("record_hypothesis", "update_hypothesis"):
            preview = (content or "").strip().replace("\n", " ")[:200]
            self.research_controller.append_result_to_stream(
                name, ok=not is_error, preview=preview
            )

        # Гипотезы: попытаться обновить виджет.
        if name in ("record_hypothesis", "update_hypothesis") and not is_error:
            if self.research_controller.update_hypothesis_widget(content):
                return

        # В research-режиме tool-сообщения в ленту не пишем — всё в сером потоке.
        if self.research_controller.thought_stream is not None:
            if not self.research_controller.is_active:
                self._add_tool_message(name, content, is_error)
            return

        self._add_tool_message(name, content, is_error)

    @Slot(str, str)
    def _on_tool_started(self, name: str, arguments_json: str) -> None:
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            args = {}
        arg_summary = ", ".join(f"{k}={v}" for k, v in args.items())
        label = TOOL_ACTIVITY_LABELS.get(name, f"🛠️ {name}")
        activity = f"{label}: {arg_summary}" if arg_summary else label
        self.status_label.setText(activity)

        # В серый поток пишем компактно.
        self.research_controller.append_action_to_stream(name, args)

        # Activity-строки в ленте — только в не-research.
        if not self.research_controller.is_active:
            self._show_activity(activity)

        self._last_assistant_widget = None
        self._current_assistant_content = ""

    @Slot(str)
    def _on_status_event(self, text: str) -> None:
        """Статус — в серый поток (нудж/фаза) и в статус-бар."""
        if text:
            if text.startswith("🔁") or text.startswith("🪞"):
                self.research_controller.append_nudge_to_stream(text)
            elif text.startswith("📌"):
                self.research_controller.append_phase_to_stream(text)

        if not self.research_controller.is_active:
            self._show_activity(text)
        self.status_label.setText(text)

    def _add_tool_message(
        self, name: str, content: str, is_error: bool = False
    ) -> None:
        prefix = "❌" if is_error else "🔧"
        display_text = f"{prefix} Инструмент: {name}\n{content[:500]}"
        if len(content) > 500:
            display_text += "\n... (результат обрезан)"
        self._add_message("tool", display_text)

    # =============================================================
    # Завершение / ошибки
    # =============================================================

    @Slot(str)
    def _on_agent_finish(self, result: str) -> None:
        # --- Research-режим: паспорт с выводами, без обычного пузыря ---
        if (
            self.research_controller.is_active
            and not self.research_controller.is_cancelled
        ):
            self.research_controller.finalize_research()
            self._reset_generation_state()
            return

        # --- Обычный режим ---
        if result and result != "Отменено":
            assistant_message = ChatMessage(role="assistant", content=result)
            self._messages.append(assistant_message)
            self.session_manager.save_message(assistant_message)
            self._show_activity("✅ Готово")

        self._reset_generation_state()

    @Slot(str)
    def _on_agent_error(self, error_msg: str) -> None:
        if self.research_controller.thought_stream is not None:
            self.research_controller.thought_stream.mark_interrupted(
                f"Ошибка: {error_msg[:120]}"
            )
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка")
        self.research_controller.reset()

    @Slot()
    def _on_iteration_limit_exceeded(self) -> None:
        self._show_activity(
            "⏸ Достигнут лимит итераций. Нажмите «Продолжить», чтобы агент "
            "сформировал Final Answer"
        )
        self.status_label.setText(
            "Достигнут лимит итераций — готово к продолжению"
        )

    @Slot(bool)
    def _on_tools_status_changed(self, active: bool) -> None:
        if active:
            self.tool_status_label.setText("🔧 Инструменты активны")
            self.tool_status_label.setStyleSheet(
                "font-size: 12px; padding: 4px 8px; background: #e8f5e9;"
                " color: #2e7d32; border: 1px solid #a5d6a7;"
                " border-radius: 4px; margin: 2px 0;"
            )
        else:
            self.tool_status_label.setText(
                "⚠️ Инструменты отключены — модель не поддерживает вызов инструментов."
            )
            self.tool_status_label.setStyleSheet(
                "font-size: 12px; padding: 4px 8px; background: #fff3e0;"
                " color: #e65100; border: 1px solid #ffcc80;"
                " border-radius: 4px; margin: 2px 0;"
            )
        self.tool_status_label.setVisible(True)

    def _reset_generation_state(self) -> None:
        """Сбрасывает состояние генерации после завершения/ошибки/отмены."""
        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None
        self.research_controller.reset()

    def _reset_send_button(self) -> None:
        self._generating = False
        self.input_edit.setEnabled(True)
        self.send_btn.setText("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2;"
            " color: white; border: none; border-radius: 4px;"
        )
        self.status_label.setText("Готов к работе")

    def _set_button_stop(self) -> None:
        self.send_btn.setText("■")
        self.send_btn.setStyleSheet(
            "font-size: 18px; padding: 8px 20px; background: #d32f2f;"
            " color: white; border: none; border-radius: 4px;"
        )

    @Slot()
    def _on_web_finish(self) -> None:
        self._reset_generation_state()
        self._show_activity("✅ Готово")
        self.status_label.setText("🌐 Поиск завершён")

    @Slot(str)
    def _on_web_error(self, error_msg: str) -> None:
        self._add_message("system", f"❌ {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка поиска")

    # =============================================================
    # Прокрутка и очистка
    # =============================================================

    def _scroll_to_bottom(self) -> None:
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_chat(self, keep_session: bool = False) -> None:
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._messages.clear()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._rag_context = None
        self._rag_scope = None
        self._last_assistant_widget = None
        self.attached_files_handler.clear()
        self._hypotheses.clear()
        self.research_controller.clear()

        if not keep_session:
            self.session_manager.reset()
        self.status_label.setText("Чат очищен")

    # =============================================================
    # Backend status
    # =============================================================

    @Slot()
    def _on_backend_status_changed(self) -> None:
        """Формирует метку активного бэкенда для статус-бара MainWindow."""
        backend = self.backend_manager.active_backend
        if backend == BackendType.OLLAMA:
            label = f"Ollama: {self.get_current_model()}"
        elif backend == BackendType.LOCAL_SLM:
            name = self.backend_manager.slm_model_name or "локальная модель"
            label = f"SLM: {name}"
        elif backend == BackendType.RULE_BASED:
            label = "Резервный режим (без LLM)"
        else:
            label = "Нет доступного бэкенда"
        self.backend_status_changed.emit(label)

    def reload_slm(self) -> None:
        """Перезагружает локальную SLM из конфига (без автовыбора).

        Публичная обёртка — `MainWindow` вызывает её после диалога SLM.
        """
        self.backend_manager.reload_slm()

    @property
    def router(self):
        """ModelRouter (используется MainWindow для VectorStore)."""
        return self.backend_manager.router

    # =============================================================
    # Публичный API для MainWindow
    # =============================================================

    def set_model(self, model: str) -> None:
        idx = self.model_combo.findData(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.addItem(model, model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    def get_current_model(self) -> str:
        model = self.model_combo.currentData()
        if model is not None:
            return str(model)
        text = self.model_combo.currentText()
        for prefix in ("✅ ", "⬇️ "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return text or "llama3.2"

    def set_tool_policy(self, tool_name: str, policy: Any) -> None:
        self.tool_policies[tool_name] = policy

    def set_vector_store(self, vector_store) -> None:
        self.vector_store = vector_store

    def set_project_root(self, project_root: str) -> None:
        self.project_root = project_root
        self.project_context.set_root(project_root)
        self._rag_scope = None

    def set_project_open(self, is_open: bool) -> None:
        if not is_open:
            self.input_edit.setPlaceholderText(
                "Введите сообщение... (@web — поиск, @file/@dir/@path — прикрепить)"
            )
            self.status_label.setText("Проект не открыт (RAG недоступен)")
        else:
            self.input_edit.setPlaceholderText(
                "Введите сообщение... (CTRL+Enter — отправить, @web — поиск, "
                "@file — прикрепить файл, @dir — прикрепить папку)"
            )
            self.status_label.setText("Готов к работе")
        self.send_btn.setVisible(True)

    def set_registry(self, registry) -> None:
        self.registry = registry
        self._update_model_list()

    def update_models(self, models: list[str]) -> None:
        self._update_model_list()

    def set_session_id(self, session_id: str) -> None:
        self.session_manager.set_session(session_id)

    def load_history(self, messages: list[ChatMessage]) -> None:
        self._clear_chat(keep_session=True)
        for msg in messages:
            self._add_message(msg.role, msg.content)
            self._messages.append(msg)

    def set_search_cache(self, cache) -> None:
        self.search_cache = cache

    def get_settings(self) -> dict:
        return {
            "model": self.get_current_model(),
            "mode": MODE_IDS.get(self.mode_combo.currentIndex(), "chat"),
        }

    def apply_settings(self, settings: dict) -> None:
        model = settings.get("model", "")
        if model:
            self.set_model(model)
        mode = settings.get("mode", "chat")
        idx = MODE_IDS_REVERSE.get(mode, 0)
        if 0 <= idx < self.mode_combo.count():
            self.mode_combo.setCurrentIndex(idx)

    def set_prompt_manager(self, manager) -> None:
        self.prompt_manager = manager
        self._update_mode_tooltips()

    def _update_mode_tooltips(self) -> None:
        if not self.prompt_manager:
            return
        for index, mode in enumerate(["chat", "plan", "agent"]):
            prompt = self.prompt_manager.get_prompt(mode)
            if prompt:
                short = prompt[:100] + "..." if len(prompt) > 100 else prompt
                self.mode_combo.setItemData(
                    index,
                    f"Системный промт:\n{short}",
                    Qt.ItemDataRole.ToolTipRole,
                )

    def set_role_manager(self, manager: RoleManager) -> None:
        self.role_manager = manager
        self.role_combo.blockSignals(True)
        self.role_combo.clear()

        for role in manager.get_all_roles():
            self.role_combo.addItem(f"{role.icon} {role.name}", role.id)

        idx = self.role_combo.findData("default")
        if idx >= 0:
            self.role_combo.setCurrentIndex(idx)
        self.role_combo.blockSignals(False)

    def get_current_role_prompt(self) -> str:
        if self.role_manager:
            return self.role_manager.get_role(self.current_role_id).system_prompt
        return ""

    def update_model_status(self, model_name: str) -> None:
        if not self.registry:
            return
        info = self.registry.get(model_name)
        if not info:
            return
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model_name:
                display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
                self.model_combo.setItemText(i, display)
                break
        if self.model_combo.currentData() == model_name:
            self._update_model_status(model_name)

    def set_checking_state(self, is_checking: bool) -> None:
        self.refresh_models_btn.setText("⏳" if is_checking else "🔄")

    def _update_model_list(self) -> None:
        if not self.registry:
            return
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        all_models = self.registry.get_all_models()
        sorted_names = sorted(
            all_models.keys(),
            key=lambda x: (not all_models[x].is_local, x.lower()),
        )
        for model_name in sorted_names:
            info = all_models[model_name]
            display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
            self.model_combo.addItem(display, model_name)

        if current and self.model_combo.findData(current) >= 0:
            self.model_combo.setCurrentIndex(self.model_combo.findData(current))
        else:
            for i in range(self.model_combo.count()):
                model_data = self.model_combo.itemData(i)
                if not model_data:
                    continue
                found = self.registry.get(model_data)
                if found is not None and found.is_local:
                    self.model_combo.setCurrentIndex(i)
                    break
            else:
                if self.model_combo.count() > 0:
                    self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._update_model_status(self.model_combo.currentData())

    def _update_model_status(self, model_name: str) -> None:
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

    def _check_mode_compatibility(self) -> None:
        if not self.registry:
            return
        model_name = self.get_current_model()
        cap = self.registry.get(model_name)

        if cap and not cap.supports_tools:
            current_mode = self._current_mode()
            if current_mode in (AgentMode.PLAN, AgentMode.AGENT):
                self.mode_combo.setCurrentIndex(0)
                QMessageBox.warning(
                    self,
                    "Режим недоступен",
                    f"Модель '{model_name}' не поддерживает инструменты.\n"
                    "Режим автоматически переключен на 'Chat'.",
                )

    def set_prompt_manager_noop(self) -> None:  # pragma: no cover
        """Заглушка на случай внешних вызовов — оставлена для совместимости."""
        pass