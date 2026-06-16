"""Управление кэшем возможностей моделей Ollama."""
from datetime import datetime
from pathlib import Path
import yaml
from loguru import logger
from pydantic import BaseModel
from core.utils import get_app_data_dir

class ModelCapability(BaseModel):
    """Возможности конкретной модели."""
    supports_tools: bool = False
    recommended_mode: str = "chat"  # chat, plan, agent
    last_checked: str = ""

class ModelCapabilitiesStore:
    """Хранилище возможностей моделей с персистентностью в YAML."""
    def __init__(self):
        self.file_path = get_app_data_dir() / "model_capabilities.yaml"
        self.capabilities: dict[str, ModelCapability] = {}
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    for name, cap_data in data.get("models", {}).items():
                        self.capabilities[name] = ModelCapability(**cap_data)
                logger.debug(f"Загружено возможностей моделей: {len(self.capabilities)}")
            except Exception as e:
                logger.error(f"Ошибка загрузки model_capabilities.yaml: {e}")

    def save(self):
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"models": {k: v.model_dump() for k, v in self.capabilities.items()}}
            with open(self.file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.debug(f"Сохранено возможностей моделей: {len(self.capabilities)}")
        except Exception as e:
            logger.error(f"Ошибка сохранения model_capabilities.yaml: {e}")

    def get(self, model_name: str) -> ModelCapability | None:
        return self.capabilities.get(model_name)

    def update(self, model_name: str, supports_tools: bool):
        """Обновляет статус модели и сохраняет."""
        mode = "agent" if supports_tools else "chat"
        self.capabilities[model_name] = ModelCapability(
            supports_tools=supports_tools,
            recommended_mode=mode,
            last_checked=datetime.now().isoformat()
        )
