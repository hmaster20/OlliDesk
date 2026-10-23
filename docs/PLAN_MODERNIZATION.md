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
| **Стриминг SLM (токены онлайн)** | ✅ | `agents/local_llm_client.py::_generate` эмитит `TextPart` на каждый токен + start/end (9.5) |
| **Живой трейс для Agent/Plan** | ✅ | `ChatThoughtStream` в Agent/Plan через `_ensure_thought_stream`; в Chat — плоский activity (9.5.3) |
| **Единая приборная панель статусов** | ✅ | `ui/widgets/status_dashboard.py::StatusDashboard` в статус-баре: backend/pipeline/tok/s/RAG/tools (9.7) |
| **SLM-first селекторы моделей** | ✅ | `ui/widgets/pipeline_selectors.py::PipelineSelectors` в верхней панели; глобальный переключатель SLM⇄Ollama в дашборде (9.3) |
| **Профили использования (Разработка / Исследование / Обучение)** | ✅ | `core/profiles.py`, `ui/widgets/profiles_selector.py`, `ui/dialogs/profile_editor_dialog.py`, меню `View → Профили` (9.4) |
| **PDF/DOCX/PPTX/XLSX/RTF + OCR + предпросмотр** | ✅ | `fs/document_extractors.py`, `ui/dialogs/document_preview_dialog.py`, `_DOC_LANGUAGES` в `VectorStore` (9.4/Фаза 5 частично) |
| **Дизайн-система (токены, полное покрытие QSS)** | ✅ | `ui/theme.py`: Darcula + тёплые акценты, QTableWidget/QHeaderView и т.д. (9.1) |
| **OCR изображений (JPG/PNG)** | ⚠️ | `rapidocr-onnxruntime` есть, подключение к drag&drop картинок — Фаза 5 |
| **Аудио/видео (STT)** | ❌ | `faster-whisper` + ffmpeg — Фаза 5 |
| **Vision-модели через Ollama** | ❌ | `qwen2.5-vl` / `llama3.2-vision` — Фаза 5 |
| **MCP** | ❌ | Фаза 7 |
| **Система плагинов** | ❌ | Фаза 7 |
| **Событийная модель сообщений (`MessagePart`)** | ✅ | `agents/ollama_client.py` (`TextPart`/`ReasoningPart`/`ToolCallPart`/`ToolResultPart`), `state/session_store.py` (колонка `parts_json`) |
| **Композитный пузырь сообщения** | ✅ | `ui/chat/message_item.py` (`ChatMessageItem` из `ReasoningWidget` + `TextWidget` + `ToolCallCard`) |
| **Выполнение shell-команд агентом** | ✅ | `tools/shell_ops.py::run_shell_command` (ASK_FIRST, AGENT-only, чёрный список, песочница cwd) |
| Компактор истории диалога | ✅ | `agents/history_compactor.py` (вызывается в `AgentLoop`; приоритет: reasoning → tool-результаты → старейшие реплики) |
| **Дифф-карточка `write_file`** | ✅ | `ToolCallCard._render_diff_mode` + `tools/file_ops.py::write_file` → dict `{diff, lines_added, lines_removed, written_content, ...}` (9.6.1) |
| **Автодополнение `@`-тегов** | ✅ | `ui/widgets/command_completer.py::CommandCompleter`, `core/chat_commands.py` (9.6.2) |
| **Экспорт чата в Markdown/HTML** | ✅ | `core/chat_export.py`, `ui/dialogs/export_chat_dialog.py`, `File → Экспорт чата…` (9.6.3) |
| **Менеджер сессий: preview + «Создать проект из сессии»** | ✅ | `ui/dialogs/session_manager_dialog.py` (9.6.4) |
| **Палитра команд `Ctrl+Shift+P`** | ✅ | `ui/command_registry.py`, `ui/widgets/command_palette.py`, `_register_palette_commands` (9.9) |
| **Виртуализация длинных чатов** | ✅ | `ui/widgets/collapsed_placeholder.py::CollapsedPlaceholder`, порог 80 (9.10) |
| **Авто-скролл с порогом 32 px** | ✅ | `ui/widgets/auto_scroll.py::AutoScrollArea` (9.5.5) |
| **Замеры производительности (TTFT/tok/s/load)** | ✅ | `agents/benchmark.py`, `ui/dialogs/benchmark_dialog.py`, `View → Замер производительности…` (4.7.B) |
| **Git-diff в редакторе (HEAD ↔ рабочая копия)** | ✅ | `tools/git_ops.py::read_file_at_head`/`get_git_status_for_file`, `EditorWidget.show_git_diff_for_file`, Ctrl+D (9.8) |
| **Вынос `panel.py` в миксины** | ✅ | `ui/chat/backends.py::BackendsMixin` + `ui/chat/research.py::ResearchMixin` (2.4) |
| Тесты UI / state | ✅ | `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py` |

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
      **В Фазе 9 профили (9.4) добавляют визуальный редактор `tool_policies`** (9.6).
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

### 4.6.C — Позже: новые подсистемы

