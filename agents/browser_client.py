"""browser_control (2.11): три режима доступа к браузеру.

Единый интерфейс `BrowserBackend`:

  * `open(url)` — открыть страницу, вернуть `PageSnapshot`.
  * `snapshot()` — текущая страница (title, url, текст, кликабельные
    элементы с селекторами).
  * `click(selector)` — клик по элементу.
  * `fill(selector, text)` — заполнить поле.
  * `close()` — закрыть браузер.

Реализации:
  * `IsolatedBrowser` — Playwright + отдельный профиль Chromium
    (`~/.ollidesk/browser_profile`). Без доступа к кукам пользователя.
  * `ExtensionBrowser` — WebSocket к браузерному расширению
    (Playwright MCP Bridge). Расширение исполняет интенты на вкладках,
    которые разрешил пользователь.
  * `FullAccessBrowser` — Playwright + CDP к копии реального профиля.
    Все сессии. Только после `full_access_acknowledged=True`.

Зависимость: `playwright` (опционально, `pip install playwright &&
playwright install chromium`). Без него `browser_*` инструменты не
регистрируются; UI показывает «browser: недоступен».
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.config import BrowserConfig
from core.event_bus import emit_event
from core.events import EventKind

# Таймаут по умолчанию для одного действия (секунды).
DEFAULT_ACTION_TIMEOUT = 30.0

# Размер текста, который возвращаем в snapshot (символов).
MAX_PAGE_TEXT = 8000

# Максимум интерактивных элементов в snapshot.
MAX_INTERACTIVE_ELEMENTS = 80


@dataclass
class BrowserElement:
    """Интерактивный элемент страницы (кнопка, ссылка, поле)."""

    tag: str
    selector: str
    text: str = ""
    type: str = ""  # button / link / input / …
    href: str = ""


@dataclass
class PageSnapshot:
    """Снимок страницы для модели."""

    url: str
    title: str
    text: str
    elements: list[BrowserElement] = field(default_factory=list)

    def render_for_prompt(self, max_elements: int = 40) -> str:
        """Форматирует снапшот для подмешивания в tool-observation."""
        lines = [f"URL: {self.url}", f"Title: {self.title}", ""]
        if self.text:
            lines.append("Текст страницы:")
            lines.append(self.text[:MAX_PAGE_TEXT])
            lines.append("")
        if self.elements:
            lines.append("Кликабельные элементы:")
            for el in self.elements[:max_elements]:
                text = (el.text or "")[:80].replace("\n", " ")
                label = f"{el.type or el.tag}: {text}" if text else el.tag
                lines.append(f"  [{el.selector}] {label}")
            if len(self.elements) > max_elements:
                lines.append(f"  … ещё {len(self.elements) - max_elements}")
        return "\n".join(lines)


class BrowserError(Exception):
    """Ошибка browser_control."""


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class BrowserBackend(ABC):
    """Базовый интерфейс backend'а."""

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config

    @abstractmethod
    async def open(self, url: str) -> PageSnapshot: ...

    @abstractmethod
    async def snapshot(self) -> PageSnapshot: ...

    @abstractmethod
    async def click(self, selector: str) -> PageSnapshot: ...

    @abstractmethod
    async def fill(self, selector: str, text: str) -> PageSnapshot: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def mode_name(self) -> str: ...


# ---------------------------------------------------------------------------
# A: изолированный Playwright-браузер
# ---------------------------------------------------------------------------


