import sys
from pathlib import Path

import llama_cpp

print("Интерпретатор:", sys.executable)
print("sys.path:")
for p in sys.path:
    print("  ", p)
print("llama_cpp из:", Path(llama_cpp.__file__).parent)
