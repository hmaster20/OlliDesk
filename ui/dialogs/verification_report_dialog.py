"""Отчёт о проверке целостности моделей (Этап 3.1).

Открывается из SLMModelsDialog после «🔍 Проверить все модели».
Показывает по каждой модели:

  * имя (с 🧠 для thinking);
  * файл;
  * статус: ✅ OK / ⚠️ Битый / ❌ SHA256 / ⬜ Нет на диске;
  * размер (МБ);
  * SHA256 (первые 8 символов expected / actual, если считалось);
  * детали (error, last_verified_at).

Действия:
  * «📥 Перекачать битые» — эмитит `redownload_requested(list[SLMModelInfo])`;
    SLMModelsDialog добавит их в очередь скачивания.
  * «🗑 Удалить битые» — эмитит `delete_broken_requested(list[str])`.
  * «Закрыть».
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import SLMModelInfo


@dataclass
class VerificationRow:
    """Одна строка отчёта (совпадает по форме с тем, что эмитит поток)."""

    model: SLMModelInfo
    status: str  # "ok" | "size_ok" | "broken" | "sha_mismatch" | "absent"
    size_mb: int
    expected_sha: str  # полный hex или ""
    actual_sha: str  # полный hex или ""
    error: str = ""


_STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    # status → (icon, label, color)
    "ok": ("✅", "OK", "#98c379"),
    "size_ok": ("✅", "OK (размер)", "#98c379"),
    "broken": ("⚠️", "Битый файл", "#e5c07b"),
    "sha_mismatch": ("❌", "SHA256 не совпал", "#e06c75"),
    "absent": ("⬜", "Не скачана", "#888888"),
}


class VerificationReportDialog(QDialog):
    """Таблица результатов проверки целостности моделей."""

    redownload_requested = Signal(object)  # list[SLMModelInfo]
    delete_broken_requested = Signal(object)  # list[str] (filename)

    def __init__(
        self,
        rows: list[VerificationRow],
        *,
        sha_was_checked: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        self._sha_was_checked = sha_was_checked
        self.setWindowTitle("Проверка целостности моделей")
        self.setMinimumSize(1000, 560)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        counts = {
            s: sum(1 for r in self._rows if r.status == s)
            for s in ("ok", "size_ok", "broken", "sha_mismatch", "absent")
        }
        summary = (
            f"Всего: <b>{len(self._rows)}</b> · "
            f"✅ OK: <b>{counts['ok'] + counts['size_ok']}</b> · "
            f"⚠️ битых: <b>{counts['broken']}</b> · "
            f"❌ SHA256: <b>{counts['sha_mismatch']}</b> · "
            f"⬜ не скачано: <b>{counts['absent']}</b>"
        )
        info = QLabel(summary)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0 8px 0;")
        layout.addWidget(info)

        if not self._sha_was_checked:
            hint = QLabel(
                "🔍 Режим «Быстрая проверка»: SHA256 не считался (только размер). "
                "Для полной проверки запустите «🔍 Проверить все модели → Полная»."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(
                "color: #e5c07b; background: rgba(229,192,123,0.1);"
                " padding: 6px 10px; border-radius: 4px;"
            )
            layout.addWidget(hint)

        # --- Таблица ---
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Модель", "Файл", "Статус", "Размер, МБ", "SHA256 (expected/actual)", "Детали"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table, stretch=1)

        self._populate_table()

        # --- Кнопки ---
        buttons_row = QHBoxLayout()

        broken_models = [r.model for r in self._rows if r.status == "broken"]
        sha_bad_models = [r.model for r in self._rows if r.status == "sha_mismatch"]
        redownload = broken_models + sha_bad_models

        self.redownload_btn = QPushButton(f"📥 Перекачать битые ({len(redownload)})")
        self.redownload_btn.setEnabled(bool(redownload))
        self.redownload_btn.setToolTip(
            "Удалить битые файлы (размер < 1 МБ или SHA256 не совпал) "
            "и поставить их в очередь скачивания."
        )
        self.redownload_btn.clicked.connect(self._on_redownload)
        buttons_row.addWidget(self.redownload_btn)

        broken_files = [r.model.file for r in self._rows if r.status in ("broken", "sha_mismatch")]
        self.delete_btn = QPushButton(f"🗑 Удалить битые ({len(broken_files)})")
        self.delete_btn.setEnabled(bool(broken_files))
        self.delete_btn.clicked.connect(self._on_delete_broken)
        buttons_row.addWidget(self.delete_btn)

        buttons_row.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons_row.addWidget(close_btn)

        layout.addLayout(buttons_row)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for row, r in enumerate(self._rows):
            icon, label, color = _STATUS_STYLE.get(r.status, ("•", r.status, "#888"))

            name_prefix = "🧠 " if getattr(r.model, "is_thinking", False) else ""
            name_item = QTableWidgetItem(f"{name_prefix}{r.model.display_name}")
            self.table.setItem(row, 0, name_item)

            file_item = QTableWidgetItem(r.model.file)
            file_item.setToolTip(r.model.repo)
            self.table.setItem(row, 1, file_item)

            status_item = QTableWidgetItem(f"{icon} {label}")
            status_item.setForeground(QColor(color))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, status_item)

            size_item = QTableWidgetItem(str(r.size_mb) if r.size_mb else "—")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, size_item)

            if r.expected_sha or r.actual_sha:
                exp = (r.expected_sha or "—")[:8]
                act = (r.actual_sha or "—")[:8]
                sha_text = f"{exp} / {act}"
            else:
                sha_text = "—"
            sha_item = QTableWidgetItem(sha_text)
            sha_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if r.expected_sha and r.actual_sha and r.expected_sha != r.actual_sha:
                sha_item.setForeground(QColor("#e06c75"))
            self.table.setItem(row, 4, sha_item)

            details_parts: list[str] = []
            if r.error:
                details_parts.append(r.error)
            if getattr(r.model, "last_verified_at", ""):
                details_parts.append(f"проверено: {r.model.last_verified_at}")
            details_item = QTableWidgetItem("; ".join(details_parts) or "")
            details_item.setToolTip("\n".join(details_parts))
            self.table.setItem(row, 5, details_item)

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    @Slot()
    def _on_redownload(self) -> None:
        to_redownload = [r.model for r in self._rows if r.status in ("broken", "sha_mismatch")]
        if not to_redownload:
            return
        self.redownload_requested.emit(to_redownload)
        self.accept()

    @Slot()
    def _on_delete_broken(self) -> None:
        files = [r.model.file for r in self._rows if r.status in ("broken", "sha_mismatch")]
        if not files:
            return
        self.delete_broken_requested.emit(files)
        self.accept()


__all__ = ["VerificationReportDialog", "VerificationRow"]