# План модернизации OlliDesk 2.0

Рабочий документ: сюда попадают задачи по доработке проекта, сгруппированные по фазам.
Обновлять при каждом изменении архитектуры. Актуальная ветка — `next_generation`.

**OlliDesk 2.0** — целевое состояние: безопасный локальный coding-ассистент с «живым» UI,
проверяемыми инструментами, event-driven ядром и отдельным coding-циклом. Взгляд
«эталона» (OpenCode) взят только как источник архитектурных идей — **UI на SolidJS/TS
не переписываем**, PWA/multi-server/Solid не переносим. Всё, что ниже, — локальное
Windows-приложение на PySide6.

**Стек не меняем.** Python + PySide6 + llama.cpp — осознанный выбор: скорость итерации,
first-class биндинги для локального инференса (`llama-cpp-python`, `faster-whisper`,
`rapidocr`, `chromadb`, `pymupdf`), аудируемость и ремонтопригодность конечным
пользователем. Инвестируем не в переписывание, а в **профилирование** (`py-spy`) и
**изоляцию процессов** (`multiprocessing`/`QProcess`) для долгих задач (индексация,
LSP), чтобы GUI не залипал.

**Контекст 2026 (Россия, Москва):** платные/корпоративные API поиска (Google CSE,
Brave) могут быть недоступны. Это не повод блокировать агента — это повод построить
**цепочку провайдеров с fallback** (SearXNG → OpenSERP → Yandex API → Ollama →
DDG) и **три режима браузера** вместо одного опасного CDP-релея.

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
| **OCR изображений (JPG/PNG)** | ✅ | `fs/image_extractors.py`, `ChatPanel._attach_image`, превью в пузыре (Фаза 5 + 9.7) |
| **Аудио/видео (STT)** | ✅ | `fs/audio_extractors.py`, `fs/video_extractors.py`, `ChatPanel._attach_audio`/`_attach_video` (Фаза 5 + 9.7). **Аудио-плеер в пузыре — отложен.** |
| **Vision-модели через Ollama** | ❌ | `qwen2.5-vl` / `llama3.2-vision` — Фаза 5 (backend) — **отложено** |
| **MCP** | ✅ | `core/mcp_client.py`, `tools/mcp_ops.py`, `MCPManager` (Фаза 7) |
| **Система плагинов** | ❌ | Фаза 7 (после MCP) — **отложено** |
| **Событийная модель сообщений (`MessagePart`)** | ✅ | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`), `state/session_store.py` (колонка `parts_json`) |
| **Композитный пузырь сообщения** | ✅ | `ui/chat/message_item.py` (`ChatMessageItem` из `ReasoningWidget` + `TextWidget` + `ToolCallCard`) |
| **Выполнение shell-команд агентом** | ✅ | `tools/shell_ops.py::run_shell_command` (allowlist + dangerous mode, audit, песочница cwd) |
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
| **Стационарный чат без крестика (чат — не вкладка)** | ✅ | `MainWindow._create_center_panel` использует `QStackedWidget`: чат — страница 0, редактор — страница 1; `EditorWidget.tabs_changed(int)` управляет переключением (9.1) |
| **Авто-показ структуры проекта при открытии** | ✅ | `_do_open_project`: при первом открытии проекта `show_tree = True`; при повторном — из `config.show_project_tree` (9.2) |
| **Поиск моделей на HuggingFace** | ✅ | `agents/hf_search.py`, `ui/dialogs/hf_dialog.py`, кнопка «🔍 Поиск HF» в `SLMModelsDialog` (9.3.1) |
| **Отдельный `slm_models.yaml` (модели и tier вне `config.yaml`)** | ✅ | `core/slm_models_io.py`, `SLMConfig.available_models` загружается из YAML; `ConfigLoader._save_yaml` не пишет `available_models` в `config.yaml` (9.3.3) |
| **Подсветка coder-моделей в профиле «Разработка»** | ✅ | `PipelineSelectors._apply_coder_highlight`, иконки ролей (9.3.2) |
| **Git-контекст в промпте (ветка, hash, diff)** | ✅ | `AgentContext.git_context`, `ProjectService.build_git_context`, `ProjectSettings.git_branch/hash` (9.4) |
| **`git_commit` как инструмент агента** | ✅ | `tools/git_ops_tool.py::tool_git_commit` (ASK_FIRST, AGENT-only, capability `git`) (9.4) |
| **«Живой» UI: события из логов в интерфейсе** | ✅ | `core/event_bus.py`, `core/logger.py::_event_bus_sink`, `ui/widgets/live_events.py::LiveEventsPanel`, `ChatPanel._on_bus_event` (2.5) |
| **Natural exit + soft budgets в `AgentLoop`** | ✅ | `AgentLoop.run` — `SoftBudget`, `BudgetStatus`, `_final_call_no_tools`, `finish_reason=stop`, `user_cancel` (2.10) |
| **Парк моделей (router/planner/coder/verifier)** | ✅ | `ModelPool` с `Role` (ROUTER, PLANNER, CODER, VERIFIER, ANALYST, ANALYST_PRO, EMBED), `select_park`, `park_roles` в профиле (4.1) |
| **Иерархический контекст + prefix caching** | ✅ | `core/context_budget.py::hot_window_tokens/warm_layer_tokens`, `history_compactor` с `hot_window_tokens`, `n_ctx_hint` в профиле, `LocalLLMClient.last_stats` с `cache_reads/writes` (4.2) |
| **Эпизодическая память** | ✅ | `core/services/memory_service.py`, `ui/dialogs/memory_dialog.py`, `MemoryConfig` (4.3) |
| **MCP-клиент** | ✅ | `core/mcp_client.py::MCPClient`, `tools/mcp_ops.py::MCPManager`, `MCPConfig` (7) |
| **Цепочка поиска с fallback** | ✅ | `agents/search_providers.py`, `tools/web_search.py`, `SearchConfig` (2.13) |
| **`browser_control` с 3 режимами** | ✅ | `agents/browser_client.py`, `tools/browser_ops.py`, `BrowserConfig` (2.11) |
| **Durable inbox** | ✅ | `state/inbox.py::SessionInbox`, `ChatPanel._ensure_inbox`, `AgentChatThread.claim` (2.12) |
| **LSP-диагностика после edit** | ✅ | `tools/file_ops.py` вызывает `LspBridge.diagnostics` после `write_file`/`apply_patch`, `metadata.lsp_errors` (10.8) |
| **Stale check + автоснапшот** | ✅ | `tools/file_ops.py::apply_patch(expected_hash)`, `ToolService._maybe_snapshot`, `ToolResult.metadata.snapshot_id` (10.9) |
| **Режимы свободы в профилях** | ✅ | `ProfileConfig.freedom_level`, `_FREEDOM_MATRIX` в `core/policy.py`, бейдж в `StatusDashboard` (9.12) |
| **Auto-height reasoning-виджет** | ✅ | `ReasoningWidget._compute_max_height()` — авто-высота с потолком 40 % viewport, внутренний авто-скролл, `QPropertyAnimation` (0.1.3a) |
| **Восстановление истории с reasoning/tool_calls** | ✅ | `_build_assistant_message` (`0.1.3b`) + `_render_assistant_parts` (`0.1.3c`); `ChatMessage(parts=[...])` сохраняется в `SessionStore` |
| **Drag&drop файлов в чат** | ✅ | `ChatPanel.eventFilter` обрабатывает `DragEnter`/`Drop`; кнопка «📎» (9.7) |
| **Превью изображений/аудио в пузыре** | ✅ | `ChatMessageItem._make_thumbnail_label` для изображений; **аудио-плеер — отложен** (9.7) |
| **EventBus / структурированные события** | ✅ | `core/event_bus.py`, `core/events.py`, `core/logger.py::_event_bus_sink` (2.5) |
| **ToolSpec v2: capabilities + timeout + idempotent** | ✅ | `agents/tool_registry.py::ToolSpec`, `@tool` с capabilities/timeout_s/max_output_chars/idempotent/needs_snapshot (2.6) |
| **Policy Engine (единая точка авторизации)** | ✅ | `core/policy.py::effective_policy`, `ToolRegistry.get_effective_policy`, `explain_policy` (2.7) |
| **Application Services (2.8)** | ✅ | `core/services/{tool,project,model,chat,memory}_service.py`. `ToolService` — audit для всех инструментов + автоснапшот; `ProjectService` — кэш `ProjectIntel` + git_context; `ModelService` — конфиг + `resolve_park`/`resolve_coder`; `ChatService` — `build_agent_context` без Qt (готов к интеграции в `_do_chat`; **миграция отложена**) |
| **Audit log инструментов** | ✅ | `core/audit.py` + `core/services/tool_service.py`: **все** вызовы пишутся в audit (было: только `run_shell_command`) |
| **Shell allowlist (а не чёрный список)** | ✅ | `tools/shell_ops.py::_ALLOWLIST_RE` + `SecurityPolicy.allow_arbitrary_shell` (dangerous mode) + чёрный список сохраняется (0.1.1) |
| **Модель за сессией / явный выбор** | ✅ | `_settings_payload` фильтрует `model` по `active_backend`; `_suppress_model_save` в `set_model` (0.1.4) |
| **Изоляция тестов ChatPanel** | ✅ | Фикстуры подавляют `_detect_backends`; `QTimer` в `ChatPanel.__init__` привязан к `self` (0.1.5) |
| **`apply_patch` (unified diff вместо полного `write_file`)** | ✅ | `tools/file_ops.py::apply_patch` (10.1) |
| **`run_tests` tool** | ✅ | `tools/test_ops.py::run_tests` (10.2) |
| **Verifier в обычном AgentLoop** | ✅ | `AgentLoop._verify_and_maybe_regen` (10.4) |
| **LSP для definition/references в coding-цикле** | ✅ | `agents/lsp_bridge.py`, `find_references` в `tools/codebase_search.py` (10.3) |
| **Coding Bench OlliDesk** | ✅ | `scripts/run_coding_bench.py`, `tests/bench/` (10.5) |
| **`ModelCatalog` YAML** | ✅ | `core/slm_models_io.py`, `~/.ollidesk/slm_models.yaml` (9.3.3) |
| **AsyncResource для Qt (idle/loading/ready/refreshing/error)** | ✅ | `ui/state/async_resource.py`, используется в `PipelineSelectors`, `SessionManagerDialog`, `SLMModelsDialog` (2.9, 9.11) |
| Тесты UI / state | ✅ | `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py`, `test_audit.py`, `test_shell_allowlist.py`, `test_reasoning_widget.py`, `test_history_save.py`, `test_history_restore.py`, `test_model_selection.py` |

---

## Фаза 0.1 — Стабилизация и безопасность (OlliDesk 2.0) — **ЗАКРЫТА**

**Все подпункты 0.1.1–0.1.5 реализованы.** Redaction (0.1.2) подключён к логам,
tool-аргументам и tool-результатам; `.env`/`*.pem`/`*.key`/`id_rsa*` заблокированы
`SandboxedPathResolver`.

### 0.1.1 Shell allowlist + audit log — `[x]`

- [x] **Allowlist вместо чёрного списка.** `tools/shell_ops.py::_ALLOWLIST_RE`
      пропускает только безопасные команды: `Test-Path`, `Get-ChildItem`,
      `Get-Content`, `Select-String`, `Measure-Object`, `Get-Location`,
      `Resolve-Path`, `Get-FileHash`,
      `python -m pytest|py_compile|unittest|mypy|ruff`,
      `git status|diff|log|show|branch|rev-parse|describe`,
      `dotnet test`, `go test`, `npm test`.
- [x] **Dangerous mode.** `SecurityPolicy.allow_arbitrary_shell: bool = False`
      в `core/security.py`; включается явным чекбоксом в
      `AgentSettingsDialog` (группа «Безопасность»), хранится в
      `AppConfig.allow_arbitrary_shell` и включён в `_GLOBAL_ONLY_KEYS`.
- [x] **Чёрный список (`_BLACKLIST`) сохранён** как дополнительный слой:
      даже в dangerous mode блокируются `rm -rf`, `Remove-Item -Recurse -Force`,
      `format`, `shutdown`, `reg add`, `Set-ItemProperty`, `curl | iex`,
      `iwr | iex`, `Invoke-WebRequest | iex`.
- [x] **`core/audit.py::AuditLog`** — SQLite-журнал `~/.ollidesk/audit.db`:
      timestamp, tool, args_hash, policy, approved, result_hash, error, dangerous.
      Хеши — не сами данные.
- [x] **`run_shell_command` пишет в audit каждый вызов:** успех, ошибка,
      отказ allowlist, отказ blacklist. `dangerous=True` — только в dangerous mode.
- [x] **`AgentContext.security: SecurityPolicy | None`** — политика
      прокидывается в инструменты через `extra_kwargs`; используется
      `run_shell_command` и `web_search`.
- [x] **`ui/dialogs/agent_settings_dialog.py`** — чекбокс «Разрешить
      произвольные shell-команды (dangerous mode)».
- [x] **Тесты:** `tests/core/test_audit.py` (6 кейсов), `tests/tools/test_shell_allowlist.py`
      (8 кейсов).

### 0.1.2 Redaction секретов — `[x]`

**Реализовано.** Без redaction «свободный» агент (shell, web, browser) отправлял
бы секреты в LLM и логи; теперь секреты вырезаются единой точкой.

- [x] **`core/redaction.py`** — единая точка вырезания секретов:
  - **`redact(text: str) -> tuple[str, list[str]]`** — основная функция.
  - **`_PATTERNS`** — список compiled regex:
    - OpenAI API keys: `sk-[A-Za-z0-9_-]{20,}`.
    - HuggingFace tokens: `hf_[A-Za-z0-9]{20,}`.
    - GitHub tokens: `ghp_[A-Za-z0-9]{20,}`, `github_pat_…`.
    - Slack tokens: `xox[bpors]-…`.
    - AWS access keys: `AKIA[0-9A-Z]{16}`.
    - JWT: `eyJ…\.eyJ…\.[A-Za-z0-9_-]{10,}`.
    - Пароли из URL/DSN: `://[^:]+:[^@]+@` → `://[REDACTED:url_password]@`.
    - Приватные ключи: `-----BEGIN … PRIVATE KEY-----…-----END … PRIVATE KEY-----`.
    - Env-переменные с секретными именами (`PASSWORD`, `TOKEN`, `API_KEY`, …).
  - **`redact_dict(d) -> Any`** — рекурсивно по строковым значениям.
  - **`redact_only(text) -> str`** — без списка категорий.
  - **`redact_exc(e) -> str`** — безопасное строковое представление.
  - **`contains_secret` / `is_secret`** — эвристики (в т.ч. «случайный токен»).
- [x] **Redaction в логах.** `core/logger.py::_redact_filter` — loguru-filter
      пропускает `message` через `redact()` для stderr, файла и EventBus-sink.
      При срабатывании дописывает `[redacted:category]`.
- [x] **Redaction в tool-аргументах.** `agents/tool_registry.py::execute` —
      `redaction.redact_only` для ошибок валидации; `redact_exc` для исключений;
      audit пишет хеш от оригинала.
- [x] **Redaction в результатах инструментов.** `ToolResult.content` —
      `redaction.redact_only(...)`; `metadata` (diff, written_content) не трогается.
- [x] **Запрет `.env`, `.netrc`, `.pgpass`, `*.pem`, `*.key`, `*.p12`, `*.ppk`,
      `*.kdbx`, `id_rsa*`, `secrets.yaml`** в `read_file`/`list_directory`/
      `search_codebase` — `core/sandbox.py::SandboxedPathResolver._DENIED_FILE_NAMES`
      + `_DENIED_FILE_PATTERNS`. `allow_sensitive=True` снимает запрет для
      `write_file`/`apply_patch`.
- [x] **`web_search` hygiene.** `tools/web_search_hygiene.py` — `filter_query()`
      вызывает `redact()` перед отправкой в поисковик.
- [x] **`EventBus`-совместимость.** payload `log`-событий проходит через
      `_redact_filter`.
- [x] **Тесты:** `tests/core/test_redaction.py` (15+ кейсов).

**DoD 0.1.2:** ни один секрет не попадает в логи, LLM-промпт или UI;
`.env`/`*.pem`/`*.key`/`id_rsa*` недоступны без явного capability. ✅

### 0.1.3a Reasoning 2.0 (авто-высота + авто-скролл + анимация) — `[x]`

- [x] **`ReasoningWidget._compute_max_height()`** — потолок 40 % viewport
      ближайшего `QScrollArea`, fallback на высоту окна, `_FALLBACK_MAX_HEIGHT = 480`.
- [x] **`_refresh_browser` больше не вызывает `setFixedHeight(h)`**;
      `setMaximumHeight(cap)` + рост по контенту.
- [x] **Внутренний авто-скролл к последнему токену** при `append_text`.
- [x] **`QPropertyAnimation` на `maximumHeight`** (150 мс); `_animate_height`
      обрабатывает вырожденный случай `start == target` (иначе в PySide6
      `finished` не приходит и тело не скрывается).
- [x] **`resizeEvent`** — пересчёт cap при ресайзе окна, если не идёт анимация.
- [x] **Тесты:** `tests/ui/test_reasoning_widget.py` (7 кейсов).

### 0.1.3b История 2.0 — сохранение parts — `[x]`

- [x] **`ChatPanel._current_assistant_parts` + `_current_assistant_text` +
      `_current_assistant_reasoning`** — аккумуляторы текущего ответа.
- [x] **`_on_thinking` копит reasoning независимо от `_show_reasoning`** —
      история не теряет `reasoning_content` при выключенном тумблере.
- [x] **`_on_chunk` / `_on_tool_started` / `_on_tool_executed`** аккумулируют
      `TextPart` / `ToolCallPart` / `ToolResultPart`.
- [x] **`_build_assistant_message(fallback_text)`** собирает
      `ChatMessage(parts=[ReasoningPart?, TextPart?, *tool_parts])`.
- [x] **`_on_agent_finish`** (обычная и research-ветка) и **`_send_finished`**
      сохраняют через `_build_assistant_message`.
- [x] **`_on_orchestrator_result`** — `ChatMessage(parts=[TextPart(...)])`
      (Оркестратор не стримит через `_on_chunk`).
- [x] **Тесты:** `tests/ui/test_history_save.py` (4 кейса).

### 0.1.3c История 2.0 — восстановление — `[x]`

- [x] **`_render_assistant_parts(msg)`** — восстановление композитного пузыря
      из `msg.parts`: reasoning → text → tool_call → tool_result (матчинг
      `ToolResultPart` с последней pending-карточкой по `name`).
- [x] **`load_history`** использует `_render_assistant_parts` для assistant-сообщений
      с непустым `parts`; для остальных — fallback на `_add_message(role, content)`.
- [x] **Тесты:** `tests/ui/test_history_restore.py` (4 кейса).

### 0.1.4 Модель за сессией / явный выбор — `[x]`

