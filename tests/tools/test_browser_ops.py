"""2.11: browser_* инструменты."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.browser_client import PageSnapshot
from tools.browser_ops import (
    browser_click,
    browser_close,
    browser_fill,
    browser_open,
    browser_snapshot,
    shutdown_browser,
)


@pytest.fixture
def fake_backend():
    backend = AsyncMock()
    backend.open = AsyncMock(
        return_value=PageSnapshot(url="u", title="t", text="x")
    )
    backend.snapshot = AsyncMock(
        return_value=PageSnapshot(url="u", title="t", text="x")
    )
    backend.click = AsyncMock(
        return_value=PageSnapshot(url="u", title="t", text="x")
    )
    backend.fill = AsyncMock(
        return_value=PageSnapshot(url="u", title="t", text="x")
    )
    backend.close = AsyncMock()
    yield backend
    import asyncio

    asyncio.run(shutdown_browser())


class TestBrowserOps:
    @pytest.mark.asyncio
    async def test_open_without_backend(self) -> None:
        with patch("tools.browser_ops._BACKEND", None), patch(
            "tools.browser_ops.create_backend", return_value=None
        ):
            out = await browser_open("https://example.com")
            assert "недоступен" in out.lower()

    @pytest.mark.asyncio
    async def test_open_calls_backend(self, fake_backend) -> None:
        with patch("tools.browser_ops._BACKEND", fake_backend):
            out = await browser_open("https://example.com")
            assert "u" in out and "t" in out
            fake_backend.open.assert_awaited_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_empty_selector_rejected(self, fake_backend) -> None:
        with patch("tools.browser_ops._BACKEND", fake_backend):
            assert "пустой selector" in await browser_click("")
            assert "пустой selector" in await browser_fill("", "x")

    @pytest.mark.asyncio
    async def test_close_calls_backend(self, fake_backend) -> None:
        with patch("tools.browser_ops._BACKEND", fake_backend):
            await browser_close()
            fake_backend.close.assert_awaited_once()