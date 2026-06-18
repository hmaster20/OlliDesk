"""Виджет списка агентов и их диалогов."""

from PySide6.QtCore import Qt, Signal, QModelIndex
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QPushButton, QHBoxLayout

from core.roles import RoleManager, RoleDefinition
from state.session_store import SessionStore, SessionInfo

class AgentListItem(QListWidgetItem):
    """Элемент списка агента с данными."""
    def __init__(self, agent_id: str, name: str, icon: str, last_message: str = "", parent=None):
        super().__init__(parent)
        self.agent_id = agent_id
        self.name = name
        self.icon = icon
        self.last_message = last_message
        self.setText(f"{icon} {name}\n{last_message[:60]}")
        self.setToolTip(last_message[:200])


class AgentListWidget(QWidget):
    """Левая панель со списком агентов и диалогов."""
    agent_selected = Signal(str)          # agent_id
    new_agent_requested = Signal()

    def __init__(self, role_manager: RoleManager, session_store: SessionStore, parent=None):
        super().__init__(parent)
        self.role_manager = role_manager
        self.session_store = session_store
        self._current_agent_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Заголовок
        header = QHBoxLayout()
        title = QLabel("💬 Диалоги")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px 12px;")
        header.addWidget(title)
        header.addStretch()

        self.new_btn = QPushButton("➕")
        self.new_btn.setFixedSize(32, 32)
        self.new_btn.setToolTip("Новый агент/диалог")
        self.new_btn.clicked.connect(self.new_agent_requested.emit)
        header.addWidget(self.new_btn)
        layout.addLayout(header)

        # Поиск
        # (можно добавить позже)

        # Список
        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                padding: 6px 12px;
                border-radius: 6px;
                margin: 2px 4px;
            }
            QListWidget::item:selected {
                background: #264f78;
            }
            QListWidget::item:hover {
                background: #2d2d2d;
            }
        """)
        self.list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list)

        self.refresh_list()

    def refresh_list(self):
        """Обновляет список из ролей и последних сессий."""
        self.list.clear()
        roles = self.role_manager.get_all_roles()
        for role in roles:
            # Получаем последний диалог для этой роли (если есть)
            sessions = self.session_store.list_sessions()
            # Фильтр по роли? Пока просто показываем все роли.
            item = AgentListItem(role.id, role.name, role.icon, "")
            self.list.addItem(item)

        # Если есть активный агент, выделяем его
        if self._current_agent_id:
            for i in range(self.list.count()):
                if self.list.item(i).agent_id == self._current_agent_id:
                    self.list.setCurrentRow(i)
                    break

    def _on_item_clicked(self, item: AgentListItem):
        self._current_agent_id = item.agent_id
        self.agent_selected.emit(item.agent_id)
