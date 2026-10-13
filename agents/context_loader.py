"""Загрузка AGENTS.md — постоянного контекста проекта (opencode-style).

Порядок приоритета (первый — самый важный, все склеиваются):

1. `<project>/AGENTS.md` — основная конвенция opencode / Cursor.
2. `<project>/.ollidesk/AGENTS.md` — локальная конвенция OlliDesk.
3. `~/.ollidesk/AGENTS.md` — глобальный файл, действует на все проекты.

Файлы читаются с защитой: отсутствие/нечитаемость — тихий пропуск.
Склейка идёт через разделитель с указанием источника, чтобы модель
понимала происхождение каждого блока.

Потребитель — `ui/chat/panel.py`: результат кладётся в
`AgentContext.agents_context`, который в системном промпте идёт как
**отдельная приоритетная секция** (обрезается последней после rag/map/rules).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from core.utils import get_app_data_dir

AGENTS_FILENAME = "AGENTS.md"

# Разделитель между блоками от разных источников.
_SEPARATOR = "\n\n---\n\n"


def load_agents_context(project_root: str | Path | None) -> str:
    """Собирает все AGENTS.md, применимые к проекту, в один блок.

    Args:
        project_root: Корень проекта или ``None`` (тогда только глобальный).

    Returns:
        Склеенный текст контекста или пустая строка, если ни одного файла нет.
    """
    blocks: list[tuple[str, str]] = []

    if project_root:
        root = Path(project_root)
        candidates: list[tuple[Path, str]] = [
            (root / AGENTS_FILENAME, "AGENTS.md"),
            (root / ".ollidesk" / AGENTS_FILENAME, ".ollidesk/AGENTS.md"),
        ]
        for path, label in candidates:
            content = _read_file(path)
            if content:
                blocks.append((label, content))

    global_path = get_app_data_dir() / AGENTS_FILENAME
    content = _read_file(global_path)
    if content:
        blocks.append(("~/.ollidesk/AGENTS.md", content))

    if not blocks:
        return ""

    parts = [f"<!-- источник: {label} -->\n{text}" for label, text in blocks]
    joined = _SEPARATOR.join(parts)
    logger.debug(
        f"AGENTS.md: загружено {len(blocks)} файл(ов), "
        f"итого {len(joined)} символов"
    )
    return joined


def _read_file(path: Path) -> str:
    """Читает файл с защитой; пустая строка при любой ошибке/пустом файле."""
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return ""
        logger.debug(f"AGENTS.md: {path} ({len(text)} символов)")
        return text
    except OSError as e:
        logger.debug(f"AGENTS.md: не удалось прочитать {path}: {e}")
        return ""