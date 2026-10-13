"""Пакет чата: виджет панели, сообщения и фоновые потоки."""

from ui.chat.message_item import (
    ChatActivityItem,
    ChatHypothesisItem,
    ChatMessageItem,
    ChatResearchPassport,
    ChatSourcesItem,
)
from ui.chat.panel import ChatPanel
from ui.chat.threads import (
    AgentChatThread,
    RagSearchThread,
    WebSearchThread,
)

__all__ = [
    "AgentChatThread",
    "ChatActivityItem",
    "ChatHypothesisItem",
    "ChatMessageItem",
    "ChatPanel",
    "ChatResearchPassport",
    "ChatSourcesItem",
    "RagSearchThread",
    "WebSearchThread",
]