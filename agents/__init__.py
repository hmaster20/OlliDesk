"""Агенты и клиенты для взаимодействия с LLM."""

from agents.agent_loop import AgentContext, AgentIterationLimitError, AgentLoop
from agents.context_compactor import compact_messages
from agents.ollama_client import (
    ChatChunk,
    ChatMessage,
    ChatOptions,
    OllamaClient,
    OllamaModelInfo,
    ToolCall,
    ToolSchema,
)
from agents.tool_registry import ToolRegistry, ToolResult, tool

__all__ = [
    "AgentContext",
    "AgentIterationLimitError",
    "AgentLoop",
    "ChatChunk",
    "ChatMessage",
    "ChatOptions",
    "OllamaClient",
    "OllamaModelInfo",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSchema",
    "compact_messages",
    "tool",
]
