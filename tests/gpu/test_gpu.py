# test_gpu.py в корне проекта
from pathlib import Path
from llama_cpp import Llama

# model_path = Path.home() / ".ollidesk" / "models" / "qwen2.5-3b-instruct-q5_k_m.gguf"
model_path = Path.home() / ".ollidesk" / "models" / "qwen2.5-coder-7b-instruct-q6_k.gguf"

if not model_path.exists():
    print(f"Нет модели: {model_path}")
    print("Скачайте: python -m agents.download_slm")
    raise SystemExit(1)

llm = Llama(
    model_path=str(model_path),
    n_ctx=4096,
    n_gpu_layers=-1,     # все слои на GPU
    n_threads=8,
    n_batch=512,
    flash_attn=True,
    verbose=True,
)
out = llm.create_chat_completion(
    messages=[{"role": "user", "content": "Скажи одним словом: привет"}],
    max_tokens=32,
)
print("Ответ:", out["choices"][0]["message"]["content"])
