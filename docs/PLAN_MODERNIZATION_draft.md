
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
| **OCR изображений (JPG/PNG)** | ✅ | `fs/image_extractors.py`, `_attach_image` в `ChatPanel` (Фаза 5 + 9.7) |
| **Аудио/видео (STT)** | ✅ | `fs/audio_extractors.py`, `fs/video_extractors.py`, `_attach_audio`/`_attach_video` (Фаза 5 + 9.7); аудио-плеер в UI — отложен |
| **Vision-модели через Ollama** | ❌ | `qwen2.5-vl` / `llama3.2-vision` — Фаза 5 (backend), не реализовано |
| **MCP** | ✅ | `core/mcp_client.py`, `tools/mcp_ops.py`, `MCPManager` (Фаза 7) |
| **Система плагинов** | ❌ | Фаза 7 (после MCP), отложено |
| **Событийная модель сообщений (`MessagePart`)** | ✅ | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`), `state/session_store.py` (колонка `parts_json`) |
| **Композитный пузырь сообщения** | ✅ | `ui/chat/message_item.py` (`ChatMessageItem` из `ReasoningWidget` + `TextWidget` + `ToolCallCard`) |
| **Выполнение shell-команд агентом** | ✅ | `tools/shell_ops.py::run_shell_command` (allowlist + dangerous mode, audit) |
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
| **Стационарный чат без крестика (чат — не вкладка)** | ✅ | `MainWindow._create_center_panel` использует `QStackedWidget`: чат — страница 0, редактор — страница 1; `EditorWidget.tabs_changed` управляет переключением (9.1) |
| **Авто-показ структуры проекта при открытии** | ✅ | `_do_open_project`: при первом открытии проекта `show_tree = True`; при повторном — из `config.show_project_tree` (9.2) |
| **Поиск моделей на HuggingFace** | ✅ | `agents/hf_search.py`, `ui/dialogs/hf_dialog.py`, кнопка «🔍 Поиск HF» в `SLMModelsDialog` (9.3.1) |
| **Отдельный `slm_models.yaml` (модели и tier вне `config.yaml`)** | ✅ | `core/slm_models_io.py`, `SLMConfig.available_models` загружается из YAML (9.3.3) |
| **Подсветка coder-моделей в профиле «Разработка»** | ✅ | `PipelineSelectors._apply_coder_highlight`, иконки ролей (9.3.2) |
| **Git-контекст в промпте (ветка, hash, diff)** | ✅ | `AgentContext.git_context`, `ProjectService.build_git_context`, `ProjectSettings.git_branch/hash` (9.4) |
| **`git_commit` как инструмент агента** | ✅ | `tools/git_ops_tool.py::tool_git_commit` (ASK_FIRST, AGENT-only) (9.4) |
| **«Живой» UI: события из логов в интерфейсе** | ✅ | `core/event_bus.py`, `core/logger.py::_event_bus_sink`, `ui/widgets/live_events.py::LiveEventsPanel`, `ChatPanel._on_bus_event` (2.5) |
| **Natural exit + soft budgets в `AgentLoop`** | ✅ | `AgentLoop.run` — `SoftBudget`, `finish_reason=stop`, `user_cancel`, `_final_call_no_tools` (2.10) |
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
| **Превью изображений/аудио в пузыре** | ✅ | `ChatMessageItem._make_thumbnail_label` для изображений; аудио-плеер — отложен (9.7) |
| **EventBus / структурированные события** | ✅ | `core/event_bus.py`, `core/events.py`, `core/logger.py::_event_bus_sink` (2.5) |
| **ToolSpec v2: capabilities + timeout + idempotent** | ✅ | `agents/tool_registry.py::ToolSpec`, `@tool` с capabilities/timeout_s/max_output_chars/idempotent/needs_snapshot (2.6) |
| **Policy Engine (единая точка авторизации)** | ✅ | `core/policy.py::effective_policy`, `ToolRegistry.get_effective_policy`, `explain_policy` (2.7) |
| **Application Services (2.8)** | ✅ | `core/services/{tool,project,model,chat,memory}_service.py`. `ToolService` — audit + автоснапшот; `ProjectService` — кэш `ProjectIntel` + git_context; `ModelService` — конфиг + `resolve_park`/`resolve_coder`; `ChatService` — `build_agent_context` без Qt (готов к интеграции) |
| **Audit log инструментов** | ✅ | `core/audit.py` + `core/services/tool_service.py`: **все** вызовы пишутся в audit |
| **Shell allowlist (а не чёрный список)** | ✅ | `tools/shell_ops.py::_ALLOWLIST_RE` + `SecurityPolicy.allow_arbitrary_shell` + чёрный список (0.1.1) |
| **Модель за сессией / явный выбор** | ✅ | `_settings_payload` фильтрует `model` по `active_backend`; `_suppress_model_save` в `set_model` (0.1.4) |
| **Изоляция тестов ChatPanel** | ✅ | Фикстуры подавляют `_detect_backends`; `QTimer` в `ChatPanel.__init__` привязан к `self` (0.1.5) |
| **`apply_patch` (unified diff вместо полного `write_file`)** | ✅ | `tools/file_ops.py::apply_patch` (10.1) |
| **`run_tests` tool** | ✅ | `tools/test_ops.py::run_tests` (10.2) |
| **Verifier в обычном AgentLoop** | ✅ | `AgentLoop._verify_and_maybe_regen` (10.4) |
| **LSP для definition/references в coding-цикле** | ✅ | `agents/lsp_bridge.py`, `find_references` в `tools/codebase_search.py` (10.3) |
| **Coding Bench OlliDesk** | ✅ | `scripts/run_coding_bench.py`, `tests/bench/` (10.5) |
| **`ModelCatalog` YAML** | ✅ | `core/slm_models_io.py`, `~/.ollidesk/slm_models.yaml` (9.3.3) |
| **AsyncResource для Qt (idle/loading/ready/refreshing/error)** | ✅ | `ui/state/async_resource.py`, используется в `PipelineSelectors`, `SessionManagerDialog` (2.9, 9.11) |
| Тесты UI / state | ✅ | `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py`, `test_audit.py`, `test_shell_allowlist.py`, `test_reasoning_widget.py`, `test_history_save.py`, `test_history_restore.py`, `test_model_selection.py` |

---

## Фаза 0.1 — Стабилизация и безопасность (OlliDesk 2.0) — **ЗАКРЫТА**

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

- [x] **`core/redaction.py`** — единая точка вырезания секретов:
  - **`redact(text: str) -> str`** — основная функция.
  - **`_PATTERNS`** — список compiled regex:
    - OpenAI API keys, HuggingFace tokens, GitHub tokens, Slack tokens,
      Anthropic, JWT, пароли из URL, приватные ключи, env-переменные.
  - **`redact_dict(d: dict) -> dict`** — рекурсивно.
  - **`redact_exc(e: Exception) -> str`** — для исключений.
  - **`is_secret(value: str) -> bool`** — эвристика.
- [x] **Redaction в логах.** `core/logger.py::setup_logger` — loguru sink
      пропускает `message` через `redact()` перед записью в файл и stderr.
- [x] **Redaction в tool-аргументах.** `agents/tool_registry.py::execute` —
      `redact_dict` для `args` (audit пишет хеш от оригинала).
- [x] **Redaction в результатах инструментов.** `ToolResult.content` —
      перед отдачей в UI/сессии — `redact()`.
- [x] **Запрет `.env`, `.git/config`, `*.pem`, `*.key`, `id_rsa*`** в
      `read_file`/`list_directory`/`search_codebase` — через
      `core/sandbox.py::SandboxedPathResolver._DENIED_PARTS`.
- [x] **`web_search` hygiene.** `tools/web_search_hygiene.py` — `filter_query()`
      вызывает `redact()` перед отправкой в поисковик.
- [x] **`EventBus`-совместимость.** payload `log`-событий проходит через `redact()`.
- [x] **Тесты:** `tests/core/test_redaction.py` (15+ кейсов).

### 0.1.3a Reasoning 2.0 (авто-высота + авто-скролл + анимация) — `[x]`

- [x] **`ReasoningWidget._compute_max_height()`** — потолок 40 % viewport
      ближайшего `QScrollArea`, fallback на высоту окна, `_FALLBACK_MAX_HEIGHT = 480`.
- [x] **`_refresh_browser` больше не вызывает `setFixedHeight(h)`**;
      `setMaximumHeight(cap)` + рост по контенту.
- [x] **Внутренний авто-скролл к последнему токену** при `append_text`.
- [x] **`QPropertyAnimation` на `maximumHeight`** (150 мс); `_animate_height`
      обрабатывает вырожденный случай `start == target`.
- [x] **`resizeEvent`** — пересчёт cap при ресайзе окна.
- [x] **Тесты:** `tests/ui/test_reasoning_widget.py` (7 кейсов).

### 0.1.3b История 2.0 — сохранение parts — `[x]`

- [x] **`ChatPanel._current_assistant_parts` + `_current_assistant_text` +
      `_current_assistant_reasoning`** — аккумуляторы текущего ответа.
- [x] **`_on_thinking` копит reasoning независимо от `_show_reasoning`**.
- [x] **`_on_chunk` / `_on_tool_started` / `_on_tool_executed`** аккумулируют
      `TextPart` / `ToolCallPart` / `ToolResultPart`.
- [x] **`_build_assistant_message(fallback_text)`** собирает
      `ChatMessage(parts=[ReasoningPart?, TextPart?, *tool_parts])`.
- [x] **`_on_agent_finish`** (обычная и research-ветка) и **`_send_finished`**
      сохраняют через `_build_assistant_message`.
- [x] **`_on_orchestrator_result`** — `ChatMessage(parts=[TextPart(...)])`.
- [x] **Тесты:** `tests/ui/test_history_save.py` (4 кейса).

### 0.1.3c История 2.0 — восстановление — `[x]`

- [x] **`_render_assistant_parts(msg)`** — восстановление композитного пузыря
      из `msg.parts`: reasoning → text → tool_call → tool_result.
- [x] **`load_history`** использует `_render_assistant_parts` для assistant-сообщений
      с непустым `parts`; для остальных — fallback на `_add_message(role, content)`.
- [x] **Тесты:** `tests/ui/test_history_restore.py` (4 кейса).

### 0.1.4 Модель за сессией / явный выбор — `[x]`

- [x] **`_settings_payload()`** — единая точка фильтра: `model` включается в
      payload только если `active_backend == OLLAMA`.
- [x] **`set_model` под `_suppress_model_save`** — программная установка модели
      не триггерит сохранение.
- [x] **`SLMModelsDialog._activate_selected`** выключает `auto_select_model`
      при явном выборе.
- [x] **Тесты:** `tests/ui/test_model_selection.py` (6 кейсов).

### 0.1.5 Изоляция тестов ChatPanel — `[x]`

- [x] **`QTimer(self)` с `timeout.connect(...)`** — таймер привязан к `self`.
- [x] **Фикстуры ChatPanel** подавляют `_detect_backends` и вызывают `shutdown()`
      в teardown.

**DoD фазы:** shell не выполняет произвольные команды без dangerous mode;
секреты не утекают в LLM/логи; история после перезапуска содержит reasoning
и tool_calls; reasoning не обрезается; модели не «перескакивают» при явном
выборе. ✅

---

## Фаза 2 — Архитектурные улучшения

### 2.1 Протокол LLM-бэкенда — `[x]`

- [x] Ввести `agents/base.py::LLMBackend(Protocol)` с методами `chat`, `embed`,
      `cancel_request`, `is_cancelled`, `reset_cancel`.
- [x] Типизировать `OllamaClient`, `LocalLLMClient`, `RuleBasedFallback` как `LLMBackend`.
- [x] Убрать `object` / `Any` из `AgentContext.vector_store` / `.search_cache`:
      введены протоколы `VectorStoreLike`, `SearchCacheLike`, `AttachedFilesLike`.
- [x] `AgentLoop` больше не знает про `fs`/`core` по duck-типингу.

### 2.2 Реестр инструментов — `[x]`

- [x] Перенести список инструментов в `tools/registry.py::ALL_TOOLS`.
- [x] Политики — из `core/config.py::AppConfig.tool_policies`; `chat_auto_tools`
      вынесен в `AppConfig`.
- [x] Исправить баг: `search_cache` инжектится в `web_search`.
- [x] Убрать `_resolve_path`-песочницу в пользу `SandboxedPathResolver`.
- [x] Метаданные `is_mutating` / `category` в `@tool`.

### 2.3 Управление потоками и отменой — `[~]` частично

- [ ] Единый `ThreadPool`/`TaskManager` вместо независимых `QThread`-классов.
- [ ] Гарантированная отмена `asyncio`-задач (`asyncio.Task.cancel()`).
- [x] Убрать `new_event_loop()` + `run_until_complete` на GUI-потоке в
      `MainWindow._do_check_ollama`, `_save_project_state`, `_stop_all_threads`.
- [x] `GitStatusDelegate._do_refresh` переведён на `QThreadPool`.
- [~] `PullThread` в `model_manager_dialog.py` остался — объединить с `_DownloadThread`.
- [ ] Корректно отменять/удалять потоки при закрытии окна (`closeEvent`).
- [ ] **`[новое]` Долгие задачи — в отдельные процессы** (индексация, LSP).

### 2.4 Разделение `ui/main_window.py` и `ui/chat/panel.py` — `[x]`

- [x] **Меню вынесено в `ui/menus.py::AppMenuBar`**.
- [x] **Публичный API `ChatPanel`** (`update_mode_tooltips`, `update_model_list`,
      `set_mode_by_name`, `get_show_reasoning`, `set_refresh_button_text`).
- [x] **`main_window.py` разбит на контроллеры**:
  - `ui/file_tree_controller.py::FileTreeController`
  - `ui/indexing_controller.py::IndexingController` + `IndexingThread` + `ProjectState`
  - `ModelCheckThread` оставлен в `main_window.py`.
- [x] **`ui/chat/panel.py` → `ui/chat/backends.py` + `ui/chat/research.py`**.
- [ ] `QCommandLine` вместо `QAction` для меню (отложено).

`MainWindow` из «всё-в-одном» превратился в UI-оркестратор.

### 2.5 EventBus и структурированные события — `[x]`

- [x] **`core/event_bus.py::EventBus`** — синглтон с `emit`/`subscribe`.
- [x] **Схема событий** (`core/events.py`, `EventKind` + TypedDict-контракты).
- [x] **Loguru-sink** в `core/logger.py` — `_event_bus_sink`.
- [x] **Точки эмита:** `fs/vector_store.py`, `agents/agent_loop.py`,
      `agents/orchestrator.py`, `ui/indexing_controller.py`,
      `ui/chat/backends.py`, `tools/file_ops.py`, `tools/mcp_ops.py`,
      `state/inbox.py`, `core/services/memory_service.py`,
      `agents/search_providers.py`, `agents/browser_client.py`.
- [x] **UI-подписка:** `ui/widgets/live_events.py::LiveEventsPanel`.
- [x] **Индикатор «программа работает»:** `StatusDashboard` — пульсирующая точка.
- [x] **Статус-строка «по этапам»** — `_on_status_event` и `LiveEventsPanel`.
- [x] **Reasoning trace** — через `LiveEventsPanel` и `ChatThoughtStream`.
- [x] **Тесты:** `tests/core/test_event_bus.py`, `tests/ui/test_live_events.py`.

### 2.6 ToolSpec v2 и capabilities — `[x]`

- [x] **`agents/tool_registry.py::ToolSpec`** — immutable dataclass
      с `capabilities`, `timeout_s`, `max_output_chars`, `idempotent`, `needs_snapshot`.
- [x] **`@tool`** принимает `capabilities`, `timeout_s`, `max_output_chars`,
      `idempotent`, `needs_snapshot`.
- [x] **`ToolRegistry.get_spec(name) -> ToolSpec`**.
- [x] **`ToolRegistry.execute`** проверяет `capabilities` через `effective_policy`.
- [x] **Per-session overrides:** `AppConfig.session_overrides`.
- [x] **`timeout_s` соблюдается через `asyncio.wait_for`**.
- [x] **`max_output_chars`** — усечение `ToolResult.content` (реализовано в `execute`).
- [x] **`idempotent`** — повторный вызов с теми же аргументами в рамках итерации
      возвращает кэш.
- [x] **`needs_snapshot`** — перед вызовом создаётся снапшот (10.9);
      `ToolResult.metadata.snapshot_id` заполняется.
- [x] **Тесты:** `tests/agents/test_tool_spec.py`.

### 2.7 Policy Engine — `[x]`

- [x] **`core/policy.py::effective_policy(tool_spec, profile, security, config) ->
      EffectivePolicy`**.
- [x] **Приоритет:** security → profile → session → config → decorator.
- [x] **Дефолт по capabilities:** `fs.read` → `auto`; `fs.write` → `ask_first`;
      `net` → `ask_first` (кроме `get_weather`/`get_exchange_rate` — `auto`);
      `shell` → `excluded` в `research`/`learning`, `ask_first` в `development`;
      `git` → `ask_first`; `browser` → `excluded` в `safe`/`research`,
      `ask_first` в `development`, `auto` в `open`; `secrets` → `excluded` всегда.
- [x] **`confirm.web_search`** — внутри security-слоя.
- [x] **`ChatPanel._build_registry`** использует `effective_policy`.
- [x] **`ToolRegistry.execute`** вызывает `effective_policy` и возвращает
      `ToolResult(is_error=True, ...)` без выполнения.
- [x] **Per-session overrides в UI:** `ToolConfirmationDialog` — кнопка
      «Разрешить на эту сессию».
- [x] **Тесты:** `tests/core/test_policy.py`.

### 2.8 Application Services — `[x]` (кроме миграции `ChatService`)

- [x] `core/services/memory_service.py` (4.3).
- [x] `core/services/tool_service.py` — audit + автоснапшот.
- [x] `core/services/project_service.py` — кэш `ProjectIntel` + git_context.
- [x] `core/services/model_service.py` — `get_config`/`reload_config`/`resolve_park`/`resolve_coder`.
- [x] `core/services/chat_service.py` — `ChatRequestSnapshot` + `build_agent_context` + `pick_max_iterations`.
- [ ] Миграция `ChatPanel._do_chat/_do_agent` на `ChatService.build_agent_context` — отложена (готово к интеграции).
- [ ] `search_service.py` / `browser_service.py` — отменено (не нужны).
- [ ] `session_service.py` — отменено.

### 2.9 AsyncResource для Qt — `[x]`

- [x] **`ui/state/async_resource.py::AsyncResource[T]`** — guard-паттерн.
- [x] **Правило:** UI не рисует пустоту, если `state in ("loading","refreshing")`
      и есть `latest`.
- [x] **Использование:** `PipelineSelectors`, `SessionManagerDialog`.
- [x] **Тесты:** `tests/ui/test_async_resource.py`.

### 2.10 Natural exit + soft budgets — `[x]`

- [x] **`AgentLoop.run()`** — `while True:` с естественным выходом по `finish_reason=stop`.
- [x] **`SoftBudget`** — `time_s`, `tokens`, `iterations`, `warning_ratio`.
- [x] **`AgentLoop._check_budget()`** — `BudgetStatus` (`ok`/`warning`/`exceeded`).
- [x] **`AgentChatThread`** эмитит `budget_warning`/`budget_exceeded`.
- [x] **Кнопка «Продолжить»** — сбрасывает бюджеты (не удваивает лимит).
- [x] **Кнопка «Продолжить до результата»** — `time_budget=∞`, `token_budget=∞`.
- [x] **`_final_call_no_tools`** — только fallback при повторе tool_calls.
- [x] **`EventBus.emit("agent.finished")`**.
- [x] **`StatusDashboard`** показывает бюджет и причину остановки.
- [x] **`AppConfig`**: `soft_budget_time_s`, `soft_budget_tokens`, `soft_budget_iterations`.
- [x] **`_build_system_prompt`**: «Потрачено N итераций · T tok · S сек».
- [x] **Тесты:** `tests/agents/test_soft_budget.py`.

### 2.11 `browser_control` с тремя режимами — `[x]`

- [x] **`core/config.py::BrowserConfig`**.
- [x] **Режим A: «Изолированный агент»** (`IsolatedBrowser`).
- [x] **Режим B: «Расширение с явным consent»** (`ExtensionBrowser`).
- [x] **Режим C: «Полный доступ»** (`FullAccessBrowser`).
- [x] **`core/profiles.py`**: `ProfileConfig.browser_mode`.
- [x] **`tools/browser_ops.py`** — `browser_open`, `browser_snapshot`, `browser_click`,
      `browser_fill`, `browser_close`.
- [x] **`tools/registry.py::ALL_TOOLS`** — добавлены `browser_*`.
- [x] **Audit log** каждого действия.
- [x] **`EventBus.emit("browser.action")`**.
- [x] **Тесты:** `tests/agents/test_browser_client.py`, `tests/tools/test_browser_ops.py`.

### 2.12 Durable inbox — `[x]`

- [x] **`state/inbox.py::SessionInbox`** — SQLite-очередь `session_inbox`.
- [x] **`enqueue`, `claim`, `complete`, `fail`, `recover`**.
- [x] **`ChatPanel._send_message`** пишет в inbox до запуска `AgentChatThread`.
- [x] **`AgentChatThread`** забирает и помечает `running`.
- [x] **`EventBus.emit("inbox.*")`**.
- [x] **`StatusDashboard`** — индикатор очереди.
- [x] **Тесты:** `tests/state/test_inbox.py`.

### 2.13 Цепочка поиска с fallback — `[x]`

- [x] **`agents/search_providers.py`** — цепочка `SearchProvider`:
      `SearXNGProvider`, `OpenSERPProvider`, `YandexSearchAPIProvider`,
      `OllamaWebSearchProvider`, `DDGProvider`.
- [x] **`core/config.py::SearchConfig`**.
- [x] **`tools/web_search.py`** — цепочка с fallback.
- [x] **Кэш** (`SearchCache`).
- [x] **`EventBus.emit("search.provider.used")`**.
- [x] **`tools/web_search_hygiene.py`** — `filter_query()` вызывает `redact()`.
- [x] **Тесты:** `tests/agents/test_search_providers.py`, `tests/tools/test_web_search_chain.py`.

---

## Фаза 4.1 — Парк моделей — `[x]`

- [x] **`agents/model_pool.py`**: `Role` (ROUTER, PLANNER, CODER, VERIFIER,
      ANALYST, ANALYST_PRO, EMBED), `ModelPool` с LRU-вытеснением (`max_loaded=3`).
- [x] **`slm_models.yaml`** (9.3.3) с тегами.
- [x] **Qwen3-Coder-30B-A3B (MoE)** — добавлена в каталог.
- [x] **Qwen2.5-Coder-14B** — fallback для coder.
- [x] **`agents/slm_selector.py::select_park`** — выбор парка под профиль.
- [x] **`ui/widgets/pipeline_selectors.py`** — селекторы ролей, подсветка coder.
- [x] **`core/profiles.py`** — `park_roles: dict[Role, model_id]`.
- [x] **`_ModelPoolLoadThread`** — последовательная загрузка, per-role timeout 90 с.
- [x] **`EventBus.emit("slm.loaded")`**.
- [x] **Тесты:** `tests/agents/test_model_pool_park.py`.

---

## Фаза 4.2 — Иерархический контекст + prefix caching — `[x]`

- [x] **Горячее окно** (`hot_window_tokens` в `core/context_budget.py`).
- [x] **Тёплый слой** (`warm_layer_tokens`).
- [x] **`ProfileConfig.n_ctx_hint`**.
- [x] **`agents/slm_optimizer.py::optimize(..., n_ctx_hint)`** — учитывает hint.
- [x] **`SLMConfig.kv_cache_type`** — `q8_0` для CPU-analyst.
- [x] **Prefix caching** в `LocalLLMClient` — `cache_reads`/`cache_writes` в `last_stats`.
- [x] **Компактор** — `hot_window_tokens` не режет горячее окно.
- [x] **`core/context_budget.py`** — `rag_char_budget`, `hot_window_tokens`, `warm_layer_tokens`.
- [x] **Тесты:** `tests/agents/test_hierarchical_context.py`.

---

## Фаза 4.3 — Эпизодическая память — `[x]`

- [x] **`~/.ollidesk/memory.db`** (SQLite + FTS5).
- [x] **`core/services/memory_service.py::MemoryService`** — `remember`, `recall`,
      `forget`, `list_recent`, `set_preference`, `get_preference`.
- [x] **Извлечение:** top-3 релевантных записи через FTS5, подмешиваются в
      `_build_system_prompt`.
- [x] **Запись:** после успешного завершения задачи — короткая рефлексия.
- [x] **Приватность:** всё локально, шифрование опционально.
- [x] **`EventBus.emit("memory.*")`**.
- [x] **`ui/dialogs/memory_dialog.py`**.
- [x] **Тесты:** `tests/core/test_memory_service.py`.

---

## Фаза 4.6 — Надёжность ReAct-агента — `[x]`

### 4.6.B — Потом: качество ответов

- [x] Step-awareness — `_build_system_prompt` с `iter_line` и soft budget.
- [x] Не бросать `AgentIterationLimitError`, а делать финальный вызов без tools.
- [x] Валидация Observation — `attempted_calls` / `successful_observations`.
- [x] Классификация ошибок инструментов — `ErrorCategory` и `retry_hint`.
- [x] Внешний верификатор после `write_file` — `_verify_python_syntax`.
- [x] История tool-сообщений в сессиях — `parts_json`.
- [x] `slm.n_ctx: 4096` → 8192.
- [x] Подключить сжатие истории — `history_compactor`.
- [x] `ChatMessageItem` рендерит `tool_calls` — композитный пузырь.
- [x] Кнопка «Продолжить» после исчерпания итераций.
- [x] `_CHAT_AUTO_TOOLS` в конфиг — `AppConfig.chat_auto_tools`.
- [x] Бюджет RAG-контекста — `rag_char_budget` и `truncate_to_chars`.
- [x] Audit log — `core/audit.py`.

### 4.6.C — Позже: новые подсистемы

- [x] Reflexion-критик для Final Answer — `agents/critic.py`, `agents/verifier.py`.
- [x] Structured trace агента — `last_research_summary`, `ChatResearchPassport`, `LiveEventsPanel`.
- [x] Метрики реестра — `ToolRegistry._stats` (отложено отображение).
- [x] Эпизодическая память — `core/services/memory_service.py`.
- [x] Типизированное редактирование аргументов — `ToolConfirmationDialog._coerce_value`.
- [ ] `model_digest` в `ModelInfo` — отложено.
- [x] Warning о `n_gpu_layers` в UI — `StatusDashboard` (tooltip).
- [x] Multi-tool-call у SLM — `LocalLLMClient._parse_tool_calls`.

### 4.6.D — Удалить

- [x] `RouterChatThread` — удалён.
- [~] `PullThread` — оставлен, объединить с `_DownloadThread` (отложено).
- [~] Закомментированные блоки — почистить (косметически).

### 4.6.E — Решения

- [x] `agents/context_compactor.py` — удалён.
- [~] Порядок работ — частично пересмотрен.

---

## Фаза 4.7 — Выбор бэкендов и локальных моделей

### 4.7.B — Замеры — `[x]`

- [x] Инструмент замеров — `agents/benchmark.py`, `ui/dialogs/benchmark_dialog.py`,
      `_BenchmarkThread`.
- [x] Проверка повторной загрузки — `LoadTimeRow`.
- [ ] Прогон 7B/14B/30B-MoE на 8k/16k/32k/64k — отложено (нужен запуск).
- [ ] Прогон с `kv_cache_type=q8_0` vs `f16` — отложено.

### 4.7.D — ModelPool — `[x]`

- [x] `ModelPool` с ролями.
- [x] `max_loaded=3`.
- [x] `n_gpu_layers` пересчитывается per-модель.

### 4.7.E — Классификатор — `[~]`

- [~] `ModelPool.pick_role` — простейший rule-based.
- [ ] `agents/task_classifier.py` — отложено.

### 4.7.F — Отклонено

- [ ] LoRA-адаптеры, Self-RAG, дообучение, Multi-Agent — отклонено.
- [ ] «Переключение за 100 мс» — нереалистично.

---

## Фаза 4.8 — Событийная модель сообщений и агентный интерфейс — `[x]`

### 4.8.E — AgentLoop и контекст

- [x] `AgentLoop` обрабатывает события — dispatch по `isinstance(part, ...)`.
- [x] Базовый приоритет сжатия в компакторе.
- [x] Первая ступень — `ReasoningPart`.
- [x] Стриминг SLM (`LocalLLMClient._generate`).

---

## Фаза 5 — Мультимодальность: backend — `[~]` (кроме vision)

- [x] Распознавание скриншотов (`fs/image_extractors.py`).
- [ ] Vision-модели через Ollama — отложено.
- [x] Аудио (`fs/audio_extractors.py`).
- [x] Видео (`fs/video_extractors.py`).
- [x] Распознавание документов (`fs/document_extractors.py`).
- [x] OCR сканов PDF.
- [x] База мультимедиа-экстрактов (`fs/media_cache.py`).
- [ ] PDF/DJVU: DJVU не реализован.

> Генерация изображений — вне объёма.

---

## Фаза 7 — Расширяемость — `[~]`

- [x] **MCP-клиент** (stdio):
  - `core/mcp_client.py::MCPClient`.
  - `tools/mcp_ops.py`.
  - `core/config.py::MCPConfig`.
  - `EventBus.emit("mcp.*")`.
  - Policy Engine проверяет capabilities.
  - Audit log.
  - Тесты: `tests/core/test_mcp_client.py`, `tests/tools/test_mcp_ops.py`.
- [ ] **Plugin API** (после MCP) — отложено.
- [x] **Permissions Dashboard** (9.10).
- [ ] Пользовательские команды (`~/.ollidesk/commands/*.md`) — отложено.
- [ ] Внешние провайдеры LLM — не планируется.

---

## Фаза 8 — Релиз и качество — `[~]` отложено

- [x] Единая версия из `pyproject.toml`.
- [ ] `CHANGELOG.md` генерируется и коммитится на каждый релиз — отложено.
- [ ] CI: `windows-latest` — отложено.
- [ ] Pre-commit — отложено.
- [ ] Релизный пайплайн — отложено.
- [ ] Проверить `ollidesk.spec` — отложено.
- [ ] README — отложено.

---

## Фаза 9 — UX/UI — `[x]` (кроме мелких отложенных)

### 9.1 Стационарные вкладки и навигация — `[x]`

- [x] Чат — не вкладка, а базовый экран.
- [x] `QTabBar` только для файлов.
- [x] Авто-скрытие `QTabWidget` при `tab_count() == 0`.
- [x] «💬 Чат» как кнопка-переключатель.
- [x] `EditorWidget.tabs_changed(int)`.
- [x] F4 работает независимо.

### 9.2 Структура проекта: автопоказ и F4 — `[x]`

- [x] При открытии проекта через `File → Открыть проект` — структура показывается.
- [x] При открытии через `@dir` — структура показывается.
- [x] `File → Открыть недавний` — то же.
- [x] F4 скрывает/показывает; состояние персистится.
- [x] Логика первого открытия.

### 9.3 Модели: поиск на HF, отдельный конфиг, подсветка coder — `[x]`

- [x] **9.3.1 Поиск моделей на HuggingFace** — `agents/hf_search.py`,
      `ui/dialogs/hf_dialog.py`, кнопка «🔍 Поиск HF».
- [x] **9.3.2 Подсветка coder-моделей** — `PipelineSelectors._apply_coder_highlight`,
      иконки ролей, бейдж freedom.
- [x] **9.3.3 `slm_models.yaml`** — `core/slm_models_io.py`, миграция, редактирование.

### 9.4 Git-интеграция в режиме разработки — `[x]`

- [x] `tools/git_ops.py`: `get_current_branch`, `get_branch_hash`,
      `get_uncommitted_diff`, `get_branch_diff`, `get_recent_commits`, `commit`.
- [x] `state/project_settings.py`: `git_branch`, `git_hash`, `git_last_sync`.
- [x] `AgentContext.git_context`.
- [x] `git_commit` инструмент.
- [x] Запрет по умолчанию.
- [x] Точечные изменения.
- [x] `StatusDashboard` — git-ветка.
- [x] `EventBus.emit("git.*")`.
- [x] Тесты.

### 9.5 Чат: редизайн и «живой» UI — `[x]` (кроме 9.5.1 копировать в код-блоке, 9.5.4 счётчик на кнопке)

- [x] **9.5.1 Визуальный редизайн пузыря** — единый стиль, tool-карточки, тёмная/светлая тема.
- [x] **9.5.2 Reasoning-виджет** — авто-высота, авто-скролл, анимация.
- [x] **9.5.3 «Живой» UI** — EventBus, LiveEventsPanel, индикатор, статусы, бюджет.
- [~] **9.5.4 Прочие UX-фиксы** — кнопка «Стоп» заметнее, индикатор «прокручено вверх»,
      markdown-таблицы, «Откатить», бейдж «Снапшот». Копировать в код-блоке — отложено.

### 9.6 История и восстановление сессий — `[x]`

- [x] Фикс 1 — аккумулировать parts при стриминге.
- [x] Фикс 2 — research-режим.
- [x] Фикс 3 — `load_history`.
- [x] Фикс 4 — `_send_finished`.
- [x] Тест: `tests/ui/test_history_restore.py`.

### 9.7 Мультимодальность в UI — `[x]` (кроме аудио-плеера)

- [x] Drag&drop в поле ввода.
- [x] Кнопка «📎 Скрепка».
- [x] Превью изображений в пузыре.
- [x] Индикатор распознавания (OCR/STT).
- [ ] Аудио-плеер — отложено.
- [x] Мультимедиа-кэш.
- [x] Превью документа.
- [x] `EventBus.emit("media.extracted")`.

### 9.8 Темы, прогресс, остальное — `[x]` (мелкие отложены)

- [~] Цвет title bar в тёмной теме — отложено.
- [~] Авто-детект темы ОС — отложено.
- [x] Прогресс индексации — ETA, очередь, «Отменить».
- [~] Сплит «чат + редактор» — отложено.
- [x] Метрики инструментов в статус-бар.
- [x] Warning о `n_gpu_layers`.
- [x] Индикатор git-ветки.
- [x] Кэш web-поиска.
- [x] Prefix caching.
- [x] Бюджет.
- [x] Причина остановки.
- [x] Индикатор очереди inbox.
- [x] Индикатор памяти.
- [x] Индикатор поиска.
- [x] Индикатор браузера.
- [x] Индикатор MCP.
- [x] Индикатор freedom_level.

### 9.9 Порядок работ в Фазе 9 — `[x]` выполнен.

### 9.10 Permissions Dashboard — `[x]`

- [x] `ui/dialogs/permissions_dashboard.py`.
- [x] Per-session overrides.
- [x] `EventBus.emit("permissions.overridden")`.
- [x] Тесты.

### 9.11 AsyncResource в UI — `[x]`

- [x] `PipelineSelectors`.
- [x] `SessionManagerDialog`.
- [x] `FileTreeController` (частично).
- [x] `ChatPanel` для RAG-источников.
- [x] `SLMModelsDialog`.
- [x] Тесты.

### 9.12 Режимы свободы в профилях — `[x]`

- [x] `ProfileConfig.freedom_level`.
- [x] Матрица.
- [x] «open» с предупреждением.
- [x] `ProfilesSelector` — бейдж.
- [x] `core/policy.py::effective_policy` учитывает `freedom_level`.
- [x] `ProfileConfig.tool_policies` переопределяет.
- [x] `ProfileEditorDialog` — селектор.
- [x] `EventBus.emit("profile.freedom.changed")`.
- [x] Тесты.

---

## Фаза 10 — Кодирование и проверяемые инструменты — `[x]`

### 10.1 `apply_patch` — `[x]`

- [x] `tools/file_ops.py::apply_patch`.
- [x] Промпт-правило.
- [x] `_TOOL_USE_INSTRUCTION`.
- [x] `ToolCallCard._render_diff_mode`.
- [x] Тесты.

### 10.2 `run_tests` tool — `[x]`

- [x] `tools/test_ops.py::run_tests`.
- [x] `ProfileConfig.run_tests_after_edit`.
- [x] `_TOOL_USE_INSTRUCTION`.
- [x] `EventBus.emit("tool.finished")`.
- [x] `ToolCallCard`.
- [x] Тесты.

### 10.3 LSP для coding-цикла — `[x]`

- [x] `agents/lsp_bridge.py`.
- [x] `find_references`.
- [x] `_TOOL_USE_INSTRUCTION`.
- [x] Fallback без LSP.
- [x] `EventBus.emit`.
- [x] Тесты.

### 10.4 Verifier в обычном AgentLoop — `[x]`

- [x] `agents/verifier.py`.
- [x] Coding-режим.
- [x] Лимит регенерации.
- [x] `EventBus.emit("agent.verify")`.
- [x] Тесты.

### 10.5 Coding Bench OlliDesk — `[x]`

- [x] `tests/bench/`.
- [x] `scripts/run_coding_bench.py`.
- [x] Цель.
- [x] Smoke-test в CI (частично).
- [x] `docs/CODING_BENCH.md`.
- [x] Тесты.

### 10.6 Model routing для кодинга — `[x]`

- [x] Профиль «Разработка» → coder-модель.
- [x] Planner 3B GPU / Coder 7B–30B CPU.
- [x] Fallback.
- [x] `_TOOL_USE_INSTRUCTION`.
- [x] `EventBus.emit("model.role.selected")`.
- [x] Тесты.

### 10.7 Coding-цикл в Orchestrator — `[x]`

- [x] `Orchestrator.run` для coding-задач.
- [x] `fix_loop`.
- [x] `RunResult` расширен.
- [x] `EventBus.emit("orchestrator.event")`.
- [x] Тесты.

### 10.8 LSP-диагностика после `apply_patch`/`write_file` — `[x]`

- [x] `tools/file_ops.py` вызывает `LspBridge.diagnostics`.
- [x] Ошибки → Observation.
- [x] `ToolResult.metadata.lsp_errors`.
- [x] `EventBus.emit("tool.finished")`.
- [x] `ToolCallCard` — красный блок.
- [x] Fallback.
- [x] `agents/lsp_bridge.py::diagnostics`.
- [x] `EventBus.emit("lsp.diagnostics")`.
- [x] Тесты.

### 10.9 Stale check + автоснапшот — `[x]`

- [x] Stale check в `apply_patch`.
- [x] Автоснапшот.
- [x] `ToolResult.metadata.snapshot_id`.
- [x] `ToolCallCard` — кнопка «↩ Откатить».
- [x] `EventBus.emit("snapshot.created")`.
- [x] Политика.
- [x] `ProfileConfig.snapshot_retention_days`.
- [x] `EventBus.emit("snapshot.cleaned")`.
- [x] Тесты.

---

## Приоритеты и порядок (обновлено)

**OlliDesk 2.0 — приоритеты сверху вниз (обновлено).**

1. **Фаза 0.1 — Стабилизация и безопасность** — ✅ закрыта.
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
14. **2.8 Application Services** — ✅ (4 сервиса, `ChatService` ждёт миграции).
15. **Фаза 7 Plugin API** — отложено.
16. **Фаза 5 + 9.7 Мультимодальность** — ✅ (кроме vision и аудио-плеера).
17. **Фаза 8 Релиз и качество** — отложено.

---

## Быстрый бэклог (мелочи)

- [x] `ruff` вместо `black`/`isort`.
- [ ] **`uv.lock` рассинхронизирован** — отложено.
- [ ] `pyproject.toml` → `hatchling` — отложено.
- [ ] `.gitignore` игнорирует `Notes.md`/`Roadmap.md`/`Model.md` — отложено.
- [ ] Добавить `pyright`/`basedpyright` — отложено.
- [ ] Убрать `.idea/` из репозитория — отложено.
- [x] **Кэш web-поиска: расширить на RAG-ответы** — сделано.
- [x] **Целевая версия Python** — `>=3.12`.
- [x] **Чтение больших файлов фрагментами** — `read_file(path, offset, limit)`.
- [x] **Опциональные зависимости `[docs]`** — `pdfplumber`, `pypdf`, `python-docx`,
      `python-pptx`, `openpyxl`, `striprtf`, `pdf2image`.
- [ ] **`[новое]`** `py-spy` в CI — отложено.
- [ ] **`[новое]`** `multiprocessing`/`QProcess` для индексации — отложено.
- [ ] **`[новое]`** `pyproject.toml`: группы `bench`, `search`, `browser` — отложено.

---

## Что было удалено (для справки)

- **Фаза 1** — завершена.
- **Фаза 3** — RAG завершена.
- **Фаза 4** — Контекст и промпты завершены.
- **4.6.A** — базовая надёжность ReAct.
- **4.7.A** — `VectorStore` через `Embedder`-протокол.
- **4.7.C** — стратегии выбора моделей «поглощены профилями (9.4)».
- **4.8.A** — `MessagePart`-модель.
- **4.8.B** — композитный пузырь.
- **4.8.C** — `run_shell_command` заменён на allowlist + dangerous mode (0.1.1).
- **4.8.D** — метаданные `is_mutating`/`category` в `@tool`.

**Новые архивные записи (OlliDesk 2.0):**
- ✅ **0.1.1–0.1.5** — закрыты.
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
3. **`ui/chat/panel.py`** → миксины `BackendsMixin` (`ui/chat/backends.py`) + `ResearchMixin` (`ui/chat/research.py`).
4. **`ui/chat/message_item.py`** — `ToolCallCard` с diff-режимом, `_truncate_preview`, `_escape_diff`; reasoning-виджет переработан (0.1.3); бейдж «Откатить» (10.9), бейдж «LSP» (10.8).
5. **`fs/vector_store.py`** — `search(categories=...)`, EventBus-эмиты `rag.search.*`.
6. **`agents/history_compactor.py`** — три ступени; `hot_window_tokens`.
7. **`agents/tool_registry.py`** — `ErrorCategory`, `ToolResult.error_category`, `ToolResult.metadata`, `ToolSpec`, `effective_policy`, `metadata.snapshot_id`.
8. **`tools/shell_ops.py`** — allowlist + dangerous mode, audit log.
9. **`state/session_store.py`** — `parts_json` + миграция.
10. **`ui/main_window.py`** — контроллеры, `_OllamaCheckThread`, `_stop_all_threads` без `new_event_loop`, `_toggle_backend`, `_on_project_tree_toggled`, `_on_ollama_checked`, `_toggle_side_panel` → `AgentSettingsDialog`, `_open_benchmark`, `_open_profile_editor`, `_on_show_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_export_chat`, `_show_file_diff`.
11. **`ui/chat/threads.py`** — `_BenchmarkThread`, `AgentChatThread.tool_executed` с `metadata`, `_ModelPoolLoadThread` с timeout 90 с; `budget_warning`.
12. **`ui/dialogs/tool_confirmation_dialog.py`** — `_coerce_value`, `QTableWidget`-редактор, кнопка «Разрешить на сессию».
13. **`core/sandbox.py`** — `SandboxedPathResolver`; расширенные `_DENIED_PARTS`.
14. **`core/config.py`** — `profiles`, `active_profile`, `show_project_tree`, `show_pipeline_selectors`, `open_modified_as_diff`, `index_documents`, `max_document_size_mb`, `document_ocr`; `_GLOBAL_ONLY_KEYS` расширен; `_sync_builtin_profiles`. `soft_budget_*`; `BrowserConfig`; `SearchConfig`; `available_models` переезжает в `slm_models.yaml`; `freedom_level`.
15. **`core/profiles.py`** (новый) — `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES`, `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `recommended_model`, `park_roles`, `coder_models`, `git_commit`, `freedom_level`, `snapshot_retention_days`.
16. **`core/chat_commands.py`** (новый) — реестр `@`-команд.
17. **`core/chat_export.py`** (новый) — `export_to_markdown`, `export_to_html`.
18. **`fs/document_extractors.py`** (новый) — `extract_document`, `is_document`, `language_for_document`.
19. **`agents/benchmark.py`** (новый) — `run_benchmark`, `BenchmarkPrompt`, `BenchmarkResult`, `save_csv`, `to_markdown`.
20. **`tools/git_ops.py`** — `read_file_at_head`, `get_git_status_for_file`, `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`, `get_branch_diff`, `get_recent_commits`, `commit`, `create_snapshot`/`undo_snapshot`.
21. **`ui/widgets/`** (новые) — `auto_scroll.py`, `status_dashboard.py`, `pipeline_selectors.py`, `profiles_selector.py`, `command_completer.py`, `command_palette.py`, `collapsed_placeholder.py`, `live_events.py`.
22. **`ui/command_registry.py`** (новый) — `CommandRegistry`, `PaletteCommand`.
23. **`ui/dialogs/`** — новые: `agent_settings_dialog.py`, `benchmark_dialog.py`, `document_preview_dialog.py`, `export_chat_dialog.py`, `profile_editor_dialog.py`, `permissions_dashboard.py`, `memory_dialog.py`.
24. **`ui/theme.py`** — полностью переработан: токены, `_build_qss`, `get_colors`, `get_stylesheet`.
25. **`ui/menus.py`** — добавлены сигналы/пункты: `export_chat_requested`, `profile_selected`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`, `permissions_requested`.
26. **§4 «Пакеты»** — синхронизировано с текущим кодом.
27. **§5 «Пайплайн»** — research-вопросы уходят в `Orchestrator`, обычные — в `AgentLoop`; fallback; natural exit; EventBus; coding-флоу.
28. **§6 «Соглашения»** — `requires-python >= 3.12`, `typing.override`.
29. **§7 «Как добавить инструмент»** — `ALL_TOOLS`; `git_commit`, `apply_patch`, `run_tests`, `find_references`, `browser_*`, `web_search` chain.
30. **§9 «Тесты»** — актуальный список покрытых модулей; план на новые тесты (большинство уже реализовано).
31. **§10 «Документация»** — добавлены `docs/SECURITY.md`, `docs/BENCHMARK.md`, `docs/CODING_BENCH.md`, `docs/MEMORY.md`, `docs/SEARCH.md`, `docs/BROWSER.md`.
32. **§12 «Подводные камни»** — дополнено: CPU-only analyst cap (в 4.2 заменён на `n_ctx_hint`), `MessagePart`, `run_shell_command` (dangerous mode), `_suppress_collapse`, `CollapsedPlaceholder`, чат не вкладка, аккумуляция parts, авто-высота reasoning, EventBus-sink, capabilities до выполнения, приоритет policy, `apply_patch`, `run_tests`, verifier, LSP, stale check, soft budgets, browser, search chain, память, freedom_level.
```