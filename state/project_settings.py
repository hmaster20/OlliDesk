"""Project-specific settings persistence."""

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel


class ProjectSettings(BaseModel):
    """Настройки проекта, сохраняемые между сессиями."""

    model: str = ""
    mode: str = "chat"
    temperature: float = 0.7
    max_tokens: int = 4096

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
