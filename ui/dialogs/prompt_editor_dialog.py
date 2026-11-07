"""Диалог редактирования системных промтов и ролей."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
)

from core.roles import RoleManager
from core.system_prompts import SystemPromptManager

_SAMPLE_CONTEXT = {
    "project_name": "MyProject",
    "language": "русский",
    "project_root": r"C:\Projects\MyProject",
    "project_map": "MyProject/\n  src/\n    main.py\n  README.md",
}


def _strip_frontmatter(text: str) -> str:
    """Убирает YAML-frontmatter роли (между ---), оставляя текст промпта."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


class PromptEditorDialog(QDialog):
    def __init__(self, prompt_manager: SystemPromptManager, role_manager: RoleManager, parent=None):
        super().__init__(parent)
        self.prompt_manager = prompt_manager
        self.role_manager = role_manager
        self.setWindowTitle("Редактор системных промтов и ролей")
        self.setMinimumSize(900, 640)

        layout = QVBoxLayout(self)

        # Верхняя панель с выбором типа
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Тип:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Системные промты", "Роли"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()

        self.default_btn = QPushButton("↩ По умолчанию")
        self.default_btn.setToolTip("Заполнить редактор заводским текстом выбранного файла.")
        self.default_btn.clicked.connect(self._insert_default)
        type_layout.addWidget(self.default_btn)
        layout.addLayout(type_layout)

        # Сплиттер: список файлов слева, редактор/превью справа
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Список файлов
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        splitter.addWidget(self.file_list)

        # Редактор и превью
        self.tabs = QTabWidget()

        self.text_edit = QTextEdit()
        self.text_edit.setFontFamily("Courier New")
        self.text_edit.setFontPointSize(11)
        self.text_edit.textChanged.connect(self._update_preview)
        self.tabs.addTab(self.text_edit, "Редактор")

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFontFamily("Courier New")
        self.preview.setFontPointSize(11)
        self.preview.setPlaceholderText("Превью промпта с подставленными переменными проекта.")
        self.tabs.addTab(self.preview, "Превью")

        splitter.addWidget(self.tabs)

        splitter.setSizes([220, 680])
        layout.addWidget(splitter)

        # Кнопки
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("💾 Сохранить")
        self.save_btn.clicked.connect(self._save_current)
        btn_layout.addWidget(self.save_btn)

        self.reload_btn = QPushButton("🔄 Обновить")
        self.reload_btn.clicked.connect(self._refresh_list)
        btn_layout.addWidget(self.reload_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        self._current_file_path: Path | None = None
        self._refresh_list()

    def _is_prompts(self) -> bool:
        return self.type_combo.currentText() == "Системные промты"

    def _base_dir(self) -> Path:
        return self.prompt_manager.prompts_dir if self._is_prompts() else self.role_manager.roles_dir

    def _on_type_changed(self):
        self._refresh_list()

    def _refresh_list(self):
        """Обновляет список файлов в зависимости от выбранного типа."""
        self.file_list.clear()
        self.text_edit.clear()
        self.preview.clear()
        self._current_file_path = None

        base_dir = self._base_dir()
        if not base_dir.exists():
            return

        for file_path in sorted(base_dir.glob("*.md")):
            self.file_list.addItem(file_path.name)

    def _on_file_selected(self, current, previous):
        """Загружает содержимое выбранного файла в редактор."""
        if not current:
            self.text_edit.clear()
            self.preview.clear()
            self._current_file_path = None
            return

        file_path = self._base_dir() / current.text()
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
                self.text_edit.setPlainText(content)
                self._current_file_path = file_path
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить файл:\n{e}")
        else:
            self.text_edit.clear()
            self._current_file_path = None

    def _update_preview(self):
        """Показывает текст редактора с подставленными переменными проекта."""
        text = self.text_edit.toPlainText()
        if not self._is_prompts():
            text = _strip_frontmatter(text)
        self.preview.setPlainText(SystemPromptManager.render_template(text, _SAMPLE_CONTEXT))

    def _insert_default(self):
        """Подставляет заводской текст выбранного файла в редактор (без автосохранения)."""
        if not self._current_file_path:
            return
        stem = self._current_file_path.stem
        if self._is_prompts():
            default = SystemPromptManager.default_prompt(stem)
        else:
            default = self.role_manager.default_templates().get(stem, "")
        if not default:
            QMessageBox.information(self, "Нет шаблона", f"Заводской текст для «{stem}» не найден.")
            return
        self.text_edit.setPlainText(default)
        self.tabs.setCurrentIndex(0)

    def _save_current(self):
        """Сохраняет текущий файл."""
        if not self._current_file_path:
            QMessageBox.warning(self, "Ошибка", "Не выбран файл для сохранения.")
            return

        content = self.text_edit.toPlainText()
        try:
            self._current_file_path.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "Успех", f"Файл сохранён:\n{self._current_file_path}")
            # Обновляем состояние менеджеров (без перезапуска приложения)
            if self._is_prompts():
                self.prompt_manager.clear_cache()
            else:
                self.role_manager.reload()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
