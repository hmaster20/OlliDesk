"""Тесты реестра палитры команд (9.9).

Без pytest-qt: `CommandRegistry` и `PaletteCommand` — чистые dataclass'ы,
без QApplication. UI-диалог (`CommandPalette`) проверяется ручным прогоном.
"""

from __future__ import annotations

import pytest

from ui.command_registry import CommandRegistry, PaletteCommand


def _make(
    cid: str,
    label: str,
    hint: str = "",
    group: str = "Test",
    shortcut: str = "",
) -> PaletteCommand:
    return PaletteCommand(
        id=cid,
        label=label,
        hint=hint,
        shortcut=shortcut,
        group=group,
    )


class TestPaletteCommandMatches:
    def test_empty_query_matches(self) -> None:
        cmd = _make("a", "Открыть проект")
        assert cmd.matches("")
        assert cmd.matches("   ")

    def test_label_substring(self) -> None:
        cmd = _make("a", "Открыть проект")
        assert cmd.matches("откр")
        assert cmd.matches("ОТКР")
        assert cmd.matches("проект")

    def test_hint_substring(self) -> None:
        cmd = _make("a", "Open", hint="Выбрать папку")
        assert cmd.matches("папку")
        assert cmd.matches("выбрать")

    def test_group_substring(self) -> None:
        cmd = _make("a", "Open", group="File")
        assert cmd.matches("file")
        assert cmd.matches("FILE")

    def test_no_match(self) -> None:
        cmd = _make("a", "Открыть проект", hint="папка", group="File")
        assert not cmd.matches("xyz")
        assert not cmd.matches("123")

    def test_partial_word(self) -> None:
        cmd = _make("a", "Переключить бэкенд")
        assert cmd.matches("бэк")
        assert cmd.matches("перекл")


class TestCommandRegistry:
    def test_empty(self) -> None:
        reg = CommandRegistry()
        assert len(reg) == 0
        assert reg.all() == []
        assert reg.filter("") == []

    def test_register(self) -> None:
        reg = CommandRegistry()
        cmd = _make("a", "Cmd A")
        reg.register(cmd)
        assert len(reg) == 1
        assert reg.get("a") is cmd

    def test_empty_id_rejected(self) -> None:
        reg = CommandRegistry()
        with pytest.raises(ValueError):
            reg.register(_make("", "Cmd"))

    def test_duplicate_id_overwrites(self) -> None:
        reg = CommandRegistry()
        a1 = _make("a", "First")
        a2 = _make("a", "Second")
        reg.register(a1)
        reg.register(a2)
        assert len(reg) == 1
        assert reg.get("a") is a2

    def test_register_many(self) -> None:
        reg = CommandRegistry()
        reg.register_many([_make("a", "A"), _make("b", "B"), _make("c", "C")])
        assert len(reg) == 3

    def test_unregister(self) -> None:
        reg = CommandRegistry()
        reg.register(_make("a", "A"))
        reg.unregister("a")
        assert len(reg) == 0
        assert reg.get("a") is None

    def test_unregister_unknown_is_noop(self) -> None:
        reg = CommandRegistry()
        reg.register(_make("a", "A"))
        reg.unregister("nonexistent")
        assert len(reg) == 1

    def test_clear(self) -> None:
        reg = CommandRegistry()
        reg.register_many([_make("a", "A"), _make("b", "B")])
        reg.clear()
        assert len(reg) == 0


class TestRegistryFilter:
    def _sample(self) -> CommandRegistry:
        reg = CommandRegistry()
        reg.register_many(
            [
                _make("open", "Открыть проект", group="File"),
                _make("export", "Экспорт чата", group="File"),
                _make("profile", "Профиль: Разработка", group="Профиль"),
                _make("backend", "Переключить бэкенд", group="Бэкенд"),
                _make("tree", "Показать/скрыть дерево", group="View"),
                _make("slm", "Локальные модели SLM", group="Модели"),
            ]
        )
        return reg

    def test_empty_query_returns_all(self) -> None:
        reg = self._sample()
        assert len(reg.filter("")) == 6

    def test_whitespace_query_returns_all(self) -> None:
        reg = self._sample()
        assert len(reg.filter("   ")) == 6

    def test_filter_by_label(self) -> None:
        reg = self._sample()
        results = reg.filter("проект")
        assert len(results) == 1
        assert results[0].id == "open"

    def test_filter_by_group(self) -> None:
        reg = self._sample()
        results = reg.filter("профиль")
        assert len(results) >= 1
        assert any(r.id == "profile" for r in results)

    def test_filter_case_insensitive(self) -> None:
        reg = self._sample()
        assert len(reg.filter("SLM")) == 1
        assert len(reg.filter("slm")) == 1
        assert len(reg.filter("Slm")) == 1

    def test_filter_no_matches(self) -> None:
        reg = self._sample()
        assert reg.filter("zzz") == []

    def test_prefix_prioritized(self) -> None:
        """Команды, где запрос — префикс label, идут первыми."""
        reg = CommandRegistry()
        reg.register_many(
            [
                _make("a", "Some text with Экспорт inside"),  # не префикс
                _make("b", "Экспорт чата"),  # префикс
                _make("c", "Другое"),  # не подходит
            ]
        )
        results = reg.filter("экспорт")
        assert len(results) == 2
        # Первая — с префиксом.
        assert results[0].id == "b"
        assert results[1].id == "a"

    def test_multiple_prefix_matches_order_stable(self) -> None:
        """Несколько префиксных совпадений — порядок регистрации."""
        reg = CommandRegistry()
        reg.register_many(
            [
                _make("a", "Открыть проект"),
                _make("b", "Открыть файл"),
                _make("c", "Открыть настройки"),
            ]
        )
        results = reg.filter("открыть")
        assert [r.id for r in results] == ["a", "b", "c"]
