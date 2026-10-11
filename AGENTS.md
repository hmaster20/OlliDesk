# AGENTS.md — OlliDesk

Руководство для ИИ-агентов (и разработчиков), работающих в этом репозитории.

**OlliDesk** — локальный AI-ассистент для Windows 10: десктоп-IDE на PySide6 (Qt6), в котором
общаешься с локальными LLM через **Ollama** или встроенный **SLM** (GGUF / llama.cpp),
с RAG-поиском по проекту, вызовом инструментов и встроенным редактором Monaco.

- Актуальная рабочая ветка: **`slm`** (в ней реализована встроенная модель SLM).
- Пакетный менеджер: **uv** (`uv.lock` в репозитории), но `Makefile` использует `.venv` + `pip`.
- Целевая платформа: **Windows 10/11**. Приложение запускается как `python main.py`.

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
├── core/                    # Инфраструктура: конфиг, логирование, промпты, роли, реестр моделей
├── agents/                  # LLM-клиенты (Ollama / SLM / rule-based), ReAct-цикл, реестр инструментов
├── tools/                   # Инструменты (@tool), декларативный список tools/registry.py
├── fs/                      # Индексация проекта + векторное хранилище ChromaDB (RAG)
├── state/                   # Персистентность: SQLite-сессии, настройки проекта
├── ui/                      # Qt-виджеты: главное окно, чат, диалоги, веб-редактор
│   ├── chat/                # Панель чата, потоки, отображение сообщений
│   ├── dialogs/             # Мастер настройки, настройки, модели, SLM, сессии, промпты
│   └── web_editor/          # Monaco (QWebEngineView) + QWebChannel-мост
├── tests/                   # pytest: tests/{core,fs,agents}
├── docs/                    # Документация (см. §10)
├── .ollidesk/               # Локальная конфигурация OlliDesk для ЭТОГО проекта (tracked)
└── .github/workflows/ci.yml # CI: ruff → mypy → pytest → codecov
```

Архивных копий UI в рабочем дереве нет: `unused/` и `ui/unused/` (53 файла, дубликаты
`main_window.py` / `chat_panel.py` / `web_editor`) удалены в Фазе 1.4 плана модернизации.
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
    │ ReAct, router) │   └──────────────┘
    └───────┬────────┘          │
            │                   │
            ▼                   ▼
    ┌────────────────┐   ┌──────────────┐
    │ fs/ (индекс +  │   │ state/ (SQLite│
    │    ChromaDB)   │   │  + YAML)     │
    └───────┬────────┘   └──────────────┘
            └────────┬────────┘
                     ▼
              ┌─────────────┐
              │ core/       │  config, logger, prompts, roles,
              │             │  model_registry, search_cache, exceptions
              └─────────────┘
```

Правило: **`core` не знает ни о ком**, кроме себя. `agents`/`fs`/`state` знают только `core`.
`tools` знает `core` + `agents.tool_registry`. `ui` знает всё. Импорты **абсолютные**
(`from core.app import main`), не относительные.

---

## 4. Пакеты: что где лежит

### `core/` — инфраструктура (синхронный код, без Qt)

