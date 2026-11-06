# План модернизации OlliDesk 2.0 — оставшиеся задачи

Рабочий документ: **только то, что не сделано**. Полностью завершённые фазы удалены,
чтобы файл оставался читаемым. Полная историческая картина — в git (тэг `v0.1.2` фиксирует
закрытие Фазы 0.1–10, кроме перечисленного ниже).

**Актуальная ветка:** `next_generation`. **Целевая платформа:** Windows 10/11, Python 3.12.

**Стек не меняем.** Python + PySide6 + llama.cpp. Инвестируем в **профилирование**
(`py-spy`) и **изоляцию процессов** (`multiprocessing`/`QProcess`) для долгих задач
(индексация, LSP), чтобы GUI не залипал.

**Миссия и контекст 2026 (Россия, Москва):** OlliDesk создается как автономный инструмент
для работы, обучения и общественно полезных задач в условиях отсутствия интернета и
санкционных ограничений. Платные API поиска (Google CSE, Brave) недоступны, поэтому
уже реализована цепочка провайдеров с fallback и три режима браузера — расширять эти
поверхности не нужно, только поддерживать. OlliDesk не делится на версии «Lite» или «Pro» —
он должен сам анализировать характеристики устройства (RAM/VRAM/CPU) и адаптироваться,
предлагая пользователю правильные модели и настройки.

**Референсное железо для разработки:** Dell Precision 7760 (Intel Core i7-11800H,
**128 ГБ RAM**, **NVIDIA T1200 Laptop GPU 4 ГБ VRAM**). Это НЕ жесткое требование, а
полигон для проверки сценариев: 4 ГБ VRAM достаточно для быстрого 7B-кодинга, а 128 ГБ RAM
позволяют запускать 32B-reasoning-модели (DeepSeek-R1) на CPU для анализа документов.

Легенда: `[ ]` не начато · `[~]` в работе · `[x]` сделано · `[-]` отменено

---

## Что уже сделано (краткая выжимка)

Полный OlliDesk 2.0 реализован — EventBus, ToolSpec v2, Policy Engine, Application
Services (4 из 5, `ChatService` готов к миграции), AsyncResource, natural exit + soft
budgets, MCP-клиент, browser_control (3 режима), durable inbox, search chain с fallback,
парк моделей (router/planner/coder/verifier), иерархический контекст + prefix caching,
эпизодическая память, HF-поиск моделей, `slm_models.yaml`, git_context + `git_commit`,
`apply_patch` + `run_tests` + `find_references` + verifier в AgentLoop + Coding Bench,
LSP-диагностика после записи, stale check + файловые снапшоты, Permissions Dashboard,
freedom_level в профилях, LiveEventsPanel с QtEventBus, стационарный чат (страница
`QStackedWidget` вместо вкладки), автопоказ структуры проекта, подсветка coder-моделей,
режимы свободы `safe`/`research`/`development`/`open`, redaction секретов, shell
allowlist + dangerous mode, audit log для всех инструментов.

Полные детали — в `AGENTS.md` §4 «Пакеты» и в git-истории `PLAN_MODERNIZATION.md`.

---

## Фаза 2 — Архитектурные остатки

### 2.3 Управление потоками и отмена — `[~]` частично

**Сделано:** `_do_check_ollama` переведён на `_OllamaCheckThread` (сигналы
`result(bool)` + `models_ready(bool, list)`); `_save_project_state` — в
`IndexingController._on_thread_finished` без повторного `scan_project` на GUI-потоке;
`GitStatusDelegate._do_refresh` — на `QThreadPool` + `_GitStatusWorker`;
`_stop_all_threads` без `new_event_loop`; `IndexingThread.state_ready` отдаёт готовое
состояние из worker'а.

- [ ] **Единый `ThreadPool`/`TaskManager` вместо независимых `QThread`-классов.**
      Сейчас в `ui/chat/threads.py`: `AgentChatThread`, `RagSearchThread`, `WebSearchThread`,
      `_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread`, `_ModelPoolLoadThread`,
      `_BenchmarkThread`; плюс `IndexingThread`, `ModelCheckThread` в `main_window.py`;
      `PullThread` в `model_manager_dialog.py`. Единый пул с ограничением параллелизма
      (≤ 4 одновременно, чтобы не запускать десяток `Llama()` при быстрых кликах).
- [ ] **Гарантированная отмена `asyncio`-задач** (`asyncio.Task.cancel()`) вместо флага
      `cancel_request()`. Сейчас отмена кооперативная (флаг), при утечке фоновой задачи
      она доработает до конца и «съест» такт модели. Задача: в `AgentChatThread` держать
      `_asyncio_task` и `cancel()` по кнопке «Стоп».
