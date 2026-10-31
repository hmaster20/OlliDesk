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
| **OCR изображений (JPG/PNG)** | ⚠️ | `rapidocr-onnxruntime` есть, подключение к drag&drop картинок — Фаза 5 (backend) + 9.7 (UI) |
| **Аудио/видео (STT)** | ❌ | `faster-whisper` + ffmpeg — Фаза 5 (backend) + 9.7 (UI) |
| **Vision-модели через Ollama** | ❌ | `qwen2.5-vl` / `llama3.2-vision` — Фаза 5 (backend) |
| **MCP** | ❌ | Фаза 7 (приоритет поднят) |
| **Система плагинов** | ❌ | Фаза 7 (после MCP) |
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
| **Natural exit + soft budgets в `AgentLoop`** | ❌ | Жёсткий `max_iterations` (2.10) |
| **Парк моделей (router/planner/coder/verifier)** | ⚠️ | Только 2 роли в `ModelPool`; Qwen3-Coder/MoE не поддержан (4.1) |
| **Иерархический контекст + prefix caching** | ❌ | `n_ctx` статичен, prefix caching не включён (4.2, 9.8) |
| **Эпизодическая память** | ❌ | Нет `memory.db` (4.3) |
| **MCP-клиент** | ❌ | Фаза 7 (приоритет поднят) |
| **Цепочка поиска с fallback** | ⚠️ | Только DDG; нет SearXNG/OpenSERP/Yandex/Ollama (2.13) |
| **`browser_control` с 3 режимами** | ❌ | Нет (2.11) |
| **Durable inbox** | ❌ | Запросы не переживают перезапуск (2.12) |
| **LSP-диагностика после edit** | ⚠️ | `_verify_python_syntax` (py_compile) есть; LSP — нет (10.8) |
| **Stale check + автоснапшот** | ❌ | `apply_patch` не проверяет hash/mtime (10.9) |
| **Режимы свободы в профилях** | ❌ | Нет `freedom_level` (9.12) |
| **Auto-height reasoning-виджет** | ✅ | `ReasoningWidget._compute_max_height()` — авто-высота с потолком 40 % viewport, внутренний авто-скролл, `QPropertyAnimation` (0.1.3a) |
| **Восстановление истории с reasoning/tool_calls** | ✅ | `_build_assistant_message` (`0.1.3b`) + `_render_assistant_parts` (`0.1.3c`); `ChatMessage(parts=[...])` сохраняется в `SessionStore` |
| **Drag&drop файлов в чат** | ❌ | Работают только `@file`/`@dir`/`@path` (9.7) |
| **Превью изображений/аудио в пузыре** | ❌ | `ChatMessage.images` не используется в UI (9.7) |
| **EventBus / структурированные события** | ❌ | Нет `core/event_bus.py` (2.5) |
| **ToolSpec v2: capabilities + timeout + idempotent** | ❌ | `@tool` знает только `name`/`description`/`policy`/`modes`/`is_mutating`/`category` (2.6) |
| **Policy Engine (единая точка авторизации)** | ❌ | Политики раскиданы по `_apply_config_policies`, `ProfileToolPolicies`, декоратору (2.7) |
| **Audit log инструментов** | ✅ | `core/audit.py::AuditLog` → `~/.ollidesk/audit.db`: timestamp, tool, args_hash, policy, approved, result_hash, error, dangerous (0.1.1) |
| **Shell allowlist (а не чёрный список)** | ✅ | `tools/shell_ops.py::_ALLOWLIST_RE` + `SecurityPolicy.allow_arbitrary_shell` (dangerous mode) + чёрный список сохраняется (0.1.1) |
| **Модель за сессией / явный выбор** | ✅ | `_settings_payload` фильтрует `model` по `active_backend`; `_suppress_model_save` в `set_model` (0.1.4) |
| **Изоляция тестов ChatPanel** | ✅ | Фикстуры подавляют `_detect_backends`; `QTimer` в `ChatPanel.__init__` привязан к `self` (0.1.5) |
| **`apply_patch` (unified diff вместо полного `write_file`)** | ❌ | `write_file` перезаписывает файл целиком (10.1) |
| **`run_tests` tool** | ❌ | Нет инструмента запуска тестов (10.2) |
| **Verifier в обычном AgentLoop** | ⚠️ | `agents/verifier.py` есть только для Orchestrator (10.4) |
| **LSP для definition/references в coding-цикле** | ⚠️ | `agents/lsp_bridge.py` есть, но не используется в `ProjectIntel` (10.3) |
| **Coding Bench OlliDesk** | ❌ | Нет набора тестовых задач и метрик pass@1 (10.5) |
| **`ModelCatalog` YAML** | ❌ | `slm_models.yaml` ещё нет (9.3.3) |
| **AsyncResource для Qt (idle/loading/ready/refreshing/error)** | ❌ | Guard Suspense-паттерна в Qt не формализован (2.9) |
| Тесты UI / state | ✅ | `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py` |

---

## Фаза 0.1 — Стабилизация и безопасность (OlliDesk 2.0)

**Фаза почти закрыта.** Реализованы 0.1.1, 0.1.3a/b/c, 0.1.4, 0.1.5.
Осталось — 0.1.2 (redaction секретов). **0.1.2 поднят в приоритете** — без него
«свобода агента» (shell, web, browser) = утечка. До завершения 0.1.2 безопасно
считать, что секреты могут утекать в логи/LLM; shell уже защищён.

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
      `run_shell_command` и `web_search` (последний раньше всегда работал с
      дефолтной `SecurityPolicy()` — теперь исправлено).
- [x] **`ui/dialogs/agent_settings_dialog.py`** — чекбокс «Разрешить
      произвольные shell-команды (dangerous mode)».
- [x] **Тесты:** `tests/core/test_audit.py` (6 кейсов), `tests/tools/test_shell_allowlist.py`
      (8 кейсов: allowlist принимает/отклоняет, dangerous mode разрешает,
      dangerous mode блокирует blacklist, пустая команда, без project_root,
      успешный вызов пишется в audit как `approved=True`).

### 0.1.2 Redaction секретов — `[ ]` (**приоритет поднят**)

**Почему приоритет поднят:** без redaction «свободный» агент (shell, web,
browser) отправляет секреты в LLM и логи. Это не блокировка, а гигиена —
условие, при котором свобода безопасна.

- [ ] **`core/redaction.py`** — единая точка вырезания секретов:
  - **`redact(text: str) -> str`** — основная функция.
  - **`_PATTERNS`** — список compiled regex:
    - OpenAI API keys: `sk-[A-Za-z0-9]{20,}`, `sk-proj-[A-Za-z0-9_-]{20,}`.
    - HuggingFace tokens: `hf_[A-Za-z0-9]{30,}`.
    - GitHub tokens: `ghp_[A-Za-z0-9]{36}`, `gho_`, `ghu_`, `ghs_`, `ghr_`.
    - Slack tokens: `xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, `xoxs-`.
    - Anthropic: `sk-ant-[A-Za-z0-9_-]{20,}`.
    - JWT: `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`.
    - Пароли из URL: `://[^:]+:([^@]+)@` → `://***:***@`.
    - Приватные ключи: `-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]+?-----END .*?PRIVATE KEY-----`.
    - Env-переменные: `(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|auth)\s*[:=]\s*["']?([^\s"']{8,})` → `\1=***`.
  - **`redact_dict(d: dict) -> dict`** — рекурсивно по ключам/значениям.
  - **`redact_exc(e: Exception) -> str`** — для исключений.
  - **`is_secret(value: str) -> bool`** — эвристика для быстрой проверки.
- [ ] **Redaction в логах.** `core/logger.py::setup_logger` — loguru sink
      пропускает `message` через `redact()` перед записью в файл и stderr.
      Тест: `logger.info(f"token={fake_sk_key}")` → в файле `token=***`.
- [ ] **Redaction в tool-аргументах.** `agents/tool_registry.py::execute` —
      перед отправкой `args` в LLM (для confirmation dialog) и в audit log
      (`args_hash` считается от **оригинала**, а не от redacted) — `redact_dict`.
- [ ] **Redaction в результатах инструментов.** `ToolResult.content` —
      перед отдачей в UI/сессии — `redact()`. Исключение: `read_file` для
      `.env` (если пользователь явно разрешил через capability `secrets` —
      откладывается до 2.6).
- [ ] **Запрет `.env`, `.git/config`, `*.pem`, `*.key`, `id_rsa*`** в
      `read_file`/`list_directory`/`search_codebase` — через расширение
      `core/sandbox.py::SandboxedPathResolver._DENIED_PARTS`.
      Полный доступ — через capability `secrets` (откладываем до Фазы 2.6).
- [ ] **`web_search` hygiene.** `tools/web_search_hygiene.py` — расширить
      паттерны под `.env`-переменные и JWT; `filter_query()` вызывает
      `redact()` перед отправкой в поисковик.
- [ ] **`EventBus`-совместимость.** Если `EventBus` эмитит `log`-события
      (2.5) — payload тоже проходит через `redact()`.
- [ ] **Тесты:** `tests/core/test_redaction.py` (15+ кейсов):
      - API-ключи (OpenAI, HF, GitHub, Slack, Anthropic).
      - JWT.
      - Пароли из URL.
      - Приватные ключи (RSA, EC, OPENSSH).
      - Env-переменные с секретными именами.
      - `redact_dict` рекурсивно.
      - `is_secret`.
      - Ложные срабатывания: обычный текст с `token` в предложении не режется.
      - Логи: `logger.info` → файл содержит `***`.
      - Tool-результат: `read_file` с ключом → `***`.
      - `args_hash` считается от оригинала.

**DoD 0.1.2:** ни один секрет не попадает в логи, LLM-промпт или UI;
`.env`/`*.pem`/`*.key`/`id_rsa*` недоступны без явного capability.

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
      `self` и отменяется при `deleteLater()`/`shutdown()`. Раньше статическая
      форма таймера выстреливала даже после удаления панели и стартовала
      реальную `Llama()` (захват CUDA), ломая QWebEngine-тесты (чёрный экран,
      зависание `make test`).
- [x] **Фикстуры ChatPanel** в `tests/ui/test_history_save.py`,
      `tests/ui/test_history_restore.py`, `tests/ui/test_model_selection.py`
      подавляют `_detect_backends` и вызывают `shutdown()` в teardown
      через `contextlib.suppress`.

**DoD фазы (после 0.1.2):** shell не выполняет произвольные команды без
dangerous mode; секреты не утекают в LLM/логи; история после перезапуска
содержит reasoning и tool_calls; reasoning не обрезается; модели не
«перескакивают» при явном выборе.

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

### 2.5 EventBus и структурированные события (OlliDesk 2.0) — **приоритет поднят**

**Боль:** «логи информативнее UI, пользователь не понимает, почему программа не отвечает».

- [ ] **`core/event_bus.py`**:
  - `EventBus(QObject)` — синглтон с сигналом `event_emitted(str, dict)`.
  - `EventBus.emit(kind: str, payload: dict)`.
  - `EventBus.subscribe(kind: str, callback) -> Unsubscribe`.
  - `EventBus.clear()` — для тестов.
  - **`seq`** — монотонный счётчик (`itertools.count()`), добавляется в payload
    как `payload["_seq"]`; для трейса (event sourcing-light). Монотонность
    проверяется в `tests/core/test_event_bus.py`.
