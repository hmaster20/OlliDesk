"""Менеджер сессий (9.6.4).

Отличия от предыдущей версии:
  * клик по строке → просмотр содержимого справа (QTextBrowser);
  * кнопки: «Загрузить», «Удалить», «Экспорт», «Создать проект из сессии»;
  * «Создать проект из сессии» — диалог выбора папки, копирует
    `_messages` как `chat_history.md` и создаёт минимальный
    `.ollidesk/config.yaml` + `AGENTS.md` для «продолжения» в новом проекте.

Экспорт использует `core/chat_export.py` (9.6.3).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from agents.ollama_client import ChatMessage
from core.chat_export import export_to_markdown
from state.session_store import SessionStore


class SessionManagerDialog(QDialog):
    def __init__(self, session_store: SessionStore, chat_panel, parent=None):
        super().__init__(parent)
        self.session_store = session_store
        self.chat_panel = chat_panel
        self._current_session_id: str | None = None
        self.setWindowTitle("Управление сессиями")
        self.resize(1100, 600)

        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Слева: таблица сессий ---
        left = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Проект", "Создана", "Обновлена"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self._load_selected)
        # [9.6.4] Клик = preview, double-click = загрузить.
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        left.addWidget(self.table)

        left_widget = QWidget()
        left_widget.setLayout(left)
        splitter.addWidget(left_widget)

        # --- Справа: preview ---
        right = QVBoxLayout()
        preview_label = QLabel("Содержимое сессии:")
        preview_label.setStyleSheet("color: #888; font-size: 11px;")
        right.addWidget(preview_label)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setStyleSheet(
            "QTextBrowser { background: #1e1e2e; color: #d4d4d4;"
            " border: 1px solid #3a3a48; border-radius: 4px; padding: 8px; }"
        )
        right.addWidget(self.preview)

        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter.addWidget(right_widget)

        splitter.setSizes([500, 600])
        layout.addWidget(splitter, stretch=1)

        # --- Кнопки ---
        btn_layout = QHBoxLayout()
        self.load_btn = QPushButton("📥 Загрузить")
        self.load_btn.clicked.connect(self._load_selected)
        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.export_btn = QPushButton("💾 Экспорт…")
        self.export_btn.clicked.connect(self._export_selected)
        # [9.6.4] Создать проект из сессии.
        self.create_project_btn = QPushButton("📁 Создать проект из сессии…")
        self.create_project_btn.setToolTip(
            "Создаёт новый проект: копирует чат в AGENTS.md, минимальный "
            "config.yaml и chat_history.md. Продолжайте с той же историей."
        )
        self.create_project_btn.clicked.connect(self._create_project_from_session)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.load_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.create_project_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self._refresh()

    # ------------------------------------------------------------------
    # Таблица / preview
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        sessions = self.session_store.list_sessions()
        self.table.blockSignals(True)
        self.table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            self.table.setItem(i, 0, QTableWidgetItem(s.id))
            self.table.setItem(i, 1, QTableWidgetItem(s.project_path))
            self.table.setItem(
                i, 2, QTableWidgetItem(s.created_at.strftime("%Y-%m-%d %H:%M"))
            )
            self.table.setItem(
                i, 3, QTableWidgetItem(s.updated_at.strftime("%Y-%m-%d %H:%M"))
            )
        self.table.blockSignals(False)
        if self.table.rowCount() > 0:
            self.table.selectRow(0)

    def _selected_session_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item is not None else None

    def _on_selection_changed(self) -> None:
        """[9.6.4] Клик по строке — preview содержимого."""
        session_id = self._selected_session_id()
        self._current_session_id = session_id
        if session_id is None:
            self.preview.clear()
            return
        try:
            history = self.session_store.get_history(session_id, limit=1000)
        except Exception as e:
            self.preview.setHtml(
                f'<div style="color: #e06c75;">Ошибка загрузки: {e}</div>'
            )
            return
        self.preview.setHtml(_render_history_preview(history))

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _load_selected(self) -> None:
        session_id = self._selected_session_id()
        if session_id is None:
            return
        history = self.session_store.get_history(session_id, limit=1000)
        self.chat_panel.set_session_id(session_id)
        self.chat_panel.load_history(history)
        self.accept()

    def _delete_selected(self) -> None:
        session_id = self._selected_session_id()
        if session_id is None:
            return
        if (
            QMessageBox.question(
                self, "Подтверждение", f"Удалить сессию {session_id}?"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.session_store.delete_session(session_id)
            self._refresh()

    def _export_selected(self) -> None:
        """[9.6.3/9.6.4] Экспорт выбранной сессии через общий модуль."""
        session_id = self._selected_session_id()
        if session_id is None:
            return
        history = self.session_store.get_history(session_id, limit=1000)
        if not history:
            QMessageBox.information(self, "Экспорт", "Сессия пуста.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспортировать сессию",
            f"{session_id}.md",
            "Markdown (*.md);;HTML (*.html);;Text (*.txt)",
        )
        if not path:
            return
        p = Path(path)
        try:
            if p.suffix.lower() == ".md":
                content = export_to_markdown(
                    history,
                    title=f"Сессия {session_id}",
                )
            elif p.suffix.lower() == ".html":
                from core.chat_export import export_to_html

                content = export_to_html(
                    history,
                    title=f"Сессия {session_id}",
                )
            else:
                # .txt — старый формат «role: content».
                parts: list[str] = []
                for m in history:
                    parts.append(f"[{m.role.upper()}] {m.content}\n")
                content = "\n".join(parts)
            p.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "Готово", f"Экспорт в {p}")
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _create_project_from_session(self) -> None:
        """[9.6.4] Создаёт проект из сессии."""
        session_id = self._selected_session_id()
        if session_id is None:
            return
        history = self.session_store.get_history(session_id, limit=1000)
        if not history:
            QMessageBox.information(self, "Создать проект", "Сессия пуста.")
            return

        folder = QFileDialog.getExistingDirectory(
            self,
            "Папка для нового проекта",
            str(Path.home()),
        )
        if not folder:
            return
        target = Path(folder)
        if not target.is_dir():
            QMessageBox.warning(self, "Ошибка", f"Не папка: {target}")
            return

        # Проверим, что папка пуста или создаём .ollidesk с нуля.
        ollidesk = target / ".ollidesk"
        if ollidesk.exists():
            reply = QMessageBox.question(
                self,
                "Папка не пуста",
                f"В {target} уже есть .ollidesk. Перезаписать chat_history.md "
                "и AGENTS.md?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            ollidesk.mkdir(parents=True, exist_ok=True)

            # 1. chat_history.md — полный экспорт.
            history_md = export_to_markdown(
                history,
                title="История чата (перенесена из сессии)",
            )
            (ollidesk / "chat_history.md").write_text(history_md, encoding="utf-8")

            # 2. AGENTS.md — краткая инструкция «это перенос сессии».
            agents_md = (
                "# AGENTS.md\n\n"
                "## Контекст\n\n"
                f"Проект создан из сессии `{session_id}` "
                f"({len(history)} сообщений). Полная история — в "
                f"`.ollidesk/chat_history.md`.\n\n"
                "## Что сделать\n\n"
                "1. Прочитай `.ollidesk/chat_history.md`, чтобы понять контекст.\n"
                "2. Продолжай задачу с того места, где остановились.\n"
            )
            (target / "AGENTS.md").write_text(agents_md, encoding="utf-8")

            # 3. config.yaml — минимальный, чтобы OlliDesk распознал проект.
            from core.config import load_config, save_config

            cfg = load_config()
            # Сбрасываем recent_projects — пользователь сам добавит.
            cfg.recent_projects = [str(target)] + [
                p for p in cfg.recent_projects if p != str(target)
            ][:9]
            save_config(cfg)

            logger.info(
                f"Проект из сессии {session_id}: {target} "
                f"({len(history)} сообщений)"
            )
            QMessageBox.information(
                self,
                "Готово",
                f"Проект создан:\n{target}\n\n"
                "Откройте его через File → Open Project.",
            )
        except OSError as e:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать проект:\n{e}"
            )


def _render_history_preview(history: list[ChatMessage]) -> str:
    """[9.6.4] Компактный HTML-preview для менеджера сессий."""
    from html import escape

    if not history:
        return '<div style="color: #888;">(пусто)</div>'

    roles = {
        "user": ("👤 Пользователь", "#1976d2"),
        "assistant": ("🤖 Ассистент", "#d4a373"),
        "tool": ("🔧 Инструмент", "#888"),
        "system": ("⚙ Система", "#e5c07b"),
    }
    parts: list[str] = []
    for msg in history:
        content = (msg.content or "").strip()
        if not content:
            continue
        label, color = roles.get(msg.role, (msg.role, "#888"))
        if msg.role == "tool":
            name = msg.name or "tool"
            label = f"🔧 {escape(name)}"
        preview = content[:1500]
        if len(content) > 1500:
            preview += f"… (+{len(content) - 1500} симв.)"
        parts.append(
            f'<div style="margin: 6px 0; padding: 4px 8px;'
            f' border-left: 3px solid {color}; background: rgba(255,255,255,0.03);">'
            f'<b style="color: {color};">{label}</b><br>'
            f"{escape(preview)}</div>"
        )
    return "".join(parts) if parts else '<div style="color: #888;">(пусто)</div>'