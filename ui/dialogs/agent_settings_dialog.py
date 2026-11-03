"""Диалог настроек агента (9.2: правая панель → поповер).

Раньше это была правая панель `MainWindow.right_panel` — занимала место
постоянно и содержала ползунок температуры, max tokens, context window,
max iterations, чекбоксы инструментов и RAG. Теперь — модальный диалог,
открывается по «⚙» из ChatPanel (сигнал `agent_panel_toggle`).

Состояние инициализируется из `AppConfig`/`ProjectSettings` через
`ChatPanel.get_settings()`, но диалог не зависит от ChatPanel напрямую —
он принимает стартовые значения и эмитит `settings_applied(dict)`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class AgentSettingsDialog(QDialog):
    """Диалог настроек агента (замена правой панели)."""

    settings_applied = Signal(dict)

    def __init__(
        self,
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context_window: int = 8192,
        max_iterations: int = 10,
        show_reasoning: bool = True,
        rag_enabled: bool = True,
        tools_enabled: dict[str, bool] | None = None,
        allow_arbitrary_shell: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки агента")
        self.setMinimumSize(440, 720)
        self._build_ui(
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
            max_iterations=max_iterations,
            show_reasoning=show_reasoning,
            rag_enabled=rag_enabled,
            tools_enabled=tools_enabled or {},
            allow_arbitrary_shell=allow_arbitrary_shell,
        )

    def _build_ui(
        self,
        *,
        temperature: float,
        max_tokens: int,
        context_window: int,
        max_iterations: int,
        show_reasoning: bool,
        rag_enabled: bool,
        tools_enabled: dict[str, bool],
        allow_arbitrary_shell: bool = False,
    ) -> None:
        layout = QVBoxLayout(self)

        # --- Генерация ---
        gen_group = QGroupBox("Генерация")
        gen_form = QFormLayout(gen_group)

        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(int(temperature * 100))
        self.temp_value = QLabel(f"{temperature:.2f}")
        self.temp_slider.valueChanged.connect(lambda v: self.temp_value.setText(f"{v / 100:.2f}"))
        temp_row = QHBoxLayout()
        temp_row.addWidget(self.temp_slider)
        temp_row.addWidget(self.temp_value)
        gen_form.addRow("Temperature:", temp_row)

        self.tokens_spin = QSpinBox()
        self.tokens_spin.setRange(256, 32768)
        self.tokens_spin.setValue(max_tokens)
        self.tokens_spin.setSingleStep(256)
        gen_form.addRow("Max tokens:", self.tokens_spin)

        self.context_spin = QSpinBox()
        self.context_spin.setRange(1024, 128000)
        self.context_spin.setValue(context_window)
        self.context_spin.setSingleStep(1024)
        gen_form.addRow("Context window:", self.context_spin)

        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(1, 50)
        self.iterations_spin.setValue(max_iterations)
        gen_form.addRow("Max iterations (агент):", self.iterations_spin)

        layout.addWidget(gen_group)

        # --- Отображение ---
        display_group = QGroupBox("Отображение")
        display_layout = QVBoxLayout(display_group)
        self.show_reasoning_check = QCheckBox("Показывать размышления модели")
        self.show_reasoning_check.setChecked(show_reasoning)
        display_layout.addWidget(self.show_reasoning_check)
        layout.addWidget(display_group)

        # --- Инструменты ---
        tools_group = QGroupBox("Инструменты")
        tools_layout = QVBoxLayout(tools_group)
        self._tool_checkboxes: dict[str, QCheckBox] = {}
        tool_names = [
            ("read_file", "Чтение файлов"),
            ("list_directory", "Список директории"),
            ("write_file", "Запись файлов"),
            ("search_codebase", "Поиск по коду"),
            ("web_search", "Поиск в интернете"),
            ("get_git_status", "Статус Git"),
            ("create_snapshot", "Снапшот Git"),
            ("undo_last_snapshot", "Откат снапшота"),
            ("run_shell_command", "Shell-команды"),
        ]
        for key, label in tool_names:
            cb = QCheckBox(label)
            cb.setChecked(tools_enabled.get(key, True))
            tools_layout.addWidget(cb)
            self._tool_checkboxes[key] = cb
        layout.addWidget(tools_group)

        # --- RAG ---
        self.rag_check = QCheckBox("Использовать RAG")
        self.rag_check.setChecked(rag_enabled)
        layout.addWidget(self.rag_check)

        # --- 0.1.1: Безопасность ---
        security_group = QGroupBox("Безопасность")
        security_layout = QVBoxLayout(security_group)
        self.allow_arbitrary_shell_check = QCheckBox(
            "Разрешить произвольные shell-команды (dangerous mode)"
        )
        self.allow_arbitrary_shell_check.setChecked(allow_arbitrary_shell)
        self.allow_arbitrary_shell_check.setToolTip(
            "По умолчанию run_shell_command работает только по allowlist "
            "(Test-Path, Get-Content, git status, python -m pytest, …). "
            "Включение dangerous mode разрешает произвольные команды, "
            "но разрушительные (rm -rf, format, shutdown, …) всё равно "
            "блокируются. Каждый вызов пишется в ~/.ollidesk/audit.db."
        )
        security_layout.addWidget(self.allow_arbitrary_shell_check)
        layout.addWidget(security_group)

        # [7] MCP.
        try:
            from core.config import load_config as _load_cfg

            _cfg = _load_cfg()
        except Exception:
            _cfg = None

        mcp_group = QGroupBox("MCP-серверы")
        mcp_layout = QVBoxLayout(mcp_group)

        self._mcp_enabled_check = QCheckBox(
            "Включить MCP (Model Context Protocol)"
        )
        self._mcp_enabled_check.setChecked(
            bool(_cfg and _cfg.mcp.enabled)
        )
        self._mcp_enabled_check.setToolTip(
            "MCP-серверы дают доступ к внешним инструментам "
            "(файлы, git, поиск, …) через единый протокол. "
            "По умолчанию выключено."
        )
        mcp_layout.addWidget(self._mcp_enabled_check)

        # Список серверов (простое текстовое представление).
        servers_text = ""
        if _cfg and _cfg.mcp.servers:
            servers_text = "\n".join(
                f"  • {s.name}: {s.command} {' '.join(s.args)} "
                f"[{', '.join(s.capabilities)}]"
                for s in _cfg.mcp.servers
            )
        servers_label = QLabel(
            f"Серверы ({len(_cfg.mcp.servers) if _cfg else 0}):\n"
            f"{servers_text or '  (нет — добавьте в config.yaml: mcp.servers)'}"
        )
        servers_label.setStyleSheet(
            "color: #888; font-family: 'Consolas', monospace;"
            " font-size: 11px; padding: 4px 8px;"
        )
        servers_label.setWordWrap(True)
        mcp_layout.addWidget(servers_label)

        layout.addWidget(mcp_group)

        # [2.11] browser_control.
        browser_group = QGroupBox("Браузер (browser_control)")
        browser_layout = QVBoxLayout(browser_group)

        _cfg_b = None
        try:
            from core.config import load_config as _load_cfg_b

            _cfg_b = _load_cfg_b()
        except Exception:
            pass

        current_mode = _cfg_b.browser.mode if _cfg_b else "isolated"
        current_ack = (
            _cfg_b.browser.full_access_acknowledged if _cfg_b else False
        )

        self._browser_mode_combo = QComboBox()
        self._browser_mode_combo.addItem(
            "🔒 isolated — отдельный профиль (безопасно)", "isolated"
        )
        self._browser_mode_combo.addItem(
            "🔗 extension — через расширение (вкладки с разрешения)", "extension"
        )
        self._browser_mode_combo.addItem(
            "⚠️ full — доступ ко всем сессиям (опасно)", "full"
        )
        _idx = self._browser_mode_combo.findData(current_mode)
        if _idx >= 0:
            self._browser_mode_combo.setCurrentIndex(_idx)
        self._browser_mode_combo.currentIndexChanged.connect(
            self._on_browser_mode_changed
        )
        browser_layout.addWidget(self._browser_mode_combo)

        hint = QLabel(
            "isolated: публичные страницы без ваших куков. "
            "extension: нужен Playwright MCP Bridge, вкладки — с разрешения. "
            "full: CDP к копии реального профиля, все сессии; каждое действие "
            "в audit log."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        browser_layout.addWidget(hint)

        self._browser_full_ack_check = QCheckBox(
            "Подтверждаю полный доступ (режим «full»)"
        )
        self._browser_full_ack_check.setChecked(bool(current_ack))
        self._browser_full_ack_check.setVisible(current_mode == "full")
        browser_layout.addWidget(self._browser_full_ack_check)

        layout.addWidget(browser_group)

        layout.addStretch()

        # Кнопки.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(buttons)

    def _collect(self) -> dict:
        return {
            "temperature": self.temp_slider.value() / 100,
            "max_tokens": self.tokens_spin.value(),
            "context_window": self.context_spin.value(),
            "max_iterations": self.iterations_spin.value(),
            "show_reasoning": self.show_reasoning_check.isChecked(),
            "rag_enabled": self.rag_check.isChecked(),
            "tools": {k: cb.isChecked() for k, cb in self._tool_checkboxes.items()},
            "allow_arbitrary_shell": self.allow_arbitrary_shell_check.isChecked(),
            "mcp_enabled": self._mcp_enabled_check.isChecked(),
            "browser_mode": self._browser_mode_combo.currentData(),
            "browser_full_ack": self._browser_full_ack_check.isChecked(),
        }

    def _on_apply(self) -> None:
        self.settings_applied.emit(self._collect())

    def _on_accept(self) -> None:
        self._on_apply()
        self.accept()

    @Slot(int)
    def _on_browser_mode_changed(self, _index: int) -> None:
        """[2.11] При выборе «full» — показать чекбокс согласия."""
        mode = self._browser_mode_combo.currentData()
        self._browser_full_ack_check.setVisible(mode == "full")
        if mode == "full":
            QMessageBox.warning(
                self,
                "Полный доступ к браузеру",
                "Агент получит доступ ко всем вашим сессиям "
                "(включая авторизованные сайты).\n\n"
                "Защита: каждое действие пишется в audit log. "
                "Для активации поставьте галочку ниже.",
                QMessageBox.StandardButton.Ok,
            )


__all__ = ["AgentSettingsDialog"]
