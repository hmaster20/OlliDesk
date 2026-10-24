"""Замеры производительности SLM/Ollama (4.7.B).

Зачем: перед любыми решениями по моделям (SLM-first vs Ollama, planner
3B + analyst 7B, квантование, n_threads) нужны реальные цифры на целевом
железе. Оценки «50 tok/s на 3B» и «5–10 tok/s на 7B CPU» — из аудита,
но у конкретной машины (T1200 4 ГБ / i7-11800H 8C/16T / 127 ГБ RAM)
числа могут отличаться в 2 раза.

Что мерим:
  * `ttft_ms` — время до первого токена (важно для UX: пользователь
    видит «печатает» через сколько секунд);
  * `tokens_per_sec` — скорость генерации после первого токена;
  * `total_ms` — полное время ответа;
  * `load_ms` — время загрузки модели (с mmap / после `gc.collect()` и
    повторной загрузки — оценить реальную стоимость переключения);
  * `n_threads`, `n_ctx`, `n_gpu_layers` — фиксируются в строке, чтобы
    было видно, при каких параметрах получен замер.

Модели прогоняются ПОСЛЕДОВАТЕЛЬНО: загрузка → промпты × repeats →
выгрузка (`del` + `gc.collect()`) → следующая. Не держим 3 модели в
памяти одновременно (иначе 7B CPU + 3B GPU могут не влезть).

Экспорт — CSV (для Excel) и Markdown (для копирования в `docs/BENCHMARK.md`).
"""

from __future__ import annotations

import asyncio
import csv
import gc
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

# ============================================================================
# Промпты и структуры данных
# ============================================================================


@dataclass(frozen=True)
class BenchmarkPrompt:
    """Один тестовый промпт.

    Разные длины ответов позволяют увидеть, как TTFT масштабируется с
    размером ответа (важно для UI: короткий вопрос → быстрый первый токен,
    длинный список → TTFT такой же, но общая длительность больше).
    """

    name: str
    text: str
    max_tokens: int = 256


BENCHMARK_PROMPTS: tuple[BenchmarkPrompt, ...] = (
    BenchmarkPrompt(
        name="short",
        text="Напиши функцию Hello World на Python.",
        max_tokens=64,
    ),
    BenchmarkPrompt(
        name="medium",
        text="Объясни разницу между threading и multiprocessing в Python. Приведи примеры.",
        max_tokens=256,
    ),
    BenchmarkPrompt(
        name="long",
        text=(
            "Напиши полный пример FastAPI-приложения: CRUD-эндпоинты для "
            "сущности «задача», валидация Pydantic, SQLite через SQLAlchemy, "
            "обработка ошибок."
        ),
        max_tokens=512,
    ),
)


@dataclass
class BenchmarkRow:
    """Одна строка: (model, prompt, repeat) × один замер генерации."""

    model_id: str
    model_name: str
    provider: str  # "slm" | "ollama"
    device: str  # "GPU" | "CPU" | "?" (Ollama сам решает)
    n_ctx: int
    n_threads: int
    n_gpu_layers: int
    prompt_name: str
    prompt_chars: int
    max_tokens: int
    repeat: int
    ttft_ms: float | None = None
    tokens: int = 0
    gen_ms: float = 0.0
    tokens_per_sec: float = 0.0
    total_ms: float = 0.0
    ok: bool = True
    error: str = ""


@dataclass
class LoadTimeRow:
    """Одна строка замера загрузки модели."""

    model_id: str
    model_name: str
    n_ctx: int
    n_threads: int
    n_gpu_layers: int
    load_ms: float
    attempt: int
    ok: bool = True
    error: str = ""


@dataclass
class BenchmarkResult:
    """Полный результат прогона."""

    rows: list[BenchmarkRow] = field(default_factory=list)
    load_rows: list[LoadTimeRow] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok_rows(self) -> list[BenchmarkRow]:
        """Только успешные замеры (для усреднения)."""
        return [r for r in self.rows if r.ok]


# ============================================================================
# Внутренние утилиты
# ============================================================================


def _resolve_slm_params(model_path: Path) -> tuple[int, int, int, int]:
    """Возвращает (n_ctx, n_threads, n_batch, n_gpu_layers) из slm_optimizer.

    Использует тот же путь, что и рабочий SLM: `optimize()` подбирает
    параметры под железо. Если модуль недоступен — безопасные дефолты
    (4096 ctx, auto threads, CPU-only).
    """
    try:
        from agents.slm_optimizer import optimize

        params = optimize(model_path, target_n_ctx=4096)
        return (
            params.n_ctx,
            params.n_threads,
            params.n_batch,
            params.n_gpu_layers,
        )
    except Exception as e:
        logger.warning(f"Не удалось оптимизировать параметры для {model_path.name}: {e}")
        return 4096, 0, 512, 0


