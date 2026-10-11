"""Тесты markdown-рендерера чата (ui/chat/message_item.py)."""

import re

from ui.chat.message_item import render_markdown


class TestBlocks:
    def test_paragraph_split(self):
        html = render_markdown("Первый.\n\nВторой.")
        assert html.count("<p ") == 2
        assert "Первый." in html
        assert "Второй." in html

    def test_multiline_paragraph_uses_br(self):
        html = render_markdown("строка один\nстрока два")
        assert "<br>" in html
        assert html.count("<p ") == 1

    def test_heading_levels(self):
        assert "<h2 " in render_markdown("## Заголовок")
        assert ">Заголовок</h2>" in render_markdown("## Заголовок")
        assert "<h1 " in render_markdown("# H1")

    def test_horizontal_rule(self):
        assert "<hr " in render_markdown("---")

    def test_blockquote(self):
        html = render_markdown("> цитата\n> вторая")
        assert "<blockquote" in html
        assert "цитата" in html
        assert "вторая" in html
        assert "&lt;br&gt;" not in html


class TestLists:
    def test_unordered(self):
        html = render_markdown("- один\n- два")
        assert "<ul " in html
        assert html.count("<li ") == 2
        assert "<p " not in html

    def test_ordered(self):
        html = render_markdown("1. один\n2. два")
        assert "<ol " in html
        assert html.count("<li ") == 2

    def test_nested_unordered(self):
        html = render_markdown("- один\n  - вложенный\n- два")
        assert html.count("<ul ") == 2
        assert "вложенный" in html
        assert html.count("<li ") == 3

    def test_nested_ordered_in_unordered(self):
        html = render_markdown("- пункт\n  1. подпункт")
        assert "<ul " in html
        assert "<ol " in html
        assert "подпункт" in html

    def test_deep_nesting(self):
        html = render_markdown("- a\n  - b\n    - c")
        assert html.count("<ul ") == 3
        assert ">c</li>" in html

    def test_sibling_list_type_change(self):
        html = render_markdown("- пункт\n\n1. номер")
        assert "<ul " in html
        assert "<ol " in html

    def test_loose_list_keeps_numbering(self):
        html = render_markdown("1. один\n\n2. два\n\n3. три")
        assert html.count("<ol ") == 1
        assert html.count("<li ") == 3

    def test_loose_list_with_nested_bullets(self):
        html = render_markdown("1. a\n   - sub\n\n2. b\n   - sub\n\n3. c")
        assert html.count("<ol ") == 1
        assert html.count("<ul ") == 2
        assert html.count("<li ") == 5


class TestInline:
    def test_inline_code(self):
        html = render_markdown("`x = 1`")
        assert "<code" in html
        assert "x = 1" in html

    def test_bold_italic(self):
        html = render_markdown("**жирный** и *курсив*")
        assert "<b>жирный</b>" in html
        assert "<i>курсив</i>" in html

    def test_strikethrough(self):
        assert "<s>del</s>" in render_markdown("~~del~~")

    def test_highlight(self):
        html = render_markdown("==важное==")
        assert "#ffd54f" in html
        assert "важное" in html

    def test_link(self):
        html = render_markdown("[текст](http://example.com)")
        assert '<a href="http://example.com"' in html
        assert ">текст</a>" in html

    def test_paragraph_escaping(self):
        html = render_markdown("<script>alert</script>")
        assert "&lt;script&gt;" in html
        assert "<script>" not in html

    def test_inline_code_escaping(self):
        html = render_markdown("`<b>tag</b>`")
        assert "&lt;b&gt;tag&lt;/b&gt;" in html


class TestCodeBlocks:
    def test_fenced_with_lang(self):
        html = render_markdown("```python\nprint(1)\n```")
        assert "#23232c" in html
        assert "<pre" in html
        assert "python" in html
        assert "print" in html
        assert "<p " not in html

    def test_code_no_line_height(self):
        html = render_markdown("```py\na\nb\n```")
        pre_tag = re.search(r"<pre[^>]*>", html)
        assert pre_tag is not None
        assert "line-height" not in pre_tag.group(0)

    def test_code_no_trailing_blank_line(self):
        html = render_markdown("```py\na\nb\n```")
        assert "</code></pre></div>" in html
        assert re.search(r"\n\s*</code>", html) is None

    def test_fenced_without_lang(self):
        html = render_markdown("```\nplain\n```")
        assert "<pre" in html

    def test_fenced_escaping(self):
        html = render_markdown("```\n<b>x</b>\n```")
        assert "&lt;b&gt;" in html

    def test_text_around_code(self):
        html = render_markdown("До.\n\n```py\nx\n```\n\nПосле.")
        assert "До." in html
        assert "После." in html
        assert "<pre" in html


class TestStreaming:
    def test_unterminated_fence_is_closed(self):
        html = render_markdown("Вот пример:\n\n```python\nprint(1)")
        assert "<pre" in html
        assert "print" in html
        assert "```" not in html

    def test_unterminated_fence_after_closed_block(self):
        html = render_markdown("```py\na\n```\n\n```py\nb")
        assert html.count("<pre") == 2

    def test_incomplete_bold_is_left_as_text(self):
        html = render_markdown("это **незакрытый жирный")
        assert "незакрытый жирный" in html


class TestTables:
    def test_basic_table(self):
        md = "| Имя | Роль |\n|---|---|\n| Qwen | chat |\n| Nomic | embed |"
        html = render_markdown(md)
        assert "<table" in html
        assert html.count("<th ") == 2
        assert html.count("<td ") == 4
        assert "Qwen" in html
        assert "Nomic" in html
        assert "<p " not in html

    def test_table_alignment_separator(self):
        md = "| A | B |\n|:--|--:|\n| 1 | 2 |"
        html = render_markdown(md)
        assert "<table" in html
        assert "A" in html

    def test_table_row_padded_to_header_width(self):
        md = "| A | B |\n|---|---|\n| 1 |"
        html = render_markdown(md)
        assert html.count("<td ") == 2

    def test_pipe_text_without_separator_is_paragraph(self):
        html = render_markdown("a | b\nc")
        assert "<table" not in html
        assert "<p " in html


class TestTaskList:
    def test_unchecked_and_checked(self):
        html = render_markdown("- [ ] сделать\n- [x] готово")
        assert "☐" in html
        assert "☑" in html
        assert "сделать" in html
        assert "готово" in html

    def test_regular_list_has_no_checkbox(self):
        html = render_markdown("- просто пункт")
        assert "☐" not in html
        assert "☑" not in html
