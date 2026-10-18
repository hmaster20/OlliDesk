# План модернизации OlliDesk

Рабочий документ: сюда попадают задачи по доработке проекта, сгруппированные по фазам.
Обновлять при каждом изменении архитектуры. Актуальная ветка — `slm`.

Легенда статусов: `[ ]` не начато · `[~]` в работе · `[x]` сделано · `[-]` отменено

---

## 0. Диагностика: что уже есть

| Возможность | Статус | Где реализовано |
|---|---|---|
| Чат с Ollama (стрим, reasoning) | ✅ | `agents/ollama_client.py`, `ui/chat/panel.py` |
| Встроенный SLM (GGUF / llama.cpp) | ✅ | `agents/local_llm_client.py`, `agents/download_slm.py` |
| Fallback Ollama → SLM → rule-based | ✅ | `agents/model_router.py` |
| Tool calling (ReAct-цикл) | ✅ | `agents/agent_loop.py`, `agents/tool_registry.py` |
| 16 инструментов (файлы, RAG, web, погода, git, валюты, гипотезы) | ✅ | `tools/` |
| Инструмент дочитывания `@file`-вложения | ✅ | `tools/attached_files.py::read_attached_file` |
| Инструменты гипотез `record/update/list_hypotheses` | ✅ | `tools/hypothesis.py` |
| Команды `@web` / `@dir` / `@file` / `@path` | ✅ | `ui/chat/panel.py`, `ui/main_window.py` |
| Токен-бюджет под окно модели для вложений | ✅ | `core/context_budget.py`, `core/budget.py` |
| Подтверждение инструментов настраивается | ✅ | `core/config.py::ConfirmSettings`, меню File, `_apply_config_policies` |
| RAG: индексация + ChromaDB | ✅ базовый | `fs/indexer.py`, `fs/vector_store.py` |
| Инкрементальная индексация | ✅ | `fs/indexer.py::incremental_scan` |
| Подтверждение инструментов + правка аргументов | ✅ | `ui/dialogs/tool_confirmation_dialog.py` |
| Monaco-редактор + diff | ✅ | `ui/web_editor/` |
| Сессии (SQLite), настройки проекта | ✅ | `state/` |
| Тёмная тема | ✅ | `ui/theme.py` |
| Мастер первой настройки | ✅ | `ui/dialogs/wizard.py` |
| Диспетчер GPU-слоёв для SLM | ✅ | `agents/gpu_utils.py`, `agents/slm_optimizer.py` |
| CI (ruff → mypy → pytest) | ✅ | `.github/workflows/ci.yml` |
| Reranking + гибридный поиск (BM25) | ✅ | `fs/bm25.py`, `fs/reranker.py`, `fs/vector_store.py` |
| **Двухмодельный пайплайн (planner GPU + analyst CPU)** | ✅ | `agents/model_pool.py`, `agents/slm_selector.py`, `ui/chat/threads.py::_ModelPoolLoadThread` |
| **Orchestrator: линейный конвейер (classify → plan → collect → synthesize → verify)** | ✅ | `agents/orchestrator.py`, `agents/evidence.py`, `agents/diagram.py`, `agents/verifier.py`, `agents/critic.py` |
| **Project Intelligence (граф зависимостей, repomap, парсеры Ansible/Terraform/Vagrant/C#/Python)** | ✅ | `fs/intel/` |
| **Research-режим: фазы, гипотезы, нуджи, паспорт исследования** | ✅ | `agents/investigation.py`, `agents/hypotheses.py`, `ui/chat/thought_stream.py`, `ui/chat/research_passport.py` |
| **AGENTS.md как постоянный контекст проекта** | ✅ | `agents/context_loader.py` |
| **OCR изображений / PDF** | ❌ | есть только зависимость `rapidocr-onnxruntime` |
| **MCP** | ❌ | — |
| **Система плагинов** | ❌ | — |
| **Событийная модель сообщений (`MessagePart`)** | ❌ | `ChatMessage.content` — строка, `reasoning_content` и `tool_calls` живут отдельно |
| **Композитный пузырь сообщения** | ⚠️ | для research-режима есть `ChatThoughtStream` (сворачиваемый лог мыслей/вызовов/результатов/гипотез), `ChatHypothesisItem`, `ChatDiagramItem`, `ChatResearchPassport`; для обычных сообщений `ChatMessageItem` остаётся однострочным `QTextBrowser` с курсивным `thinking` |
| **Выполнение shell-команд агентом** | ❌ | — |
| Компактор истории диалога | ✅ | `agents/history_compactor.py` (вызывается в `AgentLoop`) |
| Тесты UI / state | ❌ | сознательно: тесты пишем только под изменяемый код, см. 1.5 |

---

## Фаза 1 — Технический долг (блокирует всё остальное)

### 1.1–1.4 — ЗАВЕРШЕНЫ (детали и коммиты — в истории git)
- [x] **1.1 Версия приложения**: единый источник `core/_version.py::__version__`,
      `bump-my-version` + `git-cliff` настроены и проверены на живом репозитории.
- [x] **1.2 Линтер**: `ruff check .` → 0 ошибок, `ruff format` чист; `black`/`isort` удалены.
- [x] **1.3 Типизация**: `mypy .` → 0 ошибок; строгие правила для `core/agents/fs/state/tools`
      (послабления остались только для `ui/` и `tests/`); chroma-граница вынесена в
      `fs/chroma_adapter.py`.
- [x] **1.4 Мёртвый код**: удалены `unused/`, `ui/unused/`, legacy-потоки,
      `agents/context_compactor.py`, закомментированные блоки.

### 1.5 Тесты — **НИЗКИЙ ПРИОРИТЕТ, по мере надобности** (коммиты `45d3ae4`, `75ee2c3`)
- Задачи не ставим: покрытие ради покрытия не нужно. Тест пишется **только когда
  при доработке меняется код** и без проверки есть риск регрессии. Ничего «дописать
  на будущее» — если код не трогаем, тест не пишем.
- Что есть (писалось попутно с реальными правками, не «для процента»):
  - `tests/tools/test_attached_files.py`;
  - `tests/tools/test_file_ops.py` — 28 тестов, покрытие `tools/file_ops.py` 0% → 95%
    (песочница `_resolve_path`: обход через `..`, коллизия префиксов каталогов, отказ
    записи за пределы проекта). Песочница — граница безопасности, её тестировали
    не «для счётчика», а потому что она защищает файлы пользователя;
  - `tests/core/`, `tests/agents/`, `tests/fs/` — по 1–2 модуля на изменённую область.
- Оставшиеся кандидаты, к которым возвращаться **только вместе с правкой кода**:
  ReAct-цикл (`agents/agent_loop.py`), `SessionStore`/`ProjectSettings`,
  `ToolConfirmationDialog`, markdown→HTML в `message_item`.
- Порог покрытия (`--cov-fail-under`) не вводим — он давит на написание тестов
  ради метрики. В CI достаточно `ruff` → `mypy` → `pytest` (сейчас 189 тестов).
- `pytest-qt` не подключаем, пока не появится задача по тестированию UI.

**Подводный камень, найденный при написании тестов:** `Path.write_text`/`read_text`
без явного `encoding` берут локальную кодировку, а на Windows-консолях это OEM cp866.
`tools/file_ops.py` читает как `utf-8`, поэтому тест, писавший файл через
`write_text("данные")`, получал `U+FFFD` и падал, хотя код был прав. **В тестах всегда
явный `encoding="utf-8"`** — иначе они зависят от кодовой страницы машины.

---

## Фаза 2 — Архитектурные улучшения

### 2.1 Протокол LLM-бэкенда
- [x] Ввести `agents/base.py::LLMBackend(Protocol)` с методами `chat`, `embed`,
      `cancel_request`, `is_cancelled`, `reset_cancel`. (`runtime_checkable` Protocol,
      с async context manager. `check_tools_support` в Protocol **не** вынесен —
      остался методом конкретных реализаций.)
- [~] Типизировать `OllamaClient`, `LocalLLMClient`, `RuleBasedFallback` как `LLMBackend`:
      `AgentLoop` объявлен с `client: LLMBackend`, `RagSearchThread`/`VectorStore` работают
      через протокол `Embedder`; явных `implements LLMBackend` в классах нет (duck-typing
      + Protocol в сигнатурах).
- [ ] Убрать `object` / `Any` из `AgentContext.vector_store` / `.search_cache` — ввести
      минимальные протоколы `VectorStoreLike` / `SearchCacheLike`, чтобы `agents` не импортировал
      `fs` и `core.search_cache`. Сейчас поля так и остались `object`; рядом появилось новое
      `hypotheses: object` и `attached_files: object` — задача расширяется на них.
- [ ] `AgentLoop` сейчас знает про `fs`/`core` только по duck-типингу — это источник
      неочевидных `None`-ошибок.

### 2.2 Реестр инструментов
- [~] Перенести список инструментов из `ui/chat/panel.py::_build_registry` в
      `tools/registry.py` (модуль с декларативным списком), чтобы UI не был единственным
      местом регистрации. **`tools/registry.py::ALL_TOOLS` создан, но не используется —
      `_build_registry` по-прежнему импортирует инструменты по одному.**
- [ ] Политики — из `core/config.py::AppConfig.tool_policies`, а не из UI-словаря.
      Сейчас `_apply_config_policies` читает `tool_policies` и `confirm`, но
      приоритет декоратора (в коде `ASK_FIRST` у `write_file`) всё ещё не переопределён
      конфигом — только явные `set_policy` из UI и `_CHAT_AUTO_TOOLS`.
- [x] **Исправить баг:** `search_cache` не инжектился в `web_search` — добавлен в
      `_INJECTED_PARAMS` (`agents/tool_registry.py`) и прокинут из `ChatPanel` через
      `AgentContext.search_cache`. Рядом добавлены `attached_files` и `hypotheses`.
- [ ] Убрать `_resolve_path`-песочницу в пользу единого `SandboxedPathResolver`, если
      добавятся новые файловые инструменты.
- [ ] Метаданные `is_mutating` / `category` в `@tool` вместо словаря русских названий
      инструментов в UI — см. **4.8.D** (делать вместе с этим пунктом).

### 2.3 Управление потоками и отменой
- [ ] Единый `ThreadPool`/`TaskManager` вместо независимых `QThread`-классов.
      Сейчас в `ui/chat/threads.py`: `AgentChatThread`, `RagSearchThread`,
      `WebSearchThread`, `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`,
      `_ModelPoolLoadThread`; плюс `IndexingThread`, `ModelCheckThread` в `main_window.py`
      и `PullThread` в `model_manager_dialog.py`.
- [ ] Гарантированная отмена `asyncio`-задач (`asyncio.Task.cancel()`), а не только флага
      `cancel_request()`.
- [ ] Убрать `new_event_loop()` + `run_until_complete` на GUI-потоке в
      `MainWindow._do_check_ollama`, `_save_project_state`, `_stop_all_threads`
      (все три места остались).
- [ ] `GitStatusDelegate._do_refresh` и `PullThread` — убрать блокирующий `subprocess` с GUI-потока.
- [ ] Корректно отменять/удалять потоки при закрытии окна (`closeEvent`).

### 2.4 Разделение `ui/main_window.py` и `ui/chat/panel.py`
- [ ] Оба файла >1000 строк и активно используют приватные атрибуты друг друга
      (`chat_panel._update_mode_tooltips()`, `chat_panel.mode_combo`). Разделить на
      контроллеры/делегаты, общение — только через сигналы.
- [ ] Вынести меню в отдельный модуль (`ui/menus.py`) с командами (`QAction` + `QCommandLine`).

---

## Фаза 3 — RAG (качество поиска) — **ЗАВЕРШЕНА**

- [x] Гибридный поиск (BM25 + RRF), реранкинг кросс-энкодером, кэш реранка, раздельные
      эмбеддинги по типу файла (`category_for_language`), карта проекта + path-aware RAG,
      фильтры `language`/`path`/даты, инкрементальная индексация, кликабельные источники в
      UI. Реализовано в `fs/` и `ui/chat/panel.py`. Дополнительно появился слой `fs/intel/`
      (Project Intelligence Layer): `DependencyGraph`, `RepoMap`, доменные парсеры
      Ansible/Terraform/Vagrant/C#/Python — он питает Orchestrator и не заменяет RAG.

---

## Фаза 4 — Контекст и промпты — **ЗАВЕРШЕНА**

- [x] Компактор истории (`agents/history_compactor.py`), динамический системный промпт с
      бюджетом (`system_prompt_char_budget`), горячая перезагрузка промптов/ролей
      (`core/prompt_watcher.py`), редактор промптов с превью и сбросом к заводским,
      описание режимов в UI. Добавлены `core/budget.py` (единая точка бюджета по `OpType`),
      `agents/context_loader.py` (AGENTS.md как постоянный контекст),
      `agents/investigation.py` (фазы исследования), `agents/hypotheses.py` (гипотезы).

---

## Фаза 4.9 — Версионирование промптов в Git (идея из `Roadmap.md`, требует оценки)

Идея из `docs/Roadmap.md`: «История промтов — сохранять изменения промтов в Git (если проект
под контролем версий), чтобы можно было отслеживать изменения». Сознательно вынесено из
Фазы 4 в отдельный раздел: объём и польза неочевидны, поэтому сначала нужна оценка.

- [ ] **Оценить необходимость доработки и её пользу.** Вопросы для решения:
  - Редактор сейчас правит только **глобальные** промпты (`~/.ollidesk/system_prompts`,
    `~/.ollidesk/roles`), которые не принадлежат репозиторию проекта. Версионирование имеет
    смысл только для проектных промптов (`<проект>/.ollidesk/...`) — значит, редактор надо
    сперва научить их редактировать.
  - Не дублирует ли это обычный `git log` по файлу? Если пользователь и так коммитит проект
    сам, ценность только в удобстве (авто-коммит / просмотр истории из UI).
  - Риск: авто-коммиты из приложения в чужой репозиторий (перемешивание с рабочими
    изменениями, неожиданная запись в индекс). Нужен явный opt-in и коммит только этих файлов.
  - Альтернатива без коммитов: вкладка «История» в редакторе (`git log`/`diff` выбранного файла).
- [ ] (после оценки) Реализовать выбранный вариант: авто-коммит проектных промптов при
      сохранении **или** просмотр истории через `git log`, **или** снапшот через
      `tools/git_ops.py`.

---

## Фаза 4.5 — Модели SLM (отложено из разбора логов)

Идеи из `RECOMEND.md` + рекомендации по моделям из `docs/HARD.md`, которые сознательно
не реализованы в этой итерации.

- [x] **Qwen2.5-14B в каталоге SLM**: `_DEFAULT_SLM_MODELS` содержит три варианта
      `qwen2.5-14b-instruct` (`q4_k_m`, `q5_k_m`, `q6_k`). На HF файл разбит на 2 части
      (~4.5 ГБ), автопроверка частей через `HfApi.list_repo_files`
      (`agents/download_slm.py::_detect_parts_via_hf`) подхватит это сама. Предупреждение
      для UI: на T1200 (4 ГБ VRAM) `detect_optimal_gpu_layers` вернёт `n_gpu_layers ≈ 11`,
      модель загрузится, но ~90 % уйдёт на CPU.
- [ ] Для 14B при нехватке VRAM — уменьшать `n_ctx` до 2048 (экономит KV-кэш);
      связать `n_ctx` с расчётом слоёв в `agents/slm_optimizer.py` (сейчас `optimize()`
      уже подбирает `n_ctx` под бюджет VRAM+RAM, но без явного «14B → 2048» правила).
- [ ] Рекомендация «умнее локальной 7B за счёт tool-use» — проверить `qwen2.5-coder:7b`
      через Ollama как альтернативу: качество выше, VRAM можно отдать через `num_gpu`.
- [ ] **Кодинг-модель Qwen-2.5-Coder-32B-Instruct** (из `HARD.md`): добавить в каталог SLM
      под стратегию «точно» — `Q8_0` (≈34 ГБ RAM), контекст 16k–32k. Полностью на CPU
      (4 ГБ VRAM не хватает даже на разумный offload), 2–5 т/с — приемлемо для разовых
      сложных задач на 128 ГБ RAM. Покрывает Python / C# / Bash / Ansible одной записью;
      альтернатива — `Qwen-3-Coder` соразмерного масштаба.
- [ ] **Reasoning-модель DeepSeek-R1-Distill-Qwen-32B** (из `HARD.md`): добавить в каталог
      SLM под сценарий анализа текстов и финансовых документов (МСФО/РСБУ, отчёты,
      стратегии) — `Q8_0` (≈35 ГБ RAM). Цепочка рассуждений перед ответом + Qwen-база
      дают русский язык; парсинг сырых данных и математика — той же Qwen-2.5-Coder-32B.
- [~] **Квантование малых моделей: Q4_K_M → Q8_0/Q6_K** (из `HARD.md`): в
      `_DEFAULT_SLM_MODELS` уже есть варианты `q5_k_m` и `q6_k` для 7B/14B, Q8_0 —
      пока нет (для 1.5B есть `q8_0`). Сделать Q8_0 частью каталога (или выбором в
      `slm_models_dialog`), чтобы один чекпоинт можно было скачать в двух квантах.
- [x] Температура для дисциплины tool-call: дефолт `_DEFAULT_TEMPERATURE` в
      `LocalLLMClient` снижен до `0.0` (в исходной версии плана было 0.3), добавлен
      `_TEMPERATURE_BY_OP` с разными значениями для `classify`/`plan`/`tool_call`/
      `critique`/`module_summary`/`synthesis`/`regen`. Дублирующий вызов одного инструмента
      частично закрыт инструкцией в `_TOOL_USE_INSTRUCTION` и `max_iterations` в чате.
      Языковая директива: `_LANGUAGE_INSTRUCTION` в начале system-сообщения
      (`LocalLLMClient`) + `_LANGUAGE_DIRECTIVE` в конце промпта (`AgentLoop`) — лечит
      дрейф Qwen2.5 на китайский. Для Ollama (`ChatOptions`) диапазон 0.3–0.5 ещё не
      применён (см. 4.6.B).
- [ ] `@dir` не сбрасывает текущую сессию — это сделано намеренно
      (`_on_directory_attach_requested` не трогает историю чата и `recent_projects`).
      Альтернатива «полный перезаход как `File → Open Project`» — заменить тело метода
      на `self._do_open_project(path)`; решение за пользователем.

---

## Фаза 4.6 — Надёжность ReAct-агента (из `React.md`)

`React.md` — разбор ReAct-цикла с привязкой к файлам. Ниже сведённые задачи с приоритетом.
Обозначения: **Сейчас** — влияет на работоспособность и делается в первую очередь;
**Потом** — улучшает качество, но не блокирует работу; **Позже** — крупные подсистемы.

### 4.6.A — Сейчас — **ЗАВЕРШЕНА**

- [x] Дедупликат вызовов инструментов, лимит Observation, валидация аргументов через pydantic
      (`_build_arg_validator`), структурированный ответ на неизвестный инструмент, защита от
      prompt injection (`<untrusted>`), снапшоты `write_file` только при изменении, запрет
      `.git/**` и `.ollidesk/**` в `_resolve_path`.

### 4.6.B — Потом: качество ответов

- [x] **Step-awareness** (`React.md` 2.1): дописывать в system-промпт «Итерация N из M», на
      последних итерациях — «следующий ответ должен быть Final Answer». Реализовано в
      `agents/agent_loop.py::_build_system_prompt` через `iter_line` с `remaining <= 1`
      и `remaining <= 3`.
- [x] **Не бросать `AgentIterationLimitError`, а делать финальный вызов без tools**
      (`React.md` 2.1): `_final_call_no_tools` вызывается и при повторе tool_calls, и при
      лимите, и при исчерпании нуджей. `AgentIterationLimitError` бросается только если
      сам финальный вызов упал.
- [ ] **Валидация Observation** (`React.md` 2.4): запрет формировать Final Answer без хотя бы
      одного валидного Observation; при пустом/ошибочном результате — требовать другой вызов.
- [ ] **Классификация ошибок инструментов** (`React.md` 3.3): `ValidationError` →
      `fix_arguments`, `httpx.ConnectError` → `retry_later`, `FileNotFoundError` →
      `different_path`, остальное → `report_to_user`. Хвост `retry_hint` в tool-сообщении.
- [ ] **Внешний верификатор после `write_file`** (`React.md` 4.2): `python -m py_compile` для
      `.py`, ошибка возвращается агенту как Observation. Надёжнее LLM-критика.
      (Косвенно закрыто для Orchestrator через `agents/verifier.py`, но для `AgentLoop` —
      нет.)
- [ ] **История tool-сообщений в сессиях** (`React.md` 1.4): колонка `tool_calls_json TEXT` в
      `messages`, `save_message` принимает `ChatMessage` целиком. Сейчас после перезагрузки
      сессии агент не знает, какие инструменты уже вызывались (см. 4.8.A — делать вместе).
- [x] **`slm.n_ctx: 4096` → 8192** (`React.md` 10.1): `SLMConfig.n_ctx` и `target_n_ctx`
      теперь по умолчанию 8192. `_SLMLoadThread` сохранил дефолт параметра 4096, но
      фактическое значение приходит из конфига. У Qwen2.5-7B запас есть.
- [~] **`temperature` для PLAN/AGENT** (`React.md` 1.1): `_TEMPERATURE_BY_OP` в
      `LocalLLMClient` умеет давать 0.0 для `tool_call`/`plan`/`classify`, но `AgentLoop`
      не передаёт `op_type` — механизм есть, но не подключён. `ChatOptions` из
      `AgentLoop` не передаётся вовсе.
- [x] **Подключить сжатие истории** (`React.md` 1.2): вызывать компактор перед каждым
      `client.chat`, иначе история вылетит за `n_ctx` на длинных сессиях. Сделано:
      `agents/history_compactor.py` вызывается из `AgentLoop._compact_history` перед
      отправкой на бэкенд.
- [~] **`ChatMessageItem` рендерит `tool_calls`** (`React.md` 9.4): в research-режиме
      `ChatThoughtStream` показывает вызовы инструментов и их результаты; в обычных
      сообщениях `ChatMessageItem` по-прежнему рендерит только текст/`thinking`.
- [ ] **Кнопка «Продолжить» после исчерпания итераций** (`React.md` 9.3): слот
      `_on_iteration_limit_exceeded` показывает сообщение «Нажмите «Продолжить»», но
      самой кнопки в UI нет — это нужно доделать.
- [ ] **`_CHAT_AUTO_TOOLS` в конфиг** (`React.md` 9.1): `chat_auto_tools: list[str]` в
      `AppConfig`, чтобы новый инструмент не требовал подтверждения в чате «из коробки».
- [x] **Бюджет RAG-контекста** (`React.md` 7.1): `ChatPanel._on_rag_results` и
      `_on_agent_rag_ready` усекают `merged` до `rag_char_budget(self._router.num_ctx)`
      через `truncate_to_chars`.

### 4.6.C — Позже: новые подсистемы

- [~] **Reflexion-критик для Final Answer** (`React.md` 4.1): для Orchestrator это уже
      сделано — `agents/critic.py` (3B-проверка структуры/формата) и `agents/verifier.py`
      (код-проверка путей/символов/evidence) вызываются в `Orchestrator.run()`. Для
      `AgentLoop` (обычные чат-ответы) — по-прежнему не подключено.
- [~] **Structured trace агента** (`React.md` 8.1): в research-режиме есть
      `last_research_summary` и `ChatResearchPassport` с прочитанными файлами, поисками,
      гипотезами. Полноценного трейса (итерация → действие → длина observation →
      длительность) с кнопкой «Показать трейс» — нет.
- [ ] **Метрики реестра** (`React.md` 8.2): `_stats` в `ToolRegistry` (calls/errors/by_tool)
      в статус-бар — сколько ошибок tool-call за сессию.
- [ ] **Эпизодическая память** (`React.md` 7.2): `~/.ollidesk/memory.db` с рефлексиями,
      подгрузка top-3 через существующий `VectorStore`.
- [ ] **Типизированное редактирование аргументов** (`React.md` 9.2): `QTableWidget` вместо
      `key: value` — сейчас `ToolConfirmationDialog` теряет int/list.
- [ ] **`model_digest` в `ModelInfo`** (`React.md` 8.3): инвалидировать `supports_tools`
      при смене версии модели.
- [ ] **Warning о `n_gpu_layers` в UI** (`React.md` 5.3): показывать итоговое число слоёв в
      статус-строке при загрузке SLM. Сейчас `optimize()` пишет в лог, но в UI не выводится.
- [ ] **Multi-tool-call у SLM** (`React.md` 5.1): общий `agents/tool_call_parser.py` уже возвращает
      список вызовов (JSON + function-call), и `AgentLoop._extract_tool_calls_from_text` их
      принимает все. Осталось: `LocalLLMClient._parse_tool_call` сейчас отдаёт только первый
      вызов — перейти на `_parse_tool_calls` (список).

### 4.6.D — Удалить (проверить перед удалением)

- [x] `RouterChatThread` (`React.md` 1.3, 6.3) — **уже удалён** в Фазе 1.4 вместе с
      `OllamaChatThread`; в `ui/chat/threads.py` его нет. Задача закрыта.
- [ ] `ui/dialogs/model_manager_dialog.py::PullThread` — дублирует `_DownloadThread`
      из `slm_models_dialog.py`; объединить.
- [~] Закомментированные блоки в `core/search_cache.py`, `fs/vector_store.py`, `main.py` —
      не восстанавливать, вычистить при рефакторинге. Часть закомментированных строк в
      `main.py` осталась (например, в `_build_system_prompt`), плюс `_OllamaCheckThread`,
      `_SLMLoadThread`, `_SLMDownloadThread` в `threads.py` с несколькими комментариями
      «было / стало» — косметически почистить.

### 4.6.E — Решения, которые нужно подтвердить

- [x] `agents/context_compactor.py`: **удалён** в 1.4 (решение пользователя) — писать заново.
- [ ] Порядок: `React.md` предлагает «день 1 — temperature, compactor, лимит observation,
      agent config». Порядок оставлен автором, реальный приоритет — сначала дедупликат (2.2),
      лимит observation (2.3) и валидация аргументов (3.1): они дают эффект без изменения
      архитектуры.

---

## Фаза 4.7 — Выбор бэкендов и локальных моделей (разбор `docs/AUDIT.md`)

Идеи из аудита, сверенные с кодом и с железом (T1200 4 ГБ VRAM, i7-11800H 8C/16T,
127 ГБ RAM, на диске Qwen2.5 1.5B / 3B / 7B + nomic-embed).

**Актуализация:** двухмодельный пайплайн planner (GPU) + analyst (CPU) уже реализован
через `agents/model_pool.py` + `agents/orchestrator.py` и поднимается из UI
(`ChatPanel._ensure_orchestrator`). Остальные пункты фазы (стратегии, классификатор)
не реализованы.

### 4.7.A — Сделано в этой итерации — **завершено**

- [x] `VectorStore` не завязан на `OllamaClient` (протокол `Embedder`), эмбеддинги перебирают
      бэкенды (`ModelRouter.embed`), конфигурируемый приоритет (`BackendSettings`),
      автоопределение `n_threads`, замеры `load_ms`/`last_stats`.

### 4.7.B — Замеры перед любыми решениями по моделям

- [ ] Прогнать 1.5B / 3B / 7B через `last_stats` и записать реальные `ttft_ms` и
      `tokens_per_sec` до/после смены `n_threads`. От этого зависит, стоит ли вообще
      вкладываться в пул.
- [ ] Проверить, сколько времени занимает **повторная** загрузка той же модели
      (mmap + page cache при 102 ГБ свободной RAM). Оценка аудита «30–120 с на
      переключение» завышена: реальное время, вероятно, 3–10 с, а основная стоимость
      ответа (7B на CPU, 5–10 т/с → 50–100 с на 500 токенов) от выбора модели не зависит.

### 4.7.C — Стратегия выбора: минимум вместо пула

- [ ] **Тумблер стратегии** в правой панели: `быстро / баланс / точно / авто`.
      Это просто маппинг стратегия → `model_id`, ~30 строк, не требует ни пула,
      ни классификатора. Самая дешёвая полезная часть Этапа 1 аудита.
- [ ] `SLMConfig`: добавить `strategy` и `max_loaded_models`. Сейчас каталог
      `available_models`, `selected_model`, `planner_model_id`, `analyst_model_id`
      уже есть — этого достаточно для явного выбора ролей.
- [ ] **Маппинг стратегий из `HARD.md`**: «быстро» → 7B в Q8_0/Q6_K (10–20 т/с);
      «баланс» → 14B; «точно» → Qwen-2.5-Coder-32B-Instruct (Q8_0, ≈34 ГБ, 2–5 т/с) или
      DeepSeek-R1-Distill-Qwen-32B для reasoning. 32B грузится только в стратегии «точно»
      и закрепляется за сессией (см. 4.7.D), в повседневные диалоги не попадает.

### 4.7.D — ModelPool, но аддитивно (не замена `ModelRouter`)

- [x] `agents/model_pool.py` существует: `ModelPool`, `Role` (`PLANNER`, `ANALYST`,
      `ANALYST_PRO`), `RoleSpec`, `add_role()`, `runtime_ctx()`, `pick_role()`, `aclose()`.
      **Не заменяет** `ModelRouter`: `ModelRouter` остаётся основным путём для обычных
      чат-запросов; `ModelPool` живёт только под `Orchestrator` и поднимается лениво.
      `_ModelPoolLoadThread` (в `ui/chat/threads.py`) грузит роли последовательно с per-role
      timeout 90 с.
- [~] **Модель закрепляется за сессией.** `n_ctx` у моделей разный. Сейчас `planner_model_id`
      и `analyst_model_id` хранятся в `SLMConfig` (глобально / per-project), а не per-session.
      В рамках проекта пайплайн пересоздаётся при `reset_pipeline()`. Это близко к
      «закреплению за проектом», но не за сессией.
- [x] `max_loaded=2` фактически: `_ModelPoolLoadThread` грузит ровно 2 роли
      (planner + analyst). `max_gpu_resident=1` тоже выполнено: planner жёстко на GPU
      (`device="GPU"`), analyst — на CPU (`device="CPU"`), `optimize()` для analyst
      принудительно обнуляет `n_gpu_layers`.
- [x] `n_gpu_layers` пересчитывается per-модель через `agents/slm_optimizer.optimize()`
      (VRAM-first с fallback VRAM+RAM; CPU-only cap 8192; отказ от частичного offload <50 %).

### 4.7.E — Классификатор: отложить

- [ ] Понаблюдать за реальным распределением задач (Фаза 4.7.B) **до** появления
      классификатора. Псевдокод классификатора в аудите (§3.3) — это ~30 строк
      регулярок и длин, а не нейросеть.
- [~] Если понадобится: начать **только** с rule-based (`agents/task_classifier.py`),
      без LLM-классификатора. Сейчас `ModelPool.pick_role(task, ...)` делает простейший
      rule-based выбор: `task in {"classify","plan","critique","tool"}` → `PLANNER`,
      `quality_mode and project_files > 500` → `ANALYST_PRO`, иначе `ANALYST`. Отдельного
      `task_classifier.py` нет.
- [ ] Уровни L4/L5 из матрицы аудита на этом железе нереалистичны: 14B/32B/72B
      на диске нет, а 32B Q4 на CPU даёт 1–2 т/с = 4–8 минут на ответ. Матрицу
      применить только к реально установленным моделям.

### 4.7.F — Отклонено

- [ ] **LoRA-адаптеры, Self-RAG, дообучение классификатора, Multi-Agent**
      (Этапы 3–5 аудита): на ноутбуке без GPU для обучения это неделями работы
      без измеримого результата. Feedback loop (👍/👎) — отдельная простая задача,
      имеет смысл раньше всей этой группы.
- [ ] **«Переключение за ~100 мс простой сменой указателя»** (Этап 1, п. 4 аудита) —
      нереалистичная формулировка: смена указателя ничего не стоит, но модель
      должна быть уже загружена. LRU решает именно эту задачу, просто выигрыш
      равен разнице между загрузкой и обращением к прогретой модели, а не 100 мс.

---

## Фаза 4.8 — Событийная модель сообщений и агентный интерфейс (разбор `docs/UI.md`)

`UI.md` — план переработки интерфейса и модели данных по мотивам лучших практик
агентных сред. Ниже сведённые задачи с привязкой к файлам и с проверкой каждого
пункта по текущему коду — часть пунктов в исходном разборе уже частично сделана.

**Главное предупреждение.** 4.8.A — ломающая замена типа `ChatMessage` и `ChatChunk`.
Растягивать её на две итерации нельзя: пока часть кода ждёт `content: str`, а часть
читает `parts`, поведение зависит от того, каким путём прошло сообщение. Один коммит,
один зелёный прогон тестов.

### 4.8.A — Модель данных и события (блокирует 4.8.B)

- [ ] **`MessagePart` вместо `content: str`** (`agents/ollama_client.py:30`): базовый
      класс и наследники `TextPart` / `ReasoningPart` / `ToolCallPart` / `ToolResultPart`,
      у `ChatMessage` — `parts: list[MessagePart]`. Сегодня история диалога — «каша»
      из текста: `reasoning_content` и `tool_calls` либо теряются при сохранении
      сессии, либо пишутся в один `content`.
- [ ] **Событийный `ChatChunk`** (`agents/ollama_client.py:53`): `part: MessagePart | None`
      + `event_type: Literal["start", "delta", "end", "tool_call"]` вместо плоских
      `content` / `reasoning_content`.
- [ ] **Клиенты отдают события**: `OllamaClient.chat()` и `LocalLLMClient.chat()` разбирают
      поток в `reasoning_start` / `reasoning_delta` / `text_delta` / `tool_call`.
      Потребители, которых придётся править: `agents/agent_loop.py` (разбор чанка),
      `ui/chat/threads.py` (четыре места с `chunk.reasoning_content`),
      `ui/chat/panel.py`, `state/session_store.py`, `tests/agents/test_ollama_client.py`.
- [ ] **`SessionStore` хранит `ChatMessage` целиком** (это же — 4.6.B «История
      tool-сообщений в сессиях»): выполнять **вместе** с 4.8.A, иначе `parts` не
      переживут перезагрузку сессии.
- [ ] **Переходный период.** `ChatMessage.content` остаётся read-only-свойством,
      собираемым из `TextPart`, пока на `parts` не переведены
      `local_llm_client._to_llama_messages`, `SessionStore` и сборщики промптов.
      Удалить в том же коммите, что и миграцию потребителей, — «навсегда» оставлять
      нельзя.

### 4.8.B — Интерфейс: композитный пузырь сообщения

- [~] **`ChatMessageItem` → композитный виджет** (`ui/chat/message_item.py`): в
      research-режиме появились композитные виджеты `ChatThoughtStream`,
      `ChatHypothesisItem`, `ChatDiagramItem`, `ChatResearchPassport` — они собираются в
      ленту чата. Сам `ChatMessageItem` по-прежнему однострочный `QTextBrowser`.
- [~] **`ReasoningWidget`** — сворачиваемый блок для `ReasoningPart`. Базовый показ
      есть: `ChatThoughtStream` показывает рассуждения + действия + результаты +
      гипотезы одним сворачиваемым виджетом (в research-режиме); для обычных сообщений
      `ChatMessageItem.update_thinking()` рисует курсивный `QLabel` над текстом.
      Не хватает: тумблера «Показывать размышления», схлопывания по завершении стрима,
      сохранения `ReasoningPart` в `SessionStore` (сейчас теряется).
- [~] **`ToolCallWidget`** — виджет для `ToolCallPart`: в research-режиме
      `ChatThoughtStream.append_action()` / `append_result()` показывают иконку/имя/args/
      preview результата; дифф-вьюер для `write_file` не подключён (есть
      `ui/web_editor/editor_widget.py::show_diff`). В обычном режиме вызовы инструментов
      идут через `_show_activity` + `ChatActivityItem`.
- [~] **Динамическая статус-строка.** `ChatPanel::_on_tool_started` пишет в `status_label`
      название инструмента и аргументы, `tool_status_label` подсвечивается на время
      работы. Не хватает статуса размышлений (по `reasoning_delta`) и показа результата
      инструмента, а не только его старта.

### 4.8.C — Инструмент `run_shell_command`

Решение пользователя: включаем, но в жёстких рамках. Агент без shell не может
проверить `Test-Path`, посчитать строки или запустить тесты — на Windows это заметный
выигрыш. Цена — выполнение произвольных команд от имени пользователя, поэтому
политика по умолчанию `ASK_FIRST`, режим только `AGENT`.

- [ ] **`tools/shell_ops.py::run_shell_command(command: str, cwd: str = "", timeout: int = 30)`**
      — `async`, возвращает `str`, ошибка возвращается строкой с префиксом `Ошибка:`
      (контракт `tools/`, см. §7 `AGENTS.md`).
- [ ] **Только PowerShell**: `pwsh` (PowerShell Core), при его отсутствии —
      `powershell.exe`. `cmd` не использовать: модель на нём пишет команды unix-стиля.
- [ ] **`CREATE_NO_WINDOW`** при запуске — иначе мигают чёрные окна консоли.
- [ ] **Чёрный список** перед запуском: `rm -rf`, `format`, `del /f /s /q`, `rd /s`,
      `Remove-Item -Recurse -Force`, `Set-ItemProperty` / `reg add`, `shutdown`,
      `curl | iex`. При совпадении — вернуть ошибку, а не выполнять.
      ⚠️ Чёрный список **не является границей безопасности**: он обходится через
      `cmd /c`, base64, `powershell -EncodedCommand`. Это защита от ошибок модели,
      а не от злоумышленника — поэтому следующий пункт обязателен.
- [ ] **Границы важнее списка запретов:** `cwd` по умолчанию — корень проекта,
      проверка `Path.resolve().relative_to(project_root)` как в
      `tools/file_ops.py::_resolve_path`, запрет `.git/**` и `.ollidesk/**`
      (согласуется с 4.6.A).
- [ ] **Лимит вывода:** первые ~5000 символов stdout+stderr с пометкой
      «[Вывод обрезан…]». Использовать тот же механизм, что «Лимит размера
      Observation» в 4.6.A, а не новый.
- [ ] **Регистрация и промпт:** `policy=ToolPolicy.ASK_FIRST`,
      `modes=[AgentMode.AGENT]`; в `_CHAT_AUTO_TOOLS` **не** добавлять.
      В `_TOOL_USE_INSTRUCTION` (`agents/agent_loop.py`) описать инструмент и то,
      что для проверки существования путей в Windows нужен `Test-Path`, а не догадки;
      команда выполняется в корне проекта, вывод усечён.
- [ ] **Тесты** `tests/tools/test_shell_ops.py`: черный список отклоняется, выход за
      пределы проекта, таймаут, усечение вывода, `CREATE_NO_WINDOW` на Windows.

### 4.8.D — Метаданные инструментов

- [ ] **`is_mutating` и `category` в `@tool`** (`agents/tool_registry.py::tool`):
      сейчас `_tool_meta` = `name` / `description` / `policy` / `modes` / `func`.
      Добавить `is_mutating: bool = False` и
      `category: Literal["filesystem", "shell", "network", "git", "info"]`.
      Зачем: `ToolCallWidget` сам выбирает значок и цвет, а UI перестаёт держать
      словари с русскими названиями инструментов (`ui/chat/panel.py::_on_tool_started`,
      `_on_tool_call_detected`, `_on_tool_executed` — три отдельных `tool_labels`).
      Значения по умолчанию — `False` и `filesystem`, чтобы не переписывать все 16
      существующих `@tool` разом. Пересекается с **2.2** (декларативный список
      инструментов) — делать вместе.

### 4.8.E — AgentLoop и контекст

- [~] **`AgentLoop` обрабатывает события, а не `current_text`**: в research-режиме
      появились отдельные дорожки `on_thought` (серый поток) и `on_token` (финальный
      ответ); обычный режим по-прежнему стримит всё в `on_token`. Полного набора
      `on_reasoning_delta` / `on_text_delta` / `on_tool_start` / `on_tool_result` —
      нет. Сегодня UI различает токен текста и токен размышления по двум слотам, а
      сообщение для следующей итерации собирается из строки `current_text`.
- [x] **Базовый приоритет сжатия в компакторе истории** (модуль удалён в 1.4, переписан в
      Фазе 4 как `agents/history_compactor.py`): первым опустошается содержимое старых
      результатов инструментов, затем выбрасываются старейшие реплики диалога целиком
      (вместе с tool-вызовами, чтобы не плодить «осиротевшие» tool-сообщения); текст
      текущей реплики — последним и вообще не удаляется.
- [ ] **Первая ступень приоритета — `ReasoningPart`**: пока рассуждения в историю не
      попадают (нет поля), ступень не задействована. Добавить после того, как ReAct-цикл
      начнёт хранить reasoning-части (4.8.A / 4.6.A с лимитом Observation).

---

## Фаза 5 — Мультимодальность и документы

- [ ] Распознавание скриншотов: `rapidocr-onnxruntime` уже в зависимостях — подключить к
      drag&drop в чат (`ChatMessage.images` поле в модели есть).
- [ ] Распознавание документов (PDF, DOCX, PPTX, XLSX, TXT, RTF): извлечение текста и
      включение в контекст чата/агента (проектная база RAG или прикрепление `@file`).
- [ ] Распознавание изображений (JPG, PNG, WebP, BMP): OCR для извлечения текста.
      Поверх OCR — Vision-модели (из `HARD.md`): через Ollama `qwen2.5-vl:7b`
      (GGUF Q4_K_M/Q8_0, 5–9 ГБ) или `llama3.2-vision:11b` (8–12 ГБ); экспериментально
      `qwen2.5-vl:72b` (Q4_K_M, 45–50 ГБ, 1–2 т/с). GPU offload = 0 — 4 ГБ VRAM не
      вмещают мультимодальный контекст. Поле `ChatMessage.images` уже есть в модели —
      отправлять картинку вместе с запросом.
- [ ] Распознавание аудио (MP3, WAV, OGG, M4A, FLAC): транскрипция в текст (Speech-to-Text),
      извлечение текста, анализ по запросу. Стек из `HARD.md`: `faster-whisper` строго на
      CPU (`device="cpu", compute_type="float32"`), модель `large-v3` — приоритетно
      дообученная русскоязычная `whisper-large-v3-russian`; 4 ГБ VRAM не хватает даже на
      Large. Вход — 16 кГц WAV (конвертация через ffmpeg), i7-11800H разбирает час аудио
      за несколько минут.
- [ ] Распознавание видео (MP4, MKV, WebM, AVI): извлечение звуковой дорожки → STT
      (транскрипция), анализ текста, возможность прикрепления ключевых фрагментов.
      Дорожку вытаскивать ffmpeg, дальше — тот же `faster-whisper`, что и в аудио.
- [ ] Кнопка «Скрепка» (прикрепить файлы/пути) в чате — позволяет приложить файлы
      без открытия проекта (работа вне проекта), drag&drop поддержать.
- [ ] Поддержка мультимедиа в прикреплениях: `@file` и прикреплённые файлы, хранение
      метаданных (тип, размер), отображение превью.
- [ ] База мультимедиа-экстрактов: кэширование транскрипций/OCR, чтобы не дублировать
      обработку одних и тех же файлов.
- [ ] PDF/DJVU: извлечение текста (pypdf / pdfplumber) + индексация.
- [ ] Показать пользователю распознанный текст перед отправкой в модель.

Генерация изображений (из `HARD.md`: SD1.5 / SDXL `--lowvram` через ComfyUI / SD WebUI
Forge) — **вне объёма OlliDesk**: отдельный движок и внешние окна. Приложение занимается
только распознаванием (OCR/Vision/STT), синтезом — нет.

---

## Фаза 6 — UX / интерфейс

- [ ] Сдвинуть панель чата влево (из `Roadmap.md`) — сейчас слева дерево файлов.
- [ ] Цвет заголовка окна при тёмной теме: на Windows нативный title bar не наследует
      stylesheet — варианты: frameless window + собственный title bar, либо
      `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)`.
- [ ] Monaco: тема должна следовать приложению (сейчас жёстко `vs-dark`), включить
      `vs` для светлой темы.
- [ ] Иконки-индикаторы в чате: RAG, Tools (файловые), web_search — «что сейчас включено».
- [x] Подсветка `@`-команд в поле ввода (`@web`, `@dir`, `@file`, `@path`) —
      `ui/chat/panel.py::_highlight_web_command`.
- [ ] **Автодополнение и подсказки `@`-тегов** — сейчас есть только цветная подсветка
      (`_highlight_web_command`). Требуется полноценный выбор тега:
  - [ ] реестр команд в одном месте (источник истины), чтобы не дублировать строки в парсере,
        подсветке и подсказках: `_PATH_COMMANDS` + `@web` сейчас разбросаны по коду;
  - [ ] описание команд: `{"@web": "поиск в интернете", "@dir": "прикрепить папку как проект",
        "@file": "прикрепить файл в контекст", "@path": "авто-детект папка/файл"}`;
  - [ ] `QCompleter` с `CompletionMode.PopupCompletion` по модели `QStandardItemModel`
        (колонка `tag` + `hints`), наследующий `QCompleter` — не `PopupCompletion` вручную;
  - [ ] показ всплывающего списка только если `@` стоит **в начале строки** (иначе не мешает
        тексте и e-mail), как сейчас в `_extract_path_command`;
  - [ ] перехват `Tab`/`Enter` на время автодополнения, чтобы не отправлялось полу слово;
  - [ ] автовставка пробела после выбранного тега и позиционирование курсора;
  - [ ] перечислить в UI и теги, которые уже есть, но не документированы в placeholder;
  - [ ] показывать подсказку выбранного тега в placeholder, когда курсор стоит на `@tag`.
- [x] F4 — скрыть/показать панель структуры проекта (только левую панель, редактор не трогаем),
      `ui/main_window.py::_toggle_project_structure`.
- [~] **Доработка отображения рассуждений модели (`reasoning_content`)** — поток
      `thinking_received` уже приходит в панель и **уже виден**:
      `ui/chat/panel.py::_on_thinking` копит токены, `message_item.py` рисует их
      курсивным `QLabel` над текстом ответа. В research-режиме есть
      `ChatThoughtStream` (сворачиваемый лог мыслей/действий/результатов/гипотез).
      Чего не хватает:
  - [x] не смешивать с `content` — рассуждения идут отдельным полем
        `_current_thinking_content` и в `content` не попадают;
  - [x] без попадания в `SessionStore` — `_on_agent_finish` сохраняет только `content`
        (обратная сторона: после перезагрузки сессии рассуждения исчезают, см. 4.8.B);
  - [ ] тумблер в настройках панели агента «Показывать размышления» (сейчас видны всегда);
  - [~] сворачиваемый блок вместо вечно развёрнутого курсива — общий виджет с 4.8.B,
        здесь только тумблер и стили. Для research-режима `ChatThoughtStream` уже
        сворачиваемый; для обычных сообщений — нет;
  - [x] обновлять блок инкрементально во время стрима (уже работает через
        `update_thinking` / `append_thought_token`); схлопывание по завершении —
        по-прежнему нет;
  - [ ] для Ollama включать `think: true` у моделей, которые это умеют
        (`/api/chat`, поле `think`), сейчас поток либо пуст, либо зависит от модели;
  - [ ] для SLM — аналогично, если llama.cpp отдаёт отдельное поле.
- [ ] **Лишние пустые строки в облаке сообщения пользователя** — между текстом вопроса и
      кнопкой «Копировать» виден разрыв. Причина: `render_markdown` оборачивает текст в
      `<div style="line-height:1.5">` и `<p style="margin:8px 0">`, поэтому у
      первого/последнего абзаца остаётся вертикальный отступ внутри пузыря, а
      `ChatMessageItem._adjust_browser_height` добавляет ещё `+4` (и `min-height 22`).
      Варианты: для сообщений пользователя рендерить компактно (обнулять margin у первого
      и последнего блока) либо `setPlainText` без `<p>`, и подгонять высоту без лишних
      отступов.
- [ ] Прогресс индексации: показывать ETA, очередь файлов, возможность отмены.
- [ ] Менеджер сессий: просмотр содержимого по клику, экспорт, «создать проект из сессии»,
      сжатие старых сессий.
- [ ] Экспорт чата в Markdown/HTML.
- [ ] Диалог настроек: визуальный редактор `tool_policies` (чекбоксы AUTO/ASK/EXCLUDED),
      ползунки температуры, top_p, num_ctx. Сейчас во вкладке «Бэкенды LLM» можно
      включить/выключить Ollama, но политики инструментов в UI не редактируются.

---

## Фаза 7 — Расширяемость

- [ ] **MCP** — клиент MCP-серверов (stdio), чтобы системные промпты/инструменты приходили
      извне (`Roadmap.md`).
- [ ] **Система плагинов** для кастомных инструментов: точка входа `tools/plugins/*.py`,
      манифест, изоляция ошибок.
- [ ] Пользовательские команды (`~/.ollidesk/commands/*.md`) как расширение ролей и промптов.
- [ ] Внешние провайдеры LLM: `ModelConfig.provider` уже допускает `openai` /
      `openai-compatible`, но клиента нет.

---

## Фаза 8 — Релиз и качество

- [x] Единая версия из `pyproject.toml` (см. 1.1), рабочий `bump-my-version` + `git-cliff`.
- [ ] `CHANGELOG.md` генерируется и коммитится на каждый релиз.
- [ ] CI: добавить `windows-latest` в матрицу (приложение Windows-only, `CREATE_NO_WINDOW`
      и пути не проверяются на ubuntu).
- [ ] Pre-commit или `make check` в локальном хуке — сейчас `.pre-commit-config.yaml` отсутствует.
- [ ] Релизный пайплайн (GitHub Actions): сборка `OllDesk.exe`, публикация в Releases.
- [ ] Проверить `ollidesk.spec`: актуальные `hiddenimports`, размер, `llama_cpp` и `chromadb`.
- [ ] README: синхронизировать с реальным состоянием (упоминает устаревшее `scripts/download_monaco.py`).

---

## Приоритеты и порядок

1. **Фаза 2.2** (реестр инструментов + баг `search_cache`) — влияет на качество агента.
2. **Фаза 2.1** (протокол бэкенда) — фундамент для Фаз 4–7.
3. **Фаза 6** (UX) — по мере сил, отдельными мелкими задачами.
4. **Фазы 5, 7** — по остаточному времени.

**Отдельно — порядок по локальным моделям** (Фаза 4.7). Ключевая поправка к порядку
фаз: надёжность ReAct (4.6) идёт **до** интеллектуального выбора моделей. Сейчас одна
модель = одно поведение = воспроизводимые баги; пул добавит ось «какая модель была
активна для сообщения N» и усложнит отладку.

1. **4.7.B** (замеры) — без цифр всё дальнейшее гадание.
2. **4.7.C** (тумблер стратегии) — дёшево, не требует пула.
3. **4.7.D** (ModelPool обёрткой, модель закреплена за сессией) — базовая часть уже
   сделана (planner/analyst), осталась стратегия «модель за сессией».
4. **4.7.E** (классификатор) — только если замеры покажут нехватку.

**Отдельно — порядок по интерфейсу агента** (Фаза 4.8). Сначала **4.8.A**
(`MessagePart`), иначе 4.8.B придётся строить виджеты поверх строки `content` и
переделывать дважды. Дальше — 4.8.B (пузырь) и 4.8.C (`run_shell_command`), их можно
параллельно. **4.8.D** — только вместе с **2.2**, **4.8.E** — только после
подключения компактора (Фаза 4).

**Отдельно — `run_shell_command` (4.8.C).** Единственный инструмент, дающий агенту
побочные эффекты вне файлов проекта, поэтому он не в общем списке «дёшевых улучшений»:
4.6.A (лимит Observation, запрет записи в `.git`/`.ollidesk`) уже закрыта — остаётся 2.2
(декларативный реестр), иначе ограничения придётся дублировать вручную.

## Быстрый бэклог (мелочи)

- [x] `ruff` вместо `black`/`isort` — сделано в 1.2: black и isort удалены из обеих групп
      зависимостей и из конфигурации, `ruff format` + правило I001 закрывают обе задачи.
- [ ] **`uv.lock` рассинхронизирован с `pyproject.toml`**: объявлен `llama-cpp-python>=0.3.0`
      (и транзитивный `diskcache`), в локе их нет. Любой `uv run` / `uv sync` молча
      переписывает лок, и правка попадает в случайный коммит. Перегенерировать один раз
      и держать лок в коммитах; причина — в лок собран до добавления SLM-зависимости.
- [x] Дубликат `duckduckgo-search` + `ddgs` — подтвердилось: оба экспортируют `DDGS`
      с разными типами, из-за чего mypy давал `assignment` в `tools/web_search.py`.
      Устаревший пакет удалён из зависимостей (см. 1.3).
- [ ] `pyproject.toml` → `hatchling` вместо `setuptools` (упростит сборку wheel).
- [ ] `.gitignore` игнорирует `Notes.md`/`Roadmap.md`/`Model.md` по имени — файлы уже
      отслеживаются, но новые такие же будут скрыты. Переименовать правила на `/Notes.md`.
- [ ] Добавить `pyright`/`basedpyright` как второй тайпчекер (VS Code).
- [ ] Убрать `.idea/` из репозитория (уже в `.gitignore`, но проверить `git ls-files`).
- [ ] Кэш web-поиска: расширить на RAG-ответы (общий `SearchCache` с namespace).
- [ ] **Целевая версия Python**: `requires-python` ≥ 3.11, а `ruff`/`mypy` нацелены на 3.12
      (`py312`), и в `ui/dialogs/wizard.py` используется `typing.override` (нет в 3.11).
      Решить: поднять `requires-python` до 3.12 или вернуть `py311` + `typing_extensions.override`.
- [x] **Чтение больших файлов фрагментами**: `tools/file_ops.py::read_file(path, offset, limit)`
      уже поддерживает `offset`/`limit` с жёстким диапазоном
      `[MIN_READ_CHUNK_CHARS=4000, MAX_READ_FILE_CHUNK_CHARS=30000]`,
      `DEFAULT_READ_CHUNK_CHARS=12000` и хинт о продолжении в хвосте результата.
      `tools/attached_files.py::read_attached_file` использует тот же механизм.
      Возможность реализована в объёме, достаточном для чтения больших файлов.

---