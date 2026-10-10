"""Диалог управления локальными SLM-моделями (GGUF, llama.cpp)."""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agents.download_slm import (
    delete_model,
    download_model,
    get_models_dir,
    is_model_installed,
)
from core.config import AppConfig, SLMModelInfo, save_config


class _DownloadThread(QThread):
    """Скачивает одну модель в фоне."""

    finished = Signal(str, bool)  # model_id, success
    progress = Signal(str, str)  # model_id, message
    error = Signal(str, str)  # model_id, error

    def __init__(self, info: SLMModelInfo):
        super().__init__()
        self.info = info

    def run(self):
        try:
            self.progress.emit(self.info.id, f"Скачивание {self.info.file}...")
            download_model(self.info.repo, self.info.file)
            self.finished.emit(self.info.id, True)
        except Exception as e:
            self.error.emit(self.info.id, str(e))
            self.finished.emit(self.info.id, False)


class SLMModelsDialog(QDialog):
    """Диалог выбора и управления локальными SLM-моделями.

    Показывает только chat-модели (kind="chat"). Embedding-модель
    выводится отдельной строкой ниже — её нельзя сделать активной
    chat-моделью, чтобы LocalLLMClient не пытался загрузить её как
    chatml-модель.
    """

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self._changed = False
        self._download_thread: _DownloadThread | None = None

        self.setWindowTitle("Локальные модели (SLM)")
        self.setMinimumSize(820, 520)

        self._setup_ui()
        self._populate()

    # ---------- UI ----------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Локальные SLM-модели (GGUF, llama.cpp) — <b>основной</b> бэкенд: "
            "работают без Ollama. <b>Qwen 2.5 7B</b> — хороший баланс качества "
            "и скорости, <b>14B</b> — максимум качества для мощных машин."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Модель", "Ожидаемый размер", "Контекст", "Статус", "Активна", "На диске"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(self._activate_selected)
        layout.addWidget(self.table, stretch=1)

        # Отдельная секция про embed-модель
        embed_info = QLabel(
            "<b>Embedding-модель</b> (для RAG) задаётся отдельно и не участвует "
            "в выборе chat-модели — она загружается как embedding=True."
        )
        embed_info.setWordWrap(True)
        embed_info.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        layout.addWidget(embed_info)

        self.embed_label = QLabel("")
        self.embed_label.setStyleSheet("font-size: 12px; padding: 0 0 8px 0;")
        layout.addWidget(self.embed_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()

        self.download_btn = QPushButton("⬇️ Скачать")
        self.download_btn.clicked.connect(self._download_selected)
        btn_layout.addWidget(self.download_btn)

        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self.delete_btn)

        self.activate_btn = QPushButton("✅ Сделать активной")
        self.activate_btn.clicked.connect(self._activate_selected)
        btn_layout.addWidget(self.activate_btn)

        btn_layout.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def _populate(self):
        self.table.setRowCount(0)

        # Только chat-модели в таблице
        chat_models = self.config.slm.get_chat_models()
        if not chat_models:
            # Аварийный случай: не осталось ни одной chat-модели
            self.status_label.setText("⚠️ В конфиге нет chat-моделей. Проверьте ~/.ollidesk/config.yaml.")

        for row, m in enumerate(chat_models):
            self.table.insertRow(row)

            name_item = QTableWidgetItem(m.display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, m.id)
            self.table.setItem(row, 0, name_item)

            size_item = QTableWidgetItem(f"{m.size_mb} МБ")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, size_item)

            ctx_value = m.n_ctx if m.n_ctx > 0 else self.config.slm.n_ctx
            ctx_item = QTableWidgetItem(f"{ctx_value} ток.")
            ctx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, ctx_item)

            installed = is_model_installed(m.file, repo_id=m.repo)
            status_item = QTableWidgetItem("✅ Установлена" if installed else "⬇️ Не скачана")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, status_item)

            active = m.id == self.config.slm.selected_model
            active_item = QTableWidgetItem("● Да" if active else "○ Нет")
            active_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, active_item)

            real_size_item = QTableWidgetItem(self._real_size_str(m.file, m.repo))
            real_size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, real_size_item)

        # Embed-модель — отдельной строкой ниже
        embed_repo = self.config.slm.embed_model_repo
        embed_file = self.config.slm.embed_model_file
        embed_installed = is_model_installed(embed_file, repo_id=embed_repo)
        embed_status = "✅ установлена" if embed_installed else "⬇️ не скачана"
        self.embed_label.setText(f"📎 {embed_file} — {embed_status}")

        self._update_buttons()

    def _real_size_str(self, filename: str, repo_id: str) -> str:
        """Реальный размер на диске: для split — сумма всех частей."""

        from agents.download_slm import (
            _split_part_name,
            get_model_parts,
        )

        parts = get_model_parts(repo_id, filename)
        target_dir = get_models_dir()

        if parts > 1:
            stem = filename.removesuffix(".gguf")
            total_bytes = 0
            any_found = False
            for i in range(1, parts + 1):
                p = target_dir / _split_part_name(stem, i, parts)
                if p.exists():
                    total_bytes += p.stat().st_size
                    any_found = True
            if not any_found:
                return "—"
            return f"{total_bytes // (1024 * 1024)} МБ ({parts} частей)"

        p = target_dir / filename
        if not p.exists():
            return "—"
        return f"{p.stat().st_size // (1024 * 1024)} МБ"

    # ---------- helpers ----------

    def _current_model_info(self) -> SLMModelInfo | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        model_id = item.data(Qt.ItemDataRole.UserRole)
        info = self.config.slm.get_model_by_id(model_id)
        # Дополнительная страховка: не даём выбрать embed-модель как chat
        if info is not None and info.kind != "chat":
            return None
        return info

    def _update_buttons(self):
        info = self._current_model_info()
        if not info:
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.activate_btn.setEnabled(False)
            return

        installed = is_model_installed(info.file, repo_id=info.repo)
        active = info.id == self.config.slm.selected_model

        self.download_btn.setEnabled(not installed)
        self.delete_btn.setEnabled(installed and not active)
        self.activate_btn.setEnabled(installed and not active)

    # ---------- actions ----------

    def _download_selected(self):
        info = self._current_model_info()
        if not info or is_model_installed(info.file, repo_id=info.repo):
            return

        reply = QMessageBox.question(
            self,
            "Скачивание модели",
            f"Скачать {info.display_name} (~{info.size_mb} МБ)?\nИсточник: huggingface.co/{info.repo}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True, f"Скачивание {info.display_name}...")

        self._download_thread = _DownloadThread(info)
        self._download_thread.progress.connect(lambda _id, msg: self.status_label.setText(msg))
        self._download_thread.finished.connect(self._on_download_finished)
        self._download_thread.error.connect(self._on_download_error)
        self._download_thread.start()

    def _on_download_finished(self, model_id: str, success: bool):
        self._set_busy(False, "")
        self._populate()
        if success:
            self.status_label.setText(f"✅ Модель установлена: {model_id}")

    def _on_download_error(self, model_id: str, error: str):
        self._set_busy(False, "")
        QMessageBox.warning(
            self,
            "Ошибка скачивания",
            f"Не удалось скачать {model_id}:\n{error}",
        )

    def _delete_selected(self):
        info = self._current_model_info()
        if not info or not is_model_installed(info.file, repo_id=info.repo):
            return
        if info.id == self.config.slm.selected_model:
            QMessageBox.warning(
                self,
                "Активная модель",
                "Нельзя удалить активную модель. Сначала переключитесь на другую.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Удаление модели",
            f"Удалить {info.display_name}?\n(будут удалены все части разбитой модели)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if delete_model(info.file, repo_id=info.repo):
            self.status_label.setText(f"🗑 Удалена: {info.display_name}")
            self._populate()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось удалить файл модели.")

    def _activate_selected(self):
        info = self._current_model_info()
        if not info:
            return
        if info.kind != "chat":
            QMessageBox.information(
                self,
                "Недопустимая модель",
                "Embedding-модель нельзя сделать активной chat-моделью.",
            )
            return
        if not is_model_installed(info.file, repo_id=info.repo):
            QMessageBox.information(self, "Не установлена", "Сначала скачайте модель.")
            return
        if info.id == self.config.slm.selected_model:
            return

        self.config.slm.selected_model = info.id
        save_config(self.config)
        self._changed = True
        self.status_label.setText(f"✅ Активная модель: {info.display_name}")
        self._populate()

    def _set_busy(self, busy: bool, message: str):
        self.progress_bar.setVisible(busy)
        self.download_btn.setEnabled(not busy)
        self.delete_btn.setEnabled(not busy)
        self.activate_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    # ---------- public ----------

    def changed(self) -> bool:
        """True, если пользователь переключил активную модель."""
        return self._changed
