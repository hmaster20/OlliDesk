"""2.13: tools/web_search — цепочка с fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.search_providers import SearchProviderError, SearchResult
from core.config import SearchConfig
from core.search_cache import SearchCache
from core.security import NetworkMode, SecurityPolicy
from tools.web_search import web_search


def _mock_provider(name: str, results: list[SearchResult] | None = None, error: str = ""):
    p = MagicMock()
    p.name = name
    if error:
        p.search = AsyncMock(side_effect=SearchProviderError(error))
    else:
        p.search = AsyncMock(return_value=results or [])
    p.is_available.return_value = True
    return p


def _cfg_with(providers: list[str], **kwargs) -> SearchConfig:
    return SearchConfig(providers=providers, **kwargs)


@pytest.fixture(autouse=True)
def _default_config():
    with patch(
        "tools.web_search._get_search_config",
        return_value=_cfg_with(["searxng", "ddg"]),
    ):
        yield


class TestChainFallback:
    @pytest.mark.asyncio
    async def test_first_provider_wins(self) -> None:
        p1 = _mock_provider("searxng", [SearchResult("A", "https://a")])
        p2 = _mock_provider("ddg", [SearchResult("B", "https://b")])
        with patch("tools.web_search.build_chain", return_value=[p1, p2]):
            out = await web_search("test", security=SecurityPolicy())
        assert "Провайдер: searxng" in out
        assert "https://a" in out
        p2.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_on_error(self) -> None:
        p1 = _mock_provider("searxng", error="HTTP 500")
        p2 = _mock_provider("ddg", [SearchResult("B", "https://b")])
        with patch("tools.web_search.build_chain", return_value=[p1, p2]):
            out = await web_search("test", security=SecurityPolicy())
        assert "Провайдер: ddg" in out
        assert "https://b" in out

    @pytest.mark.asyncio
    async def test_fallback_on_zero_results(self) -> None:
        p1 = _mock_provider("searxng", results=[])  # 0 результатов
        p2 = _mock_provider("ddg", [SearchResult("B", "https://b")])
        with patch("tools.web_search.build_chain", return_value=[p1, p2]):
            out = await web_search("test", security=SecurityPolicy())
        assert "Провайдер: ddg" in out

    @pytest.mark.asyncio
    async def test_all_fail(self) -> None:
        p1 = _mock_provider("searxng", error="err1")
        p2 = _mock_provider("ddg", error="err2")
        with patch("tools.web_search.build_chain", return_value=[p1, p2]):
            out = await web_search("test", security=SecurityPolicy())
        assert "Не удалось найти" in out
        assert "searxng" in out
        assert "ddg" in out

    @pytest.mark.asyncio
    async def test_no_providers_configured(self) -> None:
        with patch("tools.web_search.build_chain", return_value=[]):
            out = await web_search("test", security=SecurityPolicy())
        assert "Не настроен ни один" in out


class TestHygieneAndSecurity:
    @pytest.mark.asyncio
    async def test_confidential_blocks(self) -> None:
        sec = SecurityPolicy(confidential=True)
        out = await web_search("test", security=sec)
        assert "отключён политикой" in out

    @pytest.mark.asyncio
    async def test_hygiene_redacts_secret(self) -> None:
        p1 = _mock_provider("searxng", [SearchResult("A", "https://a")])
        with patch("tools.web_search.build_chain", return_value=[p1]):
            out = await web_search(
                "TOKEN=abcdef123456 hello", security=SecurityPolicy()
            )
        # Секрет не должен утечь в запрос.
        args, _ = p1.search.call_args
        assert "abcdef123456" not in args[0]


class TestCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self, tmp_path) -> None:
        cache = SearchCache(tmp_path)
        cache.set(
            "hello",
            "<untrusted>\nПровайдер: cached\n</untrusted>",
        )
        p1 = _mock_provider("searxng")
        with patch("tools.web_search.build_chain", return_value=[p1]):
            out = await web_search(
                "hello",
                search_cache=cache,
                security=SecurityPolicy(),
            )
        assert "cached" in out
        p1.search.assert_not_called()