"""Кастомный делегат для подсветки Git-статуса в дереве файлов.

Изменения относительно предыдущей версии:

* Исправлено главное: относительный путь файла теперь берётся через
  `QFileSystemModel.filePath(index)`, а не через обход дерева вверх.
  Раньше обход доходил до корня диска (`D:`) и возвращал путь
  «Repository/Hmaster20/local/OlliDesk/main.py», который никогда не
  совпадал с ключами `git status --porcelain` («main.py»).
* `_run_git` больше не передаёт `CREATE_NO_WINDOW` на не-Windows —
  на Linux/macOS этого атрибута нет.
* Корень репозитория определяется через `git rev-parse --show-toplevel`,
  поэтому подсветка работает и при открытии подкаталога репозитория.
* Ветка умеет показывать тег (если HEAD ровно на теге).
* **[2.3]** `git status --porcelain` выполняется в `QThreadPool`, а не на
  GUI-потоке. Раньше `_do_refresh` синхронно вызывал `subprocess.run` с
  `timeout=5.0` — на большом репозитории это блокировало UI на секунды.
  Теперь worker считает статус в фоне, результат приходит через сигнал
  `_status_ready` и применяется на GUI-потоке в `_apply_status`.
  Generation-счётчик защищает от применения устаревшего результата, если
  `set_repo_path` успел переключить репозиторий, пока worker ещё бежал.
"""

from __future__ import annotations

import os
import subprocess
import sys

from loguru import logger
from PySide6.QtCore import QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
)
import contextlib

# Цвета по одному символу XY из `git status --porcelain`.
# X — индекс, Y — рабочее дерево.
_GIT_COLORS: dict[str, QColor] = {
    "M": QColor("#e5c07b"),  # modified — жёлтый
    "A": QColor("#98c379"),  # added — зелёный
    "D": QColor("#e06c75"),  # deleted — красный
    "R": QColor("#61afef"),  # renamed — синий
    "C": QColor("#56b6c2"),  # copied — бирюзовый
    "U": QColor("#d19a66"),  # unmerged — оранжевый
    "?": QColor("#98c379"),  # untracked — зелёный
    "!": QColor("#5c6370"),  # ignored — серый
}

# Отображаемая буква для каждого XY-статуса (для показа рядом с именем).
_GIT_LETTERS: dict[str, str] = {
    "M": "M",
    "A": "A",
    "D": "D",
    "R": "R",
    "C": "C",
    "U": "U",
    "?": "U",  # untracked
    "!": "I",  # ignored
}


class _GitStatusWorker(QRunnable):
    """Считает `git status` в пуле потоков.

    Не трогает состояние делегата напрямую: собирает ``status_map`` и
    ``branch`` локально и отправляет результат через
    ``delegate._status_ready``. Перенос ``subprocess.run`` с GUI-потока —
    исправление 2.3: раньше `_do_refresh` блокировал UI на всё время
    ``git status`` (``timeout=5.0``), а на большом репозитории это
    заметно даже на SSD.
    """

    def __init__(self, delegate: GitStatusDelegate, generation: int) -> None:
        super().__init__()
        self._d = delegate
        self._gen = generation

    def run(self) -> None:
        try:
            status_map, branch = self._d._compute_status_sync()
            self._d._status_ready.emit(status_map, branch, self._gen)
        except Exception as e:
            logger.debug(f"GitStatusWorker failed: {e}")
            with contextlib.suppress(Exception):
                self._d._status_ready.emit({}, "", self._gen)


