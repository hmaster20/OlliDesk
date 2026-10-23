"""Диалог замеров производительности SLM/Ollama (4.7.B).

Возможности:
  * выбор моделей (SLM — установленные, Ollama — локальные);
  * стандартный набор промптов / пользовательский;
  * повтор (для усреднения);
  * прогресс;
  * таблица результатов (генерация + загрузка);
  * экспорт CSV / Markdown (для `docs/BENCHMARK.md`).

Прогон — в `_BenchmarkThread` (QThread), GUI не блокируется.
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agents.benchmark import (
    BENCHMARK_PROMPTS,
    BenchmarkPrompt,
    BenchmarkResult,
    save_csv,
    to_markdown,
)
from core.config import AppConfig


class BenchmarkDialog(QDialog):
    """Диалог замеров SLM/Ollama."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self._config = config
        self._thread = None
        self._last_result: BenchmarkResult | None = None
        self._model_checkboxes: dict[str, QCheckBox] = {}
        self._model_specs: dict[str, dict] = {}

        self.setWindowTitle("Замер производительности моделей")
        self.setMinimumSize(960, 720)
        self._build_ui()
        self._populate_models()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Прогон моделей последовательно: загрузка → промпты × повтор → "
            "выгрузка. Результаты показывают реальные <b>TTFT</b> и "
            "<b>tok/s</b> на вашем железе — пригодятся для выбора моделей, "
            "квантования и <code>n_threads</code>."
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        # --- Модели ---
        models_row = QHBoxLayout()

        slm_box = QWidget()
        slm_layout = QVBoxLayout(slm_box)
        slm_layout.setContentsMargins(0, 0, 0, 0)
        self.slm_label = QLabel("🖥 Локальные SLM (установленные):")
        self.slm_label.setStyleSheet("font-weight: bold; padding: 4px 0;")
        slm_layout.addWidget(self.slm_label)
        self.slm_layout = slm_layout  # используется в _populate_models
        models_row.addWidget(slm_box, stretch=1)

        ollama_box = QWidget()
        ollama_layout = QVBoxLayout(ollama_box)
        ollama_layout.setContentsMargins(0, 0, 0, 0)
        self.ollama_label = QLabel("🌐 Ollama (локальные):")
        self.ollama_label.setStyleSheet("font-weight: bold; padding: 4px 0;")
        ollama_layout.addWidget(self.ollama_label)
        self.ollama_layout = ollama_layout
        models_row.addWidget(ollama_box, stretch=1)

        layout.addLayout(models_row)

        # --- Промпты ---
        self.use_standard_prompts = QCheckBox(
            "Использовать стандартный набор промптов (short / medium / long)"
        )
        self.use_standard_prompts.setChecked(True)
        self.use_standard_prompts.toggled.connect(self._on_prompts_mode_toggled)
        layout.addWidget(self.use_standard_prompts)

        self.custom_prompts_edit = QPlainTextEdit()
        self.custom_prompts_edit.setPlaceholderText(
            "Каждый промпт — на новой строке.\n"
            "Пустая строка игнорируется.\n"
            "Пример:\n"
            "Напиши функцию Hello World\n"
            "Объясни threading vs multiprocessing"
        )
        self.custom_prompts_edit.setVisible(False)
        self.custom_prompts_edit.setMaximumHeight(100)
        layout.addWidget(self.custom_prompts_edit)

        # --- Настройки ---
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Повторов на промпт:"))
        self.repeats_spin = QSpinBox()
        self.repeats_spin.setRange(1, 5)
        self.repeats_spin.setValue(2)
        self.repeats_spin.setToolTip(
            "Повтор замеров для усреднения. Каждый повтор — новая "
            "генерация (не кэшируется)."
        )
        settings_row.addWidget(self.repeats_spin)
        settings_row.addStretch()
        layout.addLayout(settings_row)

        # --- Прогресс и статус ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        layout.addWidget(self.status_label)

        # --- Сводка ---
        self.summary_label = QLabel("")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setStyleSheet(
            "color: #d4d4d4; padding: 6px 8px;"
            " background: rgba(128,128,128,0.05); border-radius: 4px;"
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # --- Таблица ---
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(10)
        self.results_table.setHorizontalHeaderLabels(
            [
                "Модель", "Provider", "Device", "Промпт",
                "TTFT (ms)", "tok/s", "Всего (ms)", "tok",
                "n_threads", "OK",
            ]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self.results_table, stretch=1)

        # --- Кнопки ---
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶ Запустить замеры")
        self.start_btn.setStyleSheet(
            "QPushButton { background: #d4a373; color: #1e1e1e;"
            " font-weight: 600; padding: 8px 20px; border: none;"
            " border-radius: 4px; }"
            " QPushButton:hover { background: #e0b88a; }"
            " QPushButton:disabled { background: #555; color: #888; }"
        )
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.save_csv_btn = QPushButton("💾 CSV")
        self.save_csv_btn.setEnabled(False)
        self.save_csv_btn.clicked.connect(self._save_csv)
        btn_row.addWidget(self.save_csv_btn)

        self.save_md_btn = QPushButton("📄 Markdown")
        self.save_md_btn.setEnabled(False)
        self.save_md_btn.clicked.connect(self._save_markdown)
        btn_row.addWidget(self.save_md_btn)

        btn_row.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Заполнение моделей
    # ------------------------------------------------------------------

    def _populate_models(self) -> None:
        # SLM — установленные.
        try:
            from agents.download_slm import get_model_path, is_model_installed
            from core.config import load_config

            cfg = load_config()
            found = 0
            for info in cfg.slm.get_chat_models():
                if not is_model_installed(info.file):
                    continue
                path = get_model_path(info.file)
                if path is None:
                    continue
                spec = {
                    "id": info.id,
                    "name": info.display_name,
                    "provider": "slm",
                    "path": str(path),
                }
                self._model_specs[info.id] = spec
                cb = QCheckBox(f"🖥 {info.display_name}")
                cb.setToolTip(f"{path.name}\ntier={info.tier}")
                cb.setChecked(True)
                self.slm_layout.addWidget(cb)
                self._model_checkboxes[info.id] = cb
                found += 1
            if found == 0:
                hint = QLabel("(нет установленных SLM-моделей)")
                hint.setStyleSheet("color: #888; font-style: italic;")
                self.slm_layout.addWidget(hint)
        except Exception as e:
            logger.warning(f"Не удалось загрузить SLM-модели: {e}")

        # Ollama — локальные.
        try:
            from core.model_registry import ModelRegistry

            reg = ModelRegistry()
            found = 0
            for name, info in reg.get_all_models().items():
                if not info.is_local:
                    continue
                spec = {
                    "id": name,
                    "name": name,
                    "provider": "ollama",
                    "base_url": "http://localhost:11434",
                }
                self._model_specs[name] = spec
                cb = QCheckBox(f"🌐 {name}")
                cb.setChecked(False)
                self.ollama_layout.addWidget(cb)
                self._model_checkboxes[name] = cb
                found += 1
            if found == 0:
                hint = QLabel("(Ollama не запущен или моделей нет)")
                hint.setStyleSheet("color: #888; font-style: italic;")
                self.ollama_layout.addWidget(hint)
        except Exception as e:
            logger.warning(f"Не удалось загрузить Ollama-модели: {e}")

    # ------------------------------------------------------------------
    # Промпты
    # ------------------------------------------------------------------

    @Slot(bool)
    def _on_prompts_mode_toggled(self, use_standard: bool) -> None:
        self.custom_prompts_edit.setVisible(not use_standard)

    def _parse_custom_prompts(self) -> list[BenchmarkPrompt]:
        """Разбирает пользовательские промпты (по одному на строку)."""
        text = self.custom_prompts_edit.toPlainText()
        prompts: list[BenchmarkPrompt] = []
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            prompts.append(
                BenchmarkPrompt(
                    name=f"custom_{i}",
                    text=stripped,
                    max_tokens=256,
                )
            )
        return prompts

    # ------------------------------------------------------------------
    # Запуск
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        selected_ids = [
            mid
            for mid, cb in self._model_checkboxes.items()
            if cb.isChecked()
        ]
        if not selected_ids:
            QMessageBox.warning(
                self, "Ошибка", "Выберите хотя бы одну модель для замера."
            )
            return

        models = [self._model_specs[mid] for mid in selected_ids]

        if self.use_standard_prompts.isChecked():
            prompts = list(BENCHMARK_PROMPTS)
        else:
            prompts = self._parse_custom_prompts()
            if not prompts:
                QMessageBox.warning(
                    self,
                    "Ошибка",
                    "Введите хотя бы один промпт (по одному на строке).",
                )
                return

        repeats = self.repeats_spin.value()

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.status_label.setText("Запуск…")
        self.start_btn.setEnabled(False)
        self.save_csv_btn.setEnabled(False)
        self.save_md_btn.setEnabled(False)
        self.results_table.setRowCount(0)
        self.summary_label.setText("")

        from ui.chat.threads import _BenchmarkThread

        self._thread = _BenchmarkThread(
            models, prompts=prompts, repeats=repeats
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_finished)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    @Slot(str)
    def _on_progress(self, message: str) -> None:
        self.status_label.setText(message)

    @Slot(str)
    def _on_error(self, error_msg: str) -> None:
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)
        self.status_label.setText(f"❌ {error_msg}")
        QMessageBox.critical(self, "Ошибка замеров", error_msg)

    @Slot(object)
    def _on_finished(self, result: BenchmarkResult) -> None:
        self._last_result = result
        self._fill_table(result)
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)
        self.save_csv_btn.setEnabled(bool(result.rows))
        self.save_md_btn.setEnabled(bool(result.rows))
        self.status_label.setText(
            f"✅ Готово: {len(result.rows)} замеров "
            f"({len(result.ok_rows)} ok), "
            f"{len(result.load_rows)} загрузок"
        )
        self._update_summary(result)

    def _fill_table(self, result: BenchmarkResult) -> None:
        rows = result.rows
        self.results_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            ttft = f"{r.ttft_ms:.0f}" if r.ttft_ms is not None else "—"
            self.results_table.setItem(i, 0, QTableWidgetItem(r.model_name))
            self.results_table.setItem(i, 1, QTableWidgetItem(r.provider))
            self.results_table.setItem(i, 2, QTableWidgetItem(r.device))
            self.results_table.setItem(i, 3, QTableWidgetItem(r.prompt_name))
            self.results_table.setItem(i, 4, QTableWidgetItem(ttft))
            self.results_table.setItem(
                i, 5, QTableWidgetItem(f"{r.tokens_per_sec:.1f}")
            )
            self.results_table.setItem(
                i, 6, QTableWidgetItem(f"{r.total_ms:.0f}")
            )
            self.results_table.setItem(i, 7, QTableWidgetItem(str(r.tokens)))
            self.results_table.setItem(
                i, 8, QTableWidgetItem(str(r.n_threads))
            )
            self.results_table.setItem(
                i, 9, QTableWidgetItem("✅" if r.ok else "❌")
            )

    def _update_summary(self, result: BenchmarkResult) -> None:
        """Средние tok/s и TTFT по каждой модели + стоимость загрузки."""
        from collections import defaultdict

        tps: dict[str, list[float]] = defaultdict(list)
        ttfts: dict[str, list[float]] = defaultdict(list)
        loads: dict[str, list[float]] = defaultdict(list)
        names: dict[str, str] = {}

        for r in result.ok_rows:
            names[r.model_id] = r.model_name
            if r.tokens_per_sec > 0:
                tps[r.model_id].append(r.tokens_per_sec)
            if r.ttft_ms is not None:
                ttfts[r.model_id].append(r.ttft_ms)

        for lr in result.load_rows:
            if lr.ok:
                names.setdefault(lr.model_id, lr.model_name)
                loads[lr.model_id].append(lr.load_ms)

        if not tps:
            self.summary_label.setText("")
            return

        lines = ["<b>Сводка по моделям:</b>", ""]
        for model_id in sorted(tps):
            name = names.get(model_id, model_id)
            avg_tps = sum(tps[model_id]) / len(tps[model_id])
            line = f"• <b>{name}</b>: ~{avg_tps:.1f} tok/s"
            if ttfts[model_id]:
                avg_ttft = sum(ttfts[model_id]) / len(ttfts[model_id])
                line += f", TTFT ~{avg_ttft:.0f} ms"
            if loads[model_id]:
                if len(loads[model_id]) > 1:
                    first = loads[model_id][0]
                    others = loads[model_id][1:]
                    avg_reload = sum(others) / len(others)
                    line += (
                        f"; загрузка: {first:.0f} ms (первая), "
                        f"~{avg_reload:.0f} ms (повторная)"
                    )
                else:
                    line += f"; загрузка: {loads[model_id][0]:.0f} ms"
            lines.append(line)

        self.summary_label.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    # Экспорт
    # ------------------------------------------------------------------

    def _save_csv(self) -> None:
        if self._last_result is None or not self._last_result.rows:
            return
        default = f"benchmark_{self._last_result.started_at[:10]}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV", default, "CSV (*.csv)"
        )
        if not path:
            return
        from pathlib import Path

        try:
            save_csv(self._last_result, Path(path))
            # Рядом сохраняется `<stem>_load.csv` — сообщаем пользователю.
            load_name = Path(path).stem + "_load.csv"
            QMessageBox.information(
                self,
                "Готово",
                f"CSV сохранены:\n{path}\n"
                f"{Path(path).parent / load_name}\n\n"
                "Открыть в Excel/LibreOffice — удобно сравнивать n_threads.",
            )
        except Exception as e:
            logger.exception("save_csv failed")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось сохранить CSV:\n{e}"
            )

    def _save_markdown(self) -> None:
        if self._last_result is None or not self._last_result.rows:
            return
        default = f"benchmark_{self._last_result.started_at[:10]}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить Markdown", default, "Markdown (*.md)"
        )
        if not path:
            return
        from pathlib import Path

        try:
            md = to_markdown(self._last_result)
            Path(path).write_text(md, encoding="utf-8")
            QMessageBox.information(
                self,
                "Готово",
                f"Markdown сохранён:\n{path}\n\n"
                "Скопируйте содержимое в docs/BENCHMARK.md или в PR-описание.",
            )
        except Exception as e:
            logger.exception("save_markdown failed")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось сохранить Markdown:\n{e}"
            )


__all__ = ["BenchmarkDialog"]