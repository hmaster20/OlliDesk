# test_3b_variants.py — тот же промпт на всех доступных 3B
import os

os.environ["LLAMA_LOG_LEVEL"] = "WARN"

from pathlib import Path
from time import perf_counter

from llama_cpp import Llama

MODELS_DIR = Path.home() / ".ollidesk" / "models"
CANDS = [
    "qwen2.5-3b-instruct-q4_k_m.gguf",  # ~2.0 ГБ
    "qwen2.5-3b-instruct-q6_k.gguf",  # ~2.9 ГБ
    "qwen2.5-coder-7b-instruct-q5_k_m.gguf",  # ~5.4 ГБ (для контроля)
]
PROMPT = "Напиши функцию на Python, которая проверяет, является ли строка палиндромом. Только код."

for name in CANDS:
    path = MODELS_DIR / name
    if not path.exists():
        print(f"SKIP  {name}: нет файла")
        continue
    try:
        llm = Llama(
            model_path=str(path),
            n_ctx=4096,
            n_gpu_layers=-1,
            n_threads=8,
            n_batch=512,
            flash_attn=True,
            verbose=False,
        )
        t0 = perf_counter()
        out = llm.create_chat_completion(
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=128,
            temperature=0.2,
        )
        dt = perf_counter() - t0
        n_tok = out["usage"]["completion_tokens"]
        print(f"OK    {name}: {n_tok} ток за {dt:.2f} с = {n_tok / dt:.1f} ток/с")
        del llm
    except Exception as e:
        print(f"FAIL  {name}: {e}")
