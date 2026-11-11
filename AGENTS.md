# AGENTS.md — OlliDesk

Руководство для ИИ-агентов (и разработчиков), работающих в этом репозитории.

**OlliDesk** — локальный AI-ассистент для Windows 10: десктоп-IDE на PySide6 (Qt6), в котором
общаешься с локальными LLM через **Ollama** или встроенный **SLM** (GGUF / llama.cpp),
с RAG-поиском по проекту, вызовом инструментов и встроенным редактором Monaco. Для
исследовательских вопросов используется двухмодельный пайплайн **planner (GPU) + analyst (CPU)**
через `Orchestrator`; обычные запросы идут через ReAct-цикл `AgentLoop`.

- Актуальная рабочая ветка: **`next_generation`** (в ней реализован OlliDesk 2.0).
- Пакетный менеджер: **uv** (`uv.lock` в репозитории), но `Makefile` использует `.venv` + `pip`.
- Целевая платформа: **Windows 10/11**. Приложение запускается как `python main.py`.
- **Целевое состояние 2.0 (EventBus, ToolSpec v2, Policy Engine, Application Services,
  AsyncResource, HF search, `slm_models.yaml`, `git_context`, `apply_patch`, `run_tests`,
  verifier в AgentLoop, Coding Bench, MCP, browser_control, durable inbox, search chain,
  память, парк моделей, иерархический контекст) реализовано.** Остаточные пункты
  (миграция `ChatService` в `_do_chat`, Plugin API, vision-модели через Ollama,
  аудио-плеер в пузыре, `PullThread` merge, релизный пайплайн) помечены **`[план X.Y]`**
  со ссылкой на раздел `docs/PLAN_MODERNIZATION.md`. Не считай их реализованными.

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
│   ├── services/            # Application Services (без Qt): chat/tool/project/model/memory
│   ├── event_bus.py         # EventBus (plain-Python синглтон, thread-safe)
│   ├── events.py            # EventKind (StrEnum) + TypedDict-контракты + args_hash()
│   ├── policy.py            # Policy Engine: effective_policy, explain_policy, _FREEDOM_MATRIX
│   ├── redaction.py         # Redaction секретов: redact(), redact_dict(), redact_exc(), is_secret()
│   ├── audit.py             # SQLite-аудит ~/.ollidesk/audit.db
│   ├── sandbox.py           # SandboxedPathResolver (единый резолвер путей)
│   ├── security.py          # SecurityPolicy (confidential, allow_arbitrary_shell, web_search mode)
│   ├── slm_models_io.py     # load/save/validate slm_models.yaml
│   ├── mcp_client.py        # MCPClient (JSON-RPC 2.0 over stdio)
│   ├── chat_commands.py     # Реестр @-команд (@web, @dir, @file, @path)
│   ├── chat_export.py       # Экспорт чата в Markdown/HTML
│   └── ... (config, logger, profiles, budget, context_budget, exceptions, utils, model_registry, search_cache, system_prompts, roles, prompt_watcher)
├── agents/                  # LLM-клиенты, ReAct, Orchestrator, ModelPool, investigation, evidence
│   ├── hf_search.py         # Поиск моделей на HuggingFace (HfApi)
│   ├── browser_client.py    # IsolatedBrowser / ExtensionBrowser / FullAccessBrowser
│   ├── search_providers.py  # SearXNG / OpenSERP / Yandex / Ollama / DDG
│   ├── benchmark.py         # Замеры TTFT/tok/s/load
│   ├── lsp_bridge.py        # LSP-мост (definition, references, diagnostics)
│   └── ... (base, ollama_client, local_llm_client, model_router, model_pool, orchestrator, agent_loop, investigation, hypotheses, evidence, diagram, critic, verifier, history_compactor, tool_registry, tool_call_parser, tool_grammar, context_loader, slm_optimizer, slm_selector, gguf_reader, hardware_profile, rule_based_fallback, download_slm, gpu_utils)
├── tools/                   # Инструменты (@tool), декларативный список tools/registry.py
│   ├── test_ops.py          # run_tests (pytest/unittest/dotnet/go/npm)
│   ├── browser_ops.py       # browser_open/snapshot/click/fill/close
│   ├── mcp_ops.py           # MCPManager + register_mcp_tools
│   └── plugins/             # [план 7] Plugin API (после ToolSpec v2 + Policy Engine) — отложено
├── fs/                      # Индексация проекта + векторное хранилище ChromaDB (RAG)
│   ├── bm25.py              # Okapi BM25 (без внешних зависимостей)
│   ├── reranker.py          # CrossEncoderReranker (sentence-transformers)
│   ├── chroma_adapter.py    # Типизированная обёртка над ChromaDB
│   ├── document_extractors.py  # PDF/DOCX/PPTX/XLSX/RTF + OCR
│   ├── image_extractors.py  # OCR изображений + превью
│   ├── audio_extractors.py  # STT через faster-whisper
│   ├── video_extractors.py  # STT видео через ffmpeg + faster-whisper
│   ├── media_cache.py       # Кэш извлечённых медиа (по sha256)
│   └── intel/               # Project Intelligence: граф зависимостей, repomap, доменные парсеры
├── state/                   # Персистентность: SQLite-сессии, настройки проекта, inbox
│   ├── inbox.py             # SessionInbox (durable inbox, pending/running/done/failed)
│   └── ... (session_store, project_settings)
├── ui/                      # Qt-виджеты: главное окно, чат, диалоги, веб-редактор
│   ├── chat/                # Панель чата (panel), миксины (backends/research), потоки, сообщения
│   ├── dialogs/             # Мастер настройки, настройки, модели, SLM, HF, сессии, промпты,
│   │                        # permissions_dashboard, memory_dialog, benchmark, export_chat,
│   │                        # document_preview, profile_editor, agent_settings
│   ├── state/               # AsyncResource[T] (guard idle/loading/ready/refreshing/error)
│   ├── widgets/             # AutoScrollArea, StatusDashboard, PipelineSelectors, ProfilesSelector,
│   │                        # CommandCompleter, CommandPalette, CollapsedPlaceholder,
│   │                        # LiveEventsPanel, QtEventBus
│   ├── file_tree_controller.py    # FileTreeController + GitStatusDelegate
│   ├── file_tree_delegate.py      # Git-подсветка + QThreadPool
│   ├── indexing_controller.py     # IndexingController + IndexingThread + ProjectState
│   ├── menus.py             # AppMenuBar
│   ├── command_registry.py  # CommandRegistry + PaletteCommand
│   ├── theme.py             # Дизайн-система (токены + QSS)
│   ├── mermaid_view.py      # DiagramView (ASCII ↔ Mermaid)
│   └── web_editor/          # Monaco (QWebEngineView) + QWebChannel-мост
├── tests/                   # pytest: tests/{core,fs,agents,state,tools,ui,bench}
├── docs/                    # Документация (см. §10)
├── .ollidesk/               # Локальная конфигурация OlliDesk для ЭТОГО проекта (tracked)
└── .github/workflows/ci.yml # CI: ruff → mypy → pytest → codecov
```

Файлы пользователя, которые **не** в репозитории, но важны:

- `~/.ollidesk/config.yaml` — глобальный конфиг (`AppConfig`). С 9.3.3 больше **не** содержит `slm.available_models`.
- `~/.ollidesk/slm_models.yaml` — **`[9.3.3]`** каталог моделей (`core/slm_models_io.py`, `schema_version=1`).
- `~/.ollidesk/models/` — GGUF-модели.
- `~/.ollidesk/system_prompts/`, `~/.ollidesk/roles/` — промпты и роли.
- `~/.ollidesk/sessions.db`, `~/.ollidesk/audit.db`, `~/.ollidesk/memory.db` — сессии, аудит, память.
- `~/.ollidesk/snapshots/<snapshot_id>/` — файловые снапшоты (10.9), `meta.json` + копии файлов.
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
      │ (panel,  │ │ (13+)   │ │ (Monaco)     │
      │ threads) │ └─────────┘ └──────────────┘
      └────┬─────┘
           │
           ▼
    ┌────────────────┐   ┌──────────────┐
    │ agents/ (LLM,  │◄──│ tools/ (@tool)│
    │ ReAct, router, │   └──────────────┘
    │ Orchestrator,  │          │
    │ ModelPool,     │          │
    │ hf_search,     │          │
    │ browser_client)│          │
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
              │             │  security, exceptions, sandbox, audit,
              │             │  redaction, event_bus, events, policy,
              │             │  slm_models_io, mcp_client, chat_commands,
              │             │  chat_export, services/*
              └─────────────┘
```

Правило: **`core` не знает ни о ком**, кроме себя. `agents`/`fs`/`state` знают только `core`.
`tools` знает `core` + `agents.tool_registry`. `ui` знает всё. Импорты **абсолютные**
(`from core.app import main`), не относительные.

**EventBus** (`core/event_bus.py`, `[2.5]` реализован) — plain-Python синглтон,
thread-safe. `agents`/`fs`/`ui` эмитят события (`tool.*`, `rag.search.*`, `llm.*`,
`phase.changed`, `index.*`, `slm.*`, `budget.*`, `agent.finished`, `memory.*`, `inbox.*`,
`mcp.*`, `browser.*`, `search.provider.used`, `hf.search.*`, `git.*`, `permissions.*`,
`media.*`), а `ui/widgets/live_events.py::LiveEventsPanel` подписывается через
`QtEventBus` (QObject-мост). `core` не зависит от `agents`/`fs`, поэтому `EventBus` живёт
в `core/`, а конкретные эмиттеры — в своих слоях.

**Policy Engine** (`core/policy.py`, `[2.7]` реализован) — единая точка авторизации
инструментов: `effective_policy(tool_spec, profile, security, config, session_overrides,
current_mode) -> EffectivePolicy`. Приоритет: **secrets → security → mode → freedom →
profile → session → config → decorator**. `explain_policy` — пошаговое объяснение
(используется `PermissionsDashboard`). Матрица `_FREEDOM_MATRIX` (`safe`/`research`/
`development`/`open`) × capability (`fs.read`/`fs.write`/`net`/`shell`/`git`/`browser`/`secrets`/`ui`).

**Application Services** (`core/services/*`, `[2.8]` реализованы) — выносят бизнес-логику
из `ChatPanel`/`MainWindow` в чистые Python-классы без Qt: `MemoryService`,
`ToolService`, `ProjectService`, `ModelService`, `ChatService`. UI вызывает сервис и
подписывается на сигналы. `ChatService.build_agent_context` готов, но миграция
`_do_chat/_do_agent` — **`[план 2.8]`**.

**AsyncResource** (`ui/state/async_resource.py`, `[2.9]` реализован) — guard для Qt-виджетов,
читающих асинхронные данные; состояние `idle/loading/ready/refreshing/error`,
`loaded() = state in ("ready","refreshing")`. Используется в `PipelineSelectors`,
`SessionManagerDialog`, `SLMModelsDialog`, `ChatPanel` (RAG-sources).

---

## 4. Пакеты: что где лежит

### `core/` — инфраструктура (синхронный код, без Qt)