- [~] **Reflexion-критик для Final Answer** (`React.md` 4.1): для Orchestrator это уже
      сделано — `agents/critic.py` (3B-проверка структуры/формата) и `agents/verifier.py`
      (код-проверка путей/символов/evidence) вызываются в `Orchestrator.run()`. Для
      `AgentLoop` (обычные чат-ответы) — по-прежнему не подключено.
- [~] **Structured trace агента** (`React.md` 8.1): в research-режиме есть
      `last_research_summary` и `ChatResearchPassport` с прочитанными файлами, поисками,
      гипотезами. Полноценного трейса (итерация → действие → длина observation →
      длительность) с кнопкой «Показать трейс» — нет. **Связано с 9.5 (живой трейс).**
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
      **Связано с 9.7 (приборная панель).**
- [x] **Multi-tool-call у SLM** (`React.md` 5.1): `LocalLLMClient._parse_tool_calls`
      возвращает список из `extract_tool_calls` (JSON + function-call), в `chat()` все
      вызовы уходят одним `ChatChunk(part=ToolCallPart(calls=[...]), event_type="tool_call")`.
      `AgentLoop` через `pending_tool_calls.extend(part.calls)` выполняет все.

### 4.6.D — Удалить (проверить перед удалением)

- [x] `RouterChatThread` (`React.md` 1.3, 6.3) — **уже удалён** вместе с
      `OllamaChatThread`; в `ui/chat/threads.py` его нет. Задача закрыта.
- [ ] `ui/dialogs/model_manager_dialog.py::PullThread` — дублирует `_DownloadThread`
      из `slm_models_dialog.py`; объединить.
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
(9.4)** — отдельные тумблеры не нужны.

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
токенам вынесен в 9.5 и уже сделан**.

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
      пузыря. **Стриминг SLM по токенам — сделан в 9.5.**
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
      сразу при распознавании (после стрима текста). См. **9.5**.

---

## Фаза 5 — Мультимодальность и документы

- [ ] Распознавание скриншотов: `rapidocr-onnxruntime` уже в зависимостях — подключить к
      drag&drop в чат (`ChatMessage.images` поле в модели есть).
- [x] **Распознавание документов (PDF, DOCX, PPTX, XLSX, RTF)** для профиля
      «Исследование» (9.4): `fs/document_extractors.py::extract_document` — извлечение
      текста через `pdfplumber`/`pypdf`, `python-docx`, `python-pptx`, `openpyxl`,
      `striprtf`; **отдельная коллекция `docs`** (см. `_DOC_LANGUAGES` в
      `fs/vector_store.py`); **предпросмотр** перед отправкой — `ui/dialogs/document_preview_dialog.py`
      (`ChatPanel._attach_document`). Опциональные зависимости — `[project.optional-dependencies].docs`.
- [x] **OCR сканов PDF** (через `rapidocr-onnxruntime` + `pdf2image` + Poppler):
      `fs/document_extractors.py::_ocr_pdf`, включается явно (`use_ocr=True`),
      кнопка «🔍 Запустить OCR» в `DocumentPreviewDialog`. По умолчанию выключен
      (`AppConfig.document_ocr = False`).
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
- [~] Кнопка «Скрепка» (прикрепить файлы/пути) в чате — `@file`/`@dir`/`@path` работают;
      drag&drop — не реализован. **Связано с 9.6.**
- [~] Поддержка мультимедиа в прикреплениях: `@file` и прикреплённые файлы, хранение
      метаданных (тип, размер), отображение превью. Документы — с предпросмотром;
      аудио/видео/картинки — не реализованы.
- [ ] База мультимедиа-экстрактов: кэширование транскрипций/OCR, чтобы не дублировать
      обработку одних и тех же файлов.
- [x] PDF/DJVU: извлечение текста через `pdfplumber`/`pypdf`, индексация. **Связано с 9.4
      (профиль «Исследование»).** DJVU — не реализован (нужен `djvulibre`).
- [x] Показать пользователю распознанный текст перед отправкой в модель —
      `DocumentPreviewDialog`.

**Предупреждение для крупных моделей (32B/70B и в SLM, и в Ollama):** «модель
требовательна, ответы придётся ждать»; отдельный селектор «большая модель для
обучения» — в **9.4.1**.

Генерация изображений (из `HARD.md`: SD1.5 / SDXL `--lowvram` через ComfyUI / SD WebUI
Forge) — **вне объёма OlliDesk**: отдельный движок и внешние окна. Приложение занимается
только распознаванием (OCR/Vision/STT), синтезом — нет.

---

## Фаза 6 — UX: быстрые правки (перенесено в Фазу 9)

**Основная переработка UI/UX — в Фазе 9 (закрыта).** Здесь остаются только мелкие правки,
которые имеет смысл сделать параллельно, с сохранением оригинальных формулировок:

- [x] **F4 — скрыть/показать панель структуры проекта** (только левую панель, редактор
      не трогаем), `ui/main_window.py::_toggle_project_structure`. **Перенесено в 9.2**
      (дерево по умолчанию скрыто, переключатель на виду — кнопка «📁» в ChatPanel).
- [x] **Подсветка `@`-команд в поле ввода** (`@web`, `@dir`, `@file`, `@path`) —
      `ui/chat/panel.py::_highlight_web_command` через `COMMAND_TAGS` из
      `core/chat_commands.py`. **Автодополнение — 9.6.2** (реализовано).
