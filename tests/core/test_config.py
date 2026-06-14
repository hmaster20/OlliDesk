from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from core.config import (
    AgentMode,
    AppConfig,
    ConfigLoader,
    ModelConfig,
    ModelRole,
    ToolPolicy,
    get_model_by_role,
)
from core.exceptions import ConfigError

SAMPLE_GLOBAL_CONFIG = {
    "version": "1.0.0",
    "schema_version": "v1",
    "default_model": "qwen2.5-coder:7b",
    "embed_model": "nomic-embed-text",
    "models": [
        {
            "name": "Qwen 2.5 Coder 7B",
            "provider": "ollama",
            "model": "qwen2.5-coder:7b",
            "api_base": "http://localhost:11434",
            "roles": ["chat", "apply"],
            "capabilities": ["tool_use"],
            "temperature": 0.7,
            "max_tokens": 2048,
            "context_length": 8192,
            "keep_alive": 1800,
        },
        {
            "name": "Nomic Embed Text",
            "provider": "ollama",
            "model": "nomic-embed-text",
            "api_base": "http://localhost:11434",
            "roles": ["embed"],
            "context_length": 8192,
        },
    ],
    "tool_policies": {
        "write_file": "ask_first",
        "web_search": "ask_first",
        "read_file": "auto",
    },
    "ignored_patterns": [".git", "node_modules", "__pycache__"],
    "index_extensions": [".py", ".js", ".ts"],
    "max_file_size_kb": 100,
    "recent_projects": [],
}


def _write_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


class TestAppConfig:
    def test_valid_config(self):
        config = AppConfig(**SAMPLE_GLOBAL_CONFIG)
        assert config.default_model == "qwen2.5-coder:7b"
        assert config.embed_model == "nomic-embed-text"
        assert len(config.models) == 2
        assert config.models[0].name == "Qwen 2.5 Coder 7B"
        assert config.models[0].provider == "ollama"
        assert ModelRole.CHAT in config.models[0].roles
        assert ModelRole.EMBED in config.models[1].roles

    def test_invalid_temperature(self):
        with pytest.raises(ValidationError):
            ModelConfig(
                name="test",
                provider="ollama",
                model="test",
                roles=[ModelRole.CHAT],
                temperature=3.0,
            )

    def test_invalid_max_tokens(self):
        with pytest.raises(ValidationError):
            ModelConfig(
                name="test",
                provider="ollama",
                model="test",
                roles=[ModelRole.CHAT],
                max_tokens=0,
            )

    def test_recent_projects_truncated(self):
        config = AppConfig(
            default_model="test",
            embed_model="test",
            recent_projects=[str(i) for i in range(20)],
        )
        assert len(config.recent_projects) == 10

    def test_max_file_size_zero(self):
        with pytest.raises(ValidationError):
            AppConfig(default_model="test", embed_model="test", max_file_size_kb=0)

    def test_empty_models(self):
        config = AppConfig(default_model="test", embed_model="test")
        assert config.models == []


