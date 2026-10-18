"""Диалог управления локальными SLM-моделями (GGUF, llama.cpp)."""

import re

from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
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

    Args:
        thread: Запускаемый поток скачивания.
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

    ВАЖНО: пользовательский сигнал называется ``sig_finished``, а не
    ``finished`` — иначе он переопределил бы встроенный ``QThread.finished``,
    и Qt не смог бы корректно вызвать внутренние обработчики жизненного цикла
    потока (в частности, очистку из ``_register_download``).
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

        self.setWindowTitle("Локальные модели (SLM)")
        self.setMinimumSize(900, 640)

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
            "Список сортируется: активная → установленные → остальные.<br>"
            "Скачивание продолжается в фоне даже после закрытия окна."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        # --- Двухмодельный пайплайн: явный выбор ролей ---
        pipeline_group = QGroupBox("Двухмодельный пайплайн (planner + analyst)")
        pipeline_form = QFormLayout(pipeline_group)

        self.planner_combo = QComboBox()
        self.planner_combo.setMinimumWidth(360)
        self.planner_combo.setToolTip(
            "Модель для planner-роли (GPU): classify/plan/critique.\n"
            "Рекомендуется 3B или 1.5B — должна влезать в VRAM целиком.\n"
            "«(автовыбор)» — используется tier-1 модель, проходящая по VRAM."
        )
        self.planner_combo.currentIndexChanged.connect(self._on_pipeline_role_changed)
        pipeline_form.addRow("Planner (GPU):", self.planner_combo)

        self.analyst_combo = QComboBox()
        self.analyst_combo.setMinimumWidth(360)
        self.analyst_combo.setToolTip(
            "Модель для analyst-роли (CPU): synthesis/regen.\n"
            "Рекомендуется 7B или 14B — качество важнее скорости.\n"
            "«(автовыбор)» — используется tier-2 модель, проходящая по RAM."
        )
        self.analyst_combo.currentIndexChanged.connect(self._on_pipeline_role_changed)
        pipeline_form.addRow("Analyst (CPU):", self.analyst_combo)

        pipeline_hint = QLabel(
            "<i>Выбор сохраняется в config.yaml (slm.planner_model_id / "
            "slm.analyst_model_id). Если не выбрано — используется автовыбор.</i>"
        )
        pipeline_hint.setWordWrap(True)
        pipeline_hint.setStyleSheet("color: #888; font-size: 11px;")
        pipeline_form.addRow("", pipeline_hint)

        layout.addWidget(pipeline_group)

        # --- Таблица всех chat-моделей ---
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

        self.auto_select_check = QCheckBox("🔁 Автовыбор модели при запуске (по железу и свободной VRAM)")
        self.auto_select_check.setChecked(self.config.slm.auto_select_model)
        self.auto_select_check.setToolTip(
            "Если выключено — при старте приложения загружается модель, "
            "явно выбранная в этом окне (поле slm.selected_model в конфиге). "
            "Если включено — модель может быть подменена автоматически "
            "по свободной VRAM и RAM."
        )
        self.auto_select_check.toggled.connect(self._on_auto_select_toggled)
        layout.addWidget(self.auto_select_check)

        btn_layout = QHBoxLayout()

        self.download_btn = QPushButton("⬇️ Скачать")
        self.download_btn.clicked.connect(self._download_selected)
        btn_layout.addWidget(self.download_btn)

        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self.delete_btn)

        self.activate_btn = QPushButton("✅ Сделать активной")
        self.activate_btn.setToolTip(
            "Назначить выделенную модель активной chat-моделью (single-model). "
            "После этого она загружается при перезапуске — автовыбор её не подменяет."
        )
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

        from agents.download_slm import is_model_installed

        def _sort_key(m: SLMModelInfo) -> tuple[int, int, str]:
            active = 0 if m.id == self.config.slm.selected_model else 1
            installed = 0 if is_model_installed(m.file) else 1
            return (active, installed, m.display_name.lower())

        chat_models = sorted(self.config.slm.get_chat_models(), key=_sort_key)
        if not chat_models:
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

            installed = is_model_installed(m.file)
            status_item = QTableWidgetItem("✅ Установлена" if installed else "⬇️ Не скачана")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, status_item)

            active = m.id == self.config.slm.selected_model
            active_item = QTableWidgetItem("● Да" if active else "○ Нет")
            active_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, active_item)

            real_size_item = QTableWidgetItem(self._real_size_str(m.file))
            real_size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, real_size_item)

        # Embed-модель — отдельной строкой ниже.
        embed_file = self.config.slm.embed_model_file
        embed_installed = is_model_installed(embed_file)
        embed_status = "✅ установлена" if embed_installed else "⬇️ не скачана"
        self.embed_label.setText(f"📎 {embed_file} — {embed_status}")

        # Комбобоксы planner/analyst.
        self._populate_pipeline_roles()

        self._update_buttons()

    def _populate_pipeline_roles(self) -> None:
        """Заполняет комбобоксы planner/analyst (только chat-модели).

        Первый элемент — «(автовыбор)» с data="" (пустая строка), остальные —
        по одной строке на каждую chat-модель. Установленные модели помечены
        префиксом ✅ (можно выбрать), неустановленные — ⬇️ (выбор сохранится,
        но пайплайн стартует только после скачивания).
        """
        chat_models = self.config.slm.get_chat_models()
        from agents.download_slm import is_model_installed

        # Сортируем: сначала установленные, внутри — по tier desc, затем по имени.
        sorted_models = sorted(
            chat_models,
            key=lambda m: (
                0 if is_model_installed(m.file) else 1,
                -m.tier,
                m.display_name.lower(),
            ),
        )

        for combo, current_id in (
            (self.planner_combo, self.config.slm.planner_model_id),
            (self.analyst_combo, self.config.slm.analyst_model_id),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(автовыбор)", "")
            for m in sorted_models:
                installed = is_model_installed(m.file)
                prefix = "✅ " if installed else "⬇️ "
                combo.addItem(f"{prefix}{m.display_name}", m.id)

            idx = combo.findData(current_id) if current_id else 0
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _on_pipeline_role_changed(self, _index: int) -> None:
        """Сохраняет выбор planner/analyst в конфиг (и в YAML).

        Защита: нельзя назначить одну и ту же модель на обе роли. Если
        пользователь попытается — показываем предупреждение и откатываем
        выбор в комбобоксе на предыдущее валидное значение.
        """
        planner_id = self.planner_combo.currentData() or ""
        analyst_id = self.analyst_combo.currentData() or ""

        if planner_id and analyst_id and planner_id == analyst_id:
            QMessageBox.warning(
                self,
                "Одна модель на две роли",
                "Planner и analyst должны быть РАЗНЫМИ моделями — иначе "
                "двухмодельный пайплайн бесполезен (две копии одной модели "
                "съедят вдвое больше памяти).\n\n"
                "Выбор не сохранён. Выберите другую модель для одной из ролей.",
            )
            # Откатываем последний комбобокс к предыдущему валидному значению.
            for combo, prev_id in (
                (self.planner_combo, self.config.slm.planner_model_id),
                (self.analyst_combo, self.config.slm.analyst_model_id),
            ):
                current = combo.currentData() or ""
                if current and current == (analyst_id if combo is self.planner_combo else planner_id):
                    combo.blockSignals(True)
                    idx = combo.findData(prev_id) if prev_id else 0
                    combo.setCurrentIndex(idx if idx >= 0 else 0)
                    combo.blockSignals(False)
            return

        if self.config.slm.planner_model_id == planner_id and self.config.slm.analyst_model_id == analyst_id:
            return

        self.config.slm.planner_model_id = planner_id
        self.config.slm.analyst_model_id = analyst_id
        save_config(self.config)
        self._changed = True
        logger.info(f"SLM pipeline: planner={planner_id or '(авто)'}, analyst={analyst_id or '(авто)'}")
        self.status_label.setText(
            f"📌 Pipeline: planner={planner_id or '(авто)'}, analyst={analyst_id or '(авто)'}"
        )

    def _real_size_str(self, filename: str) -> str:
        """Реальный размер на диске. Не ходит в HF."""
        from agents.download_slm import get_local_size_mb

        target_dir = get_models_dir()
        merged = target_dir / filename
        if merged.exists():
            size = get_local_size_mb(filename)
            return f"{size} МБ" if size > 0 else "—"

        # Split: считаем все части.
        stem = filename.removesuffix(".gguf")
        parts = sorted(target_dir.glob(f"{stem}-*-of-*.gguf"))
        if not parts:
            return "—"
        total = sum(p.stat().st_size for p in parts)
        return f"{total // (1024 * 1024)} МБ ({len(parts)} частей)"

    # ---------- helpers ----------

    def _current_model_info(self) -> SLMModelInfo | None:
        """SLMModelInfo выделенной строки (только chat-модели)."""
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
        """True, если сейчас идёт любое скачивание.

        Используется как барьер для запуска второго параллельного скачивания:
        два одновременных ``_DownloadThread`` перетирали бы
        ``self._download_thread`` и могли уронить приложение.
        """
        return self._download_thread is not None and self._download_thread.isRunning()

    def _downloading_model_id(self) -> str | None:
        """ID модели, которая сейчас скачивается, или ``None``."""
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
        any_downloading = self._is_downloading()
        downloading_id = self._downloading_model_id()

        if not info:
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.activate_btn.setEnabled(False)
            return

        installed = is_model_installed(info.file, repo_id=info.repo)
        active = info.id == self.config.slm.selected_model
        is_currently_downloading = downloading_id == info.id

        # Скачать: только если не качается ничего другого и модель ещё не скачана.
        self.download_btn.setEnabled(not any_downloading and not installed and not is_currently_downloading)

        # Удалить: не активную, не скачиваемую сейчас, и установленную.
        self.delete_btn.setEnabled(installed and not active and not is_currently_downloading)

        # Активировать: только установленную и не активную.
        # Скачивание других моделей — не помеха.
        self.activate_btn.setEnabled(installed and not active)

    # ---------- actions ----------

    def _download_selected(self):
        if self._is_downloading():
            QMessageBox.information(
                self,
                "Скачивание уже идёт",
                "Дождитесь завершения текущего скачивания. Оно продолжится в фоне даже после закрытия окна.",
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
        if self._downloading_model_id() == info.id:
            QMessageBox.information(
                self,
                "Модель скачивается",
                "Эта модель сейчас скачивается. Дождитесь завершения, чтобы удалить её.",
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

    def _on_auto_select_toggled(self, checked: bool) -> None:
        """Сохраняет решение пользователя по автовыбору модели.

        При снятии галочки автовыбор выключается навсегда, пока пользователь
        снова его не включит здесь или не поправит config.yaml вручную.
        """
        self.config.slm.auto_select_model = checked
        save_config(self.config)
        self._changed = True
        logger.info(f"SLM: auto_select_model = {checked} (selected_model='{self.config.slm.selected_model}')")
        if checked:
            self.status_label.setText("🔁 Автовыбор включён — модель может подменяться при запуске.")
        else:
            self.status_label.setText(
                "📌 Автовыбор выключен — при запуске загрузится текущая выбранная модель."
            )

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

        # Явный выбор пользователя — выключаем автовыбор, чтобы он не подменил
        # модель при следующем запуске (баг: q6_k → q4_k_m после рестарта).
        auto_disabled = False
        if self.config.slm.auto_select_model:
            self.config.slm.auto_select_model = False
            auto_disabled = True
            # Синхронизируем UI чекбокса без повторного save_config.
            self.auto_select_check.blockSignals(True)
            self.auto_select_check.setChecked(False)
            self.auto_select_check.blockSignals(False)
            logger.info(f"SLM: пользователь явно выбрал '{info.id}' — auto_select_model переключён в False")

        save_config(self.config)
        self._changed = True

        msg = f"✅ Активная модель: {info.display_name}"
        if auto_disabled:
            msg += " (автовыбор отключён — выбор сохранится при перезапуске)"
        self.status_label.setText(msg)
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

        logger.info(f"SLM: добавлена пользовательская модель id={model_id!r}, repo={repo!r}, file={file!r}")
        self.status_label.setText(f"➕ Добавлена: {display_name}. Скачайте её кнопкой «⬇️ Скачать».")
        self._populate()

    def _set_busy(self, busy: bool, message: str):
        """Показывает/скрывает прогресс-бар и управляет кнопкой «Скачать».

        Остальные кнопки пересчитывает ``_update_buttons()`` — он учитывает
        и статус скачивания, и выделение в таблице. Здесь мы не форсим
        выключение delete/activate: пользователь должен иметь возможность
        активировать или удалить *другие* модели, пока идёт скачивание.
        """
        self.progress_bar.setVisible(busy)
        # Дополнительно блокируем «Своя модель», чтобы нельзя было изменить
        # список во время активного скачивания — это сбило бы пересчёт строк.
        self.add_btn.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
        self._update_buttons()

    # ---------- public ----------

    def changed(self) -> bool:
        """True, если пользователь переключил активную модель или добавил свою."""
        return self._changed
