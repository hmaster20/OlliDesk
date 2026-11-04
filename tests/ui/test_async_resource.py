"""2.9: AsyncResource — состояния, guard-паттерн, value_or."""

from __future__ import annotations

from ui.state.async_resource import AsyncResource, ResourceState


class TestInitialState:
    def test_starts_idle(self) -> None:
        r = AsyncResource[list[int]]()
        assert r.state == ResourceState.IDLE
        assert r.latest is None
        assert r.error is None

    def test_loaded_false_initially(self) -> None:
        r = AsyncResource[list[int]]()
        assert r.loaded() is False
        assert r.is_loading_first_time() is False
        assert r.is_refreshing() is False


class TestFirstLoad:
    def test_begin_load_sets_loading(self) -> None:
        r = AsyncResource[list[int]]()
        r.begin_load()
        assert r.state == ResourceState.LOADING
        assert r.loaded() is False
        assert r.is_loading_first_time() is True

    def test_set_value_ready(self) -> None:
        r = AsyncResource[list[int]]()
        r.begin_load()
        r.set_value([1, 2, 3])
        assert r.state == ResourceState.READY
        assert r.latest == [1, 2, 3]
        assert r.loaded() is True
        assert r.last_updated_at > 0

    def test_error_without_data(self) -> None:
        r = AsyncResource[list[int]]()
        r.begin_load()
        err = RuntimeError("boom")
        r.set_error(err)
        assert r.state == ResourceState.ERROR
        assert r.error is err
        assert r.latest is None
        assert r.loaded() is False


class TestRefresh:
    def test_begin_load_after_ready_sets_refreshing(self) -> None:
        """Повторный begin_load при наличии данных → REFRESHING, не LOADING."""
        r = AsyncResource[list[int]]()
        r.set_value([1, 2])
        r.begin_load()
        assert r.state == ResourceState.REFRESHING
        assert r.latest == [1, 2]  # данные сохранены
        assert r.is_refreshing() is True
        assert r.loaded() is True

    def test_refresh_preserves_latest_until_new_value(self) -> None:
        r = AsyncResource[list[int]]()
        r.set_value([1])
        r.begin_load()
        # latest ещё [1], пока не пришёл set_value.
        assert r.latest == [1]
        r.set_value([2])
        assert r.latest == [2]
        assert r.state == ResourceState.READY

    def test_error_during_refresh_keeps_latest(self) -> None:
        """Ошибка при refreshing → ERROR, но latest сохранён (stale-value)."""
        r = AsyncResource[list[int]]()
        r.set_value([1, 2])
        r.begin_load()
        r.set_error(RuntimeError("network"))
        assert r.state == ResourceState.ERROR
        assert r.latest == [1, 2]
        assert r.loaded() is True  # guard: рисуем stale
        assert r.has_stale_error() is True


class TestValueOr:
    def test_returns_latest(self) -> None:
        r = AsyncResource[list[int]]()
        r.set_value([42])
        assert r.value_or([]) == [42]

    def test_returns_default_when_no_value(self) -> None:
        r = AsyncResource[list[int]]()
        assert r.value_or([7]) == [7]

    def test_returns_default_when_loading_first_time(self) -> None:
        r = AsyncResource[list[int]]()
        r.begin_load()
        assert r.value_or([1]) == [1]


class TestClear:
    def test_clear_resets(self) -> None:
        r = AsyncResource[list[int]]()
        r.set_value([1])
        r.clear()
        assert r.state == ResourceState.IDLE
        assert r.latest is None
        assert r.error is None
        assert r.loaded() is False


class TestStatusText:
    def test_states(self) -> None:
        r = AsyncResource[list[int]]()
        assert r.status_text() == "не загружено"
        r.begin_load()
        assert "загрузка" in r.status_text()
        r.set_value([])
        assert r.status_text() == "готово"
        r.begin_load()
        assert "обновление" in r.status_text()
        r.set_error(RuntimeError("x"))
        assert "ошибка" in r.status_text()