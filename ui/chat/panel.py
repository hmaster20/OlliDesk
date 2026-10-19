"""Панель чата с поддержкой режимов Chat / Plan / Agent."""

import asyncio
import contextlib
import json
import os
import re

os.environ.setdefault("LLAMA_LOG_LEVEL", "WARN")
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
from agents.context_loader import load_agents_context
from agents.download_slm import get_model_paths
from agents.hypotheses import HypothesisStore
from agents.investigation import is_research_question
from agents.local_llm_client import LocalLLMClient
from agents.model_pool import ModelPool, Role
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import ChatMessage, OllamaClient
from agents.orchestrator import Orchestrator, RunResult
from agents.rule_based_fallback import RuleBasedFallback
from agents.tool_registry import ToolRegistry
from core.budget import ApproxCounter
from core.config import AgentMode, ToolPolicy, load_config
from core.context_budget import attached_char_budget, rag_char_budget, truncate_to_chars
from core.model_registry import ModelRegistry
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager
from fs.project_map import detect_query_scope, list_project_files, render_project_map
from tools.attached_files import AttachedFileStore
from tools.weather import get_weather
from ui.chat.message_item import (
    ChatActivityItem,
    ChatDiagramItem,
    ChatHypothesisItem,
    ChatMessageItem,
    ChatSourcesItem,
)
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


