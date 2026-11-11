"""4.1: парк моделей — роли, LRU, max_loaded."""

from __future__ import annotations

import pytest

from agents.model_pool import ModelPool, Role, RoleSpec


class _FakeClient:
    def __init__(self, name: str):
        self.name = name
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _spec(role: Role, file: str, device: str = "CPU") -> RoleSpec:
    return RoleSpec(
        role=role,
        model_id=f"model-{file}",
        device=device,
        n_ctx=4096,
        kv_cache="q8_0",
        n_gpu_layers=0,
        temperature=0.0,
        file=file,
    )


@pytest.mark.asyncio
async def test_add_role_loads_and_caches():
    pool = ModelPool(max_loaded=3)
    loaded: list[str] = []

    def factory(spec: RoleSpec) -> _FakeClient:
        loaded.append(spec.file or spec.model_id)
        return _FakeClient(spec.file or spec.model_id)

    a = await pool.add_role(_spec(Role.PLANNER, "a.gguf"), factory)
    assert a.name == "a.gguf"
    assert loaded == ["a.gguf"]

    # Тот же файл под другой ролью — тот же клиент, без повторной загрузки.
    b = await pool.add_role(_spec(Role.ROUTER, "a.gguf", device="GPU"), factory)
    assert b is a
    assert loaded == ["a.gguf"]
    assert set(pool.loaded_roles()) == {Role.PLANNER, Role.ROUTER}


@pytest.mark.asyncio
async def test_lru_evicts_oldest_file():
    pool = ModelPool(max_loaded=2)
    closed: list[str] = []

    class _C(_FakeClient):
        async def aclose(self):
            closed.append(self.name)

    def factory(spec: RoleSpec) -> _FakeClient:
        return _C(spec.file or spec.model_id)

    # Загружаем две роли на разных файлах.
    await pool.add_role(_spec(Role.PLANNER, "a.gguf"), factory)
    await pool.add_role(_spec(Role.ANALYST, "b.gguf"), factory)
    assert set(pool.loaded_roles()) == {Role.PLANNER, Role.ANALYST}

    # Обращаемся к analyst — он становится «свежим»; planner — самый старый.
    pool.client(Role.ANALYST)

    # Загружаем третью роль — выгружается planner.
    await pool.add_role(_spec(Role.CODER, "c.gguf"), factory)
    assert set(pool.loaded_roles()) == {Role.ANALYST, Role.CODER}
    assert "a.gguf" in closed


@pytest.mark.asyncio
async def test_lru_keeps_shared_file():
    """Если на файл ссылаются две роли — клиент не закрывается, пока
    жива хоть одна."""
    pool = ModelPool(max_loaded=2)
    closed: list[str] = []

    class _C(_FakeClient):
        async def aclose(self):
            closed.append(self.name)

    def factory(spec: RoleSpec) -> _FakeClient:
        return _C(spec.file or spec.model_id)

    # Planner и router — один файл.
    await pool.add_role(_spec(Role.PLANNER, "shared.gguf"), factory)
    await pool.add_role(_spec(Role.ROUTER, "shared.gguf", device="GPU"), factory)
    # Analyst — второй файл.
    await pool.add_role(_spec(Role.ANALYST, "b.gguf"), factory)
    assert set(pool.loaded_roles()) == {Role.PLANNER, Role.ROUTER, Role.ANALYST}

    # Третья роль (coder) → LRU выгружает самую старую (planner),
    # но файл shared.gguf остаётся (router на нём).
    await pool.add_role(_spec(Role.CODER, "c.gguf"), factory)
    assert Role.PLANNER not in pool.loaded_roles()
    assert Role.ROUTER in pool.loaded_roles()
    assert "shared.gguf" not in closed  # файл НЕ закрыт


@pytest.mark.asyncio
async def test_client_raises_if_not_loaded():
    pool = ModelPool(max_loaded=3)
    with pytest.raises(RuntimeError, match="не загружена"):
        pool.client(Role.CODER)


def test_role_enum_has_park_values():
    assert {r.value for r in Role} == {
        "router",
        "planner",
        "coder",
        "verifier",
        "analyst",
        "analyst_pro",
        "embed",
    }
