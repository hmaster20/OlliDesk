"""10.5: CLI для Coding Bench.

Запуск:
  python scripts/run_coding_bench.py --model qwen2.5-7b --iterations 30
  python scripts/run_coding_bench.py --only py_fix_parser py_add_function

Результаты: `bench_results.jsonl` + `bench_results.md` в текущей директории.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding Bench OlliDesk")
    parser.add_argument("--model", required=True, help="Имя SLM-модели / Ollama-tag")
    parser.add_argument(
        "--tasks-dir",
        default=str(_REPO_ROOT / "tests" / "bench" / "tasks"),
        help="Каталог задач",
    )
    parser.add_argument("--iterations", type=int, default=30, help="Лимит итераций агента")
    parser.add_argument("--only", nargs="+", default=None, help="Только указанные задачи")
    parser.add_argument(
        "--output",
        default="bench_results.jsonl",
        help="Файл результатов (jsonl)",
    )
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.exists():
        logger.error(f"Каталог задач не найден: {tasks_dir}")
        return 1

    # Собираем клиента и реестр (так же, как ChatPanel, но проще).
    from agents.download_slm import get_model_path
    from agents.local_llm_client import LocalLLMClient
    from agents.slm_optimizer import optimize
    from agents.slm_selector import select_pair
    from agents.tool_registry import ToolRegistry
    from tools.registry import ALL_TOOLS

    pair = select_pair()
    info = pair.get("analyst") or pair.get("planner")
    if info is None:
        logger.error("Нет моделей в каталоге SLM")
        return 1
    path = get_model_path(info.file)
    if path is None:
        logger.error(f"Файл модели не найден: {info.file}")
        return 1
    params = optimize(path, target_n_ctx=8192, device="CPU")
    client = LocalLLMClient(model_path=path, params=params, max_tokens=2048)

    registry = ToolRegistry()
    for tool_func in ALL_TOOLS:
        registry.register(tool_func)

    from tests.bench.runner import run_bench, save_results

    summary = run_bench(
        tasks_dir,
        model=args.model,
        client=client,
        registry=registry,
        max_iterations=args.iterations,
        only=args.only,
    )
    save_results(summary, Path(args.output))
    logger.info(f"Итог: pass@1 = {summary.pass_at_1:.2%}, tool success = {summary.tool_success_rate:.1%}")
    return 0 if summary.pass_at_1 >= 0.3 else 2


if __name__ == "__main__":
    sys.exit(main())
