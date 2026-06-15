"""Визард первого запуска OlliDesk."""

from pathlib import Path

import httpx
from loguru import logger
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)
from typing_extensions import override

from core.config import AppConfig, ModelConfig, ModelRole, ToolPolicy, save_config


class SetupWizard(QWizard):
    """Визард первого запуска."""

    def __init__(self) -> None:
        """Инициализация визарда."""
        super().__init__()
        self.setWindowTitle("OlliDesk — Первоначальная настройка")
        self.setMinimumSize(600, 400)

        self.project_path: Path | None = None
        self.ollama_base_url: str = "http://localhost:11434"
        self.available_models: list[str] = []
        self.selected_chat_model: str | None = None
        self.selected_embed_model: str | None = None

        self._setup_pages()

    def _setup_pages(self) -> None:
        """Настраивает страницы визарда."""
        self.addPage(ProjectSelectionPage(self))
        self.addPage(OllamaConnectionPage(self))
        self.addPage(ModelSelectionPage(self))
        self.addPage(ConfirmationPage(self))

    def accept(self) -> None:
        """Принимает визард и сохраняет конфигурацию."""
        try:
            self._wizard_dismissed = self._read_dismissed()
            config = self._create_config()
            save_config(config)

            # Сохраняем локальный конфиг для проекта
            if self.project_path:
                local_config = AppConfig(
                    version=config.version,
                    schema_version=config.schema_version,
                    default_model=config.default_model,
                    embed_model=config.embed_model,
                    models=config.models,
                    tool_policies=config.tool_policies,
                    rules_file=config.rules_file,
                )
                save_config(local_config, self.project_path)

            logger.info("Конфигурация успешно создана")
            super().accept()
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфигурацию:\n{e}")

    def _read_dismissed(self) -> bool:
        """Читает состояние чекбокса 'Не показывать' со страницы подтверждения."""
        for page in self.findChildren(ConfirmationPage):
            return page.dismiss_checkbox.isChecked()
        return False

    def _create_config(self) -> AppConfig:
        """Создает конфигурацию на основе выбора пользователя."""
        chat_model = self.selected_chat_model or "qwen2.5-coder:7b"
        embed_model = self.selected_embed_model or "nomic-embed-text"

        models = [
            ModelConfig(
                name=chat_model,
                provider="ollama",
                model=chat_model,
                api_base=self.ollama_base_url,
                roles=[ModelRole.CHAT, ModelRole.APPLY],
                capabilities=["tool_use"],
                temperature=0.7,
                max_tokens=2048,
                context_length=8192,
                keep_alive=1800,
            ),
            ModelConfig(
                name=embed_model,
                provider="ollama",
                model=embed_model,
                api_base=self.ollama_base_url,
                roles=[ModelRole.EMBED],
                context_length=8192,
            ),
        ]

        return AppConfig(
            default_model=chat_model,
            embed_model=embed_model,
            models=models,
            tool_policies={
                "write_file": ToolPolicy.ASK_FIRST,
                "web_search": ToolPolicy.ASK_FIRST,
                "read_file": ToolPolicy.AUTO,
                "search_codebase": ToolPolicy.AUTO,
            },
            recent_projects=[str(self.project_path)] if self.project_path else [],
            wizard_dismissed=getattr(self, "_wizard_dismissed", False),
        )


class ProjectSelectionPage(QWizardPage):
    """Страница выбора проекта."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard = wizard  # type: ignore[assignment]
        self.setTitle("Выбор папки проекта")
        self.setSubTitle("Укажите папку, в которой будет храниться конфигурация проекта")

        layout = QVBoxLayout(self)

        label = QLabel("Папка проекта:")
        layout.addWidget(label)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Выберите папку...")
        path_layout.addWidget(self.path_edit)

        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self._browse_folder)
        path_layout.addWidget(browse_btn)

        layout.addLayout(path_layout)

        self.registerField("project_path*", self.path_edit)

    def _browse_folder(self) -> None:
        """Открывает диалог выбора папки."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if folder:
            self.path_edit.setText(folder)
            self.wizard.project_path = Path(folder)


