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
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from core.config import AppConfig, SLMModelInfo, save_config
from ui.state.async_resource import AsyncResource


class PipelineSelectors(QWidget):
    """Пара ComboBox (planner + analyst) + кнопка «⋯» + индикатор состояния."""

    planner_changed = Signal(str)  # model_id или ""
    analyst_changed = Signal(str)  # model_id или ""
    open_slm_dialog_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False
        # [9.3.2] Активный профиль — для подсветки coder-моделей.
        self._active_profile_id: str = ""
        # Иконки ролей (9.3.2): coder / reasoning / moE / router / planner /
        # verifier / embed. Показываются в префиксе названия.
        self._role_icons: dict[str, str] = {
            "coder": "⚡",
            "reasoning": "🧠",
            "moE": "📦",
            "router": "🔀",
            "planner": "📋",
            "verifier": "✅",
            "embed": "🔤",
        }

        # [9.11] Ресурс «список моделей». Не мигает при refresh:
        # при обновлении combo сохраняет старые items до `set_value`.
        self._models_resource: AsyncResource[list[SLMModelInfo]] = AsyncResource()

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.planner_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.planner_combo.setToolTip("Planner (GPU) — classify/plan/critique. Рекомендуется 3B.")
        self.planner_combo.currentIndexChanged.connect(self._on_planner_selected)
        layout.addWidget(self.planner_combo, stretch=1)

        # Analyst.
        analyst_lbl = QLabel("A:")
        analyst_lbl.setStyleSheet("color: #888; font-size: 11px;")
        analyst_lbl.setToolTip("Analyst: CPU, synthesis/regen (7B/14B)")
        layout.addWidget(analyst_lbl)
        self.analyst_combo = QComboBox()
        self.analyst_combo.setMinimumWidth(160)
        self.analyst_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.analyst_combo.setToolTip("Analyst (CPU) — synthesis/regen. Рекомендуется 7B.")
        self.analyst_combo.currentIndexChanged.connect(self._on_analyst_selected)
        layout.addWidget(self.analyst_combo, stretch=1)

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
                text = combo.itemText(i)
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

    def set_active_profile(self, profile_id: str) -> None:
        """[9.3.2] Обновляет активный профиль и перерисовывает ComboBox'ы.

        В профиле ``development`` coder-модели выделяются жирным и
        цветом-акцентом. В ``research``/``learning`` — обычный стиль.

        Args:
            profile_id: ``"development"`` | ``"research"`` | ``"learning"``
                или id пользовательского профиля.
        """
        if profile_id == self._active_profile_id:
            return
        self._active_profile_id = profile_id
        # Перерисовываем без похода в HF — только метки.
        self._apply_models_to_combos()

    def refresh_models(self) -> None:
        """Перечитывает список моделей через `AsyncResource` (9.11).

        Правило guard'а: `begin_load()` → `set_value()`/`set_error()`.
        Если в ресурсе уже есть `latest` (повторный вызов) — состояние
        будет `REFRESHING`, combo сохранит старые items до `set_value`.
        Это устраняет «вспышку» пустого combo при `refresh_models_requested`.
        """
        from agents.download_slm import is_model_installed

        self._models_resource.begin_load()
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
            self._models_resource.set_value(sorted_models)
        except Exception as e:
            logger.exception("PipelineSelectors.refresh_models failed")
            self._models_resource.set_error(e)

        self._apply_models_to_combos()

    def _apply_models_to_combos(self) -> None:
        """Рендерит `_models_resource` в оба ComboBox (guard-паттерн 9.11).

        В состоянии `LOADING` (первая загрузка) — показываем плейсхолдер.
        В `READY`/`REFRESHING`/`ERROR с latest` — рендерим `latest`.
        Ошибка без `latest` → плейсхолдер «Ошибка загрузки».
        """
        from agents.download_slm import is_model_installed

        self._updating = True
        try:
            if self._models_resource.loaded():
                models = self._models_resource.value_or([])
                for combo, current_id in (
                    (self.planner_combo, self._config.slm.planner_model_id),
                    (self.analyst_combo, self._config.slm.analyst_model_id),
                ):
                    self._fill_combo(combo, models, current_id)
            elif self._models_resource.is_loading_first_time():
                for combo in (self.planner_combo, self.analyst_combo):
                    combo.blockSignals(True)
                    combo.clear()
                    combo.addItem("Загрузка…", "")
                    combo.setEnabled(False)
                    combo.blockSignals(False)
            else:
                # ERROR без latest.
                err_text = (
                    f"Ошибка: {type(self._models_resource.error).__name__}"
                    if self._models_resource.error
                    else "Ошибка загрузки"
                )
                for combo in (self.planner_combo, self.analyst_combo):
                    combo.blockSignals(True)
                    combo.clear()
                    combo.addItem(err_text, "")
                    combo.setEnabled(False)
                    combo.blockSignals(False)
        finally:
            self._updating = False

    def _fill_combo(
        self,
        combo: QComboBox,
        models: list[SLMModelInfo],
        current_id: str,
        ) -> None:
        """Заполняет один ComboBox (guard 9.11) + подсветка coder (9.3.2).

        * Иконки ролей (⚡ coder, 🧠 reasoning, 📦 moE, …) — в префиксе.
        * В профиле ``development`` модели с тегом ``coder`` выделяются
          жирным шрифтом и цветом-акцентом.
        * Метки пересчитываются при `set_active_profile`, без запроса в HF.
        """
        from agents.download_slm import is_model_installed

        is_dev = self._active_profile_id == "development"
        accent = QColor("#d4a373")

        # Собираем целевой список.
        target: list[tuple[str, str, str]] = [("", "(автовыбор)", "")]
        for m in models:
            installed = is_model_installed(m.file)
            install_prefix = "✅ " if installed else "⬇️ "
            # Иконки ролей (может быть несколько — берём первые 2).
            role_icons: list[str] = []
            for tag in m.tags:
                icon = self._role_icons.get(tag)
                if icon and icon not in role_icons:
                    role_icons.append(icon)
            role_prefix = "".join(role_icons[:2])
            if role_prefix:
                role_prefix += " "
            label = f"{role_prefix}{install_prefix}{m.display_name}"
            target.append((m.id, label, self._build_model_tooltip(m, installed)))

        # Сверяем с текущими (без учёта стилей — только text/data).
        current: list[tuple[str, str]] = [
            (combo.itemData(i) or "", combo.itemText(i))
            for i in range(combo.count())
        ]
        if current == [(tid, tlabel) for tid, tlabel, _ in target]:
            combo.setEnabled(True)
            # Даже если текст не менялся, могли измениться стили —
            # применяем идемпотентно (setItemData без изменения).
            self._apply_coder_highlight(combo, models, is_dev, accent)
            return

        combo.blockSignals(True)
        combo.clear()
        for tid, label, tooltip in target:
            idx = combo.count()
            combo.addItem(label, tid)
            if tooltip:
                combo.setItemData(idx, tooltip, Qt.ItemDataRole.ToolTipRole)
        idx = combo.findData(current_id) if current_id else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        combo.setEnabled(True)

        # [9.3.2] Подсветка coder-моделей в профиле development.
        self._apply_coder_highlight(combo, models, is_dev, accent)
        self._sync_combo_tooltip(combo)

    def _apply_coder_highlight(
        self,
        combo: QComboBox,
        models: list[SLMModelInfo],
        is_dev: bool,
        accent: QColor,
    ) -> None:
        """Устанавливает/снимает bold+цвет для coder-моделей.

        Идемпотентно: если стиль уже установлен — повторный вызов
        не меняет `itemText` и не дёргает `currentIndexChanged`.

        Args:
            combo: ComboBox для применения.
            models: Модели, которые сейчас в combo (по id через itemData).
            is_dev: ``True`` — профиль development (подсвечивать).
            accent: Цвет акцента.
        """
        models_by_id = {m.id: m for m in models}
        for i in range(combo.count()):
            model_id = combo.itemData(i) or ""
            info = models_by_id.get(str(model_id)) if model_id else None
            is_coder = bool(info and "coder" in info.tags)
            bold = bool(is_dev and is_coder)

            font = QFont()
            font.setBold(bold)
            combo.setItemData(i, font, Qt.ItemDataRole.FontRole)

            # ForegroundRole: акцент для coder в dev, иначе — сброс (None
            # не установить, ставим пустой QColor через invalid).
            if bold:
                combo.setItemData(
                    i, accent, Qt.ItemDataRole.ForegroundRole
                )
            else:
                combo.setItemData(i, None, Qt.ItemDataRole.ForegroundRole)

    @staticmethod
    def _build_model_tooltip(m: SLMModelInfo, installed: bool) -> str:
        """Подробный HTML-tooltip для пункта ComboBox.

        Содержит: display_name (не усечён), id, tier, n_ctx, размер, файл,
        репозиторий, статус установки.
        """
        tier_labels = {
            0: "T0 микро (≤4 ГБ RAM, CPU)",
            1: "T1 малый (≈4 ГБ VRAM, GPU)",
            2: "T2 средний (6–8 ГБ VRAM / 16 ГБ RAM)",
            3: "T3 крупный (10–12 ГБ VRAM / 32 ГБ RAM)",
            4: "T4 XL (16–24 ГБ VRAM / 64 ГБ RAM)",
            5: "T5 XXL (32+ ГБ VRAM / 96+ ГБ RAM)",
        }
        ctx = f"{m.n_ctx} ток." if m.n_ctx > 0 else "из config.slm.n_ctx"
        kind_label = "chat" if m.kind == "chat" else f"{m.kind} (не для чата!)"
        lines = [
            f"<b>{m.display_name}</b>",
            f"<code>{m.id}</code>",
            "",
            f"Уровень: {tier_labels.get(m.tier, f'T{m.tier}')}",
            f"Назначение: {kind_label}",
            f"Контекст: {ctx}",
            f"Размер: ~{m.size_mb} МБ" if m.size_mb else "Размер: неизвестен",
            f"Файл: <code>{m.file}</code>",
            f"Репозиторий: <code>{m.repo}</code>",
            "",
            "✅ Установлена" if installed else "⬇️ Не скачана (кнопка ⬇️ в SLMModelsDialog)",
        ]
        return "<br>".join(line for line in lines if line is not None)

    def _sync_combo_tooltip(self, combo: QComboBox) -> None:
        """Tooltip на самом ComboBox — по текущему выбору."""
        current_id = combo.currentData() or ""
        if not current_id:
            combo.setToolTip(
                "<b>(автовыбор)</b><br>Модель выбирается автоматически по tier и свободной VRAM."
            )
            return
        info = self._config.slm.get_model_by_id(current_id)
        if info is None:
            combo.setToolTip(current_id)
            return
        from agents.download_slm import is_model_installed

        installed = is_model_installed(info.file)
        combo.setToolTip(self._build_model_tooltip(info, installed))

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
        self._sync_combo_tooltip(self.planner_combo)  # NEW
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
        self._sync_combo_tooltip(self.analyst_combo)  # NEW
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
