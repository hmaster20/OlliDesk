"""ModelPool: одновременная загрузка planner(3B,GPU) и analyst(7B,CPU).

Арифметика размещения (T1200 free≈3935 → usable≈3116 МБ, COMPUTE_BUFFER=350):
  3B q4_k_m weights≈1930 + kv q8_0@8192≈600 + 350 ≈ 2880  → ЦЕЛИКОМ В VRAM
  7B q4_k_m weights≈4680 + любое kv + 350 ≥ 5254          → НИКОГДА не в VRAM
Следовательно planner всегда GPU (n_gpu_layers=-1), analyst всегда CPU
(n_gpu_layers=0). Устройства не конфликтуют → реальный параллелизм.
RAM 127 ГБ держит обе без напряжения.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class Role(str, Enum):
    PLANNER = "planner"  # 3B GPU: classify/plan/critique/tool-grammar
    ANALYST = "analyst"  # 7B CPU: synthesis/regen (качество)
    # Слот на будущее для больших/критичных задач:
    ANALYST_PRO = "analyst_pro"  # 14B CPU (q4_k_m ≈ 9 ГБ), переключается по флагу


@dataclass
class RoleSpec:
    role: Role
    model_id: str
    device: str  # "GPU" | "CPU"
    n_ctx: int
    kv_cache: str  # "q8_0" | "f16"
    n_gpu_layers: int
    temperature: float
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    file: str | None = None  # явный gguf (обходит split-артефакты)


# Дефолты под ваше железо; переопределяются из config v3 models.*
DEFAULT_SPECS: dict[Role, RoleSpec] = {
    Role.PLANNER: RoleSpec(
        role=Role.PLANNER,
        model_id="qwen2.5-3b-instruct",
        device="GPU",
        n_ctx=8192,
        kv_cache="q8_0",
        n_gpu_layers=-1,
        temperature=0.0,
    ),
    Role.ANALYST: RoleSpec(
        role=Role.ANALYST,
        model_id="qwen2.5-7b-instruct",
        device="CPU",
        n_ctx=8192,
        kv_cache="q8_0",
        n_gpu_layers=0,
        temperature=0.35,
    ),
}


class ClientLike(Protocol):
    """Минимальный контракт клиента (LocalLLMClient/OllamaClient)."""

    n_ctx: int

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        grammar: str | None = None,
        json_schema: dict | None = None,
        stream: bool = False,
    ) -> Any: ...
    def count_tokens(self, text: str) -> int: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ModelPool:
    """Держит обе модели загруженными одновременно и роутит по Role."""

    def __init__(self) -> None:
        self._clients: dict[Role, ClientLike] = {}
        self._clients_by_file: dict[str, ClientLike] = {}
        self._specs: dict[Role, RoleSpec] = {}
        self._lock = asyncio.Lock()

    async def load(self, specs: dict[Role, RoleSpec], factory) -> None:
        """factory(spec) -> ClientLike. Грузим planner и analyst параллельно
        (разные устройства → нет contention при загрузке)."""

        async def _one(spec: RoleSpec) -> tuple[Role, ClientLike]:
            c = await asyncio.to_thread(factory, spec)  # блокирующая загрузка llama.cpp
            return spec.role, c

        results = await asyncio.gather(*(_one(s) for s in specs.values()))
        for role, client in results:
            self._clients[role] = client
            self._specs[role] = specs[role]

    async def add_role(self, spec: RoleSpec, factory) -> ClientLike:
        """Загружает роль, переиспользуя клиент по файлу модели.

        Если предыдущая роль уже загрузила модель из ТОГО ЖЕ файла —
        возвращаем существующий клиент, а не создаём вторую копию.
        Типичный сценарий: planner и analyst указывают на одну 3B
        (`qwen2.5-3b-instruct-q4_k_m.gguf`). Без кэша загружаются две
        копии: planner на GPU (n_gpu_layers=-1), analyst на CPU
        (n_gpu_layers=0) — это лишние 5 ГБ RAM и «шизофрения» скорости.

        Args:
            spec: Спецификация роли (device, n_ctx, kv_cache, ...).
            factory: ``factory(spec) -> ClientLike`` — блокирующая загрузка.

        Returns:
            Загруженный клиент (также сохранён в ``self._clients[spec.role]``).
        """
        # Кэш по файлу: если файл уже загружен под другой ролью —
        # переиспользуем его (даже если device/params отличаются —
        # модель уже в памяти, перезагрузка бессмысленна).
        key = spec.file or spec.model_id
        cached = self._clients_by_file.get(key)
        if cached is not None:
            self._clients[spec.role] = cached
            self._specs[spec.role] = spec
            return cached

        client = await asyncio.to_thread(factory, spec)
        self._clients_by_file[key] = client
        self._clients[spec.role] = client
        self._specs[spec.role] = spec
        return client
    def client(self, role: Role) -> ClientLike:
        if role not in self._clients:
            raise RuntimeError(f"Модель роли {role.value} не загружена")
        return self._clients[role]

    def spec(self, role: Role) -> RoleSpec:
        return self._specs[role]

    def runtime_ctx(self, role: Role) -> int:
        """ctx_total для BudgetManager — у ЗАГРУЖЕННОГО клиента (D1/D5)."""
        return self.client(role).n_ctx

    def pick_role(self, task: str, project_files: int, quality_mode: bool) -> Role:
        """RoleResolver: какая модель делает финал.

        Architecture/объяснение => ANALYST (качество). Classify/plan/critique
        => PLANNER всегда. ANALYST_PRO — только явный quality_mode + большой проект.
        """
        if task in {"classify", "plan", "critique", "tool"}:
            return Role.PLANNER
        if quality_mode and project_files > 500 and Role.ANALYST_PRO in self._clients:
            return Role.ANALYST_PRO
        return Role.ANALYST

    async def aclose(self) -> None:
        for c in self._clients.values():
            close = getattr(c, "aclose", None) or getattr(c, "close", None)
            if close:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
        self._clients.clear()
        self._clients_by_file.clear()
