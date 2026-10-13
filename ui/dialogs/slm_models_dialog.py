"""Диалог управления локальными SLM-моделями (GGUF, llama.cpp)."""

import re

from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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

# Модульный реестр активных скачиваний: держит сильные ссылки на QThread
# до завершения, чтобы GC не уничтожил объект во время работы.
# Иначе — "QThread: Destroyed while thread is still running" и краш приложения,
# если пользователь закрыл диалог, не дождавшись окончания скачивания,
# или запустил второе скачивание поверх первого.
_ACTIVE_DOWNLOADS: list[QThread] = []


def _register_download(thread: QThread) -> None:
    """Регистрирует поток скачивания в модульном реестре.

    Реестр держит сильную ссылку на поток до тех пор, пока он не завершится,
    поэтому закрытие диалога или потеря Python-ссылки не приведёт к
    преждевременному удалению QThread.
    """
    _ACTIVE_DOWNLOADS.append(thread)

    def _cleanup() -> None:
        try:
            _ACTIVE_DOWNLOADS.remove(thread)
        except ValueError:
            pass

    # QThread.finished — встроенный сигнал без аргументов, испускается
    # ровно один раз при выходе из run().
    thread.finished.connect(_cleanup)


class _DownloadThread(QThread):
    """Скачивает одну модель в фоне.

    ВАЖНО: сигнал называется sig_finished, а не finished, чтобы не
    переопределять встроенный QThread.finished — иначе Qt не сможет корректно
    вызвать внутренние обработчики жизненного цикла потока.
    """

    sig_finished = Signal(str, bool)  # model_id, success
    progress = Signal(str, str)  # model_id, message
    error = Signal(str, str)  # model_id, error

    def __init__(self, info: SLMModelInfo):
        super().__init__()
        self.info = info

    def run(self):
        try:
            self.progress.emit(self.info.id, f"Скачивание {self.info.file}...")
            download_model(self.info.repo, self.info.file)
            self.sig_finished.emit(self.info.id, True)
        except Exception as e:
            logger.exception(f"Ошибка скачивания {self.info.id}")
            self.error.emit(self.info.id, str(e))
            self.sig_finished.emit(self.info.id, False)