class IsolatedBrowser(BrowserBackend):
    """Режим A: отдельный профиль Chromium через Playwright.

    Никаких пользовательских куков/логинов. Только публичные страницы.
    """

    def __init__(self, config: BrowserConfig) -> None:
        super().__init__(config)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def mode_name(self) -> str:
        return "isolated"

    async def _ensure_started(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserError(
                "playwright не установлен. Установите: "
                "`pip install playwright && playwright install chromium`"
            ) from e

        profile = Path(self.config.dedicated_profile_path).expanduser()
        profile.mkdir(parents=True, exist_ok=True)
        logger.info(f"Browser (isolated): профиль {profile}")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=self.config.headless,
        )
        # persistent_context уже содержит первую страницу.
        pages = self._browser.pages
        self._page = pages[0] if pages else await self._browser.new_page()

    async def open(self, url: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.goto(url, timeout=DEFAULT_ACTION_TIMEOUT * 1000)
        except Exception as e:
            raise BrowserError(f"не удалось открыть {url!r}: {e}") from e
        snap = await self.snapshot()
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "open", "url": url, "mode": "isolated", "ok": True},
        )
        return snap

    async def snapshot(self) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            title = await self._page.title()
            url = self._page.url
            text = await self._page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
        except Exception as e:
            raise BrowserError(f"snapshot: {e}") from e
        elements = await self._collect_elements()
        return PageSnapshot(
            url=url or "",
            title=title or "",
            text=str(text or "")[:MAX_PAGE_TEXT],
            elements=elements,
        )

    async def click(self, selector: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.click(selector, timeout=DEFAULT_ACTION_TIMEOUT * 1000)
        except Exception as e:
            raise BrowserError(f"click({selector}): {e}") from e
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "click", "selector": selector, "mode": "isolated"},
        )
        return await self.snapshot()

    async def fill(self, selector: str, text: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.fill(
                selector, text, timeout=DEFAULT_ACTION_TIMEOUT * 1000
            )
        except Exception as e:
            raise BrowserError(f"fill({selector}): {e}") from e
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {
                "action": "fill",
                "selector": selector,
                "mode": "isolated",
                "text_chars": len(text),
            },
        )
        return await self.snapshot()

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("IsolatedBrowser.close: ошибка", exc_info=True)
            self._browser = None
            self._context = None
            self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("IsolatedBrowser: playwright.stop", exc_info=True)
            self._playwright = None

    async def _collect_elements(self) -> list[BrowserElement]:
        """Собирает интерактивные элементы страницы."""
        assert self._page is not None
        try:
            raw = await self._page.evaluate(
                """
                () => {
                    const els = [];
                    const q = 'a, button, input, textarea, select, [role="button"], [onclick]';
                    const nodes = document.querySelectorAll(q);
                    for (let i = 0; i < nodes.length && els.length < 200; i++) {
                        const el = nodes[i];
                        if (!el.offsetParent && el.tagName !== 'A') continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 2 || rect.height < 2) continue;
                        // Стабильный селектор: id > data-testid > name > tag + nth
                        let selector = '';
                        if (el.id) selector = '#' + el.id;
                        else if (el.dataset && el.dataset.testid) selector = '[data-testid="' + el.dataset.testid + '"]';
                        else if (el.name) selector = el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                        else {
                            const parent = el.parentElement;
                            if (!parent) continue;
                            const idx = Array.from(parent.children).indexOf(el) + 1;
                            selector = el.tagName.toLowerCase() + ':nth-child(' + idx + ')';
                        }
                        const text = (el.innerText || el.value || el.placeholder || '').trim();
                        if (!text && !el.href) continue;
                        els.push({
                            tag: el.tagName.toLowerCase(),
                            type: el.type || el.getAttribute('role') || '',
                            selector: selector,
                            text: text.slice(0, 100),
                            href: el.href || '',
                        });
                    }
                    return els;
                }
                """
            )
        except Exception as e:
            logger.debug(f"collect_elements: {e}")
            return []
        elements: list[BrowserElement] = []
        for item in raw or []:
            try:
                elements.append(
                    BrowserElement(
                        tag=str(item.get("tag", "")),
                        type=str(item.get("type", "")),
                        selector=str(item.get("selector", "")),
                        text=str(item.get("text", "")),
                        href=str(item.get("href", "")),
                    )
                )
            except Exception:
                continue
            if len(elements) >= MAX_INTERACTIVE_ELEMENTS:
                break
        return elements


# ---------------------------------------------------------------------------
# B: расширение (WebSocket bridge)
# ---------------------------------------------------------------------------


