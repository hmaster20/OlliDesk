"""Главное окно OlliDesk.

Правки относительно базовой версии (см. журнал изменений):

* `ProjectState` хранит состояние per-project в
  `<project>/.ollidesk/project_state.json` вместо единого глобального файла.
  Это устраняет переиндексацию при каждом открытии того же проекта.
* `IndexingThread._run_indexing` при нуле изменений эмитит `indexing_finished`
  сразу, без прогресс-бара — панель не показывает «Индексация: 0/0 файлов».
* `_on_index_progress` при `total == 0` скрывает прогресс немедленно.
"""

import asyncio
import json
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QModelIndex, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFileSystemModel,
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
    QTreeView,
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
from fs.indexer import FileIndexer, FileMetadata
from fs.reranker import CrossEncoderReranker
from fs.vector_store import VectorStore
from state.project_settings import ProjectSettings
from state.session_store import SessionStore
from ui.chat import ChatPanel
from ui.dialogs.prompt_editor_dialog import PromptEditorDialog
from ui.file_tree_delegate import GitStatusDelegate


class IndexingThread(QThread):
    """Поток для индексации проекта."""

    progress_updated = Signal(int, int)  # current, total
    indexing_finished = Signal()
    indexing_summary = Signal(list, list)
    indexing_error = Signal(str)

    def __init__(
        self,
        indexer: FileIndexer,
        vector_store: VectorStore,
        project_path: Path,
        previous_state: dict,
    ):
        super().__init__()
        self.indexer = indexer
        self.vector_store = vector_store
        self.project_path = project_path
        self.previous_state = previous_state

    def run(self):
        """Запускает индексацию."""
        asyncio.run(self._run_indexing())

    async def _run_indexing(self):
        """Выполняет индексацию проекта."""
        try:
            # Инкрементальный скан
            new_or_modified, deleted = await self.indexer.incremental_scan(
                self.project_path, self.previous_state
            )

            total = len(new_or_modified)
            paths = [m.path for m in new_or_modified]
            # Если изменений нет и удалять нечего — не дёргаем прогресс
            # (эмитим (0, 0), панель поймёт и не покажет прогресс-бар).
            if total == 0 and not deleted:
                logger.info(
                    "Индексация не требуется: изменений нет, "
                    f"состояние проекта актуально ({len(self.previous_state)} файлов)"
                )
                self.indexing_summary.emit([], [])
                self.indexing_finished.emit()
                return

            self.progress_updated.emit(0, total)

            # Удаляем чанки удаленных файлов
            if deleted:
                await self.vector_store.delete_by_file(deleted)

            # Обрабатываем новые и измененные файлы
            for i, file_meta in enumerate(new_or_modified):
                content = file_meta.absolute_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                chunks = self.indexer.chunk_file(
                    content, file_meta.path, file_meta.mtime
                )

                # Удаляем старые чанки файла (если он был изменен)
                if file_meta.path in self.previous_state:
                    await self.vector_store.delete_by_file([file_meta.path])

                # Добавляем новые чанки
                if chunks:
                    await self.vector_store.add_chunks(chunks)

                self.progress_updated.emit(i + 1, total)

            self.indexing_summary.emit(paths, list(deleted))
            self.indexing_finished.emit()
        except Exception as e:
            self.indexing_error.emit(str(e))


class ModelCheckThread(QThread):
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
        import asyncio

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