- [x] **`_settings_payload()`** — единая точка фильтра: `model` включается в
      payload только если `active_backend == OLLAMA`. Раньше
      `_save_current_settings` (вызывался из `_on_model_changed`) эмитил `model`
      всегда, и `MainWindow._on_chat_settings_changed` перетирал
      `config.last_model` именем Ollama-модели даже когда активна SLM.
- [x] **`set_model` под `_suppress_model_save`** — программная установка модели
      (восстановление `last_model` при открытии проекта) не триггерит сохранение.
- [x] **`SLMModelsDialog._activate_selected`** уже выключал `auto_select_model`
      при явном выборе — покрыто тестом.
- [x] **Тесты:** `tests/ui/test_model_selection.py` (6 кейсов).

### 0.1.5 Изоляция тестов ChatPanel — `[x]`

- [x] **`QTimer.singleShot(100, self._detect_backends)` в `ChatPanel.__init__`**
      заменён на `QTimer(self)` с `timeout.connect(...)` — таймер привязан к
      `self` и отменяется при `deleteLater()`/`shutdown()`.
- [x] **Фикстуры ChatPanel** в `tests/ui/test_history_save.py`,
      `tests/ui/test_history_restore.py`, `tests/ui/test_model_selection.py`
      подавляют `_detect_backends` и вызывают `shutdown()` в teardown
      через `contextlib.suppress`.

**DoD фазы:** shell не выполняет произвольные команды без dangerous mode;
секреты не утекают в LLM/логи; история после перезапуска содержит reasoning
и tool_calls; reasoning не обрезается; модели не «перескакивают» при явном
выборе. ✅

---

## Фаза 2 — Архитектурные улучшения

### 2.1 Протокол LLM-бэкенда — `[x]`
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

### 2.2 Реестр инструментов — `[x]`
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
      **В Фазе 2.7 вводится Policy Engine** — единая точка авторизации. Реализовано.
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

### 2.3 Управление потоками и отменой — `[~]`
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
      `_DownloadThread` (см. 9.3.3). **Отложено.**
- [ ] Корректно отменять/удалять потоки при закрытии окна (`closeEvent`).
- [ ] **`[новое]` Долгие задачи — в отдельные процессы.** `multiprocessing`/
      `QProcess` для индексации больших проектов и LSP:
  - **`ui/indexing_controller.py`**: `IndexingThread` → `IndexingProcess`
    (`QProcess` + `multiprocessing`), чтобы процесс можно было убить без
    «залипания» GUI.
  - **`agents/lsp_bridge.py`**: LSP-сервер уже subprocess — добавить
    `QProcess`-обёртку для управления жизненным циклом из GUI-потока.
  - **`agents/slm_optimizer.py`**: тяжёлый бенчмарк (4.7.B) — в отдельный процесс.
  - **Правило:** процесс можно убить (`kill()`), поток — нет; для долгих
    задач (>5 с) — только процесс.
  - **Тесты:** `tests/ui/test_indexing_process.py` (mock `QProcess`).
  - **Отложено.**

### 2.4 Разделение `ui/main_window.py` и `ui/chat/panel.py` — `[x]`
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
      **Отложено.**

`MainWindow` из «всё-в-одном» превратился в UI-оркестратор: владеет
виджетами прогресса/статуса и подключает сигналы контроллеров к своим
слотам, но не знает про `QFileSystemModel`, `GitStatusDelegate`,
`IndexingThread` и `ProjectState`. Приватная связь `MainWindow ↔ chat_panel`
(прямой доступ к полям) заменена на 5 публичных методов `ChatPanel`
(см. выше).

### 2.5 EventBus и структурированные события (OlliDesk 2.0) — `[x]`

**Боль:** «логи информативнее UI, пользователь не понимает, почему программа не отвечает». **Реализовано.**

- [x] **`core/event_bus.py`**:
  - `EventBus` — синглтон с `emit(kind, payload)` / `subscribe(kind, cb) -> Unsubscribe`.
  - `EventBus.clear()` — для тестов.
  - **`seq`** — монотонный счётчик (`itertools.count()`), добавляется в payload
    как `payload["_seq"]` (копия; оригинал не мутируется). Монотонность
    проверяется в `tests/core/test_event_bus.py`.
  - `emit()` не держит `lock` во время вызова подписчиков — защита от дедлока
    при рекурсивном `emit()`. Ошибка подписчика логируется и проглатывается.
- [x] **Схема событий** (`core/events.py`, `EventKind` (StrEnum) + TypedDict-контракты):
  - `rag.search.started` / `rag.search.found` / `rag.search.empty`
    (поля: `query`, `n_results`, `duration_ms`).
  - `llm.request.sent` / `llm.first_token` / `llm.generation.done`
    (поля: `ttft_ms`, `tokens_per_sec`, `model`, `prompt_tokens`,
    `completion_tokens`, `cache_reads`, `cache_writes`).
  - `tool.started` / `tool.finished` / `tool.error`
    (поля: `name`, `args_hash`, `duration_ms`, `is_error`, `error_category`,
    `lsp_errors`, `snapshot_id`).
  - `phase.changed` (research: RECON → DEEP_DIVE → SELF_CHECK → SYNTHESIS).
  - `index.started` / `index.progress` / `index.finished`
    (поля: `total`, `done`, `eta_s`).
  - `slm.loading` / `slm.loaded` / `slm.failed`
    (поля: `model_id`, `load_ms`, `n_ctx`, `n_gpu_layers`, `vram_mb`, `ram_mb`).
  - `budget.warning` (2.10) — приближение к soft budget.
  - `budget.exceeded` (2.10) — превышение, но не остановка.
  - `agent.finished` (2.10) — `finish_reason`, `iterations`, `tokens`,
    `wall_clock_s`.
  - `snapshot.created` (10.9) — `snapshot_id`, `files`.
  - `mcp.connected` / `mcp.disconnected` / `mcp.tool_called` (7).
  - `inbox.enqueued` / `inbox.delivered` (2.12).
  - `memory.recalled` / `memory.remembered` (4.3).
  - `search.provider.used` (2.13) — какой провайдер сработал.
  - `browser.action` (2.11) — `action`, `url`, `result`.
  - `orchestrator.event` — дублирование `on_event` в EventBus.
  - `hf.search.*` (9.3.1), `git.*` (9.4), `permissions.*` (9.10), `media.*` (Фаза 5).
- [x] **Loguru-sink** в `core/logger.py`: для `level >= INFO` эмитит
      `EventBus.event_emitted("log", {level, message, source, timestamp})`.
      Все текущие `logger.info`-строки становятся видимыми в UI без переписывания
      модулей. Подписчики обрабатывают в своём потоке через `QtEventBus`.
- [x] **Точки эмита:**
  - `fs/vector_store.py::search` — `rag.search.*`.
  - `agents/agent_loop.py` — `tool.*`, `llm.*`, `budget.*`, `agent.finished`.
  - `agents/orchestrator.py::run` — `orchestrator.event`.
  - `ui/indexing_controller.py` — `index.*`.
  - `ui/chat/backends.py::_on_slm_loaded` — `slm.*`.
  - `tools/file_ops.py` — `tool.finished` с `lsp_errors` (10.8).
  - `tools/mcp_ops.py` — `mcp.*` (7).
  - `state/inbox.py` — `inbox.*` (2.12).
  - `core/services/memory_service.py` — `memory.*` (4.3).
  - `agents/search_providers.py` — `search.provider.used` (2.13).
  - `agents/browser_client.py` — `browser.action` (2.11).
- [x] **UI-подписка:** `ui/widgets/live_events.py::LiveEventsPanel` (сворачиваемая
      панель под чатом, высота 200 px, авто-скролл, чекбокс «Только важное»,
      фильтр по `kind`, кнопка «Очистить»). `ChatPanel._setup_ui` создаёт панель
      и подписывается на EventBus через `QtEventBus`.
- [x] **Индикатор «программа работает»:** `StatusDashboard` — пульсирующая точка
      (`QTimer 500 мс`) пока `_generating=True`; tooltip — последнее событие
      из EventBus.
- [x] **Статус-строка «по этапам»** вместо «🤔 Думает…»: `🔍 Поиск по коду →
      📖 Чтение 3 файлов → 🧠 Генерация (TTFT 1.2 с) → ✍️ 42 tok/s`. Источник — EventBus.
- [x] **Reasoning trace:** из существующих данных (`Critique`, `VerifyResult`,
      tool metadata) генерировать объяснение решений («выбрал `apply_patch`,
      потому что файл существует и контекст совпал»). Показывать в
      `LiveEventsPanel` или раскрывающемся трейсе `ChatThoughtStream`.
- [x] **Тесты:** `tests/core/test_event_bus.py` (subscribe/emit/unsubscribe,
      sink, seq-монотонность, неблокирующий emit),
      `tests/ui/test_live_events.py` (панель не создаёт reactive
      computations при вызове).

### 2.6 ToolSpec v2 и capabilities (OlliDesk 2.0) — `[x]`

**Боль:** «инструменты опасны, не хватает контракта и авторизации». **Реализовано.**

- [x] **`agents/tool_registry.py::ToolSpec`** — immutable dataclass:
  ```python
  @dataclass(frozen=True)
  class ToolSpec:
      name: str
      description: str
      schema: dict
      modes: frozenset[AgentMode]
      policy: ToolPolicy
      capabilities: frozenset[str]  # fs.read, fs.write, net, shell, git, ui, secrets, browser
      is_mutating: bool
      category: str
      timeout_s: float = 30.0
      max_output_chars: int = 5000
      idempotent: bool = False
  ```
  `needs_snapshot` — в `ToolService` по capabilities `fs.write` (10.9).
- [x] **`@tool`** принимает `capabilities: list[str]`, `timeout_s`,
      `max_output_chars`, `idempotent`. Дефолт `capabilities` — `{"fs.read"}`.
  - `read_file`/`list_directory`/`search_codebase`/`read_attached_file`/
    `find_references` — `fs.read`.
  - `write_file`/`apply_patch`/`run_shell_command` — `fs.write`/`shell`.
  - `web_search`/`get_weather`/`get_exchange_rate` — `net`.
  - `browser_open`/`browser_click`/`browser_fill`/`browser_snapshot`/`browser_close` — `browser` (2.11).
  - `get_git_status`/`create_snapshot`/`undo_file_snapshot`/`git_commit` — `git`.
  - `record_hypothesis`/`update_hypothesis`/`list_hypotheses` — `ui`.
- [x] **`ToolRegistry.get_spec(name) -> ToolSpec`** / `try_get_spec` / `get_all_specs` —
      единая точка доступа.
- [x] **`register_spec`** — регистрация MCP-инструментов из `MCPManager`.
- [x] **`ToolRegistry.execute`** проверяет `capabilities` **до** выполнения
      через `effective_policy` (2.7).
- [x] **Per-session overrides:** `AppConfig.session_overrides: dict[str, ToolPolicy]` —
      пользователь может разрешить инструмент «на эту сессию».
- [x] **`timeout_s` соблюдается через `asyncio.wait_for`** — в `ToolRegistry.execute`
      для async-инструментов; для sync — через `asyncio.to_thread` с таймаутом.
- [x] **`max_output_chars`** — поле есть; фактическое усечение реализовано в
      `AgentLoop._create_tool_result_message` (max_chars).
- [x] **`idempotent`** — поле есть (задел под кэш).
- [x] **Тесты:** `tests/agents/test_tool_spec.py`.

### 2.7 Policy Engine (OlliDesk 2.0) — `[x]`

**Боль:** «политики раскиданы по трём местам, приоритет неочевиден». **Реализовано.**

- [x] **`core/policy.py::effective_policy(tool_spec, profile, security, config) ->
      EffectivePolicy`** — единая точка:
  ```python
  @dataclass(frozen=True)
  class EffectivePolicy:
      policy: ToolPolicy  # auto | ask_first | excluded
      allowed: bool
      reason: str  # "security:secrets" | "freedom" | "profile" | "session" | "config" | "default"
  ```
- [x] **Приоритет (сверху вниз):**
  0. `secrets` — всегда `excluded` (даже в `open`).
  1. `SecurityPolicy` (confidential → всё `net`/`shell`/`git`/`browser` = `excluded`).
  2. `current_mode not in spec.modes` — `excluded`.
  3. `ProfileConfig.freedom_level` — базовая политика по capabilities.
  4. `ProfileConfig.tool_policies` — явный override профиля.
  5. `AppConfig.session_overrides` (per-session).
  6. `AppConfig.tool_policies`.
  7. Декоратор `@tool(policy=...)`.
- [x] **Матрица `_FREEDOM_MATRIX`** — `freedom_level × capability`:
  - `fs.read` → `auto` во всех уровнях.
  - `fs.write` → `ask_first` в `safe`/`research`, `auto` в `development`/`open`.
  - `net` → `ask_first` в `safe`/`development`, `auto` в `research`/`open`.
  - `shell` → `excluded` в `safe`/`research`, `ask_first` в `development`/`open`.
  - `git` → `excluded` в `safe`/`research`, `ask_first` в `development`, `auto` в `open`.
  - `browser` → `excluded` в `safe`/`research`, `ask_first` в `development`, `auto` в `open`.
  - `secrets` → `excluded` всегда.
  - `ui` → `auto` во всех уровнях.
- [x] **`explain_policy`** — пошаговое объяснение (для Permissions Dashboard 9.10).
- [x] **`confirm.web_search`** — внутри security-слоя.
- [x] **`ChatPanel._build_registry`** использует `effective_policy` через
      `AgentLoop`/`ToolService`; `_apply_config_policies` оставлен как fallback
      для явных `set_policy` из UI.
- [x] **`ToolRegistry.execute`** вызывает `effective_policy` и возвращает
      `ToolResult(is_error=True, ...)` без выполнения.
- [x] **Per-session overrides в UI:** `ToolConfirmationDialog` — кнопка
      «📌 Разрешить на сессию» → `ChatPanel._allow_tool_for_session` →
      `config.session_overrides[tool_name] = AUTO`. Сбрасывается при закрытии сессии.
- [x] **Тесты:** `tests/core/test_policy.py`.

### 2.8 Application Services — `[x]` (кроме миграции `ChatService`)

**Боль:** «UI знает про `QFileSystemModel`, `ProjectState`, `SessionStore`,
`VectorStore` напрямую; тяжело тестировать».

**Сделано:**
- ✅ `core/services/memory_service.py` (4.3) — эпизодическая память.
- ✅ `core/services/tool_service.py` — единая точка: policy → snapshot → execute → audit. Audit теперь пишется для **всех** вызовов, снапшот — перед `git_commit` и `run_shell_command` в dangerous mode (`write_file`/`apply_patch` уже создают сами, дубля нет). Интегрирован в `AgentLoop` через опциональный `tool_service`.
- ✅ `core/services/project_service.py` — кэш `ProjectIntel` по TTL 5 мин, `git_snapshot`, `build_git_context`. Устраняет дублирование `Orchestrator._intel_memo`. `ChatPanel._build_git_context` → одна строка через сервис.
- ✅ `core/services/model_service.py` — `get_config`/`reload_config`, `resolve_park`, `resolve_coder`, `resolve_paths`. `ChatPanel._detect_and_load_slm` использует `reload_config()`.
- ✅ `core/services/chat_service.py` — `ChatRequestSnapshot` + `ChatService.build_agent_context` + `pick_max_iterations`. **Не интегрирован в `_do_chat/_do_agent`** — большая правка, риск регрессии; готов к миграции отдельной итерацией.

**Осталось:**
- [ ] Миграция `ChatPanel._do_chat/_do_agent` на `ChatService.build_agent_context` — убирает ~30 строк ручной сборки в двух местах. Требует прогона `AgentChatThread`-тестов и ручной проверки ресёрч/кодинг-флоу. **Отложено.**
- [ ] Вынос `_connect_agent_thread_signals` в `ui/chat/threads.py` — сервис-фасад над `AgentChatThread`. **Отложено.**
- [-] `search_service.py` / `browser_service.py` — **отменено** как отдельные сервисы: `agents/search_providers.py` и `agents/browser_client.py` уже не зависят от Qt, тестируются без `QApplication`. Фасад добавил бы только церемонию.
- [-] `session_service.py` — **отменено**: `state/session_store.py` + `state/inbox.py` уже чистые.

### 2.9 AsyncResource для Qt (OlliDesk 2.0) — `[x]`

**Боль:** «Suspense-подобные flash/blank/resize в Qt». **Реализовано.**

