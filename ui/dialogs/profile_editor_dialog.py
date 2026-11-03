"""Редактор профилей использования (9.4).

Список профилей слева (встроенные — read-only, пользовательские —
редактируемые), справа — форма. Встроенные можно «клонировать»,
пользовательские — удалять. Изменения сохраняются в `AppConfig.profiles`
и в YAML, `ChatPanel` перечитывает профили после закрытия диалога.
"""

from __future__ import annotations

from copy import deepcopy

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, save_config
from core.profiles import (
    ProfileConfig,
    ProfileToolPolicies,
    default_profiles,
    get_profile_by_id,
)

# Инструменты для чекбоксов политик (совпадает с tools/registry.py::ALL_TOOLS).
_TOOL_LABELS: list[tuple[str, str]] = [
    ("read_file", "Чтение файлов"),
    ("list_directory", "Список директории"),
    ("search_codebase", "Поиск по коду"),
    ("write_file", "Запись файлов"),
    ("web_search", "Поиск в интернете"),
    ("get_weather", "Погода"),
    ("get_current_datetime", "Текущие дата/время"),
    ("get_exchange_rate", "Курс валюты"),
    ("get_exchange_rates_multi", "Курсы нескольких валют"),
    ("get_git_status", "Статус Git"),
    ("create_snapshot", "Снапшот Git"),
    ("undo_last_snapshot", "Откат снапшота"),
    ("read_attached_file", "Чтение @-файла"),
    ("record_hypothesis", "Запись гипотезы"),
    ("update_hypothesis", "Обновление гипотезы"),
    ("list_hypotheses", "Список гипотез"),
    ("run_shell_command", "Shell-команды"),
]


