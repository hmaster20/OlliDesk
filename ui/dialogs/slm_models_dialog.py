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
    compute_sha256,
    delete_model,
    fetch_expected_sha256,
    get_file_status,
    get_local_size_mb,
    get_model_path,
    get_models_dir,
    is_model_installed,
)
from core.config import AppConfig, SLMModelInfo, save_config
from core.slm_models_io import save_slm_models
from ui.dialogs.verification_report_dialog import (
    VerificationReportDialog,
    VerificationRow,
)

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
            return bool(self.sort_value < other.sort_value)
        # QTableWidgetItem.__lt__ есть в рантайме, но не в стабах mypy.
        return bool(super().__lt__(other))  # type: ignore[misc]


class _DownloadThread(QThread):
    """Скачивание модели с прогрессом и resume (Этап 3.4).

    Signals:
        progress_percent(model_id, pct) — 0..100 после каждого чанка.
        progress_message(model_id, msg) — текст для статус-строки.
        sig_finished(model_id, ok).
        error(model_id, error).
    """

    progress_percent = Signal(str, int)
    progress_message = Signal(str, str)
    sig_finished = Signal(str, bool)
    error = Signal(str, str)

    def __init__(self, info: SLMModelInfo):
        super().__init__()
        self.info = info
        self._last_pct: int = -1

    def run(self):
        try:

            def cb(pct: int, msg: str) -> None:
                # Логируем в консоль (виден в терминале).
                logger.info(f"Download {self.info.id}: {pct}% — {msg}")
                self.progress_percent.emit(self.info.id, pct)
                self.progress_message.emit(self.info.id, msg)

            self.progress_message.emit(self.info.id, f"Скачивание {self.info.file}…")
            from agents.download_slm import download_model_with_progress

            download_model_with_progress(
                self.info.repo,
                self.info.file,
                progress_cb=cb,
                verify_sha256=None,
            )
            self.sig_finished.emit(self.info.id, True)
        except Exception as e:
            logger.exception(f"Ошибка скачивания {self.info.id}")
            self.error.emit(self.info.id, str(e))
            self.sig_finished.emit(self.info.id, False)


