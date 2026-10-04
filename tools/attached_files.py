"""Файлы, прикреплённые командой `@file`, и инструмент дочитывания.

Полный текст файла редко помещается в окно контекста модели, поэтому в
промпт подставляется только превью (начало и конец), а середина остаётся
доступной через инструмент `read_attached_file`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.context_budget import READ_CHUNK_CHARS

# Минимально полезный размер превью — ниже этого фрагмент бесполезен.
MIN_PREVIEW_CHARS = 400

# Доля бюджета файла, отдаваемая началу (остальное — концу).
HEAD_SHARE = 0.6


@dataclass
class AttachedFile:
    """Файл, прикреплённый пользователем через `@file`."""

    path: Path
    content: str

    @property
    def name(self) -> str:
        """Имя файла вместе с расширением."""
        return self.path.name

    @property
    def size_chars(self) -> int:
        """Полный размер файла в символах."""
        return len(self.content)

    @property
    def lines(self) -> int:
        """Примерное количество строк в файле."""
        return self.content.count("\n") + 1


@dataclass
class AttachedFileStore:
    """Набор файлов, прикрепленных в текущей сессии чата."""

    _files: list[AttachedFile] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self._files)

    def __len__(self) -> int:
        return len(self._files)

    @property
    def files(self) -> list[AttachedFile]:
        """Копия списка прикрепленных файлов."""
        return list(self._files)

    def add(self, path: Path, content: str) -> AttachedFile:
        """Добавляет файл в хранилище, заменяя предыдущую версию того же пути.

        Args:
            path: Путь к файлу.
            content: Текст файла.

        Returns:
            Добавленный (или замененный) файл.
        """
        self._files = [f for f in self._files if f.path != path]
        attached = AttachedFile(path=path, content=content)
        self._files.append(attached)
        return attached

    def clear(self) -> None:
        """Убирает все прикрепленные файлы."""
        self._files.clear()

    def find(self, path_str: str) -> AttachedFile | None:
        """Ищет файл по полному пути или по имени файла.

        Args:
            path_str: Путь или имя файла, указанное моделью.

        Returns:
            Найденный файл или None.
        """
        if not path_str:
            return None

        candidate = Path(path_str)
        for attached in self._files:
            if attached.path == candidate:
                return attached

        wanted = candidate.name.lower()
        for attached in self._files:
            if attached.name.lower() == wanted:
                return attached
        return None

    def render(self, max_chars: int) -> str:
        """Формирует блок контекста с превью файлов, укладывающимся в бюджет.

        Args:
            max_chars: Сколько символов суммарно можно отдать в промпт.

        Returns:
            Текстовый блок для системного промпта.
        """
        if not self._files or max_chars <= 0:
            return ""

        per_file = max_chars // len(self._files)
        blocks = [self._render_one(attached, per_file) for attached in self._files]
        return "\n\n".join(block for block in blocks if block)

    def _render_one(self, attached: AttachedFile, max_chars: int) -> str:
        """Формирует превью одного файла: начало и конец с указанием пропуска."""
        header = (
            f"### Прикреплённый файл: {attached.path} "
            f"({attached.lines} строк, {attached.size_chars} символов)"
        )
        hint = (
            f"Полный текст не помещается в контекст. Недостающую часть читай "
            f"инструментом read_attached_file(path=\"{attached.name}\", offset=0)."
        )

        # Минимум под структуру блока: заголовок, подсказка и ограждения.
        available = max_chars - len(header) - len(hint) - 16
        if available < MIN_PREVIEW_CHARS:
            return f"{header}\n{hint}"

        if attached.size_chars <= available:
            return f"{header}\n{hint}\n```\n{attached.content}\n```"

        head_size = int(available * HEAD_SHARE)
        tail_size = available - head_size
        head = attached.content[:head_size]
        tail = attached.content[-tail_size:] if tail_size > 0 else ""
        skipped = attached.size_chars - len(head) - len(tail)

        return (
            f"{header}\n{hint}\n"
            f"```\n{head}\n"
            f"... [пропущено ~{skipped} символов] ...\n"
            f"{tail}\n```"
        )


@tool(
    name="read_attached_file",
    description=(
        "Читает фрагмент файла, прикреплённого через @file. "
        "Используй, когда в контексте показано только начало и конец файла, "
        "а нужна середина. offset — смещение в символах, limit — сколько символов вернуть."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def read_attached_file(
    path: str,
    offset: int = 0,
    limit: int = READ_CHUNK_CHARS,
    attached_files: Any = None,
) -> str:
    """Возвращает часть ранее прикреплённого файла.

    Args:
        path: Имя прикреплённого файла (например "main.py").
        offset: Смещение в символах от начала файла.
        limit: Сколько символов вернуть.
        attached_files: Хранилище прикреплённых файлов (передаётся через context).

    Returns:
        Фрагмент файла либо строка с ошибкой.
    """
    if attached_files is None or not attached_files:
        return "Прикреплённых файлов нет. Попроси пользователя использовать @file."

    try:
        store: AttachedFileStore = attached_files
        attached = store.find(path)
        if attached is None:
            available = ", ".join(f.name for f in store.files)
            return f"Файл '{path}' не прикреплён. Доступные: {available or 'нет'}"

        start = max(0, int(offset))
        size = min(max(1, int(limit)), READ_CHUNK_CHARS)

        if start >= attached.size_chars:
            return (
                f"Смещение {start} выходит за конец файла "
                f"({attached.size_chars} символов)."
            )

        chunk = attached.content[start:start + size]
        end = start + len(chunk)
        next_hint = (
            f"\n\nПродолжение: read_attached_file(path=\"{attached.name}\", "
            f"offset={end})"
            if end < attached.size_chars
            else "\n\nЭто конец файла."
        )

        logger.debug(f"read_attached_file: {attached.name} [{start}:{end}]")
        return f"{attached.name} [символы {start}-{end} из {attached.size_chars}]\n{chunk}{next_hint}"
    except Exception as e:
        logger.exception("read_attached_file failed")
        return f"Ошибка чтения прикреплённого файла: {e}"