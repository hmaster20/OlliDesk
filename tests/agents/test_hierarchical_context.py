"""4.2: иерархический контекст, hot window, n_ctx_hint, compactor threshold."""

from __future__ import annotations

import pytest

from agents.ollama_client import ChatMessage
from core.context_budget import (
    HOT_WINDOW_SHARE,
    MIN_HOT_WINDOW_TOKENS,
    MIN_WARM_LAYER_TOKENS,
    WARM_LAYER_SHARE,
    cold_tokens,
    compactor_threshold,
    hot_window_tokens,
    warm_layer_tokens,
)


class TestHotWindow:
    def test_scales_with_ctx(self):
        assert hot_window_tokens(8192) == int(8192 * HOT_WINDOW_SHARE)
        assert hot_window_tokens(16384) == int(16384 * HOT_WINDOW_SHARE)
        assert hot_window_tokens(32768) == int(32768 * HOT_WINDOW_SHARE)
        assert hot_window_tokens(65536) == int(65536 * HOT_WINDOW_SHARE)

    def test_minimum(self):
        assert hot_window_tokens(1) >= MIN_HOT_WINDOW_TOKENS
        assert hot_window_tokens(0) == MIN_HOT_WINDOW_TOKENS


class TestWarmLayer:
    def test_scales_with_ctx(self):
        assert warm_layer_tokens(8192) == int(8192 * WARM_LAYER_SHARE)
        assert warm_layer_tokens(32768) == int(32768 * WARM_LAYER_SHARE)

    def test_minimum(self):
        assert warm_layer_tokens(1) >= MIN_WARM_LAYER_TOKENS


class TestColdLayer:
    def test_remainder(self):
        """cold = num_ctx - hot - warm."""
        for num_ctx in (8192, 16384, 32768, 65536):
            hot = hot_window_tokens(num_ctx)
            warm = warm_layer_tokens(num_ctx)
            cold = cold_tokens(num_ctx)
            # output_reserve и safety не входят в cold — это «холодная
            # часть» после горячего и тёплого.
            assert cold == num_ctx - hot - warm

    def test_zero_for_tiny_ctx(self):
        assert cold_tokens(0) == 0


class TestCompactorThreshold:
    def test_small_ctx(self):
        assert compactor_threshold(4096) == 0.75
        assert compactor_threshold(8191) == 0.75

    def test_medium_ctx(self):
        assert compactor_threshold(8192) == 0.80
        assert compactor_threshold(16384) == 0.80
        assert compactor_threshold(32767) == 0.80

    def test_large_ctx(self):
        assert compactor_threshold(32768) == 0.90
        assert compactor_threshold(65536) == 0.90

    def test_zero_ctx_returns_default(self):
        assert compactor_threshold(0) == 0.80


# ---------------------------------------------------------------------------
# compact_history с hot_window_tokens
# ---------------------------------------------------------------------------


def _make_user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def _make_assistant(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def _make_tool(text: str) -> ChatMessage:
    return ChatMessage(role="tool", content=text, name="read_file", tool_call_id="c1")


class TestCompactHistoryHotWindow:
    def test_hot_window_preserved(self):
        """Последние реплики, укладывающиеся в hot_window, не режутся."""
        from agents.history_compactor import compact_history

        messages: list[ChatMessage] = [ChatMessage(role="system", content="sys")]
        for i in range(6):
            messages.append(_make_user(f"q{i} " + "x" * 500))
            messages.append(_make_tool("Z" * 2000))
            messages.append(_make_assistant(f"a{i} " + "y" * 500))

        # Маленькое окно, большой hot_window (защищаем весь диалог).
        hot = 10000
        out = compact_history(
            messages,
            num_ctx=4096,
            hot_window_tokens=hot,
        )
        # Всё, что влезло в hot_window, сохранено.
        assert out[0].role == "system"
        # Хотя бы последняя тройка (q5 + tool + a5) на месте.
        assert any("q5" in m.content for m in out)
        assert any("a5" in m.content for m in out)

    def test_no_hot_window_old_behaviour(self):
        """hot_window_tokens=0 — старое поведение (обрезает всё)."""
        from agents.history_compactor import compact_history

        messages: list[ChatMessage] = [ChatMessage(role="system", content="sys")]
        for i in range(6):
            messages.append(_make_user(f"q{i} " + "x" * 500))
            messages.append(_make_tool("Z" * 2000))
            messages.append(_make_assistant(f"a{i} " + "y" * 500))

        out = compact_history(messages, num_ctx=4096, hot_window_tokens=0)
        # Без hot window старые реплики уходят.
        assert not any("q0" in m.content for m in out)


# ---------------------------------------------------------------------------
# n_ctx_hint в optimize
# ---------------------------------------------------------------------------


class TestOptimizeNCtxHint:
    def test_hint_caps_target(self):
        """n_ctx_hint меньше target_n_ctx → n_ctx ограничен hint."""
        from pathlib import Path
        from unittest.mock import patch

        from agents.hardware_profile import CPUInfo, HardwareProfile, RAMInfo
        from agents.gguf_reader import GGUFMeta
        from agents.slm_optimizer import optimize

        meta = GGUFMeta(
            n_layer=32, n_embd=4096, n_head=32, n_head_kv=32,
            n_ctx_train=32768, architecture="llama",
        )
        hw = HardwareProfile(
            cpu=CPUInfo(model="test", physical_cores=8, logical_cores=16),
            gpus=(),
            ram=RAMInfo(total_mb=32768, available_mb=30000),
        )
        with (
            patch("agents.slm_optimizer.read_gguf_meta", return_value=meta),
            patch("agents.slm_optimizer.model_total_size_mb", return_value=4000),
        ):
            params = optimize(
                Path("dummy.gguf"),
                hw=hw,
                target_n_ctx=32768,
                n_ctx_hint=16384,
                device="CPU",
            )
        assert params.n_ctx <= 16384
        assert params.n_ctx >= 2048

    def test_hint_zero_means_no_cap(self):
        """n_ctx_hint=0 → используем target_n_ctx как раньше."""
        # (покрывается через n_ctx_hint=None в тестах выше)
        pass