"""Меню главного окна OlliDesk (план 2.4).

Почему отдельный файл:
  * `MainWindow._setup_menu` занимал ~180 строк и держал все обработчики
    в `MainWindow` — это раздувало класс и мешало разделению окна на
    контроллеры (2.4).
  * Меню — самостоятельная единица UI с собственным жизненным циклом
    (создаётся один раз, состояние checkable-пунктов синхронизируется
    с конфигом). Ему не нужны ни `chat_panel`, ни `vector_store`, ни
    другие поля окна — только `AppConfig` (для начального состояния
    галочки «Подтверждать web_search») и список недавних проектов.

Как устроено:
  * `AppMenuBar(QMenuBar)` публикует **сигналы** на каждую команду;
    `MainWindow` подключает их к своим слотам в `_setup_menu`.
  * `set_recent_projects(list[str])` пересобирает подменю «Recent Projects»
    без пересоздания всего меню.
  * `set_config(AppConfig)` — для случая, когда MainWindow перезагружает
    конфиг (`_run_wizard`, `_open_settings`), чтобы галочка
    `confirm.web_search` не сохранялась в устаревший объект.
  * Галочки (project structure, confirm_web_search) — `checkable QAction`;
    их `toggled` транслируется в одноимённый сигнал.
  * `confirm_web_search_action` читает/пишет `AppConfig.confirm` сам —
    чтобы `MainWindow` не дублировал эту логику (было
    `_config_confirm_web_search` и `_on_confirm_web_search_toggled`).

Соглашение по именам: сигналы — `*_requested`, кроме toggled-сигналов,
которые называются `*_toggled(bool)`.
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenuBar

from core.config import AppConfig, save_config


class AppMenuBar(QMenuBar):
    """Меню-бар главного окна с сигналами на команды."""

    # --- File ---
    open_project_requested = Signal()
    open_recent_requested = Signal(str)
    export_chat_requested = Signal()  # [9.6.3]
    confirm_web_search_changed = Signal(bool)
    settings_requested = Signal()
    exit_requested = Signal()

    # --- View ---
    show_chat_requested = Signal()
    show_editor_requested = Signal()
    project_structure_toggled = Signal(bool)
    theme_toggle_requested = Signal()
    manage_models_requested = Signal()
    slm_models_requested = Signal()
    preload_pipeline_requested = Signal()
    # [4.7.B] Замер производительности (TTFT, tok/s, время загрузки).
    benchmark_requested = Signal()
    edit_prompts_requested = Signal()
    manage_sessions_requested = Signal()
    edit_iterations_requested = Signal()
    # [9.9] Палитра команд (Ctrl+Shift+P).
    command_palette_requested = Signal()
    # [9.4] Профили использования.
    profile_selected = Signal(str)  # profile_id
    profile_editor_requested = Signal()

    # --- Help ---
    run_wizard_requested = Signal()
    about_requested = Signal()

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._recent_menu = None
        self._recent_actions: list[QAction] = []

        # checkable-действия — доступны MainWindow для программной синхронизации
        self.project_structure_action: QAction | None = None
        self.confirm_web_search_action: QAction | None = None

        self._build()

    # ------------------------------------------------------------------
    # Построение
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._build_file_menu()
        self._build_view_menu()
        self._build_help_menu()

    def _build_file_menu(self) -> None:
        file_menu = self.addMenu("&Файл")

        open_project = QAction("&Открыть проект…", self)
        open_project.setShortcut(QKeySequence("Ctrl+O"))
        open_project.triggered.connect(self.open_project_requested.emit)
        file_menu.addAction(open_project)

        self._recent_menu = file_menu.addMenu("Недавние проекты")
        self._refresh_recent_menu()

        # [9.6.3] Экспорт текущего чата.
        export_chat = QAction("Экспорт чата…", self)
        export_chat.setShortcut(QKeySequence("Ctrl+Shift+X"))
        export_chat.triggered.connect(self.export_chat_requested.emit)
        file_menu.addAction(export_chat)

        file_menu.addSeparator()

        self.confirm_web_search_action = QAction("Подтверждать запросы в интернет", self)
        self.confirm_web_search_action.setCheckable(True)
        self.confirm_web_search_action.setChecked(self._read_confirm_web_search())
        self.confirm_web_search_action.toggled.connect(self._on_confirm_web_search_toggled)
        file_menu.addAction(self.confirm_web_search_action)

        settings = QAction("&Настройки…", self)
        settings.triggered.connect(self.settings_requested.emit)
        file_menu.addAction(settings)

        file_menu.addSeparator()

        exit_action = QAction("В&ыход", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.exit_requested.emit)
        file_menu.addAction(exit_action)

    def _build_view_menu(self) -> None:
        view_menu = self.addMenu("&Вид")

        show_chat = QAction("Показать вкладку &Чат", self)
        show_chat.setShortcut(QKeySequence("Ctrl+Shift+C"))
        show_chat.triggered.connect(self.show_chat_requested.emit)
        view_menu.addAction(show_chat)

        show_editor = QAction("Показать вкладку &Редактор", self)
        show_editor.setShortcut(QKeySequence("Ctrl+Shift+E"))
        show_editor.triggered.connect(self.show_editor_requested.emit)
        view_menu.addAction(show_editor)

        self.project_structure_action = QAction("Показать/скрыть &структуру проекта", self)
        self.project_structure_action.setShortcut(QKeySequence("F4"))
        self.project_structure_action.setCheckable(True)
        self.project_structure_action.setChecked(False)
        self.project_structure_action.toggled.connect(self.project_structure_toggled.emit)
        view_menu.addAction(self.project_structure_action)

        view_menu.addSeparator()

        theme = QAction("Переключить тему (тёмная / светлая)", self)
        theme.setShortcut(QKeySequence("Ctrl+T"))
        theme.triggered.connect(self.theme_toggle_requested.emit)
        view_menu.addAction(theme)

        view_menu.addSeparator()

        manage_models = QAction("Управление моделями Ollama…", self)
        manage_models.triggered.connect(self.manage_models_requested.emit)
        view_menu.addAction(manage_models)

        slm_models = QAction("Локальные модели (SLM)…", self)
        slm_models.triggered.connect(self.slm_models_requested.emit)
        view_menu.addAction(slm_models)

        # [4.7.B] Замер производительности.
        benchmark_action = QAction("Замер производительности…", self)
        benchmark_action.setToolTip(
            "Прогнать выбранные модели на стандартных/своих промптах, "
            "получить реальные TTFT, tok/s и время загрузки. "
            "CSV / Markdown для docs/BENCHMARK.md."
        )
        benchmark_action.triggered.connect(self.benchmark_requested.emit)
        view_menu.addAction(benchmark_action)

        preload_pipeline = QAction("Предзагрузить 2-model pipeline", self)
        preload_pipeline.setToolTip(
            "Загрузить planner (3B GPU) + analyst (7B CPU) в память сейчас, "
            "не дожидаясь исследовательского вопроса. Требует открытый "
            "проект и две разные установленные модели."
        )
        preload_pipeline.triggered.connect(self.preload_pipeline_requested.emit)
        view_menu.addAction(preload_pipeline)

        view_menu.addSeparator()

        edit_prompts = QAction("Редактор промптов и ролей…", self)
        edit_prompts.triggered.connect(self.edit_prompts_requested.emit)
        view_menu.addAction(edit_prompts)

        view_menu.addSeparator()

        manage_sessions = QAction("Менеджер сессий…", self)
        manage_sessions.triggered.connect(self.manage_sessions_requested.emit)
        view_menu.addAction(manage_sessions)

        view_menu.addSeparator()

        # [9.4] Подменю «Профили»: встроенные + пользовательские (checkable),
        # плюс «Редактор профилей…».
        self._profiles_menu = view_menu.addMenu("Профили")
        self._profile_actions: list[QAction] = []
        self._refresh_profiles_menu()

        edit_profiles = QAction("Редактор профилей…", self)
        edit_profiles.triggered.connect(self.profile_editor_requested.emit)
        view_menu.addAction(edit_profiles)

        view_menu.addSeparator()

        edit_iterations = QAction("Лимит итераций агента…", self)
        edit_iterations.triggered.connect(self.edit_iterations_requested.emit)
        view_menu.addAction(edit_iterations)

        view_menu.addSeparator()

        # [9.9] Палитра команд (Ctrl+Shift+P).
        palette_action = QAction("Палитра команд…", self)
        palette_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        palette_action.triggered.connect(self.command_palette_requested.emit)
        view_menu.addAction(palette_action)

    def _build_help_menu(self) -> None:
        help_menu = self.addMenu("&Справка")

        wizard = QAction("Запустить &мастер настройки…", self)
        wizard.triggered.connect(self.run_wizard_requested.emit)
        help_menu.addAction(wizard)

        help_menu.addSeparator()

        about = QAction("&О программе", self)
        about.triggered.connect(self.about_requested.emit)
        help_menu.addAction(about)

    # ------------------------------------------------------------------
    # Синхронизация с конфигом (после перезагрузки в MainWindow)
    # ------------------------------------------------------------------

    def set_config(self, config: AppConfig) -> None:
        """Обновляет ссылку на конфиг после его перезагрузки в MainWindow.

        `MainWindow._run_wizard` и `_open_settings` делают
        `self.config = load_config(...)` — создают новый объект. Без этого
        метода `AppMenuBar` держал бы старую ссылку и сохранял бы
        `confirm.web_search` в устаревший конфиг.
        """
        self._config = config
        if self.confirm_web_search_action is not None:
            self.confirm_web_search_action.blockSignals(True)
            self.confirm_web_search_action.setChecked(self._read_confirm_web_search())
            self.confirm_web_search_action.blockSignals(False)
        self._refresh_recent_menu()
        # [9.4] Пересобираем подменю профилей (могли измениться).
        self._refresh_profiles_menu()

    # ------------------------------------------------------------------
    # Recent Projects
    # ------------------------------------------------------------------

    def set_recent_projects(self, projects: list[str]) -> None:
        """Пересобирает подменю Recent Projects.

        Вызывается MainWindow после `_add_recent_project` (сохранение в
        config + перерисовка).
        """
        self._config.recent_projects = list(projects)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        if self._recent_menu is None:
            return
        self._recent_menu.clear()
        self._recent_actions.clear()
        projects = list(getattr(self._config, "recent_projects", []) or [])
        for path in projects:
            action = QAction(path, self)
            action.triggered.connect(lambda _checked=False, p=path: self.open_recent_requested.emit(p))
            self._recent_menu.addAction(action)
            self._recent_actions.append(action)
        if not projects:
            placeholder = QAction("(no recent projects)", self)
            placeholder.setEnabled(False)
            self._recent_menu.addAction(placeholder)
            self._recent_actions.append(placeholder)

    # ------------------------------------------------------------------
    # Confirm web_search
    # ------------------------------------------------------------------

    def _read_confirm_web_search(self) -> bool:
        try:
            return bool(self._config.confirm.enabled or self._config.confirm.web_search)
        except Exception as e:
            logger.warning(f"Не удалось прочитать настройку confirm: {e}")
            return False

    def _on_confirm_web_search_toggled(self, checked: bool) -> None:
        try:
            self._config.confirm.web_search = checked
            save_config(self._config)
            logger.info(f"Подтверждение web_search: {'вкл' if checked else 'выкл'}")
        except Exception as e:
            logger.exception(f"Не удалось сохранить настройку confirm.web_search: {e}")
        self.confirm_web_search_changed.emit(checked)

    def _refresh_profiles_menu(self) -> None:
        """[9.4] Пересобирает подменю «Профили» по текущему конфигу."""
        if not hasattr(self, "_profiles_menu") or self._profiles_menu is None:
            return
        self._profiles_menu.clear()
        self._profile_actions.clear()

        try:
            from core.profiles import get_profile_by_id as _get_by_id
        except Exception:
            _get_by_id = None  # type: ignore[assignment]

        active_id = getattr(self._config, "active_profile", "development")
        profiles = list(getattr(self._config, "profiles", []) or [])
        if not profiles:
            placeholder = QAction("(нет профилей)", self)
            placeholder.setEnabled(False)
            self._profiles_menu.addAction(placeholder)
            return

        for p in profiles:
            act = QAction(f"{p.icon} {p.name}", self)
            act.setCheckable(True)
            act.setChecked(p.id == active_id)
            if p.description:
                act.setToolTip(p.description)
            act.triggered.connect(lambda _checked=False, pid=p.id: self.profile_selected.emit(pid))
            self._profiles_menu.addAction(act)
            self._profile_actions.append(act)

    def set_active_profile(self, profile_id: str) -> None:
        """[9.4] Обновляет галочку в подменю (MainWindow вызывает после смены)."""
        for act in self._profile_actions:
            text = act.text()
            # Сопоставляем по tooltip-содержимому бесполезно, храним id в action.data().
            # Проще: сохраняем id в data при построении — переделаем _refresh.
            pass
        # Надёжный путь — пересобрать.
        self._refresh_profiles_menu()
