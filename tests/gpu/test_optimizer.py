# tools/test_optimizer.py
from pathlib import Path

from agents.gguf_reader import read_gguf_meta
from agents.hardware_profile import get_hardware_profile
from agents.slm_optimizer import optimize
from agents.slm_selector import select_best_model

print("Железо:", get_hardware_profile().describe())

best = select_best_model()
print("Автовыбор:", best.id if best else "—")

models_dir = Path.home() / ".ollidesk" / "models"
for p in sorted(models_dir.glob("*.gguf")):
    if "embed" in p.name.lower():
        continue
    try:
        m = read_gguf_meta(p)
        print(f"\n{p.name}: layer={m.n_layer}, kv_fp16={m.kv_bytes_per_token_fp16} B/tok")
        params = optimize(p)
        print(params.to_json())
    except Exception as e:
        print(f"  {p.name}: ошибка {e}")
