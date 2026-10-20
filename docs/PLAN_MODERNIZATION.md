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
| 17 инструментов (файлы, RAG, web, погода, git, валюты, гипотезы, shell) | ✅ | `tools/` |
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
| **Событийная модель сообщений (`MessagePart`)** | ✅ | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`), `state/session_store.py` (колонка `parts_json`) |
| **Композитный пузырь сообщения** | ✅ | `ui/chat/message_item.py` (`ChatMessageItem` из `ReasoningWidget` + `TextWidget` + `ToolCallCard`) |
| **Выполнение shell-команд агентом** | ✅ | `tools/shell_ops.py::run_shell_command` (ASK_FIRST, AGENT-only, чёрный список, песочница cwd) |
| Компактор истории диалога | ✅ | `agents/history_compactor.py` (вызывается в `AgentLoop`; приоритет: reasoning → tool-результаты → старейшие реплики) |
| Тесты UI / state | ⚠️ | точечно: `test_tool_confirmation_coerce.py` (без `pytest-qt`), `test_project_settings.py`, `test_session_store.py`; UI целиком не покрыт сознательно (см. 1.5) |

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
  - `tests/tools/test_shell_ops.py` — чёрный список (12 паттернов), песочница
    `_resolve_cwd` (8 кейсов), константы, ошибочные ветки `run_shell_command`
    (пустая/без root/вне проекта/blacklist); реальный запуск PowerShell — под
    `skipif` (Windows-only);
  - `tests/agents/test_tool_registry_errors.py` — маппинг исключений в `ErrorCategory`
    (unknown/validation/file/network/generic), `ToolResult.error_category` (default None),
    `_tool_meta` (`is_mutating`/`category`);
  - `tests/agents/test_investigation.py` — `attempted_calls`/`successful_observations`
    (базовый инвариант валидации Observation, React.md 2.4);
  - `tests/agents/test_ollama_client_parts.py` — `ChatMessage`/`ChatChunk` backward
    compat + `OllamaClient._chunks_from_ollama_message`;
  - `tests/state/test_session_store.py` — `parts_json` round-trip, миграция старой БД
    без колонки, устойчивость к битому JSON;
  - `tests/state/test_project_settings.py` — round-trip `show_reasoning`, обратная
    совместимость со старым YAML без поля;
  - `tests/ui/test_tool_confirmation_coerce.py` — `_coerce_value` (30+ кейсов, без
    `pytest-qt`, статический метод);
  - `tests/core/`, `tests/agents/`, `tests/fs/` — по 1–2 модуля на изменённую область.
- Оставшиеся кандидаты, к которым возвращаться **только вместе с правкой кода**:
  ReAct-цикл (`agents/agent_loop.py`), `ToolConfirmationDialog` (полный UI),
  markdown→HTML в `message_item`, `ChatThoughtStream`.
- Порог покрытия (`--cov-fail-under`) не вводим — он давит на написание тестов
  ради метрики. В CI достаточно `ruff` → `mypy` → `pytest`.
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
- [x] Убрать `object` / `Any` из `AgentContext.vector_store` / `.search_cache`: введены
      минимальные протоколы `agents/protocols.py::VectorStoreLike` /
      `SearchCacheLike` / `AttachedFilesLike` (плюс `ChunkLike` / `SearchResultLike`).
      Поля `AgentContext` типизированы: `vector_store: VectorStoreLike | None`,
      `search_cache: SearchCacheLike | None`, `attached_files: AttachedFilesLike | None`.
      `hypotheses: HypothesisStore | None` — прямая типизация без протокола (цикла
      `agents → hypotheses` нет, `hypotheses.py` импортирует только stdlib).
      `hasattr(...)`-проверки в `_count_open_hypotheses` / `_make_research_summary`
      убраны; `# type: ignore[attr-defined]` в `_build_system_prompt` убран.
      `cast(Any, raw_cache)` в `panel._do_chat` / `_do_agent` убран.
- [x] `AgentLoop` больше не знает про `fs`/`core` по duck-типингу: `VectorStoreLike`,
      `SearchCacheLike`, `AttachedFilesLike` — структурные контракты, конкретные
      реализации (`fs.vector_store.VectorStore`, `core.search_cache.SearchCache`,
      `tools.attached_files.AttachedFileStore`) им удовлетворяют (проверяется mypy).

### 2.2 Реестр инструментов
- [x] Перенести список инструментов из `ui/chat/panel.py::_build_registry` в
      `tools/registry.py` (модуль с декларативным списком), чтобы UI не был единственным
      местом регистрации. `tools/registry.py::ALL_TOOLS` теперь **используется** —
      `_build_registry` обходит его и регистрирует всё, кроме `read_attached_file`
      (только при наличии прикреплённых файлов).
