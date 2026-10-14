# ui/chat/attached_files_handler.py
"""Обработка команд @file / @dir / @path и хранение прикреплённых файлов.

Модуль инкапсулирует:

* разбор префикс-команд во входной строке;
* чтение файла и добавление его в `AttachedFileStore`;
* обработку запроса на подключение каталога (панель сама решает, что
  делать с ним — обычно эмитит сигнал в `MainWindow` для переиндексации);
* сборку текстового блока превью для системного промпта.

Все побочные эффекты UI (показать сообщение, эмитить сигнал) вынесены
в сигналы, чтобы модуль можно было использовать и без панели.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import QObject, Signal

from core.context_budget import attached_char_budget
from tools.attached_files import AttachedFileStore
from ui.chat.tool_labels import (
    MIN_ATTACHED_BUDGET,
    PATH_COMMANDS,
)


class AttachedFilesHandler(QObject):
    """Разбор и хранение прикреплённых файлов.

    Сигналы:
        message_emitted(text): просьба показать информационное сообщение.
        error_emitted(text): просьба показать сообщение об ошибке.
        file_attached(name, size_chars, lines): файл успешно прикреплён.
        directory_requested(path): подключить каталог (обрабатывает панель).
        resend_requested(rest): нужно переотправить остаток ввода.
    """

    message_emitted = Signal(str)
    error_emitted = Signal(str)
    file_attached = Signal(str, int, int)  # name, size_chars, lines
    directory_requested = Signal(str)
    resend_requested = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = AttachedFileStore()

    # -------------------------------------------------------------
    # Состояние
    # -------------------------------------------------------------

    def __bool__(self) -> bool:
        return bool(self._store)

    def __len__(self) -> int:
        return len(self._store)

    @property
    def store(self) -> AttachedFileStore:
        """Прямой доступ к хранилищу (нужен агенту через AgentContext)."""
        return self._store

    def clear(self) -> None:
        """Убирает все прикреплённые файлы."""
        self._store.clear()

    # -------------------------------------------------------------
    # Разбор команд
    # -------------------------------------------------------------

    @staticmethod
    def extract_path_command(text: str) -> tuple[str, str, str] | None:
        """Разбирает префикс-команду (@dir/@file/@path) в начале строки.

        Args:
            text: Полный текст пользовательского ввода.

        Returns:
            Кортеж ``(kind, path, rest)`` или ``None``, если команды нет.
            ``kind`` — одно из ``dir``, ``file``, ``path``.
        """
        for cmd in PATH_COMMANDS:
            if not text.startswith(cmd):
                continue

            kind = cmd[1:]
            tail = text[len(cmd):].lstrip()
            if not tail:
                return (kind, "", "")

            if tail.startswith('"'):
                end = tail.find('"', 1)
                if end > 0:
                    return (kind, tail[1:end], tail[end + 1:].strip())
                return (kind, tail.lstrip('"'), "")

            parts = tail.split(None, 1)
            return (kind, parts[0], parts[1] if len(parts) > 1 else "")

        return None

    # -------------------------------------------------------------
    # Обработка команды
    # -------------------------------------------------------------

    def handle_command(self, kind: str, path_str: str, rest: str) -> None:
        """Обрабатывает разобранную команду: файл или каталог.

        Args:
            kind: ``dir`` / ``file`` / ``path``.
            path_str: Путь (может быть пустым).
            rest: Остаток ввода после пути (для переотправки).
        """
        if not path_str:
            self.message_emitted.emit(f"⚠️ Укажите путь после @{kind}")
            return

        path = Path(path_str).expanduser().resolve()
        if not path.exists():
            self.error_emitted.emit(f"❌ Путь не найден: {path}")
            return

        if kind == "path":
            kind = "dir" if path.is_dir() else "file"

        if kind == "dir":
            if not path.is_dir():
                self.error_emitted.emit(
                    f"❌ Не директория: {path} (используйте @file)"
                )
                return
            self.message_emitted.emit(
                f"📁 Прикреплена директория: {path} — идёт индексация, "
                "инструменты поиска по коду работают по ней."
            )
            self.directory_requested.emit(str(path))
        elif not path.is_file():
            self.error_emitted.emit(
                f"❌ Не файл: {path} (используйте @dir)"
            )
            return
        else:
            self.attach_file(path)

        if rest:
            self.resend_requested.emit(rest)

    def attach_file(self, path: Path) -> None:
        """Читает файл с диска и добавляет его в хранилище.

        Args:
            path: Путь к файлу.
        """
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error(f"@file: ошибка чтения {path}: {e}")
            self.error_emitted.emit(f"❌ Не удалось прочитать файл: {e}")
            return

        attached = self._store.add(path, content)
        logger.info(f"@file: прикреплён {path} ({attached.size_chars} символов)")
        self.file_attached.emit(
            attached.name, attached.size_chars, attached.lines
        )

    # -------------------------------------------------------------
    # Сборка контекста для промпта
    # -------------------------------------------------------------

    def render_context(self, num_ctx: int, rag_active: bool) -> str:
        """Формирует блок превью прикреплённых файлов для системного промпта.

        Args:
            num_ctx: Окно контекста активного бэкенда в токенах.
            rag_active: True, если в промпт уже пойдёт RAG-контекст —
                в этом случае бюджет прикреплённых делится пополам.

        Returns:
            Текст для вставки в системный промпт (возможно, пустой).
        """
        if not self._store:
            return ""

        budget = attached_char_budget(num_ctx)
        if rag_active:
            budget = max(MIN_ATTACHED_BUDGET, budget // 2)
        return self._store.render(budget)