"""Диалог управления эпизодической памятью (4.3).

Возможности:
  * просмотр рефлексий (свежие первыми) и решений;
  * FTS5-поиск по рефлексиям;
  * удаление отдельной записи;
  * очистка всей памяти (с сохранением настроек);
  * экспорт в JSON.

Память — это пользовательские данные. UI обязан дать возможность
посмотреть, что там лежит, и удалить любую запись или всё сразу.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.services.memory_service import (
    Decision,
    MemoryService,
    Preference,
    Reflection,
)


class MemoryDialog(QDialog):
    """Просмотр/удаление эпизодической памяти."""

    def __init__(
        self,
        service: MemoryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Память агента")
        self.setMinimumSize(900, 600)

        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_reflections_tab(), "Рефлексии")
        self._tabs.addTab(self._build_decisions_tab(), "Решения")
        self._tabs.addTab(self._build_preferences_tab(), "Настройки")
        layout.addWidget(self._tabs, stretch=1)

        # Кнопки внизу.
        btn_row = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        btn_row.addWidget(self._count_label)
        btn_row.addStretch()

        export_btn = QPushButton("💾 Экспорт в JSON…")
        export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(export_btn)

        clear_btn = QPushButton("🗑 Очистить память")
        clear_btn.setToolTip("Удалить все рефлексии и решения. Настройки сохранятся.")
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)
        self._refresh_all()

    # ------------------------------------------------------------------
    # Рефлексии
    # ------------------------------------------------------------------

    def _build_reflections_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Поиск по рефлексиям (FTS5)…")
        self._search_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self._search_edit, stretch=1)
        search_btn = QPushButton("🔍 Найти")
        search_btn.clicked.connect(self._on_search)
        search_row.addWidget(search_btn)
        reset_btn = QPushButton("✕ Сбросить")
        reset_btn.clicked.connect(self._on_reset_search)
        search_row.addWidget(reset_btn)
        layout.addLayout(search_row)

        splitter = QHBoxLayout()

        self._reflections_table = QTableWidget()
        self._reflections_table.setColumnCount(5)
        self._reflections_table.setHorizontalHeaderLabels(
            ["ID", "Дата", "Тип", "Итог", "Теги"]
        )
        self._reflections_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._reflections_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._reflections_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._reflections_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._reflections_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self._reflections_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._reflections_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._reflections_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._reflections_table.itemSelectionChanged.connect(
            self._on_reflection_selected
        )
        splitter.addWidget(self._reflections_table, stretch=1)

        # Preview.
        self._reflection_preview = QTextBrowser()
        self._reflection_preview.setOpenExternalLinks(False)
        self._reflection_preview.setStyleSheet(
            "QTextBrowser { background: #1e1e2e; color: #d4d4d4;"
            " border: 1px solid #3a3a48; border-radius: 4px; padding: 8px; }"
        )
        splitter.addWidget(self._reflection_preview, stretch=1)

        layout.addLayout(splitter, stretch=1)

        delete_btn = QPushButton("🗑 Удалить выбранную")
        delete_btn.clicked.connect(self._on_delete_reflection)
        layout.addWidget(delete_btn)

        self._reflections: list[Reflection] = []
        return tab

    @Slot()
    def _on_search(self) -> None:
        q = self._search_edit.text().strip()
        if not q:
            self._on_reset_search()
            return
        try:
            result = self._service.recall(q, top_k=100)
            self._reflections = list(result.reflections)
            self._populate_reflections()
        except Exception:
            logger.exception("MemoryDialog: recall упал")

    @Slot()
    def _on_reset_search(self) -> None:
        self._search_edit.clear()
        self._refresh_reflections()

    def _refresh_reflections(self) -> None:
        try:
            self._reflections = self._service.list_recent(limit=200)
        except Exception:
            logger.exception("MemoryDialog: list_recent упал")
            self._reflections = []
        self._populate_reflections()

    def _populate_reflections(self) -> None:
        self._reflections_table.setRowCount(len(self._reflections))
        for row, r in enumerate(self._reflections):
            self._reflections_table.setItem(
                row, 0, QTableWidgetItem(str(r.id or ""))
            )
            self._reflections_table.setItem(
                row, 1, QTableWidgetItem(r.created_at.replace("T", " "))
            )
            self._reflections_table.setItem(
                row, 2, QTableWidgetItem(r.task_type)
            )
            self._reflections_table.setItem(
                row, 3, QTableWidgetItem(r.outcome)
            )
            self._reflections_table.setItem(
                row, 4, QTableWidgetItem(", ".join(r.tags))
            )
        if self._reflections:
            self._reflections_table.selectRow(0)

    @Slot()
    def _on_reflection_selected(self) -> None:
        row = self._reflections_table.currentRow()
        if row < 0 or row >= len(self._reflections):
            self._reflection_preview.clear()
            return
        r = self._reflections[row]
        self._reflection_preview.setHtml(
            f"<b>ID:</b> {r.id}<br>"
            f"<b>Дата:</b> {r.created_at}<br>"
            f"<b>Проект:</b> {r.project_path or '—'}<br>"
            f"<b>Тип:</b> {r.task_type}<br>"
            f"<b>Итог:</b> {r.outcome}<br>"
            f"<b>Теги:</b> {', '.join(r.tags) or '—'}<br>"
            f"<hr><pre style='white-space:pre-wrap; font-family:inherit;'>"
            f"{_escape(r.summary)}</pre>"
        )

    @Slot()
    def _on_delete_reflection(self) -> None:
        row = self._reflections_table.currentRow()
        if row < 0 or row >= len(self._reflections):
            return
        r = self._reflections[row]
        if r.id is None:
            return
        if (
            QMessageBox.question(
                self,
                "Удалить рефлексию",
                f"Удалить запись #{r.id}?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._service.forget(r.id)
        except Exception:
            logger.exception("MemoryDialog: forget упал")
            return
        self._refresh_reflections()
        self._refresh_count()

    # ------------------------------------------------------------------
    # Решения
    # ------------------------------------------------------------------

    def _build_decisions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._decisions_table = QTableWidget()
        self._decisions_table.setColumnCount(4)
        self._decisions_table.setHorizontalHeaderLabels(
            ["ID", "Дата", "Проект", "Решение"]
        )
        self._decisions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._decisions_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._decisions_table)
        return tab

    def _refresh_decisions(self) -> None:
        try:
            decisions: list[Decision] = self._service.list_decisions(limit=200)
        except Exception:
            logger.exception("MemoryDialog: list_decisions упал")
            decisions = []
        self._decisions_table.setRowCount(len(decisions))
        for row, d in enumerate(decisions):
            self._decisions_table.setItem(
                row, 0, QTableWidgetItem(str(d.id or ""))
            )
            self._decisions_table.setItem(
                row, 1, QTableWidgetItem(d.created_at.replace("T", " "))
            )
            self._decisions_table.setItem(
                row, 2, QTableWidgetItem(d.project_path)
            )
            text = d.decision
            if d.rationale:
                text += f"\n\nОбоснование: {d.rationale}"
            self._decisions_table.setItem(row, 3, QTableWidgetItem(text))

    # ------------------------------------------------------------------
    # Настройки
    # ------------------------------------------------------------------

    def _build_preferences_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._preferences_table = QTableWidget()
        self._preferences_table.setColumnCount(3)
        self._preferences_table.setHorizontalHeaderLabels(
            ["Ключ", "Значение", "Обновлено"]
        )
        self._preferences_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._preferences_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._preferences_table)
        return tab

    def _refresh_preferences(self) -> None:
        try:
            prefs: list[Preference] = self._service.list_preferences()
        except Exception:
            logger.exception("MemoryDialog: list_preferences упал")
            prefs = []
        self._preferences_table.setRowCount(len(prefs))
        for row, p in enumerate(prefs):
            self._preferences_table.setItem(row, 0, QTableWidgetItem(p.key))
            self._preferences_table.setItem(row, 1, QTableWidgetItem(p.value))
            self._preferences_table.setItem(
                row, 2, QTableWidgetItem(p.updated_at.replace("T", " "))
            )

    # ------------------------------------------------------------------
    # Прочее
    # ------------------------------------------------------------------

    def _refresh_all(self) -> None:
        self._refresh_reflections()
        self._refresh_decisions()
        self._refresh_preferences()
        self._refresh_count()

    def _refresh_count(self) -> None:
        try:
            counts = self._service.count()
        except Exception:
            counts = {"reflections": 0, "decisions": 0, "preferences": 0}
        self._count_label.setText(
            f"Рефлексий: {counts['reflections']} · "
            f"Решений: {counts['decisions']} · "
            f"Настроек: {counts['preferences']}"
        )

    @Slot()
    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт памяти",
            "ollidesk_memory.json",
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            self._service.export_json(Path(path))
            QMessageBox.information(self, "Готово", f"Экспортировано: {path}")
        except Exception as e:
            logger.exception("MemoryDialog: export упал")
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать:\n{e}")

    @Slot()
    def _on_clear(self) -> None:
        reply = QMessageBox.question(
            self,
            "Очистить память",
            "Удалить все рефлексии и решения?\n\n"
            "Пользовательские настройки (preferences) сохранятся.\n"
            "Действие необратимо (но можно сделать экспорт заранее).",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.clear(keep_preferences=True)
        except Exception as e:
            logger.exception("MemoryDialog: clear упал")
            QMessageBox.critical(self, "Ошибка", f"Не удалось очистить:\n{e}")
            return
        self._refresh_all()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["MemoryDialog"]