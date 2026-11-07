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


class EmbedderError(OlliDeskError):
    """Ошибка вычисления эмбеддингов.

    Бэкенд недоступен, модель эмбеддингов не загружена или вернула
    неожиданное число векторов. Следует из неё, что RAG работать не будет.
    """

    pass


class ToolExecutionError(OlliDeskError):
    """Ошибка выполнения инструмента агента."""

    pass


class ToolsNotSupportedError(OllamaConnectionError):
    """Модель не поддерживает вызов инструментов."""

    pass
