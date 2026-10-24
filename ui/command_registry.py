"""Реестр команд палитры (9.9).

Чистый модуль без Qt: dataclass `PaletteCommand` + `CommandRegistry`
с фильтрацией. UI (QDialog) живёт отдельно в
`ui/widgets/command_palette.py` — так реестр тестируется без QApplication.

Команды регистрирует `MainWindow._register_palette_commands()`:
File / View / Chat / Help. Callback — bound-метод MainWindow или лямбда,
которая вызывает существующий слот.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class PaletteCommand:
    """Одна команда палитры."""

    id: str  # уникальный стабильный id ("file.open_project")
    label: str  # отображаемое имя ("Открыть проект…")
    hint: str = ""  # короткая подсказка для второй строки
    shortcut: str = ""  # "Ctrl+O" (для отображения справа)
    group: str = "General"  # "File" / "View" / "Chat" / "Help"
    callback: Callable[[], None] | None = None

    def matches(self, query: str) -> bool:
        """True, если запрос (без учёта регистра) встречается в label/hint/group."""
        if not query:
            return True
        q = query.strip().lower()
        if not q:
            return True
        haystack = f"{self.label} {self.hint} {self.group}".lower()
        return q in haystack


class CommandRegistry:
    """Реестр команд с фильтрацией."""

    def __init__(self) -> None:
        self._commands: list[PaletteCommand] = []
        self._by_id: dict[str, PaletteCommand] = {}

    def register(self, command: PaletteCommand) -> None:
        """Регистрирует команду. Дубликат id перезаписывает."""
        if not command.id:
            raise ValueError("PaletteCommand.id не может быть пустым")
        if command.id in self._by_id:
            # Перезапись — на случай повторной регистрации после reload.
            self._commands = [c for c in self._commands if c.id != command.id]
        self._commands.append(command)
        self._by_id[command.id] = command

    def register_many(self, commands: list[PaletteCommand]) -> None:
        for c in commands:
            self.register(c)

    def unregister(self, command_id: str) -> None:
        if command_id in self._by_id:
            self._commands = [c for c in self._commands if c.id != command_id]
            del self._by_id[command_id]

    def get(self, command_id: str) -> PaletteCommand | None:
        return self._by_id.get(command_id)

    def all(self) -> list[PaletteCommand]:
        """Все команды в порядке регистрации."""
        return list(self._commands)

    def filter(self, query: str) -> list[PaletteCommand]:
        """Команды, подходящие под запрос.

        Сортировка: сначала те, у кого запрос — префикс label (более
        релевантные), затем остальные. Внутри — стабильный порядок
        регистрации.
        """
        q = (query or "").strip().lower()
        matches = [c for c in self._commands if c.matches(q)]
        if not q:
            return matches
        # Префикс label — выше.
        prefix_hits: list[PaletteCommand] = []
        other_hits: list[PaletteCommand] = []
        for c in matches:
            if c.label.lower().startswith(q):
                prefix_hits.append(c)
            else:
                other_hits.append(c)
        return prefix_hits + other_hits

    def clear(self) -> None:
        self._commands.clear()
        self._by_id.clear()

    def __len__(self) -> int:
        return len(self._commands)


__all__ = ["CommandRegistry", "PaletteCommand"]
