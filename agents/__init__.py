"""Агенты и клиенты для взаимодействия с LLM."""

from agents.agent_loop import AgentContext, AgentIterationLimitError, AgentLoop
from agents.local_llm_client import LocalLLMClient
from agents.model_router import BackendType, ModelRouter
from agents.ollama_client import (
    ChatChunk,
    ChatMessage,
    ChatOptions,
    OllamaClient,
    OllamaModelInfo,
    ToolCall,
    ToolSchema,
)
from agents.rule_based_fallback import RuleBasedFallback
from agents.tool_registry import ToolRegistry, ToolResult, tool

__all__ = [
    "AgentContext",
    "AgentIterationLimitError",
    "AgentLoop",
    "BackendType",
    "ChatChunk",
    "ChatMessage",
    "ChatOptions",
    "LocalLLMClient",
    "ModelRouter",
    "OllamaClient",
    "OllamaModelInfo",
    "RuleBasedFallback",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSchema",
    "tool",
]
