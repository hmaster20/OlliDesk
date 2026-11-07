"""Единый песочный резолвер путей для инструментов агента.

Объединяет логику `tools.file_ops._resolve_path` и `tools.shell_ops._resolve_cwd`,
которая раньше дублировалась в двух модулях. Все новые файловые инструменты
должны ходить через `SandboxedPathResolver.resolve`, а не реализовывать свою
проверку — иначе одна из копий неизбежно разойдётся с остальными.

Гарантии:
  * относительные пути резолвятся от project_root;
  * абсолютные — только если лежат внутри project_root (если корень задан);
  * выход за пределы проекта → None;
  * запись в `.git/` и `.ollidesk/` запрещена.
"""

from __future__ import annotations

from pathlib import Path

# Служебные каталоги проекта: .git — история и хуки,
# .ollidesk — конфиги, БД графа, индекс. Агент туда не должен ходить.
_DENIED_PARTS: frozenset[str] = frozenset({".git", ".ollidesk"})


class SandboxedPathResolver:
    """Резолвер путей с проверкой принадлежности проекту."""

    def __init__(self, project_root: str | Path | None):
        """Инициализация.

        Args:
            project_root: Корень проекта. Если ``None``, любые пути резолвятся
                относительно cwd без ограничений (для тестов и инструментов,
                работающих вне проекта).
        """
        self._root: Path | None = Path(project_root).resolve() if project_root else None

    @property
    def root(self) -> Path | None:
        """Резолвнутый корень проекта (или ``None``)."""
        return self._root

    def resolve(self, path: str | Path) -> Path | None:
        """Резолвит путь и проверяет, что он не выходит за проект.

        Args:
            path: Абсолютный или относительный (от project_root) путь.

        Returns:
            Абсолютный ``Path`` внутри проекта или ``None``, если путь
            вышел за пределы, попал в служебный каталог, пуст или невалиден.
        """
        raw = str(path or "").strip()
        if not raw:
            return None
        p = Path(raw)

        try:
            if p.is_absolute():
                resolved = p.resolve()
            elif self._root is not None:
                resolved = (self._root / p).resolve()
            else:
                resolved = p.resolve()
        except OSError:
            return None

        if self._root is None:
            return resolved

        try:
            rel = resolved.relative_to(self._root)
        except ValueError:
            return None

        if any(part in _DENIED_PARTS for part in rel.parts):
            return None
        return resolved

    def contains(self, path: Path) -> bool:
        """True, если путь внутри проекта и не в служебном каталоге."""
        if self._root is None:
            return True
        try:
            rel = path.resolve().relative_to(self._root)
        except (ValueError, OSError):
            return False
        return not any(part in _DENIED_PARTS for part in rel.parts)