| Модуль | Назначение | Ключевое |
|---|---|---|
| `core/app.py` | Bootstrap приложения | `OlliDeskApp`, `main()`; применяет тему, открывает `SetupWizard` при отсутствии конфига |
| `core/config.py` | Конфигурация (PyYAML + pydantic) | `AppConfig`, `ModelConfig`, `SLMConfig`, `SLMModelInfo`, `ConfirmSettings`, `BackendSettings`, `RerankSettings`, `EmbedModels`, `BrowserConfig`, `SearchConfig`, `MCPConfig`, `MCPServerConfig`, `MemoryConfig`, `ProfileConfig`, `ConfigLoader`, глобальный `config_loader`; енамы `ModelRole`, `ToolPolicy`, `AgentMode`. `DEFAULT_INDEX_EXTENSIONS` (широкий список: C#/Java/Go/…, `~/.ollidesk/config.yaml`), `ConfigLoader._migrate_config` поднимает `schema_version` v1 → v2 и расширяет устаревший дефолтный `index_extensions` (пользовательский список не трогает). `AppConfig.chat_auto_tools: list[str]` — инструменты, выполняемые в CHAT без подтверждения (переопределяется проектным `.ollidesk/config.yaml`). С 9.3.3 `slm.available_models` загружается из `slm_models.yaml`, а `ConfigLoader._save_yaml` не пишет его в `config.yaml`. Новые поля: `soft_budget_time_s`/`soft_budget_tokens`/`soft_budget_iterations` (2.10), `browser: BrowserConfig` (2.11), `search: SearchConfig` (2.13), `mcp: MCPConfig` (7), `memory: MemoryConfig` (4.3), `allow_arbitrary_shell: bool` (0.1.1), `hf_token: str` (9.3.1), `session_overrides: dict[str, ToolPolicy]` (2.6/2.7), `active_profile: str` + `profiles: list[ProfileConfig]` (9.4/9.12), `show_project_tree`, `show_pipeline_selectors`, `open_modified_as_diff`, `index_documents`, `max_document_size_mb`, `document_ocr` |
| `core/config_v3.py` | Схема конфига v3 | `ConfigV3`, `SecurityCfg`, `ModelsCfg` (`planner`/`analyst`/`analyst_pro`), `ContextCfg` (`policy=dynamic`), `AgentCfg` (`default_flow=linear`, `deep_mode`), `ProjectIntelligenceCfg`, `ToolsPolicyCfg` |
| `core/config_migrate.py` | Миграция конфига v2 → v3 | `migrate()`, `migrate_file()`; backup `config.yaml.v2.bak`; для CPU-analyst жёстко ограничивает `n_ctx` (8192), раскладывает planner/analyst по `device`/`n_gpu_layers` |
| `core/budget.py` | Единая точка расчёта контекстного бюджета | `OpType`, `OUTPUT_RESERVE`, `SAFETY_TOKENS`, `ARCH_SHARES`, `ApproxCounter`, `BudgetPlan`, `plan_budget()`, `trim_to_tokens()` |
| `core/context_budget.py` | Оценка токенов и бюджет контекста под окно модели | `estimate_tokens`, `resolve_num_ctx`, `attached_char_budget`, `system_prompt_char_budget`, `rag_char_budget`, `hot_window_tokens(num_ctx)` (4.2), `warm_layer_tokens(num_ctx)`, `cold_tokens`, `compactor_threshold(num_ctx)`, `KNOWN_NUM_CTX`, `READ_CHUNK_CHARS`, `MAX_READ_FILE_CHUNK_CHARS` |
| `core/security.py` | Локальность и защита от утечек | `NetworkMode`, `SecurityPolicy` (`allow_arbitrary_shell: bool = False` — dangerous mode для shell, 0.1.1), `OUTBOUND_ALLOWLIST`, `assert_outbound_allowed()`, `redact_query()` (использует `core.redaction`), `is_neutral_query()`; hygiene для `web_search` |
| `core/redaction.py` | **0.1.2** Redaction секретов | `redact(text) -> tuple[str, list[str]]`: API-ключи (`sk-…`, `hf_…`, `ghp_…`, `github_pat_…`, `xox[bpors]-…`, `AKIA…`), JWT, пароли из URL/DSN, приватные ключи (`-----BEGIN … PRIVATE KEY-----`), env-переменные с секретными именами. `redact_dict(d)`, `redact_only(text)`, `redact_exc(e)`, `contains_secret`, `is_secret`. Используется в `_redact_filter` логов, `ToolRegistry.execute`, `ToolResult.content`, `web_search_hygiene`, `session_store.save_message` |
| `core/audit.py` | **0.1.1** SQLite-аудит | `AuditLog`, `AuditEntry`, `get_audit_log()`, `_hash()` — `~/.ollidesk/audit.db`: timestamp, tool, args_hash, policy, approved, result_hash, error, dangerous. Пишется из `ToolService.execute` (для **всех** инструментов), `run_shell_command`, `_maybe_snapshot` |
| `core/sandbox.py` | Единый резолвер путей | `SandboxedPathResolver(root).resolve(path, allow_sensitive=False) -> Path | None`; `_DENIED_PARTS` (`.git`, `.ollidesk`); `_DENIED_FILE_NAMES` (`.env`, `.netrc`, `id_rsa*`, `secrets.yaml`, …); `_DENIED_FILE_PATTERNS` (`*.pem`, `*.key`, `*.p12`, `*.ppk`, `*.kdbx`, `id_rsa(\..+)?`). `allow_sensitive=True` — для `write_file`/`apply_patch`; `is_denied_file(name)` |
| `core/logger.py` | Логирование | `setup_logger(dir, level)` — sink в stderr + rotating-файл; `_SafeStream` — защита от сломанного stderr на Windows (cp1251/nul); `_redact_filter` — вырезает секреты из message для всех sink'ов (0.1.2); `_event_bus_sink` — для `level >= INFO` эмитит `EventBus.event_emitted("log", {level, message, source, timestamp})` (2.5) |
| `core/event_bus.py` | **2.5** Шина событий | `EventBus` — синглтон с `emit(kind, payload)`, `subscribe(kind, cb) -> Unsubscribe`, `clear()`, `subscriber_count()`. Монотонный `_seq` (`itertools.count()`) добавляется в payload как `_seq`. `emit()` не держит `lock` во время вызова подписчиков. Module-level `emit_event()`, `subscribe()`. Plain-Python (без Qt) — `fs/` и `agents/` не тянут PySide6 |
| `core/events.py` | **2.5** Схемы событий | `EventKind` (StrEnum): `rag.search.*`, `llm.request.sent/first_token/generation.done`, `tool.started/finished/error`, `phase.changed`, `index.started/progress/finished`, `slm.loading/loaded/failed`, `budget.warning/exceeded`, `agent.finished`, `memory.recalled/remembered`, `policy.override`, `mcp.*`, `browser.action`, `search.provider.used/chain.exhausted`, `inbox.enqueued/delivered/failed`, `hf.search.*`, `git.branch.changed/commit.created`, `permissions.overridden/reset`, `media.extracted/error`, `orchestrator.event`, `log`. TypedDict-контракты для каждого payload. `args_hash(args) -> str` (sha256[:16]) |
| `core/policy.py` | **2.7** Policy Engine | `EffectivePolicy`, `PolicyStep`, `effective_policy(tool_spec, profile, security, config, session_overrides, current_mode) -> EffectivePolicy`. Приоритет: secrets → security → mode → freedom → profile → session → config → decorator. `_FREEDOM_MATRIX` (`safe`/`research`/`development`/`open` × capabilities). `explain_policy(...) -> (EffectivePolicy, list[PolicyStep])` — пошаговое объяснение для Permissions Dashboard |
| `core/slm_models_io.py` | **9.3.3** Каталог моделей в YAML | `load_slm_models(path=None) -> list[SLMModelInfo] | None`, `save_slm_models(models, path=None)` (атомарно через `.tmp`+`replace`), `merge_with_defaults(loaded, defaults)`, `ensure_slm_models_file(defaults, path=None) -> list[SLMModelInfo]`. `SCHEMA_VERSION = 1`, `FILENAME = "slm_models.yaml"` |
| `core/mcp_client.py` | **7** MCP-клиент | `MCPClient` (JSON-RPC 2.0 over stdio), `MCPToolInfo`, `MCPToolResult`, `MCPError`; `CLIENT_PROTOCOL_VERSION = "2024-11-05"`; таймауты `INIT_TIMEOUT=30s`, `CALL_TIMEOUT=120s`, `STOP_TIMEOUT=5s`; `start()`, `list_tools()`, `call_tool()`, `stop()` |
| `core/chat_commands.py` | Реестр `@`-команд (9.5.4) | `ALL_COMMANDS` (`@web`, `@dir`, `@file`, `@path`), `COMMAND_TAGS`, `ChatCommand`, `DEFAULT_PLACEHOLDER`, `detect_active_command(text, cursor_pos)`, `match_commands(prefix)`, `get_command(tag)`, `find_first_word` |
| `core/chat_export.py` | Экспорт чата (9.5.4) | `export_to_markdown(messages, *, title, model, mode, profile, project) -> str`, `export_to_html(...) -> str`; `_plain_to_html` — минимальный markdown→HTML |
| `core/_version.py` | Версия приложения | `__version__` — единственный источник для кода; синхронизируется с `pyproject.toml` через `bump-my-version` |
| `core/utils.py` | Пути | `get_app_data_dir()` → `~/.ollidesk`; `get_resource_path()` с учётом `_MEIPASS`; `get_app_icon()` (ленивый PySide6-импорт) |
| `core/exceptions.py` | Иерархия исключений | `OlliDeskError` + `ConfigError`, `OllamaConnectionError`, `ToolsNotSupportedError`, `ModelNotFoundError`, `IndexingError`, `EmbedderError`, `ToolExecutionError` |
| `core/system_prompts.py` | Системные промпты режимов chat/plan/agent | `SystemPromptManager`, `DEFAULT_PROMPTS`, `default_prompt(mode)`, `clear_cache()`, `render_template(template, context)`; шаблоны `{{переменная}}` (`project_name`, `language`, `project_root`, `project_map`), проектный → глобальный поиск |
| `core/roles.py` | 13 ролей-персон (Markdown + YAML-frontmatter) | `RoleManager(roles_dir=None)`, `RoleDefinition`, `reload()`, `default_templates()` |
| `core/profiles.py` | Профили использования (9.4) | `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES` (development / research / learning), `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `freedom_level: Literal["safe", "research", "development", "open"]` (9.12); `recommended_model`, `park_roles: dict[str, str]`, `coder_models: list[str]`, `n_ctx_hint`, `run_tests_after_edit`, `snapshot_retention_days` |
| `core/prompt_watcher.py` | Горячая перезагрузка промптов и ролей | `PromptWatcher` (watchdog `Observer` + debounce 0.4 с); `start()/stop()/is_running`. Поднимается в `MainWindow`, гасится в `closeEvent` |
| `core/model_registry.py` | Каталог моделей + поддержка tool-calling | `ModelRegistry`, `ModelInfo` |
| `core/search_cache.py` | Кэш результатов web-поиска и порядка реранка (SQLite, TTL 24 ч) | `SearchCache.get()/set()`, `get_list()/set_list()` (JSON-список, ключ реранка с префиксом `rerank\|`) |
- **`core/services/`** (2.8) — сервисы без Qt:
  - `memory_service.py` — эпизодическая память (4.3): `MemoryService`, `Reflection`, `Decision`, `Preference`, `RecallResult`; FTS5, `~/.ollidesk/memory.db`.
  - `tool_service.py` — policy → snapshot → execute → audit (2.8, 0.1.1, 10.9).
  - `project_service.py` — кэш `ProjectIntel` (TTL 5 мин) + `git_snapshot` + `build_git_context` (2.8, 9.4).
  - `model_service.py` — config + `reload_config` + `resolve_park`/`resolve_coder`/`resolve_paths` (2.8).
  - `chat_service.py` — `ChatRequestSnapshot` + `build_agent_context` + `pick_max_iterations` (2.8). **`[план 2.8]`** — миграция `_do_chat/_do_agent` на `ChatService.build_agent_context` отложена.

### `agents/` — работа с LLM

| Модуль | Назначение |
|---|---|
| `agents/base.py` | `LLMBackend` — `runtime_checkable` Protocol: `chat()`, `cancel_request()`, `is_cancelled`, `reset_cancel()`, `embed()`, async context manager |
| `agents/ollama_client.py` | Wire-типы (`ChatMessage`, `ChatChunk`, `ToolCall`, `ChatOptions`, `ToolSchema`) + `MessagePart` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`, discriminated union по `kind`) + асинхронный HTTP/stream-клиент Ollama. `content`/`.reasoning_content`/`.tool_calls` — read-only `@property`, собираемые из `parts`; `model_validator(mode="before")` конвертирует старый стиль в `parts`. `_chunks_from_ollama_message` разбирает `message` на `ChatChunk` (reasoning → text → tool_call). Поддерживает `event_type="start"/"delta"/"tool_call"/"end"` |
| `agents/local_llm_client.py` | Клиент llama.cpp (GGUF): блокирующий инференс обёрнут в `asyncio`. **Стриминг по токенам реализован**: `_generate` эмитит `ChatChunk(part=TextPart, event_type="delta")` на каждый токен, `event_type="start"`/`"end"` по границам; tool_calls — `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`. `_convert_messages` подаёт `reasoning_content` обратно модели в `<thought>…</thought>`. `generate()`, `count_tokens()`, `last_stats` (включая `cache_reads`/`cache_writes` из `self.llm.n_tokens` до/после). `_DEFAULT_TEMPERATURE = 0.0`, `_TEMPERATURE_BY_OP`, `_LANGUAGE_INSTRUCTION`/`_FORMAT_INSTRUCTION` в начале системного сообщения |
| `agents/model_router.py` | Выбор бэкенда по приоритету из конфига с fallback: rule-based в конце. Учитывает whitelist `backends.enabled`, порядок `backends.priority`, `embed_backend`; отдаёт `num_ctx` активного клиента |
| `agents/model_pool.py` | `ModelPool`, `Role` (ROUTER, PLANNER, CODER, VERIFIER, ANALYST, ANALYST_PRO, EMBED), `RoleSpec`; `add_role()` — последовательная загрузка, `_clients_by_file` (переиспользование клиента по файлу), LRU-вытеснение по `_last_used` (`max_loaded=3`); `runtime_ctx()`, `pick_role()`, `aclose()` |
| `agents/orchestrator.py` | Линейный пайплайн: `classify` → `plan` → детерминированный `collect` (`ProjectIntel` + `EvidenceStore`) → `budget` → `synthesize` (7B) → `render_architecture` (код) → `merge` → `critique` + `verify` → при провале `regen`. Deep-mode для больших проектов, `RunResult`. **10.7 реализован**: для `task in {"write_code","refactor_plan","bug_analysis"}` — `_run_coding_flow` (`classify → plan → collect → apply_patch → run_tests → fix_loop → verify → critique`); `RunResult` расширен `coding_flow`, `tests_passed/failed/errors`, `patches`, `iterations` |
| `agents/agent_loop.py` | ReAct-цикл. `AgentLoop`, `AgentContext`, `SoftBudget`, `BudgetStatus` (2.10). Работает через `MessagePart`. Низкоуровневые `on_text_delta`/`on_reasoning_delta`/`on_stream_start`/`on_stream_end`. `_RETRY_HINTS` по `error_category`. Валидация Observation. Research-режим (фазы/гипотезы/нуджи). Natural exit по `finish_reason=stop`, safety net `_hard_ceiling`. `_final_call_no_tools` — fallback при повторах/лимите. **10.4**: `_verify_and_maybe_regen` — verifier после финального ответа, одна регенерация при провале. **9.4**: `git_context` в промпт через `AgentContext` |
| `agents/investigation.py` | `Phase` (`RECON`, `DEEP_DIVE`, `SELF_CHECK`, `SYNTHESIS`), `InvestigationState` (`attempted_calls`/`successful_observations`), `is_research_question()`, `phase_for()`, `pick_nudge()`, эвристики entry-point/config, защита от застревания на одном файле |
| `agents/hypotheses.py` | `HypothesisStore`, `Hypothesis`; статусы `open/verified/refuted/partial/inconclusive`; `render_for_prompt()`, `count_open()`, `summary()` |
| `agents/evidence.py` | `EvidenceStore`, `EvidenceItem`; компактные цитируемые доказательства, `pack_for_prompt()`, SQLite-персист |
| `agents/diagram.py` | `render_architecture()`: ASCII и Mermaid-схемы строятся КОДом из `DependencyGraph`; `Diagram` |
| `agents/critic.py` | Быстрая проверка структуры/формата ответа: `Critique`, `REQUIRED_SECTIONS`, `CRITIC_SCHEMA`, `critique()` |
| `agents/verifier.py` | Проверка финального ответа против графа и evidence: пути, символы, пустые блоки, повторы, `unsupported_claims`; `VerifyResult`, `verify()`. **10.4**: подключён в обычный `AgentLoop` (`_verify_and_maybe_regen`) |
| `agents/history_compactor.py` | Сжатие истории при >80 % окна контекста (порог из `compactor_threshold(num_ctx)`: 0.75 / 0.80 / 0.90). Три ступени: reasoning → tool-результаты → старейшие реплики. `messages_tokens` учитывает `reasoning_content`. **4.2**: `hot_window_tokens=...` не режет горячее окно |
| `agents/tool_registry.py` | `@tool`-декоратор с `is_mutating`/`category`/`capabilities`/`timeout_s`/`max_output_chars`/`idempotent`; JSON Schema из type hints; `ToolRegistry.execute()`, `get_spec`/`try_get_spec`/`get_all_specs`, `register_spec` (для MCP). **2.6**: `ToolSpec` — immutable dataclass с `capabilities: frozenset[str]`. **2.7**: `get_effective_policy(...)` → `EffectivePolicy`; `execute` уважает `effective_policy.allowed`. `ErrorCategory` + `ToolResult.error_category`; таймаут через `asyncio.wait_for`; sync-инструменты через `asyncio.to_thread`; `ToolResult.metadata` для diff-карточки; **10.9**: `metadata.snapshot_id` |
| `agents/tool_call_parser.py` | Извлечение текстовых tool-call (JSON + `name(arg)` / `name(key=value)` / `Action:`). Позиционные аргументы по схеме (`schema_param_names`). Общий для `AgentLoop` и `LocalLLMClient` |
| `agents/tool_grammar.py` | `parse_tool_call()`, `looks_like_ascii_junk()`, `should_fallback()` |
| `agents/context_loader.py` | Загрузка `AGENTS.md`: `<project>/AGENTS.md` → `<project>/.ollidesk/AGENTS.md` → `~/.ollidesk/AGENTS.md` |
| `agents/slm_optimizer.py` | `OptimizedParams`, `optimize(..., n_ctx_hint=...)`: VRAM-first, fallback VRAM+RAM, `budget_by_ram`, `kv_cache_type`, `flash_attn`, CPU-only без жёсткого cap 8192 (берётся `min(n_ctx_hint, budget_by_ram)`) |
| `agents/slm_selector.py` | `select_best_model()`, `select_pair()`, `select_park(profile_park_roles, hw) -> dict[Role, SLMModelInfo]`, `select_coder(profile_coder_models)`. Явный выбор planner/analyst/coder из конфига; guard против одинаковой модели на две роли; автовыбор по тегам |
| `agents/gguf_reader.py` | `read_gguf_meta()`: чтение метаданных GGUF напрямую; `GGUFMeta`, `kv_bytes_per_token_fp16/q8` |
| `agents/hardware_profile.py` | `HardwareProfile`, `CPUInfo`, `GPUInfo`, `RAMInfo`; `usable_vram_mb`, `threads_for_llm` |
| `agents/lsp_bridge.py` | LSP-мост: JSON-RPC 2.0 over stdio, `definition()`/`references()`, `diagnostics()`/`diagnostics_async()`, `_get_default()` (синглтон), `ensure_installed()`, `stop_all()`; `_language_for_path` — маппинг расширений (`.py`→python, `.cs`→csharp, `.rb`→ruby, `.go`→go, `.tf`→terraform). **10.8**: `diagnostics` вызывается из `write_file`/`apply_patch` |
| `agents/rule_based_fallback.py` | Ответы без LLM (регулярки: погода, время, дата) |
| `agents/download_slm.py` | Скачивание/удаление GGUF с HF, обработка split-GGUF. Локальные проверки не ходят в сеть |
| `agents/gpu_utils.py` | Опрос `nvidia-smi`, подбор `n_gpu_layers`, `detect_optimal_threads()`, `model_total_size_mb()` для split |
| `agents/hf_search.py` | **9.3.1** `search_models(query, filter_tag, sort, limit)`, `model_info(repo_id)`, `detect_tags(tags, pipeline_tag, repo_id)`, `HFUnavailableError`. Кэш `_search_cache`/`_info_cache` с `_CACHE_TTL_S = 600` |
| `agents/browser_client.py` | **2.11** `BrowserBackend` (ABC), `IsolatedBrowser`, `ExtensionBrowser`, `FullAccessBrowser`; `PageSnapshot`, `BrowserElement`, `create_backend(config)`, `is_playwright_available()`, `DEFAULT_ACTION_TIMEOUT = 30.0`, `MAX_PAGE_TEXT = 8000`, `MAX_INTERACTIVE_ELEMENTS = 80` |
| `agents/search_providers.py` | **2.13** `SearchProvider` (Protocol), `SearchResult`, `SearchProviderError`; `SearXNGProvider`, `OpenSERPProvider`, `YandexSearchAPIProvider`, `OllamaWebSearchProvider`, `DDGProvider`; `build_chain(config) -> list[SearchProvider]` |
| `agents/benchmark.py` | `run_benchmark()`, `BENCHMARK_PROMPTS`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`; используется `View → Замер производительности…` |

Контракт бэкенда задан `agents/base.py::LLMBackend` (Protocol):
`chat()`, `embed()`, `cancel_request()`, `is_cancelled`, `reset_cancel()`, async context manager.
`ModelRouter.active_client` и `ModelRouter.num_ctx` дают UI доступ к реальному
окну контекста выбранной модели (Ollama — `ChatOptions.num_ctx`, SLM — `n_ctx`).

`ModelRouter` отвечает и за эмбеддинги: `set_priority()` (порядок бэкендов для чата) и
`set_embed_backend()` приходят из `core/config.py::BackendSettings`. `embed()` перебирает
доступные бэкенды и бросает `core.exceptions.EmbedderError`, если не сработал ни один.
`VectorStore` принимает любой объект с методом `embed` (протокол `fs.vector_store.Embedder`),
поэтому `fs` не импортирует `agents`.

Метрики `LocalLLMClient`: `load_ms`, `last_stats` (`prompt_chars`, `tokens`, `gen_ms`,
`ttft_ms`, `tokens_per_sec`, `cancelled`, `cache_reads`, `cache_writes`) — в лог
пишется строка на каждый запрос, `cache_*` попадают в `StatusDashboard.set_cache`.

### `tools/` — инструменты для модели

Каждая функция помечена `@tool(...)`; декоратор **только ставит метаданные**
(`func._tool_meta`). `tools/registry.py::ALL_TOOLS` — **декларативный список и фактическая
точка регистрации**: `_build_registry` обходит его и регистрирует всё, кроме
`read_attached_file` (только при наличии прикреплённых файлов).

**2.6** `@tool` принимает `capabilities: list[str]`, `timeout_s: float = 30.0`,
`max_output_chars: int = 5000`, `idempotent: bool = False`. `ToolRegistry.get_spec(name) -> ToolSpec`
— единая точка доступа к метаданным.

| Инструмент | Модуль | Политика по умолчанию | Режимы | async | capability |
|---|---|---|---|---|---|
| `read_file`, `list_directory` | `file_ops.py` | auto | chat, plan, agent | нет | `fs.read` |
| `write_file` | `file_ops.py` | **ask_first** | agent | нет | `fs.write` |
| `apply_patch` **(10.1)** | `file_ops.py` | **ask_first** | agent | нет | `fs.write` |
| `search_codebase`, `find_references` **(10.3)** | `codebase_search.py` | auto | chat, plan, agent | да | `fs.read` |
| `web_search` | `web_search.py` | ask_first* | chat, plan, agent | да | `net` |
| `get_weather` | `weather.py` | auto | chat, plan, agent | да | `net` |
| `get_current_datetime` | `current_datetime.py` | auto | chat, plan, agent | нет | `ui` |
| `get_exchange_rate`, `get_exchange_rates_multi` | `exchange_rate.py` | auto | chat, plan, agent | да | `net` |
| `get_git_status` | `git_ops_tool.py` | auto | chat, plan, agent | нет | `git` |
| `git_commit` **(9.4)** | `git_ops_tool.py` | ask_first | agent | нет | `git` |
| `create_snapshot`, `undo_file_snapshot` | `git_ops_tool.py` | **ask_first** | agent | нет | `git` |
| `read_attached_file` | `attached_files.py` | auto | chat, plan, agent | нет | `fs.read` |
| `record_hypothesis`, `update_hypothesis`, `list_hypotheses` | `hypothesis.py` | auto | chat, plan, agent | нет | `ui` |
| `run_shell_command` | `shell_ops.py` | **ask_first** | agent | да | `shell` (dangerous mode) |
| `run_tests` **(10.2)** | `test_ops.py` | **ask_first** | agent | нет | `shell` |
| `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_close` **(2.11)** | `browser_ops.py` | ask_first | plan, agent | да | `browser` |

Особенности:
- Все инструменты возвращают `str` (или `dict` с `_llm_text` — для UI-метаданных). **Не бросают
  исключения** — ошибка возвращается строкой с префиксом `Ошибка:` (чтобы модель могла её
  прочитать и исправиться). `ToolResult.metadata` отдельно от `content` — для diff-карточки
  `write_file`/`apply_patch` и `snapshot_id`.
- **Классификация ошибок.** `ToolRegistry.execute()` при ошибке выставляет
  `ToolResult.error_category` (`ErrorCategory`: `FIX_ARGUMENTS` / `DIFFERENT_PATH` /
  `RETRY_LATER` / `REPORT_TO_USER`) по типу исключения. `AgentLoop._create_tool_result_message`
  дописывает `_RETRY_HINTS[error_category]` в конец tool-сообщения.
- **Метаданные инструмента.** `is_mutating`, `category`, `capabilities` — для Policy Engine,
  `ToolService._maybe_snapshot`, `ToolCallCard._ICONS`.
- Зависимости внедряются по имени параметра: `project_root`, `vector_store`, `search_cache`,
  `attached_files`, `hypotheses`, `security`. `ToolRegistry` отфильтровывает их из JSON-схемы
  (см. `_INJECTED_PARAMS`).
- `tools/file_ops.py` использует единый `core.sandbox.SandboxedPathResolver`. **0.1.2**:
  запрет `.env`, `*.pem`, `*.key`, `id_rsa*`, `secrets.yaml` — `_DENIED_FILE_NAMES`/
  `_DENIED_FILE_PATTERNS`; `allow_sensitive=True` для `write_file`/`apply_patch`.
- `tools/file_ops.py::apply_patch` **(10.1)** — unified diff + `expected_hash` (stale check,
  10.9). Возвращает `dict` с `diff`, `lines_added`, `lines_removed`, `syntax_error`,
  `lsp_errors`, `snapshot_id` — совместимо с `write_file`. Использует `_apply_unified_diff`
  с hunk-контекстом и поиском в радиусе ±5.
- `tools/file_ops.py::read_file` — в заголовке отдаёт `sha256=…` (первые 8 hex) и `mtime=…`
  для stale check перед `apply_patch`.
- `tools/file_ops.py::write_file`/`apply_patch` после записи для `.py` вызывают
  `_verify_python_syntax` (`python -m py_compile`); **10.8**: вызывают `LspBridge.diagnostics`
  — ошибки уходят в Observation как `⚠️ LSP: N ошибок …` и в `metadata.lsp_errors`.
- `tools/test_ops.py::run_tests` **(10.2)** — `framework` (pytest/unittest/dotnet/go/npm) +
  `target`; возвращает `dict{_llm_text, summary, failures, ok, exit_code, duration_s}`.
- `tools/codebase_search.py::find_references` **(10.3)** — LSP `references` или эвристика
  через `VectorStore.search` (помечается «эвристика»).
- `tools/git_ops.py` — `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`,
  `get_branch_diff`, `get_recent_commits`, `commit`, `create_file_snapshot`,
  `restore_snapshot`, `load_snapshot_meta`, `cleanup_old_snapshots`, `file_fingerprint`.
  `tools/git_ops_tool.py` — `@tool`-обёртки: `get_git_status`, `create_snapshot`,
  `undo_file_snapshot`, `git_commit` (10.9, 9.4).
- `tools/attached_files.py` — `AttachedFileStore.add/find/render`, `read_attached_file(path, offset, limit)`.
  В промпт уходит только начало и конец файла в пределах бюджета; середина — по требованию.
  Регистрируется только когда вложения есть.
- `tools/hypothesis.py` — `record_hypothesis`, `update_hypothesis`, `list_hypotheses`;
  хранилище инжектируется через `AgentContext.hypotheses`.
- `tools/shell_ops.py::run_shell_command` **(0.1.1)** — PowerShell-команда в песочнице `cwd`.
  **Allowlist** (`_ALLOWLIST_RE`): `Test-Path`, `Get-ChildItem`, `Get-Content`,
  `Select-String`, `Measure-Object`, `Get-Location`, `Resolve-Path`, `Get-FileHash`,
  `python -m pytest|py_compile|unittest|mypy|ruff`,
  `git status|diff|log|show|branch|rev-parse|describe`, `dotnet test`, `go test`, `npm test`.
  **Dangerous mode** (`SecurityPolicy.allow_arbitrary_shell=True`) — снимает allowlist,
  но **чёрный список** (`_BLACKLIST`: rm -rf, Remove-Item -Recurse -Force, del /f|/s|/q,
  rd /s, format, shutdown, reg add, Set-ItemProperty, curl|iwr|Invoke-WebRequest | iex)
  остаётся. Каждый вызов — в `audit.db`. `MAX_OUTPUT_CHARS = 5000`, `MAX_TIMEOUT_S = 300`.
  `policy=ASK_FIRST`, `modes=[AGENT]`, `is_mutating=True`, `category="shell"`,
  `capabilities=["shell", "fs.write"]`; в `chat_auto_tools` **не входит**.
- `tools/web_search_hygiene.py` — hygiene-фильтр: `filter_query()` (режимы `off/neutral/
  ask_first/auto`), `redact()` (0.1.2) перед отправкой.
- `tools/mcp_ops.py` **(7)** — `MCPManager` (singleton), `register_mcp_tools(registry)`,
  `_make_callable(server, tool)`. Эвристика capabilities по имени + whitelist
  `MCPServerConfig.capabilities`; `secrets` — всегда excluded.
- `tools/browser_ops.py` **(2.11)** — `browser_open/snapshot/click/fill/close`,
  singleton backend через `_get_backend(config)`, `shutdown_browser()`.
- \* `web_search` в декораторе помечен `ask_first`, но **по умолчанию выполняется без диалога**:
  в `AppConfig` есть секция `confirm` (`enabled`, `web_search`), а Policy Engine (2.7)
  учитывает её в security-слое. Тумблер — меню File → «Подтверждать запросы в интернет».
  **`confirm` важнее `tool_policies`:** старые записи `tool_policies: {web_search: ask_first}`
  больше не включают диалог — только `confirm.web_search: true` (или `confirm.enabled: true`).
  Сами запросы проходят через `core/security.py` и `tools/web_search_hygiene.py`.

### `fs/` — индекс проекта и RAG

- `fs/indexer.py` — `FileIndexer`: обход с учётом `.gitignore` (`pathspec`), фильтры по
  расширению/размеру/имени, SHA-256 хэши, чанкинг по строкам с перекрытием, определение языка,
  `incremental_scan()`. `chunk_file(content, file_path, mtime=None)` кладёт `language` и `mtime`
  в метаданные. Игнор-паттерны: `config.ignored_patterns` → `.gitignore` → `.ollideskignore` →
  `.indexignore` → `.ollidesk/indexignore` (позже совпавшее правило выигрывает). Поддержка
  `index_filenames` (`Vagrantfile`, `Dockerfile`, `Makefile`, `site.yml`, `playbook.yml`, …).
  **9.4**: `read_file_content` для документов (PDF/DOCX/…) через `extract_document`.
- `fs/vector_store.py` — `VectorStore`: ChromaDB (коллекции `codebase`/`codebase_<hash>`,
  косинус), `add_chunks()`, `search()`, `delete_by_file()`, `clear()`.
  **Раздельные модели по типу файла** (`category_for_language()` — `docs`/`code`).
  **Гибридный поиск**: векторные выдачи + BM25 через RRF (`_reciprocal_rank_fusion`).
  `Reranker` после RRF, `RerankCache` (TTL 24 ч). **2.5**: эмитит `rag.search.*` в `EventBus`.
  `_DOC_LANGUAGES` включает pdf/docx/pptx/xlsx/rtf (9.4).
- `fs/bm25.py` — Okapi BM25 без внешних зависимостей. `tokenize()`, `BM25Index.build()`,
  `search()`.
- `fs/reranker.py` — `CrossEncoderReranker`: опциональный реранкинг RAG кросс-энкодером
  `sentence_transformers.CrossEncoder`. Ленивая загрузка; выключен по умолчанию
  (`RerankSettings.enabled`).
- `fs/project_map.py` — `list_project_files()`, `render_project_map()`, `detect_query_scope()`.
- `fs/chroma_adapter.py` — единственное место небезопасной границы с ChromaDB;
  `ChunkMetadata`, `QueryPage`, `build_where(file_filter, language, modified_after, modified_before)`.
- `fs/intel/` — Project Intelligence: `analyze_project()`, `DependencyGraph`,
  `RepoMap`, `detect_and_parse_all` (`langs.py`), доменные парсеры `ansible.py`,
  `terraform.py`, `vagrant.py`, `csharp.py`, `python.py`. Детерминированное понимание
  проекта: LLM не читает проект «в лоб».
- `fs/document_extractors.py` — **Фаза 5 + 9.7** `extract_document(path, *, use_ocr=False)`,
  `is_document`, `language_for_document`, `SUPPORTED_EXTENSIONS`, `_ocr_pdf`. PDF (pdfplumber
  → pypdf), DOCX (python-docx), PPTX (python-pptx), XLSX (openpyxl), RTF (striprtf).
- `fs/image_extractors.py` — `extract_image(path, *, use_ocr=True) -> ImageResult`,
  `image_thumbnail_base64(path, max_size=200) -> str`, `is_image`, `IMAGE_EXTENSIONS`.
- `fs/audio_extractors.py` — `transcribe_audio(path) -> AudioResult`, `is_audio`,
  `AUDIO_EXTENSIONS`; faster-whisper (CPU, int8) + ffmpeg.
- `fs/video_extractors.py` — `extract_transcript(path) -> VideoResult`, `is_video`,
  `VIDEO_EXTENSIONS`; ffmpeg → WAV → `transcribe_audio`.
- `fs/media_cache.py` — `file_sha256`, `get_cached(path, kind)`, `set_cached(path, kind, text)`.

Индекс хранится в `<проект>/.ollidesk/vector_db`, состояние — в
`<проект>/.ollidesk/project_state.json`.

### `state/` — персистентность

- `state/session_store.py` — `SessionStore` поверх sqlite3. Таблицы `sessions` и `messages`.
  Колонка `parts_json TEXT` (миграция через `PRAGMA table_info`); `save_message` проходит
  через `redaction.redact_only` (0.1.2) для `content` и `parts_json`; `get_history` разбирает
  через `TypeAdapter(MessagePart)`. **10.9**: сохраняются `tool_call_id`, `name`.
- `state/project_settings.py` — `ProjectSettings` в `<проект>/.ollidesk/project_settings.yaml`
  (модель, режим, температура, max_tokens, `show_reasoning`, **9.4**: `git_branch`,
  `git_hash`, `git_last_sync`). `snapshot_git_state(project_path)` — снимок для полей git_*.
- `state/inbox.py` — **2.12** `SessionInbox`: SQLite-очередь `session_inbox` с полями
  `id/session_id/prompt/context_json/mode/created_at/updated_at/state/result_json/error`.
  `enqueue()/claim()/complete()/fail()/recover()/cleanup_old()/list_pending()/count_pending()`.
  `MAX_PENDING_PER_SESSION = 20`, `RETENTION_DAYS = 7`.

### `ui/` — интерфейс

Композиция (актуальная):
```
MainWindow (QMainWindow, ui/main_window.py)
├── QSplitter (горизонтальный, [320, 800])
│   ├── левая панель: заголовок проекта, кнопка реиндексации,
│   │                 QTreeView + GitStatusDelegate, прогресс-бар индексации
│   │                 (скрывается по F4 — View → «Структура проекта»)
│   ├── центральная панель: QStackedWidget
│   │   ├── страница 0: ChatPanel (базовый экран)
│   │   └── страница 1: EditorWidget (Monaco, собственный QTabBar для файлов)
│   └── поповер-настройки: AgentSettingsDialog (⚙ в ChatPanel)
└── QStatusBar: путь проекта + StatusDashboard (backend/pipeline/tok/s/RAG/tools/budget/cache/coder/browser/search/git/memory/mcp/freedom/inbox/activity)
```

**9.1** — чат больше не вкладка: `QStackedWidget` в `_create_center_panel`; `QTabWidget`
авто-скрывается при `tab_count() == 0` (сигнал `EditorWidget.tabs_changed(int)`).
`MainWindow._on_editor_tabs_changed` / `_switch_to_chat` / `_switch_to_editor`.

| Модуль | Назначение |
|---|---|
| `ui/main_window.py` | Оболочка, открытие проекта и список последних, оркестрация индексации, меню, переключение темы. Владеет `SessionStore`, `OllamaClient`, `ModelRegistry`, `RoleManager`, `SystemPromptManager`, `FileIndexer`, `SearchCache`. Контроллеры вынесены: `self.file_tree_controller`, `self.indexing_controller`, `self.menu_bar`. `ModelCheckThread` остался в `main_window.py`. `_do_open_project` показывает структуру при первом открытии (9.2). `_on_editor_tabs_changed`, `_switch_to_chat`, `_switch_to_editor`, `_show_chat_tab`, `_show_editor_tab`, `_diff_current_editor_file`, `_on_show_diff`, `_show_file_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_open_benchmark`, `_open_profile_editor`, `_open_memory_dialog`, `_open_permissions`, `_on_theme_changed_for_editor`, `_stop_all_threads` (без `new_event_loop`), `_do_check_ollama` (через `_OllamaCheckThread`) |
| `ui/chat/panel.py` | Панель чата с миксинами `BackendsMixin` + `ResearchMixin`. Регистрация инструментов: `_build_registry` обходит `tools.registry.ALL_TOOLS`. `_current_session_overrides()` — `chat_auto_tools`/`tool_policies`/`session_overrides`. `_ensure_memory_service`, `_ensure_inbox`, `_recover_inbox_on_startup`, `_sync_inbox_indicator`, `_build_git_context` (через `ProjectService`), `_build_assistant_message`, `_render_assistant_parts`, `_remember_if_needed`, `_build_reflection`, `_allow_tool_for_session`, `_on_undo_file_snapshot`. Research-режим: `ChatThoughtStream`, `ChatHypothesisItem`, `ChatResearchPassport`; ленивый Orchestrator (`_ensure_orchestrator`, `reset_pipeline`, `_pipeline_status_label`). `_show_reasoning`, `continue_btn` + `_continue_after_limit` + `_continue_unlimited`. `_on_tool_started`/`_on_tool_executed` (композитный пузырь + буфер `_current_assistant_parts`). `set_theme`, `set_dashboard`, `_on_bus_event`, `registry_for_permissions`, `refresh_pipeline_selectors`, `refresh_profiles_selector`, `_apply_profile`, `_on_profile_changed`, `_back_to_development`, `_on_mcp_initialized`, `_start_mcp_init`, `shutdown` |
| `ui/chat/backends.py` | `BackendsMixin`: `reload_slm`, `_detect_and_load_slm`, `_apply_backend_config`, `_detect_backends`, `_active_backend_label`, `_emit_backend_status`, `_check_ollama_async`, `_on_ollama_check_result`, `_on_slm_loaded`, `_load_slm_async`, `_download_and_load_slm`, `_on_slm_progress`, `_on_slm_error`, `_on_slm_downloaded` |
| `ui/chat/research.py` | `ResearchMixin`: `_reset_research_state`, `_on_research_mode_detected`, `_on_thought_chunk`, `_on_final_answer_started`, `_on_final_chunk`, `_on_research_summary_ready`, `_should_show_passport`, `_show_research_passport`, `_pipeline_status_label`, `_update_pipeline_status`, `_pipeline_tooltip`, `_ensure_orchestrator`, `reset_pipeline`, `_on_pool_loaded`, `_on_pool_error`, `_on_orchestrator_result`, `_on_orchestrator_event`, `_on_pool_progress` |
| `ui/chat/threads.py` | Мост async ↔ Qt: `AgentChatThread` (роутинг в `Orchestrator`/`AgentLoop`, `inbox_id`+`inbox`, `tool_service`), `RagSearchThread`, `WebSearchThread`, `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`, `_ModelPoolLoadThread` (последовательная загрузка, per-role timeout 90 с, LRU), `_BenchmarkThread`. Низкоуровневые `text_delta`/`reasoning_delta`/`stream_started`/`stream_finished`; `budget_warning`/`budget_exceeded`/`agent_finished` |
| `ui/chat/message_item.py` | Композитный «пузырь»: `ReasoningWidget` + `TextWidget` + `ToolCallCard`. `render_markdown` — вручную, `re` + `pygments`, с поддержкой заголовков/списков/чек-листов/таблиц/цитат/кода. `ChatActivityItem`, `ChatSourcesItem`, `ChatHypothesisItem`, `ChatDiagramItem`. `ToolCallCard` с diff-режимом для `write_file`/`apply_patch` (📂/🔍/↩), `_truncate_preview`, `_escape_diff`, `_add_lsp_link` (10.8), `snapshot_undo_requested` (10.9), `show_diff_requested`, `open_in_editor_requested` |
| `ui/chat/thought_stream.py` | `ChatThoughtStream`: серый лог рассуждений, `append_thought_token`, `append_action`, `append_result`, `append_hypothesis`, `append_nudge`, `append_phase`, `append_status`, `mark_interrupted` |
| `ui/chat/research_passport.py` | `ChatResearchPassport`: финальный паспорт исследования — выводы агента, KPI, прочитанные файлы, гипотезы, копирование в Markdown |
| `ui/file_tree_delegate.py` | Git-подсветка в дереве, `git status --porcelain` в `QThreadPool` (`_GitStatusWorker`), `_running`-флаг |
| `ui/file_tree_controller.py` | `FileTreeController`: `QTreeView` + `GitStatusDelegate` + файловые операции. Сигналы: `file_open_requested`, `file_renamed`, `file_deleted`, `branch_changed`, `file_diff_requested` |
| `ui/indexing_controller.py` | `IndexingThread` + `ProjectState` + `IndexingController`. Сигналы: `progress`, `finished`, `error`, `summary`; `state_ready` из worker'а |
| `ui/menus.py` | `AppMenuBar` — меню + Recent Projects + тумблеры + профили; `set_config()`, `set_recent_projects()`, `_refresh_profiles_menu()`. Новые пункты: `export_chat_requested`, `profile_selected`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`, `permissions_requested`, `memory_requested` |
| `ui/command_registry.py` | `CommandRegistry`, `PaletteCommand` |
| `ui/theme.py` | `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`, `get_colors`, `get_stylesheet` |
| `ui/widgets/auto_scroll.py` | `AutoScrollArea`: sticky-to-bottom с порогом 32 px, кнопка «▼ Вниз» |
| `ui/widgets/status_dashboard.py` | `StatusDashboard`: backend/pipeline/tok/s/RAG/tools/budget/cache/coder/browser/search/git/memory/mcp/freedom/inbox/activity. Методы: `set_activity_state`, `set_last_event_tooltip`, `set_backend`, `set_pipeline`, `set_tok_per_sec`, `set_rag`, `set_tools_active`, `set_budget`, `set_finish_reason`, `set_cache`, `set_coder`, `set_browser`, `set_search`, `set_git_branch`, `set_memory`, `set_mcp`, `set_freedom`, `set_inbox` |
| `ui/widgets/pipeline_selectors.py` | `PipelineSelectors`: ComboBox planner/analyst + «⋯» + индикатор состояния. **9.3.2**: `_apply_coder_highlight` — bold+цвет coder-моделей в `development`, иконки ролей, `set_recommended_model`, `set_active_profile` |
| `ui/widgets/profiles_selector.py` | `ProfilesSelector`: ComboBox профилей |
| `ui/widgets/command_completer.py` | `CommandCompleter`: QCompleter для `@`-команд (9.5.4) |
| `ui/widgets/command_palette.py` | `CommandPalette`: QDialog-палитра (`Ctrl+Shift+P`) |
| `ui/widgets/collapsed_placeholder.py` | `CollapsedPlaceholder`: виртуализация длинных чатов, порог 80 |
| `ui/widgets/live_events.py` | **2.5**: `QtEventBus` (QObject-мост), `LiveEventsPanel` (фильтр по kind, «только важное», авто-скролл) |
| `ui/state/async_resource.py` | **2.9**: `AsyncResource[T]`, `ResourceState`; `begin_load`, `set_value`, `set_error`, `loaded()`, `value_or()` |
| `ui/dialogs/wizard.py` | Мастер первой настройки (4 страницы). `ignore_errors = true` в mypy |
| `ui/dialogs/settings_dialog.py` | Глобальные и проектные настройки индексации; вкладка «Бэкенды LLM» |
| `ui/dialogs/model_manager_dialog.py` | Модели Ollama: таблица, sync, `ollama pull` |
| `ui/dialogs/slm_models_dialog.py` | Менеджер GGUF-моделей. **9.3.1**: кнопка «🔍 Поиск HF» (`_open_hf_dialog`, `_on_hf_download`, `_on_hf_add_to_catalog`); **9.3.3**: запись в `slm_models.yaml` через `save_slm_models` |
| `ui/dialogs/hf_dialog.py` | **9.3.1**: `HFDialog` — вкладка «Поиск HF» с фоновыми `_SearchThread`/`_InfoThread`; сигналы `download_requested`, `add_to_catalog_requested` |
| `ui/dialogs/tool_confirmation_dialog.py` | Одобрить / отклонить / отредактировать аргументы; `session_allow_requested` (📌 «Разрешить на сессию», 2.7/9.10). `_coerce_value(raw, orig_type)` |
| `ui/dialogs/session_manager_dialog.py` | Таблица сессий: открыть, удалить, экспорт, «Создать проект из сессии»; preview |
| `ui/dialogs/prompt_editor_dialog.py` | Редактор системных промптов и ролей: вкладки «Редактор»/«Превью», «↩ По умолчанию», сохранение с `clear_cache()`/`reload()` |
| `ui/dialogs/agent_settings_dialog.py` | Настройки агента (поповер из ⚙): temperature, max tokens, context window, max iterations, show_reasoning, tools, RAG, «dangerous mode» (0.1.1), MCP toggle (7), browser mode + full_access_ack (2.11) |
| `ui/dialogs/benchmark_dialog.py` | Замеры производительности (TTFT, tok/s, load), экспорт CSV/Markdown (4.7.B) |
| `ui/dialogs/document_preview_dialog.py` | Предпросмотр извлечённого текста (9.4.8); OCR-кнопка при пустом тексте |
| `ui/dialogs/export_chat_dialog.py` | Экспорт чата (MD/HTML) |
| `ui/dialogs/profile_editor_dialog.py` | Редактор профилей (9.4); `freedom_level` с предупреждением для `open` (9.12) |
| `ui/dialogs/permissions_dashboard.py` | **9.10**: `PermissionsDashboard` — 4 вкладки (по профилям, по capabilities, audit, объяснение блокировки); reset session overrides |
| `ui/dialogs/memory_dialog.py` | **4.3**: `MemoryDialog` — рефлексии/решения/настройки; поиск, удаление, экспорт |
| `ui/mermaid_view.py` | `DiagramView`: ASCII ↔ Mermaid (QWebEngine + локальный `mermaid.min.js`) |
| `ui/web_editor/editor_widget.py` | `QWebEngineView` + `PythonBridge`. `open_file(path, line=0)`, `show_git_diff_for_file(path)`, `show_diff_from_texts(...)`, `_on_editor_tabs_changed`, сигнал `tabs_changed(int)`, `set_theme(theme)`, `_on_web_loaded` (тема применяется после load) |
| `ui/web_editor/editor.js` | Monaco: загрузка файлов, diff-режим, статус-бар, `openFile(path, line)`, `setTheme(theme)`, `saveFile`, `closeFile`, `renameOpenFile` |