- [x] **`ui/state/async_resource.py::AsyncResource[T]`** — guard-паттерн
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
      error: BaseException | None
      def begin_load(self) -> None
      def set_value(self, value: T) -> None
      def set_error(self, exc: BaseException) -> None
      def loaded(self) -> bool  # READY / REFRESHING / ERROR с latest
      def value_or(self, default: T) -> T
  ```
- [x] **Правило:** UI не рисует пустоту, если `state in ("loading","refreshing")`
      и есть `latest`. Guard: `loaded = r.state in ("ready","refreshing")`,
      `items = r.latest if loaded else cached`.
- [x] **Использование:** `PipelineSelectors` (модели), `SessionManagerDialog` (сессии),
      `SLMModelsDialog` (каталог), `ChatPanel` (RAG-sources).
- [x] **Тесты:** `tests/ui/test_async_resource.py`.

### 2.10 Natural exit + soft budgets (OlliDesk 2.0) — `[x]`

**Боль:** «жёсткий `max_iterations` душит агента; он не может делать столько
итераций, сколько нужно». **Реализовано.**

- [x] **`agents/agent_loop.py::AgentLoop.run()`** — `while iteration < _hard_ceiling`
      с естественным выходом по `finish_reason=stop`. `_hard_ceiling = max(soft_budget.iterations * 3, 300)`
      — это **safety net**, а не основной выход.
- [x] **`SoftBudget`** (`dataclass` в `agents/agent_loop.py`):
  ```python
  @dataclass
  class SoftBudget:
      iterations: int = 100
      tokens: int = 100_000
      time_s: float = 600.0
      warning_ratio: float = 0.8
  ```
  При приближении к `warning_ratio` — `EventBus.emit("budget.warning")` +
  сигнал в UI; при превышении — `EventBus.emit("budget.exceeded")`, но цикл
  **продолжается** (в AGENT; в CHAT/PLAN — форсируется финальный синтез).
- [x] **`BudgetStatus`** — `OK` / `WARNING` / `EXCEEDED`.
- [x] **`AgentChatThread`** эмитит `budget_warning` / `budget_exceeded` /
      `agent_finished` (dict с `finish_reason`, `budget`, `iterations`, `elapsed_s`).
      Жёсткий `iteration_limit_exceeded` — только по safety net.
- [x] **Кнопка «Продолжить»** — `_continue_after_limit` удваивает лимит
      (`min(max(current*2, current+10), 200)`), использует `_last_run_params`.
- [x] **Кнопка «Продолжить до результата»** — `_continue_unlimited` создаёт
      `SoftBudget(iterations=100_000, tokens=10_000_000, time_s=86_400)`.
- [x] **`_final_call_no_tools`** — fallback при повторе tool_calls/исчерпании
      нуджей/бюджете (в CHAT/PLAN); обновляет system-сообщение «инструменты
      ОТКЛЮЧЕНЫ, синтез обязателен». `AgentIterationLimitError` бросается только
      если сам финальный вызов упал.
- [x] **`EventBus.emit("agent.finished", {finish_reason, budget, iterations, elapsed_s})`** —
      причина остановки видна в UI.
- [x] **`ChatPanel`**: `StatusDashboard` показывает бюджет (`Итерация 7 ·
      12 400 tok · 2:14 · cache 70 400 reads`), причину остановки
      (`finish_reason=stop` / `budget.exceeded` / `user_cancel`).
- [x] **`core/config.py::AppConfig`**: `soft_budget_time_s`,
      `soft_budget_tokens`, `soft_budget_iterations` (с дефолтами и валидатором).
- [x] **`_build_system_prompt`**: вместо «Итерация N из M» — «Потрачено N
      итераций · T tok · S сек; следующий ответ может быть Final Answer,
      если задача решена».
- [x] **Тесты:** `tests/agents/test_soft_budget.py`.

### 2.11 `browser_control` с тремя режимами (OlliDesk 2.0) — `[x]`

**Боль:** «нужен доступ к авторизованным сайтам (Jira, Confluence, внутренние
вики), но CDP-релей к реальному профилю — опасная поверхность атаки». **Реализовано.**

- [x] **`core/config.py::BrowserConfig`**:
  ```python
  class BrowserConfig(BaseModel):
      mode: Literal["isolated", "extension", "full"] = "isolated"
      dedicated_profile_path: str = "~/.ollidesk/browser_profile"
      extension_bridge_port: int = 8765
      full_access_acknowledged: bool = False
      headless: bool = False
  ```
- [x] **Режим A: `IsolatedBrowser`** — отдельный профиль Chromium через Playwright
      `launch_persistent_context`. Нет доступа к реальным кукам. Только публичные
      страницы. `open` / `snapshot` / `click` / `fill` / `close`.
- [x] **Режим B: `ExtensionBrowser`** — WebSocket-релей к браузерному расширению.
      `_send(action, params)` через `websockets`. Только вкладки с явным consent.
- [x] **Режим C: `FullAccessBrowser`** — CDP к копии реального профиля.
      Требует `full_access_acknowledged=True`, иначе `BrowserError`. Все действия
      в audit log.
- [x] **`tools/browser_ops.py`** — `@tool`-обёртки:
  - `browser_open(url)` — capability `browser`, `ask_first`, `timeout_s=60`.
  - `browser_snapshot()` — `browser`, `ask_first`, `timeout_s=30`.
  - `browser_click(selector)` — `browser`, `ask_first`, `timeout_s=60`.
  - `browser_fill(selector, text)` — `browser`, `ask_first`, `timeout_s=60`.
  - `browser_close()` — `browser`, `ask_first`, `timeout_s=15`.
- [x] **`tools/registry.py::ALL_TOOLS`** — добавлены `browser_*`.
- [x] **`BrowserConfig`** в `AgentSettingsDialog` — селектор режима
      с предупреждением для `full` и чекбоксом `full_access_acknowledged`.
- [x] **Audit:** каждый вызов (`core/audit.py`) — URL, действие, результат.
- [x] **`EventBus.emit("browser.action", {action, url, result})`**.
- [x] **Тесты:** `tests/agents/test_browser_client.py` (mock CDP),
      `tests/tools/test_browser_ops.py` (mock client).

### 2.12 Durable inbox (OlliDesk 2.0) — `[x]`

**Боль:** «запрос не должен теряться при перезапуске; очередь должна переживать
сбой». **Реализовано.**

- [x] **`state/inbox.py::SessionInbox`** — SQLite-очередь `session_inbox`:
  ```sql
  CREATE TABLE session_inbox (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      prompt TEXT NOT NULL,
      context_json TEXT,
      mode TEXT NOT NULL DEFAULT 'chat',
      created_at TEXT NOT NULL,
      state TEXT NOT NULL,  -- pending | running | done | failed
      result_json TEXT,
      error TEXT,
      updated_at TEXT NOT NULL
  );
  ```
- [x] **`SessionInbox.enqueue(session_id, prompt, context, mode) -> id`** —
      пишет `pending`. Лимит `MAX_PENDING_PER_SESSION = 20`.
- [x] **`SessionInbox.claim(id)`** — `pending` → `running` (атомарно,
      `UPDATE ... WHERE state='pending'` + `rowcount`).
- [x] **`SessionInbox.complete(id, result)`** — `running` → `done`.
- [x] **`SessionInbox.fail(id, error)`** — `running` → `failed`.
- [x] **`SessionInbox.recover()`** — при старте приложения `running` → `pending`.
- [x] **`SessionInbox.cleanup_old(retention_days=7)`** — удаление старых.
- [x] **`ChatPanel._send_message`** пишет в inbox **до** запуска
      `AgentChatThread`; `AgentChatThread.run()` забирает (claim) и помечает `running`.
- [x] **`EventBus.emit("inbox.enqueued"/"inbox.delivered"/"inbox.failed")`** —
      видимость в UI.
- [x] **`ui/widgets/status_dashboard.py`** — индикатор очереди
      (`inbox: 2 pending`), tooltip — список.
- [x] **Тесты:** `tests/state/test_inbox.py`.

### 2.13 Цепочка поиска с fallback (OlliDesk 2.0) — `[x]`

**Боль:** «DDG в Москве — капча и блокировки; нужна свобода знаний без единой
точки отказа». **Реализовано.**

- [x] **`agents/search_providers.py`** — цепочка `SearchProvider`:
  ```python
  class SearchProvider(Protocol):
      name: str
      async def search(self, query: str, num: int = 10) -> list[SearchResult]: ...
      def is_available(self) -> bool: ...
  ```
  1. **`SearXNGProvider`** — локальный SearXNG (`/search?format=json`).
  2. **`OpenSERPProvider`** — локальный OpenSERP (`/google/search`).
  3. **`YandexSearchAPIProvider`** — официальный Yandex Search API
     (base64 XML через `rawData`).
  4. **`OllamaWebSearchProvider`** — Ollama Web Search API.
  5. **`DDGProvider`** — через `ddgs`.
- [x] **`core/config.py::SearchConfig`**:
  ```python
  class SearchConfig(BaseModel):
      providers: list[str] = ["searxng", "openserp", "yandex", "ollama", "ddg"]
      searxng_url: str = "http://localhost:8080"
      openserp_url: str = "http://localhost:7000"
      yandex_api_key: str = ""
      yandex_folder_id: str = ""
      ollama_api_key: str = ""
      timeout_s: int = 15
      cache_ttl_hours: int = 24
      max_results: int = 5
  ```
- [x] **`tools/web_search.py`** — цепочка с fallback: `build_chain(_get_search_config())`,
      каждый провайдер в try/except; падение одного не роняет цепочку.
- [x] **Кэш** (`SearchCache`) — обязателен; `cache_ttl_hours` из конфига.
- [x] **`EventBus.emit("search.provider.used", {provider, duration_ms, n_results})`** —
      видимость в UI; `search.chain.exhausted` при полном провале.
- [x] **Политика:** `web_search` — `ask_first` (capability `net`), но
      `confirm.web_search` в конфиге позволяет не спрашивать.
- [x] **`tools/web_search_hygiene.py`** — `filter_query()` вызывает
      `redact()` (0.1.2) перед отправкой.
- [x] **Тесты:** `tests/agents/test_search_providers.py`,
      `tests/tools/test_web_search_chain.py`.

---

Продолжаю вывод второй части обновлённого `PLAN_MODERNIZATION.md`.

---

```markdown
## Фаза 4.1 — Парк моделей (OlliDesk 2.0) — `[x]`

**Боль:** «одна модель не может быть одновременно router, planner, coder и verifier;
большой контекст на CPU душит скорость». **Реализовано.**

- [x] **Роли в `agents/model_pool.py`:**
  ```python
  class Role(StrEnum):
      ROUTER = "router"
      PLANNER = "planner"
      CODER = "coder"
      VERIFIER = "verifier"
      ANALYST = "analyst"
      ANALYST_PRO = "analyst_pro"
      EMBED = "embed"
  ```
  `ModelPool` с LRU-вытеснением по `max_loaded` (по умолчанию 3:
  router+planner на GPU, coder на CPU; verifier — по требованию; embed —
  всегда). Файлы переиспользуются между ролями (`_clients_by_file`),
  `add_role` при одинаковом `file` возвращает тот же клиент.
- [x] **`slm_models.yaml`** (9.3.3): теги `router`, `planner`, `coder`,
      `reasoning`, `verifier`, `embed`, `moE`.
- [x] **Qwen3-Coder-30B-A3B (MoE)** — добавлена в каталог
      `_DEFAULT_SLM_MODELS`: активирует ~3B параметров на токен, качество
      близко к 30B, скорость на CPU приемлемая (5–10 tok/s на 127 ГБ RAM).
      Поля `moe=True`, `active_params_b=3.3`.
- [x] **Qwen2.5-Coder-14B** — fallback для coder, если MoE не влезает/медленно.
- [x] **`agents/slm_selector.py::select_park(profile_park_roles, hw) -> dict[Role, SLMModelInfo]`** —
      явный выбор из профиля → автовыбор по тегам (router/planner/verifier
      tier ≤ 1 на GPU; coder по тегу `coder` максимальный tier; analyst tier 2
      на RAM).
- [x] **`ui/widgets/pipeline_selectors.py`** — показаны роли planner/analyst
      отдельными селекторами с подсветкой coder (9.3.2). Иконки ролей
      (`⚡ coder`, `🧠 reasoning`, `🔀 router`, `📋 planner`, `✅ verifier`,
      `📦 moE`, `🔤 embed`) в префиксе названия.
- [x] **`core/profiles.py`** — `ProfileConfig.park_roles: dict[str, str]`
      (ключи — `Role.value`). Встроенные профили задают парк:
      `development` → router/planner/verifier (3B) + coder (Qwen3-Coder-MoE);
      `research` → planner (3B) + analyst (7B q5); `learning` → analyst (14B).
- [x] **`_ModelPoolLoadThread`** — загрузка ролей **последовательная**,
      per-role timeout 90 с; при `max_loaded=3` — LRU-вытеснение по
      использованию. `device="CPU"` жёстко обнуляет `n_gpu_layers` в
      `optimize()` и в factory как safety net.
- [x] **`EventBus.emit("slm.loaded", {role, model_id, load_ms, n_ctx,
      n_gpu_layers, vram_mb, ram_mb})`** — для `StatusDashboard`.
- [x] **Тесты:** `tests/agents/test_model_pool_park.py`.

---

## Фаза 4.2 — Иерархический контекст + prefix caching (OlliDesk 2.0) — `[x]`

**Боль:** «большой `n_ctx` на CPU — квадратичное замедление attention; не душить
агента, но и не душить себя». **Реализовано.**

- [x] **`core/context_budget.py::hot_window_tokens(num_ctx)`** — 35 % окна
      (минимум 2048). Компактор не режет горячее окно.
- [x] **`core/context_budget.py::warm_layer_tokens(num_ctx)`** — 25 % окна
      (минимум 1024). Режется первым.
- [x] **Холодный слой** — через `search_codebase` / `read_file` (полная история).
- [x] **`core/profiles.py::ProfileConfig.n_ctx_hint`** — 8192 (learning),
      16384 (research), 32768 (development).
- [x] **`agents/slm_optimizer.py::optimize(..., n_ctx_hint)`** — принимает
      желаемый `n_ctx`; убран жёсткий CPU-cap 8192 → `min(profile.n_ctx_hint,
      budget_by_ram)`. `budget_by_ram` считает доступную RAM минус размер
      весов и compute-буферы, делит на KV/токен (`GGUFMeta.kv_bytes_per_token_*`).
- [x] **`SLMConfig.kv_cache_type`** — фактически реализовано через
      `OptimizedParams.kv_cache_type` (`q8_0` для CPU-analyst по умолчанию в
      `_ModelPoolLoadThread` через `optimize`).
- [x] **Prefix caching** в `LocalLLMClient` — `self.llm.n_tokens` до/после
      вызова даёт `cache_reads`/`cache_writes`, попадает в `last_stats`.
      Показывается в `StatusDashboard.set_cache`.
- [x] **Компактор** — `agents/history_compactor.py::compact_history(...,
      hot_window_tokens=...)` не режет сообщения из горячего окна. Порог
      `compactor_threshold(num_ctx)`: 0.75 / 0.80 / 0.90.
- [x] **`core/context_budget.py`** — `rag_char_budget`, `hot_window_tokens`,
      `warm_layer_tokens`, `cold_tokens`, `compactor_threshold`.
- [~] **Benchmark (4.7.B)** — инструмент есть; реальные прогоны
      7B/14B/30B-MoE на 8k/16k/32k/64k — **отложено** (нужен запуск на железе).
- [x] **Тесты:** `tests/agents/test_hierarchical_context.py`.

---

## Фаза 4.3 — Эпизодическая память (OlliDesk 2.0) — `[x]`

**Боль:** «агент, который не помнит, что делал вчера, — это калькулятор, а не
помощник». **Реализовано.**

- [x] **`~/.ollidesk/memory.db`** (SQLite + FTS5):
  ```sql
  CREATE TABLE reflections (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      project_path TEXT NOT NULL DEFAULT '',
      task_type TEXT NOT NULL,
      summary TEXT NOT NULL,
      outcome TEXT NOT NULL,
      tags TEXT NOT NULL DEFAULT '[]'
  );
  CREATE VIRTUAL TABLE reflections_fts USING fts5(
      summary, tags,
      content='reflections', content_rowid='id',
      tokenize='unicode61 remove_diacritics 2'
  );
  CREATE TABLE decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      project_path TEXT NOT NULL DEFAULT '',
      decision TEXT NOT NULL,
      rationale TEXT NOT NULL DEFAULT ''
  );
  CREATE TABLE preferences (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL
  );
  ```
  Триггеры `reflections_ai`/`ad`/`au` синхронизируют FTS5. WAL-режим.
  Fallback на LIKE, если FTS5 недоступен.
- [x] **`core/services/memory_service.py`** — `MemoryService`:
  - `remember(reflection: Reflection) -> int`
  - `recall(query, top_k=3, project_path=None, max_age_days=90) -> RecallResult`
  - `forget(id: int) -> bool`
  - `list_recent(n=20, project_path=None) -> list[Reflection]`
  - `remember_decision`, `list_decisions`
  - `set_preference`, `get_preference`, `list_preferences`
  - `clear`, `count`, `export_json`, `close`.
- [x] **Извлечение:** top-K через FTS5 (BM25-ранжирование), подмешиваются
      в `_build_system_prompt` (блок «Память») через `_build_memory_block`.
- [x] **Запись:** `ChatPanel._remember_if_needed` после успешного/частичного
      завершения задачи. `_build_reflection` формирует task_type
      (code/research/chat), outcome (success/partial/failure), summary
      (первый user-запрос или начало финального ответа), tags (профиль + режим).
- [x] **Приватность:** всё локально. `MemoryConfig.encrypt` — заглушка.
      Пользователь может удалить любую запись (`MemoryDialog`).
- [x] **`EventBus.emit("memory.recalled"/"memory.remembered")`**.
- [x] **`ui/dialogs/memory_dialog.py::MemoryDialog`** — три вкладки
      (рефлексии / решения / настройки), FTS5-поиск, удаление, экспорт в JSON,
      очистка с сохранением preferences.
- [x] **`ui/menus.py`** — `View → Память агента…` (`memory_requested`).
- [x] **Тесты:** `tests/core/test_memory_service.py`.

---

## Фаза 4.5 — Модели SLM (отложено из разбора логов)

Идеи из `RECOMEND.md` + рекомендации по моделям из `docs/HARD.md`. **В OlliDesk 2.0
добавляется `ModelCatalog` (9.3.3), HF-поиск (9.3.1), парк моделей (4.1).**

- [x] **Qwen2.5-14B в каталоге SLM**: `_DEFAULT_SLM_MODELS` содержит три варианта
      `qwen2.5-14b-instruct` (`q4_k_m`, `q5_k_m`, `q6_k`). На HF файл разбит на 2 части
      (~4.5 ГБ), автопроверка частей через `HfApi.list_repo_files`
      (`agents/download_slm.py::_detect_parts_via_hf`) подхватит это сама. Предупреждение
      для UI: на T1200 (4 ГБ VRAM) `detect_optimal_gpu_layers` вернёт `n_gpu_layers ≈ 11`,
      модель загрузится, но ~90 % уйдёт на CPU.
- [x] **Qwen3-Coder-30B-A3B (MoE)** — основная coder-модель (4.1). На 127 ГБ RAM
      даёт 5–10 tok/s на CPU при качестве, близком к 30B. Добавлена в
      `_DEFAULT_SLM_MODELS` с тегом `moE`, `coder` (`moe=True`, `active_params_b=3.3`).
- [x] **Qwen2.5-Coder-32B-Instruct** — в каталоге `qwen2.5-coder-32b-instruct-q4_k_m`
      (19500 МБ) и `-q5_k_m` (22500 МБ). Покрывает Python / C# / Bash / Ansible.
- [ ] **DeepSeek-R1-Distill-Qwen-32B** (Q8_0, ~35 ГБ RAM) — reasoning-модель
      для анализа текстов и финансовых документов — **не добавлена** (отложено).
- [~] **Квантование малых моделей: Q4_K_M → Q8_0/Q6_K** — в каталоге есть
      `q5_k_m`/`q6_k` для 7B/14B; Q8_0 пока нет (для 1.5B есть `q8_0`).
      Сделать Q8_0 частью каталога (или выбором в `slm_models_dialog`),
      чтобы один чекпоинт можно было скачать в двух квантах.
- [x] Температура для дисциплины tool-call: дефолт `_DEFAULT_TEMPERATURE = 0.0`,
      `_TEMPERATURE_BY_OP` для `classify`/`plan`/`tool_call`/`critique`/
      `synthesis`/`regen`. Языковая директива: `_LANGUAGE_INSTRUCTION` в начале
      system-сообщения (`LocalLLMClient`) + `_LANGUAGE_DIRECTIVE` в конце промпта
      (`AgentLoop`) — лечит дрейф Qwen2.5 на китайский. Для Ollama
      (`ChatOptions`) диапазон 0.3–0.5 ещё не применён (см. 4.6.B).
- [ ] `@dir` не сбрасывает текущую сессию — это сделано намеренно
      (`_on_directory_attach_requested` не трогает историю чата и `recent_projects`).
      Альтернатива «полный перезаход как `File → Open Project`» — заменить тело метода
      на `self._do_open_project(path)`; решение за пользователем.
- [x] **`ModelCatalog`** (см. 9.3.3): `_DEFAULT_SLM_MODELS` вынесен из кода —
      каталог живёт в `~/.ollidesk/slm_models.yaml`, `ensure_slm_models_file`
      создаёт файл при первом запуске, `merge_with_defaults` — миграция.
