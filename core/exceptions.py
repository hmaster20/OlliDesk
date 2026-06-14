"""Кастомные исключения OlliDesk."""


class OlliDeskError(Exception):
    """Базовое исключение для всех ошибок OlliDesk."""

    pass


class ConfigError(OlliDeskError):
    """Ошибка загрузки или валидации конфигурации."""

    pass


class OllamaConnectionError(OlliDeskError):
    """Не удалось подключиться к Ollama API."""

    pass


class ModelNotFoundError(OlliDeskError):
    """Запрошенная модель не найдена в Ollama."""

    pass


class IndexingError(OlliDeskError):
    """Ошибка индексации проекта."""

    pass


class ToolExecutionError(OlliDeskError):
    """Ошибка выполнения инструмента агента."""

    pass