Мост QWebChannel (`PythonBridge`, зарегистрирован как `pythonBridge`):

| Направление | Член | Сигнатура |
|---|---|---|
| JS → Py | `read_file` `@Slot` | `(request_id, path)` |
| JS → Py | `write_file` `@Slot` | `(request_id, path, content)` |
| JS → Py | `set_dirty` `@Slot` | `(file_path, is_dirty)` |
| Py → JS | `file_content_ready` `Signal` | `(str, str)` → `onFileContentReady(...)` |
| Py → JS | `file_write_result` `Signal` | `(str, bool)` → `onFileWriteResult(...)` |
| Py → JS | `file_dirty_changed` `Signal` | `(str, bool)` → `_on_dirty_changed(...)` |

JS выделяет `requestId`, кладёт `resolve` в `pendingRequests` — fire-and-forget
превращается в Promise. Py → JS: `open_file(path, line)`, `show_diff`, `accept_diff`,
`reject_diff`, `get_content`, `setTheme`, `saveFile`, `closeFile`, `renameOpenFile`.
Клик по источнику RAG (`ChatSourcesItem.source_activated`) → `MainWindow.open_file_in_editor`.
Diff из карточки `write_file`/`apply_patch` (`ToolCallCard.show_diff_requested`) →
`ChatPanel.diff_requested` → `MainWindow._on_show_diff` → `EditorWidget.show_diff_from_texts`.

