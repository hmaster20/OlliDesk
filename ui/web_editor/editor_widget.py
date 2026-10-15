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
)

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