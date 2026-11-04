"""2.11: BrowserBackend — три режима."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.browser_client import (
    BrowserElement,
    BrowserError,
    ExtensionBrowser,
    FullAccessBrowser,
    IsolatedBrowser,
    PageSnapshot,
    create_backend,
)
from core.config import BrowserConfig


class TestPageSnapshot:
    def test_render_for_prompt(self) -> None:
        snap = PageSnapshot(
            url="https://example.com",
            title="Example",
            text="Hello world",
            elements=[
                BrowserElement(
                    tag="button",
                    selector="#submit",
                    text="Submit",
                    type="button",
                ),
                BrowserElement(
                    tag="a",
                    selector="a.nav",
                    text="Home",
                    type="link",
                    href="/home",
                ),
            ],
        )
        out = snap.render_for_prompt()
        assert "https://example.com" in out
        assert "Example" in out
        assert "Submit" in out
        assert "#submit" in out
        assert "Home" in out


class TestCreateBackend:
    def test_isolated_requires_playwright(self) -> None:
        cfg = BrowserConfig(mode="isolated")
        with patch(
            "agents.browser_client.is_playwright_available", return_value=False
        ):
            backend = create_backend(cfg)
            assert backend is None

    def test_isolated_returns_isolated(self) -> None:
        cfg = BrowserConfig(mode="isolated")
        with patch(
            "agents.browser_client.is_playwright_available", return_value=True
        ):
            backend = create_backend(cfg)
            assert isinstance(backend, IsolatedBrowser)

    def test_full_returns_full(self) -> None:
        cfg = BrowserConfig(mode="full", full_access_acknowledged=True)
        with patch(
            "agents.browser_client.is_playwright_available", return_value=True
        ):
            backend = create_backend(cfg)
            assert isinstance(backend, FullAccessBrowser)

    def test_extension_requires_websockets(self) -> None:
        cfg = BrowserConfig(mode="extension")
        with patch.dict("sys.modules", {"websockets": None}):
            backend = create_backend(cfg)
            assert backend is None


class TestFullAccessGuard:
    @pytest.mark.asyncio
    async def test_full_without_ack_raises(self) -> None:
        cfg = BrowserConfig(mode="full", full_access_acknowledged=False)
        backend = FullAccessBrowser(cfg)
        with pytest.raises(BrowserError, match="не активирован"):
            await backend.open("https://example.com")


class TestExtensionErrors:
    @pytest.mark.asyncio
    async def test_extension_bridge_unreachable(self) -> None:
        cfg = BrowserConfig(mode="extension")
        backend = ExtensionBrowser(cfg)
        with patch(
            "agents.browser_client.ExtensionBrowser._ensure_ws",
            side_effect=BrowserError("bridge недоступен"),
        ), pytest.raises(BrowserError, match="bridge"):
            await backend.open("https://example.com")