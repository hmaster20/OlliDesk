"""Заголовок центральной панели чата."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox, QProgressBar, QMenu, QToolButton

class ChatHeader(QWidget):
    """Заголовок с именем агента, моделью, статусом и токенами."""
    model_changed = Signal(str)
    new_chat_requested = Signal()
    settings_requested = Signal()
    stop_generation_requested = Signal()
    refresh_models_requested = Signal()  # новый сигнал

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Имя агента
        self.agent_name_label = QLabel("Агент")
        self.agent_name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self.agent_name_label)

        # Выбор модели
        layout.addWidget(QLabel("Модель:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(150)
        self.model_combo.currentTextChanged.connect(self.model_changed.emit)
        layout.addWidget(self.model_combo)

        # Кнопка обновления моделей
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("Проверить поддержку инструментов моделями")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.clicked.connect(self.refresh_models_requested.emit)
        layout.addWidget(self.refresh_btn)

        # Статус
        self.status_label = QLabel("● Готов")
        self.status_label.setStyleSheet("color: #4CAF50; font-size: 13px; margin-left: 12px;")
        layout.addWidget(self.status_label)

        # Счётчик токенов
        self.token_label = QLabel("0 / 0")
        self.token_label.setStyleSheet("font-size: 12px; color: #888; margin-left: 12px;")
        self.token_label.setToolTip("Токены в контексте")
        layout.addWidget(self.token_label)

        # Прогресс-бар токенов (тонкая полоска)
        self.token_progress = QProgressBar()
        self.token_progress.setRange(0, 100)
        self.token_progress.setValue(0)
        self.token_progress.setFixedWidth(80)
        self.token_progress.setFixedHeight(6)
        self.token_progress.setTextVisible(False)
        self.token_progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background: #333;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #60CDFF;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.token_progress)

        layout.addStretch()

        # Кнопки
        self.new_chat_btn = QPushButton("➕ Новый чат")
        self.new_chat_btn.clicked.connect(self.new_chat_requested.emit)
        layout.addWidget(self.new_chat_btn)

        self.stop_btn = QPushButton("⏹ Стоп")
        self.stop_btn.setStyleSheet("background: #d32f2f; color: white;")
        self.stop_btn.clicked.connect(self.stop_generation_requested.emit)
        self.stop_btn.setVisible(False)
        layout.addWidget(self.stop_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setToolTip("Настройки агента")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_btn)

    def set_agent_name(self, name: str):
        self.agent_name_label.setText(name)

    def set_status(self, status: str, color: str = "#4CAF50"):
        self.status_label.setText(f"● {status}")
        self.status_label.setStyleSheet(f"color: {color}; font-size: 13px;")

    def set_tokens(self, used: int, total: int):
        self.token_label.setText(f"{used} / {total}")
        if total > 0:
            percent = min(100, int(used / total * 100))
            self.token_progress.setValue(percent)
            if percent > 90:
                self.token_progress.setStyleSheet("QProgressBar::chunk { background: #FFA726; }")
            else:
                self.token_progress.setStyleSheet("QProgressBar::chunk { background: #60CDFF; }")

    def set_models(self, models: list[str], current: str):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current in models:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def show_stop_button(self, show: bool):
        self.stop_btn.setVisible(show)
        if show:
            self.set_status("Генерация...", "#2196F3")
        else:
            self.set_status("Готов", "#4CAF50")

    def set_checking_state(self, is_checking: bool):
        """Меняет иконку кнопки обновления во время проверки."""
        self.refresh_btn.setText("⏳" if is_checking else "🔄")