class ProjectState:
    """Состояние проекта для инкрементальной индексации.

    Хранится per-project в `<project>/.ollidesk/project_state.json` — это
    критично: глобальный файл (как было раньше) сбрасывался при каждом
    переключении проекта, и возврат к первому проекту давал полную
    переиндексацию всех файлов заново (см. `Add of existing embedding ID`
    в консоли ChromaDB).

    Все методы — статические: экземпляр не нужен, путь к проекту
    передаётся явно.
    """

    STATE_DIRNAME = ".ollidesk"
    STATE_FILENAME = "project_state.json"

    @classmethod
    def _state_file(cls, project_path: Path) -> Path:
        return project_path / cls.STATE_DIRNAME / cls.STATE_FILENAME

    @classmethod
    def load(cls, project_path: Path) -> dict:
        """Загружает состояние проекта.

        Args:
            project_path: Корень проекта.

        Returns:
            dict: {relative_path: FileMetadata}
        """
        state_file = cls._state_file(project_path)
        if not state_file.exists():
            return {}
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка загрузки состояния проекта: {e}")
            return {}

        result = {}
        for path_str, meta_dict in raw.items():
            # absolute_path восстанавливаем из project_path — в файле
            # он сохранён как строка, но для FileMetadata нужен Path.
            abs_path = project_path / meta_dict["path"]
            meta_dict["absolute_path"] = str(abs_path)
            try:
                result[path_str] = FileMetadata.model_validate(meta_dict)
            except Exception as e:
                logger.warning(f"Ошибка валидации state для {path_str}: {e}")
        logger.debug(f"Состояние проекта загружено: {len(result)} файлов")
        return result

    @classmethod
    def save(cls, files: dict, project_path: Path) -> None:
        """Сохраняет состояние проекта.

        Args:
            files: {relative_path: FileMetadata}
            project_path: Корень проекта.
        """
        state_file = cls._state_file(project_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for path, meta in files.items():
            serializable[path] = {
                "path": meta.path,
                "sha256": meta.sha256,
                "mtime": meta.mtime,
                "size_bytes": meta.size_bytes,
                "extension": meta.extension,
                "absolute_path": str(meta.absolute_path),
            }
        state_file.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"Состояние проекта сохранено: {len(files)} файлов")

    @classmethod
    def clear(cls, project_path: Path) -> None:
        """Удаляет файл состояния (для полной переиндексации)."""
        state_file = cls._state_file(project_path)
        if state_file.exists():
            state_file.unlink()