- [x] **Доработка отображения рассуждений модели (`reasoning_content`)** — реализовано
      через `ReasoningWidget` (4.8.B) + тумблер в `AgentSettingsDialog` (9.2). **Тумблер
      перенесён в поповер «⚙»** (9.2); сворачиваемый блок, инкрементальное обновление,
      `_show_reasoning` в `ProjectSettings` — сохранены.
- [x] **Автодополнение и подсказки `@`-тегов** — реализовано в 9.6.2:
  - [x] реестр команд в одном месте: `core/chat_commands.py` (`ALL_COMMANDS`,
        `COMMAND_TAGS`, `ChatCommand`, `DEFAULT_PLACEHOLDER`);
  - [x] описание команд: `ChatCommand.hint` + `placeholder`;
  - [x] `QCompleter` с `CompletionMode.PopupCompletion` на `QStandardItemModel`
        (`ui/widgets/command_completer.py::CommandCompleter`);
  - [x] показ popup только при `@` в начале строки (`detect_active_command`);
  - [x] перехват `Tab`/`Enter`/`Up`/`Down`/`Esc` на время автодополнения
        (`ChatPanel.eventFilter`);
  - [x] автовставка пробела после выбранного тега и позиционирование курсора
        (`_on_command_completed`);
  - [x] placeholder показывает подсказку команды, если набран `@tag `
        (`_update_command_placeholder`).
- [x] **Лишние пустые строки в облаке сообщения пользователя** — `ChatMessageItem._ensure_text_widget`
      ставит `document().setDefaultStyleSheet("p { margin: 0; }")` для `role in ("user", "system")`.
- [ ] **Прогресс индексации:** ETA, очередь файлов, возможность отмены.
      **Перенесено в 9.7.**
- [x] **Менеджер сессий:** клик → preview; экспорт (через `core/chat_export.py`);
      «создать проект из сессии»; удаление. **Реализовано в 9.6.4.**
- [x] **Экспорт чата в Markdown/HTML.** `core/chat_export.py`,
      `ui/dialogs/export_chat_dialog.py`, `File → Экспорт чата…` (`Ctrl+Shift+X`).
- [x] **Диалог настроек:** визуальный редактор `tool_policies` — реализован через
      профили (9.4) и `AgentSettingsDialog` (9.2). Ползунки температуры/top_p,
      num_ctx — в `AgentSettingsDialog`.
- [-] **Сдвинуть панель чата влево** (из `Roadmap.md`) — **отменено**: дерево слева
      скрыто по умолчанию (9.2), панель чата остаётся основной.
- [ ] **Цвет заголовка окна при тёмной теме:** на Windows нативный title bar не наследует
      stylesheet — варианты: frameless window + собственный title bar, либо
      `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)`. **Перенесено в 9.1.**
- [x] **Иконки-индикаторы в чате:** RAG, Tools, web_search — «что сейчас включено».
      Единая приборная панель `StatusDashboard` (9.7): backend, pipeline, tok/s,
      RAG, tools.

---

## Фаза 7 — Расширяемость

- [ ] **MCP** — клиент MCP-серверов (stdio), чтобы системные промпты/инструменты приходили
      извне (`Roadmap.md`).
- [ ] **Система плагинов** для кастомных инструментов: точка входа `tools/plugins/*.py`,
      манифест, изоляция ошибок. **Связано с профилями (9.4):** каждый профиль =
      набор skills (инструкции + инструменты), плагины — источник кастомных skills.
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
- [ ] Релизный пайплайн (GitHub Actions): сборка `OllDesk.exe`, публикация в Releases.
- [ ] Проверить `ollidesk.spec`: актуальные `hiddenimports`, размер, `llama_cpp` и `chromadb`.
- [ ] README: синхронизировать с реальным состоянием (упоминает устаревшее `scripts/download_monaco.py`).

---

## Фаза 9 — Редизайн UI/UX (SLM-first, живой трейс, единая тема, профили)

**Контекст.** Приоритет бэкендов сместился на локальные SLM (planner GPU + analyst CPU),
но UI остался Ollama-центричным: `model_combo` в чате заполняется из `ModelRegistry`
(Ollama), SLM planner/analyst спрятаны в `View → Локальные модели`, `LocalLLMClient`
не стримит токены (отдаёт один `ChatChunk` в конце), тёмная тема не стилизует
`QTableWidget`/`QHeaderView` — «болят глаза» в `SLMModelsDialog` и `ModelManagerDialog`.
Статусы размазаны по четырём местам (`status_bar`, верхняя панель, `status_label`,
`tool_status_label`).

**Цели:**
- SLM-first: селекторы Planner/Analyst в верхней панели, глобальный переключатель
  SLM/Ollama в статус-баре.
- Живой трейс: токены + tool calls стримом, без авто-сворачивания, авто-скролл с
  кнопкой «▼ Вниз».
- Единая тёмная тема (Darcula-основа + тёплые акценты янтарный/терракотовый),
  полное покрытие QSS.
