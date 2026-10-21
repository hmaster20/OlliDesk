"""Селектор профилей использования (9.4).

ComboBox в верхней панели ChatPanel. Заполняется из `AppConfig.profiles`,
хранит активный профиль в `AppConfig.active_profile` (persist в YAML),
эмитит `profile_changed(ProfileConfig)` — ChatPanel применяет профиль
через `_apply_profile`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QWidget

from core.config import AppConfig, save_config
from core.profiles import ProfileConfig, get_profile_by_id


class ProfilesSelector(QWidget):
    """ComboBox профилей (Разработка / Исследование / Обучение)."""

    profile_changed = Signal(object)  # ProfileConfig

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(150)
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo.setToolTip(
            "Профиль использования: влияет на набор инструментов, RAG, "
            "системный промпт и лимиты итераций"
        )
        self.combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self.combo)

        self.refresh()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Заполняет ComboBox профилями из текущего конфига."""
        self._updating = True
        try:
            self.combo.clear()
            for p in self._config.profiles:
                self.combo.addItem(f"{p.icon} {p.name}", p.id)
                idx = self.combo.count() - 1
                self.combo.setItemData(idx, p.description, Qt.ItemDataRole.ToolTipRole)
            idx = self.combo.findData(self._config.active_profile)
            self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._updating = False

    def set_config(self, config: AppConfig) -> None:
        """Заменяет конфиг (после перезагрузки из MainWindow)."""
        self._config = config
        self.refresh()

    def set_profile(self, profile_id: str) -> None:
        """Устанавливает профиль по id без эмита сигнала."""
        idx = self.combo.findData(profile_id)
        if idx >= 0 and idx != self.combo.currentIndex():
            self._updating = True
            try:
                self.combo.setCurrentIndex(idx)
            finally:
                self._updating = False

    # ------------------------------------------------------------------
    # Слот
    # ------------------------------------------------------------------

    def _on_changed(self, index: int) -> None:
        if self._updating:
            return
        profile_id = self.combo.itemData(index) or ""
        profile = get_profile_by_id(self._config.profiles, profile_id)
        if profile is None:
            return
        self._config.active_profile = profile.id
        try:
            save_config(self._config)
        except Exception:
            pass
        self.profile_changed.emit(profile)


__all__ = ["ProfilesSelector"]