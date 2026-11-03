"""2.13: провайдеры поиска + цепочка."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agents.search_providers import (
    DDGProvider,
    OllamaWebSearchProvider,
    OpenSERPProvider,
    SearXNGProvider,
    SearchProviderError,
    SearchResult,
    YandexSearchAPIProvider,
    build_chain,
)
from core.config import SearchConfig


class TestBuildChain:
    def test_empty_providers(self) -> None:
        chain = build_chain(SearchConfig(providers=[]))
        assert chain == []

    def test_ddg_only_available(self) -> None:
        """Если настроек нет — выживает только DDG."""
        cfg = SearchConfig(
            providers=["searxng", "ddg"],
            searxng_url="",  # явно пусто
        )
        with patch(
            "agents.search_providers.DDGProvider.is_available",
            return_value=True,
        ):
            chain = build_chain(cfg)
        # searxng с пустым URL не доступен — пропущен.
        assert [p.name for p in chain] == ["ddg"]

    def test_yandex_needs_key_and_folder(self) -> None:
        cfg = SearchConfig(
            providers=["yandex"],
            yandex_api_key="",
            yandex_folder_id="",
        )
        assert build_chain(cfg) == []

    def test_yandex_with_both(self) -> None:
        cfg = SearchConfig(
            providers=["yandex"],
            yandex_api_key="key",
            yandex_folder_id="folder",
        )
        chain = build_chain(cfg)
        assert len(chain) == 1
        assert chain[0].name == "yandex"

    def test_ollama_needs_key(self) -> None:
        cfg = SearchConfig(providers=["ollama"], ollama_api_key="")
        assert build_chain(cfg) == []
        cfg2 = SearchConfig(providers=["ollama"], ollama_api_key="k")
        chain = build_chain(cfg2)
        assert chain and chain[0].name == "ollama"


class TestSearXNG:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        provider = SearXNGProvider("http://localhost:8080")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"title": "A", "url": "https://a", "content": "body a"},
                {"title": "B", "url": "https://b", "content": "body b"},
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "agents.search_providers.httpx.AsyncClient",
            return_value=mock_client,
        ):
            results = await provider.search("test", num=2)
        assert len(results) == 2
        assert results[0].title == "A"

    @pytest.mark.asyncio
    async def test_http_error(self) -> None:
        provider = SearXNGProvider("http://localhost:8080")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "agents.search_providers.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(SearchProviderError, match="HTTP 500"):
                await provider.search("test")

    @pytest.mark.asyncio
    async def test_network_error(self) -> None:
        provider = SearXNGProvider("http://localhost:8080")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("refused")
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "agents.search_providers.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(SearchProviderError, match="network"):
                await provider.search("test")


class TestYandex:
    def test_availability(self) -> None:
        assert not YandexSearchAPIProvider("", "").is_available()
        assert not YandexSearchAPIProvider("key", "").is_available()
        assert YandexSearchAPIProvider("key", "folder").is_available()

    @pytest.mark.asyncio
    async def test_403_raises(self) -> None:
        provider = YandexSearchAPIProvider("key", "folder")
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "agents.search_providers.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(SearchProviderError, match="403"):
                await provider.search("test")


class TestOllama:
    def test_availability(self) -> None:
        assert not OllamaWebSearchProvider("").is_available()
        assert OllamaWebSearchProvider("k").is_available()

    @pytest.mark.asyncio
    async def test_401_raises(self) -> None:
        provider = OllamaWebSearchProvider("bad-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "agents.search_providers.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(SearchProviderError, match="401"):
                await provider.search("test")


class TestDDG:
    def test_availability_when_installed(self) -> None:
        assert DDGProvider().is_available() is True

    def test_availability_without_package(self) -> None:
        with patch.dict("sys.modules", {"ddgs": None}):
            # `import ddgs` внутри is_available бросит ImportError.
            assert DDGProvider().is_available() is False