class TestConfigLoader:
    def test_load_global_only(self, tmp_path: Path):
        global_config = tmp_path / ".ollidesk" / "config.yaml"
        _write_yaml(global_config, SAMPLE_GLOBAL_CONFIG)

        loader = ConfigLoader()
        loader.global_config_path = global_config

        config = loader.load_config()
        assert config.default_model == "qwen2.5-coder:7b"

    def test_merge_local_overrides_global(self, tmp_path: Path):
        global_config = tmp_path / ".ollidesk" / "config.yaml"
        _write_yaml(global_config, SAMPLE_GLOBAL_CONFIG)

        project = tmp_path / "project"
        project.mkdir()
        local_config = project / ".ollidesk" / "config.yaml"
        _write_yaml(local_config, {"default_model": "custom-model", "max_file_size_kb": 200})

        loader = ConfigLoader()
        loader.global_config_path = global_config

        config = loader.load_config(project)
        assert config.default_model == "custom-model"
        assert config.max_file_size_kb == 200
        assert config.embed_model == "nomic-embed-text"

    def test_merge_local_models_replace(self, tmp_path: Path):
        global_config = tmp_path / ".ollidesk" / "config.yaml"
        _write_yaml(global_config, SAMPLE_GLOBAL_CONFIG)

        project = tmp_path / "project"
        project.mkdir()
        local_config = project / ".ollidesk" / "config.yaml"
        local_models = [
            {
                "name": "Local Model",
                "provider": "ollama",
                "model": "local-model",
                "roles": ["chat"],
            },
        ]
        _write_yaml(local_config, {"models": local_models})

        loader = ConfigLoader()
        loader.global_config_path = global_config

        config = loader.load_config(project)
        assert len(config.models) == 1
        assert config.models[0].name == "Local Model"

    def test_merge_tool_policies_deep(self, tmp_path: Path):
        global_config = tmp_path / ".ollidesk" / "config.yaml"
        _write_yaml(global_config, SAMPLE_GLOBAL_CONFIG)

        project = tmp_path / "project"
        project.mkdir()
        local_config = project / ".ollidesk" / "config.yaml"
        _write_yaml(local_config, {"tool_policies": {"write_file": "auto"}})

        loader = ConfigLoader()
        loader.global_config_path = global_config

        config = loader.load_config(project)
        assert config.tool_policies["write_file"] == ToolPolicy.AUTO
        assert config.tool_policies["web_search"] == ToolPolicy.ASK_FIRST

    def test_missing_global_config(self, tmp_path: Path):
        loader = ConfigLoader()
        loader.global_config_path = tmp_path / "nonexistent" / "config.yaml"

        with pytest.raises(ConfigError, match="Ошибка валидации"):
            loader.load_config()

    def test_invalid_yaml(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("invalid: [yaml: broken", encoding="utf-8")

        loader = ConfigLoader()
        loader.global_config_path = config_path

        with pytest.raises(ConfigError, match="Ошибка парсинга YAML"):
            loader.load_config()

    def test_save_and_load_config(self, tmp_path: Path):
        loader = ConfigLoader()
        config_path = tmp_path / ".ollidesk" / "config.yaml"
        loader.global_config_path = config_path

        config = AppConfig(**SAMPLE_GLOBAL_CONFIG)
        loader.save_config(config)

        loaded = loader.load_config()
        assert loaded.default_model == config.default_model
        assert loaded.embed_model == config.embed_model
        assert len(loaded.models) == 2

    def test_save_local_config(self, tmp_path: Path):
        loader = ConfigLoader()
        global_path = tmp_path / ".ollidesk" / "config.yaml"
        _write_yaml(global_path, SAMPLE_GLOBAL_CONFIG)
        loader.global_config_path = global_path

        project = tmp_path / "project"
        project.mkdir()

        config = AppConfig(default_model="local", embed_model="local-embed")
        loader.save_config(config, project)

        loaded = loader.load_config(project)
        assert loaded.default_model == "local"
        assert loaded.embed_model == "local-embed"


class TestGetModelByRole:
    def test_find_chat_model(self):
        config = AppConfig(**SAMPLE_GLOBAL_CONFIG)
        model = get_model_by_role(config, ModelRole.CHAT)
        assert model.name == "Qwen 2.5 Coder 7B"

    def test_find_embed_model(self):
        config = AppConfig(**SAMPLE_GLOBAL_CONFIG)
        model = get_model_by_role(config, ModelRole.EMBED)
        assert model.name == "Nomic Embed Text"

    def test_model_not_found(self):
        config = AppConfig(**SAMPLE_GLOBAL_CONFIG)
        with pytest.raises(ConfigError, match="не найдена"):
            get_model_by_role(config, ModelRole.RERANK)


class TestYAMLAnchors:
    def test_yaml_anchors_work(self, tmp_path: Path):
        yaml_with_anchors = """
default_model: chat-model
embed_model: embed-model

defaults: &defaults
  provider: ollama
  api_base: http://localhost:11434
  keep_alive: 1800

models:
  - name: Chat Model
    <<: *defaults
    model: chat-model
    roles: [chat]
  - name: Embed Model
    <<: *defaults
    model: embed-model
    roles: [embed]
"""
        config_path = tmp_path / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml_with_anchors, encoding="utf-8")

        loader = ConfigLoader()
        loader.global_config_path = config_path

        config = loader.load_config()
        assert len(config.models) == 2
        assert config.models[0].provider == "ollama"
        assert config.models[0].api_base == "http://localhost:11434"
        assert config.models[1].keep_alive == 1800


class TestEnumValues:
    def test_model_role_values(self):
        assert ModelRole.CHAT.value == "chat"
        assert ModelRole.EMBED.value == "embed"
        assert ModelRole.RERANK.value == "rerank"
        assert ModelRole.APPLY.value == "apply"
        assert ModelRole.AUTOCOMPLETE.value == "autocomplete"

    def test_tool_policy_values(self):
        assert ToolPolicy.AUTO.value == "auto"
        assert ToolPolicy.ASK_FIRST.value == "ask_first"
        assert ToolPolicy.EXCLUDED.value == "excluded"

    def test_agent_mode_values(self):
        assert AgentMode.CHAT.value == "chat"
        assert AgentMode.PLAN.value == "plan"
        assert AgentMode.AGENT.value == "agent"
