# AGENTS.md — OlliDesk

Руководство для ИИ-агентов (и разработчиков), работающих в этом репозитории.

**OlliDesk** — локальный AI-ассистент для Windows 10: десктоп-IDE на PySide6 (Qt6), в котором
общаешься с локальными LLM через **Ollama** или встроенный **SLM** (GGUF / llama.cpp),
с RAG-поиском по проекту, вызовом инструментов и встроенным редактором Monaco. Для
исследовательских вопросов используется двухмодельный пайплайн **planner (GPU) + analyst (CPU)**
через `Orchestrator`; обычные запросы идут через ReAct-цикл `AgentLoop`.

- Актуальная рабочая ветка: **`slm`** (в ней реализована встроенная модель SLM).
- Пакетный менеджер: **uv** (`uv.lock` в репозитории), но `Makefile` использует `.venv` + `pip`.
- Целевая платформа: **Windows 10/11**. Приложение запускается как `python main.py`.
- **Целевое состояние 2.0** (EventBus, ToolSpec v2, Policy Engine, Application Services,
  AsyncResource, HF search, `slm_models.yaml`, `git_context`, `apply_patch`, `run_tests`,
  verifier в AgentLoop, Coding Bench) описано в **`docs/PLAN_MODERNIZATION.md`** —
  фазы 0.1, 2.5–2.9, 9.x, 10. В этом руководстве пункты, которых **ещё нет в коде**,
  помечены **`[план X.Y]`** со ссылкой на раздел плана. Не считай их реализованными.

---

## 1. Быстрый старт

```bash
# Разовая установка (создаёт .venv, ставит пакет + dev-зависимости,
# скачивает Monaco и SLM)
make setup

# Запуск
python main.py

# Проверки
make lint          # ruff check .
make test          # pytest tests/ -q --tb=short
make check         # lint + test
mypy .             # CI также гоняет mypy (строгий режим)
```

Полезно:

```bash
python -m ui.web_editor.download_monaco   # скачать Monaco в ui/web_editor/vendor
python -m agents.download_slm             # скачать GGUF-модель в ~/.ollidesk/models
uv sync                                              # пересобрать окружение из uv.lock
uv run ruff check --fix .                           # авто-фикс линтера
uv run pytest tests/core -q                         # точечный прогон тестов
```

Сборка standalone: `pyinstaller --clean ollidesk.spec` или `build.bat`.

---

## 2. Структура репозитория

```
OlliDesk/
├── main.py                  # Точка входа: телеметрия, Qt-логгер, excepthook → core.app.main()
├── pyproject.toml           # Метаданные, зависимости, конфиги ruff/mypy/pytest
├── uv.lock                  # Фиксация зависимостей
├── Makefile                 # setup / install / lint / test / check / clean
├── build.bat                # Windows-сборка PyInstaller
├── ollidesk.spec            # Конфигурация PyInstaller
├── cliff.toml               # Конфигурация git-cliff (генерация CHANGELOG)
│
├── core/                    # Инфраструктура: конфиг, логирование, промпты, роли, бюджет, безопасность
│   ├── services/            # [план 2.8] Application Services (без Qt)
│   ├── event_bus.py         # [план 2.5] EventBus (QObject + сигнал event_emitted)
│   ├── events.py            # [план 2.5] Pydantic-схемы событий
│   ├── policy.py            # [план 2.7] Policy Engine (effective_policy)
│   ├── redaction.py         # [план 0.1.2] Redaction секретов
│   └── slm_models_io.py     # [план 9.3.3] load/save/validate slm_models.yaml
├── agents/                  # LLM-клиенты, ReAct, Orchestrator, ModelPool, investigation, evidence
│   └── hf_search.py         # [план 9.3.1] Поиск моделей на HuggingFace
├── tools/                   # Инструменты (@tool), декларативный список tools/registry.py
│   ├── test_ops.py          # [план 10.2] run_tests
│   └── plugins/             # [план 7]   Plugin API (после ToolSpec v2 + Policy Engine)
├── fs/                      # Индексация проекта + векторное хранилище ChromaDB (RAG)
│   └── intel/               # Project Intelligence: граф зависимостей, repomap, доменные парсеры
├── state/                   # Персистентность: SQLite-сессии, настройки проекта
├── ui/                      # Qt-виджеты: главное окно, чат, диалоги, веб-редактор
│   ├── chat/                # Панель чата, потоки, сообщения, thought-stream, паспорт исследования
│   ├── dialogs/             # Мастер настройки, настройки, модели, SLM, сессии, промпты
│   ├── state/               # [план 2.9] AsyncResource[T] (guard idle/loading/ready/refreshing/error)
│   ├── widgets/             # AutoScrollArea, StatusDashboard, PipelineSelectors, ProfilesSelector,
│   │                        # CommandCompleter, CommandPalette, CollapsedPlaceholder
│   │                        # [план 2.5] LiveEventsPanel
│   └── web_editor/          # Monaco (QWebEngineView) + QWebChannel-мост
├── tests/                   # pytest: tests/{core,fs,agents,state,tools,ui}
├── docs/                    # Документация (см. §10)
├── .ollidesk/               # Локальная конфигурация OlliDesk для ЭТОГО проекта (tracked)
└── .github/workflows/ci.yml # CI: ruff → mypy → pytest → codecov
```

Файлы пользователя, которые **не** в репозитории, но важны:

- `~/.ollidesk/config.yaml` — глобальный конфиг (`AppConfig`).
- `~/.ollidesk/slm_models.yaml` — **`[план 9.3.3]`** каталог моделей (сейчас `_DEFAULT_SLM_MODELS` в `core/config.py`).
- `~/.ollidesk/models/` — GGUF-модели.
- `~/.ollidesk/system_prompts/`, `~/.ollidesk/roles/` — промпты и роли.
- `~/.ollidesk/sessions.db`, `~/.ollidesk/audit.db` (**`[план 0.1.1]`**) — сессии и аудит инструментов.
- `<проект>/.ollidesk/` — конфиг проекта, `project_state.json`, `vector_db/`, `graph/`, `rules.md`.

История остаётся в git.

---

## 3. Архитектура и направление зависимостей

```
                 ┌──────────┐
                 │  main.py │  bootstrap + Qt message handler
                 └────┬─────┘
                      ▼
                 ┌──────────┐
                 │ core/app │  OlliDeskApp: QApplication, тема, конфиг/мастер
                 └────┬─────┘
                      ▼
        ┌─────────────────────────────┐
        │      ui/main_window.py      │  MainWindow — корень композиции
        │  (владеет всеми сервисами)  │
        └───┬──────────┬──────────┬───┘
            │          │          │
            ▼          ▼          ▼
      ┌──────────┐ ┌─────────┐ ┌──────────────┐
      │ ui/chat  │ │ ui/dlg  │ │ ui/web_editor│
      │ (panel,  │ │ (7 шт.) │ │ (Monaco)     │
      │ threads) │ └─────────┘ └──────────────┘
      └────┬─────┘
           │
           ▼
    ┌────────────────┐   ┌──────────────┐
    │ agents/ (LLM,  │◄──│ tools/ (@tool)│
    │ ReAct, router, │   └──────────────┘
    │ Orchestrator,  │          │
    │ ModelPool)     │          │
    └───────┬────────┘          │
            │                   │
            ▼                   ▼
    ┌────────────────┐   ┌──────────────┐
    │ fs/ (индекс +  │   │ state/ (SQLite│
    │ ChromaDB +     │   │  + YAML)     │
    │ intel)         │   └──────────────┘
    └───────┬────────┘
            └────────┬────────┘
                     ▼
              ┌─────────────┐
              │ core/       │  config, logger, prompts, roles,
              │             │  model_registry, search_cache, budget,
              │             │  security, exceptions
              │             │  [план 2.5] event_bus, events
              │             │  [план 2.7] policy
              │             │  [план 2.8] services
              │             │  [план 0.1.2] redaction
              └─────────────┘
```

Правило: **`core` не знает ни о ком**, кроме себя. `agents`/`fs`/`state` знают только `core`.
`tools` знает `core` + `agents.tool_registry`. `ui` знает всё. Импорты **абсолютные**
(`from core.app import main`), не относительные.

**`[план 2.5]` EventBus** встраивается как отдельный слой: `agents`/`fs`/`ui` эмитят события
(`tool.*`, `rag.search.*`, `llm.*`, `phase.changed`, `index.*`, `slm.*`), а `ui/widgets/live_events.py`
и `StatusDashboard` подписываются. `core` не зависит от `agents`/`fs`, поэтому `EventBus` живёт
в `core/`, а конкретные эмиттеры — в своих слоях.

**`[план 2.7]` Policy Engine** становится единой точкой авторизации инструментов:
`core/policy.py::effective_policy(tool_spec, profile, security, config)` — приоритет
`security > profile > config > decorator`. `ToolRegistry.execute` вызывает её до выполнения.

**`[план 2.8]` Application Services** (`core/services/*`) выносят бизнес-логику из `ChatPanel`/
`MainWindow` в чистые Python-классы без Qt. UI вызывает сервис и подписывается на сигналы.

**`[план 2.9]` AsyncResource** — guard для Qt-виджетов, читающих асинхронные данные; состояние
`idle/loading/ready/refreshing/error`, `loaded() = state in ("ready","refreshing")`.

---

## 4. Пакеты: что где лежит

### `core/` — инфраструктура (синхронный код, без Qt)