- Профили использования: Разработка / Исследование / Обучение — ручной селектор в
  верхней панели; каждый профиль = набор skills (инструкции + инструменты +
  системный промпт + настройки RAG/нуджей).
- Все модели локальные: SLM или Ollama, без облака.
- Меньше «поиска по меню»: частое — на виду, редкое — в палитре команд
  (Ctrl+Shift+P).

### 9.1 Дизайн-система и темы
- [x] Токены (палитра, типографика, отступы, радиусы) в `ui/theme.py`; QSS собирается
      из них — одна точка правды. `_build_qss(ColorTokens, TypographyTokens, SpacingTokens, RadiusTokens)`.
- [x] Палитра: **Darcula** как основа тёмной темы + тёплые акценты (янтарный/терракотовый)
      вместо синего. Светлая — нейтральная. **Контраст WCAG AA подтверждён**
      (`tests/ui/test_theme_tokens.py`), при багфиксе изменены:
      `LIGHT_COLORS.accent=#9e5318` (5.57:1 с белым), `selection_bg=#9e5318`,
      `accent_hover=#b8651f`, `accent_pressed=#7a3f12`.
- [x] Стилизованы **все** интерактивные элементы: `QTableWidget`, `QHeaderView`,
      `QTableView`, `QTableCornerButton`, `QComboBox` popup, `QMenu`, `QToolTip`,
      `QScrollBar`, `QTabBar`, `QSplitter::handle`, `QGroupBox`, `QCheckBox`,
      `QRadioButton`, `QSpinBox`/`QDoubleSpinBox`, `QProgressBar`, `QListWidget`,
      `QLineEdit`, `QTreeView`, `QTreeWidget`. **Таблицы — приоритет №1** —
      покрыты (`QTableWidget`, `QTableView`, `QHeaderView::section`,
      `QTableCornerButton::section`).
- [x] QSS применяется до создания окон (`OlliDeskApp._apply_theme` до `MainWindow`),
      палитра и QSS из одного источника (`get_colors` + `get_stylesheet`).
- [ ] Авто-детект темы ОС + ручное переключение в настройках; проверка контраста.
- [ ] Цвет заголовка окна в тёмной теме: на Windows нативный title bar не наследует
      stylesheet — `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)` (предпочтительно)
      либо frameless + собственный title bar.

### 9.2 Главное окно: компоновка и навигация
- [x] Дерево проекта слева, **скрыто по умолчанию**, переключатель на виду — кнопка
      «📁» в верхней панели ChatPanel (`ChatPanel.project_tree_toggled` →
      `MainWindow._on_project_tree_toggled`). Состояние — `AppConfig.show_project_tree`.
      F4 остаётся (синхронизирован с кнопкой).
- [x] Правая панель настроек агента → **поповер «⚙»** (`ui/dialogs/agent_settings_dialog.py::AgentSettingsDialog`,
      открывается через `ChatPanel.agent_panel_toggle` → `MainWindow._toggle_side_panel`).
- [~] Вкладки: «Чат» (основная), «Редактор» (только при открытом проекте).
      Сплит «чат + редактор» — не реализован (отложено).
- [x] Убраны дубли статусов — единая приборная панель (9.7).

### 9.3 Модели и пайплайн (SLM / Ollama, Planner / Analyst)
- [x] **Верхняя панель:** два селектора `Planner` и `Analyst` (`ui/widgets/pipeline_selectors.py::PipelineSelectors`)
      + кнопка «⋯» (открывает `SLMModelsDialog`). Статусы ✅ установлена / ⬇️ не скачана.
- [x] **Глобальный переключатель бэкенда** `SLM (локально)` / `Ollama (локально)` —
      кликабельная иконка `backend` в `StatusDashboard` → `MainWindow._toggle_backend`.
      Обычный чат **отдельного селектора не получает**.
- [x] Явное переключение на Ollama — с предупреждением `QMessageBox` при первом включении.
- [x] Индикатор пайплайна (⏳/⚡/⚠️/💤) с tooltip: `PipelineSelectors.set_state` +
      `_pipeline_tooltip`.
- [x] `tok/s` и `TTFT` из `last_stats` после каждой генерации — `_on_stream_finished_event`
      → `StatusDashboard.set_tok_per_sec`.
- [x] `SLMModelsDialog`: убраны дублирующие селекторы planner/analyst, оставлена
      инфо-строка «Пайплайн настраивается в верхней панели чата».

### 9.4 Профили использования (Разработка / Исследование / Обучение)
- [x] `AppConfig.profiles` + встроенные пресеты (`core/profiles.py::_DEFAULT_PROFILES`):
      **Разработка** (agent, tools enabled, RAG code, `recommended_model="qwen2.5-coder-7b-instruct-q6_k"`),
      **Исследование** (plan, write/git/shell excluded, RAG docs+code,
      `recommended_model="qwen2.5-7b-instruct-q6_k"`), **Обучение** (chat, все tools excluded,
      RAG off, `show_reasoning=False`, `recommended_model="qwen2.5-14b-instruct-q4_k_m"`).
      `_sync_builtin_profiles` (валидатор `AppConfig`) — синхронизирует встроенные
      с кодом, сохраняет пользовательские.