| Модуль | Назначение | Ключевое |
|---|---|---|
| `core/app.py` | Bootstrap приложения | `OlliDeskApp`, `main()`; применяет тему, открывает `SetupWizard` при отсутствии конфига |
| `core/config.py` | Конфигурация (PyYAML + pydantic) | `AppConfig`, `ModelConfig`, `SLMConfig`, `SLMModelInfo`, `ConfirmSettings`, `BackendSettings`, `RerankSettings`, `EmbedModels`, `ConfigLoader`, глобальный `config_loader`; енамы `ModelRole`, `ToolPolicy`, `AgentMode` |
| `core/logger.py` | Логирование | `setup_logger(dir, level)` — sink в stderr + rotating-файл |
| `core/_version.py` | Версия приложения | `__version__` — единственный источник для кода; синхронизируется с `pyproject.toml` через `bump-my-version` |
| `core/utils.py` | Пути | `get_app_data_dir()` → `~/.ollidesk`; `get_resource_path()` с учётом `_MEIPASS` |
| `core/exceptions.py` | Иерархия исключений | `OlliDeskError` + `ConfigError`, `OllamaConnectionError`, `ToolsNotSupportedError`, `ModelNotFoundError`, `IndexingError`, `EmbedderError`, `ToolExecutionError` |
| `core/system_prompts.py` | Системные промпты режимов chat/plan/agent | `SystemPromptManager`; шаблоны `{{переменная}}`, проектный → глобальный поиск |
| `core/roles.py` | 13 ролей-персон (Markdown + YAML-frontmatter) | `RoleManager`, `RoleDefinition` |
| `core/model_registry.py` | Каталог моделей + поддержка tool-calling | `ModelRegistry`, `ModelInfo` |
| `core/search_cache.py` | Кэш результатов web-поиска и порядка реранка (SQLite, TTL 24 ч) | `SearchCache.get()/set()`, `get_list()/set_list()` (JSON-список, ключ реранка с префиксом `rerank\|`) |
| `core/context_budget.py` | Оценка токенов и бюджет контекста под окно модели | `estimate_tokens`, `resolve_num_ctx`, `attached_char_budget`, `KNOWN_NUM_CTX` |

### `agents/` — работа с LLM

| Модуль | Назначение |
|---|---|
| `agents/ollama_client.py` | Wire-типы (`ChatMessage`, `ToolCall`, `ChatChunk`, `ChatOptions`, `ToolSchema`) + асинхронный HTTP/stream-клиент Ollama (`OllamaClient`) |
| `agents/agent_loop.py` | ReAct-цикл: сборка системного промпта → стрим → вызов инструментов → повтор. `AgentLoop`, `AgentContext` |
| `agents/tool_registry.py` | `@tool`-декоратор, генерация JSON Schema из type hints, `ToolRegistry.execute()` |
| `agents/model_router.py` | Выбор бэкенда по приоритету из конфига с fallback: rule-based в конце. Также перебор бэкендов для эмбеддингов |
| `agents/local_llm_client.py` | Клиент llama.cpp (GGUF): блокирующий инференс обёрнут в `asyncio`, «мягкий» tool-calling через JSON в тексте |
| `agents/rule_based_fallback.py` | Ответы без LLM (регулярки: погода, время, дата) |
| `agents/download_slm.py` | Скачивание/удаление GGUF с HuggingFace, обработка split-GGUF. CLI: `python -m agents.download_slm` |
| `agents/gpu_utils.py` | Опрос `nvidia-smi`, подбор `n_gpu_layers`, `detect_optimal_threads()` (физические ядра) |

Контракт бэкенда (duck-типинг, общего базового класса **нет**):
`chat()`, `embed()`, `cancel_request()`, `is_cancelled()`, `reset_cancel()`, `check_tools_support()`.
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
всегда ставит `_LANGUAGE_INSTRUCTION` в самое НАЧАЛО системного сообщения (там она
переживает обрезку `_fit_messages`), а `_build_system_prompt` добавляет
`_LANGUAGE_DIRECTIVE` в конец промпта. Температура по умолчанию — `_DEFAULT_TEMPERATURE = 0.3`
(AgentLoop не передаёт `options`, поэтому `slm.temperature` из конфига сейчас не влияет).

### `tools/` — инструменты для модели

Каждая функция помечена `@tool(name, description, policy, modes)`; декоратор **только ставит
метаданные** (`func._tool_meta`), регистрация — явная, в `ui/chat/panel.py::_build_registry`.

| Инструмент | Модуль | Политика по умолчанию | Режимы | async |
|---|---|---|---|---|
| `read_file`, `list_directory` | `file_ops.py` | auto | chat, plan, agent | нет |
| `write_file` | `file_ops.py` | **ask_first** | agent | нет |
| `search_codebase` | `codebase_search.py` | auto | chat, plan, agent | да |
| `web_search` | `web_search.py` | ask_first* | chat, plan, agent | да |
| `get_weather` | `weather.py` | auto | chat, plan, agent | да |
| `get_current_datetime` | `current_datetime.py` | auto | chat, plan, agent | нет |
| `get_exchange_rate`, `get_exchange_rates_multi` | `exchange_rate.py` | auto | chat, plan, agent | да |
| `get_git_status` | `git_ops_tool.py` | auto | chat, plan, agent | нет |
| `create_snapshot`, `undo_last_snapshot` | `git_ops_tool.py` | **ask_first** | agent | нет |
| `read_attached_file` | `attached_files.py` | auto | chat, plan, agent | нет |

