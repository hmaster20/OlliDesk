
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from state.session_store import SessionStore


class SessionManagerDialog(QDialog):
    def __init__(self, session_store: SessionStore, chat_panel, parent=None):
        super().__init__(parent)
        self.session_store = session_store
        self.chat_panel = chat_panel
        self.setWindowTitle("Управление сессиями")
        self.resize(700, 400)
        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ID", "Проект", "Создана", "Обновлена"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self._load_selected)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.load_btn = QPushButton("Загрузить")
        self.load_btn.clicked.connect(self._load_selected)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.export_btn = QPushButton("Экспорт")
        self.export_btn.clicked.connect(self._export_selected)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.load_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self._refresh()

    def _refresh(self):
        sessions = self.session_store.list_sessions()
        self.table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            self.table.setItem(i, 0, QTableWidgetItem(s.id))
            self.table.setItem(i, 1, QTableWidgetItem(s.project_path))
            self.table.setItem(i, 2, QTableWidgetItem(s.created_at.strftime("%Y-%m-%d %H:%M")))
            self.table.setItem(i, 3, QTableWidgetItem(s.updated_at.strftime("%Y-%m-%d %H:%M")))

    def _load_selected(self):
        row = self.table.currentRow()
        if row < 0: return
        session_id = self.table.item(row, 0).text()
        history = self.session_store.get_history(session_id, limit=1000)
        self.chat_panel.set_session_id(session_id)
        self.chat_panel.load_history(history)
        self.accept()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0: return
        session_id = self.table.item(row, 0).text()
        if QMessageBox.question(self, "Подтверждение", f"Удалить сессию {session_id}?") == QMessageBox.StandardButton.Yes:
            self.session_store.delete_session(session_id)
            self._refresh()

    def _export_selected(self):
        row = self.table.currentRow()
        if row < 0: return
        session_id = self.table.item(row, 0).text()
        path, _ = QFileDialog.getSaveFileName(self, "Экспортировать сессию", f"{session_id}.txt", "Text files (*.txt)")
        if path:
            messages = self.session_store.get_history(session_id, limit=1000)
            with open(path, "w", encoding="utf-8") as f:
                for msg in messages:
                    f.write(f"[{msg.role.upper()}] {msg.content}\n\n")
            QMessageBox.information(self, "Готово", f"Экспорт в {path}")