---

## 5. Пайплайн запроса

```
Пользователь печатает в ChatPanel
  ├─ @web <запрос>          → WebSearchThread, мимо агента
  ├─ @dir <путь> [вопрос]   → directory_attach_requested → MainWindow строит
  │                           дерево, VectorStore, запускает индексацию; остаток текста — обычное сообщение
  ├─ @file <путь> [вопрос]  → AttachedFileStore.add; остаток — обычное сообщение
  ├─ @path <путь> [вопрос]  → авто-детект: каталог → как @dir, иначе как @file
  └─ обычный текст
       └─(Ctrl+Enter или кнопка)→ **inbox.enqueue** (2.12) → **RAG-пре-поиск**
       │     (RagSearchThread): detect_query_scope → file_filter,
       │     дедуп чанков по файлу, бюджет rag_char_budget(num_ctx);
       │     источники — ChatSourcesItem (клик → редактор);
       │     эмит rag.search.started/found/empty в EventBus
       └─ AgentChatThread (QThread → asyncio.run) → **inbox.claim**
           ├─ research-вопрос + Orchestrator готов?
           │   └─ Orchestrator.run(...) или _run_coding_flow (10.7 для coding):
           │       classify → plan → collect(ProjectIntel+EvidenceStore) → budget
           │       → synthesize(7B) → render_architecture(код) → merge
           │       → critique+verify → [fail] regen
           │       → RunResult(answer, ascii_diagram, mermaid_diagram, evidence_ids,
           │                   coverage, unsupported_claims, verify_ok, critique_ok,
           │                   coding_flow, tests_passed/failed/errors, patches, iterations)
           │       ChatPanel: ответ, ChatDiagramItem, сводка evidence/тестов
           └─ иначе AgentLoop.run(ctx, history, on_token, on_tool_request, ...):
               ├─ research_mode? on_thought → ChatThoughtStream,
               │                  on_token только для финального ответа → паспорт
               ├─ _build_system_prompt():
               │    фиксированные: tool-instruction + role + mode-prompt + проект
               │    [9.4] git_context (branch, hash, diff --stat) в AGENT
               │    переменные (урезаются в порядке RAG → map → rules):
               │    @file превью + RAG + project_map + rules.md + AGENTS.md
               │    + гипотезы + memory.block (4.3)
               │    всё в system_prompt_char_budget(num_ctx)
               ├─ _compact_history(): прореживание с hot_window_tokens (4.2)
               └─ ModelRouter.chat(...) → Ollama | LocalLLM | RuleBased
                   ├─ стрим ChatChunk(part=...) → chunk_received/thinking_received
                   ├─ reasoning_content (ReasoningPart) → on_thinking
                   └─ tool_calls (ToolCallPart)?
                        ├─ **effective_policy(tool_spec, ...)** (2.7): excluded → стоп
                        ├─ **capabilities-проверка** (2.6)
                        ├─ ToolService.execute() → audit + автоснапшот (10.9)
                        ├─ ASK_FIRST: on_tool_request → ToolConfirmationDialog.exec()
                        │                (аргументы редактируемы; «📌 на сессию» → session_overrides)
                        ├─ _create_tool_result_message: retry_hint по error_category
                        ├─ эмит tool.started/finished/error в EventBus
                        └─ **verify/tests**: `apply_patch`/`write_file` → LSP-ошибки в Observation
                           (10.8); `run_tests` (10.2) → failures в metadata
               ├─ `_final_call_no_tools` → **verify** (10.4) → [fail] одна регенерация
               └─ **inbox.complete** / **inbox.fail**; **budget.warning/exceeded**;
                  **agent.finished** → StatusDashboard
```

Ключевые флаги: `AgentMode.CHAT` и `PLAN` — лимит зависит от размера проекта
(`light` ≈ 3, `medium` ≈ 8, `deep` ≈ 5 с фазами/гипотезами); `AGENT` — полный
`agent_max_iterations` (30). Research-вопрос в CHAT/PLAN может идти через фазы
`RECON → DEEP_DIVE → SELF_CHECK → SYNTHESIS`, гипотезы и нуджи. При наличии
Orchestrator research-вопрос уходит в линейный пайплайн; при ошибке/таймауте — fallback.

Если все tool_calls в прогоне упали, `AgentLoop` один раз вклинивает `_OBSERVATION_NUDGE`.
Natural exit по `finish_reason=stop`. При `budget.exceeded` (2.10): в CHAT/PLAN —
принудительный финальный синтез; в AGENT — только сигнал, цикл идёт до `finish_reason=stop`
или `user_cancel`. При `iteration_limit_exceeded` (`_hard_ceiling`) `ChatPanel` показывает
«▶ Продолжить» (удвоение лимита, max 200) и «▶▶ До результата» (бюджет ∞).

Пути с пробелами — в кавычках: `@dir "C:\My Folder\Project" изучи структуру`.

Ключевые точки расширения:

| Что менять | Где |
|---|---|
| Системный промпт режима | `~/.ollidesk/system_prompts/*.md` или `<проект>/.ollidesk/system_prompts/` |
| Правила проекта | `<проект>/.ollidesk/rules.md` (`rules_file` в конфиге) |
| Контекст проекта для агента | `<проект>/AGENTS.md`, `<проект>/.ollidesk/AGENTS.md`, `~/.ollidesk/AGENTS.md` |
| Список и политики инструментов | `tools/registry.py::ALL_TOOLS` + `core/policy.py::effective_policy` |
| Параметры индексации | `~/.ollidesk/config.yaml` |
| Бэкенд LLM | `agents/model_router.py` + `core/config.py::ModelConfig` |
| Двухмодельный пайплайн / Orchestrator | `agents/orchestrator.py`, `agents/model_pool.py`, `ui/chat/panel.py::_ensure_orchestrator` |
| Project Intelligence | `fs/intel/*` |
| Evidence / диаграммы / верификация | `agents/evidence.py`, `agents/diagram.py`, `agents/verifier.py`, `agents/critic.py` |
| Гипотезы исследования | `agents/investigation.py`, `agents/hypotheses.py`, `tools/hypothesis.py` |
| Безопасность web_search | `core/security.py`, `tools/web_search_hygiene.py`, `core/redaction.py` |
| Реранкинг RAG | `fs/reranker.py` + `core/config.py::RerankSettings` |
| Раздельные эмбеддинги (код/документация) | `core/config.py::EmbedModels` + `fs/vector_store.py::category_for_language` |
| Бюджет контекста под модель | `core/context_budget.py`, `core/budget.py` |
| Части сообщения | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`) |
| Композитный пузырь | `ui/chat/message_item.py` (`ReasoningWidget`/`TextWidget`/`ToolCallCard`) |
| События UI / логи → UI | `core/event_bus.py`, `core/events.py`, `ui/widgets/live_events.py` |
| Политики инструментов (единая точка) | `core/policy.py::effective_policy` / `explain_policy` |
| Capabilities инструментов | `agents/tool_registry.py::ToolSpec` + `@tool(capabilities=...)` |
| Каталог моделей | `~/.ollidesk/slm_models.yaml`, `core/slm_models_io.py` |
| Поиск моделей на HF | `agents/hf_search.py`, `ui/dialogs/hf_dialog.py` |
| Git-контекст в промпте | `core/services/project_service.py::build_git_context`, `AgentContext.git_context` |
| `git_commit` как инструмент | `tools/git_ops_tool.py::tool_git_commit`, `tools/registry.py::ALL_TOOLS` |
| `apply_patch` | `tools/file_ops.py::apply_patch` |
| `run_tests` | `tools/test_ops.py::run_tests` |
| `find_references` (LSP) | `agents/lsp_bridge.py`, `tools/codebase_search.py::find_references` |
| Verifier в AgentLoop | `agents/agent_loop.py::_verify_and_maybe_regen` |
| Coding Bench | `tests/bench/`, `scripts/run_coding_bench.py` |
| LSP-диагностика после записи | `tools/file_ops.py` → `LspBridge.diagnostics` → `metadata.lsp_errors` |
| Stale check + снапшот | `tools/file_ops.py::apply_patch(expected_hash)`, `ToolService._maybe_snapshot`, `tools/git_ops.py::create_file_snapshot` |
| Браузер (3 режима) | `agents/browser_client.py`, `core/config.py::BrowserConfig` |
| Цепочка поиска (fallback) | `agents/search_providers.py`, `core/config.py::SearchConfig` |
| Durable inbox | `state/inbox.py`, `ChatPanel._ensure_inbox` |
| Эпизодическая память | `core/services/memory_service.py`, `ui/dialogs/memory_dialog.py` |
| Режимы свободы | `core/policy.py::_FREEDOM_MATRIX`, `ProfileConfig.freedom_level` |

---

## 6. Соглашения по коду

**Язык**
- Идентификаторы — **английский**.
- Комментарии, докстринги, лог-сообщения и текст интерфейса — **русский**.
- Докстринги публичного API — Google-style (`Args:` / `Returns:` / `Raises:`).
- Комментарии в коде не добавляй без необходимости (`.ollidesk/rules.md`).

**Форматирование и линтеры**
- `line-length = 110`. Ruff: `select = E,F,I,N,UP,B,A,C4,PT,RET,SIM`, `ignore = E501, SIM108`.
  `quote-style = "preserve"` — **не меняй вид кавычек** у существующих строк.
- `N802/N806` иногда нарушаются намеренно (методы Qt, константы) — не «исправляй» без причины.
- Black и isort **удалены**. Форматирование и сортировка импортов — только `ruff format` и
  `ruff check --fix` (I001).
- `requires-python = ">=3.12"`, `ruff`/`mypy` нацелены на `py312`. `typing.override` доступен с 3.12.

**Типизация**
- mypy `strict = true`, но с послаблениями (`disallow_untyped_defs = false`,
  `disallow_untyped_calls = false`, `disallow_any_generics = false`), `check_untyped_defs = true`.
- Для `core/`, `agents/`, `fs/`, `state/`, `tools/` — `disallow_untyped_defs = true` и
  `disallow_incomplete_defs = true`. Для `ui/` и `tests/` — мягче.
- Новый код обязан иметь аннотации. ChromaDB и llama-cpp не типизированы — `cast(Any, ...)`
  или `# type: ignore[...]` с пояснением.
