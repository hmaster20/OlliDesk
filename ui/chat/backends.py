"""Управление бэкендами LLM: SLM + Ollama (вынесено из panel.py, 2.4).

`BackendsMixin` содержит методы ChatPanel, отвечающие за:
  * детект и загрузку локальной SLM (llama.cpp);
  * автовыбор/автооптимизацию SLM-модели;
  * проверку доступности Ollama;
  * переключение активного бэкенда (`_apply_backend_config`);
  * статус-строку и `backend_status_changed`.

Поля (`_router`, `_slm_client`, `_slm_model_name`, `_slm_load_generation`,
`_slm_load_thread`, `_slm_download_thread`, `_ollama_check_thread`,
`status_label`) объявлены в ChatPanel.__init__ и доступны через `self`.
Здесь — только class-level аннотации для mypy (без значений).

Миксин не имеет собственного `__init__`: ChatPanel(QWidget, BackendsMixin,
ResearchMixin) вызывает QWidget.__init__, а поля инициализируются в
ChatPanel.__init__ явно.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QLabel

from agents.download_slm import get_model_paths
from agents.local_llm_client import LocalLLMClient
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import OllamaClient
from core.event_bus import emit_event
from core.events import EventKind
from ui.chat.threads import (
    _OllamaCheckThread,
    _SLMDownloadThread,
    _SLMLoadThread,
)

if TYPE_CHECKING:
    from core.services.model_service import ModelService

class BackendsMixin:
    """Методы ChatPanel для управления SLM и Ollama (2.4)."""

    # ------------------------------------------------------------------
    # Поля, предоставляемые ChatPanel (class-level для mypy).
    # ------------------------------------------------------------------
    _router: ModelRouter
    _ollama_client: OllamaClient
    _slm_client: LocalLLMClient | None
    _slm_loading: bool
    _model_service: ModelService
    _slm_model_id: str
    _slm_model_name: str
    _slm_load_generation: int
    _slm_load_thread: _SLMLoadThread | None
    _slm_download_thread: _SLMDownloadThread | None
    _ollama_check_thread: _OllamaCheckThread | None

    status_label: QLabel
    backend_status_changed: Any  # Signal(str)

    # --- NEW: методы ChatPanel, которые вызывает BackendsMixin ---
    _update_model_selector_visibility: Callable[[], None]

    # ------------------------------------------------------------------
    # Публичный API: перезагрузка SLM
    # ------------------------------------------------------------------

    def reload_slm(self) -> None:
        """Перезагружает SLM с сохранённой конфигурацией — без автовыбора.

        Вызывается после явного выбора модели пользователем в диалоге SLM.
        Автовыбор должен быть пропущен, иначе он затрёт выбор (см. баг:
        3B q4_k_m принудительно возвращался вместо выбранной 7B/1.5B/q6_k).

        Также сбрасывает двухмодельный пайплайн — если пользователь
        изменил planner/analyst в SLMModelsDialog, при следующем
        исследовательском вопросе пайплайн соберётся уже с новыми моделями.
        """
        from PySide6.QtCore import QTimer

        self._slm_load_generation += 1
        self._slm_model_name = ""

        self._router.set_local_slm(None)
        self._router.set_slm_available(False)
        self._slm_client = None
        self._slm_loading = False

        logger.info(
            f"reload_slm: generation → {self._slm_load_generation}, "
            f"модель будет перечитана из конфига (автовыбор пропущен)"
        )
        # reset_pipeline определён в ResearchMixin — доступен через self.
        self.reset_pipeline()  # type: ignore[attr-defined]

        self.status_label.setText("⏳ Перезагрузка локальной модели...")
        QTimer.singleShot(50, lambda: self._detect_and_load_slm(skip_auto_select=True))

        # Запускаем двухмодельный пайплайн после ручной смены моделей.
        if getattr(self, "project_root", ""):
            QTimer.singleShot(1500, self._ensure_orchestrator)  # type: ignore[attr-defined]

    def _detect_and_load_slm(self, skip_auto_select: bool = False) -> None:
        """Автовыбор модели + автооптимизация параметров, затем загрузка.

        Args:
            skip_auto_select: True — не запускать автовыбор модели, использовать
                уже записанный в конфиге ``slm.selected_model``. Нужно после
                явного выбора модели пользователем в ``SLMModelsDialog``.
        """
        from agents.slm_optimizer import optimize
        from agents.slm_selector import select_best_model

        try:
            cfg = self._model_service.reload_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для автовыбора SLM: {e}")
            cfg = None

        if skip_auto_select:
            logger.info(
                "SLM: автовыбор пропущен (после ручного переключения модели). "
                "Загружается selected_model из конфига."
            )
        elif cfg is not None and cfg.slm.auto_select_model:
            chosen = select_best_model(prefer_vram=cfg.slm.prefer_vram_fit)
            if chosen is not None and chosen.id != cfg.slm.selected_model:
                logger.info(f"Автовыбор SLM: '{cfg.slm.selected_model}' → '{chosen.id}'")
                cfg.slm.selected_model = chosen.id
                try:
                    from core.config import save_config

                    save_config(cfg)
                except Exception as e:
                    logger.warning(f"Не удалось сохранить автовыбор в конфиг: {e}")
            else:
                logger.info(
                    f"SLM: автовыбор подтвердил сохранённый выбор (selected_model='{cfg.slm.selected_model}')"
                )
        elif cfg is not None:
            logger.info(
                f"SLM: используется сохранённый выбор "
                f"(selected_model='{cfg.slm.selected_model}', "
                f"auto_select_model=false)"
            )
        else:
            logger.info(
                "SLM: конфиг недоступен, автовыбор не выполнялся "
                "(будет использована модель из get_model_paths)"
            )

        chat_path, embed_path = get_model_paths()
        if not chat_path:
            if self._config_slm_enabled():
                self._download_and_load_slm()
            return

        params = None
        try:
            if cfg is not None and cfg.slm.auto_tune:
                params = optimize(chat_path, target_n_ctx=cfg.slm.target_n_ctx)
                logger.info(f"Автопараметры SLM:\n{params.to_json()}")
        except Exception as e:
            logger.warning(f"SLM-оптимизатор упал, загружаю с параметрами из конфига: {e}")
            params = None

        self._load_slm_async(chat_path, embed_path, params=params)

    def _config_slm_enabled(self) -> bool:
        """True, если SLM разрешена в конфиге (fallback True при ошибке)."""
        try:
            from core.config import load_config

            cfg = load_config()
            return cfg.slm.enabled
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Конфиг бэкендов и детект
    # ------------------------------------------------------------------

    def _apply_backend_config(self) -> None:
        """Применяет настройки бэкендов из конфига к ModelRouter."""
        try:
            from core.config import load_config

            cfg = load_config()
            self._router.set_enabled_backends(list(cfg.backends.enabled))
            self._router.set_priority(list(cfg.backends.priority))
            self._router.set_embed_backend(cfg.backends.embed_backend)
        except Exception as e:
            logger.warning(f"Не удалось применить настройки бэкендов: {e}")
        self._emit_backend_status()

    def _detect_backends(self) -> None:
        """Определяет доступность бэкендов, уважая whitelist.

        Ollama проверяется только если пользователь его явно включил в
        настройках. Иначе приложение не отправляет ни одного HTTP-запроса
        на localhost:11434.
        """
        ollama_enabled = False
        try:
            from core.config import load_config

            cfg = load_config()
            ollama_enabled = "ollama" in cfg.backends.enabled
        except Exception:
            pass

        if ollama_enabled:
            self._check_ollama_async()
        else:
            logger.info("Ollama не в enabled-списке — пропускаю проверку и автоактивацию")
            self._router.set_ollama_available(False)

        self._detect_and_load_slm()
        self._update_model_selector_visibility()

    def _active_backend_label(self) -> str:
        """Человекочитаемое имя активного бэкенда (для UI)."""
        backend = self._router.active_backend
        if backend == BackendType.OLLAMA:
            return f"🌐 Ollama · {self.get_current_model()}"
        if backend == BackendType.LOCAL_SLM:
            # Показываем, какие модели реально загружены в пайплайн
            if getattr(self, "_orchestrator", None) is not None and getattr(self, "_model_pool", None) is not None:
                try:
                    p = self._model_pool.spec(Role.PLANNER).model_id.split(".gguf")[0][:15]
                    a = self._model_pool.spec(Role.ANALYST).model_id.split(".gguf")[0][:15]
                    return f"🖥 SLM · P:{p} A:{a}"
                except Exception:
                    pass
            name = self._slm_model_name or "локальная модель"
            return f"🖥 Локально · {name}"
        if backend == BackendType.RULE_BASED:
            return "⚠️ Резерв (без LLM)"
        return "⛔ Нет доступного бэкенда"

    def _emit_backend_status(self) -> None:
        """Эмитит `backend_status_changed` для MainWindow/дашборда."""
        self.backend_status_changed.emit(self._active_backend_label())

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------

    def _check_ollama_async(self) -> None:
        """Запускает фоновую проверку Ollama."""
        self._ollama_check_thread = _OllamaCheckThread(self._ollama_client)
        self._ollama_check_thread.result.connect(self._on_ollama_check_result)
        self._ollama_check_thread.start()

    @Slot(bool)
    def _on_ollama_check_result(self, available: bool) -> None:
        """Обрабатывает результат проверки Ollama."""
        if not available:
            self._router.set_ollama_available(False)
            return
        # Ollama ответил, но это не значит, что он разрешён — router сам решит.
        self._router.set_ollama_available(True)
        self._emit_backend_status()

    # ------------------------------------------------------------------
    # SLM: загрузка/скачивание
    # ------------------------------------------------------------------

    def _on_slm_loaded(self, client, generation: int | None = None) -> None:
        """Колбэк загрузки SLM (в GUI-потоке)."""
        if generation is not None and generation != self._slm_load_generation:
            logger.info(
                f"Отбрасываю устаревшую загрузку SLM "
                f"(gen={generation}, актуальная={self._slm_load_generation}): "
                f"{Path(client.model_path).name}"
            )
            return

        self._slm_client = client
        if not self._slm_model_name:
            self._slm_model_name = Path(client.model_path).stem
        if not self._slm_model_id:
            self._slm_model_id = Path(client.model_path).stem
        self._router.set_local_slm(client)
        self._router.set_slm_available(True)
        self._slm_loading = False
        logger.info(f"Локальная SLM загружена и доступна как fallback: {self._slm_model_name}")
        if self._router.active_backend == BackendType.LOCAL_SLM:
            self.status_label.setText(f"✅ Локальная модель загружена: {self._slm_model_name}")
        self._emit_backend_status()
        # _update_pipeline_status — из ResearchMixin.
        self._update_pipeline_status()  # type: ignore[attr-defined]

        # 2.5: событие «SLM загружена».
        emit_event(
            EventKind.SLM_LOADED.value,
            {
                "model_id": self._slm_model_id,
                "load_ms": round(float(getattr(client, "load_ms", 0.0) or 0.0), 2),
            },
        )

    def _load_slm_async(
        self,
        chat_path: Path,
        embed_path: Path | None,
        params=None,
    ) -> None:
        """Запускает загрузку SLM в фоновом потоке."""
        if self._slm_loading or self._slm_client is not None:
            return

        generation = self._slm_load_generation

        # Параметры: params (авто) → конфиг (ручной режим) → дефолты.
        n_ctx, n_threads, n_batch, n_gpu_layers, max_tokens = 4096, 0, 512, 0, 1024
        try:
            from core.config import load_config

            slm = load_config().slm
            max_tokens = slm.max_tokens
            if params is None:
                n_ctx = slm.n_ctx
                n_threads = slm.n_threads
                n_batch = slm.n_batch
                if slm.auto_gpu_layers:
                    from agents.gpu_utils import detect_optimal_gpu_layers

                    n_gpu_layers = detect_optimal_gpu_layers(
                        model_path=chat_path,
                        preferred=slm.n_gpu_layers,
                        auto=True,
                    )
                else:
                    n_gpu_layers = slm.n_gpu_layers
            info = slm.get_selected_info()
            if info is not None:
                self._slm_model_name = info.display_name
                self._slm_model_id = info.id
        except Exception as e:
            logger.debug(f"Не удалось прочитать SLM-параметры из конфига: {e}")

        self._slm_loading = True
        self.status_label.setText(f"⏳ Загрузка {self._slm_model_name or chat_path.name}...")

        # 2.5: событие «началась загрузка SLM».
        emit_event(
            EventKind.SLM_LOADING.value,
            {
                "model_id": self._slm_model_id or chat_path.name,
                "path": str(chat_path),
            },
        )

        self._slm_load_thread = _SLMLoadThread(
            chat_path,
            embed_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_batch=n_batch,
            n_gpu_layers=n_gpu_layers,
            max_tokens=max_tokens,
            params=params,
        )
        self._slm_load_thread.finished.connect(
            lambda client, gen=generation: self._on_slm_loaded(client, gen)
        )
        self._slm_load_thread.error.connect(lambda err, gen=generation: self._on_slm_error(err, gen))
        self._slm_load_thread.start()

    def _download_and_load_slm(self) -> None:
        """Скачивает chat- и embed-модель, затем загружает SLM."""
        if self._slm_loading or self._slm_client is not None:
            return

        try:
            from core.config import load_config

            slm = load_config().slm
        except Exception as e:
            logger.warning(f"Не удалось загрузить конфиг SLM: {e}")
            return

        if not slm.enabled:
            return

        info = slm.get_selected_info()
        if not info:
            logger.warning("Не найдена активная SLM-модель в конфиге")
            return

        if info.kind != "chat":
            logger.error(
                f"Активная SLM-модель '{info.id}' имеет kind='{info.kind}', "
                f"а для чата нужна kind='chat'. Загрузка отменена."
            )
            self.status_label.setText(f"⚠️ Модель '{info.display_name}' не подходит для чата.")
            return

        generation = self._slm_load_generation
        self._slm_loading = True
        self.status_label.setText(f"⏳ Скачивание {info.display_name} (~{info.size_mb} МБ)...")

        self._slm_download_thread = _SLMDownloadThread(
            model_repo=info.repo,
            model_file=info.file,
            embed_repo=slm.embed_model_repo,
            embed_file=slm.embed_model_file,
        )
        self._slm_download_thread.progress.connect(
            lambda msg, gen=generation: self._on_slm_progress(msg, gen)
        )
        self._slm_download_thread.finished.connect(
            lambda chat, embed, gen=generation: self._on_slm_downloaded(chat, embed, gen)
        )
        self._slm_download_thread.error.connect(lambda err, gen=generation: self._on_slm_error(err, gen))
        self._slm_download_thread.start()

    def _on_slm_progress(self, message: str, generation: int | None = None) -> None:
        """Прогресс скачивания SLM (в GUI-потоке)."""
        if generation is not None and generation != self._slm_load_generation:
            return
        self.status_label.setText(f"⏳ {message}")

    def _on_slm_error(self, error_msg: str, generation: int | None = None) -> None:
        """Ошибка загрузки/скачивания SLM (в GUI-потоке)."""
        if generation is not None and generation != self._slm_load_generation:
            logger.info(f"Отбрасываю ошибку устаревшей загрузки SLM (gen={generation}): {error_msg}")
            return
        self._slm_loading = False
        logger.error(f"Ошибка загрузки SLM: {error_msg}")
        self.status_label.setText(f"⚠️ SLM недоступна: {error_msg}")

        # 2.5: событие «SLM не загрузилась».
        emit_event(
            EventKind.SLM_FAILED.value,
            {
                "model_id": self._slm_model_id or "?",
                "error": error_msg,
            },
        )

    def _on_slm_downloaded(
        self,
        chat_path: str,
        embed_path: str,
        generation: int | None = None,
    ) -> None:
        """Завершение скачивания SLM → запуск загрузки (в GUI-потоке)."""
        if generation is not None and generation != self._slm_load_generation:
            logger.info(f"Отбрасываю устаревшее скачивание SLM (gen={generation})")
            return
        self._slm_loading = False
        self._load_slm_async(
            Path(chat_path),
            Path(embed_path) if embed_path else None,
        )


__all__ = ["BackendsMixin"]
