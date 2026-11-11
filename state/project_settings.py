"""Project-specific settings persistence."""

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel


class ProjectSettings(BaseModel):
    """Настройки проекта, сохраняемые между сессиями.

    [9.4] Поля git_* фиксируют «снимок» контекста при открытии проекта:
    ветка, hash, время последней синхронизации. Используются для
    `AgentContext.git_context` и индикации в UI.
    """

    model: str = ""
    mode: str = "chat"
    temperature: float = 0.7
    max_tokens: int = 4096
    # Показывать reasoning_content модели в чате. False — рассуждения
    # скрываются, но всё равно пишутся в stderr (logger) для отладки.
    # Настройка per-project: в одном проекте удобно видеть мысли, в другом —
    # нет (например, при демонстрации).
    show_reasoning: bool = True

    # [FIX] Сохраняем выбор пайплайна для конкретного проекта
    planner_model_id: str = ""
    analyst_model_id: str = ""

    # [9.4] Git-контекст на момент последнего сохранения.
    git_branch: str = ""
    git_hash: str = ""
    git_last_sync: str = ""  # ISO timestamp

    def save(self, project_path: Path) -> None:
        """Сохраняет настройки в .ollidesk/project_settings.yaml."""
        settings_dir = project_path / ".ollidesk"
        settings_dir.mkdir(parents=True, exist_ok=True)
        path = settings_dir / "project_settings.yaml"
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(
                    self.model_dump(mode="json"),
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек проекта: {e}")

    @classmethod
    def load(cls, project_path: Path) -> "ProjectSettings":
        """Загружает настройки проекта из .ollidesk/project_settings.yaml."""
        path = project_path / ".ollidesk" / "project_settings.yaml"
        if not path.exists():
            return cls()
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек проекта: {e}")
            return cls()


def snapshot_git_state(project_path: Path) -> dict[str, str]:
    """[9.4] Снимок git-состояния проекта: ветка, hash, timestamp.

    Используется `ChatPanel` при открытии проекта / перед запросом —
    чтобы `ProjectSettings.git_branch/hash` не отставали от реальности.

    Args:
        project_path: Корень проекта.

    Returns:
        dict ``{"branch": ..., "hash": ..., "timestamp": ...}``. Пустые
        значения, если проект не под git.
    """
    from datetime import datetime

    from tools.git_ops import get_branch_hash, get_current_branch

    return {
        "branch": get_current_branch(project_path) or "",
        "hash": get_branch_hash(project_path) or "",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }