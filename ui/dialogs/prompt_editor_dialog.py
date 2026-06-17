"""Диалог редактирования системных промтов и ролей."""
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QTextEdit,
    QPushButton, QMessageBox, QSplitter, QLabel, QFileDialog, QComboBox
)
from loguru import logger

from core.system_prompts import SystemPromptManager
from core.roles import RoleManager
from core.utils import get_app_data_dir


class PromptEditorDialog(QDialog):
    def __init__(self, prompt_manager: SystemPromptManager, role_manager: RoleManager, parent=None):
        super().__init__(parent)
        self.prompt_manager = prompt_manager
        self.role_manager = role_manager
        self.setWindowTitle("Редактор системных промтов и ролей")
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)

        # Верхняя панель с выбором типа
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Тип:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Системные промты", "Роли"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        # Сплиттер: список файлов слева, редактор справа
        splitter = QSplitter(Qt.Horizontal)

        # Список файлов
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        splitter.addWidget(self.file_list)

        # Редактор содержимого
        self.text_edit = QTextEdit()
        self.text_edit.setFontFamily("Courier New")
        self.text_edit.setFontPointSize(11)
        splitter.addWidget(self.text_edit)

        splitter.setSizes([200, 600])
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

    def _on_type_changed(self):
        self._refresh_list()

    def _refresh_list(self):
        """Обновляет список файлов в зависимости от выбранного типа."""
        self.file_list.clear()
        self.text_edit.clear()
        self._current_file_path = None

        if self.type_combo.currentText() == "Системные промты":
            base_dir = self.prompt_manager.prompts_dir
        else:
            base_dir = self.role_manager.roles_dir

        if not base_dir.exists():
            return

        for file_path in sorted(base_dir.glob("*.md")):
            self.file_list.addItem(file_path.name)

    def _on_file_selected(self, current, previous):
        """Загружает содержимое выбранного файла в редактор."""
        if not current:
            self.text_edit.clear()
            self._current_file_path = None
            return

        if self.type_combo.currentText() == "Системные промты":
            base_dir = self.prompt_manager.prompts_dir
        else:
            base_dir = self.role_manager.roles_dir

        file_path = base_dir / current.text()
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

    def _save_current(self):
        """Сохраняет текущий файл."""
        if not self._current_file_path:
            QMessageBox.warning(self, "Ошибка", "Не выбран файл для сохранения.")
            return

        content = self.text_edit.toPlainText()
        try:
            self._current_file_path.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "Успех", f"Файл сохранён:\n{self._current_file_path}")
            # Обновляем кэш менеджеров (если нужно)
            if self.type_combo.currentText() == "Системные промты":
                # Сбросить кэш для соответствующего режима
                mode = self._current_file_path.stem
                self.prompt_manager._cache.pop(mode, None)
            else:
                # Перезагрузить роли
                self.role_manager._load_roles()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
