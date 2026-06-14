"""Сжатие контекста при превышении лимита токенов."""

from loguru import logger

from agents.ollama_client import ChatMessage, OllamaClient

SUMMARIZE_PROMPT = (
    "Суммаризируй историю диалога до этого момента в 3-4 предложениях. "
    "Сохрани ключевые факты, принятые решения и контекст текущей задачи."
)


async def compact_messages(
    client: OllamaClient,
    messages: list[ChatMessage],
    model: str,
    context_limit: int,
) -> list[ChatMessage]:
    """Сжимает историю сообщений, если она превышает 80% context_limit.

    Подсчитывает примерную длину в токенах (4 символа ~ 1 токен).
    Если превышен порог, вызывает LLM для суммаризации и заменяет старые
    сообщения одним system/assistant блоком.

    Args:
        client: Клиент Ollama
        messages: История сообщений
        model: Модель для суммаризации
        context_limit: Максимальная длина контекста модели в токенах

    Returns:
        Сжатую историю сообщений
    """
    if not messages:
        return messages

    total_chars = sum(len(m.content or "") for m in messages)
    estimated_tokens = total_chars // 4
    threshold = int(context_limit * 0.8)

    if estimated_tokens <= threshold:
        return messages

    logger.info(
        f"Контекст ({estimated_tokens} токенов) превышает 80% лимита "
        f"({threshold}). Запускаю сжатие..."
    )

    system_parts = []
    history_parts = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            marker = f"[{m.role.upper()}]"
            history_parts.append(f"{marker}: {m.content}")

    summary_input = "\n\n".join(history_parts) if history_parts else ""

    try:
        summary_messages = [
            ChatMessage(role="system", content="Ты — ассистент, сжимающий историю диалога."),
            ChatMessage(role="user", content=f"{SUMMARIZE_PROMPT}\n\nИстория:\n{summary_input}"),
        ]

        summary_text = ""
        async for chunk in client.chat(
            model=model,
            messages=summary_messages,
            stream=True,
        ):
            if chunk.content:
                summary_text += chunk.content

        compacted = [
            ChatMessage(role="system", content="\n\n".join(system_parts)),
            ChatMessage(
                role="assistant",
                content=f"[Сжатый контекст предыдущего диалога]\n{summary_text}",
            ),
        ]

        logger.info(f"Контекст сжат: {len(messages)} -> {len(compacted)} сообщений")
        return compacted
    except Exception as e:
        logger.warning(f"Ошибка сжатия контекста: {e}; возвращаю исходные сообщения")
        return messages