- [ ] **Схема событий** (`core/events.py`, Pydantic-модели):
  - `rag.search.started` / `rag.search.found` / `rag.search.empty`
    (поля: `query`, `num_results`, `duration_ms`).
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
  - `orchestrator.event` (уже есть `on_event` в `Orchestrator.run` — дублировать в EventBus).
- [ ] **Loguru-sink** в `core/logger.py`: для `level >= INFO` эмитит
      `EventBus.event_emitted("log", {level, message, source, timestamp})`.
      Все текущие `logger.info`-строки становятся видимыми в UI без переписывания
      модулей. **Важно:** sink не блокирует GUI — `EventBus.emit` только
      кладёт в очередь, подписчики обрабатывают в своём потоке.
- [ ] **Точки эмита:**
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
- [ ] **UI-подписка:** `ui/widgets/live_events.py::LiveEventsPanel` (сворачиваемая
      панель под чатом, высота 120 px, авто-скролл, фильтр «только важное»,
      кнопка «Очистить»). `ChatPanel._setup_ui` создаёт панель и подписывается
      на EventBus.
- [ ] **Индикатор «программа работает»:** `StatusDashboard` — пульсирующая точка
      (`QTimer 500 мс`) пока `_generating=True`; tooltip — последнее событие
      из EventBus.
- [ ] **Статус-строка «по этапам»** вместо «🤔 Думает…»: `🔍 Поиск по коду →
      📖 Чтение 3 файлов → 🧠 Генерация (TTFT 1.2 с) → ✍️ 42 tok/s`. Источник — EventBus.
- [ ] **Reasoning trace:** из существующих данных (`Critique`, `VerifyResult`,
      tool metadata) генерировать объяснение решений («выбрал `apply_patch`,
      потому что файл существует и контекст совпал»). Показывать в
      `LiveEventsPanel` или раскрывающемся трейсе `ChatThoughtStream`.
