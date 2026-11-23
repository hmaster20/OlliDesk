"""Диалог подтверждения выполнения инструмента агента.

Редактирование аргументов (React.md 9.2): значения редактируются как текст,
но исходный тип сохраняется. Для примитивов (int, float, bool) — приведение
из строки; для list/dict — `json.loads`. При любой ошибке парсинга значение
остаётся строкой (не падаем: пользователь мог сознательно ввести строку).
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ToolConfirmationDialog(QDialog):
    """Диалог подтверждения вызова инструмента."""

    # [2.7] Пользователь разрешил инструмент на текущую сессию.
    session_allow_requested = Signal(str)  # tool_name

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
        self._tool_name = tool_name
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
                "font-family: Consolas, monospace; font-size: 12px; background: #1e1e1e; color: #d4d4d4;"
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

        # [2.7] Разрешить на эту сессию — добавляет в session_overrides.
        allow_session_btn = QPushButton("📌 Разрешить на сессию")
        allow_session_btn.setToolTip(
            "Разрешить этот инструмент на текущую сессию (сбрасывается при закрытии чата)."
        )
        allow_session_btn.setStyleSheet(
            "padding: 6px 16px; background: #4a6741; color: #fff; border: none; border-radius: 4px;"
        )
        allow_session_btn.clicked.connect(self._approve_session)
        btn_layout.addWidget(allow_session_btn)

        approve_btn = QPushButton("✅ Выполнить")
        approve_btn.setStyleSheet(
            "padding: 6px 16px; background: #1976d2; color: white; border: none; border-radius: 4px;"
        )
        approve_btn.clicked.connect(self._approve)
        btn_layout.addWidget(approve_btn)

        layout.addLayout(btn_layout)

    # ---------------------------------------------------------------
    # Форматирование
    # ---------------------------------------------------------------

    @staticmethod
    def _format_arguments(args: dict) -> str:
        """Форматирует аргументы для отображения (без потери информации)."""
        lines = []
        for key, value in args.items():
            if key == "content" and isinstance(value, str) and len(value) > 200:
                lines.append(f"{key}: [{len(value)} символов]")
            elif isinstance(value, (list, dict)):
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            elif value is None:
                lines.append(f"{key}: null")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_write_preview(args: dict) -> str:
        """Форматирует предпросмотр записи файла."""
        path = args.get("path", "?")
        content = args.get("content", "")
        if not isinstance(content, str):
            content = str(content)
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

    # ---------------------------------------------------------------
    # Редактирование с сохранением типов
    # ---------------------------------------------------------------

    def _edit_arguments(self):
        """Открывает табличный редактор аргументов.

        В отличие от прежнего ``QInputDialog`` (всё превращалось в строку),
        исходный тип значения сохраняется. Для list/dict/None значения
        редактируются как JSON; при ошибке парсинга — остаются строкой.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Редактировать аргументы")
        dlg.setMinimumSize(600, 400)
        dlg_layout = QVBoxLayout(dlg)

        hint = QLabel(
            "Значения редактируются как текст, но исходный тип сохраняется. "
            "Для list/dict/None вводите валидный JSON. Неверный формат → "
            "значение останется строкой."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        dlg_layout.addWidget(hint)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Аргумент", "Значение", "Тип"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.setRowCount(len(self._arguments))

        type_names: dict[str, str] = {}
        for row, (key, value) in enumerate(self._arguments.items()):
            type_name = type(value).__name__
            type_names[key] = type_name

            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, key_item)

            if isinstance(value, (list, dict)) or value is None:
                display = json.dumps(value, ensure_ascii=False) if value is not None else "null"
            else:
                display = str(value)
            table.setItem(row, 1, QTableWidgetItem(display))

            type_item = QTableWidgetItem(type_name)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            type_item.setForeground(QColor("#888"))
            table.setItem(row, 2, type_item)

        dlg_layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        dlg_layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_args: dict = {}
        for row in range(table.rowCount()):
            key_item = table.item(row, 0)
            val_item = table.item(row, 1)
            if key_item is None or val_item is None:
                continue
            key = key_item.text()
            raw = val_item.text()
            orig_type = type_names.get(key, "str")
            new_args[key] = self._coerce_value(raw, orig_type)

        self._arguments.clear()
        self._arguments.update(new_args)
        self.args_edit.setPlainText(self._format_arguments(self._arguments))

    @staticmethod
    def _coerce_value(raw: str, orig_type: str) -> Any:
        """Пытается восстановить исходный тип из строки.

        Никогда не падает: при ошибке парсинга возвращает строку.
        """
        if orig_type == "bool":
            low = raw.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            return raw
        if orig_type == "int":
            try:
                return int(raw)
            except ValueError:
                return raw
        if orig_type == "float":
            try:
                return float(raw)
            except ValueError:
                return raw
        if orig_type in ("list", "dict", "NoneType"):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return raw
        return raw

    # ---------------------------------------------------------------
    # Действия
    # ---------------------------------------------------------------

    def _approve(self):
        """Подтверждает выполнение."""
        self._approved = True
        self.accept()

    def is_approved(self) -> bool:
        """Возвращает, было ли подтверждено выполнение."""
        return self._approved

    def get_arguments(self) -> dict:
        """Возвращает (возможно, отредактированные) аргументы."""
        return self._arguments

    def _approve_session(self) -> None:
        """[2.7] Подтверждает и разрешает инструмент на текущую сессию."""
        self._approved = True
        self.session_allow_requested.emit(self._tool_name)
        self.accept()
