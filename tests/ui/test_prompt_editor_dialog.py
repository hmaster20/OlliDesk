"""Тесты чистых хелперов редактора промптов."""

from ui.dialogs.prompt_editor_dialog import _strip_frontmatter


class TestStripFrontmatter:
    def test_removes_frontmatter(self):
        text = "---\nname: Test\n---\nТело промпта\n"
        assert _strip_frontmatter(text) == "Тело промпта"

    def test_keeps_plain_text(self):
        assert _strip_frontmatter("Просто текст") == "Просто текст"

    def test_keeps_text_without_closing_marker(self):
        assert _strip_frontmatter("---\nname: Test\nнет закрывающего") == "---\nname: Test\nнет закрывающего"