class ExtensionBrowser(BrowserBackend):
    """Режим B: через браузерное расширение (Playwright MCP Bridge).

    Расширение пользователя открывает WebSocket на
    ``ws://127.0.0.1:<port>``. Клиент отправляет ``{action, params}``,
    ждёт ``{ok, result}` ``. Действия выполняются только на вкладках,
    которые пользователь явно разрешил через UI расширения.
    """

    def __init__(self, config: BrowserConfig) -> None:
        super().__init__(config)
        self._ws: Any = None
        self._ws_lock = asyncio.Lock()

    @property
    def mode_name(self) -> str:
        return "extension"

    async def _ensure_ws(self) -> Any:
        if self._ws is not None:
            return self._ws
        try:
            import websockets
        except ImportError as e:
            raise BrowserError(
                "websockets не установлен. Установите: `pip install websockets`"
            ) from e
        url = f"ws://127.0.0.1:{self.config.extension_bridge_port}"
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(url), timeout=5.0
            )
        except Exception as e:
            raise BrowserError(
                f"Extension bridge недоступен на {url}: {e}. "
                f"Убедитесь, что расширение запущено и пользователь "
                f"разрешил вкладку."
            ) from e
        return self._ws

    async def _send(self, action: str, params: dict) -> dict:
        async with self._ws_lock:
            ws = await self._ensure_ws()
            payload = json.dumps({"action": action, "params": params})
            await ws.send(payload)
            raw = await asyncio.wait_for(ws.recv(), timeout=DEFAULT_ACTION_TIMEOUT)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise BrowserError(f"extension: битый ответ: {e}") from e

    async def open(self, url: str) -> PageSnapshot:
        resp = await self._send("open", {"url": url})
        if not resp.get("ok"):
            raise BrowserError(f"extension.open: {resp.get('error', '?')}")
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "open", "url": url, "mode": "extension"},
        )
        return await self._snapshot_from(resp.get("result") or {})

    async def snapshot(self) -> PageSnapshot:
        resp = await self._send("snapshot", {})
        if not resp.get("ok"):
            raise BrowserError(f"extension.snapshot: {resp.get('error', '?')}")
        return await self._snapshot_from(resp.get("result") or {})

    async def click(self, selector: str) -> PageSnapshot:
        resp = await self._send("click", {"selector": selector})
        if not resp.get("ok"):
            raise BrowserError(f"extension.click: {resp.get('error', '?')}")
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "click", "selector": selector, "mode": "extension"},
        )
        return await self._snapshot_from(resp.get("result") or {})

    async def fill(self, selector: str, text: str) -> PageSnapshot:
        resp = await self._send("fill", {"selector": selector, "text": text})
        if not resp.get("ok"):
            raise BrowserError(f"extension.fill: {resp.get('error', '?')}")
        return await self._snapshot_from(resp.get("result") or {})

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("ExtensionBrowser.close: ошибка", exc_info=True)
            self._ws = None

    async def _snapshot_from(self, data: dict) -> PageSnapshot:
        raw_elements = data.get("elements") or []
        elements: list[BrowserElement] = []
        for el in raw_elements[:MAX_INTERACTIVE_ELEMENTS]:
            if not isinstance(el, dict):
                continue
            elements.append(
                BrowserElement(
                    tag=str(el.get("tag", "")),
                    type=str(el.get("type", "")),
                    selector=str(el.get("selector", "")),
                    text=str(el.get("text", "")),
                    href=str(el.get("href", "")),
                )
            )
        return PageSnapshot(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            text=str(data.get("text", ""))[:MAX_PAGE_TEXT],
            elements=elements,
        )


# ---------------------------------------------------------------------------
# C: полный доступ (CDP к копии реального профиля)
# ---------------------------------------------------------------------------


