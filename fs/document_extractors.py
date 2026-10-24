"""Извлечение текста из документов для профиля «Исследование» (9.4).

Поддерживает: PDF (цифровой + сканы через OCR), DOCX, PPTX, XLSX, RTF.

Ключевые принципы:
  * тяжёлые зависимости (`pdfplumber`, `python-docx`, `python-pptx`,
    `openpyxl`, `striprtf`, `rapidocr-onnxruntime`) импортируются ЛЕНИВО
    внутри функций — старт приложения и импорт `fs.indexer` не тянут их;
  * отсутствие библиотеки не падает, а логируется и даёт пустой результат
    (`ExtractResult.text == ""`), чтобы индексация продолжалась;
  * для PDF-сканов — OCR через `rapidocr-onnxruntime` (уже в зависимостях).
    OCR включается явно (`use_ocr=True`), потому что дорого (секунды на
    страницу). По умолчанию выключен: сначала пробуем извлечь текстовый
    слой, если пустой — вызывающий код решает, запускать ли OCR.

Интерфейс:
  * `extract_document(path, use_ocr=False) -> ExtractResult`.
  * `ExtractResult` — dataclass: `text`, `pages`, `kind`, `error`, `ocr_used`.
  * `SUPPORTED_EXTENSIONS` — множество расширений (с точкой), для индексатора.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Расширения, которые обрабатывает этот модуль.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".rtf"})

# Соответствие расширение → язык (для метаданных чанка).
DOCUMENT_LANGUAGES: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".rtf": "rtf",
}


@dataclass
class ExtractResult:
    """Результат извлечения текста из документа."""

    text: str
    pages: int = 0
    kind: str = "unknown"
    error: str = ""
    ocr_used: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


def is_document(path: Path | str) -> bool:
    """True, если расширение файла поддерживается экстрактором."""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def language_for_document(path: Path | str) -> str:
    """Имя «языка» для метаданных чанка (pdf/docx/…)."""
    return DOCUMENT_LANGUAGES.get(Path(path).suffix.lower(), "text")


def extract_document(path: Path, *, use_ocr: bool = False) -> ExtractResult:
    """Извлекает текст из документа по расширению."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(path, use_ocr=use_ocr)
        if ext == ".docx":
            return _extract_docx(path)
        if ext == ".pptx":
            return _extract_pptx(path)
        if ext == ".xlsx":
            return _extract_xlsx(path)
        if ext == ".rtf":
            return _extract_rtf(path)
    except Exception as e:
        logger.warning(f"Документ {path.name}: ошибка извлечения — {e}")
        return ExtractResult(text="", kind=ext.lstrip("."), error=str(e))
    return ExtractResult(text="", kind=ext.lstrip("."), error=f"unsupported extension: {ext}")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _extract_pdf(path: Path, *, use_ocr: bool) -> ExtractResult:
    """PDF: текстовый слой через pdfplumber → pypdf; OCR — если пусто и use_ocr."""
    text_parts: list[str] = []
    pages = 0
    kind = "pdf"

    # 1. pdfplumber — лучше сохраняет структуру (абзацы, таблицы).
    try:
        import pdfplumber  # type: ignore[import-not-found]

        with pdfplumber.open(str(path)) as pdf:
            pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                if page_text:
                    text_parts.append(f"\n\n--- стр. {i} ---\n\n{page_text}")
    except ImportError:
        logger.debug("pdfplumber недоступен, пробую pypdf")
    except Exception as e:
        logger.warning(f"pdfplumber упал на {path.name}: {e}")

    # 2. Fallback: pypdf.
    if not text_parts:
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(str(path))
            pages = len(reader.pages)
            for i, page in enumerate(reader.pages, 1):
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    text_parts.append(f"\n\n--- стр. {i} ---\n\n{page_text}")
        except ImportError:
            logger.debug("pypdf недоступен")
        except Exception as e:
            logger.warning(f"pypdf упал на {path.name}: {e}")

    text = "".join(text_parts).strip()

    # 3. OCR — только если задан и текстовый слой пуст.
    if use_ocr and not text:
        ocr_text = _ocr_pdf(path)
        if ocr_text:
            return ExtractResult(text=ocr_text, pages=pages, kind=kind, ocr_used=True)

    if not text:
        return ExtractResult(
            text="",
            pages=pages,
            kind=kind,
            error="PDF не содержит текстового слоя (возможно, скан). "
            "Включите OCR в настройках профиля «Исследование».",
        )
    return ExtractResult(text=text, pages=pages, kind=kind)


