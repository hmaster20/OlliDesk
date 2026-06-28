"""Диалог настроек OlliDesk — глобальные и локальные (проектные) конфиги."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, save_config


class ExcludePatternEditor(QWidget):
    """Редактор паттернов исключения (игнорирования)."""

    def __init__(self, patterns: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._patterns = list(patterns)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._refresh_list()
        layout.addWidget(self._list, stretch=1)

        btn_layout = QHBoxLayout()
        self._add_btn = QPushButton("+ Добавить")
        self._add_btn.clicked.connect(self._add_pattern)
        self._remove_btn = QPushButton("− Удалить")
        self._remove_btn.clicked.connect(self._remove_pattern)
        btn_layout.addWidget(self._add_btn)
        btn_layout.addWidget(self._remove_btn)
        layout.addLayout(btn_layout)

    def _refresh_list(self):
        self._list.clear()
        for p in self._patterns:
            item = QListWidgetItem(p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._list.addItem(item)

    def _add_pattern(self):
        item = QListWidgetItem("new_pattern")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._list.addItem(item)
        self._list.editItem(item)

    def _remove_pattern(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def get_patterns(self) -> list[str]:
        return [self._list.item(i).text().strip() for i in range(self._list.count()) if self._list.item(i).text().strip()]


class ExtensionsEditor(QWidget):
    """Редактор списка расширений."""

    def __init__(self, extensions: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._extensions = list(extensions)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QTextEdit()
        self._edit.setPlainText("\n".join(self._extensions))
        self._edit.setPlaceholderText(".py\n.js\n.ts\n...")
        layout.addWidget(self._edit)

    def get_extensions(self) -> list[str]:
        raw = self._edit.toPlainText()
        return [line.strip() for line in raw.split("\n") if line.strip()]


class SettingsDialog(QDialog):
    """Диалог настроек — редактирование глобального и локального конфига."""

    # 1. Выносим аннотации элементов PyQt на уровень класса.
    # Это жестко привяжет их к типу QSpinBox/Editor для всех методов!
    _global_ignore_editor: ExcludePatternEditor
    _global_ext_editor: ExtensionsEditor
    _global_max_size: QSpinBox
    _global_chunk_size: QSpinBox
    _global_chunk_overlap: QSpinBox

    _local_ignore_editor: ExcludePatternEditor
    _local_ext_editor: ExtensionsEditor
    _local_max_size: QSpinBox
    _local_chunk_size: QSpinBox

    def __init__(
        self,
        config: AppConfig,
        project_path: Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        # 1. Явно аннотируем конфигурацию и пути
        self._config: AppConfig = config
        self._project_path: Path | None = project_path

        # 2. Объявляем типы UI элементов, чтобы mypy точно знал их тип (особенно .value())
        # self._global_ignore_editor: ExcludePatternEditor
        # self._global_ext_editor: ExtensionsEditor
        # self._global_max_size: QSpinBox
        # self._global_chunk_size: QSpinBox
        # self._global_chunk_overlap: QSpinBox

        # self._local_ignore_editor: ExcludePatternEditor
        # self._local_ext_editor: ExtensionsEditor
        # self._local_max_size: QSpinBox
        # self._local_chunk_size: QSpinBox

        self.setWindowTitle("Настройки OlliDesk")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Вкладка глобальных настроек
        self._global_tab = self._create_global_tab()
        self._tabs.addTab(self._global_tab, "Глобальные")

        # Вкладка локальных (проектных) настроек
        self._local_tab = self._create_local_tab()
        if project_path:
            self._tabs.addTab(self._local_tab, f"Проект: {project_path.name}")
        else:
            self._local_tab.setEnabled(False)
            self._tabs.addTab(self._local_tab, "Проект (не открыт)")

        # Кнопки
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_global_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Паттерны исключения (игнорирование файлов)")
        group_layout = QVBoxLayout(group)
        self._global_ignore_editor = ExcludePatternEditor(self._config.ignored_patterns)
        group_layout.addWidget(self._global_ignore_editor)
        layout.addWidget(group)

        group2 = QGroupBox("Расширения файлов для индексации")
        group2_layout = QVBoxLayout(group2)
        self._global_ext_editor = ExtensionsEditor(self._config.index_extensions)
        group2_layout.addWidget(self._global_ext_editor)
        layout.addWidget(group2)

        nums_layout = QHBoxLayout()

        vbox1 = QVBoxLayout()
        vbox1.addWidget(QLabel("Макс. размер файла (КБ):"))
        self._global_max_size = QSpinBox()
        self._global_max_size.setRange(10, 10000)
        self._global_max_size.setValue(self._config.max_file_size_kb)
        vbox1.addWidget(self._global_max_size)

        vbox2 = QVBoxLayout()
        vbox2.addWidget(QLabel("Размер чанка (токенов):"))
        self._global_chunk_size = QSpinBox()
        self._global_chunk_size.setRange(64, 4096)
        self._global_chunk_size.setValue(self._config.chunk_size_tokens)
        vbox2.addWidget(self._global_chunk_size)

        vbox3 = QVBoxLayout()
        vbox3.addWidget(QLabel("Перекрытие чанков (токенов):"))
        self._global_chunk_overlap = QSpinBox()
        self._global_chunk_overlap.setRange(0, 1024)
        self._global_chunk_overlap.setValue(self._config.chunk_overlap_tokens)
        vbox3.addWidget(self._global_chunk_overlap)

        nums_layout.addLayout(vbox1)
        nums_layout.addLayout(vbox2)
        nums_layout.addLayout(vbox3)
        layout.addLayout(nums_layout)

        layout.addStretch()
        return tab

    def _create_local_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Локальные настройки переопределяют глобальные для текущего проекта.\n"
            "Оставляйте поля пустыми, чтобы использовать значения из глобального конфига."
        )
        info.setStyleSheet("color: #888; font-size: 12px; margin-bottom: 8px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        group = QGroupBox("Паттерны исключения (проектные)")
        group_layout = QVBoxLayout(group)
        local_patterns = self._get_local_value("ignored_patterns", [])
        self._local_ignore_editor = ExcludePatternEditor(local_patterns)
        group_layout.addWidget(self._local_ignore_editor)
        layout.addWidget(group)

        group2 = QGroupBox("Расширения файлов (проектные)")
        group2_layout = QVBoxLayout(group2)
        local_exts = self._get_local_value("index_extensions", [])
        self._local_ext_editor = ExtensionsEditor(local_exts)
        group2_layout.addWidget(self._local_ext_editor)
        layout.addWidget(group2)

        nums_layout = QHBoxLayout()

        vbox1 = QVBoxLayout()
        vbox1.addWidget(QLabel("Макс. размер файла (КБ):"))
        self._local_max_size = QSpinBox()
        self._local_max_size.setRange(10, 10000)
        self._local_max_size.setValue(self._get_local_value("max_file_size_kb", 0))
        self._local_max_size.setSpecialValueText("(глобальный)")
        vbox1.addWidget(self._local_max_size)

        vbox2 = QVBoxLayout()
        vbox2.addWidget(QLabel("Размер чанка (токенов):"))
        self._local_chunk_size = QSpinBox()
        self._local_chunk_size.setRange(64, 4096)
        self._local_chunk_size.setValue(self._get_local_value("chunk_size_tokens", 0))
        self._local_chunk_size.setSpecialValueText("(глобальный)")
        vbox2.addWidget(self._local_chunk_size)

        nums_layout.addLayout(vbox1)
        nums_layout.addLayout(vbox2)
        layout.addLayout(nums_layout)

        layout.addStretch()
        return tab

    def _get_local_value(self, key: str, default):
        if not self._project_path:
            return default
        from core.config import config_loader

        local_path = self._project_path / ".ollidesk" / "config.yaml"
        local_data = config_loader._load_yaml(local_path)
        return local_data.get(key, default)

    def _on_accept(self):
        # Сохраняем глобальный конфиг
        self._config.ignored_patterns = self._global_ignore_editor.get_patterns()
        self._config.index_extensions = self._global_ext_editor.get_extensions()
        # self._config.ignored_patterns = self._global_ignore_editor.get_patterns() # type: ignore[assignment]
        # self._config.index_extensions = self._global_ext_editor.get_extensions() # type: ignore[assignment]

        self._config.max_file_size_kb = self._global_max_size.value()
        self._config.chunk_size_tokens = self._global_chunk_size.value()
        self._config.chunk_overlap_tokens = self._global_chunk_overlap.value()
        save_config(self._config)

        # Сохраняем локальный конфиг проекта
        if self._project_path:
            local_patterns = self._local_ignore_editor.get_patterns()
            local_exts = self._local_ext_editor.get_extensions()
            local_max_size = self._local_max_size.value()
            local_chunk_size = self._local_chunk_size.value()
            # local_max_size = self._local_max_size.value()  # type: ignore[assignment]
            # local_chunk_size = self._local_chunk_size.value() # type: ignore[assignment]

            local_data = {}
            if local_patterns:
                local_data["ignored_patterns"] = local_patterns
            if local_exts:
                local_data["index_extensions"] = local_exts
            if local_max_size > 0:
                local_data["max_file_size_kb"] = local_max_size
            if local_chunk_size > 0:
                local_data["chunk_size_tokens"] = local_chunk_size

            if local_data:
                local_path = self._project_path / ".ollidesk" / "config.yaml"
                local_path.parent.mkdir(parents=True, exist_ok=True)

                # Добавляем игнорирование для untyped импорта yaml, если типы не установлены глобально
                import yaml  # type: ignore[import-untyped]
                with open(local_path, "w", encoding="utf-8") as f:
                    yaml.dump(local_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        self.accept()
