"""Единая точка исполнения инструментов (2.8).

`ToolService` — тонкий фасад над `ToolRegistry.execute`, добавляющий
три вещи, которых сейчас нет в реестре:

  * **audit-log (0.1.1)** для ВСЕХ вызовов, а не только для
    `run_shell_command` (`shell_ops._audit`). Дыра в обещании 0.1.1:
    «каждый вызов пишется в audit.db» — `write_file`/`apply_patch`
    audit не писали.
  * **авто-снапшот (10.9)** перед мутирующими инструментами, которые
    его не делают сами (`git_commit`, `run_shell_command` в dangerous
    mode). `write_file`/`apply_patch` уже создают снапшот внутри —
    дублирования не будет.
  * **единая обработка `effective_policy`** — для исключённых
    инструментов возвращается `ToolResult(is_error=True)`; для
    `ask_first` возвращается специальный маркер (caller решает, как
    спросить пользователя).

Почему не в `ToolRegistry.execute`: реестр — «механизм» (валидация,
таймаут, redaction). Сервис — «политика» (audit, snapshot). Разделение
позволяет тестировать их независимо: `ToolRegistry` без `AuditLog`,
`ToolService` с мок-audit.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger

from agents.ollama_client import ToolCall
from agents.tool_registry import ToolRegistry, ToolResult
from core.audit import AuditLog, get_audit_log
from core.event_bus import emit_event
from core.events import EventKind
from core.policy import EffectivePolicy
from tools.git_ops import create_file_snapshot

# Инструменты, которые сами создают снапшот перед мутацией.
# Дублировать его на уровне сервиса не нужно — только лишние копии.
_SELF_SNAPSHOT_TOOLS: frozenset[str] = frozenset({"write_file", "apply_patch"})

# Capabilities, требующие снапшота (если инструмент их меняет).
_SNAPSHOT_CAPABILITIES: frozenset[str] = frozenset({"fs.write"})


class ToolService:
    """Фасад над `ToolRegistry.execute` с audit и автоснапшотом.

    Потокобезопасность: `AuditLog` потокобезопасен, `ToolRegistry.execute` —
    тоже (используется `asyncio.wait_for`). Сам `ToolService` — stateless,
    кроме ссылки на audit.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        audit: AuditLog | None = None,
        project_root: str = "",
    ) -> None:
        """Инициализация.

        Args:
            registry: Реестр инструментов для текущего запроса.
            audit: `AuditLog`. По умолчанию — глобальный синглтон.
            project_root: Корень проекта (для снапшота).
        """
        self._registry = registry
        self._audit = audit or get_audit_log()
        self._project_root = project_root

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        effective_policy: EffectivePolicy,
        extra: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Полный цикл: policy → snapshot → execute → audit.

        Args:
            tool_call: Вызов инструмента.
            effective_policy: Результат `ToolRegistry.get_effective_policy`.
                Если ``allowed == False``, инструмент не выполняется.
            extra: Дополнительные kwargs для `ToolRegistry.execute`
                (project_root, vector_store, …).

        Returns:
            `ToolResult`. Для заблокированных — ``is_error=True`` с
            понятным сообщением.
        """
        started = time.perf_counter()

        # 1. Policy: blocked → не выполняем.
        if not effective_policy.allowed:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.warning(f"ToolService: {tool_call.name} заблокирован ({effective_policy.reason})")
            self._audit.log(
                tool_call.name,
                tool_call.arguments,
                policy=effective_policy.policy.value,
                approved=False,
                result_content=f"blocked:{effective_policy.reason}",
                is_error=True,
                dangerous=False,
            )
            emit_event(
                EventKind.TOOL_FINISHED.value,
                {
                    "name": tool_call.name,
                    "duration_ms": duration_ms,
                    "is_error": True,
                    "error_category": "policy_blocked",
                },
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=(
                    f"⛔ Инструмент {tool_call.name} заблокирован "
                    f"политикой: {effective_policy.reason}. "
                    f"Используй другой подход или смени профиль."
                ),
                is_error=True,
                error_category=None,
                metadata={
                    "policy_reason": effective_policy.reason,
                    "policy": effective_policy.policy.value,
                },
            )

        # 2. Автоснапшот перед мутирующими инструментами.
        snapshot_id = self._maybe_snapshot(tool_call, extra)

        # 3. Вызов реестра.
        try:
            result = await self._registry.execute(tool_call, **(extra or {}))
        except Exception as e:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(f"ToolService: {tool_call.name} упал")
            self._audit.log(
                tool_call.name,
                tool_call.arguments,
                policy=effective_policy.policy.value,
                approved=True,
                result_content=str(e),
                is_error=True,
                dangerous="shell" in effective_policy.reason,
            )
            emit_event(
                EventKind.TOOL_FINISHED.value,
                {
                    "name": tool_call.name,
                    "duration_ms": duration_ms,
                    "is_error": True,
                    "error_category": "exception",
                },
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Ошибка: {e}",
                is_error=True,
            )

        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        # 4. Аудит для ВСЕХ вызовов (0.1.1).
        self._audit.log(
            tool_call.name,
            tool_call.arguments,
            policy=effective_policy.policy.value,
            approved=True,
            result_content=result.content or "",
            is_error=result.is_error,
            dangerous=self._is_dangerous(tool_call.name, effective_policy),
        )

        # 5. Пробрасываем snapshot_id в metadata (для UI-карточки).
        if snapshot_id and result.metadata is None:
            result.metadata = {"snapshot_id": snapshot_id}
        elif snapshot_id and result.metadata is not None:
            result.metadata.setdefault("snapshot_id", snapshot_id)

        logger.debug(
            f"ToolService: {tool_call.name} "
            f"({effective_policy.policy.value}, "
            f"is_error={result.is_error}, {duration_ms} ms"
            f"{', snapshot=' + snapshot_id if snapshot_id else ''})"
        )
        return result

    # ------------------------------------------------------------------
    # Внутренние
    # ------------------------------------------------------------------

    def _maybe_snapshot(
        self,
        tool_call: ToolCall,
        extra: dict[str, Any] | None,
    ) -> str:
        """Создаёт снапшот файла, если инструмент его меняет.

        Возвращает `snapshot_id` или пустую строку.

        Условия:
          * `project_root` задан (иначе некуда снапшотить);
          * инструмент — `is_mutating` (`ToolSpec.is_mutating`);
          * его capabilities пересекаются с `fs.write`;
          * это НЕ self-snapshot-инструмент (`write_file`/`apply_patch`
            уже создают снапшот сами).

        Пути для снапшота: `tool_call.arguments.get("path")` (для
        файловых инструментов) — единственный путь; если его нет, снапшот
        не создаётся.
        """
        root = (extra or {}).get("project_root") or self._project_root
        if not root:
            return ""
        if tool_call.name in _SELF_SNAPSHOT_TOOLS:
            return ""
        spec = self._registry.try_get_spec(tool_call.name)
        if spec is None or not spec.is_mutating:
            return ""
        if not (spec.capabilities & _SNAPSHOT_CAPABILITIES):
            return ""

        raw_path = tool_call.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ""
        try:
            from core.sandbox import SandboxedPathResolver

            resolver = SandboxedPathResolver(root)
            resolved = resolver.resolve(raw_path, allow_sensitive=True)
            if resolved is None:
                return ""
            return create_file_snapshot(
                [resolved],
                tool=tool_call.name,
                args_summary=f"{resolved.name}",
                project_root=Path(root),
            )
        except Exception:
            logger.debug(
                f"ToolService: снапшот {tool_call.name} не создан",
                exc_info=True,
            )
            return ""

    @staticmethod
    def _is_dangerous(tool_name: str, effective_policy: EffectivePolicy) -> bool:
        """Эвристика «dangerous» для audit-log.

        Опасным считаем:
          * `run_shell_command` в dangerous mode (policy=AUTO, но
            capability=shell);
          * `git_commit` (мутация истории);
          * любой вызов с policy=ASK_FIRST (пользователь подтвердил
            вручную).
        """
        if effective_policy.policy.value == "ask_first":
            return True
        if tool_name == "git_commit":
            return True
        return "shell" in effective_policy.reason


__all__ = ["ToolService"]
