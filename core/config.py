"""Загрузка и валидация конфигурации OlliDesk."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.exceptions import ConfigError


class ModelConfig(BaseModel):
    name: str
    provider: str = "ollama"
    model: str
    api_base: str = "http://localhost:11434"
    roles: list[Literal["chat", "apply", "embed"]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 8192
    keep_alive: int = 1800


class AppConfig(BaseModel):
    version: str = "1.0.0"
    schema_version: str = "v1"
    default_model: str = "qwen2.5-coder:7b"
    embed_model: str = "nomic-embed-text"
    models: list[ModelConfig] = Field(default_factory=list)
    tool_policies: dict[str, str] = Field(default_factory=lambda: {"read_file": "auto"})
    ignored_patterns: list[str] = Field(default_factory=list)
    index_extensions: list[str] = Field(default_factory=lambda: [".py", ".js", ".ts"])
    max_file_size_kb: int = 100
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 50
    rules_file: str = ".ollidesk/rules.md"


def _default_config_path() -> Path:
    return Path.home() / ".ollidesk" / "config.yaml"


def _project_config_path() -> Path | None:
    cwd = Path.cwd()
    project = cwd / ".ollidesk" / "config.yaml"
    return project if project.exists() else None


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or _project_config_path() or _default_config_path()

    if not config_path.exists():
        raise ConfigError(f"Конфиг не найден: {config_path}")

    try:
        raw = config_path.read_text(encoding="utf-8")
        data: dict[str, Any] = yaml.safe_load(raw)
        return AppConfig(**data)
    except yaml.YAMLError as e:
        raise ConfigError(f"Ошибка парсинга YAML: {e}") from e
    except Exception as e:
        raise ConfigError(f"Ошибка валидации конфига: {e}") from e