- [~] Политики — из `core/config.py::AppConfig.tool_policies`, а не из UI-словаря.
      `_apply_config_policies` читает `tool_policies` и `confirm`, `chat_auto_tools`
      вынесен в `AppConfig`; приоритет декоратора (`ASK_FIRST` у `write_file`)
      всё ещё не переопределён конфигом — только явные `set_policy` из UI.
- [x] **Исправить баг:** `search_cache` не инжектился в `web_search` — добавлен в
      `_INJECTED_PARAMS` (`agents/tool_registry.py`) и прокинут из `ChatPanel` через
      `AgentContext.search_cache`. Рядом добавлены `attached_files` и `hypotheses`.
- [x] Убрать `_resolve_path`-песочницу в пользу единого `SandboxedPathResolver`.
      `core/sandbox.py::SandboxedPathResolver` — единый резолвер путей с проверкой
      принадлежности проекту и запретом `.git`/`.ollidesk`. `tools/file_ops.py`
      использует его в трёх call site (`read_file`/`list_directory`/`write_file`),
      `tools/shell_ops.py::run_shell_command` — для резолва `cwd`. Функции
      `_resolve_path` и `_resolve_cwd` удалены. Тесты песочницы переезжают в
      `tests/core/test_sandbox.py`; кейсы публичного контракта shell-инструмента
      (пустой `project_root` = отказ, пустой `cwd` = корень проекта) остаются в
      `tests/tools/test_shell_ops.py` и проверяются через `run_shell_command`.
- [x] Метаданные `is_mutating` / `category` в `@tool` — реализовано в **4.8.D**
      (словарь русских названий инструментов в UI пока остался: `_on_tool_started`
      и `_on_tool_call_detected` дублируют его для показа иконок; вынос в `ToolCallCard`
      — часть 4.8.B, там `_ICONS` в `ToolCallCard` работает как единственный источник).

### 2.3 Управление потоками и отменой
- [ ] Единый `ThreadPool`/`TaskManager` вместо независимых `QThread`-классов.
      Сейчас в `ui/chat/threads.py`: `AgentChatThread`, `RagSearchThread`,
      `WebSearchThread`, `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`,
      `_ModelPoolLoadThread`; плюс `IndexingThread`, `ModelCheckThread` в `main_window.py`
      и `PullThread` в `model_manager_dialog.py`.
- [ ] Гарантированная отмена `asyncio`-задач (`asyncio.Task.cancel()`), а не только флага
      `cancel_request()`.
- [x] Убрать `new_event_loop()` + `run_until_complete` на GUI-потоке в
      `MainWindow._do_check_ollama`, `_save_project_state`, `_stop_all_threads`.
      `_do_check_ollama` переведён на `_OllamaCheckThread` (сигналы `result(bool)` +
      `models_ready(bool, list)`; `MainWindow` слушает `models_ready`, `ChatPanel` —
      `result`); `_save_project_state` переехал в
      `IndexingController._on_thread_finished` и больше не сканирует проект повторно
      (`IndexingThread.state_ready` отдаёт готовое состояние после инкрементального
      скана — без `sha256`-обхода всего проекта на GUI-потоке);
      `_stop_all_threads` ждёт `_ollama_check_thread` без `new_event_loop`
      (`httpx.AsyncClient` убирается GC при выходе процесса — утечки нет).
- [~] `GitStatusDelegate._do_refresh` и `PullThread` — убрать блокирующий `subprocess`
      с GUI-потока. `GitStatusDelegate._do_refresh` переведён на `QThreadPool` +
      `_GitStatusWorker`: `git status --porcelain` и определение ветки/тега выполняются
      в worker-потоке, `_running`-флаг защищает от накопления однотипных проверок.
      `PullThread` в `model_manager_dialog.py` остался — объединить с
      `_DownloadThread` (см. 4.6.D).
- [ ] Корректно отменять/удалять потоки при закрытии окна (`closeEvent`).

### 2.4 Разделение `ui/main_window.py` и `ui/chat/panel.py`
- [x] **Меню вынесено в `ui/menus.py`** (`AppMenuBar(QMenuBar)`): построение
      меню + обработчики checkable-пунктов (project structure, confirm
      web_search) + подменю Recent Projects живут отдельно; `MainWindow`
      подключает сигналы (`open_project_requested`, `settings_requested`,
      `slm_models_requested`, `edit_prompts_requested`, …) к своим слотам.
      `_setup_menu` из ~180 строк схлопнулся до ~20 строк связывания.
      Удалены методы `_config_confirm_web_search`, `_on_confirm_web_search_toggled`,
      `_update_recent_projects_menu` (логика переехала в `AppMenuBar`).
      `AppMenuBar.set_config(...)` вызывается после `self.config = load_config(...)`
      в `_run_wizard` / `_open_settings` / `_open_slm_manager` — иначе
      `confirm.web_search` сохранялся бы в устаревший объект конфига.
