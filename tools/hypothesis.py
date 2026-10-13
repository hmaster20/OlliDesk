"""Инструменты управления гипотезами для проверяемого исследования проекта.

Три инструмента:

- ``record_hypothesis`` — записать гипотезу (что я предполагаю об архитектуре)
  и метод её проверки.
- ``update_hypothesis`` — обновить статус после проверки: verified / refuted /
  partial / inconclusive + конкретное доказательство из кода.
- ``list_hypotheses`` — посмотреть текущие гипотезы (для самопроверки).

Хранилище инжектируется через ``AgentContext.hypotheses`` — так же, как
``AttachedFileStore`` через ``attached_files``. Один экземпляр на сессию
чата, создаётся в ``ChatPanel``.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy


_VALID_STATUSES = frozenset({"verified", "refuted", "partial", "inconclusive"})


def _store_or_none(hypotheses: Any) -> Any:
    """Возвращает хранилище или None, если оно не передано/неправильного типа."""
    if hypotheses is None:
        return None
    if not (hasattr(hypotheses, "add") and hasattr(hypotheses, "update")):
        logger.warning(f"В hypotheses передан объект без нужного API: {type(hypotheses)}")
        return None
    return hypotheses


def _hypothesis_dict(h: Any) -> dict[str, Any]:
    """Сериализует гипотезу в словарь для JSON-ответа."""
    return {
        "id": h.id,
        "statement": h.statement,
        "verification_method": h.verification_method,
        "status": h.status,
        "evidence": h.evidence,
    }


@tool(
    name="record_hypothesis",
    description=(
        "Записывает ГИПОТЕЗУ о проекте, которую нужно проверить чтением кода. "
        "Используй в исследовательских задачах (изучение архитектуры, структуры, "
        "зависимостей, схемы компонентов): сформулируй КОНКРЕТНОЕ проверяемое "
        "предположение, а не общее соображение.\n"
        "Хорошо: «Точка входа — main.py, которая вызывает core.app.main()»; "
        "«Проект использует PySide6 для UI»; «Векторное хранилище — ChromaDB».\n"
        "Плохо: «Проект хороший», «Код структурирован», «Есть несколько модулей».\n"
        "После записи верни id, прочитай файлы для проверки и вызови "
        "update_hypothesis с конкретным доказательством (пути, имена функций)."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def record_hypothesis(
    statement: str,
    verification_method: str,
    hypotheses: Any = None,
) -> str:
    """Записывает гипотезу и возвращает её id.

    Args:
        statement: Конкретное проверяемое утверждение об архитектуре/коде.
        verification_method: Что именно нужно прочитать/найти для проверки.
        hypotheses: Хранилище гипотез (инжектируется через AgentContext).

    Returns:
        JSON с id и полями гипотезы, либо строка с ошибкой.
    """
    store = _store_or_none(hypotheses)
    if store is None:
        return "Хранилище гипотез не подключено. Гипотеза не записана."
    if not statement or not statement.strip():
        return "Ошибка: пустая формулировка гипотезы. Укажи конкретное утверждение."
    if not verification_method or not verification_method.strip():
        return (
            "Ошибка: не указан метод проверки. Опиши, какие файлы или поиски "
            "подтвердят или опровергнут гипотезу."
        )

    h = store.add(statement=statement, verification_method=verification_method)
    logger.info(f"Гипотеза записана: [{h.id}] {h.statement[:80]}")
    return json.dumps(
        {
            "ok": True,
            "hypothesis": _hypothesis_dict(h),
            "hint": (
                f"Теперь проверь [{h.id}] методом: {h.verification_method}. "
                f"После проверки вызови update_hypothesis(hypothesis_id='{h.id}', "
                "status='verified'/'refuted'/'partial', evidence='...')."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="update_hypothesis",
    description=(
        "Обновляет статус ранее записанной гипотезы после её проверки. "
        "status:\n"
        "  • verified — подтверждена кодом (в evidence — пути и конкретные "
        "    имена функций/классов/строк, которые её подтверждают);\n"
        "  • refuted — опровергнута (в evidence — что именно противоречит);\n"
        "  • partial — частично верна (уточни, что подтвердилось, а что нет);\n"
        "  • inconclusive — данных не хватило (в evidence — что мешало).\n"
        "ВЫЗЫВАЙ после каждой проверки — иначе гипотеза остаётся в статусе "
        "'open' и агент не сможет перейти к финальному ответу."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def update_hypothesis(
    hypothesis_id: str,
    status: str,
    evidence: str = "",
    hypotheses: Any = None,
) -> str:
    """Обновляет статус гипотезы.

    Args:
        hypothesis_id: id, полученный от record_hypothesis (например, "h1").
        status: verified / refuted / partial / inconclusive.
        evidence: Конкретное доказательство из кода (пути, имена, фрагменты).
        hypotheses: Хранилище гипотез (инжектируется через AgentContext).

    Returns:
        JSON с обновлённой гипотезой или строка с ошибкой.
    """
    store = _store_or_none(hypotheses)
    if store is None:
        return "Хранилище гипотез не подключено."

    if status not in _VALID_STATUSES:
        allowed = ", ".join(sorted(_VALID_STATUSES))
        return f"Ошибка: недопустимый статус '{status}'. Разрешены: {allowed}."

    h = store.update(hypothesis_id, status=status, evidence=evidence or "")  # type: ignore[arg-type]
    if h is None:
        return f"Ошибка: гипотеза '{hypothesis_id}' не найдена."

    logger.info(f"Гипотеза [{h.id}] → {h.status}: {h.evidence[:80]}")
    return json.dumps(
        {
            "ok": True,
            "hypothesis": _hypothesis_dict(h),
            "open_remaining": store.count_open(),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="list_hypotheses",
    description=(
        "Показывает список записанных гипотез и их статусы. Используй "
        "перед формированием финального ответа, чтобы проверить, не осталось "
        "ли открытых гипотез (status='open')."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def list_hypotheses(hypotheses: Any = None) -> str:
    """Возвращает список гипотез с их статусами.

    Args:
        hypotheses: Хранилище гипотез (инжектируется через AgentContext).

    Returns:
        JSON со списком гипотез и счётчиком открытых.
    """
    store = _store_or_none(hypotheses)
    if store is None:
        return "Хранилище гипотез не подключено."

    items = [_hypothesis_dict(h) for h in store.sorted_items()]
    return json.dumps(
        {
            "ok": True,
            "hypotheses": items,
            "open_count": store.count_open(),
            "total": len(items),
        },
        ensure_ascii=False,
        indent=2,
    )