- [x] Переключение — **селектор в верхней панели** (`ui/widgets/profiles_selector.py::ProfilesSelector`),
      **ручное**.
- [x] Профиль применяется через `ChatPanel._apply_profile`:
      `mode_combo`, `set_tool_policy` для каждого инструмента, `_profile_rag_enabled`,
      `_project_rules` подмешивает `system_prompt` профиля, `_profile_agent_max_iters` /
      `_profile_chat_max_iters`, `reset_pipeline()` после смены.
- [x] **Профиль «Исследование»** — документы: `fs/document_extractors.py` (PDF/DOCX/PPTX/XLSX/RTF),
      OCR сканов (`rapidocr-onnxruntime` + `pdf2image`), `DocumentPreviewDialog` —
      предпросмотр перед прикреплением, `AppConfig.document_ocr` (по умолчанию `False`),
      `RagSearchThread(categories=...)` + `VectorStore.search(categories=...)`.
      `_DOC_LANGUAGES` расширены pdf/docx/pptx/xlsx/rtf. `AppConfig`:
      `index_documents`, `max_document_size_mb`, `document_ocr`.
- [x] **Профиль «Обучение»** — `_ensure_orchestrator` не грузит двухмодельный пайплайн,
      `reset_pipeline()` при переключении; кнопка «➜ 🛠» (`back_to_development_btn`)
      для возврата одной кнопкой.
- [x] **9.4.1 Крупные модели:** `recommended_model` подстветка (★) в analyst-селекторе
      через `PipelineSelectors.set_recommended_model`. Для «Обучения» рекомендован
      `qwen2.5-14b-instruct-q4_k_m`.
- [x] `ProfileEditorDialog` — редактор пользовательских профилей (клон/удаление/сброс
      встроенных), меню `View → Профили` (checkable) + `View → Редактор профилей…`.
      Багфикс: `_on_profile_selected` сохраняет текущую форму перед переключением;
      `_clone_profile` сохраняет форму перед клонированием.

### 9.5 Живой трейс и стриминг SLM (приоритет №1)
- [x] **Стриминг `LocalLLMClient`**: `_generate` отдаёт `ChatChunk(part=TextPart(text=content),
      event_type="delta")` на каждый токен; `start`/`end` по границам; tool calls — после
      стрима (распознавание возможно только на полном тексте).
- [x] `AgentLoop` обрабатывает события — `on_text_delta`/`on_reasoning_delta`/
      `on_stream_start`/`on_stream_end` доходят до `ChatPanel._on_chunk`/`_on_thinking`.
- [x] `ChatThoughtStream` — не только research, но и **Agent/Plan**:
      `_ensure_thought_stream` создаёт при первом tool-call/status; в Chat — плоский
      activity. **Не сворачивается автоматически** — пользователь решает.
- [x] Индикатор «печатает / думает / выполняет» в статус-строке и на кнопке Stop —
      `_set_activity_state` (`thinking`/`tool`/`streaming`/`error`/`cancelled`/`idle`),
      `_STOP_BUTTON_BG` меняет фон кнопки Stop (янтарный/синий/зелёный/тёмно-красный/серый).
- [x] Tool calls — карточками в пузыре **сразу, до выполнения** (`_on_tool_started` →
      `_ensure_assistant_widget().append_tool_call`); `AgentLoop` вызывает `on_tool_start`
      до `ask_first`. Результат — ✓/✗ и «▸ Показать полностью» для длинных observation
      (`_truncate_preview`, `_full_browser`).
- [x] **Авто-скролл:** `ui/widgets/auto_scroll.py::AutoScrollArea` — порог 32 px,
      кнопка «▼ Вниз», sticky-режим. Используется в ChatPanel.

### 9.6 Чат и сообщения
- [x] Композитный пузырь: `ReasoningWidget` + `TextWidget` + `ToolCallCard`.
      **Карточка `write_file` — diff-сводка** (`+N −M`), три кнопки:
      **«📂 Открыть в редакторе»**, **«🔍 Показать diff»** (Monaco DiffEditor),
      **«↩ Откатить»** (с подтверждением). `tools/file_ops.py::write_file` возвращает
      `dict{_llm_text, status, path, is_new_file, original_content, written_content, diff,
      lines_added, lines_removed, syntax_error}`; `ToolResult.metadata` прокидывается
      через `AgentLoop.on_tool_result` → `AgentChatThread.tool_executed(str, str, bool, object)`.
- [x] **Поле ввода:** автодополнение `@`-тегов (`CommandCompleter`), Ctrl+Enter / Shift+Enter,
      подсветка `@`-тегов, placeholder подсказки. Drag&drop и «Скрепка» — не реализованы
      (связано с Фазой 5).
- [x] **Stop/Continue** — заметные, с состоянием. Цвет фона Stop по `_STOP_BUTTON_BG`.
- [x] **Markdown:** убраны лишние отступы в пузыре пользователя; таблицы под тему
      (`_render_table(dark=...)`, `TextWidget.set_dark`, авто-детект по палитре).
