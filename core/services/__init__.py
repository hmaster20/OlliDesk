"""Application Services (2.8): чистые сервисы без Qt.

Здесь живут сервисы, которые не зависят от Qt и могут тестироваться
без `QApplication`. UI-контроллеры (`ChatPanel`, `IndexingController`)
ходят сюда за логикой; сервисы эмитят события в `EventBus`, а UI
подписывается.

Первый сервис — `MemoryService` (4.3). Дальше сюда переедут
`ChatService`, `ToolService`, `ProjectService`, `ModelService` и т.д.
(2.8).
"""

from core.services.memory_service import (
    Decision,
    MemoryService,
    Preference,
    Reflection,
    RecallResult,
)

__all__ = [
    "Decision",
    "MemoryService",
    "Preference",
    "RecallResult",
    "Reflection",
]