# ============================================================================
# SLM benchmark
# ============================================================================


async def _measure_slm_model(
    *,
    model_id: str,
    model_name: str,
    model_path: Path,
    prompts: list[BenchmarkPrompt],
    repeats: int,
    result: BenchmarkResult,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    """Прогоняет одну SLM-модель: загрузка × N + промпты × repeats.

    Замеры загрузки — на КАЖДОЙ попытке (1-я: с диска, 2-я и далее: из
    page cache — оцениваем реальную стоимость переключения в UI).
    Промпты прогоняются только на первой попытке — генерация после
    загрузки идентична.

    После прогона модель выгружается (del + gc.collect()), чтобы следующая
    не упала в OOM.
    """
    from agents.local_llm_client import LocalLLMClient

    n_ctx, n_threads, n_batch, n_gpu_layers = _resolve_slm_params(model_path)
    device = "GPU" if n_gpu_layers != 0 else "CPU"

    for attempt in range(1, repeats + 1):
        if on_progress:
            on_progress(f"⏳ Загрузка {model_name} (попытка {attempt}/{repeats})…")
        t0 = time.perf_counter()
        client = None
        try:
            client = LocalLLMClient(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_batch=n_batch,
                n_gpu_layers=n_gpu_layers,
                max_tokens=512,
            )
            load_ms = (time.perf_counter() - t0) * 1000
            result.load_rows.append(
                LoadTimeRow(
                    model_id=model_id,
                    model_name=model_name,
                    n_ctx=n_ctx,
                    n_threads=n_threads,
                    n_gpu_layers=n_gpu_layers,
                    load_ms=load_ms,
                    attempt=attempt,
                )
            )
        except Exception as e:
            logger.exception(f"Загрузка {model_name} упала")
            result.load_rows.append(
                LoadTimeRow(
                    model_id=model_id,
                    model_name=model_name,
                    n_ctx=n_ctx,
                    n_threads=n_threads,
                    n_gpu_layers=n_gpu_layers,
                    load_ms=0.0,
                    attempt=attempt,
                    ok=False,
                    error=str(e),
                )
            )
            if client is not None:
                try:
                    del client
                except Exception:
                    pass
            gc.collect()
            return  # промпты прогонять бессмысленно

        # --- Промпты (только на первой попытке) ---
        if attempt == 1:
            await _run_prompts_on_client(
                client=client,
                model_id=model_id,
                model_name=model_name,
                provider="slm",
                device=device,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_gpu_layers=n_gpu_layers,
                prompts=prompts,
                repeats=repeats,
                result=result,
                on_progress=on_progress,
            )

        # Выгружаем — следующая итерация замерит чистую загрузку.
        try:
            del client
        except Exception:
            pass
        gc.collect()
        # На Windows нативная память CUDA возвращается не сразу —
        # даём ОС время, чтобы следующая загрузка не получила OOM.
        await asyncio.sleep(0.3)


async def _run_prompts_on_client(
    *,
    client,
    model_id: str,
    model_name: str,
    provider: str,
    device: str,
    n_ctx: int,
    n_threads: int,
    n_gpu_layers: int,
    prompts: list[BenchmarkPrompt],
    repeats: int,
    result: BenchmarkResult,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    """Прогон промптов на уже загруженном клиенте (SLM или Ollama)."""
    from agents.ollama_client import ChatMessage, TextPart

    total = len(prompts) * repeats
    counter = 0

    for prompt in prompts:
        for repeat in range(1, repeats + 1):
            counter += 1
            if on_progress:
                on_progress(f"🧪 {model_name}: {prompt.name} (повтор {repeat}/{repeats}) [{counter}/{total}]")

            messages = [ChatMessage(role="user", content=prompt.text)]
            t0 = time.perf_counter()
            first_token_time: float | None = None
            collected_text = ""

            try:
                async for chunk in client.chat(
                    model="",
                    messages=messages,
                    stream=True,
                ):
                    if chunk.event_type == "delta" and chunk.part is not None:
                        if isinstance(chunk.part, TextPart) and chunk.part.text:
                            if first_token_time is None:
                                first_token_time = time.perf_counter() - t0
                            collected_text += chunk.part.text
                total_ms = (time.perf_counter() - t0) * 1000

                # Для SLM: last_stats содержит ttft_ms, tokens, tokens_per_sec.
                # Для Ollama: last_stats пустой → fallback.
                stats = getattr(client, "last_stats", {}) or {}
                ttft_ms = stats.get("ttft_ms")
                tokens = int(stats.get("tokens", 0))
                tokens_per_sec = float(stats.get("tokens_per_sec", 0.0))
                gen_ms = float(stats.get("gen_ms", 0.0))

                if not stats:
                    # Fallback: считаем сами (Ollama или другой клиент).
                    ttft_ms = first_token_time * 1000 if first_token_time else None
                    # Грубая оценка токенов по символам (4 символа ≈ 1 токен).
                    tokens = max(1, len(collected_text) // 4)
                    gen_seconds = (total_ms - (ttft_ms or 0)) / 1000
                    tokens_per_sec = tokens / gen_seconds if gen_seconds > 0 else 0.0
                    gen_ms = total_ms

                result.rows.append(
                    BenchmarkRow(
                        model_id=model_id,
                        model_name=model_name,
                        provider=provider,
                        device=device,
                        n_ctx=n_ctx,
                        n_threads=n_threads,
                        n_gpu_layers=n_gpu_layers,
                        prompt_name=prompt.name,
                        prompt_chars=len(prompt.text),
                        max_tokens=prompt.max_tokens,
                        repeat=repeat,
                        ttft_ms=float(ttft_ms) if ttft_ms is not None else None,
                        tokens=tokens,
                        gen_ms=gen_ms,
                        tokens_per_sec=tokens_per_sec,
                        total_ms=total_ms,
                        ok=True,
                    )
                )
            except Exception as e:
                logger.exception(f"Промпт {prompt.name} на {model_name} упал")
                result.rows.append(
                    BenchmarkRow(
                        model_id=model_id,
                        model_name=model_name,
                        provider=provider,
                        device=device,
                        n_ctx=n_ctx,
                        n_threads=n_threads,
                        n_gpu_layers=n_gpu_layers,
                        prompt_name=prompt.name,
                        prompt_chars=len(prompt.text),
                        max_tokens=prompt.max_tokens,
                        repeat=repeat,
                        ok=False,
                        error=str(e),
                    )
                )


# ============================================================================
# Ollama benchmark
# ============================================================================


async def _measure_ollama_model(
    *,
    model_id: str,
    model_name: str,
    base_url: str,
    prompts: list[BenchmarkPrompt],
    repeats: int,
    result: BenchmarkResult,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    """Прогон одной Ollama-модели (загрузка управляется Ollama).

    Загрузку Ollama не замеряем: она ленивая (при первом запросе) и
    кэшируется сервисом. Стоимость переключения — на стороне Ollama.
    """
    from agents.ollama_client import OllamaClient

    client = OllamaClient(base_url=base_url, timeout=300.0)
    try:
        async with client:
            await _run_prompts_on_client(
                client=client,
                model_id=model_id,
                model_name=model_name,
                provider="ollama",
                device="?",  # Ollama сам решает, GPU или CPU
                n_ctx=4096,
                n_threads=0,
                n_gpu_layers=0,
                prompts=prompts,
                repeats=repeats,
                result=result,
                on_progress=on_progress,
            )
    except Exception as e:
        logger.exception(f"Ollama {model_name} упал")
        result.rows.append(
            BenchmarkRow(
                model_id=model_id,
                model_name=model_name,
                provider="ollama",
                device="?",
                n_ctx=4096,
                n_threads=0,
                n_gpu_layers=0,
                prompt_name="(connection)",
                prompt_chars=0,
                max_tokens=0,
                repeat=0,
                ok=False,
                error=str(e),
            )
        )


# ============================================================================
# Главная точка входа
# ============================================================================


async def run_benchmark(
    models: list[dict],
    *,
    prompts: list[BenchmarkPrompt] | None = None,
    repeats: int = 2,
    on_progress: Callable[[str], None] | None = None,
) -> BenchmarkResult:
    """Главная точка входа: прогон всех моделей последовательно.

    Args:
        models: Список спецификаций моделей:
            * SLM: ``{"id", "name", "provider": "slm", "path"}``;
            * Ollama: ``{"id", "name", "provider": "ollama", "base_url"}``.
        prompts: Промпты (по умолчанию `BENCHMARK_PROMPTS`).
        repeats: Сколько раз повторить каждый промпт (усреднение).
        on_progress: Колбэк ``(message: str) -> None`` для UI.

    Returns:
        `BenchmarkResult` со всеми строками.
    """
    prompts = list(prompts or BENCHMARK_PROMPTS)
    result = BenchmarkResult(
        started_at=datetime.now().isoformat(timespec="seconds"),
    )

    for model in models:
        provider = model.get("provider", "slm")
        if provider == "slm":
            await _measure_slm_model(
                model_id=model["id"],
                model_name=model["name"],
                model_path=Path(model["path"]),
                prompts=prompts,
                repeats=repeats,
                result=result,
                on_progress=on_progress,
            )
        elif provider == "ollama":
            await _measure_ollama_model(
                model_id=model["id"],
                model_name=model["name"],
                base_url=model.get("base_url", "http://localhost:11434"),
                prompts=prompts,
                repeats=repeats,
                result=result,
                on_progress=on_progress,
            )
        else:
            logger.warning(f"Unknown provider {provider!r} для {model.get('id')!r}")

    result.finished_at = datetime.now().isoformat(timespec="seconds")
    return result


# ============================================================================
# Экспорт
# ============================================================================


def save_csv(result: BenchmarkResult, path: Path) -> None:
    """Сохраняет rows в CSV + загрузки в `<stem>_load.csv`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "model_id",
                "model_name",
                "provider",
                "device",
                "n_ctx",
                "n_threads",
                "n_gpu_layers",
                "prompt_name",
                "prompt_chars",
                "max_tokens",
                "repeat",
                "ttft_ms",
                "tokens",
                "gen_ms",
                "tokens_per_sec",
                "total_ms",
                "ok",
                "error",
            ]
        )
        for row in result.rows:
            writer.writerow(
                [
                    row.model_id,
                    row.model_name,
                    row.provider,
                    row.device,
                    row.n_ctx,
                    row.n_threads,
                    row.n_gpu_layers,
                    row.prompt_name,
                    row.prompt_chars,
                    row.max_tokens,
                    row.repeat,
                    f"{row.ttft_ms:.1f}" if row.ttft_ms is not None else "",
                    row.tokens,
                    f"{row.gen_ms:.1f}",
                    f"{row.tokens_per_sec:.2f}",
                    f"{row.total_ms:.1f}",
                    "ok" if row.ok else "error",
                    row.error,
                ]
            )

    # Загрузки — в отдельный файл-суффикс (структура другая).
    load_path = path.with_stem(path.stem + "_load")
    with load_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "model_id",
                "model_name",
                "n_ctx",
                "n_threads",
                "n_gpu_layers",
                "attempt",
                "load_ms",
                "ok",
                "error",
            ]
        )
        for row in result.load_rows:
            writer.writerow(
                [
                    row.model_id,
                    row.model_name,
                    row.n_ctx,
                    row.n_threads,
                    row.n_gpu_layers,
                    row.attempt,
                    f"{row.load_ms:.1f}",
                    "ok" if row.ok else "error",
                    row.error,
                ]
            )


def to_markdown(result: BenchmarkResult) -> str:
    """Собирает Markdown-отчёт: две таблицы (генерация + загрузка)."""
    lines = ["# Замеры производительности", ""]
    lines.append(f"- Начало: {result.started_at}")
    lines.append(f"- Конец: {result.finished_at}")
    lines.append(f"- Строк генерации: {len(result.rows)} (ok: {len(result.ok_rows)})")
    lines.append(f"- Строк загрузки: {len(result.load_rows)}")
    lines.append("")

    if result.rows:
        lines.append("## Генерация")
        lines.append("")
        lines.append(
            "| Модель | Provider | Device | n_ctx | n_threads | Промпт | "
            "TTFT (ms) | tok/s | Всего (ms) | tok | OK |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in result.rows:
            ttft = f"{r.ttft_ms:.0f}" if r.ttft_ms is not None else "—"
            lines.append(
                f"| {r.model_name} | {r.provider} | {r.device} | "
                f"{r.n_ctx} | {r.n_threads} | {r.prompt_name} | "
                f"{ttft} | {r.tokens_per_sec:.1f} | {r.total_ms:.0f} | "
                f"{r.tokens} | {'✅' if r.ok else '❌'} |"
            )
        lines.append("")

    if result.load_rows:
        lines.append("## Загрузка модели")
        lines.append("")
        lines.append("| Модель | n_ctx | n_threads | n_gpu_layers | Попытка | Загрузка (ms) | OK |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in result.load_rows:
            lines.append(
                f"| {r.model_name} | {r.n_ctx} | {r.n_threads} | "
                f"{r.n_gpu_layers} | {r.attempt} | {r.load_ms:.0f} | "
                f"{'✅' if r.ok else '❌'} |"
            )
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "BENCHMARK_PROMPTS",
    "BenchmarkPrompt",
    "BenchmarkResult",
    "BenchmarkRow",
    "LoadTimeRow",
    "run_benchmark",
    "save_csv",
    "to_markdown",
]
