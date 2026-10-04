"""Тесты расчёта бюджета контекста."""

from core.context_budget import (
    ATTACHED_CONTEXT_SHARE,
    CHARS_PER_TOKEN,
    DEFAULT_NUM_CTX,
    MIN_ATTACHED_TOKENS,
    attached_char_budget,
    attached_token_budget,
    estimate_tokens,
    resolve_num_ctx,
    tokens_to_chars,
)


class TestEstimateTokens:
    """Оценка количества токенов."""

    def test_empty_text(self):
        assert estimate_tokens("") == 0

    def test_scales_with_length(self):
        short = estimate_tokens("a" * 300)
        long = estimate_tokens("a" * 3000)
        assert short < long

    def test_never_zero_for_non_empty(self):
        assert estimate_tokens("a") >= 1

    def test_uses_chars_per_token(self):
        text = "a" * 300
        assert estimate_tokens(text) == int(len(text) / CHARS_PER_TOKEN) + 1


class TestTokensToChars:
    """Перевод бюджета токенов в символы."""

    def test_conversion(self):
        assert tokens_to_chars(100) == int(100 * CHARS_PER_TOKEN)

    def test_negative_becomes_zero(self):
        assert tokens_to_chars(-5) == 0


class TestResolveNumCtx:
    """Определение окна контекста модели."""

    def test_exact_match(self):
        assert resolve_num_ctx("qwen2.5-7b-instruct") == 4096

    def test_prefix_match_ignores_tag(self):
        assert resolve_num_ctx("qwen2.5-7b-instruct-q4_k_m.gguf") == 4096

    def test_ollama_model_with_tag(self):
        assert resolve_num_ctx("llama3.1:8b") == 4096

    def test_unknown_model_uses_default(self):
        assert resolve_num_ctx("some-unknown-model") == DEFAULT_NUM_CTX

    def test_unknown_model_uses_slm_context(self):
        assert resolve_num_ctx("some-unknown-model", slm_num_ctx=8192) == 8192

    def test_known_model_ignores_slm_context(self):
        assert resolve_num_ctx("llama3.2", slm_num_ctx=32768) == 4096


class TestAttachedBudget:
    """Бюджет под прикреплённые файлы."""

    def test_share_of_context(self):
        assert attached_token_budget(4096) == int(4096 * ATTACHED_CONTEXT_SHARE)

    def test_grows_with_context(self):
        assert attached_char_budget(32768) > attached_char_budget(4096)

    def test_minimum_applied_for_tiny_context(self):
        assert attached_token_budget(16) == MIN_ATTACHED_TOKENS

    def test_budget_fits_known_models(self):
        # При n_ctx=4096 системный промпт тоже должен поместиться:
        # бюджет не может превышать половину окна.
        for num_ctx in (4096, 8192, 32768):
            assert attached_token_budget(num_ctx) <= num_ctx // 2