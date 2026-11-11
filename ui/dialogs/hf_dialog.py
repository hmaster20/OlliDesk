"""Диалог поиска моделей на HuggingFace (9.3.1).

Открывается кнопкой «🔍 Поиск HF» из `SLMModelsDialog`.

Три панели:
  * Слева — поиск: QLineEdit + QComboBox фильтра + QComboBox сортировки + QListWidget.
  * Справа сверху — карточка (`model_info`): описание, теги, лицензия, ссылка.
  * Справа снизу — `.gguf`-файлы (`HFGgufFile`) + кнопка «⬇️ Скачать» / «➕ Добавить в каталог».

Сеть в фоне (`QThread`). Оффлайн — диалог показывает сообщение и не падает.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from agents.hf_search import (
    HFModel,
    HFModelDetails,
    HFUnavailableError,
    detect_tags,
    model_info,
    search_models,
)


# ---------------------------------------------------------------------------
# Фоновые потоки
# ---------------------------------------------------------------------------


class _SearchThread(QThread):
    """Фоновый `search_models`."""

    done = Signal(list)  # list[HFModel]
    error = Signal(str)

    def __init__(self, query: str, filter_tag: str, api_sort: str, limit: int) -> None:
        super().__init__()
        self._query = query
        self._filter = filter_tag
        self._api_sort = api_sort
        self._limit = limit

    def run(self) -> None:
        try:
            models = asyncio.run(
                search_models(
                    self._query,
                    filter_tag=self._filter,
                    sort=self._api_sort,
                    limit=self._limit,
                )
            )
            self.done.emit(models)
        except HFUnavailableError as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("HF search thread failed")
            self.error.emit(f"{type(e).__name__}: {e}")


class _InfoThread(QThread):
    """Фоновый `model_info`."""

    done = Signal(object)  # HFModelDetails
    error = Signal(str)

    def __init__(self, repo_id: str) -> None:
        super().__init__()
        self._repo_id = repo_id

    def run(self) -> None:
        try:
            details = asyncio.run(model_info(self._repo_id))
            self.done.emit(details)
        except HFUnavailableError as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("HF info thread failed")
            self.error.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Диалог
# ---------------------------------------------------------------------------


class HFDialog(QDialog):
    """Поиск моделей на HuggingFace + скачивание/добавление в каталог."""

    # Эмитится, когда пользователь нажал «Скачать». Payload:
    # ``{"repo_id", "filename", "tags"}`` — MainWindow/SLMModelsDialog
    # запустит download в фоне и добавит в каталог.
    download_requested = Signal(object)
    # Эмитится, когда пользователь нажал «Добавить в каталог».
    add_to_catalog_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_details: HFModelDetails | None = None
        self._search_thread: _SearchThread | None = None
        self._info_thread: _InfoThread | None = None
        self._all_models: list[HFModel] = []

        self.setWindowTitle("Поиск моделей на HuggingFace")
        self.setMinimumSize(1100, 700)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Поиск GGUF-моделей на HuggingFace. Клик по модели → карточка "
            "с описанием и .gguf-файлами. Оффлайн — HF может быть недоступен, "
            "тогда работает локальный каталог (View → Локальные модели)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        # --- Поиск ---
        search_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Например: Qwen2.5-Coder, Llama-3.1, Mistral")
        self.query_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.query_edit, stretch=2)

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("GGUF", "gguf")
        self.filter_combo.addItem("Все", "")
        self.filter_combo.addItem("text-generation", "text-generation")
        search_row.addWidget(self.filter_combo)

        self.sort_combo = QComboBox()
        self.sort_combo.addItem("По скачиваниям", "downloads")
        self.sort_combo.addItem("По лайкам", "likes")
        self.sort_combo.addItem("По дате обновления", "lastModified")
        self.sort_combo.addItem("По издателю (А-Я)", "author")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        search_row.addWidget(self.sort_combo)

        self.search_btn = QPushButton("🔍 Найти")
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        layout.addWidget(self.status_label)

        # --- Сплиттер: список / карточка ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Слева — список результатов.
        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.results_list)

        # Справа — карточка (описание + файлы).
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.details_view = QTextBrowser()
        self.details_view.setOpenExternalLinks(False)
        self.details_view.setOpenLinks(False)
        self.details_view.anchorClicked.connect(lambda url: QDesktopServices.openUrl(QUrl(url.toString())))
        self.details_view.setStyleSheet(
            "QTextBrowser { background: #1e1e2e; color: #d4d4d4;"
            " border: 1px solid #3a3a48; border-radius: 4px; padding: 8px; }"
        )
        right_layout.addWidget(self.details_view, stretch=1)

        files_label = QLabel(".gguf-файлы:")
        files_label.setStyleSheet("color: #888; font-size: 11px; padding: 4px 0 0 0;")
        right_layout.addWidget(files_label)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(2)
        self.files_table.setHorizontalHeaderLabels(["Файл", "Размер"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        right_layout.addWidget(self.files_table, stretch=1)

        actions_row = QHBoxLayout()
        self.download_btn = QPushButton("⬇️ Скачать выбранный файл")
        self.download_btn.clicked.connect(self._on_download)
        self.download_btn.setEnabled(False)
        actions_row.addWidget(self.download_btn)

        self.add_catalog_btn = QPushButton("➕ Добавить в каталог")
        self.add_catalog_btn.setToolTip(
            "Добавить модель в локальный каталог (slm_models.yaml). Скачивание — отдельно, кнопкой «Скачать»."
        )
        self.add_catalog_btn.clicked.connect(self._on_add_to_catalog)
        self.add_catalog_btn.setEnabled(False)
        actions_row.addWidget(self.add_catalog_btn)

        actions_row.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        actions_row.addWidget(close_btn)
        right_layout.addLayout(actions_row)

        splitter.addWidget(right)
        splitter.setSizes([400, 700])
        layout.addWidget(splitter, stretch=1)

    # ------------------------------------------------------------------
    # Слоты
    # ------------------------------------------------------------------

    @Slot()
    def _on_search(self) -> None:
        if self._search_thread is not None and self._search_thread.isRunning():
            return
        query = self.query_edit.text().strip()
        filter_tag = self.filter_combo.currentData() or "gguf"

        ui_sort = self.sort_combo.currentData() or "downloads"
        # HF API не поддерживает сортировку по author (ломается с Bad Request).
        # Поэтому для API мы всегда просим "downloads", а локально сортируем как просил юзер.
        api_sort = ui_sort if ui_sort in ("downloads", "likes", "lastModified") else "downloads"

        self.progress.setVisible(True)
        self.search_btn.setEnabled(False)
        self.status_label.setText("Поиск на HuggingFace…")
        self.results_list.clear()
        self.details_view.clear()
        self.files_table.setRowCount(0)
        self._current_details = None
        self._all_models = []

        self._search_thread = _SearchThread(query, filter_tag, api_sort, limit=50)
        self._search_thread.done.connect(self._on_search_done)
        self._search_thread.error.connect(self._on_search_error)
        self._search_thread.start()

    @Slot(list)
    def _on_search_done(self, models: list[HFModel]) -> None:
        self.progress.setVisible(False)
        self.search_btn.setEnabled(True)
        self._all_models = models
        self._render_results()

    @Slot()
    def _on_sort_changed(self) -> None:
        """Пересортировывает текущий список без повторного запроса."""
        self._render_results()

    def _render_results(self) -> None:
        """Отрисовывает список моделей с учетом локальной сортировки."""
        self.results_list.clear()
        if not self._all_models:
            self.status_label.setText("Ничего не найдено")
            return

        sort_key = self.sort_combo.currentData()

        # Сортировка
        if sort_key == "downloads":
            sorted_models = sorted(self._all_models, key=lambda m: m.downloads, reverse=True)
        elif sort_key == "likes":
            sorted_models = sorted(self._all_models, key=lambda m: m.likes, reverse=True)
        elif sort_key == "lastModified":
            sorted_models = sorted(self._all_models, key=lambda m: m.last_modified, reverse=True)
        elif sort_key == "author":
            # Сортируем по издателю (часть до /)
            sorted_models = sorted(self._all_models, key=lambda m: m.repo_id.lower())
        else:
            sorted_models = self._all_models

        for m in sorted_models:
            tags_str = detect_tags(m.tags, m.pipeline_tag, m.repo_id)
            tag_badge = f" [{'/'.join(tags_str)}]" if tags_str else ""
            item = QListWidgetItem(f"{m.repo_id}{tag_badge}  ⬇ {m.downloads}  ♥ {m.likes}")
            item.setData(Qt.ItemDataRole.UserRole, m.repo_id)
            self.results_list.addItem(item)

        self.status_label.setText(f"Найдено: {len(sorted_models)} моделей")

    @Slot(str)
    def _on_search_error(self, error: str) -> None:
        self.progress.setVisible(False)
        self.search_btn.setEnabled(True)
        self.status_label.setText(f"❌ {error}")
        QMessageBox.warning(self, "HF недоступен", f"Не удалось выполнить поиск:\n{error}")

    @Slot()
    def _on_selection_changed(self) -> None:
        item = self.results_list.currentItem()
        if item is None:
            return
        repo_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(repo_id, str) or not repo_id:
            return
        if self._info_thread is not None and self._info_thread.isRunning():
            return
        self.progress.setVisible(True)
        self.status_label.setText(f"Загрузка карточки {repo_id}…")
        self._info_thread = _InfoThread(repo_id)
        self._info_thread.done.connect(self._on_info_done)
        self._info_thread.error.connect(self._on_info_error)
        self._info_thread.start()

    @Slot(object)
    def _on_info_done(self, details: HFModelDetails) -> None:
        self.progress.setVisible(False)
        self._current_details = details
        self.status_label.setText("")
        # Рендер карточки.
        html = self._render_details_html(details)
        self.details_view.setHtml(html)
        # Файлы.
        self.files_table.setRowCount(len(details.gguf_files))
        for i, f in enumerate(details.gguf_files):
            self.files_table.setItem(i, 0, QTableWidgetItem(f.filename))
            self.files_table.setItem(i, 1, QTableWidgetItem(f.size_human))

        if details.gguf_files:
            self.files_table.selectRow(0)
            self.download_btn.setEnabled(True)
            self.add_catalog_btn.setEnabled(True)
        else:
            self.download_btn.setEnabled(False)
            self.add_catalog_btn.setEnabled(False)

    @Slot(str)
    def _on_info_error(self, error: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText(f"❌ {error}")

    @Slot()
    def _on_download(self) -> None:
        if self._current_details is None:
            return
        row = self.files_table.currentRow()
        if row < 0 or row >= len(self._current_details.gguf_files):
            return
        f = self._current_details.gguf_files[row]
        tags = detect_tags(self._current_details.tags, self._current_details.pipeline_tag, self._current_details.repo_id)
        payload = {"repo_id": self._current_details.repo_id, "filename": f.filename, "tags": tags}
        self.download_requested.emit(payload)

    @Slot()
    def _on_add_to_catalog(self) -> None:
        if self._current_details is None:
            return
        row = self.files_table.currentRow()
        if row < 0 or row >= len(self._current_details.gguf_files):
            row = 0
        f = self._current_details.gguf_files[row]
        tags = detect_tags(self._current_details.tags, self._current_details.pipeline_tag, self._current_details.repo_id)
        payload = {
            "repo_id": self._current_details.repo_id,
            "filename": f.filename,
            "size_mb": f.size_mb,
            "tags": tags,
            "display_name": f"{self._current_details.repo_id.split('/')[-1]} ({f.filename.removesuffix('.gguf')})",
            "description": self._current_details.description[:500],
        }
        self.add_to_catalog_requested.emit(payload)

    # ------------------------------------------------------------------
    # Рендер
    # ------------------------------------------------------------------

    @staticmethod
    def _render_details_html(details: HFModelDetails) -> str:
        tags = detect_tags(details.tags, details.pipeline_tag, details.repo_id)
        tags_str = ", ".join(tags) if tags else "—"
        license_str = details.license or "—"
        description = details.description.replace("\n", "<br>") if details.description else "(нет описания)"
        return f"""
        <h3>{details.repo_id}</h3>
        <p>
            <b>Автор:</b> {details.author or "—"}<br>
            <b>Лицензия:</b> {license_str}<br>
            <b>Скачиваний:</b> {details.downloads} · <b>Лайков:</b> {details.likes}<br>
            <b>Pipeline:</b> {details.pipeline_tag or "—"}<br>
            <b>Теги:</b> {tags_str}<br>
            <b>Обновлено:</b> {details.last_modified or "—"}<br>
            <b>Ссылка:</b> <a href="{details.url}">{details.url}</a>
        </p>
        <hr>
        <p>{description}</p>
        """
