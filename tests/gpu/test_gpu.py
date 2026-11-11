# test_gpu.py в корне проекта
from pathlib import Path
from typing import cast

from llama_cpp import Llama
from llama_cpp.llama_types import CreateChatCompletionResponse

# model_path = Path.home() / ".ollidesk" / "models" / "qwen2.5-3b-instruct-q5_k_m.gguf"
model_path = Path.home() / ".ollidesk" / "models" / "qwen2.5-coder-7b-instruct-q6_k.gguf"

if not model_path.exists():
    print(f"Нет модели: {model_path}")
    print("Скачайте: python -m agents.download_slm")
    raise SystemExit(1)

llm = Llama(
    model_path=str(model_path),
    n_ctx=4096,
    n_gpu_layers=-1,  # все слои на GPU
    n_threads=8,
    n_batch=512,
    flash_attn=True,
    verbose=True,
)
# stream=False гарантирует CreateChatCompletionResponse, но стабы
# llama_cpp отдают union (Response | Iterator). Cast — не проверка,
# а именно сужение: stream=False → не итератор.
out = cast(
    CreateChatCompletionResponse,
    llm.create_chat_completion(
        messages=[{"role": "user", "content": "..."}],
        max_tokens=32,
        stream=False,
    ),
)
print("Ответ:", out["choices"][0]["message"]["content"])
