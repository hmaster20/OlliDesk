"""Контроллер дерева файлов проекта (план 2.4).

Содержит:
  * `QTreeView` с файловой моделью и `GitStatusDelegate`;
  * операции над деревом (new/rename/delete/copy/cut/paste/refresh),
    контекстное меню, keyboard shortcuts;
  * песочницу `_is_inside_project`.

Публичный API:
  * `tree` — `QTreeView` для вставки в layout;
  * `git_delegate` — для `refresh()` после записи файла;
  * `set_project_root(path | None)` — пересоздаёт модель и делегат;
  * `reload()` — пересоздаёт модель с сохранением root;
  * сигналы:
    - `file_open_requested(str)` — пользователь кликнул файл;
    - `file_renamed(str, str)` — переименование (для редактора);
    - `file_deleted(str)` — удаление (для редактора);
    - `branch_changed(str)` — текущая git-ветка/тег.

Не знает про `ChatPanel`, `SessionStore`, `VectorStore` — это задача
`MainWindow`. Контроллер не создаёт индексатор, не управляет сессиями.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QModelIndex, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QStandardItemModel
from PySide6.QtWidgets import (
    QFileSystemModel,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeView,
)

from ui.file_tree_delegate import GitStatusDelegate


class FileTreeController(QObject):
    """Дерево файлов проекта с git-подсветкой и файловыми операциями."""

    file_open_requested = Signal(str)
    file_renamed = Signal(str, str)
    file_deleted = Signal(str)
    branch_changed = Signal(str)
    # [9.8.3] Diff-запрос для редактора.
    file_diff_requested = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project_path: Path | None = None
        self._file_system_model: QFileSystemModel | None = None
        self._fs_clipboard: dict = {"mode": None, "paths": []}

        self._empty_model = QStandardItemModel()

        # Дерево
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.doubleClicked.connect(self._on_double_clicked)
        self.tree.clicked.connect(self._on_clicked)
        self.tree.setModel(self._empty_model)
        self.tree.setRootIndex(QModelIndex())

        # Git-делегат
        self._git_delegate = GitStatusDelegate(self.tree)
        self.tree.setItemDelegate(self._git_delegate)
        self._git_delegate.branch_changed.connect(self.branch_changed)

        self._install_shortcuts()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def git_delegate(self) -> GitStatusDelegate:
        """Делегат для `refresh()` после записи файла из редактора."""
        return self._git_delegate

    def set_project_root(self, path: Path | None) -> None:
        """Устанавливает корень проекта и пересоздаёт модель.

        Args:
            path: Корень проекта или ``None`` — дерево очищается.
        """
        self._project_path = path
        self._fs_clipboard = {"mode": None, "paths": []}

        if path is None:
            self._file_system_model = None
            self.tree.setModel(self._empty_model)
            self._git_delegate.set_repo_path(None)
            return

        model = QFileSystemModel()
        model.setRootPath(str(path))
        self._file_system_model = model
        self.tree.setModel(model)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIndex(model.index(str(path)))
        self.tree.hideColumn(1)
        self.tree.hideColumn(2)
        self.tree.hideColumn(3)

        self._git_delegate.set_view(self.tree)
        self._git_delegate.set_repo_path(str(path))

    def reload(self) -> None:
        """Пересоздаёт модель файлового дерева с сохранением root.

        ВАЖНО:
        * делегат `GitStatusDelegate` хранит прямую ссылку на view — после
          подмены модели синхронизируем view и просим делегат заново
          разобрать `git status`;
        * `setHeaderHidden(True)` ставим ПОСЛЕ `setModel` — некоторые сборки
          PySide6 повторно показывают `QHeaderView` при смене модели.
        """
        if self._project_path is None:
            return
        self.set_project_root(self._project_path)

    # ------------------------------------------------------------------
    # Клики по дереву
    # ------------------------------------------------------------------

    @Slot(QModelIndex)
    def _on_clicked(self, index: QModelIndex) -> None:
        if self._file_system_model is None:
            return
        file_path = self._file_system_model.filePath(index)
        if Path(file_path).is_file():
            self.file_open_requested.emit(file_path)

    @Slot(QModelIndex)
    def _on_double_clicked(self, index: QModelIndex) -> None:
        if self._file_system_model is None:
            return
        path = self._file_system_model.filePath(index)
        if Path(path).is_file():
            self.file_open_requested.emit(path)

    # ------------------------------------------------------------------
    # Утилиты путей
    # ------------------------------------------------------------------

    def _current_tree_path(self) -> Path | None:
        if self._file_system_model is None:
            return None
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return Path(self._file_system_model.filePath(index))

    def _target_dir_for_new(self) -> Path | None:
        """Каталог для нового файла/папки: выделенный, родитель файла, корень."""
        cur = self._current_tree_path()
        if cur and cur.is_dir():
            return cur
        if cur and cur.is_file():
            return cur.parent
        return self._project_path

    def _is_inside_project(self, path: Path) -> bool:
        if self._project_path is None:
            return False
        try:
            path.resolve().relative_to(self._project_path.resolve())
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Новый файл / папка
    # ------------------------------------------------------------------

    def _tree_new_file(self) -> None:
        target_dir = self._target_dir_for_new()
        if target_dir is None:
            return
        name, ok = QInputDialog.getText(self.tree, "Новый файл", f"Имя файла в «{target_dir.name}»:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        new_path = target_dir / name
        if new_path.exists():
            QMessageBox.warning(self.tree, "Ошибка", f"«{name}» уже существует")
            return
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text("", encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self.tree, "Ошибка", f"Не удалось создать файл:\n{e}")
            return
        self._git_delegate.refresh()
        QTimer.singleShot(200, self._open_new_path, str(new_path))

    def _open_new_path(self, path: str) -> None:
        self.reload()
        self.file_open_requested.emit(path)

    def _tree_new_folder(self) -> None:
        target_dir = self._target_dir_for_new()
        if target_dir is None:
            return
        name, ok = QInputDialog.getText(self.tree, "Новая папка", f"Имя папки в «{target_dir.name}»:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        new_path = target_dir / name
        if new_path.exists():
            QMessageBox.warning(self.tree, "Ошибка", f"«{name}» уже существует")
            return
        try:
            new_path.mkdir(parents=True, exist_ok=False)
        except OSError as e:
            QMessageBox.critical(self.tree, "Ошибка", f"Не удалось создать папку:\n{e}")
            return
        self._git_delegate.refresh()
        QTimer.singleShot(200, self.reload)

    # ------------------------------------------------------------------
    # Переименование
    # ------------------------------------------------------------------

    def _tree_rename(self) -> None:
        cur = self._current_tree_path()
        if cur is None or not self._is_inside_project(cur):
            return
        old_name = cur.name
        new_name, ok = QInputDialog.getText(self.tree, "Переименовать", "Новое имя:", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        new_path = cur.parent / new_name
        if new_path.exists():
            QMessageBox.warning(self.tree, "Ошибка", f"«{new_name}» уже существует")
            return
        try:
            cur.rename(new_path)
        except OSError as e:
            QMessageBox.critical(self.tree, "Ошибка", f"Не удалось переименовать:\n{e}")
            return

        # Обновить вкладку редактора, если файл открыт.
        if cur.is_file():
            self.file_renamed.emit(str(cur), str(new_path))

        self._git_delegate.refresh()
        QTimer.singleShot(200, self.reload)

    # ------------------------------------------------------------------
    # Удаление
    # ------------------------------------------------------------------

    def _tree_delete(self) -> None:
        cur = self._current_tree_path()
        if cur is None or not self._is_inside_project(cur):
            return
        what = "файл" if cur.is_file() else "папку"
        reply = QMessageBox.question(
            self.tree,
            "Удаление",
            f"Удалить {what} «{cur.name}»?\n"
            + ("Содержимое будет удалено безвозвратно." if cur.is_dir() else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if cur.is_dir():
                shutil.rmtree(cur)
            else:
                cur.unlink()
        except OSError as e:
            QMessageBox.critical(self.tree, "Ошибка", f"Не удалось удалить:\n{e}")
            return

        if cur.is_file():
            self.file_deleted.emit(str(cur))

        self._git_delegate.refresh()
        QTimer.singleShot(200, self.reload)

    # ------------------------------------------------------------------
    # Copy / Cut / Paste
    # ------------------------------------------------------------------

    def _tree_copy(self) -> None:
        cur = self._current_tree_path()
        if cur is None or not self._is_inside_project(cur):
            return
        self._fs_clipboard = {"mode": "copy", "paths": [cur]}

    def _tree_cut(self) -> None:
        cur = self._current_tree_path()
        if cur is None or not self._is_inside_project(cur):
            return
        self._fs_clipboard = {"mode": "cut", "paths": [cur]}

    def _tree_paste(self) -> None:
        mode = self._fs_clipboard.get("mode")
        paths: list[Path] = self._fs_clipboard.get("paths") or []
        if not mode or not paths:
            return
        target_dir = self._target_dir_for_new()
        if target_dir is None or not target_dir.is_dir():
            return
        for src in paths:
            if not src.exists():
                continue
            try:
                src_resolved = src.resolve()
                target_resolved = target_dir.resolve()
                # Запретить вставлять папку саму в себя.
                try:
                    target_resolved.relative_to(src_resolved)
                    QMessageBox.warning(
                        self.tree,
                        "Ошибка",
                        f"Нельзя вставить «{src.name}» внутрь себя.",
                    )
                    continue
                except ValueError:
                    pass

                dst = target_dir / src.name
                if dst.exists():
                    QMessageBox.warning(
                        self.tree,
                        "Ошибка",
                        f"«{dst.name}» уже существует в целевой папке.",
                    )
                    continue

                if mode == "cut":
                    shutil.move(str(src), str(dst))
                    if src.is_file():
                        self.file_renamed.emit(str(src), str(dst))
                else:
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
            except OSError as e:
                QMessageBox.critical(self.tree, "Ошибка", f"Не удалось вставить «{src.name}»:\n{e}")

        if mode == "cut":
            self._fs_clipboard = {"mode": None, "paths": []}

        self._git_delegate.refresh()
        QTimer.singleShot(200, self.reload)

    def _tree_refresh(self) -> None:
        if self._project_path is None:
            return
        self.reload()

    # ------------------------------------------------------------------
    # Проводник
    # ------------------------------------------------------------------

    def _reveal_in_explorer(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                if path.is_file():
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                if path.is_file():
                    subprocess.Popen(["open", "-R", str(path)])
                else:
                    subprocess.Popen(["open", str(path)])
            else:
                target = path if path.is_dir() else path.parent
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as e:
            logger.warning(f"Не удалось открыть проводник: {e}")

    # ------------------------------------------------------------------
    # Контекстное меню
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_context_menu(self, pos) -> None:
        if self._file_system_model is None:
            return
        index = self.tree.indexAt(pos)
        if index.isValid():
            self.tree.setCurrentIndex(index)
            target_path = Path(self._file_system_model.filePath(index))
        else:
            target_path = self._project_path

        if target_path is None:
            return

        is_file = target_path.is_file()
        is_dir = target_path.is_dir()
        menu = QMenu(self.tree)

        if is_file:
            act_open = menu.addAction("📂 Открыть")
            act_open.triggered.connect(lambda: self.file_open_requested.emit(str(target_path)))
            act_reveal = menu.addAction("📁 Открыть расположение")
            act_reveal.triggered.connect(lambda: self._reveal_in_explorer(target_path))

            # [9.8.3] Diff с HEAD.
            act_diff = menu.addAction("🔍 Показать diff с HEAD")
            act_diff.triggered.connect(
                lambda _checked=False, p=str(target_path): self.file_diff_requested.emit(p)
            )
            menu.addSeparator()

        act_new_file = menu.addAction("📄 Новый файл")
        act_new_file.setShortcut("Ctrl+N")
        act_new_file.triggered.connect(self._tree_new_file)

        act_new_folder = menu.addAction("📁 Новая папка")
        act_new_folder.setShortcut("Ctrl+Shift+N")
        act_new_folder.triggered.connect(self._tree_new_folder)

        menu.addSeparator()

        act_copy = menu.addAction("📋 Копировать")
        act_copy.setShortcut("Ctrl+C")
        act_copy.setEnabled(is_file or is_dir)
        act_copy.triggered.connect(self._tree_copy)

        act_cut = menu.addAction("✂ Вырезать")
        act_cut.setShortcut("Ctrl+X")
        act_cut.setEnabled(is_file or is_dir)
        act_cut.triggered.connect(self._tree_cut)

        act_paste = menu.addAction("📥 Вставить")
        act_paste.setShortcut("Ctrl+V")
        act_paste.setEnabled(bool(self._fs_clipboard["paths"]) and is_dir)
        act_paste.triggered.connect(self._tree_paste)

        if is_file or is_dir:
            menu.addSeparator()
            act_rename = menu.addAction("✏ Переименовать")
            act_rename.setShortcut("F2")
            act_rename.triggered.connect(self._tree_rename)
            act_delete = menu.addAction("🗑 Удалить")
            act_delete.setShortcut("Delete")
            act_delete.triggered.connect(self._tree_delete)

        menu.addSeparator()

        act_refresh = menu.addAction("🔄 Обновить")
        act_refresh.setShortcut("Ctrl+R")
        act_refresh.triggered.connect(self._tree_refresh)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Shortcuts (F2/Del/Ctrl+C/X/V/N/Ctrl+R — только в фокусе дерева)
    # ------------------------------------------------------------------

    def _install_shortcuts(self) -> None:
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        actions = [
            ("F2", self._tree_rename),
            ("Delete", self._tree_delete),
            ("Ctrl+C", self._tree_copy),
            ("Ctrl+X", self._tree_cut),
            ("Ctrl+V", self._tree_paste),
            ("Ctrl+N", self._tree_new_file),
            ("Ctrl+Shift+N", self._tree_new_folder),
            ("Ctrl+R", self._tree_refresh),
        ]
        for seq, slot in actions:
            act = QAction(self.tree)
            act.setShortcut(QKeySequence(seq))
            act.setShortcutContext(ctx)
            act.triggered.connect(slot)
            self.tree.addAction(act)