class _VerifyAllThread(QThread):
    """Фоновая проверка всех моделей (Этап 3.1).

    Режимы:
      * ``verify_sha256=False`` — быстрая (только размер). 8 моделей = <1 сек.
      * ``verify_sha256=True`` — полная (SHA256 по каждой). 8 моделей =
        1–3 минуты в зависимости от размера.

    Эмитит ``progress(current, total, model_id)`` и ``finished(list[VerificationRow])``.
    """

    progress = Signal(int, int, str)
    finished = Signal(list)  # list[VerificationRow]
    error = Signal(str)

    def __init__(self, models: list[SLMModelInfo], *, verify_sha256: bool):
        super().__init__()
        self._models = list(models)
        self._verify_sha256 = verify_sha256

    def run(self) -> None:
        try:
            rows: list[VerificationRow] = []
            total = len(self._models)
            for i, m in enumerate(self._models):
                self.progress.emit(i + 1, total, m.id)

                status = get_file_status(m.file)
                size_mb = 0
                try:
                    size_mb = get_local_size_mb(m.file)
                except Exception:
                    size_mb = 0

                expected_sha = ""
                actual_sha = ""
                error = ""

                if status == "installed" and self._verify_sha256:
                    path = get_model_path(m.file)
                    if path is not None:
                        try:
                            actual_sha = compute_sha256(path)
                        except OSError as e:
                            error = f"read: {e}"
                            status = "ok" if m.verification_status == "ok" else "size_ok"
                        else:
                            exp = fetch_expected_sha256(m.repo, m.file)
                            if exp:
                                expected_sha = exp
                                if actual_sha != exp:
                                    status = "sha_mismatch"
                                else:
                                    status = "ok"
                            else:
                                status = "size_ok"
                elif status == "installed":
                    # Быстрая проверка: размер прошёл, SHA не считали.
                    status = "size_ok"

                rows.append(
                    VerificationRow(
                        model=m,
                        status=status,
                        size_mb=size_mb,
                        expected_sha=expected_sha,
                        actual_sha=actual_sha,
                        error=error,
                    )
                )
            self.finished.emit(rows)
        except Exception as e:
            logger.exception("_VerifyAllThread failed")
            self.error.emit(str(e))


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
        self._download_percent: dict[str, int] = {}  # model_id → последний %
        self._verify_thread: _VerifyAllThread | None = None

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
            [
                "Модель",
                "Издатель",
                "Параметры",
                "Квант",
                "Размер предв.",
                "Контекст",
                "Статус",
                "Активна",
                "На диске",
            ]
        )
        # [UI-2025] Все столбцы — регулируемые мышкой (Interactive).
        # Последний растягивается при расширении окна.
        header = self.table.horizontalHeader()
        for col in range(self.table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setWordWrap(False)

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
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
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

        # [UI-2025] Проверка всех моделей (Этап 3.1).
        self.verify_all_btn = QPushButton("🔍 Проверить все модели")
        self.verify_all_btn.setToolTip(
            "Проверить целостность всех моделей в каталоге.\n"
            "Быстрая — только размер (мгновенно).\n"
            "Полная — сверить SHA256 с HuggingFace (1–3 минуты на 8 моделей)."
        )
        self.verify_all_btn.clicked.connect(self._on_verify_all_clicked)
        btn_layout.addWidget(self.verify_all_btn)

        btn_layout.addStretch()

        self.fit_btn = QPushButton("↔ Авто-ширина")
        self.fit_btn.setToolTip("Подогнать ширину всех столбцов под содержимое (потом можно двигать мышкой).")
        self.fit_btn.clicked.connect(self._on_fit_columns)
        btn_layout.addWidget(self.fit_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    @Slot()
    def _on_fit_columns(self) -> None:
        """Подгоняет ширины столбцов по содержимому."""
        self.table.resizeColumnsToContents()
        for col, max_w in ((0, 380), (1, 260)):
            if self.table.columnWidth(col) > max_w:
                self.table.setColumnWidth(col, max_w)

    def _populate(self):
        """Заполняет таблицу с учетом статусов скачивания и очереди."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        from agents.download_slm import is_model_installed

        def _sort_key(m: SLMModelInfo) -> tuple[int, int, str]:
            active = 0 if m.id == self.config.slm.selected_model else 1
            # [UI-2025] Битые — наравне с неустановленными.
            status = self._status_for(m)
            installed = 0 if status == "installed" else 1
            return (active, installed, m.display_name.lower())

        chat_models = sorted(self.config.slm.get_chat_models(), key=_sort_key)

        for row, m in enumerate(chat_models):
            self.table.insertRow(row)

            # 1. Имя (+ 🧠 для thinking-моделей)
            name_prefix = "🧠 " if getattr(m, "is_thinking", False) else ""
            name_item = QTableWidgetItem(f"{name_prefix}{m.display_name}")
            name_item.setData(Qt.ItemDataRole.UserRole, m.id)
            if getattr(m, "is_thinking", False):
                name_item.setToolTip(
                    "🧠 Модель с режимом размышлений (<think>...</think>). "
                    "Reasoning идёт в отдельную панель внутри пузыря."
                )
            self.table.setItem(row, 0, name_item)

            # 2. Издатель (парсим из repo)
            publisher = m.repo.split("/")[0] if "/" in m.repo else "Unknown"
            pub_item = QTableWidgetItem(publisher)
            pub_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, pub_item)

            # 3. Параметры — числовая сортировка (1.5B, 3B, 7B, 14B, 30B, 32B, 70B).
            # Раньше был строковый QTableWidgetItem, из-за чего 14B < 30B < 32B < 3B < 70B < 7B.
            params_match = re.search(r"(\d+(?:\.\d+)?)[bB]", m.id + " " + m.file)
            if params_match:
                try:
                    params_val = float(params_match.group(1))
                except ValueError:
                    params_val = 0.0
                params_text = f"{params_val:g}B"  # 1.5 → "1.5B", 7.0 → "7B"
            else:
                params_val = 0.0
                params_text = "?"
            params_item = NumericTableWidgetItem(params_text, params_val)
            params_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, params_item)

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
            file_status = self._status_for(m)
            if m.id == self._downloading_model_id():
                pct = self._download_percent.get(m.id, 0)
                status_str = f"⏬ Скачивание ({pct}%)" if pct > 0 else "⏬ Скачивается"
            elif any(q.id == m.id for q in self._download_queue):
                status_str = "⏳ В очереди"
            elif file_status == "installed":
                status_str = "✅ Установлена"
            elif file_status == "broken":
                status_str = "⚠️ Битый файл"
            else:
                status_str = "⬇️ Не скачана"

            status_item = QTableWidgetItem(status_str)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tip_parts: list[str] = []
            if file_status == "broken":
                # Уточняем причину: маркер .partial или размер.
                from agents.download_slm import (
                    _detect_parts_from_local,
                    _partial_path_for_file,
                    _partial_path_for_split,
                )

                stem = m.file.removesuffix(".gguf")
                is_split = _detect_parts_from_local(m.file, get_models_dir()) > 1
                has_partial = (
                    _partial_path_for_split(stem, get_models_dir()).exists()
                    if is_split
                    else _partial_path_for_file(get_models_dir() / m.file).exists()
                )
                if has_partial:
                    tip_parts.append(
                        "⏸ Скачивание прервано (маркер .partial на диске).\n"
                        "Нажмите «⬇️ Перекачать» — скачивание продолжится "
                        "с места обрыва."
                    )
                else:
                    tip_parts.append(
                        "Файл есть, но размер меньше ожидаемого (обрыв скачивания).\n"
                        "Нажмите «⬇️ Перекачать», чтобы удалить и скачать заново."
                    )
            if getattr(m, "last_verified_at", ""):
                tip_parts.append(f"Последняя проверка: {m.last_verified_at}")
                v_status = getattr(m, "verification_status", "")
                if v_status == "ok":
                    tip_parts.append("Результат: ✅ OK (размер + SHA256)")
                elif v_status == "size_ok":
                    tip_parts.append("Результат: ✅ OK (только размер)")
                elif v_status == "sha_mismatch":
                    tip_parts.append("Результат: ❌ SHA256 не совпал — перекачайте")
            if tip_parts:
                status_item.setToolTip("\n".join(tip_parts))
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

        # [UI-2025] Подгоняем ширины по содержимому. Пользователь потом
        # может двигать мышкой (Interactive).
        self.table.resizeColumnsToContents()
        # Ограничиваем «Модель» (длинные имена) и «Файл» — чтобы не занимали
        # половину окна.
        for col, max_w in ((0, 380), (1, 260)):
            if self.table.columnWidth(col) > max_w:
                self.table.setColumnWidth(col, max_w)

        # Embed-модель
        embed_file = self.config.slm.embed_model_file
        embed_installed = is_model_installed(embed_file)
        self.embed_label.setText(
            f"📎 {embed_file} — {'✅ установлена' if embed_installed else '⬇️ не скачана'}"
        )

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

    @staticmethod
    def _status_for(info: SLMModelInfo) -> str:
        """[UI-2025] Статус модели с учётом заявленного размера.

        Передаёт `declared_size_mb` в `get_file_status`, чтобы недокачанный
        файл (обрыв на 15%) считался «битым», а не «установленным».
        """
        return get_file_status(info.file, declared_size_mb=info.size_mb)

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
        """Обновляет состояние кнопок по выделенной модели и статусу скачиваний."""
        info = self._current_model_info()
        downloading_id = self._downloading_model_id()

        if not info:
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.activate_btn.setEnabled(False)
            return

        file_status = self._status_for(info)
        active = info.id == self.config.slm.selected_model
        in_queue = any(q.id == info.id for q in self._download_queue)
        is_downloading = downloading_id == info.id

        if is_downloading:
            self.download_btn.setText("⏹ Остановить")
            self.download_btn.setEnabled(False)
        elif in_queue:
            self.download_btn.setText("❌ Убрать из очереди")
            self.download_btn.setEnabled(True)
        elif file_status == "installed":
            self.download_btn.setText("✅ Установлена")
            self.download_btn.setEnabled(False)
        elif file_status == "broken":
            # [UI-2025] Если есть маркер .partial — resume, иначе перекачка.
            from agents.download_slm import (
                _detect_parts_from_local,
                _partial_path_for_file,
                _partial_path_for_split,
            )

            stem = info.file.removesuffix(".gguf")
            is_split = _detect_parts_from_local(info.file, get_models_dir()) > 1
            has_partial = (
                _partial_path_for_split(stem, get_models_dir()).exists()
                if is_split
                else _partial_path_for_file(get_models_dir() / info.file).exists()
            )
            if has_partial:
                self.download_btn.setText("▶ Продолжить")
                self.download_btn.setToolTip("Скачивание прервано. Продолжить с места обрыва (Range-запрос).")
            else:
                self.download_btn.setText("⬇️ Перекачать")
                self.download_btn.setToolTip(
                    "Размер файла меньше ожидаемого — скачивание прервалось.\nУдалить и скачать заново."
                )
            self.download_btn.setEnabled(True)
        else:
            self.download_btn.setText("⬇️ В очередь")
            self.download_btn.setEnabled(True)
            self.download_btn.setToolTip("")

        # [UI-2025] Битые тоже можно удалить (чтобы почистить).
        self.delete_btn.setEnabled(
            (file_status in ("installed", "broken")) and not active and not is_downloading and not in_queue
        )
        self.activate_btn.setEnabled(file_status == "installed" and not active)

    def _on_verify_all_clicked(self) -> None:
        """Спрашивает режим и запускает проверку."""
        if self._verify_thread is not None and self._verify_thread.isRunning():
            QMessageBox.information(self, "Проверка идёт", "Проверка уже выполняется — дождитесь завершения.")
            return

        # Модели для проверки: только те, что есть на диске (installed/broken).
        candidates = [
            m for m in self.config.slm.get_chat_models() if get_file_status(m.file) in ("installed", "broken")
        ]
        if not candidates:
            QMessageBox.information(self, "Нечего проверять", "Нет скачанных моделей для проверки.")
            return

        # Меню выбора режима.
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        act_fast = menu.addAction("⚡ Быстрая (только размер)")
        act_full = menu.addAction("🛡 Полная (размер + SHA256)")

        chosen = menu.exec(self.verify_all_btn.mapToGlobal(self.verify_all_btn.rect().bottomLeft()))
        if chosen is None:
            return
        # Разные действия → разные режимы; при неожиданном выборе — выходим.
        if chosen is act_fast:
            verify_sha256 = False
        elif chosen is act_full:
            verify_sha256 = True
        else:
            return

        if verify_sha256:
            total_mb = sum(get_local_size_mb(m.file) for m in candidates)
            reply = QMessageBox.question(
                self,
                "Полная проверка",
                f"Будет считаться SHA256 для {len(candidates)} моделей "
                f"(суммарно ~{total_mb} МБ).\n\n"
                f"Это может занять 1–3 минуты. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Запуск.
        self.verify_all_btn.setEnabled(False)
        self.verify_all_btn.setText("⏳ Проверка…")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(candidates))
        self.progress_bar.setValue(0)

        self._verify_thread = _VerifyAllThread(candidates, verify_sha256=verify_sha256)
        self._verify_thread.progress.connect(self._on_verify_progress)
        self._verify_thread.finished.connect(self._on_verify_finished)
        self._verify_thread.error.connect(self._on_verify_error)
        self._verify_thread.start()

    @Slot(int, int, str)
    def _on_verify_progress(self, current: int, total: int, model_id: str) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"🔍 Проверка {current}/{total}: {model_id}…")

    @Slot(list)
    def _on_verify_finished(self, rows: list[VerificationRow]) -> None:
        """Обновляет модели (last_verified_at, verification_status) и показывает отчёт."""
        from datetime import datetime

        from core.slm_models_io import save_slm_models

        now = datetime.now().isoformat(timespec="seconds")
        by_id = {m.id: m for m in self.config.slm.available_models}

        for r in rows:
            info = by_id.get(r.model.id)
            if info is None:
                continue
            info.last_verified_at = now
            if r.status == "ok":
                info.verification_status = "ok"
            elif r.status == "size_ok":
                info.verification_status = "size_ok"
            elif r.status == "sha_mismatch":
                info.verification_status = "sha_mismatch"
            elif r.status == "broken":
                info.verification_status = "broken"

        try:
            save_slm_models(self.config.slm.available_models)
        except Exception:
            logger.debug("Не удалось сохранить last_verified_at", exc_info=True)

        self.progress_bar.setVisible(False)
        self.verify_all_btn.setEnabled(True)
        self.verify_all_btn.setText("🔍 Проверить все модели")
        self.status_label.setText("")
        self._populate()  # обновить tooltip-ы

        dlg = VerificationReportDialog(
            rows,
            sha_was_checked=bool(rows and rows[0].actual_sha),
            parent=self,
        )
        dlg.redownload_requested.connect(self._on_verify_redownload)
        dlg.delete_broken_requested.connect(self._on_verify_delete_broken)
        dlg.exec()

        self._verify_thread = None

    @Slot(str)
    def _on_verify_error(self, error: str) -> None:
        self.progress_bar.setVisible(False)
        self.verify_all_btn.setEnabled(True)
        self.verify_all_btn.setText("🔍 Проверить все модели")
        self.status_label.setText("")
        QMessageBox.warning(self, "Ошибка проверки", error)
        self._verify_thread = None

    @Slot(object)
    def _on_verify_redownload(self, models: list) -> None:
        """Из отчёта: удалить битые файлы и поставить в очередь скачивания."""
        for m in models:
            try:
                delete_model(m.file, repo_id=m.repo)
            except Exception:
                logger.debug(f"Не удалось удалить {m.file}", exc_info=True)
            if not any(q.id == m.id for q in self._download_queue):
                self._download_queue.append(m)
        self._process_queue()
        self._populate()

    @Slot(object)
    def _on_verify_delete_broken(self, filenames: list) -> None:
        """Из отчёта: удалить битые файлы без перекачки."""
        for fname in filenames:
            try:
                delete_model(fname)
            except Exception:
                logger.debug(f"Не удалось удалить {fname}", exc_info=True)
        self._populate()

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

            # [UI-2025] Если битый файл на диске — предложим resume, если есть
        # маркер .partial. Если маркера нет — удалить и скачать заново.
        file_status = self._status_for(info)
        if file_status == "broken":
            from agents.download_slm import (
                _detect_parts_from_local,
                _partial_path_for_file,
                _partial_path_for_split,
            )

            stem = info.file.removesuffix(".gguf")
            is_split = _detect_parts_from_local(info.file, get_models_dir()) > 1
            has_partial = (
                _partial_path_for_split(stem, get_models_dir()).exists()
                if is_split
                else _partial_path_for_file(get_models_dir() / info.file).exists()
            )
            if has_partial:
                reply = QMessageBox.question(
                    self,
                    "Продолжить скачивание?",
                    f"Файл {info.file} недокачан. Продолжить с места обрыва?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                # НЕ удаляем файл — resume через Range.
            else:
                reply = QMessageBox.question(
                    self,
                    "Перекачать битый файл?",
                    f"Файл {info.file} есть, но размер меньше ожидаемого.\n\nУдалить и скачать заново?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                try:
                    delete_model(info.file, repo_id=info.repo)
                    logger.info(f"Битый файл {info.file} удалён перед перекачкой")
                except Exception:
                    logger.debug(f"Не удалось удалить битый {info.file}", exc_info=True)
            self._download_percent.pop(info.id, None)

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
            self.progress_bar.setValue(0)          # ← НОВОЕ
            self._update_queue_status()
            return

        info = self._download_queue.pop(0)

        # ↓↓↓ НОВОЕ: сброс перед новой моделью ↓↓↓
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        # ↑↑↑

        thread = _DownloadThread(info)
        thread.progress_percent.connect(self._on_download_percent)
        thread.progress_message.connect(self._on_download_progress)
        thread.sig_finished.connect(self._on_download_finished)
        thread.error.connect(self._on_download_error)

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

    @Slot(str, int)
    def _on_download_percent(self, model_id: str, pct: int) -> None:
        """[UI-2025] Прогресс скачивания — в прогресс-бар И в строку статуса."""
        self._download_percent[model_id] = pct

        # ↓↓↓ НОВОЕ: реальный прогресс в прогресс-бар (а не marquee) ↓↓↓
        if self.progress_bar.maximum() != 100:
            self.progress_bar.setRange(0, 100)   # защита: после verify мог быть другой диапазон
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(max(0, min(100, int(pct))))
        # ↑↑↑

        # Старое: зелёная заливка в ячейке «Статус» таблицы.
        from PySide6.QtGui import QBrush, QColor
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            if name_item is None:
                continue
            if name_item.data(Qt.ItemDataRole.UserRole) != model_id:
                continue
            status_item = self.table.item(row, 6)
            if status_item is None:
                continue
            status_item.setText(f"⏬ Скачивание ({pct}%)")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            alpha = int(180 * pct / 100)
            status_item.setBackground(QBrush(QColor(80, 180, 80, alpha)))
            break

    def _on_download_progress(self, _model_id: str, message: str) -> None:
        # hf_hub_download не отдает гранулярный прогресс легко,
        # поэтому показываем статус в лейбле
        self.status_label.setText(f"⏬ {message}")

    def _on_download_finished(self, model_id: str, success: bool):
        self._download_thread = None
        self._download_percent.pop(model_id, None)
        if success:
            logger.info(f"Модель {model_id} успешно скачана (SHA256 OK).")
        self._process_queue()
        self._populate()

    def _on_download_error(self, model_id: str, error: str):
        self._download_thread = None
        self._download_percent.pop(model_id, None)
        QMessageBox.warning(
            self,
            "Ошибка скачивания",
            f"Не удалось скачать {model_id}:\n\n{error}\n\n"
            f"При следующей попытке скачивание продолжится с места обрыва.",
        )
        self._process_queue()
        self._populate()

    def _delete_selected(self):
        info = self._current_model_info()
        if not info:
            return
        file_status = self._status_for(info)
        if file_status == "absent":
            return
        if info.id == self.config.slm.selected_model and file_status == "installed":
            QMessageBox.warning(self, "Активная модель", "Нельзя удалить активную модель.")
            return

        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить {info.display_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
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

        # dlg = HFDialog(self)
        dlg = HFDialog(self, known_models=self.config.slm.available_models)
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

        base_id = (
            re.sub(r"[^a-z0-9._-]+", "-", filename.removesuffix(".gguf").lower()).strip("-") or "custom-model"
        )
        model_id = base_id
        suffix = 2
        while self.config.slm.get_model_by_id(model_id) is not None:
            model_id = f"{base_id}-{suffix}"
            suffix += 1

        tags = list(payload.get("tags") or ["chat"])
        # [UI-2025] Thinking-модель, если в тегах есть "thinking" или "reasoning"
        # (detect_tags проставляет их для R1 / QwQ / Qwen 3).
        is_thinking = "thinking" in tags or "reasoning" in tags

        info = SLMModelInfo(
            id=model_id,
            repo=repo_id,
            file=filename,
            display_name=str(payload.get("display_name") or filename.removesuffix(".gguf")),
            size_mb=int(payload.get("size_mb") or 0),
            kind="chat",
            n_ctx=0,
            tags=tags,
            description=str(payload.get("description") or "")[:500],
            is_thinking=is_thinking,
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
        existing = next(
            (m for m in self.config.slm.available_models if m.repo == repo_id and m.file == filename), None
        )
        if existing is None:
            self._on_hf_add_to_catalog(payload)
            existing = next(
                (m for m in self.config.slm.available_models if m.repo == repo_id and m.file == filename),
                None,
            )

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
        if not ok or not repo.strip():
            return
        file, ok = QInputDialog.getText(self, "Своя модель", "Имя файла (.gguf):")
        if not ok or not file.strip() or not file.lower().endswith(".gguf"):
            return
        display_name, ok = QInputDialog.getText(self, "Своя модель", "Имя:", text=file.removesuffix(".gguf"))
        if not ok or not display_name.strip():
            return

        base_id = (
            re.sub(r"[^a-z0-9._-]+", "-", file.removesuffix(".gguf").lower()).strip("-") or "custom-model"
        )
        model_id = base_id
        suffix = 2
        while self.config.slm.get_model_by_id(model_id) is not None:
            model_id = f"{base_id}-{suffix}"
            suffix += 1

        new_info = SLMModelInfo(
            id=model_id, repo=repo.strip(), file=file.strip(), display_name=display_name.strip(), kind="chat"
        )
        self.config.slm.available_models.append(new_info)
        save_config(self.config)
        save_slm_models(self.config.slm.available_models)
        self._changed = True
        self._populate()

    def changed(self) -> bool:
        return self._changed