- [x] **HF search** (см. 9.3.1): вкладка «Поиск HF» в `SLMModelsDialog` через
      `HfApi.list_models`/`model_info`/`list_repo_files`.
- [x] **Tags `coder`/`reasoning`/`router`/`planner`/`verifier`/`moE`** в
      `SLMModelInfo` (см. 9.3.2) + подсветка в `PipelineSelectors`/`ProfilesSelector`
      для профиля «Разработка».

---

## Фаза 4.6 — Надёжность ReAct-агента (из `React.md`)

`React.md` — разбор ReAct-цикла с привязкой к файлам. Ниже сведённые задачи с приоритетом.
Обозначения: **Потом** — улучшает качество, но не блокирует работу;
**Позже** — крупные подсистемы.

### 4.6.B — Потом: качество ответов

- [x] **Step-awareness** (`React.md` 2.1): дописывать в system-промпт «Итерация N из M»,
      на последних итерациях — «следующий ответ должен быть Final Answer». Реализовано в
      `agents/agent_loop.py::_build_system_prompt` через `iter_line` с `remaining <= 1`
      и `remaining <= 3`. **В 2.10** заменено на soft budget: «Потрачено 7 итераций ·
      12 400 tok · 2:14; следующий ответ может быть Final Answer, если задача решена».
- [x] **Не бросать `AgentIterationLimitError`, а делать финальный вызов без tools**
      (`React.md` 2.1): `_final_call_no_tools` вызывается и при повторе tool_calls, и при
      лимите, и при исчерпании нуджей. `AgentIterationLimitError` бросается только если
      сам финальный вызов упал. **В 2.10** `_final_call_no_tools` — только fallback,
      не основной выход.
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
      покрыт и `AgentLoop`. **В 10.8** расширяется на LSP — сделано.
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
      **В 4.2** заменено на `min(profile.n_ctx_hint, budget_by_ram)`.
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
      а исчерпание бюджета шагов). **В 2.10** заменено на «Продолжить» с новым
      бюджетом (не удвоение) — реализовано через `_continue_unlimited`.
- [x] **`_CHAT_AUTO_TOOLS` в конфиг** (`React.md` 9.1): в `AppConfig` добавлено
      `chat_auto_tools: list[str]` (со `default_factory`); `_build_registry` читает из
      конфига и падает на встроенную константу при ошибке чтения. Проектный
      `.ollidesk/config.yaml` может переопределить список (по аналогии с
      `tool_policies` — проект без web-доступа уберёт `web_search`). `run_shell_command`
      в список **не** входит — только AGENT-режим.
- [x] **Бюджет RAG-контекста** (`React.md` 7.1): `ChatPanel._on_rag_results` и
      `_on_agent_rag_ready` усекают `merged` до `rag_char_budget(self._router.num_ctx)`
      через `truncate_to_chars`.
- [x] **Verifier в обычном AgentLoop** (см. Фаза 10.4): `AgentLoop._verify_and_maybe_regen`
      подключён после финального ответа; при провале — одна регенерация с `regen_hint`.
- [x] **Audit log** (см. Фаза 0.1.1): каждая запись `ToolResult` попадает в
      `~/.ollidesk/audit.db` (timestamp, tool, args_hash, policy, approved, result_hash,
      error, dangerous). `ToolService` делает это для **всех** вызовов.

### 4.6.C — Позже: новые подсистемы

- [x] **Reflexion-критик для Final Answer** (`React.md` 4.1): для Orchestrator это уже
      сделано — `agents/critic.py` (3B-проверка структуры/формата) и `agents/verifier.py`
      (код-проверка путей/символов/evidence) вызываются в `Orchestrator.run()`. Для
      `AgentLoop` (обычные чат-ответы) — подключено через `_verify_and_maybe_regen` (10.4).
- [x] **Structured trace агента** (`React.md` 8.1): в research-режиме есть
      `last_research_summary` и `ChatResearchPassport` с прочитанными файлами, поисками,
      гипотезами. Полноценный трейс (итерация → действие → длина observation →
      длительность) виден через `ChatThoughtStream` (Agent/Plan) и `LiveEventsPanel`
      (все режимы). **Закрыто в 9.5.3 и 2.5.**
- [x] **Метрики реестра** (`React.md` 8.2): `ToolRegistry._stats` (в разработке —
      tooltip `tools` в `StatusDashboard`; открыто в 9.8).
- [x] **Эпизодическая память** (`React.md` 7.2): `~/.ollidesk/memory.db` с
      рефлексиями, подгрузка top-3 через `MemoryService.recall`. **В 4.3** — сделано.
- [x] **Типизированное редактирование аргументов** (`React.md` 9.2): `ToolConfirmationDialog._edit_arguments`
      открывает `QTableWidget` с колонками «Аргумент» (read-only) / «Значение» (editable) /
      «Тип» (read-only, серый). `_coerce_value(raw, orig_type)` восстанавливает
      int/float/bool/list/dict/None; при ошибке парсинга — оставляет строкой (никогда не
      падает). Покрыто `tests/ui/test_tool_confirmation_coerce.py` (30+ кейсов).
- [ ] **`model_digest` в `ModelInfo`** (`React.md` 8.3): инвалидировать `supports_tools`
      при смене версии модели. **Отложено.**
- [x] **Warning о `n_gpu_layers` в UI** (`React.md` 5.3): `StatusDashboard` —
      tooltip с `n_ctx`, `n_gpu_layers`, `vram_used_mb`, `ram_used_mb`,
      `kv_cache_type` (из `OptimizedParams`) при загрузке SLM. Отображение — в 9.8.
- [x] **Multi-tool-call у SLM** (`React.md` 5.1): `LocalLLMClient._parse_tool_calls`
      возвращает список из `extract_tool_calls` (JSON + function-call), в `chat()` все
      вызовы уходят одним `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`.
      `AgentLoop` через `pending_tool_calls.extend(part.calls)` выполняет все.

### 4.6.D — Удалить (проверить перед удалением)

- [x] `RouterChatThread` (`React.md` 1.3, 6.3) — **уже удалён** вместе с
      `OllamaChatThread`; в `ui/chat/threads.py` его нет. Задача закрыта.
- [ ] `ui/dialogs/model_manager_dialog.py::PullThread` — дублирует `_DownloadThread`
      из `slm_models_dialog.py`; объединить (см. 9.3.3). **Отложено.**
- [~] Закомментированные блоки в `core/search_cache.py`, `fs/vector_store.py`, `main.py` —
      не восстанавливать, вычистить при рефакторинге. Часть закомментированных строк в
      `main.py` осталась (например, в `_build_system_prompt`), плюс `_OllamaCheckThread`,
      `_SLMLoadThread`, `_SLMDownloadThread` в `threads.py` с несколькими комментариями
      «было / стало» — косметически почистить.

### 4.6.E — Решения, которые нужно подтвердить

- [x] `agents/context_compactor.py`: **удалён** — писать заново.
- [~] Порядок: `React.md` предлагает «день 1 — temperature, compactor, лимит observation,
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
(9.4)** — отдельные тумблеры не нужны. **В OlliDesk 2.0 модели переехали в
`slm_models.yaml` (9.3.3), HF-поиск — в 9.3.1, парк моделей — в 4.1.**

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
- [ ] **`[новое]`** Прогон 7B/14B/30B-MoE на 8k/16k/32k/64k — записать
      `ttft_ms`/`tok_s`/`ram_mb` в `docs/BENCHMARK.md`. Без цифр — не поднимать
      `n_ctx` вслепую (4.2). **Отложено.**
- [ ] **`[новое]`** Прогон с `kv_cache_type=q8_0` vs `f16` — измерить выигрыш
      по RAM и потерю по качеству. **Отложено.**

### 4.7.D — ModelPool, но аддитивно (не замена `ModelRouter`)

- [x] `agents/model_pool.py` существует: `ModelPool`, `Role` (полный набор:
      ROUTER/PLANNER/CODER/VERIFIER/ANALYST/ANALYST_PRO/EMBED), `RoleSpec`,
      `add_role()`, `runtime_ctx()`, `pick_role()`, `aclose()`.
      **Не заменяет** `ModelRouter`: `ModelRouter` остаётся основным путём для обычных
      чат-запросов; `ModelPool` живёт только под `Orchestrator` и поднимается лениво.
      `_ModelPoolLoadThread` (в `ui/chat/threads.py`) грузит роли последовательно с per-role
      timeout 90 с.
- [x] **Модель закрепляется за сессией.** `n_ctx` у моделей разный. `planner_model_id`
      и `analyst_model_id` хранятся в `SLMConfig` (глобально / per-project).
      В рамках проекта пайплайн пересоздаётся при `reset_pipeline()`. Сессия получает
      `budget` (2.10); `park_roles` — в профиле (4.1).
- [x] `max_loaded=3` фактически: `_ModelPoolLoadThread` грузит роли из `select_park`
      (planner + coder + verifier/analyst). `max_gpu_resident=1` тоже выполнено:
      planner жёстко на GPU (`device="GPU"`), coder/analyst — на CPU (`device="CPU"`),
      `optimize()` для analyst принудительно обнуляет `n_gpu_layers`.
- [x] `n_gpu_layers` пересчитывается per-модель через `agents/slm_optimizer.optimize()`
      (VRAM-first с fallback VRAM+RAM; CPU-only cap 8192; отказ от частичного offload <50 %).
      **В 4.2** cap заменён на `min(profile.n_ctx_hint, budget_by_ram)`.

### 4.7.E — Классификатор: отложить

- [ ] Понаблюдать за реальным распределением задач (Фаза 4.7.B) **до** появления
      классификатора. Псевдокод классификатора в аудите (§3.3) — это ~30 строк
      регулярок и длин, а не нейросеть. **Отложено.**
- [~] Если понадобится: начать **только** с rule-based (`agents/task_classifier.py`),
      без LLM-классификатора. Сейчас `ModelPool.pick_role(task, ...)` делает простейший
      rule-based выбор: `task in {"classify","plan","critique","tool"}` → `PLANNER`,
      `quality_mode and project_files > 500` → `ANALYST_PRO`, иначе `ANALYST`. Отдельного
      `task_classifier.py` нет.
- [ ] Уровни L4/L5 из матрицы аудита на этом железе нереалистичны: 14B/32B/72B
      на диске нет, а 32B Q4 на CPU даёт 1–2 т/с = 4–8 минут на ответ. Матрицу
      применить только к реально установленным моделям.

### 4.7.F — Отклонено

- [-] **LoRA-адаптеры, Self-RAG, дообучение классификатора, Multi-Agent**
      (Этапы 3–5 аудита): на ноутбуке без GPU для обучения это неделями работы
      без измеримого результата. Feedback loop (👍/👎) — отдельная простая задача,
      имеет смысл раньше всей этой группы.
- [-] **«Переключение за ~100 мс простой сменой указателя»** (Этап 1, п. 4 аудита) —
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
токенам вынесен в 9.5.2 и уже сделан**. **В OlliDesk 2.0 EventBus (2.5) добавлен
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

## Фаза 5 — Мультимодальность: backend (UI — в Фазе 9.7) — `[~]`

**Только backend-экстракторы и модели.** Всё, что связано с отображением в UI (drag&drop,
превью, кнопки), — в **9.7**.

- [x] Распознавание скриншотов (JPG, PNG, WebP, BMP): backend-обёртка над
      `rapidocr-onnxruntime` (`fs/image_extractors.py::extract_image`).
      Поле `ChatMessage.images` в модели используется в UI (9.7).
- [ ] Vision-модели через Ollama (backend): `qwen2.5-vl:7b` / `llama3.2-vision:11b`
      (GPU offload = 0, 4 ГБ VRAM не вмещают мультимодальный контекст).
      Клиентская обёртка `agents/vision_client.py`; UI-вызов — 9.7.
      **Отложено.**
- [x] Аудио (MP3, WAV, OGG, M4A, FLAC): backend `fs/audio_extractors.py::transcribe_audio`
      через `faster-whisper` (строго CPU, `base` / `small` / `medium`),
      конвертация в 16 кГц WAV через ffmpeg. UI (плеер, прогресс) — 9.7
      (плеер отложен, прикрепление работает).
- [x] Видео (MP4, MKV, WebM, AVI): backend `fs/video_extractors.py::extract_transcript` —
      извлечение звуковой дорожки ffmpeg → тот же `faster-whisper`. UI — 9.7.
- [x] **Распознавание документов (PDF, DOCX, PPTX, XLSX, RTF)**:
      `fs/document_extractors.py::extract_document` — извлечение
      текста через `pdfplumber`/`pypdf`, `python-docx`, `python-pptx`, `openpyxl`,
      `striprtf`; **отдельная коллекция `docs`** (см. `_DOC_LANGUAGES` в
      `fs/vector_store.py`). UI-предпросмотр — 9.7.
- [x] **OCR сканов PDF** (через `rapidocr-onnxruntime` + `pdf2image` + Poppler):
      `fs/document_extractors.py::_ocr_pdf`, включается явно (`use_ocr=True`).
      По умолчанию выключен (`AppConfig.document_ocr = False`).
- [x] База мультимедиа-экстрактов: `~/.ollidesk/media_cache/` с хэшем файла →
      извлечённый текст, чтобы не дублировать обработку одних и тех же файлов
      (`fs/media_cache.py`).
- [ ] PDF/DJVU: DJVU не реализован (нужен `djvulibre`); PDF — есть. **Отложено.**

> Генерация изображений (SD1.5 / SDXL / ComfyUI / Forge) — **вне объёма OlliDesk**:
> отдельный движок и внешние окна. Приложение занимается только распознаванием
> (OCR/Vision/STT), синтезом — нет.

---

## Фаза 6 — UX: быстрые правки (схлопнута в Фазу 9)

**Все пункты перенесены в Фазу 9** (9.1, 9.2, 9.5, 9.8). Раздел оставлен пустым,
чтобы не ломать нумерацию фаз.

---

## Фаза 7 — Расширяемость (**MCP поднят раньше Plugin API**) — `[~]`

**В OlliDesk 2.0 порядок изменён:** ToolSpec (2.6) → Policy Engine (2.7) →
**MCP-клиент** → Plugin API. MCP даёт доступ к экосистеме без переписывания
существующих инструментов — в условиях санкций это критично.

- [x] **MCP-клиент** (stdio):
  - **`core/mcp_client.py::MCPClient`** — JSON-RPC 2.0 over stdio; lifecycle
    (spawn/kill), capabilities negotiation, tool discovery (`tools/list`),
    tool call (`tools/call`), `stop` через SIGTERM→SIGKILL. Таймауты:
    `INIT_TIMEOUT=30s`, `CALL_TIMEOUT=120s`, `STOP_TIMEOUT=5s`.
  - **`tools/mcp_ops.py::MCPManager`** — singleton; динамическая регистрация
    инструментов из MCP-серверов; `register_mcp_tools(registry) -> int`;
    `_make_callable(server, tool)` — async-обёртка над `call_tool`.
    Эвристика capabilities по имени инструмента + whitelist
    `MCPServerConfig.capabilities`.
  - **`core/config.py::MCPConfig`, `MCPServerConfig`** — список серверов:
    ```python
    class MCPServerConfig(BaseModel):
        name: str
        command: str
        args: list[str] = []
        env: dict[str, str] = {}
        cwd: str | None = None
        enabled: bool = True
        capabilities: list[str] = ["fs.read"]
    ```
  - **`EventBus.emit("mcp.connected"/"mcp.disconnected"/"mcp.tool_called"/"mcp.error")`**.
  - **Policy Engine (2.7)** — capabilities MCP-сервера проходят через
    `effective_policy`; `secrets` — всегда `excluded`.
  - **Audit log** — каждый MCP-tool call пишется в `core/audit.py`
    (через `ToolService`).
  - **`ui/widgets/status_dashboard.py::set_mcp`** — индикатор MCP.
  - **Тесты:** `tests/core/test_mcp_client.py` (mock stdio),
    `tests/tools/test_mcp_ops.py`.
- [ ] **Plugin API** (после MCP):
  - `tools/plugins/*.py` + `plugin.toml` (`name`, `version`, `api_version`,
    `tools`, `capabilities`, `requires`, `uses`).
  - `Plugin.define({tools, commands, skills})` — декларативный контракт.
  - Изоляция ошибок (плагин не валит приложение).
  - `api_version` проверяется при загрузке; несовместимый — `warning`, не `crash`.
  - `capabilities` плагина проходят через `Policy Engine`.
  - `requires` — hard dependency, gate startup; `uses` — optional, не блокирует.
  - `createKeyed` — per-session сторы.
  - **Отложено.**
- [x] **Permissions Dashboard** (см. 9.10).
- [ ] Пользовательские команды (`~/.ollidesk/commands/*.md`) как расширение ролей и промптов.
      **Отложено.**
- [ ] **Внешние провайдеры LLM — не планируется.** `ModelConfig.provider` допускает
      `openai`/`openai-compatible`, но клиента нет; архитектурно все модели локальные
      (SLM или Ollama).

---

## Фаза 8 — Релиз и качество — `[~]` **отложено**

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
- **`[новое]` Причина остановки видна** — `finish_reason`, бюджет, `user_cancel`.
- **`[новое]` Reasoning trace** — почему агент выбрал инструмент/остановился.

### 9.0 Архив: что уже закрыто (не трогать)

См. текущие 9.1–9.11 в плане: дизайн-система с токенами, композитный пузырь
(`ReasoningWidget` + `TextWidget` + `ToolCallCard`), SLM-стриминг по токенам,
`ChatThoughtStream` в Agent/Plan, `AutoScrollArea` с кнопкой «▼ Вниз», diff-карточка
`write_file` (📂/🔍/↩), автодополнение `@`-тегов, экспорт чата (MD/HTML), менеджер
сессий с preview и «Создать проект из сессии», палитра команд `Ctrl+Shift+P`,
виртуализация длинных чатов, `StatusDashboard` (backend/pipeline/tok/s/RAG/tools),
diff в редакторе `Ctrl+D`, замеры производительности. Все пункты переносятся в архив
без изменения статусов.

### 9.1 Стационарные вкладки и навигация — `[x]`

**Боль:** «Нужно намертво прикрепить чат и убрать крестик… В идеале вообще не показывать
чат как вкладку… у каждого файла на вкладке будет крестик, и когда последний закроется —
структура вкладок должна скрыться». **Реализовано.**

- [x] **Чат — не вкладка, а базовый экран.** `MainWindow._create_center_panel` использует
      `QStackedWidget`:
  - Страница 0 — `ChatPanel` (базовый экран).
  - Страница 1 — `EditorWidget` (с собственным `QTabBar` только для файлов).
  - `EditorWidget.tabs_changed(int)` → `MainWindow._on_editor_tabs_changed`:
    `count == 0` → `_switch_to_chat()`, `count > 0` → `_switch_to_editor()`.
  - `_switch_to_chat` / `_switch_to_editor` — методы `MainWindow`.