class SLMModelsDialog(QDialog):
    """Диалог выбора и управления локальными SLM-моделями.

    Показывает только chat-модели (kind="chat"). Embedding-модель
    выводится отдельной строкой ниже — её нельзя сделать активной
    chat-моделью, чтобы LocalLLMClient не пытался загрузить её как
    chatml-модель.

    Список может существенно вырастать: встроенные модели заданы в
    core.config._DEFAULT_SLM_MODELS, а пользователь добавляет свои
    через кнопку «➕ Своя модель» — они сохраняются в config.yaml
    и не теряются при обновлении приложения (_sync_builtin_models
    сохраняет всё, чего нет в дефолтном списке).

    Скачивание продолжается в фоне даже после закрытия диалога: потоки
    хранятся в модульном реестре `_ACTIVE_DOWNLOADS` до завершения.
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
            "и скорости, <b>14B</b> — максимум качества для мощных машин.<br>"
            "Список сортируется: активная → установленные → остальные.<br>"
            "Скачивание продолжается в фоне даже после закрытия окна."
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

        self.add_btn = QPushButton("➕ Своя модель")
        self.add_btn.setToolTip(
            "Добавить модель из Hugging Face вручную. Она попадёт в список "
            "и сохранится в ~/.ollidesk/config.yaml."
        )
        self.add_btn.clicked.connect(self._add_custom_model)
        btn_layout.addWidget(self.add_btn)

        btn_layout.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def _populate(self):
        self.table.setRowCount(0)

        # Сортируем: активная → установленные → остальные (внутри — по имени).
        def _sort_key(m: SLMModelInfo) -> tuple[int, int, str]:
            active = 0 if m.id == self.config.slm.selected_model else 1
            installed = 0 if is_model_installed(m.file, repo_id=m.repo) else 1
            return (active, installed, m.display_name.lower())

        chat_models = sorted(self.config.slm.get_chat_models(), key=_sort_key)
        if not chat_models:
            self.status_label.setText(
                "⚠️ В конфиге нет chat-моделей. Проверьте ~/.ollidesk/config.yaml."
            )

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
        if info is not None and info.kind != "chat":
            return None
        return info

    def _is_downloading(self) -> bool:
        """True, если сейчас идёт скачивание (кнопки должны быть выключены)."""
        return self._download_thread is not None and self._download_thread.isRunning()

    def _update_buttons(self):
        # Пока идёт скачивание, ВСЕ действия блокируются — иначе пользователь
        # может запустить второе скачивание, перезаписав self._download_thread
        # (первый поток потеряет Python-ссылку и крашнется).
        if self._is_downloading():
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.activate_btn.setEnabled(False)
            return

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
        if self._is_downloading():
            QMessageBox.information(
                self,
                "Скачивание уже идёт",
                "Дождитесь завершения текущего скачивания. Оно продолжится "
                "в фоне даже после закрытия окна.",
            )
            return

        info = self._current_model_info()
        if not info or is_model_installed(info.file, repo_id=info.repo):
            return

        reply = QMessageBox.question(
            self,
            "Скачивание модели",
            f"Скачать {info.display_name} (~{info.size_mb} МБ)?\n"
            f"Источник: huggingface.co/{info.repo}\n\n"
            "Скачивание продолжится в фоне, окно можно закрыть.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True, f"Скачивание {info.display_name}...")

        thread = _DownloadThread(info)
        thread.progress.connect(self._on_download_progress)
        thread.sig_finished.connect(self._on_download_finished)
        thread.error.connect(self._on_download_error)

        # Модульный реестр держит сильную ссылку до завершения потока —
        # защита от "QThread: Destroyed while thread is still running",
        # если диалог будет закрыт раньше окончания скачивания.
        _register_download(thread)

        self._download_thread = thread
        thread.start()

    def _on_download_progress(self, _model_id: str, message: str) -> None:
        self.status_label.setText(message)

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

    def _add_custom_model(self):
        """Добавляет пользовательскую SLM-модель в available_models.

        Пользователь вводит HF-репозиторий, имя файла и отображаемое имя;
        запись сохраняется в config.yaml. id генерируется из имени файла —
        это гарантирует стабильность при перезапусках и совместимость
        с get_model_paths.
        """
        repo, ok = QInputDialog.getText(
            self,
            "Своя SLM-модель",
            "HF-репозиторий (например, Qwen/Qwen2.5-7B-Instruct-GGUF):",
        )
        if not ok or not repo.strip():
            return
        repo = repo.strip()

        file, ok = QInputDialog.getText(
            self,
            "Своя SLM-модель",
            "Имя файла в репозитории (например, qwen2.5-7b-instruct-q4_k_m.gguf):",
        )
        if not ok or not file.strip():
            return
        file = file.strip()
        if not file.lower().endswith(".gguf"):
            QMessageBox.warning(
                self,
                "Неверное имя файла",
                "Имя файла должно заканчиваться на .gguf",
            )
            return

        display_name, ok = QInputDialog.getText(
            self,
            "Своя SLM-модель",
            "Отображаемое имя (как показывать в списке):",
            text=file.removesuffix(".gguf"),
        )
        if not ok or not display_name.strip():
            return
        display_name = display_name.strip()

        # Стабильный id из имени файла: qwen2.5-7b-instruct-q4_k_m
        base_id = re.sub(r"[^a-z0-9._-]+", "-", file.removesuffix(".gguf").lower())
        base_id = base_id.strip("-") or "custom-model"

        # Если id занят — добавляем суффикс -2, -3, …
        model_id = base_id
        suffix = 2
        while self.config.slm.get_model_by_id(model_id) is not None:
            model_id = f"{base_id}-{suffix}"
            suffix += 1

        new_info = SLMModelInfo(
            id=model_id,
            repo=repo,
            file=file,
            display_name=display_name,
            size_mb=0,  # точный размер узнаем из HF при первом скачивании
            kind="chat",
            n_ctx=0,  # 0 → взять slm.n_ctx из конфига
        )
        self.config.slm.available_models.append(new_info)
        save_config(self.config)
        self._changed = True

        logger.info(
            f"SLM: добавлена пользовательская модель id={model_id!r}, "
            f"repo={repo!r}, file={file!r}"
        )
        self.status_label.setText(
            f"➕ Добавлена: {display_name}. Скачайте её кнопкой «⬇️ Скачать»."
        )
        self._populate()

    def _set_busy(self, busy: bool, message: str):
        self.progress_bar.setVisible(busy)
        self.download_btn.setEnabled(not busy)
        self.delete_btn.setEnabled(not busy)
        self.activate_btn.setEnabled(not busy)
        self.add_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    # ---------- public ----------

    def changed(self) -> bool:
        """True, если пользователь переключил активную модель или добавил свою."""
        return self._changed