class OllamaConnectionPage(QWizardPage):
    """Страница подключения к Ollama."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard = wizard  # type: ignore[assignment]
        self.setTitle("Подключение к Ollama")
        self.setSubTitle("Укажите адрес Ollama API и проверьте подключение")

        layout = QVBoxLayout(self)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Ollama URL:"))
        self.url_edit = QLineEdit("http://localhost:11434")
        url_layout.addWidget(self.url_edit)

        check_btn = QPushButton("Проверить")
        check_btn.clicked.connect(self._check_connection)
        url_layout.addWidget(check_btn)

        layout.addLayout(url_layout)

        self.status_label = QLabel("Статус: Не проверено")
        self.status_label.setStyleSheet("color: gray; padding: 10px;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.registerField("ollama_url*", self.url_edit)

    def _check_connection(self) -> None:
        """Проверяет подключение к Ollama."""
        url = self.url_edit.text().strip()
        self.wizard.ollama_base_url = url

        self.status_label.setText("Статус: Проверка...")
        self.status_label.setStyleSheet("color: orange; padding: 10px;")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Неопределенный прогресс

        try:
            response = httpx.get(f"{url}/api/tags", timeout=5.0)
            response.raise_for_status()
            data = response.json()

            self.wizard.available_models = [m["name"] for m in data.get("models", [])]

            if self.wizard.available_models:
                self.status_label.setText(
                    f"✅ Успешно! Найдено моделей: {len(self.wizard.available_models)}"
                )
                self.status_label.setStyleSheet("color: green; padding: 10px;")
            else:
                self.status_label.setText(
                    "⚠️ Подключено, но модели не найдены. Загрузите модели через `ollama pull`"
                )
                self.status_label.setStyleSheet("color: orange; padding: 10px;")

        except httpx.ConnectError:
            self.status_label.setText(
                "❌ Ошибка: Не удалось подключиться к Ollama. Убедитесь, что Ollama запущен."
            )
            self.status_label.setStyleSheet("color: red; padding: 10px;")
        except Exception as e:
            self.status_label.setText(f"❌ Ошибка: {e}")
            self.status_label.setStyleSheet("color: red; padding: 10px;")
        finally:
            self.progress_bar.setVisible(False)


class ModelSelectionPage(QWizardPage):
    """Страница выбора моделей."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard = wizard  # type: ignore[assignment]
        self.setTitle("Выбор моделей")
        self.setSubTitle("Выберите модели для чата и векторизации")

        layout = QVBoxLayout(self)

        # Модель для чата
        layout.addWidget(QLabel("Модель для чата (chat/agent):"))
        self.chat_model_combo = QComboBox()
        layout.addWidget(self.chat_model_combo)

        # Модель для эмбеддингов
        layout.addWidget(QLabel("Модель для эмбеддингов (embed):"))
        self.embed_model_combo = QComboBox()
        layout.addWidget(self.embed_model_combo)

        self.registerField("chat_model*", self.chat_model_combo, "currentText")
        self.registerField("embed_model*", self.embed_model_combo, "currentText")

    @override
    def initializePage(self) -> None:
        """Инициализирует страницу (загружает список моделей)."""
        models = self.wizard.available_models

        # Фильтруем модели для чата (исключаем embed-модели)
        chat_models = [m for m in models if "embed" not in m.lower()]
        embed_models = [m for m in models if "embed" in m.lower()]

        self.chat_model_combo.clear()
        self.chat_model_combo.addItems(chat_models if chat_models else models)

        self.embed_model_combo.clear()
        self.embed_model_combo.addItems(embed_models if embed_models else models)

        # Выбираем модели по умолчанию
        if "qwen2.5-coder:7b" in chat_models:
            self.chat_model_combo.setCurrentText("qwen2.5-coder:7b")
        if "nomic-embed-text" in embed_models:
            self.embed_model_combo.setCurrentText("nomic-embed-text")

    @override
    def validatePage(self) -> bool:
        """Валидирует страницу перед переходом к следующей."""
        self.wizard.selected_chat_model = self.chat_model_combo.currentText()
        self.wizard.selected_embed_model = self.embed_model_combo.currentText()

        if not self.wizard.selected_chat_model or not self.wizard.selected_embed_model:
            QMessageBox.warning(self, "Ошибка", "Выберите обе модели")
            return False

        return True


class ConfirmationPage(QWizardPage):
    """Страница подтверждения."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.wizard = wizard  # type: ignore[assignment]
        self.setTitle("Подтверждение")
        self.setSubTitle("Проверьте настройки перед сохранением")

        layout = QVBoxLayout(self)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 10px;")
        layout.addWidget(self.summary_label)

        self.dismiss_checkbox = QCheckBox("Больше не показывать это окно при запуске")
        self.dismiss_checkbox.setChecked(True)
        layout.addWidget(self.dismiss_checkbox)

    @override
    def initializePage(self) -> None:
        """Инициализирует страницу (показывает сводку)."""
        summary = f"""
        <b>Папка проекта:</b> {self.wizard.project_path or 'Не указана'}<br>
        <b>Ollama URL:</b> {self.wizard.ollama_base_url}<br>
        <b>Модель для чата:</b> {self.wizard.selected_chat_model}<br>
        <b>Модель для эмбеддингов:</b> {self.wizard.selected_embed_model}<br>
        <br>
        Конфигурация будет сохранена в:<br>
        • <code>~/.ollidesk/config.yaml</code> (глобально)<br>
        • <code>{self.wizard.project_path}/.ollidesk/config.yaml</code> (локально)
        """
        self.summary_label.setText(summary)