Особенности:
- Все инструменты возвращают `str`. **Не бросают исключения** — ошибка возвращается строкой с
  префиксом `Ошибка:` (чтобы модель могла её прочитать и исправиться).
- Зависимости внедряются по имени параметра: `project_root`, `vector_store`, `search_cache`,
  `attached_files`. `ToolRegistry` отфильтровывает их из JSON-схемы (см. `_INJECTED_PARAMS`).
- `tools/file_ops.py::_resolve_path` — песочница: `Path.resolve()` + `relative_to(project_root)`;
  выход за пределы проекта запрещён.
- `tools/codebase_search.py::search_codebase` принимает необязательные `language=`, `path=`
  (префикс пути) и `modified_after=`/`modified_before=` (даты `YYYY-MM-DD`, преобразуются в
  unix-границы дня) — фильтры уходят в `VectorStore.search` (`language`/`path_prefix`/
  `modified_after`/`modified_before`).
- `tools/git_ops.py` — низкоуровневые хелперы (GitPython/stash), `tools/git_ops_tool.py` — `@tool`-обёртки.
- `tools/attached_files.py` — хранилище вложений `@file` (`AttachedFileStore.add/find/render`)
  и `read_attached_file(path, offset, limit)`. В промпт уходит только начало и конец файла
  в пределах бюджета `attached_char_budget(...)`; середина читается инструментом по требованию.
  Регистрируется в `_build_registry` только когда вложения есть.
- \* `web_search` в декораре помечен `ask_first`, но **по умолчанию выполняется без диалога**:
  в `AppConfig` есть секция `confirm` (`enabled`, `web_search`), а `ChatPanel::_apply_config_policies`
  ставит `ToolPolicy.AUTO`, если оба флага выключены. Тумблер — меню File →
  «Подтверждать запросы в интернет».
  **Для `web_search` секция `confirm` важнее `tool_policies`:** старые записи
  `tool_policies: {web_search: ask_first}` больше не включают диалог — его включает
  только `confirm.web_search: true` (или `confirm.enabled: true` для всех инструментов).

### `fs/` — индекс проекта и RAG

- `fs/indexer.py` — `FileIndexer`: обход с учётом `.gitignore` (`pathspec`), фильтры по
  расширению/размеру, SHA-256 хэши, чанкинг по строкам с перекрытием, определение языка
  (`_detect_language`), `incremental_scan()` для догрузки только изменений.
  `chunk_file(content, file_path, mtime=None)` кладёт `language` и (если передан) `mtime`
  в метаданные чанка. Игнор-паттерны собираются в один `PathSpec` в порядке возрастания
  приоритета: `config.ignored_patterns` → `.gitignore` проекта → `.ollideskignore` →
  `.indexignore` → `.ollidesk/indexignore` (позже совпавшее правило выигрывает, поэтому
  `!pattern` в явном файле вернёт файл в индекс). Модели: `FileMetadata`, `TextChunk`.
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
  обычный реранк).
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

Индекс хранится в `<проект>/.ollidesk/vector_db`, состояние — в `~/.ollidesk/project_state.json`.

### `state/` — персистентность

- `state/session_store.py` — `SessionStore` поверх sqlite3: таблицы `sessions` и `messages`,
  короткоживущие соединения, ID сессии вида `session_YYYYmmdd_HHMMSS`. Сохраняются только
  `role` + `content` (tool_calls и картинки — нет).
- `state/project_settings.py` — `ProjectSettings` в `<проект>/.ollidesk/project_settings.yaml`
  (модель, режим, температура, max_tokens).

### `ui/` — интерфейс

