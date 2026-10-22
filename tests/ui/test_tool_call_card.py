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