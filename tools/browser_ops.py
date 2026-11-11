"""browser_* инструменты (2.11).

Каждый — тонкая async-обёртка над `BrowserBackend`. Singleton backend
создаётся при первом вызове (или при старте ChatPanel, если playwright
доступен). Политика: `capabilities=["browser"]` → `effective_policy`
даёт `auto`/`ask_first`/`excluded` в зависимости от freedom_level.
"""

from __future__ import annotations

from loguru import logger

from agents.browser_client import (
    BrowserBackend,
    BrowserError,
    create_backend,
)
from agents.tool_registry import tool
from core.config import AgentMode, BrowserConfig, ToolPolicy

# Singleton backend (один браузер на процесс).
_BACKEND: BrowserBackend | None = None


def _get_backend(config: BrowserConfig | None) -> BrowserBackend | None:
    """Возвращает singleton backend (создаёт при первом вызове)."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    if config is None:
        try:
            from core.config import load_config

            config = load_config().browser
        except Exception:
            logger.debug("browser: не удалось прочитать BrowserConfig")
            return None
    _BACKEND = create_backend(config)
    return _BACKEND


async def shutdown_browser() -> None:
    """Закрывает backend (вызывается из ChatPanel.shutdown)."""
    global _BACKEND
    if _BACKEND is not None:
        try:
            await _BACKEND.close()
        except Exception:
            logger.debug("browser shutdown failed", exc_info=True)
        _BACKEND = None


@tool(
    name="browser_open",
    description=(
        "Открывает URL в браузере. Возвращает снимок страницы "
        "(title, текст, кликабельные элементы с селекторами). "
        "Используй для работы с авторизованными сайтами, документацией, "
        "веб-приложениями. Режим (isolated/extension/full) определяется "
        "настройками браузера."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["browser"],
    timeout_s=60.0,
)
async def browser_open(url: str) -> str:
    """Открывает URL, возвращает снимок страницы."""
    if not url or not url.strip():
        return "Ошибка: пустой URL."
    backend = _get_backend(None)
    if backend is None:
        return (
            "Ошибка: browser_control недоступен. Установите playwright "
            "(`pip install playwright && playwright install chromium`) "
            "или включите extension-режим."
        )
    try:
        snap = await backend.open(url.strip())
    except BrowserError as e:
        return f"Ошибка браузера: {e}"
    except Exception as e:
        logger.exception("browser_open failed")
        return f"Ошибка: {e}"
    return snap.render_for_prompt()


@tool(
    name="browser_snapshot",
    description=(
        "Возвращает снимок текущей страницы (title, текст, кликабельные "
        "элементы). Вызывай после browser_click/browser_fill, чтобы увидеть "
        "результат."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["browser"],
    timeout_s=30.0,
)
async def browser_snapshot() -> str:
    """Снимок текущей страницы."""
    backend = _get_backend(None)
    if backend is None:
        return "Ошибка: browser_control недоступен."
    try:
        snap = await backend.snapshot()
    except BrowserError as e:
        return f"Ошибка браузера: {e}"
    return snap.render_for_prompt()


@tool(
    name="browser_click",
    description=(
        "Кликает по элементу с указанным CSS-селектором. Селектор — из "
        "browser_snapshot (`[selector]`). Возвращает новый снимок страницы."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["browser"],
    timeout_s=60.0,
)
async def browser_click(selector: str) -> str:
    """Клик по элементу."""
    if not selector or not selector.strip():
        return "Ошибка: пустой selector."
    backend = _get_backend(None)
    if backend is None:
        return "Ошибка: browser_control недоступен."
    try:
        snap = await backend.click(selector.strip())
    except BrowserError as e:
        return f"Ошибка клика: {e}"
    return snap.render_for_prompt()


@tool(
    name="browser_fill",
    description=(
        "Заполняет поле ввода (input/textarea). Селектор — из "
        "browser_snapshot. Возвращает новый снимок страницы."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["browser"],
    timeout_s=60.0,
)
async def browser_fill(selector: str, text: str) -> str:
    """Заполнение поля."""
    if not selector or not selector.strip():
        return "Ошибка: пустой selector."
    backend = _get_backend(None)
    if backend is None:
        return "Ошибка: browser_control недоступен."
    try:
        snap = await backend.fill(selector.strip(), text)
    except BrowserError as e:
        return f"Ошибка заполнения: {e}"
    return snap.render_for_prompt()


@tool(
    name="browser_close",
    description="Закрывает браузер (и все его вкладки).",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["browser"],
    timeout_s=15.0,
)
async def browser_close() -> str:
    """Закрывает браузер."""
    await shutdown_browser()
    return "Браузер закрыт."


__all__ = [
    "browser_click",
    "browser_close",
    "browser_fill",
    "browser_open",
    "browser_snapshot",
    "shutdown_browser",
]