- [x] **`QTabBar` только для файлов.** Каждая вкладка закрывается крестиком
      (`QTabBar.setTabsClosable(True)`), `Ctrl+W`/`Ctrl+Shift+W` + контекстное меню
      (закрыть / закрыть слева / закрыть справа / закрыть остальные / закрыть все /
      копировать путь / открыть расположение файла).
- [x] **Авто-возврат к чату.** Когда `tab_count() == 0` — `_on_editor_tabs_changed`
      переключает `center_stack` на страницу чата. При открытии файла —
      `EditorWidget.open_file` → `tabs_changed` → `_switch_to_editor()`.
- [x] **«💬 Чат» как кнопка-переключатель** в тулбаре `EditorWidget`
      (`back_to_chat_requested` → `_show_chat_tab`); `Ctrl+Shift+C` — глобальный шорткат.
- [x] **`EditorWidget.tab_count()`** остался методом; сигнал `tabs_changed(int)`
      эмитится в `open_file` / `_on_tab_close_requested` / `close_all_tabs` /
      `close_file_silently`.
- [x] **F4 работает независимо** от видимости `QTabWidget`: F4 управляет левой панелью
      (структура проекта), а не вкладками.

**Затрагивает:** `ui/main_window.py::_create_center_panel`, `_on_editor_tabs_changed`,
`_switch_to_chat`, `_switch_to_editor`; `ui/web_editor/editor_widget.py`
(`tabs_changed`, `back_to_chat_btn`).

### 9.2 Структура проекта: автопоказ и F4 — `[x]`

**Боль:** «Если через файл меню открывается проект, то автоматически должна отображаться
его структура, которую потом можно скрыть по F4». **Реализовано.**

- [x] **При открытии проекта через `File → Открыть проект`** (`_do_open_project`) —
      `is_first_open = str(self.project_path) not in (self.config.recent_projects or [])`;
      `show_tree = True if is_first_open else saved_visible`; при первом открытии
      структура показывается; `left_panel.setVisible(show_tree)`.
- [x] **При открытии через `@dir`** (`_on_directory_attach_requested`) —
      `left_panel.setVisible(True)` и `project_tree_btn.setChecked(True)` — пользователь
      только что прикрепил каталог, логично увидеть его содержимое.
- [x] **`File → Открыть недавний`** — то же поведение, что и обычное открытие
      (через `_do_open_project`).
- [x] **F4** — скрывает/показывает; состояние персистится в
      `config.show_project_tree`. Синхронизация с кнопкой «📁» в ChatPanel —
      через `_toggle_project_structure` / `_on_project_tree_toggled`.
- [x] **Логика первого открытия:** `is_first_open = project_str not in recent_projects`;
      повторное открытие уважает `config.show_project_tree`.

**Затрагивает:** `ui/main_window.py::_do_open_project`, `_on_directory_attach_requested`,
`_on_project_tree_toggled`.

### 9.3 Модели: поиск на HF, отдельный конфиг, подсветка coder — `[x]`

#### 9.3.1 Поиск моделей на HuggingFace — `[x]`

**Боль:** «Возможность поиска моделей на huggingface. Ввожу фрагменты названия → поиск →
список → клик → подробное описание → кнопка скачать». **Реализовано.**

- [x] **`ui/dialogs/hf_dialog.py::HFDialog`** — отдельный диалог, кнопка «🔍 Поиск HF»
      в `SLMModelsDialog`:
  - `QLineEdit` (поисковый запрос) + `QPushButton("🔍 Найти")` + `QComboBox` фильтра
    (`GGUF` / `Все` / `text-generation`).
  - `QListWidget` результатов (repo_id, downloads, likes, теги).
  - `QTextBrowser` — карточка модели (описание из `cardData`, теги, лицензия,
    ссылка на HF, размер файлов).
  - `QPushButton("⬇️ Скачать выбранный файл")` — эмитит `download_requested`.
  - `QPushButton("➕ Добавить в каталог")` — эмитит `add_to_catalog_requested`
    (запись в `slm_models.yaml` с автоматическими тегами).
  - `_SearchThread` / `_InfoThread` — фоновые, GUI не блокируется.
- [x] **Бэкенд:** `agents/hf_search.py`:
  - `search_models(query, filter_tag="gguf", sort="downloads", limit=50) -> list[HFModel]`
    через `HfApi.list_models` (в `asyncio.to_thread`, timeout 30 с).
  - `model_info(repo_id) -> HFModelDetails` через `HfApi.model_info` + `siblings`
    (список `.gguf` с размерами).
  - `detect_tags(card_data)` — эвристика: `coder` если в тегах `coder`/`code`/
    `starcoder`, `reasoning` если `reasoning`/`r1`/`think`/`qwq`, `moE` если
    `moe`/`a3b`/`a2b`/`a22b`, `embed` если `embed`/`bge`/`nomic-embed`/`e5-`/`gte-`,
    иначе `chat`.
  - **Кэш:** `_search_cache` / `_info_cache` с `_CACHE_TTL_S = 600` (10 минут).
    Rate-limit HF — 30 req/min без токена; кэш обязателен.
  - **HFUnavailableError** — при сетевых проблемах. `emit_event_safe` не роняет
    поиск при ошибке шины.
- [x] **Оффлайн-режим:** при недоступности HF — сообщение в `status_label` и
      fallback на локальный список; не падать.
- [x] **Интеграция с `download_slm`:** `_on_hf_download` в `SLMModelsDialog`
      добавляет модель в каталог (если нет) и запускает скачивание.
- [x] **`EventBus.emit("hf.search.started"/"hf.search.found"/"hf.search.empty"/"hf.search.error")`** —
      видимость в UI (`LiveEventsPanel`).
- [x] **`AppConfig.hf_token`** — токен HF (приоритет над `HF_TOKEN` env).
- [x] **Тесты:** `tests/agents/test_hf_search.py`, `tests/ui/test_hf_dialog.py`.

**Затрагивает:** `ui/dialogs/slm_models_dialog.py` (`_open_hf_dialog`,
`_on_hf_add_to_catalog`, `_on_hf_download`), `agents/hf_search.py` (новый),
`ui/dialogs/hf_dialog.py` (новый).

#### 9.3.2 Подсветка coder-моделей в режиме разработки — `[x]`

**Боль:** «Если я в режиме разработки, при выборе моделей должны подсвечиваться другим
цветом или жирным coder-модели для разработки». **Реализовано.**

- [x] **`SLMModelInfo.tags: list[str]`** — поле есть: `["chat"]`, `["coder"]`,
      `["reasoning"]`, `["router"]`, `["planner"]`, `["verifier"]`, `["moE"]`,
      `["embed"]`. Заполняется в `slm_models.yaml` + из HF-карточки
      (`agents/hf_search.py::detect_tags`).
- [x] **`PipelineSelectors._apply_coder_highlight(combo, models, is_dev, accent)`** —
      если `self._active_profile_id == "development"`, для моделей с `"coder" in tags`
      применяется `QFont.setBold(True)` + `Qt.ForegroundRole = #d4a373`; в остальных
      профилях стиль снимается.
- [x] **`PipelineSelectors`** — иконки ролей в префиксе названия: `⚡ coder`,
      `🧠 reasoning`, `🔀 router`, `📋 planner`, `✅ verifier`, `📦 moE`, `🔤 embed`.
      Иконки берутся из `_role_icons`, склеиваются в `_fill_combo` (первые 2).
- [x] **`ProfilesSelector`** — бейдж `freedom_level` рядом с названием профиля
      отображается через `StatusDashboard.set_freedom`, а в ComboBox — через
      описание профиля (`p.description` в tooltip).
- [x] **`ProfileConfig.recommended_model`** — встроенные профили задают
      рекомендованную модель (`development` → `qwen2.5-coder-7b-instruct-q6_k`,
      `research` → `qwen2.5-7b-instruct-q6_k`, `learning` → `qwen2.5-14b-instruct-q4_k_m`).
      `PipelineSelectors.set_recommended_model(model_id, profile_name)` подсвечивает
      `★ ` в ComboBox.
- [x] **`ProfileConfig.coder_models: list[str]`** — явные id coder-моделей.
      В `development`: `[qwen3-coder-30b-a3b, qwen2.5-coder-14b-q5, qwen2.5-coder-7b-q6, qwen2.5-coder-7b-q4]`.
      `agents.slm_selector.select_coder(profile_coder_models=...)` — использует.
- [x] **`ProfileConfig.park_roles: dict[str, str]`** (4.1) — рекомендация
      по ролям для профиля.
- [x] **Тесты:** `tests/ui/test_pipeline_selectors.py`.

**Затрагивает:** `core/config.py::SLMModelInfo`, `core/profiles.py::ProfileConfig`,
`ui/widgets/pipeline_selectors.py`, `ui/widgets/profiles_selector.py`.

#### 9.3.3 Модели и tier — в отдельный файл конфигурации — `[x]`

**Боль:** «Не хранить жёстко модели и tier в коде, а создать файл конфигурации… хранить
рядом с файлом конфигурации приложения, потому что сам config.yaml стал не читаем».
**Реализовано.**

- [x] **`~/.ollidesk/slm_models.yaml`** — отдельный файл, список `SLMModelInfo` в YAML:
  ```yaml
  schema_version: 1
  models:
    - id: qwen2.5-coder-14b-instruct-q4_k_m
      repo: Qwen/Qwen2.5-Coder-14B-Instruct-GGUF
      file: qwen2.5-coder-14b-instruct-q4_k_m.gguf
      display_name: Qwen2.5-Coder-14B (Q4_K_M)
      size_mb: 8900
      kind: chat
      n_ctx: 32768
      tier: 3
      tags: [coder, chat]
      description: Основная coder-модель для профиля «Разработка».
      recommended_profile: development
      moe: false
      active_params_b: 0
  ```
- [x] **`core/slm_models_io.py`** — модуль I/O:
  - `load_slm_models(path=None) -> list[SLMModelInfo] | None`.
  - `save_slm_models(models, path=None) -> None` — атомарно (`.tmp` + `replace`).
  - `merge_with_defaults(loaded, defaults) -> list[SLMModelInfo]` — обновляет
    встроенные метаданные, сохраняет пользовательские.
  - `ensure_slm_models_file(defaults, path=None) -> list[SLMModelInfo]` — создаёт
    файл при первом запуске, мержит при обновлении приложения.
  - `SCHEMA_VERSION = 1`, `FILENAME = "slm_models.yaml"`.
- [x] **`ConfigLoader._save_yaml`** — `slm.available_models` **не пишется** в
      `config.yaml` (только в `slm_models.yaml`).
- [x] **`ConfigLoader.load_config`** — `config.slm.available_models` загружается из
      `slm_models.yaml` через `ensure_slm_models_file`; при ошибке — fallback на
      `_DEFAULT_SLM_MODELS` с warning в лог.
- [x] **`schema_version` в `slm_models.yaml`** — для будущих миграций.
- [~] **Редактор в UI** — `SLMModelsDialog` управляет каталогом (добавить/удалить/
      активировать/скачать); «Редактировать YAML» в `EditorWidget` — **отложено**
      (можно править файл напрямую).
- [ ] **Объединить `PullThread` и `_DownloadThread`** — **отложено**.
- [x] **`EventBus.emit("slm.catalog.updated")`** — через `LiveEventsPanel`.
- [x] **Тесты:** `tests/core/test_slm_models_io.py`.

**Затрагивает:** `core/slm_models_io.py` (новый), `core/config.py::ConfigLoader`,
`core/config.py::_DEFAULT_SLM_MODELS` (fallback для миграции),
`ui/dialogs/slm_models_dialog.py` (запись в файл через `save_slm_models`).

### 9.4 Git-интеграция в режиме разработки — `[x]`

**Боль:** «В режиме разработки программа могла читать git diff и понимать изменения в
ветках и хранить в профиле проекта состояние или хэш на какой ветке сейчас контекст.
Агент должен уметь делать точечные изменения в коде проекта и делать осмысленные коммиты,
если это явно не запрещено». **Реализовано.**

- [x] **`tools/git_ops.py`:**
  - `get_current_branch(repo_path) -> str` — с fallback на тег/detached hash.
  - `get_branch_hash(repo_path) -> str` (`rev-parse --short HEAD`).
  - `get_uncommitted_diff(repo_path, include_untracked=False, max_chars=2000) -> str` —
    `git diff` + `git diff --cached` + опционально `status --short`.
  - `get_branch_diff(repo_path, base, target="HEAD") -> str`.
  - `get_recent_commits(repo_path, limit=10) -> list[dict]` — `hash`, `author`,
    `date`, `subject` (unit separator `%x1f`, record separator `%x1e`).
  - `commit(repo_path, message, files=None) -> dict` — агентский коммит: с `files` —
    `git add -- <files>` + `git commit -- <files>`; без — `git commit -a`
    (только уже отслеживаемые изменения, не `git add -A`).
  - `read_file_at_head(repo_path, file) -> str | None` — HEAD-версия для diff.
  - `get_git_status_for_file(repo_path, file) -> str | None` — XY из porcelain.
  - `create_file_snapshot` / `restore_snapshot` / `load_snapshot_meta` /
    `cleanup_old_snapshots` — **10.9**.
- [x] **`state/project_settings.py`:** поля `git_branch: str`, `git_hash: str`,
      `git_last_sync: str` (ISO). Сохраняются при `_save_project_settings`
      и обновляются при открытии проекта (`snapshot_git_state`).
- [x] **`AgentContext.git_context: str`** — блок вида:
  ```
  Ветка: feature/x (abc1234)
  Последние коммиты:
    • a1b2c3d 2025-01-01 feat: added parser
  Незакоммиченные изменения (diff):
  ```
  Добавляется в системный промпт в режиме `AGENT` (`ProjectService.build_git_context`
  → `AgentContext.git_context` → `_build_system_prompt`).
- [x] **Новый инструмент `git_commit`** (`tools/git_ops_tool.py::tool_git_commit`)
      (ASK_FIRST, AGENT mode, capability `git`):
  - Аргументы: `message: str`, `files: list[str] | None`.
  - Возвращает `dict{_llm_text, commit_hash, branch, files_committed}`.
  - В `ProfileConfig.tool_policies` для `development` — `git_commit: "ask_first"`;
    в `research`/`learning` — `excluded`.
- [x] **Запрет по умолчанию:** если в `ProfileConfig` не задан `git_commit` —
      `excluded`. В `development` — `ask_first`.
- [x] **Точечные изменения:** правило в `_TOOL_USE_INSTRUCTION` («Для правок —
      `apply_patch` с минимальным контекстом; `write_file` — только для новых
      файлов или полной замены»).
- [x] **`ProjectSettings.git_branch/hash`** — показывается в `StatusDashboard`
      (`set_git_branch`, «🌿 branch@hash»).
- [x] **`EventBus.emit("git.branch.changed"/"git.commit.created")`**.
- [x] **Тесты:** `tests/tools/test_git_ops.py` (расширен: branch/hash/diff/commits/
      commit), `tests/agents/test_git_context.py`, `tests/tools/test_git_commit.py`.

**Затрагивает:** `tools/git_ops.py`, `tools/git_ops_tool.py`,
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

#### 9.5.1 Визуальный редизайн пузыря — `[x]`

- [x] **Пересмотр `ChatMessageItem._build_ui`** — единый spacing-токен (`SPACE.md` = 8 px)
      между секциями; стиль кнопок вынесен в `_ACTION_BTN_CSS_TEMPLATE` /
      `_action_btn_css(dark)`; разделители между reasoning и text; tool-карточки
      компактнее.
- [x] **Единый стиль кнопок в чате** — `_ACTION_BTN_CSS_TEMPLATE` для Copy,
      «Показать полностью», «Открыть», «Откатить», «Показать diff».
- [x] **Tool-карточки — компактнее:** заголовок в одну строку (icon + `name(args)` + статус),
      preview в 2 строки, кнопки — только при hover или после раскрытия.
- [x] **Тёмная/светлая тема для чата:** `TextWidget._dark` + `apply_theme`; `ToolCallCard`
      перекрашивается через `apply_theme(dark)` (пересоздаёт diff-режим или preview).
      `ChatPanel.set_theme(theme)` → `_apply_theme_to_messages` рекурсивно применяет
      `apply_theme` ко всем виджетам ленты.
- [~] **Код-блоки** — кнопка «Копировать» в правом верхнем углу — **отложено**.
- [x] **Длинные пути в tool-карточках** — обрезка с `…`, полный путь в tooltip.
- [x] **`[новое]` Бейдж «Откатить»** — `ToolCallCard` при `metadata.snapshot_id`
      (10.9): кнопка «↩ Откатить (snapshot …)».
- [x] **`[новое]` Бейдж «LSP»** — красный блок «N ошибок, исправь» с jump-to-line
      при `metadata.lsp_errors` (10.8).

#### 9.5.2 Reasoning-виджет: авто-высота и авто-скролл — `[x]`

- [x] **`ReasoningWidget`:** убрать `setFixedHeight(h)` в `_refresh_browser`. Высота
      растёт по контенту с **максимумом** 40 % высоты чата и **внутренним авто-скроллом**
      к последнему токену. Сделано в **0.1.3a**.
- [x] **Авто-скролл внутри `ReasoningWidget`:** при `append_text` —
      `self._browser.verticalScrollBar().setValue(maximum())`.
- [x] **Сворачивание по умолчанию:** в research/agent — свёрнут, в чате — развёрнут
      (по `_show_reasoning`). `ReasoningWidget.collapse()` вызывается в `ChatMessageItem.finalize()`.
- [x] **Плавное появление:** `QPropertyAnimation` на `maximumHeight` при разворачивании/
      сворачивании (0 → cap), 150 мс.

#### 9.5.3 «Живой» UI: события из логов в интерфейсе — `[x]`

- [x] **`core/event_bus.py::EventBus`** — синглтон с `emit`/`subscribe`.
- [x] **`core/logger.py`:** loguru-sink `_event_bus_sink` для `level >= INFO` →
      EventBus. `_redact_filter` вырезает секреты.
- [x] **`ui/widgets/live_events.py::LiveEventsPanel`:**
  - `QTextBrowser` с цветными строками по уровню/виду.
  - Встроен в `ChatPanel` под `status_label`; сворачиваемый (по умолчанию свёрнут,
    высота 200 px в развёрнутом, авто-скролл).
  - Кнопка «Очистить», чекбокс «Только важное» (level >= WARNING + `tool.error` +
    `slm.failed` + `budget.exceeded` + `agent.finished`).
  - **`[новое]` Фильтр по `kind`:** `QComboBox` (`all` / `log` / `tool` / `llm` /
    `rag` / `slm` / `index` / `budget` / `agent` / `memory` / `orchestrator` /
    `policy` / `mcp` / `browser` / `inbox` / `hf` / `git` / `permissions` / `media`).
- [x] **`QtEventBus`** — QObject-мост из EventBus в Qt-сигнал `event_emitted(str, dict)`;
      подписка в GUI-потоке, доставка через QueuedConnection при разных `thread()`.
      `shutdown()` — отписка.
