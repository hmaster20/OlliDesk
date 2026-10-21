"""Контроллер фоновой индексации проекта (план 2.4).

Содержит:
  * `IndexingThread` — QThread с инкрементальным сканом и векторизацией;
  * `ProjectState` — чтение/запись `<project>/.ollidesk/project_state.json`;
  * `IndexingController` — QObject, управляющий потоком.

Не зависит от виджетов: прогресс/статус транслируются через сигналы.
`MainWindow` создаёт `IndexingController`, подключает его сигналы к своим
слотам (`_on_index_progress` и т.п.), владеет UI-виджетами прогресса
(`index_progress`, `index_status_label`, `reindex_btn`).

Вызовы:
  * `configure(project_path, indexer, vector_store)` — установить зависимости
    (загружает `previous_state` из `ProjectState.load`);
  * `start()` — запустить поток (no-op, если уже бежит);
  * `reindex()` — сбросить state и запустить;
  * `stop()` — graceful stop для `closeEvent`.

Потокобезопасность: `_on_thread_finished` больше НЕ сканирует проект повторно
через `new_event_loop().run_until_complete()` на GUI-потоке (раньше это фризило
UI на сотнях файлов × sha256). `IndexingThread` сам собирает актуальное
состояние и эмитит его через `state_ready` — сохранение в JSON дешёвое.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QObject, QThread, Signal, Slot

from fs.indexer import FileIndexer, FileMetadata
from fs.vector_store import VectorStore


class IndexingThread(QThread):
    """Поток для индексации проекта."""

    progress_updated = Signal(int, int)  # current, total
    indexing_finished = Signal()
    indexing_summary = Signal(list, list)  # added_paths, deleted_paths
    indexing_error = Signal(str)
    # NEW: актуальное состояние {rel_path: FileMetadata} для ProjectState.save.
    # Раньше контроллер после finish повторно вызывал scan_project — лишний
    # sha256-скан всех файлов на GUI-потоке.
    state_ready = Signal(object)

    def __init__(
        self,
        indexer: FileIndexer,
        vector_store: VectorStore,
        project_path: Path,
        previous_state: dict,
    ):
        super().__init__()
        self.indexer = indexer
        self.vector_store = vector_store
        self.project_path = project_path
        self.previous_state = previous_state

    def run(self):
        """Запускает индексацию."""
        asyncio.run(self._run_indexing())

    async def _run_indexing(self):
        """Выполняет индексацию проекта."""
        try:
            # Инкрементальный скан
            new_or_modified, deleted = await self.indexer.incremental_scan(
                self.project_path, self.previous_state
            )

            total = len(new_or_modified)
            paths = [m.path for m in new_or_modified]
            # Если изменений нет и удалять нечего — не дёргаем прогресс
            # (эмитим (0, 0), панель поймёт и не покажет прогресс-бар).
            if total == 0 and not deleted:
                logger.info(
                    "Индексация не требуется: изменений нет, "
                    f"состояние проекта актуально ({len(self.previous_state)} файлов)"
                )
                # Состояние не менялось — отдаём previous_state как есть.
                self.state_ready.emit(dict(self.previous_state))
                self.indexing_summary.emit([], [])
                self.indexing_finished.emit()
                return

            self.progress_updated.emit(0, total)

            # Удаляем чанки удаленных файлов
            if deleted:
                await self.vector_store.delete_by_file(deleted)

            # Обрабатываем новые и измененные файлы
            for i, file_meta in enumerate(new_or_modified):
                # [9.4] Единая точка чтения: текст или извлечённый из документа.
                content = self.indexer.read_file_content(file_meta.absolute_path)
                if not content:
                    # Документ не распознан / пустой — пропускаем без падения.
                    self.progress_updated.emit(i + 1, total)
                    continue
                chunks = self.indexer.chunk_file(content, file_meta.path, file_meta.mtime)

                # Удаляем старые чанки файла (если он был изменен)
                if file_meta.path in self.previous_state:
                    await self.vector_store.delete_by_file([file_meta.path])

                # Добавляем новые чанки
                if chunks:
                    await self.vector_store.add_chunks(chunks)

                self.progress_updated.emit(i + 1, total)

            # Актуальное состояние БЕЗ повторного sha256-скана всех файлов:
            # берём previous_state, вычитаем удалённые, добавляем изменённые.
            # Эквивалентно scan_project(path), но без I/O по всему проекту.
            new_state = dict(self.previous_state)
            for p in deleted:
                new_state.pop(p, None)
            for m in new_or_modified:
                new_state[m.path] = m

            self.state_ready.emit(new_state)
            self.indexing_summary.emit(paths, list(deleted))
            self.indexing_finished.emit()
        except Exception as e:
            self.indexing_error.emit(str(e))


class ProjectState:
    """Состояние проекта для инкрементальной индексации.

    Хранится per-project в `<project>/.ollidesk/project_state.json` — это
    критично: глобальный файл сбрасывался при каждом переключении проекта,
    и возврат к первому проекту давал полную переиндексацию всех файлов
    заново (см. `Add of existing embedding ID` в консоли ChromaDB).
    """

    STATE_DIRNAME = ".ollidesk"
    STATE_FILENAME = "project_state.json"

    @classmethod
    def _state_file(cls, project_path: Path) -> Path:
        return project_path / cls.STATE_DIRNAME / cls.STATE_FILENAME

    @classmethod
    def load(cls, project_path: Path) -> dict[str, FileMetadata]:
        """Загружает состояние проекта.

        Args:
            project_path: Корень проекта.

        Returns:
            dict: {relative_path: FileMetadata}
        """
        state_file = cls._state_file(project_path)
        if not state_file.exists():
            return {}
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка загрузки состояния проекта: {e}")
            return {}

        result: dict[str, FileMetadata] = {}
        for path_str, meta_dict in raw.items():
            abs_path = project_path / meta_dict["path"]
            meta_dict["absolute_path"] = str(abs_path)
            try:
                result[path_str] = FileMetadata.model_validate(meta_dict)
            except Exception as e:
                logger.warning(f"Ошибка валидации state для {path_str}: {e}")
        logger.debug(f"Состояние проекта загружено: {len(result)} файлов")
        return result

    @classmethod
    def save(cls, files: dict, project_path: Path) -> None:
        """Сохраняет состояние проекта.

        Args:
            files: {relative_path: FileMetadata}
            project_path: Корень проекта.
        """
        state_file = cls._state_file(project_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        serializable: dict[str, dict] = {}
        for path, meta in files.items():
            serializable[path] = {
                "path": meta.path,
                "sha256": meta.sha256,
                "mtime": meta.mtime,
                "size_bytes": meta.size_bytes,
                "extension": meta.extension,
                "absolute_path": str(meta.absolute_path),
            }
        state_file.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"Состояние проекта сохранено: {len(files)} файлов")

    @classmethod
    def clear(cls, project_path: Path) -> None:
        """Удаляет файл состояния (для полной переиндексации)."""
        state_file = cls._state_file(project_path)
        if state_file.exists():
            state_file.unlink()


class IndexingController(QObject):
    """Управляет фоновой индексацией и состоянием проекта.

    Все зависимости (`project_path`, `indexer`, `vector_store`) передаются
    через `configure()` — контроллер не хранит ссылку на `MainWindow`.
    """

    progress = Signal(int, int)
    finished = Signal()
    error = Signal(str)
    summary = Signal(list, list)  # added_paths, deleted_paths

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._indexing_thread: IndexingThread | None = None
        self._previous_state: dict[str, FileMetadata] = {}
        self._project_path: Path | None = None
        self._indexer: FileIndexer | None = None
        self._vector_store: VectorStore | None = None
        # NEW: накопитель state между сигналом `state_ready` и `finished`.
        # Заполняется в `_on_state_ready` (worker-поток → GUI-поток),
        # сохраняется в `_on_thread_finished` и сразу очищается.
        self._state_to_save: dict | None = None

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def previous_state(self) -> dict[str, FileMetadata]:
        """Снимок `{rel_path: FileMetadata}` предыдущей индексации."""
        return dict(self._previous_state)

    @property
    def is_running(self) -> bool:
        return self._indexing_thread is not None and self._indexing_thread.isRunning()

    def configure(
        self,
        *,
        project_path: Path,
        indexer: FileIndexer,
        vector_store: VectorStore,
    ) -> None:
        """Устанавливает зависимости и загружает предыдущее состояние.

        Вызывается из `MainWindow._do_open_project` /
        `_on_directory_attach_requested` после создания `VectorStore`.

        Args:
            project_path: Корень проекта.
            indexer: `FileIndexer` для текущего проекта.
            vector_store: `VectorStore` для текущего проекта.
        """
        self._project_path = project_path
        self._indexer = indexer
        self._vector_store = vector_store
        self._previous_state = ProjectState.load(project_path)
        self._state_to_save = None
        logger.info(
            f"IndexingController.configure: {project_path.name}, "
            f"previous_state={len(self._previous_state)} файлов"
        )

    def start(self) -> None:
        """Запускает индексацию (no-op, если уже бежит)."""
        if self._indexing_thread is not None and self._indexing_thread.isRunning():
            logger.warning("Индексация уже выполняется")
            return
        if self._project_path is None or self._indexer is None or self._vector_store is None:
            logger.warning("IndexingController.start без configure() — пропускаем")
            return

        self._state_to_save = None
        self._indexing_thread = IndexingThread(
            indexer=self._indexer,
            vector_store=self._vector_store,
            project_path=self._project_path,
            previous_state=self._previous_state,
        )
        # NEW: state_ready подключается ДО finished, чтобы _state_to_save
        # успел заполниться к моменту _on_thread_finished.
        self._indexing_thread.progress_updated.connect(self.progress)
        self._indexing_thread.state_ready.connect(self._on_state_ready)
        self._indexing_thread.indexing_summary.connect(self.summary)
        self._indexing_thread.indexing_finished.connect(self._on_thread_finished)
        self._indexing_thread.indexing_error.connect(self.error)
        self._indexing_thread.start()

    def reindex(self) -> None:
        """Полная переиндексация: сбрасывает state и запускает заново."""
        if self._project_path is None:
            return
        self._previous_state = {}
        ProjectState.clear(self._project_path)
        self.start()

    def stop(self) -> None:
        """Graceful stop потока (для `MainWindow.closeEvent`)."""
        if self._indexing_thread is not None and self._indexing_thread.isRunning():
            self._indexing_thread.quit()
            self._indexing_thread.wait(2000)

    # ------------------------------------------------------------------
    # Внутренние слоты
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_state_ready(self, state: dict) -> None:
        """Запоминает новое состояние до сигнала `finished`.

        Вызывается на GUI-потоке (Qt::AutoConnection из worker-потока
        через очередь событий). Само сохранение — в `_on_thread_finished`,
        чтобы гарантированно записать JSON один раз (не дважды при
        случайном двойном сигнале).
        """
        self._state_to_save = state

    def _on_thread_finished(self) -> None:
        """Сохраняет новое состояние после успешной индексации.

        Раньше здесь повторно вызывался `scan_project` на GUI-потоке через
        `new_event_loop().run_until_complete()`. Это фризило UI на сотнях
        файлов × sha256, причём полностью напрасно: `IndexingThread`
        уже знает новое состояние. Теперь сохранение — дешёвый JSON-dump.

        `MainWindow._on_index_finished` остаётся UI-слотом (скрыть прогресс,
        показать сообщение).
        """
        if self._project_path is None:
            self.finished.emit()
            return

        state = self._state_to_save
        self._state_to_save = None
        if state is not None:
            try:
                ProjectState.save(state, self._project_path)
                self._previous_state = state
            except Exception as e:
                logger.warning(f"Не удалось сохранить состояние проекта: {e}")

        self.finished.emit()