"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import asyncio
import contextlib
import json
import os
import re

os.environ.setdefault("LLAMA_LOG_LEVEL", "WARN")
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agents.agent_loop import AgentContext
from agents.context_loader import load_agents_context
from agents.hypotheses import HypothesisStore
from agents.investigation import is_research_question
from agents.model_pool import Role
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import (
    ChatMessage,
    MessagePart,
    OllamaClient,
    ReasoningPart,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)
from agents.rule_based_fallback import RuleBasedFallback
from agents.tool_registry import ToolRegistry
from core.chat_commands import (
    COMMAND_TAGS,
    DEFAULT_PLACEHOLDER,
    detect_active_command,
    find_first_word,
    get_command,
)
from core.config import AgentMode, ToolPolicy, load_config
from core.context_budget import attached_char_budget, rag_char_budget, truncate_to_chars
from core.model_registry import ModelInfo, ModelRegistry
from core.profiles import ProfileConfig, get_default_profile, get_profile_by_id
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager
from fs.project_map import detect_query_scope, list_project_files, render_project_map
from tools.attached_files import AttachedFileStore
from tools.weather import get_weather
from ui.chat.backends import BackendsMixin
from ui.chat.message_item import (
    ChatActivityItem,
    ChatDiagramItem,
    ChatHypothesisItem,
    ChatMessageItem,
    ChatSourcesItem,
)
from ui.chat.research import ResearchMixin
from ui.chat.research_passport import ChatResearchPassport
from ui.chat.thought_stream import ChatThoughtStream
from ui.chat.threads import (
    AgentChatThread,
    RagSearchThread,
    WebSearchThread,
    _ModelPoolLoadThread,
    _OllamaCheckThread,
    _SLMDownloadThread,
    _SLMLoadThread,
)
from ui.widgets.auto_scroll import AutoScrollArea
from ui.widgets.collapsed_placeholder import CollapsedPlaceholder
from ui.widgets.command_completer import CommandCompleter
from ui.widgets.pipeline_selectors import PipelineSelectors
from ui.widgets.profiles_selector import ProfilesSelector

if TYPE_CHECKING:
    from agents.local_llm_client import LocalLLMClient
    from agents.model_pool import ModelPool
    from agents.orchestrator import Orchestrator
    from ui.chat.message_item import ToolCallCard

# Инструменты, которые в Chat-режиме должны выполняться без подтверждения.
# Основной список живёт в AppConfig.chat_auto_tools; эта константа — fallback,
# если конфиг не читается (повреждён, отсутствует). См. _build_registry().
_CHAT_AUTO_TOOLS = (
    "web_search",
    "read_file",
    "search_codebase",
    "list_directory",
    "get_weather",
    "get_current_datetime",
    "get_exchange_rate",
    "get_exchange_rates_multi",
    "get_git_status",
    "read_attached_file",
    "record_hypothesis",
    "update_hypothesis",
    "list_hypotheses",
)

# Команды-префиксы для прикрепления путей (в стиле @web)
_PATH_COMMANDS = ("@dir", "@file", "@path")

# Сколько символов прикрепленного файла показывать в системном промпте,
# когда остаётся мало контекста (например, файлы уже не помещаются по объёму).
_MIN_ATTACHED_BUDGET = 600

# Сколько чанков запрашивать у векторного поиска перед дедупликацией по файлам.
_RAG_N_RESULTS = 8

# [9.10] Виртуализация: при каком числе «месседж-виджетов» в layout
# старые начинают сворачиваться в placeholder. Значение подобрано так,
# чтобы QScrollArea оставался отзывчивым при прокрутке (обычно —
# порог, после которого начинаются просадки FPS).
_MAX_VISIBLE_MESSAGE_WIDGETS = 80

# [9.10] Сколько «самых свежих» свёрнутых разворачивать за один клик.
_RESTORE_BATCH = 40

# [9.10] Сколько старых свернуть за раз, когда порог превышен.
# Сворачиваем с запасом: следующий раз — только когда снова наберётся
# _MAX_VISIBLE_MESSAGE_WIDGETS + _COLLAPSE_BUFFER.
_COLLAPSE_BUFFER = 20

# Явное описание режимов для тултипов селектора: что включено и почему.
_MODE_DESCRIPTIONS: dict[int, str] = {
    0: (
        "💬 Chat — обычный диалог с моделью.\n"
        "Инструменты только для чтения: чтение файлов, поиск по коду, "
        "погода, время, курсы.\n"
        "Ничего не меняет на диске. Подходит для вопросов и объяснений."
    ),
    1: (
        "📋 Plan — исследование и планирование.\n"
        "Инструменты только для чтения: обзор каталогов, чтение файлов, "
        "поиск по коду.\n"
        "Запись и Git недоступны — режим для составления плана без изменений."
    ),
    2: (
        "🤖 Agent — полный доступ.\n"
        "Все инструменты: чтение, запись файлов, Git-снапшоты, поиск, web.\n"
        "Может менять проект (операции записи подтверждаются)."
    ),
}


def read_project_rules(project_root: str | None) -> str:
    """Читает правила проекта из `rules_file` (по умолчанию `.ollidesk/rules.md`)."""
    if not project_root:
        return ""
    try:
        rules_file = load_config().rules_file or ".ollidesk/rules.md"
        path = Path(project_root) / rules_file
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"Не удалось прочитать правила проекта: {e}")
    return ""


