"""Диалог подтверждения выполнения инструмента агента."""

import difflib

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def _compute_diff(original: str, modified: str) -> str:
    """Создаёт унифицированный diff между двумя строками."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile="original",
        tofile="modified",
        lineterm="",
    )
    return "".join(diff)


class ToolConfirmationDialog(QDialog):
    """Диалог подтверждения вызова инструмента."""

    def __init__(
        self,
        tool_name: str,
        tool_description: str,
        arguments: dict,
        parent: QWidget | None = None,
    ):
        """
        Args:
            tool_name: Имя инструмента (человекочитаемое)
            tool_description: Описание инструмента
            arguments: Аргументы вызова
            parent: Родительский виджет
        """
        super().__init__(parent)
        self.setWindowTitle(f"Подтверждение: {tool_name}")
        self.setMinimumSize(600, 450)
        self.resize(700, 500)

        self._approved = False
        self._arguments = dict(arguments)
        self._setup_ui(tool_name, tool_description)

    def _setup_ui(self, tool_name: str, tool_description: str):
        """Настраивает UI диалога."""
        layout = QVBoxLayout(self)

        header = QLabel(f"🔧 Инструмент: {tool_name}")
        header.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px 0;")
        layout.addWidget(header)

        desc = QLabel(tool_description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #555; padding: 4px 0;")
        layout.addWidget(desc)

        layout.addWidget(QLabel("Аргументы:"))
        self.args_edit = QPlainTextEdit()
        self.args_edit.setReadOnly(True)
        self.args_edit.setPlainText(self._format_arguments(self._arguments))
        self.args_edit.setMaximumHeight(120)
        self.args_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.args_edit)

        if tool_name == "Запись файла" and "content" in self._arguments:
            layout.addWidget(QLabel("Предпросмотр изменений:"))
            diff_edit = QTextEdit()
            diff_edit.setReadOnly(True)
            diff_edit.setStyleSheet(
                "font-family: Consolas, monospace; font-size: 12px;"
                " background: #1e1e1e; color: #d4d4d4;"
            )
            preview = self._format_write_preview(self._arguments)
            diff_edit.setPlainText(preview)
            layout.addWidget(diff_edit, stretch=1)

        layout.addStretch()

        btn_layout = QHBoxLayout()

        edit_btn = QPushButton("✏️ Редактировать аргументы")
        edit_btn.clicked.connect(self._edit_arguments)
        btn_layout.addWidget(edit_btn)

        btn_layout.addStretch()

        reject_btn = QPushButton("❌ Отклонить")
        reject_btn.setStyleSheet("padding: 6px 16px;")
        reject_btn.clicked.connect(self.reject)
        btn_layout.addWidget(reject_btn)

        approve_btn = QPushButton("✅ Выполнить")
        approve_btn.setStyleSheet(
            "padding: 6px 16px; background: #1976d2; color: white; border: none;"
            " border-radius: 4px;"
        )
        approve_btn.clicked.connect(self._approve)
        btn_layout.addWidget(approve_btn)

        layout.addLayout(btn_layout)

    @staticmethod
    def _format_arguments(args: dict) -> str:
        """Форматирует аргументы для отображения."""
        lines = []
        for key, value in args.items():
            if key == "content" and len(str(value)) > 200:
                lines.append(f"{key}: [{len(value)} символов]")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_write_preview(args: dict) -> str:
        """Форматирует предпросмотр записи файла."""
        path = args.get("path", "?")
        content = args.get("content", "")
        lines = [
            f"📄 Файл: {path}",
            f"📝 Размер: {len(content)} символов, {len(content.splitlines())} строк",
            "",
            "--- Содержимое ---",
            content[:2000],
        ]
        if len(content) > 2000:
            lines.append(f"\n... (показано 2000 из {len(content)} символов)")
        return "\n".join(lines)

    def _edit_arguments(self):
        """Открывает диалог редактирования аргументов."""
        from PySide6.QtWidgets import QInputDialog

        text = self._format_arguments(self._arguments)
        new_text, ok = QInputDialog.getMultiLineText(
            self, "Редактировать аргументы",
            "Измените аргументы (key: value, одна строка на аргумент):",
            text,
        )
        if ok and new_text:
            new_args = {}
            for line in new_text.strip().split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    new_args[key.strip()] = value.strip()
            self._arguments.update(new_args)
            # --- обновляем отображение аргументов ---
            self.args_edit.setPlainText(self._format_arguments(self._arguments))

    def _approve(self):
        """Подтверждает выполнение."""
        self._approved = True
        self.accept()

    def is_approved(self) -> bool:
        """Возвращает, было ли подтверждено выполнение.

        Returns:
            True если пользователь нажал "Выполнить"
        """
        return self._approved

    def get_arguments(self) -> dict:
        """Возвращает (возможно, отредактированные) аргументы.

        Returns:
            dict с аргументами
        """
        return self._arguments