- [x] Менеджер сессий: клик → preview (`_on_selection_changed` + `QTextBrowser`),
      экспорт (Markdown/HTML/TXT через `core/chat_export.py`), «Создать проект из
      сессии» (`.ollidesk/chat_history.md`, `AGENTS.md`, обновление `recent_projects`).

### 9.7 Статус-бар и приборная панель
- [x] **Статус-бар внизу — всегда на виду:**
      `ui/widgets/status_dashboard.py::StatusDashboard`:
      - **бэкенд** (SLM/Ollama/Резерв) кликабельно для переключения;
      - **pipeline** (planner + analyst) кликабельно для управления SLM;
      - **tok/s**;
      - **RAG** (кол-во чанков / статус);
      - **tools** (активность текущего запроса).
      На каждом элементе — tooltip.
- [~] **Активность над полем чата** (`tool_status_label`) — оставлен как transient
      для текущего запроса; дублирования с дашбордом нет (tools — глобально,
      tool_status_label — про текущий прогон).
- [x] Убраны `ollama_status_label`/`backend_status_label`/`pipeline_status_label`/
      `status_label` как отдельные сущности — сведены в `StatusDashboard`.
      Удалён мёртвый `_update_ollama_status`; `_on_ollama_checked` реализован.
- [ ] **Прогресс индексации**: ETA, очередь файлов, отмена.

### 9.8 Редактор и diff
- [x] Редактор — **отдельная вкладка** (EditorWidget с собственным QTabBar).
- [x] `write_file` из агента: карточка в чате с diff-сводкой и кнопками
      «📂 Открыть» / «🔍 Показать diff» / «↩ Откатить» (9.6.1).
- [x] Подсветка изменённых файлов в дереве (`GitStatusDelegate`, 2.3), клик —
      diff в редакторе при `AppConfig.open_modified_as_diff=True`
      (`FileTreeController._on_clicked` + `_is_file_modified`).
- [x] `EditorWidget.show_git_diff_for_file` (HEAD ↔ рабочая копия) через
      `tools/git_ops.py::read_file_at_head` + `get_git_status_for_file`.
      `show_diff_from_texts` для diff-карточки. Горячая клавиша `Ctrl+D` в редакторе.

### 9.9 Горячие клавиши и палитра команд
- [x] **Ctrl+Shift+P — палитра команд** (`ui/command_registry.py::CommandRegistry`,
      `ui/widgets/command_palette.py::CommandPalette`):
      «Открыть проект», «Переключить профиль», «Переключить бэкенд»,
      «Показать/скрыть дерево», «Новый чат», «Настройки SLM», «Замер
      производительности», «Экспорт чата», «Редактор промптов», «Менеджер сессий»,
      «Переключить тему», «Настройки агента», «Выход», «О программе».
      Глобальный shortcut через `QShortcut` + пункт меню `View → Палитра команд…`.
- [x] **Глобальные:** `Ctrl+O`, `Ctrl+Shift+C/E`, `F4`, `Ctrl+T`, `Ctrl+Shift+P`,
      `Ctrl+Shift+X` (экспорт). F2/Del/Ctrl+C/X/V/N/R — только в фокусе дерева
      (`Qt.ShortcutContext.WidgetWithChildrenShortcut` на `self.tree`).

### 9.10 Производительность UI
- [x] **Виртуализация длинных чатов** через `CollapsedPlaceholder`
      (`ui/widgets/collapsed_placeholder.py`): при >80 «месседж-виджетов» старые
      сворачиваются в placeholder «📜 N сообщений — ▸ показать». Клик разворачивает
      порцию (`_RESTORE_BATCH=40`) — самые свежие. Порядок сохраняется.
      `_suppress_collapse` в `load_history` — один проход O(n) вместо O(n²).
- [x] Ленивая загрузка reasoning/tool-карточек: свернуты по умолчанию (`ReasoningWidget`
      `collapse()` в `finalize()`); tool-card «▸ Показать полностью» — по клику.
- [x] Не блокировать GUI-поток: стриминг SLM в отдельном потоке, `_BenchmarkThread`,
      `_ModelPoolLoadThread` — везде `QThread`, GUI не блокируется.

### 9.11 Минимальные тесты UI
- [x] `tests/ui/test_auto_scroll.py` — 12 кейсов: порог 32 px, sticky↔not-sticky,
      `scroll_to_bottom_if_sticky`, `scroll_to_bottom_force`, `set_auto_scroll_enabled`,
      `stick_changed`. Без `pytest-qt` — QApplication в module-scope.
- [x] `tests/ui/test_theme_tokens.py` — ~50 кейсов: валидность hex, 15 пар WCAG AA × 2 темы
      (30 проверок), 45 QSS-селекторов × 2 темы (90 проверок), «QSS собирается из
      токенов», «нет хардкод-hex в QSS», `_contrast_ratio` sanity (21:1 для white/black).
- [x] `pytest-qt` **не подключаем** — по плану 9.11. UI-тесты используют
      `QApplication.instance() or QApplication([])` в module-scope фикстуре.

### 9.12 Порядок работ в Фазе 9
**Все шаги 1–10 закрыты** (см. 9.1–9.11 + 2.4). Фаза 9 завершена.
Остались задачи вне порядка 9.12:
- Цвет заголовка окна (Windows) — открытая косметика.
- Авто-детект темы ОС — открытая фича.
- Drag&drop в поле ввода — Фаза 5.

