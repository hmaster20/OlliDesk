"""Ollama-контроллер: доступность сервиса и проверка поддержки tools.

Вынесено из ui/main_window.py (план 2.4, «Ollama-остров»). Здесь живёт
всё, что специфично для Ollama как резервного бэкенда:
  * OllamaClient + ModelRegistry (справочник Ollama-моделей);
  * фоновая проверка доступности (_OllamaCheckThread);
  * фоновая проверка поддержки инструментов (ModelCheckThread);
  * состояние: is_connected / available_models / is_checking.

MainWindow не знает про потоки и HTTP — только подключает сигналы
контроллера к своим UI-слотам (статус-бар, ChatPanel, QMessageBox),
по образцу FileTreeController / IndexingController.

Когда Ollama будет выведена из эксплуатации, достаточно удалить этот
файл, его создание/подключение в MainWindow и пункт меню/палитры.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from agents.ollama_client import OllamaClient
from core.model_registry import ModelRegistry
from ui.chat.threads import _OllamaCheckThread

_DEFAULT_URL = "http://localhost:11434"


class ModelCheckThread(QThread):
    """Поток проверки поддержки инструментов моделями Ollama.

    Логика перенесена из main_window.py без изменений: проверяются только
    локальные модели с непроверенным ``supports_tools``; при недоступном
    сервисе цикл прерывается на первой ошибке (не N таймаутов подряд).

    ⚠ Сигнал ``finished(dict)`` переименован в ``check_finished(dict)``:
    прежнее имя перекрывало встроенный ``QThread.finished``. Все подписчики
    живут в этом же файле, внешнее поведение не меняется.
    """

    progress = Signal(str, int, int)  # model_name, current, total
    model_checked = Signal(str, bool)  # model_name, supports_tools
    check_finished = Signal(dict)  # {model_name: supports_tools}

    def __init__(self, base_url: str, registry: ModelRegistry) -> None:
        super().__init__()
        self.base_url = base_url
        self.registry = registry
        self._cancel = False

    def cancel(self) -> None:
        """Запрашивает остановку проверки."""
        self._cancel = True

    def run(self) -> None:
        asyncio.run(self._check())

    async def _check(self) -> None:
        from agents.ollama_client import OllamaClient
        from core.exceptions import OllamaConnectionError

        client = OllamaClient(base_url=self.base_url)
        results: dict[str, bool] = {}
        models_to_check = [
            name
            for name, info in self.registry.models.items()
            if info.is_local and info.supports_tools is None
        ]
        total = len(models_to_check)
        if total == 0:
            self.check_finished.emit(results)
            return
        async with client:
            for i, model in enumerate(models_to_check):
                if self._cancel:
                    logger.info(f"Проверка моделей прервана пользователем на модели {model}")
                    break
                self.progress.emit(model, i + 1, total)
                try:
                    supports = await client.check_tools_support(model)
                    results[model] = supports
                    self.registry.update_tool_support(model, supports)
                    self.model_checked.emit(model, supports)
                except OllamaConnectionError as e:
                    # Сервис недоступен: перебирать остальные N моделей
                    # бессмысленно — все упадут с тем же таймаутом.
                    logger.warning(
                        f"Ollama недоступна, проверка моделей прервана на {i + 1}/{total} ({model}): {e}"
                    )
                    break
                except Exception as e:
                    logger.error(f"Ошибка проверки модели {model}: {e}")
                    results[model] = False
                    self.registry.update_tool_support(model, False)
                    self.model_checked.emit(model, False)
        self.check_finished.emit(results)


class OllamaController(QObject):
    """Состояние и фоновые проверки Ollama. Наружу — только сигналы."""

    models_synced = Signal(list)  # реестр синхронизирован со списком моделей
    check_progress = Signal(str, int, int)
    model_checked = Signal(str, bool)
    check_finished = Signal(dict)
    check_state_changed = Signal(str)  # checking | stopping | idle | background
    check_refused = Signal(str)  # disabled | unavailable

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = OllamaClient(base_url=self._url_from_config())
        self._registry = ModelRegistry()
        self._connected = False
        self._available_models: list[str] = []
        self._check_thread: _OllamaCheckThread | None = None
        self._tools_thread: ModelCheckThread | None = None
        self._is_checking = False

    # ------------------------------------------------------------------
    # Публичный API (для MainWindow)
    # ------------------------------------------------------------------
    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def available_models(self) -> list[str]:
        return list(self._available_models)

    @property
    def is_checking(self) -> bool:
        return self._is_checking

    def check_connection(self) -> None:
        """Фоновая проверка доступности — только если Ollama включена в конфиге.

        Никаких HTTP-запросов на localhost, если бэкенд выключен.
        """
        if not self._enabled_in_config():
            logger.debug("Ollama не в enabled-списке — проверка подключения пропущена")
            return
        QTimer.singleShot(500, self._do_check_connection)

    def toggle_tools_check(self) -> None:
        """Кнопка 🔄: старт проверки tools, повторное нажатие — остановка."""
        if self._is_checking:
            self.stop_tools_check()
            return
        if self._tools_thread is not None and self._tools_thread.isRunning():
            return
        if not self._enabled_in_config():
            self.check_refused.emit("disabled")
            return
        if not self._connected:
            self.check_refused.emit("unavailable")
            return
        self._is_checking = True
        self.check_state_changed.emit("checking")
        self._tools_thread = ModelCheckThread(self._client.base_url, self._registry)
        self._tools_thread.progress.connect(self.check_progress)
        self._tools_thread.model_checked.connect(self.model_checked)
        self._tools_thread.check_finished.connect(self._on_tools_finished)
        self._tools_thread.start()

    def stop_tools_check(self) -> None:
        """Останавливает проверку tools.

        Не используем terminate(): Python-поток с asyncio.run внутри через
        PyThreadState_SetAsyncExc не убивается — только портится состояние.
        Шлём cancel() и ждём штатного выхода (потолок — один httpx-таймаут).
        """
        thread = self._tools_thread
        if thread is not None and thread.isRunning():
            thread.cancel()
            self.check_state_changed.emit("stopping")
            if thread.wait(10000):
                self._is_checking = False
                self.check_state_changed.emit("idle")
                return
            logger.warning(
                "ModelCheckThread не завершился за 10 сек; продолжает работу в фоне и выйдет по cancel()"
            )
            self.check_state_changed.emit("background")
            return
        self._is_checking = False
        self.check_state_changed.emit("idle")

    def shutdown(self) -> None:
        """Останавливает фоновые потоки (вызывается из MainWindow.closeEvent)."""
        if self._check_thread is not None and self._check_thread.isRunning():
            self._check_thread.wait(1000)
        self.stop_tools_check()

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------
    @staticmethod
    def _url_from_config() -> str:
        try:
            from core.config import load_config

            return load_config().backends.ollama_url or _DEFAULT_URL
        except Exception:
            return _DEFAULT_URL

    @staticmethod
    def _enabled_in_config() -> bool:
        try:
            from core.config import load_config

            return "ollama" in load_config().backends.enabled
        except Exception:
            return False

    def _do_check_connection(self) -> None:
        if self._check_thread is not None and self._check_thread.isRunning():
            return
        self._check_thread = _OllamaCheckThread(self._client)
        self._check_thread.models_ready.connect(self._on_connection_checked)
        self._check_thread.start()

    def _on_connection_checked(self, ok: bool, models: list) -> None:
        self._available_models = list(models or [])
        self._connected = bool(ok) and bool(self._available_models)
        if not self._connected:
            logger.info(f"Ollama: не подключён (ok={ok}, моделей={len(self._available_models)})")
            return
        logger.info(f"Ollama: подключён, моделей={len(self._available_models)}")
        try:
            self._registry.sync_with_ollama(self._available_models)
        except Exception as e:
            logger.warning(f"sync_with_ollama failed: {e}")
        self.models_synced.emit(list(self._available_models))

    def _on_tools_finished(self, results: dict) -> None:
        self._is_checking = False
        self.check_finished.emit(results)


__all__ = ["ModelCheckThread", "OllamaController"]