Композиция:
```
MainWindow (QMainWindow, ui/main_window.py)
├── QSplitter (горизонтальный, [320, 800])
│   ├── левая панель: заголовок проекта, кнопка реиндексации,
│   │                 QTreeView + GitStatusDelegate, прогресс-бар индексации
│   │                 (скрывается по F4 — View → «Структура проекта»)
│   ├── центральная панель: QTabWidget
│   │   ├── вкладка «Чат»   → ChatPanel (не закрывается)
│   │   └── вкладка «Редактор» → EditorWidget (Monaco)
│   └── правая панель: настройки агента, скрыта по умолчанию
└── QStatusBar: путь проекта + статус Ollama
```

| Модуль | Строк | Назначение |
|---|---|---|
| `ui/main_window.py` | ~1280 | Оболочка, открытие проекта и список последних, оркестрация индексации, меню, переключение темы. Владеет `SessionStore`, `OllamaClient`, `ModelRegistry`, `RoleManager`, `SystemPromptManager`, `FileIndexer`, `SearchCache`. Здесь же `ProjectState`, `IndexingThread`, обработчик `@dir` (`_on_directory_attach_requested`) и F4 (`_toggle_project_structure`) |
| `ui/chat/panel.py` | ~1500 | Панель чата: выбор роли/модели/режима, стриминг пузырей, подтверждение инструментов, детекция бэкендов, пре-RAG-поиск, команды `@web`/`@dir`/`@file`/`@path`. **Здесь единственное место регистрации инструментов** (`_build_registry`) |
| `ui/chat/threads.py` | ~330 | Мост async ↔ Qt: 6 `QThread`-классов, каждый запускает `asyncio.run()`. `RagSearchThread.results_ready` отдаёт `(context, [(file_path, start, end), ...])` — структурированные источники RAG для кликабельного блока |
| `ui/chat/message_item.py` | ~250 | Один «пузырь» сообщения: markdown → HTML (вручную, `re` + `pygments`) → `QTextBrowser`; кнопка «Копировать» под текстом. `ChatActivityItem` — серая строка журнала, `ChatSourcesItem` — кликабельные источники RAG (`source_activated`) |
| `ui/file_tree_delegate.py` | ~160 | Цвета строк дерева файлов по `git status --porcelain`, дебаунс 500 мс |
| `ui/theme.py` | ~180 | `LIGHT_STYLESHEET` / `DARK_STYLESHEET` + палитра |
| `ui/dialogs/wizard.py` | ~260 | Мастер первой настройки (4 страницы). В mypy: `ignore_errors = true` |
| `ui/dialogs/settings_dialog.py` | ~240 | Глобальные и проектные настройки индексации |
| `ui/dialogs/model_manager_dialog.py` | ~245 | Модели Ollama: таблица, sync, `ollama pull` |
| `ui/dialogs/slm_models_dialog.py` | ~295 | Менеджер GGUF-моделей: скачать / удалить / активировать |
| `ui/dialogs/tool_confirmation_dialog.py` | ~152 | Одобрить / отклонить / **отредактировать аргументы** вызова инструмента |
| `ui/dialogs/session_manager_dialog.py` | ~80 | Таблица сессий: открыть, удалить, экспорт |
| `ui/dialogs/prompt_editor_dialog.py` | ~115 | Редактор системных промптов и ролей |
| `ui/web_editor/editor_widget.py` | ~265 | `QWebEngineView` + `PythonBridge` (QWebChannel). `open_file(path, line=0)` открывает файл и (при `line>0`) ставит курсор на строку |
| `ui/web_editor/editor.js` | ~300 | Monaco: загрузка файлов, diff-режим, статус-бар, `openFile(path, line)` с `revealLineInCenter` |

Мост QWebChannel (`PythonBridge`, зарегистрирован как `pythonBridge`):

| Направление | Член | Сигнатура |
|---|---|---|
| JS → Py | `read_file` `@Slot` | `(request_id, path)` |
| JS → Py | `write_file` `@Slot` | `(request_id, path, content)` |
| Py → JS | `file_content_ready` `Signal` | `(str, str)` → `onFileContentReady(...)` |
| Py → JS | `file_write_result` `Signal` | `(str, bool)` → `onFileWriteResult(...)` |

