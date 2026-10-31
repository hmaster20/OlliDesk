"""Чтение/запись каталога SLM-моделей (9.3.3).

Каталог моделей (`~/.ollidesk/slm_models.yaml`) отделён от `config.yaml`:
`config.yaml` стал нечитаемым, а список моделей меняется независимо от
остальной конфигурации. Здесь — только I/O и валидация, без Qt.

Формат файла:

```yaml
schema_version: 1
models:
  - id: qwen2.5-coder-14b-instruct-q4_k_m
    repo: Qwen/Qwen2.5-Coder-14B-Instruct-GGUF
    file: qwen2.5-coder-14b-instruct-q4_k_m.gguf
    display_name: Qwen2.5-Coder-14B (Q4_K_M)
    size_mb: 9000
    kind: chat
    n_ctx: 32768
    tier: 3
    tags: [chat, coder]
    description: Основная coder-модель для профиля «Разработка».
    recommended_profile: development
    moe: false
    active_params_b: 0
```

load_slm_models при отсутствии файла создаёт его из встроенного каталога
core.config._DEFAULT_SLM_MODELS. merge_with_defaults добавляет новые
встроенные модели в существующий файл (при обновлении приложения),
сохраняя пользовательские записи.
"""

from future import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import ValidationError

from core.config import SLMModelInfo
from core.utils import get_app_data_dir

SCHEMA_VERSION: int = 1
FILENAME: str = "slm_models.yaml"

def _default_path() -> Path:
"""Путь к каталогу моделей по умолчанию."""
return get_app_data_dir() / FILENAME

def load_slm_models(path: Path | None = None) -> list[SLMModelInfo] | None:
"""Читает каталог моделей из YAML.

Args:
path: Явный путь (по умолчанию ~/.ollidesk/slm_models.yaml).

Returns:
Список моделей или None, если файла нет (вызывающий код
должен решить: fallback на _DEFAULT_SLM_MODELS или создать
файл через :func:save_slm_models).

Raises:
ValueError: Если YAML синтаксически битый.
"""
p = path or _default_path()
if not p.exists():
return None
try:
with open(p, encoding="utf-8") as f:
data = yaml.safe_load(f)
except yaml.YAMLError as e:
raise ValueError(f"Ошибка парсинга YAML {p}: {e}") from e
if not isinstance(data, dict):
raise ValueError(f"Ожидался dict, получено {type(data).name} в {p}")

version = int(data.get("schema_version", 0))
if version > SCHEMA_VERSION:
logger.warning(
f"slm_models.yaml: schema_version={version} > "
f"поддерживаемой {SCHEMA_VERSION}. Пробую прочитать как есть."
)

raw_models = data.get("models") or []
if not isinstance(raw_models, list):
raise ValueError(f"'models' должен быть списком, получено {type(raw_models).name}")

models: list[SLMModelInfo] = []
for raw in raw_models:
try:
models.append(SLMModelInfo(**raw))
except (ValidationError, TypeError) as e:
logger.warning(f"slm_models.yaml: пропускаю невалидную запись {raw!r}: {e}")
logger.info(f"slm_models.yaml: загружено {len(models)} моделей из {p}")
return models

def save_slm_models(
models: list[SLMModelInfo],
path: Path | None = None,
) -> None:
"""Записывает каталог моделей в YAML (атомарно).

Args:
models: Список моделей.
path: Явный путь (по умолчанию ~/.ollidesk/slm_models.yaml).
"""
p = path or _default_path()
p.parent.mkdir(parents=True, exist_ok=True)
payload: dict[str, Any] = {
"schema_version": SCHEMA_VERSION,
"models": [m.model_dump(mode="json") for m in models],
}
tmp = p.with_suffix(p.suffix + ".tmp")
with open(tmp, "w", encoding="utf-8") as f:
yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
tmp.replace(p)
logger.debug(f"slm_models.yaml: сохранено {len(models)} моделей в {p}")

def merge_with_defaults(
loaded: list[SLMModelInfo],
defaults: list[SLMModelInfo],
) -> list[SLMModelInfo]:
"""Добавляет новые встроенные модели, сохраняя пользовательские.

Встроенные модели (по id) обновляются метаданными из defaults —
чтобы после bump version в каталоге появились новые поля (tags,
description и т.п.). Пользовательские (id которых нет в defaults)
сохраняются как есть.

Args:
loaded: Модели из slm_models.yaml (пользовательский файл).
defaults: Встроенный каталог (_DEFAULT_SLM_MODELS).

Returns:
Объединённый список в порядке: сначала defaults (обновлённые),
затем пользовательские, которых нет в defaults.
"""
default_ids = {m.id for m in defaults}
loaded_by_id = {m.id: m for m in loaded}

result: list[SLMModelInfo] = []
for d in defaults:

# Встроенная модель: если в файле была — мержим новые поля поверх
# пользовательского значения (уважаем то, что пользователь мог
# отредактировать, например display_name).
user = loaded_by_id.pop(d.id, None)
if user is not None:
merged = d.model_copy(
update={
"display_name": user.display_name or d.display_name,
"size_mb": user.size_mb or d.size_mb,
"n_ctx": user.n_ctx or d.n_ctx,
"tags": list(dict.fromkeys(d.tags + user.tags)),
}
)
result.append(merged)
else:
result.append(d)

# Пользовательские модели (которых нет в defaults) — как есть.
for m in loaded_by_id.values():
result.append(m)
return result

def ensure_slm_models_file(
defaults: list[SLMModelInfo],
path: Path | None = None,
) -> list[SLMModelInfo]:
"""Гарантирует, что файл каталога существует и содержит все модели.

Логика:

файла нет → пишет defaults;

файл есть → мержит с defaults (см. :func:merge_with_defaults),
перезаписывает файл (обновляет метаданные встроенных моделей).

Args:
defaults: Встроенный каталог (обычно из _DEFAULT_SLM_MODELS).
path: Явный путь.

Returns:
Итоговый список моделей (тот, что записан в файл).
"""
p = path or _default_path()
loaded = load_slm_models(p)
if loaded is None:
logger.info(f"slm_models.yaml: создаю из встроенного каталога ({len(defaults)} моделей)")
save_slm_models(defaults, p)
return list(defaults)

merged = merge_with_defaults(loaded, defaults)

Перезаписываем, только если появились новые модели или изменилось
число (например, defaults расширился после bump).
if [m.id for m in merged] != [m.id for m in loaded] or len(merged) != len(loaded):
save_slm_models(merged, p)
return merged

all = [
"FILENAME",
"SCHEMA_VERSION",
"ensure_slm_models_file",
"load_slm_models",
"merge_with_defaults",
"save_slm_models",
]