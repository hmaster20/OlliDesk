"""Предпросмотр извлечённого текста из документа (9.4.8).

Перед прикреплением PDF/DOCX/… к чату пользователь видит, что распознано:
полный текст (обрезан до 20K символов), количество страниц, использовался
ли OCR. Кнопки:

  * «Прикрепить» — текст уходит в `AttachedFileStore` (в контекст пойдёт
    превью + доступ через `read_attached_file`).
  * «Отмена» — файл не прикрепляется.

Если OCR не запускался (`use_ocr=False`), а текста нет — предложить
запустить OCR (для сканов). Кнопка «Запустить OCR» перезапускает извлечение
с `use_ocr=True` (дорого, но пользователь явно подтвердил).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fs.document_extractors import ExtractResult, extract_document

# Сколько символов показывать в предпросмотре. Полный текст может быть
# сотни тысяч символов — в QPlainTextEdit это тяжело для UI.
_PREVIEW_CHARS = 20_000


class DocumentPreviewDialog(QDialog):
    """Диалог предпросмотра извлечённого текста из документа."""

    text_approved = Signal(str, dict)  # extracted_text, meta

    def __init__(
        self,
        path: Path,
        result: ExtractResult,
        *,
        with_ocr_available: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._result = result
        self._with_ocr_available = with_ocr_available
        self._approved_text: str = ""

        self.setWindowTitle(f"Документ: {path.name}")
        self.setMinimumSize(720, 560)
        self._build_ui(result)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, result: ExtractResult) -> None:
        layout = QVBoxLayout(self)

        header = QLabel(f"📄 <b>{self._path.name}</b>")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet("font-size: 14px; padding: 2px 0;")
        layout.addWidget(header)

        # Мета-строка: страниц, тип, OCR, ошибка.
        meta_parts: list[str] = []
        if result.pages:
            meta_parts.append(f"страниц: {result.pages}")
        if result.kind:
            meta_parts.append(f"тип: {result.kind}")
        if result.ocr_used:
            meta_parts.append("OCR: да")
        else:
            meta_parts.append("OCR: нет")
        meta_parts.append(f"символов: {len(result.text)}")
        meta_label = QLabel(" · ".join(meta_parts))
        meta_label.setStyleSheet("color: #888; font-size: 11px; padding: 0 0 8px 0;")
        layout.addWidget(meta_label)

        # Ошибка/предупреждение.
        if result.error:
            err = QLabel(f"⚠️ {result.error}")
            err.setWordWrap(True)
            err.setStyleSheet(
                "color: #e5c07b; background: rgba(229,192,123,0.1);"
                " padding: 6px 10px; border-radius: 4px;"
            )
            layout.addWidget(err)

        # Текст.
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Cascadia Mono", 10))
        self.text_edit.setStyleSheet(
            "QPlainTextEdit { background: #1e1e2e; color: #d4d4d4;"
            " border: 1px solid #3a3a48; border-radius: 4px; padding: 6px; }"
        )
        preview = result.text
        if len(preview) > _PREVIEW_CHARS:
            preview = (
                preview[:_PREVIEW_CHARS]
                + f"\n\n… [предпросмотр обрезан, всего {len(result.text)} символов]"
            )
        self.text_edit.setPlainText(preview or "(пусто — распознать не удалось)")
        layout.addWidget(self.text_edit, stretch=1)

        # Кнопки.
        buttons = QHBoxLayout()

        # OCR-кнопка — только если OCR не запускался и текста нет.
        self._ocr_btn: QPushButton | None = None
        if self._with_ocr_available and not result.ocr_used and not result.text:
            self._ocr_btn = QPushButton("🔍 Запустить OCR")
            self._ocr_btn.setToolTip(
                "Распознать текст через rapidocr-onnxruntime (дорого: "
                "секунды на страницу). Требует pdf2image + Poppler."
            )
            self._ocr_btn.clicked.connect(self._run_ocr)
            buttons.addWidget(self._ocr_btn)

        buttons.addStretch()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self._attach_btn = QPushButton("📎 Прикрепить")
        self._attach_btn.setDefault(True)
        self._attach_btn.setStyleSheet(
            "QPushButton { padding: 6px 16px; background: #d4a373;"
            " color: #1e1e1e; border: none; border-radius: 4px;"
            " font-weight: 600; }"
            " QPushButton:hover { background: #e0b88a; }"
            " QPushButton:disabled { background: #555; color: #888; }"
        )
        self._attach_btn.clicked.connect(self._on_attach)
        self._attach_btn.setEnabled(bool(result.text))
        buttons.addWidget(self._attach_btn)

        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _run_ocr(self) -> None:
        """Перезапускает извлечение с `use_ocr=True`."""
        if self._ocr_btn is None:
            return
        self._ocr_btn.setEnabled(False)
        self._ocr_btn.setText("⏳ OCR…")
        try:
            self._result = extract_document(self._path, use_ocr=True)
        except Exception as e:
            logger.exception("OCR failed")
            QMessageBox.critical(
                self, "Ошибка OCR",
                f"Не удалось запустить OCR:\n{e}\n\n"
                "Проверьте, что установлены `pdf2image` + Poppler."
            )
            self._ocr_btn.setEnabled(True)
            self._ocr_btn.setText("🔍 Запустить OCR")
            return

        # Обновляем UI (пересоздаём весь layout — проще, чем патчить по частям).
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
        self._build_ui(self._result)

        if not self._result.text:
            QMessageBox.information(
                self, "OCR",
                "OCR не смог распознать текст. Проверьте качество скана."
            )

    def _on_attach(self) -> None:
        if not self._result.text:
            return
        meta = {
            "kind": self._result.kind,
            "pages": self._result.pages,
            "ocr_used": self._result.ocr_used,
        }
        self._approved_text = self._result.text
        self.text_approved.emit(self._result.text, meta)
        self.accept()

    def approved_text(self) -> str:
        """Текст, который пользователь согласился прикрепить (после accept())."""
        return self._approved_text


__all__ = ["DocumentPreviewDialog"]