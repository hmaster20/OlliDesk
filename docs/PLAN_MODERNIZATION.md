# План модернизации OlliDesk 2.0

Рабочий документ: сюда попадают задачи по доработке проекта, сгруппированные по фазам.
Обновлять при каждом изменении архитектуры. Актуальная ветка — `slm`.

**OlliDesk 2.0** — целевое состояние: безопасный локальный coding-ассистент с «живым» UI,
проверяемыми инструментами, event-driven ядром и отдельным coding-циклом. Взгляд
«эталона» (OpenCode) взят только как источник архитектурных идей — **UI на SolidJS/TS
не переписываем**, PWA/multi-server/Solid не переносим. Всё, что ниже, — локальное
Windows-приложение на PySide6.

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
| Тёмная тема | ✅ | `ui/theme.py` — дизайн-система с токенами, полное QSS-покрытие (9.1) |
| Мастер первой настройки | ✅ | `ui/dialogs/wizard.py` |
| Диспетчер GPU-слоёв для SLM | ✅ | `agents/gpu_utils.py`, `agents/slm_optimizer.py` |
| CI (ruff → mypy → pytest) | ✅ | `.github/workflows/ci.yml` |
| Reranking + гибридный поиск (BM25) | ✅ | `fs/bm25.py`, `fs/reranker.py`, `fs/vector_store.py` |
| **Двухмодельный пайплайн (planner GPU + analyst CPU)** | ✅ | `agents/model_pool.py`, `agents/slm_selector.py`, `ui/chat/threads.py::_ModelPoolLoadThread` |
| **Orchestrator: линейный конвейер (classify → plan → collect → synthesize → verify)** | ✅ | `agents/orchestrator.py`, `agents/evidence.py`, `agents/diagram.py`, `agents/verifier.py`, `agents/critic.py` |
| **Project Intelligence (граф зависимостей, repomap, парсеры Ansible/Terraform/Vagrant/C#/Python)** | ✅ | `fs/intel/` |
| **Research-режим: фазы, гипотезы, нуджи, паспорт исследования** | ✅ | `agents/investigation.py`, `agents/hypotheses.py`, `ui/chat/thought_stream.py`, `ui/chat/research_passport.py` |
| **AGENTS.md как постоянный контекст проекта** | ✅ | `agents/context_loader.py` |
| **Стриминг SLM (токены онлайн)** | ✅ | `agents/local_llm_client.py::_generate` эмитит `TextPart` на каждый токен + start/end (9.5.2) |
| **Живой трейс для Agent/Plan** | ✅ | `ChatThoughtStream` в Agent/Plan через `_ensure_thought_stream`; в Chat — плоский activity (9.5.2) |
| **Единая приборная панель статусов** | ✅ | `ui/widgets/status_dashboard.py::StatusDashboard` в статус-баре: backend/pipeline/tok/s/RAG/tools (9.8) |
| **SLM-first селекторы моделей** | ✅ | `ui/widgets/pipeline_selectors.py::PipelineSelectors` в верхней панели; глобальный переключатель SLM⇄Ollama в дашборде (9.3) |
| **Профили использования (Разработка / Исследование / Обучение)** | ✅ | `core/profiles.py`, `ui/widgets/profiles_selector.py`, `ui/dialogs/profile_editor_dialog.py`, меню `View → Профили` (9.4) |
| **PDF/DOCX/PPTX/XLSX/RTF + OCR + предпросмотр** | ✅ | `fs/document_extractors.py`, `ui/dialogs/document_preview_dialog.py`, `_DOC_LANGUAGES` в `VectorStore` (Фаза 5 частично + 9.7) |
| **Дизайн-система (токены, полное покрытие QSS)** | ✅ | `ui/theme.py`: Darcula + тёплые акценты, QTableWidget/QHeaderView и т.д. (9.1) |
| **OCR изображений (JPG/PNG)** | ⚠️ | `rapidocr-onnxruntime` есть, подключение к drag&drop картинок — Фаза 5 (backend) + 9.7 (UI) |
| **Аудио/видео (STT)** | ❌ | `faster-whisper` + ffmpeg — Фаза 5 (backend) + 9.7 (UI) |
| **Vision-модели через Ollama** | ❌ | `qwen2.5-vl` / `llama3.2-vision` — Фаза 5 (backend) |
| **MCP** | ❌ | Фаза 7 |
| **Система плагинов** | ❌ | Фаза 7 |
| **Событийная модель сообщений (`MessagePart`)** | ✅ | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`), `state/session_store.py` (колонка `parts_json`) |
| **Композитный пузырь сообщения** | ✅ | `ui/chat/message_item.py` (`ChatMessageItem` из `ReasoningWidget` + `TextWidget` + `ToolCallCard`) |
| **Выполнение shell-команд агентом** | ✅ | `tools/shell_ops.py::run_shell_command` (ASK_FIRST, AGENT-only, чёрный список, песочница cwd) |
| Компактор истории диалога | ✅ | `agents/history_compactor.py` (вызывается в `AgentLoop`; приоритет: reasoning → tool-результаты → старейшие реплики) |
| **Дифф-карточка `write_file`** | ✅ | `ToolCallCard._render_diff_mode` + `tools/file_ops.py::write_file` → dict `{diff, lines_added, lines_removed, written_content, ...}` (9.5.3) |
| **Автодополнение `@`-тегов** | ✅ | `ui/widgets/command_completer.py::CommandCompleter`, `core/chat_commands.py` (9.5.4) |
| **Экспорт чата в Markdown/HTML** | ✅ | `core/chat_export.py`, `ui/dialogs/export_chat_dialog.py`, `File → Экспорт чата…` (9.5.4) |
| **Менеджер сессий: preview + «Создать проект из сессии»** | ✅ | `ui/dialogs/session_manager_dialog.py` (9.5.4) |
| **Палитра команд `Ctrl+Shift+P`** | ✅ | `ui/command_registry.py`, `ui/widgets/command_palette.py`, `_register_palette_commands` (9.8) |
| **Виртуализация длинных чатов** | ✅ | `ui/widgets/collapsed_placeholder.py::CollapsedPlaceholder`, порог 80 (9.8) |
| **Авто-скролл с порогом 32 px** | ✅ | `ui/widgets/auto_scroll.py::AutoScrollArea` (9.5.1) |
| **Замеры производительности (TTFT/tok/s/load)** | ✅ | `agents/benchmark.py`, `ui/dialogs/benchmark_dialog.py`, `View → Замер производительности…` (4.7.B) |
| **Git-diff в редакторе (HEAD ↔ рабочая копия)** | ✅ | `tools/git_ops.py::read_file_at_head`/`get_git_status_for_file`, `EditorWidget.show_git_diff_for_file`, Ctrl+D (9.4) |
| **Вынос `panel.py` в миксины** | ✅ | `ui/chat/backends.py::BackendsMixin` + `ui/chat/research.py::ResearchMixin` (2.4) |
| **Стационарный чат без крестика (чат — не вкладка)** | ❌ | `MainWindow._create_center_panel` ещё кладёт чат в `QTabWidget` (9.1) |
| **Авто-показ структуры проекта при открытии** | ⚠️ | Сейчас восстанавливается `config.show_project_tree` (по умолчанию `False`) (9.2) |
| **Поиск моделей на HuggingFace** | ❌ | `SLMModelsDialog` работает только с локальным каталогом (9.3.1) |
| **Отдельный `slm_models.yaml` (модели и tier вне `config.yaml`)** | ❌ | `_DEFAULT_SLM_MODELS` в `core/config.py`, `available_models` в `config.yaml` (9.3.3) |
| **Подсветка coder-моделей в профиле «Разработка»** | ❌ | В `PipelineSelectors`/`ProfilesSelector` нет визуальной дифференциации (9.3.2) |
| **Git-контекст в промпте (ветка, hash, diff)** | ❌ | `AgentContext` не содержит `git_context`; `ProjectSettings` не хранит branch/hash (9.4) |
| **`git_commit` как инструмент агента** | ❌ | `tools/git_ops.py` умеет только `create_snapshot`/`undo_last_snapshot` (9.4) |
| **«Живой» UI: события из логов в интерфейсе** | ❌ | `EventBus` отсутствует; `status_label` показывает только «🤔 Думает…» (2.5, 9.5.3) |
| **Auto-height reasoning-виджет** | ⚠️ | `ReasoningWidget.setFixedHeight(min(max(40, line_count*16+8), 240))` — тесно (9.5.2) |
| **Восстановление истории с reasoning/tool_calls** | ⚠️ | `_on_agent_finish` сохраняет только `TextPart`; reasoning/tool_calls теряются (9.6) |
| **Drag&drop файлов в чат** | ❌ | Работают только `@file`/`@dir`/`@path` (9.7) |
| **Превью изображений/аудио в пузыре** | ❌ | `ChatMessage.images` не используется в UI (9.7) |
| **EventBus / структурированные события** | ❌ | Нет `core/event_bus.py` (2.5) |
| **ToolSpec v2: capabilities + timeout + idempotent** | ❌ | `@tool` знает только `name`/`description`/`policy`/`modes`/`is_mutating`/`category` (2.6) |
| **Policy Engine (единая точка авторизации)** | ❌ | Политики раскиданы по `_apply_config_policies`, `ProfileToolPolicies`, декоратору (2.7) |
| **Audit log инструментов** | ❌ | Нет БД `audit.db` (Фаза 0.1) |
| **Shell allowlist (а не чёрный список)** | ⚠️ | `tools/shell_ops.py::_BLACKLIST` — чёрный список; allowlist отсутствует (Фаза 0.1) |
| **`apply_patch` (unified diff вместо полного `write_file`)** | ❌ | `write_file` перезаписывает файл целиком (Фаза 10) |
| **`run_tests` tool** | ❌ | Нет инструмента запуска тестов (Фаза 10) |
| **Verifier в обычном AgentLoop** | ⚠️ | `agents/verifier.py` есть только для Orchestrator (Фаза 10) |
| **LSP для definition/references в coding-цикле** | ⚠️ | `agents/lsp_bridge.py` есть, но не используется в `ProjectIntel` (Фаза 10) |
| **Coding Bench OlliDesk** | ❌ | Нет набора тестовых задач и метрик pass@1 (Фаза 10) |
| **`ModelCatalog` YAML** | ❌ | `slm_models.yaml` ещё нет (9.3.3) |
| **AsyncResource для Qt (idle/loading/ready/refreshing/error)** | ❌ | Guard Suspense-паттерна в Qt не формализован (2.9) |
| Тесты UI / state | ✅ | `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py` |

---

## Фаза 0.1 — Стабилизация и безопасность (OlliDesk 2.0)

**Ближайшая цель: 1–2 недели.** Закрывает самые опасные и раздражающие дефекты.
Это фундамент, без которого остальные фазы 2.0 не имеют смысла.

### 0.1.1 Shell hardening

- [ ] **Allowlist вместо чёрного списка.** `tools/shell_ops.py`: заменить `_BLACKLIST`
      на `_ALLOWLIST_PATTERNS` — разрешены только безопасные команды:
      `Test-Path`, `Get-ChildItem`, `Get-Content`, `Select-String`, `Measure-Object`,
      `Get-Location`, `Resolve-Path`, `Get-FileHash`, `python -m pytest`,
      `python -m py_compile`, `git status`, `git diff`, `git log`, `git show`,
      `git branch`, `git rev-parse`, `dotnet test`, `go test`, `npm test`.
- [ ] **Dangerous mode** — отдельный флаг `SecurityPolicy.allow_arbitrary_shell = False`
      (в `core/security.py`). Включается **только** явным чекбоксом в
      `AgentSettingsDialog` + подтверждением в диалоге. Каждый вызов — в audit log
      с пометкой `dangerous=True`. По умолчанию — выключено.
- [ ] **Запрет обхода через `cmd /c`, `powershell -EncodedCommand`, `iex`, `| iex`,
      `Invoke-Expression`, `curl | bash`, `iwr | iex`** — при попытке возвращать
      `Ошибка: команда заблокирована allowlist-политикой` (а не выполнять).
- [ ] **`run_shell_command` без `project_root` — отказ**, даже при `dangerous mode`
      (уже частично реализовано, довести до явного теста).
- [ ] **Тесты:** `tests/tools/test_shell_allowlist.py` — allowlist, dangerous mode,
      audit entries.

### 0.1.2 Секреты и redaction

- [ ] **`core/redaction.py`** — единая точка вырезания секретов:
      API-ключи (`sk-…`, `hf_…`, `ghp_…`), JWT, пароли из URL, приватные ключи
      (`-----BEGIN … PRIVATE KEY-----`), env-переменные с секретными именами.
- [ ] **Redaction в логах** (loguru sink), **в tool-аргументах** (перед отправкой в LLM)
      и **в результатах** инструментов (перед отдачей в UI/сессии).
- [ ] **Запрет `.env`, `.git/config`, `*.pem`, `*.key`, `id_rsa*`** в `read_file`/
      `list_directory`/`search_codebase` (через `SandboxedPathResolver._DENIED_PARTS`
      расширить + capability `fs.secrets` для явного доступа с подтверждением).
- [ ] **`web_search` hygiene** — уже есть в `tools/web_search_hygiene.py`; расширить
      паттерны под `.env`-переменные и JWT.

### 0.1.3 История и reasoning

- [ ] **История 2.0 — аккумуляция parts в ChatPanel.** `self._current_assistant_parts:
      list[MessagePart]`. В `_on_chunk` — `TextPart`, `_on_thinking` — `ReasoningPart`,
      `_on_tool_started`/`_on_tool_executed` — `ToolCallPart`/`ToolResultPart`.
      В `_on_agent_finish` — `ChatMessage(role="assistant", parts=self._current_assistant_parts)`.
- [ ] **Research-ветка:** в `_on_agent_finish` (research) сохранять
      `summary["final_answer"]` как `TextPart` + `hypotheses` как `TextPart`-markdown.
- [ ] **`load_history`:** убедиться, что `parts_json` рендерится в `ChatMessageItem`
      без потерь.
- [ ] **Тест:** `tests/ui/test_history_restore.py` — text + reasoning + tool_call →
      finish → reload → все части на месте.
- [ ] **Reasoning 2.0 — авто-высота.** `ReasoningWidget._refresh_browser`: убрать
      `setFixedHeight(h)`, использовать `TextWidget._adjust_height`-паттерн
      (auto-height с `max = 40% от высоты чата`) + внутренний авто-скролл к последнему
      токену (`_browser.verticalScrollBar().setValue(maximum())`).
- [ ] **Reasoning 2.0 — плавное появление** `QPropertyAnimation` на `maximumHeight`
      (0 → auto), 150 мс, только для разворачивания/сворачивания.

### 0.1.4 Модель за сессией / явный выбор

- [ ] **`ModelSelection.trackSessionCommit`** — ждать **оба** ack'а (agent + model),
      откатывать только matching draft. См. §4.6.
- [ ] **Явный выбор пользователя** в `SLMModelsDialog`/`PipelineSelectors` выключает
      `auto_select_model` (уже есть в `_activate_selected`, довести до общего API).
- [ ] **`last_model` не перетирается** при смене режима в Chat-режиме (уже сделано
      через `_emit_settings_changed`, но проверить сценарий «SLM + Ollama disabled»).
- [ ] **`SLMConfig.auto_select_model=False`** не запускает `select_best_model()` в
      `_detect_and_load_slm` — уже сделано, покрыть тестом.

**DoD:** shell не выполняет произвольные команды без dangerous mode; секреты не
утекают в LLM/логи; история после перезапуска содержит reasoning и tool_calls;
reasoning не обрезается; модели не «перескакивают» при явном выборе.

---

## Фаза 2 — Архитектурные улучшения

### 2.1 Протокол LLM-бэкенда
- [x] Ввести `agents/base.py::LLMBackend(Protocol)` с методами `chat`, `embed`,
      `cancel_request`, `is_cancelled`, `reset_cancel`. (`runtime_checkable` Protocol,
      с async context manager. `check_tools_support` в Protocol **не** вынесен —
      остался методом конкретных реализаций.)
- [x] Типизировать `OllamaClient`, `LocalLLMClient`, `RuleBasedFallback` как `LLMBackend`:
      `AgentLoop` объявлен с `client: LLMBackend`, `AgentChatThread` — с `client: LLMBackend`,
      `ModelRouter._client_for()` / `ModelRouter.active_client` больше не возвращают `Any` —
      возвращают `LLMBackend | None`. Это заставляет mypy проверять структурную
      совместимость всех трёх реализаций с `LLMBackend` на каждом call-site
      (`set_ollama` / `set_local_slm` / `set_rule_based` / `active_client` / `chat` / `embed`).
      `RagSearchThread`/`VectorStore` работают через протокол `Embedder` (`ModelRouter`
      удовлетворяет ему структурно через `async def embed(...)`). Явных `implements
      LLMBackend` в классах нет — Python использует структурную типизацию, `isinstance`
      проверяется в `tests/agents/test_protocol_compliance.py` (smoke), сигнатуры —
      mypy в CI. `num_ctx` намеренно **не** добавлен в `LLMBackend`: `RuleBasedFallback`
      его не имеет (нет модели), `AgentLoop._compact_history` использует `getattr`.
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
      **В Фазе 9 профили (9.4) добавляют визуальный редактор `tool_policies`**, см. 9.5.4.
      **В Фазе 2.7 вводится Policy Engine** — единая точка авторизации.
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
> **Влияет на отзывчивость UI** — см. 9.5.3 («живой» UI) и 9.8 (прогресс индексации).

- [ ] Единый `ThreadPool`/`TaskManager` вместо независимых `QThread`-классов.
      Сейчас в `ui/chat/threads.py`: `AgentChatThread`, `RagSearchThread`,
      `WebSearchThread`, `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`,
      `_ModelPoolLoadThread`, `_BenchmarkThread`; плюс `IndexingThread`, `ModelCheckThread` в `main_window.py`
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
      `_DownloadThread` (см. 9.3.3).
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
    `file_deleted(str)` / `branch_changed(str)` / `file_diff_requested(str)`; `MainWindow` подключает их
    к `open_file_in_editor` / `editor_widget.rename_open_file` /
    `editor_widget.close_file_silently` / `_on_branch_changed` / `_show_file_diff`.
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
- [x] **`ui/chat/panel.py` → `ui/chat/backends.py` + `ui/chat/research.py`**
      (миксины `BackendsMixin` + `ResearchMixin`).
      `BackendsMixin` — 15 методов: `reload_slm`, `_detect_and_load_slm`,
      `_config_slm_enabled`, `_apply_backend_config`, `_detect_backends`,
      `_active_backend_label`, `_emit_backend_status`, `_check_ollama_async`,
      `_on_ollama_check_result`, `_on_slm_loaded`, `_load_slm_async`,
      `_download_and_load_slm`, `_on_slm_progress`, `_on_slm_error`,
      `_on_slm_downloaded`.
      `ResearchMixin` — 18 методов: `_reset_research_state`,
      `_on_research_mode_detected`, `_on_thought_chunk`,
      `_on_final_answer_started`, `_on_final_chunk`,
      `_on_research_summary_ready`, `_should_show_passport`,
      `_show_research_passport`, `_pipeline_status_label`,
      `_update_pipeline_status`, `_pipeline_tooltip`, `_ensure_orchestrator`,
      `reset_pipeline`, `_on_pool_loaded`, `_on_pool_error`,
      `_on_orchestrator_result`, `_on_orchestrator_event`,
      `_on_pool_progress`.
      MRO: `class ChatPanel(QWidget, BackendsMixin, ResearchMixin)` —
      QWidget первым (PySide6 требование). Все поля объявлены в
      `ChatPanel.__init__`, миксины обращаются через `self`. Внешний API
      ChatPanel не изменился. `panel.py` уменьшился с ~2600 до ~1900
      строк. Тест MRO — `tests/ui/test_panel_mro.py`.
- [ ] `QCommandLine` вместо `QAction` для меню. На macOS `QAction`-шорткаты
      всё равно уходят в системное меню; `QCommandLine` даёт выигрыш только
      при появлении CLI-режима (`ollidesk --headless`). Отдельная задача.

`MainWindow` из «всё-в-одном» превратился в UI-оркестратор: владеет
виджетами прогресса/статуса и подключает сигналы контроллеров к своим
слотам, но не знает про `QFileSystemModel`, `GitStatusDelegate`,
`IndexingThread` и `ProjectState`. Приватная связь `MainWindow ↔ chat_panel`
(прямой доступ к полям) заменена на 5 публичных методов `ChatPanel`
(см. выше).

### 2.5 EventBus и структурированные события (OlliDesk 2.0)

**Боль:** «логи информативнее UI, пользователь не понимает, почему программа не отвечает».

- [ ] **`core/event_bus.py`**:
  - `EventBus(QObject)` — синглтон с сигналом `event_emitted(str, dict)`.
  - `EventBus.emit(kind: str, payload: dict)`.
  - `EventBus.subscribe(kind: str, callback) -> Unsubscribe`.
  - `EventBus.clear()` — для тестов.
- [ ] **Схема событий** (`core/events.py`, Pydantic-модели):
  - `rag.search.started` / `rag.search.found` / `rag.search.empty`
  - `llm.request.sent` / `llm.first_token` / `llm.generation.done`
    (поля: `ttft_ms`, `tokens_per_sec`, `model`)
  - `tool.started` / `tool.finished` / `tool.error`
    (поля: `name`, `args_hash`, `duration_ms`, `is_error`, `error_category`)
  - `phase.changed` (research: RECON → DEEP_DIVE → …)
  - `index.started` / `index.progress` / `index.finished`
  - `slm.loading` / `slm.loaded` / `slm.failed`
  - `orchestrator.event` (уже есть `on_event` в `Orchestrator.run` — дублировать в EventBus)
- [ ] **Loguru-sink** в `core/logger.py`: для `level >= INFO` эмитит
      `EventBus.event_emitted("log", {level, message, source, timestamp})`. Все
      текущие `logger.info`-строки становятся видимыми в UI без переписывания модулей.
- [ ] **Точки эмита:**
  - `fs/vector_store.py::search` — `rag.search.*`.
  - `agents/agent_loop.py` — `tool.*`, `llm.*`.
  - `agents/orchestrator.py::run` — `orchestrator.event` (через существующий `on_event`).
  - `ui/indexing_controller.py` — `index.*`.
  - `ui/chat/backends.py::_on_slm_loaded` — `slm.*`.
- [ ] **UI-подписка:** `ui/widgets/live_events.py::LiveEventsPanel` (сворачиваемая
      панель под чатом, высота 120 px, авто-скролл, фильтр «только важное»,
      кнопка «Очистить»). `ChatPanel._setup_ui` создаёт панель и подписывается
      на EventBus.
- [ ] **Индикатор «программа работает»:** `StatusDashboard` — пульсирующая точка
      (`QTimer 500 мс`) пока `_generating=True`; tooltip — последнее событие
      из EventBus.
- [ ] **Статус-строка «по этапам»** вместо «🤔 Думает…»: `🔍 Поиск по коду →
      📖 Чтение 3 файлов → 🧠 Генерация (TTFT 1.2 с) → ✍️ 42 tok/s`. Источник — EventBus.
- [ ] **Тесты:** `tests/core/test_event_bus.py` (subscribe/emit/unsubscribe,
      sink), `tests/ui/test_live_events.py` (панель не создаёт reactive
      computations при вызове, как `toast-owner` в OpenCode).

### 2.6 ToolSpec v2 и capabilities (OlliDesk 2.0)

**Боль:** «инструменты опасны, не хватает контракта и авторизации».

- [ ] **`agents/tool_registry.py::ToolSpec`** — immutable dataclass:
  ```python
  @dataclass(frozen=True)
  class ToolSpec:
      name: str
      description: str
      schema: dict
      modes: list[AgentMode]
      policy: ToolPolicy
      capabilities: frozenset[Literal["fs.read","fs.write","net","shell","git","ui","secrets"]]
      is_mutating: bool
      category: str  # "filesystem" | "shell" | "network" | "git" | "info"
      timeout_s: int = 30
      max_output_chars: int = 5000
      idempotent: bool = False
  ```
- [ ] **`@tool`** принимает `capabilities: list[str]`, `timeout_s`,
      `max_output_chars`, `idempotent`. Дефолт `capabilities` — `{"fs.read"}`.
  - `read_file`/`list_directory`/`search_codebase`/`read_attached_file` — `fs.read`.
  - `write_file`/`apply_patch`/`run_shell_command` (dangerous) — `fs.write`/`shell`.
  - `web_search`/`get_weather`/`get_exchange_rate` — `net`.
  - `get_git_status`/`create_snapshot`/`undo_last_snapshot`/`git_commit` — `git`.
  - `record_hypothesis`/`update_hypothesis`/`list_hypotheses` — `ui`.
- [ ] **`ToolRegistry.get_spec(name) -> ToolSpec`** — единая точка доступа.
- [ ] **`ToolRegistry.execute`** проверяет `capabilities` **до** выполнения
      (см. 2.7 Policy Engine).
- [ ] **Тесты:** `tests/agents/test_tool_spec.py` — capabilities у всех
      зарегистрированных инструментов, immutable, `timeout_s > 0`.

### 2.7 Policy Engine (OlliDesk 2.0)

**Боль:** «политики раскиданы по трём местам, приоритет неочевиден».

- [ ] **`core/policy.py::effective_policy(tool_spec, profile, security, config) ->
      EffectivePolicy`** — единая точка:
  ```python
  @dataclass(frozen=True)
  class EffectivePolicy:
      policy: ToolPolicy  # auto | ask_first | excluded
      allowed: bool
      reason: str  # "profile" | "security" | "config" | "default"
  ```
- [ ] **Приоритет (сверху вниз):**
  1. `SecurityPolicy` (confidential → всё `net`/`shell`/`git` = `excluded`).
  2. `ProfileConfig.tool_policies` (самый сильный).
  3. `AppConfig.tool_policies`.
  4. Декоратор `@tool(policy=...)`.
- [ ] **Дефолт по capabilities:**
  - `fs.read` → `auto`.
  - `fs.write` → `ask_first`.
  - `net` → `ask_first` (кроме `get_weather`/`get_exchange_rate` — `auto`).
  - `shell` → `excluded` в профиле `research`/`learning`, `ask_first` в `development`.
  - `git` → `ask_first` (кроме `get_git_status` — `auto`).
  - `secrets` → `excluded` всегда (даже в development).
- [ ] **`ChatPanel._build_registry`** использует `effective_policy` для каждого
      инструмента; `_apply_config_policies` удаляется (логика в Policy Engine).
- [ ] **`ToolRegistry.execute`** вызывает `effective_policy` и возвращает
      `ToolResult(is_error=True, error_category=FIX_ARGUMENTS, content="Заблокировано
      политикой: …")` без выполнения.
- [ ] **Тесты:** `tests/core/test_policy.py` — 20+ кейсов (профиль, security,
      capabilities, приоритет).

### 2.8 Application Services (OlliDesk 2.0)

**Боль:** «UI знает про `QFileSystemModel`, `ProjectState`, `SessionStore`,
`VectorStore` напрямую; тяжело тестировать».

- [ ] **`core/services/`** — чистые Python-сервисы без Qt:
  - `session_service.py` — создание/удаление сессий, `parts_json`, миграции.
  - `chat_service.py` — отправка сообщений, отмена, стриминг (через `EventBus`).
  - `tool_service.py` — `execute_tool(invocation, context)`, audit, метрики.
  - `model_service.py` — выбор модели, `ModelCatalog`, `auto_select`.
  - `project_service.py` — `ProjectIntel`, `repomap`, `git_context`.
- [ ] **`ui/*_controller.py`** переписываются на сервисы: `ChatPanel` вызывает
      `ChatService.send()`, `IndexingController` — `ProjectService.index()`,
      `FileTreeController` — `ProjectService.list_files()`.
- [ ] **Тесты сервисов без Qt** — покрытие ≥80% на критичных путях
      (сессия, отмена, policy, audit).

### 2.9 AsyncResource для Qt (OlliDesk 2.0)

**Боль:** «Suspense-подобные flash/blank/resize в Qt».

- [ ] **`ui/state/async_resource.py::AsyncResource[T]`** — guard-паттерн
      из OpenCode, адаптированный под Qt:
  ```python
  class ResourceState(StrEnum):
      IDLE = "idle"
      LOADING = "loading"
      READY = "ready"
      REFRESHING = "refreshing"
      ERROR = "error"

  class AsyncResource(Generic[T]):
      state: ResourceState
      latest: T | None
      error: Exception | None
      def loaded(self) -> bool:  # state in ("ready","refreshing")
      def value_or(self, default: T) -> T
  ```
- [ ] **Правило:** UI не рисует пустоту, если `state in ("loading","refreshing")`
      и есть `latest`. Guard: `loaded = r.state in ("ready","refreshing")`,
      `items = r.latest if loaded else cached`.
- [ ] **Использование:** model list (Ollama), session history, settings,
      project tree, RAG-sources.
- [ ] **Тесты:** `tests/ui/test_async_resource.py` — не «мигает» при
      `refreshing`, отдаёт `latest`, сообщает ошибку.

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
не реализованы в этой итерации. **В OlliDesk 2.0 добавляется `ModelCatalog` (9.3.3) и
HF-поиск (9.3.1).**

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
- [ ] **`ModelCatalog`** (см. 9.3.3): вынести `_DEFAULT_SLM_MODELS` из `core/config.py`
      в `~/.ollidesk/slm_models.yaml`; `_sync_builtin_models` → миграция при первом
      запуске.
- [ ] **HF search** (см. 9.3.1): вкладка «Поиск HF» в `SLMModelsDialog` через
      `HfApi.list_models`/`model_info`/`list_repo_files`.
- [ ] **Tags `coder`/`reasoning`** в `SLMModelInfo` (см. 9.3.2) + подсветка в
      `PipelineSelectors`/`ProfilesSelector` для профиля «Разработка».

---

## Фаза 4.6 — Надёжность ReAct-агента (из `React.md`)

`React.md` — разбор ReAct-цикла с привязкой к файлам. Ниже сведённые задачи с приоритетом.
Обозначения: **Потом** — улучшает качество, но не блокирует работу;
**Позже** — крупные подсистемы.

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
      сессии — при условии, что они попали в `ChatMessage.parts` (см. 9.6 — сборка
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
      композитный пузырь — `ToolCallCard` встраивается в пузырь через
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
- [ ] **Verifier в обычном AgentLoop** (см. Фаза 10.4): подключить
      `agents/verifier.py` после финального ответа; при провале — не отдавать ответ,
      а запросить регенерацию с `regen_hint`.
- [ ] **Audit log** (см. Фаза 0.1.1): каждая запись `ToolResult` попадает в
      `~/.ollidesk/audit.db` (timestamp, tool, args_hash, policy, approved, result_hash,
      error, dangerous).

### 4.6.C — Позже: новые подсистемы

- [~] **Reflexion-критик для Final Answer** (`React.md` 4.1): для Orchestrator это уже
      сделано — `agents/critic.py` (3B-проверка структуры/формата) и `agents/verifier.py`
      (код-проверка путей/символов/evidence) вызываются в `Orchestrator.run()`. Для
      `AgentLoop` (обычные чат-ответы) — по-прежнему не подключено; **в Фазе 10.4** —
      подключаем.
- [~] **Structured trace агента** (`React.md` 8.1): в research-режиме есть
      `last_research_summary` и `ChatResearchPassport` с прочитанными файлами, поисками,
      гипотезами. Полноценного трейса (итерация → действие → длина observation →
      длительность) с кнопкой «Показать трейс» — нет. **Закрывается в 9.5.3 («живой» UI)
      и 2.5 (EventBus).**
- [ ] **Метрики реестра** (`React.md` 8.2): `_stats` в `ToolRegistry` (calls/errors/by_tool)
      в статус-бар — сколько ошибок tool-call за сессию. **Отображение — в 9.8.**
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
      **Отображение — в 9.8.**
- [x] **Multi-tool-call у SLM** (`React.md` 5.1): `LocalLLMClient._parse_tool_calls`
      возвращает список из `extract_tool_calls` (JSON + function-call), в `chat()` все
      вызовы уходят одним `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`.
      `AgentLoop` через `pending_tool_calls.extend(part.calls)` выполняет все.

### 4.6.D — Удалить (проверить перед удалением)

- [x] `RouterChatThread` (`React.md` 1.3, 6.3) — **уже удалён** вместе с
      `OllamaChatThread`; в `ui/chat/threads.py` его нет. Задача закрыта.
- [ ] `ui/dialogs/model_manager_dialog.py::PullThread` — дублирует `_DownloadThread`
      из `slm_models_dialog.py`; объединить (см. 9.3.3).
- [~] Закомментированные блоки в `core/search_cache.py`, `fs/vector_store.py`, `main.py` —
      не восстанавливать, вычистить при рефакторинге. Часть закомментированных строк в
      `main.py` осталась (например, в `_build_system_prompt`), плюс `_OllamaCheckThread`,
      `_SLMLoadThread`, `_SLMDownloadThread` в `threads.py` с несколькими комментариями
      «было / стало» — косметически почистить.

### 4.6.E — Решения, которые нужно подтвердить

- [x] `agents/context_compactor.py`: **удалён** — писать заново.
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
(`ChatPanel._ensure_orchestrator`). Стратегии выбора моделей **поглощены профилями
(9.4)** — отдельные тумблеры не нужны. **В OlliDesk 2.0 модели переезжают в
`slm_models.yaml` (9.3.3), а HF-поиск — в 9.3.1.**

### 4.7.B — Замеры перед любыми решениями по моделям

- [x] Прогнать 1.5B / 3B / 7B через `last_stats` и записать реальные `ttft_ms` и
      `tokens_per_sec` до/после смены `n_threads`. **Реализован инструмент:**
      `agents/benchmark.py` (`run_benchmark`, `BenchmarkPrompt`, `BenchmarkRow`,
      `LoadTimeRow`, `save_csv`, `to_markdown`), `ui/dialogs/benchmark_dialog.py`,
      `_BenchmarkThread` в `ui/chat/threads.py`, `View → Замер производительности…`.
      Экспорт CSV (`bench.csv` + `bench_load.csv`) и Markdown (для
      `docs/BENCHMARK.md`). Стандартные промпты short/medium/long + пользовательские.
      Реальные цифры — за пользователем (нужен прогон на его железе).
- [x] Проверить, сколько времени занимает **повторная** загрузка той же модели
      (mmap + page cache при 102 ГБ свободной RAM). **Реализовано:** `_measure_slm_model`
      в `agents/benchmark.py` загружает модель × repeats, каждый раз
      `del client + gc.collect() + sleep(0.3)`, пишет `LoadTimeRow(attempt=N)`.
      Отчёт различает первую и повторную загрузку в `to_markdown`/сводке. Реальные
      цифры — за пользователем (T1200/i7-11800H: ожидаемо 3–10 с, а не 30–120).

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
агентных сред. Большая часть фазы завершена: тип `ChatMessage`/`ChatChunk` переведён
на `MessagePart` (read-only `content` для обратной совместимости), композитный пузырь
сообщения, `run_shell_command`, метаданные `is_mutating`/`category` — всё в проде.
Открыт только 4.8.E — низкоуровневые события реализованы, **стриминг SLM по
токенам вынесен в 9.5.2 и уже сделан**. **В OlliDesk 2.0 EventBus (2.5) добавляет
структурированные события поверх существующей модели.**

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
      подключены к `_on_stream_started_event` / `_on_stream_finished_event`
      (обновляют tok/s в дашборде) и `logger.trace`. Высокоуровневые
      `chunk_received`/`thinking_received` остаются источником для композитного
      пузыря. **Стриминг SLM по токенам — сделан в 9.5.2.**
- [x] **Базовый приоритет сжатия в компакторе истории** (модуль переписан в
      Фазе 4 как `agents/history_compactor.py`): три ступени — reasoning → содержимое
      старых tool-результатов → старейшие реплики целиком (вместе с tool-вызовами).
      После каждой ступени досрочный выход, если влезли в бюджет. `messages_tokens`
      теперь учитывает и `reasoning_content` (без этого ступень 1 никогда не сработала бы).
- [x] **Первая ступень приоритета — `ReasoningPart`** (`_stub_old_reasoning`):
      в старых (`index < len - keep_recent`) assistant-сообщениях `ReasoningPart`
      заменяется на `ReasoningPart(text=REASONING_STUB)`, тип сохраняется
      (важно для сериализации в `SessionStore`). `TextPart`/`ToolCallPart` в том же
      сообщении не трогаются.
- [x] **Стриминг SLM (`LocalLLMClient._generate`)**: отдаёт `ChatChunk(part=TextPart,
      event_type="delta")` на каждый токен, `start`/`end` по границам, tool calls —
      сразу при распознавании (после стрима текста). См. **9.5.2**.

---

## Фаза 5 — Мультимодальность: backend (UI — в Фазе 9.7)

**Только backend-экстракторы и модели.** Всё, что связано с отображением в UI (drag&drop,
превью, кнопки), — в **9.7**.

- [ ] Распознавание скриншотов (JPG, PNG, WebP, BMP): backend-обёртка над
      `rapidocr-onnxruntime` (`fs/image_extractors.py::extract_image`).
      Поле `ChatMessage.images` в модели уже есть — UI-часть в 9.7.
- [ ] Vision-модели через Ollama (backend): `qwen2.5-vl:7b` / `llama3.2-vision:11b`
      (GPU offload = 0, 4 ГБ VRAM не вмещают мультимодальный контекст).
      Клиентская обёртка `agents/vision_client.py`; UI-вызов — 9.7.
- [ ] Аудио (MP3, WAV, OGG, M4A, FLAC): backend `fs/audio_extractors.py::transcribe_audio`
      через `faster-whisper` (строго CPU, `large-v3` / `whisper-large-v3-russian`),
      конвертация в 16 кГц WAV через ffmpeg. UI (плеер, прогресс) — 9.7.
- [ ] Видео (MP4, MKV, WebM, AVI): backend `fs/video_extractors.py::extract_transcript` —
      извлечение звуковой дорожки ffmpeg → тот же `faster-whisper`. UI — 9.7.
- [x] **Распознавание документов (PDF, DOCX, PPTX, XLSX, RTF)**:
      `fs/document_extractors.py::extract_document` — извлечение
      текста через `pdfplumber`/`pypdf`, `python-docx`, `python-pptx`, `openpyxl`,
      `striprtf`; **отдельная коллекция `docs`** (см. `_DOC_LANGUAGES` в
      `fs/vector_store.py`). UI-предпросмотр — 9.7.
- [x] **OCR сканов PDF** (через `rapidocr-onnxruntime` + `pdf2image` + Poppler):
      `fs/document_extractors.py::_ocr_pdf`, включается явно (`use_ocr=True`).
      По умолчанию выключен (`AppConfig.document_ocr = False`).
- [ ] База мультимедиа-экстрактов: `~/.ollidesk/media_cache/` с хэшем файла →
      извлечённый текст, чтобы не дублировать обработку одних и тех же файлов.
- [ ] PDF/DJVU: DJVU не реализован (нужен `djvulibre`); PDF — есть.

> Генерация изображений (SD1.5 / SDXL / ComfyUI / Forge) — **вне объёма OlliDesk**:
> отдельный движок и внешние окна. Приложение занимается только распознаванием
> (OCR/Vision/STT), синтезом — нет.

---

## Фаза 6 — UX: быстрые правки (схлопнута в Фазу 9)

**Все пункты перенесены в Фазу 9** (9.1, 9.2, 9.5, 9.8). Раздел оставлен пустым,
чтобы не ломать нумерацию фаз.

---

## Фаза 7 — Расширяемость

**В OlliDesk 2.0 порядок изменён:** сначала `ToolSpec` (2.6) и `Policy Engine` (2.7),
потом Plugin API, **только потом** MCP.

- [ ] **Plugin API** (после 2.6/2.7):
  - `tools/plugins/*.py` + `plugin.toml` (name, version, api_version, tools, capabilities).
  - `Plugin.define({tools, commands, skills})` — декларативный контракт.
  - Изоляция ошибок (плагин не валит приложение).
  - `api_version` проверяется при загрузке; несовместимый — `warning`, не `crash`.
  - `capabilities` плагина проходят через `Policy Engine`.
- [ ] **MCP** (после Plugin API) — клиент MCP-серверов (stdio), чтобы системные
      промпты/инструменты приходили извне (`Roadmap.md`).
- [ ] **Permissions Dashboard** (см. 9.10):
  - по профилям и инструментам;
  - capabilities;
  - последние вызовы (audit log);
  - «почему заблокировано».
- [ ] Пользовательские команды (`~/.ollidesk/commands/*.md`) как расширение ролей и промптов.
- [ ] **Внешние провайдеры LLM — не планируется.** `ModelConfig.provider` допускает
      `openai`/`openai-compatible`, но клиента нет; архитектурно все модели локальные
      (SLM или Ollama).

---

## Фаза 8 — Релиз и качество

- [x] Единая версия из `pyproject.toml`, рабочий `bump-my-version` + `git-cliff`.
- [ ] `CHANGELOG.md` генерируется и коммитится на каждый релиз.
- [ ] CI: добавить `windows-latest` в матрицу (приложение Windows-only, `CREATE_NO_WINDOW`
      и пути не проверяются на ubuntu).
- [ ] Pre-commit или `make check` в локальном хуке — сейчас `.pre-commit-config.yaml` отсутствует.
- [ ] Релизный пайплайн (GitHub Actions): сборка `OlliDesk.exe`, публикация в Releases.
- [ ] Проверить `ollidesk.spec`: актуальные `hiddenimports`, размер, `llama_cpp` и `chromadb`.
- [ ] README: синхронизировать с реальным состоянием (упоминает устаревшее `scripts/download_monaco.py`).

---

## Фаза 9 — UX/UI (единый дом)

**Принципы фазы (новые, из болей пользователя):**
- **UI «живой»**: пользователь видит все этапы обработки запроса (RAG-поиск, отправка в
  LLM, TTFT, генерация токенов, tool calls, фазы research), а не только «🤔 Думает…».
  Логи приложения — источник правды; UI обязан их зеркалить. **EventBus (2.5)** —
  техническая основа.
- **Чат — не «каша», а структура**: явные отступы, секции, визуальная иерархия.
  Reasoning, текст ответа и tool-карточки — визуально разделены.
- **Ничего не «прячется» фиксированной высотой**: reasoning/tool-результаты растут по
  контенту или скроллятся внутри себя; внешний layout сдвигается автоматически.
- **Стационарные вкладки — только для файлов**: чат не вкладка, а базовый экран;
  вкладки появляются на файлах и исчезают, когда файлов нет.
- **Модели и tier — не в коде**: отдельный `~/.ollidesk/slm_models.yaml`, редактируемый
  руками и программой.
- **Git — часть контекста**: агент знает ветку, hash, diff и умеет делать осмысленные
  коммиты (с подтверждением).

### 9.0 Архив: что уже закрыто (не трогать)

См. текущие 9.1–9.11 в плане: дизайн-система с токенами, композитный пузырь
(`ReasoningWidget` + `TextWidget` + `ToolCallCard`), SLM-стриминг по токенам,
`ChatThoughtStream` в Agent/Plan, `AutoScrollArea` с кнопкой «▼ Вниз», diff-карточка
`write_file` (📂/🔍/↩), автодополнение `@`-тегов, экспорт чата (MD/HTML), менеджер
сессий с preview и «Создать проект из сессии», палитра команд `Ctrl+Shift+P`,
виртуализация длинных чатов, `StatusDashboard` (backend/pipeline/tok/s/RAG/tools),
diff в редакторе `Ctrl+D`, замеры производительности. Все пункты переносятся в архив
без изменения статусов.

### 9.1 Стационарные вкладки и навигация

**Боль:** «Нужно намертво прикрепить чат и убрать крестик… В идеале вообще не показывать
чат как вкладку… у каждого файла на вкладке будет крестик, и когда последний закроется —
структура вкладок должна скрыться».

- [ ] **Чат — не вкладка, а базовый экран.** Перестроить `MainWindow._create_center_panel`:
      корневой `QStackedWidget` или композиция «базовый виджет + опциональный `QTabWidget`».
  - Состояние «нет открытых файлов»: виден только `ChatPanel` (без `QTabWidget`).
  - Состояние «≥1 файл открыт»: сверху — `QTabBar` с файлами, под ней — `EditorWidget`;
    чат переключается горячей клавишей / кнопкой «💬».
  - Убрать вкладку «💬 Чат» из `QTabWidget` совсем. Убрать крестик у чата
    (`_close_tab` больше не нужен как «защита индекса 0» — его нет).
- [ ] **`QTabBar` только для файлов.** Каждая вкладка закрывается крестиком
      (`QTabBar.setTabsClosable(True)`), `Ctrl+W`/`Ctrl+Shift+W` (уже реализованы в
      `EditorWidget` контекстном меню).
- [ ] **Авто-скрытие `QTabWidget`.** Когда `tab_count() == 0` — `QTabWidget.setVisible(False)`
      (или `QStackedWidget.setCurrentWidget(chat)`). Когда открывается файл
      (`EditorWidget.open_file`) — `setVisible(True)` и фокус на редактор.
- [ ] **«💬 Чат» как кнопка-переключатель.** Отдельная кнопка (в тулбаре над вкладками
      или в верхней панели ChatPanel) для возврата к чату из редактора. `Ctrl+Shift+C` уже есть.
- [ ] **`EditorWidget.tab_count()` сигнал** `tabs_changed(int)` — чтобы `MainWindow` знал,
      когда скрывать/показывать `QTabWidget`. Сейчас `tab_count()` — метод, а не сигнал.
- [ ] **F4 работает независимо** от видимости `QTabWidget`: F4 управляет левой панелью
      (структура проекта), а не вкладками. Проверить, что не конфликтует.

**Затрагивает:** `ui/main_window.py::_create_center_panel`, `_close_tab`, `_show_chat_tab`,
`_show_editor_tab`; `ui/web_editor/editor_widget.py` (новый сигнал `tabs_changed`);
`ui/chat/panel.py` (кнопка «вернуться к чату»).

### 9.2 Структура проекта: автопоказ и F4

**Боль:** «Если через файл меню открывается проект, то автоматически должна отображаться
его структура, которую потом можно скрыть по F4».

- [ ] **При открытии проекта через `File → Открыть проект`** (`_do_open_project`) —
      `left_panel.setVisible(True)` и `config.show_project_tree = True`. Сейчас
      `_do_open_project` восстанавливает `show_project_tree` из конфига (по умолчанию
      `False`), из-за чего первое открытие показывает скрытую панель.
- [ ] **При открытии через `@dir`** (`_on_directory_attach_requested`) — аналогично:
      показать структуру (пользователь только что «прикрепил» каталог — логично
      увидеть его содержимое).
- [ ] **`File → Открыть недавний`** — то же поведение.
- [ ] **F4** (уже работает) — скрывает/показывает; состояние персистится в
      `config.show_project_tree`. Проверить синхронизацию с кнопкой «📁» в ChatPanel
      (сейчас синхронизируется через `_toggle_project_structure` — оставить).
- [ ] **Отдельный «режим открытия»:** если пользователь открывает проект впервые —
      структура видна; если это просто переключение между проектами и пользователь
      ранее скрыл панель — уважать `config.show_project_tree`. Логика:
      `show_on_first_open = not config.recent_projects or project_path not in config.recent_projects`.

**Затрагивает:** `ui/main_window.py::_do_open_project`, `_on_directory_attach_requested`,
`_on_project_tree_toggled`.

### 9.3 Модели: поиск на HF, отдельный конфиг, подсветка coder

#### 9.3.1 Поиск моделей на HuggingFace

**Боль:** «Возможность поиска моделей на huggingface. Ввожу фрагменты названия → поиск →
список → клик → подробное описание → кнопка скачать».

- [ ] **Вкладка «Поиск HF»** в `SLMModelsDialog` (или отдельный `HFDialog`):
  - `QLineEdit` (поисковый запрос) + `QPushButton("Искать")` + `QComboBox` фильтра
    (`gguf` / `chat` / `coder` / `reasoning`).
  - `QListWidget` результатов (id, downloads, likes, размер первого `.gguf`).
  - `QTextBrowser` — карточка модели (описание из `cardData`, теги, лицензия, ссылка на HF).
  - `QPushButton("Скачать")` — вызывает `download_model(repo_id, filename)`
    (уже есть в `agents/download_slm.py`).
- [ ] **Бэкенд:** `HfApi.list_models(search=query, filter="gguf", sort="downloads",
      direction=-1, limit=50)`; `HfApi.model_info(repo_id, cardData=True)` для описания;
      `HfApi.list_repo_files(repo_id)` для списка `.gguf`-файлов. Тонкая обёртка —
      `agents/hf_search.py`.
- [ ] **Оффлайн-режим:** при недоступности HF — сообщение в `status_label` и fallback
      на локальный список; не падать.
- [ ] **Интеграция с `download_slm`:** после скачивания — добавить запись в
      `slm_models.yaml` (см. 9.3.3) и обновить `_populate` в диалоге.

**Затрагивает:** `ui/dialogs/slm_models_dialog.py` (новая вкладка), новый
`agents/hf_search.py`, `agents/download_slm.py` (без изменений).

#### 9.3.2 Подсветка coder-моделей в режиме разработки

**Боль:** «Если я в режиме разработки, при выборе моделей должны подсвечиваться другим
цветом или жирным coder-модели для разработки».

- [ ] **`SLMModelInfo.tags: list[str]`** — добавить поле (например, `["coder"]`,
      `["reasoning"]`, `["chat"]`, `["embed"]`). Заполняется в `slm_models.yaml`
      вручную или из HF-карточки (`cardData.tags`).
- [ ] **`PipelineSelectors.refresh_models`:** если `self._profile.id == "development"` —
      для моделей с `"coder" in tags` применять `QFont.setBold(True)` и цвет
      (например, `#d4a373`) через `setItemData(Qt.FontRole, ...)` /
      `setItemData(Qt.ForegroundRole, ...)`.
- [ ] **В `ProfilesSelector`** — то же правило: в профиле «Разработка» показывать
      coder-модели с префиксом `⚡` или `★`.
- [ ] **`ProfileConfig.recommended_model`** — уже есть; добавить
      `ProfileConfig.coder_models: list[str]` (id) для явной рекомендации.

**Затрагивает:** `core/config.py::SLMModelInfo`, `core/profiles.py::ProfileConfig`,
`ui/widgets/pipeline_selectors.py`, `ui/widgets/profiles_selector.py`.

#### 9.3.3 Модели и tier — в отдельный файл конфигурации

**Боль:** «Не хранить жёстко модели и tier в коде, а создать файл конфигурации… хранить
рядом с файлом конфигурации приложения, потому что сам config.yaml стал не читаем».

- [ ] **`~/.ollidesk/slm_models.yaml`** — новый файл, список `SLMModelInfo` в YAML.
  - Схема: `id`, `repo`, `file`, `display_name`, `size_mb`, `kind` (`chat`/`embed`),
    `n_ctx`, `tier`, `tags`, `description`, `recommended_profile` (опционально).
  - **Миграция при первом запуске:** если файла нет — записать из `_DEFAULT_SLM_MODELS`
    (в `core.config` оставить только как fallback для миграции; затем удалить или
    пометить deprecated).
  - **Чтение:** `load_config()` подхватывает `slm_models.yaml` в
    `SLMConfig.available_models`; при отсутствии — fallback на `_DEFAULT_SLM_MODELS`
    + предупреждение в лог.
  - **Запись:** `SLMModelsDialog` (добавление/удаление/редактирование),
    `_add_custom_model`, `_on_download_finished` — пишут в `slm_models.yaml` через
    `save_slm_models()`.
- [ ] **`config.yaml` похудеет**: `slm.available_models` больше не хранится там
      (только в `slm_models.yaml`); `slm.selected_model`, `planner_model_id`,
      `analyst_model_id` остаются в `config.yaml` (это выбор пользователя, не каталог).
- [ ] **`schema_version` в `slm_models.yaml`** — для будущих миграций.
- [ ] **Редактор в UI:** `SLMModelsDialog` получает режим «Редактировать YAML»
      (открыть в `EditorWidget` или `QPlainTextEdit`); после сохранения — валидация
      через `SLMModelInfo(**data)`.
- [ ] **Объединить `PullThread` и `_DownloadThread`** (см. 2.3): обе загрузки —
      через единый `_DownloadThread` + запись в `slm_models.yaml`.

**Затрагивает:** `core/config.py` (`SLMConfig`, `_DEFAULT_SLM_MODELS` → миграция),
новый `core/slm_models_io.py` (load/save/validate), `ui/dialogs/slm_models_dialog.py`,
`core/config.py::ConfigLoader.save_config` (не пишет `available_models`).

### 9.4 Git-интеграция в режиме разработки

**Боль:** «В режиме разработки программа могла читать git diff и понимать изменения в
ветках и хранить в профиле проекта состояние или хэш на какой ветке сейчас контекст.
Агент должен уметь делать точечные изменения в коде проекта и делать осмысленные коммиты,
если это явно не запрещено».

- [ ] **`tools/git_ops.py`:**
  - `get_current_branch(repo_path) -> str`
  - `get_branch_hash(repo_path) -> str` (`rev-parse HEAD`)
  - `get_uncommitted_diff(repo_path) -> str` (`git diff` + `git diff --cached`)
  - `get_branch_diff(repo_path, base, target) -> str` (`git diff base..target`)
  - `get_recent_commits(repo_path, limit=10) -> list[dict]`
  - `commit(repo_path, message, files=None) -> str` (агентский коммит; `git add` + `git commit`).
- [ ] **`state/project_settings.py`:** новые поля `git_branch: str`, `git_hash: str`,
      `git_last_sync: str` (ISO). Сохраняются при `_save_project_settings` (уже есть)
      и обновляются при открытии проекта / перед каждым запросом агента.
- [ ] **`AgentContext.git_context: str`** — блок вида:
      ```
      Ветка: feature/x (hash abc1234)
      Последние коммиты: …
      Незакоммиченные изменения (diff --stat):
        src/foo.py | 12 +++++---
      ```
      Добавляется в системный промпт в режиме `development` (в `_build_system_prompt` /
      `_TOOL_USE_INSTRUCTION`).
- [ ] **Новый инструмент `git_commit`** (ASK_FIRST, AGENT mode, capability `git`):
  - Аргументы: `message: str`, `files: list[str] | None` (по умолчанию — все изменённые).
  - Возвращает `dict{_llm_text, commit_hash, branch, files_committed}`.
  - В `ProfileConfig.tool_policies` для `development` — `git_commit: "ask_first"`;
    в `research`/`learning` — `excluded`.
- [ ] **Запрет по умолчанию:** если в `ProfileConfig` не задан `git_commit`, —
      `excluded` (агент не коммитит без явного разрешения). В `development` — `ask_first`.
- [ ] **Точечные изменения:** уже есть `write_file` + diff-карточка (9.5.3). Добавить
      в промпт агента явное правило: «При правке — читай diff перед коммитом; коммить
      только свои изменения, не `git add -A` без явного запроса».
- [ ] **`ProjectSettings.git_branch/hash`** — показывать в `StatusDashboard` (отдельный
      индикатор «🌿 branch@hash», см. 9.8).

**Затрагивает:** `tools/git_ops.py`, `tools/git_ops_tool.py` (новый инструмент),
`state/project_settings.py`, `agents/agent_loop.py` (`git_context` в промпт),
`core/profiles.py` (`git_commit` policy), `ui/widgets/status_dashboard.py`,
`tools/registry.py::ALL_TOOLS`.

### 9.5 Чат: редизайн и «живой» UI

**Боль 1:** «Сам чат, отступы, фон, кнопки, облака — всё убогое и больше похоже на кашу».

**Боль 2:** «Если открыть кнопку рассуждения — его высота фиксирована, новый текст скрывает,
нужно пролистывать самому. Должно всё сдвигаться само, быть понятно и интерактивно.
Требует полного переосмысления».

**Боль 3:** «Логи намного информативнее UI. Много скрыто, пользователь не понимает,
почему программа не отвечает. UI должен быть "живым"».

#### 9.5.1 Визуальный редизайн пузыря

- [ ] **Пересмотр `ChatMessageItem._build_ui`:**
  - Единый spacing-токен (`SPACE.md` = 8 px) между секциями (reasoning / text / tool cards).
  - Внутренние отступы пузыря — 12 px (сейчас `padding: 6px 12px` — тесно).
  - Разделители: тонкая линия `border-top: 1px solid border` между reasoning и text.
  - Заголовок «Ассистент» — вынести в отдельную строку с иконкой и временем (опционально),
    не сливать с текстом.
  - Для user-пузыря — выравнивание вправо, `max-width: 80%`, фон `surface_alt`,
    скругление `RADIUS.lg`.
  - Убрать `QFrame#_bubble` со `background: transparent` у assistant — сделать лёгкий фон
    `surface` с `border-radius`, чтобы пузырь читался.
- [ ] **Единый стиль кнопок в чате** (Copy, «Показать полностью», «Открыть», «Откатить»,
      «Показать diff»): один `QSS`-класс, один размер, один hover. Сейчас у каждой кнопки
      свой inline-стиль.
- [ ] **Tool-карточки — компактнее:** заголовок в одну строку (icon + `name(args)` + статус),
      preview в 2 строки, кнопки — только при hover или после раскрытия.
- [ ] **Тёмная/светлая тема для чата:** проверить, что `TextWidget` (markdown) и
      `ToolCallCard` не «слепнут» при светлой теме. Сейчас `TextWidget` авто-детектит
      по палитре, но `ToolCallCard` — нет (`background: rgba(120,120,140,0.06)` —
      невидимо на белом).
- [ ] **Код-блоки** — кнопка «Копировать» в правом верхнем углу (сейчас только общий
      Copy сообщения).
- [ ] **Длинные пути в tool-карточках** — обрезка с `…`, полный путь в tooltip.

#### 9.5.2 Reasoning-виджет: авто-высота и авто-скролл

- [x] **`ReasoningWidget`:** убрать `setFixedHeight(h)` в `_refresh_browser`. Высота
      должна расти по контенту (как `TextWidget._adjust_height`), но с **максимумом**
      (например, 40 % высоты чата) и **внутренним авто-скроллом** к последнему токену.
      Сделано в **0.1.3**.
- [x] **Авто-скролл внутри `ReasoningWidget`:** при `append_text` —
      `self._browser.verticalScrollBar().setValue(maximum())`, чтобы новые токены были
      видны без ручного скролла. Внешний чат при этом тоже сдвигается (AutoScrollArea).
      Сделано в **0.1.3**.
- [~] **Сворачивание по умолчанию:** в research/agent — свёрнут, в чате — развёрнут
      (по `_show_reasoning`). Поведение уже есть, но высота ломает UX. Осталось
      протестировать после 0.1.3.
- [x] **Плавное появление:** `QPropertyAnimation` на `maximumHeight` при разворачивании/
      сворачивании (0 → auto), 150 мс. Сделано в **0.1.3**.

#### 9.5.3 «Живой» UI: события из логов в интерфейсе

- [x] **`core/event_bus.py::EventBus`** — синглтон `QObject` с сигналом
      `event_emitted(str, dict)`. См. **2.5**.
- [x] **`core/logger.py`:** loguru-sink для `level >= INFO` → EventBus. См. **2.5**.
- [ ] **`ui/widgets/live_events.py::LiveEventsPanel`:**
  - `QPlainTextEdit` (read-only) или `QTextBrowser` с цветными строками по уровню.
  - Встроен в `ChatPanel` под `status_label` (сворачиваемый, высота 120 px, авто-скролл).
  - Или отдельная вкладка «📡 События» (если чат не вкладка — см. 9.1, то лучше панель).
  - Кнопка «Очистить», чекбокс «Только важное» (фильтр по `level >= WARNING` +
    структурированные события).
- [ ] **Индикатор «программа работает»:** в `StatusDashboard` — анимированная точка
      (пульсация `QTimer` 500 мс) пока `_generating = True`; тултип — последнее событие
      из `EventBus`. Решает боль «не понимаю, почему не отвечает».
- [ ] **Прогресс по этапам** вместо «🤔 Думает…»: `status_label` показывает этап
      (`🔍 Поиск по коду → 📖 Чтение 3 файлов → 🧠 Генерация (TTFT 1.2 с) → ✍️ 42 tok/s`).
      Источник — `EventBus`.
- [ ] **`ChatThoughtStream`** — уже реализует часть этого для research/agent; расширить
      на все режимы (в Chat — плоский `LiveEventsPanel`, в Agent/Plan —
      `ChatThoughtStream` + `LiveEventsPanel` для системных событий).

**Затрагивает:** новый `core/event_bus.py`, `core/logger.py`, новый
`ui/widgets/live_events.py`, `ui/chat/panel.py` (встроить панель, подписаться),
`ui/widgets/status_dashboard.py` (пульсация + tooltip), `agents/orchestrator.py`
(уже эмитит — пробросить в `EventBus`), `fs/vector_store.py` (`rag.search.*`),
`agents/agent_loop.py` (`tool.*`, `llm.*`).

#### 9.5.4 Прочие UX-фиксы чата

- [ ] **Кнопка «Стоп»** — заметнее (красный фон уже есть в `_STOP_BUTTON_BG`);
      добавить пульсацию при `streaming`.
- [ ] **Индикатор «прокручено вверх»** — `AutoScrollArea` уже показывает «▼ Вниз»;
      добавить счётчик новых сообщений на кнопке (`▼ Вниз (3)`).
- [ ] **Markdown-таблицы в чате** — проверить на светлой теме (сейчас
      `_render_table(dark=...)` — ок, но `TextWidget` авто-детект может ошибаться при
      смене темы на лету; подписаться на `theme_changed`).

### 9.6 История и восстановление сессий

**Боль:** «Проблема с восстановлением истории. Сейчас не отображается то, что было
сформировано при прошлом запросе. Сам запрос сохраняется, а вот история — нет».

- [x] **Диагностика:** `_on_agent_finish` (`ui/chat/panel.py`) и `_send_finished`
      создают `ChatMessage(role="assistant", content=result)` — только `TextPart`.
      Reasoning и tool_calls, накопленные в виджете, теряются. В research-режиме
      `_on_agent_finish` вообще не сохраняет финальный ответ (только паспорт).
- [x] **Фикс 1 — аккумулировать parts при стриминге** (0.1.3):
  - В `ChatPanel.__init__` — `self._current_assistant_parts: list[MessagePart] = []`.
  - В `_on_chunk` — `TextPart` (аккумулируется строкой, `TextPart` создаётся один раз в конце).
  - В `_on_thinking` — `ReasoningPart`.
  - В `_on_tool_started` — `ToolCallPart(calls=[ToolCall(...)])` или `ToolResultPart`
    после `_on_tool_executed`.
  - В `_on_agent_finish` — `ChatMessage(role="assistant",
    parts=self._current_assistant_parts)`; сохранить в `session_store` и `self._messages`.
- [x] **Фикс 2 — research-режим:** в `_on_agent_finish` (research-ветка) — сохранить
      `summary["final_answer"]` как `TextPart` в `ChatMessage` и записать в сессию.
      Плюс сохранить `hypotheses` как структурированный блок (опционально — `TextPart`
      с markdown-таблицей).
- [x] **Фикс 3 — `load_history`:** убедиться, что `session_store.get_history`
      восстанавливает `parts_json` (уже реализовано в
      `state/session_store.py::_parse_parts`); проверить, что
      `ChatMessage(parts=parts, tool_call_id=..., name=...)` корректно рендерится в
      `ChatMessageItem` при `load_history`.
- [x] **Фикс 4 — `_send_finished`** (старый путь, если ещё используется): та же логика
      аккумуляции parts.
- [x] **Тест:** `tests/ui/test_history_restore.py` — создать сессию, эмулировать
      стриминг (text + reasoning + tool_call), вызвать `_on_agent_finish`, перезагрузить
      через `get_history`, проверить, что все parts на месте.

**Затрагивает:** `ui/chat/panel.py` (`_on_chunk`, `_on_thinking`, `_on_tool_started`,
`_on_tool_executed`, `_on_agent_finish`, `_send_finished`, `load_history`),
`state/session_store.py` (уже готов).

### 9.7 Мультимодальность в UI (backend — в Фазе 5)

- [ ] **Drag&drop в поле ввода** (`ChatPanel.input_edit`): файлы (изображения, PDF,
      аудио) → `_attach_file` / `_attach_document`.
- [ ] **Кнопка «📎 Скрепка»** рядом с полем ввода: `QFileDialog` с фильтром по
      поддерживаемым типам.
- [ ] **Превью изображений** в пузыре user-сообщения (если `ChatMessage.images` не пуст) —
      миниатюра + клик для полного размера.
- [ ] **Индикатор распознавания** (OCR/STT): прогресс-бар в `DocumentPreviewDialog`
      для длинных PDF/аудио.
- [ ] **Аудио-плеер** в пузыре (если прикреплено аудио) — `QMediaPlayer` +
      кнопка play/pause.
- [ ] **Мультимедиа-кэш** — `~/.ollidesk/media_cache/` с хэшем файла → извлечённый
      текст; при повторном прикреплении — не пересчитывать.

Backend-часть (экстракторы, OCR, STT, vision) — остаётся в **Фазе 5**, только
вызовы из UI здесь.

### 9.8 Темы, прогресс, остальное (бывшие пункты 9.1/9.7 + UX из других фаз)

- [ ] **Цвет title bar в тёмной теме (Windows)** — `DwmSetWindowAttribute(
      DWMWA_USE_IMMERSIVE_DARK_MODE)` (предпочтительно) либо frameless + собственный
      title bar.
- [ ] **Авто-детект темы ОС** + ручное переключение в настройках.
- [ ] **Прогресс индексации:** ETA (по средней скорости), очередь файлов (список в
      tooltip), кнопка «Отменить» (сейчас только «Переиндексировать» = запуск заново).
- [ ] **Сплит «чат + редактор»** — если после 9.1 чат не вкладка, то возможен
      `QSplitter` «чат слева / редактор справа» (отложено, но в 9.1 архитектура
      `QStackedWidget` + `QTabWidget` это позволяет).
- [ ] **Метрики инструментов в статус-бар** (из 4.6): `_stats` в `ToolRegistry`
      (calls/errors/by_tool) → tooltip к `tools` в `StatusDashboard`.
- [ ] **Warning о `n_gpu_layers` в UI** (из 4.6): в `StatusDashboard` при загрузке SLM —
      tooltip с `n_ctx`, `n_gpu_layers`, `vram_used_mb`, `ram_used_mb`, `kv_cache_type`
      (из `OptimizedParams`).
- [ ] **Индикатор git-ветки** в `StatusDashboard` (из 9.4): `🌿 feature/x@abc1234`.
- [ ] **Кэш web-поиска** (из бэклога): namespace `rag` в `SearchCache` — чтобы
      RAG-ответы кэшировались.

### 9.9 Порядок работ в Фазе 9 (новый)

Приоритет — по боли пользователя:

1. **0.1.3 История + 0.1.4 Reasoning** (bug, ломает доверие и UX) — 1–2 дня.
2. **9.1 Стационарные вкладки** (архитектурная правка, разблокирует 9.5.3) — 2–3 дня.
3. **2.5 EventBus + 9.5.3 Живой UI** (`LiveEventsPanel`, пульсация, статусы по этапам) — 3–5 дней.
4. **9.5.1 Визуальный редизайн пузыря** — 3–5 дней.
5. **9.3 Модели** (поиск HF + `slm_models.yaml` + подсветка coder) — 5–7 дней.
6. **9.4 Git-интеграция** (`git_context` + `git_commit` + `ProjectSettings`) — 3–5 дней.
7. **9.2 Автопоказ структуры** — 1 день.
8. **9.7 Мультимодальность в UI** — 3–5 дней (после backend Фазы 5).
9. **9.8 Остальное** — по мере.

**Критерий готовности фазы:** пользователь открывает проект → видит структуру; задаёт
вопрос → видит все этапы (RAG → LLM → TTFT → токены → tool calls) в живой панели;
reasoning растёт сам; после перезагрузки история (включая reasoning и tool calls) на
месте; модели ищутся на HF и лежат в `slm_models.yaml`; в режиме разработки coder-модели
подсвечены; агент знает ветку/hash и умеет коммитить (с подтверждением).

---

## Фаза 10 — Кодирование и проверяемые инструменты (OlliDesk 2.0)

**Цель:** «максимально научилось кодировать» = лучше контекст + лучше проверка + лучше
цикл. Всё, что ниже, опирается на `ToolSpec` (2.6), `Policy Engine` (2.7), `EventBus` (2.5)
и `AsyncResource` (2.9).

### 10.1 `apply_patch` вместо полного `write_file`

- [ ] **`tools/file_ops.py::apply_patch`** — новый инструмент (capability `fs.write`,
      `is_mutating=True`, `ask_first`):
  - Принимает unified diff (или JSON-патч).
  - Проверяет контекст (hunk context must match).
  - При ошибке — не пишет, возвращает `ToolResult(is_error=True,
    error_category=DIFFERENT_PATH, content="Контекст не совпал: …")`.
  - При успехе — пишет и возвращает `dict{_llm_text, diff, lines_added,
    lines_removed, written_content, original_content, is_new_file, syntax_error}` —
    та же структура, что `write_file` (переиспользовать `ToolCallCard._render_diff_mode`).
  - `write_file` остаётся для новых файлов и перезаписи целиком; для правок агент
    предпочитает `apply_patch`.
- [ ] **Промпт-правило:** в `_TOOL_USE_INSTRUCTION` добавить «Для правок существующих
      файлов используй `apply_patch` с минимальным контекстом; `write_file` — только
      для новых файлов или полной замены».

### 10.2 `run_tests` tool

- [ ] **`tools/test_ops.py::run_tests`** (capability `shell`, `ask_first`, AGENT-only):
  - Аргументы: `framework: Literal["pytest","unittest","dotnet","go","npm"]`,
    `target: str | None`, `timeout_s: int = 120`.
  - Запускает соответствующую команду в `project_root`, парсит вывод в структуру:
    `{passed, failed, errors, skipped, failures: list[{test, message, file, line}]}`.
  - Возвращает `dict{_llm_text, summary, failures}` — UI рисует failures как карточки
    с jump-to-line в редакторе.
  - При таймауте — `ToolResult(is_error=True, error_category=RETRY_LATER,
    content="Тесты превысили таймаут …")`.
- [ ] **`ProfileConfig.run_tests_after_edit: bool = True`** для `development` —
      после `write_file`/`apply_patch` агент **сам** предлагает запустить тесты
      (но подтверждение — через `ask_first`).
- [ ] **В `_TOOL_USE_INSTRUCTION`** — правило: «После правки `.py`/`.cs`/`.go`/
      `.ts` — запусти `run_tests`. Если тесты упали — исправь и повтори, но не более
      N раз».

### 10.3 LSP для coding-цикла

- [ ] **`agents/lsp_bridge.py`** уже есть (`definition`, `references`); интегрировать
      в `fs/intel/`:
  - `ProjectIntel` при построении call graph использует LSP `references` для
    `class`/`method`/`function` (опционально, при `enable_lsp=True`).
  - Новый инструмент `find_references(symbol, file=None, line=None)` — capability `fs.read`,
    `auto`. Возвращает `dict{_llm_text, references: list[{file, line, preview}]}`.
- [ ] **`_TOOL_USE_INSTRUCTION`:** «Для поиска использований используй
      `find_references`; для поиска определения — `ProjectIntel`/`search_codebase`».
- [ ] **Fallback без LSP:** если LSP-сервер не установлен — `find_references` работает
      через BM25+vector (search_codebase по имени символа), помечает результат
      как «эвристика».

### 10.4 Verifier в обычном AgentLoop

- [ ] **`agents/verifier.py`** уже есть (`verify(answer, graph, evidence,
      known_paths, known_symbols, strict)`); подключить в `AgentLoop._final_call_no_tools`:
  - После сбора `final_text` — вызвать `verify(...)`.
  - Если `ver.ok == False` и `research_mode` или `coding_task` — не отдавать ответ,
    а сделать **один** регенерационный вызов с `regen_hint` (как в `Orchestrator.run`).
  - Если и второй ответ не проходит — отдать второй с явной пометкой
    `⚠️ не подтверждено: …`.
- [ ] **Coding-режим:** после `apply_patch`/`write_file` + `run_tests` — прогнать
      verifier по путям/символам в ответе; пустые блоки и «из головы» имена —
      регенерация.

### 10.5 Coding Bench OlliDesk

- [ ] **`tests/bench/`** — набор из 20 задач на мини-репозиториях:
  - 5 Python (pytest), 5 C# (MSTest), 5 TypeScript (vitest), 5 Go (go test).
  - Каждая задача: `repo/` + `task.md` + `tests/` + `expected.patch`.
  - Метрики: `pass@1` по тестам, `tool_success_rate`, `invalid_args_rate`,
    `time_to_answer_s`, `ttft_ms`.
- [ ] **`scripts/run_coding_bench.py`** — запускает `AgentLoop`/`Orchestrator` на каждой
      задаче, пишет `bench_results.jsonl`, считает агрегаты.
- [ ] **Цель:** `pass@1` +20–30 % к текущему, `invalid_args_rate ≤ 2 %`,
      `tool_success_rate ≥ 95 %`.

### 10.6 Model routing для кодинга

- [ ] **Профиль «Разработка»** → coder-модель (`tags: ["coder"]`, см. 9.3.2):
      `qwen2.5-coder-7b-instruct` / `qwen2.5-coder-14b-instruct` /
      `qwen2.5-coder-32b-instruct` (по доступной RAM/VRAM).
- [ ] **Planner 3B GPU / Analyst 7B–32B CPU** — уже есть.
- [ ] **Fallback:** если coder-модели нет — обычная `qwen2.5-7b-instruct`, пометить
      в `StatusDashboard` «⚠️ coder-модель не выбрана».
- [ ] **`_TOOL_USE_INSTRUCTION`:** «Ты — coding-ассистент. Читай файлы порциями,
      правь через `apply_patch`, запускай `run_tests`, проверяй `find_references`.
      Не выдумывай пути и имена — используй `ProjectIntel`».

### 10.7 Coding-цикл в Orchestrator

- [ ] **`Orchestrator.run`** для `task in {"write_code","refactor_plan","bug_analysis"}`
      использует расширенный флоу:
      `classify → plan → collect → apply_patch → run_tests → fix_loop → verify → critique`.
  - `fix_loop` — до 3 итераций `apply_patch → run_tests`; при провале — `verify` +
    `regen` с `fix_hint`.
- [ ] **`RunResult`** расширяется: `tests_passed`, `tests_failed`, `patches: list[dict]`,
      `iterations: int`.

**DoD фазы:** на Coding Bench pass@1 заметно выше; после правки файла агент умеет
запускать тесты и исправлять ошибки; verifier ловит галлюцинации путей; LSP используется
для reference lookup; coder-модель выбирается автоматически по профилю.

---

## Приоритеты и порядок

**OlliDesk 2.0 — приоритеты сверху вниз.**

1. **Фаза 0.1 — Стабилизация и безопасность** (1–2 недели):
   shell allowlist, redaction, история 2.0, reasoning 2.0, model selection fix.
2. **Фаза 2.5–2.9 — Ядро 2.0** (2–4 недели):
   EventBus, ToolSpec v2, Policy Engine, Application Services, AsyncResource.
3. **Фаза 9.1–9.6 — UX/UI 2.0** (3–5 недель):
   чат не вкладка, LiveEventsPanel, редизайн пузыря, reasoning 2.0, история 2.0.
4. **Фаза 9.3 + 9.4 — Модели и Git** (2–3 недели):
   HF search, `slm_models.yaml`, coder-теги, `git_context`, `git_commit`.
5. **Фаза 10 — Кодирование и проверяемые инструменты** (3–5 недель):
   `apply_patch`, `run_tests`, LSP, verifier в AgentLoop, Coding Bench.
6. **Фаза 7 — Расширяемость** (Plugin API, потом MCP) — после стабилизации ядра.
7. **Фаза 5 + 9.7 — Мультимодальность** (backend + UI).
8. **Фаза 8 — Релиз и качество** (CI windows-latest, PyInstaller, README).

**Порядок по локальным моделям** (Фаза 4.7 + 9.3): сначала `ModelCatalog` (9.3.3) и
HF search (9.3.1), затем coder-теги (9.3.2), затем routing (10.6). Цифры — из
`agents/benchmark.py`, публикуются в `docs/BENCHMARK.md`.

**Порядок по интерфейсу агента** (Фаза 4.8 + 2.5 + 10): сначала `EventBus` (2.5),
затем `LiveEventsPanel` (9.5.3), затем `verifier` в AgentLoop (10.4) и
`run_tests` (10.2) — только после этого имеет смысл включать `run_tests_after_edit`.

---

## Быстрый бэклог (мелочи)

- [x] `ruff` вместо `black`/`isort` — сделано: black и isort удалены из обеих групп
      зависимостей и из конфигурации, `ruff format` + правило I001 закрывают обе задачи.
- [ ] **`uv.lock` рассинхронизирован с `pyproject.toml`**: объявлен `llama-cpp-python>=0.3.0`
      (и транзитивный `diskcache`), в локе их нет. Любой `uv run` / `uv sync` молча
      переписывает лок, и правка попадает в случайный коммит. Перегенерировать один раз
      и держать лок в коммитах; причина — в лок собран до добавления SLM-зависимости.
- [x] Дубликат `duckduckgo-search` + `ddgs` — подтвердилось: оба экспортируют `DDGS`
      с разными типами, из-за чего mypy давал `assignment` в `tools/web_search.py`.
      Устаревший пакет удалён из зависимостей.
- [ ] `pyproject.toml` → `hatchling` вместо `setuptools` (упростит сборку wheel).
- [ ] `.gitignore` игнорирует `Notes.md`/`Roadmap.md`/`Model.md` по имени — файлы уже
      отслеживаются, но новые такие же будут скрыты. Переименовать правила на `/Notes.md`.
- [ ] Добавить `pyright`/`basedpyright` как второй тайпчекер (VS Code).
- [ ] Убрать `.idea/` из репозитория (уже в `.gitignore`, но проверить `git ls-files`).
- [x] **Кэш web-поиска: расширить на RAG-ответы** (общий `SearchCache` с namespace) —
      см. 9.8.
- [x] **Целевая версия Python**: `requires-python` поднят до `>=3.12`, `ruff`/`mypy`
      нацелены на `py312`, `ui/dialogs/wizard.py` использует `typing.override` (3.12+).
- [x] **Чтение больших файлов фрагментами**: `tools/file_ops.py::read_file(path, offset, limit)`
      поддерживает `offset`/`limit` с жёстким диапазоном
      `[MIN_READ_CHUNK_CHARS=4000, MAX_READ_FILE_CHUNK_CHARS=30000]`,
      `DEFAULT_READ_CHUNK_CHARS=12000` и хинт о продолжении в хвосте результата.
      `tools/attached_files.py::read_attached_file` использует тот же механизм.
      Возможность реализована в объёме, достаточном для чтения больших файлов.
- [x] **Опциональные зависимости `[docs]`** — парсеры PDF/DOCX/PPTX/XLSX/RTF
      (`pdfplumber`, `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `striprtf`,
      `pdf2image`). Для OCR сканов нужен системный Poppler.

---

## Что было удалено (для справки)

- **Фаза 1** (1.1–1.5) — полностью завершена: версия из `_version.py`, `ruff`/`mypy` в CI, chroma-граница в `fs/chroma_adapter.py`, удаление мёртвого кода, политика тестов.
- **Фаза 3** — RAG завершена: гибридный поиск, реранкинг, раздельные эмбеддинги, path-aware RAG, `fs/intel/`.
- **Фаза 4** — Контекст и промпты завершены: компактор, `core/budget.py`, `AGENTS.md`, фазы/гипотезы.
- **4.6.A** — базовая надёжность ReAct.
- **4.7.A** — `VectorStore` через `Embedder`-протокол, `last_stats`, `n_threads`.
- **4.7.C** — стратегии выбора моделей «поглощены профилями (9.4)».
- **4.8.A** — `MessagePart`-модель и событийный `ChatChunk`.
- **4.8.B** — композитный пузырь (`ReasoningWidget` + `TextWidget` + `ToolCallCard`).
- **4.8.C** — `run_shell_command` (ASK_FIRST, AGENT-only, чёрный список, песочница).
  **В OlliDesk 2.0** — заменён на allowlist + dangerous mode (0.1.1).
- **4.8.D** — метаданные `is_mutating`/`category` в `@tool`.

Кросс-ссылки в оставшихся разделах обновлены: диагностическая таблица (без «см. 1.5»), Фаза 4.6 (без пункта «Сейчас»), Фаза 4.7 (без «4.7.C»), Фаза 4.8 (без 4.8.A–D), «Приоритеты» (без `run_shell_command`-параграфа), «Быстрый бэклог» (без ссылок на 1.2/1.3).

**Новые архивные записи (OlliDesk 2.0):**
- **0.1.1** (shell allowlist), **0.1.2** (redaction), **0.1.3** (история + reasoning), **0.1.4** (model selection) — по завершении переезжают сюда.
- **2.5** (EventBus), **2.6** (ToolSpec v2), **2.7** (Policy Engine), **2.8** (Application Services), **2.9** (AsyncResource) — по завершении.
- **9.3.1–9.3.3** (HF search, coder tags, `slm_models.yaml`), **9.4** (git_context + `git_commit`), **9.5.2** (reasoning 2.0), **9.6** (история 2.0), **9.5.3** (LiveEventsPanel) — по завершении.
- **10.1–10.7** (кодирование) — по завершении.

---

## AGENTS.md — что изменилось

Обновил сохранение структуры и форматирования. Основные правки (OlliDesk 2.0):

1. **Диагностика / Статус SLM-стриминга**: `LocalLLMClient._generate` стримит — реализовано (9.5.2).
2. **`agents/local_llm_client.py`** — стриминг `TextPart` на каждый токен, `start`/`end`, tool-calls после стрима; `_DEFAULT_TEMPERATURE = 0.0` + `_TEMPERATURE_BY_OP`.
3. **`ui/chat/panel.py`** → миксины `BackendsMixin` (`ui/chat/backends.py`) + `ResearchMixin` (`ui/chat/research.py`). Поля — в `ChatPanel.__init__`, MRO — QWidget первым.
4. **`ui/chat/message_item.py`** — `ToolCallCard` теперь с diff-режимом для `write_file` (📂/🔍/↩), `_truncate_preview`, `_escape_diff`; **reasoning-виджет** — переработан в 0.1.3 (авто-высота, авто-скролл, плавное появление).
5. **`fs/vector_store.py`** — `search(categories=...)` (пост-RRF фильтр по `code`/`docs`), `_DOC_LANGUAGES` включает pdf/docx/pptx/xlsx/rtf; EventBus-эмиты `rag.search.*` (2.5).
6. **`agents/history_compactor.py`** — три ступени (reasoning → tool → реплики).
7. **`agents/tool_registry.py`** — `ErrorCategory`, `ToolResult.error_category`, `ToolResult.metadata`, `is_mutating`/`category`, приём `dict` от инструментов; **в 2.6** — `ToolSpec` + `capabilities`, **в 2.7** — `effective_policy`.
8. **`tools/shell_ops.py`** — **в 0.1.1** allowlist вместо `_BLACKLIST`, dangerous mode, audit log; `MAX_OUTPUT_CHARS=5000`, `MAX_TIMEOUT_S=300` (без изменений).
9. **`state/session_store.py`** — `parts_json` + идемпотентная миграция.
10. **`ui/main_window.py`** — контроллеры `FileTreeController`/`IndexingController`/`AppMenuBar`, `_do_check_ollama` на `_OllamaCheckThread`, `_stop_all_threads` без `new_event_loop`, `_toggle_backend`, `_on_project_tree_toggled`, `_on_ollama_checked`, `_toggle_side_panel` → `AgentSettingsDialog`, `_open_benchmark`, `_open_profile_editor`, `_on_show_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_export_chat`, `_show_file_diff`.
11. **`ui/chat/threads.py`** — `_BenchmarkThread`, `AgentChatThread.tool_executed` с 4-м аргументом `metadata`, `_ModelPoolLoadThread` с per-role timeout 90 с.
12. **`ui/dialogs/tool_confirmation_dialog.py`** — `_coerce_value`, `QTableWidget`-редактор аргументов.
13. **`core/sandbox.py`** — `SandboxedPathResolver`; **в 0.1.2** — расширенные `_DENIED_PARTS` (`.env`, `*.pem`, `*.key`, `id_rsa*`).
14. **`core/config.py`** — `profiles`/`active_profile`/`show_project_tree`/`show_pipeline_selectors`/`open_modified_as_diff`/`index_documents`/`max_document_size_mb`/`document_ocr`; `_GLOBAL_ONLY_KEYS` расширен; `_sync_builtin_profiles` (валидатор). **9.3.3** — `available_models` переезжает в `slm_models.yaml`.
15. **`core/profiles.py`** (новый) — `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES`, `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `recommended_model` в каждом встроенном профиле. **9.3.2** — добавляется `coder_models: list[str]`. **9.4** — `git_commit: "ask_first"` в `development`, `excluded` в `research`/`learning`.
16. **`core/chat_commands.py`** (новый) — реестр `@`-команд (`ALL_COMMANDS`, `COMMAND_TAGS`, `detect_active_command`, `match_commands`, `get_command`).
17. **`core/chat_export.py`** (новый) — `export_to_markdown`, `export_to_html`, `_plain_to_html`.
18. **`fs/document_extractors.py`** (новый) — `extract_document`, `is_document`, `language_for_document`, `SUPPORTED_EXTENSIONS`, `_ocr_pdf`.
19. **`agents/benchmark.py`** (новый) — `run_benchmark`, `BenchmarkPrompt`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`, `BENCHMARK_PROMPTS`.
20. **`tools/git_ops.py`** — `read_file_at_head`, `get_git_status_for_file`. **9.4** — `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`, `get_branch_diff`, `get_recent_commits`, `commit`.
21. **`ui/widgets/`** (новые) — `auto_scroll.py::AutoScrollArea`, `status_dashboard.py::StatusDashboard`, `pipeline_selectors.py::PipelineSelectors`, `profiles_selector.py::ProfilesSelector`, `command_completer.py::CommandCompleter`, `command_palette.py::CommandPalette`, `collapsed_placeholder.py::CollapsedPlaceholder`. **2.5** — `live_events.py::LiveEventsPanel`. **9.5.3** — подключение к EventBus.
22. **`ui/command_registry.py`** (новый) — `CommandRegistry`, `PaletteCommand`.
23. **`ui/dialogs/`** — новые: `agent_settings_dialog.py`, `benchmark_dialog.py`, `document_preview_dialog.py`, `export_chat_dialog.py`, `profile_editor_dialog.py`.
24. **`ui/theme.py`** — полностью переработан: `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `get_colors`, `get_stylesheet`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`.
25. **`ui/menus.py`** — добавлены сигналы/пункты: `export_chat_requested`, `profile_selected(str)`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`.
26. **§4 «Пакеты»** — синхронизировано с текущим кодом (`fs/intel/`, `agents/orchestrator.py`, `agents/evidence.py`, `agents/verifier.py`, `agents/critic.py`, `agents/benchmark.py`, `agents/hf_search.py` (9.3.1), `core/event_bus.py` (2.5), `core/policy.py` (2.7), `core/services/*` (2.8), `core/profiles.py`, `core/chat_commands.py`, `core/chat_export.py`, `core/redaction.py` (0.1.2), `core/slm_models_io.py` (9.3.3), `fs/document_extractors.py`, `fs/image_extractors.py` (Фаза 5), `fs/audio_extractors.py` (Фаза 5), `fs/video_extractors.py` (Фаза 5), `tools/test_ops.py` (10.2), `ui/widgets/live_events.py` (2.5), `ui/widgets/*`, `ui/state/async_resource.py` (2.9), `ui/chat/backends.py`, `ui/chat/research.py`).
27. **§5 «Пайплайн»** — убран устаревший роутинг «всегда ReAct»: research-вопросы уходят в `Orchestrator`, обычные — в `AgentLoop`; описан fallback при таймауте/ошибке. **2.5** — все этапы эмитят события в `EventBus`, UI показывает их в `LiveEventsPanel`. **10.7** — Orchestrator для coding-задач использует расширенный флоу с `apply_patch`/`run_tests`/`fix_loop`.
28. **§6 «Соглашения»** — `requires-python >= 3.12`, `typing.override` в `wizard.py`.
29. **§7 «Как добавить инструмент»** — `tools/registry.py::ALL_TOOLS` как фактическая точка регистрации. **9.4** — `git_commit` добавлен в `ALL_TOOLS` (ASK_FIRST, AGENT-only, capability `git`). **10.1** — `apply_patch` (capability `fs.write`). **10.2** — `run_tests` (capability `shell`). **10.3** — `find_references` (capability `fs.read`).
30. **§9 «Тесты»** — актуальный список покрытых модулей (см. `tests/`): `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py`, `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_sandbox.py`, `test_shell_ops.py`, `test_event_bus.py` (2.5), `test_policy.py` (2.7), `test_tool_spec.py` (2.6), `test_async_resource.py` (2.9), `test_history_restore.py` (9.6), `test_redaction.py` (0.1.2), `test_shell_allowlist.py` (0.1.1), `test_slm_models_io.py` (9.3.3), `test_hf_search.py` (9.3.1), `test_coding_bench.py` (10.5).
31. **§10 «Документация»** — без изменений в структуре (все файлы актуальны); добавлены `docs/SECURITY.md` (0.1.1), `docs/BENCHMARK.md` (10.5), `docs/CODING_BENCH.md` (10.5).
32. **§12 «Подводные камни»** — дополнено: CPU-only analyst cap `n_ctx=8192`, `MessagePart`/`with_content`/`model_copy`-ограничение, `run_shell_command` — единственный инструмент с побочными эффектами вне файлов проекта, `_suppress_collapse` в `load_history` (один проход O(n)), `CollapsedPlaceholder` — виджеты остаются в памяти (только удалены из layout). **9.1** — чат больше не вкладка, `QTabWidget` скрывается при `tab_count() == 0`. **9.6** — `_current_assistant_parts` аккумулирует части при стриминге; без этого история теряет reasoning/tool_calls. **9.5.2** — `ReasoningWidget` использует авто-высоту с максимумом (40 % чата) и внутренним авто-скроллом; `setFixedHeight` больше не применяется. **0.1.1** — `run_shell_command` требует dangerous mode для произвольных команд; allowlist — единственный штатный путь. **2.5** — EventBus-sink может блокировать GUI, если подписчик синхронный и тяжёлый; подписчики должны быть неблокирующими. **2.6** — capabilities проверяются **до** выполнения; `timeout_s` соблюдается через `asyncio.wait_for`. **2.7** — приоритет policy: security > profile > config > decorator. **10.1** — `apply_patch` не пишет файл, если контекст не совпал; `write_file` — только для новых файлов. **10.2** — `run_tests` парсит вывод, но не гарантирует 100 % покрытия фреймворков; fallback — сырой stdout. **10.4** — verifier может «зациклиться» на регенерации; лимит — 1 повтор.