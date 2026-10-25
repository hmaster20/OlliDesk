"""Главное окно OlliDesk.

Правки относительно базовой версии (см. журнал изменений):

* `ProjectState` хранит состояние per-project в
  `<project>/.ollidesk/project_state.json` вместо единого глобального файла.
  Это устраняет переиндексацию при каждом открытии того же проекта.
* `IndexingThread._run_indexing` при нуле изменений эмитит `indexing_finished`
  сразу, без прогресс-бара — панель не показывает «Индексация: 0/0 файлов».
* `_on_index_progress` при `total == 0` скрывает прогресс немедленно.

План 2.4 — разбиение `main_window.py` на контроллеры:

* Меню-бар вынесен в `ui.menus.AppMenuBar` — все `QAction` и подменю
  Recent Projects живут там, `MainWindow._setup_menu` только связывает сигналы.
* Дерево файлов и файловые операции — `ui.file_tree_controller.FileTreeController`
  (QTreeView + GitStatusDelegate + new/rename/delete/copy/cut/paste/reload,
  F2/Del/Ctrl+C/X/V/N/R, контекстное меню).
* Индексация и `ProjectState` — `ui.indexing_controller.IndexingController`
  (+ `IndexingThread` и `ProjectState` в том же файле).
* `MainWindow` остаётся UI-оркестратором: владеет виджетами прогресса/статуса,
  подключает сигналы контроллеров к своим слотам, но не знает про внутренности.
"""

import asyncio
import contextlib
from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from agents.ollama_client import OllamaClient
from core.config import AppConfig, ToolPolicy
from core.model_registry import ModelRegistry
from core.profiles import get_profile_by_id
from core.prompt_watcher import PromptWatcher
from core.search_cache import SearchCache
from core.system_prompts import SystemPromptManager
from core.utils import get_app_data_dir
from fs.indexer import FileIndexer
from fs.reranker import CrossEncoderReranker
from fs.vector_store import VectorStore
from state.project_settings import ProjectSettings
from state.session_store import SessionStore
from ui.chat import ChatPanel
from ui.chat.threads import _OllamaCheckThread  # 🆕
from ui.command_registry import CommandRegistry, PaletteCommand
from ui.dialogs.agent_settings_dialog import AgentSettingsDialog
from ui.dialogs.prompt_editor_dialog import PromptEditorDialog
from ui.file_tree_controller import FileTreeController
from ui.indexing_controller import IndexingController
from ui.menus import AppMenuBar
from ui.widgets.command_palette import CommandPalette
from ui.widgets.status_dashboard import StatusDashboard


class ModelCheckThread(QThread):
    """Поток для проверки поддержки инструментов моделями Ollama.

    Остаётся в `main_window.py`, потому что это одноразовая операция,
    привязанная к жизненному циклу окна (`_start_model_check` / `_stop_model_check`).
    При появлении третьего потребителя — вынести в `ui/model_check_controller.py`
    по образцу `FileTreeController`/`IndexingController`.
    """

    progress = Signal(str, int, int)  # model_name, current, total
    model_checked = Signal(str, bool)  # model_name, supports_tools
    finished = Signal(dict)

    def __init__(self, base_url: str, registry: ModelRegistry):
        super().__init__()
        self.base_url = base_url
        self.registry = registry
        self._cancel = False

    def cancel(self):
        """Запрашивает остановку проверки."""
        self._cancel = True

    def run(self):
        asyncio.run(self._check())

    async def _check(self):
        from agents.ollama_client import OllamaClient
        from core.exceptions import OllamaConnectionError

        client = OllamaClient(base_url=self.base_url)

        results: dict[str, bool] = {}

        # Собираем модели для проверки
        models_to_check = [
            name
            for name, info in self.registry.models.items()
            if info.is_local and info.supports_tools is None
        ]
        total = len(models_to_check)
        if total == 0:
            self.finished.emit(results)
            return

        async with client:
            for i, model in enumerate(models_to_check):
                if self._cancel:
                    logger.info(f"Проверка моделей прервана пользователем на модели {model}")
                    break
                self.progress.emit(model, i + 1, total)
                try:
                    supports = await client.check_tools_support(model)
                    results[model] = supports
                    self.registry.update_tool_support(model, supports)
                    self.model_checked.emit(model, supports)
                except OllamaConnectionError as e:
                    # [FIX] Сервис недоступен. Перебирать остальные N моделей
                    # бессмысленно — все упадут с тем же таймаутом. Логируем
                    # ОДНУ строку вместо 39.
                    logger.warning(
                        f"Ollama недоступна, проверка моделей прервана на "
                        f"{i + 1}/{total} ({model}): {e}"
                    )
                    break
                except Exception as e:
                    logger.error(f"Ошибка проверки модели {model}: {e}")
                    results[model] = False
                    self.registry.update_tool_support(model, False)
                    self.model_checked.emit(model, False)
        self.finished.emit(results)


