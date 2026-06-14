"""Главное окно OlliDesk."""

from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from typing_extensions import override

from agents.ollama_client import OllamaClient
from core.config import AppConfig
from state.session_store import SessionStore
from ui.chat_panel import ChatPanel


class MainWindow(QMainWindow):
    """Главное окно приложения."""

    def __init__(self, config: AppConfig):
        """
        Инициализация главного окна.

        Args:
            config: Конфигурация приложения
        """
        super().__init__()
        self.config = config

        self.setWindowTitle("OlliDesk — Local AI Coding Assistant")
        self.setMinimumSize(1200, 800)
        self.resize(1600, 1000)

        # Инициализация хранилища сессий
        db_path = Path.home() / ".ollidesk" / "sessions.db"
        self.session_store = SessionStore(db_path)

        # Инициализация клиента Ollama
        self.ollama_client = OllamaClient(
            base_url="http://localhost:11434",
        )
        self._ollama_connected = False
        self._available_models: list[str] = []

        self._setup_ui()
        self._setup_menu()
        self._setup_status_bar()
        self._check_ollama_connection()

        logger.info("Главное окно создано")

    def _setup_ui(self) -> None:
        """Настраивает UI с тремя панелями."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QHBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # QSplitter для трех панелей
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Левая панель (20%) — Дерево файлов
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)

        # Центральная панель (50%) — Чат / Редактор
        center_panel = self._create_center_panel()
        splitter.addWidget(center_panel)

        # Правая панель (30%) — Настройки агента
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)

        # Устанавливаем пропорции (20% / 50% / 30%)
        splitter.setSizes([320, 800, 480])

        layout.addWidget(splitter)

    def _create_left_panel(self) -> QWidget:
        """Создает левую панель (дерево файлов)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)

        label = QLabel("📁 Проект")
        label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(label)

        placeholder = QLabel("Дерево файлов проекта\n(будет реализовано в Фазе 3)")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: gray; padding: 20px;")
        layout.addWidget(placeholder)

        layout.addStretch()
        return panel

    def _create_center_panel(self) -> QWidget:
        """Создает центральную панель (чат)."""
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
        return self.chat_panel

    def _create_right_panel(self) -> QWidget:
        """Создает правую панель (настройки агента)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)

        label = QLabel("⚙️ Настройки агента")
        label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(label)

        placeholder = QLabel(
            "Выбор модели\nРежим (Chat/Plan/Agent)\nTemperature\nTools"
            "\n(будет реализовано в Фазе 5)"
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: gray; padding: 20px;")
        layout.addWidget(placeholder)

        layout.addStretch()
        return panel

    def _setup_menu(self) -> None:
        """Настраивает меню."""
        menubar = self.menuBar()

        # Меню File
        file_menu = menubar.addMenu("&File")

        open_project_action = QAction("&Open Project...", self)
        open_project_action.setShortcut("Ctrl+O")
        open_project_action.triggered.connect(self._open_project)
        file_menu.addAction(open_project_action)

        file_menu.addSeparator()

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Меню Help
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_status_bar(self) -> None:
        """Настраивает статус-бар."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Индикатор Ollama
        self.ollama_status_label = QLabel("● Ollama: Проверка...")
        self.ollama_status_label.setStyleSheet("color: gray; padding: 5px;")
        self.status_bar.addPermanentWidget(self.ollama_status_label)

        # Информация о проекте
        self.project_label = QLabel("Проект: не открыт")
        self.project_label.setStyleSheet("padding: 5px;")
        self.status_bar.addWidget(self.project_label)

    def _check_ollama_connection(self) -> None:
        """Проверяет подключение к Ollama."""
        QTimer.singleShot(500, self._do_check_ollama)

    def _do_check_ollama(self) -> None:
        """Выполняет проверку Ollama."""
        import asyncio

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

    def _update_ollama_status(self, ok: bool = False, models: list[str] | None = None) -> None:
        """Обновляет статус Ollama в статус-баре."""
        if ok and models:
            self.ollama_status_label.setText(f"● Ollama: Подключен ({len(models)} моделей)")
            self.ollama_status_label.setStyleSheet("color: green; padding: 5px;")
            self.chat_panel.update_models(models)
            if models:
                self.chat_panel.set_model(models[0])
        else:
            self.ollama_status_label.setText("● Ollama: Не подключен")
            self.ollama_status_label.setStyleSheet("color: red; padding: 5px;")

    def _open_project(self) -> None:
        """Открывает диалог выбора проекта."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if folder:
            logger.info(f"Открыт проект: {folder}")
            self.project_label.setText(f"Проект: {Path(folder).name}")
            # TODO: Реализовать открытие проекта через ProjectManager в Фазе 5

    def _open_settings(self) -> None:
        """Открывает настройки (заглушка)."""
        QMessageBox.information(self, "Settings", "Настройки будут реализованы в Фазе 5")

    def _show_about(self) -> None:
        """Показывает окно About."""
        QMessageBox.about(
            self,
            "About OlliDesk",
            "OlliDesk v1.0.0\n\n"
            "Локальный AI-ассистент для написания кода.\n\n"
            "Powered by Ollama + PySide6",
        )

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Обрабатывает закрытие окна."""
        logger.info("Закрытие главного окна")
        event.accept()
