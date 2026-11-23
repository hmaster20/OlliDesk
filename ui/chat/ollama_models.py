"""Ollama-селектор моделей: UI-обвязка ChatPanel (вынесено из panel.py).

Симметрично BackendsMixin (SLM) — весь код, специфичный для Ollama:
главный combo моделей, реестр ModelRegistry, статусы/тултипы,
проверка совместимости режимов с tools, публичные методы для MainWindow.

Конвенция проекта (как у BackendsMixin): поля и виджеты создаются
в ChatPanel.__init__ / _setup_ui, миксин обращается к ним через self.
Аннотации в блоке TYPE_CHECKING — только для mypy, в рантайме не существуют.

Когда Ollama будет выведена из эксплуатации, достаточно удалить этот
файл, убрать миксин из баз ChatPanel и блок Ollama-виджетов в _setup_ui.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from agents.model_router import BackendType
from core.config import AgentMode
from core.model_registry import ModelInfo

if TYPE_CHECKING:
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

    from agents.model_router import ModelRouter
    from core.model_registry import ModelRegistry


class OllamaModelsMixin:
    """Логика Ollama-селектора ChatPanel (см. docstring модуля)."""

    if TYPE_CHECKING:
        # Виджеты — создаются в ChatPanel._setup_ui.
        model_label: QLabel
        model_combo: QComboBox
        refresh_models_btn: QPushButton
        model_status_label: QLabel
        mode_combo: QComboBox
        # Поля — инициализируются в ChatPanel.__init__.
        registry: ModelRegistry | None
        _suppress_model_save: bool
        _router: ModelRouter

        # Методы универсального слоя ChatPanel, используемые миксином.
        def _current_mode(self) -> AgentMode: ...
        def _emit_settings_changed(self) -> None: ...

    # ------------------------------------------------------------------
    # Реестр и список моделей
    # ------------------------------------------------------------------
    def set_registry(self, registry):
        self.registry = registry
        self._update_model_list()

    def update_models(self, models: list[str]) -> None:
        self._update_model_list()

    def update_model_list(self) -> None:
        """Перерисовывает список моделей (после синхронизации с Ollama).

        Публичная замена `_update_model_list()` для MainWindow
        (в `_on_model_check_finished`).
        """
        self._update_model_list()

    def _update_model_list(self):
        if not self.registry:
            return
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        all_models = self.registry.get_all_models()
        sorted_names = sorted(
            all_models.keys(),
            key=lambda x: (not all_models[x].is_local, x.lower()),
        )
        for model_name in sorted_names:
            info = all_models[model_name]
            display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
            idx = self.model_combo.count()
            self.model_combo.addItem(display, model_name)
            self.model_combo.setItemData(
                idx,
                self._build_registry_model_tooltip(info),
                Qt.ItemDataRole.ToolTipRole,
            )
        if current and self.model_combo.findData(current) >= 0:
            self.model_combo.setCurrentIndex(self.model_combo.findData(current))
        else:
            for i in range(self.model_combo.count()):
                model_data = self.model_combo.itemData(i)
                if not model_data:
                    continue
                found = self.registry.get(model_data)
                if found is not None and found.is_local:
                    self.model_combo.setCurrentIndex(i)
                    break
            else:
                if self.model_combo.count() > 0:
                    self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._update_model_status(self.model_combo.currentData())

    def _update_model_selector_visibility(self) -> None:
        """Скрывает выбор Ollama-модели, если Ollama не активна в конфиге.

        Когда Ollama выключена (не в `cfg.backends.enabled`), combo моделей,
        кнопка «🔄» (проверка tools) и иконка статуса бесполезны.
        Безопасно вызывается в любой момент: если UI ещё не создан
        (`model_combo` отсутствует), тихо выходит.
        """
        if not hasattr(self, "model_combo"):
            return
        try:
            from core.config import load_config as _load_cfg

            cfg = _load_cfg()
            ollama_enabled = "ollama" in cfg.backends.enabled
        except Exception:
            ollama_enabled = False
        for widget in (
            getattr(self, "model_label", None),
            self.model_combo,
            self.refresh_models_btn,
            self.model_status_label,
        ):
            if widget is not None:
                widget.setVisible(ollama_enabled)

    # ------------------------------------------------------------------
    # Статус и тултипы
    # ------------------------------------------------------------------
    def _update_model_status(self, model_name: str):
        if not self.registry:
            self.model_status_label.setText("❓")
            self.model_status_label.setToolTip("Реестр моделей не загружен")
            return
        info = self.registry.get(model_name)
        if not info:
            self.model_status_label.setText("❓")
            self.model_status_label.setToolTip("Модель не найдена в реестре")
            return
        if info.is_local:
            if info.supports_tools is True:
                status_icon = "✅"
                tooltip = "Поддерживает инструменты"
            elif info.supports_tools is False:
                status_icon = "⚠️"
                tooltip = "Только чат (нет поддержки tools)"
            else:
                status_icon = "❓"
                tooltip = "Поддержка tools не проверена"
        else:
            status_icon = "⬇️"
            tooltip = "Модель не скачана локально"
        if info.description:
            tooltip += f"\n{info.description}"
        self.model_status_label.setText(status_icon)
        self.model_status_label.setToolTip(tooltip)

    def update_model_status(self, model_name: str):
        if not self.registry:
            return
        info = self.registry.get(model_name)
        if not info:
            return
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == model_name:
                display = f"✅ {model_name}" if info.is_local else f"⬇️ {model_name}"
                self.model_combo.setItemText(i, display)
                break
        if self.model_combo.currentData() == model_name:
            self._update_model_status(model_name)

    @staticmethod
    def _build_registry_model_tooltip(info: ModelInfo) -> str:
        """HTML-tooltip для пункта главного селектора моделей (Ollama)."""
        lines = [f"<b>{info.name}</b>", ""]
        if info.description:
            lines.append(info.description)
            lines.append("")
        if info.recommended_modes:
            lines.append(f"Рекомендуемые режимы: {', '.join(info.recommended_modes)}")
        if info.supports_tools is True:
            lines.append("Инструменты: ✅ поддерживает")
        elif info.supports_tools is False:
            lines.append("Инструменты: ❌ только чат")
        else:
            lines.append("Инструменты: ❓ не проверено")
        lines.append("Локально: ✅" if info.is_local else "Локально: ⬇️ не скачана")
        return "<br>".join(lines)

    # ------------------------------------------------------------------
    # Выбор модели
    # ------------------------------------------------------------------
    def set_model(self, model: str):
        """Устанавливает модель в combo (0.1.4).

        Программная установка (восстановление ``last_model``) не должна
        триггерить сохранение: ``_suppress_model_save`` подавляет только
        сохранение; обновление статуса/совместимости остаётся.
        """
        self._suppress_model_save = True
        try:
            idx = self.model_combo.findData(model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.addItem(model, model)
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        finally:
            self._suppress_model_save = False

    def get_current_model(self) -> str:
        if self._router.active_backend != BackendType.OLLAMA:
            return ""  # Если Ollama не активна, не отдаём мусор из скрытого комбобокса
        model = self.model_combo.currentData()
        if model is not None:
            return str(model)
        text = self.model_combo.currentText()
        for prefix in ("✅ ", "⬇️ "):
            if text.startswith(prefix):
                return text[len(prefix) :]
        return text or "llama3.2"

    def _on_model_changed(self, index: int):
        """Реакция на смену модели в combo (0.1.4).

        Статус/совместимость обновляются всегда; сохранение — только
        при ручном действии пользователя (см. ``set_model``).
        """
        model_name = self.get_current_model()
        if model_name:
            self._update_model_status(model_name)
            self._check_mode_compatibility()
            if not self._suppress_model_save:
                self._save_current_settings()

    def _save_current_settings(self):
        """Эмитит settings_changed (model — только если Ollama активна).

        ⚠ Микро-упрощение при переносе: делегирует универсальному
        ``_emit_settings_changed()`` — тот же emit того же payload,
        без дублирования тела.
        """
        try:
            self._emit_settings_changed()
        except Exception as e:
            logger.warning(f"Не удалось сохранить настройки: {e}")

    # ------------------------------------------------------------------
    # Совместимость режимов с tools
    # ------------------------------------------------------------------
    def _ollama_mode_guard(self, index: int) -> bool:
        """Ollama-ограничение: Plan/Agent недоступны без поддержки tools.

        Вызывается из ChatPanel._on_mode_changed. True = переключение
        заблокировано (режим сброшен на Chat + предупреждение).
        Для SLM/неизвестных моделей ничего не ограничивает.
        """
        if index <= 0 or not self.registry:
            return False
        model_name = self.get_current_model()
        cap = self.registry.get(model_name)
        if cap and not cap.supports_tools:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(0)
            self.mode_combo.blockSignals(False)
            QMessageBox.warning(
                cast(QWidget, self),
                "Режим недоступен",
                f"Модель '{model_name}' не поддерживает инструменты.\n"
                "Используйте модели с пометкой ✅ Agent/Plan.",
            )
            return True
        return False

    def _check_mode_compatibility(self):
        if not self.registry:
            return
        model_name = self.get_current_model()
        cap = self.registry.get(model_name)
        if cap and not cap.supports_tools:
            current_mode = self._current_mode()
            if current_mode in (AgentMode.PLAN, AgentMode.AGENT):
                self.mode_combo.setCurrentIndex(0)
                QMessageBox.warning(
                    cast(QWidget, self),
                    "Режим недоступен",
                    f"Модель '{model_name}' не поддерживает инструменты.\n"
                    "Режим автоматически переключен на 'Chat'.",
                )

    # ------------------------------------------------------------------
    # Кнопка проверки tools (🔄)
    # ------------------------------------------------------------------
    def set_checking_state(self, is_checking: bool):
        self.refresh_models_btn.setText("⏳" if is_checking else "🔄")

    def set_refresh_button_text(self, text: str) -> None:
        """Меняет текст кнопки обновления моделей (⏹ Стоп / 🔄 / ⏳).

        Публичная замена прямому `refresh_models_btn.setText(...)` из
        MainWindow (индикация процесса проверки поддержки tools).
        """
        self.refresh_models_btn.setText(text)
