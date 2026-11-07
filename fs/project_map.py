"""Компактная карта структуры проекта для системного промпта и поиска.

Модели показывается дерево каталогов и файлов проекта, чтобы она знала, что
в проекте есть (например, каталог `cloud/` с ролями), и могла читать нужные
файлы инструментами, а не опираться только на случайную RAG-выборку.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from core.config import AppConfig
from fs.indexer import FileIndexer

# Токен запроса: кусок без пробелов и частых знаков препинания. Так в тексте
# «изучи роли в каталоге cloud/» находится токен `cloud/`.
_TOKEN_RE = re.compile(r"[^\s,.;:!?()\[\]{}<>\"'«»]+")


def list_project_files(project_path: Path, config: AppConfig) -> list[str]:
    """Возвращает относительные пути файлов проекта (как в индексе).

    Args:
        project_path: Корень проекта.
        config: Конфигурация с игнор-паттернами и расширениями.

    Returns:
        Отсортированный список относительных путей.
    """
    try:
        return FileIndexer(config, project_path).list_project_files(project_path)
    except Exception as e:
        logger.warning(f"Не удалось получить список файлов проекта: {e}")
        return []


def render_project_map(
    paths: list[str],
    max_depth: int = 3,
    max_entries: int = 250,
) -> str:
    """Строит компактное дерево каталогов и файлов.

    Args:
        paths: Относительные пути файлов (со слэшами).
        max_depth: Максимальная глубина дерева.
        max_entries: Максимум строк в карте.

    Returns:
        Текст карты или пустая строка, если путей нет.
    """
    if not paths:
        return ""

    tree: dict[str, Any] = {}
    for path in paths:
        node = tree
        for part in path.split("/"):
            node = node.setdefault(part, {})

    lines: list[str] = []
    counter = 0
    truncated = False

    def walk(node: dict[str, Any], depth: int) -> None:
        nonlocal counter, truncated
        if truncated or depth > max_depth:
            return
        dirs = sorted(k for k, v in node.items() if v)
        files = sorted(k for k, v in node.items() if not v)
        for name in dirs:
            if counter >= max_entries:
                truncated = True
                return
            lines.append("  " * depth + name + "/")
            counter += 1
            walk(node[name], depth + 1)
        for name in files:
            if counter >= max_entries:
                truncated = True
                return
            lines.append("  " * depth + name)
            counter += 1

    walk(tree, 0)
    if truncated:
        lines.append("  … (список усечён)")
    return "Структура проекта:\n" + "\n".join(lines)


def _project_dirs(files: list[str]) -> set[str]:
    """Собирает множество каталогов проекта из путей файлов.

    Args:
        files: Относительные пути файлов.

    Returns:
        Множество относительных путей каталогов.
    """
    dirs: set[str] = set()
    for path in files:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return dirs


def detect_query_scope(
    text: str,
    files: list[str],
) -> tuple[str, list[str]] | None:
    """Ищет в запросе каталог проекта, чтобы сузить RAG-поиск.

    Если пользователь пишет «изучи роли в каталоге cloud», а в проекте есть
    каталог `cloud/`, поиск нужно вести именно по нему, а не по всей базе.

    Args:
        text: Текст запроса пользователя.
        files: Относительные пути файлов проекта.

    Returns:
        Пару (относительный путь каталога, файлы внутри) или ``None``.
    """
    if not text or not files:
        return None

    dirs = _project_dirs(files)
    if not dirs:
        return None

    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        token = raw.strip("/\\").rstrip(".,;:!?")
        if len(token) < 2 or token.lower() in seen:
            continue
        seen.add(token.lower())
        needle = token.replace("\\", "/").lower()

        exact = [d for d in dirs if d.lower() == needle]
        if exact:
            matches = exact
        else:
            matches = [
                d for d in dirs if d.lower().endswith("/" + needle) or d.split("/")[-1].lower() == needle
            ]
        if not matches:
            continue

        rel = min(matches, key=len)
        scoped = [f for f in files if f.startswith(rel + "/")]
        if scoped:
            return rel, scoped
    return None