- Контракт LLM-бэкенда — `agents/base.py::LLMBackend` (`runtime_checkable` Protocol).
- `ToolSpec` — immutable `@dataclass(frozen=True)`; `capabilities` — `frozenset[str]`
  (закрытый набор: `fs.read`, `fs.write`, `net`, `shell`, `git`, `ui`, `secrets`, `browser`).

**Синхронность и потоки**
- `core/`, `state/`, `tools/{file_ops,current_datetime,git_ops*,test_ops}` — синхронные.
- `fs/`, `agents/`, асинхронные `tools/` — `async def`.
- Qt-код **не смешивать** с async: блокирующий вызов — через `QThread` + `asyncio.run(...)`;
  результаты — сигналами.
- Отмена — кооперативная: `cancel_request()` → флаг в цикле стрима; UI эскалирует
  `quit()` → `wait()` → `terminate()`.
- `local_llm_client.py` — пример моста блокирующего C++-инференса в async
  (`threading.Thread` + `asyncio.Queue` + `run_coroutine_threadsafe` + `Lock`).
- Подписчики `EventBus` **не должны блокировать GUI**; тяжёлая работа — в worker'е.
  Для UI-подписки используется `QtEventBus` (QObject-мост; AutoConnection → QueuedConnection).

**Логирование**
- `from loguru import logger` на уровне модуля. Никакого `logging.getLogger`, кроме двух
  точечных подавлений в `main.py` (posthog/chromadb).