class ChatPanel(QWidget):
    """Панель чата с сообщениями, вводом и управлением."""

    mode_changed = Signal(str)
    agent_panel_toggle = Signal(bool)
    refresh_models_requested = Signal()
    settings_changed = Signal(dict)
    backend_status_changed = Signal(str)
    pipeline_status_changed = Signal(str)

    directory_attach_requested = Signal(str)
    source_activated = Signal(str, int)

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

        self._setup_ui()
        self._update_pipeline_status()
        QTimer.singleShot(100, self._detect_backends)

    # ---------------------------------------------------------
    # Детект и загрузка бэкендов
    # ---------------------------------------------------------

    def reload_slm(self):
        """Перезагружает SLM с сохранённой конфигурацией — без автовыбора.

        Вызывается после явного выбора модели пользователем в диалоге SLM.
        Автовыбор должен быть пропущен, иначе он затрёт выбор (см. баг:
        3B q4_k_m принудительно возвращался вместо выбранной 7B/1.5B/q6_k).

        Также сбрасывает двухмодельный пайплайн — если пользователь
        изменил planner/analyst в SLMModelsDialog, при следующем
        исследовательском вопросе пайплайн соберётся уже с новыми моделями.
        """
        self._slm_load_generation += 1
        self._slm_model_name = ""

        self._router.set_local_slm(None)
        self._router.set_slm_available(False)
        self._slm_client = None
        self._slm_loading = False

        logger.info(
            f"reload_slm: generation → {self._slm_load_generation}, "
            f"модель будет перечитана из конфига (автовыбор пропущен)"
        )
        self.reset_pipeline()

        self.status_label.setText("⏳ Перезагрузка локальной модели...")
        QTimer.singleShot(50, lambda: self._detect_and_load_slm(skip_auto_select=True))

        # ИСПРАВЛЕНИЕ: запускаем двухмодельный пайплайн после ручной смены моделей
        if self.project_root:
            QTimer.singleShot(1500, self._ensure_orchestrator)

    def _detect_and_load_slm(self, skip_auto_select: bool = False):
        """Автовыбор модели + автооптимизация параметров, затем загрузка.

        Args:
            skip_auto_select: True — не запускать автовыбор модели, использовать
                уже записанный в конфиге ``slm.selected_model``. Нужно после
                явного выбора модели пользователем в ``SLMModelsDialog``.
        """
        from agents.slm_optimizer import optimize
        from agents.slm_selector import select_best_model

        try:
            cfg = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для автовыбора SLM: {e}")
            cfg = None

        if skip_auto_select:
            logger.info(
                "SLM: автовыбор пропущен (после ручного переключения модели). "
                "Загружается selected_model из конфига."
            )
        elif cfg is not None and cfg.slm.auto_select_model:
            chosen = select_best_model(prefer_vram=cfg.slm.prefer_vram_fit)
            if chosen is not None and chosen.id != cfg.slm.selected_model:
                logger.info(f"Автовыбор SLM: '{cfg.slm.selected_model}' → '{chosen.id}'")
                cfg.slm.selected_model = chosen.id
                try:
                    from core.config import save_config

                    save_config(cfg)
                except Exception as e:
                    logger.warning(f"Не удалось сохранить автовыбор в конфиг: {e}")
            else:
                logger.info(
                    f"SLM: автовыбор подтвердил сохранённый выбор (selected_model='{cfg.slm.selected_model}')"
                )
        elif cfg is not None:
            logger.info(
                f"SLM: используется сохранённый выбор "
                f"(selected_model='{cfg.slm.selected_model}', "
                f"auto_select_model=false)"
            )
        else:
            logger.info(
                "SLM: конфиг недоступен, автовыбор не выполнялся "
                "(будет использована модель из get_model_paths)"
            )

        # 2. Загрузка (при auto_tune — с оптимизацией).
        chat_path, embed_path = get_model_paths()
        if not chat_path:
            if self._config_slm_enabled():
                self._download_and_load_slm()
            return

        params = None
        try:
            if cfg is not None and cfg.slm.auto_tune:
                params = optimize(chat_path, target_n_ctx=cfg.slm.target_n_ctx)
                logger.info(f"Автопараметры SLM:\n{params.to_json()}")
        except Exception as e:
            logger.warning(f"SLM-оптимизатор упал, загружаю с параметрами из конфига: {e}")
            params = None

        self._load_slm_async(chat_path, embed_path, params=params)

    def _config_slm_enabled(self) -> bool:
        try:
            from core.config import load_config

            cfg = load_config()
            return cfg.slm.enabled
        except Exception:
            return True

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

    def _apply_backend_config(self) -> None:
        try:
            from core.config import load_config

            cfg = load_config()
            self._router.set_enabled_backends(list(cfg.backends.enabled))
            self._router.set_priority(list(cfg.backends.priority))
            self._router.set_embed_backend(cfg.backends.embed_backend)
        except Exception as e:
            logger.warning(f"Не удалось применить настройки бэкендов: {e}")
        self._emit_backend_status()

    def _detect_backends(self):
        """Определяет доступность бэкендов, уважая whitelist.

        Ollama проверяется только если пользователь его явно включил в
        настройках. Иначе приложение не отправляет ни одного HTTP-запроса
        на localhost:11434.
        """
        ollama_enabled = False
        try:
            from core.config import load_config

            cfg = load_config()
            ollama_enabled = "ollama" in cfg.backends.enabled
        except Exception:
            pass

        if ollama_enabled:
            self._check_ollama_async()
        else:
            logger.info("Ollama не в enabled-списке — пропускаю проверку и автоактивацию")
            self._router.set_ollama_available(False)

        self._detect_and_load_slm()

    def _active_backend_label(self) -> str:
        backend = self._router.active_backend
        if backend == BackendType.OLLAMA:
            return f"🌐 Ollama · {self.get_current_model()}"
        if backend == BackendType.LOCAL_SLM:
            name = self._slm_model_name or "локальная модель"
            return f"🖥 Локально · {name}"
        if backend == BackendType.RULE_BASED:
            return "⚠️ Резерв (без LLM)"
        return "⛔ Нет доступного бэкенда"

    def _emit_backend_status(self) -> None:
        self.backend_status_changed.emit(self._active_backend_label())

    def _check_ollama_async(self):
        self._ollama_check_thread = _OllamaCheckThread(self._ollama_client)
        self._ollama_check_thread.result.connect(self._on_ollama_check_result)
        self._ollama_check_thread.start()

    @Slot(bool)
    def _on_ollama_check_result(self, available: bool):
        if not available:
            self._router.set_ollama_available(False)
            return
        # Ollama ответил, но это не значит, что он разрешён — router сам решит.
        self._router.set_ollama_available(True)
        self._emit_backend_status()

    def _on_slm_loaded(self, client, generation: int | None = None):
        if generation is not None and generation != self._slm_load_generation:
            logger.info(
                f"Отбрасываю устаревшую загрузку SLM "
                f"(gen={generation}, актуальная={self._slm_load_generation}): "
                f"{Path(client.model_path).name}"
            )
            return

        self._slm_client = client
        if not self._slm_model_name:
            self._slm_model_name = Path(client.model_path).stem
        self._router.set_local_slm(client)
        self._router.set_slm_available(True)
        self._slm_loading = False
        logger.info(f"Локальная SLM загружена и доступна как fallback: {self._slm_model_name}")
        if self._router.active_backend == BackendType.LOCAL_SLM:
            self.status_label.setText(f"✅ Локальная модель загружена: {self._slm_model_name}")
        self._emit_backend_status()
        self._update_pipeline_status()

    def _load_slm_async(self, chat_path: Path, embed_path: Path | None, params=None):
        if self._slm_loading or self._slm_client is not None:
            return

        generation = self._slm_load_generation

        # Параметры: params (авто) → конфиг (ручной режим) → дефолты.
        n_ctx, n_threads, n_batch, n_gpu_layers, max_tokens = 4096, 0, 512, 0, 1024
        try:
            from core.config import load_config

            slm = load_config().slm
            max_tokens = slm.max_tokens
            if params is None:
                n_ctx = slm.n_ctx
                n_threads = slm.n_threads
                n_batch = slm.n_batch
                if slm.auto_gpu_layers:
                    from agents.gpu_utils import detect_optimal_gpu_layers

                    n_gpu_layers = detect_optimal_gpu_layers(
                        model_path=chat_path,
                        preferred=slm.n_gpu_layers,
                        auto=True,
                    )
                else:
                    n_gpu_layers = slm.n_gpu_layers
            info = slm.get_selected_info()
            if info is not None:
                self._slm_model_name = info.display_name
        except Exception as e:
            logger.debug(f"Не удалось прочитать SLM-параметры из конфига: {e}")

        self._slm_loading = True
        self.status_label.setText(f"⏳ Загрузка {self._slm_model_name or chat_path.name}...")

        self._slm_load_thread = _SLMLoadThread(
            chat_path,
            embed_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_batch=n_batch,
            n_gpu_layers=n_gpu_layers,
            max_tokens=max_tokens,
            params=params,
        )
        self._slm_load_thread.finished.connect(
            lambda client, gen=generation: self._on_slm_loaded(client, gen)
        )
        self._slm_load_thread.error.connect(lambda err, gen=generation: self._on_slm_error(err, gen))
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

        if info.kind != "chat":
            logger.error(
                f"Активная SLM-модель '{info.id}' имеет kind='{info.kind}', "
                f"а для чата нужна kind='chat'. Загрузка отменена."
            )
            self.status_label.setText(f"⚠️ Модель '{info.display_name}' не подходит для чата.")
            return

        generation = self._slm_load_generation
        self._slm_loading = True
        self.status_label.setText(f"⏳ Скачивание {info.display_name} (~{info.size_mb} МБ)...")

        self._slm_download_thread = _SLMDownloadThread(
            model_repo=info.repo,
            model_file=info.file,
            embed_repo=slm.embed_model_repo,
            embed_file=slm.embed_model_file,
        )
        self._slm_download_thread.progress.connect(
            lambda msg, gen=generation: self._on_slm_progress(msg, gen)
        )
        self._slm_download_thread.finished.connect(
            lambda chat, embed, gen=generation: self._on_slm_downloaded(chat, embed, gen)
        )
        self._slm_download_thread.error.connect(lambda err, gen=generation: self._on_slm_error(err, gen))
        self._slm_download_thread.start()

    def _on_slm_progress(self, message: str, generation: int | None = None):
        if generation is not None and generation != self._slm_load_generation:
            return
        self.status_label.setText(f"⏳ {message}")

    def _on_slm_error(self, error_msg: str, generation: int | None = None):
        if generation is not None and generation != self._slm_load_generation:
            logger.info(f"Отбрасываю ошибку устаревшей загрузки SLM (gen={generation}): {error_msg}")
            return
        self._slm_loading = False
        logger.error(f"Ошибка загрузки SLM: {error_msg}")
        self.status_label.setText(f"⚠️ SLM недоступна: {error_msg}")

    def _on_slm_downloaded(self, chat_path: str, embed_path: str, generation: int | None = None):
        if generation is not None and generation != self._slm_load_generation:
            logger.info(f"Отбрасываю устаревшее скачивание SLM (gen={generation})")
            return
        self._slm_loading = False
        self._load_slm_async(
            Path(chat_path),
            Path(embed_path) if embed_path else None,
        )

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------

    def _add_message(self, role: str, content: str, mode_name: str = "") -> ChatMessageItem:
        widget = ChatMessageItem(role, content, mode_name=mode_name)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        self._update_bubble_width(widget)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return widget

    def _show_activity(self, text: str) -> None:
        if not text:
            return
        item = ChatActivityItem(text)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _show_sources(self, sources: list[tuple[str, int, int]]) -> None:
        if not sources:
            return
        item = ChatSourcesItem(sources)
        item.source_activated.connect(self._on_source_activated)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)
        QTimer.singleShot(0, self._scroll_to_bottom)

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
        return read_project_rules(self.project_root)

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
        """Старая дорожка (не-research): стриминг в обычное сообщение."""
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
        self.model_status_label.setStyleSheet("font-size: 12px; padding: 4px 8px; color: gray;")
        top_bar.addWidget(self.model_status_label)

        # Постоянный индикатор двухмодельного пайплайна (planner 3B + analyst 7B).
        # Показывает: 💤 не активирован / ⏳ грузится / ⚡ готов / ⚠️ ошибка.
        self.pipeline_status_label = QLabel("")
        self.pipeline_status_label.setStyleSheet(
            "font-size: 11px; padding: 2px 6px; color: #888;"
            " background: rgba(128,128,128,0.08); border-radius: 3px;"
        )
        top_bar.addWidget(self.pipeline_status_label)

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_bar.addWidget(self.model_combo)

        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("font-size: 13px; padding: 4px; margin-left: 8px;")
        top_bar.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["💬 Chat", "📋 Plan", "🤖 Agent"])
        for index, description in _MODE_DESCRIPTIONS.items():
            self.mode_combo.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setToolTip(_MODE_DESCRIPTIONS[0])
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
        self.status_label.setStyleSheet(
            "font-size: 12px; color: gray; padding: 4px 8px; background: #f5f5f5; border-radius: 4px;"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(
            "Введите сообщение... (CTRL+Enter — отправить, @web — поиск, "
            "@file — прикрепить файл, @dir — прикрепить папку)"
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

                pattern = r"^(@web|@dir|@file|@path)(?=\s|$)"
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

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
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
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._messages.clear()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
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

        # Очищаем активный store — в следующий запрос гипотезы не утекут.
        # Визуальные виджеты (`_hypothesis_widgets`) НЕ трогаем — это архив.
        self._hypotheses.clear()

        # Паспорт не строим.
        self._last_research_summary = None
        self._pending_final_answer = ""

        self._reset_send_button()
        self.status_label.setText("⏹ Остановлено пользователем")
        self._add_message(
            "system",
            "⏹ Генерация остановлена пользователем.\n"
            "Визуальный лог рассуждений сохранён выше для ознакомления, "
            "но НЕ будет учтён в следующем запросе.",
        )
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._research_mode_active = False
        self._research_cancelled = False
        self._current_thought_stream = None

    def _send_message(self):
        if self._generating:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
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
        self.status_label.setText("Поиск в кодовой базе...")
        self._rag_context = None
        self._rag_scope = self._query_scope(text)

        if self.vector_store:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=_RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
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
        self.status_label.setText("🤖 Агент думает...")
        self._rag_scope = self._query_scope(text)

        if self.vector_store:
            self._show_activity("🔍 Сканирую проект…")
            if self._rag_scope:
                self._show_activity(f"🎯 Фокус на каталоге: {self._rag_scope[0]}/")
            self._rag_thread = RagSearchThread(
                self.vector_store,
                text,
                n_results=_RAG_N_RESULTS,
                file_filter=self._rag_scope[1] if self._rag_scope else None,
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

    def _reset_research_state(self) -> None:
        """Сбрасывает state текущего research-прогона перед новым запросом."""
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._current_thought_stream = None

    def _do_chat(self, text: str):
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
                try:
                    total_bytes += (Path(self.project_root) / rel).stat().st_size
                except OSError:
                    pass
        else:
            total_bytes = 0  # не используется при file_count > 100

        plan = self._resolve_research_mode(
            text,
            file_count=file_count,
            total_bytes=total_bytes,
        )
        is_research = plan["research"] and is_research_question(text)
        max_iters = plan["max_iters"]

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
            search_cache=cast(Any, raw_cache),
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

        self._connect_agent_thread_signals()

    def _do_agent(self, text: str, mode: AgentMode, rag_context: str):
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
                try:
                    total_bytes += (Path(self.project_root) / rel).stat().st_size
                except OSError:
                    pass
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
            max_iters = plan["max_iters"]
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
            search_cache=cast(Any, raw_cache),
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
        self._agent_thread.start()

    # ---------------------------------------------------------
    # Слоты research-режима
    # ---------------------------------------------------------

    @Slot(bool)
    def _on_research_mode_detected(self, is_research: bool) -> None:
        """Запоминает, research-ли этот прогон.

        Если да — `chunk_received` для отображения не используется;
        серый поток идёт через `thought_chunk`, а финал — через
        `final_chunk` (в буфер паспорта).
        """
        self._research_mode_active = is_research
        if is_research:
            logger.info("Research-режим активен: серый поток мыслей включён")

    @Slot(str)
    def _on_thought_chunk(self, token: str) -> None:
        """Токен мысли — в серый поток.

        [M4] looks_like_ascii_junk рассчитан на блоки ≥20 символов, поэтому
        на каждом отдельном токене не срабатывает. Проверяем накопленный
        буфер (до 500 символов): если в нём уже накопилось ≥3 «каракулей» —
        показываем вместо мыслей строку «резервный сбор данных».
        """
        from agents.tool_grammar import looks_like_ascii_junk

        if self._current_thought_stream is None:
            self._current_thought_stream = ChatThoughtStream()
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, self._current_thought_stream)

        self._current_thought_stream.append_thought_token(token)

        # Периодически проверяем накопленный буфер (после ~5 токенов
        # по 4+ символа он станет ≥20 и детектор начнёт работать).
        buf = self._current_thought_stream._current_thought_buffer
        if len(buf) >= 40 and looks_like_ascii_junk(buf):
            # Сворачиваем «мысли» и оставляем только пометку.
            self._current_thought_stream._current_thought_buffer = ""
            self._current_thought_stream.append_nudge(
                "⚙️ Резервный сбор данных (модель не вызвала инструмент)"
            )

        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot()
    def _on_final_answer_started(self) -> None:
        """Начался финальный ответ — сбрасываем буфер паспорта."""
        self._pending_final_answer = ""

    @Slot(str)
    def _on_final_chunk(self, token: str) -> None:
        """Токен финального ответа — в буфер паспорта, НЕ в ленту."""
        self._pending_final_answer += token

    @Slot(object)
    def _on_research_summary_ready(self, summary: dict) -> None:
        """Запоминает сводку research-прогона (до finish_received)."""
        self._last_research_summary = summary
        logger.debug(
            "Research-сводка: файлов прочитано "
            f"{len(summary.get('files_read') or [])}, "
            f"гипотез {len(summary.get('hypotheses') or [])}"
        )

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
                logger.debug(
                    f"chat_auto_tools: fallback на встроенный список: {e}"
                )
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

    @Slot(str, str, bool)
    def _on_tool_executed(self, name: str, content: str, is_error: bool):
        """Обрабатывает завершение инструмента.

        * В research-режиме — пишет в серый поток.
        * Для тулов гипотез — обновляет/создаёт виджет `ChatHypothesisItem`
          (вместо стандартного сообщения).
        * В не-research режиме — как раньше.
        """
        # Записываем в серый поток (кроме случая, когда он не создан —
        # тогда это не research и поток не нужен).
        if self._current_thought_stream is not None:
            if name in ("record_hypothesis", "update_hypothesis"):
                pass  # гипотезы обработаем отдельно ниже
            else:
                preview = (content or "").strip().replace("\n", " ")[:200]
                self._current_thought_stream.append_result(name, ok=not is_error, preview=preview)

        if (
            name in ("record_hypothesis", "update_hypothesis")
            and not is_error
            and self._update_hypothesis_widget(content)
        ):
            return

        if self._current_thought_stream is not None:
            # В research-режиме обычные tool-сообщения в ленту НЕ пишем —
            # всё уже видно в сером потоке, не засоряем.
            if not self._research_mode_active:
                self._add_tool_message(name, content, is_error)
            return

        self._add_tool_message(name, content, is_error)

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

        QTimer.singleShot(0, self._scroll_to_bottom)
        return True

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
            "read_attached_file": "📄 Дочитывание файла",
            "record_hypothesis": "💡 Запись гипотезы",
            "update_hypothesis": "🔬 Обновление гипотезы",
            "list_hypotheses": "📋 Список гипотез",
            "run_shell_command": "⚡ Shell-команда",
        }
        label = tool_labels.get(name, f"🛠️ {name}")
        activity = f"{label}: {arg_summary}" if arg_summary else label
        self.status_label.setText(activity)

        # В серый поток пишем компактно; в research-режиме — это единственный
        # способ увидеть действие в UI.
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_action(name, args)

        # Activity-строки в ленте показываем только в не-research.
        if not self._research_mode_active:
            self._show_activity(activity)

        self._last_assistant_widget = None
        self._current_assistant_content = ""

    @Slot(str)
    def _on_status_event(self, text: str):
        """Статус — в серый поток (как нудж/фаза) и в статус-бар.

        В research-режиме в ленту не пишем (всё видно в сером потоке).
        """
        if self._current_thought_stream is not None and text:
            # Нуджи приходят как «🔁 …», фазы — как «📌 …».
            if text.startswith("🔁") or text.startswith("🪞"):
                self._current_thought_stream.append_nudge(text)
            elif text.startswith("📌"):
                self._current_thought_stream.append_phase(text)
        if not self._research_mode_active:
            self._show_activity(text)
        self.status_label.setText(text)

    @Slot()
    def _on_finish(self):
        self._send_finished()

    @Slot(str)
    def _on_chat_error(self, error_msg: str):
        self._add_message("system", f"❌ Ошибка: {error_msg}")
        self._send_finished()

    @Slot(str)
    def _on_agent_finish(self, result: str):
        # --- Research-режим: паспорт с выводами, без обычного пузыря ---
        if self._research_mode_active and not self._research_cancelled:
            summary = self._last_research_summary
            if summary:
                summary["final_answer"] = self._pending_final_answer
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

            self._reset_send_button()
            self._current_assistant_content = ""
            self._current_thinking_content = ""
            self._last_assistant_widget = None
            self._current_thought_stream = None
            self._pending_final_answer = ""
            self._last_research_summary = None
            self._research_mode_active = False
            return

        # --- Обычный режим: как раньше ---
        if result and result != "Отменено":
            assistant_message = ChatMessage(role="assistant", content=result)
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)
            self._show_activity("✅ Готово")

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None
        self._current_thought_stream = None
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._research_mode_active = False

    @staticmethod
    def _should_show_passport(summary: dict | None) -> bool:
        """Показывать ли паспорт по этой сводке.

        Правило: показываем, если прочитано ≥2 файла или есть хотя бы одна
        гипотеза. Пустой паспорт (0 файлов, 0 гипотез, без выводов) — шум.
        """
        if not summary:
            return False
        files = summary.get("files_read") or []
        hypotheses = summary.get("hypotheses") or []
        final_answer = (summary.get("final_answer") or "").strip()
        return bool(len(files) >= 2 or hypotheses or final_answer)

    def _show_research_passport(self, summary: dict) -> None:
        """Вставляет виджет «Паспорт исследования» под финальным ответом."""
        try:
            widget = ChatResearchPassport(summary)
        except Exception as e:
            logger.warning(f"Не удалось построить паспорт исследования: {e}")
            return
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, widget)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _reset_send_button(self):
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

    def _send_finished(self):
        if self._current_assistant_content:
            assistant_message = ChatMessage(role="assistant", content=self._current_assistant_content)
            self._messages.append(assistant_message)
            if self._session_id:
                self.session_store.save_message(self._session_id, assistant_message)

        self._reset_send_button()
        self._current_assistant_content = ""
        self._current_thinking_content = ""
        self._last_assistant_widget = None

    @Slot(str)
    def _on_agent_error(self, error_msg: str):
        # Помечаем серый поток как прерванный, если он был.
        if self._current_thought_stream is not None:
            self._current_thought_stream.mark_interrupted(f"Ошибка: {error_msg[:120]}")
        self._add_message("system", f"❌ Ошибка агента: {error_msg}")
        self._reset_send_button()
        self.status_label.setText("❌ Ошибка")
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None

    @Slot()
    def _on_iteration_limit_exceeded(self):
        self._show_activity(
            "⏸ Достигнут лимит итераций. Нажмите «Продолжить», чтобы агент сформировал Final Answer"
        )
        self.status_label.setText("Достигнут лимит итераций — готово к продолжению")

    @Slot(bool)
    def _on_tools_status_changed(self, active: bool):
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

    def set_vector_store(self, vector_store):
        self.vector_store = vector_store

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

        if project_root:
            QTimer.singleShot(1500, self._ensure_orchestrator)
        self._update_pipeline_status()

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

    def _ensure_orchestrator(self) -> None:
        """Ленивая загрузка двух моделей + Orchestrator (в фоне).

        Вызывается при открытии проекта (с задержкой), из меню
        «View → Предзагрузить 2-model pipeline» и при research-вопросе.
        Если уже загружен/грузится — no-op. Ошибка →
        ``_pool_load_failed=True``, orchestrator остаётся None.

        Индикатор `pipeline_status_label` показывает состояние в UI:
        ⏳ → ⚡ или ⚠️. Таймаут per-role (90 с) гарантирует, что UI не
        зависнет, даже если llama.cpp ушёл в бесконечный OOM-retry.
        """
        if self._orchestrator is not None:
            return
        if self._pool_load_failed:
            return
        if self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            return

        self._pool_load_thread = _ModelPoolLoadThread()
        self._pool_load_thread.progress.connect(self._on_pool_progress)
        self._pool_load_thread.finished.connect(self._on_pool_loaded)
        self._pool_load_thread.error.connect(self._on_pool_error)
        self._pool_load_thread.start()
        self.status_label.setText("⏳ Загрузка двух моделей (3B GPU + 7B CPU)…")
        self._update_pipeline_status()

    def reset_pipeline(self) -> None:
        """Сбрасывает двухмодельный пайплайн (после смены моделей в UI).

        Вызывается из `reload_slm()` и из MainWindow после закрытия
        SLMModelsDialog с изменениями. Следующий `_ensure_orchestrator()`
        пересоздаст пайплайн с новыми моделями из конфига.

        Также сбрасывает `_pool_load_failed`, чтобы «⚠️ недоступен» не
        блокировал повторную попытку после исправления конфига
        (например, скачивания недостающей модели).
        """
        if self._model_pool is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._model_pool.aclose())
                loop.close()
            except Exception:
                logger.debug("ModelPool.aclose failed", exc_info=True)

        self._model_pool = None
        self._orchestrator = None
        self._pool_load_failed = False
        self._update_pipeline_status()
        logger.info("SLM pipeline сброшен — будет пересоздан при следующем запросе")

    @Slot(object)
    def _on_pool_loaded(self, pool: ModelPool | None) -> None:
        if pool is None:
            self._on_pool_error("ModelPool вернул None")
            return

        intel_cache_dir: Path | None = None
        if self.project_root:
            intel_cache_dir = Path(self.project_root) / ".ollidesk" / "graph"
            intel_cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._model_pool = pool
            self._orchestrator = Orchestrator(
                pool=pool,
                intel_cache_dir=intel_cache_dir,
                vector_store=self.vector_store,
                counter=ApproxCounter(),
            )
            logger.info(f"Orchestrator готов: planner+analyst загружены, intel_cache={intel_cache_dir}")
            self.status_label.setText("✅ Двухмодельный пайплайн готов")
            self._update_pipeline_status()
        except Exception as e:
            logger.exception("Не удалось создать Orchestrator")
            self._on_pool_error(f"Orchestrator init: {e}")

    @Slot(str)
    def _on_pool_error(self, error_msg: str) -> None:
        logger.warning(f"ModelPool/Orchestrator недоступен: {error_msg}")
        self._pool_load_failed = True
        self._orchestrator = None
        self._model_pool = None
        self.status_label.setText(f"⚠️ Оркестратор недоступен (ReAct продолжит работу): {error_msg[:100]}")
        self._update_pipeline_status()

    @Slot(object)
    def _on_orchestrator_result(self, result: RunResult) -> None:
        """Обрабатывает полный RunResult: ответ + схема + evidence.

        Вызывается из AgentChatThread после `chunk_received` с полным
        текстом ответа (там уже создан виджет сообщения через _on_chunk).
        Здесь добавляем диаграмму и краткую сводку evidence, затем cleanup.
        """
        try:
            # Сохраняем assistant-сообщение в историю (иначе следующая
            # итерация не увидит ответ в messages_history).
            if result.answer:
                assistant_message = ChatMessage(role="assistant", content=result.answer)
                self._messages.append(assistant_message)
                if self._session_id:
                    self.session_store.save_message(self._session_id, assistant_message)

            # Диаграмма компонентов (ASCII + Mermaid toggle).
            if result.ascii_diagram or result.mermaid_diagram:
                diagram_item = ChatDiagramItem(
                    result.ascii_diagram or "",
                    result.mermaid_diagram or "",
                )
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, diagram_item)

            # Evidence pack — короткая строка, не мешает чтению.
            if result.evidence_ids:
                verify_mark = "✅" if result.verify_ok else "⚠️"
                critique_mark = "✅" if result.critique_ok else "⚠️"
                unsupported = result.unsupported_claims
                summary = (
                    f"📚 Источников: {len(result.evidence_ids)} · "
                    f"покрытие {result.coverage:.0%} · "
                    f"verify {verify_mark} · critique {critique_mark}"
                )
                if unsupported:
                    summary += f" · без доказательств: {unsupported}"
                self._add_message("system", summary)

            QTimer.singleShot(0, self._scroll_to_bottom)
        except Exception:
            logger.exception("_on_orchestrator_result failed")
        finally:
            # Cleanup как в _on_agent_finish.
            self._reset_send_button()
            self._current_assistant_content = ""
            self._current_thinking_content = ""
            self._last_assistant_widget = None
            self._current_thought_stream = None
            self._pending_final_answer = ""
            self._last_research_summary = None
            self._research_mode_active = False
            self.status_label.setText("Готов к работе")

    @Slot(str, dict)
    def _on_orchestrator_event(self, kind: str, data: dict) -> None:
        """Пробрасывает событие Orchestrator'а в UI (статус + thought)."""
        if kind == "classify":
            self.status_label.setText("🧠 Классификация…")
        elif kind == "plan":
            task = data.get("task", "?")
            files = data.get("files", 0)
            deep = data.get("deep", False)
            self.status_label.setText(f"📋 План: {task}, файлов={files}" + (" (deep)" if deep else ""))
        elif kind == "coverage":
            val = float(data.get("value", 0.0))
            self.status_label.setText(f"📊 Покрытие: {val:.0%}")
        elif kind == "synthesize":
            self.status_label.setText("✍️ Синтез (7B CPU)…")
        elif kind == "regen":
            self.status_label.setText(f"🔄 Регенерация: {str(data.get('reason', ''))[:80]}")
        elif kind == "fallback":
            self._add_message(
                "system",
                f"⚠️ Оркестратор упал, использую старый ReAct: {data.get('reason', '')[:120]}",
            )
        elif kind == "done":
            elapsed = data.get("elapsed_s")
            if elapsed is not None:
                logger.info(f"Orchestrator done за {elapsed} с")

    def shutdown(self) -> None:
        """Вызывается MainWindow.closeEvent — освобождает ресурсы."""
        if self._orchestrator is not None:
            try:
                # LSP-клиенты живут вне Orchestrator (используются точечно),
                # но на всякий случай сбрасываем ссылку.
                self._orchestrator = None
            except Exception:
                pass
        if self._model_pool is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._model_pool.aclose())
                loop.close()
            except Exception:
                pass
            self._model_pool = None

    def _pipeline_status_label(self) -> str:
        """Короткое описание состояния двухмодельного пайплайна.

        Отвечает на вопрос «загружены ли обе модели 3B + 7B»:
          • 💤 — не активирован (нет открытого проекта);
          • ⏸ — модели загружены/ждут, но не в работе;
          • ⏳ — идёт загрузка planner/analyst;
          • ⚡ — готов, обе модели в памяти, Orchestrator создан;
          • ⚠️ — ошибка, работает fallback (одна модель).
        """
        if self._model_pool is not None and self._orchestrator is not None:
            try:
                planner_id = self._model_pool.spec(Role.PLANNER).model_id
                analyst_id = self._model_pool.spec(Role.ANALYST).model_id
                return f"⚡ 2-model: {planner_id} (GPU) + {analyst_id} (CPU)"
            except Exception:
                return "⚡ 2-model: загружен"

        if self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            return "⏳ 2-model: загрузка planner+analyst…"

        if self._pool_load_failed:
            return "⚠️ 2-model: недоступен (fallback → 1 модель)"

        if self._model_pool is not None:
            return "⏸ 2-model: модели загружены, ждут проект"

        if not self.project_root:
            return "💤 2-model: не активирован (нет проекта)"

        return "⏸ 2-model: ожидание запуска"

    def _update_pipeline_status(self) -> None:
        """Пересчитывает и применяет постоянный индикатор пайплайна."""
        if not hasattr(self, "pipeline_status_label"):
            return
        text = self._pipeline_status_label()
        self.pipeline_status_label.setText(text)

        if self._model_pool is not None and self._orchestrator is not None:
            color = "#4e9a06"  # зелёный — готов
        elif self._pool_load_failed:
            color = "#d97706"  # оранжевый — ошибка
        elif self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            color = "#e5c07b"  # жёлтый — грузится
        else:
            color = "#888"  # серый — не активен

        self.pipeline_status_label.setStyleSheet(
            f"font-size: 11px; padding: 2px 6px; color: {color};"
            f" background: rgba(128,128,128,0.08); border-radius: 3px;"
        )
        self.pipeline_status_changed.emit(text)

    @Slot(str)
    def _on_pool_progress(self, message: str) -> None:
        """Прогресс загрузки двух моделей — в transient status и pipeline label."""
        self.status_label.setText(f"⚙️ {message}")
        if hasattr(self, "pipeline_status_label"):
            self.pipeline_status_label.setText(f"⏳ {message}")
            self.pipeline_status_label.setStyleSheet(
                "font-size: 11px; padding: 2px 6px; color: #e5c07b;"
                " background: rgba(128,128,128,0.08); border-radius: 3px;"
            )


# ---
