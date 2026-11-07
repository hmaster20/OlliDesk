"""Схема конфига v3 (pydantic). Разрешено менять формат; обратная совместимость
AgentLoop не держится. Ключевые отличия от v2: двухмодельный блок models.*,
security.*, context.policy=dynamic, agent.default_flow=linear, project_intelligence.*.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NetworkCfg(BaseModel):
    web_search: Literal["off", "neutral", "ask_first", "auto"] = "neutral"
    lsp_download: bool = True
    lsp_download_size_mb_warn: int = 50
    huggingface_download: bool = True


class SecurityCfg(BaseModel):
    mode: Literal["local_only"] = "local_only"
    telemetry: bool = False
    confidential: bool = False
    network: NetworkCfg = Field(default_factory=NetworkCfg)


class ModelRoleCfg(BaseModel):
    id: str
    device: Literal["GPU", "CPU"] = "CPU"
    n_ctx: int = 8192
    kv_cache: Literal["q8_0", "f16"] = "q8_0"
    n_gpu_layers: int = 0
    temperature: float = 0.0
    file: str | None = None
    budgets: dict[str, int] = Field(default_factory=dict)


class ModelsCfg(BaseModel):
    planner: ModelRoleCfg = Field(
        default_factory=lambda: ModelRoleCfg(
            id="qwen2.5-3b-instruct",
            device="GPU",
            n_gpu_layers=-1,
            temperature=0.0,
            budgets={"classify": 32, "plan": 256, "critique": 192},
        )
    )
    analyst: ModelRoleCfg = Field(
        default_factory=lambda: ModelRoleCfg(
            id="qwen2.5-7b-instruct",
            device="CPU",
            n_gpu_layers=0,
            temperature=0.35,
            budgets={"synthesis": 1100, "regen": 900},
        )
    )
    analyst_pro: ModelRoleCfg | None = None


class ContextCfg(BaseModel):
    policy: Literal["dynamic"] = "dynamic"
    tokenizer: Literal["exact_when_loaded", "approx"] = "exact_when_loaded"
    safety_reserve_tokens: int = 256
    shares: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "architecture": {
                "system": 0.12,
                "repo_map": 0.25,
                "evidence": 0.45,
                "dynamic": 0.10,
                "history": 0.08,
            }
        }
    )


class DeepModeCfg(BaseModel):
    threshold_files: int = 500
    max_iters: int = 5
    nudges: int = 2
    coverage_target: float = 0.8


class AgentCfg(BaseModel):
    default_flow: Literal["linear"] = "linear"
    deep_mode: DeepModeCfg = Field(default_factory=DeepModeCfg)
    tool_calling: Literal["structured", "soft"] = "structured"
    deterministic_fallback: bool = True
    small_project_files: int = 100
    small_project_bytes: int = 300_000


class ParsersCfg(BaseModel):
    python: str = "tree-sitter"
    csharp: str = "tree-sitter"
    ruby: str = "tree-sitter"
    ansible: str = "domain"
    terraform: str = "hcl"
    vagrant: str = "ruby-dsl"


class ProjectIntelligenceCfg(BaseModel):
    enabled: bool = True
    parsers: ParsersCfg = Field(default_factory=ParsersCfg)
    dependency_graph: bool = True
    call_graph: Literal["off", "lsp_optional"] = "lsp_optional"
    repo_map_levels: list[str] = Field(default_factory=lambda: ["L0", "L1"])
    cache_dir: str = ".ollidesk/graph"
    index_extensions: list[str] = Field(
        default_factory=lambda: [
            ".py",
            ".pyi",
            ".cs",
            ".csx",
            ".csproj",
            ".sln",
            ".rb",
            ".tf",
            ".tfvars",
            ".hcl",
            ".pkr.hcl",
            ".yml",
            ".yaml",
            ".json",
            ".toml",
            ".go",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".html",
            ".css",
            ".md",
            ".xml",
            ".config",
            ".resx",
            ".razor",
            ".sh",
            ".bash",
            ".sql",
            ".rs",
            ".java",
            ".kt",
            ".php",
        ]
    )
    index_filenames: list[str] = Field(
        default_factory=lambda: [
            "Vagrantfile",
            "Rakefile",
            "Gemfile",
            "Guardfile",
            "Podfile",
            "Dockerfile",
            "Makefile",
            "Playbook",
            "site.yml",
            "playbook.yml",
            "build.gradle",
            "CMakeLists.txt",
        ]
    )


class ToolsPolicyCfg(BaseModel):
    read_file: str = "auto"
    search_codebase: str = "auto"
    list_directory: str = "auto"
    write_file: str = "ask_first"
    git_commit: str = "ask_first"
    web_search: str = "ask_first"
    get_weather: str = "auto"


class ConfigV3(BaseModel):
    version: Literal[3] = 3
    security: SecurityCfg = Field(default_factory=SecurityCfg)
    backend_priority: list[str] = Field(default_factory=lambda: ["LOCAL_SLM"])
    embed_backend: str = "auto"
    models: ModelsCfg = Field(default_factory=ModelsCfg)
    context: ContextCfg = Field(default_factory=ContextCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    project_intelligence: ProjectIntelligenceCfg = Field(default_factory=ProjectIntelligenceCfg)
    tools: ToolsPolicyCfg = Field(default_factory=ToolsPolicyCfg)
    # перенесённые из v2:
    ignored_patterns: list[str] = Field(default_factory=list)
    last_model: str | None = None
    last_mode: str | None = None
    project_path: str | None = None
