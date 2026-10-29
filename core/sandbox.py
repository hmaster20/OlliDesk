"""Единый песочный резолвер путей для инструментов агента.

Объединяет логику `tools.file_ops._resolve_path` и `tools.shell_ops._resolve_cwd`,
которая раньше дублировалась в двух модулях. Все новые файловые инструменты
должны ходить через `SandboxedPathResolver.resolve`, а не реализовывать свою
проверку — иначе одна из копий неизбежно разойдётся с остальными.

Гарантии:
  * относительные пути резолвятся от project_root;
  * абсолютные — только если лежат внутри project_root (если корень задан);
  * выход за пределы проекта → None;
  * запись в `.git/` и `.ollidesk/` запрещена;
  * секретные файлы (0.1.2: `.env`, `*.pem`, `*.key`, `id_rsa*`, …)
    запрещены для ЧТЕНИЯ; запись разрешена через `allow_sensitive=True`.
"""

from __future__ import annotations

import re
from pathlib import Path

# Служебные каталоги проекта: .git — история и хуки,
# .ollidesk — конфиги, БД графа, индекс. Агент туда не должен ходить.
_DENIED_PARTS: frozenset[str] = frozenset({".git", ".ollidesk"})

# 0.1.2: точные имена файлов-секретов (case-insensitive).
# `.git/config` уже заблокирован через `.git` в `_DENIED_PARTS`.
_DENIED_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".netrc",
        ".pgpass",
        ".npmrc",
        ".pypirc",
        ".htpasswd",
        ".git-credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.yaml",
        "secrets.yml",
    }
)

# 0.1.2: паттерны (lowercase basename, match() — привязка к началу).
_DENIED_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\.env\..+$"),  # .env.local, .env.production, …
    re.compile(r"^.+\.pem$"),  # server.pem
    re.compile(r"^.+\.key$"),  # private.key
    re.compile(r"^.+\.p12$"),  # bundle.p12
    re.compile(r"^.+\.pfx$"),  # cert.pfx
    re.compile(r"^.+\.ppk$"),  # PuTTY
    re.compile(r"^.+\.kdbx$"),  # KeePass
    re.compile(r"^.+\.jks$"),  # Java keystore
    re.compile(r"^.+\.keystore$"),
    re.compile(r"^id_rsa(\..+)?$"),  # id_rsa, id_rsa.pub
    re.compile(r"^id_dsa(\..+)?$"),
    re.compile(r"^id_ecdsa(\..+)?$"),
    re.compile(r"^id_ed25519(\..+)?$"),
)


def is_denied_file(name: str) -> bool:
    """True, если basename — секретный файл (нельзя читать/листать).

    Args:
        name: Только имя файла (``path.name``), не путь.

    Returns:
        True для ``.env``, ``server.pem``, ``id_rsa``, ``secrets.yaml`` и т.п.
    """
    if not name:
        return False
    low = name.lower()
    if low in _DENIED_FILE_NAMES:
        return True
    return any(p.match(low) for p in _DENIED_FILE_PATTERNS)


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

    def resolve(self, path: str | Path, *, allow_sensitive: bool = False) -> Path | None:
        """Резолвит путь и проверяет, что он не выходит за проект.

        Args:
            path: Абсолютный или относительный (от project_root) путь.
            allow_sensitive: ``True`` — не блокировать секретные имена
                (``.env``, ``*.pem``, …). Нужно для ``write_file`` /
                ``apply_patch``: создавать ``.env`` агенту можно, а читать
                содержимое — нет (см. capability ``secrets`` — Фаза 2.6).

        Returns:
            Абсолютный ``Path`` внутри проекта или ``None``, если путь
            вышел за пределы, попал в служебный каталог/секрет (без
            ``allow_sensitive``), пуст или невалиден.
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
        if not allow_sensitive and is_denied_file(resolved.name):
            return None
        return resolved

    def contains(self, path: Path) -> bool:
        """True, если путь внутри проекта и не в служебном/секретном каталоге."""
        if self._root is None:
            return True
        try:
            rel = path.resolve().relative_to(self._root)
        except (ValueError, OSError):
            return False
        if any(part in _DENIED_PARTS for part in rel.parts):
            return False
        return not is_denied_file(path.name)