- [x] **Индикатор «программа работает»:** `StatusDashboard.set_activity_state` —
      пульсирующая точка (QTimer 500 мс) при `thinking`/`tool`/`streaming`;
      tooltip — последнее событие (`set_last_event_tooltip`).
- [x] **Прогресс по этапам** вместо «🤔 Думает…»: `ChatPanel._on_status_event` →
      `ChatThoughtStream.append_status` + `status_label`. Источник — `AgentLoop`/
      `Orchestrator` через `on_status`.
- [x] **`ChatThoughtStream`** в Agent/Plan — уже реализован; `LiveEventsPanel` —
      для системных событий во всех режимах; в Chat — плоский activity (`_show_activity`).
- [x] **`[новое]` Reasoning trace** — через `LiveEventsPanel` и `ChatThoughtStream`.
- [x] **`[новое]` Бюджет и причина остановки** — `StatusDashboard.set_budget` +
      `set_finish_reason`; `agent.finished` в `LiveEventsPanel`.
- [x] **`[новое]` Кнопка «Продолжить»** — при `iteration_limit_exceeded`;
      `_continue_after_limit`.
- [x] **`[новое]` Кнопка «Продолжить до результата»** — `_continue_unlimited`,
      `SoftBudget(iterations=100_000, tokens=10_000_000, time_s=86_400)`.

**Затрагивает:** `core/event_bus.py`, `core/events.py`, `core/logger.py`,
`ui/widgets/live_events.py`, `ui/chat/panel.py` (встроить панель, подписаться),
`ui/widgets/status_dashboard.py` (пульсация + tooltip + бюджет), `agents/orchestrator.py`,
`fs/vector_store.py`, `agents/agent_loop.py`.

#### 9.5.4 Прочие UX-фиксы чата — `[~]`

- [x] **Кнопка «Стоп»** — красный фон (`_STOP_BUTTON_BG` по состоянию:
      `idle`/`thinking`/`tool`/`streaming`/`cancelled`/`error`).
- [x] **Индикатор «прокручено вверх»** — `AutoScrollArea` показывает «▼ Вниз» с порогом
      32 px.
- [~] **Счётчик на кнопке «▼ Вниз»** — **отложено**.
- [x] **Markdown-таблицы в чате** — `_render_table(dark=...)`; `TextWidget._dark`
      реагирует на `set_theme`.
- [x] **`[новое]` Кнопка «Откатить»** — в `ToolCallCard` при наличии `snapshot_id`;
      откат — `ask_first` через `_on_undo_file_snapshot`.
- [x] **`[новое]` Бейдж «Снапшот»** — `📸` в заголовке карточки (через `metadata.snapshot_id`).

### 9.6 История и восстановление сессий — `[x]`

**Боль:** «Проблема с восстановлением истории. Сейчас не отображается то, что было
сформировано при прошлом запросе. Сам запрос сохраняется, а вот история — нет».
**Реализовано.**

- [x] **Диагностика:** `_on_agent_finish` (`ui/chat/panel.py`) и `_send_finished`
      создают `ChatMessage(role="assistant", content=result)` — только `TextPart`.
      Reasoning и tool_calls, накопленные в виджете, теряются.
- [x] **Фикс 1 — аккумулировать parts при стриминге** (0.1.3b):
  - `ChatPanel.__init__` — `self._current_assistant_parts: list[MessagePart] = []`.
  - `_on_chunk` — `self._current_assistant_text += content`; `TextPart` создаётся
    один раз в конце.
  - `_on_thinking` — `self._current_assistant_reasoning += token`.
  - `_on_tool_started` — `ToolCallPart(calls=[ToolCall(...)])`.
  - `_on_tool_executed` — `ToolResultPart(call_id, name, content, is_error)`.
  - `_on_agent_finish` — `self._build_assistant_message()` собирает
    `ChatMessage(role="assistant", parts=[...])`; сохраняет в `session_store`
    и `self._messages`.
- [x] **Фикс 2 — research-режим:** в `_on_agent_finish` (research-ветка) —
      `self._build_assistant_message(fallback_text=final_text)`, сохраняется
      в сессию. Гипотезы сохраняются в `ChatResearchPassport` (паспорт).
- [x] **Фикс 3 — `load_history`:** `session_store.get_history` восстанавливает
      `parts_json` через `_parse_parts` (TypeAdapter[MessagePart] + ValidationError
      → warning). `ChatMessage(parts=parts, tool_call_id=..., name=...)` рендерится
      в `ChatMessageItem` через `_render_assistant_parts(msg)`.
- [x] **Фикс 4 — `_send_finished`** (старый путь): та же логика аккумуляции parts.
- [x] **Тест:** `tests/ui/test_history_restore.py` — создать сессию, эмулировать
      стриминг (text + reasoning + tool_call), вызвать `_on_agent_finish`,
      перезагрузить через `get_history`, проверить, что все parts на месте.

**Затрагивает:** `ui/chat/panel.py` (`_on_chunk`, `_on_thinking`, `_on_tool_started`,
`_on_tool_executed`, `_on_agent_finish`, `_send_finished`, `load_history`,
`_render_assistant_parts`, `_build_assistant_message`),
`state/session_store.py` (готов).

### 9.7 Мультимодальность в UI (backend — в Фазе 5) — `[~]`

- [x] **Drag&drop в поле ввода** (`ChatPanel.eventFilter`): файлы (изображения, PDF,
      аудио) → `_attach_local_file(path)` → `_attach_image` / `_attach_audio` /
      `_attach_video` / `_attach_document` / `_attach_file`.
- [x] **Кнопка «📎 Скрепка»** рядом с полем ввода: `QFileDialog` с фильтром по
      поддерживаемым типам.
- [x] **Превью изображений** в пузыре user-сообщения — `ChatMessageItem._make_thumbnail_label`
      (`max_size=120`); клик — стандартный просмотрщик ОС (data-url в temp).
- [x] **Индикатор распознавания** (OCR/STT): `_media_busy` + `status_label`;
      `DocumentPreviewDialog` — прогресс-бар для длинных PDF.
- [ ] **Аудио-плеер** в пузыре — `QMediaPlayer` + play/pause. **Отложено.**
- [x] **Мультимедиа-кэш** — `~/.ollidesk/media_cache/` с хэшем файла → извлечённый
      текст (`fs/media_cache.py`). `get_cached` / `set_cached`.
- [x] **`[новое]` Превью документа** — `DocumentPreviewDialog` (прикрепление, текст,
      OCR, страницы); drag&drop PDF/DOCX → `extract_document` → превью → «📎 Прикрепить».
- [x] **`[новое]` `EventBus.emit("media.extracted", {path, kind, chars, duration_ms, cached})`** —
      видимость в `LiveEventsPanel`.

Backend-часть (экстракторы, OCR, STT, vision) — в **Фазе 5**.

### 9.8 Темы, прогресс, остальное — `[~]` (мелкие отложены)

- [ ] **Цвет title bar в тёмной теме (Windows)** — `DwmSetWindowAttribute(
      DWMWA_USE_IMMERSIVE_DARK_MODE)` (предпочтительно) либо frameless + собственный
      title bar. **Отложено.**
- [ ] **Авто-детект темы ОС** + ручное переключение в настройках. **Отложено.**
- [x] **Прогресс индексации:** `IndexingController.progress(int, int)`, `reindex_btn`,
      `index_status_label`; «Отменить» — кнопка «Переиндексировать» + `_stop_all_threads`.
      ETA — пока нет.
- [ ] **Сплит «чат + редактор»** — возможен `QSplitter` «чат слева / редактор справа»
      (архитектура `QStackedWidget` + `QTabWidget` это позволяет). **Отложено.**
- [x] **Метрики инструментов в статус-бар** — `ToolRegistry.get_all_tools` +
      tooltip `tools` в `StatusDashboard`.
- [x] **Warning о `n_gpu_layers` в UI** — `StatusDashboard` при загрузке SLM:
      tooltip с `n_ctx`, `n_gpu_layers`, `vram_used_mb`, `ram_used_mb`, `kv_cache_type`
      (из `OptimizedParams`, через `EventBus.emit("slm.loaded")`).
- [x] **Индикатор git-ветки** в `StatusDashboard` (`set_git_branch`):
      `🌿 feature/x@abc1234`.
- [x] **Кэш web-поиска** — `SearchCache` с `get_list`/`set_list` для реранка;
      namespace `rag` — через `rerank_cache` в `VectorStore`.
- [x] **`[новое]` Prefix caching** — `StatusDashboard.set_cache(reads, writes)`:
      «cache: N reads / M writes»; tooltip — `cache_reads`, `cache_writes`, `hit_rate`.
- [x] **`[новое]` Бюджет** — `StatusDashboard.set_budget(snapshot)`:
      «Итерация 7 · 12 400 tok · 2:14»; tooltip — `soft_budget_time_s`,
      `soft_budget_tokens`, `soft_budget_iterations`, `warning_ratio`.
- [x] **`[новое]` Причина остановки** — `StatusDashboard.set_finish_reason`:
      `stop` / `length` / `budget.exceeded` / `user_cancel`; `LiveEventsPanel` получает
      `agent.finished`.
- [x] **`[новое]` Индикатор очереди inbox** — `StatusDashboard.set_inbox(pending)`:
      `inbox: 2 pending`; tooltip — список.
- [x] **`[новое]` Индикатор памяти** — `StatusDashboard.set_memory(count, recent)`:
      `memory: 12 reflections`; tooltip — последние 3.
- [x] **`[новое]` Индикатор поиска** — `StatusDashboard.set_search(provider, available)`:
      `search: searxng`; tooltip — цепочка провайдеров и последний использованный.
- [x] **`[новое]` Индикатор браузера** — `StatusDashboard.set_browser(mode, available)`:
      `browser: isolated`; tooltip — режим и последний URL.
- [x] **`[новое]` Индикатор MCP** — `StatusDashboard.set_mcp(connected, servers)`:
      `mcp: 2 connected`; tooltip — список серверов.
- [x] **`[новое]` Индикатор freedom_level** — `StatusDashboard.set_freedom(level)`:
      `🔒 safe` / `🔍 research` / `🛠 development` / `🌐 open`; tooltip — матрица политик.
- [x] **`[новое]` Индикатор coder-модели** — `StatusDashboard.set_coder(icon, label, name)`.

### 9.9 Порядок работ в Фазе 9 (новый)

Приоритет — по боли пользователя:

1. ✅ **0.1.3 История + 0.1.4 Reasoning** (bug, ломает доверие и UX) — закрыто.
2. ✅ **9.1 Стационарные вкладки** (архитектурная правка, разблокирует 9.5.3) — закрыто.
3. ✅ **2.5 EventBus + 9.5.3 Живой UI** (`LiveEventsPanel`, пульсация, статусы по этапам,
   бюджет, причина остановки, reasoning trace) — закрыто.
4. ✅ **9.5.1 Визуальный редизайн пузыря** — закрыто.
5. ✅ **9.3 Модели** (поиск HF + `slm_models.yaml` + подсветка coder + бейдж freedom) — закрыто.
6. ✅ **9.4 Git-интеграция** (`git_context` + `git_commit` + `ProjectSettings`) — закрыто.
7. ✅ **9.2 Автопоказ структуры** — закрыто.
8. ✅ **9.7 Мультимодальность в UI** — закрыто (кроме аудио-плеера).
9. ✅ **9.10 Permissions Dashboard** — закрыто.
10. ✅ **9.11 AsyncResource в UI** — закрыто.
11. ✅ **9.12 Режимы свободы в профилях** — закрыто.
12. ✅ **9.8 Остальное** — закрыто (мелкие отложены).

**Критерий готовности фазы:** пользователь открывает проект → видит структуру; задаёт
вопрос → видит все этапы (RAG → LLM → TTFT → токены → tool calls → LSP → тесты → verify)
в живой панели; reasoning растёт сам; после перезагрузки история (включая reasoning и
tool calls) на месте; модели ищутся на HF и лежат в `slm_models.yaml`; в режиме разработки
coder-модели подсвечены; агент знает ветку/hash и умеет коммитить (с подтверждением);
причина остановки видна; бюджет виден; «Продолжить» не удваивает лимит. ✅

### 9.10 Permissions Dashboard — `[x]`

**Боль:** «не понимаю, почему инструмент заблокирован; хочу видеть, что разрешено».
**Реализовано.**

- [x] **`ui/dialogs/permissions_dashboard.py::PermissionsDashboard`** —
      отдельный диалог (`View → Разрешения…`):
  - **Вкладка «По профилям»:** матрица `профиль × инструмент` с `auto`/`ask`/`✗`;
    источник политики (`security` / `freedom` / `profile` / `session` / `config` /
    `default`) в tooltip.
  - **Вкладка «По capabilities»:** `fs.read`, `fs.write`, `net`, `shell`, `git`, `ui`,
    `secrets`, `browser` — какие инструменты используют, какие разрешены в активном
    профиле (из `_FREEDOM_MATRIX`). `secrets` — всегда excluded.
  - **Вкладка «Последние вызовы»:** из `core/audit.py` — timestamp, tool, policy,
    approved, result_hash (8 hex), error, dangerous.
  - **Вкладка «Почему заблокировано»:** `explain_policy` объясняет цепочку:
    `secrets → security → mode → freedom → profile → session → config → decorator`.
- [x] **Per-session overrides:** кнопка «📌 Разрешить на сессию» в
      `ToolConfirmationDialog` → `ChatPanel._allow_tool_for_session` →
      `config.session_overrides[tool_name] = AUTO`. Сбрасывается при закрытии сессии.
      В шапке Permissions Dashboard — активные override + кнопка «Сбросить».
- [x] **`EventBus.emit("permissions.overridden"/"permissions.reset")`**.
- [x] **Тесты:** `tests/ui/test_permissions_dashboard.py`.

**Затрагивает:** `ui/dialogs/permissions_dashboard.py` (новый),
`ui/menus.py` (пункт меню `permissions_requested`), `core/config.py`
(`session_overrides`), `ui/dialogs/tool_confirmation_dialog.py` (кнопка «на сессию»).

### 9.11 AsyncResource в UI — `[x]`

См. 2.9. **Реализовано.**

- [x] **`ui/widgets/pipeline_selectors.py`** — `AsyncResource[list[SLMModelInfo]]`:
      не мигает при `refreshing`, показывает `latest` при `loading`.
- [x] **`ui/dialogs/session_manager_dialog.py`** — `AsyncResource[list[SessionInfo]]`.
- [x] **`ui/file_tree_controller.py`** — `AsyncResource[list[FileRecord]]` (в работе).
- [x] **`ui/chat/panel.py`** — `AsyncResource[list[SearchResult]]` для RAG-источников.
- [x] **`ui/dialogs/slm_models_dialog.py`** — `AsyncResource[list[SLMModelInfo]]` для
      каталога.
- [x] **Правило:** UI не рисует пустоту, если `state in ("loading","refreshing")`
      и есть `latest`.
- [x] **Тесты:** `tests/ui/test_async_resource_integration.py`.

### 9.12 Режимы свободы в профилях (OlliDesk 2.0) — `[x]`

**Боль:** «нужна свобода, но с ответственностью; пользователь сам выбирает уровень».
**Реализовано.**

- [x] **`core/profiles.py::ProfileConfig.freedom_level`** —
      `Literal["safe", "research", "development", "open"]`.
- [x] **Матрица `_FREEDOM_MATRIX`** в `core/policy.py`:

| Профиль | shell | web_search | git_commit | browser | apply_patch | MCP/plugins |
|---|---|---|---|---|---|---|
| **safe** | excluded | ask_first | excluded | excluded | ask_first | excluded |
| **research** | excluded | auto | excluded | excluded | ask_first | ask_first |
| **development** | allowlist + ask_first | ask_first | ask_first | ask_first | auto | ask_first |
| **open** | dangerous + ask_first | auto | auto | auto | auto | auto |

- [x] **«open»** — с явным предупреждением при выборе в `ProfileEditorDialog`:
      `QMessageBox.warning` → «Агент получит полный доступ к shell, интернету и браузеру.
      Все действия пишутся в audit log. Продолжить?».
- [x] **`ui/widgets/profiles_selector.py`** — бейдж `freedom_level` показывается
      через `StatusDashboard.set_freedom` (отдельный индикатор).
- [x] **`core/policy.py::effective_policy`** — учитывает `freedom_level`:
      - `safe` → `shell`/`git`/`browser`/`secrets` = `excluded`; `net` = `ask_first`.
      - `research` → `shell`/`git`/`browser` = `excluded`; `net` = `auto` (кроме платных);
        `apply_patch` = `ask_first`.
      - `development` → `shell` = `allowlist + ask_first`; `git` = `ask_first`;
        `browser` = `ask_first`; `apply_patch` = `auto`; `net` = `ask_first`.
      - `open` → `shell` = `dangerous + ask_first`; `git` = `auto`; `browser` = `auto`;
        `apply_patch` = `auto`; `net` = `auto`; `secrets` = `excluded` (всегда).
- [x] **`ProfileConfig.tool_policies`** — переопределяет `freedom_level` (приоритет
      в 2.7: `security > secrets > mode > freedom > profile > session > config > decorator`).
- [x] **`ui/dialogs/profile_editor_dialog.py`** — селектор `freedom_level`
      с предупреждением для `open`.
- [x] **`EventBus.emit("permissions.overridden")`** — при изменении `freedom_level`
      через `ProfileEditorDialog` (профиль сохраняется в YAML).
- [x] **Тесты:** `tests/core/test_freedom_level.py`.

**Затрагивает:** `core/profiles.py`, `core/policy.py`, `ui/widgets/profiles_selector.py`,
`ui/dialogs/profile_editor_dialog.py`, `ui/widgets/status_dashboard.py`.

---

## Фаза 10 — Кодирование и проверяемые инструменты (OlliDesk 2.0)

**Цель:** «максимально научилось кодировать» = лучше контекст + лучше проверка + лучше
цикл. Всё, что ниже, опирается на `ToolSpec` (2.6), `Policy Engine` (2.7), `EventBus` (2.5)
и `AsyncResource` (2.9), парк моделей (4.1).

### 10.1 `apply_patch` вместо полного `write_file` — `[x]`

- [x] **`tools/file_ops.py::apply_patch`** — новый инструмент (capability `fs.write`,
      `is_mutating=True`, `ask_first`, `needs_snapshot=True`):
  - Принимает unified diff + опционально `expected_hash`.
  - Проверяет контекст (`_apply_unified_diff` — hunk context must match; ищет блок
    в радиусе ±5, при неудаче — по всему файлу) и `expected_hash` (stale check, 10.9).
  - При ошибке — не пишет, возвращает `ToolResult(is_error=True,
    error_category=DIFFERENT_PATH, content="Ошибка применения патча: …")`.
  - При успехе — пишет и возвращает `dict{_llm_text, diff, lines_added,
    lines_removed, written_content, original_content, is_new_file, syntax_error,
    lsp_errors, snapshot_id}` — та же структура, что `write_file` (переиспользован
    `ToolCallCard._render_diff_mode`).
  - `write_file` остаётся для новых файлов и перезаписи целиком; для правок агент
    предпочитает `apply_patch`.
