"""Тесты реестра `@`-команд (9.6.2)."""

from __future__ import annotations

from core.chat_commands import (
    ALL_COMMANDS,
    COMMAND_TAGS,
    DEFAULT_PLACEHOLDER,
    detect_active_command,
    find_first_word,
    get_command,
    match_commands,
)


class TestRegistry:
    def test_tags(self) -> None:
        assert {"@web", "@dir", "@file", "@path"} == COMMAND_TAGS

    def test_all_have_hint_and_placeholder(self) -> None:
        for c in ALL_COMMANDS:
            assert c.hint
            assert c.placeholder
            assert c.tag.startswith("@")

    def test_get_command(self) -> None:
        assert get_command("@web") is not None
        assert get_command("@web").tag == "@web"
        assert get_command("@unknown") is None

    def test_get_command_strips(self) -> None:
        assert get_command("  @web  ") is not None

    def test_default_placeholder(self) -> None:
        assert "@web" in DEFAULT_PLACEHOLDER
        assert "@file" in DEFAULT_PLACEHOLDER


class TestFindFirstWord:
    def test_simple(self) -> None:
        assert find_first_word("@web привет") == "@web"

    def test_no_space(self) -> None:
        assert find_first_word("@web") == "@web"

    def test_empty(self) -> None:
        assert find_first_word("") == ""

    def test_leading_space(self) -> None:
        assert find_first_word("  @web") == ""

    def test_newline(self) -> None:
        assert find_first_word("@web\nhello") == "@web"


class TestDetectActiveCommand:
    def test_start_typing(self) -> None:
        assert detect_active_command("@", 1) == "@"

    def test_partial_web(self) -> None:
        assert detect_active_command("@w", 2) == "@w"

    def test_full_web(self) -> None:
        assert detect_active_command("@web", 4) == "@web"

    def test_with_args_not_active(self) -> None:
        # `@web query` — аргументы, popup не нужен.
        assert detect_active_command("@web query", 11) is None

    def test_cursor_after_space(self) -> None:
        assert detect_active_command("@web ", 5) is None

    def test_not_at_start(self) -> None:
        assert detect_active_command("hello @web", 10) is None

    def test_empty(self) -> None:
        assert detect_active_command("", 0) is None

    def test_cursor_before_at(self) -> None:
        assert detect_active_command("@web", 0) is None

    def test_second_line(self) -> None:
        assert detect_active_command("@web\n@f", 7) is None


class TestMatchCommands:
    def test_all(self) -> None:
        assert len(match_commands("@")) == 4

    def test_prefix_w(self) -> None:
        matches = match_commands("@w")
        assert len(matches) == 1
        assert matches[0].tag == "@web"

    def test_prefix_fi(self) -> None:
        matches = match_commands("@fi")
        assert len(matches) == 1
        assert matches[0].tag == "@file"

    def test_case_insensitive(self) -> None:
        assert len(match_commands("@WEB")) == 1
        assert len(match_commands("@Fi")) == 1

    def test_no_match(self) -> None:
        assert match_commands("@xyz") == []

    def test_empty_prefix(self) -> None:
        assert match_commands("") == []

    def test_not_at(self) -> None:
        assert match_commands("web") == []
