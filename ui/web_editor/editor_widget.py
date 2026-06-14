"""Виджет редактора на базе QWebEngineView."""

from pathlib import Path

from loguru import logger
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget


class PythonBridge(QObject):
    """Мост между Python и JavaScript."""

    file_read = Signal(str, str)  # path, content
    file_written = Signal(str, bool)  # path, success

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

    @Slot(str, "QVariant")
    def read_file(self, path, callback):
        """
        Читает файл и возвращает содержимое в JS.

        Args:
            path: Путь к файлу
            callback: JS callback для возврата результата
        """
        logger.debug(f"PythonBridge.read_file: {path}")
        try:
            if self._file_reader:
                content = self._file_reader(path)
                callback(content)
            else:
                callback("")
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {e}")
            callback("")

    @Slot(str, str, "QVariant")
    def write_file(self, path, content, callback):
        """
        Записывает содержимое в файл.

        Args:
            path: Путь к файлу
            content: Содержимое
            callback: JS callback для возврата результата
        """
        logger.debug(f"PythonBridge.write_file: {path}")
        try:
            if self._file_writer:
                success = self._file_writer(path, content)
                callback(success)
            else:
                callback(False)
        except Exception as e:
            logger.error(f"Ошибка записи файла: {e}")
            callback(False)


class EditorWidget(QWidget):
    """Виджет редактора Monaco."""

    def __init__(self, parent=None):
        """Инициализация виджета."""
        super().__init__(parent)
        self.current_file: str | None = None

        self._setup_ui()
        self._setup_bridge()

    def _setup_ui(self):
        """Настраивает UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.web_view = QWebEngineView()
        layout.addWidget(self.web_view)

        html_path = Path(__file__).parent / "editor.html"
        self.web_view.setUrl(QUrl.fromLocalFile(str(html_path)))

        logger.info(f"EditorWidget загружен: {html_path}")

    def _setup_bridge(self):
        """Настраивает QWebChannel."""
        self.bridge = PythonBridge(self)

        channel = QWebChannel(self.web_view.page())
        channel.registerObject("pythonBridge", self.bridge)
        self.web_view.page().setWebChannel(channel)

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
        escaped = file_path.replace("\\", "\\\\").replace("'", "\\'")
        self.web_view.page().runJavaScript(f"openFile('{escaped}')")
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
