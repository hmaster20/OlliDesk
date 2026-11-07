"""Реестр инструментов с автоматической генерацией JSON-схем."""

import inspect
import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar, get_type_hints

import httpx
from loguru import logger
from pydantic import BaseModel, TypeAdapter, ValidationError, create_model

from agents.ollama_client import ToolCall, ToolSchema
from core.config import AgentMode, ToolPolicy
from core.exceptions import ToolExecutionError

F = TypeVar("F", bound=Callable[..., Any])


class ErrorCategory(StrEnum):
    """Категория ошибки инструмента — подсказывает модели следующий шаг.

    Используется `AgentLoop` при формировании tool-сообщения: к тексту ошибки
    дописывается `retry_hint` (см. `agents/agent_loop.py::_RETRY_HINTS`), чтобы
    модель не повторяла тот же вызов «в лоб», а скорректировала стратегию.
    """

    # Аргументы не прошли валидацию (pydantic/JSON-схема, неизвестный
    # инструмент). Модель должна перечитать схему и исправить вызов.
    FIX_ARGUMENTS = "fix_arguments"
    # Файл/каталог не найден. Модель должна проверить путь и попробовать
    # ДРУГОЙ — повтор с тем же аргументом бесполезен.
    DIFFERENT_PATH = "different_path"
    # Транспортная сетевая ошибка (connect/timeout). Повтор через паузу
    # может помочь; продолжать с тем, что есть, тоже допустимо.
    RETRY_LATER = "retry_later"
    # Прочие падения. Модель должна честно сообщить пользователю, а не
    # подменять результат выдумкой.
    REPORT_TO_USER = "report_to_user"