- [x] **Публичный API `ChatPanel` вместо приватных полей**:
      `update_mode_tooltips()` / `update_model_list()` / `set_mode_by_name(mode)` /
      `get_show_reasoning()` / `set_refresh_button_text(text)`.
      `MainWindow` больше не обращается к `chat_panel._update_mode_tooltips`,
      `chat_panel.mode_combo`, `chat_panel._show_reasoning`,
      `chat_panel._update_model_list`, `chat_panel.refresh_model_btn`.
      `core/app.py` переведён на `set_mode_by_name` вместо
      `mode_combo.setCurrentIndex`.
- [x] **`main_window.py` разбит на контроллеры**:
  - **`ui/file_tree_controller.py::FileTreeController`** — `QTreeView` +
    `GitStatusDelegate` + все операции над деревом (`_tree_new_file` /
    `_tree_new_folder` / `_tree_rename` / `_tree_delete` / `_tree_copy` /
    `_tree_cut` / `_tree_paste` / `_tree_refresh`, контекстное меню,
    F2/Del/Ctrl+C/X/V/N/R, `_reveal_in_explorer`, `_is_inside_project`).
    Публикует сигналы `file_open_requested(str)` / `file_renamed(str, str)` /
    `file_deleted(str)` / `branch_changed(str)`; `MainWindow` подключает их
    к `open_file_in_editor` / `editor_widget.rename_open_file` /
    `editor_widget.close_file_silently` / `_on_branch_changed`.
    Поля `file_tree`, `file_system_model`, `_git_delegate`, `_fs_clipboard`,
    `_empty_model` больше не принадлежат `MainWindow`.
    `FileTreeController.git_delegate` — публичное свойство для
    `MainWindow._write_file` (после Ctrl+S в редакторе обновляем подсветку).
  - **`ui/indexing_controller.py`** — `IndexingThread` (QThread) +
    `ProjectState` (per-project JSON) + `IndexingController(QObject)`.
    Публикует сигналы `progress(int, int)` / `finished()` / `error(str)` /
    `summary(list, list)`; `MainWindow` подключает `_on_index_progress` /
    `_on_index_finished` / `_on_index_error` / `_on_indexing_summary`
    (это чистые UI-слоты — рисуют прогресс-бар, статус, reindex-кнопку).
    `_start_indexing` / `_reindex_project` в `MainWindow` — тонкие обёртки
    над `indexing_controller.start()` / `.reindex()`. `_save_project_state`
    и `_previous_state` переехали в контроллер (`_on_thread_finished` сам
    сохраняет `ProjectState`, состояние приходит из worker'а через
    `state_ready` — без повторного `scan_project` на GUI-потоке).
    `MainWindow._stop_all_threads` вызывает `indexing_controller.stop()`;
    `closeEvent` сбрасывает делегат через
    `file_tree_controller.set_project_root(None)`.
    Удалены ~30 методов/атрибутов из `MainWindow`.
  - `ModelCheckThread` **оставлен в `main_window.py`**: одноразовая
    операция, привязанная к жизненному циклу окна (`_start_model_check` /
    `_stop_model_check`). Вынести в `ui/model_check_controller.py` — при
    появлении третьего потребителя (сейчас — один).
- [ ] `ui/chat/panel.py` → `ui/chat/backends.py` + `ui/chat/research.py`.
      `panel.py` всё ещё >2000 строк:
      - `backends.py` — `_detect_and_load_slm`, `_load_slm_async`,
        `_download_and_load_slm`, `_on_slm_*`, `_apply_backend_config`,
        `_detect_backends`, `_check_ollama_async`, `_on_ollama_check_result`,
        `_on_slm_loaded`, `reload_slm`, `_config_slm_enabled`;
      - `research.py` — `_on_research_mode_detected`, `_on_thought_chunk`,
        `_on_final_answer_started`, `_on_final_chunk`,
        `_on_research_summary_ready`, `_show_research_passport`,
        `_should_show_passport`, `_on_orchestrator_event`,
        `_on_orchestrator_result`, `_pipeline_status_label`,
        `_update_pipeline_status`, `_on_pool_loaded`, `_on_pool_error`,
        `_on_pool_progress`, `_ensure_orchestrator`, `reset_pipeline`.
      Объём — ~700 строк миграции, сложная сеть сигналов
      (`_SLMLoadThread`, `_ModelPoolLoadThread`, research-слоты).
      Требует отдельного захода с прогоном на живой сборке.