class ProfileEditorDialog(QDialog):
    """Диалог управления профилями использования."""

    profiles_saved = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        # Работаем с копией — сохраняем по OK, откатываем по Cancel.
        self._profiles: list[ProfileConfig] = deepcopy(config.profiles)
        self._current_profile_id: str | None = None
        self._updating = False

        self.setWindowTitle("Профили использования")
        self.setMinimumSize(900, 640)

        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Профиль — это пресет: режим агента, набор инструментов, "
            "RAG, системный промпт и лимиты итераций. Встроенные профили "
            "(🛠 Разработка / 🔬 Исследование / 🎓 Обучение) доступны только "
            "для чтения — их можно клонировать и дорабатывать."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        splitter = QHBoxLayout()

        # --- Слева: список профилей ---
        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_profile_selected)
        left.addWidget(self.list_widget, stretch=1)

        list_btns = QHBoxLayout()
        self.add_btn = QPushButton("➕ Клон")
        self.add_btn.setToolTip("Создать копию выбранного профиля для редактирования")
        self.add_btn.clicked.connect(self._clone_profile)
        list_btns.addWidget(self.add_btn)

        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self._delete_profile)
        list_btns.addWidget(self.delete_btn)
        left.addLayout(list_btns)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(260)
        splitter.addWidget(left_widget)

        # --- Справа: форма ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_holder = QWidget()
        self.form_layout = QVBoxLayout(form_holder)
        self.form_layout.setSpacing(8)

        # Заголовок/идентификация.
        id_group = QGroupBox("Идентификация")
        id_form = QFormLayout(id_group)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("латиница, цифры, _ и -")
        self.id_edit.setToolTip("Уникальный id профиля (нельзя менять у встроенного)")
        id_form.addRow("ID:", self.id_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Название для UI")
        id_form.addRow("Название:", self.name_edit)

        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("🛠")
        self.icon_edit.setMaxLength(2)
        id_form.addRow("Иконка:", self.icon_edit)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Краткое описание (tooltip)")
        id_form.addRow("Описание:", self.desc_edit)
        self.form_layout.addWidget(id_group)

        # Режим + RAG.
        behavior_group = QGroupBox("Поведение")
        behavior_form = QFormLayout(behavior_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("💬 Chat (только диалог)", "chat")
        self.mode_combo.addItem("📋 Plan (read-only)", "plan")
        self.mode_combo.addItem("🤖 Agent (полный доступ)", "agent")
        behavior_form.addRow("Режим агента:", self.mode_combo)

        # [9.12] Уровень свободы.
        self.freedom_combo = QComboBox()
        self.freedom_combo.addItem(
            "🔒 safe — только чтение, shell/git/browser запрещены", "safe"
        )
        self.freedom_combo.addItem(
            "🔍 research — чтение + web, shell/git/browser запрещены", "research"
        )
        self.freedom_combo.addItem(
            "🛠 development — запись auto, shell/git ask_first", "development"
        )
        self.freedom_combo.addItem(
            "🌐 open — всё auto (shell ask_first, secrets никогда)", "open"
        )
        self.freedom_combo.setToolTip(
            "Матрица политик по capabilities (core/policy.py::_FREEDOM_MATRIX). "
            "«open» даёт максимальную свободу, но shell всё равно спрашивает, "
            "а .env/*.pem/id_rsa* недоступны всегда."
        )
        self.freedom_combo.currentIndexChanged.connect(
            self._on_freedom_changed
        )
        behavior_form.addRow("Свобода:", self.freedom_combo)

        self.rag_check = QCheckBox("Включить RAG-контекст")
        behavior_form.addRow("RAG:", self.rag_check)

        self.rag_cats_row = QHBoxLayout()
        self.rag_code_check = QCheckBox("code")
        self.rag_docs_check = QCheckBox("docs")
        self.rag_cats_row.addWidget(self.rag_code_check)
        self.rag_cats_row.addWidget(self.rag_docs_check)
        self.rag_cats_row.addStretch()
        behavior_form.addRow("Категории RAG:", self.rag_cats_row)

        self.reasoning_check = QCheckBox("Показывать размышления модели")
        behavior_form.addRow("Reasoning:", self.reasoning_check)

        iters_row = QHBoxLayout()
        self.agent_iters_spin = QSpinBox()
        self.agent_iters_spin.setRange(1, 200)
        self.agent_iters_spin.setValue(30)
        iters_row.addWidget(QLabel("Agent:"))
        iters_row.addWidget(self.agent_iters_spin)
        iters_row.addSpacing(12)
        self.chat_iters_spin = QSpinBox()
        self.chat_iters_spin.setRange(1, 50)
        self.chat_iters_spin.setValue(7)
        iters_row.addWidget(QLabel("Chat:"))
        iters_row.addWidget(self.chat_iters_spin)
        iters_row.addStretch()
        behavior_form.addRow("Итерации:", iters_row)

        self.recommended_model_edit = QLineEdit()
        self.recommended_model_edit.setPlaceholderText("необязательно, e.g. qwen2.5-7b-instruct")
        behavior_form.addRow("Модель (подсказка):", self.recommended_model_edit)
        self.form_layout.addWidget(behavior_group)

        # Системный промпт.
        prompt_group = QGroupBox("Системный промпт профиля")
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Дополнение к промпту роли. Идёт в extra_rules, не заменяет роль."
        )
        self.prompt_edit.setMinimumHeight(100)
        prompt_layout.addWidget(self.prompt_edit)
        self.form_layout.addWidget(prompt_group)

        # Tool policies.
        tools_group = QGroupBox("Политики инструментов")
        tools_form = QFormLayout(tools_group)
        self._tool_combos: dict[str, QComboBox] = {}
        for tool_key, label in _TOOL_LABELS:
            combo = QComboBox()
            combo.addItem("auto (без подтверждения)", "auto")
            combo.addItem("ask_first (спрашивать)", "ask_first")
            combo.addItem("excluded (запрещён)", "excluded")
            tools_form.addRow(label + ":", combo)
            self._tool_combos[tool_key] = combo
        self.form_layout.addWidget(tools_group)

        self.form_layout.addStretch()
        scroll.setWidget(form_holder)
        splitter.addWidget(scroll, stretch=1)
        layout.addLayout(splitter, stretch=1)

        # --- Кнопки ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        restore_btn = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        if restore_btn is not None:
            restore_btn.setText("↩ Сбросить встроенные")
            restore_btn.setToolTip(
                "Восстановить встроенные профили (Разработка / Исследование / Обучение). "
                "Пользовательские профили сохранятся."
            )
            restore_btn.clicked.connect(self._restore_builtins)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Список
    # ------------------------------------------------------------------

    def _refresh_list(self) -> None:
        current_id = self._current_profile_id
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._profiles:
            suffix = "" if p.builtin else " ✎"
            item = QListWidgetItem(f"{p.icon} {p.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            if p.builtin:
                item.setToolTip("Встроенный профиль (только для чтения)")
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        # Выбираем предыдущий или первый.
        idx = self._find_list_index(current_id) if current_id else 0
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(max(0, idx))

    def _find_list_index(self, profile_id: str | None) -> int:
        if profile_id is None:
            return 0
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == profile_id:
                return i
        return 0

    def _on_profile_selected(self, current: QListWidgetItem | None, _previous) -> None:
        """Загружает профиль в форму.

        ВАЖНО: перед загрузкой нового профиля сохраняет правки предыдущего
        в `self._profiles` (in-memory). Иначе при переключении без явного
        Save пользователь теряет изменения.
        """
        # Сохраняем текущую форму (если был предыдущий профиль).
        if self._current_profile_id is not None and not self._updating:
            self._persist_current_from_form()

        if current is None:
            self._current_profile_id = None
            self._set_form_enabled(False)
            return
        profile_id = current.data(Qt.ItemDataRole.UserRole)
        profile = get_profile_by_id(self._profiles, profile_id)
        if profile is None:
            self._current_profile_id = None
            self._set_form_enabled(False)
            return
        self._current_profile_id = profile.id
        self._load_profile_into_form(profile)

    # ------------------------------------------------------------------
    # Форма
    # ------------------------------------------------------------------

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in (
            self.id_edit,
            self.name_edit,
            self.icon_edit,
            self.desc_edit,
            self.mode_combo,
            self.rag_check,
            self.rag_code_check,
            self.rag_docs_check,
            self.reasoning_check,
            self.agent_iters_spin,
            self.chat_iters_spin,
            self.recommended_model_edit,
            self.prompt_edit,
            *self._tool_combos.values(),
        ):
            w.setEnabled(enabled)

    def _load_profile_into_form(self, profile: ProfileConfig) -> None:
        self._updating = True
        try:
            self._set_form_enabled(True)

            is_builtin = profile.builtin
            self.id_edit.setText(profile.id)
            self.id_edit.setEnabled(not is_builtin)

            self.name_edit.setText(profile.name)
            self.icon_edit.setText(profile.icon)
            self.desc_edit.setText(profile.description)

            idx = self.mode_combo.findData(profile.agent_mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)

            self.rag_check.setChecked(profile.rag_enabled)
            self.rag_code_check.setChecked("code" in profile.rag_categories)
            self.rag_docs_check.setChecked("docs" in profile.rag_categories)
            self.reasoning_check.setChecked(profile.show_reasoning)
            self.agent_iters_spin.setValue(profile.agent_max_iterations)
            self.chat_iters_spin.setValue(profile.chat_max_iterations)
            self.recommended_model_edit.setText(profile.recommended_model)
            self.prompt_edit.setPlainText(profile.system_prompt)

            # [9.12] Свобода.
            freedom = getattr(profile, "freedom_level", "development")
            idx = self.freedom_combo.findData(freedom)
            if idx >= 0:
                self.freedom_combo.setCurrentIndex(idx)

            policies = profile.tool_policies.model_dump()
            for key, combo in self._tool_combos.items():
                value = policies.get(key, "auto")
                idx = combo.findData(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

            # Кнопку удаления выключаем для встроенных.
            self.delete_btn.setEnabled(not is_builtin)
        finally:
            self._updating = False

    def _form_to_profile(self) -> ProfileConfig | None:
        """Собирает ProfileConfig из формы. None при ошибке валидации."""
        if self._current_profile_id is None:
            return None
        current = get_profile_by_id(self._profiles, self._current_profile_id)
        if current is None:
            return None

        new_id = current.id if current.builtin else self.id_edit.text().strip()
        if not new_id:
            return None
        if (
            not current.builtin
            and new_id != current.id
            and get_profile_by_id(self._profiles, new_id) is not None
        ):
            QMessageBox.warning(self, "Ошибка", f"Профиль с id {new_id!r} уже существует.")
            return None

        rag_categories: list[str] = []
        if self.rag_check.isChecked():
            if self.rag_code_check.isChecked():
                rag_categories.append("code")
            if self.rag_docs_check.isChecked():
                rag_categories.append("docs")

        policies_dict: dict[str, str] = {
            key: (combo.currentData() or "auto") for key, combo in self._tool_combos.items()
        }

        return ProfileConfig(
            id=new_id,
            name=self.name_edit.text().strip() or new_id,
            icon=self.icon_edit.text().strip() or "🧩",
            description=self.desc_edit.text().strip(),
            builtin=current.builtin,
            agent_mode=self.mode_combo.currentData() or "agent",
            rag_enabled=self.rag_check.isChecked(),
            rag_categories=rag_categories,
            system_prompt=self.prompt_edit.toPlainText().strip(),
            show_reasoning=self.reasoning_check.isChecked(),
            agent_max_iterations=int(self.agent_iters_spin.value()),
            chat_max_iterations=int(self.chat_iters_spin.value()),
            tool_policies=ProfileToolPolicies(**policies_dict),
            recommended_model=self.recommended_model_edit.text().strip(),
            freedom_level=self.freedom_combo.currentData() or "development",
        )

    def _persist_current_from_form(self) -> bool:
        """Сохраняет текущую форму в self._profiles (in-memory)."""
        if self._current_profile_id is None:
            return False
        updated = self._form_to_profile()
        if updated is None:
            return False
        for i, p in enumerate(self._profiles):
            if p.id == self._current_profile_id:
                self._profiles[i] = updated
                return True
        return False

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_freedom_changed(self, _index: int) -> None:
        """[9.12] Подтверждение для «open»."""
        if self._updating:
            return
        if self.freedom_combo.currentData() != "open":
            return
        reply = QMessageBox.warning(
            self,
            "Свобода «open»",
            "Агент получит полный доступ к shell, интернету, git и браузеру.\n\n"
            "Защита:\n"
            "  • shell — всегда запрашивает подтверждение;\n"
            "  • .env/*.pem/id_rsa* — недоступны всегда;\n"
            "  • каждый вызов пишется в audit log.\n\n"
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            # Откатываем на development.
            idx = self.freedom_combo.findData("development")
            if idx >= 0:
                self._updating = True
                try:
                    self.freedom_combo.setCurrentIndex(idx)
                finally:
                    self._updating = False

    def _clone_profile(self) -> None:
        if self._current_profile_id is None:
            return

        # 1. Сохраняем правки текущей формы ДО клонирования — иначе
        # `_on_profile_selected`, вызванный из `_refresh_list`, сохранит
        # не то, что видит пользователь.
        self._persist_current_from_form()

        src = get_profile_by_id(self._profiles, self._current_profile_id)
        if src is None:
            return
        # Спрашиваем новый id.
        base = f"{src.id}-copy"
        new_id = base
        suffix = 2
        while get_profile_by_id(self._profiles, new_id) is not None:
            new_id = f"{base}{suffix}"
            suffix += 1
        new_id, ok = QInputDialog.getText(self, "Клон профиля", "ID нового профиля:", text=new_id)
        if not ok or not new_id.strip():
            return
        new_id = new_id.strip()
        if get_profile_by_id(self._profiles, new_id) is not None:
            QMessageBox.warning(self, "Ошибка", f"Профиль {new_id!r} уже существует.")
            return

        # model_copy копирует ВСЕ поля, включая recommended_model и
        # tool_policies — не только перечисленные в `update`.
        clone = src.model_copy(
            update={
                "id": new_id,
                "name": f"{src.name} (копия)",
                "builtin": False,
            }
        )
        self._profiles.append(clone)
        # Меняем _current_profile_id ДО _refresh_list, но _on_profile_selected
        # уже не перезапишет клона: _persist_current_from_form найдёт старый
        # профиль по _current_profile_id (мы только что его сохранили).
        self._current_profile_id = new_id
        self._refresh_list()

    def _delete_profile(self) -> None:
        if self._current_profile_id is None:
            return
        profile = get_profile_by_id(self._profiles, self._current_profile_id)
        if profile is None:
            return
        if profile.builtin:
            QMessageBox.information(
                self, "Встроенный профиль", "Встроенный профиль нельзя удалить — только клонировать."
            )
            return
        reply = QMessageBox.question(
            self,
            "Удалить профиль",
            f"Удалить профиль «{profile.name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._profiles = [p for p in self._profiles if p.id != profile.id]
        self._current_profile_id = None
        self._refresh_list()

    def _restore_builtins(self) -> None:
        custom = [p for p in self._profiles if not p.builtin]
        self._profiles = default_profiles() + custom
        self._refresh_list()
        QMessageBox.information(
            self, "Готово", "Встроенные профили восстановлены. Пользовательские сохранены."
        )

    # ------------------------------------------------------------------
    # Сохранение
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        # Сохраняем текущую форму.
        if not self._persist_current_from_form() and self._current_profile_id is not None:
            QMessageBox.warning(self, "Ошибка", "Не удалось сохранить профиль: проверьте поля формы.")
            return

        # Обновляем конфиг.
        self._config.profiles = self._profiles
        # Если active_profile указывал на удалённый — сбрасываем на development.
        if get_profile_by_id(self._profiles, self._config.active_profile) is None:
            self._config.active_profile = "development"

        try:
            save_config(self._config)
            logger.info(
                f"Профили сохранены: {len(self._profiles)} "
                f"(встроенных: {sum(1 for p in self._profiles if p.builtin)}, "
                f"пользовательских: {sum(1 for p in self._profiles if not p.builtin)})"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфиг:\n{e}")
            return

        self.profiles_saved.emit()
        self.accept()


__all__ = ["ProfileEditorDialog"]