class MainWindow(QMainWindow):
    """Главное окно приложения.

    Контроллеры (создаются в `_setup_ui`):
      * `self.file_tree_controller` — дерево файлов, git-подсветка,
        new/rename/delete/copy/cut/paste, контекстное меню.
      * `self.indexing_controller` — `IndexingThread` + `ProjectState`.

    Связь MainWindow ↔ контроллеры — только сигналы:
      * `file_tree_controller.file_open_requested(str)` → `open_file_in_editor`;
      * `file_tree_controller.file_renamed(str, str)` → `_on_file_renamed`;
      * `file_tree_controller.file_deleted(str)` → `_on_file_deleted`;
      * `file_tree_controller.branch_changed(str)` → `_on_branch_changed`;
      * `indexing_controller.progress(int, int)` → `_on_index_progress`;
      * `indexing_controller.finished()` → `_on_index_finished`;
      * `indexing_controller.error(str)` → `_on_index_error`;
      * `indexing_controller.summary(list, list)` → `_on_indexing_summary`.
    """

    theme_changed = Signal(str)

    def __init__(self, config: AppConfig):
        """
        Инициализация главного окна.

        Args:
            config: Конфигурация приложения
        """
        super().__init__()
        self.config = config

        # Состояние проекта (должно быть до _update_window_title)
        self.project_path: Path | None = None

        self._update_window_title()
        self.setMinimumSize(1200, 800)
        self.resize(1600, 1000)

        # Инициализация хранилища сессий
        db_path = get_app_data_dir() / "sessions.db"
        self.session_store = SessionStore(db_path)

        # Инициализация клиента Ollama
        self.ollama_client = OllamaClient(base_url="http://localhost:11434")
        self._ollama_connected = False
        self._available_models: list[str] = []
        # 🆕 Фоновый поток проверки Ollama вместо new_event_loop() на GUI-потоке.
        self._ollama_check_thread: _OllamaCheckThread | None = None

        # Инициализация хранилища возможностей моделей
        self.model_registry = ModelRegistry()

        # Инициализация менеджера ролей
        from core.roles import RoleManager

        self.role_manager = RoleManager()

        # Инициализация менеджера промптов
        self.prompt_manager = SystemPromptManager()

        # Горячая перезагрузка промптов и ролей при правке файлов на диске
        self.prompt_watcher = PromptWatcher(self.prompt_manager, self.role_manager)
        self.prompt_watcher.start()

        # Индексация
        self.indexer = FileIndexer(config, self.project_path)
        self.vector_store: VectorStore | None = None
        # Реранкер создаётся лениво при открытии проекта и кэшируется по имени модели
        self._reranker: CrossEncoderReranker | None = None

        self._model_check_thread: ModelCheckThread | None = None
        self._is_checking = False

        # [9.9] Палитра команд.
        self._palette_registry = CommandRegistry()
        self._palette_dialog: CommandPalette | None = None

        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()
        self._register_palette_commands()
        self._install_global_shortcuts()
        self._check_ollama_connection()

        # [9.2] Правая панель заменена на поповер-диалог AgentSettingsDialog.

        self.chat_panel.settings_changed.connect(self._on_chat_settings_changed)
        self.chat_panel.directory_attach_requested.connect(self._on_directory_attach_requested)

        # [9.2] Синхронизируем кнопку «📁» с сохранённым состоянием.
        if hasattr(self.chat_panel, "project_tree_btn"):
            show_tree = bool(getattr(self.config, "show_project_tree", False))
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(show_tree)
            self.chat_panel.project_tree_btn.blockSignals(False)
            self.left_panel.setVisible(show_tree)

        # [9.4] Синхронизируем селектор профиля и реагируем на смену.
        self.chat_panel.profile_changed.connect(self._on_profile_changed)
        if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
            try:
                from core.config import load_config as _load_cfg_prof

                _cfg_prof = _load_cfg_prof()
                self.chat_panel._profiles_selector.set_profile(_cfg_prof.active_profile)
            except Exception:
                logger.debug("Не удалось синхронизировать профиль на старте", exc_info=True)

        self.chat_panel.backend_status_changed.connect(self._on_backend_status_changed)
        self.chat_panel.source_activated.connect(self.open_file_in_editor)
        # 🆕 Смена темы → Monaco.
        self.theme_changed.connect(self._on_theme_changed_for_editor)

        logger.info("Главное окно создано")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Настраивает UI: левая панель + центр + кнопка + правая панель.

        Контроллеры (план 2.4) создаются до `_create_left_panel`, потому что
        левая панель вставляет их виджеты в layout и подключает их сигналы
        к слотам MainWindow.
        """
        # --- Контроллеры ---
        self.file_tree_controller = FileTreeController(parent=self)
        self.file_tree_controller.file_open_requested.connect(self.open_file_in_editor)
        # [9.8] Diff из карточки write_file → Monaco DiffEditor.
        # ВАЖНО: chat_panel создаётся позже (в _create_center_panel). Подключение
        # сигнала diff_requested перенесено туда — здесь chat_panel ещё нет.
        self.file_tree_controller.file_diff_requested.connect(self._show_file_diff)
        self.file_tree_controller.file_renamed.connect(self._on_file_renamed)
        self.file_tree_controller.file_deleted.connect(self._on_file_deleted)

        self.indexing_controller = IndexingController(parent=self)
        self.indexing_controller.progress.connect(self._on_index_progress)
        self.indexing_controller.finished.connect(self._on_index_finished)
        self.indexing_controller.error.connect(self._on_index_error)
        self.indexing_controller.summary.connect(self._on_indexing_summary)

        # --- Виджеты ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QHBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Основной сплиттер (левая + центральная панели)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self._create_left_panel()
        self.left_panel = left_panel
        left_panel.setVisible(False)  # по умолчанию свернута
        self.main_splitter.addWidget(left_panel)

        center_panel = self._create_center_panel()
        self.main_splitter.addWidget(center_panel)

        # Инициализация кэша Web Search
        self.search_cache = SearchCache(get_app_data_dir() / "cache")
        self.chat_panel.set_search_cache(self.search_cache)

        self.main_splitter.setSizes([320, 800])
        layout.addWidget(self.main_splitter, stretch=1)

        # [9.2] Правая панель настроек агента — теперь поповер-диалог
        # AgentSettingsDialog, открывается по «⚙» из ChatPanel.

        # [9.2] Кнопка «📁» в ChatPanel управляет видимостью дерева.
        self.chat_panel.project_tree_toggled.connect(self._on_project_tree_toggled)
        # [9.3] Кнопка «⋯» в PipelineSelectors открывает SLMModelsDialog.
        self.chat_panel.slm_models_requested.connect(self._open_slm_manager)

    @Slot(object)
    def _on_show_diff(self, payload: dict) -> None:
        """[9.8] Открывает Monaco DiffEditor с original↔modified.

        Вызывается из карточки `write_file` («🔍 Показать diff»).
        В редактор передаётся и `file_path`, чтобы после accept/reject
        пользователь мог сохранить — Monaco уже умеет.
        """
        if not payload or not hasattr(self, "editor_widget"):
            return
        original = str(payload.get("original") or "")
        modified = str(payload.get("modified") or "")
        language = str(payload.get("language") or "plaintext")
        file_path = str(payload.get("path") or "")

        self.editor_widget.show_diff_from_texts(
            original,
            modified,
            language=language,
            file_path=file_path or None,
        )
        # Переключаемся на вкладку редактора.
        index = self.tab_widget.indexOf(self.editor_widget)
        if index < 0:
            self.tab_widget.addTab(self.editor_widget, "📝 Редактор")
        else:
            self.tab_widget.setCurrentWidget(self.editor_widget)

    @Slot(str)
    def _show_file_diff(self, path: str) -> None:
        """[9.8.3] Показать diff файла в редакторе (HEAD ↔ рабочая копия)."""
        if hasattr(self, "editor_widget"):
            self.editor_widget.show_git_diff_for_file(path)
            index = self.tab_widget.indexOf(self.editor_widget)
            if index >= 0:
                self.tab_widget.setCurrentWidget(self.editor_widget)

    def _create_left_panel(self) -> QWidget:
        """Создает левую панель (дерево файлов проекта + индексация).

        Дерево файлов приходит из `self.file_tree_controller.tree`; всё
        остальное (заголовок, кнопка reindex, прогресс-бар, статус) —
        UI-элементы MainWindow, потому что они управляются слотами
        прогресса/ошибок индексации.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Заголовок: иконка + имя проекта + текущая ветка/тег Git
        header_layout = QHBoxLayout()
        self.project_icon_label = QLabel("📁")
        self.project_icon_label.setStyleSheet("font-size: 16px; padding: 8px 0 4px 10px;")
        header_layout.addWidget(self.project_icon_label)

        self.project_title_label = QLabel("Проект")
        self.project_title_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 10px 4px 0;")
        header_layout.addWidget(self.project_title_label)

        self.branch_label = QLabel("")
        self.branch_label.setToolTip("Текущая ветка / тег Git")
        self.branch_label.setStyleSheet("font-size: 12px; color: #98c379; padding: 8px 10px 4px 0;")
        header_layout.addWidget(self.branch_label)

        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Кнопка Reindex наверху
        self.reindex_btn = QPushButton("🔄 Переиндексировать")
        self.reindex_btn.setStyleSheet("font-size: 13px; padding: 8px; margin: 4px 10px;")
        self.reindex_btn.clicked.connect(self._reindex_project)
        self.reindex_btn.setVisible(False)
        layout.addWidget(self.reindex_btn)

        # Дерево файлов — из FileTreeController (план 2.4).
        self.file_tree_controller.branch_changed.connect(self._on_branch_changed)
        layout.addWidget(self.file_tree_controller.tree, stretch=1)
        layout.setStretchFactor(self.file_tree_controller.tree, 1)

        # Прогресс-бар индексации
        self.index_progress = QProgressBar()
        self.index_progress.setRange(0, 100)
        self.index_progress.setValue(0)
        self.index_progress.setVisible(False)
        self.index_progress.setStyleSheet("font-size: 12px; padding: 2px; margin: 4px 10px;")
        layout.addWidget(self.index_progress)

        # Статус индексации
        self.index_status_label = QLabel("")
        self.index_status_label.setStyleSheet("color: gray; font-size: 12px; padding: 0 10px;")
        self.index_status_label.setVisible(False)
        layout.addWidget(self.index_status_label)

        return panel

    def _create_center_panel(self) -> QWidget:
        """Создает центральную панель (вкладки: Чат / Редактор)."""
        from PySide6.QtWidgets import QTabWidget

        from ui.web_editor.editor_widget import EditorWidget

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)

        default_model = self.config.default_model if hasattr(self.config, "default_model") else "llama3.2"
        self.chat_panel = ChatPanel(
            session_store=self.session_store,
            base_url="http://localhost:11434",
            model=default_model,
        )
        self.chat_panel.set_project_open(False)
        self.chat_panel.agent_panel_toggle.connect(self._toggle_side_panel)

        self.chat_panel.set_registry(self.model_registry)
        self.chat_panel.set_role_manager(self.role_manager)
        self.chat_panel.set_prompt_manager(self.prompt_manager)
        self.chat_panel.refresh_models_requested.connect(self._start_model_check)
        # [9.8] Diff из карточки write_file → Monaco DiffEditor.
        # Подключаем ЗДЕСЬ: chat_panel уже создан (перенесено из _setup_ui,
        # где обращение к атрибуту падало с AttributeError).
        self.chat_panel.diff_requested.connect(self._on_show_diff)

        self.tab_widget.addTab(self.chat_panel, "💬 Чат")

        self.editor_widget = EditorWidget()
        self.editor_widget.set_file_reader(self._read_file)
        self.editor_widget.set_file_writer(self._write_file)
        # 🆕 Инициализация темы Monaco из текущего конфига.
        self.editor_widget.set_theme(self.config.theme)
        self.tab_widget.addTab(self.editor_widget, "📝 Редактор")

        # [9.8] Ctrl+D в редакторе → diff с HEAD текущего файла.
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QAction, QKeySequence

            diff_action = QAction(self.editor_widget)
            diff_action.setShortcut(QKeySequence("Ctrl+D"))
            diff_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            diff_action.triggered.connect(self._diff_current_editor_file)
            self.editor_widget.addAction(diff_action)
        except Exception:
            logger.debug("Не удалось установить Ctrl+D для diff", exc_info=True)

        layout.addWidget(self.tab_widget)
        return panel

    @Slot()
    def _diff_current_editor_file(self) -> None:
        """[9.8] Ctrl+D: diff текущего файла редактора с HEAD."""
        if not hasattr(self, "editor_widget"):
            return
        file_path = self.editor_widget.current_file
        if not file_path:
            self.status_bar.showMessage("Diff: файл не открыт", 3000)
            return
        self.editor_widget.show_git_diff_for_file(file_path)
        self.status_bar.showMessage(f"Diff с HEAD: {Path(file_path).name}", 3000)

    # 🆕 Слот: смена темы → Monaco.
    @Slot(str)
    def _on_theme_changed_for_editor(self, theme: str) -> None:
        """Прокидывает смену темы в Monaco Editor.

        Вызывается из `MainWindow.theme_changed` (испускается
        `_toggle_theme`), когда `OlliDeskApp._on_theme_changed` уже
        применил QSS к приложению.
        """
        if hasattr(self, "editor_widget"):
            self.editor_widget.set_theme(theme)

    @Slot(dict)
    def _on_chat_settings_changed(self, settings: dict):
        if "model" in settings:
            self.config.last_model = settings["model"]
        self.config.last_mode = settings.get("mode", "chat")
        from core.config import save_config
        logger.info(f"⚙️ settings_changed: {settings}")
        save_config(self.config)

    def _read_file(self, path: str) -> str:
        """Читает файл (callback для EditorWidget)."""
        try:
            file_path = Path(path)
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return ""
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {e}")
            return ""

    def _write_file(self, path: str, content: str) -> bool:
        """Записывает файл (callback для EditorWidget)."""
        try:
            file_path = Path(path)
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Файл сохранен: {path}")
            # После сохранения git-статус мог измениться — обновляем делегат.
            # План 2.4: делегат живёт в FileTreeController.
            self.file_tree_controller.git_delegate.refresh()
            return True
        except Exception as e:
            logger.error(f"Ошибка записи файла: {e}")
            return False

    def _close_tab(self, index: int):
        """Закрывает вкладку (кроме чата)."""
        if index == 0:  # Чат нельзя закрыть
            return
        self.tab_widget.removeTab(index)

    def open_file_in_editor(self, file_path: str, line: int = 0):
        """Открывает файл в редакторе.

        Многофайловость обеспечивает сам `EditorWidget` (собственный QTabBar
        и отдельные модели Monaco). Во внешнем `QTabWidget` окна вкладка
        «📝 Редактор» одна — она переиспользуется.
        """
        self.editor_widget.open_file(file_path, line)

        index = self.tab_widget.indexOf(self.editor_widget)
        if index < 0:
            self.tab_widget.addTab(self.editor_widget, "📝 Редактор")
        else:
            self.tab_widget.setTabText(index, "📝 Редактор")

        self.tab_widget.setCurrentWidget(self.editor_widget)

    def _setup_menu(self) -> None:
        """Создаёт меню-бар и подключает его сигналы (план 2.4).

        Само построение меню живёт в `ui.menus.AppMenuBar`; здесь — только
        связь сигналов с обработчиками MainWindow.
        """
        self.menu_bar = AppMenuBar(self.config, self)
        self.setMenuBar(self.menu_bar)

        # File
        self.menu_bar.open_project_requested.connect(self._open_project)
        self.menu_bar.open_recent_requested.connect(self._open_recent_project)
        self.menu_bar.export_chat_requested.connect(self._export_chat)
        self.menu_bar.settings_requested.connect(self._open_settings)
        self.menu_bar.exit_requested.connect(self.close)

        # View
        self.menu_bar.show_chat_requested.connect(self._show_chat_tab)
        self.menu_bar.show_editor_requested.connect(self._show_editor_tab)
        self.menu_bar.project_structure_toggled.connect(self._toggle_project_structure)
        self.menu_bar.theme_toggle_requested.connect(self._toggle_theme)
        self.menu_bar.manage_models_requested.connect(self._open_model_manager)
        self.menu_bar.slm_models_requested.connect(self._open_slm_manager)
        self.menu_bar.preload_pipeline_requested.connect(self._preload_two_model_pipeline)
        # [4.7.B] Замер производительности.
        self.menu_bar.benchmark_requested.connect(self._open_benchmark)
        self.menu_bar.edit_prompts_requested.connect(self._open_prompt_editor)
        self.menu_bar.manage_sessions_requested.connect(self._open_session_manager)
        self.menu_bar.edit_iterations_requested.connect(self._edit_agent_iterations)
        # [9.9] Палитра команд.
        self.menu_bar.command_palette_requested.connect(self._open_command_palette)

        # [9.4] Профили использования.
        self.menu_bar.profile_selected.connect(self._on_menu_profile_selected)
        self.menu_bar.profile_editor_requested.connect(self._open_profile_editor)

        # Help
        self.menu_bar.run_wizard_requested.connect(self._run_wizard)
        self.menu_bar.about_requested.connect(self._show_about)

    def _open_benchmark(self) -> None:
        """[4.7.B] Открывает диалог замеров производительности.

        Перед прогоном предупреждает, что это займёт время (7B CPU на
        длинных промптах — до минуты на строку).
        """
        from ui.dialogs.benchmark_dialog import BenchmarkDialog

        reply = QMessageBox.question(
            self,
            "Замер производительности",
            "Прогон займёт время: 7B на CPU при длинном промпте может "
            "дать ответ за 30–60 секунд, а при повторах — пропорционально "
            "больше.\n\n"
            "Модели прогоняются последовательно (загрузка → промпты → "
            "выгрузка). GUI не блокируется. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        dlg = BenchmarkDialog(self.config, self)
        dlg.exec()

    def _export_chat(self) -> None:
        """[9.6.3] Экспорт текущего чата в Markdown/HTML."""
        if not self.chat_panel:
            return
        self.chat_panel.export_chat(parent=self)

    def _edit_agent_iterations(self) -> None:
        """Позволяет изменить лимит итераций ReAct-цикла агента через меню."""
        try:
            from core.config import load_config, save_config

            cfg = load_config()
            current = cfg.agent_max_iterations
            value, ok = QInputDialog.getInt(
                self,
                "Лимит итераций агента",
                "Максимум итераций ReAct-цикла (вызовов инструментов):",
                current,
                1,
                500,
                1,
            )
            if not ok:
                return
            cfg.agent_max_iterations = value
            save_config(cfg)
            self.status_bar.showMessage(f"Лимит итераций агента: {value}", 4000)
        except Exception as e:
            logger.exception(f"Не удалось сохранить лимит итераций агента: {e}")

    def _open_prompt_editor(self):
        dlg = PromptEditorDialog(self.prompt_manager, self.role_manager, self)
        if dlg.exec():
            self.chat_panel.update_mode_tooltips()
            self.chat_panel.set_role_manager(self.role_manager)

    def _setup_status_bar(self) -> None:
        """[9.7] Статус-бар с единой приборной панелью.

        Убраны отдельные `ollama_status_label` / `backend_status_label`
        (заменены на StatusDashboard). `project_label` остаётся —
        путь к проекту нужен, но не дублирует бэкенд-инфо.
        """
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Слева — путь к проекту.
        self.project_label = QLabel("Проект: не открыт")
        self.project_label.setStyleSheet("padding: 5px;")
        self.status_bar.addWidget(self.project_label)

        # Справа — приборная панель.
        self.dashboard = StatusDashboard()
        self.dashboard.backend_toggle_requested.connect(self._toggle_backend)
        self.dashboard.slm_models_requested.connect(self._open_slm_manager)
        self.status_bar.addPermanentWidget(self.dashboard)

        # Инжектим панель в ChatPanel — она будет синхронизировать
        # backend/pipeline/tok/s.
        if hasattr(self, "chat_panel") and self.chat_panel is not None:
            self.chat_panel.set_dashboard(self.dashboard)

        # Инжектим панель в ChatPanel для синхронизации бэкенда/pipeline/tok/s.
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel.set_dashboard(self.dashboard)

    # ------------------------------------------------------------------
    # [9.9] Палитра команд
    # ------------------------------------------------------------------

    def _install_global_shortcuts(self) -> None:
        """[9.9] Ctrl+Shift+P — глобальный shortcut на всё окно.

        Меню `View → Палитра команд…` уже имеет такой же shortcut —
        при фокусе в меню QAction сработает. Глобальный QShortcut нужен,
        когда фокус в поле ввода чата, в дереве, в редакторе — где
        QAction меню не получает key event.
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        try:
            shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(self._open_command_palette)
        except Exception:
            logger.debug("Не удалось установить Ctrl+Shift+P на окно", exc_info=True)

    def _register_palette_commands(self) -> None:
        """[9.9] Регистрирует команды палитры.

        Каждая команда — `PaletteCommand` с callback на существующий слот.
        Профили и бэкенд добавляются динамически (зависят от конфига).
        """
        reg = self._palette_registry
        reg.clear()

        # --- File ---
        reg.register_many(
            [
                PaletteCommand(
                    id="file.open_project",
                    label="Открыть проект…",
                    hint="Выбрать папку проекта",
                    shortcut="Ctrl+O",
                    group="File",
                    callback=self._open_project,
                ),
                PaletteCommand(
                    id="file.export_chat",
                    label="Экспорт чата…",
                    hint="Markdown / HTML",
                    shortcut="Ctrl+Shift+X",
                    group="File",
                    callback=self._export_chat,
                ),
                PaletteCommand(
                    id="file.settings",
                    label="Настройки…",
                    hint="Глобальные и проектные",
                    group="File",
                    callback=self._open_settings,
                ),
                PaletteCommand(
                    id="file.exit",
                    label="Выход",
                    hint="Закрыть OlliDesk",
                    shortcut="Ctrl+Q",
                    group="File",
                    callback=self.close,
                ),
            ]
        )

        # --- View ---
        reg.register_many(
            [
                PaletteCommand(
                    id="view.show_chat",
                    label="Показать Чат",
                    shortcut="Ctrl+Shift+C",
                    group="View",
                    callback=self._show_chat_tab,
                ),
                PaletteCommand(
                    id="view.show_editor",
                    label="Показать Редактор",
                    shortcut="Ctrl+Shift+E",
                    group="View",
                    callback=self._show_editor_tab,
                ),
                PaletteCommand(
                    id="view.toggle_tree",
                    label="Показать/скрыть дерево проекта",
                    shortcut="F4",
                    group="View",
                    callback=self._toggle_project_tree_from_palette,
                ),
                PaletteCommand(
                    id="view.toggle_theme",
                    label="Переключить тему (тёмная / светлая)",
                    shortcut="Ctrl+T",
                    group="View",
                    callback=self._toggle_theme,
                ),
                PaletteCommand(
                    id="view.agent_settings",
                    label="Настройки агента…",
                    hint="Temperature, max tokens, инструменты, RAG",
                    group="View",
                    callback=self._toggle_side_panel,
                ),
                PaletteCommand(
                    id="view.prompt_editor",
                    label="Редактор промптов и ролей…",
                    group="View",
                    callback=self._open_prompt_editor,
                ),
                PaletteCommand(
                    id="view.session_manager",
                    label="Менеджер сессий…",
                    group="View",
                    callback=self._open_session_manager,
                ),
                PaletteCommand(
                    id="view.edit_iterations",
                    label="Лимит итераций агента…",
                    group="View",
                    callback=self._edit_agent_iterations,
                ),
            ]
        )

        # --- Профили (динамически из конфига) ---
        try:
            from core.config import load_config

            cfg = load_config()
            for p in cfg.profiles:
                reg.register(
                    PaletteCommand(
                        id=f"profile.{p.id}",
                        label=f"Профиль: {p.icon} {p.name}",
                        hint=p.description[:80] if p.description else "",
                        group="Профиль",
                        callback=lambda _pid=p.id, _p=p: self._apply_profile_from_palette(_p),
                    )
                )
        except Exception:
            logger.debug("Не удалось зарегистрировать профили в палитре", exc_info=True)

        # --- Бэкенд ---
        reg.register(
            PaletteCommand(
                id="backend.toggle",
                label="Переключить бэкенд (SLM ⇄ Ollama)",
                hint="Если Ollama выключен — предложит включить",
                group="Бэкенд",
                callback=self._toggle_backend,
            )
        )

        # --- Модели / пайплайн ---
        reg.register_many(
            [
                PaletteCommand(
                    id="models.slm_models",
                    label="Локальные модели (SLM)…",
                    hint="Скачать / удалить / активировать",
                    group="Модели",
                    callback=self._open_slm_manager,
                ),
                PaletteCommand(
                    id="models.preload_pipeline",
                    label="Предзагрузить 2-model pipeline",
                    hint="planner + analyst в память",
                    group="Модели",
                    callback=self._preload_two_model_pipeline,
                ),
                PaletteCommand(
                    id="models.check_tools",
                    label="Проверить поддержку инструментов",
                    hint="Ollama-модели: tools / только чат",
                    group="Модели",
                    callback=self._start_model_check,
                ),
                PaletteCommand(
                    id="models.benchmark",
                    label="Замер производительности…",
                    hint="SLM/Ollama: TTFT, tok/s, время загрузки",
                    group="Модели",
                    callback=self._open_benchmark,
                ),
            ]
        )

        # --- Chat ---
        reg.register_many(
            [
                PaletteCommand(
                    id="chat.new",
                    label="Новый чат (очистить)",
                    hint="Очищает текущую сессию",
                    group="Чат",
                    callback=self._new_chat_from_palette,
                ),
                PaletteCommand(
                    id="chat.stop",
                    label="Остановить генерацию",
                    hint="Если идёт ответ модели",
                    group="Чат",
                    callback=self._stop_generation_from_palette,
                ),
            ]
        )

        # --- Help ---
        reg.register_many(
            [
                PaletteCommand(
                    id="help.wizard",
                    label="Мастер настройки…",
                    group="Помощь",
                    callback=self._run_wizard,
                ),
                PaletteCommand(
                    id="help.about",
                    label="О программе",
                    group="Помощь",
                    callback=self._show_about,
                ),
            ]
        )

        logger.info(f"Палитра команд: зарегистрировано {len(self._palette_registry)} команд")

    @Slot()
    def _open_command_palette(self) -> None:
        """[9.9] Открывает палитру команд (Ctrl+Shift+P).

        Диалог создаётся один раз, повторный Ctrl+Shift+P переиспользует
        его (`open_fresh` сбрасывает поиск).
        """
        if self._palette_dialog is None:
            self._palette_dialog = CommandPalette(self._palette_registry, self)
        self._palette_dialog.open_fresh()

    def _toggle_project_tree_from_palette(self) -> None:
        """Хелпер для палитры: переключает видимость дерева проекта."""
        visible = not self.left_panel.isVisible()
        self._toggle_project_structure(visible)
        # Синхронизируем кнопку «📁» в ChatPanel (см. 9.2).
        if hasattr(self.chat_panel, "project_tree_btn"):
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(visible)
            self.chat_panel.project_tree_btn.blockSignals(False)
        # Синхронизируем action в меню.
        action = self.menu_bar.project_structure_action
        if action is not None:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)

    def _apply_profile_from_palette(self, profile) -> None:
        """Хелпер для палитры: применяет профиль из `PaletteCommand`."""
        if not self.chat_panel:
            return
        try:
            # Обновляем селектор и применяем — как из меню View → Профили.
            if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
                self.chat_panel._profiles_selector.set_profile(profile.id)
            self.chat_panel._on_profile_changed(profile)
            self.menu_bar._refresh_profiles_menu()
        except Exception:
            logger.exception("Не удалось применить профиль из палитры")

    def _new_chat_from_palette(self) -> None:
        """Хелпер для палитры: очищает текущую сессию чата."""
        if not self.chat_panel:
            return
        try:
            self.chat_panel._clear_chat(keep_session=False)
            self.chat_panel.set_session_id("")
        except Exception:
            logger.exception("Не удалось очистить чат из палитры")

    def _stop_generation_from_palette(self) -> None:
        """Хелпер для палитры: останавливает генерацию (no-op если idle)."""
        if not self.chat_panel:
            return
        try:
            if getattr(self.chat_panel, "_generating", False):
                self.chat_panel._stop_generation()
        except Exception:
            logger.exception("Не удалось остановить генерацию из палитры")

    def _toggle_backend(self) -> None:
        """[9.3] Переключает SLM ⇄ Ollama (если Ollama включён в конфиге).

        Если Ollama не включён в `AppConfig.backends.enabled` — показываем
        диалог с предложением включить в настройках.
        """
        try:
            from core.config import load_config, save_config

            cfg = load_config()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать конфиг: {e}")
            return

        current = cfg.backends.priority[0] if cfg.backends.priority else "local_slm"
        target = "ollama" if current == "local_slm" else "local_slm"

        if target == "ollama" and "ollama" not in cfg.backends.enabled:
            reply = QMessageBox.question(
                self,
                "Включить Ollama?",
                "Ollama — внешний локальный сервис, требует запущенного "
                "`ollama serve`. Включить его в этой сессии?\n\n"
                "SLM останется приоритетной, Ollama будет fallback.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            cfg.backends.enabled = list(cfg.backends.enabled) + ["ollama"]
            save_config(cfg)

        # Меняем приоритет и применяем на лету.
        new_priority = ["ollama", "local_slm"] if target == "ollama" else ["local_slm", "ollama"]
        cfg.backends.priority = new_priority
        save_config(cfg)
        if self.chat_panel:
            self.chat_panel.router.set_priority(new_priority)
            self.chat_panel._emit_backend_status()
            self.chat_panel._sync_dashboard()
            self.chat_panel._update_model_selector_visibility()
        self.status_bar.showMessage(f"Бэкенд переключён: {target.upper()}", 3000)

    def _check_ollama_connection(self) -> None:
        """[9.7] Проверяет Ollama ТОЛЬКО если он разрешён в конфиге.

        Приборная панель (StatusDashboard) сама покажет состояние
        бэкенда; отдельный `ollama_status_label` больше не нужен.
        Если Ollama выключен в конфиге — проверка не запускается
        (никаких HTTP-запросов на localhost:11434).
        """
        try:
            from core.config import load_config

            cfg = load_config()
            if "ollama" not in cfg.backends.enabled:
                logger.info("Ollama не в enabled-списке — проверка подключения пропущена")
                return
        except Exception:
            return
        QTimer.singleShot(500, self._do_check_ollama)

    @Slot(bool, list)
    def _on_ollama_checked(self, ok: bool, models: list[str]) -> None:
        """[9.7] Обрабатывает результат фоновой проверки Ollama (GUI-поток).

        Заменяет удалённый `ollama_status_label`:
          * при успехе — синхронизирует `ModelRegistry`, обновляет
            список моделей в ChatPanel, ставит первую локальную модель
            по умолчанию (только если пользователь ещё не выбирал);
          * приборную панель обновляет `ChatPanel._sync_dashboard`
            (вызывается из `_on_backend_status_changed`).
        """
        self._available_models = list(models or [])
        self._ollama_connected = bool(ok) and bool(self._available_models)

        if not self._ollama_connected:
            logger.info(f"Ollama: не подключён (ok={ok}, моделей={len(self._available_models)})")
            return

        logger.info(f"Ollama: подключён, моделей={len(self._available_models)}")

        # Синхронизируем реестр.
        try:
            self.model_registry.sync_with_ollama(self._available_models)
        except Exception as e:
            logger.warning(f"sync_with_ollama failed: {e}")

        # Обновляем список в ChatPanel.
        if self.chat_panel is not None:
            try:
                if getattr(self.chat_panel, "registry", None) is None:
                    self.chat_panel.set_registry(self.model_registry)
                else:
                    self.chat_panel.update_models(self._available_models)
            except Exception:
                logger.debug(
                    "Не удалось обновить модели в ChatPanel",
                    exc_info=True,
                )

            # Первая локальная модель по умолчанию — только если пользователь
            # ещё ничего не выбрал (не перетираем `last_model`).
            current_model = self.chat_panel.get_current_model()
            if not current_model and self._available_models:
                first_local = next(
                    (
                        m
                        for m in self._available_models
                        if (info := self.model_registry.get(m)) is not None and info.is_local
                    ),
                    self._available_models[0],
                )
                self.chat_panel.set_model(first_local)

            # Dashboard.
            self.chat_panel._sync_dashboard()

    # 🆕 Полностью переписано: фоновая проверка вместо new_event_loop().
    def _do_check_ollama(self) -> None:
        """Проверка Ollama в фоновом потоке.

        Раньше здесь создавался `new_event_loop().run_until_complete(...)`
        на GUI-потоке — это блокировало окно на всё время `is_available()`
        (до 3 с таймаута при недоступном сервере) и на `list_models()`.
        Теперь — `_OllamaCheckThread` (тот же, что использует ChatPanel
        для `_on_ollama_check_result`), сигналы `result(bool)` и
        `models_ready(bool, list)` обрабатываются на GUI-потоке.

        `_OllamaCheckThread.result` уже подключён в ChatPanel — двойной
        коннект не нужен: MainWindow слушает только `models_ready`.
        """
        if self._ollama_check_thread is not None and self._ollama_check_thread.isRunning():
            return
        self._ollama_check_thread = _OllamaCheckThread(self.ollama_client)
        self._ollama_check_thread.models_ready.connect(self._on_ollama_checked)
        self._ollama_check_thread.start()

    @Slot(str)
    def _on_backend_status_changed(self, label: str) -> None:
        """[9.7] Проброс в приборную панель (совместимость)."""
        if self.chat_panel:
            self.chat_panel._sync_dashboard()

    def _update_window_title(self) -> None:
        """Обновляет заголовок окна с именем проекта."""
        base = "OlliDesk — Local AI Coding Assistant"
        if self.project_path:
            self.setWindowTitle(f"{base} ———> {self.project_path.name}")
        else:
            self.setWindowTitle(base)

    @Slot(str)
    def _on_branch_changed(self, branch: str) -> None:
        """Обновляет лейбл с текущей веткой Git в шапке левой панели.

        Сигнал приходит из `self.file_tree_controller.branch_changed`
        (план 2.4). `branch_label` создаётся в `_create_left_panel`.
        """
        if branch:
            self.branch_label.setText(f"🌿 {branch}")
        else:
            self.branch_label.setText("")

    @Slot(str, str)
    def _on_file_renamed(self, old_path: str, new_path: str) -> None:
        """Переименование из FileTreeController → обновить вкладку редактора."""
        if hasattr(self, "editor_widget"):
            self.editor_widget.rename_open_file(old_path, new_path)

    @Slot(str)
    def _on_file_deleted(self, path: str) -> None:
        """Удаление из FileTreeController → закрыть вкладку редактора."""
        if hasattr(self, "editor_widget"):
            self.editor_widget.close_file_silently(path)

    def _open_project(self) -> None:
        """Открывает диалог выбора проекта."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if folder:
            self._do_open_project(Path(folder))

    def _add_recent_project(self, path: str) -> None:
        """Добавляет проект в список недавних и перерисовывает меню.

        План 2.4: `AppMenuBar.set_recent_projects` пересобирает подменю
        Recent Projects; `_update_recent_projects_menu` удалён.
        """
        from core.config import save_config

        projects = self.config.recent_projects
        if path in projects:
            projects.remove(path)
        projects.insert(0, path)
        self.config.recent_projects = projects[:10]
        save_config(self.config)
        self.menu_bar.set_recent_projects(self.config.recent_projects)

    def _open_recent_project(self, path: str) -> None:
        """Открывает проект из списка недавних.

        Сигнал `open_recent_requested(str)` от `AppMenuBar` вызывает этот
        слот. Если путь исчез — удаляем запись и перерисовываем меню через
        `self.menu_bar.set_recent_projects` (вместо удалённого
        `_update_recent_projects_menu`).
        """
        folder = Path(path)
        if folder.exists():
            self._do_open_project(folder)
        else:
            QMessageBox.warning(
                self,
                "Проект не найден",
                f"Папка проекта не существует:\n{path}",
            )
            projects = self.config.recent_projects
            if path in projects:
                projects.remove(path)
                self.config.recent_projects = projects[:10]
                from core.config import save_config

                save_config(self.config)
                self.menu_bar.set_recent_projects(self.config.recent_projects)

    def _do_open_project(self, project_path: Path) -> None:
        """Внутренняя логика открытия проекта (без диалога).

        План 2.4: дерево файлов настраивается через `FileTreeController`,
        индексация — через `IndexingController` (configure + start).
        """
        self.project_path = project_path
        logger.info(f"Открыт проект: {self.project_path}")
        self.project_label.setText(f"Проект: {self.project_path.name}")

        # Дерево — через FileTreeController (план 2.4).
        self.file_tree_controller.set_project_root(self.project_path)
        self.project_title_label.setText(self.project_path.name)
        self._update_window_title()

        # [9.2] Восстанавливаем видимость дерева проекта.
        try:
            from core.config import load_config as _load_cfg

            _cfg = _load_cfg()
            show_tree = bool(getattr(_cfg, "show_project_tree", False))
        except Exception:
            show_tree = False
        self.left_panel.setVisible(show_tree)
        if hasattr(self.chat_panel, "project_tree_btn"):
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(show_tree)
            self.chat_panel.project_tree_btn.blockSignals(False)

        # [9.4] Применяем профиль из конфига (мог быть изменён в другом окне).
        try:
            from core.config import load_config as _load_cfg_prof

            _cfg_prof = _load_cfg_prof()
            active_profile = get_profile_by_id(_cfg_prof.profiles, _cfg_prof.active_profile)
            if active_profile is not None and self.chat_panel is not None:
                self.chat_panel._apply_profile(active_profile, emit_signal=False)
        except Exception:
            logger.debug("Не удалось применить профиль при открытии проекта", exc_info=True)

        persist_dir = self.project_path / ".ollidesk" / "vector_db"
        self.vector_store = VectorStore(
            persist_dir=persist_dir,
            embed_client=self.chat_panel.router,
            embed_model=self.config.embed_model,
            reranker=self._get_reranker(),
            rerank_pool=self.config.rerank.top_k,
            embed_models=self._embed_models(),
            rerank_cache=self.search_cache,
        )
        self.chat_panel.set_vector_store(self.vector_store)
        # Пересоздаём индексер с путём проекта, чтобы подхватить игнор-файлы
        self.indexer = FileIndexer(self.config, self.project_path)

        # Загружаем последнюю сессию для этого проекта
        sessions = self.session_store.list_sessions(str(project_path))
        if sessions:
            last = sessions[0]  # уже отсортированы по updated_at DESC
            history = self.session_store.get_history(last.id, limit=1000)
            self.chat_panel.set_session_id(last.id)
            self.chat_panel.load_history(history)

        self.chat_panel.set_project_root(str(self.project_path))
        self.chat_panel.set_project_open(True)

        # Индексация: контроллер сам загрузит previous_state (план 2.4).
        self.indexing_controller.configure(
            project_path=self.project_path,
            indexer=self.indexer,
            vector_store=self.vector_store,
        )
        self._add_recent_project(str(self.project_path))
        self.reindex_btn.setVisible(True)
        self.reindex_btn.setEnabled(True)

        # Восстанавливаем настройки проекта (они приоритетнее глобальных)
        project_settings = ProjectSettings.load(self.project_path)
        if project_settings.model:
            self.chat_panel.apply_settings(project_settings.model_dump())
        else:
            if self.config.last_model:
                self.chat_panel.set_model(self.config.last_model)
            if self.config.last_mode:
                self.chat_panel.set_mode_by_name(self.config.last_mode)

        # Синхронизируем чекбокс с фактическим состоянием — apply_settings
        # мог применить show_reasoning=False из project_settings.yaml.
        if hasattr(self, "show_reasoning_check"):
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(self.chat_panel.get_show_reasoning())
            self.show_reasoning_check.blockSignals(False)

        self._start_indexing()

    def _get_reranker(self) -> CrossEncoderReranker | None:
        """Возвращает реранкер по текущему конфигу (или ``None``, если выключен).

        Экземпляр кэшируется и пересоздаётся только при смене модели — модель
        кросс-энкодера загружается лениво, не хочется перезагружать её каждый раз.
        """
        settings = self.config.rerank
        if not settings.enabled:
            return None
        if self._reranker is None or self._reranker.model_name != settings.model:
            self._reranker = CrossEncoderReranker(settings.model)
        return self._reranker

    def _embed_models(self) -> dict[str, str]:
        """Карта ``категория -> модель`` для раздельных эмбеддингов (без ``None``)."""
        cfg = self.config.embed_models
        return {category: model for category, model in (("code", cfg.code), ("docs", cfg.docs)) if model}

    def _on_directory_attach_requested(self, path_str: str) -> None:
        """Подключает каталог из команды @dir без пересоздания сессии чата.

        Отличие от `_do_open_project`: текущая сессия и история чата не трогаются,
        каталог не добавляется в список недавних. Пользователь может держать
        открытым один проект, а вопросы задавать по другому каталогу.

        План 2.4: дерево — `file_tree_controller.set_project_root`,
        индексация — `indexing_controller.configure`/`start`.
        """
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            logger.warning(f"@dir: путь недоступен: {path}")
            return

        logger.info(f"@dir: прикрепляю директорию {path}")
        self.project_path = path
        self.project_label.setText(f"Проект: {path.name}")

        self.file_tree_controller.set_project_root(path)
        self.project_title_label.setText(path.name)
        self._update_window_title()

        # Индекс хранится в .ollidesk внутри самого каталога
        self.vector_store = VectorStore(
            persist_dir=path / ".ollidesk" / "vector_db",
            embed_client=self.chat_panel.router,
            embed_model=self.config.embed_model,
            reranker=self._get_reranker(),
            rerank_pool=self.config.rerank.top_k,
            embed_models=self._embed_models(),
            rerank_cache=self.search_cache,
        )
        self.chat_panel.set_vector_store(self.vector_store)
        self.chat_panel.set_project_root(str(path))
        self.chat_panel.set_project_open(True)
        # Пересоздаём индексер для нового каталога
        self.indexer = FileIndexer(self.config, self.project_path)

        # Индексация: контроллер сам загрузит previous_state (план 2.4).
        if self.indexing_controller.is_running:
            logger.info("@dir: ждём завершения индексации предыдущего каталога")
            self.indexing_controller.stop()

        self.indexing_controller.configure(
            project_path=path,
            indexer=self.indexer,
            vector_store=self.vector_store,
        )
        self.reindex_btn.setVisible(True)
        self.reindex_btn.setEnabled(True)
        self._start_indexing()

    # ------------------------------------------------------------------
    # Индексация (тонкие обёртки над IndexingController, план 2.4)
    # ------------------------------------------------------------------

    def _reindex_project(self) -> None:
        """Запускает полную переиндексацию.

        Сброс `previous_state` и `ProjectState.clear` — внутри контроллера
        (`IndexingController.reindex`). MainWindow только готовит UI.
        """
        if not self.project_path:
            return
        self.index_progress.setVisible(True)
        self.index_progress.setValue(0)
        self.index_status_label.setVisible(True)
        self.index_status_label.setText("⏳ Индексация...")
        self.reindex_btn.setEnabled(False)
        self.indexing_controller.reindex()

    def _start_indexing(self) -> None:
        """Запускает индексацию в фоне (тонкая обёртка над контроллером)."""
        if not self.project_path or not self.vector_store:
            return
        if self.indexing_controller.is_running:
            logger.warning("Индексация уже выполняется")
            return
        self.index_progress.setVisible(True)
        self.index_progress.setValue(0)
        self.index_status_label.setVisible(True)
        self.index_status_label.setText("⏳ Индексация...")
        self.reindex_btn.setEnabled(False)
        self.indexing_controller.start()

    @Slot(int, int)
    def _on_index_progress(self, current: int, total: int):
        """Обновляет прогресс-бар индексации.

        При total == 0 индексация не нужна — скрываем прогресс немедленно,
        чтобы пользователь не думал, что идёт переиндексация.
        """
        if total == 0:
            self.index_progress.setVisible(False)
            self.index_status_label.setVisible(False)
            self.reindex_btn.setEnabled(True)
            self.status_bar.showMessage("Индексация не требуется", 3000)
            return
        self.index_progress.setRange(0, total)
        self.index_progress.setValue(current)
        self.index_status_label.setText(f"⏳ Индексация: {current}/{total} файлов")

    @Slot()
    def _on_index_finished(self):
        """Обрабатывает завершение индексации (UI).

        Сохранение `ProjectState` теперь делает `IndexingController`
        в `_on_thread_finished` (без `scan_project` — состояние приходит
        из worker'а через `state_ready`) — здесь только UI.
        """
        if self.index_progress.isVisible():
            self.index_progress.setValue(self.index_progress.maximum())
            self.index_status_label.setText("✅ Индексация завершена")

        QTimer.singleShot(3000, self._hide_progress)
        self.reindex_btn.setEnabled(True)

    @Slot(str)
    def _on_index_error(self, error_msg: str):
        """Обрабатывает ошибку индексации."""
        self.index_status_label.setText(f"❌ Ошибка: {error_msg}")
        self.index_progress.setVisible(True)  # keep visible
        self.reindex_btn.setEnabled(True)

    @Slot(list, list)
    def _on_indexing_summary(self, added: list[str], deleted: list[str]) -> None:
        if not added and not deleted:
            self.status_bar.showMessage("Индексация не требуется — изменений нет", 5000)
            return
        preview = ", ".join(added[:8]) + (" …" if len(added) > 8 else "")
        text = f"Проиндексировано: {len(added)} новых/изменённых"
        if deleted:
            text += f", удалено: {len(deleted)}"
        self.status_bar.showMessage(f"{text} — {preview}", 8000)
        logger.info(f"Индексация: new/modified={len(added)}, deleted={len(deleted)}")
        for p in added:
            logger.info(f"  + {p}")
        for p in deleted:
            logger.info(f"  − {p}")

    def _hide_progress(self) -> None:
        """Скрывает прогресс-бар индексации."""
        self.index_progress.setVisible(False)
        self.index_status_label.setVisible(False)

    def _toggle_side_panel(self) -> None:
        """[9.2] Открывает AgentSettingsDialog (замена правой панели)."""
        if not self.chat_panel:
            return
        settings = self.chat_panel.get_settings()
        dialog = AgentSettingsDialog(
            temperature=float(settings.get("temperature", 0.7)),
            max_tokens=int(settings.get("max_tokens", 4096)),
            context_window=int(settings.get("context_window", 8192)),
            max_iterations=int(self.config.agent_max_iterations),
            show_reasoning=bool(settings.get("show_reasoning", True)),
            rag_enabled=bool(settings.get("rag_enabled", True)),
            tools_enabled={
                name: policy != ToolPolicy.EXCLUDED for name, policy in self.chat_panel.tool_policies.items()
            },
            parent=self,
        )
        dialog.settings_applied.connect(self._on_agent_settings_applied)
        dialog.exec()

    @Slot(dict)
    def _on_agent_settings_applied(self, settings: dict) -> None:
        """[9.2] Применяет настройки агента к ChatPanel и конфигу."""
        # Температура/max_tokens/context_window — в текущую модель.
        # ChatPanel пока не хранит их per-model, но get_settings() возвращает
        # только model/mode/show_reasoning. Расширяем постепенно:
        model_name = self.chat_panel.get_current_model()
        try:
            for m in self.config.models:
                if m.model == model_name:
                    m.temperature = float(settings["temperature"])
                    m.max_tokens = int(settings["max_tokens"])
                    m.context_length = int(settings["context_window"])
                    break
        except Exception:
            logger.debug("Не удалось применить temp/tokens к модели", exc_info=True)

        # Max iterations.
        self.config.agent_max_iterations = int(settings["max_iterations"])
        self.config.chat_max_iterations = max(1, min(int(settings["max_iterations"]), 15))

        # Reasoning.
        self.chat_panel.set_show_reasoning(bool(settings["show_reasoning"]))
        if hasattr(self, "show_reasoning_check"):
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(bool(settings["show_reasoning"]))
            self.show_reasoning_check.blockSignals(False)

        # Tools: policy auto/excluded.
        tools = settings.get("tools") or {}
        for name, enabled in tools.items():
            policy = ToolPolicy.AUTO if enabled else ToolPolicy.EXCLUDED
            self.chat_panel.set_tool_policy(name, policy)
            if hasattr(self, "_tool_checkboxes") and name in self._tool_checkboxes:
                cb = self._tool_checkboxes[name]
                cb.blockSignals(True)
                cb.setChecked(enabled)
                cb.blockSignals(False)

        # RAG.
        if hasattr(self, "rag_check"):
            self.rag_check.blockSignals(True)
            self.rag_check.setChecked(bool(settings.get("rag_enabled", True)))
            self.rag_check.blockSignals(False)

        try:
            from core.config import save_config

            save_config(self.config)
        except Exception:
            logger.debug("Не удалось сохранить настройки агента", exc_info=True)

        self.status_bar.showMessage("Настройки агента применены", 3000)

    def _show_chat_tab(self) -> None:
        """Показывает вкладку чата (создаёт если закрыта)."""
        if self.tab_widget.indexOf(self.chat_panel) < 0:
            self.tab_widget.insertTab(0, self.chat_panel, "💬 Чат")
        self.tab_widget.setCurrentWidget(self.chat_panel)

    def _show_editor_tab(self) -> None:
        """Показывает вкладку редактора (создаёт если закрыта)."""
        if self.tab_widget.indexOf(self.editor_widget) < 0:
            self.tab_widget.addTab(self.editor_widget, "📝 Редактор")
        self.tab_widget.setCurrentWidget(self.editor_widget)

    @Slot(bool)
    def _toggle_project_structure(self, visible: bool) -> None:
        """[9.2] Показывает/скрывает левую панель (F4, меню).

        Синхронизирует кнопку «📁» в ChatPanel и сохраняет состояние в конфиг.
        """
        self.left_panel.setVisible(visible)
        if hasattr(self.chat_panel, "project_tree_btn"):
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(visible)
            self.chat_panel.project_tree_btn.blockSignals(False)
        try:
            from core.config import save_config

            self.config.show_project_tree = visible
            save_config(self.config)
        except Exception:
            logger.debug("Не удалось сохранить show_project_tree", exc_info=True)
        logger.debug(f"Панель структуры проекта: {'показана' if visible else 'скрыта'}")

    @Slot(bool)
    def _on_project_tree_toggled(self, visible: bool) -> None:
        """[9.2] Пользователь кликнул «📁» в ChatPanel — переключаем дерево."""
        self.left_panel.setVisible(visible)
        # Синхронизируем галочку меню.
        action = self.menu_bar.project_structure_action
        if action is not None:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)
        # Персистим в конфиг (per-project).
        try:
            from core.config import save_config

            self.config.show_project_tree = visible
            save_config(self.config)
        except Exception:
            logger.debug("Не удалось сохранить show_project_tree", exc_info=True)

    @Slot(str)
    def _on_profile_changed(self, profile_id: str) -> None:
        """[9.4] Профиль сменился — синхронизируем UI MainWindow.

        * чекбокс «Показывать размышления» (из ChatPanel);
        * статус-бар — короткое сообщение;
        * селектор профиля (на случай вызова из ChatPanel API).
        """
        # [9.4] Обновляем галочку в подменю View → Профили.
        self.menu_bar._refresh_profiles_menu()
        # Синхронизируем чекбокс reasoning (он в AgentSettingsDialog).
        if hasattr(self, "show_reasoning_check") and self.chat_panel:
            value = self.chat_panel.get_show_reasoning()
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(value)
            self.show_reasoning_check.blockSignals(False)

        # Синхронизируем селектор профиля в ChatPanel (на случай API-вызова).
        if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
            self.chat_panel._profiles_selector.set_profile(profile_id)

        # Показываем имя профиля в статус-баре.
        profile = get_profile_by_id(self.config.profiles, profile_id)
        if profile is not None:
            self.status_bar.showMessage(f"{profile.icon} Профиль: {profile.name}", 4000)
        logger.info(f"Профиль изменён: {profile_id}")

    @Slot(str)
    def _on_menu_profile_selected(self, profile_id: str) -> None:
        """[9.4] Пользователь выбрал профиль из меню View → Профили."""
        if not self.chat_panel:
            return
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            profile = get_profile_by_id(cfg.profiles, profile_id)
            if profile is None:
                return
            # Обновляем селектор и применяем профиль.
            if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
                self.chat_panel._profiles_selector.set_profile(profile_id)
            self.chat_panel._on_profile_changed(profile)
        except Exception as e:
            logger.warning(f"Не удалось применить профиль из меню: {e}")

    def _open_profile_editor(self) -> None:
        """[9.4] Открывает ProfileEditorDialog. После сохранения перечитывает
        конфиг, обновляет селектор профиля в ChatPanel и подменю в меню.
        """
        from ui.dialogs.profile_editor_dialog import ProfileEditorDialog

        dlg = ProfileEditorDialog(self.config, self)
        dlg.profiles_saved.connect(self._on_profiles_saved)
        dlg.exec()

    @Slot()
    def _on_profiles_saved(self) -> None:
        """[9.4] Профили сохранены: перечитываем конфиг и синхронизируем UI."""
        try:
            from core.config import load_config

            self.config = load_config()
            self.menu_bar.set_config(self.config)
        except Exception as e:
            logger.warning(f"Не удалось перечитать конфиг после редактора профилей: {e}")

        # Обновляем селектор профиля в ChatPanel.
        if self.chat_panel is not None:
            self.chat_panel.refresh_profiles_selector()
            # Применяем активный профиль (мог измениться/исчезнуть).
            try:
                profile = get_profile_by_id(self.config.profiles, self.config.active_profile)
                if profile is not None:
                    self.chat_panel._apply_profile(profile, emit_signal=False)
                if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
                    self.chat_panel._profiles_selector.set_profile(self.config.active_profile)
            except Exception:
                logger.debug("Не удалось применить профиль после сохранения", exc_info=True)

        self.status_bar.showMessage("Профили сохранены", 3000)

    def _run_wizard(self) -> None:
        """Принудительно запускает визард настройки."""
        from ui.dialogs.wizard import SetupWizard

        try:
            wizard = SetupWizard()
        except Exception as e:
            logger.exception("Не удалось открыть визард")
            QMessageBox.critical(self, "Ошибка визарда", f"Не удалось открыть мастер настройки:\n{e}")
            return
        if wizard.exec():
            from core.config import load_config

            try:
                self.config = load_config()
                self.menu_bar.set_config(self.config)
                if self.chat_panel:
                    models = [m.model for m in self.config.models]
                    self.chat_panel.update_models(models)
                    default = self.config.default_model
                    self.chat_panel.set_model(default)
                    self.chat_panel.set_project_open(self.project_path is not None)
                from core.config import save_config

                save_config(self.config)
            except Exception as e:
                logger.error(f"Ошибка загрузки конфига после визарда: {e}")
        else:
            logger.info("Визард отменён пользователем")

    def _open_settings(self) -> None:
        """Открывает диалог настроек."""
        from ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(config=self.config, project_path=self.project_path, parent=self)
        if dlg.exec():
            # Перезагружаем конфиг
            from core.config import load_config

            try:
                self.config = load_config(self.project_path)
                self.menu_bar.set_config(self.config)
                # Обновляем индексер с новым конфигом
                self.indexer = FileIndexer(self.config, self.project_path)
                logger.info("Настройки сохранены, конфиг перезагружен")
            except Exception as e:
                logger.error(f"Ошибка перезагрузки конфига: {e}")

    def _toggle_theme(self) -> None:
        """Переключает между тёмной и светлой темой."""
        new_theme = "dark" if self.config.theme == "light" else "light"
        self.config.theme = new_theme
        self.theme_changed.emit(new_theme)

    def _show_about(self) -> None:
        """Показывает окно «О программе»."""
        from core._version import __version__

        QMessageBox.about(
            self,
            "О программе OlliDesk",
            f"OlliDesk v{__version__}\n\n"
            "Локальный AI-ассистент для написания кода.\n\n"
            "Работает на llama.cpp (SLM) и Ollama.\n"
            "UI — PySide6, редактор — Monaco.",
        )

    def closeEvent(self, event):
        logger.info("Закрытие главного окна")
        # [9.9] Закрываем палитру команд, если была открыта.
        if self._palette_dialog is not None:
            with contextlib.suppress(Exception):
                self._palette_dialog.close()
            self._palette_dialog = None
        self._save_project_settings()
        if self.chat_panel:
            self.config.last_model = self.chat_panel.get_current_model()
            mode_map = {0: "chat", 1: "plan", 2: "agent"}
            self.config.last_mode = mode_map.get(self.chat_panel.mode_combo.currentIndex(), "chat")
            from core.config import save_config

            logger.info(
                f"🔄 closeEvent: last_model='{self.config.last_model}', last_mode='{self.config.last_mode}'"
            )
            save_config(self.config)
            logger.info("✅ Конфиг сохранён в closeEvent")
        self._stop_all_threads()
        self._stop_model_check()
        self.prompt_watcher.stop()

        # K3: освобождаем ModelPool (обе llama.cpp модели) и Orchestrator.
        if self.chat_panel and hasattr(self.chat_panel, "shutdown"):
            try:
                self.chat_panel.shutdown()
            except Exception:
                logger.debug("ChatPanel.shutdown failed", exc_info=True)

        # Останавливаем фоновый опрос git-статусов у делегата.
        if hasattr(self, "file_tree_controller"):
            self.file_tree_controller.set_project_root(None)

        event.accept()

    def _save_project_settings(self) -> None:
        """Сохраняет настройки проекта."""
        if not self.project_path:
            return
        settings = self.chat_panel.get_settings()
        ProjectSettings(**settings).save(self.project_path)

    # 🆕 Убран new_event_loop() — закрытие окна больше не блокируется.
    def _stop_all_threads(self) -> None:
        """Останавливает фоновые потоки.

        Ollama-клиент больше не закрываем через
        `new_event_loop().run_until_complete(self.ollama_client.__aexit__)`:
        это блокировало закрытие окна (до 3 с) и потенциально висело на
        Windows Proactor при незавершённых httpx-коннектах. `httpx.AsyncClient`
        уберётся GC при выходе процесса — ресурс-утечки нет, а закрытие
        мгновенное.
        """
        # План 2.4: индексация — через контроллер.
        if hasattr(self, "indexing_controller"):
            self.indexing_controller.stop()
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel._stop_generation()
        # Ждём фоновый Ollama-check (максимум 1 с) — поток daemon, но лучше
        # не оставлять его висеть на закрывающемся QApplication.
        if self._ollama_check_thread is not None and self._ollama_check_thread.isRunning():
            self._ollama_check_thread.wait(1000)

    def _open_model_manager(self):
        from ui.dialogs.model_manager_dialog import ModelManagerDialog

        dlg = ModelManagerDialog(self.model_registry, self.ollama_client.base_url, self)
        dlg.exec()

    def _open_slm_manager(self):
        """Открывает диалог управления локальными SLM-моделями.

        После закрытия:
          * если модели изменились — перечитываем конфиг;
          * `reload_slm()` перезагружает активную single-SLM и сбрасывает
            двухмодельный пайплайн (см. panel.reset_pipeline), чтобы
            следующий research-запрос шёл уже с новыми planner/analyst.
        """
        from core.config import load_config
        from ui.dialogs.slm_models_dialog import SLMModelsDialog

        dlg = SLMModelsDialog(self.config, self)
        dlg.exec()

        if dlg.changed():
            try:
                self.config = load_config()
                self.menu_bar.set_config(self.config)
            except Exception as e:
                logger.warning(f"Не удалось перезагрузить конфиг: {e}")

            if hasattr(self, "chat_panel") and self.chat_panel:
                self.chat_panel.reload_slm()
                # [9.3] Селекторы Planner/Analyst могли измениться — обновляем.
                self.chat_panel.refresh_pipeline_selectors()

                self.status_bar.showMessage(
                    "SLM: конфигурация применена. Пайплайн будет пересоздан при следующем research-запросе.",
                    6000,
                )

    def _preload_two_model_pipeline(self) -> None:
        """View → «Предзагрузить 2-model pipeline».

        Загружает planner + analyst в память через тот же
        ``_ensure_orchestrator``, что и при открытии проекта. Повторный
        вызов при уже загруженном пайплайне или активной загрузке —
        показывает уведомление и ничего не делает.

        Разрешает «повторить» после неудачной попытки: сбрасывает
        ``_pool_load_failed``, чтобы можно было исправить конфиг
        (скачать 7B) и попробовать снова без рестарта приложения.
        """
        if not self.chat_panel:
            return

        if self.chat_panel._model_pool is not None:
            QMessageBox.information(
                self,
                "2-model pipeline",
                "Двухмодельный пайплайн уже загружен — planner и analyst "
                "в памяти. Вопрос об архитектуре уйдёт в него автоматически.",
            )
            return

        if self.chat_panel._pool_load_thread is not None and self.chat_panel._pool_load_thread.isRunning():
            QMessageBox.information(
                self,
                "2-model pipeline",
                "Загрузка уже идёт — см. индикатор в панели чата (⏳ 2-model: …).",
            )
            return

        if not self.chat_panel.project_root:
            QMessageBox.warning(
                self,
                "2-model pipeline",
                "Сначала откройте проект: путь проекта нужен для кэша графа и evidence.",
            )
            return

        # Сбрасываем флаг прошлой ошибки, чтобы _ensure_orchestrator
        # не сделал no-op.
        self.chat_panel._pool_load_failed = False
        self.chat_panel._ensure_orchestrator()
        self.status_bar.showMessage("Загрузка двух моделей (planner + analyst)…", 5000)

    def _start_model_check(self):
        """Запускает или останавливает проверку моделей."""
        if self._is_checking:
            self._stop_model_check()
            return

        if (
            hasattr(self, "_model_check_thread")
            and self._model_check_thread
            and self._model_check_thread.isRunning()
        ):
            return

        # [FIX] Проверка идёт через Ollama API. Без включённого/запущенного
        # Ollama перебор N моделей даёт N × 5 сек таймаутов и N строк ERROR.
        try:
            from core.config import load_config

            cfg = load_config()
            if "ollama" not in cfg.backends.enabled:
                QMessageBox.information(
                    self,
                    "Ollama выключена",
                    "Проверка поддержки инструментов выполняется через Ollama API.\n\n"
                    "Ollama сейчас выключена в списке активных бэкендов: "
                    "Файл → Настройки → Бэкенды LLM.",
                )
                return
        except Exception:
            logger.debug("Не удалось прочитать конфиг для проверки Ollama", exc_info=True)

        if not self._ollama_connected:
            QMessageBox.information(
                self,
                "Ollama недоступна",
                "Ollama не отвечает на localhost:11434, поэтому проверка "
                "поддержки инструментов невозможна.\n\n"
                "Запустите сервис в терминале: `ollama serve`.",
            )
            return

        self.status_bar.showMessage("🔄 Проверка поддержки инструментов моделями...")
        self.chat_panel.set_refresh_button_text("⏹ Стоп")
        self._is_checking = True

        self._model_check_thread = ModelCheckThread(self.ollama_client.base_url, self.model_registry)
        self._model_check_thread.progress.connect(self._on_model_check_progress)
        self._model_check_thread.model_checked.connect(self._on_model_checked)
        self._model_check_thread.finished.connect(self._on_model_check_finished)
        self._model_check_thread.start()

    def _stop_model_check(self):
        """Останавливает проверку моделей.

        [FIX] Не используем terminate(): Python-поток с asyncio.run внутри
        через PyThreadState_SetAsyncExc не убивается — только портится
        состояние (в логе видно, что после «прервана» проверки продолжались).
        Вместо этого шлём cancel() и ждём штатного завершения: текущий
        check_tools_support выйдет по httpx-таймауту (~5-10 сек), затем
        цикл увидит self._cancel и завершится.
        """
        if self._model_check_thread and self._model_check_thread.isRunning():
            self._model_check_thread.cancel()
            self.status_bar.showMessage("⏹ Останавливаю проверку (до 10 сек)...")
            self.chat_panel.set_refresh_button_text("⏹ Останавливаю…")
            # Даём потоку время корректно выйти. UI не блокируется надолго:
            # 10 сек — потолок на один httpx-таймаут.
            if self._model_check_thread.wait(10000):
                self.status_bar.showMessage("⏹ Проверка остановлена", 3000)
            else:
                # Поток всё ещё живёт (например, соединение висит дольше).
                # НЕ убиваем — выйдет сам по cancel(). Сообщаем пользователю.
                logger.warning(
                    "ModelCheckThread не завершился за 10 сек; "
                    "продолжает работу в фоне и выйдет по cancel()"
                )
                self.status_bar.showMessage(
                    "⏹ Останавливаю проверку в фоне (завершится при следующем ответе Ollama)",
                    5000,
                )
                return
        self._is_checking = False
        self.chat_panel.set_refresh_button_text("🔄")

    @Slot(str, int, int)
    def _on_model_check_progress(self, model_name: str, current: int, total: int):
        self.status_bar.showMessage(f"🔄 Проверка {current}/{total}: {model_name}...")

    @Slot(str, bool)
    def _on_model_checked(self, model_name: str, supports: bool):
        """[2.4] Обновляет UI для проверенной модели (публичный API ChatPanel)."""
        self.chat_panel.update_model_status(model_name)
        if self.chat_panel.get_current_model() == model_name:
            self.chat_panel.update_model_status(model_name)

    @Slot(dict)
    def _on_model_check_finished(self, results: dict):
        self.status_bar.showMessage("✅ Проверка моделей завершена", 5000)
        self.chat_panel.set_checking_state(False)
        self.chat_panel.set_refresh_button_text("🔄")
        self._is_checking = False
        self.chat_panel.update_model_list()
        self.chat_panel.update_models(self._available_models)

    def _open_session_manager(self):
        from ui.dialogs.session_manager_dialog import SessionManagerDialog

        dlg = SessionManagerDialog(self.session_store, self.chat_panel, self)
        dlg.exec()
