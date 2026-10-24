"""Базовые тесты экстрактора документов (9.4).

Не создаём реальные PDF/DOCX — проверяем только контракт:
  * отсутствующий файл → пустой текст + error;
  * неподдерживаемое расширение → error;
  * is_document / language_for_document работают по расширению.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fs.document_extractors import (
    DOCUMENT_LANGUAGES,
    SUPPORTED_EXTENSIONS,
    extract_document,
    is_document,
    language_for_document,
)


class TestIsDocument:
    @pytest.mark.parametrize("ext", [".pdf", ".docx", ".pptx", ".xlsx", ".rtf"])
    def test_supported(self, ext: str) -> None:
        assert is_document(f"file{ext}")

    @pytest.mark.parametrize("name", ["file.py", "file.md", "file.txt", "file.html"])
    def test_unsupported(self, name: str) -> None:
        assert not is_document(name)

    def test_case_insensitive(self) -> None:
        assert is_document("FILE.PDF")
        assert is_document("Doc.Docx")


class TestLanguageForDocument:
    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".pdf", "pdf"),
            (".docx", "docx"),
            (".pptx", "pptx"),
            (".xlsx", "xlsx"),
            (".rtf", "rtf"),
        ],
    )
    def test_supported(self, ext: str, expected: str) -> None:
        assert language_for_document(f"file{ext}") == expected

    def test_unknown(self) -> None:
        assert language_for_document("file.xyz") == "text"

    def test_all_supported_have_language(self) -> None:
        for ext in SUPPORTED_EXTENSIONS:
            assert ext in DOCUMENT_LANGUAGES


class TestExtractDocumentMissing:
    def test_nonexistent_pdf(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.pdf"
        result = extract_document(path)
        assert not result.ok
        assert result.error != ""
        assert result.text == ""

    def test_unsupported_ext(self, tmp_path: Path) -> None:
        path = tmp_path / "file.xyz"
        path.write_text("hello")
        result = extract_document(path)
        assert not result.ok
        assert "unsupported" in result.error.lower()


class TestExtractDocumentPdfNoText:
    def test_empty_pdf_without_ocr(self, tmp_path: Path) -> None:
        """PDF без текстового слоя: error с подсказкой про OCR."""
        # Создаём минимальный валидный PDF с пустой страницей.
        # Если pdfplumber/pypdf недоступны — тест всё равно пройдёт:
        # error будет содержать "не содержит текстового слоя" или
        # "pdfplumber"/"pypdf" отсутствует.
        path = tmp_path / "empty.pdf"
        path.write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"xref\n"
            b"0 4\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\n"
            b"startxref\n"
            b"190\n"
            b"%%EOF\n"
        )
        result = extract_document(path, use_ocr=False)
        # Либо pdfplumber/pypdf вытащили пустую строку → error про OCR,
        # либо библиотеки недоступны → error про установку.
        # В любом случае текст пустой.
        assert result.text == ""
        assert result.error != ""
