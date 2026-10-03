"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import json
import re
from pathlib import Path
from typing import Any, cast

from loguru import logger
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
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
from agents.download_slm import get_model_paths
from agents.local_llm_client import LocalLLMClient
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import ChatMessage, OllamaClient
from agents.rule_based_fallback import RuleBasedFallback
from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from core.model_registry import ModelRegistry
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager
from tools.weather import get_weather
from ui.chat.message_item import ChatMessageItem
from ui.chat.threads import (
    AgentChatThread,
    RagSearchThread,
    RouterChatThread,
    WebSearchThread,
    _OllamaCheckThread,
    _SLMDownloadThread,
    _SLMLoadThread,
)


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    tool_requested = Signal(str, str, str)
    mode_changed = Signal(str)
    agent_panel_toggle = Signal(bool)
    refresh_models_requested = Signal()
    settings_changed = Signal(dict)

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
        self._ollama_thread: RouterChatThread | None = None
        self._agent_thread: AgentChatThread | None = None
        self._rag_thread: RagSearchThread | None = None
        self._rag_context: str | None = None
        self._generating = False
        self._last_assistant_widget: ChatMessageItem | None = None
        self.tool_policies: dict[str, ToolPolicy] = {}
        self.registry: ModelRegistry | None = None

        self.role_manager: RoleManager | None = None
        self.current_role_id: str = "default"

        self.prompt_manager: SystemPromptManager | None = None

        self._router = ModelRouter()
        self._ollama_client = OllamaClient(base_url=base_url)
        self._router.set_ollama(self._ollama_client)

        self._rule_based = RuleBasedFallback(weather_tool=get_weather)
        self._router.set_rule_based(self._rule_based)

        self._slm_client: LocalLLMClient | None = None
        self._slm_loading = False
        self._slm_load_thread: _SLMLoadThread | None = None
        self._slm_download_thread: _SLMDownloadThread | None = None
        self._ollama_check_thread: _OllamaCheckThread | None = None

        self._setup_ui()
        QTimer.singleShot(100, self._detect_backends)

    # ---------------------------------------------------------
    # Детект и загрузка бэкендов
    # ---------------------------------------------------------

    def _detect_backends(self):
        """Полная детекция бэкендов при старте панели."""
        self._check_ollama_async()
        self._detect_and_load_slm()

    def _detect_and_load_slm(self):
        """Проверяет наличие файла активной SLM и загружает/скачивает её."""
        chat_path, embed_path = get_model_paths()
        if chat_path:
            self._load_slm_async(chat_path, embed_path)
        elif self._config_slm_enabled():
            self._download_and_load_slm()

    def reload_slm(self):
        """Перезагружает локальную SLM (после смены активной модели в UI)."""
        # Сбрасываем текущий клиент
        self._router.set_local_slm(None)
        self._router.set_slm_available(False)
        self._slm_client = None
        self._slm_loading = False

        # Останавливаем загрузку, если она идёт
        if self._slm_load_thread and self._slm_load_thread.isRunning():
            self._slm_load_thread.quit()
            self._slm_load_thread.wait(1000)
        if self._slm_download_thread and self._slm_download_thread.isRunning():
            self._slm_download_thread.quit()
            self._slm_download_thread.wait(1000)

        self.status_label.setText("⏳ Перезагрузка локальной модели...")
        QTimer.singleShot(50, self._detect_and_load_slm)

    def _config_slm_enabled(self) -> bool:
        try:
            from core.config import load_config

            cfg = load_config()
            return cfg.slm.enabled
        except Exception:
            return True

    def _check_ollama_async(self):
        self._ollama_check_thread = _OllamaCheckThread(self._ollama_client)
        self._ollama_check_thread.result.connect(self._on_ollama_check_result)
        self._ollama_check_thread.start()

    @Slot(bool)
    def _on_ollama_check_result(self, available: bool):
        self._router.set_ollama_available(available)
        if available:
            self.status_label.setText("✅ Ollama подключён")
        else:
            if self._slm_client is not None:
                self.status_label.setText(
                    "✅ Локальная модель загружена (Ollama недоступен)"
                )
            else:
                self.status_label.setText(
                    "⚠️ Ollama не найден. Используется резервный режим (погода/время/дата)."
                )


    @Slot(object)
    def _on_slm_loaded(self, client):
        self._slm_client = client
        self._router.set_local_slm(client)
        self._router.set_slm_available(True)
        self._slm_loading = False
        logger.info("Локальная SLM загружена и доступна как fallback")
        if self._router.active_backend == BackendType.LOCAL_SLM:
            self.status_label.setText("✅ Локальная модель загружена")

    def _load_slm_async(self, chat_path: Path, embed_path: Path | None):
        if self._slm_loading or self._slm_client is not None:
            return

        # Параметры инференса — из конфига
        n_ctx, n_threads, n_batch, n_gpu_layers = 4096, 4, 512, 0
        try:
            from core.config import load_config

            slm = load_config().slm
            n_ctx = slm.n_ctx
            n_threads = slm.n_threads
            n_batch = slm.n_batch
            n_gpu_layers = slm.n_gpu_layers
        except Exception:
            pass

        self._slm_loading = True
        self.status_label.setText("⏳ Загрузка локальной модели...")

        self._slm_load_thread = _SLMLoadThread(
            chat_path,
            embed_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_batch=n_batch,
            n_gpu_layers=n_gpu_layers,
        )
        self._slm_load_thread.finished.connect(self._on_slm_loaded)
        self._slm_load_thread.error.connect(self._on_slm_error)
        self._slm_load_thread.start()

    def _download_and_load_slm(self):
        if self._slm_loading or self._slm_client is not None:
            return

        try:
            from core.config import load_config

            slm = load_config().slm
        except Exception as e:
            logger.warning(f"Не удалось загрузить конфиг SLM: {e}")
            return

        if not slm.enabled:
            return

        info = slm.get_selected_info()
        if not info:
            logger.warning("Не найдена активная SLM-модель в конфиге")
            return

        self._slm_loading = True
        self.status_label.setText(
            f"⏳ Скачивание {info.display_name} (~{info.size_mb} МБ)..."
        )

        self._slm_download_thread = _SLMDownloadThread(
            model_repo=info.repo,
            model_file=info.file,
            embed_repo=slm.embed_model_repo,
            embed_file=slm.embed_model_file,
            is_split=info.is_split,
            parts=info.parts,
        )
        self._slm_download_thread.progress.connect(self._on_slm_progress)
        self._slm_download_thread.finished.connect(self._on_slm_downloaded)
        self._slm_download_thread.error.connect(self._on_slm_error)
        self._slm_download_thread.start()

    @Slot(str)
    def _on_slm_progress(self, message: str):
        self.status_label.setText(f"⏳ {message}")

    @Slot(str)
    def _on_slm_error(self, error_msg: str):
        self._slm_loading = False
        logger.error(f"Ошибка загрузки SLM: {error_msg}")
        self.status_label.setText(f"⚠️ SLM недоступна: {error_msg}")

    @Slot(str, str)
    def _on_slm_downloaded(self, chat_path: str, embed_path: str):
        self._slm_loading = False
        self._load_slm_async(
            Path(chat_path),
            Path(embed_path) if embed_path else None,
        )

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------

    def _add_message(
        self, role: str, content: str, mode_name: str = ""
    ) -> ChatMessageItem:
        widget = ChatMessageItem(role, content, mode_name=mode_name)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        self._update_bubble_width(widget)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return widget

    def _update_bubble_width(self, widget: QWidget | None = None) -> None:
        viewport = self.scroll_area.viewport()
        if not viewport:
            return
        scroll_width = viewport.width()
        if scroll_width <= 0:
            return

        ratios = {"user": 0.80, "assistant": 0.95, "tool": 0.90, "system": 0.90}

        def apply_policy(bubble, role: str):
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

    @Slot(str)
    def _on_thinking(self, token: str):
        self._current_thinking_content += token
        if self._last_assistant_widget is None:
            widget = ChatMessageItem(
                "assistant",
                "",
                thinking=self._current_thinking_content,
                mode_name=self._current_mode_name(),
            )
            self._last_assistant_widget = widget
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
            self._update_bubble_width(widget)
        else:
            self._last_assistant_widget.update_thinking(self._current_thinking_content)
        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot(str)
    def _on_chunk(self, content: str):
        self._current_assistant_content += content
        if self._last_assistant_widget is None:
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
            self._last_assistant_widget.update_content(self._current_assistant_content)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        self.refresh_models_btn = QPushButton("🔄")
        self.refresh_models_btn.setToolTip("Проверить поддержку инструментов моделями")
        self.refresh_models_btn.setStyleSheet("font-size: 14px; padding: 2px 8px;")
        self.refresh_models_btn.clicked.connect(self.refresh_models_requested.emit)
        top_bar.addWidget(self.refresh_models_btn)

        self.model_status_label = QLabel("")
        self.model_status_label.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; color: gray;"
        )
        top_bar.addWidget(self.model_status_label)

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_bar.addWidget(self.model_combo)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        self.mode_combo.setItemData(
            0,
            "Chat — Обычный диалог с LLM. Без инструментов, только текст.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.mode_combo.setItemData(
            1,
            "Plan — Чтение файлов и поиск по коду (read-only).",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.mode_combo.setItemData(
            2,
            "Agent — Полный доступ: чтение, запись, Git, поиск.",
            Qt.ItemDataRole.ToolTipRole,
        )
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
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
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
        self.status_label.setStyleSheet(
            "font-size: 12px; color: gray; padding: 4px 8px;"
            " background: #f5f5f5; border-radius: 4px;"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(
            "Введите сообщение... (CTRL + Enter — отправить)"
        )
        self.input_edit.setFixedHeight(100)
        self.input_edit.setStyleSheet(
            "font-size: 14px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, stretch=1)

        self.input_edit.textChanged.connect(self._highlight_web_command)
        self.scroll_area.viewport().installEventFilter(self)

        self.send_btn = QPushButton("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.send_btn.clicked.connect(self._toggle_send_stop)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

    def _highlight_web_command(self):
        try:
            if not self.input_edit or not self.input_edit.document():
                return
            text = self.input_edit.toPlainText()
            if not text:
                # Нечего подсвечивать — не трогаем формат/курсор.
                return
            self.input_edit.blockSignals(True)
            try:
                cursor = self.input_edit.textCursor()
                if cursor.isNull():
                    return
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.setCharFormat(QTextCharFormat())
                cursor.clearSelection()

                for m in re.finditer(r"^@web(?=\s|$)", text):
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
            logger.warning(f"Ошибка в _highlight_web_command: {e}")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent):
                if (
                    event.key() == Qt.Key.Key_Return
                    and event.modifiers() == Qt.KeyboardModifier.ControlModifier
                ):
                    self._toggle_send_stop()
                    return True

        if obj is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._update_bubble_width()

        return super().eventFilter(obj, event)

    def _current_mode_name(self) -> str:
        mapping = {0: "Чат", 1: "План", 2: "Агент"}
        return mapping.get(self.mode_combo.currentIndex(), "Чат")

    def _current_mode(self) -> AgentMode:
        mapping = {0: AgentMode.CHAT, 1: AgentMode.PLAN, 2: AgentMode.AGENT}
        return mapping.get(self.mode_combo.currentIndex(), AgentMode.CHAT)

    def _add_tool_message(self, name: str, content: str, is_error: bool = False):
        prefix = "❌" if is_error else "🔧"
        display_text = f"{prefix} Инструмент: {name}\n{content[:500]}"
        if len(content) > 500:
            display_text += "\n... (результат обрезан)"
        self._add_message("tool", display_text)

    def _on_mode_changed(self, index: int):
        mapping = {0: "chat", 1: "plan", 2: "agent"}
        mode = mapping.get(index, "chat")

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

    def _emit_settings_changed(self):
        mode_map = {0: "chat", 1: "plan", 2: "agent"}
        self.settings_changed.emit(
            {
                "model": self.get_current_model(),
                "mode": mode_map.get(self.mode_combo.currentIndex(), "chat"),
            }
        )

    def _toggle_agent_panel(self):
        self.agent_panel_toggle.emit(True)

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_chat(self, keep_session: bool = False):
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._messages.clear()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._rag_context = None
        if not keep_session:
            self._session_id = None
        self._last_assistant_widget = None
        self.status_label.setText("Чат очищен")

    def _create_session(self):
        if not self._session_id:
            project_path = self.project_root if self.project_root else "global"
            self._session_id = self.session_store.create_session(project_path)

    def _toggle_send_stop(self):
        if self._generating:
            self._stop_generation()
        else:
            self._send_message()

    def _stop_generation(self):
        self._generating = False

        if self._ollama_thread and self._ollama_thread.isRunning():
            self._ollama_thread.cancel()

        if (
            self._agent_thread
            and self._agent_thread.isRunning()
            and self._agent_thread.client
        ):
            self._agent_thread.client.cancel_request()

        if (
            hasattr(self, "_web_thread")
            and self._web_thread
            and self._web_thread.isRunning()
        ):
            self._web_thread.quit()
            self._web_thread.wait(1000)
            if self._web_thread.isRunning():
                self._web_thread.terminate()
                self._web_thread.wait(500)

        if self._rag_thread and self._rag_thread.isRunning():
            self._rag_thread.quit()
            self._rag_thread.wait(1000)
            if self._rag_thread.isRunning():
                self._rag_thread.terminate()
                self._rag_thread.wait(500)

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
        if self._generating:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        # ФИКС: забираем текст и сразу очищаем ввод — до любых ветвлений.
        # Это гарантирует, что поле ввода не «залипнет» ни при обычной
        # отправке, ни при @web-запросе.
        self.input_edit.clear()
        self.input_edit.setFocus()

        if text.startswith("@web"):
            query = text[4:].strip()
            if query:
                self._start_web_search(query)
            else:
                self._add_message("system", "⚠️ Укажите запрос после @web")
            return

        self._create_session()

        user_message = ChatMessage(role="user", content=text)
        self._messages.append(user_message)
        if self._session_id:
            self.session_store.save_message(self._session_id, user_message)
        self._add_message("user", text)
        self.status_label.setText("Обработка...")

        mode = self._current_mode()
        if mode == AgentMode.CHAT:
            self._start_chat(text)
        else:
            self._start_agent(text, mode)

    def _start_web_search(self, query: str):
        # Очистка поля ввода — ответственность вызывающего (_send_message).
        if not query.strip():
            self._add_message("system", "⚠️ Укажите запрос после @web")
            return

        self._create_session()
        user_message = ChatMessage(role="user", content=f"@web {query}")
        self._messages.append(user_message)
        if self._session_id:
            self.session_store.save_message(self._session_id, user_message)
        self._add_message("user", f"🌐 Поиск в интернете: {query}")

        self._generating = True
        self._set_button_stop()
        self.input_edit.setEnabled(False)
        self.status_label.setText("🌐 Поиск в интернете...")

        self._web_thread = WebSearchThread(
            router=self._router,
            model=self.get_current_model(),
            query=query,
            messages_history=list(self._messages),
            search_cache=getattr(self, "search_cache", None),
        )

        self._web_thread.chunk_received.connect(self._on_chunk)
        self._web_thread.thinking_received.connect(self._on_thinking)
        self._web_thread.finish_received.connect(self._on_web_finish)
        self._web_thread.error_occurred.connect(self._on_web_error)
        self._web_thread.start()

    def _start_chat(self, text: str):
        self._generating = True
        self._set_button_stop()
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
        self._rag_context = context
        if sources:
            self.status_label.setText(f"📚 Источники: {sources}")

    @Slot(str)
    def _on_rag_error(self, error_msg: str):
        logger.warning(f"Ошибка RAG: {error_msg}; продолжаем без контекста")

    def set_prompt_manager(self, manager):
        self.prompt_manager = manager
        self._update_mode_tooltips()

    def _update_mode_tooltips(self):
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

    def _start_agent(self, text: str, mode: AgentMode):
        self._generating = True
        self._set_button_stop()
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

    def _on_agent_rag_ready(
        self, context: str, sources: str, text: str, mode: AgentMode
    ):
        if sources:
            self.status_label.setText(f"📚 {sources}")
        self._do_agent(text, mode, context)

    def _do_chat(self, text: str):
        self.status_label.setText("Генерация ответа...")

        chat_messages: list[ChatMessage] = list(self._messages)

        system_prompt_parts = []
        if self.prompt_manager:
            p_root = Path(self.project_root) if self.project_root else None
            sys_prompt = self.prompt_manager.get_prompt("chat", project_root=p_root)
            if sys_prompt:
                system_prompt_parts.append(sys_prompt)

        if self._rag_context:
            system_prompt_parts.append(
                f"Контекст из кодовой базы:\n{self._rag_context}"
            )

        if system_prompt_parts:
            combined = "\n\n".join(system_prompt_parts)
            chat_messages.insert(0, ChatMessage(role="system", content=combined))

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()

        try:
            registry = self._build_registry()
        except Exception as e:
            logger.warning(f"Не удалось собрать registry для Chat: {e}")
            registry = None

        self._ollama_thread = RouterChatThread(
            router=self._router,
            model=model_name,
            messages=chat_messages,
            registry=registry,
            project_root=self.project_root,
            vector_store=self.vector_store,
            search_cache=getattr(self, "search_cache", None),
            max_iterations=4,
        )

        self._ollama_thread.chunk_received.connect(self._on_chunk)
        self._ollama_thread.thinking_received.connect(self._on_thinking)
        self._ollama_thread.tool_started.connect(self._on_tool_started)
        self._ollama_thread.tool_executed.connect(self._on_tool_executed)
        self._ollama_thread.finish_received.connect(self._on_finish)
        self._ollama_thread.error_occurred.connect(self._on_chat_error)
        self._ollama_thread.start()

    def _do_agent(self, text: str, mode: AgentMode, rag_context: str):
        client = self._router.active_client
        if client is None:
            self._add_message(
                "system", "⚠️ Нет доступного LLM-бэкенда для агента."
            )
            self._reset_send_button()
            return

        role_prompt = self.get_current_role_prompt()
        p_root = Path(self.project_root) if self.project_root else None
        mode_prompt = (
            self.prompt_manager.get_prompt(mode.value, project_root=p_root)
            if self.prompt_manager
            else ""
        )

        combined = "\n\n".join(filter(None, [role_prompt, mode_prompt]))

        raw_cache = getattr(self, "search_cache", None)

        context = AgentContext(
            system_prompt=combined,
            rag_context=rag_context,
            project_root=self.project_root,
            vector_store=self.vector_store,
            mode=mode,
            search_cache=cast(Any, raw_cache),
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()
        self._agent_thread = AgentChatThread(
            client=client,
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
        self._agent_thread.tools_status_changed.connect(
            self._on_tools_status_changed
        )
        self._agent_thread.start()

    def _build_registry(self) -> ToolRegistry:
        """Строит реестр инструментов для агента и чата."""
        from tools.codebase_search import search_codebase
        from tools.current_datetime import get_current_datetime
        from tools.exchange_rate import (
            get_exchange_rate,
            get_exchange_rates_multi,
        )
        from tools.file_ops import list_directory, read_file, write_file
        from tools.git_ops_tool import (
            tool_create_snapshot,
            tool_git_status,
            tool_undo_snapshot,
        )
        from tools.weather import get_weather
        from tools.web_search import web_search

        registry = ToolRegistry()
        registry.register(read_file)
        registry.register(list_directory)
        registry.register(write_file)
        registry.register(search_codebase)
        registry.register(web_search)
        registry.register(get_weather)
        registry.register(get_current_datetime)
        registry.register(get_exchange_rate)
        registry.register(get_exchange_rates_multi)
        registry.register(tool_git_status)
        registry.register(tool_create_snapshot)
        registry.register(tool_undo_snapshot)

        for tool_name, policy in self.tool_policies.items():
            registry.set_policy(tool_name, policy)

        return registry

    @Slot(str, str, str)
    def _on_tool_call_detected(
        self, name: str, description: str, arguments_json: str
    ):
        from ui.dialogs.tool_confirmation_dialog import ToolConfirmationDialog

        arguments = json.loads(arguments_json)
        tool_labels = {
            "read_file": "Чтение файла",
            "write_file": "Запись файла",
            "list_directory": "Просмотр директории",
            "search_codebase": "Поиск в кодовой базе",
            "web_search": "Поиск в интернете",
            "get_weather": "Погода",
            "get_current_datetime": "Текущие дата и время",
            "get_exchange_rate": "Курс валюты",
            "get_exchange_rates_multi": "Курсы нескольких валют",
            "get_git_status": "Статус Git",
            "create_snapshot": "Создание снапшота Git",
            "undo_last_snapshot": "Откат снапшота Git",
        }
        display_name = tool_labels.get(name, name)
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
    def _on_tool_executed(self, name: str, content: str, is_error: bool):
        self._add_tool_message(name, content, is_error)

    @Slot(str, str)
    def _on_tool_started(self, name: str, arguments_json: str):
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
            "get_weather": "🌤 Погода",
            "get_current_datetime": "🕒 Дата/время",
            "get_exchange_rate": "💱 Курс валюты",
            "get_exchange_rates_multi": "💱 Курсы валют",
            "get_git_status": "🔎 Проверка Git-статуса",
            "create_snapshot": "📸 Создание снапшота Git",
            "undo_last_snapshot": "⏪ Откат снапшота Git",
        }
        label = tool_labels.get(name, f"🛠️ {name}")
        self.status_label.setText(f"{label}: {arg_summary}")

    @Slot()
    def _on_finish(self):
        self._send_finished()

    @Slot(str)
    def _on_chat_error(self, error_msg: str):
        self._add_message("system", f"❌ Ошибка: {error_msg}")
        self._send_finished()

    @Slot(str)
    def _on_agent_finish(self, result: str):
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
        self._generating = False
        self.input_edit.setEnabled(True)
        self.send_btn.setText("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2; color: white;"
            " border: none; border-radius: 4px;"
        )
        self.status_label.setText("Готов к работе")

    def _set_button_stop(self) -> None:
        self.send_btn.setText("■")
        self.send_btn.setStyleSheet(
            "font-size: 18px; padding: 8px 20px; background: #d32f2f; color: white;"
            " border: none; border-radius: 4px;"
        )

    def _send_finished(self):
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
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка")

    @Slot(bool)
    def _on_tools_status_changed(self, active: bool):
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
        idx = self.model_combo.findData(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.addItem(model, model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    def set_tool_policy(self, tool_name: str, policy: Any) -> None:
        self.tool_policies[tool_name] = policy

    def set_vector_store(self, vector_store):
        self.vector_store = vector_store

    def set_project_root(self, project_root: str):
        self.project_root = project_root

    def get_settings(self) -> dict:
        mode_map = {0: "chat", 1: "plan", 2: "agent"}
        return {
            "model": self.get_current_model(),
            "mode": mode_map.get(self.mode_combo.currentIndex(), "chat"),
        }

    def apply_settings(self, settings: dict) -> None:
        model = settings.get("model", "")
        if model:
            self.set_model(model)
        mode = settings.get("mode", "chat")
        mode_map = {"chat": 0, "plan": 1, "agent": 2}
        idx = mode_map.get(mode, 0)
        if 0 <= idx < self.mode_combo.count():
            self.mode_combo.setCurrentIndex(idx)

    def set_project_open(self, is_open: bool):
        if not is_open:
            self.input_edit.setPlaceholderText(
                "Введите сообщение... (@web для поиска в интернете)"
            )
            self.status_label.setText("Проект не открыт (RAG недоступен)")
        else:
            self.input_edit.setPlaceholderText(
                "Введите сообщение... (CTRL+Enter — отправить, @web — поиск)"
            )
            self.status_label.setText("Готов к работе")
        self.send_btn.setVisible(True)

    def set_registry(self, registry):
        self.registry = registry
        self._update_model_list()

    def update_models(self, models: list[str]) -> None:
        self._update_model_list()

    def _update_model_list(self):
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
                if (
                    model_data
                    and (info := self.registry.get(model_data)) is not None
                    and info.is_local
                ):
                    self.model_combo.setCurrentIndex(i)
                    break
            else:
                if self.model_combo.count() > 0:
                    self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._update_model_status(self.model_combo.currentData())

    def _update_model_status(self, model_name: str):
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
        model_name = self.get_current_model()
        if model_name:
            self._update_model_status(model_name)
            self._check_mode_compatibility()
            self._save_current_settings()

    def _save_current_settings(self):
        try:
            mode_map = {0: "chat", 1: "plan", 2: "agent"}
            self.settings_changed.emit(
                {
                    "model": self.get_current_model(),
                    "mode": mode_map.get(self.mode_combo.currentIndex(), "chat"),
                }
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить настройки: {e}")

    def set_checking_state(self, is_checking: bool):
        self.refresh_models_btn.setText("⏳" if is_checking else "🔄")

    def _check_mode_compatibility(self):
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

    def set_role_manager(self, manager: RoleManager):
        self.role_manager = manager
        self.role_combo.blockSignals(True)
        self.role_combo.clear()

        for role in manager.get_all_roles():
            self.role_combo.addItem(f"{role.icon} {role.name}", role.id)

        idx = self.role_combo.findData("default")
        if idx >= 0:
            self.role_combo.setCurrentIndex(idx)
        self.role_combo.blockSignals(False)

    def _on_role_changed(self, index: int):
        if self.role_manager:
            role_id = self.role_combo.itemData(index)
            self.current_role_id = role_id
            logger.info(f"Смена роли на: {role_id}")

    def get_current_role_prompt(self) -> str:
        if self.role_manager:
            return self.role_manager.get_role(self.current_role_id).system_prompt
        return ""

    def update_model_status(self, model_name: str):
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

    def get_current_model(self) -> str:
        model = self.model_combo.currentData()
        if model is not None:
            return str(model)
        text = self.model_combo.currentText()
        for prefix in ("✅ ", "⬇️ "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return text or "llama3.2"

    @Slot()
    def _on_web_finish(self):
        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None
        self.status_label.setText("🌐 Поиск завершён")

    @Slot(str)
    def _on_web_error(self, error_msg: str):
        self._add_message("system", f"❌ {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка поиска")

    def set_session_id(self, session_id: str):
        self._session_id = session_id

    def load_history(self, messages: list[ChatMessage]):
        self._clear_chat(keep_session=True)
        for msg in messages:
            self._add_message(msg.role, msg.content)
            self._messages.append(msg)

    def keyPressEvent(self, event):
        if self.input_edit.hasFocus():
            key = event.key()
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                pass
        super().keyPressEvent(event)

    def set_search_cache(self, cache):
        self.search_cache = cache