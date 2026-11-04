"""ModelPool: одновременная загрузка planner(3B,GPU) и analyst(7B,CPU).

Арифметика размещения (T1200 free≈3935 → usable≈3116 МБ, COMPUTE_BUFFER=350):
  3B q4_k_m weights≈1930 + kv q8_0@8192≈600 + 350 ≈ 2880  → ЦЕЛИКОМ В VRAM
  7B q4_k_m weights≈4680 + любое kv + 350 ≥ 5254          → НИКОГДА не в VRAM
Следовательно planner всегда GPU (n_gpu_layers=-1), analyst всегда CPU
(n_gpu_layers=0). Устройства не конфликтуют → реальный параллелизм.
RAM 127 ГБ держит обе без напряжения.
"""

from __future__ import annotations
from loguru import logger
import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Role(StrEnum):
    """Роли моделей в парке (4.1).

    * ``ROUTER`` — 1.5B/3B, GPU. Быстро решает, какой инструмент звать
      (``classify``/``plan``/``tool``). Переиспользует planner-файл, если
      тот же (ModelPool кэширует по файлу).
    * ``PLANNER`` — 3B, GPU. classify/plan/critique для research.
    * ``CODER`` — 7B–30B (MoE), CPU. Правка кода, ``apply_patch``.
    * ``VERIFIER`` — 1.5B/3B, GPU. Дешёвая проверка формата (``critic.py``).
    * ``ANALYST`` — 7B, CPU. Синтез/regen для research.
    * ``ANALYST_PRO`` — 14B, CPU. Опциональный «quality mode».
    * ``EMBED`` — embedding-модель для RAG.
    """

    ROUTER = "router"
    PLANNER = "planner"
    CODER = "coder"
    VERIFIER = "verifier"
    ANALYST = "analyst"
    ANALYST_PRO = "analyst_pro"
    EMBED = "embed"


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


# 4.1: дефолтные спеки для research-флоу. Router/Coder/Verifier НЕ в
# DEFAULT_SPECS — они загружаются только если профиль явно требует
# (park_roles). ModelPool по умолчанию остаётся двухмодельным.
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
    # Сигнатура совпадает с LocalLLMClient.embed(self, model, texts):
    # Orchestrator сам embed не вызывает (работает через vector_store),
    # но Protocol должен структурно совпадать с factory(), иначе mypy
    # отвергает передачу LocalLLMClient в add_role().
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...


class ModelPool:
    """Держит модели загруженными и роутит по Role (4.1).

    LRU-вытеснение по ``max_loaded``: если при ``add_role`` в пуле уже
    ``max_loaded`` уникальных файлов, выгружается роль, которая дольше
    всего не использовалась (``client(role)`` обновляет ``_last_used``).

    Файлы переиспользуются между ролями: если planner и router — один
    и тот же файл, второй ``add_role`` вернёт тот же клиент (кэш по
    ``file or model_id``).
    """

    def __init__(self, *, max_loaded: int = 3) -> None:
        """Инициализация.

        Args:
            max_loaded: Максимум уникальных файлов в памяти одновременно.
                По умолчанию 3: router+planner (GPU) + coder (CPU). При
                превышении — LRU-вытеснение.
        """
        self._clients: dict[Role, ClientLike] = {}
        self._clients_by_file: dict[str, ClientLike] = {}
        self._file_by_role: dict[Role, str] = {}
        self._specs: dict[Role, RoleSpec] = {}
        self._last_used: dict[Role, float] = {}
        self._lock = asyncio.Lock()
        self.max_loaded = max(1, int(max_loaded))

    async def load(
        self,
        specs: dict[Role, RoleSpec],
        factory: Callable[[RoleSpec], ClientLike],
    ) -> None:
        """Загружает несколько ролей (последовательно, чтобы не было VRAM-гонки)."""
        for spec in specs.values():
            await self.add_role(spec, factory)

    async def add_role(
        self,
        spec: RoleSpec,
        factory: Callable[[RoleSpec], ClientLike],
    ) -> ClientLike:
        """Загружает роль, переиспользуя клиент по файлу; делает LRU-вытеснение.

        Args:
            spec: Спецификация роли.
            factory: Блокирующая загрузка (``factory(spec) -> ClientLike``).

        Returns:
            Загруженный клиент.
        """
        key = spec.file or spec.model_id
        cached = self._clients_by_file.get(key)
        if cached is not None:
            self._clients[spec.role] = cached
            self._specs[spec.role] = spec
            self._file_by_role[spec.role] = key
            self._last_used[spec.role] = time.monotonic()
            return cached

        # LRU: перед загрузкой нового файла — выгрузить самый старый, если
        # уникальных файлов уже max_loaded.
        if len(self._clients_by_file) >= self.max_loaded:
            await self._evict_lru()

        client = await asyncio.to_thread(factory, spec)
        self._clients_by_file[key] = client
        self._clients[spec.role] = client
        self._specs[spec.role] = spec
        self._file_by_role[spec.role] = key
        self._last_used[spec.role] = time.monotonic()
        logger.info(
            f"ModelPool: загружена роль {spec.role.value} "
            f"({spec.model_id}, file={key}, device={spec.device}, "
            f"loaded_files={len(self._clients_by_file)}/{self.max_loaded})"
        )
        return client

    async def _evict_lru(self) -> None:
        """Выгружает роль, которая дольше всего не использовалась.

        Не выгружаем, если у роли остались «соседи» по файлу: клиент
        держится, пока есть хоть одна роль с тем же файлом.
        """
        if not self._last_used:
            return
        # Все роли на одном файле — «одна запись» для LRU.
        file_roles: dict[str, list[Role]] = {}
        for role, file_key in self._file_by_role.items():
            file_roles.setdefault(file_key, []).append(role)

        # Самая старая по _last_used роль.
        candidates = sorted(self._last_used.items(), key=lambda kv: kv[1])
        for role, _ts in candidates:
            file_key = self._file_by_role.get(role)
            if file_key is None:
                continue
            # Удаляем запись `file_key`, только если больше нет ролей на него.
            roles_on_file = [r for r in file_roles.get(file_key, []) if r in self._clients]
            if len(roles_on_file) > 1:
                # Оставляем клиент, удаляем только эту роль.
                self._clients.pop(role, None)
                self._specs.pop(role, None)
                self._file_by_role.pop(role, None)
                self._last_used.pop(role, None)
                logger.info(
                    f"ModelPool LRU: выгружена роль {role.value} "
                    f"(файл {file_key} остаётся для {len(roles_on_file) - 1} других ролей)"
                )
                return

            # Последняя роль на файле — выгружаем клиент целиком.
            client = self._clients_by_file.pop(file_key, None)
            self._clients.pop(role, None)
            self._specs.pop(role, None)
            self._file_by_role.pop(role, None)
            self._last_used.pop(role, None)
            # Закрываем клиент (best-effort).
            if client is not None:
                close = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close:
                    try:
                        res = close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        logger.debug("ModelPool LRU: ошибка закрытия клиента", exc_info=True)
            logger.info(f"ModelPool LRU: выгружен клиент {file_key} (роль {role.value})")
            return

    def client(self, role: Role) -> ClientLike:
        """Возвращает клиент роли; обновляет `_last_used`."""
        if role not in self._clients:
            raise RuntimeError(f"Модель роли {role.value} не загружена")
        self._last_used[role] = time.monotonic()
        return self._clients[role]

    def loaded_roles(self) -> list[Role]:
        """Список загруженных ролей (для UI/диагностики)."""
        return list(self._clients.keys())

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
