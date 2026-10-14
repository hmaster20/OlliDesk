# ui/chat/backend_manager.py
"""Управление LLM-бэкендами: Ollama, локальная SLM, rule-based fallback.

Инкапсулирует:

* `ModelRouter` и его настройку (приоритет бэкендов, embed-бэкенд);
* проверку доступности Ollama;
* автовыбор и загрузку локальной SLM (llama.cpp) с автооптимизацией;
* скачивание GGUF-моделей при первом запуске;
* отмену устаревших загрузок через поколения (`_slm_load_generation`).

Взаимодействие с UI — только через сигналы. Сам BackendManager не знает
ни о комбобоксах, ни о статус-баре; метку активного бэкенда собирает панель.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal

from agents.download_slm import get_model_paths
from agents.local_llm_client import LocalLLMClient
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import OllamaClient
from agents.rule_based_fallback import RuleBasedFallback
from tools.weather import get_weather
from ui.chat.threads import (
    _OllamaCheckThread,
    _SLMDownloadThread,
    _SLMLoadThread,
)

# Значения по умолчанию для ручного режима (auto_tune=False).
_DEFAULT_N_CTX = 4096
_DEFAULT_N_THREADS = 0
_DEFAULT_N_BATCH = 512
_DEFAULT_N_GPU_LAYERS = 0
_DEFAULT_MAX_TOKENS = 1024


class BackendManager(QObject):
    """Координатор LLM-бэкендов.

    Сигналы:
        status_message(text): короткое сообщение для status_label панели.
        backend_status_changed(): активный бэкенд изменился — панель
            пересобирает и показывает метку.
        ollama_availability_changed(available): результат проверки Ollama.
        slm_loaded(display_name): локальная SLM успешно загружена.
        slm_error(error_msg): ошибка загрузки/скачивания SLM.
    """

    status_message = Signal(str)
    backend_status_changed = Signal()
    ollama_availability_changed = Signal(bool)
    slm_loaded = Signal(str)
    slm_error = Signal(str)

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        # --- Router + клиенты ---
        self._router = ModelRouter()
        self._ollama_client = OllamaClient(base_url=base_url)
        self._router.set_ollama(self._ollama_client)

        self._rule_based = RuleBasedFallback(weather_tool=get_weather)
        self._router.set_rule_based(self._rule_based)

        # --- SLM ---
        self._slm_client: LocalLLMClient | None = None
        self._slm_loading: bool = False
        self._slm_model_name: str = ""
        self._slm_load_generation: int = 0
        self._slm_load_thread: _SLMLoadThread | None = None
        self._slm_download_thread: _SLMDownloadThread | None = None

        # --- Ollama check ---
        self._ollama_check_thread: _OllamaCheckThread | None = None

        self.apply_config()

    # -------------------------------------------------------------
    # Доступ к состоянию и клиентам
    # -------------------------------------------------------------

    @property
    def router(self) -> ModelRouter:
        """Маршрутизатор запросов — используется AgentLoop и RAG."""
        return self._router

    @property
    def ollama_client(self) -> OllamaClient:
        """Ollama-клиент (для диалогов управления моделями)."""
        return self._ollama_client

    @property
    def slm_model_name(self) -> str:
        """Отображаемое имя загруженной SLM (пусто, если не загружена)."""
        return self._slm_model_name

    @property
    def active_backend(self) -> BackendType:
        """Тип активного бэкенда (для форматирования метки в панели)."""
        return self._router.active_backend

    # -------------------------------------------------------------
    # Применение настроек из конфига
    # -------------------------------------------------------------

    def apply_config(self) -> None:
        """Применяет приоритет бэкендов и выбор embed-бэкенда из конфига."""
        try:
            from core.config import load_config

            cfg = load_config()
            self._router.set_priority(list(cfg.backends.priority))
            self._router.set_embed_backend(cfg.backends.embed_backend)
        except Exception as e:
            logger.warning(f"Не удалось применить настройки бэкендов: {e}")
        self._emit_backend_status()

    def _emit_backend_status(self) -> None:
        self.backend_status_changed.emit()

    # -------------------------------------------------------------
    # Детект бэкендов
    # -------------------------------------------------------------

    def detect_backends(self) -> None:
        """Запускает проверку Ollama и попытку загрузить локальную SLM."""
        self.check_ollama_async()
        self.load_slm()

    def check_ollama_async(self) -> None:
        """Проверяет доступность Ollama в фоне."""
        self._ollama_check_thread = _OllamaCheckThread(self._ollama_client)
        self._ollama_check_thread.result.connect(self._on_ollama_check_result)
        self._ollama_check_thread.start()

    def _on_ollama_check_result(self, available: bool) -> None:
        self._router.set_ollama_available(available)
        self.ollama_availability_changed.emit(available)

        if available:
            self.status_message.emit("✅ Ollama подключён")
        elif self._slm_client is not None:
            self.status_message.emit(
                "✅ Локальная модель загружена (Ollama недоступен)"
            )
        else:
            self.status_message.emit(
                "⚠️ Ollama не найден. Используется резервный режим."
            )

        self._emit_backend_status()

    # -------------------------------------------------------------
    # Загрузка локальной SLM
    # -------------------------------------------------------------

    def load_slm(self, skip_auto_select: bool = False) -> None:
        """Автовыбор + автооптимизация + загрузка локальной SLM.

        Args:
            skip_auto_select: True — не запускать автовыбор модели, взять
                текущую из конфига. Используется после явного выбора
                модели пользователем в диалоге SLM.
        """
        from agents.slm_optimizer import optimize
        from agents.slm_selector import select_best_model

        try:
            from core.config import load_config

            cfg = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для автовыбора SLM: {e}")
            cfg = None

        if skip_auto_select:
            logger.info(
                "SLM: автовыбор пропущен (пользователь выбрал модель вручную)"
            )
        elif cfg is not None and cfg.slm.auto_select_model:
            chosen = select_best_model(prefer_vram=cfg.slm.prefer_vram_fit)
            if chosen is not None and chosen.id != cfg.slm.selected_model:
                logger.info(
                    f"Автовыбор SLM: '{cfg.slm.selected_model}' → '{chosen.id}'"
                )
                cfg.slm.selected_model = chosen.id
                try:
                    from core.config import save_config

                    save_config(cfg)
                except Exception as e:
                    logger.warning(
                        f"Не удалось сохранить автовыбор в конфиг: {e}"
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
            logger.warning(
                f"SLM-оптимизатор упал, загружаю с параметрами из конфига: {e}"
            )
            params = None

        self._load_slm_async(chat_path, embed_path, params=params)

    def reload_slm(self) -> None:
        """Перезагружает SLM с текущей конфигурацией (без автовыбора).

        Вызывается после явного выбора модели в диалоге SLM. Увеличивает
        `_slm_load_generation`, чтобы отбросить результаты устаревших загрузок
        (например, если пользователь переключил модель дважды подряд).
        """
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
        self.status_message.emit("⏳ Перезагрузка локальной модели...")
        QTimer.singleShot(
            50, lambda: self.load_slm(skip_auto_select=True)
        )

    def _config_slm_enabled(self) -> bool:
        try:
            from core.config import load_config

            return load_config().slm.enabled
        except Exception:
            return True

    # -------------------------------------------------------------
    # Загрузка / скачивание (приватные шаги)
    # -------------------------------------------------------------

    def _load_slm_async(
        self,
        chat_path: Path,
        embed_path: Path | None,
        params: object | None = None,
    ) -> None:
        """Запускает фоновую загрузку SLM с параметрами из конфига или optimizer."""
        if self._slm_loading or self._slm_client is not None:
            return

        generation = self._slm_load_generation

        n_ctx = _DEFAULT_N_CTX
        n_threads = _DEFAULT_N_THREADS
        n_batch = _DEFAULT_N_BATCH
        n_gpu_layers = _DEFAULT_N_GPU_LAYERS
        max_tokens = _DEFAULT_MAX_TOKENS

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
        except Exception as e:
            logger.debug(f"Не удалось прочитать SLM-параметры из конфига: {e}")

        self._slm_loading = True
        self.status_message.emit(
            f"⏳ Загрузка {self._slm_model_name or chat_path.name}..."
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
        self._slm_load_thread.error.connect(
            lambda err, gen=generation: self._on_slm_error(err, gen)
        )
        self._slm_load_thread.start()

    def _download_and_load_slm(self) -> None:
        """Скачивает активную chat-модель и embedding-модель, затем грузит."""
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
            self.status_message.emit(
                f"⚠️ Модель '{info.display_name}' не подходит для чата."
            )
            return

        generation = self._slm_load_generation
        self._slm_loading = True
        self.status_message.emit(
            f"⏳ Скачивание {info.display_name} (~{info.size_mb} МБ)..."
        )

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
            lambda chat, embed, gen=generation: self._on_slm_downloaded(
                chat, embed, gen
            )
        )
        self._slm_download_thread.error.connect(
            lambda err, gen=generation: self._on_slm_error(err, gen)
        )
        self._slm_download_thread.start()

    # -------------------------------------------------------------
    # Слоты фоновых потоков
    # -------------------------------------------------------------

    def _on_slm_progress(self, message: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._slm_load_generation:
            return
        self.status_message.emit(f"⏳ {message}")

    def _on_slm_error(self, error_msg: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._slm_load_generation:
            logger.info(
                f"Отбрасываю ошибку устаревшей загрузки SLM "
                f"(gen={generation}): {error_msg}"
            )
            return
        self._slm_loading = False
        logger.error(f"Ошибка загрузки SLM: {error_msg}")
        self.status_message.emit(f"⚠️ SLM недоступна: {error_msg}")
        self.slm_error.emit(error_msg)

    def _on_slm_loaded(
        self,
        client: LocalLLMClient,
        generation: int | None = None,
    ) -> None:
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

        self._router.set_local_slm(client)
        self._router.set_slm_available(True)
        self._slm_loading = False

        logger.info(
            f"Локальная SLM загружена и доступна как fallback: "
            f"{self._slm_model_name}"
        )

        if self._router.active_backend == BackendType.LOCAL_SLM:
            self.status_message.emit(
                f"✅ Локальная модель загружена: {self._slm_model_name}"
            )

        self.slm_loaded.emit(self._slm_model_name)
        self._emit_backend_status()

    def _on_slm_downloaded(
        self,
        chat_path: str,
        embed_path: str,
        generation: int | None = None,
    ) -> None:
        if generation is not None and generation != self._slm_load_generation:
            logger.info(f"Отбрасываю устаревшее скачивание SLM (gen={generation})")
            return
        self._slm_loading = False
        self._load_slm_async(
            Path(chat_path),
            Path(embed_path) if embed_path else None,
        )

    # -------------------------------------------------------------
    # Завершение работы
    # -------------------------------------------------------------

    def close(self) -> None:
        """Останавливает фоновые потоки и закрывает Ollama-клиент.

        Вызывается из `MainWindow.closeEvent` — панель просто делегирует.
        """
        for thread in (
            self._ollama_check_thread,
            self._slm_load_thread,
            self._slm_download_thread,
        ):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2000)

        # Закрываем httpx-клиент Ollama в отдельном loop'е.
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    self._ollama_client.__aexit__(None, None, None)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.debug(f"Не удалось закрыть Ollama-клиент: {e}")