- Не пиши секреты и полные пути с пользовательскими данными в лог — `_redact_filter`
  (0.1.2) вырежет их, но лучше не полагаться на автозамену.
- События — в `EventBus`, логи — текст. Не дублируй `logger.info` того, что уже эмитится
  в `EventBus` структурированным событием.

**Обработка ошибок**
- `ui/` показывает пользователю сообщение; `tools/`/`state/` — возвращают строку/дефолт;
  `agents/ollama_client.py` и `core/config.py` бросают типизированные исключения из
  `core.exceptions`.
- **Не глотай исключения молча** (`.ollidesk/rules.md`): минимум `logger.exception(...)`
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
       policy=ToolPolicy.ASK_FIRST,       # AUTO | ASK_FIRST | EXCLUDED
       modes=[AgentMode.AGENT],            # CHAT | PLAN | AGENT
       is_mutating=False,                  # True — меняет состояние
       category="filesystem",              # filesystem | shell | network | git | info
       capabilities=["fs.read"],           # обязательны для Policy Engine
       timeout_s=30.0,
       max_output_chars=5000,
       idempotent=False,
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
   - Возвращай `str` (или `dict` с `_llm_text`). Никогда не поднимай исключение наружу.
   - Зависимости (`project_root`, `vector_store`, `search_cache`, `attached_files`,
     `hypotheses`, `security`) — обычные параметры; реестр отфильтрует их из схемы.
2. **Добавить функцию в `tools/registry.py::ALL_TOOLS`**.
3. **Указать `capabilities`** — без этого `effective_policy` не сможет авторизовать.
   `secrets` — всегда excluded (даже в `open`).
4. Проверить: `make lint`, `mypy .`, `make test`.

Порядок политик в `effective_policy`: **secrets → security → mode → freedom → profile →
session → config → decorator**. Явные `set_policy` из UI попадают в `session_overrides`;
`chat_auto_tools` (в CHAT) — тоже session-override.

**Регистрация — только через ALL_TOOLS.** Декоратор не добавляет инструмент в реестр
автоматически — состав инструментов должен быть явным.

## 8. Как добавить UI-элемент

1. Логика — в методе виджета или в пакетном модуле `ui/`; **никакой бизнес-логики в слоте**.
2. Тяжёлое/блокирующее — новый `QThread`-класс в `ui/chat/threads.py` (или рядом с виджетом)
   с сигналами; в `run()` — `asyncio.run(...)`.