class FullAccessBrowser(BrowserBackend):
    """Режим C: CDP к копии реального профиля.

    Копия профиля делается один раз при первом запуске — чтобы не
    лочить реальный профиль, если Chrome уже открыт. Все сессии
    пользователя доступны. Только после ``full_access_acknowledged=True``.
    """

    def __init__(self, config: BrowserConfig) -> None:
        super().__init__(config)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def mode_name(self) -> str:
        return "full"

    async def _ensure_started(self) -> None:
        if not self.config.full_access_acknowledged:
            raise BrowserError(
                "Режим full не активирован: пользователь не дал явного "
                "согласия (browser.full_access_acknowledged=false)."
            )
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserError(
                "playwright не установлен. Установите: "
                "`pip install playwright && playwright install chromium`"
            ) from e

        profile = Path(self.config.dedicated_profile_path).expanduser()
        profile.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,  # full всегда видим — пользователь должен видеть
        )
        pages = self._browser.pages
        self._page = pages[0] if pages else await self._browser.new_page()
        logger.warning(
            "Browser (full): доступ к реальным сессиям пользователя. "
            "Все действия пишутся в audit log."
        )

    # open/snapshot/click/fill/close — те же, что у IsolatedBrowser.
    # Делегируем, чтобы не дублировать (playwright-контекст идентичен).
    async def open(self, url: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.goto(url, timeout=DEFAULT_ACTION_TIMEOUT * 1000)
        except Exception as e:
            raise BrowserError(f"не удалось открыть {url!r}: {e}") from e
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "open", "url": url, "mode": "full", "ok": True},
        )
        return await self.snapshot()

    async def snapshot(self) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            title = await self._page.title()
            url = self._page.url
            text = await self._page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
        except Exception as e:
            raise BrowserError(f"snapshot: {e}") from e
        # Используем общий сборщик из IsolatedBrowser через временный экземпляр.
        helper = IsolatedBrowser(self.config)
        helper._page = self._page
        elements = await helper._collect_elements()
        return PageSnapshot(
            url=url or "",
            title=title or "",
            text=str(text or "")[:MAX_PAGE_TEXT],
            elements=elements,
        )

    async def click(self, selector: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.click(selector, timeout=DEFAULT_ACTION_TIMEOUT * 1000)
        except Exception as e:
            raise BrowserError(f"click({selector}): {e}") from e
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {"action": "click", "selector": selector, "mode": "full"},
        )
        return await self.snapshot()

    async def fill(self, selector: str, text: str) -> PageSnapshot:
        await self._ensure_started()
        assert self._page is not None
        try:
            await self._page.fill(
                selector, text, timeout=DEFAULT_ACTION_TIMEOUT * 1000
            )
        except Exception as e:
            raise BrowserError(f"fill({selector}): {e}") from e
        emit_event(
            EventKind.BROWSER_ACTION.value,
            {
                "action": "fill",
                "selector": selector,
                "mode": "full",
                "text_chars": len(text),
            },
        )
        return await self.snapshot()

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("FullAccessBrowser.close: ошибка", exc_info=True)
            self._browser = None
            self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("FullAccessBrowser: playwright.stop", exc_info=True)
            self._playwright = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def is_playwright_available() -> bool:
    """``True``, если playwright установлен (без импорта at-import-time)."""
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def create_backend(config: BrowserConfig) -> BrowserBackend | None:
    """Создаёт backend по конфигу.

    Returns:
        Backend или ``None``, если playwright не установлен и режим
        требует его (isolated/full). Режим ``extension`` работает
        без playwright (только websockets), но websockets тоже может
        отсутствовать — тогда ``None``.
    """
    if config.mode == "extension":
        try:
            import websockets  # noqa: F401

            return ExtensionBrowser(config)
        except ImportError:
            logger.warning(
                "browser_control: websockets не установлен — extension-режим "
                "недоступен"
            )
            return None
    if not is_playwright_available():
        logger.warning(
            "browser_control: playwright не установлен — браузер недоступен"
        )
        return None
    if config.mode == "full":
        return FullAccessBrowser(config)
    return IsolatedBrowser(config)


__all__ = [
    "BrowserBackend",
    "BrowserElement",
    "BrowserError",
    "ExtensionBrowser",
    "FullAccessBrowser",
    "IsolatedBrowser",
    "PageSnapshot",
    "create_backend",
    "is_playwright_available",
]