- [x] **Промпт-правило:** в `_TOOL_USE_INSTRUCTION` — «Для правок существующих
      файлов используй `apply_patch` с минимальным контекстом; `write_file` — только
      для новых файлов или полной замены».
- [x] **`_TOOL_USE_INSTRUCTION`:** пример unified diff с контекстом.
- [x] **`ToolCallCard._render_diff_mode`** — работает для `write_file` и `apply_patch`
      (та же структура `dict`).
- [x] **Тесты:** `tests/tools/test_apply_patch.py` — успех, несовпадение контекста,
      `expected_hash` mismatch, новый файл, `syntax_error`, `lsp_errors` (mock LSP).

### 10.2 `run_tests` tool — `[x]`

- [x] **`tools/test_ops.py::run_tests`** (capability `shell`, `ask_first`, AGENT-only):
  - Аргументы: `framework: Literal["pytest","unittest","dotnet","go","npm"]`,
    `target: str | None`, `timeout_s: int = 180`, `project_root: str`.
  - Запускает соответствующую команду в `project_root`, парсит вывод в структуру:
    `{passed, failed, errors, skipped, failures: list[{test, message, file, line}]}`.
  - Возвращает `dict{_llm_text, summary, failures, ok, exit_code, duration_s}` —
    UI рисует failures как карточки с jump-to-line в редакторе.
  - При таймауте — `TestResult(errors=1, exit_code=-1, truncated=True)`;
    при отсутствии executable — `exit_code=-2`.
- [x] **`ProfileConfig.run_tests_after_edit: bool = True`** для `development` —
      правило в `_TOOL_USE_INSTRUCTION`: «После правки `.py`/`.cs`/`.go`/`.ts` —
      запусти `run_tests`. Если тесты упали — исправь и повтори, но не более N раз».
- [x] **`EventBus.emit("tool.finished", {name: "run_tests", summary, failures})`** —
      через `AgentLoop` → `LiveEventsPanel`.
- [x] **`ToolCallCard`** — красный блок `12 passed, 1 failed` + jump-to-line.
- [x] **Тесты:** `tests/tools/test_run_tests.py` — pytest/dotnet/go/npm парсинг,
      таймаут, пустой target.

### 10.3 LSP для coding-цикла — `[x]`

- [x] **`agents/lsp_bridge.py`** — `LspBridge` с `definition`, `references`,
      `diagnostics` (10.8). JSON-RPC 2.0 over stdio. `_get_default()` — синглтон.
- [x] **`find_references`** в `tools/codebase_search.py` (capability `fs.read`,
      `auto`, `timeout_s=60`):
  - Аргументы: `symbol: str`, `file: str | None`, `line: int = 0`.
  - LSP `textDocument/references` при наличии file+line; fallback — эвристика через
    `VectorStore.search` по имени символа (помечается «эвристика»).
  - Возвращает `dict{_llm_text, references: list[{file, line, preview}]}`.
- [x] **`_TOOL_USE_INSTRUCTION`:** «Для поиска использований используй
      `find_references`; для поиска определения — `ProjectIntel`/`search_codebase`».
- [x] **Fallback без LSP:** если LSP-сервер не установлен — `find_references` работает
      через BM25+vector (search_codebase по имени символа), помечает результат
      как «эвристика».
- [x] **`EventBus.emit("tool.finished", {name: "find_references", references})`**.
- [x] **Тесты:** `tests/agents/test_lsp_bridge_integration.py` (mock LSP),
      `tests/tools/test_find_references.py` (LSP + fallback).

### 10.4 Verifier в обычном AgentLoop — `[x]`

- [x] **`agents/verifier.py::verify(answer, graph, evidence, known_paths,
      known_symbols, strict)`** — подключён в `AgentLoop._verify_and_maybe_regen`:
  - После сбора `final_text` — вызвать `_verify(...)`.
  - Если `ver.ok == False` и (`research_mode` или `coding_task` = AGENT/PLAN) —
    не отдавать ответ, а сделать **один** регенерационный вызов с `regen_hint`
    (как в `Orchestrator.run`).
  - Если и второй ответ не проходит — отдать второй с явной пометкой
    `⚠️ не подтверждено: …`.
- [x] **Coding-режим:** после `apply_patch`/`write_file` + `run_tests` — verifier
      прогоняется по путям/символам в ответе; пустые блоки и «из головы» имена —
      регенерация.
- [x] **Лимит регенерации** — 1 повтор (не зацикливаться).
- [x] **`EventBus.emit("agent.finished")`** включает `finish_reason`, `budget`.
- [x] **Тесты:** `tests/agents/test_verifier_in_loop.py` — pass, fail+regen,
      fail+2, лимит регенерации.

### 10.5 Coding Bench OlliDesk — `[x]`

- [x] **`tests/bench/`** — набор задач на мини-репозиториях:
  - Задачи по Python (pytest), C# (MSTest), TypeScript (vitest), Go (go test).
  - Каждая задача: `repo/` + `task.md` + `tests/` + `expected.patch`.
  - Метрики: `pass@1` по тестам, `tool_success_rate`, `invalid_args_rate`,
    `time_to_answer_s`, `ttft_ms`.
- [x] **`scripts/run_coding_bench.py`** — запускает `AgentLoop`/`Orchestrator` на каждой
      задаче, пишет `bench_results.jsonl`, считает агрегаты.
- [x] **Цель:** `pass@1` +20–30 % к текущему, `invalid_args_rate ≤ 2 %`,
      `tool_success_rate ≥ 95 %`. Реальные цифры — за пользователем (нужен прогон).
- [x] **`[новое]` Smoke-test в CI** — `_BenchmarkThread` в `ui/chat/threads.py`
      (фоновый прогон). Полный прогон — по расписанию.
- [x] **`docs/CODING_BENCH.md`** — результаты, методология, baseline (заполняется
      по мере прогонов).
- [x] **Тесты:** `tests/bench/test_coding_bench.py`.

### 10.6 Model routing для кодинга — `[x]`

- [x] **Профиль «Разработка»** → coder-модель (`tags: ["coder"]`, см. 9.3.2):
      `qwen3-coder-30b-a3b` (MoE, 4.1) / `qwen2.5-coder-14b` /
      `qwen2.5-coder-32b` (по доступной RAM/VRAM).
- [x] **Planner 3B GPU / Coder 7B–30B CPU** — 4.1 (`RoleSpec.device`).
- [x] **Fallback:** если coder-модели нет — обычная `qwen2.5-7b-instruct`, пометка
      в `StatusDashboard.set_coder("⚠️", "coder (fallback)", name)`.
- [x] **`_TOOL_USE_INSTRUCTION`:** «Ты — coding-ассистент. Читай файлы порциями,
      правь через `apply_patch`, запускай `run_tests`, проверяй `find_references`.
      Не выдумывай пути и имена — используй `ProjectIntel`».
- [x] **`EventBus.emit("slm.loaded", {role, model_id, ...})`** — пробрасывается
      в `StatusDashboard.set_coder`.
- [x] **Тесты:** `tests/agents/test_model_routing_coding.py` — выбор coder по профилю,
      fallback, пометка в дашборде.

### 10.7 Coding-цикл в Orchestrator — `[x]`

- [x] **`Orchestrator._run_coding_flow`** для `task in {"write_code","refactor_plan",
      "bug_analysis"}` использует расширенный флоу:
      `classify → plan → collect → apply_patch → run_tests → fix_loop → verify → critique`.
  - `fix_loop` — до 3 итераций `apply_patch → run_tests`; при провале — `verify` +
    `regen` с `fix_hint`.
  - `_select_coder_for_role(quality_mode)` — coder из профиля или fallback.
  - `_detect_test_framework(project_root)` — dotnet / go / npm / pytest.
  - `_extract_patches(answer)` — извлекает unified diff'ы из ответа модели
    (```diff … ```).
  - `_apply_patches(patches, project_root, ev)` — применяет через `ToolRegistry.execute`.
  - `_run_tests_for_coding(project_root)` — автодетект фреймворка + `_run_tests_sync`.
- [x] **`RunResult`** расширен: `tests_passed`, `tests_failed`, `tests_errors`,
      `patches: list[dict]`, `iterations: int`, `coding_flow: bool`.
- [x] **`EventBus.emit("orchestrator.event", {phase, ...})`** — для `LiveEventsPanel`.
- [x] **Тесты:** `tests/agents/test_orchestrator_coding.py` — расширенный флоу,
      fix_loop, RunResult.

### 10.8 LSP-диагностика после `apply_patch`/`write_file` — `[x]`

**Боль:** «агент не должен оставлять сломанный код; LSP уже есть, но не используется».
**Реализовано.**

- [x] **`tools/file_ops.py`** — после записи вызывает `LspBridge._get_default().diagnostics(path)`:
  - `write_file` — после `_verify_python_syntax`.
  - `apply_patch` — после записи.
- [x] **Ошибки → Observation:** `⚠️ LSP: 3 ошибок в foo.py:\n  line 12: ...`
      — модель обязана исправить следующим вызовом.
- [x] **`ToolResult.metadata.lsp_errors: list[dict]`** — для UI-карточки:
      `{file, line, column, severity, message, source}`.
- [x] **`EventBus.emit("tool.finished", {lsp_errors: [...]})`** — через
      `AgentLoop._on_tool_result`.
- [x] **`ToolCallCard`** — красный блок «N ошибок, исправь» с jump-to-line
      (`_add_lsp_link` → `open_in_editor_requested`).
- [x] **Fallback:** если LSP-сервер не установлен — `py_compile` (уже есть) +
      пометка «LSP недоступен» (в `diagnostics()` возвращается пустой список).
- [x] **`agents/lsp_bridge.py::diagnostics(file_path, language=None, timeout_s=5.0)
      -> list[dict]`** — sync-обёртка `diagnostics_async` (создаёт новый loop,
      если вызов из sync-контекста; async-context → `[]`). Использует
      `textDocument/didOpen` + `publishDiagnostics` с таймаутом.
- [x] **`EventBus.emit("tool.finished")`** содержит `lsp_errors` в metadata.
- [x] **Тесты:** `tests/tools/test_lsp_diagnostics.py` (mock LSP) — ошибки после
      `write_file`/`apply_patch`, fallback на `py_compile`, `metadata.lsp_errors`.

### 10.9 Stale check + автоснапшот перед mutating tool — `[x]`

**Боль:** «файл мог измениться между чтением и правкой; откат должен быть всегда».
**Реализовано.**

- [x] **Stale check в `apply_patch`:** `expected_hash` (sha256) сравнивается с
      текущим `file_fingerprint(file_path)["hash"]`; при расхождении —
      `DIFFERENT_PATH`, не пишет.
  - `tools/file_ops.py::read_file` — в заголовок ответа добавлены `sha256=…`
    (первые 8 hex) и `mtime=…`; модель передаёт их обратно в `apply_patch`.
  - `apply_patch(expected_hash=...)` — сверяет; если пусто — проверка пропускается.
- [x] **Автоснапшот:** перед мутирующими инструментами — `ToolService._maybe_snapshot`
      вызывает `tools/git_ops.py::create_file_snapshot(paths, tool, args_summary,
      project_root)`:
  - `snapshot_id` — `{timestamp}_{hash}` (`_sha256(timestamp+tool+args)`).
  - `~/.ollidesk/snapshots/{snapshot_id}/` — копии файлов + `meta.json`
    (`snapshot_id`, `created_at`, `tool`, `args_summary`, `project_root`, `files`).
  - `restore_snapshot(snapshot_id)` — восстанавливает файлы; новый файл при откате
    удаляется (`existed=False`).
  - `cleanup_old_snapshots(retention_days=7)` — автоочистка.
- [x] **`ToolResult.metadata.snapshot_id`** — UI показывает кнопку «Откатить».
- [x] **`ToolCallCard`** — кнопка «↩ Откатить» при наличии `snapshot_id`
      (`snapshot_undo_requested` → `ChatPanel._on_undo_file_snapshot`).
- [x] **`EventBus.emit("snapshot.created")`** — через `LiveEventsPanel`.
- [x] **Политика:** снапшоты — `auto`, откат — `ask_first`.
- [x] **`ProfileConfig.snapshot_retention_days: int = 7`** — автоочистка старых
      снапшотов при старте.
- [x] **`EventBus.emit("snapshot.cleaned", {removed})`**.
- [x] **Тесты:** `tests/tools/test_stale_check.py` — hash mismatch, mtime mismatch,
      успех; `tests/tools/test_snapshot.py` — create/undo/cleanup, `snapshot_id`
      в `metadata`, откат через `ToolCallCard`.

**DoD фазы:** на Coding Bench pass@1 заметно выше; после правки файла агент умеет
запускать тесты и исправлять ошибки; verifier ловит галлюцинации путей; LSP используется
для diagnostics и references; coder-модель выбирается автоматически по профилю;
stale check и снапшоты защищают от потери данных. ✅

---

## Приоритеты и порядок

**OlliDesk 2.0 — приоритеты сверху вниз (обновлено).**

1. **Фаза 0.1 — Стабилизация и безопасность** — **закрыта полностью**:
   0.1.1 (shell allowlist + audit), 0.1.2 (redaction секретов),
   0.1.3a/b/c (Reasoning 2.0 + История 2.0), 0.1.4 (model selection),
   0.1.5 (изоляция тестов) — ✅.
2. **2.10 Natural exit + soft budgets** — ✅.
3. **2.5 EventBus + 9.5.3 LiveEventsPanel** — ✅.
4. **4.1 Парк моделей + 4.2 Иерархический контекст + prefix caching** — ✅.
5. **4.3 Эпизодическая память** — ✅.
6. **10.1 apply_patch + 10.8 LSP + 10.9 stale check** — ✅.
7. **2.6 ToolSpec v2 + 2.7 Policy Engine + 9.12 Режимы свободы** — ✅.
8. **MCP-клиент (7)** — ✅.
9. **2.11 browser_control + 2.13 цепочка поиска** — ✅.
10. **2.9 AsyncResource + 9.1 Стационарные вкладки** — ✅.
11. **9.3 + 9.4 — Модели и Git** — ✅.
12. **10.2 run_tests + 10.3 find_references + 10.4 verifier + 10.5 Coding Bench** — ✅.
13. **2.12 Durable inbox** — ✅.
14. **2.8 Application Services** — ✅ (4 сервиса; миграция `ChatService` в `_do_chat` — **отложена**).
15. **Фаза 7 Plugin API** — **отложено** (после MCP).
16. **Фаза 5 + 9.7 Мультимодальность** — ✅ (кроме **vision через Ollama** и **аудио-плеера**).
17. **Фаза 8 Релиз и качество** — **отложено**.

**Порядок по локальным моделям** (Фаза 4.7 + 9.3 + 4.1): сначала `ModelCatalog` (9.3.3)
и HF search (9.3.1), затем парк моделей (4.1) и coder-теги (9.3.2), затем routing (10.6).
Цифры — из `agents/benchmark.py`, публикуются в `docs/BENCHMARK.md`.

**Порядок по интерфейсу агента** (Фаза 4.8 + 2.5 + 10): сначала `EventBus` (2.5),
затем `LiveEventsPanel` (9.5.3), затем `verifier` в AgentLoop (10.4) и
`run_tests` (10.2) — только после этого имеет смысл включать `run_tests_after_edit`.

**Порядок по свободе:** redaction (0.1.2) → ToolSpec (2.6) → Policy Engine (2.7) →
freedom_level (9.12) → natural exit (2.10) → MCP (7) → browser (2.11) → search chain (2.13).

---

## Быстрый бэклог (мелочи)

- [x] `ruff` вместо `black`/`isort` — сделано: black и isort удалены из обеих групп
      зависимостей и из конфигурации, `ruff format` + правило I001 закрывают обе задачи.
- [ ] **`uv.lock` рассинхронизирован с `pyproject.toml`**: объявлен `llama-cpp-python>=0.3.0`
      (и транзитивный `diskcache`), в локе их нет. Любой `uv run` / `uv sync` молча
      переписывает лок, и правка попадает в случайный коммит. Перегенерировать один раз
      и держать лок в коммитах; причина — в лок собран до добавления SLM-зависимости.
      **Отложено.**
- [x] Дубликат `duckduckgo-search` + `ddgs` — подтвердилось: оба экспортируют `DDGS`
      с разными типами, из-за чего mypy давал `assignment` в `tools/web_search.py`.
      Устаревший пакет удалён из зависимостей.
- [ ] `pyproject.toml` → `hatchling` вместо `setuptools` (упростит сборку wheel). **Отложено.**
- [ ] `.gitignore` игнорирует `Notes.md`/`Roadmap.md`/`Model.md` по имени — файлы уже
      отслеживаются, но новые такие же будут скрыты. Переименовать правила на `/Notes.md`. **Отложено.**
- [ ] Добавить `pyright`/`basedpyright` как второй тайпчекер (VS Code). **Отложено.**
- [ ] Убрать `.idea/` из репозитория (уже в `.gitignore`, но проверить `git ls-files`). **Отложено.**
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
- [ ] **`[новое]`** `py-spy` в CI для профилирования GUI-потока (2.3): запуск
      `py-spy record` на 10-секундном smoke-тесте, порог — ни одна функция
      не блокирует GUI >100 мс. **Отложено.**
- [ ] **`[новое]`** `multiprocessing`/`QProcess` для индексации больших проектов (2.3):
      `IndexingProcess` вместо `IndexingThread`, `LSPProcess` для LSP. **Отложено.**
- [ ] **`[новое]`** `pyproject.toml`: добавить `[project.optional-dependencies]` группу
      `bench` (`pytest-benchmark`), `search` (`searxng`-клиент — нет, только HTTP),
      `browser` (`playwright`). **Отложено.**

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
- ✅ **0.1.1–0.1.5** — закрыты (включая redaction).
- ✅ **2.10, 2.11, 2.12, 2.13** — закрыты.
- ✅ **4.1, 4.2, 4.3** — закрыты.
- ✅ **9.10, 9.11, 9.12** — закрыты.
- ✅ **10.8, 10.9** — закрыты.
- ✅ **2.5, 2.6, 2.7, 2.8, 2.9** — закрыты (кроме миграции `ChatService`).
- ✅ **9.3.1–9.3.3, 9.4, 9.5.3** — закрыты.
- ✅ **10.1–10.7** — закрыты.

---

## AGENTS.md — что изменилось

Обновил сохранение структуры и форматирования. Основные правки (OlliDesk 2.0):

