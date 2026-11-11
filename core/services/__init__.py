"""Application Services (2.8): чистые сервисы без Qt.

Здесь живут сервисы, которые не зависят от Qt и могут тестироваться
без `QApplication`. UI-контроллеры (`ChatPanel`, `IndexingController`)
ходят сюда за логикой; сервисы эмитят события в `EventBus`, а UI
подписывается.

Сервисы:
  * `MemoryService` (4.3) — эпизодическая память.
  * `ToolService` (0.1.1 + 10.9 + 2.8) — единая точка исполнения
    инструментов: audit-log для всех вызовов, авто-снапшот перед
    мутирующими.
  * `ProjectService` (2.8) — кэш `ProjectIntel` + git-контекст.
  * `ModelService` (2.8) — единая точка выбора моделей (парк / coder /
    пути).
  * `ChatService` (2.8) — сборка `AgentContext` без Qt.
"""

from core.services.chat_service import ChatRequestSnapshot, ChatService
from core.services.memory_service import (
    Decision,
    MemoryService,
    Preference,
    RecallResult,
    Reflection,
)
from core.services.model_service import ModelService
from core.services.project_service import GitSnapshot, ProjectService
from core.services.tool_service import ToolService

__all__ = [
    "ChatRequestSnapshot",
    "ChatService",
    "Decision",
    "GitSnapshot",
    "MemoryService",
    "ModelService",
    "Preference",
    "ProjectService",
    "RecallResult",
    "Reflection",
    "ToolService",
]
