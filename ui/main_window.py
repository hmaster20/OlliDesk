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
from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from agents.ollama_client import OllamaClient
from core.config import AppConfig
from core.model_registry import ModelRegistry
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
from ui.dialogs.prompt_editor_dialog import PromptEditorDialog
from ui.file_tree_controller import FileTreeController
from ui.indexing_controller import IndexingController
from ui.menus import AppMenuBar


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

        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()
        self._check_ollama_connection()

        # Правая панель скрыта по умолчанию
        self.right_panel.setVisible(False)

        self.chat_panel.settings_changed.connect(self._on_chat_settings_changed)
        self.chat_panel.directory_attach_requested.connect(self._on_directory_attach_requested)
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

        # Правая панель (настройки агента)
        self.right_panel = self._create_right_panel()
        layout.addWidget(self.right_panel)

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

        self.tab_widget.addTab(self.chat_panel, "💬 Чат")

        self.editor_widget = EditorWidget()
        self.editor_widget.set_file_reader(self._read_file)
        self.editor_widget.set_file_writer(self._write_file)
        # 🆕 Инициализация темы Monaco из текущего конфига.
        self.editor_widget.set_theme(self.config.theme)
        self.tab_widget.addTab(self.editor_widget, "📝 Редактор")

        layout.addWidget(self.tab_widget)
        return panel

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
        self.config.last_model = settings.get("model", "")
        self.config.last_mode = settings.get("mode", "chat")
        from core.config import save_config

        logger.info(f"⚙️ settings_changed: model={settings.get('model')}, mode={settings.get('mode')}")
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

    def _create_right_panel(self) -> QWidget:
        """Создает правую панель (настройки агента)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)

        label = QLabel("⚙️ Настройки агента")
        label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(label)

        # Temperature
        temp_label = QLabel("Temperature:")
        temp_label.setStyleSheet("font-size: 12px; padding: 4px 10px 0;")
        layout.addWidget(temp_label)

        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(70)
        self.temp_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.temp_slider.setStyleSheet("font-size: 12px; padding: 0 10px;")
        layout.addWidget(self.temp_slider)

        self.temp_value_label = QLabel("0.70")
        self.temp_value_label.setStyleSheet("font-size: 12px; color: gray; padding: 0 10px;")
        self.temp_slider.valueChanged.connect(lambda v: self.temp_value_label.setText(f"{v / 100:.2f}"))
        layout.addWidget(self.temp_value_label)

        # Тумблер показа reasoning_content в чате (4.8.B).
        self.show_reasoning_check = QCheckBox("Показывать размышления модели")
        self.show_reasoning_check.setChecked(True)
        self.show_reasoning_check.setStyleSheet("font-size: 12px; padding: 8px 10px;")
        self.show_reasoning_check.setToolTip(
            "Если выключено, reasoning_content модели не отображается в чате "
            "(полезно при демонстрации или для уменьшения визуального шума)."
        )
        self.show_reasoning_check.toggled.connect(self._on_show_reasoning_toggled)
        layout.addWidget(self.show_reasoning_check)

        # Max tokens
        tokens_label = QLabel("Max tokens:")
        tokens_label.setStyleSheet("font-size: 12px; padding: 8px 10px 0;")
        layout.addWidget(tokens_label)

        self.tokens_spin = QSpinBox()
        self.tokens_spin.setRange(256, 32768)
        self.tokens_spin.setValue(4096)
        self.tokens_spin.setSingleStep(256)
        self.tokens_spin.setStyleSheet(
            "QSpinBox { font-size: 13px; padding: 4px; margin: 0 10px;"
            " background: transparent; border: 1px solid #555;"
            " border-radius: 4px; color: #ccc; }"
            " QSpinBox::up-button, QSpinBox::down-button {"
            "  border: 1px solid #555; border-radius: 2px; background: #333; }"
            " QSpinBox::up-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-bottom: 6px solid #ccc; }"
            " QSpinBox::down-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-top: 6px solid #ccc; }"
            " QSpinBox:hover { border: 1px solid #1976d2; }"
            " QSpinBox:focus { border: 1px solid #42a5f5; }"
        )
        layout.addWidget(self.tokens_spin)

        # Context window
        ctx_label = QLabel("Context window:")
        ctx_label.setStyleSheet("font-size: 12px; padding: 8px 10px 0;")
        layout.addWidget(ctx_label)

        self.context_spin = QSpinBox()
        self.context_spin.setRange(1024, 128000)
        self.context_spin.setValue(8192)
        self.context_spin.setSingleStep(1024)
        self.context_spin.setStyleSheet(
            "QSpinBox { font-size: 13px; padding: 4px; margin: 0 10px;"
            " background: transparent; border: 1px solid #555;"
            " border-radius: 4px; color: #ccc; }"
            " QSpinBox::up-button, QSpinBox::down-button {"
            "  border: 1px solid #555; border-radius: 2px; background: #333; }"
            " QSpinBox::up-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-bottom: 6px solid #ccc; }"
            " QSpinBox::down-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-top: 6px solid #ccc; }"
            " QSpinBox:hover { border: 1px solid #1976d2; }"
            " QSpinBox:focus { border: 1px solid #42a5f5; }"
        )
        layout.addWidget(self.context_spin)

        # Max iterations (агент)
        self.iterations_label = QLabel("Max iterations (агент):")
        self.iterations_label.setStyleSheet("font-size: 12px; padding: 8px 10px 0;")
        layout.addWidget(self.iterations_label)

        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(1, 50)
        self.iterations_spin.setValue(10)
        self.iterations_spin.setStyleSheet(
            "QSpinBox { font-size: 13px; padding: 4px; margin: 0 10px;"
            " background: transparent; border: 1px solid #555;"
            " border-radius: 4px; color: #ccc; }"
            " QSpinBox::up-button, QSpinBox::down-button {"
            "  border: 1px solid #555; border-radius: 2px; background: #333; }"
            " QSpinBox::up-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-bottom: 6px solid #ccc; }"
            " QSpinBox::down-arrow { image: none;"
            "  border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent;"
            "  border-top: 6px solid #ccc; }"
            " QSpinBox:hover { border: 1px solid #1976d2; }"
            " QSpinBox:focus { border: 1px solid #42a5f5; }"
        )
        layout.addWidget(self.iterations_spin)

        # Инструменты
        self.tools_box = QGroupBox("Инструменты")
        tools_layout = QVBoxLayout(self.tools_box)
        self.tool_checkboxes: dict[str, QCheckBox] = {}
        tool_names = [
            ("read_file", "Чтение файлов"),
            ("list_directory", "Список директории"),
            ("write_file", "Запись файлов"),
            ("search_codebase", "Поиск по коду"),
            ("web_search", "Поиск в интернете"),
            ("get_git_status", "Статус Git"),
            ("create_snapshot", "Снапшот Git"),
            ("undo_last_snapshot", "Откат снапшота"),
        ]
        for tool_key, tool_label in tool_names:
            cb = QCheckBox(tool_label)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 12px; padding: 2px 10px;")
            cb.toggled.connect(lambda checked, k=tool_key: self._on_tool_toggled(k, checked))
            tools_layout.addWidget(cb)
            self.tool_checkboxes[tool_key] = cb
        self.tools_box.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.tools_box)

        # RAG switch
        self.rag_check = QCheckBox("Использовать RAG")
        self.rag_check.setChecked(True)
        self.rag_check.setStyleSheet("font-size: 12px; padding: 8px 10px;")
        layout.addWidget(self.rag_check)

        layout.addStretch()

        # По умолчанию скрыта (показывается только по кнопке)
        panel.setVisible(False)
        return panel

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
        self.menu_bar.edit_prompts_requested.connect(self._open_prompt_editor)
        self.menu_bar.manage_sessions_requested.connect(self._open_session_manager)
        self.menu_bar.edit_iterations_requested.connect(self._edit_agent_iterations)

        # Help
        self.menu_bar.run_wizard_requested.connect(self._run_wizard)
        self.menu_bar.about_requested.connect(self._show_about)

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
        """Настраивает статус-бар."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.ollama_status_label = QLabel("● Ollama: Проверка...")
        self.ollama_status_label.setStyleSheet("color: gray; padding: 5px;")
        self.status_bar.addPermanentWidget(self.ollama_status_label)

        self.backend_status_label = QLabel("● Активный бэкенд: определение...")
        self.backend_status_label.setStyleSheet("color: gray; padding: 5px;")
        self.status_bar.addPermanentWidget(self.backend_status_label)

        self.project_label = QLabel("Проект: не открыт")
        self.project_label.setStyleSheet("padding: 5px;")
        self.status_bar.addWidget(self.project_label)

    @Slot(str)
    def _on_backend_status_changed(self, label: str) -> None:
        """Показывает в статус-баре активный LLM-бэкенд и модель."""
        self.backend_status_label.setText(f"● Модель: {label}")

    def _check_ollama_connection(self) -> None:
        """Проверяет подключение к Ollama только если он разрешён в конфиге."""
        try:
            from core.config import load_config

            cfg = load_config()
            if "ollama" not in cfg.backends.enabled:
                self.ollama_status_label.setText("● Ollama: выключен")
                self.ollama_status_label.setStyleSheet("color: #888; padding: 5px;")
                return
        except Exception:
            return
        QTimer.singleShot(500, self._do_check_ollama)

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

    # 🆕 Слот для фоновой проверки Ollama.
    @Slot(bool, list)
    def _on_ollama_checked(self, ok: bool, models: list) -> None:
        """Обрабатывает результат фоновой проверки Ollama (GUI-поток)."""
        self._available_models = list(models)
        self._ollama_connected = ok and bool(models)
        self._update_ollama_status(ok, models)

    def _update_ollama_status(self, ok: bool = False, models: list[str] | None = None) -> None:
        if ok and models:
            self.ollama_status_label.setText(f"● Ollama: Подключен ({len(models)} моделей)")
            self.ollama_status_label.setStyleSheet("color: green; padding: 5px;")
            self.model_registry.sync_with_ollama(models)
            if not hasattr(self.chat_panel, "registry") or self.chat_panel.registry is None:
                self.chat_panel.set_registry(self.model_registry)
            else:
                self.chat_panel.update_models(models)
            # Устанавливаем модель ТОЛЬКО если она ещё не выбрана
            current_model = self.chat_panel.get_current_model()
            if not current_model and models:
                first_local = next(
                    (m for m in models if (info := self.model_registry.get(m)) is not None and info.is_local),
                    models[0],
                )
                self.chat_panel.set_model(first_local)
        else:
            self.ollama_status_label.setText("● Ollama: Не подключен")
            self.ollama_status_label.setStyleSheet("color: red; padding: 5px;")

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
                "Project not found",
                f"Project path does not exist:\n{path}",
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
        """Переключает видимость правой панели."""
        if hasattr(self, "right_panel"):
            visible = not self.right_panel.isVisible()
            self.right_panel.setVisible(visible)

    def _on_tool_toggled(self, tool_key: str, checked: bool) -> None:
        """Обрабатывает переключение инструмента."""
        from core.config import ToolPolicy

        policy = ToolPolicy.AUTO if checked else ToolPolicy.EXCLUDED
        self.chat_panel.set_tool_policy(tool_key, policy)

    @Slot(bool)
    def _on_show_reasoning_toggled(self, checked: bool) -> None:
        """Прокидывает переключение reasoning в ChatPanel."""
        if self.chat_panel:
            self.chat_panel.set_show_reasoning(checked)

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
        """Показывает или скрывает левую панель со структурой проекта (F4).

        Args:
            visible: True — показать панель, False — скрыть.
        """
        self.left_panel.setVisible(visible)
        logger.debug(f"Панель структуры проекта: {'показана' if visible else 'скрыта'}")

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
        """Показывает окно About."""
        QMessageBox.about(
            self,
            "About OlliDesk",
            "OlliDesk v1.0.0\n\nЛокальный AI-ассистент для написания кода.\n\nPowered by Ollama + PySide6",
        )

    def closeEvent(self, event):
        logger.info("Закрытие главного окна")
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
                # reload_slm внутри вызывает reset_pipeline — пайплайн
                # пересоздастся при следующем запросе с новыми моделями.
                self.chat_panel.reload_slm()
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

        self.status_bar.showMessage("🔄 Проверка поддержки инструментов моделями...")
        self.chat_panel.set_refresh_button_text("⏹ Стоп")
        self._is_checking = True

        self._model_check_thread = ModelCheckThread(self.ollama_client.base_url, self.model_registry)
        self._model_check_thread.progress.connect(self._on_model_check_progress)
        self._model_check_thread.model_checked.connect(self._on_model_checked)
        self._model_check_thread.finished.connect(self._on_model_check_finished)
        self._model_check_thread.start()

    def _stop_model_check(self):
        """Останавливает проверку моделей."""
        if self._model_check_thread and self._model_check_thread.isRunning():
            self._model_check_thread.cancel()
            self._model_check_thread.wait(2000)
            if self._model_check_thread.isRunning():
                self._model_check_thread.terminate()
                self._model_check_thread.wait(1000)
        self._is_checking = False
        self.chat_panel.set_refresh_button_text("🔄")
        self.status_bar.showMessage("⏹ Проверка остановлена", 3000)

    @Slot(str, int, int)
    def _on_model_check_progress(self, model_name: str, current: int, total: int):
        self.status_bar.showMessage(f"🔄 Проверка {current}/{total}: {model_name}...")

    @Slot(str, bool)
    def _on_model_checked(self, model_name: str, supports: bool):
        """Обновляет UI для проверенной модели."""
        self.chat_panel.update_model_status(model_name)
        if self.chat_panel.model_combo.currentData() == model_name:
            self.chat_panel._update_model_status(model_name)

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