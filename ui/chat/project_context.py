# ui/chat/project_context.py
"""Контекст открытого проекта для системного промпта и RAG.

Собирает всё, что зависит от корня проекта, но НЕ зависит от UI:

* список файлов (для построения дерева);
* карту структуры проекта (текстовое дерево);
* правила проекта (`.ollidesk/rules.md`);
* определение области запроса (каталог, о котором идёт речь);
* список файлов выбранного каталога;
* объединение scope-listing с RAG-контекстом.

Класс намеренно лишён сигналов Qt: панель просто вызывает его методы.
Так его можно покрыть юнит-тестами без QApplication.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from core.config import load_config
from fs.project_map import (
    detect_query_scope,
    list_project_files,
    render_project_map,
)


class ProjectContext:
    """Состояние и вычисления, привязанные к корню открытого проекта.

    Атрибуты:
        project_root: Корень проекта (пустая строка, если проект не открыт).
    """

    def __init__(self) -> None:
        self.project_root: str = ""
        self._project_files: list[str] = []
        self._project_map: str = ""
        self._scanned: bool = False
        self._scan_root: str | None = None

    # -------------------------------------------------------------
    # Установка/сброс корня
    # -------------------------------------------------------------

    def set_root(self, project_root: str) -> None:
        """Устанавливает корень проекта и сбрасывает кэш сканирования.

        Args:
            project_root: Новый корень проекта (пустая строка — нет проекта).
        """
        if self.project_root == project_root and self._scanned:
            return
        self.project_root = project_root
        self._project_files = []
        self._project_map = ""
        self._scanned = False
        self._scan_root = None

    # -------------------------------------------------------------
    # Сканирование
    # -------------------------------------------------------------

    def ensure_scan(self) -> None:
        """Сканирует проект один раз (или после смены корня)."""
        root = self.project_root or ""
        if self._scanned and self._scan_root == root:
            return

        self._scanned = True
        self._scan_root = root
        self._project_files = []
        self._project_map = ""

        if not root:
            return

        project_path = Path(root)
        if not project_path.is_dir():
            return

        try:
            config = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для карты проекта: {e}")
            return

        try:
            self._project_files = list_project_files(project_path, config)
            self._project_map = render_project_map(self._project_files)
        except Exception as e:
            logger.warning(f"Не удалось построить карту проекта: {e}")

    # -------------------------------------------------------------
    # Публичные аксессоры
    # -------------------------------------------------------------

    @property
    def files(self) -> list[str]:
        """Список относительных путей файлов проекта (после ensure_scan)."""
        self.ensure_scan()
        return self._project_files

    def map_text(self) -> str:
        """Карта структуры проекта (дерево каталогов и файлов)."""
        self.ensure_scan()
        return self._project_map

    def project_name(self) -> str:
        """Имя корневого каталога проекта."""
        return Path(self.project_root).name if self.project_root else ""

    def template_context(self) -> dict:
        """Контекст для шаблонизации системного промпта (`{{key}}`)."""
        return {
            "project_name": self.project_name(),
            "language": "русский",
            "project_root": self.project_root or "",
            "project_map": self.map_text(),
        }

    def rules(self) -> str:
        """Читает правила проекта (`rules_file` из конфига, по умолчанию `.ollidesk/rules.md`)."""
        if not self.project_root:
            return ""
        try:
            rules_file = load_config().rules_file or ".ollidesk/rules.md"
            path = Path(self.project_root) / rules_file
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Не удалось прочитать правила проекта: {e}")
        return ""

    # -------------------------------------------------------------
    # Область запроса (scope)
    # -------------------------------------------------------------

    def query_scope(self, text: str) -> tuple[str, list[str]] | None:
        """Ищет в запросе каталог проекта для сужения RAG-поиска.

        Args:
            text: Текст запроса пользователя.

        Returns:
            Пара ``(относительный путь каталога, файлы внутри)`` или ``None``.
        """
        self.ensure_scan()
        if not self._project_files:
            return None
        return detect_query_scope(text, self._project_files)

    def scope_listing(
        self,
        scope: tuple[str, list[str]] | None,
        limit: int = 100,
    ) -> str:
        """Формирует текстовый список файлов внутри выбранного каталога.

        Args:
            scope: Результат ``query_scope`` (или ``None``).
            limit: Максимум строк в списке.

        Returns:
            Текст либо пустая строка.
        """
        if not scope:
            return ""
        rel, files = scope
        shown = files[:limit]
        lines = [f"Файлы в каталоге `{rel}/` (всего {len(files)}):"]
        lines.extend(f"- {path}" for path in shown)
        if len(files) > limit:
            lines.append(f"… и ещё {len(files) - limit} файлов")
        return "\n".join(lines)

    def merge_scope_into_context(
        self,
        context: str,
        scope: tuple[str, list[str]] | None,
    ) -> str:
        """Добавляет listing выбранного каталога к RAG-контексту.

        Args:
            context: RAG-контекст (может быть пустым).
            scope: Область запроса (результат ``query_scope``).

        Returns:
            Объединённый текст (listing идёт первым).
        """
        listing = self.scope_listing(scope)
        return "\n\n".join(filter(None, [listing, context]))