"""Расчёт бюджета контекстного окна под прикреплённые файлы и RAG.

Модели в OlliDesk работают с небольшим окном контекста: локальная GGUF
запускается с `slm.n_ctx` (по умолчанию 4096), а Ollama применяет `num_ctx`
из Modelfile и тоже по умолчанию 4096 — даже для моделей, обученных на 128K.
Поэтому прикреплённый файл нельзя вставлять в промпт целиком: нужно
посчитать, сколько токенов ещё помещается, показать модели начало и конец,
а середину отдать через инструмент `read_attached_file`.
"""

from __future__ import annotations

# Среднее число символов на один токен для русского текста и кода.
# Для кириллицы отношение ближе к 2, для кода — к 3.5; берём среднее с запасом.
CHARS_PER_TOKEN = 3.0

# Окно контекста по умолчанию, если размер модели неизвестен.
DEFAULT_NUM_CTX = 4096

# Доля окна контекста, которую можно занять прикреплёнными файлами и RAG.
# Остальное уходит на системный промпт, роль, инструкции по инструментам,
# историю диалога и генерируемый ответ.
ATTACHED_CONTEXT_SHARE = 0.25

# Минимальный бюджет, даже если окно совсем маленькое.
MIN_ATTACHED_TOKENS = 256

# Размер порции, которую отдаёт инструмент read_attached_file за один вызов.
READ_CHUNK_CHARS = 4000

# Окно контекста для моделей, которые используются в OlliDesk.
#
# ВАЖНО про Ollama: llama3.1 обучена на 128K, qwen2.5-coder на 32K, но
# Ollama без явного `num_ctx` в Modelfile применяет 4096. Указывать нужно
# именно фактически доступное окно, иначе бюджет окажется завышен в 8-32 раза
# и прикреплённый файл вытеснит системный промпт.
KNOWN_NUM_CTX: dict[str, int] = {
    # --- Локальные GGUF (llama.cpp), размер задаётся slm.n_ctx ---
    "qwen2.5-1.5b-instruct": 4096,
    "qwen2.5-3b-instruct": 4096,
    "qwen2.5-7b-instruct": 4096,
    # --- Ollama (num_ctx из Modelfile, по умолчанию 4096) ---
    "llama3.1": 4096,
    "llama3.2": 4096,
    "qwen2.5-coder": 4096,
    "deepseek-coder-v2": 4096,
    "gemma2": 4096,
}


def estimate_tokens(text: str) -> int:
    """Оценивает количество токенов в тексте.

    Args:
        text: Исходный текст.

    Returns:
        Оценка числа токенов (сверху, с запасом).
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def tokens_to_chars(tokens: int) -> int:
    """Переводит бюджет токенов в количество символов.

    Args:
        tokens: Бюджет в токенах.

    Returns:
        Максимальное количество символов.
    """
    return max(0, int(tokens * CHARS_PER_TOKEN))


def resolve_num_ctx(model: str, slm_num_ctx: int | None = None) -> int:
    """Определяет фактическое окно контекста модели.

    Args:
        model: Имя модели (как в конфиге или реестре).
        slm_num_ctx: Значение `slm.n_ctx`, если модель загружена как GGUF.

    Returns:
        Размер окна контекста в токенах.
    """
    if model in KNOWN_NUM_CTX:
        return KNOWN_NUM_CTX[model]

    for prefix, num_ctx in KNOWN_NUM_CTX.items():
        if model.startswith(prefix):
            return num_ctx

    if slm_num_ctx and slm_num_ctx > 0:
        return slm_num_ctx

    return DEFAULT_NUM_CTX


def attached_token_budget(num_ctx: int) -> int:
    """Считает, сколько токенов отдать прикреплённым файлам.

    Args:
        num_ctx: Окно контекста модели в токенах.

    Returns:
        Бюджет под прикреплённые файлы в токенах.
    """
    return max(MIN_ATTACHED_TOKENS, int(num_ctx * ATTACHED_CONTEXT_SHARE))


def attached_char_budget(num_ctx: int) -> int:
    """Считает бюджет прикреплённых файлов сразу в символах.

    Args:
        num_ctx: Окно контекста модели в токенах.

    Returns:
        Бюджет под прикреплённые файлы в символах.
    """
    return tokens_to_chars(attached_token_budget(num_ctx))


def rag_char_budget(num_ctx: int) -> int:
    """Считает бюджет RAG-контекста в символах.

    RAG и прикреплённые файлы делят общий бюджет ``ATTACHED_CONTEXT_SHARE``
    пополам, чтобы вместе не вытеснить системный промпт, историю диалога и
    генерируемый ответ.

    Args:
        num_ctx: Окно контекста модели в токенах.

    Returns:
        Бюджет под RAG-контекст в символах.
    """
    return max(MIN_ATTACHED_TOKENS, attached_char_budget(num_ctx) // 2)


def truncate_to_chars(text: str, max_chars: int) -> str:
    """Обрезает текст до ``max_chars`` символов, добавляя пометку.

    Args:
        text: Исходный текст.
        max_chars: Максимальная длина результата.

    Returns:
        Текст не длиннее ``max_chars`` (с пометкой об обрезке) либо пустая строка.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = "\n… [контекст обрезан]"
    if max_chars <= len(marker):
        return text[:max_chars]
    return text[: max_chars - len(marker)] + marker
