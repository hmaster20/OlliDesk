"""Диалог управления локальными SLM-моделями (GGUF, llama.cpp)."""

import contextlib
import re
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
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
from core.slm_models_io import save_slm_models

# Модульный реестр активных скачиваний: держит сильные ссылки на QThread
# до завершения, чтобы GC не уничтожил объект во время работы.
# Иначе — "QThread: Destroyed while thread is still running" и краш приложения,
# если пользователь закрыл диалог, не дождавшись окончания скачивания.
_ACTIVE_DOWNLOADS: list[QThread] = []


def _register_download(thread: QThread) -> None:
    """Регистрирует поток скачивания в модульном реестре.

    Реестр держит сильную ссылку на поток до тех пор, пока он не завершится,
    поэтому закрытие диалога или потеря Python-ссылки не приведёт к
    преждевременному удалению QThread.

    Args:
        thread: Запускаемый поток скачивания.
    """
    _ACTIVE_DOWNLOADS.append(thread)

    def _cleanup() -> None:
        with contextlib.suppress(ValueError):
            _ACTIVE_DOWNLOADS.remove(thread)

    thread.finished.connect(_cleanup)


class NumericTableWidgetItem(QTableWidgetItem):
    """Кастомный элемент таблицы для правильной числовой сортировки."""
    def __init__(self, text: str, sort_value: Any):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class _DownloadThread(QThread):
    """Скачивает одну модель в фоне."""

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
    хранятся в модульном реестре ``_ACTIVE_DOWNLOADS`` до завершения.

    Параллельные скачивания запрещены (кнопка «Скачать» выключается, пока
    идёт любая загрузка). Действия над уже скачанными моделями (активация,
    удаление) во время скачивания другой модели разрешены — они не трогают
    файлы в процессе записи.
    """

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self._changed = False
        self._download_thread: _DownloadThread | None = None

        # Очередь скачивания
        self._download_queue: list[SLMModelInfo] = []

        self.setWindowTitle("Локальные модели (SLM)")
        self.setMinimumSize(1100, 640)
        self.resize(1200, 800)
        # Разрешаем разворачивать на весь экран
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self.setSizeGripEnabled(True)

        self._setup_ui()
        self._populate()

    # ---------- UI ----------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Локальные SLM-модели (GGUF, llama.cpp) — <b>основной</b> бэкенд: "
            "работают без Ollama. Двухмодельный пайплайн использует "
            "<b>planner</b> (GPU, 1.5B/3B) для classify/plan/critique "
            "и <b>analyst</b> (CPU, 7B/14B) для синтеза ответа.<br>"
            "Список сортируется по клику на заголовки столбцов.<br>"
            "Можно скачивать несколько моделей — они встанут в очередь.<br>"
            "Скачивание продолжается в фоне даже после закрытия окна."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        info_row = QLabel(
            "Пайплайн (planner + analyst) настраивается в верхней панели чата — селекторы «P:» и «A:»."
        )
        info_row.setWordWrap(True)
        info_row.setStyleSheet("color: #888; font-size: 11px; padding: 4px 0 8px 0;")
        layout.addWidget(info_row)

        # --- Таблица всех chat-моделей ---
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Модель", "Издатель", "Параметры", "Квант", "Размер предв.", "Контекст", "Статус", "Активна", "На диске"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        # Включаем сортировку
        self.table.setSortingEnabled(True)
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

        self.auto_select_check = QCheckBox("🔁 Автовыбор модели при запуске (по железу и свободной VRAM)")
        self.auto_select_check.setChecked(self.config.slm.auto_select_model)
        self.auto_select_check.setToolTip(
            "Если выключено — при старте приложения загружается модель, "
            "явно выбранная в этом окне. Если включено — модель может быть "
            "подменена автоматически по свободной VRAM и RAM."
        )
        self.auto_select_check.toggled.connect(self._on_auto_select_toggled)
        layout.addWidget(self.auto_select_check)

        btn_layout = QHBoxLayout()

        self.download_btn = QPushButton("⬇️ Скачать")
        self.download_btn.clicked.connect(self._on_download_clicked)
        btn_layout.addWidget(self.download_btn)

        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self.delete_btn)

        self.activate_btn = QPushButton("✅ Сделать активной")
        self.activate_btn.setToolTip("Назначить выделенную модель активной chat-моделью.")
        self.activate_btn.clicked.connect(self._activate_selected)
        btn_layout.addWidget(self.activate_btn)

        self.add_btn = QPushButton("➕ Своя модель")
        self.add_btn.setToolTip("Добавить модель из Hugging Face вручную.")
        self.add_btn.clicked.connect(self._add_custom_model)
        btn_layout.addWidget(self.add_btn)

        self.hf_search_btn = QPushButton("🔍 Поиск HF")
        self.hf_search_btn.setToolTip("Поиск GGUF-моделей на HuggingFace.")
        self.hf_search_btn.clicked.connect(self._open_hf_dialog)
        btn_layout.addWidget(self.hf_search_btn)

        btn_layout.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def _populate(self):
        """Заполняет таблицу с учетом статусов скачивания и очереди."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        from agents.download_slm import is_model_installed

        def _sort_key(m: SLMModelInfo) -> tuple[int, int, str]:
            active = 0 if m.id == self.config.slm.selected_model else 1
            installed = 0 if is_model_installed(m.file) else 1
            return (active, installed, m.display_name.lower())

        chat_models = sorted(self.config.slm.get_chat_models(), key=_sort_key)

        for row, m in enumerate(chat_models):
            self.table.insertRow(row)

            # 1. Имя
            name_item = QTableWidgetItem(m.display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, m.id)
            self.table.setItem(row, 0, name_item)

            # 2. Издатель (парсим из repo)
            publisher = m.repo.split("/")[0] if "/" in m.repo else "Unknown"
            pub_item = QTableWidgetItem(publisher)
            pub_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, pub_item)

            # 3. Параметры
            params_match = re.search(r"(\d+(?:\.\d+)?[bBmM])", m.id + " " + m.file)
            params = params_match.group(1).upper() if params_match else "?"
            self.table.setItem(row, 2, QTableWidgetItem(params))

            # 4. Квантование
            quant_match = re.search(r"(q\d+(?:_[a-z0-9]+)*|f16|f32)", m.file.lower())
            quant = quant_match.group(1).upper() if quant_match else "?"
            self.table.setItem(row, 3, QTableWidgetItem(quant))

            # 5. Ожидаемый размер
            size_item = NumericTableWidgetItem(f"{m.size_mb} МБ", m.size_mb)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, size_item)

            # 6. Контекст
            ctx_value = m.n_ctx if m.n_ctx > 0 else self.config.slm.n_ctx
            ctx_item = NumericTableWidgetItem(f"{ctx_value} ток.", ctx_value)
            ctx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, ctx_item)

            # 7. Статус
            installed = is_model_installed(m.file)
            if m.id == self._downloading_model_id():
                status_str = "⏬ Скачивается"
            elif any(q.id == m.id for q in self._download_queue):
                status_str = "⏳ В очереди"
            elif installed:
                status_str = "✅ Установлена"
            else:
                status_str = "⬇️ Не скачана"

            status_item = QTableWidgetItem(status_str)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, status_item)

            # 8. Активна
            active = m.id == self.config.slm.selected_model
            active_item = QTableWidgetItem("● Да" if active else "○ Нет")
            active_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 7, active_item)

            # 9. На диске
            real_size_item = QTableWidgetItem(self._real_size_str(m.file))
            real_size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 8, real_size_item)

        # Включаем сортировку обратно
        self.table.setSortingEnabled(True)

        # Embed-модель
        embed_file = self.config.slm.embed_model_file
        embed_installed = is_model_installed(embed_file)
        self.embed_label.setText(f"📎 {embed_file} — {'✅ установлена' if embed_installed else '⬇️ не скачана'}")

        self._update_buttons()

    def _real_size_str(self, filename: str) -> str:
        """Реальный размер на диске. Не ходит в HF."""
        from agents.download_slm import get_local_size_mb

        target_dir = get_models_dir()
        merged = target_dir / filename
        if merged.exists():
            size = get_local_size_mb(filename)
            return f"{size} МБ" if size > 0 else "—"

        stem = filename.removesuffix(".gguf")
        parts = sorted(target_dir.glob(f"{stem}-*-of-*.gguf"))
        if not parts:
            return "—"
        total = sum(p.stat().st_size for p in parts)
        return f"{total // (1024 * 1024)} МБ ({len(parts)} частей)"

    # ---------- helpers ----------

    def _current_model_info(self) -> SLMModelInfo | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        # После сортировки индексы строк могут измениться, берем по UserRole в колонке 0
        item = self.table.item(row, 0)
        if not item:
            return None
        return self.config.slm.get_model_by_id(item.data(Qt.ItemDataRole.UserRole))

    def _downloading_model_id(self) -> str | None:
        thread = self._download_thread
        if thread is None or not thread.isRunning():
            return None
        return thread.info.id

    def _update_buttons(self):
        """Обновляет состояние кнопок по выделенной модели и статусу скачиваний.

        Скачивание блокирует только те действия, которые реально конфликтуют
        с записью файла:

        * «Скачать» — запрещено всегда, пока идёт любое скачивание
          (одновременные загрузки перетирают ``self._download_thread``);
        * «Удалить» — запрещено для модели, которая сейчас качается
          (нельзя удалять файл в процессе записи);
        * «Активировать» — не зависит от скачивания других моделей, потому
          что переключение активной модели не трогает файлы на диске.
        """
        info = self._current_model_info()
        downloading_id = self._downloading_model_id()

        if not info:
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.activate_btn.setEnabled(False)
            return

        installed = is_model_installed(info.file, repo_id=info.repo)
        active = info.id == self.config.slm.selected_model
        in_queue = any(q.id == info.id for q in self._download_queue)
        is_downloading = downloading_id == info.id

        if is_downloading:
            self.download_btn.setText("⏹ Остановить")
            self.download_btn.setEnabled(False)
        elif in_queue:
            self.download_btn.setText("❌ Убрать из очереди")
            self.download_btn.setEnabled(True)
        elif installed:
            self.download_btn.setText("✅ Установлена")
            self.download_btn.setEnabled(False)
        else:
            self.download_btn.setText("⬇️ В очередь")
            self.download_btn.setEnabled(True)

        self.delete_btn.setEnabled(installed and not active and not is_downloading and not in_queue)
        self.activate_btn.setEnabled(installed and not active)

    # ---------- Actions ----------

    def _on_download_clicked(self):
        info = self._current_model_info()
        if not info:
            return

        # Если в очереди -> удаляем
        if any(q.id == info.id for q in self._download_queue):
            self._download_queue = [q for q in self._download_queue if q.id != info.id]
            self._update_queue_status()
            self._populate()
            return

        # Иначе добавляем в очередь
        self._download_queue.append(info)
        self._update_queue_status()
        self._populate()
        self._process_queue()

    def _process_queue(self):
        """Запускает следующую модель из очереди, если ничего не качается."""
        if self._download_thread is not None and self._download_thread.isRunning():
            return
        if not self._download_queue:
            self.progress_bar.setVisible(False)
            self._update_queue_status()
            return

        info = self._download_queue.pop(0)
        self.progress_bar.setVisible(True)

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
        self._update_queue_status()
        self._populate()

    def _update_queue_status(self):
        downloading = self._downloading_model_id()
        if downloading:
            queue_len = len(self._download_queue)
            msg = f"⏬ Скачивание: {downloading}."
            if queue_len > 0:
                msg += f" (В очереди: {queue_len} шт.)"
            self.status_label.setText(msg)
        elif self._download_queue:
            self.status_label.setText(f"⏳ В очереди: {len(self._download_queue)} шт. Ожидание...")
        else:
            self.status_label.setText("")

    def _on_download_progress(self, _model_id: str, message: str) -> None:
        # hf_hub_download не отдает гранулярный прогресс легко,
        # поэтому показываем статус в лейбле
        self.status_label.setText(f"⏬ {message}")

    def _on_download_finished(self, model_id: str, success: bool):
        self._download_thread = None

        if success:
            logger.info(f"Модель {model_id} успешно скачана.")

        # Запускаем следующий из очереди
        self._process_queue()
        self._populate()

    def _on_download_error(self, model_id: str, error: str):
        self._download_thread = None
        QMessageBox.warning(self, "Ошибка скачивания", f"Не удалось скачать {model_id}:\n{error}")
        self._process_queue()
        self._populate()

    # Остальные методы (удаление, активация и т.д.)
    def _delete_selected(self):
        info = self._current_model_info()
        if not info or not is_model_installed(info.file, repo_id=info.repo):
            return
        if info.id == self.config.slm.selected_model:
            QMessageBox.warning(self, "Активная модель", "Нельзя удалить активную модель.")
            return

        reply = QMessageBox.question(self, "Удаление", f"Удалить {info.display_name}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            delete_model(info.file, repo_id=info.repo)
            self._populate()

    def _on_auto_select_toggled(self, checked: bool) -> None:
        """Сохраняет решение пользователя по автовыбору модели.

        При снятии галочки автовыбор выключается навсегда, пока пользователь
        снова его не включит здесь или не поправит config.yaml вручную.
        """
        self.config.slm.auto_select_model = checked
        save_config(self.config)
        save_slm_models(self.config.slm.available_models)
        self._changed = True
        if checked:
            self.status_label.setText("🔁 Автовыбор включён.")
        else:
            self.status_label.setText("📌 Автовыбор выключен.")

    def _activate_selected(self):
        info = self._current_model_info()
        if not info or not is_model_installed(info.file, repo_id=info.repo):
            return
        if info.kind != "chat":
            QMessageBox.information(self, "Недопустимая модель", "Embedding-модель нельзя сделать активной.")
            return

        if info.id != self.config.slm.selected_model:
            self.config.slm.selected_model = info.id
            self.config.slm.auto_select_model = False
            self.auto_select_check.setChecked(False)
            save_config(self.config)
            save_slm_models(self.config.slm.available_models)
            self._changed = True
            self.status_label.setText(f"✅ Активная модель: {info.display_name}")
            self._populate()

    @Slot()
    def _open_hf_dialog(self) -> None:
        """[9.3.1] Открывает диалог поиска на HuggingFace.

        Диалог эмитит `download_requested` / `add_to_catalog_requested`.
        Обработка — здесь, потому что `SLMModelsDialog` уже знает, как
        добавлять модели в каталог и запускать скачивание.
        """
        from ui.dialogs.hf_dialog import HFDialog

        dlg = HFDialog(self)
        dlg.download_requested.connect(self._on_hf_download)
        dlg.add_to_catalog_requested.connect(self._on_hf_add_to_catalog)
        dlg.exec()

    @Slot(object)
    def _on_hf_add_to_catalog(self, payload: Any) -> None:
        """[9.3.1] Добавляет модель из HF-диалога в каталог (без скачивания)."""
        if not isinstance(payload, dict):
            return
        repo_id = str(payload.get("repo_id") or "").strip()
        filename = str(payload.get("filename") or "").strip()
        if not repo_id or not filename:
            return

        base_id = re.sub(r"[^a-z0-9._-]+", "-", filename.removesuffix(".gguf").lower()).strip("-") or "custom-model"
        model_id = base_id
        suffix = 2
        while self.config.slm.get_model_by_id(model_id) is not None:
            model_id = f"{base_id}-{suffix}"
            suffix += 1

        info = SLMModelInfo(
            id=model_id, repo=repo_id, file=filename,
            display_name=str(payload.get("display_name") or filename.removesuffix(".gguf")),
            size_mb=int(payload.get("size_mb") or 0), kind="chat", n_ctx=0,
            tags=list(payload.get("tags") or ["chat"]),
            description=str(payload.get("description") or "")[:500],
        )
        self.config.slm.available_models.append(info)
        save_config(self.config)
        save_slm_models(self.config.slm.available_models)
        self._changed = True
        self._populate()

    @Slot(object)
    def _on_hf_download(self, payload: Any) -> None:
        """[9.3.1] Запускает скачивание модели из HF-диалога.

        Порядок:
          1. Если модели ещё нет в каталоге — добавляем (как в
             `_on_hf_add_to_catalog`).
          2. Запускаем скачивание через `_download_selected` (тот же
             механизм, что и для каталожных моделей).
        """
        if not isinstance(payload, dict):
            return
        repo_id = str(payload.get("repo_id") or "").strip()
        filename = str(payload.get("filename") or "").strip()
        if not repo_id or not filename:
            return

        # 1. Проверяем, есть ли уже в каталоге.
        existing = next((m for m in self.config.slm.available_models if m.repo == repo_id and m.file == filename), None)
        if existing is None:
            self._on_hf_add_to_catalog(payload)
            existing = next((m for m in self.config.slm.available_models if m.repo == repo_id and m.file == filename), None)

        if existing is None:
            return

        # 2. Выделяем строку этой модели в таблице.
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == existing.id:
                self.table.selectRow(row)
                break

        # 3. Добавляем в очередь и запускаем
        if not any(q.id == existing.id for q in self._download_queue):
            self._download_queue.append(existing)
            self._process_queue()
            self._populate()

    def _add_custom_model(self):
        """Добавляет пользовательскую SLM-модель в available_models.

        Пользователь вводит HF-репозиторий, имя файла и отображаемое имя;
        запись сохраняется в config.yaml. id генерируется из имени файла —
        это гарантирует стабильность при перезапусках и совместимость
        с get_model_paths.
        """
        repo, ok = QInputDialog.getText(self, "Своя модель", "HF-репозиторий:")
        if not ok or not repo.strip(): return
        file, ok = QInputDialog.getText(self, "Своя модель", "Имя файла (.gguf):")
        if not ok or not file.strip() or not file.lower().endswith(".gguf"): return
        display_name, ok = QInputDialog.getText(self, "Своя модель", "Имя:", text=file.removesuffix(".gguf"))
        if not ok or not display_name.strip(): return

        base_id = re.sub(r"[^a-z0-9._-]+", "-", file.removesuffix(".gguf").lower()).strip("-") or "custom-model"
        model_id = base_id
        suffix = 2
        while self.config.slm.get_model_by_id(model_id) is not None:
            model_id = f"{base_id}-{suffix}"
            suffix += 1

        new_info = SLMModelInfo(id=model_id, repo=repo.strip(), file=file.strip(), display_name=display_name.strip(), kind="chat")
        self.config.slm.available_models.append(new_info)
        save_config(self.config)
        save_slm_models(self.config.slm.available_models)
        self._changed = True
        self._populate()

    def changed(self) -> bool:
        return self._changed