"""Структурная совместимость реализаций LLMBackend (Фаза 2.1).

`runtime_checkable` Protocol проверяет только наличие имён методов/атрибутов,
не сигнатуры. Тест ловит случайное удаление метода (например, `reset_cancel`)
или переименование. Полная проверка сигнатур — за mypy (`tests/` в CI идёт
после `mypy`).

`LocalLLMClient` требует реальный файл модели, поэтому проверяется только
`OllamaClient` и `RuleBasedFallback` (у `LocalLLMClient` сигнатура `chat`/
`embed` структурно совпадает с `OllamaClient` — если `OllamaClient` проходит
`LLMBackend`, то и `LocalLLMClient` пройдёт; это подтверждается mypy на
`_do_chat`/`_do_agent` в `ChatPanel`).
"""

from __future__ import annotations

from agents.base import LLMBackend
from agents.ollama_client import OllamaClient
from agents.rule_based_fallback import RuleBasedFallback

# Единый список имён, которые должны быть у каждого LLMBackend.
_PROTOCOL_MEMBERS = (
    "chat",
    "embed",
    "cancel_request",
    "is_cancelled",
    "reset_cancel",
    "__aenter__",
    "__aexit__",
)


def test_ollama_client_satisfies_protocol() -> None:
    client = OllamaClient(base_url="http://localhost:11434")
    assert isinstance(client, LLMBackend), (
        "OllamaClient не удовлетворяет LLMBackend (см. agents/base.py). "
        "Проверьте наличие chat/embed/cancel_request/is_cancelled/"
        "reset_cancel/__aenter__/__aexit__."
    )


def test_rule_based_fallback_satisfies_protocol() -> None:
    client = RuleBasedFallback()
    assert isinstance(client, LLMBackend), (
        "RuleBasedFallback не удовлетворяет LLMBackend (см. agents/base.py)."
    )


def test_ollama_client_has_all_protocol_members() -> None:
    """Явная проверка имён: `runtime_checkable` смотрит только `hasattr`.

    Если кто-то переименует `reset_cancel` → `reset`, `isinstance` пройдёт
    (если имя осталось), но этот тест упадёт — это защита от смысловых
    переименований, которые ломают контракт с `AgentLoop`.
    """
    client = OllamaClient()
    for name in _PROTOCOL_MEMBERS:
        assert hasattr(client, name), f"OllamaClient missing {name!r}"


def test_rule_based_fallback_has_all_protocol_members() -> None:
    client = RuleBasedFallback()
    for name in _PROTOCOL_MEMBERS:
        assert hasattr(client, name), f"RuleBasedFallback missing {name!r}"


def test_protocol_members_are_callable_or_property() -> None:
    """`chat`/`embed`/`cancel_request`/`reset_cancel`/`__aenter__`/`__aexit__` — методы,
    `is_cancelled` — property. Проверяем, что не превратились в атрибуты-значения.
    """
    for cls in (OllamaClient, RuleBasedFallback):
        for name in ("chat", "embed", "cancel_request", "reset_cancel", "__aenter__", "__aexit__"):
            attr = getattr(cls, name)
            assert callable(attr), f"{cls.__name__}.{name!r} должен быть методом"
        # is_cancelled — property (в экземпляре — bool).
        instance = cls()
        assert isinstance(instance.is_cancelled, bool), (
            f"{cls.__name__}.is_cancelled должен быть bool (property)"
        )