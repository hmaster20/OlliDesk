"""Policy Engine (2.7): единая точка авторизации инструментов.

Заменяет три места, где политики раскиданы сейчас:
  * `@tool(policy=...)` — дефолт;
  * `ChatPanel._apply_config_policies` — config.tool_policies;
  * `ProfileToolPolicies` — профиль.

`effective_policy(spec, ...)` сводит их в один `EffectivePolicy` с полем
``reason`` — «почему именно так». Это и для UI (Permissions Dashboard),
и для отладки («почему команда заблокирована»).

**Приоритет (сверху вниз):**

1. ``secrets`` — всегда ``excluded`` (даже в профиле ``open``).
2. ``security.confidential`` — блокирует ``net``/``shell``/``git``/``browser``.
3. ``current_mode not in spec.modes`` — блокирует (safety; модель не должна
   вызывать инструменты не из своего режима).
4. ``freedom_level`` профиля — базовая политика по capabilities.
5. ``profile.tool_policies`` — явный override профиля.
6. ``session_overrides`` — per-session override пользователя.
7. ``config.tool_policies`` — глобальный конфиг.
8. ``@tool(policy=...)`` — дефолт декоратора.

Особый случай: если ``freedom_level=auto``, а декоратор=``ask_first``
(мутирующий инструмент) — доверяем декоратору. Инструмент сам знает,
что он опасный; свобода не должна молча включать запись.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import AgentMode, ToolPolicy

if TYPE_CHECKING:
    from agents.tool_registry import ToolSpec
    from core.config import AppConfig
    from core.profiles import ProfileConfig
    from core.security import SecurityPolicy

FreedomLevel = str  # Literal["safe", "research", "development", "open"]


@dataclass(frozen=True)
class EffectivePolicy:
    """Итоговая политика инструмента (2.7).

    Attributes:
        policy: ``auto`` | ``ask_first`` | ``excluded``.
        allowed: ``False`` — инструмент заблокирован (execute вернёт
            ошибку, не выполняя).
        reason: Слой, принявший решение: ``"security:secrets"``,
            ``"security:confidential"``, ``"mode"``, ``"freedom"``,
            ``"profile"``, ``"session"``, ``"config"``, ``"default"``.
    """

    policy: ToolPolicy
    allowed: bool
    reason: str


# Матрица freedom_level × capability (9.12).
# Значение — минимальная политика для capability:
#   "auto"      — можно без подтверждения;
#   "ask_first" — подтверждение обязательно;
#   "excluded"  — запрещено.
#
# ВАЖНО: `secrets` ВСЕГДА excluded — независимо от freedom_level.
# Это осознанное решение: агент не должен читать `.env`/`*.pem`/`id_rsa*`
# даже в режиме «open». Для чтения секретов — отдельный capability,
# который появится, когда будет UI «разрешить конкретный файл на сессию».
_FREEDOM_MATRIX: dict[str, dict[str, str]] = {
    "safe": {
        "fs.read": "auto",
        "fs.write": "ask_first",
        "net": "ask_first",
        "shell": "excluded",
        "git": "excluded",
        "browser": "excluded",
        "secrets": "excluded",
        "ui": "auto",
    },
    "research": {
        "fs.read": "auto",
        "fs.write": "ask_first",
        "net": "auto",
        "shell": "excluded",
        "git": "excluded",
        "browser": "excluded",
        "secrets": "excluded",
        "ui": "auto",
    },
    "development": {
        "fs.read": "auto",
        "fs.write": "auto",
        "net": "ask_first",
        "shell": "ask_first",
        "git": "ask_first",
        "browser": "ask_first",
        "secrets": "excluded",
        "ui": "auto",
    },
    "open": {
        "fs.read": "auto",
        "fs.write": "auto",
        "net": "auto",
        "shell": "ask_first",  # shell ВСЕГДА спрашивает (dangerous mode)
        "git": "auto",
        "browser": "auto",
        "secrets": "excluded",  # никогда
        "ui": "auto",
    },
}

# Приоритет строгости: excluded > ask_first > auto.
_STRICTNESS: dict[str, int] = {
    "auto": 0,
    "ask_first": 1,
    "excluded": 2,
}


def _capabilities_of(spec: ToolSpec) -> frozenset[str]:
    """Capabilities инструмента; ``{"fs.read"}``, если пусто."""
    return spec.capabilities or frozenset({"fs.read"})


def _most_restrictive(policies: list[str]) -> str:
    """Возвращает самую строгую политику из списка."""
    if not policies:
        return "auto"
    return max(policies, key=lambda p: _STRICTNESS.get(p, 0))


def effective_policy(
    spec: ToolSpec,
    *,
    profile: ProfileConfig | None = None,
    security: SecurityPolicy | None = None,
    config: AppConfig | None = None,
    session_overrides: dict[str, ToolPolicy] | None = None,
    current_mode: AgentMode | None = None,
) -> EffectivePolicy:
    """Сводит политики в один `EffectivePolicy` (2.7).

    Args:
        spec: Immutable-контракт инструмента (`agents.tool_registry.ToolSpec`).
        profile: Активный профиль (``ProfileConfig``) или ``None``.
        security: Политика безопасности (``SecurityPolicy``) или ``None``.
        config: Глобальный конфиг (``AppConfig``) или ``None``.
        session_overrides: Per-session override пользователя
            (``{"run_shell_command": ASK_FIRST}``).
        current_mode: Текущий режим (`AgentMode.CHAT/PLAN/AGENT`).
            ``None`` — не проверять.

    Returns:
        `EffectivePolicy` с полем ``reason``.
    """
    caps = _capabilities_of(spec)

    # 0. `secrets` — всегда excluded. Даже в open. Это не обсуждается.
    if "secrets" in caps:
        return EffectivePolicy(
            ToolPolicy.EXCLUDED, False, "security:secrets"
        )

    # 1. SecurityPolicy: confidential → блокируем опасные capabilities.
    if security is not None and security.confidential:
        if caps & {"net", "shell", "git", "browser"}:
            return EffectivePolicy(
                ToolPolicy.EXCLUDED, False, "security:confidential"
            )

    # 2. Режим: инструмент не для текущего режима.
    if current_mode is not None and current_mode not in spec.modes:
        return EffectivePolicy(
            ToolPolicy.EXCLUDED, False, "mode"
        )

    # 3. freedom_level из профиля — базовая политика по capabilities.
    freedom_policy: ToolPolicy | None = None
    if profile is not None:
        level = getattr(profile, "freedom_level", None)
        if level and level in _FREEDOM_MATRIX:
            matrix = _FREEDOM_MATRIX[level]
            per_cap = [matrix.get(cap, "auto") for cap in caps]
            strictest = _most_restrictive(per_cap)
            if strictest == "excluded":
                return EffectivePolicy(
                    ToolPolicy.EXCLUDED, False, "freedom:excluded"
                )
            freedom_policy = (
                ToolPolicy.ASK_FIRST
                if strictest == "ask_first"
                else ToolPolicy.AUTO
            )

    # 4. profile.tool_policies — явный override профиля.
    if profile is not None:
        explicit = getattr(profile.tool_policies, spec.name, None)
        if explicit is not None:
            try:
                p = ToolPolicy(explicit)
            except ValueError:
                p = None
            if p is not None:
                return EffectivePolicy(
                    p, p != ToolPolicy.EXCLUDED, "profile"
                )

    # 5. session overrides.
    if session_overrides and spec.name in session_overrides:
        p = session_overrides[spec.name]
        return EffectivePolicy(
            p, p != ToolPolicy.EXCLUDED, "session"
        )

    # 6. config.tool_policies.
    if config is not None and spec.name in config.tool_policies:
        p = config.tool_policies[spec.name]
        return EffectivePolicy(
            p, p != ToolPolicy.EXCLUDED, "config"
        )

    # 7. freedom fallback: `auto` + decorator `ask_first` → уважаем
    #    декоратор (мутирующий инструмент сам знает, что опасный).
    if freedom_policy is not None:
        if (
            freedom_policy == ToolPolicy.AUTO
            and spec.policy == ToolPolicy.ASK_FIRST
        ):
            return EffectivePolicy(
                ToolPolicy.ASK_FIRST, True, "default:decorator"
            )
        return EffectivePolicy(
            freedom_policy,
            freedom_policy != ToolPolicy.EXCLUDED,
            "freedom",
        )

    # 8. decorator default.
    return EffectivePolicy(
        spec.policy, spec.policy != ToolPolicy.EXCLUDED, "default"
    )


# ---------------------------------------------------------------------------
# 9.10: объяснение цепочки политик
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyStep:
    """Один шаг в цепочке применения политик (9.10).

    Attributes:
        layer: ``"secrets"`` | ``"security"`` | ``"mode"`` | ``"freedom"``
            | ``"profile"`` | ``"session"`` | ``"config"`` | ``"decorator"``.
        applied: Применялся ли слой (если нет — «пропущен»).
        policy: Результирующая политика слоя (``auto``/``ask_first``/``excluded``).
        reason: Пояснение (например, ``"confidential=true"``,
            ``"freedom_level=safe, capabilities={shell}"``).
        blocking: ``True`` — этот слой принял финальное решение (дальше
            не идти).
    """

    layer: str
    applied: bool
    policy: str
    reason: str
    blocking: bool = False


def explain_policy(
    spec: ToolSpec,
    *,
    profile: ProfileConfig | None = None,
    security: SecurityPolicy | None = None,
    config: AppConfig | None = None,
    session_overrides: dict[str, ToolPolicy] | None = None,
    current_mode: AgentMode | None = None,
) -> tuple[EffectivePolicy, list[PolicyStep]]:
    """Возвращает `EffectivePolicy` + пошаговое объяснение (9.10).

    Дублирует логику `effective_policy`, но записывает КАЖДЫЙ слой в
    список `PolicyStep` (применён / пропущен / заблокировал). UI
    показывает этот список в Permissions Dashboard → «Почему заблокировано».

    Args:
        spec: `ToolSpec`.
        profile: Активный профиль.
        security: `SecurityPolicy`.
        config: `AppConfig`.
        session_overrides: Per-session overrides.
        current_mode: Текущий режим.

    Returns:
        ``(effective, steps)``. ``effective`` — то же, что вернул бы
        `effective_policy`; ``steps`` — история слоёв.
    """
    caps = _capabilities_of(spec)
    steps: list[PolicyStep] = []

    def _step(
        layer: str,
        *,
        applied: bool,
        policy: str,
        reason: str,
        blocking: bool = False,
    ) -> None:
        steps.append(
            PolicyStep(
                layer=layer,
                applied=applied,
                policy=policy,
                reason=reason,
                blocking=blocking,
            )
        )

    # 0. secrets — всегда excluded.
    if "secrets" in caps:
        _step(
            "secrets",
            applied=True,
            policy="excluded",
            reason=f"capabilities={sorted(caps)} содержит secrets",
            blocking=True,
        )
        return (
            EffectivePolicy(
                ToolPolicy.EXCLUDED, False, "security:secrets"
            ),
            steps,
        )
    _step(
        "secrets",
        applied=False,
        policy="—",
        reason="secrets не в capabilities",
    )

    # 1. SecurityPolicy.
    if security is not None and security.confidential:
        if caps & {"net", "shell", "git", "browser"}:
            _step(
                "security",
                applied=True,
                policy="excluded",
                reason="confidential=true, capabilities пересекаются с {net,shell,git,browser}",
                blocking=True,
            )
            return (
                EffectivePolicy(
                    ToolPolicy.EXCLUDED, False, "security:confidential"
                ),
                steps,
            )
        _step(
            "security",
            applied=False,
            policy="—",
            reason="confidential=true, но capabilities не опасные",
        )
    else:
        _step(
            "security",
            applied=False,
            policy="—",
            reason="confidential=false или security=None",
        )

    # 2. Режим.
    if current_mode is not None and current_mode not in spec.modes:
        _step(
            "mode",
            applied=True,
            policy="excluded",
            reason=f"mode={current_mode.value} не в spec.modes={sorted(m.value for m in spec.modes)}",
            blocking=True,
        )
        return (
            EffectivePolicy(ToolPolicy.EXCLUDED, False, "mode"),
            steps,
        )
    _step(
        "mode",
        applied=False,
        policy="—",
        reason="режим допустим",
    )

    # 3. freedom_level.
    freedom_policy: ToolPolicy | None = None
    if profile is not None:
        level = getattr(profile, "freedom_level", None)
        if level and level in _FREEDOM_MATRIX:
            matrix = _FREEDOM_MATRIX[level]
            per_cap = [matrix.get(cap, "auto") for cap in caps]
            strictest = _most_restrictive(per_cap)
            _step(
                "freedom",
                applied=True,
                policy=strictest,
                reason=(
                    f"freedom_level={level}, "
                    f"capabilities={sorted(caps)} → {strictest}"
                ),
                blocking=(strictest == "excluded"),
            )
            if strictest == "excluded":
                return (
                    EffectivePolicy(
                        ToolPolicy.EXCLUDED, False, "freedom:excluded"
                    ),
                    steps,
                )
            freedom_policy = (
                ToolPolicy.ASK_FIRST
                if strictest == "ask_first"
                else ToolPolicy.AUTO
            )
        else:
            _step(
                "freedom",
                applied=False,
                policy="—",
                reason="freedom_level не задан или неизвестен",
            )
    else:
        _step(
            "freedom",
            applied=False,
            policy="—",
            reason="profile=None",
        )

    # 4. profile.tool_policies.
    if profile is not None:
        explicit = getattr(profile.tool_policies, spec.name, None)
        if explicit is not None:
            try:
                p = ToolPolicy(explicit)
            except ValueError:
                p = None
            if p is not None:
                _step(
                    "profile",
                    applied=True,
                    policy=p.value,
                    reason=f"profile.tool_policies[{spec.name!r}]={p.value}",
                    blocking=True,
                )
                return (
                    EffectivePolicy(
                        p, p != ToolPolicy.EXCLUDED, "profile"
                    ),
                    steps,
                )
        _step(
            "profile",
            applied=False,
            policy="—",
            reason="нет явного override в profile.tool_policies",
        )
    else:
        _step(
            "profile",
            applied=False,
            policy="—",
            reason="profile=None",
        )

    # 5. session overrides.
    if session_overrides and spec.name in session_overrides:
        p = session_overrides[spec.name]
        _step(
            "session",
            applied=True,
            policy=p.value,
            reason=f"session_overrides[{spec.name!r}]={p.value}",
            blocking=True,
        )
        return (
            EffectivePolicy(p, p != ToolPolicy.EXCLUDED, "session"),
            steps,
        )
    _step(
        "session",
        applied=False,
        policy="—",
        reason="нет session-override",
    )

    # 6. config.tool_policies.
    if config is not None and spec.name in config.tool_policies:
        p = config.tool_policies[spec.name]
        _step(
            "config",
            applied=True,
            policy=p.value,
            reason=f"config.tool_policies[{spec.name!r}]={p.value}",
            blocking=True,
        )
        return (
            EffectivePolicy(p, p != ToolPolicy.EXCLUDED, "config"),
            steps,
        )
    _step(
        "config",
        applied=False,
        policy="—",
        reason="нет override в config.tool_policies",
    )

    # 7. freedom fallback / decorator.
    if freedom_policy is not None:
        if (
            freedom_policy == ToolPolicy.AUTO
            and spec.policy == ToolPolicy.ASK_FIRST
        ):
            _step(
                "decorator",
                applied=True,
                policy="ask_first",
                reason="freedom=auto, но декоратор=ask_first (мутирующий)",
                blocking=True,
            )
            return (
                EffectivePolicy(
                    ToolPolicy.ASK_FIRST, True, "default:decorator"
                ),
                steps,
            )
        _step(
            "decorator",
            applied=True,
            policy=freedom_policy.value,
            reason=f"freedom={freedom_policy.value}",
            blocking=True,
        )
        return (
            EffectivePolicy(
                freedom_policy,
                freedom_policy != ToolPolicy.EXCLUDED,
                "freedom",
            ),
            steps,
        )

    _step(
        "decorator",
        applied=True,
        policy=spec.policy.value,
        reason=f"@tool(policy={spec.policy.value})",
        blocking=True,
    )
    return (
        EffectivePolicy(
            spec.policy, spec.policy != ToolPolicy.EXCLUDED, "default"
        ),
        steps,
    )


__all__ = [
    "EffectivePolicy",
    "PolicyStep",
    "effective_policy",
    "explain_policy",
]