---

## Приоритеты и порядок

1. **Фаза 2.1** (протокол бэкенда — типизация `vector_store`/`search_cache`/`hypotheses`)
   — фундамент для Фаз 5, 7. Фаза 2.2 (реестр инструментов) завершена: `_build_registry`
   работает через `tools.registry.ALL_TOOLS`, `chat_auto_tools` в `AppConfig`,
   `is_mutating`/`category` в `@tool`.
2. **Фаза 9** (редизайн UI/UX) — **закрыта** (см. разделы 9.1–9.11).
3. **Фаза 2.4** (`panel.py` → `backends.py` + `research.py`) — **закрыта**:
   миксины `BackendsMixin` + `ResearchMixin`, MRO `ChatPanel(QWidget, BackendsMixin,
   ResearchMixin)`, тест `test_panel_mro.py`.
4. **Фаза 6** (UX) — закрыта через Фазу 9.
5. **Фазы 5, 7** — по остаточному времени.

**Отдельно — порядок по локальным моделям** (Фаза 4.7). Ключевая поправка к порядку
фаз: надёжность ReAct (4.6) идёт **до** интеллектуального выбора моделей. Стратегии
выбора **поглощены профилями (9.4)** — отдельный тумблер не нужен.

1. **4.7.B** (замеры) — **инструмент готов** (`agents/benchmark.py`, `View → Замер
   производительности…`). Цифры собираются пользователем на его железе; инструмент
   экспортирует CSV/Markdown для `docs/BENCHMARK.md`.
2. **4.7.D** (ModelPool обёрткой, модель закреплена за сессией) — базовая часть уже
   сделана (planner/analyst), осталась стратегия «модель за сессией».
3. **4.7.E** (классификатор) — только если замеры покажут нехватку.

**Отдельно — порядок по интерфейсу агента** (Фаза 4.8). Завершены модель данных
(`MessagePart`), композитный пузырь, `run_shell_command`, метаданные инструментов,
**стриминг SLM по токенам (9.5)**, **серый трейс в Agent/Plan (9.5.3)**,
**tool-cards до выполнения (9.5.7)**.

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
- [ ] Кэш web-поиска: расширить на RAG-ответы (общий `SearchCache` с namespace).
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
- **4.8.D** — метаданные `is_mutating`/`category` в `@tool`.

Кросс-ссылки в оставшихся разделах обновлены: диагностическая таблица (без «см. 1.5»), Фаза 4.6 (без пункта «Сейчас»), Фаза 4.7 (без «4.7.C»), Фаза 4.8 (без 4.8.A–D), «Приоритеты» (без `run_shell_command`-параграфа), «Быстрый бэклог» (без ссылок на 1.2/1.3).

---

## AGENTS.md — что изменилось

Обновил сохранение структуры и форматирования. Основные правки:

