"""Реестр инструментов с автоматической генерацией JSON-схем."""

import asyncio
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints

import httpx
from loguru import logger
from pydantic import BaseModel, TypeAdapter, ValidationError, create_model

from agents.ollama_client import ToolCall, ToolSchema
from core import redaction
from core.config import AgentMode, ToolPolicy
from core.exceptions import ToolExecutionError

if TYPE_CHECKING:
    from core.config import AppConfig
    from core.policy import EffectivePolicy
    from core.profiles import ProfileConfig
    from core.security import SecurityPolicy

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


# ---------------------------------------------------------------------------
# 2.6: ToolSpec v2 и capabilities
# ---------------------------------------------------------------------------

# Допустимые capability-строки. Закрытый список — правило: capability
# описывает класс доступа, а не конкретный инструмент. Новые добавляются
# осознанно, потому что от них зависит Policy Engine (2.7).
_VALID_CAPABILITIES: frozenset[str] = frozenset(
    {
        "fs.read",  # чтение файлов проекта (read_file, list_directory, …)
        "fs.write",  # запись файлов / изменение рабочей копии
        "net",  # исходящий HTTP (web_search, get_weather, курсы)
        "shell",  # выполнение shell-команд
        "git",  # git-операции (status/diff/stash/commit)
        "ui",  # внутреннее состояние агента (гипотезы, RAG-контекст)
        "secrets",  # доступ к секретам (.env, *.pem, id_rsa*) — 2.7
    }
)

# Дефолт для инструментов без явных capabilities. Менять его — ломать
# совместимость: инструмент без `capabilities=` по умолчанию read-only.
_DEFAULT_CAPABILITIES: frozenset[str] = frozenset({"fs.read"})


@dataclass(frozen=True)
class ToolSpec:
    """Immutable контракт инструмента (2.6).

    Раньше метаданные жили в `dict[str, Any]` и были изменяемы. `ToolSpec`
    фиксирует форму, чтобы Policy Engine (2.7) и `ToolRegistry.execute`
    могли опираться на гарантированные поля. Все поля — immutable.

    Attributes:
        name: Уникальное имя (то, что модель вызывает).
        description: Описание для LLM.
        schema: JSON Schema аргументов (без инжектируемых).
        modes: frozenset разрешённых AgentMode.
        policy: Политика по умолчанию (AUTO/ASK_FIRST/EXCLUDED).
        capabilities: Классы доступа (``fs.read``, ``net``, …).
        is_mutating: Меняет ли состояние (файлы / git / shell).
        category: UI-подсказка (``filesystem`` | ``shell`` | …).
        timeout_s: Таймаут выполнения в секундах (``0`` = без ограничения).
        max_output_chars: Максимум символов результата (0 = без ограничения).
            Пока не применяется в `execute`; поле для будущей обрезки в UI/логах.
        idempotent: True — повторный вызов с теми же аргументами безопасен
            (поле-подсказка для будущего кэша).
    """

    name: str
    description: str
    schema: dict[str, Any]
    modes: frozenset[AgentMode]
    policy: ToolPolicy
    capabilities: frozenset[str]
    is_mutating: bool
    category: str
    timeout_s: float = 30.0
    max_output_chars: int = 5000
    idempotent: bool = False


def _normalize_capabilities(
    caps: list[str] | tuple[str, ...] | frozenset[str] | None,
) -> frozenset[str]:
    """Приводит capabilities к frozenset и валидирует имена.

    Args:
        caps: Список/кортеж/frozenset имён capability. ``None`` — дефолт
            (``{"fs.read"}``).

    Returns:
        frozenset валидных capability-строк.

    Raises:
        ValueError: Если передано неизвестное имя (опечатка / устаревшее).
    """
    if caps is None:
        return _DEFAULT_CAPABILITIES
    result = caps if isinstance(caps, frozenset) else frozenset(caps)
    unknown = result - _VALID_CAPABILITIES
    if unknown:
        raise ValueError(
            f"Неизвестные capabilities: {sorted(unknown)}. Допустимые: {sorted(_VALID_CAPABILITIES)}"
        )
    return result


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
    *,
    capabilities: list[str] | None = None,
    timeout_s: float = 30.0,
    max_output_chars: int = 5000,
    idempotent: bool = False,
) -> Callable[[F], F]:
    """Декоратор для регистрации функции как инструмента (2.6: ToolSpec v2).

    Args:
        name: Имя инструмента (уникальное)
        description: Описание для LLM
        policy: Политика выполнения
        modes: Режимы агента, в которых доступен инструмент
        is_mutating: True, если инструмент меняет состояние (файлы, git,
            shell). Используется UI для выбора иконки/цвета; дефолт False.
        category: ``filesystem`` | ``shell`` | ``network`` | ``git`` | ``info``.
            Тоже для UI-подсказок; дефолт ``filesystem``.
        capabilities: Классы доступа (``fs.read``, ``fs.write``, ``net``,
            ``shell``, ``git``, ``ui``, ``secrets``). ``None`` — дефолт
            ``{"fs.read"}``. Проверка capabilities → Policy Engine (2.7).
        timeout_s: Таймаут выполнения, секунды. ``0`` — без ограничения
            (инструмент сам управляет временем). Применяется в
            ``ToolRegistry.execute`` через ``asyncio.wait_for``.
        max_output_chars: Максимум символов результата (0 = без ограничения).
            Пока не применяется; задел под обрезку в UI/логах.
        idempotent: True — повторный вызов безопасен (задел под кэш).

    Raises:
        ValueError: Неизвестная capability / отрицательный timeout.
    """
    caps = _normalize_capabilities(capabilities)
    if timeout_s < 0:
        raise ValueError(f"timeout_s must be >= 0, got {timeout_s}")
    if max_output_chars < 0:
        raise ValueError(f"max_output_chars must be >= 0, got {max_output_chars}")

    def decorator(func: F) -> F:
        tool_meta: dict[str, Any] = {
            "name": name,
            "description": description,
            "policy": policy,
            "modes": modes or [AgentMode.PLAN, AgentMode.AGENT],
            "is_mutating": is_mutating,
            "category": category,
            "capabilities": caps,
            "timeout_s": timeout_s,
            "max_output_chars": max_output_chars,
            "idempotent": idempotent,
            "func": func,
        }
        func._tool_meta = tool_meta  # type: ignore[attr-defined]
        return func

    return decorator


