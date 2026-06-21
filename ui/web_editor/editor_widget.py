"""Виджет редактора на базе QWebEngineView."""

import json

from loguru import logger
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel

from core.utils import get_resource_path

class PythonBridge(QObject):
    """Мост между Python и JavaScript (сигнальная версия)."""

    file_content_ready = Signal(str, str)  # request_id, content
    file_write_result = Signal(str, bool)  # request_id, success

    def __init__(self, parent=None):
        """Инициализация моста."""
        super().__init__(parent)
        self._file_reader = None
        self._file_writer = None

    def set_file_reader(self, reader):
        """Устанавливает функцию чтения файла."""
        self._file_reader = reader

    def set_file_writer(self, writer):
        """Устанавливает функцию записи файла."""
        self._file_writer = writer

    @Slot(str, str)
    def read_file(self, request_id, path):
        """
        Читает файл и отправляет содержимое через сигнал.

        Args:
            request_id: Идентификатор запроса (из JS)
            path: Путь к файлу
        """
        logger.debug(f"PythonBridge.read_file: {path} (id={request_id})")
        try:
            if self._file_reader:
                content = self._file_reader(path)
                self.file_content_ready.emit(request_id, content)
            else:
                self.file_content_ready.emit(request_id, "")
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {e}")
            self.file_content_ready.emit(request_id, "")

    @Slot(str, str, str)
    def write_file(self, request_id, path, content):
        """
        Записывает содержимое в файл и отправляет результат через сигнал.

        Args:
            request_id: Идентификатор запроса (из JS)
            path: Путь к файлу
            content: Содержимое
        """
        logger.debug(f"PythonBridge.write_file: {path} (id={request_id})")
        try:
            if self._file_writer:
                success = self._file_writer(path, content)
                self.file_write_result.emit(request_id, success)
            else:
                self.file_write_result.emit(request_id, False)
        except Exception as e:
            logger.error(f"Ошибка записи файла: {e}")
            self.file_write_result.emit(request_id, False)


class EditorWidget(QWidget):
    """Виджет редактора Monaco."""

    def __init__(self, parent=None):
        """Инициализация виджета."""
        super().__init__(parent)
        self.current_file: str | None = None

        self._setup_ui()
        self._setup_bridge()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        try:
            self.web_view = QWebEngineView()
        except Exception as e:
            logger.error(f"Не удалось создать QWebEngineView: {e}")
            self.web_view = QLabel("WebEngine недоступен")
            self.web_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.web_view.setStyleSheet("color: #888; font-size: 16px; padding: 20px;")

        layout.addWidget(self.web_view)

        if isinstance(self.web_view, QWebEngineView):
            html_path = get_resource_path("ui/web_editor/editor.html")
            if not html_path.exists():
                logger.error(f"editor.html не найден: {html_path}")
            self.web_view.setUrl(QUrl.fromLocalFile(str(html_path)))
            self._setup_bridge()   # настройка моста только для WebEngine

    def _setup_bridge(self):
        """Настраивает QWebChannel."""
        self.bridge = PythonBridge(self)

        channel = QWebChannel(self.web_view.page())
        channel.registerObject("pythonBridge", self.bridge)
        self.web_view.page().setWebChannel(channel)

        # Пробрасываем сигналы в JS
        self.bridge.file_content_ready.connect(self._on_file_content_ready)
        self.bridge.file_write_result.connect(self._on_file_write_result)

    @Slot(str, str)
    def _on_file_content_ready(self, request_id: str, content: str) -> None:
        """Отправляет содержимое файла в JS."""
        escaped = json.dumps({"requestId": request_id, "content": content})
        self.web_view.page().runJavaScript(f"onFileContentReady({escaped})")

    @Slot(str, bool)
    def _on_file_write_result(self, request_id: str, success: bool) -> None:
        """Отправляет результат записи в JS."""
        escaped = json.dumps({"requestId": request_id, "success": success})
        self.web_view.page().runJavaScript(f"onFileWriteResult({escaped})")

    def set_file_reader(self, reader):
        """Устанавливает функцию чтения файла."""
        self.bridge.set_file_reader(reader)

    def set_file_writer(self, writer):
        """Устанавливает функцию записи файла."""
        self.bridge.set_file_writer(writer)

    def open_file(self, file_path: str):
        """
        Открывает файл в редакторе.

        Args:
            file_path: Путь к файлу
        """
        self.current_file = file_path
        escaped = json.dumps(file_path)
        self.web_view.page().runJavaScript(f"openFile({escaped})")
        logger.info(f"Открыт файл в редакторе: {file_path}")

    def show_diff(self, original: str, modified: str, language: str = "plaintext"):
        """
        Показывает Diff Viewer.

        Args:
            original: Оригинальный текст
            modified: Измененный текст
            language: Язык для подсветки
        """
        original_escaped = (
            original.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "")
        )
        modified_escaped = (
            modified.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "")
        )

        self.web_view.page().runJavaScript(
            f"showDiff('{original_escaped}', '{modified_escaped}', '{language}')"
        )
        logger.info("Diff Viewer открыт")

    def accept_diff(self):
        """Принимает изменения в Diff Viewer."""
        self.web_view.page().runJavaScript("acceptDiff()")

    def reject_diff(self):
        """Отклоняет изменения в Diff Viewer."""
        self.web_view.page().runJavaScript("rejectDiff()")

    def get_content(self, callback):
        """
        Получает содержимое редактора.

        Args:
            callback: Функция для возврата результата
        """
        self.web_view.page().runJavaScript("getContent()", callback)