- [~] **`PullThread` → `_DownloadThread`.** `model_manager_dialog.py::PullThread` дублирует
      `slm_models_dialog.py::_DownloadThread` (оба делают `ollama pull` / HF-скачивание).
      Объединить в один поток + запись в `slm_models.yaml`.
- [ ] **Корректно отменять/удалять потоки при закрытии окна** (`closeEvent`).
      Сейчас `_stop_all_threads` вызывает `quit()`+`wait(1000)`+`terminate()` — при
      `terminate()` состояние `asyncio`-цикла может быть испорчено. Правило:
      `AgentChatThread` при `closeEvent` — только `cancel()` + `wait()` без `terminate()`;
      если не выходит за 5 с — предупреждение, но не убийство.
- [ ] **`[новое]` Долгие задачи — в отдельные процессы** (`multiprocessing`/`QProcess`):
  - **`ui/indexing_controller.py`**: `IndexingThread` → `IndexingProcess` (можно
    убить без «залипания» GUI); при `kill()` — потеря только прогресса одного
    проекта, не всего приложения.
  - **`agents/lsp_bridge.py`**: LSP-сервер уже subprocess — добавить `QProcess`-обёртку
    для управления жизненным циклом из GUI-потока (сейчас `subprocess.Popen` через
    `asyncio.create_subprocess_exec`, что на Windows иногда висит при kill).
  - **`agents/slm_optimizer.py`**: тяжёлый benchmark (4.7.B) — в отдельный процесс.
  - **Правило:** поток нельзя убить чисто, процесс — можно; для задач >5 с — только процесс.
  - **Тесты:** `tests/ui/test_indexing_process.py` (mock `QProcess`).

### 2.4 QCommandLine вместо QAction — `[ ]`

На macOS `QAction`-шорткаты уходят в системное меню; `QCommandLine` даёт выигрыш только
при появлении CLI-режима (`ollidesk --headless`). Отдельная задача; сейчас приоритет низкий,
потому что Windows-only и CLI не нужен.

### 2.8 Миграция `ChatService.build_agent_context` — `[ ]`

`core/services/chat_service.py::ChatService.build_agent_context` **готов**, но
`ChatPanel._do_chat/_do_agent` продолжают собирать `AgentContext` вручную (~30 строк в
двух местах). Миграция убирает дублирование и упрощает тесты, но требует прогона всех
`AgentChatThread`-тестов + ручной проверки research/coding-флоу. Риск регрессии средний.

- [ ] Заменить ручную сборку `AgentContext(...)` в `_do_chat` и `_do_agent` на:
  ```python
  snapshot = ChatRequestSnapshot(
      system_prompt=combined, rag_context=merged_rag,
      agents_context=agents_context, project_root=self.project_root,
      project_name=self._project_name(), project_map=self._project_map_text(),
      vector_store=self.vector_store, num_ctx=self._router.num_ctx,
      search_cache=raw_cache, attached_files=self._attached_files or None,
      hypotheses=self._hypotheses, extra_rules=self._project_rules(),
      security=security, memory_service=self._ensure_memory_service(),
      profile=self._profile, app_config=self._safe_load_config(),
      git_context=self._build_git_context(),
  )
  context = ChatService.build_agent_context(snapshot, mode=mode)
  ```
- [ ] Прогнать `tests/agents/test_agent_loop*`, `tests/ui/test_history_*`,
      `tests/ui/test_tool_call_card.py`.
- [ ] Ручная проверка: research-вопрос (уходит в Orchestrator), обычный чат
      (уходит в AgentLoop), coding-задача (уходит в `_run_coding_flow`).

---

## Фаза 4 — Остатки по моделям и ReAct

### 4.5 Модели SLM и Hardware-Aware каталог — `[~]` частично

**Сделано:** `slm_models.yaml` с `schema_version=1` (`core/slm_models_io.py`), каталог
вынесен из `config.yaml`; Qwen3-Coder-30B-A3B (MoE), Qwen2.5-14B, Qwen2.5-Coder-32B —
в `_DEFAULT_SLM_MODELS`; HF-поиск (`agents/hf_search.py`, `ui/dialogs/hf_dialog.py`).

