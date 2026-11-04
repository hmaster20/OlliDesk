"""Сервис выбора моделей (2.8).

Единая точка чтения конфига + выбора моделей (парк / coder / пути).
Убирает повторные `load_config()` из:
  * `_ModelPoolLoadThread`,
  * `ChatPanel._detect_and_load_slm`,
  * `MainWindow._open_slm_manager`.

Модель конфига кэшируется в памяти. `reload_config()` — при смене
файла на диске (SLMModelsDialog, ручное редактирование, смена профиля).
"""

from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger

from agents.download_slm import get_model_paths
from agents.model_pool import Role
from agents.slm_selector import select_coder, select_pair_park, select_park
from core.config import AppConfig, SLMModelInfo, load_config
from core.profiles import ProfileConfig


class ModelService:
    """Фасад над `load_config` + `slm_selector` + `download_slm`.

    Потокобезопасность: `threading.RLock` вокруг кэша конфига.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config: AppConfig | None = config
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Конфиг
    # ------------------------------------------------------------------

    def reload_config(self) -> AppConfig:
        """Перечитывает конфиг с диска (всегда) и кэширует."""
        with self._lock:
            self._config = load_config()
            return self._config

    def get_config(self) -> AppConfig:
        """Возвращает конфиг (кэш или `load_config`)."""
        with self._lock:
            if self._config is None:
                self._config = load_config()
            return self._config

    def get_active_profile(self) -> ProfileConfig | None:
        """Активный профиль из конфига (или ``None``)."""
        cfg = self.get_config()
        for p in cfg.profiles:
            if p.id == cfg.active_profile:
                return p
        return None

    # ------------------------------------------------------------------
    # Выбор моделей
    # ------------------------------------------------------------------

    def resolve_park(
        self,
        profile: ProfileConfig | None = None,
    ) -> dict[Role, SLMModelInfo]:
        """Парк моделей для профиля (явные id → автовыбор по тегам).

        Args:
            profile: Профиль. Если ``None`` — из `get_active_profile()`.

        Returns:
            dict ``{Role: SLMModelInfo}``. Пустой — если ни одна модель
            не подходит.
        """
        profile = profile or self.get_active_profile()
        park_roles = dict(profile.park_roles) if profile else {}
        if park_roles:
            return select_park(profile_park_roles=park_roles)
        return select_pair_park()

    def resolve_coder(
        self,
        profile: ProfileConfig | None = None,
    ) -> SLMModelInfo | None:
        """Coder-модель для профиля (10.6)."""
        profile = profile or self.get_active_profile()
        coder_models = list(profile.coder_models) if profile else []
        return select_coder(profile_coder_models=coder_models)

    def resolve_paths(self) -> tuple[Path | None, Path | None]:
        """Пути к chat- и embed-моделям активной SLM.

        Обёртка над `agents.download_slm.get_model_paths`. Если пути
        не найдены — ``(None, None)``.
        """
        try:
            return get_model_paths()
        except Exception as e:
            logger.warning(f"ModelService.resolve_paths: {e}")
            return (None, None)


__all__ = ["ModelService"]