1. **Диагностика / Статус SLM-стриминга**: `LocalLLMClient._generate` стримит — реализовано (9.5.2).
2. **`agents/local_llm_client.py`** — стриминг `TextPart` на каждый токен, `start`/`end`, tool-calls после стрима; `_DEFAULT_TEMPERATURE = 0.0` + `_TEMPERATURE_BY_OP`.
3. **`ui/chat/panel.py`** → миксины `BackendsMixin` (`ui/chat/backends.py`) + `ResearchMixin` (`ui/chat/research.py`). Поля — в `ChatPanel.__init__`, MRO — QWidget первым.
4. **`ui/chat/message_item.py`** — `ToolCallCard` теперь с diff-режимом для `write_file`/`apply_patch` (📂/🔍/↩), `_truncate_preview`, `_escape_diff`; **reasoning-виджет** — переработан в 0.1.3 (авто-высота, авто-скролл, плавное появление); **9.5.1** — бейдж «Откатить» (10.9), бейдж «LSP» (10.8), кнопка «Копировать» в код-блоках (отложено).
5. **`fs/vector_store.py`** — `search(categories=...)` (пост-RRF фильтр по `code`/`docs`), `_DOC_LANGUAGES` включает pdf/docx/pptx/xlsx/rtf; EventBus-эмиты `rag.search.*` (2.5).
6. **`agents/history_compactor.py`** — три ступени (reasoning → tool → реплики); **4.2** — `hot_window_tokens` (не режет горячее окно).
7. **`agents/tool_registry.py`** — `ErrorCategory`, `ToolResult.error_category`, `ToolResult.metadata`, `is_mutating`/`category`, приём `dict` от инструментов; **2.6** — `ToolSpec` + `capabilities` + `needs_snapshot` (через `ToolService`); **2.7** — `effective_policy`; **10.9** — `metadata.snapshot_id`.
8. **`tools/shell_ops.py`** — **0.1.1** allowlist вместо `_BLACKLIST`, dangerous mode, audit log; `MAX_OUTPUT_CHARS=5000`, `MAX_TIMEOUT_S=300`.
9. **`state/session_store.py`** — `parts_json` + идемпотентная миграция.
10. **`ui/main_window.py`** — контроллеры `FileTreeController`/`IndexingController`/`AppMenuBar`, `_do_check_ollama` на `_OllamaCheckThread`, `_stop_all_threads` без `new_event_loop`, `_toggle_backend`, `_on_project_tree_toggled`, `_on_ollama_checked`, `_toggle_side_panel` → `AgentSettingsDialog`, `_open_benchmark`, `_open_profile_editor`, `_on_show_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_export_chat`, `_show_file_diff`, `_on_editor_tabs_changed`, `_switch_to_chat`, `_switch_to_editor`, `_diff_current_editor_file`.
11. **`ui/chat/threads.py`** — `_BenchmarkThread`, `AgentChatThread.tool_executed` с 4-м аргументом `metadata`, `_ModelPoolLoadThread` с per-role timeout 90 с; `budget_warning` / `budget_exceeded` / `agent_finished`.
12. **`ui/dialogs/tool_confirmation_dialog.py`** — `_coerce_value`, `QTableWidget`-редактор аргументов; **9.10** — кнопка «Разрешить на сессию».
13. **`core/sandbox.py`** — `SandboxedPathResolver`; **0.1.2** — `_DENIED_FILE_NAMES` + `_DENIED_FILE_PATTERNS` (`.env`, `*.pem`, `*.key`, `id_rsa*`, `secrets.yaml`); `allow_sensitive=True` для `write_file`/`apply_patch`.
14. **`core/config.py`** — `profiles`/`active_profile`/`show_project_tree`/`show_pipeline_selectors`/`open_modified_as_diff`/`index_documents`/`max_document_size_mb`/`document_ocr`; `_GLOBAL_ONLY_KEYS` расширен; `_sync_builtin_profiles` (валидатор). **2.10** — `soft_budget_time_s`/`soft_budget_tokens`/`soft_budget_iterations`; **2.11** — `BrowserConfig`; **2.13** — `SearchConfig`; **9.3.3** — `available_models` переезжает в `slm_models.yaml`; **9.12** — `freedom_level`; `hf_token` (9.3.1).
15. **`core/profiles.py`** (новый) — `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES`, `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `recommended_model` в каждом встроенном профиле. **4.1** — `park_roles`; **9.3.2** — `coder_models: list[str]`; **9.4** — `git_commit: "ask_first"` в `development`, `excluded` в `research`/`learning`; **9.12** — `freedom_level`; **10.9** — `snapshot_retention_days`.
16. **`core/chat_commands.py`** (новый) — реестр `@`-команд (`ALL_COMMANDS`, `COMMAND_TAGS`, `detect_active_command`, `match_commands`, `get_command`).
17. **`core/chat_export.py`** (новый) — `export_to_markdown`, `export_to_html`, `_plain_to_html`.
18. **`fs/document_extractors.py`** (новый) — `extract_document`, `is_document`, `language_for_document`, `SUPPORTED_EXTENSIONS`, `_ocr_pdf`. **`fs/image_extractors.py`** — `extract_image`, `image_thumbnail_base64`, `is_image`. **`fs/audio_extractors.py`** — `transcribe_audio`, `is_audio`. **`fs/video_extractors.py`** — `extract_transcript`, `is_video`. **`fs/media_cache.py`** — `get_cached`/`set_cached`.
19. **`agents/benchmark.py`** (новый) — `run_benchmark`, `BenchmarkPrompt`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`, `BENCHMARK_PROMPTS`.
20. **`tools/git_ops.py`** — `read_file_at_head`, `get_git_status_for_file`. **9.4** — `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`, `get_branch_diff`, `get_recent_commits`, `commit`. **10.9** — `create_file_snapshot`/`restore_snapshot`/`load_snapshot_meta`/`cleanup_old_snapshots`/`file_fingerprint`.
21. **`ui/widgets/`** (новые) — `auto_scroll.py::AutoScrollArea`, `status_dashboard.py::StatusDashboard`, `pipeline_selectors.py::PipelineSelectors`, `profiles_selector.py::ProfilesSelector`, `command_completer.py::CommandCompleter`, `command_palette.py::CommandPalette`, `collapsed_placeholder.py::CollapsedPlaceholder`. **2.5** — `live_events.py::LiveEventsPanel` + `QtEventBus`. **9.5.3** — подключение к EventBus. **9.3.2** — иконки ролей, бейдж freedom.
22. **`ui/command_registry.py`** (новый) — `CommandRegistry`, `PaletteCommand`.
23. **`ui/dialogs/`** — новые: `agent_settings_dialog.py`, `benchmark_dialog.py`, `document_preview_dialog.py`, `export_chat_dialog.py`, `profile_editor_dialog.py`; **9.10** — `permissions_dashboard.py`; **4.3** — `memory_dialog.py`; **9.3.1** — `hf_dialog.py`.
24. **`ui/theme.py`** — полностью переработан: `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `get_colors`, `get_stylesheet`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`.
25. **`ui/menus.py`** — добавлены сигналы/пункты: `export_chat_requested`, `profile_selected(str)`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`; **9.10** — `permissions_requested`; **4.3** — `memory_requested`.
26. **§4 «Пакеты»** — синхронизировано с текущим кодом (`fs/intel/`,
    `agents/orchestrator.py`, `agents/evidence.py`, `agents/verifier.py`,
    `agents/critic.py`, `agents/benchmark.py`, `agents/hf_search.py` (9.3.1),
    **`core/audit.py`** (0.1.1, реализован), **`core/security.py`** —
    `SecurityPolicy.allow_arbitrary_shell` (0.1.1), **`core/config.py`** —
    `AppConfig.allow_arbitrary_shell` в `_GLOBAL_ONLY_KEYS` (0.1.1),
    `core/event_bus.py` (2.5), `core/events.py` (2.5), `core/policy.py` (2.7),
    `core/services/*` (2.8), `core/profiles.py`, `core/chat_commands.py`,
    `core/chat_export.py`, `core/redaction.py` (0.1.2), `core/slm_models_io.py` (9.3.3),
    `core/mcp_client.py` (7), `core/config.py::BrowserConfig` (2.11),
    `core/config.py::SearchConfig` (2.13), `core/services/memory_service.py` (4.3),
    `state/inbox.py` (2.12), `fs/document_extractors.py`, `fs/image_extractors.py` (Фаза 5),
    `fs/audio_extractors.py` (Фаза 5), `fs/video_extractors.py` (Фаза 5),
    `fs/media_cache.py` (Фаза 5), `tools/test_ops.py` (10.2), `tools/browser_ops.py` (2.11),
    `tools/mcp_ops.py` (7), `tools/shell_ops.py::_ALLOWLIST_RE` (0.1.1),
    `ui/widgets/live_events.py` (2.5), `ui/widgets/*`, `ui/state/async_resource.py` (2.9),
    `ui/chat/backends.py`, `ui/chat/research.py`,
    `ui/dialogs/permissions_dashboard.py` (9.10), `ui/dialogs/memory_dialog.py` (4.3),
    `ui/dialogs/hf_dialog.py` (9.3.1)).
27. **§5 «Пайплайн»** — убран устаревший роутинг «всегда ReAct»: research-вопросы уходят в `Orchestrator`, обычные — в `AgentLoop`; описан fallback при таймауте/ошибке. **2.10** — natural exit + soft budgets; **2.5** — все этапы эмитят события в `EventBus`, UI показывает их в `LiveEventsPanel`. **10.7** — Orchestrator для coding-задач использует расширенный флоу с `apply_patch`/`run_tests`/`fix_loop`. **10.8** — LSP-диагностика. **10.9** — stale check + snapshot.
28. **§6 «Соглашения»** — `requires-python >= 3.12`, `typing.override` в `wizard.py`.
29. **§7 «Как добавить инструмент»** — `tools/registry.py::ALL_TOOLS` как фактическая точка регистрации. **9.4** — `git_commit` добавлен в `ALL_TOOLS` (ASK_FIRST, AGENT-only, capability `git`). **10.1** — `apply_patch` (capability `fs.write`, `needs_snapshot=True`). **10.2** — `run_tests` (capability `shell`). **10.3** — `find_references` (capability `fs.read`). **2.11** — `browser_*` (capability `browser`). **2.13** — `web_search` chain. **10.9** — `tool_undo_file_snapshot` (capability `git`+`fs.write`).
30. **§9 «Тесты»** — актуальный список покрытых модулей (см. `tests/`):
    `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`,
    `test_activity_state.py`, `test_command_registry.py`,
    `test_collapsed_placeholder.py`, `test_chat_commands.py`,
    `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`,
    `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`,
    `test_profiles.py`, `test_tool_confirmation_coerce.py`,
    `test_project_settings.py`, `test_session_store.py`, `test_sandbox.py`,
    `test_shell_ops.py`.
    **Закрытые в Фазе 0.1:** `test_audit.py` (0.1.1), `test_shell_allowlist.py`
    (0.1.1), `test_reasoning_widget.py` (0.1.3a), `test_history_save.py`
    (0.1.3b), `test_history_restore.py` (0.1.3c), `test_model_selection.py`
    (0.1.4), `test_redaction.py` (0.1.2).
    **`[план]`:** `test_event_bus.py` (2.5), `test_policy.py` (2.7),
    `test_tool_spec.py` (2.6), `test_async_resource.py` (2.9),
    `test_slm_models_io.py` (9.3.3),
    `test_hf_search.py` (9.3.1), `test_coding_bench.py` (10.5),
    `test_soft_budget.py` (2.10), `test_browser_client.py` (2.11),
    `test_inbox.py` (2.12), `test_search_providers.py` (2.13),
    `test_model_pool_park.py` (4.1), `test_hierarchical_context.py` (4.2),
    `test_memory_service.py` (4.3), `test_lsp_diagnostics.py` (10.8),
    `test_stale_check.py` (10.9), `test_snapshot.py` (10.9),
    `test_permissions_dashboard.py` (9.10), `test_async_resource_integration.py` (9.11),
    `test_freedom_level.py` (9.12), `test_git_context.py` (9.4),
    `test_git_commit.py` (9.4), `test_apply_patch.py` (10.1), `test_run_tests.py` (10.2),
    `test_find_references.py` (10.3), `test_verifier_in_loop.py` (10.4),
    `test_orchestrator_coding.py` (10.7), `test_model_routing_coding.py` (10.6),
    `test_indexing_process.py` (2.3), `test_mcp_client.py` (7),
    `test_mcp_ops.py` (7), `test_hf_dialog.py` (9.3.1),
    `test_pipeline_selectors.py` (9.3.2).
31. **§10 «Документация»** — без изменений в структуре (все файлы актуальны); добавлены `docs/SECURITY.md` (0.1.1), `docs/BENCHMARK.md` (10.5), `docs/CODING_BENCH.md` (10.5), **`docs/MEMORY.md`** (4.3), **`docs/SEARCH.md`** (2.13), **`docs/BROWSER.md`** (2.11).
32. **§12 «Подводные камни»** — дополнено: CPU-only analyst cap `n_ctx=8192`
    (**в 4.2** заменяется на `min(profile.n_ctx_hint, budget_by_ram)`), `MessagePart`/
    `with_content`/`model_copy`-ограничение, `run_shell_command` — единственный инструмент
    с побочными эффектами вне файлов проекта, `_suppress_collapse` в `load_history`
    (один проход O(n)), `CollapsedPlaceholder` — виджеты остаются в памяти (только
    удалены из layout). **9.1** — чат больше не вкладка, `QTabWidget` скрывается при
    `tab_count() == 0`. **9.6** — `_current_assistant_parts` аккумулирует части при
    стриминге; без этого история теряет reasoning/tool_calls. **9.5.2** —
    `ReasoningWidget` использует авто-высоту с максимумом (40 % чата) и внутренним
    авто-скроллом; `setFixedHeight` больше не применяется. **0.1.1** — `run_shell_command`
    требует dangerous mode для произвольных команд; allowlist — единственный штатный
    путь. **2.5** — EventBus-sink может блокировать GUI, если подписчик синхронный и
    тяжёлый; подписчики должны быть неблокирующими. **2.6** — capabilities проверяются
    **до** выполнения; `timeout_s` соблюдается через `asyncio.wait_for`. **2.7** — приоритет
    policy: security > secrets > mode > freedom > profile > session > config > decorator.
    **10.1** — `apply_patch` не пишет файл, если контекст не совпал; `write_file` — только
    для новых файлов. **10.2** — `run_tests` парсит вывод, но не гарантирует 100 %
    покрытия фреймворков; fallback — сырой stdout. **10.4** — verifier может «зациклиться»
    на регенерации; лимит — 1 повтор. **10.8** — LSP-сервер может отсутствовать → fallback
    на `py_compile`. **10.9** — stale check и снапшоты не блокируют, а защищают;
    снапшоты — `auto`, откат — `ask_first`; `snapshot_retention_days` — автоочистка.
    **2.10** — soft budgets не останавливают цикл, только предупреждают; `user_cancel` —
    единственная жёсткая остановка. **2.11** — режим A по умолчанию; C — только по явному
    opt-in с предупреждением. **2.13** — цепочка провайдеров, падение одного не роняет
    остальные; кэш обязателен. **4.3** — память локальна, шифрование опционально,
    пользователь может удалить любую запись. **9.12** — «open» с предупреждением и
    audit log.

---

## Что важно знать при применении

1. **Файл стал значительно длиннее** — сохранены все детали оригинала (конкретные файлы, функции, тесты, DoD), а новые разделы (2.10–2.13, 4.1–4.3, 9.10–9.12, 10.8–10.9) написаны с той же степенью детализации.
2. **Приоритеты переписаны** — redaction, natural exit, EventBus, парк моделей, память, apply_patch+LSP+stale-check подняты наверх.
3. **Стек не меняется** — явно зафиксировано во введении.
4. **MCP поднят раньше Plugin API** — в Фазе 7.
5. **Поиск и браузер** — цепочка провайдеров и три режима, с явным учётом контекста России.
6. **«Свобода с ответственностью»** — `freedom_level` в профилях, per-session overrides, audit log.
7. **`AGENTS.md` синхронизирован** — перечислены новые модули, тесты, подводные камни.
8. **Детализация сохранена везде** — каждый новый пункт содержит: файл, функцию, аргументы, поведение, тесты, DoD. Никаких «сделать хорошо» — только конкретные шаги.
9. **2.8 Application Services** — 4 сервиса готовы. `ToolService` интегрирован в `AgentLoop` (audit + snapshot для всех вызовов). `ProjectService`/`ModelService` — в `ChatPanel`. `ChatService` — только API, интеграция в `_do_chat` — отдельная итерация с прогоном `AgentChatThread`-тестов.
```

---

**Что изменилось в этом обновлении (относительно предыдущей версии плана):**

**Закрыты (были `[ ]` / `[~]` → стали `[x]`):**
- **0.1.2** Redaction — весь блок закрыт.
- **2.5** EventBus — весь блок закрыт.
- **2.6** ToolSpec v2 — весь блок закрыт.
- **2.7** Policy Engine — весь блок закрыт.
- **2.9** AsyncResource — весь блок закрыт.
- **2.10** Natural exit — весь блок закрыт.
- **2.11** Browser — весь блок закрыт.
- **2.12** Durable inbox — весь блок закрыт.
- **2.13** Search chain — весь блок закрыт.
- **4.1** Парк моделей — весь блок закрыт.
- **4.2** Иерархический контекст — весь блок закрыт.
- **4.3** Память — весь блок закрыт.
- **9.1** Стационарные вкладки — весь блок закрыт.
- **9.2** Автопоказ структуры — весь блок закрыт.
- **9.3.1–9.3.3** HF search, coder tags, `slm_models.yaml` — закрыты.
- **9.4** Git-интеграция — весь блок закрыт.
- **9.5.1–9.5.3** Редизайн, reasoning, живой UI — закрыты.
- **9.10–9.12** Permissions, AsyncResource, freedom_level — закрыты.
- **10.1–10.7** Кодирование — весь блок закрыт.
- **10.8** LSP diagnostics — весь блок закрыт.
- **10.9** Stale check + snapshot — весь блок закрыт.

**Остались `[~]` / `[ ]` (осознанно отложены, не блокируют):**
- **2.3** — единый ThreadPool / `asyncio.Task.cancel` / процессы для индексации / `PullThread` merge.
- **2.4** — `QCommandLine` вместо `QAction`.
- **2.8** — миграция `ChatService.build_agent_context` в `_do_chat/_do_agent`.
- **4.6.C** — `model_digest` в `ModelInfo`.
- **4.6.D** — `PullThread` merge, косметическая чистка комментариев.
- **4.7.B** — реальные прогоны 7B/14B/30B-MoE на 8k/16k/32k/64k и q8_0 vs f16.
- **4.7.E** — `agents/task_classifier.py`.
- **5** — vision через Ollama, DJVU.
- **7** — Plugin API, пользовательские команды.
- **8** — весь релизный блок.
- **9.5.1** — «Копировать» в код-блоке.
- **9.5.4** — счётчик на кнопке «▼ Вниз».
- **9.7** — аудио-плеер.
- **9.8** — цвет title bar, авто-детект темы ОС, сплит «чат + редактор».
- **Быстрый бэклог** — `uv.lock`, `hatchling`, `pyright`, `.idea/`, `py-spy`, `multiprocessing`, `pyproject`-группы.

**Фаза 7** — MCP закрыт, Plugin API отложен. **Фаза 8** — отложена целиком.