class ToolRegistry:
    """Реестр зарегистрированных инструментов."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        # 2.6: immutable ToolSpec по имени. Единая точка доступа для
        # Policy Engine (2.7), UI (tooltips) и тестов.
        self._specs: dict[str, ToolSpec] = {}

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

        # 2.6: immutable ToolSpec.
        self._specs[meta["name"]] = ToolSpec(
            name=meta["name"],
            description=meta["description"],
            schema=schema,
            modes=frozenset(meta["modes"]),
            policy=meta["policy"],
            capabilities=meta["capabilities"],
            is_mutating=meta["is_mutating"],
            category=meta["category"],
            timeout_s=float(meta["timeout_s"]),
            max_output_chars=int(meta["max_output_chars"]),
            idempotent=bool(meta["idempotent"]),
        )

        logger.debug(
            f"Зарегистрирован инструмент: {meta['name']} "
            f"(policy={meta['policy'].value}, "
            f"caps={sorted(meta['capabilities'])}, "
            f"timeout={meta['timeout_s']}s)"
        )

    def register_spec(
        self,
        spec: ToolSpec,
        func: Callable[..., Any],
        *,
        validator: type[BaseModel] | None = None,
    ) -> None:
        """Регистрирует готовый `ToolSpec` + callable (7: MCP-инструменты).

        В отличие от `register`, здесь нет `@tool`-декоратора: спецификация
        приходит извне (MCP-манифест сервера). `func` — async или sync
        callable; сигнатура принимает **kwargs, соответствующие
        `spec.schema["properties"]`.

        Args:
            spec: Immutable-контракт инструмента.
            func: Callable. Для MCP — async-обёртка над `MCPClient.call_tool`.
            validator: Pydantic-модель аргументов (опционально). Для MCP
                не используется — валидация на стороне сервера.
        """
        self._specs[spec.name] = spec
        self._tools[spec.name] = {
            "name": spec.name,
            "description": spec.description,
            "policy": spec.policy,
            "modes": list(spec.modes),
            "is_mutating": spec.is_mutating,
            "category": spec.category,
            "capabilities": spec.capabilities,
            "timeout_s": spec.timeout_s,
            "max_output_chars": spec.max_output_chars,
            "idempotent": spec.idempotent,
            "func": func,
            "schema": spec.schema,
            "validator": validator,
        }
        logger.debug(
            f"Зарегистрирован ToolSpec: {spec.name} "
            f"(policy={spec.policy.value}, caps={sorted(spec.capabilities)})"
        )

    # ------------------------------------------------------------------
    # 2.6: доступ к immutable ToolSpec
    # ------------------------------------------------------------------

    def get_spec(self, tool_name: str) -> ToolSpec:
        """Возвращает immutable ``ToolSpec`` по имени.

        Args:
            tool_name: Имя зарегистрированного инструмента.

        Returns:
            ``ToolSpec`` для инструмента.

        Raises:
            KeyError: Если инструмент не зарегистрирован. Caller должен
                либо знать имя (UI-таблица, Policy Engine после
                ``execute``-валидации), либо использовать ``try_get_spec``.
        """
        spec = self._specs.get(tool_name)
        if spec is None:
            raise KeyError(
                f"Инструмент '{tool_name}' не зарегистрирован. Доступные: {sorted(self._specs.keys())}"
            )
        return spec

    def try_get_spec(self, tool_name: str) -> ToolSpec | None:
        """То же, что :meth:`get_spec`, но ``None`` вместо ``KeyError``."""
        return self._specs.get(tool_name)

    def get_all_specs(self) -> list[ToolSpec]:
        """Все зарегистрированные ``ToolSpec`` (для UI/диагностики)."""
        return list(self._specs.values())

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

    def get_effective_policy(
        self,
        tool_name: str,
        *,
        profile: "ProfileConfig | None" = None,
        security: "SecurityPolicy | None" = None,
        config: "AppConfig | None" = None,
        session_overrides: dict[str, ToolPolicy] | None = None,
        current_mode: AgentMode | None = None,
    ) -> "EffectivePolicy":
        """Возвращает `EffectivePolicy` с учётом всех слоёв (2.7).

        Args:
            tool_name: Имя инструмента.
            profile: Активный профиль (``ProfileConfig``).
            security: Политика безопасности (``SecurityPolicy``).
            config: Глобальный конфиг (``AppConfig``).
            session_overrides: Per-session override пользователя.
            current_mode: Текущий режим (``AgentMode``).

        Returns:
            `EffectivePolicy`. Неизвестный инструмент → ``EXCLUDED``
            с reason ``"unknown"``.
        """
        from core.policy import EffectivePolicy, effective_policy

        spec = self.try_get_spec(tool_name)
        if spec is None:
            return EffectivePolicy(policy=ToolPolicy.EXCLUDED, allowed=False, reason="unknown")
        return effective_policy(
            spec,
            profile=profile,
            security=security,
            config=config,
            session_overrides=session_overrides,
            current_mode=current_mode,
        )

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
                    content=redaction.redact_only(
                        f"Ошибка аргументов инструмента '{tool_call.name}': {e}",
                    ),
                    is_error=True,
                    error_category=ErrorCategory.FIX_ARGUMENTS,
                )
            args_from_model = validated.model_dump()
        else:
            args_from_model = dict(tool_call.arguments)

        merged_args = {**args_from_model, **filtered_kwargs}
        # 2.6: timeout_s из ToolSpec. ``timeout_s=0`` — без ограничения
        # (run_shell_command сам управляет временем через свой параметр
        # `timeout`, max 300с). Sync-инструменты уходят в отдельный поток
        # через ``asyncio.to_thread`` — loop-поток не блокируется (важно
        # для AgentChatThread: loop один, но и для будущего параллелизма
        # инструментов).
        spec = self._specs.get(tool_call.name)
        timeout_s = spec.timeout_s if spec is not None else 0.0

        try:
            if inspect.iscoroutinefunction(func):
                coro = func(**merged_args)
                if timeout_s > 0:
                    result_content = await asyncio.wait_for(coro, timeout=timeout_s)
                else:
                    result_content = await coro
            else:
                if timeout_s > 0:
                    result_content = await asyncio.wait_for(
                        asyncio.to_thread(func, **merged_args),
                        timeout=timeout_s,
                    )
                else:
                    result_content = await asyncio.to_thread(func, **merged_args)

            # [9.6.1] Инструмент может вернуть dict — тогда `_llm_text`
            # идёт в content (для LLM), остальные поля — в metadata (для UI).
            # 0.1.2: content проходит redaction (секреты не утекают в LLM/UI/сессию);
            # metadata (diff, written_content, original_content) НЕ трогаем —
            # это пользовательские файлы, third-party их не видит.
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
                    content=redaction.redact_only(llm_text),
                    metadata=metadata or None,
                )

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=redaction.redact_only(str(result_content)),
            )
        except TimeoutError:
            logger.warning(f"Инструмент {tool_call.name}: таймаут {timeout_s:.1f}с (timeout_s из ToolSpec)")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=redaction.redact_only(
                    f"Ошибка: инструмент превысил таймаут "
                    f"{timeout_s:.1f} с (timeout_s). "
                    f"Сократи объём работы или используй другой инструмент."
                ),
                is_error=True,
                error_category=ErrorCategory.RETRY_LATER,
            )
        except ToolExecutionError:
            raise
        except FileNotFoundError as e:
            logger.warning(f"Инструмент {tool_call.name}: файл не найден: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=redaction.redact_only(f"Ошибка: файл не найден: {e}"),
                is_error=True,
                error_category=ErrorCategory.DIFFERENT_PATH,
            )
        except (ConnectionError, httpx.TransportError) as e:
            logger.warning(f"Инструмент {tool_call.name}: временная сетевая ошибка: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=redaction.redact_only(f"Ошибка: временная сетевая проблема: {e}"),
                is_error=True,
                error_category=ErrorCategory.RETRY_LATER,
            )
        except Exception as e:
            # 0.1.2: redact_exc безопасно обрабатывает даже «битые» исключения
            # (битый __str__), и редактирует секреты в тексте ошибки.
            safe_error = redaction.redact_exc(e)
            logger.error(f"Ошибка выполнения инструмента {tool_call.name}: {safe_error}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: {safe_error}",
                is_error=True,
                error_category=ErrorCategory.REPORT_TO_USER,
            )