class GitStatusDelegate(QStyledItemDelegate):
    """Делегат, окрашивающий имена файлов по Git-статусу."""

    branch_changed = Signal(str)
    # (status_map, branch, generation) — generation позволяет отбрасывать
    # устаревшие результаты worker'ов, если репозиторий успел смениться.
    _status_ready = Signal(object, str, int)

    def __init__(self, parent: QTreeView | None = None) -> None:
        super().__init__(parent)
        # Прямая ссылка на виджет — не полагаемся на self.parent().
        self._view: QTreeView | None = parent
        self._repo_path: str | None = None
        # Реальный корень репозитория (может отличаться от _repo_path,
        # если открыт подкаталог репозитория). Заполняется в set_repo_path.
        self._repo_root: str | None = None
        self._status_map: dict[str, str] = {}
        self._branch: str = ""
        # 2.3: git status выполняется в фоне; _running не даёт накапливать
        # однотипные проверки. _generation сбрасывает устаревшие результаты.
        self._running: bool = False
        self._generation: int = 0
        self._pool = QThreadPool.globalInstance()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._do_refresh)

        self._status_ready.connect(self._apply_status)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_view(self, view: QTreeView) -> None:
        """Позволяет переопределить виджет-владелец (после reload дерева)."""
        self._view = view

    def set_repo_path(self, path: str | None) -> None:
        """Назначает корень проекта и пытается определить git-репозиторий.

        Args:
            path: Путь к открытому проекту или ``None`` для отключения.
        """
        # Инвалидируем все in-flight worker'ы: их результат относится к
        # старому репозиторию и не должен примениться к новому.
        self._generation += 1
        self._running = False

        self._repo_path = path
        self._repo_root = None
        self._status_map.clear()
        self._branch = ""

        if not path:
            self._poll_timer.stop()
            self.branch_changed.emit("")
            self.refresh()
            return

        # Определяем реальный корень репозитория: git status --porcelain
        # возвращает пути относительно него, а не относительно произвольной
        # cwd. Если path — не git-репозиторий, _repo_root останется None,
        # и подсветка просто не будет работать (это нормально).
        out = self._run_git("rev-parse", "--show-toplevel")
        if out:
            root = out.strip()
            if root:
                self._repo_root = root
                logger.info(f"GitStatusDelegate: репозиторий найден (repo_root='{root}')")
            else:
                logger.info(f"GitStatusDelegate: {path} не является git-репозиторием")
        else:
            logger.info(
                f"GitStatusDelegate: {path} не является git-репозиторием — подсветка статусов отключена"
            )

        self._poll_timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Отложенное обновление (debounce 300 мс)."""
        self._refresh_timer.start(300)

    @property
    def current_branch(self) -> str:
        return self._branch

    # ------------------------------------------------------------------
    # Git (общие утилиты; могут вызываться как с GUI, так и с worker-потока)
    # ------------------------------------------------------------------

    def _run_git(
        self,
        *args: str,
        cwd: str | None = None,
        timeout: float = 5.0,
    ) -> str | None:
        """Запускает git с защитой от не-UTF8 и без окна на Windows.

        Если передан явный ``cwd`` — используется он. Иначе берётся
        ``self._repo_path``. Worker всегда передаёт явный ``cwd`` — чтобы
        не зависеть от гонки с ``set_repo_path`` на GUI-потоке.
        """
        target = cwd or self._repo_path
        if not target:
            return None
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=target,
                timeout=timeout,
                creationflags=flags,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"git {' '.join(args)} failed: {e}")
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def _read_branch_at(self, cwd: str) -> str:
        """Текущая ветка / тег / short-hash для явного ``cwd``.

        Приоритет:
          1. Если HEAD стоит ровно на теге — показываем тег (`v0.1.2`).
          2. Обычная ветка — её имя.
          3. Detached HEAD — `describe --tags` (ближайший тег + hash),
             иначе короткий hash.
        """
        tag = self._run_git("describe", "--tags", "--exact-match", "HEAD", cwd=cwd)
        if tag and tag.strip():
            return tag.strip()

        ref = self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        if ref:
            branch = ref.strip()
            if branch and branch != "HEAD":
                return branch

        described = self._run_git("describe", "--tags", "--always", "--abbrev=7", cwd=cwd)
        if described and described.strip():
            return described.strip()

        short = self._run_git("rev-parse", "--short", "HEAD", cwd=cwd)
        return f"({short.strip()})" if short else ""

    # ------------------------------------------------------------------
    # Фоновое обновление (2.3)
    # ------------------------------------------------------------------

    def _do_refresh(self) -> None:
        """Запускает git status в пуле потоков (debounce уже сработал).

        Раньше здесь синхронно вызывались `_read_branch` + `_run_git("status")`
        с таймаутом 5 с — на GUI-потоке. Теперь:
          * если репозиторий не задан — только перерисовка;
          * если уже бежит предыдущий worker — пропускаем (не плодим
            одинаковые subprocess'ы);
          * иначе — `_GitStatusWorker` в `QThreadPool.globalInstance()`,
            результат приходит в `_apply_status` через сигнал.
        """
        if not self._repo_path or not self._repo_root:
            self._update_viewport()
            return
        if self._running:
            # Предыдущий git status ещё не завершился — не плодим новые.
            return
        self._running = True
        generation = self._generation
        self._pool.start(_GitStatusWorker(self, generation))

    def _compute_status_sync(self) -> tuple[dict[str, str], str]:
        """Синхронная часть — выполняется в worker-потоке.

        Возвращает ``(status_map, branch)``. ``status_map`` — словарь
        ``{relative_path: xy}`` (как из `git status --porcelain`).
        Чтение ``self._repo_root`` снапшотится в локальную переменную,
        чтобы избежать гонки с ``set_repo_path`` на GUI-потоке.
        """
        repo_root = self._repo_root
        if not repo_root:
            return {}, ""

        branch = ""
        try:
            branch = self._read_branch_at(repo_root)
        except Exception:
            logger.debug("GitStatusDelegate: чтение ветки упало", exc_info=True)

        status_map: dict[str, str] = {}
        out = self._run_git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            cwd=repo_root,
        )
        if out is not None:
            for raw_line in out.splitlines():
                try:
                    parsed = self._parse_porcelain_line(raw_line)
                except Exception:
                    logger.debug(
                        f"GitStatusDelegate: не разобрал строку: {raw_line!r}",
                    )
                    continue
                if parsed is not None:
                    path, xy = parsed
                    status_map[path] = xy
        return status_map, branch

    @Slot(object, str, int)
    def _apply_status(self, status_map: dict, branch: str, generation: int) -> None:
        """Применяет результат worker'а на GUI-потоке.

        Если ``generation`` не совпадает с текущим — результат относится
        к другому репозиторию и отбрасывается (см. ``set_repo_path``).
        """
        self._running = False
        if generation != self._generation:
            # Stale: репозиторий сменился, пока worker считал статус.
            return

        self._status_map = status_map

        if branch != self._branch:
            self._branch = branch
            try:
                self.branch_changed.emit(branch)
            except Exception:
                logger.debug(
                    "GitStatusDelegate: branch_changed handler упал",
                    exc_info=True,
                )

        self._update_viewport()

    def _update_viewport(self) -> None:
        """Безопасно перерисовывает viewport дерева.

        ВАЖНО: у `QAbstractItemView` `viewport()` — это **метод**, а не
        атрибут. Раньше `getattr(parent, "viewport", None)` возвращал bound
        method, и `viewport.update()` падал с AttributeError.
        """
        view = self._view
        if view is None:
            return
        try:
            view.viewport().update()
        except Exception:
            logger.debug(
                "GitStatusDelegate: не удалось обновить viewport",
                exc_info=True,
            )

    @staticmethod
    def _parse_porcelain_line(line: str) -> tuple[str, str] | None:
        """Разбирает одну строку `git status --porcelain` → ``(path, xy)``.

        Возвращает ``None``, если строка слишком короткая. Невалидные
        строки отбрасываются с debug-логом в ``_compute_status_sync``.
        """
        if len(line) < 4:
            return None
        xy = line[:2]
        path = line[3:]

        # Rename / copy: "R  old -> new" — берём новый путь.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]

        # Экранированные пути вида `"caf\303\251.txt"`.
        if path.startswith('"') and path.endswith('"'):
            inner = path[1:-1]
            try:
                path = inner.encode("latin-1", "backslashreplace").decode("unicode_escape")
            except Exception:
                path = inner

        return path.replace("\\", "/"), xy

    # ------------------------------------------------------------------
    # Относительный путь
    # ------------------------------------------------------------------

    def _relative_path(self, index) -> str | None:
        """Путь относительно корня репозитория (со слэшами) или ``None``.

        Использует `QFileSystemModel.filePath(index)` — он даёт абсолютный
        путь для любого валидного индекса. Раньше путь собирался обходом
        дерева вверх, и обход доходил до корня диска (`D:`), из-за чего
        путь никогда не совпадал с ключами `git status` (пути относительно
        корня репозитория).
        """
        view = self._view
        if view is None:
            return None
        model = view.model()
        if model is None:
            return None

        file_path_fn = getattr(model, "filePath", None)
        if not callable(file_path_fn):
            return None

        try:
            abs_str = str(file_path_fn(index))
        except Exception:
            return None
        if not abs_str:
            return None

        repo_root = self._repo_root or self._repo_path
        if not repo_root:
            return None

        # Нормализуем оба пути: на Windows сравнение должно быть
        # регистронезависимым, а разделители — одинаковыми.
        abs_norm = os.path.normcase(os.path.normpath(abs_str))
        repo_norm = os.path.normcase(os.path.normpath(repo_root))

        if abs_norm == repo_norm:
            return ""

        sep = os.sep
        if not abs_norm.startswith(repo_norm + sep):
            return None

        rel = abs_str[len(repo_root) :].lstrip("\\/")
        return rel.replace("\\", "/")

    # ------------------------------------------------------------------
    # QStyledItemDelegate
    # ------------------------------------------------------------------

    def _status_for(self, file_path: str) -> str | None:
        """Статус для относительного пути (или любого вложенного, если это каталог)."""
        if not self._status_map:
            return None
        xy = self._status_map.get(file_path)
        if xy is not None:
            return xy
        # Для каталогов — берём статус любого вложенного файла.
        prefix = file_path + "/"
        for rel_path, sym in self._status_map.items():
            if rel_path.startswith(prefix):
                return sym
        return None

    def initStyleOption(  # noqa: N802 (Qt override)
        self, option: QStyleOptionViewItem, index
    ) -> None:
        try:
            super().initStyleOption(option, index)
        except Exception:
            return

        try:
            if not self._repo_root or not self._status_map:
                return

            rel_path = self._relative_path(index)
            if rel_path is None or rel_path == "":
                return

            xy = self._status_for(rel_path)
            if not xy:
                return

            x, y = xy[0], xy[1]
            letter: str | None = None
            color: QColor | None = None

            if xy == "??":
                letter = _GIT_LETTERS["?"]
                color = _GIT_COLORS["?"]
            elif xy == "!!":
                letter = _GIT_LETTERS["!"]
                color = _GIT_COLORS["!"]
            elif y != " " and y in _GIT_COLORS:
                letter = _GIT_LETTERS.get(y, y)
                color = _GIT_COLORS[y]
            elif x != " " and x in _GIT_COLORS:
                letter = _GIT_LETTERS.get(x, x)
                color = _GIT_COLORS[x]
            else:
                return

            option.palette.setColor(QPalette.ColorRole.Text, color)
            option.palette.setColor(QPalette.ColorRole.HighlightedText, color)

            # Добавляем букву рядом с именем, как в VS Code.
            if letter and option.text:
                option.text = f"{option.text}  {letter}"
        except Exception:
            logger.debug("GitStatusDelegate.initStyleOption failed", exc_info=True)