- [ ] `QCommandLine` вместо `QAction` для меню. На macOS `QAction`-шорткаты
      всё равно уходят в системное меню; `QCommandLine` даёт выигрыш только
      при появлении CLI-режима (`ollidesk --headless`). Отдельная задача.

`MainWindow` из «всё-в-одном» превратился в UI-оркестратор: владеет
виджетами прогресса/статуса и подключает сигналы контроллеров к своим
слотам, но не знает про `QFileSystemModel`, `GitStatusDelegate`,
`IndexingThread` и `ProjectState`. Приватная связь `MainWindow ↔ chat_panel`
(прямой доступ к полям) заменена на 5 публичных методов `ChatPanel`
(см. выше).

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
- [x] **Валидация Observation** (`React.md` 2.4): в `InvestigationState` добавлены
      `attempted_calls` / `successful_observations`; в `AgentLoop.run()` между вызовами
      инструментов и попыткой Final Answer стоит проверка `attempted > 0 and successful == 0`
      (одноразовая — флаг `nudged_observation_failure`); при срабатывании в историю
      вклинивается `_OBSERVATION_NUDGE` с требованием сменить стратегию (search_codebase,
      list_directory, run_shell_command с Test-Path). В synthesis-фазе и на последней
      итерации проверка отключена.
- [x] **Классификация ошибок инструментов** (`React.md` 3.3): в `agents/tool_registry.py`
      добавлен `ErrorCategory` (FIX_ARGUMENTS / DIFFERENT_PATH / RETRY_LATER /
      REPORT_TO_USER) и `ToolResult.error_category`. `ToolRegistry.execute` маппит
      `ValidationError` → FIX_ARGUMENTS, `FileNotFoundError` → DIFFERENT_PATH,
      `httpx.TransportError`/`ConnectionError`/`TimeoutError` → RETRY_LATER,
      остальное → REPORT_TO_USER. В `AgentLoop._create_tool_result_message` передаётся
      `error_category`, к тексту ошибки дописывается `retry_hint` из `_RETRY_HINTS`
      (после усечения, чтобы не потерялся). Нудж «все вызовы упали» — отдельный, поверх этой
      классификации.
- [x] **Внешний верификатор после `write_file`** (`React.md` 4.2): в `tools/file_ops.py`
      добавлен `_verify_python_syntax` (`python -m py_compile` для `.py`, `CREATE_NO_WINDOW`,
      таймаут 10 с, любые ошибки — тихий пропуск). `write_file` вызывает её после записи
      и при ошибке дописывает к Observation `⚠️ Синтаксическая ошибка Python (py_compile):\n…`.
      Для Orchestrator это уже было закрыто через `agents/verifier.py`; теперь
      покрыт и `AgentLoop`.
- [x] **История tool-сообщений в сессиях** (`React.md` 1.4): в `state/session_store.py`
      добавлена колонка `parts_json TEXT` (ALTER TABLE идемпотентный, через
      `PRAGMA table_info`), `save_message` пишет `message.parts` в JSON (плюс `content`
      для обратной совместимости), `get_history` читает `parts_json` с фоллбэком на
      `content`. `reasoning_content` и `tool_calls` теперь переживают перезагрузку
      сессии — при условии, что они попали в `ChatMessage.parts` (см. 4.8.B — сборка
      `ChatMessage` из виджета при finish).
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
- [x] **`ChatMessageItem` рендерит `tool_calls`** (`React.md` 9.4): реализовано как
      композитный пузырь (4.8.B) — `ToolCallCard` встраивается в пузырь через
      `ChatMessageItem.append_tool_call()`, вызывается из `panel._on_tool_started`.
      В research-режиме параллельно работает `ChatThoughtStream` (сворачиваемый лог).
- [x] **Кнопка «Продолжить» после исчерпания итераций** (`React.md` 9.3): в
      `ChatPanel._setup_ui` добавлена `continue_btn` (скрыта по умолчанию);
      `_on_iteration_limit_exceeded` показывает её и сбрасывает research-флаги;
      `_continue_after_limit` перезапускает запрос с удвоенным лимитом
      (`min(max(current*2, current+10), 200)`), используя сохранённые
      `_last_run_params` (kind / text / mode / rag_context / max_iters).
      `_do_chat`/`_do_agent` принимают `override_limit`; `_clear_chat`/`_stop_generation`/
      `_send_message` скрывают кнопку. `AgentChatThread` при `AgentIterationLimitError`
      больше не эмитит `error_occurred` — только `iteration_limit_exceeded` (это не сбой,
      а исчерпание бюджета шагов).
