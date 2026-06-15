"""Реестр инструментов с автоматической генерацией JSON-схем."""

import inspect
from collections.abc import Callable
from typing import Any, TypeVar, get_type_hints

from loguru import logger
from pydantic import TypeAdapter

from agents.ollama_client import ToolCall, ToolSchema
from core.config import AgentMode, ToolPolicy
from core.exceptions import ToolExecutionError

F = TypeVar("F", bound=Callable[..., Any])


class ToolResult:
    """Результат выполнения инструмента."""

    def __init__(self, tool_call_id: str, name: str, content: str, is_error: bool = False):
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content
        self.is_error = is_error


def _type_to_json_schema(tp: type) -> dict[str, Any]:
    """Конвертирует Python type в JSON Schema фрагмент через Pydantic."""
    try:
        adapter: TypeAdapter[Any] = TypeAdapter(tp)
        schema = adapter.json_schema()
        if "$defs" in schema:
            del schema["$defs"]
        return schema
    except Exception:
        return {}


_INJECTED_PARAMS = {"vector_store", "project_root"}


def _generate_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Генерирует JSON Schema для функции из type hints и докстринга.

    Параметры, перечисленные в _INJECTED_PARAMS, исключаются из схемы —
    они передаются автоматически из контекста агента.
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func)

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        if name == "return":
            continue
        if name.startswith("_"):
            continue
        if name in _INJECTED_PARAMS:
            continue

        param_type = hints.get(name, str)
        prop_schema = _type_to_json_schema(param_type)
        properties[name] = prop_schema

        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default is not None:
            properties[name]["default"] = param.default

    schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    description = ""
    if func.__doc__:
        clean = inspect.cleandoc(func.__doc__)
        description = clean.split("\n\n")[0] if "\n\n" in clean else clean

    return {
        "name": "",
        "description": description,
        "parameters": schema,
    }


def tool(
    name: str,
    description: str,
    policy: ToolPolicy = ToolPolicy.ASK_FIRST,
    modes: list[AgentMode] | None = None,
) -> Callable[[F], F]:
    """Декоратор для регистрации функции как инструмента.

    Args:
        name: Имя инструмента (уникальное)
        description: Описание для LLM
        policy: Политика выполнения
        modes: Режимы агента, в которых доступен инструмент
    """
    def decorator(func: F) -> F:
        tool_meta: dict[str, Any] = {
            "name": name,
            "description": description,
            "policy": policy,
            "modes": modes or [AgentMode.PLAN, AgentMode.AGENT],
            "func": func,
        }
        func._tool_meta = tool_meta  # type: ignore[attr-defined]
        return func
    return decorator


class ToolRegistry:
    """Реестр зарегистрированных инструментов."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def register(self, func: Callable[..., Any]) -> None:
        """Регистрирует функцию, декорированную @tool."""
        meta: dict[str, Any] | None = getattr(func, "_tool_meta", None)
        if not meta:
            raise ValueError(f"Функция {func.__name__} не имеет декоратора @tool")

        schema = _generate_schema(func)
        schema["name"] = meta["name"]

        self._tools[meta["name"]] = {**meta, "schema": schema}
        logger.debug(f"Зарегистрирован инструмент: {meta['name']} (policy={meta['policy'].value})")

    def get_schemas(self, mode: AgentMode) -> list[ToolSchema]:
        """Возвращает схемы инструментов, разрешённых в данном режиме."""
        result = []
        for _name, meta in self._tools.items():
            if meta.get("policy") == ToolPolicy.EXCLUDED:
                continue
            if mode not in meta.get("modes", []):
                continue
            result.append(ToolSchema(function=meta["schema"]))
        return result

    def get_policy(self, tool_name: str) -> ToolPolicy:
        """Возвращает политику выполнения инструмента."""
        meta = self._tools.get(tool_name)
        if not meta:
            return ToolPolicy.EXCLUDED
        policy_val = meta.get("policy", ToolPolicy.EXCLUDED)
        if isinstance(policy_val, ToolPolicy):
            return policy_val
        return ToolPolicy.AUTO

    def get_description(self, tool_name: str) -> str:
        """Возвращает описание инструмента."""
        meta = self._tools.get(tool_name)
        return meta.get("description", "") if meta else ""

    def set_policy(self, tool_name: str, policy: ToolPolicy) -> None:
        """Устанавливает политику для инструмента."""
        if tool_name in self._tools:
            self._tools[tool_name]["policy"] = policy

    def get_all_tools(self) -> list[dict[str, Any]]:
        """Возвращает список всех зарегистрированных инструментов."""
        return [
            {"name": name, "description": meta.get("description", ""),
             "policy": meta.get("policy", ToolPolicy.AUTO)}
            for name, meta in self._tools.items()
        ]

    async def execute(self, tool_call: ToolCall, **extra_kwargs: Any) -> ToolResult:
        """Асинхронно выполняет инструмент и возвращает результат.

        Args:
            tool_call: Вызов инструмента
            extra_kwargs: Дополнительные аргументы (например, project_root, vector_store)

        Returns:
            ToolResult с результатом или ошибкой
        """
        meta = self._tools.get(tool_call.name)
        if not meta:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Инструмент '{tool_call.name}' не найден",
                is_error=True,
            )

        func = meta["func"]
        sig = inspect.signature(func)
        filtered_kwargs = {k: v for k, v in extra_kwargs.items() if k in sig.parameters}
        merged_args = {**tool_call.arguments, **filtered_kwargs}
        try:
            if inspect.iscoroutinefunction(func):
                result_content = await func(**merged_args)
            else:
                result_content = func(**merged_args)

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=str(result_content),
            )
        except ToolExecutionError:
            raise
        except Exception as e:
            logger.error(f"Ошибка выполнения инструмента {tool_call.name}: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: {e}",
                is_error=True,
            )
