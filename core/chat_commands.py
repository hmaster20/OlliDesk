"""Реестр `@`-команд чата (9.6.2).

Единый источник истины для:
  * автодополнения в поле ввода (`ui.widgets.command_completer`);
  * подсветки `@`-тегов (`ChatPanel._highlight_web_command`);
  * описания команд в placeholder и tooltip.

Новые команды добавляются сюда — UI подхватит автоматически.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatCommand:
    """Описание одной `@`-команды."""

    tag: str          # "@web", "@dir", "@file", "@path"
    hint: str         # краткая подсказка в popup
    placeholder: str  # что писать в placeholder поля ввода после выбора


ALL_COMMANDS: tuple[ChatCommand, ...] = (
    ChatCommand(
        tag="@web",
        hint="поиск в интернете",
        placeholder="Введите запрос для поиска в интернете…",
    ),
    ChatCommand(
        tag="@dir",
        hint="прикрепить папку как проект",
        placeholder="Введите путь к папке…",
    ),
    ChatCommand(
        tag="@file",
        hint="прикрепить файл в контекст",
        placeholder="Введите путь к файлу…",
    ),
    ChatCommand(
        tag="@path",
        hint="авто-детект: папка или файл",
        placeholder="Введите путь (папка или файл)…",
    ),
)

# Множество тегов — для быстрой проверки.
COMMAND_TAGS: frozenset[str] = frozenset(c.tag for c in ALL_COMMANDS)

# Формат подсказки в placeholder по умолчанию.
DEFAULT_PLACEHOLDER = (
    "Введите сообщение... (CTRL+Enter — отправить, "
    "@web — поиск, @file — прикрепить файл, @dir — прикрепить папку)"
)

# Соответствие tag → ChatCommand (для быстрого доступа).
_BY_TAG: dict[str, ChatCommand] = {c.tag: c for c in ALL_COMMANDS}


def get_command(tag: str) -> ChatCommand | None:
    """Возвращает описание команды по тегу (или None)."""
    return _BY_TAG.get(tag.strip().lower())


def find_first_word(text: str) -> str:
    """Первое слово до пробела/перевода строки (без strip)."""
    if not text:
        return ""
    end = len(text)
    for i, ch in enumerate(text):
        if ch.isspace():
            end = i
            break
    return text[:end]


def detect_active_command(text: str, cursor_pos: int) -> str | None:
    """Определяет, набирается ли сейчас `@`-команда в начале текста.

    Правила (по плану 9.6.2):
      * `@` только в начале первой строки;
      * курсор в пределах первого слова;
      * слово начинается с `@` (регистр учитывается, но popup
        фильтрует по подстроке без учёта регистра).

    Args:
        text: Полный текст поля ввода.
        cursor_pos: Позиция курсора (0-based).

    Returns:
        Набираемый префикс (`"@w"`, `"@fi"`, …) или ``None``.
    """
    if not text or cursor_pos <= 0:
        return None
    # Только первая строка и только если первый символ — `@`.
    if text[0] != "@":
        return None
    # Курсор должен быть в пределах первого слова.
    first_word = find_first_word(text)
    if cursor_pos > len(first_word):
        return None
    # Внутри первого слова не должно быть переноса/пробела до курсора.
    if any(ch.isspace() for ch in text[:cursor_pos]):
        return None
    return text[:cursor_pos]


def match_commands(prefix: str) -> list[ChatCommand]:
    """Возвращает команды, подходящие под префикс (без учёта регистра)."""
    if not prefix or not prefix.startswith("@"):
        return []
    low = prefix.lower()
    return [c for c in ALL_COMMANDS if c.tag.startswith(low)]


__all__ = [
    "ALL_COMMANDS",
    "COMMAND_TAGS",
    "ChatCommand",
    "DEFAULT_PLACEHOLDER",
    "detect_active_command",
    "find_first_word",
    "get_command",
    "match_commands",
]