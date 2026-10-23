"""Диалог экспорта чата (9.6.3).

Простейший: выбор формата (Markdown / HTML), путь через
`QFileDialog.getSaveFileName`, кнопка «Экспорт». Сама сборка — в
`core/chat_export.py` (чистые функции).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agents.ollama_client import ChatMessage
from core.chat_export import export_to_html, export_to_markdown


class ExportChatDialog(QDialog):
    """Диалог экспорта чата в файл."""

    def __init__(
        self,
        messages: list[ChatMessage],
        *,
        title: str = "OlliDesk — чат",
        model: str = "",
        mode: str = "",
        profile: str = "",
        project: str = "",
        default_filename: str = "ollidesk_chat.md",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._messages = messages
        self._title = title
        self._model = model
        self._mode = mode
        self._profile = profile
        self._project = project
        self._default_filename = default_filename

        self.setWindowTitle("Экспорт чата")
        self.setMinimumWidth(500)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            f"Сообщений в чате: <b>{len(self._messages)}</b>.<br>"
            "Markdown — для архива и git; HTML — автономный файл с inline CSS."
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        form = QFormLayout()

        self.format_combo = QComboBox()
        self.format_combo.addItem("Markdown (.md)", "md")
        self.format_combo.addItem("HTML (.html)", "html")
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Формат:", self.format_combo)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(Path.home() / self._default_filename))
        path_row.addWidget(self.path_edit, stretch=1)
        browse_btn = QPushButton("📂 Обзор…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        form.addRow("Путь:", path_row)

        layout.addLayout(form)
        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("💾 Экспорт")
            save_btn.setDefault(True)
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Слоты
    # ------------------------------------------------------------------

    def _on_format_changed(self, _index: int) -> None:
        """Меняет расширение в поле пути при смене формата."""
        fmt = self.format_combo.currentData()
        current = Path(self.path_edit.text())
        new_suffix = ".md" if fmt == "md" else ".html"
        new_path = current.with_suffix(new_suffix)
        self.path_edit.setText(str(new_path))

    def _browse(self) -> None:
        fmt = self.format_combo.currentData()
        if fmt == "md":
            filter_str = "Markdown (*.md)"
            default = self.path_edit.text() or "ollidesk_chat.md"
        else:
            filter_str = "HTML (*.html)"
            default = self.path_edit.text() or "ollidesk_chat.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт чата", default, filter_str
        )
        if path:
            self.path_edit.setText(path)

    def _on_export(self) -> None:
        path = Path(self.path_edit.text()).expanduser()
        if not path:
            QMessageBox.warning(self, "Ошибка", "Укажите путь для экспорта.")
            return
        fmt = self.format_combo.currentData()

        try:
            if fmt == "md":
                content = export_to_markdown(
                    self._messages,
                    title=self._title,
                    model=self._model,
                    mode=self._mode,
                    profile=self._profile,
                    project=self._project,
                )
            else:
                content = export_to_html(
                    self._messages,
                    title=self._title,
                    model=self._model,
                    mode=self._mode,
                    profile=self._profile,
                    project=self._project,
                )
        except Exception as e:
            logger.exception("export failed")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось собрать экспорт:\n{e}"
            )
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.info(f"Экспорт чата: {path} ({len(content)} символов)")
            QMessageBox.information(
                self, "Готово", f"Чат экспортирован:\n{path}"
            )
        except OSError as e:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось записать файл:\n{e}"
            )
            return

        self.accept()