JS выделяет `requestId`, кладёт `resolve` в `pendingRequests` — fire-and-forget превращается в
Promise. Py → JS: `open_file(path, line)`, `show_diff`, `accept_diff`, `reject_diff`, `get_content` — всё через
`page().runJavaScript(...)`. Клик по источнику RAG (`ChatSourcesItem.source_activated`) идёт в
`MainWindow.open_file_in_editor`, который открывает файл в редакторе на нужной строке.

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
       │     источники отдаются в UI как ChatSourcesItem (клик → редактор)
       └─ AgentChatThread (QThread → asyncio.run)
           └─ AgentLoop.run(ctx, history, on_token, on_tool_request, ...)
               ├─ _build_system_prompt():
               │    инструкция по tool-calling + подсказка режима
               │    + системный промпт режима (SystemPromptManager, проектный → глобальный)
               │    + контекст: превью @file-вложений + RAG (VectorStore.search)
               │    + карта структуры проекта (AgentContext.project_map)
               │    + rules.md проекта
               └─ ModelRouter.chat(...) → OllamaClient | LocalLLMClient | RuleBasedFallback
                   ├─ стрим ChatChunk → сигнал chunk_received → пузырь в UI
                   ├─ reasoning_content → сигнал thinking_received
                   └─ tool_calls?
                        ├─ ToolRegistry.execute() → ToolResult
                        ├─ ASK_FIRST: on_tool_request → ToolConfirmationDialog.exec()
                        │                (аргументы можно отредактировать до выполнения)
                        └─ цикл повторяется, пока max_iterations не исчерпан
