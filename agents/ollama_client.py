class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 60.0): ...

    async def list_models(self) -> list[OllamaModelInfo]:
        """GET /api/tags — список доступных моделей."""

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
        options: ChatOptions | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """POST /api/chat со стримингом."""

    async def embed(
        self,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        """POST /api/embed — батчевая векторизация."""


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    images: list[str] | None = None  # base64
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]

class ChatChunk(BaseModel):
    """Один токен из стрима."""
    content: str
    tool_calls: list[ToolCall] | None = None
    done: bool = False
