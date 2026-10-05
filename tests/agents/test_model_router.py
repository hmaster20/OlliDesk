from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.model_router import BackendType, ModelRouter
from core.exceptions import EmbedderError


def make_slm(embed_result=None, embed_error=None):
    """Заглушка LocalLLMClient."""
    client = MagicMock()
    client.num_ctx = 4096
    if embed_error is not None:
        client.embed = AsyncMock(side_effect=embed_error)
    else:
        client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]] if embed_result is None else embed_result)
    return client


class TestBackendPriority:
    def test_default_priority_is_ollama_first(self):
        router = ModelRouter()
        router.set_ollama(MagicMock())
        router.set_local_slm(make_slm())
        router.set_rule_based(MagicMock())

        router.set_slm_available(True)
        slm_only = router.active_backend

        router.set_ollama_available(True)
        both = router.active_backend

        assert slm_only == BackendType.LOCAL_SLM
        assert both == BackendType.OLLAMA

    def test_slm_first_when_configured(self):
        router = ModelRouter()
        router.set_ollama(MagicMock())
        router.set_local_slm(make_slm())
        router.set_rule_based(MagicMock())
        router.set_priority(["local_slm", "ollama"])

        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert router.active_backend == BackendType.LOCAL_SLM

    def test_unknown_names_ignored(self):
        router = ModelRouter()
        router.set_ollama(MagicMock())
        router.set_priority(["nonexistent", "ollama"])

        router.set_ollama_available(True)
        assert router.active_backend == BackendType.OLLAMA

    def test_empty_priority_falls_back_to_default(self):
        router = ModelRouter()
        router.set_ollama(MagicMock())
        router.set_local_slm(make_slm())
        router.set_priority([])

        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert router.active_backend == BackendType.OLLAMA

    def test_falls_back_to_rule_based(self):
        router = ModelRouter()
        router.set_rule_based(MagicMock())

        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert router.active_backend == BackendType.RULE_BASED


class TestEmbedRouting:
    @pytest.mark.asyncio
    async def test_uses_active_backend(self):
        router = ModelRouter()
        router.set_ollama(MagicMock(embed=AsyncMock(return_value=[[1.0, 0.0]])))
        router.set_local_slm(make_slm())
        router.set_ollama_available(True)

        assert await router.embed("m", ["a"]) == [[1.0, 0.0]]

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_slm_when_ollama_empty(self):
        """Ядро правки: RAG выживает без Ollama."""
        ollama = MagicMock(embed=AsyncMock(return_value=[]))
        slm = make_slm(embed_result=[[0.5, 0.5]])

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert router.active_backend == BackendType.OLLAMA
        assert await router.embed("m", ["a"]) == [[0.5, 0.5]]

    @pytest.mark.asyncio
    async def test_auto_falls_back_on_exception(self):
        ollama = MagicMock(embed=AsyncMock(side_effect=ConnectionError("Ollama упала")))
        slm = make_slm(embed_result=[[0.5, 0.5]])

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert await router.embed("m", ["a"]) == [[0.5, 0.5]]

    @pytest.mark.asyncio
    async def test_embed_backend_pinned_to_slm(self):
        ollama = MagicMock(embed=AsyncMock(return_value=[[1.0, 0.0]]))
        slm = make_slm(embed_result=[[0.5, 0.5]])

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_embed_backend("local_slm")
        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert await router.embed("m", ["a"]) == [[0.5, 0.5]]
        ollama.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_embed_backend_pinned_to_ollama(self):
        ollama = MagicMock(embed=AsyncMock(return_value=[[1.0, 0.0]]))
        slm = make_slm(embed_result=[[0.5, 0.5]])

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_embed_backend("ollama")
        router.set_ollama_available(True)
        router.set_slm_available(True)

        assert await router.embed("m", ["a"]) == [[1.0, 0.0]]
        slm.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_all_backends_fail(self):
        ollama = MagicMock(embed=AsyncMock(return_value=[]))
        slm = make_slm(embed_result=[])

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_ollama_available(True)
        router.set_slm_available(True)

        with pytest.raises(EmbedderError, match="Не удалось посчитать эмбеддинги"):
            await router.embed("m", ["a"])

    @pytest.mark.asyncio
    async def test_raises_without_any_backend(self):
        router = ModelRouter()
        with pytest.raises(EmbedderError, match="нет ни одного доступного"):
            await router.embed("m", ["a"])

    @pytest.mark.asyncio
    async def test_error_message_lists_all_problems(self):
        ollama = MagicMock(embed=AsyncMock(side_effect=ConnectionError("таймаут")))
        slm = make_slm(embed_error=RuntimeError("модель не загружена"))

        router = ModelRouter()
        router.set_ollama(ollama)
        router.set_local_slm(slm)
        router.set_ollama_available(True)
        router.set_slm_available(True)

        with pytest.raises(EmbedderError) as exc:
            await router.embed("m", ["a"])

        message = str(exc.value)
        assert "таймаут" in message
        assert "модель не загружена" in message