- [x] **`_CHAT_AUTO_TOOLS` в конфиг** (`React.md` 9.1): в `AppConfig` добавлено
      `chat_auto_tools: list[str]` (со `default_factory`); `_build_registry` читает из
      конфига и падает на встроенную константу при ошибке чтения. Проектный
      `.ollidesk/config.yaml` может переопределить список (по аналогии с
      `tool_policies` — проект без web-доступа уберёт `web_search`). `run_shell_command`
      в список **не** входит — только AGENT-режим.
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
- [x] **Типизированное редактирование аргументов** (`React.md` 9.2): `ToolConfirmationDialog._edit_arguments`
      открывает `QTableWidget` с колонками «Аргумент» (read-only) / «Значение» (editable) /
      «Тип» (read-only, серый). `_coerce_value(raw, orig_type)` восстанавливает
      int/float/bool/list/dict/None; при ошибке парсинга — оставляет строкой (никогда не
      падает). Покрыто `tests/ui/test_tool_confirmation_coerce.py` (30+ кейсов).
- [ ] **`model_digest` в `ModelInfo`** (`React.md` 8.3): инвалидировать `supports_tools`
      при смене версии модели.
- [ ] **Warning о `n_gpu_layers` в UI** (`React.md` 5.3): показывать итоговое число слоёв в
      статус-строке при загрузке SLM. Сейчас `optimize()` пишет в лог, но в UI не выводится.
- [x] **Multi-tool-call у SLM** (`React.md` 5.1): `LocalLLMClient._parse_tool_calls`
      возвращает список из `extract_tool_calls` (JSON + function-call), в `chat()` все
      вызовы уходят одним `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`.
      `AgentLoop` через `pending_tool_calls.extend(part.calls)` выполняет все.

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
один зелёный прогон тестов. **Реализовано в одном заходе** — `content` оставлен
read-only property для обратной совместимости (см. 4.8.A).

### 4.8.A — Модель данных и события (блокирует 4.8.B) — **ЗАВЕРШЕНА**

- [x] **`MessagePart` вместо `content: str`** (`agents/ollama_client.py`): базовый
      `kind`-discriminated union — `TextPart` / `ReasoningPart` / `ToolCallPart` /
      `ToolResultPart`; у `ChatMessage` — `parts: list[MessagePart]`. `content` /
      `reasoning_content` / `tool_calls` — read-only `@property`, собираемые из
      соответствующих частей. `model_validator(mode="before")` конвертирует старые
      конструкторы `ChatMessage(content=..., reasoning_content=..., tool_calls=...)`
      в `parts` (если `parts` уже передан — старые поля игнорируются, чтобы
      `model_copy(update=...)` не ломал части).
- [x] **Событийный `ChatChunk`** (`agents/ollama_client.py`): `part: MessagePart | None`
      + `event_type: Literal["start", "delta", "end", "tool_call"] | None` вместо плоских
      `content` / `reasoning_content`. Read-only `@property` для обратной совместимости;
      `model_validator(mode="before")` конвертирует старый стиль.
- [x] **Клиенты отдают события**: `OllamaClient._chunks_from_ollama_message` разбирает
      `message` на отдельные `ChatChunk` (reasoning → text → tool_call, в этом порядке);
      `LocalLLMClient` отдаёт `ChatChunk(part=TextPart/ToolCallPart, event_type="delta"/"tool_call")`
      и `event_type="end"` при завершении. `LocalLLMClient._convert_messages` использует
      read-only property и подаёт `reasoning_content` обратно модели в обёртке
      `<thought>…</thought>` для поддержки chain-of-thought.
- [x] **`SessionStore` хранит `ChatMessage` целиком** (это же — 4.6.B «История
      tool-сообщений в сессиях»): колонка `parts_json TEXT` в `messages`,
      ALTER TABLE идемпотентный (`PRAGMA table_info`), `save_message` пишет
      `parts` в JSON, `get_history` разбирает через `TypeAdapter(MessagePart)`,
      невалидные части пропускаются с warning, битый JSON — fallback на `content`.
      Старые БД без `parts_json` читаются через `content`.
