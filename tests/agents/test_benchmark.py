"""Тесты benchmark-модуля (4.7.B).

Без реальных моделей: проверяем только экспорт (CSV/Markdown), dataclass,
BENCHMARK_PROMPTS. Сам прогон требует llama.cpp/Ollama — на живой сборке.
"""

from __future__ import annotations

import csv
from pathlib import Path

from agents.benchmark import (
    BENCHMARK_PROMPTS,
    BenchmarkResult,
    BenchmarkRow,
    LoadTimeRow,
    save_csv,
    to_markdown,
)


def _sample_result() -> BenchmarkResult:
    return BenchmarkResult(
        started_at="2025-01-01T10:00:00",
        finished_at="2025-01-01T10:05:00",
        rows=[
            BenchmarkRow(
                model_id="qwen2.5-3b",
                model_name="Qwen 2.5 3B",
                provider="slm",
                device="GPU",
                n_ctx=8192,
                n_threads=8,
                n_gpu_layers=-1,
                prompt_name="short",
                prompt_chars=40,
                max_tokens=64,
                repeat=1,
                ttft_ms=120.5,
                tokens=42,
                gen_ms=800.0,
                tokens_per_sec=52.5,
                total_ms=920.5,
                ok=True,
            ),
            BenchmarkRow(
                model_id="qwen2.5-7b",
                model_name="Qwen 2.5 7B",
                provider="slm",
                device="CPU",
                n_ctx=8192,
                n_threads=8,
                n_gpu_layers=0,
                prompt_name="medium",
                prompt_chars=80,
                max_tokens=256,
                repeat=1,
                ttft_ms=2500.0,
                tokens=200,
                gen_ms=30000.0,
                tokens_per_sec=6.7,
                total_ms=32500.0,
                ok=True,
            ),
        ],
        load_rows=[
            LoadTimeRow(
                model_id="qwen2.5-3b",
                model_name="Qwen 2.5 3B",
                n_ctx=8192,
                n_threads=8,
                n_gpu_layers=-1,
                load_ms=1500.0,
                attempt=1,
            ),
            LoadTimeRow(
                model_id="qwen2.5-3b",
                model_name="Qwen 2.5 3B",
                n_ctx=8192,
                n_threads=8,
                n_gpu_layers=-1,
                load_ms=350.0,
                attempt=2,
            ),
        ],
    )


class TestBenchmarkPrompts:
    def test_three_prompts(self) -> None:
        assert len(BENCHMARK_PROMPTS) == 3
        names = [p.name for p in BENCHMARK_PROMPTS]
        assert names == ["short", "medium", "long"]

    def test_increasing_max_tokens(self) -> None:
        tokens = [p.max_tokens for p in BENCHMARK_PROMPTS]
        assert tokens == sorted(tokens)
        assert tokens[0] < tokens[-1]

    def test_all_have_text(self) -> None:
        for p in BENCHMARK_PROMPTS:
            assert p.text.strip()
            assert len(p.text) > 10


class TestSaveCsv:
    def test_writes_generation_csv(self, tmp_path: Path) -> None:
        result = _sample_result()
        path = tmp_path / "bench.csv"
        save_csv(result, path)
        assert path.exists()
        with path.open(encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "model_id" in header
            assert "ttft_ms" in header
            assert "tokens_per_sec" in header
            rows = list(reader)
            assert len(rows) == 2
            assert rows[0][header.index("ttft_ms")] == "120.5"
            assert rows[0][header.index("model_id")] == "qwen2.5-3b"

    def test_writes_load_csv(self, tmp_path: Path) -> None:
        result = _sample_result()
        path = tmp_path / "bench.csv"
        save_csv(result, path)
        load_path = tmp_path / "bench_load.csv"
        assert load_path.exists()
        with load_path.open(encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "load_ms" in header
            assert "attempt" in header
            rows = list(reader)
            assert len(rows) == 2
            # Вторая попытка быстрее (page cache).
            first_load = float(rows[0][header.index("load_ms")])
            second_load = float(rows[1][header.index("load_ms")])
            assert second_load < first_load

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        result = _sample_result()
        path = tmp_path / "subdir" / "nested" / "bench.csv"
        save_csv(result, path)
        assert path.exists()


class TestToMarkdown:
    def test_header(self) -> None:
        md = to_markdown(_sample_result())
        assert "# Замеры производительности" in md
        assert "2025-01-01" in md

    def test_generation_table(self) -> None:
        md = to_markdown(_sample_result())
        assert "## Генерация" in md
        assert "| Модель |" in md
        assert "Qwen 2.5 3B" in md
        assert "52.5" in md  # tokens_per_sec
        assert "120" in md  # ttft (округлён до целого)

    def test_load_table(self) -> None:
        md = to_markdown(_sample_result())
        assert "## Загрузка модели" in md
        assert "1500" in md  # первая загрузка
        assert "350" in md   # вторая загрузка

    def test_empty_result(self) -> None:
        md = to_markdown(BenchmarkResult())
        assert "# Замеры производительности" in md
        assert "## Генерация" not in md


class TestBenchmarkResultOkRows:
    def test_ok_rows_filter(self) -> None:
        result = _sample_result()
        result.rows.append(
            BenchmarkRow(
                model_id="err",
                model_name="err",
                provider="slm",
                device="?",
                n_ctx=0,
                n_threads=0,
                n_gpu_layers=0,
                prompt_name="err",
                prompt_chars=0,
                max_tokens=0,
                repeat=1,
                ok=False,
                error="boom",
            )
        )
        assert len(result.ok_rows) == 2
        assert len(result.rows) == 3