3. Доступ между виджетами — через сигналы, а не прямыми вызовами приватных методов.
4. Тексты интерфейса — по-русски, в константах/словарях виджета.
5. Если действие эмитит событие в `EventBus` — подписывайся через `EventBus.subscribe(kind, cb)`,
   **не блокируй GUI** (тяжёлые подписчики — в worker'е).
6. Если виджет читает асинхронные данные — используй `AsyncResource[T]` (guard `loaded()`),
   чтобы не было пустоты/скачков на `loading`.

## 9. Тесты

```bash
make test                      # весь набор
uv run pytest tests/core -q    # один пакет
uv run pytest -k "test_tool_call" -q
uv run pytest --cov=agents --cov=core --cov=fs --cov=state --cov=tools --cov=ui --cov-report=term-missing
```

Соглашения:
- `asyncio_mode = "auto"`.
- Группировка в `class TestXxx:` (без `unittest.TestCase`).
- Изоляция — через встроенный `tmp_path`; фикстура `temp_project` — в `tests/conftest.py`.
- Моки — `unittest.mock` (`AsyncMock`, `MagicMock`, `patch.object`).
- HTTP мокается подменой приватной фабрики: `patch.object(client, "_get_client", return_value=mock)`.
- Эмбеддинги — `patch.object(vs.embed_client, "embed", AsyncMock(return_value=[[...]]))`.
- Покрытие: `agents`, `core`, `fs`, `state`, `tools`, `ui`.

Фактически тесты есть для:
- **core:** `test_config.py`, `test_context_budget.py`, `test_system_prompts.py`,
  `test_event_bus.py` (2.5), `test_events.py`, `test_policy.py` (2.7),
  `test_redaction.py` (0.1.2), `test_audit.py` (0.1.1), `test_sandbox.py`,
  `test_slm_models_io.py` (9.3.3), `test_memory_service.py` (4.3),
  `test_freedom_level.py` (9.12).
- **agents:** `test_ollama_client.py`, `test_ollama_client_parts.py`,
  `test_model_router.py`, `test_gpu_utils.py`, `test_tool_registry.py`,
  `test_tool_registry_errors.py`, `test_tool_spec.py` (2.6),
  `test_agent_loop.py`, `test_history_compactor.py`, `test_investigation.py`,
  `test_tool_call_parser.py`, `test_soft_budget.py` (2.10),
  `test_browser_client.py` (2.11), `test_search_providers.py` (2.13),
  `test_model_pool_park.py` (4.1), `test_hierarchical_context.py` (4.2),
  `test_verifier_in_loop.py` (10.4), `test_lsp_bridge_integration.py` (10.3),
  `test_git_context.py` (9.4), `test_orchestrator_coding.py` (10.7),
  `test_model_routing_coding.py` (10.6), `test_hf_search.py` (9.3.1),
  `test_benchmark.py`, `test_mcp_client.py` (7).
- **fs:** `test_indexer.py`, `test_vector_store.py`, `test_bm25.py`,
  `test_document_extractors.py`, `test_project_map.py`.
- **state:** `test_session_store.py` (`parts_json` round-trip + миграция),
  `test_project_settings.py` (git_branch/hash), `test_inbox.py` (2.12).
- **tools:** `test_attached_files.py`, `test_file_ops.py`, `test_file_ops_diff.py`,
  `test_shell_ops.py`, `test_shell_allowlist.py` (0.1.1), `test_apply_patch.py` (10.1),
  `test_run_tests.py` (10.2), `test_find_references.py` (10.3),
  `test_stale_check.py` (10.9), `test_snapshot.py` (10.9),
  `test_git_ops.py`, `test_git_commit.py` (9.4), `test_lsp_diagnostics.py` (10.8),
  `test_browser_ops.py` (2.11), `test_web_search_chain.py` (2.13), `test_mcp_ops.py` (7).
- **ui:** `test_tool_confirmation_coerce.py`, `test_auto_scroll.py`, `test_theme_tokens.py`,
  `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`,
  `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`,
  `test_panel_mro.py`, `test_profiles.py`, `test_reasoning_widget.py` (0.1.3a),
  `test_history_save.py` (0.1.3b), `test_history_restore.py` (0.1.3c),
  `test_model_selection.py` (0.1.4), `test_live_events.py` (2.5),
  `test_async_resource.py` (2.9), `test_async_resource_integration.py` (9.11),
  `test_permissions_dashboard.py` (9.10), `test_pipeline_selectors.py` (9.3.2),
  `test_hf_dialog.py` (9.3.1).
- **tests/bench:** `test_coding_bench.py` (10.5).

Новый код без теста — неоконченная работа; особенно для `Orchestrator`, `ModelPool`,
`investigation`, `evidence`, `diagram`, `verifier`, `critic`, `EventBus`, `Policy Engine`,
`apply_patch`, `run_tests`, `_verify_and_maybe_regen`, `_run_coding_flow`.

**`[план]`** — оставшиеся тесты: `test_indexing_process.py` (2.3) — процессы вместо потоков.

## 10. Документация

| Файл | О чём |
|---|---|
| `README.md` | Быстрый старт, стек |
| `docs/USER_GUIDE.md` | Руководство пользователя |
| `docs/PLAN_MODERNIZATION.md` | **План доработки проекта** (актуальные задачи, приоритеты, статус; фазы 0.1–10) |
| `docs/Roadmap.md` | Идеи и заметки по развитию |
| `docs/Model.md` | Заметки по моделям |
| `docs/DEPENDENCY.md` | Управление зависимостями |
| `docs/VERSION.md` | Релизный процесс (SemVer, git-cliff, bump) |
| `docs/Notes.md` | Заметки по окружению и командам |
| `docs/SECURITY.md` | **0.1.1** Политика безопасности: shell allowlist, dangerous mode, redaction, audit log |
| `docs/BENCHMARK.md` | **4.7.B** Результаты замеров TTFT/tok/s/load |
| `docs/CODING_BENCH.md` | **10.5** Метрики Coding Bench (pass@1, tool success, invalid args) |
| `docs/MEMORY.md` | **4.3** Эпизодическая память (схема, recall, приватность) |
| `docs/SEARCH.md` | **2.13** Цепочка поиска (провайдеры, кэш, fallback) |
| `docs/BROWSER.md` | **2.11** Три режима браузера (isolated/extension/full) |
| `.ollidesk/rules.md` | Правила проекта, инжектируются в агента |
| `.ollidesk/config.yaml` | Конфигурация OlliDesk для этого репозитория |

При изменении архитектуры или процесса разработки обновляй `AGENTS.md` и
`docs/PLAN_MODERNIZATION.md`.

## 11. Git и коммиты

- Conventional Commits (настроена в `cliff.toml`): `feat:`, `fix:`, `chore:`, `docs:`,
  `refactor:`, `test:`, `upd:`, `add:`. Сообщения — по-русски, с областью через `|`:
  `chore: SLM - загрузка | стабилизация`.
- Версия — SemVer (`docs/VERSION.md`). Сейчас `0.1.2` в `pyproject.toml`.
  Единственный источник для кода — `core/_version.py::__version__`, его поднимает
  `bump-my-version bump patch|minor` вместе с `pyproject.toml`, ставит тег `v{new_version}`
  и генерирует `CHANGELOG.md` хуком `git cliff`. Вручную не менять.
- Коммит не делать без явной просьбы пользователя. Перед коммитом проверить
  `git status`, `git diff`, `git log --oneline -10`.
- CHANGELOG генерируется `git cliff --tag v<version> -o CHANGELOG.md`, теги — аннотированные (`-a`).

## 12. Подводные камни

1. **Qt-объекты только в GUI-потоке.** Никогда не трогай виджеты из `QThread` — только через
   сигналы.
2. **Версия — только в `core/_version.py::\__version__`.** `importlib.metadata` в рантайме
   **не** используется: метаданные обновляются только при переустановке и сразу после `bump`
   отдают старую версию.
3. **`ConfigLoader` — глобальный синглтон** (`core/config.py`, модульный `config_loader`).
   В тестах переопределяй `loader.global_config_path`. Схема v3 (`core/config_v3.py`,
   `core/config_migrate.py`) сосуществует с v2; не смешивай `AppConfig` и `ConfigV3` без
   явной миграции.
4. **`@file` попадает в контекст не целиком.** Промпт получает только начало и конец файла
   в пределах `attached_char_budget(ModelRouter.num_ctx)`; середина — через `read_attached_file`.
5. **`@dir` переключает каталог, но не сессию.** `_on_directory_attach_requested` намеренно
   не трогает историю чата и `recent_projects`.
6. **ChromaDB тянет телеметрию.** `main.py` подменяет `sys.modules["posthog"]` заглушкой.
   Не удаляй этот код и не меняй порядок импортов.
7. **Monaco должна быть скачана** (`make download-monaco`), иначе вкладка «Редактор» покажет
   заглушку «WebEngine недоступен».
8. **SLM требует отдельного wheel** `llama-cpp-python` под конкретный Python/Windows —
   см. `docs/Notes.md`.
9. **`main.py` выставляет `AA_ShareOpenGLContexts` до `QApplication`** — обязательное
   условие для `QWebEngineView`.
10. **Сессии без проекта** используют `project_path = "global"`, а не `Path.cwd()`.
11. **`slm.n_threads = 0` — «авто».** `detect_optimal_threads()` возвращает число физических
    ядер (минимум 2).
12. **Менять модель в середине диалога нельзя.** У моделей разный `n_ctx`; смена бэкенда
    переполнит контекст. Ограничение зафиксировано в Фазе 4.7.D плана.
13. **Двухмодельный пайплайн требует ДВЕ разные модели.** `select_pair()` возвращает
    `analyst=None`, если planner и analyst — одна модель; `_ModelPoolLoadThread` делает
    fail-fast. Загрузка ролей последовательная, с per-role timeout 90 с; не заменяй
    на параллельный `asyncio.gather` без защиты VRAM.
14. **`Orchestrator` не стримит токены.** Отдаёт `RunResult` через `orchestrator_result`.
    При ошибке/таймауте (10 минут, `ORCHESTRATOR_TIMEOUT_S = 600.0`) `AgentChatThread`
    делает fallback на `AgentLoop`.
15. **Паспорт исследования строится только при наличии summary/финального текста.**
    Если финальный синтез не сформирован, `ChatPanel` показывает явное предупреждение в
    разделе «Выводы агента».
16. **CPU-only analyst: `n_ctx` берётся из `n_ctx_hint` профиля и `budget_by_ram`** (4.2).
    Жёсткого cap 8192 больше нет; не поднимай `n_ctx` вслепую — сначала проверь RAM и
    реальную скорость генерации (`agents/benchmark.py`).
17. **`ChatMessage`/`ChatChunk` — типизированные части (`MessagePart`).** `content` /
    `reasoning_content` / `tool_calls` — read-only `@property`, собираемые из `parts`.
    Для изменения используй `ChatMessage.with_content(...)` или конструируй
    `ChatMessage(parts=[...])` напрямую. `model_validator(mode="before")` принимает старый
    стиль, но **игнорирует** его, если `parts` уже передан.
18. **`run_shell_command` — единственный инструмент с побочными эффектами вне файлов
    проекта.** Не добавляй его в `chat_auto_tools`, не ослабляй `modes=[AGENT]` /
    `policy=ASK_FIRST` / песочницу `cwd`. Allowlist + dangerous mode + audit log — единый
    штатный путь; чёрный список сохраняется даже в dangerous mode.
19. **Стриминг SLM по токенам реализован** (`LocalLLMClient._generate`): `event_type="delta"`
    на каждый токен, `start`/`end` по границам, tool_calls — одним `ToolCallPart` после
    стрима. `AgentLoop`/`ChatPanel` получают `on_text_delta`/`on_reasoning_delta`/
    `on_stream_start`/`on_stream_end` и обновляют `tok/s` в `StatusDashboard`.
20. **История 2.0 реализована.** `_current_assistant_parts` аккумулирует части в
    `_on_chunk`/`_on_thinking`/`_on_tool_*`; `_build_assistant_message` собирает
    `ChatMessage(parts=[...])`, `_render_assistant_parts` восстанавливает композитный пузырь
    при `load_history`. `session_store` пишет `parts_json` + `content`.
21. **`ReasoningWidget` — авто-высота с максимумом 40 % viewport чата** + внутренний
    авто-скролл + `QPropertyAnimation` при разворачивании/сворачивании (150 мс). `setFixedHeight`
    больше не применяется.
22. **`EventBus` реализован.** Подписчики **не должны блокировать GUI**; тяжёлая работа —
    в worker'е через `QThread`. `QtEventBus` — QObject-мост, AutoConnection → QueuedConnection.
    `EventBus.emit()` не держит `lock` во время вызова подписчиков — защита от дедлока при
    рекурсивном `emit()`.
23. **`ToolSpec`/`capabilities` реализованы.** Для новых инструментов `capabilities` —
    обязательны; `secrets` — всегда excluded в `effective_policy`. `Policy Engine` заменяет
    `_apply_config_policies`; приоритет `security > secrets > mode > freedom > profile >
    session > config > decorator`.
24. **Shell allowlist реализован** (0.1.1). Dangerous mode (`allow_arbitrary_shell=True`)
    снимает allowlist, но чёрный список остаётся. Каждый вызов в audit.db. Не расширяй
    dangerous-поверхность без явного решения.
25. **`slm_models.yaml` реализован** (9.3.3). Каталог моделей — в `~/.ollidesk/slm_models.yaml`;
    `_DEFAULT_SLM_MODELS` в `core/config.py` — только fallback для миграции. `config.yaml`
    больше **не** содержит `slm.available_models` (`ConfigLoader._save_yaml` его вырезает).
26. **`apply_patch` реализован** (10.1). `write_file` — для новых/полной замены;
    `apply_patch` — для минимальных правок с stale check (`expected_hash`). При несовпадении
    контекста — `ToolResult` `error_category=DIFFERENT_PATH` (не пишет).
27. **Verifier подключён в обычный AgentLoop** (10.4). `_verify_and_maybe_regen` — одна
    регенерация при провале; второй провал — пометка `⚠️ не подтверждено: …`.
28. **`_current_assistant_parts` реализован** (0.1.3b). В research-ветке
    `_on_agent_finish` собирает `ChatMessage(parts=[TextPart(final_text)])` и сохраняет
    в сессию.
29. **EventBus-sink и подписчики.** Все подписчики — неблокирующие. Тяжёлое (I/O, парсинг)
    — в worker'е через `QThread`. `logger.info` тоже проходит через sink.
30. **`effective_policy` приоритет.** `secrets` → `security` → `mode` → `freedom` → `profile`
    → `session` → `config` → `decorator`. `confirm.web_search` — внутри security-слоя.
    Старые `tool_policies: {web_search: ask_first}` больше не включают диалог — только
    `confirm.web_search: true`.
31. **`browser_control` три режима** (2.11). `isolated` по умолчанию; `extension` — через
    WebSocket-релей к расширению; `full` — только при явном `full_access_acknowledged=True`,
    иначе `BrowserError`. `create_backend(config)` возвращает `None`, если playwright/
    websockets не установлены.
32. **`Durable inbox`** (2.12). `ChatPanel._send_message` пишет в inbox до запуска
    `AgentChatThread`; `claim()` в `run()`, `complete`/`fail` в `_on_agent_finished`. `recover()`
    при старте: `running → pending`. Лимит `MAX_PENDING_PER_SESSION = 20`.
33. **`MemoryService`** (4.3). FTS5-поиск (BM25), при отсутствии FTS5 — LIKE-fallback.
    `_remember_if_needed` после успешного/частичного завершения задачи; `recall` при
    сборке `AgentContext`. Всё локально; `MemoryConfig.encrypt` — заглушка.
34. **`Search chain`** (2.13). Провайдеры пробуются по порядку из `SearchConfig.providers`;
    падение одного не роняет цепочку; кэш `SearchCache` с `cache_ttl_hours`; при полном
    провале — `search.chain.exhausted`.
35. **`Stale check` и снапшоты** (10.9). `apply_patch(expected_hash=...)` сверяет
    `file_fingerprint`; при несовпадении — отказ. `ToolService._maybe_snapshot` создаёт
    файловый снапшот перед мутирующими инструментами (кроме `write_file`/`apply_patch`, они
    создают сами). `restore_snapshot` / `cleanup_old_snapshots(retention_days=7)` —
    `ask_first` / `auto`.
36. **Не путай «сделано» и «запланировано» в `PLAN_MODERNIZATION.md`.** Полностью
    завершённые разделы удалены, чтобы файл оставался компактным; актуальные задачи — в
    фазах 0.1, 2.5–2.9, 4.5–4.9, 5–10. Если правишь код в области, упомянутой в плане,
    сверяйся с текущим разделом и обновляй статус (`[ ]` → `[x]`). **Пункты, помеченные
    как `[план X.Y]`, ещё не реализованы** — не считай их доступными в коде.