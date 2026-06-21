"""Диалог управления реестром моделей."""
import subprocess
import sys

from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.model_registry import ModelInfo, ModelRegistry


class PullThread(QThread):
    """Поток для выполнения ollama pull."""
    finished = Signal(str, bool)  # model_name, success
    progress = Signal(str)        # строка вывода

    def __init__(self, model_name: str):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            # Запускаем ollama pull
            process = subprocess.Popen(
                ["ollama", "pull", self.model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ''):
                    self.progress.emit(line.strip())
            process.wait()
            success = process.returncode == 0
            self.finished.emit(self.model_name, success)
        except Exception as e:
            logger.error(f"Ошибка pull {self.model_name}: {e}")
            self.finished.emit(self.model_name, False)


class ModelManagerDialog(QDialog):
    def __init__(self, registry: ModelRegistry, ollama_url: str, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.ollama_url = ollama_url
        self.setWindowTitle("Управление моделями")
        self.setMinimumSize(800, 500)
        self.resize(900, 600)

        layout = QVBoxLayout(self)

        # Таблица моделей
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Модель", "Локально", "Tools", "Описание", "Действие"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Кнопки управления
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 Синхронизировать с Ollama")
        self.refresh_btn.clicked.connect(self.sync_models)
        btn_layout.addWidget(self.refresh_btn)

        self.add_btn = QPushButton("➕ Добавить модель")
        self.add_btn.clicked.connect(self.add_model)
        btn_layout.addWidget(self.add_btn)

        self.pull_all_btn = QPushButton("⬇️ Скачать все отсутствующие")
        self.pull_all_btn.clicked.connect(self.pull_all)
        btn_layout.addWidget(self.pull_all_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        # Прогресс-бар для отображения pull
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.populate_table()

    def populate_table(self):
        """Заполняет таблицу данными из реестра."""
        self.table.setRowCount(0)
        all_models = self.registry.get_all_models()
        for row, (name, info) in enumerate(all_models.items()):
            self.table.insertRow(row)
            # Имя
            item = QTableWidgetItem(name)
            self.table.setItem(row, 0, item)
            # Локально
            local_text = "✅" if info.is_local else "⬇️"
            item_local = QTableWidgetItem(local_text)
            item_local.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_local)
            # Tools
            if info.supports_tools is True:
                tools_text = "✅"
                tooltip = "Поддерживает инструменты"
            elif info.supports_tools is False:
                tools_text = "❌"
                tooltip = "Только чат"
            else:
                tools_text = "❓"
                tooltip = "Не проверено"
            item_tools = QTableWidgetItem(tools_text)
            item_tools.setToolTip(tooltip)
            item_tools.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, item_tools)
            # Описание
            desc_item = QTableWidgetItem(info.description)
            self.table.setItem(row, 3, desc_item)
            # Действие: кнопка Pull (если не локальна) или "Проверить" (если неизвестно)
            btn = QPushButton()
            if not info.is_local:
                btn.setText("⬇️ Pull")
                btn.clicked.connect(lambda checked, name=name: self.pull_model(name))
            elif info.supports_tools is None:
                btn.setText("🔍 Проверить")
                btn.clicked.connect(lambda checked, name=name: self.check_model(name))
            else:
                btn.setText("✅")
                btn.setEnabled(False)
            self.table.setCellWidget(row, 4, btn)

    def sync_models(self):
        """Синхронизирует реестр с Ollama (обновляет локальность)."""
        # Получаем список моделей из Ollama
        import httpx
        try:
            resp = httpx.get(f"{self.ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                ollama_models = [m["name"] for m in data.get("models", [])]
                self.registry.sync_with_ollama(ollama_models)
                self.populate_table()
                QMessageBox.information(self, "Синхронизация", f"Обновлено. Найдено {len(ollama_models)} моделей.")
            else:
                QMessageBox.warning(self, "Ошибка", f"Не удалось получить список моделей: {resp.status_code}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Ошибка подключения к Ollama: {e}")

    def pull_model(self, model_name: str):
        """Запускает загрузку модели через ollama pull."""
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Загрузить модель '{model_name}'?\nЭто может занять время.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # неопределённый прогресс
        self.pull_thread = PullThread(model_name)
        self.pull_thread.finished.connect(self.on_pull_finished)
        self.pull_thread.progress.connect(lambda txt: logger.debug(f"Pull: {txt}"))  # можно отображать в логе
        self.pull_thread.start()

    def on_pull_finished(self, model_name: str, success: bool):
        self.progress_bar.setVisible(False)
        if success:
            QMessageBox.information(self, "Успех", f"Модель '{model_name}' успешно загружена.")
            # Обновляем реестр: is_local = True
            info = self.registry.get(model_name)
            if info:
                info.is_local = True
                self.registry.save()
            self.populate_table()
        else:
            QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить '{model_name}'.")

    def check_model(self, model_name: str):
        """Проверяет поддержку инструментов для модели."""
        # Вызываем метод из основного приложения? Проще запустить проверку здесь же.
        # Но можно вызвать через сигнал или просто выполнить.
        import asyncio

        from agents.ollama_client import OllamaClient
        async def check():
            client = OllamaClient(base_url=self.ollama_url)
            async with client:
                return await client.check_tools_support(model_name)
        try:
            loop = asyncio.new_event_loop()
            supports = loop.run_until_complete(check())
            loop.close()
            self.registry.update_tool_support(model_name, supports)
            self.populate_table()
            QMessageBox.information(self, "Результат", f"Модель '{model_name}' {'поддерживает' if supports else 'не поддерживает'} инструменты.")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось проверить: {e}")

    def add_model(self):
        """Ручное добавление модели в реестр."""
        model_name, ok = QInputDialog.getText(self, "Добавить модель", "Введите имя модели (например, llama3.2):")
        if ok and model_name.strip():
            if model_name in self.registry.models:
                QMessageBox.warning(self, "Ошибка", "Модель уже существует в реестре.")
                return
            # Создаём запись с неизвестными параметрами
            self.registry.models[model_name] = ModelInfo(
                name=model_name,
                description="Пользовательская модель. Отредактируйте описание вручную.",
                recommended_modes=["chat"],
                supports_tools=None,
                is_local=False
            )
            self.registry.save()
            self.populate_table()
            QMessageBox.information(self, "Готово", f"Модель '{model_name}' добавлена в реестр.")

    def pull_all(self):
        """Скачивает все модели, которые не локальны."""
        to_pull = [name for name, info in self.registry.models.items() if not info.is_local]
        if not to_pull:
            QMessageBox.information(self, "Информация", "Все модели уже локальны.")
            return
        # Спрашиваем подтверждение
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Будет загружено {len(to_pull)} моделей. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Запускаем последовательную загрузку (можно сделать параллельно, но осторожно)
        # Пока простой вариант: загружаем по одной
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(to_pull))
        self.progress_bar.setValue(0)
        self.pull_queue = to_pull[:]
        self.pull_all_next()

    def pull_all_next(self):
        if not self.pull_queue:
            self.progress_bar.setVisible(False)
            QMessageBox.information(self, "Готово", "Все модели загружены.")
            self.populate_table()
            return
        model = self.pull_queue.pop(0)
        self.pull_thread = PullThread(model)
        self.pull_thread.finished.connect(lambda name, success: self.on_pull_all_item(name, success))
        self.pull_thread.start()

    def on_pull_all_item(self, model_name: str, success: bool):
        if success:
            info = self.registry.get(model_name)
            if info:
                info.is_local = True
                self.registry.save()
        else:
            logger.warning(f"Не удалось загрузить {model_name}")
        self.progress_bar.setValue(self.progress_bar.value() + 1)
        # Задержка, чтобы не перегружать сеть
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, self.pull_all_next)
