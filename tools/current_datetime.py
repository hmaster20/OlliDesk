"""Инструмент текущих даты и времени."""

from datetime import datetime

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
_MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


@tool(
    name="get_current_datetime",
    description=(
        "Возвращает ТЕКУЩИЕ дату, время и день недели. "
        "Используй при вопросах «какое сегодня число», «какой день недели», "
        "«сколько сейчас времени», «какой сегодня день» и т.п."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def get_current_datetime() -> str:
    """Возвращает текущие дату и время.

    Returns:
        Строка с датой и временем.
    """
    now = datetime.now()
    return (
        f"Сейчас: {now.day} {_MONTHS_GEN[now.month]} {now.year} года, "
        f"{_WEEKDAYS[now.weekday()]}, {now.strftime('%H:%M')}."
    )