1. **Диагностика / Статус SLM-стриминга**: `LocalLLMClient._generate` стримит — реализовано (9.5).
2. **`agents/local_llm_client.py`** — стриминг `TextPart` на каждый токен, `start`/`end`, tool-calls после стрима; `_DEFAULT_TEMPERATURE = 0.0` + `_TEMPERATURE_BY_OP`.
3. **`ui/chat/panel.py`** → миксины `BackendsMixin` (`ui/chat/backends.py`) + `ResearchMixin` (`ui/chat/research.py`). Поля — в `ChatPanel.__init__`, MRO — QWidget первым.
4. **`ui/chat/message_item.py`** — `ToolCallCard` теперь с diff-режимом для `write_file` (📂/🔍/↩), `_truncate_preview`, `_escape_diff`.
5. **`fs/vector_store.py`** — `search(categories=...)` (пост-RRF фильтр по `code`/`docs`), `_DOC_LANGUAGES` включает pdf/docx/pptx/xlsx/rtf.
6. **`agents/history_compactor.py`** — три ступени (reasoning → tool → реплики).
7. **`agents/tool_registry.py`** — `ErrorCategory`, `ToolResult.error_category`, `ToolResult.metadata`, `is_mutating`/`category`, приём `dict` от инструментов.
8. **`tools/shell_ops.py`** — `run_shell_command` (ASK_FIRST, AGENT-only, `_BLACKLIST`, песочница, `MAX_OUTPUT_CHARS=5000`, `MAX_TIMEOUT_S=300`).
9. **`state/session_store.py`** — `parts_json` + идемпотентная миграция.
10. **`ui/main_window.py`** — контроллеры `FileTreeController`/`IndexingController`/`AppMenuBar`, `_do_check_ollama` на `_OllamaCheckThread`, `_stop_all_threads` без `new_event_loop`, `_toggle_backend`, `_on_project_tree_toggled`, `_on_ollama_checked`, `_toggle_side_panel` → `AgentSettingsDialog`, `_open_benchmark`, `_open_profile_editor`, `_on_show_diff`, `_install_global_shortcuts`, `_register_palette_commands`, `_on_menu_profile_selected`, `_export_chat`, `_show_file_diff`.
11. **`ui/chat/threads.py`** — `_BenchmarkThread`, `AgentChatThread.tool_executed` с 4-м аргументом `metadata`, `_ModelPoolLoadThread` с per-role timeout 90 с.
12. **`ui/dialogs/tool_confirmation_dialog.py`** — `_coerce_value`, `QTableWidget`-редактор аргументов.
13. **`core/sandbox.py`** — `SandboxedPathResolver`.
14. **`core/config.py`** — `profiles`/`active_profile`/`show_project_tree`/`show_pipeline_selectors`/`open_modified_as_diff`/`index_documents`/`max_document_size_mb`/`document_ocr`; `_GLOBAL_ONLY_KEYS` расширен; `_sync_builtin_profiles` (валидатор).
15. **`core/profiles.py`** (новый) — `ProfileConfig`, `ProfileToolPolicies`, `_DEFAULT_PROFILES`, `default_profiles()`, `builtin_profile_ids()`, `get_default_profile()`, `get_profile_by_id()`. `recommended_model` в каждом встроенном профиле.
16. **`core/chat_commands.py`** (новый) — реестр `@`-команд (`ALL_COMMANDS`, `COMMAND_TAGS`, `detect_active_command`, `match_commands`, `get_command`).
17. **`core/chat_export.py`** (новый) — `export_to_markdown`, `export_to_html`, `_plain_to_html`.
18. **`fs/document_extractors.py`** (новый) — `extract_document`, `is_document`, `language_for_document`, `SUPPORTED_EXTENSIONS`, `_ocr_pdf`.
19. **`agents/benchmark.py`** (новый) — `run_benchmark`, `BenchmarkPrompt`, `BenchmarkResult`, `BenchmarkRow`, `LoadTimeRow`, `save_csv`, `to_markdown`, `BENCHMARK_PROMPTS`.
20. **`tools/git_ops.py`** — `read_file_at_head`, `get_git_status_for_file`.
21. **`ui/widgets/`** (новые) — `auto_scroll.py::AutoScrollArea`, `status_dashboard.py::StatusDashboard`, `pipeline_selectors.py::PipelineSelectors`, `profiles_selector.py::ProfilesSelector`, `command_completer.py::CommandCompleter`, `command_palette.py::CommandPalette`, `collapsed_placeholder.py::CollapsedPlaceholder`.
22. **`ui/command_registry.py`** (новый) — `CommandRegistry`, `PaletteCommand`.
23. **`ui/dialogs/`** — новые: `agent_settings_dialog.py`, `benchmark_dialog.py`, `document_preview_dialog.py`, `export_chat_dialog.py`, `profile_editor_dialog.py`.
24. **`ui/theme.py`** — полностью переработан: `ColorTokens`/`TypographyTokens`/`SpacingTokens`/`RadiusTokens`, `_build_qss`, `get_colors`, `get_stylesheet`, `DARK_STYLESHEET`/`LIGHT_STYLESHEET`.
25. **`ui/menus.py`** — добавлены сигналы/пункты: `export_chat_requested`, `profile_selected(str)`, `profile_editor_requested`, `benchmark_requested`, `command_palette_requested`.
26. **§4 «Пакеты»** — синхронизировано с текущим кодом (`fs/intel/`, `agents/orchestrator.py`, `agents/evidence.py`, `agents/verifier.py`, `agents/critic.py`, `agents/benchmark.py`, `core/profiles.py`, `core/chat_commands.py`, `core/chat_export.py`, `fs/document_extractors.py`, `ui/widgets/*`, `ui/chat/backends.py`, `ui/chat/research.py`).
27. **§5 «Пайплайн»** — убран устаревший роутинг «всегда ReAct»: research-вопросы уходят в `Orchestrator`, обычные — в `AgentLoop`; описан fallback при таймауте/ошибке.
28. **§6 «Соглашения»** — `requires-python >= 3.12`, `typing.override` в `wizard.py`.
29. **§7 «Как добавить инструмент»** — `tools/registry.py::ALL_TOOLS` как фактическая точка регистрации.
30. **§9 «Тесты»** — актуальный список покрытых модулей (см. `tests/`): `test_auto_scroll.py`, `test_theme_tokens.py`, `test_tool_call_card.py`, `test_activity_state.py`, `test_command_registry.py`, `test_collapsed_placeholder.py`, `test_chat_commands.py`, `test_chat_export.py`, `test_document_extractors.py`, `test_benchmark.py`, `test_git_ops.py`, `test_file_ops_diff.py`, `test_panel_mro.py`, `test_profiles.py`, `test_tool_confirmation_coerce.py`, `test_project_settings.py`, `test_session_store.py`, `test_sandbox.py`, `test_shell_ops.py`.
31. **§10 «Документация»** — без изменений в структуре (все файлы актуальны).
32. **§12 «Подводные камни»** — дополнено: CPU-only analyst cap `n_ctx=8192`, `MessagePart`/`with_content`/`model_copy`-ограничение, `run_shell_command` — единственный инструмент с побочными эффектами вне файлов проекта, `_suppress_collapse` в `load_history` (один проход O(n)), `CollapsedPlaceholder` — виджеты остаются в памяти (только удалены из layout).