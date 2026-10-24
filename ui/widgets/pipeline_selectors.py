"""Селекторы Planner/Analyst в верхней панели чата (9.3).

Заменяют `pipeline_status_label` из ChatPanel. Два ComboBox — planner
(GPU, 3B/1.5B) и analyst (CPU, 7B/14B) + кнопка «⋯» (открыть SLMModelsDialog)
+ маленький индикатор состояния пайплайна (⏳/⚡/⚠️/💤) с tooltip.

Логика выбора:
  * ComboBox заполняется из `cfg.slm.get_chat_models()`;
  * первый элемент — «(автовыбор)» (пустая строка);
  * установленные модели — префикс ✅, неустановленные — ⬇️;
  * выбор сохраняется в `AppConfig.slm` и эмитит сигнал — ChatPanel
    вызывает `reset_pipeline()`, чтобы следующие research-запросы
    собирали пайплайн с новыми моделями.
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from core.config import AppConfig, save_config


class PipelineSelectors(QWidget):
    """Пара ComboBox (planner + analyst) + кнопка «⋯» + индикатор состояния."""

    planner_changed = Signal(str)  # model_id или ""
    analyst_changed = Signal(str)  # model_id или ""
    open_slm_dialog_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Индикатор состояния.
        self._state_icon = QLabel("💤")
        self._state_icon.setStyleSheet("font-size: 12px; color: #888;")
        self._state_icon.setToolTip("Состояние двухмодельного пайплайна")
        layout.addWidget(self._state_icon)

        # Planner.
        planner_lbl = QLabel("P:")
        planner_lbl.setStyleSheet("color: #888; font-size: 11px;")
        planner_lbl.setToolTip("Planner: GPU, classify/plan/critique (3B/1.5B)")
        layout.addWidget(planner_lbl)
        self.planner_combo = QComboBox()
        self.planner_combo.setMinimumWidth(160)
        self.planner_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.planner_combo.setToolTip("Planner (GPU) — classify/plan/critique. Рекомендуется 3B.")
        self.planner_combo.currentIndexChanged.connect(self._on_planner_selected)
        layout.addWidget(self.planner_combo)

        # Analyst.
        analyst_lbl = QLabel("A:")
        analyst_lbl.setStyleSheet("color: #888; font-size: 11px;")
        analyst_lbl.setToolTip("Analyst: CPU, synthesis/regen (7B/14B)")
        layout.addWidget(analyst_lbl)
        self.analyst_combo = QComboBox()
        self.analyst_combo.setMinimumWidth(160)
        self.analyst_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.analyst_combo.setToolTip("Analyst (CPU) — synthesis/regen. Рекомендуется 7B.")
        self.analyst_combo.currentIndexChanged.connect(self._on_analyst_selected)
        layout.addWidget(self.analyst_combo)

        # Кнопка «⋯».
        self.more_btn = QToolButton()
        self.more_btn.setText("⋯")
        self.more_btn.setToolTip("Управление локальными моделями (SLM)")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self.open_slm_dialog_requested.emit)
        layout.addWidget(self.more_btn)

        self.refresh_models()

    def set_recommended_model(self, model_id: str, profile_name: str = "") -> None:
        """[9.4] Подсвечивает рекомендованную профилем модель в обоих ComboBox.

        Не меняет выбор — только визуальная подсказка. Если `model_id` пуст,
        снимает все подсветки.

        Args:
            model_id: id модели (`SLMModelInfo.id`), рекомендованной профилем.
            profile_name: имя профиля для tooltip'а.
        """
        # Снимаем прежние звёздочки во всех ComboBox.
        for combo in (self.planner_combo, self.analyst_combo):
            for i in range(combo.count()):
                data = combo.itemData(i) or ""
                text = combo.itemText(i)
                # Снимаем ведущие "★ " если они были.
                if text.startswith("★ "):
                    combo.setItemText(i, text[2:])

        if not model_id:
            return

        for combo in (self.planner_combo, self.analyst_combo):
            for i in range(combo.count()):
                if combo.itemData(i) == model_id:
                    text = combo.itemText(i)
                    combo.setItemText(i, f"★ {text}")
                    tip = combo.itemData(i, Qt.ItemDataRole.ToolTipRole) or ""
                    suffix = f" (рекомендовано профилем «{profile_name}»)" if profile_name else ""
                    combo.setItemData(i, f"{tip}{suffix}".strip(), Qt.ItemDataRole.ToolTipRole)
                    break

    # ------------------------------------------------------------------
    # Заполнение ComboBox'ов
    # ------------------------------------------------------------------

    def refresh_models(self) -> None:
        """Перечитывает список моделей из конфига и обновляет ComboBox'ы."""
        from agents.download_slm import is_model_installed

        self._updating = True
        try:
            chat_models = self._config.slm.get_chat_models()
            sorted_models = sorted(
                chat_models,
                key=lambda m: (
                    0 if is_model_installed(m.file) else 1,
                    -m.tier,
                    m.display_name.lower(),
                ),
            )

            for combo, current_id in (
                (self.planner_combo, self._config.slm.planner_model_id),
                (self.analyst_combo, self._config.slm.analyst_model_id),
            ):
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("(автовыбор)", "")
                for m in sorted_models:
                    installed = is_model_installed(m.file)
                    prefix = "✅ " if installed else "⬇️ "
                    combo.addItem(f"{prefix}{m.display_name}", m.id)
                idx = combo.findData(current_id) if current_id else 0
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Слоты
    # ------------------------------------------------------------------

    def _on_planner_selected(self, index: int) -> None:
        if self._updating:
            return
        model_id = self.planner_combo.itemData(index) or ""
        analyst_id = self.analyst_combo.currentData() or ""
        if model_id and analyst_id and model_id == analyst_id:
            self._warn_same_model()
            self._revert_combo(self.planner_combo, self._config.slm.planner_model_id)
            return
        self._config.slm.planner_model_id = model_id
        save_config(self._config)
        logger.info(f"Pipeline: planner={model_id or '(авто)'}")
        self.planner_changed.emit(model_id)

    def _on_analyst_selected(self, index: int) -> None:
        if self._updating:
            return
        model_id = self.analyst_combo.itemData(index) or ""
        planner_id = self.planner_combo.currentData() or ""
        if model_id and planner_id and model_id == planner_id:
            self._warn_same_model()
            self._revert_combo(self.analyst_combo, self._config.slm.analyst_model_id)
            return
        self._config.slm.analyst_model_id = model_id
        save_config(self._config)
        logger.info(f"Pipeline: analyst={model_id or '(авто)'}")
        self.analyst_changed.emit(model_id)

    def _warn_same_model(self) -> None:
        QMessageBox.warning(
            self,
            "Одна модель на две роли",
            "Planner и analyst должны быть РАЗНЫМИ моделями — иначе "
            "двухмодельный пайплайн бесполезен (две копии съедят вдвое "
            "больше памяти).",
        )

    def _revert_combo(self, combo: QComboBox, current_id: str) -> None:
        self._updating = True
        try:
            idx = combo.findData(current_id) if current_id else 0
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Состояние пайплайна (из ChatPanel._update_pipeline_status)
    # ------------------------------------------------------------------

    def set_state(self, icon: str, text: str, tooltip: str = "", color: str = "#888") -> None:
        """Обновляет индикатор состояния пайплайна.

        Args:
            icon: «💤», «⏳», «⏸», «⚡», «⚠️».
            text: Краткое описание (для заголовка).
            tooltip: Подробности (planner/analyst device, n_ctx и т.п.).
            color: Цвет иконки (CSS-color).
        """
        self._state_icon.setText(icon)
        self._state_icon.setStyleSheet(f"font-size: 12px; color: {color};")
        full = f"{text}\n{tooltip}" if tooltip else text
        self._state_icon.setToolTip(full)


__all__ = ["PipelineSelectors"]
