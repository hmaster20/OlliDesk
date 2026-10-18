"""Чтение метаданных GGUF напрямую из файла (без llama.cpp, без CUDA)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from loguru import logger

_GGUF_MAGIC = b"GGUF"

# GGUF value types (см. gguf.md в llama.cpp).
_T_UINT8, _T_INT8 = 0, 1
_T_UINT16, _T_INT16 = 2, 3
_T_UINT32, _T_INT32, _T_FLOAT32 = 4, 5, 6
_T_BOOL, _T_STRING, _T_ARRAY = 7, 8, 9
_T_UINT64, _T_INT64, _T_FLOAT64 = 10, 11, 12


def _skip_value(f, vtype: int) -> None:
    """Пропускает значение без декодирования (быстро для больших массивов)."""
    if vtype in (_T_UINT8, _T_INT8, _T_BOOL):
        f.seek(1, 1)
    elif vtype in (_T_UINT16, _T_INT16):
        f.seek(2, 1)
    elif vtype in (_T_UINT32, _T_INT32, _T_FLOAT32):
        f.seek(4, 1)
    elif vtype in (_T_UINT64, _T_INT64, _T_FLOAT64):
        f.seek(8, 1)
    elif vtype == _T_STRING:
        n = struct.unpack("<Q", f.read(8))[0]
        f.seek(n, 1)
    elif vtype == _T_ARRAY:
        elem_type = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n):
            _skip_value(f, elem_type)
    else:
        raise ValueError(f"unknown GGUF type: {vtype}")


def _read_value(f, vtype: int):
    if vtype == _T_UINT8:
        return struct.unpack("B", f.read(1))[0]
    if vtype == _T_INT8:
        return struct.unpack("b", f.read(1))[0]
    if vtype == _T_UINT16:
        return struct.unpack("<H", f.read(2))[0]
    if vtype == _T_INT16:
        return struct.unpack("<h", f.read(2))[0]
    if vtype == _T_UINT32:
        return struct.unpack("<I", f.read(4))[0]
    if vtype == _T_INT32:
        return struct.unpack("<i", f.read(4))[0]
    if vtype == _T_FLOAT32:
        return struct.unpack("<f", f.read(4))[0]
    if vtype == _T_BOOL:
        return struct.unpack("?", f.read(1))[0]
    if vtype == _T_STRING:
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf-8", errors="replace")
    if vtype == _T_ARRAY:
        # Нам нужны только числовые/строковые массивы небольших размеров.
        elem_type = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        return [_read_value(f, elem_type) for _ in range(n)]
    if vtype == _T_UINT64:
        return struct.unpack("<Q", f.read(8))[0]
    if vtype == _T_INT64:
        return struct.unpack("<q", f.read(8))[0]
    if vtype == _T_FLOAT64:
        return struct.unpack("<d", f.read(8))[0]
    raise ValueError(f"unknown GGUF type: {vtype}")


@dataclass(frozen=True)
class GGUFMeta:
    n_layer: int
    n_embd: int
    n_head: int
    n_head_kv: int
    n_ctx_train: int
    architecture: str

    @property
    def head_dim(self) -> int:
        return self.n_embd // self.n_head if self.n_head else 0

    @property
    def kv_bytes_per_token_fp16(self) -> int:
        """KV-кэш fp16: 2 (K+V) × n_layer × n_head_kv × head_dim × 2 байта."""
        return 2 * self.n_layer * self.n_head_kv * self.head_dim * 2

    @property
    def kv_bytes_per_token_q8(self) -> int:
        """KV-кэш q8_0 — примерно вдвое компактнее fp16."""
        return 2 * self.n_layer * self.n_head_kv * self.head_dim * 1


# Ключи, которые читаем. Всё остальное (tokens, merges, chat_template)
# пропускаем через seek — они не нужны и весят десятки МБ.
_WANTED = {
    "general.architecture",
    "llama.block_count",
    "qwen2.block_count",
    "llama.embedding_length",
    "qwen2.embedding_length",
    "llama.attention.head_count",
    "qwen2.attention.head_count",
    "llama.attention.head_count_kv",
    "qwen2.attention.head_count_kv",
    "llama.context_length",
    "qwen2.context_length",
}


@lru_cache(maxsize=32)
def read_gguf_meta(path: Path | str) -> GGUFMeta:
    """Читает метаданные GGUF напрямую. Кэшируется по пути."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"GGUF не найден: {path}")

    with open(path, "rb") as f:
        if f.read(4) != _GGUF_MAGIC:
            raise ValueError(f"не GGUF-файл: {path}")
        _version = struct.unpack("<I", f.read(4))[0]
        _tensor_count = struct.unpack("<Q", f.read(8))[0]
        kv_count = struct.unpack("<Q", f.read(8))[0]

        meta: dict = {}
        for _ in range(kv_count):
            key_len = struct.unpack("<Q", f.read(8))[0]
            key = f.read(key_len).decode("utf-8", errors="replace")
            vtype = struct.unpack("<I", f.read(4))[0]
            if key in _WANTED:
                meta[key] = _read_value(f, vtype)
            else:
                _skip_value(f, vtype)

    arch = str(meta.get("general.architecture", "llama")).lower()

    def _get(*names: str, default: int = 0) -> int:
        for n in names:
            v = meta.get(n)
            if v is not None:
                return int(v)
        return default

    n_layer = _get(f"{arch}.block_count", "llama.block_count")
    n_embd = _get(f"{arch}.embedding_length", "llama.embedding_length")
    n_head = _get(f"{arch}.attention.head_count", "llama.attention.head_count")
    n_head_kv = _get(f"{arch}.attention.head_count_kv", "llama.attention.head_count_kv", default=n_head)
    n_ctx_train = _get(f"{arch}.context_length", "llama.context_length")

    result = GGUFMeta(
        n_layer=n_layer,
        n_embd=n_embd,
        n_head=n_head,
        n_head_kv=n_head_kv,
        n_ctx_train=n_ctx_train,
        architecture=arch,
    )
    logger.debug(
        f"GGUF {path.name}: arch={arch}, layer={n_layer}, embd={n_embd}, "
        f"head={n_head}/{n_head_kv}, ctx_train={n_ctx_train}, "
        f"KV/token fp16={result.kv_bytes_per_token_fp16} B"
    )
    return result
