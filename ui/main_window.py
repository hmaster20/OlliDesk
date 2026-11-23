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

import contextlib
from functools import partial
from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal, Slot
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
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, ToolPolicy
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
from ui.command_registry import CommandRegistry, PaletteCommand
from ui.dialogs.agent_settings_dialog import AgentSettingsDialog
from ui.dialogs.prompt_editor_dialog import PromptEditorDialog
from ui.file_tree_controller import FileTreeController
from ui.indexing_controller import IndexingController
from ui.menus import AppMenuBar
from ui.ollama_controller import OllamaController
from ui.widgets.command_palette import CommandPalette
from ui.widgets.status_dashboard import StatusDashboard


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
        self.project_path: Path | None = None
        self._update_window_title()
        self.setMinimumSize(1200, 800)
        self.resize(1600, 1000)
        db_path = get_app_data_dir() / "sessions.db"
        self.session_store = SessionStore(db_path)
        from core.roles import RoleManager

        self.role_manager = RoleManager()
        self.prompt_manager = SystemPromptManager()
        self.prompt_watcher = PromptWatcher(self.prompt_manager, self.role_manager)
        self.prompt_watcher.start()
        self.indexer = FileIndexer(config, self.project_path)
        self.vector_store: VectorStore | None = None
        self._reranker: CrossEncoderReranker | None = None
        self._palette_registry = CommandRegistry()
        self._palette_dialog: CommandPalette | None = None

        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()
        self._register_palette_commands()
        self._install_global_shortcuts()
        self.ollama_controller.check_connection()

        self.chat_panel.settings_changed.connect(self._on_chat_settings_changed)
        self.chat_panel.directory_attach_requested.connect(self._on_directory_attach_requested)

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

        # --- Ollama (резервный бэкенд): проверки живут в контроллере ---
        self.ollama_controller = OllamaController(parent=self)
        self.ollama_controller.models_synced.connect(self._on_ollama_models_synced)
        self.ollama_controller.check_progress.connect(self._on_model_check_progress)
        self.ollama_controller.model_checked.connect(self._on_model_checked)
        self.ollama_controller.check_finished.connect(self._on_model_check_finished)
        self.ollama_controller.check_state_changed.connect(self._on_ollama_check_state)
        self.ollama_controller.check_refused.connect(self._on_ollama_check_refused)

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

        self.search_cache = SearchCache(get_app_data_dir() / "cache")
        self.chat_panel.set_search_cache(self.search_cache)

        self.main_splitter.setSizes([320, 800])
        layout.addWidget(self.main_splitter, stretch=1)

        self.chat_panel.project_tree_toggled.connect(self._on_project_tree_toggled)
        self.chat_panel.slm_models_requested.connect(self._open_slm_manager)

    @Slot(object)
    def _on_show_diff(self, payload: dict) -> None:
        """[9.8 + 9.1] Открывает Monaco DiffEditor с original↔modified."""
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
        self._switch_to_editor()

    @Slot(str)
    def _show_file_diff(self, path: str) -> None:
        """[9.8.3 + 9.1] Показать diff файла в редакторе (HEAD ↔ рабочая копия)."""
        if hasattr(self, "editor_widget"):
            self.editor_widget.show_git_diff_for_file(path)
            self._switch_to_editor()

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
        """Центральная панель: QStackedWidget (чат / редактор).

        Чат — базовый экран, не вкладка. Вкладки есть только в редакторе
        (внутри `EditorWidget.tab_bar`) и только для файлов. При закрытии
        последней вкладки — автоматический возврат к чату.
        """
        from ui.web_editor.editor_widget import EditorWidget

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.center_stack = QStackedWidget()
        default_model = self.config.default_model if hasattr(self.config, "default_model") else "llama3.2"
        self.chat_panel = ChatPanel(
            session_store=self.session_store,
            base_url=self.config.backends.ollama_url or "http://localhost:11434",
            model=default_model,
        )
        self.chat_panel.set_project_open(False)
        self.chat_panel.agent_panel_toggle.connect(self._toggle_side_panel)

        self.chat_panel.set_registry(self.ollama_controller.registry)
        self.chat_panel.set_role_manager(self.role_manager)
        self.chat_panel.set_prompt_manager(self.prompt_manager)
        self.chat_panel.refresh_models_requested.connect(self._start_model_check)
        self.chat_panel.diff_requested.connect(self._on_show_diff)
        self.center_stack.addWidget(self.chat_panel)  # index 0
        self.editor_widget = EditorWidget()
        self.editor_widget.set_file_reader(self._read_file)
        self.editor_widget.set_file_writer(self._write_file)
        self.editor_widget.set_theme(self.config.theme)
        self.editor_widget.tabs_changed.connect(self._on_editor_tabs_changed)
        self.editor_widget.back_to_chat_requested.connect(self._show_chat_tab)
        self.center_stack.addWidget(self.editor_widget)  # index 1
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

        layout.addWidget(self.center_stack)
        return panel

    @Slot(int)
    def _on_editor_tabs_changed(self, count: int) -> None:
        """Реакция на изменение числа вкладок в редакторе.

        * ``count == 0`` → вкладок нет, возвращаем чат (базовый экран);
        * ``count > 0`` → открыт хотя бы один файл, переключаемся в редактор.

        Раньше эта проверка была внутри `_close_tab` (защита индекса 0).
        Теперь чат не вкладка — `_close_tab` удалён.
        """
        if count == 0:
            self._switch_to_chat()
        else:
            self._switch_to_editor()

    def _switch_to_chat(self) -> None:
        """Показывает страницу чата (базовый экран)."""
        if hasattr(self, "center_stack"):
            self.center_stack.setCurrentIndex(0)
            logger.debug("Переключено на чат")

    def _switch_to_editor(self) -> None:
        """Показывает страницу редактора."""
        if hasattr(self, "center_stack"):
            self.center_stack.setCurrentIndex(1)
            logger.debug("Переключено на редактор")

    @Slot()
    def _diff_current_editor_file(self) -> None:
        """Ctrl+D: diff текущего файла редактора с HEAD."""
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
        """Смена темы: Monaco Editor + ChatPanel (пузыри, reasoning, tool-карточки).
        Прокидывает смену темы в Monaco Editor.
        """
        if hasattr(self, "editor_widget"):
            self.editor_widget.set_theme(theme)
        if hasattr(self, "chat_panel") and self.chat_panel is not None:
            try:
                self.chat_panel.set_theme(theme)
            except Exception:
                logger.debug("chat_panel.set_theme упал", exc_info=True)

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
            self.file_tree_controller.git_delegate.refresh()
            return True
        except Exception as e:
            logger.error(f"Ошибка записи файла: {e}")
            return False

    def open_file_in_editor(self, file_path: str, line: int = 0):
        """Открывает файл в редакторе и переключает на страницу редактора.

        Чат больше не вкладка — `EditorWidget.open_file` сам
        добавляет файл в свой `tab_bar`. `tabs_changed` эмитится, но мы
        дополнительно принудительно переключаемся, чтобы реакция была
        мгновенной (важно при первом открытии файла из дерева).
        """
        self.editor_widget.open_file(file_path, line)
        self._switch_to_editor()

    def _setup_menu(self) -> None:
        """Создаёт меню-бар и подключает его сигналы.

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
        # Замер производительности.
        self.menu_bar.benchmark_requested.connect(self._open_benchmark)
        self.menu_bar.edit_prompts_requested.connect(self._open_prompt_editor)
        self.menu_bar.manage_sessions_requested.connect(self._open_session_manager)
        self.menu_bar.edit_iterations_requested.connect(self._edit_agent_iterations)
        self.menu_bar.memory_requested.connect(self._open_memory_dialog)

        self.menu_bar.toggle_status_pill_requested.connect(self._on_pill_visibility_changed)

        # Палитра команд.
        self.menu_bar.command_palette_requested.connect(self._open_command_palette)
        self.menu_bar.permissions_requested.connect(self._open_permissions)

        # Профили использования.
        self.menu_bar.profile_selected.connect(self._on_menu_profile_selected)
        self.menu_bar.profile_editor_requested.connect(self._open_profile_editor)

        # Help
        self.menu_bar.run_wizard_requested.connect(self._run_wizard)
        self.menu_bar.about_requested.connect(self._show_about)

    def _open_benchmark(self) -> None:
        """Открывает диалог замеров производительности.

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
        """Экспорт текущего чата в Markdown/HTML."""
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
        """Статус-бар с единой приборной панелью."""
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

        # Инжектим панель в ChatPanel для синхронизации бэкенда/pipeline/tok/s.
        if getattr(self, "chat_panel", None) is not None:
            self.chat_panel.set_dashboard(self.dashboard)

        self.dashboard.pill_visibility_changed.connect(self._on_pill_visibility_changed)
        self.dashboard.apply_visibility_settings(self.config.hidden_status_pills)

    def _install_global_shortcuts(self) -> None:
        """Ctrl+Shift+P — глобальный shortcut на всё окно.

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
        """Регистрирует команды палитры.

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
                    callback=self._exit_from_palette,
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
                        # partial вместо lambda: mypy выводит сигнатуру Callable[[], None]
                        # из bound-метода и одного аргумента. Lambda с default-arg
                        # сбивает inference («Cannot infer type of lambda»).
                        callback=partial(self._apply_profile_from_palette, p),
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

    def _exit_from_palette(self) -> None:
        """Хелпер для палитры: закрывает окно (без возврата bool).

        `QWidget.close()` возвращает bool (принято ли событие), а
        `PaletteCommand.callback` ждёт `Callable[[], None]`. Оборачиваем,
        чтобы не «тащить» bool-возврат в API палитры.
        """
        self.close()

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
        """Переключает SLM ⇄ Ollama (если Ollama включён в конфиге).

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
            self.ollama_controller.check_connection()

        new_priority = ["ollama", "local_slm"] if target == "ollama" else ["local_slm", "ollama"]
        cfg.backends.priority = new_priority
        save_config(cfg)
        if self.chat_panel:
            self.chat_panel.router.set_priority(new_priority)
            self.chat_panel._emit_backend_status()
            self.chat_panel._sync_dashboard()
            self.chat_panel._update_model_selector_visibility()
        self.status_bar.showMessage(f"Бэкенд переключён: {target.upper()}", 3000)

    @Slot(str)
    def _on_backend_status_changed(self, label: str) -> None:
        """Проброс в приборную панель (совместимость)."""
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
        """Обновляет лейбл с текущей веткой Git в шапке левой панели."""
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
        """Добавляет проект в список недавних и перерисовывает меню."""
        from core.config import save_config

        projects = self.config.recent_projects
        if path in projects:
            projects.remove(path)
        projects.insert(0, path)
        self.config.recent_projects = projects[:10]
        save_config(self.config)
        self.menu_bar.set_recent_projects(self.config.recent_projects)

    def _open_recent_project(self, path: str) -> None:
        """Открывает проект из списка недавних."""
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
        """Внутренняя логика открытия проекта (без диалога)."""
        self.project_path = project_path
        logger.info(f"Открыт проект: {self.project_path}")
        self.project_label.setText(f"Проект: {self.project_path.name}")
        self.file_tree_controller.set_project_root(self.project_path)
        self.project_title_label.setText(self.project_path.name)
        self._update_window_title()
        project_str = str(self.project_path)
        is_first_open = project_str not in (self.config.recent_projects or [])
        try:
            from core.config import load_config as _load_cfg

            _cfg = _load_cfg()
            saved_visible = bool(getattr(_cfg, "show_project_tree", False))
        except Exception:
            saved_visible = False

        show_tree = True if is_first_open else saved_visible

        self.left_panel.setVisible(show_tree)
        if hasattr(self.chat_panel, "project_tree_btn"):
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(show_tree)
            self.chat_panel.project_tree_btn.blockSignals(False)
        action = self.menu_bar.project_structure_action
        if action is not None:
            action.blockSignals(True)
            action.setChecked(show_tree)
            action.blockSignals(False)
        self._switch_to_chat()
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
        self.indexer = FileIndexer(self.config, self.project_path)
        sessions = self.session_store.list_sessions(str(project_path))
        if sessions:
            last = sessions[0]  # уже отсортированы по updated_at DESC
            history = self.session_store.get_history(last.id, limit=1000)
            self.chat_panel.set_session_id(last.id)
            self.chat_panel.load_history(history)

        self.chat_panel.set_project_root(str(self.project_path))
        self.chat_panel.set_project_open(True)
        self.indexing_controller.configure(
            project_path=self.project_path,
            indexer=self.indexer,
            vector_store=self.vector_store,
        )
        self._add_recent_project(str(self.project_path))
        self.reindex_btn.setVisible(True)
        self.reindex_btn.setEnabled(True)

        project_settings = ProjectSettings.load(self.project_path)
        if project_settings.model:
            self.chat_panel.apply_settings(project_settings.model_dump())
        else:
            if self.config.last_model:
                self.chat_panel.set_model(self.config.last_model)
            if self.config.last_mode:
                self.chat_panel.set_mode_by_name(self.config.last_mode)
        try:
            from state.project_settings import snapshot_git_state

            git_state = snapshot_git_state(self.project_path)
            if git_state["branch"]:
                project_settings.git_branch = git_state["branch"]
                project_settings.git_hash = git_state["hash"]
                project_settings.git_last_sync = git_state["timestamp"]
                project_settings.save(self.project_path)
                logger.info(f"git context: {git_state['branch']}@{git_state['hash']}")
        except Exception:
            logger.debug("git snapshot не сохранён", exc_info=True)
        if hasattr(self, "show_reasoning_check"):
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(self.chat_panel.get_show_reasoning())
            self.show_reasoning_check.blockSignals(False)

        self._start_indexing()

    def _get_reranker(self) -> CrossEncoderReranker | None:
        """Возвращает реранкер по текущему конфигу (или ``None``, если выключен)."""
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
        """
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            logger.warning(f"@dir: путь недоступен: {path}")
            return
        try:
            from state.project_settings import snapshot_git_state

            git_state = snapshot_git_state(path)
            if git_state["branch"]:
                logger.info(f"@dir git context: {git_state['branch']}@{git_state['hash']}")
        except Exception:
            pass

        logger.info(f"@dir: прикрепляю директорию {path}")
        self.project_path = path
        self.project_label.setText(f"Проект: {path.name}")

        self.file_tree_controller.set_project_root(path)
        self.project_title_label.setText(path.name)
        self._update_window_title()
        self.left_panel.setVisible(True)
        if hasattr(self.chat_panel, "project_tree_btn"):
            self.chat_panel.project_tree_btn.blockSignals(True)
            self.chat_panel.project_tree_btn.setChecked(True)
            self.chat_panel.project_tree_btn.blockSignals(False)
        action = self.menu_bar.project_structure_action
        if action is not None:
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)
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
        self._switch_to_chat()
        self.indexer = FileIndexer(self.config, self.project_path)
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

    def _reindex_project(self) -> None:
        """Запускает полную переиндексацию."""
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
        """Обновляет прогресс-бар индексации."""
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
        """Обрабатывает завершение индексации (UI)."""
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
        """Открывает AgentSettingsDialog (замена правой панели)."""
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
            allow_arbitrary_shell=bool(getattr(self.config, "allow_arbitrary_shell", False)),
            parent=self,
        )
        dialog.settings_applied.connect(self._on_agent_settings_applied)
        dialog.exec()

    @Slot(dict)
    def _on_agent_settings_applied(self, settings: dict) -> None:
        """Применяет настройки агента к ChatPanel и конфигу."""
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

        self.config.agent_max_iterations = int(settings["max_iterations"])
        self.config.chat_max_iterations = max(1, min(int(settings["max_iterations"]), 15))
        self.chat_panel.set_show_reasoning(bool(settings["show_reasoning"]))
        if hasattr(self, "show_reasoning_check"):
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(bool(settings["show_reasoning"]))
            self.show_reasoning_check.blockSignals(False)

        tools = settings.get("tools") or {}
        for name, enabled in tools.items():
            policy = ToolPolicy.AUTO if enabled else ToolPolicy.EXCLUDED
            self.chat_panel.set_tool_policy(name, policy)
            if hasattr(self, "_tool_checkboxes") and name in self._tool_checkboxes:
                cb = self._tool_checkboxes[name]
                cb.blockSignals(True)
                cb.setChecked(enabled)
                cb.blockSignals(False)

        if hasattr(self, "rag_check"):
            self.rag_check.blockSignals(True)
            self.rag_check.setChecked(bool(settings.get("rag_enabled", True)))
            self.rag_check.blockSignals(False)

        self.config.allow_arbitrary_shell = bool(settings.get("allow_arbitrary_shell", False))
        mcp_enabled = bool(settings.get("mcp_enabled", False))
        if self.config.mcp.enabled != mcp_enabled:
            self.config.mcp.enabled = mcp_enabled
            if mcp_enabled:
                if self.chat_panel is not None:
                    self.chat_panel._start_mcp_init()
            else:
                try:
                    from tools.mcp_ops import MCPManager

                    MCPManager.instance().shutdown_sync()
                except Exception:
                    logger.debug("MCP shutdown failed", exc_info=True)
        b_mode = str(settings.get("browser_mode", "isolated"))
        b_ack = bool(settings.get("browser_full_ack", False))
        if b_mode == "full" and not b_ack:
            logger.warning("browser: full без явного подтверждения — откатываю на isolated")
            b_mode = "isolated"
            b_ack = False
        self.config.browser.mode = b_mode  # type: ignore[assignment]
        self.config.browser.full_access_acknowledged = b_ack
        try:
            import asyncio as _aio

            from tools.browser_ops import shutdown_browser

            _loop = _aio.new_event_loop()
            try:
                _loop.run_until_complete(shutdown_browser())
            finally:
                _loop.close()
        except Exception:
            logger.debug("browser shutdown на смене режима", exc_info=True)

        try:
            from core.config import save_config

            save_config(self.config)
        except Exception:
            logger.debug("Не удалось сохранить настройки агента", exc_info=True)

        self.status_bar.showMessage("Настройки агента применены", 3000)

    @Slot()
    def _show_chat_tab(self) -> None:
        """Возвращает на страницу чата. Вкладки файлов сохраняются."""
        self._switch_to_chat()

    @Slot()
    def _show_editor_tab(self) -> None:
        """Переключает на страницу редактора.

        Если не открыто ни одного файла — редактор пуст, показывать
        нечего. Сообщаем пользователю и остаёмся в чате.
        """
        if self.editor_widget.tab_count() == 0:
            self.status_bar.showMessage("Редактор пуст: откройте файл из дерева проекта.", 3000)
            self._switch_to_chat()
            return
        self._switch_to_editor()

    @Slot(bool)
    def _toggle_project_structure(self, visible: bool) -> None:
        """Показывает/скрывает левую панель (F4, меню)."""
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
        """Пользователь кликнул «📁» в ChatPanel — переключаем дерево."""
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
        """Профиль сменился — синхронизируем UI MainWindow.

        * чекбокс «Показывать размышления» (из ChatPanel);
        * статус-бар — короткое сообщение;
        * селектор профиля (на случай вызова из ChatPanel API).
        """
        self.menu_bar._refresh_profiles_menu()
        if hasattr(self, "show_reasoning_check") and self.chat_panel:
            value = self.chat_panel.get_show_reasoning()
            self.show_reasoning_check.blockSignals(True)
            self.show_reasoning_check.setChecked(value)
            self.show_reasoning_check.blockSignals(False)
        if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
            self.chat_panel._profiles_selector.set_profile(profile_id)
        profile = get_profile_by_id(self.config.profiles, profile_id)
        if profile is not None:
            self.status_bar.showMessage(f"{profile.icon} Профиль: {profile.name}", 4000)
        logger.info(f"Профиль изменён: {profile_id}")

    @Slot(str)
    def _on_menu_profile_selected(self, profile_id: str) -> None:
        """Пользователь выбрал профиль из меню View → Профили."""
        if not self.chat_panel:
            return
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            profile = get_profile_by_id(cfg.profiles, profile_id)
            if profile is None:
                return
            if hasattr(self.chat_panel, "_profiles_selector") and self.chat_panel._profiles_selector:
                self.chat_panel._profiles_selector.set_profile(profile_id)
            self.chat_panel._on_profile_changed(profile)
        except Exception as e:
            logger.warning(f"Не удалось применить профиль из меню: {e}")

    def _open_profile_editor(self) -> None:
        """Открывает ProfileEditorDialog. После сохранения перечитывает
        конфиг, обновляет селектор профиля в ChatPanel и подменю в меню.
        """
        from ui.dialogs.profile_editor_dialog import ProfileEditorDialog

        dlg = ProfileEditorDialog(self.config, self)
        dlg.profiles_saved.connect(self._on_profiles_saved)
        dlg.exec()

    @Slot()
    def _on_profiles_saved(self) -> None:
        """Профили сохранены: перечитываем конфиг и синхронизируем UI."""
        try:
            from core.config import load_config

            self.config = load_config()
            self.menu_bar.set_config(self.config)
        except Exception as e:
            logger.warning(f"Не удалось перечитать конфиг после редактора профилей: {e}")

        if self.chat_panel is not None:
            self.chat_panel.refresh_profiles_selector()
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
                    if "ollama" in self.config.backends.enabled:
                        models = [m.model for m in self.config.models]
                        self.chat_panel.update_models(models)
                        self.chat_panel.set_model(self.config.default_model)
                    self.chat_panel.set_project_open(self.project_path is not None)
            except Exception as e:
                logger.error(f"Ошибка загрузки конфига после визарда: {e}")
        else:
            logger.info("Визард отменён пользователем")

    def _open_settings(self) -> None:
        """Открывает диалог настроек."""
        from ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(config=self.config, project_path=self.project_path, parent=self)
        if dlg.exec():
            from core.config import load_config

            try:
                self.config = load_config(self.project_path)
                self.menu_bar.set_config(self.config)
                self.indexer = FileIndexer(self.config, self.project_path)
                logger.info("Настройки сохранены, конфиг перезагружен")
            except Exception as e:
                logger.error(f"Ошибка перезагрузки конфига: {e}")

    @Slot(str, bool)
    def _on_pill_visibility_changed(self, pill_id: str, visible: bool) -> None:
        """Централизованно обрабатывает скрытие/отображение плиток.

        Вызывается как из контекстного меню StatusDashboard, так и из
        верхнего меню View → Элементы статус-бара.
        """
        hidden = list(self.config.hidden_status_pills)
        if visible and pill_id in hidden:
            hidden.remove(pill_id)
        elif not visible and pill_id not in hidden:
            hidden.append(pill_id)
        self.config.hidden_status_pills = hidden
        from core.config import save_config

        save_config(self.config)
        self.dashboard.apply_visibility_settings(hidden)
        self.menu_bar.update_status_pills_menu(hidden)

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
        if self._palette_dialog is not None:
            with contextlib.suppress(Exception):
                self._palette_dialog.close()
            self._palette_dialog = None
        self._save_project_settings()
        if self.chat_panel:
            model = self.chat_panel.get_current_model()
            if model:
                self.config.last_model = model
            self.config.last_mode = self.chat_panel.get_settings().get("mode", "chat")
            from core.config import save_config

            logger.info(
                f"🔄 closeEvent: last_model='{self.config.last_model}', last_mode='{self.config.last_mode}'"
            )
            save_config(self.config)
            logger.info("✅ Конфиг сохранён в closeEvent")
        self._stop_all_threads()
        self.ollama_controller.shutdown()
        self.prompt_watcher.stop()

        if self.chat_panel and hasattr(self.chat_panel, "shutdown"):
            try:
                self.chat_panel.shutdown()
            except Exception:
                logger.debug("ChatPanel.shutdown failed", exc_info=True)

        if hasattr(self, "file_tree_controller"):
            self.file_tree_controller.set_project_root(None)

        event.accept()

    def _save_project_settings(self) -> None:
        """Сохраняет настройки проекта (+ git-снимок, 9.4)."""
        if not self.project_path:
            return
        settings = self.chat_panel.get_settings()
        try:
            from state.project_settings import snapshot_git_state

            existing = ProjectSettings.load(self.project_path)
            git_state = snapshot_git_state(self.project_path)
            if git_state["branch"]:
                existing.git_branch = git_state["branch"]
                existing.git_hash = git_state["hash"]
                existing.git_last_sync = git_state["timestamp"]
            settings["git_branch"] = existing.git_branch
            settings["git_hash"] = existing.git_hash
            settings["git_last_sync"] = existing.git_last_sync
        except Exception:
            logger.debug("git snapshot при save не обновлён", exc_info=True)
        ProjectSettings(**settings).save(self.project_path)

    def _stop_all_threads(self) -> None:
        """Останавливает фоновые потоки."""
        if hasattr(self, "indexing_controller"):
            self.indexing_controller.stop()
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel._stop_generation()

    def _open_model_manager(self):
        from ui.dialogs.model_manager_dialog import ModelManagerDialog

        dlg = ModelManagerDialog(self.ollama_controller.registry, self.ollama_controller.base_url, self)
        dlg.exec()

    def _open_slm_manager(self):
        """Открывает диалог управления локальными SLM-моделями."""
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
        self.chat_panel._pool_load_failed = False
        self.chat_panel._ensure_orchestrator()
        self.status_bar.showMessage("Загрузка двух моделей (planner + analyst)…", 5000)

    _OLLAMA_CHECK_UI: dict[str, tuple[str, str, int]] = {
        "checking": ("🔄 Проверка поддержки инструментов моделями...", "⏹ Стоп", 0),
        "stopping": ("⏹ Останавливаю проверку (до 10 сек)...", "⏹ Останавливаю…", 0),
        "idle": ("⏹ Проверка остановлена", "🔄", 3000),
        "background": (
            "⏹ Останавливаю проверку в фоне (завершится при следующем ответе Ollama)",
            "⏹ Останавливаю…",
            5000,
        ),
    }

    def _start_model_check(self) -> None:
        """Кнопка 🔄 / палитра: старт-стоп проверки tools (логика в контроллере)."""
        self.ollama_controller.toggle_tools_check()

    @Slot(list)
    def _on_ollama_models_synced(self, models: list) -> None:
        """Реестр Ollama синхронизирован — обновляем список моделей в ChatPanel."""
        if self.chat_panel is None:
            return
        try:
            if getattr(self.chat_panel, "registry", None) is None:
                self.chat_panel.set_registry(self.ollama_controller.registry)
            else:
                self.chat_panel.update_models(models)
        except Exception:
            logger.debug("Не удалось обновить модели в ChatPanel", exc_info=True)
        if not self.chat_panel.get_current_model() and models:
            registry = self.ollama_controller.registry
            first_local = next(
                (m for m in models if (info := registry.get(m)) is not None and info.is_local),
                models[0],
            )
            self.chat_panel.set_model(first_local)
        self.chat_panel._sync_dashboard()

    @Slot(str)
    def _on_ollama_check_state(self, state: str) -> None:
        text, btn, timeout = self._OLLAMA_CHECK_UI.get(state, ("", "🔄", 0))
        if text:
            self.status_bar.showMessage(text, timeout)
        self.chat_panel.set_refresh_button_text(btn)

    @Slot(str)
    def _on_ollama_check_refused(self, reason: str) -> None:
        if reason == "disabled":
            QMessageBox.information(
                self,
                "Ollama выключена",
                "Проверка поддержки инструментов выполняется через Ollama API.\n\n"
                "Ollama сейчас выключена в списке активных бэкендов: "
                "Файл → Настройки → Бэкенды LLM.",
            )
            return
        QMessageBox.information(
            self,
            "Ollama недоступна",
            f"Ollama не отвечает на {self.ollama_controller.base_url}, поэтому "
            "проверка поддержки инструментов невозможна.\n\n"
            "Запустите сервис в терминале: `ollama serve`.",
        )

    @Slot(str, int, int)
    def _on_model_check_progress(self, model_name: str, current: int, total: int):
        self.status_bar.showMessage(f"🔄 Проверка {current}/{total}: {model_name}...")

    @Slot(str, bool)
    def _on_model_checked(self, model_name: str, supports: bool):
        """Обновляет UI для проверенной модели (публичный API ChatPanel)."""
        self.chat_panel.update_model_status(model_name)

    @Slot(dict)
    def _on_model_check_finished(self, results: dict):
        self.status_bar.showMessage("✅ Проверка моделей завершена", 5000)
        self.chat_panel.set_checking_state(False)
        self.chat_panel.set_refresh_button_text("🔄")
        self.chat_panel.update_model_list()
        self.chat_panel.update_models(self.ollama_controller.available_models)

    @Slot()
    def _open_memory_dialog(self) -> None:
        """открывает диалог просмотра/удаления памяти агента."""
        from core.config import load_config

        try:
            cfg = load_config()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать конфиг: {e}")
            return
        if not cfg.memory.enabled:
            QMessageBox.information(
                self,
                "Память отключена",
                "Включите память в настройках (⚙ → Память агента) или в config.yaml: `memory.enabled: true`.",
            )
            return
        if self.chat_panel is None:
            return
        service = self.chat_panel._ensure_memory_service()
        if service is None:
            QMessageBox.warning(
                self,
                "Память недоступна",
                "Не удалось инициализировать SQLite. См. лог.",
            )
            return
        from ui.dialogs.memory_dialog import MemoryDialog

        dlg = MemoryDialog(service, self)
        dlg.exec()

    def _open_session_manager(self):
        from ui.dialogs.session_manager_dialog import SessionManagerDialog

        dlg = SessionManagerDialog(self.session_store, self.chat_panel, self)
        dlg.exec()

    def _open_permissions(self) -> None:
        """Открывает Permissions Dashboard."""
        if self.chat_panel is None:
            return
        try:
            registry = self.chat_panel.registry_for_permissions()
        except Exception:
            logger.exception("Не удалось собрать реестр для Permissions")
            return
        active_profile = self.chat_panel.get_active_profile()
        current_mode = self.chat_panel._current_mode()  # приватный, но это UI-окно
        session_overrides = dict(self.config.session_overrides)
        import contextlib

        with contextlib.suppress(Exception):
            session_overrides.update(self.chat_panel._current_session_overrides())

        from ui.dialogs.permissions_dashboard import PermissionsDashboard

        dlg = PermissionsDashboard(
            registry,
            self.config,
            active_profile=active_profile,
            current_mode=current_mode,
            session_overrides=session_overrides,
            parent=self,
        )
        dlg.exec()
        try:
            self.config.session_overrides = dict(
                dlg._session_overrides  # noqa: SLF001
            )
            from core.config import save_config

            save_config(self.config)
            if self.chat_panel is not None:
                self.chat_panel._sync_dashboard()
        except Exception:
            logger.debug(
                "Не удалось сохранить session overrides после закрытия",
                exc_info=True,
            )