| Модуль | Назначение | Ключевое |
|---|---|---|
| `core/app.py` | Bootstrap приложения | `OlliDeskApp`, `main()`; применяет тему, открывает `SetupWizard` при отсутствии конфига |
| `core/config.py` | Конфигурация (PyYAML + pydantic) | `AppConfig`, `ModelConfig`, `SLMConfig`, `SLMModelInfo`, `ConfirmSettings`, `BackendSettings`, `RerankSettings`, `EmbedModels`, `ConfigLoader`, глобальный `config_loader`; енамы `ModelRole`, `ToolPolicy`, `AgentMode`. `DEFAULT_INDEX_EXTENSIONS` (широкий список: C#/Java/Go/…, `~/.ollidesk/config.yaml`), `ConfigLoader._migrate_config` поднимает `schema_version` v1 → v2 и расширяет устаревший дефолтный `index_extensions` (пользовательский список не трогает). `AppConfig.chat_auto_tools: list[str]` — инструменты, выполняемые в CHAT без подтверждения (переопределяется проектным `.ollidesk/config.yaml`) |
| `core/config_v3.py` | Схема конфига v3 | `ConfigV3`, `SecurityCfg`, `ModelsCfg` (`planner`/`analyst`/`analyst_pro`), `ContextCfg` (`policy=dynamic`), `AgentCfg` (`default_flow=linear`, `deep_mode`), `ProjectIntelligenceCfg`, `ToolsPolicyCfg` |
| `core/config_migrate.py` | Миграция конфига v2 → v3 | `migrate()`, `migrate_file()`; backup `config.yaml.v2.bak`; для CPU-analyst жёстко ограничивает `n_ctx` (8192), раскладывает planner/analyst по `device`/`n_gpu_layers` |
| `core/budget.py` | Единая точка расчёта контекстного бюджета | `OpType`, `OUTPUT_RESERVE`, `SAFETY_TOKENS`, `ARCH_SHARES`, `ApproxCounter`, `BudgetPlan`, `plan_budget()`, `trim_to_tokens()` |
| `core/context_budget.py` | Оценка токенов и бюджет контекста под окно модели | `estimate_tokens`, `resolve_num_ctx`, `attached_char_budget`, `system_prompt_char_budget` (`SYSTEM_PROMPT_SHARE`), `rag_char_budget`, `KNOWN_NUM_CTX`, `READ_CHUNK_CHARS`, `MAX_READ_FILE_CHUNK_CHARS` |
| `core/security.py` | Локальность и защита от утечек | `NetworkMode`, `SecurityPolicy`, `OUTBOUND_ALLOWLIST`, `assert_outbound_allowed()`, `redact_query()`, `is_neutral_query()`; hygiene для `web_search`. **`[план 0.1.1]`** — добавить `allow_arbitrary_shell: bool = False` (dangerous mode). **`[план 0.1.2]`** — `core/redaction.py` как отдельный модуль, `redact_query` переиспользует его |
| `core/logger.py` | Логирование | `setup_logger(dir, level)` — sink в stderr + rotating-файл. **`[план 2.5]`** — дополнительный loguru-sink для `level >= INFO` эмитит `EventBus.event_emitted("log", …)` |
| `core/_version.py` | Версия приложения | `__version__` — единственный источник для кода; синхронизируется с `pyproject.toml` через `bump-my-version` |
| `core/utils.py` | Пути | `get_app_data_dir()` → `~/.ollidesk`; `get_resource_path()` с учётом `_MEIPASS` |
| `core/exceptions.py` | Иерархия исключений | `OlliDeskError` + `ConfigError`, `OllamaConnectionError`, `ToolsNotSupportedError`, `ModelNotFoundError`, `IndexingError`, `EmbedderError`, `ToolExecutionError` |
| `core/system_prompts.py` | Системные промпты режимов chat/plan/agent | `SystemPromptManager`, модульный `DEFAULT_PROMPTS`, `default_prompt(mode)`, `clear_cache()`; шаблоны `{{переменная}}` (`project_name`, `language`, `project_root`, `project_map` — см. `ChatPanel._template_context`), проектный → глобальный поиск |
| `core/roles.py` | 13 ролей-персон (Markdown + YAML-frontmatter) | `RoleManager(roles_dir=None)`, `RoleDefinition`, `reload()`, `default_templates()` |
| `core/prompt_watcher.py` | Горячая перезагрузка промптов и ролей | `PromptWatcher` (watchdog `Observer` + debounce 0.4 с): на изменение `*.md` в `system_prompts/` и `roles/` зовёт `prompt_manager.clear_cache()` + `role_manager.reload()`; `start()/stop()/is_running`. Поднимается в `MainWindow`, гасится в `closeEvent` |
| `core/model_registry.py` | Каталог моделей + поддержка tool-calling | `ModelRegistry`, `ModelInfo` |
| `core/search_cache.py` | Кэш результатов web-поиска и порядка реранка (SQLite, TTL 24 ч) | `SearchCache.get()/set()`, `get_list()/set_list()` (JSON-список, ключ реранка с префиксом `rerank\|`) |
| `core/event_bus.py` | **`[план 2.5]`** Шина событий | `EventBus(QObject)` с сигналом `event_emitted(str, dict)`, `emit(kind, payload)`, `subscribe(kind, callback)`, `clear()` (для тестов) |
| `core/events.py` | **`[план 2.5]`** Схемы событий | Pydantic-модели: `rag.search.started/found/empty`, `llm.request.sent/first_token/generation.done`, `tool.started/finished/error`, `phase.changed`, `index.started/progress/finished`, `slm.loading/loaded/failed`, `log` |
| `core/policy.py` | **`[план 2.7]`** Policy Engine | `EffectivePolicy`, `effective_policy(tool_spec, profile, security, config)` — приоритет `security > profile > config > decorator`; дефолт по capability (`fs.read` → auto, `fs.write` → ask_first, `net` → ask_first (кроме info-инструментов), `shell` → excluded/ask_first по профилю, `git` → ask_first (кроме `get_git_status`), `secrets` → excluded) |
| `core/redaction.py` | **`[план 0.1.2]`** Redaction секретов | `redact(text) -> str`: API-ключи (`sk-…`, `hf_…`, `ghp_…`), JWT, пароли из URL, приватные ключи (`-----BEGIN … PRIVATE KEY-----`), env-переменные с секретными именами. Используется в логах, tool-аргументах и результатах |
| `core/slm_models_io.py` | **`[план 9.3.3]`** Каталог моделей в YAML | `load_slm_models(path) -> list[SLMModelInfo]`, `save_slm_models(models, path)`, `validate_slm_models(list[dict]) -> list[SLMModelInfo]`; `schema_version`; миграция из `_DEFAULT_SLM_MODELS` при первом запуске |
- **`core/services/`** (2.8) — сервисы без Qt:
  - `memory_service.py` — эпизодическая память (4.3).
  - `tool_service.py` — policy → snapshot → execute → audit (2.8, 0.1.1, 10.9).
  - `project_service.py` — кэш `ProjectIntel` + git_context (2.8).
  - `model_service.py` — config + `resolve_park`/`resolve_coder` (2.8).
  - `chat_service.py` — `build_agent_context` + `pick_max_iterations` (2.8).

### `agents/` — работа с LLM

| Модуль | Назначение |
|---|---|
| `agents/base.py` | `LLMBackend` — `runtime_checkable` Protocol: `chat()`, `cancel_request()`, `is_cancelled`, `reset_cancel()`, `embed()`, async context manager. Используется `AgentLoop`, `ModelRouter` и UI-потоками |
| `agents/ollama_client.py` | Wire-типы (`ChatMessage`, `ChatChunk`, `ToolCall`, `ChatOptions`, `ToolSchema`) + `MessagePart` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`, discriminated union по `kind`) + асинхронный HTTP/stream-клиент Ollama (`OllamaClient`). `ChatMessage.content`/`.reasoning_content`/`.tool_calls` и `ChatChunk.content`/`.reasoning_content`/`.tool_calls` — read-only `@property`, собираемые из `parts`; `model_validator(mode="before")` конвертирует старый стиль (`content=/reasoning_content=/tool_calls=`) в `parts` (если `parts` уже передан — старые поля игнорируются). `_chunks_from_ollama_message` разбирает `message` на отдельные `ChatChunk` (reasoning → text → tool_call, `event_type="delta"/"tool_call"`) |
| `agents/local_llm_client.py` | Клиент llama.cpp (GGUF): блокирующий инференс обёрнут в `asyncio`. **Стриминг по токенам реализован** (`[9.5]`): `_generate` эмитит `ChatChunk(part=TextPart, event_type="delta")` на каждый токен, `event_type="start"` в начале, `event_type="end"` в конце; tool_calls парсятся на полном тексте и эмитятся одним `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`. `_convert_messages` подаёт `reasoning_content` обратно модели в обёртке `<thought>…</thought>`; `generate()` для structured output, `count_tokens()`. Температура по умолчанию `_DEFAULT_TEMPERATURE = 0.0`, есть `_TEMPERATURE_BY_OP` (`classify`/`plan`/`tool_call`/`critique`/`synthesis`/`regen`), языковая и форматная директивы ставятся в начало системного сообщения |
| `agents/model_router.py` | Выбор бэкенда по приоритету из конфига с fallback: rule-based в конце. Учитывает whitelist `backends.enabled`, порядок `backends.priority`, `embed_backend`; отдаёт `num_ctx` активного клиента |
| `agents/model_pool.py` | `ModelPool`, `Role` (`PLANNER`, `ANALYST`, `ANALYST_PRO`), `RoleSpec`; `add_role()` — последовательная загрузка одной роли (используется `_ModelPoolLoadThread` с per-role timeout 90 с); `runtime_ctx()`, `pick_role()`, `aclose()` |
| `agents/orchestrator.py` | Линейный пайплайн: `classify` → `plan` → детерминированный `collect` (`ProjectIntel` + `EvidenceStore`) → `budget` → `synthesize` (7B) → `render_architecture` (код) → `merge` → `critique` + `verify` → при провале `regen`. Deep-mode для больших проектов, `RunResult`. **`[план 10.7]`** — для `task in {"write_code","refactor_plan","bug_analysis"}` расширяется `apply_patch → run_tests → fix_loop → verify → critique` |
| `agents/agent_loop.py` | ReAct-цикл: сборка системного промпта → стрим → вызов инструментов → повтор. `AgentLoop`, `AgentContext`. Работает через `MessagePart`: `isinstance(part, ReasoningPart/TextPart/ToolCallPart)` → dispatch в `on_thinking`/`on_token`/`pending_tool_calls.extend(calls)`. Низкоуровневые события `on_text_delta`/`on_reasoning_delta`/`on_stream_start`/`on_stream_end` проброшены до `ChatPanel` через `AgentChatThread`; `_on_stream_started_event` / `_on_stream_finished_event` обновляют tok/s в `StatusDashboard`. `_RETRY_HINTS` дописывает подсказку по `error_category` в tool-сообщение; валидация Observation (`attempted_calls > 0 and successful_observations == 0` → `_OBSERVATION_NUDGE`). Research-режим: фазы, гипотезы, нуджи, отдельные дорожки `on_thought`/`on_token`, принудительный финальный синтез без tools при повторах/лимите/исчерпании нуджей. **`[план 10.4]`** — после `_final_call_no_tools` вызывать `agents.verifier.verify(...)`; при провале — одна регенерация с `regen_hint` |
| `agents/investigation.py` | Состояние и фазы исследования: `Phase` (`RECON`, `DEEP_DIVE`, `SELF_CHECK`, `SYNTHESIS`), `InvestigationState` (включая `attempted_calls`/`successful_observations`), `is_research_question()`, `phase_for()`, `pick_nudge()`, эвристики entry-point/config, защита от застревания на одном файле |
| `agents/hypotheses.py` | `HypothesisStore`, `Hypothesis`; статусы `open/verified/refuted/partial/inconclusive`; `render_for_prompt()`, `count_open()`, `summary()` |
| `agents/evidence.py` | `EvidenceStore`, `EvidenceItem`; компактные цитируемые доказательства, `pack_for_prompt()`, SQLite-персист, `ids_referenced()`, `all_paths()` |
| `agents/diagram.py` | `render_architecture()`: ASCII и Mermaid-схемы строятся КОДом из `DependencyGraph`, без рисования моделью; `Diagram` |
| `agents/critic.py` | Быстрая проверка структуры/формата ответа: `Critique`, `REQUIRED_SECTIONS`, `CRITIC_SCHEMA`, `critique()` |
| `agents/verifier.py` | Проверка финального ответа против графа и evidence: пути, символы, пустые блоки, повторы, `unsupported_claims`; `VerifyResult`, `verify()`. **`[план 10.4]`** — подключается в обычный `AgentLoop`, не только в `Orchestrator` |
| `agents/history_compactor.py` | Сжатие истории при >80 % окна контекста. Детерминированное прореживание по приоритету в три ступени: (1) `ReasoningPart` в старых assistant-сообщениях → `REASONING_STUB`; (2) содержимое старых tool-результатов → `TOOL_RESULT_STUB` (через `ChatMessage.with_content`); (3) старейшие реплики целиком. После каждой ступени — досрочный выход, если влезли в бюджет. `messages_tokens` учитывает `reasoning_content` (без этого ступень 1 никогда не сработала бы). `compact_history`, `messages_tokens` |
| `agents/tool_registry.py` | `@tool`-декоратор с `is_mutating` и `category`, генерация JSON Schema из type hints, `ToolRegistry.execute()`. `ErrorCategory` (`FIX_ARGUMENTS`/`DIFFERENT_PATH`/`RETRY_LATER`/`REPORT_TO_USER`) + `ToolResult.error_category`; в `execute()` маппинг исключений (`ValidationError` → FIX_ARGUMENTS, `FileNotFoundError` → DIFFERENT_PATH, `httpx.TransportError`/`ConnectionError`/`TimeoutError` → RETRY_LATER, остальное → REPORT_TO_USER). Валидация аргументов pydantic-моделью до выполнения (инъектируемые параметры исключены из схемы и не могут быть переопределены моделью), неизвестный инструмент → JSON `{"error": "unknown_tool", "requested", "available"}`. **`[план 2.6]`** — `ToolSpec` (immutable dataclass) с `capabilities: frozenset[Literal["fs.read","fs.write","net","shell","git","ui","secrets"]]`, `timeout_s`, `max_output_chars`, `idempotent`; `@tool` принимает эти параметры; `get_spec(name) -> ToolSpec`. **`[план 2.7]`** — `execute` проверяет `effective_policy` до выполнения |
| `agents/tool_call_parser.py` | Извлечение текстовых tool-call из ответа: JSON (`{"name": ..., "arguments": {...}}`) и синтаксис `name("arg")` / `name(key=value)` / `Action:`. Позиционные аргументы сопоставляются со схемой (`schema_param_names`). Общий для `AgentLoop` и `LocalLLMClient` |
| `agents/tool_grammar.py` | `parse_tool_call()`, `looks_like_ascii_junk()`, `should_fallback()` — детектор «каракулей» вместо схемы и сигнал для детерминированного сбора данных кодом |
| `agents/context_loader.py` | Загрузка `AGENTS.md`: `<project>/AGENTS.md` → `<project>/.ollidesk/AGENTS.md` → `~/.ollidesk/AGENTS.md`; склейка с указанием источника |
| `agents/slm_optimizer.py` | `OptimizedParams`, `optimize()`: VRAM-first, fallback VRAM+RAM, выбор `n_ctx`, `n_gpu_layers`, `kv_cache_type`, `flash_attn`, CPU-only cap 8192, отказ от частичного offload <50 % |
| `agents/slm_selector.py` | `select_best_model()`, `select_pair()`: выбор chat-модели по tier + fit; явный выбор planner/analyst из конфига; guard против одинаковой модели на две роли |
| `agents/gguf_reader.py` | `read_gguf_meta()`: чтение метаданных GGUF напрямую (без llama.cpp/CUDA), `GGUFMeta`, расчёт KV-кэша |
| `agents/hardware_profile.py` | `HardwareProfile`, `CPUInfo`, `GPUInfo`, `RAMInfo`; определение CPU/GPU/RAM, `usable_vram_mb`, `threads_for_llm` |
| `agents/lsp_bridge.py` | Опциональный LSP-мост: JSON-RPC 2.0 over stdio, `definition()`/`references()`, `ensure_installed()`, `stop_all()`; не требует `pygls` в runtime. **`[план 10.3]`** — использовать в `ProjectIntel` для call graph и в инструменте `find_references` |
| `agents/rule_based_fallback.py` | Ответы без LLM (регулярки: погода, время, дата) |
| `agents/download_slm.py` | Скачивание/удаление GGUF с HuggingFace, обработка split-GGUF. Локальные проверки не ходят в сеть; HF используется только при скачивании. CLI: `python -m agents.download_slm` |
| `agents/gpu_utils.py` | Опрос `nvidia-smi`, подбор `n_gpu_layers`, `detect_optimal_threads()` (физические ядра), `model_total_size_mb()` для split-моделей |
| `agents/hf_search.py` | **`[план 9.3.1]`** Обёртка над `HfApi.list_models`/`model_info`/`list_repo_files` для HF-поиска моделей |
| `agents/benchmark.py` | `run_benchmark()`, `BENCHMARK_PROMPTS`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`; используется `View → Замер производительности…` |

Контракт бэкенда задан `agents/base.py::LLMBackend` (Protocol):
`chat()`, `embed()`, `cancel_request()`, `is_cancelled`, `reset_cancel()`, async context manager.
Дополнительно `ModelRouter.active_client` и `ModelRouter.num_ctx` дают UI доступ к реальному
окну контекста выбранной модели (Ollama — `ChatOptions.num_ctx`, SLM — `n_ctx`).

`ModelRouter` отвечает и за эмбеддинги: `set_priority()` (порядок бэкендов для чата) и
`set_embed_backend()` приходят из `core/config.py::BackendSettings`. `embed()` перебирает
доступные бэкенды (активный → остальные) и бросает `core.exceptions.EmbedderError`, если
не сработал ни один — так RAG переживает остановку Ollama. `VectorStore` получает
`ChatPanel.router` и принимает любой объект с методом `embed` (протокол `fs.vector_store.Embedder`),
поэтому `fs` не импортирует `agents`.

Метрики `LocalLLMClient`: `load_ms` (время загрузки GGUF) и `last_stats`
(`prompt_chars`, `tokens`, `gen_ms`, `ttft_ms`, `tokens_per_sec`, `cancelled`) —
в лог пишется строка на каждый запрос.

Язык и температура: встроенная SLM склонна к дрейфу языка (Qwen2.5 7B q4 вставлял
китайские слова в русский текст). Защита двойная: `LocalLLMClient._prepare_messages`
ставит `_LANGUAGE_INSTRUCTION` (язык) и `_FORMAT_INSTRUCTION` (Markdown-структура) в самое
НАЧАЛО системного сообщения (там они переживают обрезку `_fit_messages`), а
`_build_system_prompt` добавляет `_LANGUAGE_DIRECTIVE` в конец промпта. Температура по
умолчанию — `_DEFAULT_TEMPERATURE = 0.0`; `_TEMPERATURE_BY_OP` задаёт значения для
`classify`/`plan`/`tool_call`/`critique`/`synthesis`/`regen`. `Orchestrator` передаёт
`op_type` через `generate()`, `AgentLoop` полагается на дефолт клиента.

Инструкция по инструментам для SLM (`LocalLLMClient._build_tool_instruction`) нейтральна к
набору инструментов: единый JSON-формат, примеры (`list_directory`/`read_file`/
`search_codebase`/`web_search`/…), пошаговый рецепт «Как изучить проект» и правила
(вызывать инструменты самому, не выдумывать содержимое файлов).

### `tools/` — инструменты для модели

Каждая функция помечена `@tool(name, description, policy, modes, is_mutating, category)`;
декоратор **только ставит метаданные** (`func._tool_meta`). `tools/registry.py::ALL_TOOLS` —
**декларативный список и фактическая точка регистрации**: `_build_registry` обходит его
и регистрирует всё, кроме `read_attached_file` (только при наличии прикреплённых файлов).

**`[план 2.6]`** `@tool` расширяется параметрами `capabilities: list[str]`,
`timeout_s: int = 30`, `max_output_chars: int = 5000`, `idempotent: bool = False`.
`ToolRegistry.get_spec(name) -> ToolSpec` — единая точка доступа к метаданным.

| Инструмент | Модуль | Политика по умолчанию | Режимы | async | capability (`[план 2.6]`) |
|---|---|---|---|---|---|
| `read_file`, `list_directory` | `file_ops.py` | auto | chat, plan, agent | нет | `fs.read` |
| `write_file` | `file_ops.py` | **ask_first** | agent | нет | `fs.write` |
| `apply_patch` **`[план 10.1]`** | `file_ops.py` | **ask_first** | agent | нет | `fs.write` |
| `search_codebase` | `codebase_search.py` | auto | chat, plan, agent | да | `fs.read` |
| `web_search` | `web_search.py` | ask_first* | chat, plan, agent | да | `net` |
| `get_weather` | `weather.py` | auto | chat, plan, agent | да | `net` |
| `get_current_datetime` | `current_datetime.py` | auto | chat, plan, agent | нет | `info` |
| `get_exchange_rate`, `get_exchange_rates_multi` | `exchange_rate.py` | auto | chat, plan, agent | да | `net` |
| `get_git_status` | `git_ops_tool.py` | auto | chat, plan, agent | нет | `git` |
| `git_commit` **`[план 9.4]`** | `git_ops_tool.py` | ask_first | agent | нет | `git` |
| `create_snapshot`, `undo_last_snapshot` | `git_ops_tool.py` | **ask_first** | agent | нет | `git` |
| `read_attached_file` | `attached_files.py` | auto | chat, plan, agent | нет | `fs.read` |
| `record_hypothesis`, `update_hypothesis`, `list_hypotheses` | `hypothesis.py` | auto | chat, plan, agent | нет | `info` |
| `run_shell_command` | `shell_ops.py` | **ask_first** | agent | да | `shell` (dangerous mode) |
| `run_tests` **`[план 10.2]`** | `test_ops.py` | **ask_first** | agent | да | `shell` |
| `find_references` **`[план 10.3]`** | `codebase_search.py` | auto | chat, plan, agent | да | `fs.read` |

Особенности:
- Все инструменты возвращают `str` (или `dict` с `_llm_text` — для UI-метаданных). **Не бросают
  исключения** — ошибка возвращается строкой с префиксом `Ошибка:` (чтобы модель могла её
  прочитать и исправиться). `ToolResult.metadata` отдельно от `content` — для diff-карточки
  `write_file`.
- **Классификация ошибок.** `ToolRegistry.execute()` при ошибке выставляет
  `ToolResult.error_category` (`ErrorCategory`: `FIX_ARGUMENTS` / `DIFFERENT_PATH` /
  `RETRY_LATER` / `REPORT_TO_USER`) по типу исключения. `AgentLoop._create_tool_result_message`
  дописывает `_RETRY_HINTS[error_category]` в конец tool-сообщения (после усечения, чтобы
  подсказка не потерялась).
- **Метаданные инструмента.** `is_mutating` (по умолчанию `False`) и `category`
  (`filesystem`/`shell`/`network`/`git`/`info`, по умолчанию `filesystem`) — для UI-подсказок
  (`ToolCallCard._ICONS`). Аннотированы: `write_file` (filesystem, mutating),
  `tool_create_snapshot`/`tool_undo_snapshot` (git, mutating), `get_git_status` (git),
  `web_search`/`get_weather`/`get_exchange_rate`/`get_exchange_rates_multi` (network),
  `get_current_datetime`/`record_hypothesis`/`update_hypothesis`/`list_hypotheses` (info),
  `run_shell_command` (shell, mutating).
- Зависимости внедряются по имени параметра: `project_root`, `vector_store`, `search_cache`,
  `attached_files`, `hypotheses`. `ToolRegistry` отфильтровывает их из JSON-схемы (см. `_INJECTED_PARAMS`).
- `tools/file_ops.py` использует единый `core.sandbox.SandboxedPathResolver` (проверка
  принадлежности проекту, запрет `.git`/`.ollidesk`). **`[план 0.1.2]`** — расширить `_DENIED_PARTS`
  на `.env`, `*.pem`, `*.key`, `id_rsa*`; доступ к ним — через capability `secrets` с подтверждением.
- `tools/file_ops.py::list_directory` показывает дерево одного уровня: подкаталоги с числом
  элементов и файлы с размером; служебные каталоги (`bin`, `obj`, `node_modules`, `.git` и др.
  из `_SKIP_NAMES`) пропускаются (если указать их явно — показываются).
- `tools/file_ops.py::read_file` читает порциями; `limit` жёстко нормализуется в диапазон
  `[MIN_READ_CHUNK_CHARS, MAX_READ_FILE_CHUNK_CHARS]`, в конце ответа приходит hint о продолжении.
- `tools/file_ops.py::write_file` после записи для `.py` запускает `_verify_python_syntax`
  (`python -m py_compile`, `CREATE_NO_WINDOW`, таймаут 10 с, любые ошибки — тихий пропуск).
  При синтаксической ошибке её текст возвращается модели как часть Observation
  (`⚠️ Синтаксическая ошибка Python (py_compile):\n…`), и модель исправляет файл следующим
  вызовом. Ошибка **не** откатывает запись.
- `tools/codebase_search.py::search_codebase` принимает необязательные `language=`, `path=`
  (префикс пути) и `modified_after=`/`modified_before=` (даты `YYYY-MM-DD`, преобразуются в
  unix-границы дня) — фильтры уходят в `VectorStore.search` (`language`/`path_prefix`/
  `modified_after`/`modified_before`).
- `tools/git_ops.py` — низкоуровневые хелперы (GitPython/stash), `tools/git_ops_tool.py` — `@tool`-обёртки.
  **`[план 9.4]`** — добавить `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`,
  `get_branch_diff`, `get_recent_commits`, `commit`; новый `@tool git_commit` (ask_first, agent).
- `tools/attached_files.py` — хранилище вложений `@file` (`AttachedFileStore.add/find/render`)
  и `read_attached_file(path, offset, limit)`. В промпт уходит только начало и конец файла
  в пределах бюджета `attached_char_budget(...)`; середина читается инструментом по требованию.
  Регистрируется в `_build_registry` только когда вложения есть.
- `tools/hypothesis.py` — `record_hypothesis`, `update_hypothesis`, `list_hypotheses`; хранилище
  инжектируется через `AgentContext.hypotheses`, один экземпляр на сессию чата.
- `tools/shell_ops.py::run_shell_command` — PowerShell-команда в песочнице `cwd`
  (через `core.sandbox.SandboxedPathResolver`: запрет выхода за пределы проекта, `.git`,
  `.ollidesk`). `pwsh` приоритетно, `powershell.exe` fallback; `CREATE_NO_WINDOW` на Windows.
  **Текущий чёрный список** (`_BLACKLIST`: rm -rf, Remove-Item -Recurse -Force, del /f|/s|/q,
  rd /s, format <drive>:, shutdown, reg add, Set-ItemProperty, curl|iwr|Invoke-WebRequest | iex) —
  защита от ошибок модели, **НЕ** от злоумышленника (обходится через `cmd /c`, base64,
  `-EncodedCommand`). `MAX_OUTPUT_CHARS = 5000`, `MAX_TIMEOUT_S = 300`. `policy=ASK_FIRST`,
  `modes=[AGENT]`, `is_mutating=True`, `category="shell"`; в `chat_auto_tools` **не входит**.
  Тесты `tests/tools/test_shell_ops.py`.
  **`[план 0.1.1]`** — заменить `_BLACKLIST` на allowlist (`Test-Path`, `Get-ChildItem`,
  `Get-Content`, `Select-String`, `Measure-Object`, `python -m pytest`,
  `python -m py_compile`, безопасные `git …`); произвольные команды — только при
  `SecurityPolicy.allow_arbitrary_shell=True` (dangerous mode) с явным подтверждением и audit log.
- `tools/web_search_hygiene.py` — hygiene-фильтр web_search: `filter_query()`, режимы
  `off/neutral/ask_first/auto`, redaction и отказ от небезопасных запросов.
- \* `web_search` в декораторе помечен `ask_first`, но **по умолчанию выполняется без диалога**:
  в `AppConfig` есть секция `confirm` (`enabled`, `web_search`), а `ChatPanel::_apply_config_policies`
  ставит `ToolPolicy.AUTO`, если оба флага выключены. Тумблер — меню File →
  «Подтверждать запросы в интернет».
  **Для `web_search` секция `confirm` важнее `tool_policies`:** старые записи
  `tool_policies: {web_search: ask_first}` больше не включают диалог — его включает
  только `confirm.web_search: true` (или `confirm.enabled: true` для всех инструментов).
  Сами запросы проходят через `core/security.py` и `tools/web_search_hygiene.py`.
  **`[план 2.7]`** — приоритет `confirm` сохраняется в Policy Engine.

### `fs/` — индекс проекта и RAG

- `fs/indexer.py` — `FileIndexer`: обход с учётом `.gitignore` (`pathspec`), фильтры по
  расширению/размеру/имени, SHA-256 хэши, чанкинг по строкам с перекрытием, определение языка
  (`_detect_language` — python/js/ts + C#, Java, Go, Rust, C/C++, PHP, Ruby и др.),
  `incremental_scan()` для догрузки только изменений.
  `chunk_file(content, file_path, mtime=None)` кладёт `language` и (если передан) `mtime`
  в метаданные чанка. Игнор-паттерны собираются в один `PathSpec` в порядке возрастания
  приоритета: `config.ignored_patterns` → `.gitignore` проекта → `.ollideskignore` →
  `.indexignore` → `.ollidesk/indexignore` (позже совпавшее правило выигрывает, поэтому
  `!pattern` в явном файле вернёт файл в индекс). Поддерживаются `index_filenames`
  (`Vagrantfile`, `Dockerfile`, `Makefile`, `site.yml`, `playbook.yml`, …). Модели:
  `FileMetadata`, `TextChunk`.
- `fs/vector_store.py` — `VectorStore`: обёртка над ChromaDB (коллекции `codebase`/`codebase_<hash>`,
  косинус), батчи по 32, `add_chunks()`, `search()`, `delete_by_file()`, `clear()`.
  Модели: `SearchResult`, `Embedder` (протокол), `Reranker` (протокол).
  Embedder-параметр — **любой** объект с `async embed(model, texts)`; в UI передаётся
  `ChatPanel.router`. `_embed()` проверяет, что векторов пришло столько же, сколько
  текстов, и бросает `EmbedderError`.
  **Раздельные модели по типу файла**: `category_for_language()` относит markdown/rst/text/txt
  к `docs`, остальное — к `code`; `embed_models` (`{"code": ..., "docs": ...}`, из
  `AppConfig.embed_models`) переопределяет модель категории. Векторы разных моделей
  несравнимы, поэтому **на каждую уникальную модель — своя коллекция** (`codebase` для
  модели по умолчанию, иначе `codebase_<sha1[:8]>`); `add_chunks()` раскладывает чанки по
  коллекциям, `search()` опрашивает все и подаёт выдачи отдельными списками. Без
  `embed_models` — одна коллекция `codebase` (поведение прежнее). Коллекция модели по
  умолчанию доступна как свойство `collection`.
  **Поиск гибридный** (`search()`): векторные выдачи (`_vector_search_lists`, по одной на
  коллекцию; при неудаче эмбеддингов конкретная коллекция молча пропускается) + лексическая
  (`_bm25_search` поверх `fs/bm25.py`, единый индекс по всем коллекциям) объединяются
  через reciprocal rank fusion (`_reciprocal_rank_fusion`, `k=60`, дедуп по `chunk.id`,
  в `score` кладётся RRF-оценка). Поэтому `search()` не падает при недоступном эмбеддере —
  BM25 продолжает находить по токенам. BM25-индекс строится лениво из всех коллекций
  (`_ensure_bm25`, `ChromaCollection.get_all`) и сбрасывается (`_invalidate_bm25`) при
  `add_chunks`/`delete_by_file`/`clear`. `search()` принимает фильтры `language` и
  `modified_after`/`modified_before` (уходят в chroma `where` через `build_where`) и
  `path_prefix` (постфильтр по `file_path`, применяется и к векторной, и к BM25-выдаче).
  Даты хранятся в метаданных чанка как `mtime` (кладёт `FileIndexer.chunk_file`); для
  старых индексов без `mtime` фильтр по дате их отсечёт — нужна переиндексация. После RRF
  опциональный `Reranker` переупорядочивает верхушку `rerank_pool` кандидатов; ошибка
  реранкера не пробрасывается — поиск отдаёт порядок RRF. Порядок кэшируется по ключу
  `rerank|<sha1>` из запроса и содержимого кандидатов (протокол `RerankCache`, реализация —
  `core.search_cache.SearchCache` с `get_list`/`set_list`; при промахе или устаревших id —
  обычный реранк). **`[план 2.5]`** — эмитит `rag.search.started/found/empty` в `EventBus`.
- `fs/bm25.py` — Okapi BM25 без внешних зависимостей (`rank_bm25` не тянем). `tokenize()`
  приводит к нижнему регистру и дробит идентификаторы (snake_case + camelCase,
  `HTTPServer` → `http`, `server`; кириллица через `\w` с `UNICODE`). `BM25Index.build()`
  строит `idf`/`tf`/среднюю длину, `search()` возвращает `(индекс документа, оценка)`.
- `fs/reranker.py` — `CrossEncoderReranker`: опциональный реранкинг RAG кросс-энкодером
  `sentence_transformers.CrossEncoder` (модель по умолчанию `BAAI/bge-reranker-v2-m3`).
  Реализует протокол `fs.vector_store.Reranker`; `VectorStore` прогоняет через него верхушку
  после RRF (`rerank_pool`) и при падении реранкера возвращает порядок RRF. Импорт torch и
  загрузка модели — ленивые (внутри `_load`), выключен по умолчанию (`RerankSettings.enabled`).
  Настраивается в `core/config.py::RerankSettings` (enabled/model/top_k), создаётся в
  `MainWindow._get_reranker()`.
- `fs/project_map.py` — компактная карта структуры проекта и определение каталога из
  запроса. `list_project_files()` переиспользует фильтры `FileIndexer` (без хеширования),
  `render_project_map()` строит дерево каталогов/файлов, `detect_query_scope()` ищет в
  тексте запроса существующий каталог (например, «каталоге Cloud» → `cloud`). Карта
  уходит в системный промпт (`AgentContext.project_map`), найденный каталог сужает
  RAG-поиск (`ChatPanel._query_scope` → `RagSearchThread.file_filter`).
- `fs/chroma_adapter.py` — единственное место, где живёт небезопасная граница с ChromaDB.
  chromadb формально типизирован, но в `Collection.add`/`query` внутри `Union` стоят
  инвариантные `List[...]`, из-за чего корректный `list[list[float]]` mypy отвергает —
  отсюда три `# type: ignore[arg-type]` с пояснениями. Наружу адаптер отдаёт свои типы
  (`QueryPage`, `ChunkMetadata`), снимает внешний уровень вложенности ответа `query()`
  и передаёт `IncludeEnum` вместо строк. `build_where(file_filter, language, modified_after,
  modified_before)` собирает `where` из условий через `$and` (строковый префикс chromadb не
  умеет — он постфильтруется; диапазон дат идёт в `mtime` через `$gte`/`$lte`).
- `fs/intel/__init__.py` — Project Intelligence Layer: `analyze_project()`, `ProjectIntel`,
  `FileRecord`, `Symbol`, `_confidence()`. Детерминированное понимание проекта: LLM не
  читает проект «в лоб», слой строит карту, граф и доказательства.
- `fs/intel/graph.py` — `DependencyGraph`, `GraphNode`, `GraphEdge`; узлы
  `file/module/class/interface/function/method/role/task/resource/module_tf/box/provisioner/var`,
  рёбра `imports/references/uses/depends_on/includes/module_call/notifies/provisions/calls`.
  SQLite-персист, `top_nodes()`, `subgraph()`, `_score()`, инкрементальность по mtime/sha256.
- `fs/intel/repomap.py` — `RepoMap`, `build_repomap()`: L0 (дерево каталогов), L1 (top-символы
  по `graph.score`), L2 (лениво по запросу); `as_prompt_block()`.
- `fs/intel/langs.py` — диспетчер парсеров: tree-sitter → regex/heuristic → только `FileRecord`;
  определение языка по расширению/имени, entry-point кандидаты, сбор `manifests`, `domain`.
- `fs/intel/ansible.py` — доменный анализатор Ansible: роли, tasks/handlers/defaults/vars/
  templates/files, meta-зависимости, playbooks, inventory, group_vars/host_vars, vault.
- `fs/intel/terraform.py` — Terraform/HCL: module/resource/data/variable/output/provider/locals,
  `depends_on`, module calls, `.tfvars`, `.pkr.hcl`.
- `fs/intel/vagrant.py` — Vagrant/Ruby: provision-цепочка (`inline`/`path`), env-переменные,
  boxes/providers/networks/synced_folders, закомментированные provision как техдолг.
- `fs/intel/csharp.py` — C#/.NET/WinForms: namespace, class/interface/struct/enum/record,
  `Program.Main`, `Application.Run`, readiness-гейты, partial-классы, `.csproj`/`.sln`,
  MSTest-сигналы, `AGENTS.md`/`PLAN.md` как контракт.
- `fs/intel/python.py` — Python: AST-парсинг, классы/функции, импорты, доменные флаги
  (`network_gated`, `http_client`, `qt_background_worker`, `fs_watcher`, `db_layer`,
  `config_layer`, `etl_import`), regex-fallback при `SyntaxError`.

Индекс хранится в `<проект>/.ollidesk/vector_db`, состояние — в
`<проект>/.ollidesk/project_state.json` (per-project `ProjectState` в `IndexingController`).

### `state/` — персистентность

- `state/session_store.py` — `SessionStore` поверх sqlite3: таблицы `sessions` и `messages`,
  короткоживущие соединения, ID сессии вида `session_YYYYmmdd_HHMMSS`. Колонка
  `parts_json TEXT` (`ALTER TABLE` идемпотентный, через `PRAGMA table_info`); `save_message`
  пишет `message.parts` в JSON + `content` для обратной совместимости; `get_history` разбирает
  через `TypeAdapter(MessagePart)`, невалидные части пропускаются, битый JSON — fallback на
  `content`. Старые БД без `parts_json` читаются через `content`. Картинки (`images`)
  по-прежнему не сохраняются.
- `state/project_settings.py` — `ProjectSettings` в `<проект>/.ollidesk/project_settings.yaml`
  (модель, режим, температура, max_tokens, `show_reasoning`). `show_reasoning` — per-project
  тумблер показа reasoning в чате; старый YAML без поля читается (дефолт True).
  **`[план 9.4]`** — добавить `git_branch`, `git_hash`, `git_last_sync`.
- `~/.ollidesk/audit.db` — **`[план 0.1.1]`** SQLite-аудит вызовов `run_shell_command` и
  мутирующих инструментов: `timestamp`, `tool`, `args_hash`, `policy`, `approved`,
  `result_hash`, `error`, `dangerous`.

### `ui/` — интерфейс

Композиция (текущая):
```
MainWindow (QMainWindow, ui/main_window.py)
├── QSplitter (горизонтальный, [320, 800])
│   ├── левая панель: заголовок проекта, кнопка реиндексации,
│   │                 QTreeView + GitStatusDelegate, прогресс-бар индексации
│   │                 (скрывается по F4 — View → «Структура проекта»)
│   ├── центральная панель: QTabWidget
│   │   ├── вкладка «Чат»   → ChatPanel (не закрывается)
│   │   └── вкладка «Редактор» → EditorWidget (Monaco)
│   └── правая панель: настройки агента, скрыта по умолчанию (9.2 → поповер AgentSettingsDialog)
└── QStatusBar: путь проекта + StatusDashboard (backend/pipeline/tok/s/RAG/tools)
```

**`[план 9.1]`** — чат перестаёт быть вкладкой: `QStackedWidget` (базовый экран — чат,
при открытых файлах — `QTabBar`+`EditorWidget`); `QTabWidget` авто-скрывается при
`tab_count() == 0`; новый сигнал `EditorWidget.tabs_changed(int)`.

| Модуль | Строк | Назначение |
|---|---|---|
| `ui/main_window.py` | ~1280 | Оболочка, открытие проекта и список последних, оркестрация индексации, меню, переключение темы. Владеет `SessionStore`, `OllamaClient`, `ModelRegistry`, `RoleManager`, `SystemPromptManager`, `FileIndexer`, `SearchCache`. Контроллеры вынесены: `self.file_tree_controller` (`FileTreeController`), `self.indexing_controller` (`IndexingController`), `self.menu_bar` (`AppMenuBar`). `ModelCheckThread` остался в `main_window.py` (одноразовая операция). Обработчик `@dir` (`_on_directory_attach_requested`), F4 (`_toggle_project_structure`), чекбокс `show_reasoning_check` (синхронизируется с `ChatPanel._show_reasoning` при `_do_open_project`), пункт меню «Предзагрузить 2-model pipeline» (`_preload_two_model_pipeline`). **`[план 9.1]`** — `_create_center_panel` перестраивается под «чат — базовый экран»; **`[план 9.2]`** — автопоказ структуры проекта при открытии |
| `ui/chat/panel.py` | ~1500 | Панель чата: выбор роли/модели/режима, стриминг пузырей, подтверждение инструментов, детекция бэкендов, пре-RAG-поиск, команды `@web`/`@dir`/`@file`/`@path`. **Здесь единственное место регистрации инструментов** (`_build_registry` — обходит `tools.registry.ALL_TOOLS`, фильтрует `read_attached_file` по наличию вложений; политики CHAT — из `cfg.chat_auto_tools` с fallback на `_CHAT_AUTO_TOOLS`). `_show_reasoning` (тумблер показа reasoning, `set_show_reasoning`), `continue_btn` + `_continue_after_limit` (кнопка «Продолжить» после исчерпания итераций, сохраняет `_last_run_params`), `_ensure_assistant_widget` (композитный пузырь через `append_text`/`append_reasoning`/`append_tool_call`). Управляет research-режимом, `ChatThoughtStream`, `ChatHypothesisItem`, `ChatResearchPassport`; лениво поднимает двухмодельный пайплайн (`_ensure_orchestrator`, `reset_pipeline`, `_pipeline_status_label`). **`[план 0.1.3]`** — аккумулировать `parts` в `_on_chunk`/`_on_thinking`/`_on_tool_*` → сохранять в `_on_agent_finish` через `ChatMessage(parts=...)` (сейчас пишется только `content`); **`[план 2.5]`** — эмитить `llm.*`/`tool.*` в `EventBus`; **`[план 2.8]`** — перевести на `ChatService`/`ToolService` |
| `ui/chat/threads.py` | ~330 | Мост async ↔ Qt: `AgentChatThread` (роутинг research-вопроса в `Orchestrator`, иначе ReAct `AgentLoop`; при `AgentIterationLimitError` эмитит только `iteration_limit_exceeded` — это не сбой), `RagSearchThread`, `WebSearchThread`, `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`, `_ModelPoolLoadThread` (последовательная загрузка planner/analyst с per-role timeout 90 с), `_BenchmarkThread`. `RagSearchThread.results_ready` отдаёт `(context, [(file_path, start, end), ...])` — структурированные источники RAG. Низкоуровневые сигналы `text_delta` / `reasoning_delta` / `stream_started` / `stream_finished` проброшены из `AgentLoop`; `_on_stream_started_event` / `_on_stream_finished_event` в `ChatPanel` обновляют tok/s в `StatusDashboard` и пишут `logger.trace` |
| `ui/chat/message_item.py` | ~330 | Композитный «пузырь» сообщения: `ReasoningWidget` + `TextWidget` + `ToolCallCard`. Старый API (`ChatMessageItem(role, content, thinking=...)`, `update_content`, `update_thinking`, `.content`/`.thinking`) сохранён; новый — `append_text`/`append_reasoning`/`append_tool_call`/`finalize`. `render_markdown` — вручную, `re` + `pygments`, с поддержкой заголовков/вложенных списков/чек-листов/таблиц/цитат/кода. `ChatActivityItem` — серая строка журнала, `ChatSourcesItem` — кликабельные источники RAG (`source_activated`), `ChatHypothesisItem` — гипотеза с in-place обновлением, `ChatDiagramItem` — ASCII/Mermaid-схема. **`[план 0.1.3]`** — `ReasoningWidget` переходит с `setFixedHeight(240)` на авто-высоту с максимумом 40 % чата + внутренний авто-скролл + `QPropertyAnimation` при разворачивании |
| `ui/chat/thought_stream.py` | — | `ChatThoughtStream`: свёрнутый по умолчанию серый лог рассуждений; `append_thought_token`, `append_action`, `append_result`, `append_hypothesis`, `append_nudge`, `append_phase`, `mark_interrupted`; заголовок обновляется на каждом токене мысли |
| `ui/chat/research_passport.py` | — | `ChatResearchPassport`: финальный паспорт исследования — выводы агента, KPI (файлы/поиски/символы), список прочитанных файлов, гипотезы со статусами, копирование в Markdown |
| `ui/file_tree_delegate.py` | ~160 | Цвета строк дерева файлов по `git status --porcelain`, дебаунс 500 мс, корректный `filePath(index)` и `repo_root` через `git rev-parse --show-toplevel`. `git status` выполняется в `QThreadPool` (`_GitStatusWorker`), `_running`-флаг против накопления одинаковых проверок |
| `ui/file_tree_controller.py` | — | `FileTreeController`: `QTreeView` + `GitStatusDelegate` + операции дерева (new/rename/delete/copy/cut/paste/refresh, контекстное меню, F2/Del/Ctrl+C/X/V/N/R). Публикует `file_open_requested`/`file_renamed`/`file_deleted`/`branch_changed`/`file_diff_requested` |
| `ui/indexing_controller.py` | — | `IndexingThread` + `ProjectState` + `IndexingController`: сигналы `progress`/`finished`/`error`/`summary`; `state_ready` из worker'а, без повторного `scan_project` на GUI-потоке |
| `ui/menus.py` | — | `AppMenuBar(QMenuBar)` — меню + Recent Projects + тумблеры; `set_config()`, `set_recent_projects()`, `_refresh_profiles_menu()` |
| `ui/command_registry.py` | — | `CommandRegistry`, `PaletteCommand` (для `Ctrl+Shift+P`) |
| `ui/theme.py` | ~180 | `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`, `get_colors`, `get_stylesheet` |
| `ui/widgets/auto_scroll.py` | — | `AutoScrollArea`: sticky-to-bottom с порогом 32 px, кнопка «▼ Вниз» |
| `ui/widgets/status_dashboard.py` | — | `StatusDashboard`: backend/pipeline/tok/s/RAG/tools + tooltip'ы |
| `ui/widgets/pipeline_selectors.py` | — | `PipelineSelectors`: ComboBox planner/analyst + «⋯» + индикатор состояния. **`[план 9.3.2]`** — подсветка `coder`-моделей в профиле «Разработка» |
| `ui/widgets/profiles_selector.py` | — | `ProfilesSelector`: ComboBox профилей |
| `ui/widgets/command_completer.py` | — | `CommandCompleter`: QCompleter для `@`-команд |
| `ui/widgets/command_palette.py` | — | `CommandPalette`: QDialog-палитра (`Ctrl+Shift+P`) |
| `ui/widgets/collapsed_placeholder.py` | — | `CollapsedPlaceholder`: виртуализация длинных чатов, порог 80 |
| `ui/widgets/live_events.py` | — | **`[план 2.5]`** `LiveEventsPanel`: сворачиваемая панель под чатом, подписана на `EventBus`, фильтр «только важное», авто-скролл |
| `ui/state/async_resource.py` | — | **`[план 2.9]`** `AsyncResource[T]`: guard `idle/loading/ready/refreshing/error`, `loaded()`, `latest`, `value_or()` |
| `ui/dialogs/wizard.py` | ~260 | Мастер первой настройки (4 страницы). `ignore_errors = true` в mypy |
| `ui/dialogs/settings_dialog.py` | ~240 | Глобальные и проектные настройки индексации; вкладка «Бэкенды LLM» |
| `ui/dialogs/model_manager_dialog.py` | ~245 | Модели Ollama: таблица, sync, `ollama pull` |
| `ui/dialogs/slm_models_dialog.py` | ~295 | Менеджер GGUF-моделей: скачать / удалить / активировать; явный выбор planner/analyst, `auto_select_model`, добавление своей модели. **`[план 9.3.1]`** — вкладка «Поиск HF»; **`[план 9.3.3]`** — запись в `slm_models.yaml` (вместо `config.yaml`) |
| `ui/dialogs/tool_confirmation_dialog.py` | ~152 | Одобрить / отклонить / **отредактировать аргументы** вызова инструмента. Редактирование — `QTableWidget` (Аргумент / Значение / Тип); `_coerce_value(raw, orig_type)` восстанавливает int/float/bool/list/dict/None, при ошибке парсинга — оставляет строкой (никогда не падает). Покрыто `tests/ui/test_tool_confirmation_coerce.py` |
| `ui/dialogs/session_manager_dialog.py` | ~80 | Таблица сессий: открыть, удалить, экспорт, «Создать проект из сессии» |
| `ui/dialogs/prompt_editor_dialog.py` | ~200 | Редактор системных промптов и ролей: вкладки «Редактор»/«Превью» (рендер `{{переменные}}` с примером контекста), кнопка «↩ По умолчанию», сохранение с `clear_cache()`/`reload()` |
| `ui/dialogs/agent_settings_dialog.py` | — | Настройки агента (поповер из ⚙): temperature, max tokens, context window, max iterations, show_reasoning, tools, RAG. **`[план 0.1.1]`** — добавить чекбокс «dangerous mode» для shell |
| `ui/dialogs/benchmark_dialog.py` | — | Замеры производительности (TTFT, tok/s, load), экспорт CSV/Markdown |
| `ui/dialogs/document_preview_dialog.py` | — | Предпросмотр извлечённого текста из документа (OCR-кнопка) |
| `ui/dialogs/export_chat_dialog.py` | — | Экспорт чата (MD/HTML) |
| `ui/dialogs/profile_editor_dialog.py` | — | Редактор профилей использования |
| `ui/web_editor/editor_widget.py` | ~265 | `QWebEngineView` + `PythonBridge` (QWebChannel). `open_file(path, line=0)` открывает файл и (при `line>0`) ставит курсор на строку. `show_git_diff_for_file(path)` — diff HEAD↔рабочая копия. **`[план 9.1]`** — новый сигнал `tabs_changed(int)` |
| `ui/web_editor/editor.js` | ~300 | Monaco: загрузка файлов, diff-режим, статус-бар, `openFile(path, line)` с `revealLineInCenter`, `setTheme(theme)` |

Мост QWebChannel (`PythonBridge`, зарегистрирован как `pythonBridge`):

| Направление | Член | Сигнатура |
|---|---|---|
| JS → Py | `read_file` `@Slot` | `(request_id, path)` |
| JS → Py | `write_file` `@Slot` | `(request_id, path, content)` |
| JS → Py | `set_dirty` `@Slot` | `(file_path, is_dirty)` |
| Py → JS | `file_content_ready` `Signal` | `(str, str)` → `onFileContentReady(...)` |
| Py → JS | `file_write_result` `Signal` | `(str, bool)` → `onFileWriteResult(...)` |
| Py → JS | `file_dirty_changed` `Signal` | `(str, bool)` → `_on_dirty_changed(...)` |

JS выделяет `requestId`, кладёт `resolve` в `pendingRequests` — fire-and-forget превращается в
Promise. Py → JS: `open_file(path, line)`, `show_diff`, `accept_diff`, `reject_diff`, `get_content`,
`setTheme`, `saveFile`, `closeFile`, `renameOpenFile` — всё через `page().runJavaScript(...)`.
Клик по источнику RAG (`ChatSourcesItem.source_activated`) идёт в `MainWindow.open_file_in_editor`,
который открывает файл в редакторе на нужной строке. Diff из карточки `write_file`
(`ToolCallCard.show_diff_requested`) идёт через `ChatPanel.diff_requested` → `MainWindow._on_show_diff`
→ `EditorWidget.show_diff_from_texts(...)`.

---

## 5. Пайплайн запроса

```
Пользователь печатает в ChatPanel
  ├─ @web <запрос>          → WebSearchThread, мимо агента
  ├─ @dir <путь> [вопрос]   → сигнал directory_attach_requested → MainWindow строит
  │                           дерево, VectorStore, запускает индексацию; остаток текста
  │                           уходит обычным сообщением
  ├─ @file <путь> [вопрос]  → AttachedFileStore.add; остаток — обычное сообщение
  ├─ @path <путь> [вопрос]  → авто-детект: каталог → как @dir, иначе как @file
  └─ обычный текст
       └─(Ctrl+Enter или кнопка)→ RAG-пре-поиск (RagSearchThread):
       │     detect_query_scope → file_filter на упомянутый каталог,
       │     дедупликация чанков по файлу, бюджет rag_char_budget(num_ctx);
       │     источники отдаются в UI как ChatSourcesItem (клик → редактор);
       │     эмит rag.search.started/found/empty в EventBus [план 2.5]
       └─ AgentChatThread (QThread → asyncio.run)
           ├─ research-вопрос + Orchestrator готов?
           │   └─ Orchestrator.run(user_query, project_root, history, on_event):
           │       classify(3B) → plan(3B) → collect(код: ProjectIntel + EvidenceStore)
           │       → budget → synthesize(7B) → render_architecture(код)
           │       → merge → critique(3B) + verify(код) → [fail] regen(7B)
           │       → RunResult(answer, ascii_diagram, mermaid_diagram, evidence_ids,
           │                   coverage, unsupported_claims, verify_ok, critique_ok)
           │       ChatPanel: ответ, ChatDiagramItem, краткая сводка evidence
           │   [план 10.7] для task in {"write_code","refactor_plan","bug_analysis"}:
           │     + apply_patch → run_tests → fix_loop → verify → critique
           └─ иначе AgentLoop.run(ctx, history, on_token, on_tool_request, ...):
               ├─ research_mode? on_thought → ChatThoughtStream,
               │                  on_token только для финального ответа → паспорт
               ├─ _build_system_prompt():
               │    фиксированные блоки: инструкция по tool-calling + подсказка режима
               │    + системный промпт режима (SystemPromptManager, {{переменные}})
               │    + блок проекта (имя + корень)
               │    + [план 9.4] git_context (ветка, hash, diff --stat) в development
               │    переменные секции (урезаются в порядке RAG → карта → правила):
               │    + контекст: превью @file-вложений + RAG (VectorStore.search)
               │    + карта структуры проекта + rules.md + AGENTS.md + гипотезы
               │    всё укладывается в system_prompt_char_budget(num_ctx)
               ├─ _compact_history(): прореживание при >80 % num_ctx (reasoning → tool → реплики)
               └─ ModelRouter.chat(...) → OllamaClient | LocalLLMClient | RuleBasedFallback
                   ├─ стрим ChatChunk(part=...) → сигнал chunk_received / thinking_received
                   │    → композитный пузырь (TextWidget / ReasoningWidget)
                   ├─ reasoning_content (ReasoningPart) → сигнал thinking_received
                   └─ tool_calls (ToolCallPart)?
                        ├─ [план 2.7] effective_policy(tool_spec, …) — если excluded, стоп
                        ├─ [план 2.6] capabilities-проверка до выполнения
                        ├─ ToolRegistry.execute() → ToolResult (у ошибок — error_category)
                        ├─ ASK_FIRST: on_tool_request → ToolConfirmationDialog.exec()
                        │                (аргументы можно отредактировать до выполнения)
                        ├─ _create_tool_result_message: _RETRY_HINTS[error_category] в Observation
                        ├─ эмит tool.started/finished/error в EventBus [план 2.5]
                        └─ цикл повторяется, пока max_iterations не исчерпан
```

Ключевые флаги: `AgentMode.CHAT` и `PLAN` — лимит зависит от размера проекта
(`light` ≈ 3 итерации, `medium` ≈ 8, `deep` ≈ 5 с фазами/гипотезами);
`AGENT` — полный `agent_max_iterations` (по умолчанию 30). Research-вопрос в CHAT/PLAN
может идти через фазы `RECON → DEEP_DIVE → SELF_CHECK → SYNTHESIS`, гипотезы и нуджи.
При наличии Orchestrator research-вопрос уходит в линейный пайплайн; при ошибке/таймауте —
fallback на `AgentLoop`.

Если все tool_calls в прогоне упали (`attempted_calls > 0`, `successful_observations == 0`),
`AgentLoop` один раз вклинивает `_OBSERVATION_NUDGE` — модель обязана сменить стратегию,
а не выдавать Final Answer по данным, которых не получала. При исчерпании `max_iterations`
`AgentChatThread` эмитит `iteration_limit_exceeded`, а `ChatPanel._on_iteration_limit_exceeded`
показывает кнопку «Продолжить» (`_continue_after_limit` удваивает лимит, max 200).

**`[план 10.4]`** — после `_final_call_no_tools` вызывать `agents.verifier.verify(...)`;
при провале — одна регенерация с `regen_hint`, второй провал отдаётся с пометкой
`⚠️ не подтверждено: …`.

Пути с пробелами — в кавычках: `@dir "C:\My Folder\Project" изучи структуру`.

Ключевые точки расширения:

| Что менять | Где |
|---|---|
| Системный промпт режима | `~/.ollidesk/system_prompts/*.md` или `<проект>/.ollidesk/system_prompts/` (доступны `{{project_name}}`, `{{language}}`, `{{project_root}}`, `{{project_map}}`) |
| Правила проекта | `<проект>/.ollidesk/rules.md` (`rules_file` в конфиге) |
| Контекст проекта для агента | `<проект>/AGENTS.md`, `<проект>/.ollidesk/AGENTS.md`, `~/.ollidesk/AGENTS.md` (`agents/context_loader.py`) |
| Список и политики инструментов | `tools/registry.py::ALL_TOOLS` (декларативный список) + `ui/chat/panel.py::_build_registry` (применение политик) |
| Параметры индексации | `~/.ollidesk/config.yaml`, `ignored_patterns`, `index_extensions`, `index_filenames`, `chunk_size_tokens` |
| Бэкенд LLM | `agents/model_router.py` + `core/config.py::ModelConfig` |
| Двухмодельный пайплайн / Orchestrator | `agents/orchestrator.py`, `agents/model_pool.py`, `ui/chat/panel.py::_ensure_orchestrator` |
| Project Intelligence | `fs/intel/*` (`analyze_project`, `DependencyGraph`, `RepoMap`, доменные парсеры) |
| Evidence / диаграммы / верификация | `agents/evidence.py`, `agents/diagram.py`, `agents/verifier.py`, `agents/critic.py` |
| Гипотезы исследования | `agents/investigation.py`, `agents/hypotheses.py`, `tools/hypothesis.py` |
| Безопасность web_search | `core/security.py`, `tools/web_search_hygiene.py` |
| Реранкинг RAG | `fs/reranker.py` + `core/config.py::RerankSettings` (`enabled`/`model`/`top_k`) |
| Раздельные эмбеддинги (код/документация) | `core/config.py::EmbedModels` (`code`/`docs`) + `fs/vector_store.py::category_for_language` |
| Бюджет контекста под модель | `core/context_budget.py`, `core/budget.py` |
| Части сообщения (`MessagePart`/`ChatChunk`) | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`) |
| Композитный пузырь (reasoning/text/tool_call) | `ui/chat/message_item.py` (`ReasoningWidget`/`TextWidget`/`ToolCallCard`) |
| События UI / логи → UI | `core/event_bus.py` (**`[план 2.5]`**), `ui/widgets/live_events.py` |
| Политики инструментов (единая точка) | `core/policy.py` (**`[план 2.7]`**) |
| Capabilities инструментов | `agents/tool_registry.py::ToolSpec` (**`[план 2.6]`**) + `@tool(capabilities=...)` |
| Каталог моделей | `~/.ollidesk/slm_models.yaml` (**`[план 9.3.3]`**), `core/slm_models_io.py` |
| Поиск моделей на HF | `agents/hf_search.py` (**`[план 9.3.1]`**) |
| Git-контекст в промпте | `agents/agent_loop.py::_build_system_prompt` (**`[план 9.4]`**) + `ProjectSettings.git_branch/hash` |
| `git_commit` как инструмент | `tools/git_ops_tool.py` (**`[план 9.4]`**), `tools/registry.py::ALL_TOOLS` |
| `apply_patch` вместо `write_file` | `tools/file_ops.py::apply_patch` (**`[план 10.1]`**) |
| `run_tests` | `tools/test_ops.py` (**`[план 10.2]`**) |
| `find_references` (LSP) | `agents/lsp_bridge.py` + `tools/codebase_search.py` (**`[план 10.3]`**) |
| Verifier в обычном AgentLoop | `agents/agent_loop.py::_final_call_no_tools` (**`[план 10.4]`**) |
| Coding Bench | `tests/bench/`, `scripts/run_coding_bench.py` (**`[план 10.5]`**) |

---

## 6. Соглашения по коду

Строго соблюдай существующий стиль — это то, что уже есть в коде.

**Язык**
- Идентификаторы (переменные, функции, классы) — **английский**.
- Комментарии, докстринги, лог-сообщения и весь текст интерфейса — **русский**.
- Докстринги публичного API — в стиле Google (`Args:` / `Returns:` / `Raises:`).
- Комментарии в коде не добавляй без необходимости (см. правило проекта в `.ollidesk/rules.md`).

**Форматирование и линтеры**
- `line-length = 110`. Ruff: `select = E,F,I,N,UP,B,A,C4,PT,RET,SIM`, `ignore = E501, SIM108`.
  `quote-style = "preserve"` — **не меняй вид кавычек** у существующих строк.
- `N802/N806` иногда нарушаются намеренно (методы Qt вроде `resizeEvent`, константы в функциях) —
  не «исправляй» это без причины.
- Black и isort **удалены** из dev-зависимостей. Форматирование и сортировка импортов —
  только `ruff format` и `ruff check --fix` (правило I001).
- Текущий базис: `ruff check .` — 0 ошибок, `ruff format --check .` — чисто, `mypy .` — 0 ошибок
  (`strict = true`, но с послаблениями ниже). Не усугубляй: строгие правила (`disallow_untyped_defs`)
  уже включены для `core/`, `agents/`, `fs/`, `state/`, `tools/`; остаются только `ui/` и `tests/`.
- `requires-python = ">=3.12"`, `ruff`/`mypy` нацелены на `py312`.
  `typing.override` в `ui/dialogs/wizard.py` доступен только с 3.12.

**Типизация**
- mypy `strict = true`, но с послаблениями (`disallow_untyped_defs = false`,
  `disallow_untyped_calls = false`, `disallow_any_generics = false`), `check_untyped_defs = true`.
- Новый код обязан иметь аннотации. ChromaDB и llama-cpp не типизированы — оборачивай
  в `cast(Any, ...)` или `# type: ignore[...]` с пояснением.
- Контракт LLM-бэкенда задан `agents/base.py::LLMBackend` (`runtime_checkable` Protocol);
  базового класса с реализацией нет — поведение держится на duck-типинге и Protocol.
- **`[план 2.6]`** `ToolSpec` — immutable `@dataclass(frozen=True)`; `capabilities` —
  `frozenset[Literal[...]]`, чтобы mypy ловил опечатки в строках.

**Синхронность и потоки**
- `core/`, `state/`, `tools/{file_ops,current_datetime,git_ops*}` — синхронные.
- `fs/`, `agents/`, асинхронные `tools/` — `async def`.
- Qt-код **не смешивать** с async: любой блокирующий вызов оборачивается в `QThread`, внутри
  которого `asyncio.run(self._coro())`. Результаты возвращаются **сигналами**.
- Отмена — кооперативная: `cancel_request()` → флаг, который проверяется в цикле стрима;
  на уровне UI эскалация `quit()` → `wait()` → `terminate()`.
- `local_llm_client.py` — пример моста блокирующего C++-инференса в async
  (`threading.Thread` + `asyncio.Queue` + `run_coroutine_threadsafe` + `Lock`).
- **`[план 2.5]`** подписчики `EventBus` не должны блокировать GUI; тяжёлая работа — в worker'е.

**Логирование**
- `from loguru import logger` на уровне модуля. Никакого `logging.getLogger`, кроме двух
  точечных подавлений в `main.py` (posthog/chromadb).
- Не пиши секреты и полные пути с пользовательскими данными в лог. **`[план 0.1.2]`** —
  все логи проходят через `core.redaction.redact(...)` до отправки в sink/EventBus.
- **`[план 2.5]`** не злоупотребляй `logger.info` для событий, которые и так эмитятся в
  `EventBus`: события — структура, логи — текст.

**Обработка ошибок**
- Слой `ui/` показывает пользователю сообщение; `tools/`/`state/` — возвращают строку/дефолт;
  `agents/ollama_client.py` и `core/config.py` бросают типизированные исключения из
  `core.exceptions`.
- **Не глотай исключения молча** (правило `.ollidesk/rules.md`): минимум `logger.exception(...)`
  или явный возврат ошибки.

---

## 7. Как добавить новый инструмент (канонический путь)

1. **Создать функцию** в `tools/<name>.py`:
   ```python
   from __future__ import annotations
   from agents.tool_registry import tool
   from core.config import AgentMode, ToolPolicy
   from loguru import logger


   @tool(
       name="my_tool",
       description="Краткое описание для LLM.",
       policy=ToolPolicy.ASK_FIRST,  # AUTO | ASK_FIRST | EXCLUDED
       modes=[AgentMode.AGENT],  # CHAT | PLAN | AGENT
       is_mutating=False,  # True — меняет состояние (файлы/git/shell)
       category="filesystem",  # filesystem | shell | network | git | info
       # capabilities=["fs.read"], timeout_s=30, max_output_chars=5000,  # [план 2.6]
   )
   async def my_tool(query: str, project_root: str | None = None) -> str:
       """Первое предложение докстринга попадёт в описание схемы.

       Args:
           query: Что искать.
       Returns:
           Текст результата или строка с ошибкой.
       """
       try:
           ...
           return "ok"
       except Exception as e:
           logger.exception("my_tool failed")
           return f"Ошибка: {e}"
   ```
   - Возвращай `str` (или `dict` с `_llm_text` для UI-метаданных). Никогда не поднимай
     исключение наружу.
   - Зависимости (`project_root`, `vector_store`, `search_cache`, `attached_files`,
     `hypotheses`) добавляй как обычные параметры — реестр отфильтрует их из схемы сам.
2. **Добавить функцию в `tools/registry.py::ALL_TOOLS`** (декларативный список).
   `_build_registry` обойдёт его и зарегистрирует в `ToolRegistry`.
3. **Задать политику** через `policy=` в декораторе (или `self.registry.set_policy(...)`
   в `_build_registry`, если нужен override по конфигу).
4. Проверить: `make lint`, `mypy .`, `make test`.

**Порядок политик** в `_build_registry(mode)`: декоратор → режим агента
(`cfg.chat_auto_tools` в CHAT; встроенный fallback `_CHAT_AUTO_TOOLS` при ошибке чтения) →
`config.yaml` (`tool_policies`, `confirm`) → тумблеры правой панели UI. Пользовательские
тумблеры побеждают конфиг.

**`[план 2.7]`** — весь этот порядок заменится единым `effective_policy(tool_spec, profile,
security, config)`; `_apply_config_policies` уйдёт, а приоритет станет
`security > profile > config > decorator`.

**`[план 2.6]`** — при добавлении инструмента **обязательно** указать `capabilities`;
без этого `Policy Engine` не сможет его авторизовать. Дефолт `capabilities=["fs.read"]`
действует только для legacy-инструментов, новые должны быть явными.

**Регистрация — только через ALL_TOOLS.** Декоратор не добавляет инструмент в реестр
автоматически — это сделано намеренно, чтобы состав инструментов был явным.

## 8. Как добавить UI-элемент

1. Логика — в методе виджета или в пакетном модуле `ui/`; **никакой бизнес-логики в слоте**.
2. Тяжёлое или блокирующее — новый `QThread`-класс в `ui/chat/threads.py` (или рядом с виджетом)
   с сигналами; в `run()` — `asyncio.run(...)`.
3. Доступ между виджетами — через сигналы (`ChatPanel.settings_changed`,
   `MainWindow.theme_changed`), а не прямыми вызовами чужих приватных методов.
4. Тексты интерфейса — по-русски, добавляй в словарь/константы виджета.
5. **`[план 2.5]`** — если действие эмитит событие в `EventBus` (`core/event_bus.py`),
   подписывайся через `EventBus.subscribe(kind, callback)` и **не блокируй GUI**
   (тяжёлые подписчики — в worker'е, UI — только сигналы).
6. **`[план 2.9]`** — если виджет читает асинхронные данные, используй `AsyncResource[T]`
   (guard `loaded()`), чтобы не было пустоты/скачков на `loading`.

## 9. Тесты

```bash
make test                      # весь набор
uv run pytest tests/core -q    # один пакет
uv run pytest -k "test_tool_call" -q
uv run pytest --cov=agents --cov=core --cov=fs --cov=state --cov=tools --cov=ui --cov-report=term-missing
```

Соглашения:
- `asyncio_mode = "auto"` — `@pytest.mark.asyncio` ставится для ясности, но не обязателен.
- Группировка в `class TestXxx:` (без `unittest.TestCase`).
- Изоляция — через встроенный `tmp_path`. Фикстура `temp_project` — в `tests/conftest.py`.
- Моки — `unittest.mock` (`AsyncMock`, `MagicMock`, `patch.object`). `pytest-mock` установлен,
  но не используется.
- HTTP мокается подменой приватной фабрики: `patch.object(client, "_get_client", return_value=mock)`.
- Эмбеддинги — `patch.object(vs.embed_client, "embed", AsyncMock(return_value=[[...]]))`.
- Покрытие: `agents`, `core`, `fs`, `state`, `tools`, `ui`. Фактически тесты есть для
  `core/config.py`, `core/context_budget.py`, `core/system_prompts.py`, `fs/*`,
  `agents/ollama_client.py` (+ `test_ollama_client_parts.py` — `MessagePart` / `ChatChunk`),
  `agents/model_router.py`, `agents/gpu_utils.py`, `agents/tool_registry.py`
  (+ `test_tool_registry_errors.py` — `ErrorCategory` / `is_mutating`/`category`),
  `agents/agent_loop.py`, `agents/history_compactor.py`, `agents/investigation.py`
  (`test_investigation.py` — `attempted_calls`/`successful_observations`),
  `agents/tool_call_parser.py`, `state/session_store.py` (`test_session_store.py` —
  `parts_json` round-trip + миграция), `state/project_settings.py`
  (`test_project_settings.py` — `show_reasoning` round-trip),
  `tools/attached_files.py`, `tools/file_ops.py`, `tools/shell_ops.py`,
  `ui/dialogs/tool_confirmation_dialog.py` (`test_tool_confirmation_coerce.py` —
  `_coerce_value` без `pytest-qt`).

  **Расширенный список (9.x):** `test_auto_scroll.py`, `test_theme_tokens.py`,
  `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`,
  `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`,
  `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`,
  `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py`,
  `test_sandbox.py`.

  **`[план]`** — новые тесты по фазам: `test_event_bus.py` (2.5),
  `test_policy.py` (2.7), `test_tool_spec.py` (2.6), `test_async_resource.py` (2.9),
  `test_history_restore.py` (0.1.3), `test_redaction.py` (0.1.2),
  `test_shell_allowlist.py` (0.1.1), `test_slm_models_io.py` (9.3.3),
  `test_hf_search.py` (9.3.1), `test_coding_bench.py` (10.5).

  Новый код без теста — неоконченная работа; особенно это касается `Orchestrator`,
  `ModelPool`, `investigation`, `evidence`, `diagram`, `verifier`, `critic`,
  `EventBus` (**`[план 2.5]`**), `Policy Engine` (**`[план 2.7]`**), `apply_patch`
  (**`[план 10.1]`**), `run_tests` (**`[план 10.2]`**).

## 10. Документация

| Файл | О чём |
|---|---|
| `README.md` | Быстрый старт, стек |
| `docs/USER_GUIDE.md` | Руководство пользователя |
| `docs/PLAN_MODERNIZATION.md` | **План доработки проекта** (актуальные задачи, приоритеты, статус; фазы 0.1, 2.5–2.9, 9.x, 10) |
| `docs/Roadmap.md` | Идеи и заметки по развитию |
| `docs/Model.md` | Заметки по моделям |
| `docs/DEPENDENCY.md` | Управление зависимостями |
| `docs/VERSION.md` | Релизный процесс (SemVer, git-cliff, bump) |
| `docs/Notes.md` | Заметки по окружению и командам |
| `docs/SECURITY.md` | **`[план 0.1.1]`** Политика безопасности: shell allowlist, dangerous mode, redaction, audit log |
| `docs/BENCHMARK.md` | **`[план 10.5]`** Результаты замеров TTFT/tok/s/load |
| `docs/CODING_BENCH.md` | **`[план 10.5]`** Метрики Coding Bench (pass@1, tool success, invalid args) |
| `.ollidesk/rules.md` | Правила проекта, инжектируются в агента |
| `.ollidesk/config.yaml` | Конфигурация OlliDesk для этого репозитория |

При изменении архитектуры или процесса разработки обновляй `AGENTS.md` и
`docs/PLAN_MODERNIZATION.md` — это единственный источник правды по структуре.

## 11. Git и коммиты

- Конвенция — Conventional Commits (настроена в `cliff.toml`):
  `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `upd:`, `add:`.
  Сообщения — по-русски, с указанием области через `|`: `chore: SLM - загрузка | стабилизация`.
- Версия — SemVer (`docs/VERSION.md`). Сейчас `0.1.2` в `pyproject.toml`.
  Единственный источник для кода — `core/_version.py::__version__`, его поднимает
  `bump-my-version bump patch|minor` вместе с `pyproject.toml`, ставит тег
  `v{new_version}` и генерирует `CHANGELOG.md` хуком `git cliff`. Вручную не менять.
- Коммит не делать без явной просьбы пользователя. Перед коммитом проверить
  `git status`, `git diff`, `git log --oneline -10`; в индекс класть только нужные файлы.
- CHANGELOG генерируется `git cliff --tag v<version> -o CHANGELOG.md`, теги — аннотированные (`-a`).

## 12. Подводные камни

1. **Qt-объекты только в GUI-потоке.** Никогда не трогай виджеты из `QThread` — только через
   сигналы.
2. **Версия — только в `core/_version.py::\__version__`.** В `pyproject.toml` версия
   живёт для упаковки, и её поднимает `bump-my-version` вместе с `_version.py`.
   `importlib.metadata` в рантайме **не** используется: метаданные пакета обновляются
   только при переустановке и сразу после `bump` отдают старую версию.
3. **`ConfigLoader` — глобальный синглтон** (`core/config.py`, модульный `config_loader`).
   В тестах переопределяй `loader.global_config_path`, а не читай глобальный конфиг.
   Схема v3 (`core/config_v3.py`, `core/config_migrate.py`) сосуществует с v2; не смешивай
   `AppConfig` и `ConfigV3` без явной миграции.
4. **`@file` попадает в контекст не целиком.** Промпт получает только начало и конец файла
   в пределах `attached_char_budget(ModelRouter.num_ctx)`; середина — через
   `read_attached_file`. Не возвращай «просто склей полный текст».
5. **`@dir` переключает каталог, но не сессию.** `MainWindow._on_directory_attach_requested`
   намеренно не трогает историю чата и `recent_projects` — в отличие от `_do_open_project`.
6. **ChromaDB тянет телеметрию.** `main.py` подменяет `sys.modules["posthog"]` заглушкой.
   Не удаляй этот код и не меняй порядок импортов (`from core.app import main` идёт до подмены).
7. **Monaco должна быть скачана** (`make download-monaco`), иначе вкладка «Редактор» покажет
   заглушку «WebEngine недоступен».
8. **SLM требует отдельного wheel** `llama-cpp-python` под конкретный Python/Windows —
   см. `docs/Notes.md`.
9. **`main.py` выставляет `AA_ShareOpenGLContexts` до `QApplication`** — это обязательное
   условие для `QWebEngineView`.
10. **Сессии без проекта** используют `project_path = "global"`, а не `Path.cwd()` — не «чини»
    это обратно.
11. **`slm.n_threads = 0` — это «авто», а не «без потоков».** Значение разбирает
    `detect_optimal_threads()` в `agents/local_llm_client.py` (число физических ядер,
    минимум 2). Ставь `0`, чтобы не хардкодить.
12. **Менять модель в середине диалога нельзя.** У моделей разный `n_ctx`, а история
    чата уже собрана под текущее окно — смена бэкенда переполнит контекст. Это
    ограничение зафиксировано в Фазе 4.7.D плана.
13. **Двухмодельный пайплайн требует ДВЕ разные модели.** `select_pair()` вернёт
    `analyst=None`, если planner и analyst указывают на одну модель; `_ModelPoolLoadThread`
    делает fail-fast. Загрузка ролей последовательная, с per-role timeout 90 с; не заменяй
    её на параллельный `asyncio.gather` без защиты VRAM.
14. **`Orchestrator` не стримит токены.** Он отдаёт `RunResult` через `orchestrator_result`;
    `ChatPanel` сам добавляет ответ, `ChatDiagramItem` и сводку evidence. При ошибке/таймауте
    (10 минут, `ORCHESTRATOR_TIMEOUT_S = 600.0`) `AgentChatThread` делает fallback на `AgentLoop`.
15. **Паспорт исследования строится только при наличии summary/финального текста.**
    Если финальный синтез не сформирован, `ChatPanel` показывает явное предупреждение в
    разделе «Выводы агента». Не рассчитывай на непустой паспорт при отмене или ошибке.
16. **CPU-only analyst имеет жёсткий cap `n_ctx` (8192).** Это сделано в `slm_optimizer` и
    `config_migrate` ради TTFT/throughput; не поднимай cap вслепую — сначала проверь RAM и
    реальную скорость генерации.
17. **`ChatMessage`/`ChatChunk` — типизированные части (`MessagePart`).** `content` /
    `reasoning_content` / `tool_calls` — read-only `@property`, собираемые из `parts`.
    Для изменения используй `ChatMessage.with_content(...)` (копия с заменённым `TextPart`,
    сохраняет `tool_call_id`/`name` и прочие части) или конструируй `ChatMessage(parts=[...])`
    напрямую. `model_validator(mode="before")` принимает старый стиль
    (`content=...`, `reasoning_content=...`, `tool_calls=...`), но **игнорирует** их, если
    `parts` уже передан — не пытайся «обновить» сообщение через
    `model_copy(update={"content": ...})`: это молча ничего не сделает (именно так поступил
    компактор в исходной версии; сейчас использует `with_content`).
18. **`run_shell_command` — единственный инструмент с побочными эффектами вне файлов проекта.**
    Не добавляй его в `chat_auto_tools` и не ослабляй `modes=[AGENT]` / `policy=ASK_FIRST` /
    песочницу `cwd` без явного решения. Текущий чёрный список — защита от ошибок модели, не
    от злоумышленника. **`[план 0.1.1]`** — заменяется на allowlist + dangerous mode +
    audit log; до этого считай `run_shell_command` небезопасным при включённом dangerous-подобном
    режиме.
19. **Стриминг SLM по токенам реализован** (`[9.5]`): `LocalLLMClient._generate` эмитит
    `ChatChunk(part=TextPart, event_type="delta")` на каждый токен, `event_type="start"`/`"end"`
    по границам, tool_calls — одним `ToolCallPart` после стрима текста. `AgentLoop` и
    `ChatPanel` (через `AgentChatThread`) получают `on_text_delta`/`on_reasoning_delta`/
    `on_stream_start`/`on_stream_end` и обновляют `tok/s` в `StatusDashboard`. Раньше
    в этом пункте было «не реализован» — теперь не так. **Не «докручивай стриминг» там,
    где он уже есть** — он в `local_llm_client._generate`.
20. **История 2.0 ещё не завершена** (`[план 0.1.3]`). `ChatPanel._on_agent_finish` /
    `_send_finished` до сих пор создают `ChatMessage(role="assistant", content=result)` —
    только `TextPart`. Reasoning и tool_calls, накопленные в виджете, теряются при
    перезагрузке сессии. Нужен `_current_assistant_parts: list[MessagePart]`, который
    аккумулируется в `_on_chunk`/`_on_thinking`/`_on_tool_*` и передаётся в
    `ChatMessage(parts=...)`. **Не полагайся на «история уже сохраняется» — она сохраняет
    только текст.**
21. **`ReasoningWidget` всё ещё использует `setFixedHeight(240)`** (`[план 0.1.3]`).
    Длинные рассуждения обрезаются до 240 px, новый текст уходит за пределы видимой области.
    Нужно перейти на авто-высоту с максимумом 40 % от высоты чата + внутренний авто-скролл +
    `QPropertyAnimation` при разворачивании. **Не увеличивай `240` — это симптом, не лечение.**
22. **`EventBus` пока не существует** (`[план 2.5]`). `status_label` показывает только
    «🤔 Думает…», логи идут в `loguru` и не видны в UI. Когда EventBus появится —
    подписчики не должны блокировать GUI; тяжёлая работа — в worker'е.
23. **`ToolSpec` / `capabilities` пока не существуют** (`[план 2.6]`). `@tool` знает только
    `name`/`description`/`policy`/`modes`/`is_mutating`/`category`. Когда появятся —
    `capabilities` обязательны для новых инструментов, а `Policy Engine` (**`[план 2.7]`**)
    заменит `_apply_config_policies`.
24. **Shell allowlist пока не реализован** (`[план 0.1.1]`). Текущий `_BLACKLIST` — защита
    от ошибок модели, **НЕ** от злоумышленника. Не расширяй dangerous-поверхность
    (`cmd /c`, `-EncodedCommand`, `iex`) без явного решения и audit log.
25. **`slm_models.yaml` пока нет** (`[план 9.3.3]`). Каталог моделей — в
    `core/config.py::_DEFAULT_SLM_MODELS` + `config.yaml::slm.available_models`. При
    миграции `_DEFAULT_SLM_MODELS` сохраняется как fallback, пользовательские модели
    переносятся без потерь; `config.yaml` после 9.3.3 больше **не** содержит
    `slm.available_models`.
26. **`apply_patch` пока нет** (`[план 10.1]`). `write_file` перезаписывает файл целиком.
    Когда `apply_patch` появится — `write_file` остаётся для новых файлов и полной замены,
    а `apply_patch` — для минимальных правок; при несовпадении контекста — `ToolResult`
    `error_category=DIFFERENT_PATH` (не пишет).
27. **Verifier в обычном AgentLoop пока не подключён** (`[план 10.4]`). `agents/verifier.py`
    используется только в `Orchestrator`. Пока — не полагайся на то, что «ответ проверен».
28. **`_current_assistant_parts` ещё нет** (`[план 0.1.3]`). Когда появится — в
    `_on_agent_finish` сообщение собирается через `ChatMessage(parts=...)`; в research-ветке
    туда же кладётся `summary["final_answer"]` как `TextPart` + `hypotheses` как `TextPart`-markdown.
29. **EventBus-sink и подписчики.** Если в `core/logger.py` появится sink, эмитящий в
    `EventBus`, **все** подписчики должны быть неблокирующими — иначе GUI зависнет на
    каждом `logger.info`. Тяжёлое (I/O, парсинг) — в worker'е через `QThread`.
30. **`effective_policy` приоритет.** При реализации Policy Engine (**`[план 2.7]`**)
    приоритет — `security > profile > config > decorator`. `confirm.web_search` внутри
    security-слоя; старые `tool_policies: {web_search: ask_first}` больше не включают
    диалог — только `confirm.web_search: true`.
31. **Не путай «сделано» и «запланировано» в `PLAN_MODERNIZATION.md`.** Полностью
    завершённые разделы из плана удалены, чтобы файл оставался компактным; актуальные
    задачи — в фазах 0.1, 2.5–2.9, 4.5–4.9, 5–10. Если правишь код в области, упомянутой в
    плане, сверяйся с текущим разделом и обновляй статус (`[ ]` → `[x]`). **Пункты,
    помеченные в этом файле как `[план X.Y]`, ещё не реализованы** — не считай их
    доступными в коде.