class ChatPanel(QWidget, BackendsMixin, ResearchMixin):
    """Панель чата с сообщениями, вводом и управлением.

    [2.4] Логика управления SLM/Ollama вынесена в `BackendsMixin`
    (`ui/chat/backends.py`), research-режим и двухмодельный пайплайн —
    в `ResearchMixin` (`ui/chat/research.py`). Все поля для миксинов
    по-прежнему инициализируются здесь в `__init__`; миксины обращаются
    к ним через `self`.

    MRO: `ChatPanel → QWidget → BackendsMixin → ResearchMixin`.
    Qt-класс должен идти первым — PySide6 этого требует.
    """

    mode_changed = Signal(str)
    agent_panel_toggle = Signal(bool)
    refresh_models_requested = Signal()
    settings_changed = Signal(dict)
    backend_status_changed = Signal(str)
    pipeline_status_changed = Signal(str)

    directory_attach_requested = Signal(str)
    source_activated = Signal(str, int)

    # [9.2] Дерево проекта (кнопка «📁» в верхней панели → MainWindow).
    project_tree_toggled = Signal(bool)
    # [9.3] Открыть SLMModelsDialog из кнопки «⋯» в PipelineSelectors.
    slm_models_requested = Signal()
    # [9.4] Смена профиля использования (MainWindow синхронизирует чекбокс).
    profile_changed = Signal(str)  # profile_id
    # [9.8] Показать diff в Monaco DiffEditor (из карточки write_file).
    # payload: {"path", "original", "modified", "language"}.
    diff_requested = Signal(object)

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

        # 0.1.3b: аккумулятор частей текущего ответа ассистента.
        # Нужен, чтобы _on_agent_finish/_send_finished сохраняли полный
        # ChatMessage (reasoning + text + tool_calls + tool_results),
        # а не только TextPart. Без этого reasoning и tool-calls терялись
        # при перезагрузке сессии.
        self._current_assistant_parts: list[MessagePart] = []
        self._current_assistant_text: str = ""
        self._current_assistant_reasoning: str = ""

        self._agent_thread: AgentChatThread | None = None
        self._rag_thread: RagSearchThread | None = None
        self._rag_context: str | None = None
        self._rag_scope: tuple[str, list[str]] | None = None
        self._project_files: list[str] = []
        self._project_map: str = ""
        self._project_scan_root: str | None = None
        self._project_scanned: bool = False
        self._attached_files = AttachedFileStore()

        self._hypotheses = HypothesisStore()
        self._hypothesis_widgets: dict[str, ChatHypothesisItem] = {}

        # --- Новые поля для серого потока и паспорта ---
        self._research_mode_active: bool = False
        self._research_cancelled: bool = False
        self._pending_final_answer: str = ""
        self._last_research_summary: dict | None = None
        self._current_thought_stream: ChatThoughtStream | None = None

        self._generating = False
        self._last_run_params: dict | None = None  # для кнопки «Продолжить»
        # Показывать reasoning_content модели в чате. Управляется чекбоксом
        # в правой панели; сохраняется в ProjectSettings.show_reasoning.
        self._show_reasoning: bool = True
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
        self._apply_backend_config()

        self._slm_client: LocalLLMClient | None = None
        self._slm_loading = False
        self._slm_model_name = ""
        self._slm_load_generation = 0
        self._slm_load_thread: _SLMLoadThread | None = None
        # === двухмодельный пайплайн (Orchestrator) ===
        # Загружается в фоне при первом открытии проекта. Пока не готов —
        # ChatPanel работает через старый AgentLoop (без Orchestrator).
        self._model_pool: ModelPool | None = None
        self._orchestrator: Orchestrator | None = None
        self._pool_load_thread: _ModelPoolLoadThread | None = None
        self._pool_load_failed: bool = False
        self._slm_download_thread: _SLMDownloadThread | None = None
        self._ollama_check_thread: _OllamaCheckThread | None = None

        # [9.3] Селекторы Planner/Analyst в верхней панели.
        self._pipeline_selectors: PipelineSelectors | None = None
        # [9.4] Селектор профиля использования (Разработка / Исследование / Обучение).
        self._profiles_selector: ProfilesSelector | None = None
        # Активный профиль + его runtime-флаги.
        self._profile: ProfileConfig | None = None
        self._profile_rag_enabled: bool = True
        self._profile_agent_max_iters: int = 30
        self._profile_chat_max_iters: int = 7
        # [9.7] Приборная панель в статус-баре (инжектится из MainWindow).
        self._dashboard = None

        # [9.5.7] Активная tool-карточка: последняя созданная
        # в `_on_tool_started`; `_on_tool_executed` вызывает у неё
        # `set_result(...)` и сбрасывает. В multi-tool-call все вызовы
        # последовательны (start→result), поэтому одной ссылки достаточно.
        self._active_tool_card = None
        # [9.5.4] Индикатор активности: idle | thinking | tool | streaming | error | cancelled.
        self._activity_state: str = "idle"

        # [9.6.2] QCompleter для `@`-команд.
        self._command_completer: CommandCompleter | None = None
        # Есть ли сейчас активное автодополнение (для перехвата Tab/Enter).
        self._command_popup_active: bool = False

        # [9.10] Свёрнутые виджеты (placeholder'ы) в layout.
        # Заполняется в `_maybe_collapse_old_messages`, очищается в `_clear_chat`.
        # Раньше объявлялось только в MainWindow — из-за этого первый же
        # `load_history` падал с AttributeError (ChatPanel — владелец layout,
        # именно он должен хранить placeholder'ы).
        self._collapsed_placeholders: list[CollapsedPlaceholder] = []

        # [9.10] Флаг: подавляет `_maybe_collapse_old_messages` на время
        # массовой загрузки (`load_history`), чтобы не делать O(n²).
        self._suppress_collapse: bool = False

        self._setup_ui()
        # [9.4] Применяем профиль по умолчанию на старте (после _setup_ui,
        # чтобы _profiles_selector уже существовал).
        self._apply_initial_profile()
        self._update_model_selector_visibility()
        self._update_pipeline_status()
        QTimer.singleShot(100, self._detect_backends)

    # ---------------------------------------------------------
    # Детект и загрузка бэкендов
    # ---------------------------------------------------------

    def _iteration_limit(self, mode: AgentMode, is_research: bool = False) -> int:
        """Лимит итераций для текущего режима и типа задачи.

        Логика:
        * Agent-режим — всегда `agent_max_iterations` (полный доступ,
            задача может быть длинной: рефакторинг, миграция и т. п.).
        * Research-вопрос в Chat/Plan — повышенный потолок (не меньше 15
            и не меньше половины от agent-лимита). Фазовая модель
            (RECON → DEEP_DIVE → SELF_CHECK → SYNTHESIS) + работа с гипотезами
            естественным образом требуют 12–20 шагов; 7 физически мало.
        * Обычный вопрос в Chat/Plan — `chat_max_iterations` (по умолчанию 7).

        Фазовая модель внутри `AgentLoop` — главный регулятор остановки;
        лимит — только аварийный стоп-кран, чтобы модель не ушла в
        бесконечность.
        """
        default_chat = 7
        default_agent = 30
        try:
            from core.config import load_config

            cfg = load_config()
        except Exception:
            cfg = None

        if mode == AgentMode.AGENT:
            return cfg.agent_max_iterations if cfg else default_agent

        if cfg is None:
            # Без конфига — эвристика: research требует больше итераций.
            return 15 if is_research else default_chat

        if is_research:
            # Не меньше 15, и не меньше половины agent-лимита. Верхняя
            # граница — сам agent-лимит, чтобы не превысить общий потолок.
            return max(
                15,
                min(cfg.agent_max_iterations, cfg.agent_max_iterations // 2 + 8),
            )

        return cfg.chat_max_iterations

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------

    def _add_message(self, role: str, content: str, mode_name: str = "") -> ChatMessageItem:
        widget = ChatMessageItem(role, content, mode_name=mode_name)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        self._update_bubble_width(widget)
        QTimer.singleShot(0, self._scroll_to_bottom)
        # [9.10] Свернуть старые сообщения, если порог превышен.
        # При массовой загрузке (`load_history`) сворачивание вызывается
        # один раз в конце — флаг `_suppress_collapse` отключает
        # промежуточные проходы O(n²).
        if not getattr(self, "_suppress_collapse", False):
            self._maybe_collapse_old_messages()
        return widget

    def _render_assistant_parts(self, msg: ChatMessage) -> ChatMessageItem:
        """Восстанавливает композитный пузырь из ChatMessage.parts (0.1.3c).

        Порядок частей — как их складывал `_on_chunk`/`_on_thinking`/
        `_on_tool_started`/`_on_tool_executed`: reasoning → text → tool_call
        → tool_result → tool_call → tool_result → …

        `ToolResultPart` привязывается к последней «pending»-карточке с тем
        же `name`. Порядок частей гарантирует, что к моменту прихода
        результата карточка уже создана (start всегда идёт до executed).

        Args:
            msg: Сообщение ассистента с непустым `parts`.

        Returns:
            Собранный `ChatMessageItem`. Пустые части (`text=""`) пропускаются.
        """
        widget = ChatMessageItem(
            msg.role,
            content="",
            mode_name=self._current_mode_name(),
        )
        # Очередь pending-карточек по имени инструмента. Список, потому что
        # модель может вызвать один и тот же инструмент дважды подряд.
        pending_cards: dict[str, list[ToolCallCard]] = {}
        for part in msg.parts:
            if isinstance(part, ReasoningPart) and part.text:
                widget.append_reasoning(part.text)
            elif isinstance(part, TextPart) and part.text:
                widget.append_text(part.text)
            elif isinstance(part, ToolCallPart):
                for tc in part.calls:
                    args_json = json.dumps(tc.arguments, ensure_ascii=False)
                    card = widget.append_tool_call(tc.name, args_json)
                    if card is not None:
                        pending_cards.setdefault(tc.name, []).append(card)
            elif isinstance(part, ToolResultPart):
                queue = pending_cards.get(part.name) or []
                if queue:
                    card = queue.pop(0)
                    card.set_result(part.content, part.is_error)
        widget.finalize()
        return widget

    def _ensure_assistant_widget(self) -> ChatMessageItem:
        """Гарантирует наличие текущего пузыря ассистента.

        Композитный пузырь 4.8.B: reasoning, text и tool_call-карточки
        добавляются в ОДИН виджет по мере стриминга. Если текущего пузыря
        нет (например, модель сразу вызвала tool без reasoning/текста) —
        создаём его.
        """
        if self._last_assistant_widget is None:
            widget = ChatMessageItem("assistant", "", mode_name=self._current_mode_name())
            self._last_assistant_widget = widget
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
            self._update_bubble_width(widget)
            # [9.10] Свернуть старые, если порог превышен.
            self._maybe_collapse_old_messages()
        return self._last_assistant_widget

    def _show_activity(self, text: str) -> None:
        if not text:
            return
        item = ChatActivityItem(text)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)
        QTimer.singleShot(0, self._scroll_to_bottom)
        # [9.10]
        self._maybe_collapse_old_messages()

    def _show_sources(self, sources: list[tuple[str, int, int]]) -> None:
        if not sources:
            return
        item = ChatSourcesItem(sources)
        item.source_activated.connect(self._on_source_activated)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)
        QTimer.singleShot(0, self._scroll_to_bottom)
        # [9.10]
        self._maybe_collapse_old_messages()

    def _on_source_activated(self, file_path: str, line: int) -> None:
        if self.project_root and not Path(file_path).is_absolute():
            file_path = str(Path(self.project_root) / file_path)
        self.source_activated.emit(file_path, line)

    # ---------------------------------------------------------
    # @dir / @file / @path — прикрепление путей
    # ---------------------------------------------------------

    @staticmethod
    def _extract_path_command(text: str) -> tuple[str, str, str] | None:
        for cmd in _PATH_COMMANDS:
            if not text.startswith(cmd):
                continue

            kind = cmd[1:]
            tail = text[len(cmd) :].lstrip()
            if not tail:
                return (kind, "", "")

            if tail.startswith('"'):
                end = tail.find('"', 1)
                if end > 0:
                    return (kind, tail[1:end], tail[end + 1 :].strip())
                return (kind, tail.lstrip('"'), "")

            parts = tail.split(None, 1)
            return (kind, parts[0], parts[1] if len(parts) > 1 else "")

        return None

    def _attach_file(self, path: Path) -> None:
        """[9.4.8] Прикрепляет файл: текст — сразу, документ — с предпросмотром."""
        from fs.document_extractors import extract_document, is_document

        # Документы (PDF/DOCX/PPTX/XLSX/RTF) — сначала извлечение,
        # затем предпросмотр, только по «Прикрепить» — в store.
        if is_document(path):
            self._attach_document(path, extract_document)
            return

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error(f"@file: ошибка чтения {path}: {e}")
            self._add_message("system", f"❌ Не удалось прочитать файл: {e}")
            return

        attached = self._attached_files.add(path, content)
        logger.info(f"@file: прикреплён {path} ({attached.size_chars} символов)")
        self._add_message(
            "system",
            f"📎 Прикреплён файл: {attached.name} "
            f"({attached.lines} строк, {attached.size_chars} символов). "
            "В контекст попадёт только начало и конец, остальное — "
            "через инструмент read_attached_file.",
        )

    def _attach_document(self, path: Path, extractor) -> None:
        """[9.4.8] Извлекает документ, показывает предпросмотр, прикрепляет."""
        from ui.dialogs.document_preview_dialog import DocumentPreviewDialog

        # OCR из конфига — если пользователь включил в AppConfig.
        try:
            from core.config import load_config

            use_ocr = bool(load_config().document_ocr)
        except Exception:
            use_ocr = False

        self.status_label.setText(f"⏳ Извлечение текста из {path.name}…")
        try:
            result = extractor(path, use_ocr=use_ocr)
        except Exception as e:
            logger.exception(f"Документ {path.name}: ошибка извлечения")
            self._add_message("system", f"❌ Не удалось извлечь текст: {e}")
            self.status_label.setText("Готов к работе")
            return

        self.status_label.setText("Готов к работе")

        dialog = DocumentPreviewDialog(
            path,
            result,
            with_ocr_available=not use_ocr,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            logger.info(f"@file: прикрепление {path.name} отменено пользователем")
            return

        text = dialog.approved_text()
        if not text:
            return

        attached = self._attached_files.add(path, text)
        ocr_mark = " (OCR)" if result.ocr_used else ""
        logger.info(
            f"@file: прикреплён документ {path.name} "
            f"({attached.size_chars} симв., {result.pages} стр., OCR={result.ocr_used})"
        )
        self._add_message(
            "system",
            f"📎 Прикреплён документ: {attached.name}{ocr_mark} "
            f"({result.pages} стр., {attached.size_chars} символов). "
            "В контекст попадёт только начало и конец, остальное — "
            "через инструмент read_attached_file.",
        )

    def _handle_path_command(self, kind: str, path_str: str, rest: str) -> None:
        if not path_str:
            self._add_message("system", f"⚠️ Укажите путь после @{kind}")
            return

        path = Path(path_str).expanduser().resolve()
        if not path.exists():
            self._add_message("system", f"❌ Путь не найден: {path}")
            return

        if kind == "path":
            kind = "dir" if path.is_dir() else "file"

        if kind == "dir":
            if not path.is_dir():
                self._add_message("system", f"❌ Не директория: {path} (используйте @file)")
                return
            self._add_message(
                "system",
                f"📁 Прикреплена директория: {path} — идёт индексация, "
                "инструменты поиска по коду работают по ней.",
            )
            self.directory_attach_requested.emit(str(path))
        elif not path.is_file():
            self._add_message("system", f"❌ Не файл: {path} (используйте @dir)")
            return
        else:
            self._attach_file(path)

        if rest:
            self.input_edit.setPlainText(rest)
            self._send_message()

    def _ensure_project_scan(self) -> None:
        root = self.project_root or ""
        if self._project_scanned and self._project_scan_root == root:
            return

        self._project_scanned = True
        self._project_scan_root = root
        self._project_files = []
        self._project_map = ""

        if not root:
            return

        project_path = Path(root)
        if not project_path.is_dir():
            return

        try:
            config = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для карты проекта: {e}")
            return

        self._project_files = list_project_files(project_path, config)
        self._project_map = render_project_map(self._project_files)

    def _project_map_text(self) -> str:
        self._ensure_project_scan()
        return self._project_map

    def _project_name(self) -> str:
        return Path(self.project_root).name if self.project_root else ""

    def _template_context(self) -> dict:
        return {
            "project_name": self._project_name(),
            "language": "русский",
            "project_root": self.project_root or "",
            "project_map": self._project_map_text(),
        }

    def _project_rules(self) -> str:
        """Правила проекта + системный промпт профиля (9.4)."""
        base = read_project_rules(self.project_root)
        profile_prompt = (self._profile.system_prompt or "").strip() if self._profile else ""
        if profile_prompt:
            return "\n\n".join(filter(None, [base, f"[Профиль: {self._profile.name}]\n{profile_prompt}"]))
        return base

    def _query_scope(self, text: str) -> tuple[str, list[str]] | None:
        self._ensure_project_scan()
        if not self._project_files:
            return None
        return detect_query_scope(text, self._project_files)

    def _scope_listing(self) -> str:
        if not self._rag_scope:
            return ""
        rel, files = self._rag_scope
        limit = 100
        shown = files[:limit]
        lines = [f"Файлы в каталоге `{rel}/` (всего {len(files)}):"]
        lines.extend(f"- {path}" for path in shown)
        if len(files) > limit:
            lines.append(f"… и ещё {len(files) - limit} файлов")
        return "\n".join(lines)

    def _merge_scope_into_context(self, context: str) -> str:
        listing = self._scope_listing()
        return "\n\n".join(filter(None, [listing, context]))

    def _attached_context(self) -> str:
        if not self._attached_files:
            return ""

        budget = attached_char_budget(self._router.num_ctx)
        if self._rag_context:
            budget = max(_MIN_ATTACHED_BUDGET, budget // 2)
        return self._attached_files.render(budget)

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
            bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

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
    @Slot(str)
    def _on_thinking(self, token: str):
        if not token:
            return
        # 0.1.3b: reasoning аккумулируется в буфер НЕЗАВИСИМО от того,
        # показываем ли его в UI (_show_reasoning). Иначе при выключенном
        # тумблере история сессии теряла бы reasoning_content.
        self._current_assistant_reasoning += token
        if not self._show_reasoning:
            return
        self._current_thinking_content += token
        widget = self._ensure_assistant_widget()
        widget.append_reasoning(token)
        self.scroll_area.scroll_to_bottom_if_sticky()

    @Slot(str)
    def _on_chunk(self, content: str):
        """[9.5] Стриминг TextPart-токенов (SLM теперь эмитит по одному).

        AutoScrollArea сам решает, скроллить или нет: если пользователь
        проскроллил вверх — не дёргаем, показываем «▼ Вниз».
        """
        if not content:
            return
        if self._activity_state != "streaming":
            self._set_activity_state("streaming")
        self._current_assistant_content += content
        # 0.1.3b: копим текст отдельно — _build_assistant_message
        # соберёт из него TextPart один раз при завершении.
        self._current_assistant_text += content
        widget = self._ensure_assistant_widget()
        widget.append_text(content)
        # QTimer.singleShot(0, ...) НЕ нужен: AutoScrollArea.scroll_to_bottom_if_sticky
        # уже использует QTimer(0) внутри, чтобы не дёргать layout в середине append.
        self.scroll_area.scroll_to_bottom_if_sticky()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()

        # [9.2] Кнопка «📁» — переключатель дерева проекта.
        self.project_tree_btn = QPushButton("📁")
        self.project_tree_btn.setToolTip("Показать/скрыть дерево проекта (F4)")
        self.project_tree_btn.setCheckable(True)
        self.project_tree_btn.setChecked(False)
        self.project_tree_btn.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 2px 8px; border: none;"
            " background: transparent; }"
            " QPushButton:hover { background: rgba(128,128,128,0.2); border-radius: 4px; }"
            " QPushButton:checked { background: rgba(212,163,115,0.3); border-radius: 4px; }"
        )
        self.project_tree_btn.toggled.connect(self.project_tree_toggled.emit)
        top_bar.addWidget(self.project_tree_btn)

        # [9.4] Селектор профиля использования.
        try:
            from core.config import load_config as _load_cfg_for_profiles

            _cfg_for_profiles = _load_cfg_for_profiles()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для ProfilesSelector: {e}")
            _cfg_for_profiles = None
        if _cfg_for_profiles is not None:
            self._profiles_selector = ProfilesSelector(_cfg_for_profiles)
            self._profiles_selector.profile_changed.connect(self._on_profile_changed)
            top_bar.addWidget(self._profiles_selector)

        # [9.4] Кнопка «Возврат в Разработку» — видна только когда активен
        # не-development профиль. Возврат одним кликом.
        self.back_to_development_btn = QPushButton("➜ 🛠")
        self.back_to_development_btn.setToolTip("Вернуться в профиль «Разработка»")
        self.back_to_development_btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 2px 8px;"
            " background: rgba(212,163,115,0.2); color: #d4a373;"
            " border: 1px solid rgba(212,163,115,0.4); border-radius: 4px; }"
            " QPushButton:hover { background: rgba(212,163,115,0.35); }"
        )
        self.back_to_development_btn.setVisible(False)  # показывается в _apply_profile
        self.back_to_development_btn.clicked.connect(self._back_to_development)
        top_bar.addWidget(self.back_to_development_btn)

        role_label = QLabel("Роль:")
        role_label.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(role_label)

        self.role_combo = QComboBox()
        self.role_combo.setMinimumWidth(180)
        self.role_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        # Tooltip на закрытом combo — что такое роль и когда её менять.
        self.role_combo.setToolTip(
            "Роль — это системный промпт, задающий стиль и специализацию ответов "
            "(хранится в ~/.ollidesk/roles/*.md).\n\n"
            "• «🤖 Ассистент OlliDesk» — универсальная роль по умолчанию. "
            "Подходит для большинства задач: код, объяснения, рефакторинг.\n"
            "• Специализированные роли (Python, C#, DevOps, Frontend, Security, "
            "Tech Writer, PM и др.) — узкий фокус: более точные и глубокие ответы "
            "в своей области, но хуже справляются с чужими темами.\n\n"
            "Менять роль СТОИТ, когда задача явно из области специализации "
            "(например, код на C# или аудит безопасности).\n"
            "Оставлять по умолчанию — когда задача универсальная или непонятно, "
            "какая роль лучше.\n\n"
            "Смена роли действует на СЛЕДУЮЩИЙ запрос — текущая история "
            "не перегенерируется."
        )
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)
        top_bar.addWidget(self.role_combo)

        self.model_label = QLabel("Модель:")
        self.model_label.setStyleSheet("font-size: 13px; padding: 4px;")
        top_bar.addWidget(self.model_label)

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
        self.model_status_label.setStyleSheet("font-size: 12px; padding: 4px 8px; color: gray;")
        top_bar.addWidget(self.model_status_label)

        # [9.3] Селекторы Planner/Analyst (заменяют pipeline_status_label).
        # ComboBox'ы заполняются из текущего конфига; кнопка «⋯» открывает
        # SLMModelsDialog; выбор сохраняется в config.yaml.
        try:
            from core.config import load_config as _load_cfg_for_pipeline

            _cfg_for_pipeline = _load_cfg_for_pipeline()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для PipelineSelectors: {e}")
            _cfg_for_pipeline = None
        if _cfg_for_pipeline is not None:
            self._pipeline_selectors = PipelineSelectors(_cfg_for_pipeline)
            self._pipeline_selectors.planner_changed.connect(self._on_planner_changed)
            self._pipeline_selectors.analyst_changed.connect(self._on_analyst_changed)
            self._pipeline_selectors.open_slm_dialog_requested.connect(self.slm_models_requested.emit)
            top_bar.addWidget(self._pipeline_selectors, stretch=1)

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_bar.addWidget(self.model_combo)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        for index, description in _MODE_DESCRIPTIONS.items():
            self.mode_combo.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setStyleSheet("font-size: 13px; padding: 4px;")
        # ВАЖНО: connect ДО setCurrentIndex, чтобы `_on_mode_changed(2)`
        # сам выставил tooltip для текущего элемента (иначе между
        # setCurrentIndex(2) и connect tooltip остаётся от _MODE_DESCRIPTIONS[0]).
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setCurrentIndex(2)
        top_bar.addWidget(self.mode_combo)

        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setToolTip("Настройки агента")
        self.gear_btn.setStyleSheet(
            "QPushButton { font-size: 16px; padding: 2px 8px; border: none;"
            " background: transparent; }"
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
            "font-size: 12px; padding: 4px 8px; background: #e8f5e9;"
            " color: #2e7d32; border: 1px solid #a5d6a7;"
            " border-radius: 4px; margin: 2px 0;"
        )
        self.tool_status_label.setVisible(False)
        layout.addWidget(self.tool_status_label)

        # [9.5] AutoScrollArea — «прилипание к нижней кромке» + кнопка «▼ Вниз».
        self.scroll_area = AutoScrollArea()
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
            "font-size: 12px; color: gray; padding: 4px 8px; background: #f5f5f5; border-radius: 4px;"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(DEFAULT_PLACEHOLDER)
        self.input_edit.setFixedHeight(100)
        self.input_edit.setStyleSheet(
            "font-size: 14px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;"
        )
        self.input_edit.setAcceptRichText(False)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, stretch=1)

        # [9.6.2] Автодополнение `@`-команд.
        self._command_completer = CommandCompleter(self)
        self._command_completer.setWidget(self.input_edit)
        self._command_completer.command_activated.connect(self._on_command_completed)
        self.input_edit.textChanged.connect(self._on_input_text_changed)
        # При скролле/фокусе — обновляем позицию popup.
        self.input_edit.cursorPositionChanged.connect(self._update_command_placeholder)

        # Кнопка «Продолжить» показывается только при исчерпании лимита
        # итераций (см. _on_iteration_limit_exceeded / _continue_after_limit).
        self.continue_btn = QPushButton("▶ Продолжить")
        self.continue_btn.setToolTip(
            "Дать агенту больше шагов: увеличить лимит в 2 раза (макс. 200) "
            "и повторить запрос с текущей историей."
        )
        self.continue_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 16px; background: #f57c00;"
            " color: white; border: none; border-radius: 4px;"
        )
        self.continue_btn.setVisible(False)
        self.continue_btn.clicked.connect(self._continue_after_limit)
        input_layout.addWidget(self.continue_btn)

        self.send_btn = QPushButton("▶ Отправить")
        self.send_btn.setStyleSheet(
            "font-size: 14px; padding: 8px 20px; background: #1976d2;"
            " color: white; border: none; border-radius: 4px;"
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
                return
            self.input_edit.blockSignals(True)
            try:
                cursor = self.input_edit.textCursor()
                if cursor.isNull():
                    return
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.setCharFormat(QTextCharFormat())
                cursor.clearSelection()

                # [9.6.2] Паттерн из реестра команд — единый источник.
                tags_alt = "|".join(re.escape(t) for t in sorted(COMMAND_TAGS))
                pattern = rf"^({tags_alt})(?=\s|$)"
                for m in re.finditer(pattern, text, re.MULTILINE):
                    start, end = m.start(), m.end()
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setForeground(QColor("#42a5f5"))
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setBackground(QColor("#e3f2fd"))
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                    fmt.setUnderlineColor(QColor("#42a5f5"))
                    cursor.mergeCharFormat(fmt)
            finally:
                self.input_edit.blockSignals(False)
        except Exception as e:
            logger.warning(f"Ошибка в _highlight_web_command: {e}")

    # ------------------------------------------------------------------
    # [9.6.2] Автодополнение `@`-команд
    # ------------------------------------------------------------------

    @Slot()
    def _on_input_text_changed(self) -> None:
        """Обновляет popup и placeholder при вводе в поле.

        Popup показывается только если `@` в начале первой строки
        и курсор внутри первого слова (см. `detect_active_command`).
        """
        if self._command_completer is None:
            return
        text = self.input_edit.toPlainText()
        cursor_pos = self.input_edit.textCursor().position()
        prefix = detect_active_command(text, cursor_pos)

        if prefix is not None:
            self._command_completer.show_for_prefix(prefix)
            self._command_popup_active = self._command_completer.is_popup_visible()
        else:
            self._command_completer.hide_popup()
            self._command_popup_active = False

        self._update_command_placeholder()

    @Slot(str)
    def _on_command_completed(self, tag: str) -> None:
        """Пользователь выбрал команду — вставляем tag + пробел."""
        if not tag:
            return
        text = self.input_edit.toPlainText()
        first = find_first_word(text)
        rest = text[len(first) :]

        # Если пользователь набрал частичный префикс — заменяем на полный tag.
        new_text = f"{tag} {rest.lstrip()}" if rest.strip() else f"{tag} "
        self.input_edit.blockSignals(True)
        try:
            self.input_edit.setPlainText(new_text)
            cursor = self.input_edit.textCursor()
            cursor.movePosition(Qt.TextCursor.MoveOperation.End)
            self.input_edit.setTextCursor(cursor)
        finally:
            self.input_edit.blockSignals(False)

        self._command_popup_active = False
        if self._command_completer is not None:
            self._command_completer.hide_popup()
        self._update_command_placeholder()
        self.input_edit.setFocus()

    @Slot()
    def _update_command_placeholder(self) -> None:
        """[9.6.2] Показывает подсказку команды в placeholder.

        Если пользователь набрал `@tag ` (с пробелом) и курсор стоит
        после него — placeholder становится подсказкой этой команды.
        Иначе — стандартный placeholder.
        """
        text = self.input_edit.toPlainText()
        first = find_first_word(text)

        # Случай 1: `@tag` (без пробела) — общая подсказка по команде.
        if first in COMMAND_TAGS and not text[len(first) :].strip():
            cmd = get_command(first)
            if cmd is not None:
                self.input_edit.setPlaceholderText(cmd.placeholder)
                return
        # Случай 2: пользователь набрал `@tag args...` — не перетираем placeholder
        # (аргументы уже видны, подсказка не нужна).
        if first in COMMAND_TAGS:
            self.input_edit.setPlaceholderText("")
            return
        # Случай 3: пусто — стандартный.
        self.input_edit.setPlaceholderText(DEFAULT_PLACEHOLDER)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # [9.6.2] Tab / Enter при видимом popup — навигация и выбор в completer.
        if (
            obj is self.input_edit
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and self._command_popup_active
            and self._command_completer is not None
            and self._command_completer.is_popup_visible()
        ):
            popup = self._command_completer.popup()
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                # Навигация по popup.
                popup.keyPressEvent(event)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                # Выбор текущего элемента.
                index = popup.currentIndex()
                if index.isValid():
                    tag = index.data(Qt.ItemDataRole.DisplayRole)
                    self._on_command_completed(str(tag))
                    return True
                # Если ничего не выделено — Tab/Enter ведут себя как обычно.
            if key == Qt.Key.Key_Escape:
                self._command_completer.hide_popup()
                self._command_popup_active = False
                return True

        if (
            obj is self.input_edit
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Return
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
        self.mode_combo.setToolTip(_MODE_DESCRIPTIONS.get(index, ""))

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
        payload: dict = {"mode": mode_map.get(self.mode_combo.currentIndex(), "chat")}
        # Модель имеет смысл только когда активна Ollama. При SLM model_combo
        # содержит «последнюю выбранную Ollama-модель» (например, 7B) и
        # сохранять её в last_model при каждой смене режима бессмысленно —
        # в итоге config.last_model остаётся 7B, хотя реально грузится 3B.
        if self._router.active_backend == BackendType.OLLAMA:
            payload["model"] = self.get_current_model()
        self.settings_changed.emit(payload)

    def _toggle_agent_panel(self):
        self.agent_panel_toggle.emit(True)

    def _scroll_to_bottom(self):
        """[9.5] Скроллит к нижней кромке, только если пользователь там."""
        self.scroll_area.scroll_to_bottom_if_sticky()

    def _scroll_to_bottom_force(self):
        """[9.5] Принудительный скролл к нижней кромке (для Cmd+End)."""
        self.scroll_area.scroll_to_bottom_force()

    def _clear_chat(self, keep_session: bool = False):
        # [9.10] Сначала удаляем свёрнутые виджеты внутри placeholder'ов,
        # потом сами placeholder'ы (они тоже в layout — цикл ниже их
        # обработает, но скрытые виджеты остались бы «сиротами» в памяти).
        for placeholder in self._collapsed_placeholders:
            try:
                # Скроем и удалим содержимое placeholder'а.
                for w in getattr(placeholder, "_collapsed", []):
                    with contextlib.suppress(Exception):
                        w.deleteLater()
                placeholder.deleteLater()
            except Exception:
                logger.debug(
                    "Ошибка удаления placeholder'а при _clear_chat",
                    exc_info=True,
                )
        self._collapsed_placeholders.clear()

        # Удаляем всё, что осталось в layout (включая placeholder'ы,
        # которые не попали в self._collapsed_placeholders, — например,
        # placeholder'ы из предыдущего прогона, если clear вызван повторно).
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
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self._rag_context = None
        self._attached_files.clear()
        self._hypotheses.clear()
        self._hypothesis_widgets.clear()
        self._last_research_summary = None
        self._pending_final_answer = ""
        self._current_thought_stream = None
        self._research_mode_active = False
        self._research_cancelled = False
        if not keep_session:
            self._session_id = None
        self._active_tool_card = None
        if self._command_completer is not None:
            self._command_completer.hide_popup()
            self._command_popup_active = False
        self._last_assistant_widget = None
        self._last_run_params = None
        if hasattr(self, "continue_btn"):
            self.continue_btn.setVisible(False)
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
        """Останавливает генерацию с сохранением визуального лога.

        Семантика (этап 4): Стоп = отмена, но информация НЕ теряется:
        * серый поток рассуждений остаётся видимым (с пометкой «прервано»);
        * виджеты гипотез остаются в ленте как визуальный архив;
        * активный `HypothesisStore` очищается — в следующий запрос
          гипотезы НЕ утекают;
        * паспорт не строится;
        * внизу добавляется курсивный комментарий.
        """
        self._research_cancelled = True
        self._generating = False

        # если pool ещё грузится — не ждём 30 с, отменяем.
        if self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            self._pool_load_thread.quit()
            self._pool_load_thread.wait(500)
            if self._pool_load_thread.isRunning():
                self._pool_load_thread.terminate()
                self._pool_load_thread.wait(500)

        if self._agent_thread and self._agent_thread.isRunning() and self._agent_thread.client:
            self._agent_thread.client.cancel_request()

        if hasattr(self, "_web_thread") and self._web_thread and self._web_thread.isRunning():
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

        if self._agent_thread and self._agent_thread.isRunning():
            self._agent_thread.quit()
            self._agent_thread.wait(1000)
            if self._agent_thread.isRunning():
                self._agent_thread.terminate()
                self._agent_thread.wait(500)

        # Помечаем серый поток как прерванный.
        if self._current_thought_stream is not None:
            self._current_thought_stream.mark_interrupted("Рассуждение прервано пользователем.")

        # [9.5.7] Активная карточка могла остаться «⏳» — прерываем.
        if self._active_tool_card is not None:
            self._active_tool_card.set_result("Прервано пользователем", is_error=True)
            self._active_tool_card = None

        # Очищаем активный store — в следующий запрос гипотезы не утекут.
        # Визуальные виджеты (`_hypothesis_widgets`) НЕ трогаем — это архив.
        self._hypotheses.clear()

        # Паспорт не строим.
        self._last_research_summary = None
        self._pending_final_answer = ""

        self._reset_send_button()
        # [9.5.4] Явный статус «остановлено» после idle.
        self._set_activity_state("cancelled")
        self._add_message(
            "system",
            "⏹ Генерация остановлена пользователем.\n"
            "Визуальный лог рассуждений сохранён выше для ознакомления, "
            "но НЕ будет учтён в следующем запросе.",
        )
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self._research_mode_active = False
        self._research_cancelled = False
        self._current_thought_stream = None
        if hasattr(self, "continue_btn"):
            self.continue_btn.setVisible(False)

    def _send_message(self):
        if self._generating:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if hasattr(self, "continue_btn"):
            self.continue_btn.setVisible(False)
        # 0.1.3b: новый запрос — новый буфер частей.
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self.input_edit.clear()
        self.input_edit.setFocus()

        # @web и @dir/@file/@path — как сейчас
        if text.startswith("@web"):
            query = text[4:].strip()
            if query:
                self._start_web_search(query)
            else:
                self._add_message("system", "⚠️ Укажите запрос после @web")
            return

        parsed = self._extract_path_command(text)
        if parsed is not None:
            self._handle_path_command(*parsed)
            return

        self._create_session()

        # [D9] идемпотентность: стабильный id = hash(role|content|session).
        import hashlib

        msg_id = hashlib.sha1(f"{self._session_id or ''}|user|{text}".encode()).hexdigest()[:16]

        if any(m.id == msg_id for m in self._messages):
            logger.debug(f"Повтор user-сообщения {msg_id!r} — пропускаем дубль")
            return

        user_message = ChatMessage(id=msg_id, role="user", content=text)
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
        self._show_activity(f"🌐 Ищу в интернете: {query}")

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
        self._set_activity_state("thinking", "поиск по кодовой базе")
        self._rag_context = None
        self._rag_scope = self._query_scope(text)

        # [9.4] Профиль «Обучение» выключает RAG полностью.
        if self.vector_store and self._profile_rag_enabled:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=_RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
                categories=self._profile.rag_categories if self._profile else None,
            )
            self._rag_thread.results_ready.connect(lambda ctx, src: self._on_rag_results(ctx, src))
            self._rag_thread.search_error.connect(self._on_rag_error)
            self._rag_thread.finished.connect(lambda: self._do_chat(text))
            self._rag_thread.start()
        else:
            self._do_chat(text)

    @Slot(str, list)
    def _on_rag_results(self, context: str, sources: list):
        merged = self._merge_scope_into_context(context)
        if merged:
            budget = rag_char_budget(self._router.num_ctx)
            self._rag_context = truncate_to_chars(merged, budget)
        else:
            self._rag_context = None
        if sources:
            self._show_sources(sources)
            summary = ", ".join(f"{p}:{s}" for p, s, _end in sources[:3])
            self.status_label.setText(f"📚 Источники: {summary}")

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
        self._set_activity_state("thinking", "агент запущен")
        self._rag_scope = self._query_scope(text)

        # [9.4] Профиль «Обучение» выключает RAG полностью.
        if self.vector_store and self._profile_rag_enabled:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=_RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
                categories=self._profile.rag_categories if self._profile else None,
            )
            self._rag_thread.results_ready.connect(
                lambda ctx, src: self._on_agent_rag_ready(ctx, src, text, mode)
            )
            self._rag_thread.search_error.connect(lambda err: self._do_agent(text, mode, ""))
            self._rag_thread.start()
        else:
            self._do_agent(text, mode, "")

    def _on_agent_rag_ready(self, context: str, sources: list, text: str, mode: AgentMode):
        merged = self._merge_scope_into_context(context)
        if merged:
            budget = rag_char_budget(self._router.num_ctx)
            merged = truncate_to_chars(merged, budget)
        if sources:
            self._show_sources(sources)
            summary = ", ".join(f"{p}:{s}" for p, s, _end in sources[:3])
            self.status_label.setText(f"📚 {summary}")
        self._do_agent(text, mode, merged)

    # ---------------------------------------------------------
    # Универсальный вход в ReAct-цикл
    # ---------------------------------------------------------

    def _do_chat(self, text: str, override_limit: int | None = None):
        """Запускает ReAct-цикл в режиме Chat.

        Размер проекта определяет флоу:
          • light (≤100 файлов / ≤300 КБ) — 3 итерации, без research-фаз;
          • medium — research-вопрос идёт с 8 итерациями, без фаз;
          • deep (>500 файлов) — 5 итераций с фазами/гипотезами.
        Раньше research-вопрос всегда гонялся через full research (23 итерации),
        и маленький 43-файловый проект тратил 15 минут.
        """
        logger.debug("_do_chat: запуск через AgentChatThread (ReAct)")
        self._reset_research_state()

        # [D8] Определяем размер проекта и режим ДО запуска цикла.
        # _ensure_project_scan() заполняет self._project_files (кэш на сессию),
        # тот же вызов уже используется в _project_map_text().

        self._ensure_project_scan()
        file_count = len(self._project_files)
        # total_bytes считаем только если файлов мало (≤100) — иначе всё
        # равно deep/medium, а суммарный размер не влияет на решение.
        if file_count <= 100:
            total_bytes = 0
            for rel in self._project_files:
                with contextlib.suppress(OSError):
                    total_bytes += (Path(self.project_root) / rel).stat().st_size
        else:
            total_bytes = 0  # не используется при file_count > 100

        plan = self._resolve_research_mode(
            text,
            file_count=file_count,
            total_bytes=total_bytes,
        )

        # [9.4] Профиль ограничивает верхнюю границу итераций для
        # не-research вопросов. Research-лимит не трогаем — там фазовая
        # машина сама регулирует.
        if override_limit is None and self._profile is not None and not plan["research"]:
            plan["max_iters"] = min(plan["max_iters"], self._profile_chat_max_iters)

        is_research = plan["research"] and is_research_question(text)
        max_iters = override_limit if override_limit is not None else plan["max_iters"]

        if is_research:
            logger.info(
                f"Chat-режим, research-вопрос: лимит итераций {max_iters} "
                f"(файлов={file_count}, режим фаз={plan['phases']}, "
                f"гипотезы={'on' if plan['hypotheses'] else 'off'})"
            )
        else:
            logger.info(
                f"Chat-режим, {max_iters} итераций "
                f"(файлов={file_count}, light={'да' if plan['phases'] == 'off' else 'нет'})"
            )

        role_prompt = self.get_current_role_prompt()
        p_root = Path(self.project_root) if self.project_root else None
        mode_prompt = (
            self.prompt_manager.get_prompt("chat", project_root=p_root, context=self._template_context())
            if self.prompt_manager
            else ""
        )
        combined = "\n\n".join(filter(None, [role_prompt, mode_prompt]))

        raw_cache = getattr(self, "search_cache", None)
        agents_context = load_agents_context(self.project_root)
        merged_rag = "\n\n".join(filter(None, [self._attached_context(), self._rag_context or ""]))

        context = AgentContext(
            system_prompt=combined,
            rag_context=merged_rag,
            agents_context=agents_context,
            project_root=self.project_root,
            project_name=self._project_name(),
            project_map=self._project_map_text(),
            vector_store=self.vector_store,
            num_ctx=self._router.num_ctx,
            mode=AgentMode.CHAT,
            # 2.1: без cast(Any, ...) — SearchCache совместим с SearchCacheLike.
            search_cache=raw_cache,
            attached_files=self._attached_files if self._attached_files else None,
            hypotheses=self._hypotheses,
            extra_rules=self._project_rules(),
        )

        client = self._router.active_client
        if client is None:
            self._add_message("system", "⚠️ Нет доступного LLM-бэкенда.")
            self._reset_send_button()
            return

        registry = self._build_registry(AgentMode.CHAT)

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()
        self._agent_thread = AgentChatThread(
            client=client,
            registry=registry,
            model=model_name,
            context=context,
            messages_history=list(self._messages),
            agent_mode=AgentMode.CHAT,
            max_iterations=max_iters,
            orchestrator=self._orchestrator,
        )
        self._last_run_params = {
            "kind": "chat",
            "text": text,
            "rag_context": self._rag_context or "",
            "max_iters": max_iters,
        }

        self._connect_agent_thread_signals()

    def _do_agent(
        self,
        text: str,
        mode: AgentMode,
        rag_context: str,
        override_limit: int | None = None,
    ):
        """Запускает ReAct-цикл в режимах Plan / Agent.

        Размер проекта определяет лимит итераций и наличие research-фаз:
          • Agent-режим — всегда `agent_max_iterations` (полный доступ, задача
            может быть длинной: рефакторинг, миграция), research не ограничиваем;
          • Plan-режим — как Chat (light=3 / medium=8 / deep=5 итераций),
            но с read-only инструментами.
        """
        self._reset_research_state()

        client = self._router.active_client
        if client is None:
            self._add_message("system", "⚠️ Нет доступного LLM-бэкенда для агента.")
            self._reset_send_button()
            return

        # Определяем режим по размеру проекта.

        self._ensure_project_scan()
        file_count = len(self._project_files)
        if file_count <= 100:
            total_bytes = 0
            for rel in self._project_files:
                with contextlib.suppress(OSError):
                    total_bytes += (Path(self.project_root) / rel).stat().st_size
        else:
            total_bytes = 0

        plan = self._resolve_research_mode(
            text,
            file_count=file_count,
            total_bytes=total_bytes,
        )
        is_research = plan["research"] and is_research_question(text)

        # Agent-режим: полный лимит независимо от размера проекта (рефакторинг
        # может быть длинным); research-фазы не ограничиваем.
        if mode == AgentMode.AGENT:
            if override_limit is not None:
                max_iters = override_limit
            elif self._profile is not None:
                max_iters = self._profile_agent_max_iters
            else:
                try:
                    from core.config import load_config

                    max_iters = load_config().agent_max_iterations
                except Exception:
                    max_iters = 30
            logger.info(
                f"Agent-режим, {max_iters} итераций "
                f"(файлов={file_count}, research={'on' if is_research else 'off'})"
            )
        else:
            # Plan-режим: как Chat — размер проекта важен.
            max_iters = override_limit if override_limit is not None else plan["max_iters"]
            if override_limit is None and self._profile is not None:
                max_iters = min(max_iters, self._profile_agent_max_iters)
            if is_research:
                logger.info(
                    f"Plan-режим, research-вопрос: лимит {max_iters} "
                    f"(файлов={file_count}, фазы={plan['phases']})"
                )
            else:
                logger.info(f"Plan-режим, {max_iters} итераций (файлов={file_count})")

        role_prompt = self.get_current_role_prompt()
        p_root = Path(self.project_root) if self.project_root else None
        mode_prompt = (
            self.prompt_manager.get_prompt(mode.value, project_root=p_root, context=self._template_context())
            if self.prompt_manager
            else ""
        )
        combined = "\n\n".join(filter(None, [role_prompt, mode_prompt]))

        raw_cache = getattr(self, "search_cache", None)
        agents_context = load_agents_context(self.project_root)
        merged_rag = "\n\n".join(filter(None, [self._attached_context(), rag_context]))

        context = AgentContext(
            system_prompt=combined,
            rag_context=merged_rag,
            agents_context=agents_context,
            project_root=self.project_root,
            project_name=self._project_name(),
            project_map=self._project_map_text(),
            vector_store=self.vector_store,
            num_ctx=self._router.num_ctx,
            mode=mode,
            # 2.1: без cast(Any, ...) — SearchCache совместим с SearchCacheLike.
            search_cache=raw_cache,
            attached_files=self._attached_files if self._attached_files else None,
            hypotheses=self._hypotheses,
            extra_rules=self._project_rules(),
        )

        self._current_assistant_content = ""
        self._current_thinking_content = ""

        model_name = self.get_current_model()
        self._agent_thread = AgentChatThread(
            client=client,
            registry=self._build_registry(mode),
            model=model_name,
            context=context,
            messages_history=list(self._messages),
            agent_mode=mode,
            max_iterations=max_iters,
            orchestrator=self._orchestrator,
        )

        self._last_run_params = {
            "kind": "agent",
            "text": text,
            "mode": mode,
            "rag_context": rag_context,
            "max_iters": max_iters,
        }

        self._connect_agent_thread_signals()

    def _connect_agent_thread_signals(self) -> None:
        """Подключает сигналы AgentChatThread к слотам панели."""
        if self._agent_thread is None:
            return

        # --- Orchestrator (P3) ---
        self._agent_thread.orchestrator_event.connect(self._on_orchestrator_event)
        self._agent_thread.orchestrator_result.connect(self._on_orchestrator_result)

        # --- существующие ---
        self._agent_thread.research_mode_detected.connect(self._on_research_mode_detected)
        self._agent_thread.thought_chunk.connect(self._on_thought_chunk)
        self._agent_thread.final_answer_started.connect(self._on_final_answer_started)
        self._agent_thread.final_chunk.connect(self._on_final_chunk)
        self._agent_thread.research_summary_ready.connect(self._on_research_summary_ready)
        self._agent_thread.chunk_received.connect(self._on_chunk)
        self._agent_thread.thinking_received.connect(self._on_thinking)
        self._agent_thread.tool_call_detected.connect(self._on_tool_call_detected)
        self._agent_thread.tool_executed.connect(self._on_tool_executed)
        self._agent_thread.tool_started.connect(self._on_tool_started)
        self._agent_thread.finish_received.connect(self._on_agent_finish)
        self._agent_thread.error_occurred.connect(self._on_agent_error)
        with contextlib.suppress(Exception):
            self._agent_thread.iteration_limit_exceeded.connect(self._on_iteration_limit_exceeded)
        self._agent_thread.tools_status_changed.connect(self._on_tools_status_changed)
        self._agent_thread.status_changed.connect(self._on_status_event)

        # [4.8.E] Низкоуровневые delta-события. Пока только для диагностики:
        # UI по-прежнему получает токены через _on_chunk/_on_thinking,
        # чтобы не дублировать композитный пузырь. Будущие потребители —
        # TTFT-метрики, раздельная подсветка reasoning-блоков.
        self._agent_thread.text_delta.connect(self._on_text_delta_event)
        self._agent_thread.reasoning_delta.connect(self._on_reasoning_delta_event)
        self._agent_thread.stream_started.connect(self._on_stream_started_event)
        self._agent_thread.stream_finished.connect(self._on_stream_finished_event)

        self._agent_thread.start()

    # ---------------------------------------------------------
    # Существующие слоты
    # ---------------------------------------------------------

    def _build_registry(self, mode: AgentMode | None = None) -> ToolRegistry:
        """Собирает реестр из декларативного ALL_TOOLS.

        * `read_attached_file` регистрируется, только если есть прикреплённые
          файлы (инструмент бесполезен без store).
        * В CHAT-режиме политики инструментов из `cfg.chat_auto_tools`
          принудительно ставятся в AUTO. Если конфиг не читается —
          используется встроенный `_CHAT_AUTO_TOOLS`.
        * `cfg.tool_policies` и явные `set_policy` из UI применяются
          последними и могут переопределить AUTO/ASK_FIRST.
        """
        from tools.registry import ALL_TOOLS

        registry = ToolRegistry()
        has_attached = bool(self._attached_files)

        for tool_func in ALL_TOOLS:
            meta = getattr(tool_func, "_tool_meta", None)
            if meta is None:
                continue
            if meta["name"] == "read_attached_file" and not has_attached:
                continue
            registry.register(tool_func)

        if mode == AgentMode.CHAT:
            try:
                cfg = load_config()
                auto_tools = list(cfg.chat_auto_tools) or list(_CHAT_AUTO_TOOLS)
            except Exception as e:
                logger.debug(f"chat_auto_tools: fallback на встроенный список: {e}")
                auto_tools = list(_CHAT_AUTO_TOOLS)
            for name in auto_tools:
                registry.set_policy(name, ToolPolicy.AUTO)

        self._apply_config_policies(registry)

        for tool_name, policy in self.tool_policies.items():
            registry.set_policy(tool_name, policy)

        return registry

    def _apply_config_policies(self, registry: ToolRegistry) -> None:
        try:
            from core.config import load_config

            cfg = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать политики из конфига: {e}")
            return

        for tool_name, policy in cfg.tool_policies.items():
            registry.set_policy(tool_name, policy)

        confirm = cfg.confirm
        if not (confirm.enabled or confirm.web_search):
            registry.set_policy("web_search", ToolPolicy.AUTO)

    @Slot(str, str, str)
    def _on_tool_call_detected(self, name: str, description: str, arguments_json: str):
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
            "read_attached_file": "Чтение прикреплённого файла",
            "record_hypothesis": "Запись гипотезы",
            "update_hypothesis": "Обновление гипотезы",
            "list_hypotheses": "Список гипотез",
            "run_shell_command": "Shell-команда",
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

    @Slot(str, str, bool, object)
    def _on_tool_executed(
        self,
        name: str,
        content: str,
        is_error: bool,
        metadata: dict | None = None,
    ):
        """[9.5.7] Обновляет tool-карточку результатом (✓/✗ + preview).

        [9.6.1] `metadata` (dict | None) — структурированные данные от
        инструмента. Для `write_file` запускает diff-режим карточки.
        """
        card_was_present = self._active_tool_card is not None

        # 0.1.3b: фиксируем результат в буфере. Порядок в буфере —
        # ToolCallPart → ToolResultPart → ToolCallPart → … — важен
        # для _render_assistant_parts, который матчит результат с
        # последней pending-карточкой по name.
        self._current_assistant_parts.append(
            ToolResultPart(
                call_id="",
                name=name,
                content=content or "",
                is_error=bool(is_error),
            )
        )

        # [9.5.4] Возвращаемся в «думает» — модель обработает результат.
        self._set_activity_state("thinking")

        # 1. Обновляем активную карточку.
        if self._active_tool_card is not None:
            self._active_tool_card.set_result(content or "", is_error, metadata)
            self._active_tool_card = None

        # 2. Серый трейс — компактная запись результата (research).
        if self._current_thought_stream is not None and name not in (
            "record_hypothesis",
            "update_hypothesis",
        ):
            preview = (content or "").strip().replace("\n", " ")[:200]
            self._current_thought_stream.append_result(name, ok=not is_error, preview=preview)

        # 3. Гипотезы — отдельный виджет (как было).
        if (
            name in ("record_hypothesis", "update_hypothesis")
            and not is_error
            and self._update_hypothesis_widget(content)
        ):
            return

        # 4. Fallback: если карточки не было — отдельное сообщение.
        if not card_was_present and not self._research_mode_active:
            self._add_tool_message(name, content, is_error)

    # ------------------------------------------------------------------
    # [9.6.1] Diff-карточка write_file — слоты
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_open_tool_result_in_editor(self, path: str) -> None:
        """Открывает файл (после write_file) в редакторе через MainWindow."""
        if path:
            self.source_activated.emit(path, 0)

    @Slot(dict)
    def _on_show_tool_diff(self, payload: dict) -> None:
        """[9.8] Карточка write_file запросила diff — пробрасываем в MainWindow.

        MainWindow подписан на `ChatPanel.diff_requested` и открывает
        Monaco DiffEditor с original↔modified.
        """
        if not payload:
            return
        self.diff_requested.emit(payload)

    @Slot(dict)
    def _on_revert_write_file(self, payload: dict) -> None:
        """Откатывает запись write_file к original_content.

        Args:
            payload: {"path", "original_content", "is_new_file"} от карточки.
        """
        from PySide6.QtWidgets import QMessageBox

        path = str(payload.get("path") or "")
        original = str(payload.get("original_content") or "")
        is_new_file = bool(payload.get("is_new_file"))
        if not path:
            return

        target = Path(path)
        if is_new_file and target.exists():
            reply = QMessageBox.question(
                self,
                "Откат создания файла",
                f"Удалить созданный агентом файл?\n\n{path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                target.unlink()
                self.status_label.setText(f"↩ Удалён: {target.name}")
                logger.info(f"revert write_file: удалён {path}")
            except OSError as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось удалить файл:\n{e}")
            return

        reply = QMessageBox.question(
            self,
            "Откат изменений",
            f"Восстановить предыдущее содержимое?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(original, encoding="utf-8")
            self.status_label.setText(f"↩ Откат: {target.name}")
            logger.info(f"revert write_file: восстановлен {path}")
            # Обновляем git-статус.
            if hasattr(self, "source_activated"):
                # Файл мог быть открыт в редакторе — перечитать.
                self.source_activated.emit(path, 0)
        except OSError as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось восстановить файл:\n{e}")

    def _update_hypothesis_widget(self, content: str) -> bool:
        """Разбирает JSON-ответ тула гипотезы и обновляет/создаёт виджет."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(data, dict) or not data.get("ok"):
            return False
        h = data.get("hypothesis")
        if not isinstance(h, dict):
            return False
        hid = str(h.get("id") or "")
        if not hid:
            return False

        widget = self._hypothesis_widgets.get(hid)
        if widget is None:
            widget = ChatHypothesisItem(
                hypothesis_id=hid,
                statement=str(h.get("statement") or ""),
            )
            self._hypothesis_widgets[hid] = widget
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        widget.update_state(
            status=str(h.get("status") or "open"),
            evidence=str(h.get("evidence") or ""),
            verification_method=str(h.get("verification_method") or ""),
        )

        # Дублируем в серый поток как компактную строку.
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_hypothesis(
                hypothesis_id=hid,
                status=str(h.get("status") or "open"),
                statement=str(h.get("statement") or ""),
                evidence=str(h.get("evidence") or ""),
            )

            # Новый элемент появился — если пользователь «на дне», следуем за стримом.
        # Если он листает вверх — не дёргаем (AutoScrollArea сама покажет «▼ Вниз»).
        self._scroll_to_bottom()
        return True

    @Slot(str, str)
    def _on_tool_started(self, name: str, arguments_json: str):
        """[9.5.7] Карточка tool-call создаётся ДО выполнения инструмента.

        В `AgentLoop` `on_tool_start` вызывается до `ask_first` (см. патч
        `agents/agent_loop.py`), поэтому пользователь видит карточку с ⏳
        одновременно с диалогом подтверждения. Результат придёт в ту же
        карточку через `_on_tool_executed` → `set_result(...)`.

        В research-режиме карточка тоже создаётся (в пузыре), а не только
        в сером трейсе — это нужно для «▸ Показать полностью» на длинных
        observation.
        """
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            args = {}
        arg_summary = ", ".join(f"{k}={v}" for k, v in args.items())
        # 0.1.3b: фиксируем вызов в буфере текущего ответа.
        # При восстановлении сессии _render_assistant_parts восстановит
        # карточку и привяжет к ней результат из ToolResultPart.
        self._current_assistant_parts.append(
            ToolCallPart(calls=[ToolCall(name=name, arguments=args)])
        )
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
            "read_attached_file": "📄 Дочитывание файла",
            "record_hypothesis": "💡 Запись гипотезы",
            "update_hypothesis": "🔬 Обновление гипотезы",
            "list_hypotheses": "📋 Список гипотез",
            "run_shell_command": "⚡ Shell-команда",
        }
        label = tool_labels.get(name, f"🛠️ {name}")
        activity = f"{label}: {arg_summary}" if arg_summary else label

        # [9.5.3] Создаём трейс в Agent/Plan (не в Chat), пишем action.
        stream = self._ensure_thought_stream()
        if stream is not None:
            stream.append_action(name, args)

        # [9.5.4] Статус: выполняет инструмент (без дубля иконки).
        self._set_activity_state("tool", name)

        # Activity-строка только в Chat-режиме (где трейса нет).
        if stream is None and not self._research_mode_active:
            self._show_activity(activity)

        # [9.5.7] Карточка tool-call ВСЕГДА (и в research, и в обычном).
        widget = self._ensure_assistant_widget()
        card = widget.append_tool_call(name, arguments_json)
        self._active_tool_card = card

        # [9.6.1] Подключаем сигналы diff-режима (write_file).
        if card is not None:
            card.open_in_editor_requested.connect(self._on_open_tool_result_in_editor)
            card.revert_requested.connect(self._on_revert_write_file)
            # [9.8] «Показать diff» — Monaco DiffEditor.
            card.show_diff_requested.connect(self._on_show_tool_diff)

        # Сбрасываем пузырь ТОЛЬКО если модель уже дала текст —
        # следующий текст пойдёт в новый пузырь (это новый ответ модели).
        # Если текста не было (multi-tool-call) — продолжаем в тот же пузырь.
        if self._current_assistant_content or self._current_thinking_content:
            self._last_assistant_widget = None
            self._current_assistant_content = ""
            self._current_thinking_content = ""

    @Slot(str)
    def _on_status_event(self, text: str):
        """[9.5.3] Статус — в серый трейс (Agent/Plan/research) или activity (Chat)."""
        if not text:
            return
        stream = self._ensure_thought_stream()
        if stream is not None:
            if text.startswith("🔁") or text.startswith("🪞"):
                stream.append_nudge(text)
            elif text.startswith("📌"):
                stream.append_phase(text)
            else:
                stream.append_status(text)
        # Activity-строка только в Chat-режиме (где трейса нет).
        if stream is None and not self._research_mode_active:
            self._show_activity(text)
        # В статус-строке — только если не перетёрто другим состоянием.
        if self._activity_state == "idle":
            self.status_label.setText(text)

    # ---------------------------------------------------------
    # [4.8.E] Низкоуровневые delta-события (диагностика)
    # ---------------------------------------------------------

    @Slot(str)
    def _on_text_delta_event(self, token: str) -> None:
        """[4.8.E] Text-delta от AgentLoop.

        Не пишет в UI — высокоуровневый `_on_chunk` остаётся единственным
        источником для `TextWidget`. `logger.trace` включается только при
        `LOGURU_LEVEL=TRACE`, в проде — no-op.
        """
        logger.trace(f"text_delta: {token[:40]!r}")

    # ------------------------------------------------------------------
    # [9.5] Стриминговые границы + tok/s в приборную панель
    # ------------------------------------------------------------------

    @Slot()
    def _on_stream_started_event(self) -> None:
        """Начало нового стрима — «печатает»."""
        logger.trace("stream_started")
        if hasattr(self, "send_btn"):
            # Меняем иконку кнопки Stop — уже ■, добавим tooltip.
            self.send_btn.setToolTip("Остановить генерацию")
        if hasattr(self, "_dashboard"):
            self._dashboard.set_tools_active(False)

    @Slot(str)
    def _on_reasoning_delta_event(self, token: str) -> None:
        """[4.8.E] Reasoning-delta от AgentLoop. Аналогично, только лог."""
        logger.trace(f"reasoning_delta: {token[:40]!r}")

    @Slot()
    def _on_stream_finished_event(self) -> None:
        """[4.8.E] Конец стрима модели — обновляем tok/s в приборной панели."""
        logger.trace("stream_finished")
        if self._dashboard is None:
            return
        client = self._router.active_client
        stats = getattr(client, "last_stats", None) or {}
        tps = stats.get("tokens_per_sec", 0.0)
        with contextlib.suppress(TypeError, ValueError):
            self._dashboard.set_tok_per_sec(float(tps))

    # ------------------------------------------------------------------
    # [9.5.3] Серый трейс в обычном Agent/Plan (не только research)
    # ------------------------------------------------------------------

    def _ensure_thought_stream(self) -> "ChatThoughtStream | None":
        """Возвращает серый трейс для текущего прогона, создаёт при необходимости.

        [9.5.3] В Agent/Plan (не research) трейс создаётся с первым
        tool-call или статусом — до этого момента виджета нет.
        В Chat-режиме (tools off, обычно одна итерация) трейс не нужен:
        статусы идут плоскими `ChatActivityItem`.
        В research `_current_thought_stream` уже создан в
        `_on_thought_chunk` — просто возвращаем.
        """
        if self._current_thought_stream is not None:
            return self._current_thought_stream
        # Chat-режим: плоский activity, без трейса.
        if self._current_mode() == AgentMode.CHAT and not self._research_mode_active:
            return None
        self._current_thought_stream = ChatThoughtStream()
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, self._current_thought_stream)
        self.scroll_layout.setStretch(self.scroll_layout.indexOf(self._current_thought_stream), 0)
        # [9.10]
        self._maybe_collapse_old_messages()
        return self._current_thought_stream

    # ------------------------------------------------------------------
    # [9.10] Виртуализация длинных чатов
    # ------------------------------------------------------------------

    def _is_message_widget(self, widget: QWidget | None) -> bool:
        """True, если виджет — «сообщение» (можно сворачивать в placeholder).

        Включает: ChatMessageItem, ChatActivityItem, ChatSourcesItem,
        ChatThoughtStream, ChatResearchPassport, ChatDiagramItem,
        ChatHypothesisItem. Stretch-элементы (layout-распорки) и
        CollapsedPlaceholder — не считаются.
        """
        if widget is None:
            return False
        if isinstance(widget, CollapsedPlaceholder):
            return False
        return isinstance(
            widget,
            (
                ChatMessageItem,
                ChatActivityItem,
                ChatSourcesItem,
                ChatThoughtStream,
                ChatResearchPassport,
                ChatDiagramItem,
                ChatHypothesisItem,
            ),
        )

    def _message_widgets(self) -> list[tuple[int, QWidget]]:
        """Список (layout_index, widget) для всех «сообщений» в layout."""
        out: list[tuple[int, QWidget]] = []
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if self._is_message_widget(widget):
                out.append((i, widget))
        return out

    def _maybe_collapse_old_messages(self) -> None:
        """[9.10] Сворачивает самые старые сообщения при превышении порога.

        Вызывается после добавления нового виджета. Порог —
        `_MAX_VISIBLE_MESSAGE_WIDGETS`; сверху добавляется
        `_COLLAPSE_BUFFER`, чтобы не сворачивать на каждом следующем
        сообщении.
        """
        messages = self._message_widgets()
        if len(messages) <= _MAX_VISIBLE_MESSAGE_WIDGETS:
            return

        # Сколько свернуть: всё, что превышает порог, + буфер.
        over = len(messages) - _MAX_VISIBLE_MESSAGE_WIDGETS
        to_collapse_count = over + _COLLAPSE_BUFFER

        # Берём самые старые (в начале layout).
        to_collapse = messages[:to_collapse_count]
        if not to_collapse:
            return

        # Индекс в layout, с которого начинается группа (первый из to_collapse).
        start_idx = to_collapse[0][0]

        # Отсоединяем виджеты из layout и скрываем.
        collapsed_widgets: list[QWidget] = []
        for _idx, widget in to_collapse:
            self.scroll_layout.removeWidget(widget)
            widget.setVisible(False)
            collapsed_widgets.append(widget)

        # Вставляем placeholder на место первого свёрнутого виджета.
        placeholder = CollapsedPlaceholder(collapsed_widgets, parent=self.scroll_content)
        placeholder.restore_requested.connect(self._on_restore_collapsed)
        self.scroll_layout.insertWidget(start_idx, placeholder)
        self._collapsed_placeholders.append(placeholder)

        logger.debug(
            f"[9.10] Свёрнуто {len(collapsed_widgets)} сообщений в placeholder "
            f"(всего сообщений было {len(messages)})"
        )

    @Slot(object)
    def _on_restore_collapsed(self, placeholder) -> None:
        """[9.10] Пользователь кликнул по placeholder — разворачиваем порцию."""
        if not isinstance(placeholder, CollapsedPlaceholder):
            return
        self._restore_placeholder_batch(placeholder)

    def _restore_placeholder_batch(self, placeholder: CollapsedPlaceholder) -> None:
        """Разворачивает `_RESTORE_BATCH` последних свёрнутых виджетов.

        Вставляет их в layout СРАЗУ ПОСЛЕ placeholder'а — так хронология
        сохраняется: placeholder (более старые) → следующие за ним
        (более свежие, которые только что развернули).

        Если `placeholder.collapsed_count == 0` — placeholder удаляется.
        """
        # Позиция placeholder'а в layout.
        try:
            idx = self.scroll_layout.indexOf(placeholder)
        except Exception:
            idx = -1
        if idx < 0:
            logger.debug("[9.10] Placeholder не найден в layout — пропускаем")
            return

        widgets = placeholder.pop_restore_batch(_RESTORE_BATCH)
        if not widgets:
            # Нечего разворачивать — удаляем placeholder.
            self.scroll_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            if placeholder in self._collapsed_placeholders:
                self._collapsed_placeholders.remove(placeholder)
            return

        # Вставляем виджеты после placeholder'а, сохраняя их хронологический
        # порядок: widgets[0] — самый старый из порции, widgets[-1] — самый свежий.
        insert_at = idx + 1
        for offset, widget in enumerate(widgets):
            self.scroll_layout.insertWidget(insert_at + offset, widget)
            widget.setVisible(True)
            # Обновляем максимальную ширину пузыря (на случай ресайза окна,
            # пока виджет был скрыт).
            if isinstance(widget, ChatMessageItem):
                self._update_bubble_width(widget)

        # Если всё развернули — placeholder не нужен.
        if placeholder.collapsed_count == 0:
            self.scroll_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            if placeholder in self._collapsed_placeholders:
                self._collapsed_placeholders.remove(placeholder)

        self.scroll_area.scroll_to_bottom_if_sticky()
        logger.debug(
            f"[9.10] Развёрнуто {len(widgets)} сообщений, осталось свёрнутых: {placeholder.collapsed_count}"
        )

    def _expand_all_collapsed(self) -> None:
        """[9.10] Разворачивает все placeholder'ы (для «Показать всё»).

        Не используется в UI напрямую, но полезно для палитры команд /
        отладки. Проходит по списку placeholder'ов и разворачивает
        каждый до конца.
        """
        # Копируем список — `_restore_placeholder_batch` может удалять
        # элементы из `_collapsed_placeholders` при полном развороте.
        for placeholder in list(self._collapsed_placeholders):
            while placeholder.collapsed_count > 0:
                self._restore_placeholder_batch(placeholder)

    # ------------------------------------------------------------------
    # [9.5.4] Индикатор активности
    # ------------------------------------------------------------------

    _ACTIVITY_ICONS = {
        "idle": "💤",
        "thinking": "🤔",
        "tool": "🔧",
        "streaming": "✍️",
        "error": "❌",
        "cancelled": "⏹",
    }
    _ACTIVITY_LABELS = {
        "idle": "Готов к работе",
        "thinking": "Думает…",
        "tool": "Выполняет инструмент…",
        "streaming": "Печатает…",
        "error": "Ошибка",
        "cancelled": "Остановлено",
    }
    # [9.5.4] Цвет фона кнопки Stop по состоянию. Кнопка остаётся «■»
    # (привычный Stop), но цвет фона подсказывает, что происходит.
    _STOP_BUTTON_BG = {
        "idle": "#d32f2f",  # обычный Stop
        "thinking": "#d4a373",  # янтарный — «думает»
        "tool": "#6ba3d6",  # синий — «выполняет»
        "streaming": "#98c379",  # зелёный — «печатает»
        "cancelled": "#7a7a8a",  # серый — «остановлено»
        "error": "#c0392b",  # тёмно-красный — «ошибка»
    }

    def _set_activity_state(self, state: str, detail: str = "") -> None:
        """[9.5.4] Единый индикатор активности.

        Меняет:
          * текст `status_label` (эмодзи + подпись + detail);
          * tooltip кнопки Stop/Отправить;
          * ЦВЕТ ФОНА кнопки Stop (текст остаётся «■» при генерации,
            «▶ Отправить» — вне её).

        Не дублирует эмодзи в `detail` (detail — чистый текст без иконок).
        """
        icon = self._ACTIVITY_ICONS.get(state, "●")
        label = self._ACTIVITY_LABELS.get(state, state)
        full = f"{icon} {label} {detail}".strip() if detail else f"{icon} {label}"
        self._activity_state = state
        self.status_label.setText(full)

        if self._generating:
            # Кнопка Stop: ■ + фон по состоянию.
            self.send_btn.setText("■")
            bg = self._STOP_BUTTON_BG.get(state, "#d32f2f")
            self.send_btn.setStyleSheet(
                f"font-size: 18px; padding: 8px 20px; background: {bg};"
                " color: white; border: none; border-radius: 4px;"
            )
            self.send_btn.setToolTip(f"{full}\nНажмите, чтобы остановить")
        else:
            # Вне генерации кнопка — «Отправить», фон синий.
            self.send_btn.setText("▶ Отправить")
            self.send_btn.setStyleSheet(
                "font-size: 14px; padding: 8px 20px; background: #1976d2;"
                " color: white; border: none; border-radius: 4px;"
            )
            self.send_btn.setToolTip("Отправить (Ctrl+Enter)")

    def _build_assistant_message(self, *, fallback_text: str = "") -> ChatMessage:
        """Собирает ChatMessage из накопленных частей текущего ответа (0.1.3b).

        Reasoning добавляется первым, чтобы модель и UI видели его до текста
        (порядок важен для воспроизведения истории). Tool-call/result-части
        идут в порядке реального выполнения.

        Args:
            fallback_text: Что подставить, если буферы пусты (например,
                модель вернула только финальный текст без стрима — на
                случай будущих бэкендов).

        Returns:
            ChatMessage с parts; пустой TextPart гарантирует, что сообщение
            не окажется совсем без частей (pydantic-модель допускает parts=[]).
        """
        parts: list[MessagePart] = []
        if self._current_assistant_reasoning:
            parts.append(ReasoningPart(text=self._current_assistant_reasoning))
        text = self._current_assistant_text or fallback_text
        if text:
            parts.append(TextPart(text=text))
        parts.extend(self._current_assistant_parts)
        if not parts:
            parts.append(TextPart(text=""))
        return ChatMessage(role="assistant", parts=parts)

    @Slot(str)
    def _on_agent_finish(self, result: str):
        # 4.8.B: свернуть reasoning в последнем пузыре — модель закончила.
        if self._last_assistant_widget is not None:
            self._last_assistant_widget.finalize()

        # --- Research-режим: паспорт с выводами, без обычного пузыря ---
        if self._research_mode_active and not self._research_cancelled:
            summary = self._last_research_summary
            final_text = self._pending_final_answer
            if summary:
                summary["final_answer"] = final_text
                # Если финального текста нет — добавляем явную плашку.
                # Раньше паспорт показывался с пустым разделом «Выводы»,
                # и пользователь не понимал, что произошло.
                if not (summary["final_answer"] or "").strip():
                    reason = (result or "").strip() or "финальный синтез не был сформирован"
                    summary["final_answer"] = (
                        "⚠️ **Финальный синтез не был сформирован.**\n\n"
                        f"Служебное сообщение агента: {reason}\n\n"
                        "Ниже — доказательная база (что успел прочитать агент) "
                        "и список гипотез. Попробуйте переформулировать вопрос "
                        "или увеличьте лимит итераций агента."
                    )
                if self._should_show_passport(summary):
                    self._show_research_passport(summary)

            # 0.1.3b/c: в research-режиме тоже сохраняем ответ в сессию,
            # иначе после перезагрузки пользователь видел только паспорт,
            # а reasoning/tool-calls/финальный текст пропадали.
            if final_text and self._session_id:
                assistant_message = self._build_assistant_message(
                    fallback_text=final_text
                )
                self._messages.append(assistant_message)
                self.session_store.save_message(self._session_id, assistant_message)

            self._reset_send_button()
            self._current_assistant_content = ""
            self._current_thinking_content = ""
            self._current_assistant_parts = []
            self._current_assistant_text = ""
            self._current_assistant_reasoning = ""
            self._last_assistant_widget = None
            self._current_thought_stream = None
            self._pending_final_answer = ""
            self._last_research_summary = None
            self._research_mode_active = False
            return

        # --- Обычный режим: как раньше, но через _build_assistant_message ---
        if result and result != "Отменено":
            assistant_message = self._build_assistant_message(fallback_text=result)
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)
            # [9.5.3] В Chat-режиме (без трейса) — плоский activity.
            # В Agent/Plan трейс уже показывает ход; финальный статус
            # выставит `_reset_send_button` → idle.
            if self._current_thought_stream is None:
                self._show_activity("✅ Готово")

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self._last_assistant_widget = None
        # [9.5.3] Ссылку сбрасываем, но виджет трейса остаётся в ленте.
        self._current_thought_stream = None
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._research_mode_active = False

    def _reset_send_button(self):
        self._generating = False
        self.input_edit.setEnabled(True)
        # [9.5.7] Сбрасываем ссылку на активную карточку.
        self._active_tool_card = None
        # [9.5.4] Единая точка: idle ставит и текст, и фон, и tooltip.
        self._set_activity_state("idle")

    def _set_button_stop(self) -> None:
        """Fallback: ■ и красный фон. `_set_activity_state` уточнит цвет.

        Оставлен для совместимости (вызывается из `_start_chat`/`_start_agent`
        до `_set_activity_state("thinking")`).
        """
        self.send_btn.setText("■")
        self.send_btn.setStyleSheet(
            "font-size: 18px; padding: 8px 20px; background: #d32f2f;"
            " color: white; border: none; border-radius: 4px;"
        )

    def _send_finished(self):
        if self._last_assistant_widget is not None:
            self._last_assistant_widget.finalize()

        if self._current_assistant_text or self._current_assistant_reasoning:
            assistant_message = self._build_assistant_message(
                fallback_text=self._current_assistant_content
            )
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self._last_assistant_widget = None

    @Slot(str)
    def _on_agent_error(self, error_msg: str):
        # Помечаем серый поток как прерванный, если он был.
        if self._current_thought_stream is not None:
            self._current_thought_stream.mark_interrupted(f"Ошибка: {error_msg[:120]}")
        # [9.5.4] Индикатор: ошибка (короткий detail).
        self._set_activity_state("error", error_msg[:60])
        if self._active_tool_card is not None:
            self._active_tool_card.set_result(f"Ошибка: {error_msg[:200]}", is_error=True)
            self._active_tool_card = None
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()
        # _reset_send_button ставит idle — явно выставим error после.
        self._set_activity_state("error", error_msg[:60])
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None

    @Slot()
    def _on_iteration_limit_exceeded(self):
        """Прерываем возможный research-прогон..."""
        self._reset_research_state()
        self._reset_send_button()
        self._show_activity(
            "⏸ Достигнут лимит итераций. Нажмите «Продолжить», чтобы дать агенту больше шагов."
        )
        # [9.5.4] Индикатор: «лимит итераций» вместо общего idle.
        self._set_activity_state("cancelled", "лимит итераций")
        self.continue_btn.setVisible(True)
        self.continue_btn.setEnabled(True)

    @Slot()
    def _continue_after_limit(self) -> None:
        """Перезапускает запрос с удвоенным лимитом итераций.

        Модель начинает с ТЕКУЩЕЙ истории диалога (включая прошлый
        незавершённый ответ), но получает больше бюджета шагов. Если снова
        упрётся — кнопка появится снова, и можно будет продолжить дальше.
        """
        if self._generating:
            return
        params = self._last_run_params
        if not params:
            self.continue_btn.setVisible(False)
            return

        current = int(params.get("max_iters", 10))
        # Удваиваем, но не меньше +10 и не больше 200 — иначе 7B на CPU
        # будет считать часами.
        new_limit = min(max(current * 2, current + 10), 200)

        self.continue_btn.setVisible(False)
        self._add_message(
            "system",
            f"▶ Продолжаю с увеличенным лимитом: {current} → {new_limit} шагов. "
            "Модель начнёт с текущей истории, но получит больше бюджета. "
            "Если снова упрётся — кнопка появится опять.",
        )

        if params["kind"] == "chat":
            self._do_chat(params["text"], override_limit=new_limit)
        else:
            self._do_agent(
                params["text"],
                params["mode"],
                params["rag_context"],
                override_limit=new_limit,
            )

    @Slot(bool)
    def _on_tools_status_changed(self, active: bool):
        if self._dashboard is not None:
            self._dashboard.set_tools_active(active)
        if hasattr(self, "_dashboard") and self._dashboard is not None:
            self._dashboard.set_tools_active(active)
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

    def set_model(self, model: str):
        idx = self.model_combo.findData(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.addItem(model, model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    def set_tool_policy(self, tool_name: str, policy: Any) -> None:
        self.tool_policies[tool_name] = policy

    def set_show_reasoning(self, show: bool) -> None:
        """Включает/выключает отображение reasoning_content в чате.

        False — токены размышлений продолжают приходить и логируются, но
        виджет сообщения не создаётся/не обновляется. Уже показанные блоки
        reasoning не скрываются — это «тумблер для будущих токенов».
        """
        self._show_reasoning = bool(show)
        logger.debug(f"Показ размышлений: {'вкл' if show else 'выкл'}")

    def set_vector_store(self, vector_store):
        self.vector_store = vector_store

    # ---------------------------------------------------------
    # Публичный API для MainWindow (план 2.4)
    #
    # Раньше MainWindow обращался к приватным полям напрямую:
    #   chat_panel._update_mode_tooltips()
    #   chat_panel.mode_combo.setCurrentIndex(...)
    #   chat_panel._show_reasoning
    #   chat_panel._update_model_list()
    #   chat_panel.refresh_models_btn.setText(...)
    # Это связывало классы и делало невозможным вынести меню
    # в отдельный модуль. Теперь — явные методы с понятным контрактом.
    # ---------------------------------------------------------

    def update_mode_tooltips(self) -> None:
        """Пересчитывает тултипы селектора режимов (из актуальных промптов).

        Публичная замена `_update_mode_tooltips()` для MainWindow
        (после сохранения промптов в редакторе).
        """
        self._update_mode_tooltips()

    def update_model_list(self) -> None:
        """Перерисовывает список моделей (после синхронизации с Ollama).

        Публичная замена `_update_model_list()` для MainWindow
        (в `_on_model_check_finished`).
        """
        self._update_model_list()

    def set_mode_by_name(self, mode: str) -> None:
        """Переключает режим по имени: 'chat' | 'plan' | 'agent'.

        Публичная замена прямому доступу к `mode_combo.setCurrentIndex(...)`
        из MainWindow (после открытия проекта восстанавливаем сохранённый
        режим из config / project_settings).
        """
        mode_map = {"chat": 0, "plan": 1, "agent": 2}
        idx = mode_map.get(mode, 2)
        if 0 <= idx < self.mode_combo.count():
            self.mode_combo.setCurrentIndex(idx)

    def get_show_reasoning(self) -> bool:
        """Текущее состояние тумблера «Показывать размышления».

        Публичная замена прямому чтению `_show_reasoning` из MainWindow
        (синхронизация чекбокса в правой панели при `_do_open_project`).
        """
        return self._show_reasoning

    def set_refresh_button_text(self, text: str) -> None:
        """Меняет текст кнопки обновления моделей (⏹ Стоп / 🔄 / ⏳).

        Публичная замена прямому `refresh_models_btn.setText(...)` из
        MainWindow (индикация процесса проверки поддержки tools).
        """
        self.refresh_models_btn.setText(text)

    @property
    def router(self) -> ModelRouter:
        return self._router

    def set_project_root(self, project_root: str):
        self.project_root = project_root
        self._project_scanned = False
        self._project_scan_root = None
        self._rag_scope = None

        # полный сброс пайплайна вместо ручного обнуления
        self.reset_pipeline()

        # [9.4] Профиль «Обучение» не использует пайплайн — не триггерим загрузку.
        if project_root and (self._profile is None or self._profile.id != "learning"):
            QTimer.singleShot(1500, self._ensure_orchestrator)
        self._update_pipeline_status()

    def get_settings(self) -> dict:
        mode_map = {0: "chat", 1: "plan", 2: "agent"}
        return {
            "model": self.get_current_model(),
            "mode": mode_map.get(self.mode_combo.currentIndex(), "chat"),
            "show_reasoning": self._show_reasoning,
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
        self._show_reasoning = bool(settings.get("show_reasoning", True))

    def export_chat(self, parent: QWidget | None = None) -> None:
        """[9.6.3] Открывает диалог экспорта текущего чата."""
        from ui.dialogs.export_chat_dialog import ExportChatDialog

        if not self._messages:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "Экспорт чата", "Чат пуст — нечего экспортировать.")
            return

        profile_name = self._profile.name if self._profile else ""
        dialog = ExportChatDialog(
            messages=list(self._messages),
            title="OlliDesk — чат",
            model=self.get_current_model(),
            mode=self._current_mode().value,
            profile=profile_name,
            project=self._project_name(),
            default_filename=f"chat_{self._project_name() or 'ollidesk'}.md",
            parent=parent or self,
        )
        dialog.exec()

    def set_project_open(self, is_open: bool):
        """[9.6.2] Placeholder обновляется через `_update_command_placeholder`,
        но при отсутствии проекта — стандартный.
        """
        if not is_open:
            self.input_edit.setPlaceholderText(
                "Введите сообщение... (@web — поиск, @file/@dir/@path — прикрепить)"
            )
            self.status_label.setText("Проект не открыт (RAG недоступен)")
        else:
            self.status_label.setText("Готов к работе")
            self._update_command_placeholder()
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
            idx = self.model_combo.count()
            self.model_combo.addItem(display, model_name)
            self.model_combo.setItemData(
                idx,
                self._build_registry_model_tooltip(info),
                Qt.ItemDataRole.ToolTipRole,
            )

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

    def _update_model_selector_visibility(self) -> None:
        """Скрывает выбор Ollama-модели, если Ollama не активна в конфиге.

        Назначение: когда Ollama выключена (не в `cfg.backends.enabled`),
        combo моделей Ollama, кнопка «🔄» (проверка tools) и иконка статуса
        модели бесполезны — все записи в combo неактивны, `_start_model_check`
        сразу отказывает. Оставлять их на виду — источник недоумения.

        Безопасно вызывается в любой момент: если UI ещё не создан
        (`model_combo` отсутствует), тихо выходит.
        """
        if not hasattr(self, "model_combo"):
            return
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            ollama_enabled = "ollama" in cfg.backends.enabled
        except Exception:
            ollama_enabled = False
        for widget in (
            getattr(self, "model_label", None),
            self.model_combo,
            self.refresh_models_btn,
            self.model_status_label,
        ):
            if widget is not None:
                widget.setVisible(ollama_enabled)

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
            idx = self.role_combo.count()
            self.role_combo.addItem(f"{role.icon} {role.name}", role.id)
            # Per-item tooltip: описание роли из её frontmatter.
            # Для default — «Универсальный AI-ассистент …»,
            # для python_dev — «Senior Python разработчик (FastAPI, Django, Pydantic)» и т.д.
            tooltip = role.description.strip() if role.description else ""
            if not tooltip and role.id == "default":
                tooltip = "Универсальный ассистент — подходит для большинства задач."
            if tooltip:
                self.role_combo.setItemData(idx, tooltip, Qt.ItemDataRole.ToolTipRole)

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
                return text[len(prefix) :]
        return text or "llama3.2"

    @Slot()
    def _on_web_finish(self):
        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None
        self._show_activity("✅ Готово")
        self.status_label.setText("🌐 Поиск завершён")

    @Slot(str)
    def _on_web_error(self, error_msg: str):
        self._add_message("system", f"❌ {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка поиска")

    def set_session_id(self, session_id: str):
        self._session_id = session_id

    def load_history(self, messages: list[ChatMessage]):
        """[9.10] Загрузка истории с одним проходом сворачивания в конце.

        0.1.3c: рендерим не только content, но и reasoning/tool_calls/
        tool_results из `msg.parts`. Для старых сообщений без parts
        (`content`-only) остаётся текстовый рендер — обратная совместимость
        через `ChatMessage.parts`, собранный в `model_validator`.
        """
        self._clear_chat(keep_session=True)
        self._suppress_collapse = True
        try:
            for msg in messages:
                if msg.role == "assistant" and msg.parts:
                    widget = self._render_assistant_parts(msg)
                    self.scroll_layout.insertWidget(
                        self.scroll_layout.count() - 1, widget
                    )
                    self._update_bubble_width(widget)
                else:
                    self._add_message(msg.role, msg.content)
                self._messages.append(msg)
        finally:
            self._suppress_collapse = False
        # Один проход после загрузки всей истории.
        self._maybe_collapse_old_messages()

    def keyPressEvent(self, event):
        if self.input_edit.hasFocus():
            key = event.key()
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                pass
        super().keyPressEvent(event)

    def set_search_cache(self, cache):
        self.search_cache = cache

    def _resolve_research_mode(
        self,
        question: str,
        *,
        file_count: int,
        total_bytes: int,
    ) -> dict:
        """Решает, какой флоу использовать для вопроса (D8).

        light (≤100 файлов / ≤300 КБ): 3 итерации, без фаз/гипотез —
        маленький проект не должен гоняться через full research.

        deep (>500 файлов): research с фазами и гипотезами, 5 итераций.

        medium (остальное): research без фаз, 8 итераций.
        """
        light = file_count <= 100 and total_bytes <= 300_000
        if light:
            return {
                "research": False,
                "max_iters": 3,
                "split_thoughts": False,
                "hypotheses": False,
                "phases": "off",
            }
        deep = file_count > 500
        return {
            "research": True,
            "max_iters": 5 if deep else 8,
            "split_thoughts": deep,
            "hypotheses": deep,
            "phases": "full" if deep else "light",
        }

    # ------------------------------------------------------------------
    # P3: Orchestrator lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Вызывается MainWindow.closeEvent — освобождает ресурсы."""
        if self._orchestrator is not None:
            with contextlib.suppress(Exception):
                self._orchestrator = None
        if self._model_pool is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._model_pool.aclose())
                loop.close()
            except Exception:
                pass
            self._model_pool = None

    # ------------------------------------------------------------------
    # [9.3] Селекторы Planner/Analyst
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_planner_changed(self, model_id: str) -> None:
        """Пользователь сменил planner — пересоздаём пайплайн."""
        logger.info(f"Pipeline: planner={model_id or '(авто)'} — reset_pipeline")
        self.reset_pipeline()
        self._update_pipeline_status()

    @Slot(str)
    def _on_analyst_changed(self, model_id: str) -> None:
        """Пользователь сменил analyst — пересоздаём пайплайн."""
        logger.info(f"Pipeline: analyst={model_id or '(авто)'} — reset_pipeline")
        self.reset_pipeline()
        self._update_pipeline_status()

    # ------------------------------------------------------------------
    # [9.4] Профили использования
    # ------------------------------------------------------------------

    def _apply_initial_profile(self) -> None:
        """Применяет профиль из конфига при старте приложения."""
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            profile = get_profile_by_id(cfg.profiles, cfg.active_profile)
            if profile is None:
                profile = get_default_profile(cfg.profiles)
            self._apply_profile(profile, emit_signal=False)
        except Exception as e:
            logger.warning(f"Не удалось применить стартовый профиль: {e}")

    def _apply_profile(
        self,
        profile: ProfileConfig,
        *,
        emit_signal: bool = True,
    ) -> None:
        """[9.4] Применяет профиль к ChatPanel.

        Устанавливает режим, tool_policies, флаг RAG, show_reasoning,
        лимиты итераций. НЕ трогает `_pipeline_selectors` — planner/analyst
        выбираются отдельно. Системный промпт профиля подмешивается
        в `extra_rules` при каждом запросе (см. `_project_rules`).
        """
        from core.config import ToolPolicy as _ToolPolicy

        self._profile = profile
        logger.info(
            f"Профиль: {profile.id} ({profile.name}), "
            f"mode={profile.agent_mode}, rag={profile.rag_enabled}, "
            f"reasoning={profile.show_reasoning}"
        )

        # 1. Режим.
        mode_map = {"chat": 0, "plan": 1, "agent": 2}
        idx = mode_map.get(profile.agent_mode, 2)
        if 0 <= idx < self.mode_combo.count() and self.mode_combo.currentIndex() != idx:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(idx)
            self.mode_combo.blockSignals(False)
            # Синхронизируем tooltip, т.к. blockSignals подавил
            # `currentIndexChanged` → `_on_mode_changed` не вызвался.
            self.mode_combo.setToolTip(_MODE_DESCRIPTIONS.get(idx, ""))

        # 2. show_reasoning.
        self._show_reasoning = bool(profile.show_reasoning)

        # 3. Tool policies (высший приоритет в _build_registry).
        for tool_name, policy_str in profile.tool_policies.model_dump().items():
            try:
                policy = _ToolPolicy(policy_str)
            except ValueError:
                logger.warning(
                    f"Профиль {profile.id}: неизвестная политика {policy_str!r} для {tool_name} — пропускаю"
                )
                continue
            self.set_tool_policy(tool_name, policy)

        # 4. RAG.
        self._profile_rag_enabled = bool(profile.rag_enabled and self.vector_store is not None)

        # 5. Лимиты итераций.
        self._profile_agent_max_iters = int(profile.agent_max_iterations)
        self._profile_chat_max_iters = int(profile.chat_max_iterations)

        # 5.5 [9.4] Рекомендованная модель — визуальная подсказка в селекторах.
        if self._pipeline_selectors is not None:
            self._pipeline_selectors.set_recommended_model(profile.recommended_model, profile.name)

        # 6. Кнопка «Возврат в Разработку» — видна только для не-development.
        if hasattr(self, "back_to_development_btn"):
            self.back_to_development_btn.setVisible(profile.id != "development")

        # 7. Синхронизация UI-индикаторов.
        if emit_signal:
            self.profile_changed.emit(profile.id)

    @Slot(object)
    def _on_profile_changed(self, profile) -> None:
        """Пользователь выбрал другой профиль в селекторе."""
        if not isinstance(profile, ProfileConfig):
            return
        self._apply_profile(profile, emit_signal=True)
        # Профиль влияет на pipeline (какие модели), пересобираем.
        self.reset_pipeline()
        self.status_label.setText(f"{profile.icon} Профиль: {profile.name}")
        logger.info(f"Профиль сменён: {profile.id} → reset_pipeline")

    @Slot()
    def _back_to_development(self) -> None:
        """[9.4] Возврат в профиль «Разработка» одним кликом."""
        if self._profiles_selector is None:
            return
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            dev = get_profile_by_id(cfg.profiles, "development")
            if dev is None:
                return
            self._profiles_selector.set_profile("development")
            self._on_profile_changed(dev)
        except Exception as e:
            logger.warning(f"Не удалось вернуться в development: {e}")

    def refresh_profiles_selector(self) -> None:
        """Перечитывает список профилей (после редактирования в диалоге)."""
        if self._profiles_selector is None:
            return
        try:
            from core.config import load_config as _load_cfg

            self._profiles_selector.set_config(_load_cfg())
        except Exception as e:
            logger.warning(f"Не удалось обновить ProfilesSelector: {e}")

    def get_active_profile(self) -> ProfileConfig | None:
        """Текущий активный профиль (для MainWindow)."""
        return self._profile

    def refresh_pipeline_selectors(self) -> None:
        """Перечитывает список моделей в селекторах (после SLMModelsDialog)."""
        if self._pipeline_selectors is None:
            return
        try:
            from core.config import load_config as _load_cfg_for_pipeline

            self._pipeline_selectors._config = _load_cfg_for_pipeline()  # noqa: SLF001
            self._pipeline_selectors.refresh_models()
        except Exception as e:
            logger.warning(f"Не удалось обновить PipelineSelectors: {e}")

    # ------------------------------------------------------------------
    # [9.7] Приборная панель — инжектится из MainWindow
    # ------------------------------------------------------------------

    def set_dashboard(self, dashboard) -> None:
        """MainWindow передаёт StatusDashboard после создания статус-бара."""
        self._dashboard = dashboard
        self._sync_dashboard()

    def _sync_dashboard(self) -> None:
        """Синхронизирует бэкенд/pipeline/tok/s с приборной панелью."""
        if self._dashboard is None:
            return
        # Бэкенд.
        backend = self._router.active_backend
        if backend == BackendType.OLLAMA:
            self._dashboard.set_backend("ollama", "Ollama", self.get_current_model())
        elif backend == BackendType.LOCAL_SLM:
            self._dashboard.set_backend("slm", "SLM", self._slm_model_name or "локальная")
        elif backend == BackendType.RULE_BASED:
            self._dashboard.set_backend("rule", "Резерв", "без LLM")
        else:
            self._dashboard.set_backend("none", "Нет", "—")

        # Pipeline.
        if self._orchestrator is not None and self._model_pool is not None:
            try:
                ps = self._model_pool.spec(Role.PLANNER)
                an = self._model_pool.spec(Role.ANALYST)
                self._dashboard.set_pipeline(
                    "⚡",
                    f"{ps.model_id} + {an.model_id}",
                    f"planner={ps.model_id} (GPU, n_ctx={ps.n_ctx})\n"
                    f"analyst={an.model_id} (CPU, n_ctx={an.n_ctx})",
                )
            except Exception:
                self._dashboard.set_pipeline("⚡", "готов")
        elif self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            self._dashboard.set_pipeline_loading("загрузка…")
        elif self._pool_load_failed:
            self._dashboard.set_pipeline("⚠️", "недоступен (fallback)")
        elif not self.project_root:
            self._dashboard.set_pipeline("💤", "не активирован")
        else:
            self._dashboard.set_pipeline("⏸", "ожидание")

    @staticmethod
    def _build_registry_model_tooltip(info: ModelInfo) -> str:
        """HTML-tooltip для пункта главного селектора моделей (Ollama)."""
        lines = [f"<b>{info.name}</b>", ""]
        if info.description:
            lines.append(info.description)
            lines.append("")
        if info.recommended_modes:
            lines.append(f"Рекомендуемые режимы: {', '.join(info.recommended_modes)}")
        if info.supports_tools is True:
            lines.append("Инструменты: ✅ поддерживает")
        elif info.supports_tools is False:
            lines.append("Инструменты: ❌ только чат")
        else:
            lines.append("Инструменты: ❓ не проверено")
        lines.append("Локально: ✅" if info.is_local else "Локально: ⬇️ не скачана")
        return "<br>".join(lines)


# ---