- [x] **Переходный период.** `ChatMessage.content` — read-only `@property`, собираемое
      из `TextPart`. Все потребители (`local_llm_client`, `SessionStore`, `agent_loop`,
      `ui/chat/*`) работают через property. **Решение:** оставлено навсегда как
      долговременный API — переписывать 200+ мест использования на `parts[0].text`
      бессмысленно, а `content` — самый ergonomicный способ получить «текст ответа».
      При желании убрать — отдельным большим заходом.

### 4.8.B — Интерфейс: композитный пузырь сообщения — **ЗАВЕРШЕНА**

- [x] **`ChatMessageItem` → композитный виджет** (`ui/chat/message_item.py`):
      `ChatMessageItem` теперь собирается из `ReasoningWidget` + `TextWidget` +
      `ToolCallCard` по мере стриминга. Старый API (`ChatMessageItem(role, content, thinking=…)`,
      `update_content`, `update_thinking`, `.content`/`.thinking`) сохранён; новый
      API — `append_text(token)` / `append_reasoning(token)` / `append_tool_call(name, args_json)` /
      `finalize()`. `panel._ensure_assistant_widget` гарантирует наличие пузыря,
      `_on_thinking`/`_on_chunk`/`_on_tool_started` пишут в него через `append_*`,
      `_on_agent_finish`/`_send_finished`/`_on_orchestrator_result` вызывают `finalize()`
      (сворачивает reasoning).
- [x] **`ReasoningWidget`** — сворачиваемый блок для `ReasoningPart`. Развёрнут на
      первом токене, остаётся развёрнутым до `finalize()` или ручного сворачивания.
      Заголовок с иконкой 💭 и счётчиком символов; тело — `QTextBrowser` с
      курсивом, auto-height до 240px. `append_text`/`set_text`/`collapse`. Тумблер
      «Показывать размышления» в правой панели (Phase 6) — включён/выключен через
      `ProjectSettings.show_reasoning`.
- [x] **`ToolCallCard`** — виджет для `ToolCallPart`: иконка по имени + имя +
      компактные аргументы (`key=value`, обрезка длинных значений до 60 символов).
      Встраивается в пузырь через `ChatMessageItem.append_tool_call`. Дифф-вьюер
      `write_file` пока не подключён (нужен сигнал от карточки до
      `editor_widget.show_diff` — отдельный заход).
- [~] **Динамическая статус-строка.** `ChatPanel::_on_tool_started` пишет в `status_label`
      название инструмента и аргументы, `tool_status_label` подсвечивается на время
      работы. Не хватает статуса размышлений (по `reasoning_delta`) и показа результата
      инструмента, а не только его старта.

### 4.8.C — Инструмент `run_shell_command` — **ЗАВЕРШЕНА**

Решение пользователя: включаем, но в жёстких рамках. Агент без shell не может
проверить `Test-Path`, посчитать строки или запустить тесты — на Windows это заметный
выигрыш. Цена — выполнение произвольных команд от имени пользователя, поэтому
политика по умолчанию `ASK_FIRST`, режим только `AGENT`.

- [x] **`tools/shell_ops.py::run_shell_command(command, cwd="", timeout=30, project_root="")`**
      — `async`, возвращает `str`, ошибка возвращается строкой с префиксом `Ошибка:`.
      Песочница `cwd` через `_resolve_cwd` (копия логики `tools/file_ops._resolve_path`:
      запрет выхода за пределы проекта и записи в `.git`/`.ollidesk`).
- [x] **Только PowerShell**: `pwsh` (Core) приоритетно, при отсутствии —
      `powershell.exe`. `cmd` не используется.
- [x] **`CREATE_NO_WINDOW`** при запуске на Windows — без мигающих консолей.
- [x] **Чёрный список** (`_BLACKLIST`) перед запуском: `rm -r/-rf`, `Remove-Item -Recurse -Force`,
      `del/erase /f|/s|/q`, `rd/rmdir /s`, `format <drive>:`, `shutdown`, `reg add`,
      `Set-ItemProperty`, `curl|iwr|Invoke-WebRequest … | iex`. При совпадении — ошибка.
      Список — защита от ошибок модели, **НЕ** от злоумышленника (обходится через
      `cmd /c`, base64, `-EncodedCommand`) — поэтому ASK_FIRST + песочница обязательны.
- [x] **Границы важнее списка запретов:** `cwd` по умолчанию — корень проекта,
      проверка `Path.resolve().relative_to(project_root)`, запрет `.git/**` и
      `.ollidesk/**`.
- [x] **Лимит вывода:** первые 5000 символов (`MAX_OUTPUT_CHARS`) с пометкой
      «[Вывод обрезан до 5000 символов]»; таймаут до 300 с (`MAX_TIMEOUT_S`),
      `asyncio.wait_for` + `proc.kill()` при превышении.