- [ ] **Тесты:** `tests/core/test_event_bus.py` (subscribe/emit/unsubscribe,
      sink, seq-монотонность, неблокирующий emit),
      `tests/ui/test_live_events.py` (панель не создаёт reactive
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
      capabilities: frozenset[Literal[
          "fs.read", "fs.write", "net", "shell", "git", "ui", "secrets", "browser"
      ]]
      is_mutating: bool
      category: str  # "filesystem" | "shell" | "network" | "git" | "info" | "browser"
      timeout_s: int = 30
      max_output_chars: int = 5000
      idempotent: bool = False
      needs_snapshot: bool = False  # авто-снапшот перед вызовом (10.9)
  ```
- [ ] **`@tool`** принимает `capabilities: list[str]`, `timeout_s`,
      `max_output_chars`, `idempotent`, `needs_snapshot`. Дефолт `capabilities` — `{"fs.read"}`.
  - `read_file`/`list_directory`/`search_codebase`/`read_attached_file`/
    `find_references` — `fs.read`.
  - `write_file`/`apply_patch`/`run_shell_command` (dangerous) — `fs.write`/`shell`.
  - `web_search`/`get_weather`/`get_exchange_rate` — `net`.
  - `browser_open`/`browser_click`/`browser_fill`/`browser_snapshot` — `browser` (2.11).
  - `get_git_status`/`create_snapshot`/`undo_last_snapshot`/`git_commit` — `git`.
  - `record_hypothesis`/`update_hypothesis`/`list_hypotheses` — `ui`.
- [ ] **`ToolRegistry.get_spec(name) -> ToolSpec`** — единая точка доступа.
- [ ] **`ToolRegistry.execute`** проверяет `capabilities` **до** выполнения
      (см. 2.7 Policy Engine).
- [ ] **Per-session overrides:** `AppConfig.session_overrides: dict[str, ToolPolicy]` —
      пользователь может разрешить инструмент «на эту сессию», не меняя
      глобальный конфиг. Приоритет — в 2.7.
- [ ] **`timeout_s` соблюдается через `asyncio.wait_for`** — в `ToolRegistry.execute`
      для async-инструментов; для sync — через `ThreadPoolExecutor` с таймаутом.
- [ ] **`max_output_chars`** — усечение `ToolResult.content` с хвостом
      `… (усечено, всего N символов)`.
- [ ] **`idempotent`** — если `True`, повторный вызов с теми же аргументами в
      рамках одной итерации не выполняется (возвращается кэш).
- [ ] **`needs_snapshot`** — перед вызовом создаётся снапшот (10.9);
      `ToolResult.metadata.snapshot_id` заполняется.
- [ ] **Тесты:** `tests/agents/test_tool_spec.py` — capabilities у всех
      зарегистрированных инструментов, immutable, `timeout_s > 0`,
      `max_output_chars > 0`, `needs_snapshot` только у mutating.

### 2.7 Policy Engine (OlliDesk 2.0)

**Боль:** «политики раскиданы по трём местам, приоритет неочевиден».

- [ ] **`core/policy.py::effective_policy(tool_spec, profile, security, config) ->
      EffectivePolicy`** — единая точка:
  ```python
  @dataclass(frozen=True)
  class EffectivePolicy:
      policy: ToolPolicy  # auto | ask_first | excluded
      allowed: bool
      reason: str  # "security" | "profile" | "session" | "config" | "default"
  ```
- [ ] **Приоритет (сверху вниз):**
  1. `SecurityPolicy` (confidential → всё `net`/`shell`/`git`/`browser` = `excluded`).
  2. `ProfileConfig.tool_policies` (самый сильный).
  3. `AppConfig.session_overrides` (per-session).
  4. `AppConfig.tool_policies`.
  5. Декоратор `@tool(policy=...)`.
- [ ] **Дефолт по capabilities:**
  - `fs.read` → `auto`.
  - `fs.write` → `ask_first`.
  - `net` → `ask_first` (кроме `get_weather`/`get_exchange_rate` — `auto`).
  - `shell` → `excluded` в профиле `research`/`learning`, `ask_first` в `development`.
  - `git` → `ask_first` (кроме `get_git_status` — `auto`).
  - `browser` → `excluded` в `safe`/`research`, `ask_first` в `development`,
    `auto` в `open` (2.11).
  - `secrets` → `excluded` всегда (даже в development).
- [ ] **`confirm.web_search`** — внутри security-слоя; старые
      `tool_policies: {web_search: ask_first}` больше не включают диалог —
      только `confirm.web_search: true`.
- [ ] **`ChatPanel._build_registry`** использует `effective_policy` для каждого
      инструмента; `_apply_config_policies` удаляется.
- [ ] **`ToolRegistry.execute`** вызывает `effective_policy` и возвращает
      `ToolResult(is_error=True, error_category=FIX_ARGUMENTS, content="Заблокировано
      политикой: …")` без выполнения.
- [ ] **Per-session overrides в UI:** `ToolConfirmationDialog` — кнопка
      «Разрешить на эту сессию» → `config.session_overrides[tool_name] = AUTO`.
      Сбрасывается при закрытии сессии.
- [ ] **Тесты:** `tests/core/test_policy.py` — 20+ кейсов (профиль, security,
      capabilities, приоритет, session_overrides, confirm.web_search).

### 2.8 Application Services (OlliDesk 2.0)

**Боль:** «UI знает про `QFileSystemModel`, `ProjectState`, `SessionStore`,
`VectorStore` напрямую; тяжело тестировать».

- [ ] **`core/services/`** — чистые Python-сервисы без Qt:
  - `session_service.py` — создание/удаление сессий, `parts_json`, миграции.
  - `chat_service.py` — отправка сообщений, отмена, стриминг (через `EventBus`).
  - `tool_service.py` — `execute_tool(invocation, context)`, audit, метрики,
    snapshots (10.9).
  - `model_service.py` — выбор модели, `ModelCatalog`, `auto_select`, парк моделей (4.1).
  - `project_service.py` — `ProjectIntel`, `repomap`, `git_context`.
  - `memory_service.py` — эпизодическая память (4.3).
  - `search_service.py` — цепочка поиска (2.13).
  - `browser_service.py` — режимы браузера (2.11).
- [ ] **`ui/*_controller.py`** переписываются на сервисы: `ChatPanel` вызывает
      `ChatService.send()`, `IndexingController` — `ProjectService.index()`,
      `FileTreeController` — `ProjectService.list_files()`.
- [ ] **Тесты сервисов без Qt** — покрытие ≥80% на критичных путях
      (сессия, отмена, policy, audit, memory, search, browser).

### 2.9 AsyncResource для Qt (OlliDesk 2.0) — **приоритет поднят**

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

### 2.10 Natural exit + soft budgets (OlliDesk 2.0) — **новое, приоритет высокий**

**Боль:** «жёсткий `max_iterations` душит агента; он не может делать столько
итераций, сколько нужно».

- [ ] **`agents/agent_loop.py::AgentLoop.run()`** — `while True:` с естественным
      выходом:
  ```python
  while True:
      response = await client.chat(...)
      if response.finish_reason == "stop" and not pending_tool_calls:
          break
      # ... выполнение tool_calls ...
  ```
  Убрать жёсткий `max_iterations` как основной выход. `max_iterations` остаётся
  **только** для `AgentMode.CHAT`/`PLAN` (light/medium/deep) как мягкий
  потолок, не как жёсткий лимит.
- [ ] **`SoftBudget`** (`dataclass` в `agents/agent_loop.py`):
  ```python
  @dataclass
  class SoftBudget:
      time_s: float = 600.0
      tokens: int = 100_000
      iterations: int = 100
      warning_ratio: float = 0.8
  ```
  При приближении к `warning_ratio` — `EventBus.emit("budget.warning")` +
  сигнал в UI; при превышении — `EventBus.emit("budget.exceeded")`, но цикл
  **продолжается**.
- [ ] **`AgentLoop._check_budget()`** — вызывается после каждой итерации;
      возвращает `BudgetStatus` (`ok` / `warning` / `exceeded`).
- [ ] **`AgentChatThread`** эмитит `budget_warning` вместо
      `iteration_limit_exceeded`; кнопка «Продолжить» сбрасывает бюджеты
      (не удваивает лимит) с явным показом, сколько уже потрачено.
- [ ] **Кнопка «Продолжить до результата»** — `time_budget=∞`,
      `token_budget=∞`, только `user_cancel` остановит.
- [ ] **`_final_call_no_tools`** остаётся **только fallback** при повторе
      tool_calls/исчерпании нуджей, не как основной выход. `AgentIterationLimitError`
      бросается только если сам финальный вызов упал.
- [ ] **`EventBus.emit("agent.finished", {finish_reason, iterations, tokens,
      wall_clock_s})`** — причина остановки видна в UI.
- [ ] **`ChatPanel`**: `StatusDashboard` показывает бюджет (`Итерация 7 ·
      12 400 tok · 2:14 · cache 70 400 reads`), причину остановки
      (`finish_reason=stop` / `budget.exceeded` / `user_cancel`).
- [ ] **`core/config.py::AppConfig`**: `soft_budget_time_s`,
      `soft_budget_tokens`, `soft_budget_iterations` (с дефолтами; профили
      могут переопределять через `ProfileConfig.soft_budget_*`).
- [ ] **`_build_system_prompt`**: вместо «Итерация N из M» — «Потрачено N
      итераций · T tok · S сек; следующий ответ может быть Final Answer,
      если задача решена».
- [ ] **Тесты:** `tests/agents/test_soft_budget.py` — natural exit по
      `finish_reason=stop`, warning при 80 %, exceeded продолжает цикл,
      `user_cancel` останавливает, `_final_call_no_tools` только при повторе.

### 2.11 `browser_control` с тремя режимами (OlliDesk 2.0) — **новое**

**Боль:** «нужен доступ к авторизованным сайтам (Jira, Confluence, внутренние
вики), но CDP-релей к реальному профилю — опасная поверхность атаки».

- [ ] **`core/config.py::BrowserConfig`**:
  ```python
  class BrowserConfig(BaseModel):
      mode: Literal["isolated", "extension", "full"] = "isolated"
      dedicated_profile_path: str = "~/.ollidesk/browser_profile"
      extension_bridge_port: int = 8765
      full_access_acknowledged: bool = False  # для mode="full"
  ```
- [ ] **Режим A: «Изолированный агент» (по умолчанию).**
  - Отдельный профиль Chromium (`--user-data-dir=<dedicated>`) через CDP.
  - Нет доступа к реальным кукам/логинам. Только публичные страницы.
  - `agents/browser_client.py::BrowserClient` — `open(url)`, `snapshot()`,
    `click(selector)`, `fill(selector, text)`, `close()`.
  - Политика: `ask_first` в `development`, `auto` в `open` (с предупреждением).
- [ ] **Режим B: «Расширение с явным consent» (для авторизованных сайтов).**
  - Пользователь ставит расширение (Playwright MCP Bridge или собственное).
  - Агент отправляет интенты; расширение исполняет локально, на вкладке,
    которую пользователь явно разрешил.
  - Минимальные permissions (`activeTab`, не `cookies`).
  - `agents/browser_client.py::ExtensionBrowserClient` — `intent(action, params)`
    через WebSocket-релей.
  - Политика: `ask_first` всегда.
- [ ] **Режим C: «Полный доступ» (только по явному opt-in).**
  - CDP-релей к реальному профилю (копия профиля в `--user-data-dir`).
  - Красное предупреждение в `ui/dialogs/agent_settings_dialog.py`:
    «Агент получит доступ ко всем вашим сессиям. Продолжить?» →
    `BrowserConfig.full_access_acknowledged = True`.
  - Audit log каждого действия (`core/audit.py`).
  - Только в профиле `open`, только для конкретной задачи.
- [ ] **`core/profiles.py`**: `ProfileConfig.browser_mode` — `isolated` в
      `safe`/`research`, `isolated` в `development`, `full` в `open`.
- [ ] **`tools/browser_ops.py`** — `@tool`-обёртки:
  - `browser_open(url)` — capability `browser`, `ask_first`.
  - `browser_snapshot()` — `browser`, `auto` (после `browser_open`).
  - `browser_click(selector)` — `browser`, `ask_first`.
  - `browser_fill(selector, text)` — `browser`, `ask_first`.
  - `browser_close()` — `browser`, `auto`.
- [ ] **`tools/registry.py::ALL_TOOLS`** — добавить `browser_*`.
- [ ] **Audit:** каждый вызов — URL, действие, результат.
- [ ] **`EventBus.emit("browser.action", {action, url, result})`**.
- [ ] **`agents/browser_client.py`** — обёртка над Playwright/CDP с capabilities
      `browser`; таймаут 30 с по умолчанию.
- [ ] **Тесты:** `tests/agents/test_browser_client.py` (mock CDP),
      `tests/tools/test_browser_ops.py` (mock client).

### 2.12 Durable inbox (OlliDesk 2.0) — **новое**

**Боль:** «запрос не должен теряться при перезапуске; очередь должна переживать
сбой».

- [ ] **`state/inbox.py::SessionInbox`** — SQLite-очередь `session_inbox`:
  ```sql
  CREATE TABLE session_inbox (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      prompt TEXT NOT NULL,
      context_json TEXT,
      created_at TEXT NOT NULL,
      state TEXT NOT NULL,  -- pending | running | done | failed
      result_json TEXT,
      error TEXT,
      updated_at TEXT NOT NULL
  );
  ```
- [ ] **`SessionInbox.enqueue(session_id, prompt, context) -> id`** —
      пишет `pending`.
- [ ] **`SessionInbox.claim(id)`** — `pending` → `running` (атомарно).
- [ ] **`SessionInbox.complete(id, result)`** — `running` → `done`.
- [ ] **`SessionInbox.fail(id, error)`** — `running` → `failed`.
- [ ] **`SessionInbox.recover()`** — при старте приложения `running` → `pending`
      (чтобы не залипнуть).
- [ ] **`ChatPanel._send_message`** пишет в inbox **до** запуска
      `AgentChatThread`; `AgentChatThread` забирает и помечает `running`.
- [ ] **`EventBus.emit("inbox.enqueued"/"inbox.delivered")`** — видимость в UI.
- [ ] **`ui/widgets/status_dashboard.py`** — индикатор очереди
      (`inbox: 2 pending`), tooltip — список.
- [ ] **Тесты:** `tests/state/test_inbox.py` — enqueue/claim/complete,
      recover, конкурентный claim (один `running`), SQLite-персист.

### 2.13 Цепочка поиска с fallback (OlliDesk 2.0) — **новое**

**Боль:** «DDG в Москве — капча и блокировки; нужна свобода знаний без единой
точки отказа».

- [ ] **`agents/search_providers.py`** — цепочка `SearchProvider`:
  ```python
  class SearchProvider(Protocol):
      name: str
      async def search(self, query: str, num: int = 10) -> list[SearchResult]: ...
  ```
  1. **`SearXNGProvider`** (локальный SearXNG в Docker/WSL) — мета-поиск по
     20+ движкам. `core/config.py::SearchConfig.searxng_url`.
  2. **`OpenSERPProvider`** (локальный OpenSERP) — бесплатный API к
     Google/Yandex/Baidu/Bing/DDG. `SearchConfig.openserp_url`.
  3. **`YandexSearchAPIProvider`** (платный, опциональный) — официальный API.
     `SearchConfig.yandex_api_key`.
  4. **`OllamaWebSearchProvider`** (опциональный) — поиск от Ollama.
     `SearchConfig.ollama_api_key`.
  5. **`DDGProvider`** (последний fallback) — только одиночные запросы.
- [ ] **`core/config.py::SearchConfig`**:
  ```python
  class SearchConfig(BaseModel):
      providers: list[str] = ["searxng", "openserp", "yandex", "ollama", "ddg"]
      searxng_url: str = "http://localhost:8080"
      openserp_url: str = "http://localhost:7000"
      yandex_api_key: str = ""
      ollama_api_key: str = ""
      timeout_s: int = 15
      cache_ttl_hours: int = 24
  ```
- [ ] **`tools/web_search.py`** — цепочка с fallback, каждый провайдер в
      отдельном потоке/процессе; падение одного не роняет цепочку.
- [ ] **Кэш** (`SearchCache` уже есть) — обязателен; агент не платит дважды.
- [ ] **`EventBus.emit("search.provider.used", {provider, duration_ms, num_results})`** —
      видимость в UI, какой провайдер сработал.
- [ ] **Политика:** `SearXNG`/`OpenSERP` — `auto` в `research`; `Yandex API` —
      `ask_first` (платно); `DDG` — `auto`, но с предупреждением при капче.
- [ ] **`tools/web_search_hygiene.py`** — `filter_query()` вызывает
      `redact()` (0.1.2) перед отправкой.
- [ ] **Тесты:** `tests/agents/test_search_providers.py` (mock HTTP),
      `tests/tools/test_web_search_chain.py` (fallback, кэш, таймауты).

---

## Фаза 4.1 — Парк моделей (OlliDesk 2.0) — **новое, критично**

**Боль:** «одна модель не может быть одновременно router, planner, coder и verifier;
большой контекст на CPU душит скорость».

- [ ] **Роли в `agents/model_pool.py`:**
  ```python
  class Role(StrEnum):
      ROUTER = "router"
      PLANNER = "planner"
      CODER = "coder"
      VERIFIER = "verifier"
      EMBED = "embed"
  ```
  LRU-вытеснение по `max_loaded` (по умолчанию 3: router+planner на GPU,
  coder на CPU; verifier — по требованию; embed — всегда).
- [ ] **`slm_models.yaml`** (9.3.3): теги `router`, `planner`, `coder`,
      `reasoning`, `verifier`, `embed`, `moE`.
- [ ] **Qwen3-Coder-30B-A3B (MoE)** — добавить в каталог как основную
      coder-модель: активирует ~3B параметров на токен, качество близко к
      30B, скорость на CPU приемлемая (5–10 tok/s на 127 ГБ RAM). Это меняет
      правила игры на T1200.
- [ ] **Qwen2.5-Coder-14B** — fallback для coder, если MoE не влезает/медленно.
- [ ] **`agents/slm_selector.py::select_park(profile, hardware) -> dict[Role, model_id]`** —
      выбор парка моделей под профиль задачи и доступную RAM/VRAM.
- [ ] **`ui/widgets/pipeline_selectors.py`** — показать роли
      (router/planner/coder/verifier) отдельными селекторами с подсветкой
      coder (9.3.2).
- [ ] **`core/profiles.py`** — `ProfileConfig.park_roles: dict[Role, model_id]`.
- [ ] **`_ModelPoolLoadThread`** — загрузка ролей последовательная, per-role
      timeout 90 с; при `max_loaded=3` — LRU-вытеснение по использованию.
- [ ] **`EventBus.emit("slm.loaded", {role, model_id, load_ms, n_ctx,
      n_gpu_layers, vram_mb, ram_mb})`** — для `StatusDashboard`.
- [ ] **Тесты:** `tests/agents/test_model_pool_park.py` — выбор парка,
      LRU-вытеснение, fallback при нехватке RAM, MoE-детект по тегу.

---

## Фаза 4.2 — Иерархический контекст + prefix caching (OlliDesk 2.0) — **новое, критично**

**Боль:** «большой `n_ctx` на CPU — квадратичное замедление attention; не душить
агента, но и не душить себя».

- [ ] **Горячее окно** (8–16k): текущий файл, последние tool-результаты, активная
      гипотеза. Не режется компактором.
- [ ] **Тёплый слой** (32–64k): RAG-выдержки, repomap, AGENTS.md — подгружается
      по требованию, режется первым.
- [ ] **Холодный слой** (вне окна): полная история, старая документация — через
      `search_codebase` / `read_file`.
- [ ] **`core/profiles.py::ProfileConfig.n_ctx_hint`** — 8192 (chat), 16384 (research),
      32768 (development), 65536 (deep).
- [ ] **`agents/slm_optimizer.py::optimize(..., n_ctx_hint: int | None = None)`** —
      принимать желаемый `n_ctx`; убрать жёсткий CPU-cap 8192 →
      `min(profile.n_ctx_hint, budget_by_ram)`.
      `budget_by_ram` — расчёт по доступной RAM и KV-кэшу модели
      (`agents/gguf_reader.py` даёт `KV/токен`).
- [ ] **`core/config.py::SLMConfig.kv_cache_type`** — по умолчанию `q8_0` для
      CPU-analyst (вдвое меньше KV).
- [ ] **Prefix caching** в `LocalLLMClient` — включить (если доступно в
      llama.cpp); показывать в `StatusDashboard` «cache: N reads» (как в OpenCode).
      `LocalLLMClient.last_stats` расширяется `cache_reads`/`cache_writes`.
- [ ] **Компактор** — порог поднять с 80% до 90% при `n_ctx >= 32768`; не резать
      горячее окно. `agents/history_compactor.py::compact_history` получает
      `hot_window_tokens` и не трогает сообщения в горячем окне.
- [ ] **`core/context_budget.py`** — `rag_char_budget(num_ctx)` уже
      масштабируется; при 32k можно давать больше контекста. Добавить
      `hot_window_tokens(num_ctx)` и `warm_layer_tokens(num_ctx)`.
- [ ] **Benchmark (4.7.B)** — прогнать 7B/14B/30B-MoE на 8k/16k/32k/64k,
      записать `ttft_ms`/`tok_s`/`ram_mb` в `docs/BENCHMARK.md`. Без цифр —
      не поднимать cap вслепую.
- [ ] **Тесты:** `tests/agents/test_hierarchical_context.py` — компактор не
      режет горячее окно, `n_ctx_hint` учитывается, prefix caching включается,
      `budget_by_ram` корректен для 7B/14B/30B.

---

## Фаза 4.3 — Эпизодическая память (OlliDesk 2.0) — **новое, критично**

**Боль:** «агент, который не помнит, что делал вчера, — это калькулятор, а не
помощник».

- [ ] **`~/.ollidesk/memory.db`** (SQLite + FTS5):
  ```sql
  CREATE TABLE reflections (
      id INTEGER PRIMARY KEY,
      created_at TEXT NOT NULL,
      project_path TEXT,
      task_type TEXT,  -- research | code | bug_fix | refactor
      summary TEXT NOT NULL,  -- «что сработало / не сработало»
      outcome TEXT,  -- success | partial | failure
      tags TEXT  -- JSON-массив
  );
  CREATE VIRTUAL TABLE reflections_fts USING fts5(summary, tags);
  CREATE TABLE decisions (
      id INTEGER PRIMARY KEY,
      created_at TEXT NOT NULL,
      project_path TEXT,
      decision TEXT NOT NULL,
      rationale TEXT
  );
  CREATE TABLE preferences (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL
  );
  ```
- [ ] **`core/services/memory_service.py`** — `MemoryService`:
  - `remember(reflection: Reflection) -> int`
  - `recall(query: str, top_k: int = 3) -> list[Reflection]`
  - `forget(id: int) -> None`
  - `list_recent(n: int = 20) -> list[Reflection]`
  - `set_preference(key, value)`, `get_preference(key)`
- [ ] **Извлечение:** top-3 релевантных записи через FTS5 (или `VectorStore`,
      если включён); подмешиваются в `_build_system_prompt` (блок «Память»)
      в пределах `memory_char_budget(num_ctx)`.
- [ ] **Запись:** после успешного завершения задачи — короткая рефлексия
      (генерируется `verifier`/`critic`, не основной моделью). Формат:
      «Задача: …; Что сработало: …; Что не сработало: …; Итог: …».
- [ ] **Приватность:** всё локально, шифрование опционально
      (`MemoryConfig.encrypt: bool = False`), пользователь может
      посмотреть/удалить любую запись (`ui/dialogs/memory_dialog.py`).
- [ ] **`EventBus.emit("memory.recalled"/"memory.remembered")`**.
- [ ] **`ui/dialogs/memory_dialog.py`** — список рефлексий, поиск, удаление,
      экспорт в JSON.
- [ ] **Тесты:** `tests/core/test_memory_service.py` — remember/recall/forget,
      FTS5-поиск, приватность (удаление), `memory_char_budget`.

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
- [ ] **Qwen3-Coder-30B-A3B (MoE)** — основная coder-модель (4.1). На 127 ГБ RAM
      даёт 5–10 tok/s на CPU при качестве, близком к 30B. Добавить в
      `_DEFAULT_SLM_MODELS` с тегом `moE`, `coder`.
- [ ] **Qwen2.5-Coder-32B-Instruct** (Q8_0, ~34 ГБ RAM) — «точно» для разовых
      сложных задач. Контекст 16k–32k. Покрывает Python / C# / Bash / Ansible
      одной записью.
- [ ] **DeepSeek-R1-Distill-Qwen-32B** (Q8_0, ~35 ГБ RAM) — reasoning-модель
      для анализа текстов и финансовых документов (МСФО/РСБУ, отчёты, стратегии).
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
- [ ] **`ModelCatalog`** (см. 9.3.3): вынести `_DEFAULT_SLM_MODELS` из `core/config.py`
      в `~/.ollidesk/slm_models.yaml`; `_sync_builtin_models` → миграция при первом
      запуске.
- [ ] **HF search** (см. 9.3.1): вкладка «Поиск HF» в `SLMModelsDialog` через
      `HfApi.list_models`/`model_info`/`list_repo_files`.
- [ ] **Tags `coder`/`reasoning`/`router`/`planner`/`verifier`/`moE`** в
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
      и `remaining <= 3`. **В 2.10** заменяется на soft budget: «Потрачено 7 итераций ·
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
      покрыт и `AgentLoop`. **В 10.8** расширяется на LSP.
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
      **В 4.2** заменяется на `min(profile.n_ctx_hint, budget_by_ram)`.
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
      а исчерпание бюджета шагов). **В 2.10** заменяется на «Продолжить» с новым
      бюджетом (не удвоение).
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
- [x] **Audit log** (см. Фаза 0.1.1): каждая запись `ToolResult` попадает в
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
      подгрузка top-3 через существующий `VectorStore`. **В 4.3.**
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
      `n_ctx` вслепую (4.2).
- [ ] **`[новое]`** Прогон с `kv_cache_type=q8_0` vs `f16` — измерить выигрыш
      по RAM и потерю по качеству.

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
      «закреплению за проектом», но не за сессией. **В 2.10** сессия получает
      `budget`; **в 4.1** — `park_roles`.
- [x] `max_loaded=2` фактически: `_ModelPoolLoadThread` грузит ровно 2 роли
      (planner + analyst). `max_gpu_resident=1` тоже выполнено: planner жёстко на GPU
      (`device="GPU"`), analyst — на CPU (`device="CPU"`), `optimize()` для analyst
      принудительно обнуляет `n_gpu_layers`. **В 4.1** `max_loaded` → 3
      (router + planner на GPU, coder на CPU).
- [x] `n_gpu_layers` пересчитывается per-модель через `agents/slm_optimizer.optimize()`
      (VRAM-first с fallback VRAM+RAM; CPU-only cap 8192; отказ от частичного offload <50 %).
      **В 4.2** cap заменяется на `min(profile.n_ctx_hint, budget_by_ram)`.

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

## Фаза 7 — Расширяемость (**MCP поднят раньше Plugin API**)

**В OlliDesk 2.0 порядок изменён:** ToolSpec (2.6) → Policy Engine (2.7) →
**MCP-клиент** → Plugin API. MCP даёт доступ к экосистеме без переписывания
существующих инструментов — в условиях санкций это критично.

- [ ] **MCP-клиент** (stdio):
  - **`core/mcp_client.py::MCPClient`** — JSON-RPC 2.0 over stdio; lifecycle
    (spawn/kill), capabilities negotiation, tool discovery (`tools/list`),
    tool call (`tools/call`), resource read (`resources/read`), prompt
    (`prompts/get`).
  - **`tools/mcp_ops.py`** — динамическая регистрация инструментов из
    MCP-серверов; каждый MCP-tool получает `ToolSpec` с `capabilities` из
    манифеста сервера.
  - **`core/config.py::MCPConfig`** — список серверов:
    ```python
    class MCPServerConfig(BaseModel):
        name: str
        command: str
        args: list[str] = []
        env: dict[str, str] = {}
        capabilities: list[str] = []  # какие capabilities разрешены
        enabled: bool = True
    ```
  - **`EventBus.emit("mcp.connected"/"mcp.disconnected"/"mcp.tool_called")`**.
  - **Policy Engine (2.7)** — capabilities MCP-сервера проходят через
    `effective_policy`; `secrets` — всегда `excluded`.
  - **Audit log** — каждый MCP-tool call пишется в `core/audit.py`.
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
    (`gguf` / `chat` / `coder` / `reasoning` / `moE`).
  - `QListWidget` результатов (id, downloads, likes, размер первого `.gguf`).
  - `QTextBrowser` — карточка модели (описание из `cardData`, теги, лицензия, ссылка на HF).
  - `QPushButton("Скачать")` — вызывает `download_model(repo_id, filename)`
    (уже есть в `agents/download_slm.py`).
  - `QPushButton("Добавить в каталог")` — запись в `slm_models.yaml` (9.3.3) с
    автоматическими тегами из `cardData.tags` (`coder`/`reasoning`/`moE`).
- [ ] **Бэкенд:** `agents/hf_search.py` — тонкая обёртка:
  - `search_models(query, filter="gguf", sort="downloads", limit=50) -> list[HFModel]`
    через `HfApi.list_models`.
  - `model_info(repo_id, cardData=True) -> HFModelDetails` через `HfApi.model_info`.
  - `list_gguf_files(repo_id) -> list[HFGgufFile]` через `HfApi.list_repo_files`
    (с размером через `model_info.siblings`).
  - `detect_tags(card_data) -> list[str]` — эвристика: `coder` если в тегах
    `code`/`coder`/`starcoder`, `reasoning` если `reasoning`/`r1`/`think`,
    `moE` если в названии `A3B`/`MoE`/`moe`, `embed` если `embed`/`bge`/`nomic`.
  - **Rate limit:** `HfApi` без токена — 30 req/min; добавить `HF_TOKEN` из
    `core/config.py::AppConfig.hf_token` (опционально) и backoff при 429.
- [ ] **Оффлайн-режим:** при недоступности HF — сообщение в `status_label` и
      fallback на локальный список; не падать.
- [ ] **Интеграция с `download_slm`:** после скачивания — добавить запись в
      `slm_models.yaml` (см. 9.3.3) и обновить `_populate` в диалоге.
- [ ] **`EventBus.emit("hf.search.started"/"hf.search.found"/"hf.download.progress")`** —
      видимость в UI.
- [ ] **Тесты:** `tests/agents/test_hf_search.py` (mock `HfApi`), `tests/ui/test_hf_dialog.py`
      (mock search, offline fallback).

**Затрагивает:** `ui/dialogs/slm_models_dialog.py` (новая вкладка), новый
`agents/hf_search.py`, `agents/download_slm.py` (без изменений).

#### 9.3.2 Подсветка coder-моделей в режиме разработки

**Боль:** «Если я в режиме разработки, при выборе моделей должны подсвечиваться другим
цветом или жирным coder-модели для разработки».

- [ ] **`SLMModelInfo.tags: list[str]`** — добавить поле (например, `["coder"]`,
      `["reasoning"]`, `["chat"]`, `["embed"]`, `["router"]`, `["planner"]`,
      `["verifier"]`, `["moE"]`). Заполняется в `slm_models.yaml` вручную или из
      HF-карточки (`cardData.tags` через `agents/hf_search.py::detect_tags`).
- [ ] **`PipelineSelectors.refresh_models`:** если `self._profile.id == "development"` —
      для моделей с `"coder" in tags` применять `QFont.setBold(True)` и цвет
      (например, `#d4a373`) через `setItemData(Qt.FontRole, ...)` /
      `setItemData(Qt.ForegroundRole, ...)`.
- [ ] **`PipelineSelectors`** — добавить иконки ролей: `⚡ coder`, `🧠 reasoning`,
      `🔀 router`, `📋 planner`, `✅ verifier`, `📦 moE`, `🔤 embed` (показывать
      в tooltip и в префиксе названия).
- [ ] **В `ProfilesSelector`** — то же правило: в профиле «Разработка» показывать
      coder-модели с префиксом `⚡` или `★`.
- [ ] **`ProfileConfig.recommended_model`** — уже есть; добавить
      `ProfileConfig.coder_models: list[str]` (id) для явной рекомендации.
- [ ] **`ProfileConfig.park_roles: dict[Role, model_id]`** (4.1) — рекомендация
      по ролям для профиля.
- [ ] **`ProfilesSelector`** — бейдж `freedom_level` (9.12) рядом с названием профиля:
      `🔒 safe`, `🔍 research`, `🛠 development`, `🌐 open`.
- [ ] **Тесты:** `tests/ui/test_pipeline_selectors.py` — bold/цвет для coder в
      development, обычный шрифт в research/learning, иконки ролей, бейдж freedom.

**Затрагивает:** `core/config.py::SLMModelInfo`, `core/profiles.py::ProfileConfig`,
`ui/widgets/pipeline_selectors.py`, `ui/widgets/profiles_selector.py`.

#### 9.3.3 Модели и tier — в отдельный файл конфигурации

**Боль:** «Не хранить жёстко модели и tier в коде, а создать файл конфигурации… хранить
рядом с файлом конфигурации приложения, потому что сам config.yaml стал не читаем».

- [ ] **`~/.ollidesk/slm_models.yaml`** — новый файл, список `SLMModelInfo` в YAML.
  - Схема:
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
        tier: balanced
        tags: [coder, chat]
        description: Основная coder-модель для профиля «Разработка».
        recommended_profile: development
        moe: false
      - id: qwen3-coder-30b-a3b-instruct-q4_k_m
        repo: Qwen/Qwen3-Coder-30B-A3B-Instruct-GGUF
        file: qwen3-coder-30b-a3b-instruct-q4_k_m.gguf
        display_name: Qwen3-Coder-30B-A3B (MoE, Q4_K_M)
        size_mb: 18500
        kind: chat
        n_ctx: 65536
        tier: quality
        tags: [coder, chat, moE]
        description: MoE-модель, ~3B активных параметров на токен. 5–10 tok/s на CPU.
        recommended_profile: development
        moe: true
        active_params_b: 3
    ```
  - **Миграция при первом запуске:** если файла нет — записать из
    `_DEFAULT_SLM_MODELS` (в `core.config` оставить только как fallback для
    миграции; затем удалить или пометить deprecated).
  - **Чтение:** `load_config()` подхватывает `slm_models.yaml` в
    `SLMConfig.available_models`; при отсутствии — fallback на `_DEFAULT_SLM_MODELS`
    + предупреждение в лог.
  - **Запись:** `SLMModelsDialog` (добавление/удаление/редактирование),
    `_add_custom_model`, `_on_download_finished` — пишут в `slm_models.yaml` через
    `save_slm_models()`.
- [ ] **`core/slm_models_io.py`** — новый модуль:
  - `load_slm_models(path: Path | None = None) -> list[SLMModelInfo]`.
  - `save_slm_models(models: list[SLMModelInfo], path: Path | None = None) -> None`.
  - `validate_slm_models(data: list[dict]) -> list[SLMModelInfo]` — pydantic-валидация
    с `schema_version`-миграцией.
  - `migrate_from_defaults(default_models: list[SLMModelInfo], path: Path) -> None` —
    запись при первом запуске.
  - `merge_with_defaults(loaded: list[SLMModelInfo], defaults: list[SLMModelInfo]) -> list[SLMModelInfo]`
    — добавляет новые встроенные модели, не удаляя пользовательские.
- [ ] **`config.yaml` похудеет**: `slm.available_models` больше не хранится там
      (только в `slm_models.yaml`); `slm.selected_model`, `planner_model_id`,
      `analyst_model_id` остаются в `config.yaml` (это выбор пользователя, не каталог).
- [ ] **`schema_version` в `slm_models.yaml`** — для будущих миграций.
- [ ] **Редактор в UI:** `SLMModelsDialog` получает режим «Редактировать YAML»
      (открыть в `EditorWidget` или `QPlainTextEdit`); после сохранения — валидация
      через `SLMModelInfo(**data)`.
- [ ] **Объединить `PullThread` и `_DownloadThread`** (см. 2.3): обе загрузки —
      через единый `_DownloadThread` + запись в `slm_models.yaml`.
- [ ] **`EventBus.emit("slm.catalog.updated")`** — при изменении каталога.
- [ ] **Тесты:** `tests/core/test_slm_models_io.py` — load/save/validate, миграция,
      merge, schema_version, битый YAML, отсутствующий файл.

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
  - `get_recent_commits(repo_path, limit=10) -> list[dict]` — `hash`, `author`,
    `date`, `subject`.
  - `commit(repo_path, message, files=None) -> str` (агентский коммит; `git add` +
    `git commit`); `files=None` — только уже отслеживаемые изменённые, не `git add -A`.
  - `read_file_at_head(repo_path, file) -> str` — уже есть.
  - `get_git_status_for_file(repo_path, file) -> str` — уже есть.
  - `create_snapshot(repo_path, files=None) -> str` — уже есть (stash-подобный);
    **10.9** — расширяется до полноценного снапшота с `snapshot_id`.
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
- [ ] **`EventBus.emit("git.branch.changed"/"git.commit.created")`**.
- [ ] **Тесты:** `tests/tools/test_git_ops.py` — расширить: branch/hash/diff/commits/
      commit; `tests/agents/test_git_context.py` — `git_context` в промпте;
      `tests/tools/test_git_commit.py` — коммит только своих изменений.

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
- [ ] **`[новое]` Бейдж «Откатить»** — если `ToolResult.metadata.snapshot_id` (10.9),
      показывать кнопку «↩ Откатить» в `ToolCallCard`.
- [ ] **`[новое]` Бейдж «LSP»** — если `ToolResult.metadata.lsp_errors` (10.8),
      красный блок «3 ошибки, исправь» с jump-to-line.

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

#### 9.5.3 «Живой» UI: события из логов в интерфейсе — **приоритет поднят**

- [ ] **`core/event_bus.py::EventBus`** — синглтон `QObject` с сигналом
      `event_emitted(str, dict)`. См. **2.5**.
- [ ] **`core/logger.py`:** loguru-sink для `level >= INFO` → EventBus. См. **2.5**.
- [ ] **`ui/widgets/live_events.py::LiveEventsPanel`:**
  - `QPlainTextEdit` (read-only) или `QTextBrowser` с цветными строками по уровню.
  - Встроен в `ChatPanel` под `status_label` (сворачиваемый, высота 120 px, авто-скролл).
  - Или отдельная вкладка «📡 События» (если чат не вкладка — см. 9.1, то лучше панель).
  - Кнопка «Очистить», чекбокс «Только важное» (фильтр по `level >= WARNING` +
    структурированные события).
  - **`[новое]` Фильтр по `kind`:** `tool.*`, `llm.*`, `rag.*`, `index.*`, `slm.*`,
    `budget.*`, `mcp.*`, `inbox.*`, `memory.*`, `search.*`, `browser.*`.
- [ ] **Индикатор «программа работает»:** в `StatusDashboard` — анимированная точка
      (пульсация `QTimer` 500 мс) пока `_generating = True`; тултип — последнее событие
      из `EventBus`. Решает боль «не понимаю, почему не отвечает».
- [ ] **Прогресс по этапам** вместо «🤔 Думает…»: `status_label` показывает этап
      (`🔍 Поиск по коду → 📖 Чтение 3 файлов → 🧠 Генерация (TTFT 1.2 с) → ✍️ 42 tok/s`).
      Источник — `EventBus`.
- [ ] **`ChatThoughtStream`** — уже реализует часть этого для research/agent; расширить
      на все режимы (в Chat — плоский `LiveEventsPanel`, в Agent/Plan —
      `ChatThoughtStream` + `LiveEventsPanel` для системных событий).
- [ ] **`[новое]` Reasoning trace:** из существующих данных (`Critique`, `VerifyResult`,
      tool metadata, `finish_reason`) генерировать объяснение решений:
      - «Выбрал `apply_patch`, потому что файл существует и контекст совпал».
      - «Остановился, потому что `finish_reason=stop` и тесты прошли».
      - «Не смог найти `foo()`, потому что LSP вернул 0 references; попробую поиск по имени».
      Показывать в `LiveEventsPanel` (свёрнуто) или раскрывающемся трейсе.
- [ ] **`[новое]` Бюджет и причина остановки:** `StatusDashboard` показывает
      `Итерация 7 · 12 400 tok · 2:14 · cache 70 400 reads`; в `LiveEventsPanel` —
      `agent.finished` с `finish_reason` (`stop` / `budget.exceeded` / `user_cancel`).
- [ ] **`[новое]` Кнопка «Продолжить»** — при `budget.exceeded` / `finish_reason=length`:
      новый бюджет (не удвоение), с показом, сколько уже потрачено (2.10).
- [ ] **`[новое]` Кнопка «Продолжить до результата»** — `time_budget=∞`,
      `token_budget=∞`, только `user_cancel` остановит (2.10).

**Затрагивает:** новый `core/event_bus.py`, `core/logger.py`, новый
`ui/widgets/live_events.py`, `ui/chat/panel.py` (встроить панель, подписаться),
`ui/widgets/status_dashboard.py` (пульсация + tooltip + бюджет), `agents/orchestrator.py`
(уже эмитит — пробросить в `EventBus`), `fs/vector_store.py` (`rag.search.*`),
`agents/agent_loop.py` (`tool.*`, `llm.*`, `budget.*`, `agent.finished`).

#### 9.5.4 Прочие UX-фиксы чата

- [ ] **Кнопка «Стоп»** — заметнее (красный фон уже есть в `_STOP_BUTTON_BG`);
      добавить пульсацию при `streaming`.
- [ ] **Индикатор «прокручено вверх»** — `AutoScrollArea` уже показывает «▼ Вниз»;
      добавить счётчик новых сообщений на кнопке (`▼ Вниз (3)`).
- [ ] **Markdown-таблицы в чате** — проверить на светлой теме (сейчас
      `_render_table(dark=...)` — ок, но `TextWidget` авто-детект может ошибаться при
      смене темы на лету; подписаться на `theme_changed`).
- [ ] **`[новое]` Кнопка «Откатить»** — в `ToolCallCard` при наличии `snapshot_id`
      (10.9); откат — `ask_first` через `ToolConfirmationDialog`.
- [ ] **`[новое]` Бейдж «Снапшот»** — если `needs_snapshot=True` и снапшот создан,
      показывать `📸` в заголовке карточки.

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
- [ ] **`[новое]` Превью документа** — `DocumentPreviewDialog` уже есть; добавить
      drag&drop PDF/DOCX в чат → автоматический `extract_document` → превью.
- [ ] **`[новое]` `EventBus.emit("media.extracted", {path, kind, chars, duration_ms})`** —
      видимость в `LiveEventsPanel`.

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
- [ ] **`[новое]` Prefix caching** — показывать «cache: N reads / M writes» в
      `StatusDashboard` (4.2); tooltip — `cache_reads`, `cache_writes`, `hit_rate`.
- [ ] **`[новое]` Бюджет** — «Итерация 7 · 12 400 tok · 2:14 · cache 70 400 reads»
      (2.10); tooltip — `soft_budget_time_s`, `soft_budget_tokens`, `soft_budget_iterations`,
      `warning_ratio`.
- [ ] **`[новое]` Причина остановки** — `finish_reason` (`stop` / `length` /
      `budget.exceeded` / `user_cancel`), показывать в `StatusDashboard` и
      `LiveEventsPanel` (2.10).
- [ ] **`[новое]` Индикатор очереди inbox** — `inbox: 2 pending` (2.12); tooltip —
      список.
- [ ] **`[новое]` Индикатор памяти** — `memory: 12 reflections` (4.3); tooltip —
      последние 3.
- [ ] **`[новое]` Индикатор поиска** — `search: searxng` (2.13); tooltip — цепочка
      провайдеров и последний использованный.
- [ ] **`[новое]` Индикатор браузера** — `browser: isolated` (2.11); tooltip — режим
      и последний URL.
- [ ] **`[новое]` Индикатор MCP** — `mcp: 2 connected` (7); tooltip — список серверов.
- [ ] **`[новое]` Индикатор freedom_level** — `🔒 safe` / `🔍 research` /
      `🛠 development` / `🌐 open` (9.12); tooltip — матрица политик.

### 9.9 Порядок работ в Фазе 9 (новый)

Приоритет — по боли пользователя:

1. ✅ **0.1.3 История + 0.1.4 Reasoning** (bug, ломает доверие и UX) — закрыто.
2. **9.1 Стационарные вкладки** (архитектурная правка, разблокирует 9.5.3) — 2–3 дня.
3. **2.5 EventBus + 9.5.3 Живой UI** (`LiveEventsPanel`, пульсация, статусы по этапам,
   бюджет, причина остановки, reasoning trace) — 3–5 дней. **Приоритет поднят.**
4. **9.5.1 Визуальный редизайн пузыря** — 3–5 дней.
5. **9.3 Модели** (поиск HF + `slm_models.yaml` + подсветка coder + бейдж freedom) — 5–7 дней.
6. **9.4 Git-интеграция** (`git_context` + `git_commit` + `ProjectSettings`) — 3–5 дней.
7. **9.2 Автопоказ структуры** — 1 день.
8. **9.7 Мультимодальность в UI** — 3–5 дней (после backend Фазы 5).
9. **9.10 Permissions Dashboard** — 2–3 дня.
10. **9.11 AsyncResource в UI** — 2–3 дня.
11. **9.12 Режимы свободы в профилях** — 1–2 дня.
12. **9.8 Остальное** — по мере.

**Критерий готовности фазы:** пользователь открывает проект → видит структуру; задаёт
вопрос → видит все этапы (RAG → LLM → TTFT → токены → tool calls → LSP → тесты → verify)
в живой панели; reasoning растёт сам; после перезагрузки история (включая reasoning и
tool calls) на месте; модели ищутся на HF и лежат в `slm_models.yaml`; в режиме разработки
coder-модели подсвечены; агент знает ветку/hash и умеет коммитить (с подтверждением);
причина остановки видна; бюджет виден; «Продолжить» не удваивает лимит.

### 9.10 Permissions Dashboard — **новое**

**Боль:** «не понимаю, почему инструмент заблокирован; хочу видеть, что разрешено».

- [ ] **`ui/dialogs/permissions_dashboard.py::PermissionsDashboard`** —
      отдельный диалог (`View → Разрешения…`):
  - **Вкладка «По профилям»:** матрица `профиль × инструмент` с `auto`/`ask_first`/`excluded`;
    источник политики (`security` / `profile` / `session` / `config` / `default`).
  - **Вкладка «По capabilities»:** `fs.read`, `fs.write`, `net`, `shell`, `git`, `ui`,
    `secrets`, `browser` — какие инструменты используют, какие разрешены.
  - **Вкладка «Последние вызовы»:** из `core/audit.py` — timestamp, tool, policy,
    approved, error, dangerous; фильтр по инструменту/профилю.
  - **Вкладка «Почему заблокировано»:** выбор инструмента → `effective_policy` объясняет
    цепочку: `security → profile → session → config → decorator` с указанием, какой
    слой заблокировал.
- [ ] **Per-session overrides:** кнопка «Разрешить на эту сессию» в
      `ToolConfirmationDialog` → `config.session_overrides[tool_name] = AUTO`;
      сбрасывается при закрытии сессии. Отображается в Permissions Dashboard.
- [ ] **`EventBus.emit("permissions.overridden")`** — при изменении session_overrides.
- [ ] **Тесты:** `tests/ui/test_permissions_dashboard.py` — матрица, объяснение
      блокировки, session_overrides.

**Затрагивает:** новый `ui/dialogs/permissions_dashboard.py`,
`ui/menus.py` (пункт меню), `core/config.py` (`session_overrides`),
`ui/dialogs/tool_confirmation_dialog.py` (кнопка «на сессию»).

### 9.11 AsyncResource в UI — **новое**

См. 2.9. Использование в списках моделей, сессий, дерева проекта, RAG-источников.

- [ ] **`ui/widgets/pipeline_selectors.py`** — `AsyncResource[list[SLMModelInfo]]`:
      не мигать при `refreshing`, показывать `latest` при `loading`.
- [ ] **`ui/dialogs/session_manager_dialog.py`** — `AsyncResource[list[SessionInfo]]`.
- [ ] **`ui/file_tree_controller.py`** — `AsyncResource[list[FileRecord]]` для дерева.
- [ ] **`ui/chat/panel.py`** — `AsyncResource[list[SearchResult]]` для RAG-источников.
- [ ] **`ui/dialogs/slm_models_dialog.py`** — `AsyncResource[list[SLMModelInfo]]` для
      каталога.
- [ ] **Правило:** UI не рисует пустоту, если `state in ("loading","refreshing")`
      и есть `latest`.
- [ ] **Тесты:** `tests/ui/test_async_resource_integration.py` — не мигает при
      `refreshing`, отдаёт `latest`, сообщает ошибку.

### 9.12 Режимы свободы в профилях (OlliDesk 2.0) — **новое, критично**

**Боль:** «нужна свобода, но с ответственностью; пользователь сам выбирает уровень».

- [ ] **`core/profiles.py::ProfileConfig.freedom_level`** —
      `Literal["safe", "research", "development", "open"]`.
- [ ] **Матрица:**

| Профиль | shell | web_search | git_commit | browser | apply_patch | MCP/plugins |
|---|---|---|---|---|---|---|
| **safe** | excluded | ask_first | excluded | excluded | ask_first | excluded |
| **research** | excluded | auto | excluded | excluded | ask_first | ask_first |
| **development** | allowlist + ask_first | ask_first | ask_first | ask_first | auto | ask_first |
| **open** | dangerous + ask_first | auto | auto | auto | auto | auto |

- [ ] **«open»** — с явным предупреждением при выборе:
      `QMessageBox.warning` → «Агент получит полный доступ к shell, интернету и браузеру.
      Все действия пишутся в audit log. Продолжить?» → `acknowledged_open = True`.
- [ ] **`ui/widgets/profiles_selector.py`** — показать `freedom_level` бейджем:
      `🔒 safe`, `🔍 research`, `🛠 development`, `🌐 open`.
- [ ] **`core/policy.py::effective_policy`** — учитывает `freedom_level`:
      - `safe` → `shell`/`git`/`browser`/`secrets` = `excluded`; `net` = `ask_first`.
      - `research` → `shell`/`git`/`browser` = `excluded`; `net` = `auto` (кроме платных);
        `apply_patch` = `ask_first`.
      - `development` → `shell` = `allowlist + ask_first`; `git` = `ask_first`;
        `browser` = `ask_first`; `apply_patch` = `auto`; `net` = `ask_first`.
      - `open` → `shell` = `dangerous + ask_first`; `git` = `auto`; `browser` = `auto`;
        `apply_patch` = `auto`; `net` = `auto`; `secrets` = `excluded` (всегда).
- [ ] **`ProfileConfig.tool_policies`** — переопределяет `freedom_level` (приоритет
      в 2.7: `security > profile > session > config > decorator`).
- [ ] **`ui/dialogs/profile_editor_dialog.py`** — селектор `freedom_level` с
      предупреждением для `open`.
- [ ] **`EventBus.emit("profile.freedom.changed")`** — для `StatusDashboard`.
- [ ] **Тесты:** `tests/core/test_freedom_level.py` — матрица, `open` с
      предупреждением, `secrets` всегда `excluded`, приоритет policy.

**Затрагивает:** `core/profiles.py`, `core/policy.py`, `ui/widgets/profiles_selector.py`,
`ui/dialogs/profile_editor_dialog.py`, `ui/widgets/status_dashboard.py`.

---

## Фаза 10 — Кодирование и проверяемые инструменты (OlliDesk 2.0)

**Цель:** «максимально научилось кодировать» = лучше контекст + лучше проверка + лучше
цикл. Всё, что ниже, опирается на `ToolSpec` (2.6), `Policy Engine` (2.7), `EventBus` (2.5)
и `AsyncResource` (2.9), парк моделей (4.1).

### 10.1 `apply_patch` вместо полного `write_file`

- [ ] **`tools/file_ops.py::apply_patch`** — новый инструмент (capability `fs.write`,
      `is_mutating=True`, `ask_first`, `needs_snapshot=True`):
  - Принимает unified diff (или JSON-патч) + опционально `expected_hash`.
  - Проверяет контекст (hunk context must match) и `expected_hash` (stale check, 10.9).
  - При ошибке — не пишет, возвращает `ToolResult(is_error=True,
    error_category=DIFFERENT_PATH, content="Контекст не совпал: …")`.
  - При успехе — пишет и возвращает `dict{_llm_text, diff, lines_added,
    lines_removed, written_content, original_content, is_new_file, syntax_error,
    lsp_errors}` — та же структура, что `write_file` (переиспользовать
    `ToolCallCard._render_diff_mode`).
  - `write_file` остаётся для новых файлов и перезаписи целиком; для правок агент
    предпочитает `apply_patch`.
- [ ] **Промпт-правило:** в `_TOOL_USE_INSTRUCTION` добавить «Для правок существующих
      файлов используй `apply_patch` с минимальным контекстом; `write_file` — только
      для новых файлов или полной замены».
- [ ] **`_TOOL_USE_INSTRUCTION`:** добавить пример unified diff с контекстом.
- [ ] **`ToolCallCard._render_diff_mode`** — уже работает для `write_file`; расширить
      на `apply_patch` (та же структура `dict`).
- [ ] **Тесты:** `tests/tools/test_apply_patch.py` — успех, несовпадение контекста,
      `expected_hash` mismatch, новый файл, `syntax_error`, `lsp_errors` (mock LSP).

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
- [ ] **`EventBus.emit("tool.finished", {name: "run_tests", summary, failures})`** —
      для `ToolCallCard` и `LiveEventsPanel`.
- [ ] **`ToolCallCard`** — красный блок `12 passed, 1 failed` + jump-to-line.
- [ ] **Тесты:** `tests/tools/test_run_tests.py` — pytest/dotnet/go/npm парсинг,
      таймаут, пустой target.

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
- [ ] **`EventBus.emit("tool.finished", {name: "find_references", references})`**.
- [ ] **Тесты:** `tests/agents/test_lsp_bridge_integration.py` (mock LSP),
      `tests/tools/test_find_references.py` (LSP + fallback).

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
- [ ] **Лимит регенерации** — 1 повтор (не зацикливаться).
- [ ] **`EventBus.emit("agent.verify", {ok, unsupported_claims, regen_attempted})`**.
- [ ] **Тесты:** `tests/agents/test_verifier_in_loop.py` — pass, fail+regen, fail+2,
      лимит регенерации.

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
- [ ] **`[новое]` Smoke-test в CI** на маленькой модели (1.5B/3B) — 3 задачи,
      порог `pass@1 ≥ 0.3`; полный прогон — по расписанию (nightly).
- [ ] **`docs/CODING_BENCH.md`** — результаты, методология, baseline.
- [ ] **Тесты:** `tests/bench/test_coding_bench.py` — smoke-прогон 3 задач.

### 10.6 Model routing для кодинга

- [ ] **Профиль «Разработка»** → coder-модель (`tags: ["coder"]`, см. 9.3.2):
      `qwen3-coder-30b-a3b` (MoE, 4.1) / `qwen2.5-coder-14b` /
      `qwen2.5-coder-32b` (по доступной RAM/VRAM).
- [ ] **Planner 3B GPU / Coder 7B–30B CPU** — 4.1.
- [ ] **Fallback:** если coder-модели нет — обычная `qwen2.5-7b-instruct`, пометка
      в `StatusDashboard` «⚠️ coder-модель не выбрана».
- [ ] **`_TOOL_USE_INSTRUCTION`:** «Ты — coding-ассистент. Читай файлы порциями,
      правь через `apply_patch`, запускай `run_tests`, проверяй `find_references`.
      Не выдумывай пути и имена — используй `ProjectIntel`».
- [ ] **`EventBus.emit("model.role.selected", {role, model_id})`**.
- [ ] **Тесты:** `tests/agents/test_model_routing_coding.py` — выбор coder по профилю,
      fallback, пометка в дашборде.

### 10.7 Coding-цикл в Orchestrator

- [ ] **`Orchestrator.run`** для `task in {"write_code","refactor_plan","bug_analysis"}`
      использует расширенный флоу:
      `classify → plan → collect → apply_patch → run_tests → fix_loop → verify → critique`.
  - `fix_loop` — до 3 итераций `apply_patch → run_tests`; при провале — `verify` +
    `regen` с `fix_hint`.
- [ ] **`RunResult`** расширяется: `tests_passed`, `tests_failed`, `patches: list[dict]`,
      `iterations: int`.
- [ ] **`EventBus.emit("orchestrator.event", {phase, ...})`** — для `LiveEventsPanel`.
- [ ] **Тесты:** `tests/agents/test_orchestrator_coding.py` — расширенный флоу,
      fix_loop, RunResult.

### 10.8 LSP-диагностика после `apply_patch`/`write_file` — **новое, критично**

**Боль:** «агент не должен оставлять сломанный код; LSP уже есть, но не используется».

- [ ] **`tools/file_ops.py`** — после записи вызывать `agents/lsp_bridge.py`
      `diagnostics(path)`:
  - `write_file` — после `_verify_python_syntax` (уже есть) вызвать LSP, если сервер
    доступен.
  - `apply_patch` — после записи вызвать LSP.
- [ ] **Ошибки → Observation:** `⚠️ LSP: 3 ошибки в foo.py:\n  line 12: ...`
      — модель обязана исправить следующим вызовом.
- [ ] **`ToolResult.metadata.lsp_errors: list[dict]`** — для UI-карточки:
      `{file, line, column, severity, message, source}`.
- [ ] **`EventBus.emit("tool.finished", {lsp_errors: [...]})`**.
- [ ] **`ToolCallCard`** — красный блок «3 ошибки, исправь» с jump-to-line.
- [ ] **Fallback:** если LSP-сервер не установлен — `py_compile` (уже есть) +
      пометка «LSP недоступен».
- [ ] **`agents/lsp_bridge.py::diagnostics(path) -> list[dict]`** — новый метод;
      использует `textDocument/publishDiagnostics`; таймаут 5 с.
- [ ] **`EventBus.emit("lsp.diagnostics", {path, errors})`**.
- [ ] **Тесты:** `tests/tools/test_lsp_diagnostics.py` (mock LSP) — ошибки после
      `write_file`/`apply_patch`, fallback на `py_compile`, `metadata.lsp_errors`.

### 10.9 Stale check + автоснапшот перед mutating tool — **новое, критично**

**Боль:** «файл мог измениться между чтением и правкой; откат должен быть всегда».

- [ ] **Stale check в `apply_patch`:** `expected_hash` (sha256) или mtime
      сравнивается с моментом чтения; при расхождении — `DIFFERENT_PATH`,
      не пишет.
  - `tools/file_ops.py::read_file` — сохраняет `hash`/`mtime` в `AgentContext.read_files`
    (`dict[path, {hash, mtime, read_at}]`).
  - `apply_patch(expected_hash=...)` — сверяет; если `None` — сверяет mtime.
- [ ] **Автоснапшот:** перед `write_file`/`apply_patch`/`run_shell_command`
      (`needs_snapshot=True`) — `tools/git_ops.py::create_snapshot` (или копия
      файла в `~/.ollidesk/snapshots/`):
  - `snapshot_id` — `{timestamp}_{hash}`.
  - `~/.ollidesk/snapshots/{snapshot_id}/` — копии файлов + `meta.json`
    (`tool`, `args`, `created_at`, `project_root`).
  - `undo_snapshot(snapshot_id)` — восстанавливает файлы.
- [ ] **`ToolResult.metadata.snapshot_id`** — UI показывает кнопку «Откатить».
- [ ] **`ToolCallCard`** — кнопка «↩ Откатить» при наличии `snapshot_id`.
- [ ] **`EventBus.emit("snapshot.created")`**.
- [ ] **Политика:** снапшоты — `auto`, откат — `ask_first`.
- [ ] **`ProfileConfig.snapshot_retention_days: int = 7`** — автоочистка старых
      снапшотов при старте.
- [ ] **`EventBus.emit("snapshot.cleaned", {removed})`**.
- [ ] **Тесты:** `tests/tools/test_stale_check.py` — hash mismatch, mtime mismatch,
      успех; `tests/tools/test_snapshot.py` — create/undo/cleanup, `snapshot_id`
      в `metadata`, откат через `ToolCallCard`.

**DoD фазы:** на Coding Bench pass@1 заметно выше; после правки файла агент умеет
запускать тесты и исправлять ошибки; verifier ловит галлюцинации путей; LSP используется
для diagnostics и references; coder-модель выбирается автоматически по профилю;
stale check и снапшоты защищают от потери данных.

---

## Приоритеты и порядок

**OlliDesk 2.0 — приоритеты сверху вниз (обновлено).**

1. **Фаза 0.1 — Стабилизация и безопасность** — почти закрыта:
   0.1.1 (shell allowlist + audit), 0.1.3a/b/c (Reasoning 2.0 + История 2.0),
   0.1.4 (model selection), 0.1.5 (изоляция тестов) — ✅. Осталось — 0.1.2
   (redaction секретов) — **следующий шаг, приоритет поднят**: без него «свобода»
   = утечка. См. §Фаза 0.1.
2. **2.10 Natural exit + soft budgets** (3–5 дней) — снимает главное «удушение»
   агента; без этого остальное не даёт свободы.
3. **2.5 EventBus + 9.5.3 LiveEventsPanel** (3–5 дней) — делает работу видимой;
   без этого «свобода» = чёрный ящик.
4. **4.1 Парк моделей + 4.2 Иерархический контекст + prefix caching** (1–2 недели) —
   Qwen3-Coder-30B-A3B меняет правила игры на T1200; большое окно без
   квадратичного замедления.
5. **4.3 Эпизодическая память** (3–5 дней) — превращает инструмент в компаньона.
6. **10.1 apply_patch + 10.8 LSP + 10.9 stale check** (1–2 недели) — замыкает
   coding-цикл с проверкой результата.
7. **2.6 ToolSpec v2 + 2.7 Policy Engine + 9.12 Режимы свободы** (1–2 недели) —
   единая авторизация, свобода с ответственностью.
8. **MCP-клиент (7)** (1–2 недели) — доступ к экосистеме без переписывания.
9. **2.11 browser_control + 2.13 цепочка поиска** (1–2 недели) — свобода знаний
   без единой точки отказа.
10. **2.9 AsyncResource + 9.1 Стационарные вкладки** (3–5 дней) — дружелюбный UI.
11. **9.3 + 9.4 — Модели и Git** (2–3 недели).
12. **10.2 run_tests + 10.3 find_references + 10.4 verifier + 10.5 Coding Bench**
    (2–3 недели).
13. **2.12 Durable inbox + 2.8 Application Services** (1–2 недели).
14. **Фаза 7 Plugin API** — после MCP.
15. **Фаза 5 + 9.7 Мультимодальность**.
16. **Фаза 8 Релиз и качество**.

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
- [ ] **`[новое]`** `py-spy` в CI для профилирования GUI-потока (2.3): запуск
      `py-spy record` на 10-секундном smoke-тесте, порог — ни одна функция
      не блокирует GUI >100 мс.
- [ ] **`[новое]`** `multiprocessing`/`QProcess` для индексации больших проектов (2.3):
      `IndexingProcess` вместо `IndexingThread`, `LSPProcess` для LSP.
- [ ] **`[новое]`** `pyproject.toml`: добавить `[project.optional-dependencies]` группу
      `bench` (`pytest-benchmark`), `search` (`searxng`-клиент — нет, только HTTP),
      `browser` (`playwright`).

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
- ✅ **0.1.1** (shell allowlist + audit), **0.1.3a** (Reasoning 2.0),
  **0.1.3b** (История 2.0 — сохранение), **0.1.3c** (История 2.0 — восстановление),
  **0.1.4** (модель за сессией), **0.1.5** (изоляция тестов ChatPanel) — закрыты.
- **0.1.2** (redaction) — следующий шаг.
- **2.10** (natural exit), **2.11** (browser), **2.12** (inbox), **2.13** (search chain) — новые.
- **4.1** (парк моделей), **4.2** (иерархический контекст), **4.3** (память) — новые.
- **9.10** (permissions dashboard), **9.11** (AsyncResource в UI), **9.12** (freedom_level) — новые.
- **10.8** (LSP diagnostics), **10.9** (stale check + snapshot) — новые.
- **2.5** (EventBus), **2.6** (ToolSpec v2), **2.7** (Policy Engine), **2.8** (Application Services), **2.9** (AsyncResource) — по завершении.
- **9.3.1–9.3.3** (HF search, coder tags, `slm_models.yaml`), **9.4** (git_context + `git_commit`), **9.5.3** (LiveEventsPanel) — по завершении.
- **10.1–10.7** (кодирование) — по завершении.

---

## AGENTS.md — что изменилось

Обновил сохранение структуры и форматирования. Основные правки (OlliDesk 2.0):

1. **Диагностика / Статус SLM-стриминга**: `LocalLLMClient._generate` стримит — реализовано (9.5.2).
2. **`agents/local_llm_client.py`** — стриминг `TextPart` на каждый токен, `start`/`end`, tool-calls после стрима; `_DEFAULT_TEMPERATURE = 0.0` + `_TEMPERATURE_BY_OP`.
3. **`ui/chat/panel.py`** → миксины `BackendsMixin` (`ui/chat/backends.py`) + `ResearchMixin` (`ui/chat/research.py`). Поля — в `ChatPanel.__init__`, MRO — QWidget первым.
4. **`ui/chat/message_item.py`** — `ToolCallCard` теперь с diff-режимом для `write_file` (📂/🔍/↩), `_truncate_preview`, `_escape_diff`; **reasoning-виджет** — переработан в 0.1.3 (авто-высота, авто-скролл, плавное появление); **9.5.1** — бейдж «Откатить» (10.9), бейдж «LSP» (10.8), кнопка «Копировать» в код-блоках.
5. **`fs/vector_store.py`** — `search(categories=...)` (пост-RRF фильтр по `code`/`docs`), `_DOC_LANGUAGES` включает pdf/docx/pptx/xlsx/rtf; EventBus-эмиты `rag.search.*` (2.5).
6. **`agents/history_compactor.py`** — три ступени (reasoning → tool → реплики); **4.2** — `hot_window_tokens` (не режет горячее окно).
7. **`agents/tool_registry.py`** — `ErrorCategory`, `ToolResult.error_category`, `ToolResult.metadata`, `is_mutating`/`category`, приём `dict` от инструментов; **в 2.6** — `ToolSpec` + `capabilities` + `needs_snapshot`; **в 2.7** — `effective_policy`; **10.9** — `metadata.snapshot_id`.
8. **`tools/shell_ops.py`** — **в 0.1.1** allowlist вместо `_BLACKLIST`, dangerous mode, audit log; `MAX_OUTPUT_CHARS=5000`, `MAX_TIMEOUT_S=300` (без изменений).
9. **`state/session_store.py`** — `parts_json` + идемпотентная миграция.
10. **`ui/main_window.py`** — контроллеры `FileTreeController`/`IndexingController`/`AppMenuBar`, `_do_check_ollama` на `_OllamaCheckThread`, `_stop_all_threads` без `new_event_loop`, `_toggle_backend`, `_on_project_tree_toggled`, `_on_ollama_checked`, `_toggle_side_panel` → `AgentSettingsDialog`, `_open_benchmark`, `_open_profile_editor`, `_on_show_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_export_chat`, `_show_file_diff`.
11. **`ui/chat/threads.py`** — `_BenchmarkThread`, `AgentChatThread.tool_executed` с 4-м аргументом `metadata`, `_ModelPoolLoadThread` с per-role timeout 90 с; **в 2.10** — `budget_warning` вместо `iteration_limit_exceeded`.
12. **`ui/dialogs/tool_confirmation_dialog.py`** — `_coerce_value`, `QTableWidget`-редактор аргументов; **9.10** — кнопка «Разрешить на сессию».
13. **`core/sandbox.py`** — `SandboxedPathResolver`; **в 0.1.2** — расширенные `_DENIED_PARTS` (`.env`, `*.pem`, `*.key`, `id_rsa*`).
14. **`core/config.py`** — `profiles`/`active_profile`/`show_project_tree`/`show_pipeline_selectors`/`open_modified_as_diff`/`index_documents`/`max_document_size_mb`/`document_ocr`; `_GLOBAL_ONLY_KEYS` расширен; `_sync_builtin_profiles` (валидатор). **2.10** — `soft_budget_time_s`/`soft_budget_tokens`/`soft_budget_iterations`; **2.11** — `BrowserConfig`; **2.13** — `SearchConfig`; **9.3.3** — `available_models` переезжает в `slm_models.yaml`; **9.12** — `freedom_level`.
15. **`core/profiles.py`** (новый) — `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES`, `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `recommended_model` в каждом встроенном профиле. **4.1** — `park_roles`; **9.3.2** — `coder_models: list[str]`; **9.4** — `git_commit: "ask_first"` в `development`, `excluded` в `research`/`learning`; **9.12** — `freedom_level`; **10.9** — `snapshot_retention_days`.
16. **`core/chat_commands.py`** (новый) — реестр `@`-команд (`ALL_COMMANDS`, `COMMAND_TAGS`, `detect_active_command`, `match_commands`, `get_command`).
17. **`core/chat_export.py`** (новый) — `export_to_markdown`, `export_to_html`, `_plain_to_html`.
18. **`fs/document_extractors.py`** (новый) — `extract_document`, `is_document`, `language_for_document`, `SUPPORTED_EXTENSIONS`, `_ocr_pdf`.
19. **`agents/benchmark.py`** (новый) — `run_benchmark`, `BenchmarkPrompt`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`, `BENCHMARK_PROMPTS`.
20. **`tools/git_ops.py`** — `read_file_at_head`, `get_git_status_for_file`. **9.4** — `get_current_branch`, `get_branch_hash`, `get_uncommitted_diff`, `get_branch_diff`, `get_recent_commits`, `commit`. **10.9** — `create_snapshot`/`undo_snapshot` (с `snapshot_id`).
21. **`ui/widgets/`** (новые) — `auto_scroll.py::AutoScrollArea`, `status_dashboard.py::StatusDashboard`, `pipeline_selectors.py::PipelineSelectors`, `profiles_selector.py::ProfilesSelector`, `command_completer.py::CommandCompleter`, `command_palette.py::CommandPalette`, `collapsed_placeholder.py::CollapsedPlaceholder`. **2.5** — `live_events.py::LiveEventsPanel`. **9.5.3** — подключение к EventBus. **9.3.2** — иконки ролей, бейдж freedom.
22. **`ui/command_registry.py`** (новый) — `CommandRegistry`, `PaletteCommand`.
23. **`ui/dialogs/`** — новые: `agent_settings_dialog.py`, `benchmark_dialog.py`, `document_preview_dialog.py`, `export_chat_dialog.py`, `profile_editor_dialog.py`; **9.10** — `permissions_dashboard.py`; **4.3** — `memory_dialog.py`.
24. **`ui/theme.py`** — полностью переработан: `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `get_colors`, `get_stylesheet`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`.
25. **`ui/menus.py`** — добавлены сигналы/пункты: `export_chat_requested`, `profile_selected(str)`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`; **9.10** — `permissions_requested`.
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
    `core/services/search_service.py` (2.13), `core/services/browser_service.py` (2.11),
    `state/inbox.py` (2.12), `fs/document_extractors.py`, `fs/image_extractors.py` (Фаза 5),
    `fs/audio_extractors.py` (Фаза 5), `fs/video_extractors.py` (Фаза 5),
    `tools/test_ops.py` (10.2), `tools/browser_ops.py` (2.11), `tools/mcp_ops.py` (7),
    `tools/shell_ops.py::_ALLOWLIST_RE` (0.1.1), `ui/widgets/live_events.py` (2.5),
    `ui/widgets/*`, `ui/state/async_resource.py` (2.9), `ui/chat/backends.py`,
    `ui/chat/research.py`, `ui/dialogs/permissions_dashboard.py` (9.10),
    `ui/dialogs/memory_dialog.py` (4.3)).