```

Ключевые флаги: `AgentMode.CHAT` (только чтение, `max_iterations=7`), `PLAN` (только чтение),
`AGENT` (все инструменты, `max_iterations=10`).

Пути с пробелами — в кавычках: `@dir "C:\My Folder\Project" изучи структуру`.

Ключевые точки расширения:

| Что менять | Где |
|---|---|
| Системный промпт режима | `~/.ollidesk/system_prompts/*.md` или `<проект>/.ollidesk/system_prompts/` |
| Правила проекта | `<проект>/.ollidesk/rules.md` (`rules_file` в конфиге) |
| Список и политики инструментов | `ui/chat/panel.py::_build_registry` |
| Параметры индексации | `~/.ollidesk/config.yaml`, `ignored_patterns`, `index_extensions`, `chunk_size_tokens` |
| Бэкенд LLM | `agents/model_router.py` + `core/config.py::ModelConfig` |
| Реранкинг RAG | `fs/reranker.py` + `core/config.py::RerankSettings` (`enabled`/`model`/`top_k`) |
| Раздельные эмбеддинги (код/документация) | `core/config.py::EmbedModels` (`code`/`docs`) + `fs/vector_store.py::category_for_language` |
| Бюджет контекста под модель | `core/context_budget.py` (`CHARS_PER_TOKEN`, `ATTACHED_CONTEXT_SHARE`, `KNOWN_NUM_CTX`) |

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
- Black и isort **удалены** из dev-зависимостей (Фаза 1.2). Форматирование и сортировка импортов —
  только `ruff format` и `ruff check --fix` (правило I001).
- Текущий базис: `ruff check .` — 0 ошибок, `ruff format --check .` — чисто, `mypy .` — 0 ошибок
  (`strict = true`, но с послаблениями ниже). Не усугубляй; снятие послаблений — фаза 1.3
  плана.

**Типизация**
- mypy `strict = true`, но с послаблениями (`disallow_untyped_defs = false`,
  `disallow_untyped_calls = false`, `disallow_any_generics = false`), `check_untyped_defs = true`.
- Новый код обязан иметь аннотации. ChromaDB и llama-cpp не типизированы — оборачивай
  в `cast(Any, ...)` или `# type: ignore[...]` с пояснением.
- Общий базовый класс / `Protocol` у LLM-бэкендов **нет** — контракт держится на duck-типинге.

**Синхронность и потоки**
- `core/`, `state/`, `tools/{file_ops,current_datetime,git_ops*}` — синхронные.
- `fs/`, `agents/`, асинхронные `tools/` — `async def`.
- Qt-код **не смешивать** с async: любой блокирующий вызов оборачивается в `QThread`, внутри
  которого `asyncio.run(self._coro())`. Результаты возвращаются **сигналами**.
- Отмена — кооперативная: `cancel_request()` → флаг, который проверяется в цикле стрима;
  на уровне UI эскалация `quit()` → `wait()` → `terminate()`.
- `local_llm_client.py` — пример моста блокирующего C++-инференса в async
  (`threading.Thread` + `asyncio.Queue` + `run_coroutine_threadsafe` + `Lock`).

**Логирование**
- `from loguru import logger` на уровне модуля. Никакого `logging.getLogger`, кроме двух
  точечных подавлений в `main.py` (posthog/chromadb).
- Не пиши секреты и полные пути с пользовательскими данными в лог.

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
   from agents.tool_registry import ToolPolicy, tool
   from loguru import logger

   @tool(
       name="my_tool",
       description="Краткое описание для LLM.",
       policy=ToolPolicy.ASK_FIRST,   # AUTO | ASK_FIRST | EXCLUDED
       modes=["agent"],                # chat | plan | agent
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
   - Возвращай `str`. Никогда не поднимай исключение наружу.
   - Зависимости (`project_root`, `vector_store`, `search_cache`, `attached_files`) добавляй
     как обычные параметры — реестр отфильтрует их из схемы сам.
2. **Зарегистрировать** в `ui/chat/panel.py::_build_registry`: `self.registry.register(my_tool)`.
3. **Задать политику** там же через `self.registry.set_policy("my_tool", ToolPolicy.AUTO)`.
4. Проверить: `make lint`, `mypy .`, `make test`.

**Порядок политик** в `_build_registry(mode)`: декоратор → режим агента (`_CHAT_AUTO_TOOLS`
в CHAT) → `config.yaml` (`tool_policies`, `confirm`) → тумблеры правой панели UI.
Пользовательские тумблеры побеждают конфиг.

**Регистрация только вручную.** Декоратор не добавляет инструмент в реестр автоматически —
это сделано намеренно, чтобы состав инструментов был явным.

## 8. Как добавить UI-элемент

1. Логика — в методе виджета или в пакетном модуле `ui/`; **никакой бизнес-логики в слоте**.
2. Тяжёлое или блокирующее — новый `QThread`-класс в `ui/chat/threads.py` (или рядом с виджетом)
   с сигналами; в `run()` — `asyncio.run(...)`.
3. Доступ между виджетами — через сигналы (`ChatPanel.settings_changed`,
   `MainWindow.theme_changed`), а не прямыми вызовами чужих приватных методов.
4. Тексты интерфейса — по-русски, добавляй в словарь/константы виджета.

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
  `core/config.py`, `core/context_budget.py`, `fs/*`, `agents/ollama_client.py`,
  `agents/model_router.py`, `agents/gpu_utils.py`, `agents/tool_registry.py`,
  `tools/attached_files.py`. Новый код без теста — неоконченная работа.

## 10. Документация

| Файл | О чём |
|---|---|
| `README.md` | Быстрый старт, стек |
| `docs/USER_GUIDE.md` | Руководство пользователя |
| `docs/PLAN_MODERNIZATION.md` | **План доработки проекта** (актуальные задачи, приоритеты, статус) |
| `docs/Roadmap.md` | Идеи и заметки по развитию |
| `docs/Model.md` | Заметки по моделям |
| `docs/DEPENDENCY.md` | Управление зависимостями |
| `docs/VERSION.md` | Релизный процесс (SemVer, git-cliff, bump) |
| `docs/Notes.md` | Заметки по окружению и командам |
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
11. **Каталогов `unused/` и `ui/unused/` больше нет.** Архивные версии UI удалены в Фазе 1.4
    (53 файла, они даже не были в git — лежали только в рабочем дереве под `.gitignore`).
    Если снова появится старый дубликат `main_window.py`/`chat_panel.py` — не создавай архив,
    положись на историю git.
12. **`slm.n_threads = 0` — это «авто», а не «без потоков».** Значение разбирает
    `detect_optimal_threads()` в `agents/local_llm_client.py` (число физических ядер,
    минимум 2). Ставь `0`, чтобы не хардкодить.
13. **Менять модель в середине диалога нельзя.** У моделей разный `n_ctx`, а история
    чата уже собрана под текущее окно — смена бэкенда переполнит контекст. Это
    ограничение зафиксировано в Фазе 4.7.D плана.