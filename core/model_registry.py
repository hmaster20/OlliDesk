"""Реестр моделей: справочник с описаниями и кэш возможностей."""
import yaml
from datetime import datetime
from pathlib import Path
from loguru import logger
from pydantic import BaseModel, Field
from core.utils import get_app_data_dir

class ModelInfo(BaseModel):
    """Информация о модели в реестре."""
    name: str
    description: str = "Пользовательская модель. Отредактируйте описание в model_registry.yaml"
    recommended_modes: list[str] = Field(default_factory=lambda: ["chat"])
    supports_tools: bool | None = None  # None = неизвестно, True/False = проверено
    last_checked: str | None = None
    is_local: bool = False  # Скачана ли модель в Ollama

class ModelRegistry:
    """Управляет справочником моделей и их локальным статусом."""
    def __init__(self):
        # C:\Users\<USERNAME>\.ollidesk\model_registry.yaml
        self.file_path = get_app_data_dir() / "model_registry.yaml"
        self.models: dict[str, ModelInfo] = {}
        self._load_or_create()

    def _load_or_create(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for key, val in data.get("models", {}).items():
                    self.models[key] = ModelInfo(**val)
                logger.info(f"Загружен реестр моделей: {len(self.models)} записей")
            except Exception as e:
                logger.error(f"Ошибка загрузки model_registry.yaml: {e}")
                self._create_default()
        else:
            self._create_default()

    def _create_default(self):
        """Создает файл с дефолтным справочником популярных моделей."""
        logger.info("Создаю дефолтный справочник моделей (model_registry.yaml)")
        defaults = {
            "qwen2.5-coder:7b": ModelInfo(
                name="Qwen 2.5 Coder 7B",
                description="Отличная сбалансированная модель для написания и рефакторинга кода. Быстрая и эффективная.",
                recommended_modes=["chat", "plan", "agent"],
                supports_tools=True
            ),
            "qwen2.5-coder:32b": ModelInfo(
                name="Qwen 2.5 Coder 32B",
                description="Продвинутая версия для сложных архитектурных задач и рефакторинга больших файлов.",
                recommended_modes=["plan", "agent"],
                supports_tools=True
            ),
            "llama3.1:70b": ModelInfo(
                name="Llama 3.1 70B",
                description="Мощная коммерческая модель для сложных логических задач и агентных сценариев. Требует ~40-60 ГБ RAM.",
                recommended_modes=["agent", "plan"],
                supports_tools=True
            ),
            "qwen2.5:72b": ModelInfo(
                name="Qwen 2.5 72B",
                description="Одна из лучших открытых моделей общего назначения. Идеальна для глубокого анализа кода.",
                recommended_modes=["agent", "plan", "chat"],
                supports_tools=True
            ),
            "deepseek-r1:70b": ModelInfo(
                name="DeepSeek R1 Distill Llama 70B",
                description="Продвинутая модель с цепочкой рассуждений (Reasoning). Идеальна для тяжелых логических задач и планирования.",
                recommended_modes=["plan", "agent"],
                supports_tools=True
            ),
            "command-r-plus": ModelInfo(
                name="Command R+ (104B)",
                description="Огромная модель, отлично справляется с RAG и сложными агентными workflows. Требует ~60+ ГБ RAM.",
                recommended_modes=["agent"],
                supports_tools=True
            )
        }
        self.models = defaults
        self.save()

    def save(self):
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"models": {k: v.model_dump() for k, v in self.models.items()}}
            with open(self.file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.debug(f"Реестр моделей сохранен: {self.file_path}")
        except Exception as e:
            logger.error(f"Ошибка сохранения model_registry.yaml: {e}")

    def sync_with_ollama(self, ollama_model_names: list[str]):
        """Обновляет флаг is_local и добавляет неизвестные Ollama модели в реестр."""
        ollama_set = set(ollama_model_names)
        for key in self.models:
            self.models[key].is_local = key in ollama_set
        # Добавляем новые из Ollama, которых нет в справочнике
        for name in ollama_set:
            if name not in self.models:
                self.models[name] = ModelInfo(
                    name=name,
                    description="Модель добавлена из Ollama. Вы можете отредактировать её описание в model_registry.yaml",
                    recommended_modes=["chat"],
                    supports_tools=None,
                    is_local=True
                )
        self.save()

    def get(self, model_name: str) -> ModelInfo | None:
        return self.models.get(model_name)

    def get_all_models(self) -> dict[str, ModelInfo]:
        return self.models

    def update_tool_support(self, model_name: str, supports: bool):
        if model_name in self.models:
            self.models[model_name].supports_tools = supports
            self.models[model_name].last_checked = datetime.now().isoformat()
            self.save()

    def get_model_names(self) -> list[str]:
        return list(self.models.keys())
