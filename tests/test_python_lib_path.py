import sys, llama_cpp
from pathlib import Path
print("Интерпретатор:", sys.executable)
print("sys.path:")
for p in sys.path:
    print("  ", p)
print("llama_cpp из:", Path(llama_cpp.__file__).parent)