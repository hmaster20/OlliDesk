# ui/chat/session_manager.py
"""Управление сессией чата: создание, установка id, загрузка истории.

Тонкая обёртка над `state.session_store.SessionStore`. Хранит текущий
session_id и снимает с панели обязанность помнить о нём вручную.
"""

from __future__ import annotations

from loguru import logger

from agents.ollama_client import ChatMessage
from state.session_store import SessionStore


class SessionManager:
    """Состояние текущей сессии чата.

    Атрибуты:
        session_id: Идентификатор активной сессии (``None`` до первого запроса).
    """

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self.session_id: str | None = None

    # -------------------------------------------------------------
    # Создание / смена
    # -------------------------------------------------------------

    def ensure_session(self, project_path: str) -> str:
        """Создаёт сессию, если её ещё нет, и возвращает id.

        Args:
            project_path: Путь к проекту (или ``global``) для привязки сессии.

        Returns:
            id активной сессии.
        """
        if self.session_id:
            return self.session_id
        project = project_path if project_path else "global"
        self.session_id = self._store.create_session(project)
        return self.session_id

    def set_session(self, session_id: str) -> None:
        """Делает указанную сессию активной (без создания)."""
        self.session_id = session_id

    def reset(self) -> None:
        """Снимает привязку к сессии (для «Очистить чат»)."""
        self.session_id = None

    # -------------------------------------------------------------
    # Сохранение / загрузка
    # -------------------------------------------------------------

    def save_message(self, message: ChatMessage) -> None:
        """Сохраняет сообщение в текущую сессию (если она активна)."""
        if not self.session_id:
            return
        self._store.save_message(self.session_id, message)

    def load_latest_for_project(
        self, project_path: str, limit: int = 1000
    ) -> list[ChatMessage]:
        """Загружает историю последней сессии проекта.

        Args:
            project_path: Путь к проекту.
            limit: Максимум сообщений.

        Returns:
            Список сообщений (пустой, если сессий нет).
        """
        sessions = self._store.list_sessions(project_path)
        if not sessions:
            return []
        last = sessions[0]  # уже отсортированы по updated_at DESC
        self.session_id = last.id
        return self._store.get_history(last.id, limit=limit)

    def load_session(self, session_id: str, limit: int = 1000) -> list[ChatMessage]:
        """Загружает историю конкретной сессии."""
        self.session_id = session_id
        return self._store.get_history(session_id, limit=limit)

    # -------------------------------------------------------------
    # Диагностика
    # -------------------------------------------------------------

    def __bool__(self) -> bool:
        return self.session_id is not None

    @property
    def store(self) -> SessionStore:
        """Прямой доступ к SessionStore (например, для диалога управления)."""
        return self._store