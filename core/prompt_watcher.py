"""Горячая перезагрузка системных промтов и ролей через watchdog."""

from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from core.roles import RoleManager
from core.system_prompts import SystemPromptManager


class _MarkdownHandler(FileSystemEventHandler):
    """Отслеживает изменения Markdown-файлов и дёргает перезагрузку."""

    def __init__(self, watcher: PromptWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = getattr(event, "dest_path", "") or event.src_path
        if str(path).endswith(".md"):
            self._watcher._schedule_reload()


class PromptWatcher:
    """Наблюдает за каталогами промптов и ролей и перезагружает их без рестарта.

    Изменения на диске собираются с небольшой задержкой (debounce), чтобы одна
    правка через редактор не вызывала лавину перезагрузок.
    """

    def __init__(
        self,
        prompt_manager: SystemPromptManager,
        role_manager: RoleManager,
        debounce_s: float = 0.4,
    ) -> None:
        self._prompt_manager = prompt_manager
        self._role_manager = role_manager
        self._debounce_s = debounce_s
        self._observer: BaseObserver | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Запущен ли наблюдатель."""
        return self._observer is not None

    def start(self) -> None:
        """Запускает наблюдение за каталогами промптов и ролей."""
        if self._observer is not None:
            return
        observer = Observer()
        handler = _MarkdownHandler(self)
        watched: set[Path] = set()
        for directory in (self._prompt_manager.prompts_dir, self._role_manager.roles_dir):
            if directory in watched:
                continue
            watched.add(directory)
            directory.mkdir(parents=True, exist_ok=True)
            observer.schedule(handler, str(directory), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info(f"Промпты и роли: горячая перезагрузка включена ({len(watched)} каталог(ов))")

    def stop(self) -> None:
        """Останавливает наблюдение."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
            logger.info("Промпты и роли: горячая перезагрузка остановлена")

    def _schedule_reload(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._reload)
            self._timer.daemon = True
            self._timer.start()

    def _reload(self) -> None:
        try:
            self._prompt_manager.clear_cache()
            self._role_manager.reload()
            logger.info("Промпты и роли перезагружены (изменения на диске)")
        except Exception:
            logger.exception("Не удалось перезагрузить промпты и роли")