- [x] **Регистрация и промпт:** `policy=ToolPolicy.ASK_FIRST`, `modes=[AgentMode.AGENT]`,
      `is_mutating=True`, `category="shell"`; в `_CHAT_AUTO_TOOLS` **не** входит.
      В `_TOOL_USE_INSTRUCTION` добавлен раздел «СПЕЦИАЛЬНО про Windows-пути»
      (Test-Path вместо догадок), `run_shell_command` указан в списке инструментов
      с явным «только в режиме Agent».
- [x] **Тесты** `tests/tools/test_shell_ops.py`: чёрный список (12 позитивов,
      7 безопасных), `_resolve_cwd` (8 кейсов: root / inside / outside / escape /
      `.git` / `.ollidesk`), константы, ошибочные ветки публичной функции.
      Реальный запуск PowerShell — под `skipif(sys.platform != "win32" or ps_exe is None)`.

### 4.8.D — Метаданные инструментов — **ЗАВЕРШЕНА**

- [x] **`is_mutating` и `category` в `@tool`** (`agents/tool_registry.py::tool`):
      добавлены `is_mutating: bool = False` и `category: str = "filesystem"`,
      попали в `_tool_meta` и в `ToolRegistry.get_all_tools()`. Аннотированы:
      `write_file` (filesystem, mutating), `tool_create_snapshot`/`tool_undo_snapshot`
      (git, mutating), `get_git_status` (git), `web_search`/`get_weather`/
      `get_exchange_rate`/`get_exchange_rates_multi` (network),
      `get_current_datetime`/`record_hypothesis`/`update_hypothesis`/`list_hypotheses` (info),
      `run_shell_command` (shell, mutating). Значения по умолчанию — `False`/`filesystem`,
      чтобы не переписывать все 16 старых `@tool` разом. Словарь русских названий
      в UI (`_on_tool_started`, `_on_tool_call_detected`) пока остался — вынос в
      `ToolCallCard._ICONS` частично закрыт (см. 4.8.B), но три словаря
      ещё дублируются.

### 4.8.E — AgentLoop и контекст

- [x] **`AgentLoop` обрабатывает события, а не `current_text`**: dispatch по
      `isinstance(part, ...)` в двух циклах (`_final_call_no_tools` и `run`) —
      `ReasoningPart` → `on_thinking`, `TextPart` → `on_token`/`on_thought`,
      `ToolCallPart` → `pending_tool_calls.extend(calls)`. [4.8.E] Добавлены
      низкоуровневые event-callbacks: `on_text_delta` / `on_reasoning_delta` /
      `on_stream_start` / `on_stream_end`; клиенты (`OllamaClient`,
      `LocalLLMClient`) эмитят явный `event_type="start"` в начале стрима.
      В `AgentChatThread` проброшены 4 новых сигнала (`text_delta`,
      `reasoning_delta`, `stream_started`, `stream_finished`), в `ChatPanel`
      подключены к `logger.trace` — UI-поведение не изменилось (высокоуровневые
      `chunk_received`/`thinking_received` остаются источником для композитного
      пузыря). Существующие `on_thinking`/`on_thought`/`on_token` сохранены
      как высокоуровневые колбэки.
- [x] **Базовый приоритет сжатия в компакторе истории** (модуль удалён в 1.4, переписан в
      Фазе 4 как `agents/history_compactor.py`): три ступени — reasoning → содержимое
      старых tool-результатов → старейшие реплики целиком (вместе с tool-вызовами).
      После каждой ступени досрочный выход, если влезли в бюджет. `messages_tokens`
      теперь учитывает и `reasoning_content` (без этого ступень 1 никогда не сработала бы).
