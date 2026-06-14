class ToolRegistry:
    def register(self, func: Callable, policy: ToolPolicy = ToolPolicy.ASK_FIRST) -> None:
        """Регистрирует функцию как инструмент. Извлекает схему из docstring + type hints."""

    def get_schemas(self, mode: AgentMode) -> list[ToolSchema]:
        """Возвращает схемы инструментов для текущего режима.
        CHAT -> [], PLAN -> только read-only, AGENT -> все."""

    def get_policy(self, tool_name: str) -> ToolPolicy: ...

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """Выполняет инструмент, возвращает результат."""
