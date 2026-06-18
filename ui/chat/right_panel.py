"""Правая панель настроек агента."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit, QSlider, QSpinBox, QHBoxLayout, QPushButton, QTabWidget, QGroupBox, QCheckBox

class RightSettingsPanel(QWidget):
    """Настройки агента: промпт, параметры, контекст, статистика."""
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self.setStyleSheet("""
            QWidget#rightPanel {
                background: #252525;
                border-left: 1px solid #3D3D3D;
            }
        """)
        self.setObjectName("rightPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Заголовок
        title = QLabel("⚙️ Настройки агента")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        # Вкладки
        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: transparent; }
            QTabBar::tab { padding: 6px 12px; }
        """)

        # Вкладка "Конфигурация"
        config_tab = QWidget()
        config_layout = QVBoxLayout(config_tab)

        config_layout.addWidget(QLabel("Системный промпт:"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("Введите системный промпт...")
        self.prompt_edit.setMinimumHeight(120)
        config_layout.addWidget(self.prompt_edit)

        # Параметры генерации
        group = QGroupBox("Параметры генерации")
        group_layout = QVBoxLayout(group)

        # Температура
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(QLabel("Температура:"))
        self.temp_slider = QSlider(Qt.Horizontal)
        self.temp_slider.setRange(0, 200)
        self.temp_slider.setValue(70)
        self.temp_slider.setTickInterval(10)
        self.temp_label = QLabel("0.70")
        temp_layout.addWidget(self.temp_slider)
        temp_layout.addWidget(self.temp_label)
        group_layout.addLayout(temp_layout)
        self.temp_slider.valueChanged.connect(lambda v: self.temp_label.setText(f"{v/100:.2f}"))

        # Max tokens
        tokens_layout = QHBoxLayout()
        tokens_layout.addWidget(QLabel("Max tokens:"))
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 32768)
        self.max_tokens_spin.setValue(4096)
        self.max_tokens_spin.setSingleStep(256)
        tokens_layout.addWidget(self.max_tokens_spin)
        group_layout.addLayout(tokens_layout)

        config_layout.addWidget(group)
        config_layout.addStretch()
        tabs.addTab(config_tab, "Конфигурация")

        # Вкладка "Контекст"
        context_tab = QWidget()
        context_layout = QVBoxLayout(context_tab)
        context_layout.addWidget(QLabel("Управление контекстом"))
        # Можно добавить кнопку сжатия
        compress_btn = QPushButton("Сжать контекст")
        context_layout.addWidget(compress_btn)
        context_layout.addStretch()
        tabs.addTab(context_tab, "Контекст")

        # Вкладка "Информация"
        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        info_layout.addWidget(QLabel("Статистика использования"))
        info_layout.addWidget(QLabel("Запросов: 0"))
        info_layout.addWidget(QLabel("Токенов сегодня: 0"))
        info_layout.addStretch()
        tabs.addTab(info_tab, "Информация")

        layout.addWidget(tabs)

        # Кнопка сохранения
        save_btn = QPushButton("Сохранить настройки")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

    def _save(self):
        settings = {
            "system_prompt": self.prompt_edit.toPlainText(),
            "temperature": self.temp_slider.value() / 100,
            "max_tokens": self.max_tokens_spin.value(),
        }
        self.settings_changed.emit(settings)