- [x] **Первая ступень приоритета — `ReasoningPart`** (`_stub_old_reasoning`):
      в старых (`index < len - keep_recent`) assistant-сообщениях `ReasoningPart`
      заменяется на `ReasoningPart(text=REASONING_STUB)`, тип сохраняется
      (важно для сериализации в `SessionStore`). `TextPart`/`ToolCallPart` в том же
      сообщении не трогаются.

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
- [x] Monaco: тема следует за приложением (раньше была жёстко `vs-dark`).
      `EditorWidget.set_theme(theme)` прокидывает `"light"` → `vs`, `"dark"` → `vs-dark`
      в `editor.js::setTheme`; `MainWindow` вызывает при создании (`_create_center_panel`)
      и на сигнал `theme_changed` (`_on_theme_changed_for_editor`). `diffEditor`
      перекрашивается через `updateOptions({theme})`.
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
- [x] **Доработка отображения рассуждений модели (`reasoning_content`)** — реализовано
      через `ReasoningWidget` (4.8.B) + тумблер в правой панели:
  - [x] не смешивать с `content` — рассуждения идут отдельным полем
        `_current_thinking_content` и в `content` не попадают;
  - [x] без попадания в `SessionStore` — `_on_agent_finish` сохраняет только `content`
        (обратная сторона: после перезагрузки сессии рассуждения исчезают, см. 4.8.B —
        можно сохранять через `ChatMessageItem.get_parts()` в отдельном заходе);
  - [x] тумблер в настройках панели агента «Показывать размышления»:
        `_show_reasoning` в `ChatPanel`, чекбокс `show_reasoning_check` в правой панели
        `MainWindow`, значение сохраняется в `ProjectSettings.show_reasoning`
        (per-project) и восстанавливается при `_do_open_project`;
  - [x] сворачиваемый блок вместо вечно развёрнутого курсива — `ReasoningWidget`
        (4.8.B), auto-collapse в `finalize()`;
  - [x] обновлять блок инкрементально во время стрима — `ReasoningWidget.append_text`,
        вызывается из `ChatMessageItem.append_reasoning`;
  - [ ] для Ollama включать `think: true` у моделей, которые это умеют
        (`/api/chat`, поле `think`), сейчас поток либо пуст, либо зависит от модели;
  - [ ] для SLM — аналогично, если llama.cpp отдаёт отдельное поле
        (сейчас не отдаёт — см. `LocalLLMClient` docstring).
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

1. **Фаза 2.1** (протокол бэкенда — типизация `vector_store`/`search_cache`/`hypotheses`)
   — фундамент для Фаз 5, 7. Фаза 2.2 (реестр инструментов) завершена: `_build_registry`
   работает через `tools.registry.ALL_TOOLS`, `chat_auto_tools` в `AppConfig`,
   `is_mutating`/`category` в `@tool`.
2. **Фаза 6** (UX) — по мере сил, отдельными мелкими задачами.
3. **Фазы 5, 7** — по остаточному времени.

**Отдельно — порядок по локальным моделям** (Фаза 4.7). Ключевая поправка к порядку
фаз: надёжность ReAct (4.6) идёт **до** интеллектуального выбора моделей. Сейчас одна
модель = одно поведение = воспроизводимые баги; пул добавит ось «какая модель была
активна для сообщения N» и усложнит отладку.

1. **4.7.B** (замеры) — без цифр всё дальнейшее гадание.
2. **4.7.C** (тумблер стратегии) — дёшево, не требует пула.
3. **4.7.D** (ModelPool обёрткой, модель закреплена за сессией) — базовая часть уже
   сделана (planner/analyst), осталась стратегия «модель за сессией».
4. **4.7.E** (классификатор) — только если замеры покажут нехватку.

**Отдельно — порядок по интерфейсу агента** (Фаза 4.8). **ЗАВЕРШЕНО:** 4.8.A
(`MessagePart`), 4.8.B (композитный пузырь), 4.8.C (`run_shell_command`), 4.8.D
(`is_mutating`/`category`). **4.8.E** (события) — первая ступень ReasoningPart
реализована, полного набора `on_reasoning_delta`/`on_text_delta`/`on_tool_start`/
`on_tool_result` пока нет (не блокирует: работают существующие `on_thinking`/
`on_thought`/`on_token`). Остаток — динамическая статус-строка (4.8.B) и дифф-вьюер
`write_file` в `ToolCallCard`.

**Отдельно — `run_shell_command` (4.8.C).** Завершено: ASK_FIRST + AGENT-only +
песочница `cwd` + чёрный список + `CREATE_NO_WINDOW` + лимит вывода 5000 симв. +
тесты (`tests/tools/test_shell_ops.py`). В `_CHAT_AUTO_TOOLS` **не** входит.

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
- [x] **Целевая версия Python**: `requires-python` поднят до `>=3.12`, `ruff`/`mypy`
      нацелены на `py312`, `ui/dialogs/wizard.py` использует `typing.override` (3.12+).
- [x] **Чтение больших файлов фрагментами**: `tools/file_ops.py::read_file(path, offset, limit)`
      поддерживает `offset`/`limit` с жёстким диапазоном
      `[MIN_READ_CHUNK_CHARS=4000, MAX_READ_FILE_CHUNK_CHARS=30000]`,
      `DEFAULT_READ_CHUNK_CHARS=12000` и хинт о продолжении в хвосте результата.
      `tools/attached_files.py::read_attached_file` использует тот же механизм.
      Возможность реализована в объёме, достаточном для чтения больших файлов.