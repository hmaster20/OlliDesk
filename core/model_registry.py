"""Реестр Ollama-моделей: справочник с описаниями и кэш возможностей.

Относится только к бэкенду Ollama (SLM-модели живут в slm_models.yaml).
Когда Ollama выключена в backends.enabled, объект всё равно полностью
сконструирован и пригоден к использованию (пустой in-memory справочник),
но не читает, не создаёт и не сохраняет model_registry.yaml.
"""
from datetime import datetime

import yaml
from loguru import logger
from pydantic import BaseModel, Field

from core.utils import get_app_data_dir


class ModelInfo(BaseModel):
    """Информация о модели в реестре."""

    name: str
    description: str = "Пользовательская модель. Отредактируйте описание в model_registry.yaml"
    recommended_modes: list[str] = Field(default_factory=lambda: ["chat"])
    supports_tools: bool | None = None
    last_checked: str | None = None
    is_local: bool = False


class ModelRegistry:
    """Управляет справочником Ollama-моделей и их локальным статусом.

    Инвариант: после __init__ ВСЕГДА существуют self.models и self.file_path.
    Ранний return до инициализации полей ломал SLM-only конфигурацию
    (AttributeError в ChatPanel._update_model_list).
    """

    def __init__(self) -> None:
        # 1. Полная конструкция объекта — без ранних return.
        self.file_path = get_app_data_dir() / "model_registry.yaml"
        self.models: dict[str, ModelInfo] = {}
        # 2. Только I/O зависит от того, включена ли Ollama.
        if self._ollama_enabled():
            self._load_or_create()
        else:
            logger.debug("Ollama выключена — model_registry.yaml не читается и не создаётся")

    @staticmethod
    def _ollama_enabled() -> bool:
        """Ollama в backends.enabled? Ошибка чтения конфига не роняет объект."""
        try:
            from core.config import load_config

            return "ollama" in load_config().backends.enabled
        except Exception as e:
            logger.warning(f"Не удалось проверить бэкенды для реестра моделей: {e}")
            return False

    def _load_or_create(self) -> None:
        if self.file_path.exists():
            try:
                with open(self.file_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for key, val in data.get("models", {}).items():
                    self.models[key] = ModelInfo(**val)
                logger.info(f"Загружен реестр моделей: {len(self.models)} записей")
            except Exception as e:
                logger.error(f"Ошибка загрузки model_registry.yaml: {e}")
                self._create_default()
        else:
            self._create_default()

    def _create_default(self) -> None:
        """Создает файл с дефолтным справочником популярных моделей."""
        logger.info("Создаю дефолтный справочник моделей (model_registry.yaml)")
        defaults = {
            "qwen2.5-coder:7b": ModelInfo(name="Qwen 2.5 Coder 7B", description="Отличная сбалансированная модель для кода.", recommended_modes=["chat", "plan", "agent"], supports_tools=True),
            "qwen2.5-coder:32b": ModelInfo(name="Qwen 2.5 Coder 32B", description="Продвинутая версия для сложных задач.", recommended_modes=["plan", "agent"], supports_tools=True),
            "llama3.1:70b": ModelInfo(name="Llama 3.1 70B", description="Мощная модель для сложных логических задач.", recommended_modes=["agent", "plan"], supports_tools=True),
            "qwen2.5:72b": ModelInfo(name="Qwen 2.5 72B", description="Одна из лучших открытых моделей общего назначения.", recommended_modes=["agent", "plan", "chat"], supports_tools=True),
            "deepseek-r1:70b": ModelInfo(name="DeepSeek R1 Distill Llama 70B", description="Модель с цепочкой рассуждений.", recommended_modes=["plan", "agent"], supports_tools=True),
            "command-r-plus": ModelInfo(name="Command R+ (104B)", description="Отлично справляется с RAG.", recommended_modes=["agent"], supports_tools=True),
        }
        self.models = defaults
        self.save()

    def save(self) -> None:
        # Проверяем динамически: Ollama могли выключить уже после создания реестра.
        if not self._ollama_enabled():
            return
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"models": {k: v.model_dump() for k, v in self.models.items()}}
            with open(self.file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.debug(f"Реестр моделей сохранен: {self.file_path}")
        except Exception as e:
            logger.error(f"Ошибка сохранения model_registry.yaml: {e}")

    def sync_with_ollama(self, ollama_model_names: list[str]) -> None:
        """Обновляет флаг is_local и добавляет неизвестные Ollama-модели.

        Вызывается только при реальном подключении к Ollama. Если реестр
        пуст (Ollama была выключена на старте и включена в этой сессии) —
        сначала подгружаем справочник, чтобы модели получили описания.
        """
        if not self.models:
            self._load_or_create()
        ollama_set = set(ollama_model_names)
        for key in self.models:
            self.models[key].is_local = key in ollama_set
        for name in ollama_set:
            if name not in self.models:
                self.models[name] = ModelInfo(
                    name=name,
                    description="Модель добавлена из Ollama.",
                    recommended_modes=["chat"],
                    supports_tools=None,
                    is_local=True,
                )
        self.save()

    def get(self, model_name: str) -> ModelInfo | None:
        return self.models.get(model_name)

    def get_all_models(self) -> dict[str, ModelInfo]:
        return self.models

    def update_tool_support(self, model_name: str, supports: bool) -> None:
        if model_name in self.models:
            self.models[model_name].supports_tools = supports
            self.models[model_name].last_checked = datetime.now().isoformat()
            self.save()

    def get_model_names(self) -> list[str]:
        return list(self.models.keys())