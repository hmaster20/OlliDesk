"""Конфигурация OlliDesk."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

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
    PLAN = "plan"  # Read-only инструменты
    AGENT = "agent"  # Все инструменты


class ModelConfig(BaseModel):
    """Конфигурация одной модели Ollama."""

    name: str
    provider: Literal["ollama", "openai", "openai-compatible"]
    model: str
    api_base: str = "http://localhost:11434"
    roles: list[ModelRole] = Field(default_factory=lambda: [ModelRole.CHAT])
    capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 8192
    keep_alive: int = 1800
    reasoning: bool = False

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """temperature ∈ [0, 2]."""
        if not 0 <= v <= 2:
            raise ValueError(f"temperature must be in [0, 2], got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        """max_tokens > 0."""
        if v <= 0:
            raise ValueError(f"max_tokens must be > 0, got {v}")
        return v


class SLMModelInfo(BaseModel):
    """Описание одной доступной локальной SLM-модели (GGUF).

    kind="chat"  — используется как chat-модель (загружается с chat_format="chatml")
    kind="embed" — используется как embedding-модель (загружается с embedding=True)
    """

    id: str
    repo: str
    file: str
    display_name: str
    size_mb: int = 0
    kind: Literal["chat", "embed"] = "chat"
    # Окно контекста этой модели в токенах (0 = взять slm.n_ctx).
    # Задаётся per-model, потому что маленьким моделям KV-кэш дешёв,
    # а крупным — большой n_ctx упирается в VRAM/RAM.
    n_ctx: int = 0


_DEFAULT_SLM_MODELS: list[dict] = [
    {
        "id": "qwen2.5-14b-instruct",
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "file": "qwen2.5-14b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 14B (максимум качества, нужна мощная машина)",
        "size_mb": 9000,
        "kind": "chat",
        "n_ctx": 8192,
    },
    {
        "id": "qwen2.5-7b-instruct",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 7B (умная, рекомендуется)",
        "size_mb": 4680,
        "kind": "chat",
        "n_ctx": 8192,
    },
    {
        "id": "qwen2.5-3b-instruct",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 3B (баланс)",
        "size_mb": 2030,
        "kind": "chat",
        "n_ctx": 16384,
    },
    {
        "id": "qwen2.5-1.5b-instruct",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 1.5B (быстрая, для слабых машин)",
        "size_mb": 1120,
        "kind": "chat",
        "n_ctx": 16384,
    },
]


class SLMConfig(BaseModel):
    """Конфигурация встроенной SLM (llama.cpp)."""

    enabled: bool = True

    # Активная chat-модель (id из available_models, kind="chat")
    selected_model: str = "qwen2.5-7b-instruct"

    # Каталог доступных моделей.
    # Embedding-модель сюда НЕ добавляем — она задаётся отдельно ниже.
    available_models: list[SLMModelInfo] = Field(
        default_factory=lambda: [SLMModelInfo(**m) for m in _DEFAULT_SLM_MODELS]
    )

    @model_validator(mode="after")
    def _sync_builtin_models(self) -> "SLMConfig":
        """Обновляет встроенные модели до актуального каталога.

        Сохранённый в config.yaml список мог устареть (например, не знать
        про 14B или новое n_ctx). Поэтому метаданные встроенных моделей
        берём из кода, а пользовательские (не из ``_DEFAULT_SLM_MODELS``)
        оставляем как есть.
        """
        default_ids = {d["id"] for d in _DEFAULT_SLM_MODELS}
        custom = [m for m in self.available_models if m.id not in default_ids]
        self.available_models = [SLMModelInfo(**d) for d in _DEFAULT_SLM_MODELS] + custom
        return self

    # --- Параметры инференса ---
    n_ctx: int = 4096
    # n_threads: 0 = авто-определение по числу физических ядер (рекомендуется),
    # любое другое значение (>0) используется как есть. На гиперпоточном CPU
    # llama.cpp обычно выигрывает на числе физических ядер, а не логических.
    n_threads: int = 0
    n_batch: int = 512

    # GPU:
    #   auto_gpu_layers=True  → n_gpu_layers определяется автоматически
    #                            через nvidia-smi и размер модели
    #   auto_gpu_layers=False → используется значение n_gpu_layers как есть
    #                            (0 = CPU-only, -1 = все слои на GPU, N>0 = N слоёв)
    auto_gpu_layers: bool = True
    n_gpu_layers: int = 0

    max_tokens: int = 1024
    temperature: float = 0.7

    # --- Embedding-модель (одна, отдельно, не участвует в выборе chat) ---
    embed_model_repo: str = "nomic-ai/nomic-embed-text-v1.5-GGUF"
    embed_model_file: str = "nomic-embed-text-v1.5.Q4_K_M.gguf"

    # --- Хелперы ---

    def get_chat_models(self) -> list[SLMModelInfo]:
        """Только chat-модели (фильтрует embed)."""
        return [m for m in self.available_models if m.kind == "chat"]

    def get_selected_info(self) -> SLMModelInfo | None:
        """SLMModelInfo активной chat-модели.

        Ищем по id среди chat-моделей. Если не нашли — отдаём первую chat.
        Никогда не возвращаем embed-модель.
        """
        for m in self.available_models:
            if m.kind == "chat" and m.id == self.selected_model:
                return m
        # fallback — первая доступная chat
        for m in self.available_models:
            if m.kind == "chat":
                return m
        return None

    def get_model_by_id(self, model_id: str) -> SLMModelInfo | None:
        """Модель по id (любого kind). Для UI-таблицы."""
        for m in self.available_models:
            if m.id == model_id:
                return m
        return None


class ConfirmSettings(BaseModel):
    """Настройки подтверждения вызова инструментов.

    По умолчанию web_search выполняется без диалога: раздражает подтверждать
    каждый сетевой запрос. Диалог включается явно через confirm.web_search или
    общий флаг confirm.enabled.
    """

    enabled: bool = False
    web_search: bool = False


class BackendSettings(BaseModel):
    """Выбор LLM-бэкендов.

    Attributes:
        priority: Порядок предпочтения для чата. Допустимые значения —
            ``ollama`` и ``local_slm``. Бэкенды из списка, которых нет или
            которые недоступны, молча пропускаются; ``rule_based`` всегда
            последний.
        embed_backend: Как считать эмбеддинги для RAG: ``auto`` (следовать
            активному чат-бэкенду, при неудаче пробовать остальные),
            ``ollama`` или ``local_slm``.
    """

    priority: list[str] = Field(default_factory=lambda: ["local_slm", "ollama"])
    embed_backend: str = "auto"

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: list[str]) -> list[str]:
        """Только известные бэкенды, без повторов."""
        allowed = {"ollama", "local_slm"}
        unknown = [b for b in v if b not in allowed]
        if unknown:
            raise ValueError(f"Неизвестные бэкенды в priority: {unknown}")
        seen: list[str] = []
        for backend in v:
            if backend not in seen:
                seen.append(backend)
        return seen

    @field_validator("embed_backend")
    @classmethod
    def validate_embed_backend(cls, v: str) -> str:
        """Один из auto/ollama/local_slm."""
        allowed = {"auto", "ollama", "local_slm"}
        if v not in allowed:
            raise ValueError(f"embed_backend должен быть одним из {sorted(allowed)}, got {v!r}")
        return v


class RerankSettings(BaseModel):
    """Реранкинг RAG-выдачи кросс-энкодером (sentence-transformers).

    Кросс-энкодер оценивает пару (запрос, чанк) совместно и точнее би-энкодера
    векторного поиска, но считается на CPU, а модель скачивается при первом
    использовании. Поэтому по умолчанию выключен и включается явно.

    Attributes:
        enabled: Включён ли реранкинг.
        model: HF-id модели-кросс-энкодера (например, ``BAAI/bge-reranker-v2-m3``).
        top_k: Сколько лучших кандидатов после RRF подавать на реранк.
    """

    enabled: bool = False
    model: str = "BAAI/bge-reranker-v2-m3"
    top_k: int = 20

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        """top_k не меньше 1."""
        if v < 1:
            raise ValueError(f"top_k must be >= 1, got {v}")
        return v


class EmbedModels(BaseModel):
    """Раздельные модели эмбеддингов по категории файлов.

    Позволяет обслуживать код и документацию разными моделями (например,
    code-специализированной для кода и текстовой для markdown). Векторы разных
    моделей несравнимы, поэтому каждая модель получает свою коллекцию, а выдачи
    объединяются через RRF (`fs.vector_store.VectorStore`). Пустое значение
    категории означает общий ``embed_model``.

    Attributes:
        code: Модель для кода (python, javascript, …); ``None`` — ``embed_model``.
        docs: Модель для документации (markdown, rst, text); ``None`` — ``embed_model``.
    """

    code: str | None = None
    docs: str | None = None


class AppConfig(BaseModel):
    """Главная конфигурация приложения."""

    version: str = "1.0.0"
    schema_version: str = "v1"
    default_model: str
    embed_model: str
    embed_models: EmbedModels = Field(default_factory=EmbedModels)
    models: list[ModelConfig] = Field(default_factory=list)
    tool_policies: dict[str, ToolPolicy] = Field(default_factory=dict)
    confirm: ConfirmSettings = Field(default_factory=ConfirmSettings)
    backends: BackendSettings = Field(default_factory=BackendSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    slm: SLMConfig = Field(default_factory=SLMConfig)
    ignored_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".gitignore",
            ".gitattributes",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".cache",
            ".tox",
            "venv",
            ".venv",
            "env",
            ".env",
            "dist",
            "build",
            "target",
            "*.egg-info",
            ".eggs",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            "*.min.js",
            "*.min.css",
            ".DS_Store",
            "Thumbs.db",
            ".ollidesk/vector_db",
            ".ollidesk/chroma",
            ".ollidesk/*.sqlite3",
            ".ollidesk/*.db",
            "logs",
            "log",
            "tmp",
            "temp",
            "vendor",
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
    last_mode: str = "agent"
    agent_max_iterations: int = 30
    chat_max_iterations: int = 7

    @field_validator("recent_projects")
    @classmethod
    def validate_recent_projects(cls, v: list[str]) -> list[str]:
        """Не больше 10 проектов."""
        return v[:10]

    @field_validator("agent_max_iterations", "chat_max_iterations")
    @classmethod
    def validate_iterations(cls, v: int) -> int:
        """Лимит итераций агента/чата не меньше 1."""
        if v < 1:
            raise ValueError(f"iterations must be >= 1, got {v}")
        return v

    @field_validator("max_file_size_kb")
    @classmethod
    def validate_max_file_size(cls, v: int) -> int:
        """max_file_size_kb > 0."""
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
        """Загружает глобальный и локальный конфиги, мержит (локальный важнее).

        Args:
            project_path: путь к проекту (опционально)

        Returns:
            AppConfig

        Raises:
            ConfigError: если конфиг невалиден
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
        """Загружает YAML или возвращает пустой dict."""
        if not path.exists():
            logger.debug(f"Конфиг не найден: {path}")
            return {}

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Ошибка парсинга YAML {path}: {e}") from e

    def _merge_configs(self, global_config: dict[str, Any], local_config: dict[str, Any]) -> dict[str, Any]:
        """Мержит два конфига, приоритет — за локальным."""
        merged = global_config.copy()

        for key, value in local_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
            elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = value
            else:
                merged[key] = value

        return merged

    def save_config(self, config: AppConfig, project_path: Path | None = None) -> None:
        """Сохраняет конфигурацию в YAML."""
        logger.info(
            f"💾 Сохранение конфига: last_model='{config.last_model}', "
            f"last_mode='{config.last_mode}', project_path={project_path}"
        )
        config_dict = config.model_dump(mode="json")
        if project_path:
            local_config_path = project_path / ".ollidesk" / "config.yaml"
            self._save_yaml(config_dict, local_config_path)
        else:
            self._save_yaml(config_dict, self.global_config_path)

    def _save_yaml(self, data: dict[str, Any], path: Path) -> None:
        """Сохраняет данные в YAML-файл."""
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            logger.info(f"Конфигурация сохранена: {path}")
        except Exception as e:
            raise ConfigError(f"Ошибка сохранения конфигурации {path}: {e}") from e


# Глобальный загрузчик
config_loader = ConfigLoader()


def _detect_project_path() -> Path | None:
    """Определяет путь к проекту по CWD."""
    cwd = Path.cwd()
    if (cwd / ".ollidesk" / "config.yaml").exists():
        return cwd
    return None


def load_config(project_path: Path | None = None) -> AppConfig:
    """Обёртка загрузки конфига.

    Если project_path не указан, пытается определить по CWD.
    """
    if project_path is None:
        project_path = _detect_project_path()
    return config_loader.load_config(project_path)


def save_config(config: AppConfig, project_path: Path | None = None) -> None:
    """Обёртка сохранения конфига."""
    config_loader.save_config(config, project_path)
    logger.info(f"Сохранение конфига: last_model={config.last_model}, last_mode={config.last_mode}")


def get_model_by_role(config: AppConfig, role: ModelRole) -> ModelConfig:
    """Возвращает первую модель с указанной ролью."""
    for model in config.models:
        if role in model.roles:
            return model
    raise ConfigError(f"Модель с ролью {role.value} не найдена в конфигурации")
