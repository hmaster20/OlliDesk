"""AsyncResource — guard Suspense-паттерна в Qt (2.9)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class ResourceState(StrEnum):
    """Состояние AsyncResource (2.9)."""

    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    REFRESHING = "refreshing"
    ERROR = "error"


@dataclass
class AsyncResource(Generic[T]):
    """Контейнер с состоянием и latest-значением (2.9)."""

    state: ResourceState = ResourceState.IDLE
    latest: T | None = None
    error: BaseException | None = None
    last_updated_at: float = 0.0

    def begin_load(self) -> None:
        """Начинает загрузку/обновление."""
        self.error = None
        if self.latest is None:
            self.state = ResourceState.LOADING
        else:
            self.state = ResourceState.REFRESHING

    def set_value(self, value: T) -> None:
        """Устанавливает новое значение (успешная загрузка)."""
        self.latest = value
        self.error = None
        self.state = ResourceState.READY
        self.last_updated_at = time.perf_counter()

    def set_error(self, exc: BaseException) -> None:
        """Фиксирует ошибку."""
        self.error = exc
        self.state = ResourceState.ERROR

    def clear(self) -> None:
        """Полный сброс."""
        self.state = ResourceState.IDLE
        self.latest = None
        self.error = None
        self.last_updated_at = 0.0

    def loaded(self) -> bool:
        """True, если UI может рисовать latest."""
        if self.state in (ResourceState.READY, ResourceState.REFRESHING):
            return True
        if self.state == ResourceState.ERROR and self.latest is not None:
            return True
        return False

    def is_loading_first_time(self) -> bool:
        """True, если идёт первая загрузка (latest is None)."""
        return self.state == ResourceState.LOADING and self.latest is None

    def is_refreshing(self) -> bool:
        """True, если идёт обновление поверх существующих данных."""
        return self.state == ResourceState.REFRESHING

    def has_stale_error(self) -> bool:
        """True, если есть ошибка, но данные можно рисовать."""
        return self.state == ResourceState.ERROR and self.latest is not None

    def value_or(self, default: T) -> T:
        """Возвращает latest или default."""
        return self.latest if self.latest is not None else default

    def status_text(self) -> str:
        """Человекочитаемый статус."""
        match self.state:
            case ResourceState.IDLE:
                return "не загружено"
            case ResourceState.LOADING:
                return "загрузка…"
            case ResourceState.READY:
                return "готово"
            case ResourceState.REFRESHING:
                return "обновление…"
            case ResourceState.ERROR:
                err = type(self.error).__name__ if self.error else "?"
                return f"ошибка: {err}"
            case _:
                return str(self.state)


__all__ = ["AsyncResource", "ResourceState"]