class ToolResult:
    """Результат выполнения инструмента.

    [9.6.1] Добавлено поле `metadata: dict | None` — для структурированных
    результатов (например, diff от `write_file`). `content` остаётся
    строкой для LLM; UI берёт `metadata` для богатой визуализации.
    """

    def __init__(
        self,
        tool_call_id: str,
        name: str,
        content: str,
        is_error: bool = False,
        error_category: ErrorCategory | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content
        self.is_error = is_error
        self.error_category = error_category
        # [9.6.1] Структурированные данные (diff, statistics, …) для UI.
        self.metadata = metadata


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


_INJECTED_PARAMS = {
    "vector_store",
    "project_root",
    "search_cache",
    "attached_files",
    "hypotheses",
    "security",
}


def _build_arg_validator(func: Callable[..., Any]) -> type[BaseModel] | None:
    """Строит pydantic-модель для аргументов инструмента (без инъектируемых).

    Валидация ловит пропущенные обязательные аргументы и неверные типы до
    выполнения, чтобы модель получила понятную ошибку и исправила вызов.
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    fields: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name == "return" or name.startswith("_") or name in _INJECTED_PARAMS:
            continue
        param_type = hints.get(name, str)
        if param.default is inspect.Parameter.empty:
            fields[name] = (param_type, ...)
        else:
            fields[name] = (param_type, param.default)
    if not fields:
        return None
    try:
        return create_model(f"{func.__name__}_args", **fields)
    except Exception:
        logger.exception(f"Не удалось построить валидатор аргументов для {func.__name__}")
        return None


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
    is_mutating: bool = False,
    category: str = "filesystem",
) -> Callable[[F], F]:
    """Декоратор для регистрации функции как инструмента.

    Args:
        name: Имя инструмента (уникальное)
        description: Описание для LLM
        policy: Политика выполнения
        modes: Режимы агента, в которых доступен инструмент
        is_mutating: True, если инструмент меняет состояние (файлы, git,
            shell). Используется UI для выбора иконки/цвета; дефолт False.
        category: ``filesystem`` | ``shell`` | ``network`` | ``git`` | ``info``.
            Тоже для UI-подсказок; дефолт ``filesystem``.
    """

    def decorator(func: F) -> F:
        tool_meta: dict[str, Any] = {
            "name": name,
            "description": description,
            "policy": policy,
            "modes": modes or [AgentMode.PLAN, AgentMode.AGENT],
            "is_mutating": is_mutating,
            "category": category,
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

        self._tools[meta["name"]] = {
            **meta,
            "schema": schema,
            "validator": _build_arg_validator(func),
        }
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
            {
                "name": name,
                "description": meta.get("description", ""),
                "policy": meta.get("policy", ToolPolicy.AUTO),
                "is_mutating": meta.get("is_mutating", False),
                "category": meta.get("category", "filesystem"),
            }
            for name, meta in self._tools.items()
        ]

    async def execute(self, tool_call: ToolCall, **extra_kwargs: Any) -> ToolResult:
        """Асинхронно выполняет инструмент и возвращает результат.

        Args:
            tool_call: Вызов инструмента
            extra_kwargs: Дополнительные аргументы (например, project_root, vector_store)

        Returns:
            ToolResult с результатом или ошибкой. У ошибок выставлен
            ``error_category``, чтобы ``AgentLoop`` мог дописать подсказку
            (см. `agents/agent_loop.py::_RETRY_HINTS`).
        """
        meta = self._tools.get(tool_call.name)
        if not meta:
            payload = {
                "error": "unknown_tool",
                "requested": tool_call.name,
                "available": sorted(self._tools.keys()),
            }
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps(payload, ensure_ascii=False),
                is_error=True,
                error_category=ErrorCategory.FIX_ARGUMENTS,
            )

        func = meta["func"]
        sig = inspect.signature(func)
        filtered_kwargs = {k: v for k, v in extra_kwargs.items() if k in sig.parameters}

        validator = meta.get("validator")
        if validator is not None:
            provided = {k: v for k, v in tool_call.arguments.items() if k not in _INJECTED_PARAMS}
            try:
                validated = validator.model_validate(provided)
            except ValidationError as e:
                logger.warning(f"Неверные аргументы вызова {tool_call.name}: {e}")
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=f"Ошибка аргументов инструмента '{tool_call.name}': {e}",
                    is_error=True,
                    error_category=ErrorCategory.FIX_ARGUMENTS,
                )
            args_from_model = validated.model_dump()
        else:
            args_from_model = dict(tool_call.arguments)

        merged_args = {**args_from_model, **filtered_kwargs}
        try:
            if inspect.iscoroutinefunction(func):
                result_content = await func(**merged_args)
            else:
                result_content = func(**merged_args)

            # [9.6.1] Инструмент может вернуть dict — тогда `_llm_text`
            # идёт в content (для LLM), остальные поля — в metadata (для UI).
            # Строгий контракт: dict должен содержать `_llm_text` (str);
            # если нет — JSON дампим в content, metadata = весь dict.
            if isinstance(result_content, dict):
                llm_text = result_content.get("_llm_text")
                if not isinstance(llm_text, str):
                    try:
                        llm_text = json.dumps(result_content, ensure_ascii=False)
                    except (TypeError, ValueError):
                        llm_text = str(result_content)
                metadata = {k: v for k, v in result_content.items() if k != "_llm_text"}
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=llm_text,
                    metadata=metadata or None,
                )

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=str(result_content),
            )
        except ToolExecutionError:
            raise
        except FileNotFoundError as e:
            logger.warning(f"Инструмент {tool_call.name}: файл не найден: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: файл не найден: {e}",
                is_error=True,
                error_category=ErrorCategory.DIFFERENT_PATH,
            )
        except (ConnectionError, TimeoutError, httpx.TransportError) as e:
            logger.warning(f"Инструмент {tool_call.name}: временная сетевая ошибка: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: временная сетевая проблема: {e}",
                is_error=True,
                error_category=ErrorCategory.RETRY_LATER,
            )
        except Exception as e:
            logger.error(f"Ошибка выполнения инструмента {tool_call.name}: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: {e}",
                is_error=True,
                error_category=ErrorCategory.REPORT_TO_USER,
            )
