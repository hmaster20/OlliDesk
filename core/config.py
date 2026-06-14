class ModelRole(str, Enum):
    CHAT = "chat"
    EMBED = "embed"
    RERANK = "rerank"
    APPLY = "apply"
    AUTOCOMPLETE = "autocomplete"

class ModelConfig(BaseModel):
    name: str
    provider: Literal["ollama", "openai", "openai-compatible"]
    model: str
    api_base: str = "http://localhost:11434"
    roles: list[ModelRole] = [ModelRole.CHAT]
    capabilities: list[str] = []  # ["tool_use", "image_input"]
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 8192
    keep_alive: int = 1800  # seconds, для Ollama
    reasoning: bool = False

class ToolPolicy(str, Enum):
    AUTO = "auto"
    ASK_FIRST = "ask_first"
    EXCLUDED = "excluded"

class AgentMode(str, Enum):
    CHAT = "chat"        # только диалог
    PLAN = "plan"        # read-only tools
    AGENT = "agent"      # все tools

class AppConfig(BaseModel):
    version: str = "1.0.0"
    schema_version: str = "v1"
    default_model: str
    embed_model: str
    rerank_model: str | None = None
    models: list[ModelConfig] = []
    tool_policies: dict[str, ToolPolicy] = {}
    ignored_patterns: list[str] = [
        ".git", "node_modules", "__pycache__", "venv",
        ".venv", "dist", "build", ".env", "*.pyc"
    ]
    index_extensions: list[str] = [
        ".py", ".js", ".ts", ".tsx", ".jsx",
        ".html", ".css", ".md", ".json", ".yaml", ".yml"
    ]
    max_file_size_kb: int = 100
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 50
    rules_file: str = ".ollidesk/rules.md"
    recent_projects: list[str] = []  # max 10
