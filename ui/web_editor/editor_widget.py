"""Виджет редактора на базе QWebEngineView с несколькими вкладками."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTabBar,
    QVBoxLayout,
    QWidget,
    QMenu,
)
import subprocess
import sys

from PySide6.QtGui import QGuiApplication
from core.utils import get_resource_path


class PythonBridge(QObject):
    """Мост Python ↔ JS: чтение/запись + сигнал dirty."""

    file_content_ready = Signal(str, str)
    file_write_result = Signal(str, bool)
    file_dirty_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_reader = None
        self._file_writer = None

    def set_file_reader(self, reader):
        self._file_reader = reader

    def set_file_writer(self, writer):
        self._file_writer = writer

    @Slot(str, str)
    def read_file(self, request_id, path):
        logger.debug(f"PythonBridge.read_file: {path} (id={request_id})")
        try:
            content = self._file_reader(path) if self._file_reader else ""
            self.file_content_ready.emit(request_id, content)
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {e}")
            self.file_content_ready.emit(request_id, "")

    @Slot(str, str, str)
    def write_file(self, request_id, path, content):
        logger.debug(f"PythonBridge.write_file: {path} (id={request_id})")
        try:
            success = bool(self._file_writer(path, content)) if self._file_writer else False
            self.file_write_result.emit(request_id, success)
        except Exception as e:
            logger.error(f"Ошибка записи файла: {e}")
            self.file_write_result.emit(request_id, False)

    @Slot(str, bool)
    def set_dirty(self, file_path, is_dirty):
        """JS сообщает, что файл стал «грязным» или сохранился."""
        self.file_dirty_changed.emit(file_path, bool(is_dirty))


class EditorWidget(QWidget):
    """Редактор Monaco с собственным таб-баром."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_file: str | None = None
        self._open_files: list[str] = []
        self._dirty: dict[str, bool] = {}

        self._setup_ui()
        self._setup_bridge()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_bar = QTabBar()
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setStyleSheet(
            "QTabBar { background: #2d2d2d; }"
            "QTabBar::tab { background: #2d2d2d; color: #999;"
            " padding: 6px 14px; border: none;"
            " border-right: 1px solid #1e1e1e; }"
            "QTabBar::tab:selected { background: #1e1e1e; color: #d4d4d4;"
            " border-bottom: 2px solid #4e9a06; }"
            "QTabBar::tab:hover { background: #333; }"
        )
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addWidget(self.tab_bar)
        tab_row.addStretch()
        layout.addLayout(tab_row)

        self.web_view: QWebEngineView | QLabel
        try:
            self.web_view = QWebEngineView()
        except Exception as e:
            logger.error(f"Не удалось создать QWebEngineView: {e}")
            fallback = QLabel("WebEngine недоступен")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color: #888; font-size: 16px; padding: 20px;")
            self.web_view = fallback

        layout.addWidget(self.web_view, stretch=1)

        if isinstance(self.web_view, QWebEngineView):
            html_path = get_resource_path("ui/web_editor/editor.html")
            if not html_path.exists():
                logger.error(f"editor.html не найден: {html_path}")
            self.web_view.setUrl(QUrl.fromLocalFile(str(html_path)))

    def _setup_bridge(self):
        if not isinstance(self.web_view, QWebEngineView):
            return
        self.bridge = PythonBridge(self)
        channel = QWebChannel(self.web_view.page())
        channel.registerObject("pythonBridge", self.bridge)
        self.web_view.page().setWebChannel(channel)

        self.bridge.file_content_ready.connect(self._on_file_content_ready)
        self.bridge.file_write_result.connect(self._on_file_write_result)
        self.bridge.file_dirty_changed.connect(self._on_dirty_changed)

    # ------------------------------------------------------------------
    # Открытие / переключение / закрытие вкладок
    # ------------------------------------------------------------------

    def open_file(self, file_path: str, line: int = 0) -> None:
        """Открывает файл: создаёт вкладку, если её ещё нет."""
        file_path = str(Path(file_path).resolve())

        if file_path not in self._open_files:
            self._open_files.append(file_path)
            idx = self.tab_bar.addTab(self._tab_title(file_path))
            self.tab_bar.setTabData(idx, file_path)
            self._dirty[file_path] = False

        idx = self._open_files.index(file_path)
        if self.tab_bar.currentIndex() != idx:
            # Переключение вызовет _on_tab_changed → _send_open.
            self.tab_bar.setCurrentIndex(idx)
        else:
            # Уже выбрана — просто переоткрыть (например, с указанной строкой).
            self._send_open(file_path, line)

        self.current_file = file_path
        logger.info(
            f"Открыт файл: {file_path}" + (f" (строка {line})" if line else "")
        )

    def _send_open(self, file_path: str, line: int = 0) -> None:
        if not isinstance(self.web_view, QWebEngineView):
            return
        escaped = json.dumps(file_path)
        self.web_view.page().runJavaScript(f"openFile({escaped}, {int(line)})")

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._open_files):
            return
        file_path = self._open_files[index]
        self.current_file = file_path
        self._send_open(file_path, 0)

    @Slot(int)
    def _on_tab_close_requested(self, index: int) -> None:
        if index < 0 or index >= len(self._open_files):
            return
        file_path = self._open_files[index]

        if self._dirty.get(file_path, False):
            reply = QMessageBox.question(
                self,
                "Несохранённые изменения",
                f"Файл «{Path(file_path).name}» содержит несохранённые изменения.\n"
                "Закрыть без сохранения?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if isinstance(self.web_view, QWebEngineView):
            escaped = json.dumps(file_path)
            self.web_view.page().runJavaScript(f"closeFile({escaped})")

        self.tab_bar.removeTab(index)
        self._open_files.pop(index)
        self._dirty.pop(file_path, None)

    def _tab_title(self, file_path: str) -> str:
        name = Path(file_path).name
        return f"● {name}" if self._dirty.get(file_path) else name

    @Slot(str, bool)
    def _on_dirty_changed(self, file_path: str, is_dirty: bool) -> None:
        if file_path not in self._open_files:
            return
        self._dirty[file_path] = is_dirty
        idx = self._open_files.index(file_path)
        self.tab_bar.setTabText(idx, self._tab_title(file_path))

    # ------------------------------------------------------------------
    # Контекстное меню вкладок (Notepad++-стиль)
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_tab_context_menu(self, pos) -> None:
        """Показывает меню для вкладки, по которой кликнули правой кнопкой."""
        clicked_index = self.tab_bar.tabAt(pos)
        if clicked_index < 0:
            # Правый клик по пустому месту — только «Закрыть все».
            menu = QMenu(self)
            act_close_all = menu.addAction("Закрыть все вкладки")
            act_close_all.setEnabled(self.tab_bar.count() > 0)
            if menu.exec(self.tab_bar.mapToGlobal(pos)) is act_close_all:
                self._close_all_tabs_confirm()
            return

        # Синхронизируем текущую вкладку с той, по которой кликнули.
        if clicked_index != self.tab_bar.currentIndex():
            self.tab_bar.setCurrentIndex(clicked_index)

        menu = QMenu(self)

        act_save = menu.addAction("💾 Сохранить")
        act_save.setShortcut("Ctrl+S")
        act_save.setEnabled(self._dirty.get(self.current_file or "", False))

        menu.addSeparator()

        act_close = menu.addAction("✕ Закрыть")
        act_close.setShortcut("Ctrl+W")

        act_close_left = menu.addAction("Закрыть все слева")
        act_close_left.setEnabled(clicked_index > 0)

        act_close_right = menu.addAction("Закрыть все справа")
        act_close_right.setEnabled(clicked_index < self.tab_bar.count() - 1)

        act_close_others = menu.addAction("Закрыть остальные")
        act_close_others.setEnabled(self.tab_bar.count() > 1)

        act_close_all = menu.addAction("Закрыть все")
        act_close_all.setShortcut("Ctrl+Shift+W")

        menu.addSeparator()

        act_copy_path = menu.addAction("📋 Скопировать путь")
        act_reveal = menu.addAction("📂 Открыть расположение файла")

        chosen = menu.exec(self.tab_bar.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_save:
            self._save_current_tab()
        elif chosen is act_close:
            self._on_tab_close_requested(clicked_index)
        elif chosen is act_close_left:
            self._close_tabs_to_left(clicked_index)
        elif chosen is act_close_right:
            self._close_tabs_to_right(clicked_index)
        elif chosen is act_close_others:
            self._close_tabs_others(clicked_index)
        elif chosen is act_close_all:
            self._close_all_tabs_confirm()
        elif chosen is act_copy_path:
            self._copy_current_path()
        elif chosen is act_reveal:
            self._reveal_current_in_explorer()

    def _save_current_tab(self) -> None:
        if not isinstance(self.web_view, QWebEngineView):
            return
        self.web_view.page().runJavaScript("saveFile()")

    def _copy_current_path(self) -> None:
        if not self.current_file:
            return
        QGuiApplication.clipboard().setText(self.current_file)

    def _reveal_current_in_explorer(self) -> None:
        """Открывает расположение файла в системном проводнике."""
        if not self.current_file:
            return
        path = Path(self.current_file)
        if not path.exists():
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as e:
            logger.warning(f"Не удалось открыть проводник: {e}")

    def _close_tabs_to_left(self, upto_index: int) -> None:
        """Закрывает вкладки с 0 по `upto_index - 1` (не трогая саму вкладку)."""
        for i in range(upto_index - 1, -1, -1):
            self._close_tab_no_prompt(i)

    def _close_tabs_to_right(self, from_index: int) -> None:
        """Закрывает вкладки после `from_index` (не трогая саму вкладку)."""
        for i in range(self.tab_bar.count() - 1, from_index, -1):
            self._close_tab_no_prompt(i)

    def _close_tabs_others(self, keep_index: int) -> None:
        """Закрывает все вкладки, кроме `keep_index`."""
        for i in range(self.tab_bar.count() - 1, -1, -1):
            if i != keep_index:
                self._close_tab_no_prompt(i)

    def _close_all_tabs_confirm(self) -> None:
        dirty = [p for p, d in self._dirty.items() if d]
        if dirty:
            reply = QMessageBox.question(
                self,
                "Несохранённые изменения",
                f"{len(dirty)} файл(ов) содержат несохранённые изменения.\n"
                "Закрыть всё без сохранения?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.close_all_tabs()

    def _close_tab_no_prompt(self, index: int) -> None:
        """Закрывает вкладку без диалога (для «закрыть слева/справа/остальные»)."""
        if index < 0 or index >= len(self._open_files):
            return
        file_path = self._open_files[index]
        if isinstance(self.web_view, QWebEngineView):
            escaped = json.dumps(file_path)
            self.web_view.page().runJavaScript(f"closeFile({escaped})")
        self.tab_bar.removeTab(index)
        self._open_files.pop(index)
        self._dirty.pop(file_path, None)

    # ------------------------------------------------------------------
    # Хуки для файловых операций из дерева проекта
    # ------------------------------------------------------------------

    def rename_open_file(self, old_path: str, new_path: str) -> None:
        """Вызывается из дерева при переименовании: обновляет вкладку и JS."""
        old = str(Path(old_path).resolve())
        new = str(Path(new_path).resolve())
        if old not in self._open_files:
            return
        idx = self._open_files.index(old)
        self._open_files[idx] = new
        self.tab_bar.setTabData(idx, new)
        self.tab_bar.setTabText(idx, self._tab_title(new))
        self._dirty[new] = self._dirty.pop(old, False)
        if self.current_file == old:
            self.current_file = new
        if isinstance(self.web_view, QWebEngineView):
            self.web_view.page().runJavaScript(
                f"renameOpenFile({json.dumps(old)}, {json.dumps(new)})"
            )

    def close_file_silently(self, path: str) -> None:
        """Закрывает вкладку файла (например, после удаления). Без диалога."""
        target = str(Path(path).resolve())
        if target not in self._open_files:
            return
        idx = self._open_files.index(target)
        if isinstance(self.web_view, QWebEngineView):
            self.web_view.page().runJavaScript(f"closeFile({json.dumps(target)})")
        self.tab_bar.removeTab(idx)
        self._open_files.pop(idx)
        self._dirty.pop(target, None)
        if self.current_file == target:
            self.current_file = None


    # ------------------------------------------------------------------
    # Bridge slots
    # ------------------------------------------------------------------

    @Slot(str, str)
    def _on_file_content_ready(self, request_id: str, content: str) -> None:
        if not isinstance(self.web_view, QWebEngineView):
            return
        escaped = json.dumps({"requestId": request_id, "content": content})
        self.web_view.page().runJavaScript(f"onFileContentReady({escaped})")

    @Slot(str, bool)
    def _on_file_write_result(self, request_id: str, success: bool) -> None:
        if not isinstance(self.web_view, QWebEngineView):
            return
        escaped = json.dumps({"requestId": request_id, "success": success})
        self.web_view.page().runJavaScript(f"onFileWriteResult({escaped})")

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_file_reader(self, reader):
        self.bridge.set_file_reader(reader)

    def set_file_writer(self, writer):
        self.bridge.set_file_writer(writer)

    def tab_count(self) -> int:
        return self.tab_bar.count()

    def close_all_tabs(self) -> None:
        while self.tab_bar.count() > 0:
            self.tab_bar.removeTab(0)
        self._open_files.clear()
        self._dirty.clear()
        self.current_file = None

    # Совместимость со старым API --------------------------------------

    def show_diff(self, original: str, modified: str, language: str = "plaintext"):
        if not isinstance(self.web_view, QWebEngineView):
            return
        self.web_view.page().runJavaScript(
            f"showDiff({json.dumps(original)}, {json.dumps(modified)}, {json.dumps(language)})"
        )
        logger.info("Diff Viewer открыт")

    def accept_diff(self):
        if isinstance(self.web_view, QWebEngineView):
            self.web_view.page().runJavaScript("acceptDiff()")

    def reject_diff(self):
        if isinstance(self.web_view, QWebEngineView):
            self.web_view.page().runJavaScript("rejectDiff()")

    def get_content(self, callback):
        if isinstance(self.web_view, QWebEngineView):
            self.web_view.page().runJavaScript("getContent()", callback)