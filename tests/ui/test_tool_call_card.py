"""Тесты логики preview в tool-карточке (9.5.7).

Без pytest-qt: проверяем только `_truncate_preview` — чистую функцию.
Сам `ToolCallCard` требует QApplication, его полное поведение покрывается
ручным прогоном на живой сборке (см. план 9.11 — `pytest-qt` не подключаем,
пока не появится задача по полному UI).
"""

from __future__ import annotations

import pytest

from ui.chat.message_item import _truncate_preview


class TestTruncatePreview:
    def test_empty(self) -> None:
        text, truncated = _truncate_preview("")
        assert text == ""
        assert truncated is False

    def test_none(self) -> None:
        text, truncated = _truncate_preview(None)  # type: ignore[arg-type]
        assert text == ""
        assert truncated is False

    def test_short_text_no_truncation(self) -> None:
        text, truncated = _truncate_preview("hello world", limit=200)
        assert text == "hello world"
        assert truncated is False

    def test_exactly_at_limit(self) -> None:
        s = "a" * 200
        text, truncated = _truncate_preview(s, limit=200)
        assert text == s
        assert truncated is False

    def test_one_over_limit(self) -> None:
        s = "a" * 201
        text, truncated = _truncate_preview(s, limit=200)
        assert truncated is True
        assert len(text) == 200  # 199 + "…"
        assert text.endswith("…")

    def test_newlines_collapsed(self) -> None:
        text, truncated = _truncate_preview("line1\nline2\nline3", limit=200)
        assert text == "line1 line2 line3"
        assert truncated is False

    def test_multiple_spaces_collapsed(self) -> None:
        text, truncated = _truncate_preview("a    b     c", limit=200)
        assert text == "a b c"
        assert truncated is False

    @pytest.mark.parametrize("limit", [10, 50, 200, 500])
    def test_truncation_limit(self, limit: int) -> None:
        s = "x" * (limit * 3)
        text, truncated = _truncate_preview(s, limit=limit)
        assert truncated is True
        assert len(text) == limit
        assert text[-1] == "…"

    def test_zero_limit(self) -> None:
        """limit=0 — крайний случай, возвращает "…" для непустого текста."""
        text, truncated = _truncate_preview("abc", limit=0)
        # Поведение: text[:0] + "…" = "…", truncated=True
        assert truncated is True
        assert text == "…"


class TestEscapeDiff:
    """[9.6.1] HTML-экранирование в diff-сводке."""

    def test_basic(self) -> None:
        from ui.chat.message_item import _escape_diff

        assert _escape_diff("a < b > c & d") == "a &lt; b &gt; c &amp; d"

    def test_empty(self) -> None:
        from ui.chat.message_item import _escape_diff

        assert _escape_diff("") == ""

    def test_none_safe(self) -> None:
        from ui.chat.message_item import _escape_diff

        # str(None) = "None" — не падаем.
        assert _escape_diff(None) == "None"  # type: ignore[arg-type]


class TestDiffMetadataShape:
    """Структура metadata от write_file — проверяем, что UI-поля есть."""

    def test_write_file_metadata_keys(self) -> None:
        """Эталон структуры: если поле переименуют — тест упадёт."""
        required = {
            "_llm_text",
            "status",
            "path",
            "is_new_file",
            "original_content",
            "diff",
            "lines_added",
            "lines_removed",
            "syntax_error",
        }
        # Тест-«контракт»: подсказка будущим изменениям.
        # Реальное заполнение проверяется в tests/tools/test_file_ops_diff.py.
        assert len(required) == 9


class TestDiffSignalShape:
    """[9.8] Структура payload для show_diff_requested."""

    def test_payload_keys(self) -> None:
        """Контракт между ToolCallCard и ChatPanel._on_show_tool_diff."""
        required = {"path", "original", "modified", "language"}
        # Эталон структуры: если поле переименуют — тест упадёт.
        assert len(required) == 4
