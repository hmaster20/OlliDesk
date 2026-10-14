# ui/chat/__init__.py
"""Пакет чата: виджет панели, менеджеры, сообщения, потоки."""

from ui.chat.attached_files_handler import AttachedFilesHandler
from ui.chat.backend_manager import BackendManager
from ui.chat.message_item import (
    ChatActivityItem,
    ChatHypothesisItem,
    ChatMessageItem,
    ChatSourcesItem,
)
from ui.chat.panel import ChatPanel
from ui.chat.project_context import ProjectContext
from ui.chat.research_controller import ResearchController
from ui.chat.research_passport import ChatResearchPassport
from ui.chat.session_manager import SessionManager
from ui.chat.thought_stream import ChatThoughtStream
from ui.chat.threads import (
    AgentChatThread,
    RagSearchThread,
    WebSearchThread,
)
from ui.chat.tool_registry_builder import build_registry

__all__ = [
    # UI
    "ChatPanel",
    "ChatActivityItem",
    "ChatHypothesisItem",
    "ChatMessageItem",
    "ChatResearchPassport",
    "ChatSourcesItem",
    "ChatThoughtStream",
    # Менеджеры
    "AttachedFilesHandler",
    "BackendManager",
    "ProjectContext",
    "ResearchController",
    "SessionManager",
    "build_registry",
    # Потоки
    "AgentChatThread",
    "RagSearchThread",
    "WebSearchThread",
]