class MainWindow(QMainWindow):
    """Главное окно приложения."""

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
        self._previous_state: dict = {}

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
        self._indexing_thread: IndexingThread | None = None
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
        self.chat_panel.directory_attach_requested.connect(
            self._on_directory_attach_requested
        )
        self.chat_panel.backend_status_changed.connect(
            self._on_backend_status_changed
        )
        self.chat_panel.source_activated.connect(self.open_file_in_editor)

        logger.info("Главное окно создано")

    def _setup_ui(self) -> None:
        """Настраивает UI: левая панель + центр + кнопка + правая панель."""
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
        """Создает левую панель (дерево файлов проекта + индексация)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Заголовок: иконка + имя проекта + текущая ветка Git
        header_layout = QHBoxLayout()
        self.project_icon_label = QLabel("📁")
        self.project_icon_label.setStyleSheet(
            "font-size: 16px; padding: 8px 0 4px 10px;"
        )
        header_layout.addWidget(self.project_icon_label)

        self.project_title_label = QLabel("Проект")
        self.project_title_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 8px 10px 4px 0;"
        )
        header_layout.addWidget(self.project_title_label)

        self.branch_label = QLabel("")
        self.branch_label.setToolTip("Текущая ветка Git")
        self.branch_label.setStyleSheet(
            "font-size: 12px; color: #98c379; padding: 8px 10px 4px 0;"
        )
        header_layout.addWidget(self.branch_label)

        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Кнопка Reindex наверху
        self.reindex_btn = QPushButton("🔄 Переиндексировать")
        self.reindex_btn.setStyleSheet(
            "font-size: 13px; padding: 8px; margin: 4px 10px;"
        )
        self.reindex_btn.clicked.connect(self._reindex_project)
        self.reindex_btn.setVisible(False)
        layout.addWidget(self.reindex_btn)

        # Дерево файлов (пустое до открытия проекта)
        self.file_tree = QTreeView()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAnimated(True)
        self.file_tree.setIndentation(16)
        self.file_tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.file_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.PreventContextMenu
        )
        self.file_tree.setStyleSheet("font-size: 13px; border: none;")
        self.file_tree.doubleClicked.connect(self._on_file_tree_clicked)
        self.file_tree.clicked.connect(self._on_file_tree_clicked)
        self._git_delegate = GitStatusDelegate(self.file_tree)
        self.file_tree.setItemDelegate(self._git_delegate)
        self._git_delegate.branch_changed.connect(self._on_branch_changed)
        layout.addWidget(self.file_tree, stretch=1)

        # Пустая модель — никакие диски не показываем
        self.file_system_model: QFileSystemModel | None = None
        self._empty_model = QStandardItemModel()
        self._empty_model.setHorizontalHeaderLabels(["Проект не открыт"])
        self.file_tree.setModel(self._empty_model)
        self.file_tree.setRootIndex(QModelIndex())

        # Прогресс-бар индексации
        self.index_progress = QProgressBar()
        self.index_progress.setRange(0, 100)
        self.index_progress.setValue(0)
        self.index_progress.setVisible(False)
        self.index_progress.setStyleSheet(
            "font-size: 12px; padding: 2px; margin: 4px 10px;"
        )
        layout.addWidget(self.index_progress)

        # Статус индексации
        self.index_status_label = QLabel("")
        self.index_status_label.setStyleSheet(
            "color: gray; font-size: 12px; padding: 0 10px;"
        )
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

        default_model = (
            self.config.default_model
            if hasattr(self.config, "default_model")
            else "llama3.2"
        )
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
        self.tab_widget.addTab(self.editor_widget, "📝 Редактор")

        layout.addWidget(self.tab_widget)
        return panel

    def _on_chat_settings_changed(self, settings: dict):
        self.config.last_model = settings.get("model", "")
        self.config.last_mode = settings.get("mode", "chat")
        from core.config import save_config

        logger.info(
            f"⚙️ settings_changed: model={settings.get('model')}, "
            f"mode={settings.get('mode')}"
        )
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
            self._git_delegate.refresh()
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
        self.temp_value_label.setStyleSheet(
            "font-size: 12px; color: gray; padding: 0 10px;"
        )
        self.temp_slider.valueChanged.connect(
            lambda v: self.temp_value_label.setText(f"{v / 100:.2f}")
        )
        layout.addWidget(self.temp_value_label)

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
        self.iterations_label.setStyleSheet(
            "font-size: 12px; padding: 8px 10px 0;"
        )
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
            cb.toggled.connect(
                lambda checked, k=tool_key: self._on_tool_toggled(k, checked)
            )
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
        """Настраивает меню."""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        open_project_action = QAction("&Open Project...", self)
        open_project_action.setShortcut("Ctrl+O")
        open_project_action.triggered.connect(self._open_project)
        file_menu.addAction(open_project_action)

        # Недавние проекты
        self._recent_projects_menu = file_menu.addMenu("Recent Projects")
        self._recent_actions: list[QAction] = []
        self._update_recent_projects_menu()

        file_menu.addSeparator()

        self.confirm_web_search_action = QAction(
            "Подтверждать запросы в интернет", self
        )
        self.confirm_web_search_action.setCheckable(True)
        self.confirm_web_search_action.setChecked(
            self._config_confirm_web_search()
        )
        self.confirm_web_search_action.toggled.connect(
            self._on_confirm_web_search_toggled
        )
        file_menu.addAction(self.confirm_web_search_action)

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&View")

        self.show_chat_action = QAction("Show &Chat Tab", self)
        self.show_chat_action.setShortcut("Ctrl+Shift+C")
        self.show_chat_action.triggered.connect(self._show_chat_tab)
        view_menu.addAction(self.show_chat_action)

        self.show_editor_action = QAction("Show &Editor Tab", self)
        self.show_editor_action.setShortcut("Ctrl+Shift+E")
        self.show_editor_action.triggered.connect(self._show_editor_tab)
        view_menu.addAction(self.show_editor_action)

        self.project_structure_action = QAction(
            "Показать/Скрыть &структуру проекта", self
        )
        self.project_structure_action.setShortcut("F4")
        self.project_structure_action.setCheckable(True)
        self.project_structure_action.setChecked(False)
        self.project_structure_action.toggled.connect(
            self._toggle_project_structure
        )
        view_menu.addAction(self.project_structure_action)

        view_menu.addSeparator()

        self.theme_action = QAction("Toggle Dark/Light Theme", self)
        self.theme_action.setShortcut("Ctrl+T")
        self.theme_action.triggered.connect(self._toggle_theme)

        view_menu.addSeparator()
        manage_models_action = QAction("Manage Models...", self)
        manage_models_action.triggered.connect(self._open_model_manager)
        view_menu.addAction(manage_models_action)

        view_menu.addSeparator()
        slm_models_action = QAction("Локальные модели (SLM)...", self)
        slm_models_action.triggered.connect(self._open_slm_manager)
        view_menu.addAction(slm_models_action)

        view_menu.addAction(self.theme_action)

        view_menu.addSeparator()
        edit_prompts_action = QAction("Edit Prompts & Roles...", self)
        edit_prompts_action.triggered.connect(self._open_prompt_editor)
        view_menu.addAction(edit_prompts_action)

        view_menu.addSeparator()
        manage_sessions_action = QAction("Manage Sessions...", self)
        manage_sessions_action.triggered.connect(self._open_session_manager)
        view_menu.addAction(manage_sessions_action)

        view_menu.addSeparator()
        self.agent_iterations_action = QAction(
            "Лимит итераций агента...", self
        )
        self.agent_iterations_action.triggered.connect(
            self._edit_agent_iterations
        )
        view_menu.addAction(self.agent_iterations_action)

        help_menu = menubar.addMenu("&Help")

        wizard_action = QAction("Run Setup &Wizard...", self)
        wizard_action.triggered.connect(self._run_wizard)
        help_menu.addAction(wizard_action)

        help_menu.addSeparator()

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _config_confirm_web_search(self) -> bool:
        """Читает из конфига признак подтверждения web_search.

        Returns:
            True, если перед запросом в интернет нужно показывать диалог.
        """
        try:
            from core.config import load_config

            cfg = load_config()
            return cfg.confirm.enabled or cfg.confirm.web_search
        except Exception as e:
            logger.warning(f"Не удалось прочитать настройку confirm: {e}")
            return False

    @Slot(bool)
    def _on_confirm_web_search_toggled(self, checked: bool) -> None:
        """Сохраняет в конфиг признак подтверждения web_search.

        Args:
            checked: True — показывать диалог, False — выполнять сразу.
        """
        try:
            from core.config import load_config, save_config

            cfg = load_config()
            cfg.confirm.web_search = checked
            save_config(cfg)
            logger.info(f"Подтверждение web_search: {'вкл' if checked else 'выкл'}")
        except Exception as e:
            logger.exception(
                f"Не удалось сохранить настройку confirm.web_search: {e}"
            )

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
            logger.exception(
                f"Не удалось сохранить лимит итераций агента: {e}"
            )

    def _open_prompt_editor(self):
        dlg = PromptEditorDialog(self.prompt_manager, self.role_manager, self)
        if dlg.exec():
            # После сохранения обновляем тултипы
            self.chat_panel._update_mode_tooltips()
            # Можно перезагрузить роли в комбобоксе
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

    def _do_check_ollama(self) -> None:
        """Выполняет проверку Ollama."""

        async def _check():
            try:
                async with self.ollama_client as client:
                    models = await client.list_models()
                self._available_models = [m.name for m in models]
                self._ollama_connected = True
                return True, self._available_models
            except Exception:
                self._ollama_connected = False
                self._available_models = []
                return False, []

        loop = asyncio.new_event_loop()
        try:
            ok, models = loop.run_until_complete(_check())
        finally:
            loop.close()

        self._update_ollama_status(ok, models)

    def _update_ollama_status(
        self, ok: bool = False, models: list[str] | None = None
    ) -> None:
        if ok and models:
            self.ollama_status_label.setText(
                f"● Ollama: Подключен ({len(models)} моделей)"
            )
            self.ollama_status_label.setStyleSheet(
                "color: green; padding: 5px;"
            )
            self.model_registry.sync_with_ollama(models)
            if (
                not hasattr(self.chat_panel, "registry")
                or self.chat_panel.registry is None
            ):
                self.chat_panel.set_registry(self.model_registry)
            else:
                self.chat_panel.update_models(models)
            # Устанавливаем модель ТОЛЬКО если она ещё не выбрана
            current_model = self.chat_panel.get_current_model()
            if not current_model and models:
                # Используем моржовый оператор (:=), чтобы сохранить объект
                # и проверить его на None
                first_local = next(
                    (
                        m
                        for m in models
                        if (info := self.model_registry.get(m)) is not None
                        and info.is_local
                    ),
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

    def _on_file_tree_clicked(self, index: QModelIndex) -> None:
        """Открывает файл из дерева проекта в редакторе."""
        if self.file_system_model is None:
            return
        file_path = self.file_system_model.filePath(index)
        if Path(file_path).is_file():
            self.open_file_in_editor(file_path)

    @Slot(str)
    def _on_branch_changed(self, branch: str) -> None:
        """Обновляет лейбл с текущей веткой Git в шапке левой панели."""
        if branch:
            self.branch_label.setText(f"🌿 {branch}")
        else:
            self.branch_label.setText("")

    def _open_project(self) -> None:
        """Открывает диалог выбора проекта."""
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку проекта"
        )
        if folder:
            self._do_open_project(Path(folder))

    def _add_recent_project(self, path: str) -> None:
        """Добавляет проект в список недавних."""
        from core.config import save_config

        projects = self.config.recent_projects
        if path in projects:
            projects.remove(path)
        projects.insert(0, path)
        self.config.recent_projects = projects[:10]
        save_config(self.config)
        self._update_recent_projects_menu()

    def _update_recent_projects_menu(self) -> None:
        """Обновляет подменю недавних проектов."""
        self._recent_projects_menu.clear()
        self._recent_actions.clear()
        for path in self.config.recent_projects:
            action = QAction(path, self)
            action.triggered.connect(
                lambda checked, p=path: self._open_recent_project(p)
            )
            self._recent_projects_menu.addAction(action)
            self._recent_actions.append(action)
        if not self.config.recent_projects:
            action = QAction("(no recent projects)", self)
            action.setEnabled(False)
            self._recent_projects_menu.addAction(action)
            self._recent_actions.append(action)

    def _open_recent_project(self, path: str) -> None:
        """Открывает проект из списка недавних."""
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
                self._update_recent_projects_menu()

    def _do_open_project(self, project_path: Path) -> None:
        """Внутренняя логика открытия проекта (без диалога)."""
        self.project_path = project_path
        logger.info(f"Открыт проект: {self.project_path}")
        self.project_label.setText(f"Проект: {self.project_path.name}")
        self.file_system_model = QFileSystemModel()
        self.file_system_model.setRootPath(str(self.project_path))
        self.file_tree.setModel(self.file_system_model)
        self.file_tree.setRootIndex(
            self.file_system_model.index(str(self.project_path))
        )
        self.file_tree.hideColumn(1)
        self.file_tree.hideColumn(2)
        self.file_tree.hideColumn(3)
        self.project_title_label.setText(self.project_path.name)
        self._git_delegate.set_repo_path(str(self.project_path))
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
        else:
            # создаём новую сессию, но не сразу, а при первом сообщении
            pass

        self.chat_panel.set_project_root(str(self.project_path))
        self.chat_panel.set_project_open(True)
        # Состояние проекта читаем из <project>/.ollidesk/project_state.json
        self._previous_state = ProjectState.load(self.project_path)
        self._add_recent_project(str(self.project_path))
        self.reindex_btn.setVisible(True)
        self.reindex_btn.setEnabled(True)

        # Восстанавливаем настройки проекта (они приоритетнее глобальных)
        project_settings = ProjectSettings.load(self.project_path)
        if project_settings.model:
            self.chat_panel.apply_settings(project_settings.model_dump())
        else:
            # Если проектных нет, применяем глобальные
            if self.config.last_model:
                self.chat_panel.set_model(self.config.last_model)
            if self.config.last_mode:
                mode_map = {"chat": 0, "plan": 1, "agent": 2}
                idx = mode_map.get(self.config.last_mode, 2)
                self.chat_panel.mode_combo.setCurrentIndex(idx)

        self._start_indexing()

    def _get_reranker(self) -> CrossEncoderReranker | None:
        """Возвращает реранкер по текущему конфигу (или ``None``, если выключен).

        Экземпляр кэшируется и пересоздаётся только при смене модели — модель
        кросс-энкодера загружается лениво, не хочется перезагружать её каждый раз.
        """
        settings = self.config.rerank
        if not settings.enabled:
            return None
        if (
            self._reranker is None
            or self._reranker.model_name != settings.model
        ):
            self._reranker = CrossEncoderReranker(settings.model)
        return self._reranker

    def _embed_models(self) -> dict[str, str]:
        """Карта ``категория -> модель`` для раздельных эмбеддингов (без ``None``)."""
        cfg = self.config.embed_models
        return {
            category: model
            for category, model in (("code", cfg.code), ("docs", cfg.docs))
            if model
        }

    def _on_directory_attach_requested(self, path_str: str) -> None:
        """Подключает каталог из команды @dir без пересоздания сессии чата.

        Отличие от `_do_open_project`: текущая сессия и история чата не трогаются,
        каталог не добавляется в список недавних. Пользователь может держать
        открытым один проект, а вопросы задавать по другому каталогу.

        Args:
            path_str: Путь к подключаемому каталогу.
        """
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            logger.warning(f"@dir: путь недоступен: {path}")
            return

        logger.info(f"@dir: прикрепляю директорию {path}")
        self.project_path = path
        self.project_label.setText(f"Проект: {path.name}")

        self.file_system_model = QFileSystemModel()
        self.file_system_model.setRootPath(str(path))
        self.file_tree.setModel(self.file_system_model)
        self.file_tree.setRootIndex(self.file_system_model.index(str(path)))
        self.file_tree.hideColumn(1)
        self.file_tree.hideColumn(2)
        self.file_tree.hideColumn(3)
        self.project_title_label.setText(path.name)
        self._git_delegate.set_repo_path(str(path))
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

        # Состояние — из <project>/.ollidesk/project_state.json
        self._previous_state = ProjectState.load(path)
        self.reindex_btn.setVisible(True)
        self.reindex_btn.setEnabled(True)

        # Индексация предыдущего каталога должна завершиться, иначе новая не запустится
        if self._indexing_thread and self._indexing_thread.isRunning():
            logger.info(
                "@dir: ждём завершения индексации предыдущего каталога"
            )
            self._indexing_thread.quit()
            self._indexing_thread.wait(3000)

        self._start_indexing()

    def _reindex_project(self) -> None:
        """Запускает полную переиндексацию."""
        if not self.project_path:
            return

        # Сбрасываем предыдущее состояние для полной переиндексации
        self._previous_state = {}
        if self.project_path:
            ProjectState.clear(self.project_path)
        self._start_indexing()

    def _start_indexing(self) -> None:
        """Запускает индексацию проекта в фоновом потоке."""
        if not self.project_path or not self.vector_store:
            return

        if self._indexing_thread and self._indexing_thread.isRunning():
            logger.warning("Индексация уже выполняется")
            return

        # Показываем прогресс
        self.index_progress.setVisible(True)
        self.index_progress.setValue(0)
        self.index_status_label.setVisible(True)
        self.index_status_label.setText("⏳ Индексация...")
        self.reindex_btn.setEnabled(False)

        self._indexing_thread = IndexingThread(
            indexer=self.indexer,
            vector_store=self.vector_store,
            project_path=self.project_path,
            previous_state=self._previous_state,
        )
        self._indexing_thread.progress_updated.connect(self._on_index_progress)
        self._indexing_thread.indexing_summary.connect(self._on_indexing_summary)
        self._indexing_thread.indexing_finished.connect(self._on_index_finished)
        self._indexing_thread.indexing_error.connect(self._on_index_error)
        self._indexing_thread.start()

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
        self.index_status_label.setText(
            f"⏳ Индексация: {current}/{total} файлов"
        )

    @Slot()
    def _on_index_finished(self):
        """Обрабатывает завершение индексации."""
        # Прогресс мог остаться от предыдущего запуска — прячем его.
        if self.index_progress.isVisible():
            self.index_progress.setValue(self.index_progress.maximum())
            self.index_status_label.setText("✅ Индексация завершена")

        # Сохраняем новое состояние (сканируем снова для актуальных метаданных)
        self._save_project_state()

        # Скрываем прогресс через 3 секунды
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

    def _save_project_state(self) -> None:
        """Сохраняет состояние проекта после индексации."""
        if not self.project_path:
            return

        # Создаем локальную переменную. Теперь mypy на 100% уверен,
        # что path имеет тип Path
        path = self.project_path

        async def _get_state():
            files = await self.indexer.scan_project(path)
            return {f.path: f for f in files}

        loop = asyncio.new_event_loop()
        try:
            state = loop.run_until_complete(_get_state())
            ProjectState.save(state, path)
            self._previous_state = state
        finally:
            loop.close()

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
        logger.debug(
            f"Панель структуры проекта: "
            f"{'показана' if visible else 'скрыта'}"
        )

    def _run_wizard(self) -> None:
        """Принудительно запускает визард настройки."""
        from ui.dialogs.wizard import SetupWizard

        try:
            wizard = SetupWizard()
        except Exception as e:
            logger.exception("Не удалось открыть визард")
            QMessageBox.critical(self, "Ошибка визарда",
                f"Не удалось открыть мастер настройки:\n{e}")
            return
        if wizard.exec():
            from core.config import load_config

            try:
                self.config = load_config()
                if self.chat_panel:
                    models = [m.model for m in self.config.models]
                    self.chat_panel.update_models(models)
                    default = self.config.default_model
                    self.chat_panel.set_model(default)
                    self.chat_panel.set_project_open(
                        self.project_path is not None
                    )
                from core.config import save_config

                save_config(self.config)
            except Exception as e:
                logger.error(f"Ошибка загрузки конфига после визарда: {e}")
        else:
            logger.info("Визард отменён пользователем")

    def _open_settings(self) -> None:
        """Открывает диалог настроек."""
        from ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(
            config=self.config, project_path=self.project_path, parent=self
        )
        if dlg.exec():
            # Перезагружаем конфиг
            from core.config import load_config

            try:
                self.config = load_config(self.project_path)
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
            "OlliDesk v1.0.0\n\n"
            "Локальный AI-ассистент для написания кода.\n\n"
            "Powered by Ollama + PySide6",
        )

    def closeEvent(self, event):
        logger.info("Закрытие главного окна")
        self._save_project_settings()
        if self.chat_panel:
            self.config.last_model = self.chat_panel.get_current_model()
            mode_map = {0: "chat", 1: "plan", 2: "agent"}
            self.config.last_mode = mode_map.get(
                self.chat_panel.mode_combo.currentIndex(), "chat"
            )
            from core.config import save_config

            logger.info(
                f"🔄 closeEvent: last_model='{self.config.last_model}', "
                f"last_mode='{self.config.last_mode}'"
            )
            save_config(self.config)
            logger.info("✅ Конфиг сохранён в closeEvent")
        self._stop_all_threads()
        self._stop_model_check()
        self.prompt_watcher.stop()
        # Сбрасываем git-состояние делегата, чтобы фоновый poll_timer остановился.
        self._git_delegate.set_repo_path(None)
        event.accept()

    def _save_project_settings(self) -> None:
        """Сохраняет настройки проекта."""
        if not self.project_path:
            return
        settings = self.chat_panel.get_settings()
        ProjectSettings(**settings).save(self.project_path)

    def _stop_all_threads(self) -> None:
        """Останавливает все фоновые потоки."""
        if self._indexing_thread and self._indexing_thread.isRunning():
            self._indexing_thread.quit()
            self._indexing_thread.wait(2000)
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel._stop_generation()
        # Останавливаем клиент Ollama
        if hasattr(self, "ollama_client") and self.ollama_client:
            import asyncio

            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(
                    self.ollama_client.__aexit__(None, None, None)
                )
                loop.close()
            except Exception:
                pass

    def _open_model_manager(self):
        from ui.dialogs.model_manager_dialog import ModelManagerDialog

        dlg = ModelManagerDialog(
            self.model_registry, self.ollama_client.base_url, self
        )
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
            except Exception as e:
                logger.warning(f"Не удалось перезагрузить конфиг: {e}")

            if hasattr(self, "chat_panel") and self.chat_panel:
                self.chat_panel.reload_slm()

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

        self.status_bar.showMessage(
            "🔄 Проверка поддержки инструментов моделями..."
        )
        # Меняем текст кнопки на "Стоп" (она остаётся активной)
        self.chat_panel.refresh_models_btn.setText("⏹ Стоп")
        self._is_checking = True

        self._model_check_thread = ModelCheckThread(
            self.ollama_client.base_url, self.model_registry
        )
        self._model_check_thread.progress.connect(
            self._on_model_check_progress
        )
        self._model_check_thread.model_checked.connect(self._on_model_checked)
        self._model_check_thread.finished.connect(
            self._on_model_check_finished
        )
        self._model_check_thread.start()

    def _stop_model_check(self):
        """Останавливает проверку моделей."""
        if (
            self._model_check_thread
            and self._model_check_thread.isRunning()
        ):
            self._model_check_thread.cancel()
            self._model_check_thread.wait(2000)
            if self._model_check_thread.isRunning():
                self._model_check_thread.terminate()
                self._model_check_thread.wait(1000)
        self._is_checking = False
        self.chat_panel.refresh_models_btn.setText("🔄")
        self.status_bar.showMessage("⏹ Проверка остановлена", 3000)

    @Slot(str, int, int)
    def _on_model_check_progress(
        self, model_name: str, current: int, total: int
    ):
        self.status_bar.showMessage(
            f"🔄 Проверка {current}/{total}: {model_name}..."
        )

    @Slot(str, bool)
    def _on_model_checked(self, model_name: str, supports: bool):
        """Обновляет UI для проверенной модели."""
        # Обновляем статус в комбобоксе и метке
        self.chat_panel.update_model_status(model_name)
        # Если это текущая модель, обновляем статусную метку
        if self.chat_panel.model_combo.currentData() == model_name:
            self.chat_panel._update_model_status(model_name)

    @Slot(dict)
    def _on_model_check_finished(self, results: dict):
        self.status_bar.showMessage("✅ Проверка моделей завершена", 5000)
        self.chat_panel.set_checking_state(False)
        self.chat_panel.refresh_models_btn.setText("🔄")
        self._is_checking = False
        # Обновляем полный список, чтобы подтянуть все изменения
        self.chat_panel._update_model_list()
        self.chat_panel.update_models(self._available_models)

    def _open_session_manager(self):
        from ui.dialogs.session_manager_dialog import SessionManagerDialog

        dlg = SessionManagerDialog(self.session_store, self.chat_panel, self)
        dlg.exec()