27. **§5 «Пайплайн»** — убран устаревший роутинг «всегда ReAct»: research-вопросы уходят в `Orchestrator`, обычные — в `AgentLoop`; описан fallback при таймауте/ошибке. **2.10** — natural exit + soft budgets; **2.5** — все этапы эмитят события в `EventBus`, UI показывает их в `LiveEventsPanel`. **10.7** — Orchestrator для coding-задач использует расширенный флоу с `apply_patch`/`run_tests`/`fix_loop`. **10.8** — LSP-диагностика. **10.9** — stale check + snapshot.
28. **§6 «Соглашения»** — `requires-python >= 3.12`, `typing.override` в `wizard.py`.
29. **§7 «Как добавить инструмент»** — `tools/registry.py::ALL_TOOLS` как фактическая точка регистрации. **9.4** — `git_commit` добавлен в `ALL_TOOLS` (ASK_FIRST, AGENT-only, capability `git`). **10.1** — `apply_patch` (capability `fs.write`, `needs_snapshot=True`). **10.2** — `run_tests` (capability `shell`). **10.3** — `find_references` (capability `fs.read`). **2.11** — `browser_*` (capability `browser`). **2.13** — `web_search` chain.
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
    (0.1.4).
    **`[план]`:** `test_event_bus.py` (2.5), `test_policy.py` (2.7),
    `test_tool_spec.py` (2.6), `test_async_resource.py` (2.9),
    `test_redaction.py` (0.1.2), `test_slm_models_io.py` (9.3.3),
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
    policy: security > profile > session > config > decorator. **10.1** — `apply_patch`
    не пишет файл, если контекст не совпал; `write_file` — только для новых файлов.
    **10.2** — `run_tests` парсит вывод, но не гарантирует 100 % покрытия фреймворков;
    fallback — сырой stdout. **10.4** — verifier может «зациклиться» на регенерации;
    лимит — 1 повтор. **10.8** — LSP-сервер может отсутствовать → fallback на
    `py_compile`. **10.9** — stale check и снапшоты не блокируют, а защищают;
    снапшоты — `auto`, откат — `ask_first`; `snapshot_retention_days` — автоочистка.
    **2.10** — soft budgets не останавливают цикл, только предупреждают; `user_cancel` —
    единственная жёсткая остановка. **2.11** — режим A по умолчанию; C — только по явному
    opt-in с предупреждением. **2.13** — цепочка провайдеров, падение одного не роняет
    остальные; кэш обязателен. **4.3** — память локальна, шифрование опционально,
    пользователь может удалить любую запись. **9.12** — «open» с предупреждением и
    audit log.
```

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