def _ocr_pdf(path: Path) -> str:
    """OCR PDF через rapidocr-onnxruntime + pdf2image (Poppler).

    Требует: `rapidocr-onnxruntime`, `pdf2image`, установленный Poppler.
    Ошибки не пробрасываются — возвращается пустая строка.
    """
    try:
        import numpy as np
        from pdf2image import convert_from_path  # type: ignore[import-not-found]
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        logger.info(f"OCR PDF недоступен ({e}): нужны rapidocr-onnxruntime, pdf2image, poppler")
        return ""

    try:
        engine = RapidOCR()
        pages = convert_from_path(str(path), dpi=200)
    except Exception as e:
        logger.warning(f"OCR {path.name}: не удалось подготовить страницы — {e}")
        return ""

    parts: list[str] = []
    for i, pil_page in enumerate(pages, 1):
        try:
            img = np.array(pil_page)
            result, _ = engine(img)
        except Exception as e:
            logger.warning(f"OCR {path.name}: страница {i} упала — {e}")
            continue
        if not result:
            continue
        page_text = "\n".join(str(item[1]) for item in result)
        if page_text.strip():
            parts.append(f"\n\n--- стр. {i} (OCR) ---\n\n{page_text}")
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# DOCX / PPTX / XLSX / RTF
# ---------------------------------------------------------------------------


def _extract_docx(path: Path) -> ExtractResult:
    """DOCX: python-docx, по абзацам; таблицы — отдельно (rows → "cell | cell")."""
    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError:
        return ExtractResult(
            text="",
            kind="docx",
            error="python-docx не установлен. `pip install python-docx`.",
        )

    doc = Document(str(path))
    parts: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    for ti, table in enumerate(doc.tables, 1):
        parts.append(f"\n\n--- таблица {ti} ---")
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            parts.append(" | ".join(cells))

    text = "\n\n".join(parts).strip()
    return ExtractResult(text=text, kind="docx", pages=0)


def _extract_pptx(path: Path) -> ExtractResult:
    """PPTX: python-pptx; слайды нумеруются, текст с shape."""
    try:
        from pptx import Presentation  # type: ignore[import-not-found]
    except ImportError:
        return ExtractResult(
            text="",
            kind="pptx",
            error="python-pptx не установлен. `pip install python-pptx`.",
        )

    prs = Presentation(str(path))
    parts: list[str] = []
    for si, slide in enumerate(prs.slides, 1):
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                if txt:
                    slide_texts.append(txt)
        if slide_texts:
            parts.append(f"\n\n--- слайд {si} ---\n\n" + "\n\n".join(slide_texts))

    text = "".join(parts).strip()
    return ExtractResult(text=text, kind="pptx", pages=len(prs.slides))


def _extract_xlsx(path: Path) -> ExtractResult:
    """XLSX: openpyxl, каждый лист — отдельная секция, row → " | cell"."""
    try:
        from openpyxl import load_workbook  # type: ignore[import-not-found]
    except ImportError:
        return ExtractResult(
            text="",
            kind="xlsx",
            error="openpyxl не установлен. `pip install openpyxl`.",
        )

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        parts.append(f"\n\n--- лист: {sheet_name} ---")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None]
            if cells:
                parts.append(" | ".join(cells))
    wb.close()

    text = "".join(parts).strip()
    return ExtractResult(text=text, kind="xlsx", pages=len(wb.sheetnames))


def _extract_rtf(path: Path) -> ExtractResult:
    """RTF: striprtf; fallback — простой regex-парсер."""
    try:
        from striprtf.striprtf import rtf_to_text  # type: ignore[import-not-found]
    except ImportError:
        # Fallback: читаем как текст, вычищаем RTF-команды.
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ExtractResult(text="", kind="rtf", error=str(e))
        import re

        plain = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", raw)
        plain = plain.replace("{", "").replace("}", "")
        return ExtractResult(text=plain.strip(), kind="rtf")

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ExtractResult(text="", kind="rtf", error=str(e))

    try:
        text = rtf_to_text(raw, errors="ignore").strip()
    except Exception as e:
        return ExtractResult(text="", kind="rtf", error=str(e))

    return ExtractResult(text=text, kind="rtf")


__all__ = [
    "DOCUMENT_LANGUAGES",
    "ExtractResult",
    "SUPPORTED_EXTENSIONS",
    "extract_document",
    "is_document",
    "language_for_document",
]