- [ ] **Расширение каталога под разные сценарии.** В `SLMModelInfo` добавить поля
      `task_tags: list[str]` (например, `["chat", "coding", "reasoning", "docs", "fast"]`)
      и `recommended_for`. Заполнить для моделей:
      - `Qwen2.5-7B-Instruct` (Q4_K_M/IQ4_XS) — чат, быстрый ассистент.
      - `Qwen2.5-Coder-7B-Instruct` (Q4_K_M) — локальная разработка (Python/C#).
      - `Qwen2.5-Coder-14B-Instruct` (Q4_K_M) — сложный кодинг.
      - `DeepSeek-R1-Distill-Qwen-32B` (Q4_K_M) — reasoning, анализ документов,
        просветительские задачи. Теги `["reasoning", "docs", "analyst_pro"]`.
      - `Phi-3.5-mini` или `Qwen2.5-3B-Instruct` (Q4_K_M) — минимальные требования
        (от 8 ГБ RAM), быстрые служебные задачи.
- [ ] **Q8_0 в каталог для 7B/14B.** Сейчас в `_DEFAULT_SLM_MODELS` есть `q4_k_m`/`q5_k_m`/
      `q6_k`, но Q8_0 только для 1.5B. Добавить `qwen2.5-7b-instruct-q8_0` и
      `qwen2.5-14b-instruct-q8_0` (≈8 и ≈15 ГБ; на ПК с 32+ ГБ RAM влезают, качество
      близко к fp16).
- [ ] **Решение по `@dir`:** сейчас `_on_directory_attach_requested` намеренно не сбрасывает
      сессию и не добавляет в `recent_projects` (каталог «прикрепляется» к текущему чату).
      Альтернатива — «полный перезаход как `File → Open Project`». Решение за
      пользователем; менять не обязательно, но зафиксировать в `.ollidesk/rules.md`.

### 4.6.B Temperature для PLAN/AGENT — `[~]`

**Сделано:** `_TEMPERATURE_BY_OP` в `LocalLLMClient` умеет давать 0.0 для
`tool_call`/`plan`/`classify`/`critique`, 0.35 для `synthesis`/`regen`.

- [ ] **`AgentLoop` не передаёт `op_type` в `client.chat(...)`** — механизм есть, но
      не подключён. Сейчас в AGENT используется дефолт `_DEFAULT_TEMPERATURE = 0.0`
      (для всех итераций), что для синтеза субоптимально (0.0 → повторы, 0.35 → живее).
      Задача: в `AgentLoop.run` передавать `op_type="tool_call"` для итераций с tools
      и `op_type="synthesis"` для `_final_call_no_tools`. Тест: `tests/agents/test_op_type.py`
      проверяет, что клиент получает правильный `op_type` в обоих путях.

### 4.6.C `model_digest` в `ModelInfo` — `[ ]`

- [ ] **`core/model_registry.py::ModelInfo`** — добавить `model_digest: str = ""`.
      `ModelRegistry.sync_with_ollama` сравнивает `digest` из `GET /api/tags` с сохранённым
      и сбрасывает `supports_tools`/`last_checked` в `None`, если хэш изменился (модель
      переехала на новую версию — старые данные `supports_tools` невалидны). Иначе после
      `ollama pull qwen2.5-coder:7b` UI может показывать «tools поддержаны», хотя на деле
      модель могла потерять поддержку или наоборот.

### 4.6.D Удалить мёртвый код — `[~]`

- [ ] **`ui/dialogs/model_manager_dialog.py::PullThread`** — объединить с `_DownloadThread`
      (см. 2.3).
- [~] **Закомментированные блоки** в `core/search_cache.py`, `fs/vector_store.py`, `main.py`
      (в т.ч. закомментированный `_build_git_context` в `panel.py`) и в `threads.py`
      (`_OllamaCheckThread`, `_SLMLoadThread`, `_SLMDownloadThread` с комментариями
      «было/стало») — почистить косметически.

### 4.7.B Реальные прогоны benchmark — `[ ]`

**Сделано:** `agents/benchmark.py` + `ui/dialogs/benchmark_dialog.py` — инструмент
готов, отчёт CSV/Markdown.

- [ ] **Прогон моделей на референсном железе (Dell T1200 / 128 ГБ RAM) и минимальном
      (8-16 ГБ RAM, без GPU)**: 7B/14B/32B на 8k/16k/32k/64k. Записать
      `ttft_ms`/`tok_s`/`ram_mb` в `docs/BENCHMARK.md`. Без этих цифр нельзя обоснованно
      настраивать авто-рекомендации (4.7.E) и выбирать между 7B и 14B в `_ModelPoolLoadThread`.
- [ ] **Прогон с `kv_cache_type=q8_0` vs `f16`** — измерить выигрыш по RAM и потерю
      по качеству. Особенно важно для CPU-analyst, где KV-кэш — значительная часть RAM.

### 4.7.E Hardware-Aware Task Classifier & Recommender — `[~]`

**Сделано:** `ModelPool.pick_role(task, ...)` — простейший rule-based выбор
(`task in {"classify","plan","critique","tool"}` → PLANNER, иначе ANALYST).
`agents/hardware_profile.py` уже умеет определять RAM/VRAM/CPU.

- [ ] **`agents/task_classifier.py`** — начать **только** с rule-based (без LLM-классификатора).
      ~30 строк регулярок и длин: «архитектура», «баг», «рефакторинг», «тест»,
      «документация», «объясни», «проанализируй документ» → `TaskType` (`chat`, `coding`,
      `reasoning`, `docs`, `fast`).
- [ ] **`agents/model_recommender.py`** — `recommend_model(task_type, hardware, catalog) -> list[RecommendedModel]`.
      Система **сама** сопоставляет требования модели с доступной RAM/VRAM пользователя
      и возвращает ранжированный список с обоснованием.
      - Пример: задача `coding` + 4 ГБ VRAM + 16 ГБ RAM → Рекомендация: `Qwen2.5-Coder-7B` (Q4_K_M, offload на GPU).
      - Пример: задача `reasoning` + 4 ГБ VRAM + 128 ГБ RAM → Рекомендация: `DeepSeek-R1-Distill-Qwen-32B` (Q4_K_M, CPU).
      - Пример: задача `chat` + 8 ГБ RAM (без GPU) → Рекомендация: `Qwen2.5-3B` (Q4_K_M, CPU).
- [ ] **Feedback loop (👍/👎).** Отдельная простая задача — кнопки в UI-пузыре → запись
      в `~/.ollidesk/feedback.db` → будущий few-shot для планировщика / коррекция
      весов в `model_recommender`. Раньше LoRA/Self-RAG (отклонено в 4.7.F).

### 4.7.G Адаптивный UI и рекомендации — `[ ]`

- [ ] **Model Recommendation Wizard** при первом запуске. OlliDesk сам определяет
      железо через `hardware_profile.py` и предлагает 2-3 сценария использования
      («Разработка кода», «Анализ документов/Обучение», «Просто общение»). На выходе —
      рекомендованная модель, квантование и `n_ctx_hint`, записанные в `slm_models.yaml` и профиль.
- [ ] **Визуальные рекомендации в UI (без принуждения):**
      - `SLMModelsDialog` — колонка/бейдж «Рекомендуется для: coding, chat» (из `task_tags`).
      - `PipelineSelectors` — при выборе роли `CODER` показывать подсказку «Для этой роли
        рекомендуются: Qwen2.5-Coder-7B/14B».
      - `StatusDashboard` — если активная модель не соответствует задаче (например, пишете код
        на Qwen2.5-7B-Instruct, а в каталоге есть Coder), показывать ненавязчивый индикатор
        «💡 Для кодинга рекомендована Qwen2.5-Coder-7B». Без авто-переключения.
      - `ChatPanel` — при первом сообщении в сессии предлагать: «Похоже на задачу кодинга.
        Переключить coder-модель?» (только с подтверждением, см. п. 12 ограничений).
- [ ] **Fast-path router перед AgentLoop.** Для сообщений типа «погода», «время», «дата»
      или простых вопросов — отвечать rule-based или самой легкой доступной моделью,
      не поднимая тяжелую основную модель. Экономит такт модели и время.

### 4.7.H Dynamic Ollama Modelfile — `[ ]`

**Проблема:** Сейчас OlliDesk передает `num_ctx`, `temperature`, `num_thread` в API запроса к Ollama.
Ollama применяет их на лету, но не оптимизирует граф под конкретное железо. Из-за этого
теряется скорость на Windows (неверные `use_mmap`, `num_batch`, дефолтный `num_gpu`).

**Что внедрить:**
- [ ] **`agents/ollama_optimizer.py`** — генератор `Modelfile`.
      На основе `hardware_profile.py` формирует:
      - `PARAMETER num_ctx <n_ctx_hint>`
      - `PARAMETER num_gpu <рассчитанный из VRAM>`
      - `PARAMETER num_thread <физ. ядра>`
      - `PARAMETER num_batch 512`
      - `PARAMETER use_mmap true`
      - `PARAMETER use_mlock <true, если RAM > 32 ГБ, иначе false>`
      - `PARAMETER repeat_penalty 1.1`
      - **НЕ включает `SYSTEM`** (промпт динамический).
- [ ] **UI-кнопка «Оптимизировать»**: В `SLMModelsDialog` или `ModelManagerDialog`
      добавить кнопку «Создать оптимизированную копию». OlliDesk запускает
      `ollama create ollidesk-<имя> -f <временный_файл>` и предлагает переключиться на неё.
- [ ] **Кэширование результатов**: Хранить хэш характеристик железа. Если железо не менялось
      (или RAM/VRAM не пересчитывались), не пересоздавать модель каждый раз.
- [ ] **Автоматический выбор в UI**: Если оптимизированная модель существует,
      в `PipelineSelectors` показывать `ollidesk-optimized-qwen2.5-coder:7b`.

### 4.7.F Отклонено (для справки) — `[-]`

- **LoRA-адаптеры, Self-RAG, дообучение, Multi-Agent** (Этапы 3–5 аудита): на ноутбуке
  без GPU для обучения — недели работы без измеримого результата.
- **«Переключение за ~100 мс простой сменой указателя»** — нереалистично: LRU решает
  именно эту задачу, выигрыш — разница между загрузкой и обращением к прогретой модели.

---

## Фаза 5 — Мультимодальность: остатки

**Сделано:** `fs/image_extractors.py` (OCR), `fs/audio_extractors.py` (STT),
`fs/video_extractors.py` (STT видео), `fs/document_extractors.py` (PDF/DOCX/PPTX/XLSX/RTF +
OCR сканов), `fs/media_cache.py`; UI — drag&drop, «📎», превью изображений, `DocumentPreviewDialog`.

- [ ] **Vision-модели через Ollama (backend).** `qwen2.5-vl:7b` / `llama3.2-vision:11b`.
      Отдельный `agents/vision_client.py` (без смешивания с основным чатом — vision требует
      `images` в payload, у `ChatMessage.images` это поле есть, но `ModelRouter` его
      игнорирует). На 4 ГБ VRAM — GPU offload = 0, всё на CPU; латентность
      20–60 с на картинку 800×600. Вызов — через `read_attached_file` (для картинок)
      или отдельный инструмент `describe_image(path, prompt)`. Политика — `ask_first`.
- [ ] **PDF/DJVU** — `djvulibre` не реализован, нужен `djvulibre-bin` в системе;
      `pdf2image` уже конвертирует DJVU при наличии Poppler + djvulibre. Добавить
      ветку в `fs/document_extractors.py::extract_document` (`is_djvu()` + `_extract_djvu`).

> Генерация изображений (SD1.5 / SDXL / ComfyUI / Forge) — **вне объёма OlliDesk**.

---

## Фаза 7 — Plugin API

**Сделано:** MCP-клиент (stdio, `core/mcp_client.py`), `MCPManager` в `tools/mcp_ops.py`,
`MCPConfig` в `core/config.py`, `register_mcp_tools(registry)` → `ToolRegistry.register_spec`,
capabilities MCP проходят через `effective_policy`, audit log, `EventBus.emit("mcp.*")`,
индикатор MCP в `StatusDashboard`.

- [ ] **Plugin API (после MCP)** — отдельная задача. Пока не начато:
  - `tools/plugins/*.py` + `plugin.toml` (`name`, `version`, `api_version`, `tools`,
    `capabilities`, `requires`, `uses`).
  - `Plugin.define({tools, commands, skills})` — декларативный контракт.
  - Изоляция ошибок (плагин не валит приложение).
  - `api_version` проверяется при загрузке; несовместимый — `warning`, не `crash`.
  - `capabilities` плагина проходят через `Policy Engine`.
  - `requires` — hard dependency, gate startup; `uses` — optional, не блокирует.
  - `createKeyed` — per-session сторы.
- [ ] **Пользовательские команды** (`~/.ollidesk/commands/*.md`) — расширение ролей и
      промптов; отдельная задача (парсинг + инжект в системный промпт).

---

## Фаза 8 — Релиз и качество — `[~]` отложено целиком

- [x] Единая версия из `pyproject.toml` + `bump-my-version` + `git-cliff`.
- [ ] **`CHANGELOG.md`** генерируется и коммитится на каждый релиз
      (`git cliff --tag v<version> -o CHANGELOG.md` → `git add` → `git commit`).
      Сейчас `bump-my-version` генерирует файл, но не коммитит его.
- [ ] **CI: `windows-latest`** в матрицу. Приложение Windows-only;
      `CREATE_NO_WINDOW`, пути с `\`, `QWebEngineView` под Windows — на ubuntu не
      проверяются. Минимум 3 job: `ubuntu-latest` (ruff/mypy/pytest базовый),
      `windows-latest` (полный pytest), `windows-latest` + PyInstaller (smoke).
- [ ] **Pre-commit** или `make check` в локальном хуке — `.pre-commit-config.yaml`
      отсутствует. Добавить `ruff check --fix`, `ruff format`, `mypy`, `pytest -x`.
- [ ] **Релизный пайплайн** (GitHub Actions): сборка `OlliDesk.exe` по тегу `v*`,
      публикация в Releases, прикрепление `CHANGELOG.md`.
- [ ] **Проверить `ollidesk.spec`** — актуальные `hiddenimports` для
      `llama_cpp`/`chromadb`/`rapidocr`/`faster_whisper`/`playwright`; размер дистрибутива;
      первый запуск на чистой Windows 10 без Python.
- [ ] **README** синхронизировать: упоминает устаревшее `scripts/download_monaco.py`
      (теперь `make download-monaco`), не упоминает `slm_models.yaml`, `apply_patch`,
      `run_tests`, MCP, browser, `next_generation`.

---

## Фаза 9 — UI-мелочи

### 9.5.1 «Копировать» в код-блоке — `[ ]`

- [ ] **`ui/chat/message_item.py::_render_code_block`** — добавить мини-кнопку
      «📋 Копировать» в правый верхний угол каждого `<div class="code-header">`. Сейчас
      копируется всё сообщение (`_copy_to_clipboard`), не отдельный блок. Реализация:
      JS-обработчик в `QTextBrowser` (anchor-click) + Python-слот `copy_code_block(block_id)`.

### 9.5.4 Счётчик на кнопке «▼ Вниз» — `[ ]`

- [ ] **`ui/widgets/auto_scroll.py`** — показывать количество новых сообщений на кнопке
      (`▼ Вниз (3)`) вместо простого `▼ Вниз`. Счётчик инкрементится, пока пользователь
      вверху и `_stick_to_bottom=False`; сбрасывается при `scroll_to_bottom_force()`.
      Реализация: сигнал `new_content_below(int)` из `ChatPanel._add_message` → слот
      `AutoScrollArea.bump_new_count()`.

### 9.7 Аудио-плеер в пузыре — `[ ]`

- [ ] **`ui/chat/message_item.py`** — если `AttachedFile.kind == "audio"`, показывать
      мини-плеер (`QMediaPlayer` + `QAudioOutput` + play/pause + ползунок). Сейчас
      аудио прикрепляется, но в пузыре только текст расшифровки. На T1200 `QMediaPlayer`
      работает (Windows Media Foundation); без ffmpeg плеер не заработает — предупреждение.

### 9.8 Темы, прогресс, сплит — `[~]`

- [ ] **Цвет title bar в тёмной теме (Windows)** — `DwmSetWindowAttribute(
      DWMWA_USE_IMMERSIVE_DARK_MODE)`. Реализация: `ctypes.windll.dwmapi` +
      `QWindow.winId()` после `show()`. При отсутствии `dwmapi` (Linux/macOS) —
      gracefully skip.
- [ ] **Авто-детект темы ОС** — `QGuiApplication.styleHints().colorScheme()` (Qt 6.5+,
      PySide6 6.7+ уже есть). При первом запуске, если `config.theme` — дефолт `"light"`,
      подставить `"dark"`, если ОС в тёмной теме. Ручное переключение — приоритетнее.
- [ ] **Сплит «чат + редактор»** — после 9.1 архитектура `QStackedWidget` + `QTabWidget`
      это позволяет: переключать `QStackedWidget` на `QSplitter(ChatPanel, EditorWidget)`,
      когда открыты файлы; кнопка «💬» в тулбаре EditorWidget сворачивает сплит обратно
      в чат. Отложено (большой UX-риск).

---

## Фаза 11 — Интеллект, Достоверность и Ресурсы (Cognitive Layer)

### 11.1 Эпистемическая честность (Grounding & Honesty) — `[ ]`
- [ ] **Active Retrieval**: В `AgentLoop._verify_and_maybe_regen` добавить логику: если
      `verify()` находит `unsupported_claims`, не просто регенерировать, а вызвать
      `search_codebase` или `read_file` по ключевым словам из claim, добавить контекст
      в промпт и только потом регенерировать.
- [ ] **Confidence Prompting**: В системный промпт добавить инструкцию: "Если ты не уверен
      в ответе и не можешь подтвердить его фактами из контекста, начни ответ с фразы
      'Я не уверен, но...'. Никогда не выдумывай имена файлов или функций".
- [ ] **UI-индикатор достоверности**: Если ответ содержит теги `⚠️ не подтверждено`,
      подсвечивать пузырь чата желтым или добавлять иконку "Требует проверки".

### 11.2 Динамический Resource Governor — `[ ]`
- [ ] **Мониторинг ресурсов в реальном времени**: В `LocalLLMClient` перед каждым вызовом
      `_generate` проверять доступную RAM через `psutil`. Если < 10% свободно — принудительно
      снижать `n_ctx` на 25% и логировать `budget.warning`.
- [ ] **Мягкое вытеснение (Graceful Eviction)**: Если `ModelPool` хочет загрузить новую
      модель, но нет места, не падать, а выгружать самую старую (LRU) с предупреждением в UI:
      "Модель X выгружена, чтобы освободить память для Y".
- [ ] **Каскадный Fallback**: Если `AgentLoop` зацикливается (3 итерации с одинаковыми тулами),
      автоматически предложить пользователю: "Задача сложная. Переключиться на более мощную
      модель (DeepSeek-R1)?".

### 11.3 Глубокий анализ кода (AST & Semantic Graph) — `[ ]`
- [ ] **Tree-sitter интеграция**: Добавить `tree-sitter` и `tree-sitter-languages` в
      зависимости. Переписать `FileIndexer.chunk_file` для Python/C#/JS/Go, чтобы чанки
      формировались по AST-узлам (функции, классы), а не по строкам.
- [ ] **Semantic Graph**: В `fs/intel/` добавить парсинг интерфейсов и сигнатур. Строить
      граф: `Class -> Method -> Argument -> Type`. Использовать в `find_references` и при
      генерации кода.

### 11.4 Проактивность (Background Agent) — `[ ]`
- [ ] **Фоновый аудит проекта**: При открытии проекта запускать в отдельном `QProcess`
      легкий анализ (LSP-ошибки, устаревшие TODO, отсутствующие тесты). Результат — в
      `StatusDashboard` (иконка "💡 3 совета по улучшению").
- [ ] **Умные уведомления**: Если пользователь долго не пишет, предложить: "Я заметил,
      что вы работали над X. Хотите, я подготовлю план рефакторинга?".

### 11.5 Инструменты для общества и просвещения — `[ ]`
- [ ] **`analyze_table`**: Инструмент для работы с XLSX/CSV. Умеет делать описательную
      статистику, группировку и базовые графики (через `matplotlib`).
- [ ] **`translate_text`**: Локальный перевод (NLLB-200 или Qwen) для документов и чата.
      Интеграция в `DocumentPreviewDialog`.
- [ ] **Local Wiki**: Инструмент для создания Markdown-заметок в `.ollidesk/wiki/`,
      которые автоматически попадают в RAG.

### 11.6 True Air-Gapped Mode — `[ ]`
- [ ] **Сетевой блокиратор**: В `core/security.py` добавить глобальный флаг `strict_offline`.
      При его включении перехватывать `socket.socket.connect` и блокировать любые исходящие
      соединения (кроме `127.0.0.1`), выбрасывая понятную ошибку.

---

## Быстрый бэклог (мелочи)

- [ ] **`uv.lock` рассинхронизирован с `pyproject.toml`**: объявлен
      `llama-cpp-python>=0.3.0` (и транзитивный `diskcache`), в локе их нет. Любой
      `uv run` / `uv sync` молча переписывает лок, и правка попадает в случайный коммит.
      Перегенерировать один раз (`uv lock --upgrade-package llama-cpp-python`) и держать
      лок в коммитах. Причина — лок собран до добавления SLM-зависимости.
- [ ] **`pyproject.toml` → `hatchling`** вместо `setuptools` (упростит сборку wheel,
      PEP 517).
- [ ] **`.gitignore` игнорирует `Notes.md`/`Roadmap.md`/`Model.md` по имени** — файлы
      уже отслеживаются, но новые такие же будут скрыты. Переименовать правила на
      `/Notes.md` и т.п.
- [ ] **Добавить `pyright`/`basedpyright`** как второй тайпчекер (VS Code-режим);
      mypy остаётся в CI. Разница в диагностике выявит потенциальные баги.
- [ ] **Убрать `.idea/` из репозитория** — уже в `.gitignore`, но проверить `git ls-files`.
- [ ] **`py-spy` в CI** для профилирования GUI-потока (2.3): `py-spy record` на
      10-секундном smoke-тесте, порог — ни одна функция не блокирует GUI > 100 мс.
      Пока не начато.
- [ ] **`multiprocessing`/`QProcess` для индексации** (2.3): `IndexingProcess` вместо
      `IndexingThread`, `LSPProcess` для LSP. Пока не начато.
- [ ] **`pyproject.toml`: группы `bench`, `search`, `browser`**:
      - `bench` — `pytest-benchmark`.
      - `search` — ничего (SearXNG/OpenSERP клиенты — только HTTP, не тянут пакетов).
      - `browser` — `playwright` (опционально, уже упоминается в `agents/browser_client.py`).

---

## Приоритеты

**Порядок сверху вниз — по соотношению «польза / риск регрессии» с учетом миссии
автономности и адаптивности.**

1. **11.3 AST-чанкинг и Semantic Graph** (2–3 дня) — это самый большой и быстрый прирост
   качества кодинга без затрат ресурсов. Модели начинают видеть функции целиком, а не
   обрывки строк.
2. **11.1 Active Retrieval & Grounding** (1–2 дня) — резко снижает галлюцинации. Агент
   учится искать доказательства, если сомневается, и честно признавать незнание.
3. **4.7.B Benchmark-прогоны** (1 день + прогон) — фундамент для всей рекомендательной
   системы. Нужно понять реальные лимиты 7B/14B/32B на разном железе.
4. **11.2 Dynamic Resource Governor** (1–2 дня) — предотвращает падения (OOM) на слабых
   машинах, делает поведение предсказуемым.
5. **4.7.E Hardware-Aware Task Classifier & Recommender** (1-1.5 дня) — создание ядра
   рекомендательной системы на основе бенчмарков и Governor.
6. **2.8 Миграция `ChatService` в `_do_chat/_do_agent`** (0.5–1 день) — убирает ~30
   строк дублирования, упрощает тесты. Риск средний, но тесты уже готовы.
7. **4.7.G Адаптивный UI и рекомендации** (1.5–2 дня) — Model Recommendation Wizard,
   визуальные подсказки, fast-path router. Делает OlliDesk "умным" с первого запуска.
8. **4.7.H Dynamic Ollama Modelfile** (1 день) — генерация `Modelfile` под железо для
   максимальной скорости Ollama на Windows (правильные `num_gpu`, `use_mmap`, `num_batch`).
9. **11.6 True Air-Gapped Mode** (0.5 дня) — критически важно для миссии (работа в
   закрытых сетях, защита от случайных утечек).
10. **4.6.B `op_type` в `AgentLoop`** (0.5 дня) — temperature 0.35 для синтеза, 0.0 для
    tool_calls. Эффект заметен в research/coding, риск минимальный.
11. **4.6.C `model_digest`** (0.5 дня) — предотвращает ложный «tools supported» после
    обновления модели через `ollama pull`.
12. **11.5 Инструменты для просвещения** (`analyze_table`, `translate_text`, Local Wiki)
    (2–3 дня) — расширяет миссию OlliDesk за пределы IT.
13. **11.4 Проактивность (Background Agent)** (2 дня) — фоновый аудит проекта, умные
    уведомления. Требует аккуратной работы с процессами (11.2, 2.3).
14. **9.5.1 «Копировать» в код-блоке** (0.5 дня) — маленькая UX-победа, легко.
15. **9.5.4 счётчик на «▼ Вниз»** (0.5 дня) — маленькая UX-победа, легко.
16. **Быстрый бэклог — `uv.lock`, `pyright`, `.gitignore`, `.idea/`** (0.5 дня на всё) —
    убирает ежедневный шум в коммитах.
17. **4.5 Расширение каталога (DeepSeek-R1, Q8_0, 7B/14B)** (1–2 дня + прогоны) —
    обновление `_DEFAULT_SLM_MODELS` и `slm_models.yaml`.
18. **2.3 Процессы вместо потоков** (3–5 дней) — устраняет залипание GUI при индексации
    больших проектов и убийстве LSP. Требует тестов `test_indexing_process.py`.
19. **8 Релизный пайплайн + PyInstaller + README** (2–3 дня) — без этого «готовая сборка»
    не воспроизводится.
20. **9.8 `DwmSetWindowAttribute` + авто-детект темы ОС** (0.5–1 день) — UX-полировка.
21. **5 Vision через Ollama** (2–3 дня) — `agents/vision_client.py` + `read_attached_file`
    для картинок + `ask_first` политика. На 4 ГБ VRAM — медленно (20–60 с).
22. **7 Plugin API** (1–2 недели) — после MCP-экосистемы и обратной связи.
23. **2.4 `QCommandLine`** — отложено (Windows-only, CLI пока не нужен).
24. **9.7 Аудио-плеер** (0.5 дня) — редко нужно, но приятно.
25. **5 DJVU** (1 день) — редко нужно.
26. **9.8 Сплит «чат + редактор»** — отложено (большой UX-риск).

**Что не делать:**
- Деление на «Lite» и «Pro» версии. OlliDesk един, адаптивен и самостоятелен.
- LoRA-адаптеры, Self-RAG, дообучение, Multi-Agent (4.7.F) — отклонено.
- Внешние провайдеры LLM (OpenAI) — архитектурно все модели локальные.
- Генерация изображений (SD/ComfyUI) — вне объёма OlliDesk.
- Переписывание UI на SolidJS/TS, PWA, multi-server — не наш путь.

---

## AGENTS.md — что надо обновить при закрытии очередного пункта

Обновил структуру `AGENTS.md` под актуальное состояние (см. отдельный файл). При
закрытии любого пункта из этого плана — короткое обновление в `AGENTS.md`:

- **§4 «Пакеты»** — добавить новый модуль/метод или убрать `[план X.Y]`.
- **§5 «Пайплайн»** — если меняется поведение агента (например, `op_type` в 4.6.B —
  добавить строку про температуру).
- **§9 «Тесты»** — добавить новый тест-файл.
- **§12 «Подводные камни»** — если открылся новый подводный камень (например,
  для 11.2 «Resource Governor должен проверять RAM перед каждым `_generate`»).

**Правило обновления:**
- `[ ]` → `[x]` в этом файле — **удалить раздел целиком** (файл должен содержать только
  то, что осталось).
- В `AGENTS.md` — добавить одну строку в §4/§5/§12, а не копировать весь раздел плана.

Историю закрытия (кто, когда, какой PR) — держать в git-логах через Conventional Commits
(`feat: 11.1 Active Retrieval в AgentLoop`). `cliff.toml` группирует по `feat:`/`fix:`/`chore:` —
`CHANGELOG.md` автоматически отразит закрытие.
