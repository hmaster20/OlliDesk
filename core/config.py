"""Конфигурация OlliDesk."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from core.exceptions import ConfigError


class ModelRole(StrEnum):
    """Роль модели в системе."""

    CHAT = "chat"
    EMBED = "embed"
    RERANK = "rerank"
    APPLY = "apply"
    AUTOCOMPLETE = "autocomplete"


class ToolPolicy(StrEnum):
    """Политика выполнения инструмента."""

    AUTO = "auto"  # Выполнять без подтверждения
    ASK_FIRST = "ask_first"  # Требовать подтверждения пользователя
    EXCLUDED = "excluded"  # Исключить из использования


class AgentMode(StrEnum):
    """Режим работы агента."""

    CHAT = "chat"  # Только диалог, без инструментов
    PLAN = "plan"  # Read-only инструменты (чтение файлов, поиск)
    AGENT = "agent"  # Все инструменты (чтение, запись, выполнение)


class ModelConfig(BaseModel):
    """Конфигурация одной модели."""

    name: str
    provider: Literal["ollama", "openai", "openai-compatible"]
    model: str
    api_base: str = "http://localhost:11434"
    roles: list[ModelRole] = Field(default_factory=lambda: [ModelRole.CHAT])
    capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 8192
    keep_alive: int = 1800  # секунды, для Ollama
    reasoning: bool = False

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """Проверяет, что temperature в диапазоне [0, 2]."""
        if not 0 <= v <= 2:
            raise ValueError(f"temperature must be in [0, 2], got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        """Проверяет, что max_tokens > 0."""
        if v <= 0:
            raise ValueError(f"max_tokens must be > 0, got {v}")
        return v


class AppConfig(BaseModel):
    """Главная конфигурация приложения."""

    version: str = "1.0.0"
    schema_version: str = "v1"
    default_model: str
    embed_model: str
    rerank_model: str | None = None
    models: list[ModelConfig] = Field(default_factory=list)
    tool_policies: dict[str, ToolPolicy] = Field(default_factory=dict)
    ignored_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git",
            "node_modules",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            ".env",
            "*.pyc",
        ]
    )
    index_extensions: list[str] = Field(
        default_factory=lambda: [
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".html",
            ".css",
            ".md",
            ".json",
            ".yaml",
            ".yml",
        ]
    )
    max_file_size_kb: int = 100
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 50
    rules_file: str = ".ollidesk/rules.md"
    recent_projects: list[str] = Field(default_factory=list)
    wizard_dismissed: bool = False
    theme: str = "light"
    last_model: str = ""
    last_mode: str = "chat"

    @field_validator("recent_projects")
    @classmethod
    def validate_recent_projects(cls, v: list[str]) -> list[str]:
        """Ограничивает список последних проектов до 10 элементов."""
        return v[:10]

    @field_validator("max_file_size_kb")
    @classmethod
    def validate_max_file_size(cls, v: int) -> int:
        """Проверяет, что max_file_size_kb > 0."""
        if v <= 0:
            raise ValueError(f"max_file_size_kb must be > 0, got {v}")
        return v


class ConfigLoader:
    """Загрузчик и мержер конфигурации."""

    def __init__(self) -> None:
        """Инициализация загрузчика."""
        from core.utils import get_app_data_dir
        self.global_config_path = get_app_data_dir() / "config.yaml"

    def load_config(self, project_path: Path | None = None) -> AppConfig:
        """
        Загружает глобальный и локальный конфиги, мержит с приоритетом локального.

        Args:
            project_path: Путь к проекту (опционально)

        Returns:
            AppConfig: Объединенная конфигурация

        Raises:
            ConfigError: Если конфиг не найден или невалиден
        """
        global_config = self._load_yaml(self.global_config_path)

        if project_path:
            local_config_path = project_path / ".ollidesk" / "config.yaml"
            local_config = self._load_yaml(local_config_path)
            merged_config = self._merge_configs(global_config, local_config)
        else:
            merged_config = global_config

        try:
            return AppConfig(**merged_config)
        except Exception as e:
            raise ConfigError(f"Ошибка валидации конфигурации: {e}") from e

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        """
        Загружает YAML-файл с поддержкой якорей.

        Args:
            path: Путь к YAML-файлу

        Returns:
            dict: Распарсенный YAML или пустой словарь, если файл не найден
        """
        if not path.exists():
            logger.debug(f"Конфиг не найден: {path}")
            return {}

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Ошибка парсинга YAML {path}: {e}") from e

    def _merge_configs(
        self, global_config: dict[str, Any], local_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Мержит два конфига с приоритетом локального.

        Args:
            global_config: Глобальный конфиг
            local_config: Локальный конфиг

        Returns:
            dict: Объединенный конфиг
        """
        merged = global_config.copy()

        for key, value in local_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                # Рекурсивный мерж для словарей (например, tool_policies)
                merged[key] = {**merged[key], **value}
            elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
                # Для списков (например, models) — заменяем полностью
                merged[key] = value
            else:
                # Для остальных типов — перезаписываем
                merged[key] = value

        return merged

    def save_config(self, config: AppConfig, project_path: Path | None = None) -> None:
        """
        Сохраняет конфигурацию в YAML-файл.

        Args:
            config: Конфигурация для сохранения
            project_path: Путь к проекту (опционально)
        """
        config_dict = config.model_dump(mode="json")

        if project_path:
            local_config_path = project_path / ".ollidesk" / "config.yaml"
            self._save_yaml(config_dict, local_config_path)
        else:
            self._save_yaml(config_dict, self.global_config_path)

    def _save_yaml(self, data: dict[str, Any], path: Path) -> None:
        """
        Сохраняет данные в YAML-файл.

        Args:
            data: Данные для сохранения
            path: Путь к файлу
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            logger.info(f"Конфигурация сохранена: {path}")
        except Exception as e:
            raise ConfigError(f"Ошибка сохранения конфигурации {path}: {e}") from e


# Глобальный загрузчик (используется в main.py)
config_loader = ConfigLoader()


def _detect_project_path() -> Path | None:
    """Определяет путь к проекту, проверяя CWD."""
    cwd = Path.cwd()
    if (cwd / ".ollidesk" / "config.yaml").exists():
        return cwd
    return None


def load_config(project_path: Path | None = None) -> AppConfig:
    """
    Загружает конфигурацию (обертка для удобства).

    Args:
        project_path: Путь к проекту (опционально).
        Если не указан, пытается определить по CWD.

    Returns:
        AppConfig: Конфигурация
    """
    if project_path is None:
        project_path = _detect_project_path()
    return config_loader.load_config(project_path)


def save_config(config: AppConfig, project_path: Path | None = None) -> None:
    """
    Сохраняет конфигурацию (обертка для удобства).

    Args:
        config: Конфигурация для сохранения
        project_path: Путь к проекту (опционально)
    """
    config_loader.save_config(config, project_path)


def get_model_by_role(config: AppConfig, role: ModelRole) -> ModelConfig:
    """
    Возвращает первую модель с указанной ролью.

    Args:
        config: Конфигурация
        role: Роль модели

    Returns:
        ModelConfig: Модель с указанной ролью

    Raises:
        ConfigError: Если модель не найдена
    """
    for model in config.models:
        if role in model.roles:
            return model

    raise ConfigError(f"Модель с ролью {role.value} не найдена в конфигурации")
