"""AsyncResource — guard Suspense-паттерна в Qt (2.9).

Зачем: классический `QComboBox.clear() + addItems(...)` на каждое
обновление даёт «вспышку» пустого виджета. Пользователь видит «прыжок»,
если обновление идёт часто (например, при `refresh_models_requested`).
`AsyncResource` решает три задачи:

  1. Разделяет **первую загрузку** (`loading`, нет данных — показать
     плейсхолдер) и **обновление** (`refreshing`, есть `latest` — показать
     старые данные, не мигать).
  2. Даёт единый API для UI: `loaded()` → можно рисовать `latest`,
     `value_or(default)` → безопасный доступ.
  3. Ошибка не теряет предыдущие данные: `state == ERROR` + `latest != None`
     → рендерим `latest` + badge ошибки (UX лучше, чем пустота).

Инварианты:
  * `latest` устанавливается ТОЛЬКО через `set_value()`, не напрямую.
  * Переходы: `IDLE → LOADING → READY/ERROR`; `READY/ERROR → REFRESHING
    → READY/ERROR` (при `begin_load()`).
  * `loaded()` — `True` для `READY` и `REFRESHING`. Для `ERROR` с
    `latest != None` — тоже `True` (данные есть, просто устарели).
  * `value_or(default)` — безопасный геттер, никогда не бросает.

Использование (9.11):

```python
r = AsyncResource[list[str]]()
r.begin_load()           # LOADING
# … фоновая загрузка …
r.set_value(models)      # READY

# UI-рендер:
if r.loaded():
    combo.set_items(r.latest)
else:
    combo.set_placeholder("Загрузка…")

AsyncResource — обычный Python-объект (без QObject): работает и без
QApplication, и в тестах. Сигналы не эмитит — UI сам перерисовывается
в _sync_from_resource(), вызываемый из потока, который обновил
AsyncResource.
"""

from future import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")

class ResourceState(StrEnum):
"""Состояние AsyncResource (2.9)."""

IDLE = "idle" # ничего не загружено (initial / after clear)
LOADING = "loading" # идёт загрузка, latest может быть None
READY = "ready" # latest актуален
REFRESHING = "refreshing" # идёт обновление, latest — предыдущий
ERROR = "error" # ошибка; latest может быть None или предыдущим

@dataclass
class AsyncResource(Generic[T]):
"""Контейнер с состоянием и latest-значением (2.9).

Attributes:
state: Текущее состояние.
latest: Последнее успешно полученное значение (может быть None
до первой успешной загрузки).
error: Последняя ошибка (None, если ошибки не было).
last_updated_at: perf_counter() успешной загрузки — для
диагностики «когда данные устарели».
"""

state: ResourceState = ResourceState.IDLE
latest: T | None = None
error: BaseException | None = None
last_updated_at: float = 0.0

------------------------------------------------------------------
Переходы состояний
------------------------------------------------------------------
def begin_load(self) -> None:
"""Начинает загрузку/обновление.

Если данных ещё нет (latest is None) → LOADING.

Если данные уже есть → REFRESHING (не мигать пустотой).

Вызывается до запуска фоновой загрузки.
"""
self.error = None
if self.latest is None:
self.state = ResourceState.LOADING
else:
self.state = ResourceState.REFRESHING

def set_value(self, value: T) -> None:
"""Устанавливает новое значение (успешная загрузка).

Переводит состояние в READY и очищает ошибку.
"""
import time

self.latest = value
self.error = None
self.state = ResourceState.READY
self.last_updated_at = time.perf_counter()

def set_error(self, exc: BaseException) -> None:
"""Фиксирует ошибку.

Если latest уже есть — состояние ERROR, но данные
сохраняются (UI может рендерить stale-value + badge).

Если данных нет — ERROR без latest.

Args:
exc: Исключение (для отображения и логирования).
"""
self.error = exc
self.state = ResourceState.ERROR

def clear(self) -> None:
"""Полный сброс (например, при смене проекта).

Возвращает к IDLE, обнуляет latest/error.
"""
self.state = ResourceState.IDLE
self.latest = None
self.error = None
self.last_updated_at = 0.0

------------------------------------------------------------------
Предикаты и безопасный доступ
------------------------------------------------------------------
def loaded(self) -> bool:
"""True, если UI может рисовать latest.

READY / REFRESHING — да.

ERROR с latest != None — да (stale-value).

IDLE / LOADING / ERROR без latest — нет.

Правило guard-паттерна: если loaded() — рендерим latest,
иначе — плейсхолдер.
"""
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
"""Возвращает latest или default.

Никогда не бросает. Даже если loaded() == False — вернёт
default (для случая, когда UI рисует плейсхолдер-значение).
"""
return self.latest if self.latest is not None else default

------------------------------------------------------------------
Диагностика
------------------------------------------------------------------
def status_text(self) -> str:
"""Человекочитаемый статус (для статус-бара / tooltip)."""
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
err = type(self.error).name if self.error else "?"
return f"ошибка: {err}"
case _:
return str(self.state)

all = ["AsyncResource", "ResourceState"]