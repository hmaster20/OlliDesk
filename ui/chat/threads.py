"""Фоновые потоки для панели чата."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger
from PySide6.QtCore import QThread, Signal

from agents.agent_loop import (
    AgentContext,
    AgentIterationLimitError,
    AgentLoop,
)
from agents.base import LLMBackend
from agents.download_slm import download_model, get_model_parts
from agents.local_llm_client import LocalLLMClient
from agents.model_router import ModelRouter
from agents.ollama_client import ChatMessage, OllamaClient
from agents.orchestrator import Orchestrator, RunResult
from agents.tool_registry import ToolRegistry
from core.config import AgentMode
from fs.vector_store import SearchResult

# Лимит времени Orchestrator. Синтез 7B на CPU занимает 200-250 с,
# поэтому 5 минут не хватало даже без регенерации.
ORCHESTRATOR_TIMEOUT_S = 600.0
# Доля бюджета, которая остаётся под регенерацию (≈ такой же по длительности
# повторный синтез); при исчерпании orchestrator пропускает regen сам.
ORCHESTRATOR_TIME_BUDGET_S = 540.0


def _dedupe_by_file(results: list[SearchResult]) -> list[SearchResult]:
    """Оставляет по одному лучшему чанку на файл."""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        if r.file_path in seen:
            continue
        seen.add(r.file_path)
        unique.append(r)
    return unique


class AgentChatThread(QThread):
    """Поток агента: ReAct-цикл (старый) ИЛИ Orchestrator (новый линейный флоу).

    Роутинг: если передан `orchestrator` и последний user-вопрос —
    research-вопрос (``is_research_question`` → True), поток вызывает
    ``Orchestrator.run()`` (детерминированный сбор + синтез 7B + renderer).
    Иначе — старый ReAct-цикл через ``AgentLoop``.

    Новые сигналы (только для Orchestrator-пути):
      * ``orchestrator_event(str, dict)`` — прогресс (classify/plan/coverage/
        synthesize/regen/error/fallback);
      * ``orchestrator_result(object)`` — финальный ``RunResult`` (answer,
        ascii_diagram, mermaid_diagram, evidence_ids, coverage, verify_ok…).

    [4.8.E] Низкоуровневые delta-сигналы:
      * ``text_delta(str)`` / ``reasoning_delta(str)`` — каждый токен
        текста/reasoning от модели;
      * ``stream_started()`` / ``stream_finished()`` — границы одного
        стрима (start/end-event от клиента).
    """

    # --- старые сигналы (без изменений) ---
    research_mode_detected = Signal(bool)
    thought_chunk = Signal(str)
    final_answer_started = Signal()
    final_chunk = Signal(str)
    research_summary_ready = Signal(object)
    chunk_received = Signal(str)
    thinking_received = Signal(str)
    tool_call_detected = Signal(str, str, str)
    # [9.6.1] + object: metadata (dict | None) для diff-карточки write_file.
    tool_executed = Signal(str, str, bool, object)
    tool_started = Signal(str, str)
    finish_received = Signal(str)
    error_occurred = Signal(str)
    iteration_limit_exceeded = Signal()
    iteration_changed = Signal(int, int)
    tools_status_changed = Signal(bool)
    status_changed = Signal(str)

    # [4.8.E] Низкоуровневые delta-события (ортогональны chunk_received).
    text_delta = Signal(str)
    reasoning_delta = Signal(str)
    stream_started = Signal()
    stream_finished = Signal()

    # --- новые сигналы для Orchestrator ---
    orchestrator_event = Signal(str, dict)
    orchestrator_result = Signal(object)  # RunResult

    def __init__(
        self,
        client: LLMBackend,
        registry: ToolRegistry,
        model: str,
        context: AgentContext,
        messages_history: list[ChatMessage],
        agent_mode: AgentMode,
        max_iterations: int = 10,
        orchestrator: Orchestrator | None = None,
    ):
        super().__init__()
        self.client = client
        self.registry = registry
        self.model = model
        self.context = context
        self.messages_history = messages_history
        self.agent_mode = agent_mode
        self.max_iterations = max_iterations
        self.orchestrator = orchestrator

        self._tool_response: tuple[bool, dict[str, Any] | None] | None = None
        self._tool_event = threading.Event()
        self._research_mode: bool = False

    def respond_to_tool(self, approved: bool, arguments: dict | None = None):
        self._tool_response = (approved, arguments)
        self._tool_event.set()

    async def _on_tool_request(self, name: str, description: str, arguments: dict) -> bool:
        args_json = json.dumps(arguments, ensure_ascii=False)
        self.tool_call_detected.emit(name, description, args_json)
        self._tool_event.clear()
        await asyncio.get_event_loop().run_in_executor(None, self._tool_event.wait)
        approved, modified_args = self._tool_response or (False, None)
        if modified_args:
            arguments.clear()
            arguments.update(modified_args)
        return approved

    def run(self):
        asyncio.run(self._run_agent())

    # ------------------------------------------------------------------
    # Роутинг: Orchestrator (research) → AgentLoop (обычный)
    # ------------------------------------------------------------------

    async def _run_agent(self):
        # Пытаемся распознать research-вопрос для роутинга.
        last_user = next(
            (m for m in reversed(self.messages_history) if m.role == "user"),
            None,
        )
        user_text = last_user.content if last_user else ""

        if self.orchestrator is not None and user_text:
            from agents.investigation import is_research_question

            if is_research_question(user_text):
                try:
                    # ИСПРАВЛЕНИЕ: не даем оркестратору висеть вечно.
                    # 5 минут не хватало: синтез 7B на CPU — 200-250 с.
                    await asyncio.wait_for(
                        self._run_orchestrator(user_text),
                        timeout=ORCHESTRATOR_TIMEOUT_S,
                    )
                    return
                except TimeoutError:
                    logger.error(f"Orchestrator timed out after {ORCHESTRATOR_TIMEOUT_S:.0f}s")
                    self.orchestrator_event.emit(
                        "fallback",
                        {
                            "reason": f"Таймаут {ORCHESTRATOR_TIMEOUT_S / 60:.0f} минут",
                            "message": "Использую старый ReAct",
                        },
                    )
                except Exception as e:
                    logger.exception("Orchestrator failed; fallback to AgentLoop")
                    self.orchestrator_event.emit(
                        "fallback",
                        {"reason": str(e), "message": "Использую старый ReAct"},
                    )
                    # проваливаемся в AgentLoop

        # --- Старый путь: AgentLoop ---
        self.tools_status_changed.emit(True)

        async def _notify_tools_disabled():
            self.tools_status_changed.emit(False)

        async def _on_tool_start(name: str, args: dict):
            args_json = json.dumps(args, ensure_ascii=False)
            self.tool_started.emit(name, args_json)

        async def _on_thought(token: str):
            self.thought_chunk.emit(token)

        async def _on_final_chunk(token: str):
            self.final_chunk.emit(token)

        async def _on_final_started():
            self.final_answer_started.emit()

        # [4.8.E] Низкоуровневые события от AgentLoop.
        async def _on_text_delta(token: str):
            self.text_delta.emit(token)

        async def _on_reasoning_delta(token: str):
            self.reasoning_delta.emit(token)

        async def _on_stream_start():
            self.stream_started.emit()

        async def _on_stream_finish():
            self.stream_finished.emit()

        async def _run_with(client) -> tuple[str, AgentLoop]:
            loop = AgentLoop(client=client, registry=self.registry, model=self.model)
            from agents.investigation import is_research_question

            last_user_local = next(
                (m for m in reversed(self.messages_history) if m.role == "user"),
                None,
            )
            self._research_mode = is_research_question(last_user_local.content if last_user_local else "")
            self.research_mode_detected.emit(self._research_mode)

            result = await loop.run(
                context=self.context,
                messages_history=self.messages_history,
                on_token=(_on_final_chunk if self._research_mode else self._on_token),
                on_tool_request=self._on_tool_request,
                max_iterations=self.max_iterations,
                on_thinking=self._on_thinking,
                on_tools_disabled=_notify_tools_disabled,
                on_tool_start=_on_tool_start,
                on_status=self._on_status,
                on_tool_result=self._on_tool_result,
                on_thought=_on_thought if self._research_mode else None,
                on_final_answer_started=(_on_final_started if self._research_mode else None),
                # [4.8.E] низкоуровневые события
                on_text_delta=_on_text_delta,
                on_reasoning_delta=_on_reasoning_delta,
                on_stream_start=_on_stream_start,
                on_stream_end=_on_stream_finish,
            )
            return result, loop

        try:
            if hasattr(self.client, "__aenter__"):
                async with self.client as client:
                    result, loop = await _run_with(client)
            else:
                result, loop = await _run_with(self.client)

            summary = getattr(loop, "last_research_summary", None)
            if summary:
                self.research_summary_ready.emit(summary)

            self.finish_received.emit(result)
        except AgentIterationLimitError:
            # error_occurred НЕ эмитим: UI в _on_iteration_limit_exceeded
            # показывает «⏸ Достигнут лимит» и кнопку «Продолжить». Дубль
            # «❌ Ошибка агента: ...» только запутал бы пользователя —
            # это не сбой, а исчерпание бюджета шагов.
            self.iteration_limit_exceeded.emit()
        except Exception as e:
            self.error_occurred.emit(f"Ошибка агента: {e}")

    # ------------------------------------------------------------------
    # Orchestrator-путь
    # ------------------------------------------------------------------

    async def _run_orchestrator(self, user_query: str) -> None:
        """Запускает новый линейный пайплайн.

        Важно: НЕ эмитим ``research_mode_detected(True)`` — иначе ChatPanel
        уйдёт в паспорт исследования (там нет summary и раздел «Выводы»
        будет пустым). Оркестратор даёт готовый ответ + диаграмму +
        evidence pack; Panel обрабатывает их через ``orchestrator_result``.
        """
        assert self.orchestrator is not None

        def _on_event(kind: str, data: dict) -> None:
            # Эмитим наружу как есть.
            self.orchestrator_event.emit(kind, data)
            # Зеркалим прогресс в thought-stream — пользователь видит,
            # что работа идёт (без каракулей).
            if kind == "classify":
                self.thought_chunk.emit(f"🤔 Классификация: {str(data.get('query', ''))[:80]}\n")
            elif kind == "plan":
                self.thought_chunk.emit(
                    f"📋 План: task={data.get('task')}, "
                    f"scope={data.get('scope')}, deep={data.get('deep')}, "
                    f"файлов={data.get('files')}\n"
                )
            elif kind == "coverage":
                val = float(data.get("value", 0.0))
                self.thought_chunk.emit(f"📊 Покрытие: {val:.0%}\n")
            elif kind == "synthesize":
                self.status_changed.emit("✍️ Синтез ответа (7B CPU)…")
            elif kind == "regen":
                self.thought_chunk.emit(f"🔄 Регенерация: {data.get('reason', '')}\n")
            elif kind == "regen_skipped":
                self.thought_chunk.emit(
                    f"⏭ Регенерация пропущена ({data.get('reason', '')}) — оставляю первый вариант\n"
                )
            elif kind == "nudge":
                gaps = data.get("gaps") or []
                preview = ", ".join(str(g) for g in gaps[:3])
                self.thought_chunk.emit(f"🔁 Досбор данных (итерация {data.get('iter')}): {preview}\n")

        project_root = Path(self.context.project_root or ".")
        if not project_root.exists():
            raise FileNotFoundError(f"Корень проекта не найден: {project_root}")

        self.status_changed.emit("🧠 Оркестратор: классификация…")
        result: RunResult = await self.orchestrator.run(
            user_query,
            project_root=project_root,
            history=self.messages_history,
            on_event=_on_event,
            quality_mode=False,
            time_budget_s=ORCHESTRATOR_TIME_BUDGET_S,
        )

        # Стримим ответ одним куском (оркестратор не стримит токены).
        # ChatPanel._on_chunk создаст ChatMessageItem.
        self.research_mode_detected.emit(False)
        if result.answer:
            self.chunk_received.emit(result.answer)

        # Полный RunResult (answer + diagram + evidence) — Panel разрулит.
        self.orchestrator_result.emit(result)
        # НЕ эмитим finish_received — Panel сделает cleanup в
        # _on_orchestrator_result; иначе будет дубль сообщения.

    async def _on_token(self, token: str):
        self.chunk_received.emit(token)

    async def _on_thinking(self, token: str):
        self.thinking_received.emit(token)

    async def _on_status(self, text: str):
        self.status_changed.emit(text)

    async def _on_tool_result(
        self,
        name: str,
        content: str,
        is_error: bool,
        metadata: dict | None = None,
    ):
        self.tool_executed.emit(name, content, is_error, metadata)


class RagSearchThread(QThread):
    """Поток для асинхронного RAG-поиска."""

    results_ready = Signal(str, list)
    search_error = Signal(str)

    def __init__(
        self,
        vector_store,
        query: str,
        n_results: int = 5,
        file_filter: list[str] | None = None,
        categories: list[str] | None = None,
    ):
        super().__init__()
        self.vector_store = vector_store
        self.query = query
        self.n_results = n_results
        self.file_filter = file_filter
        # [9.4] Категории RAG из профиля (code / docs / оба).
        self.categories = categories

    def run(self):
        try:
            asyncio.run(self._search())
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                self.search_error.emit(str(e))
            else:
                raise

    async def _search(self):
        try:
            results = await self.vector_store.search(
                self.query,
                n_results=self.n_results,
                file_filter=self.file_filter,
                categories=self.categories,
            )
            results = _dedupe_by_file(results)[: self.n_results]
            if results:
                context_parts = []
                sources: list[tuple[str, int, int]] = []
                for r in results:
                    context_parts.append(
                        f"### Файл: {r.file_path} "
                        f"(строки {r.chunk.start_line}-{r.chunk.end_line})\n"
                        f"```\n{r.chunk.content}\n```"
                    )
                    sources.append((r.file_path, r.chunk.start_line, r.chunk.end_line))

                context = "\n\n".join(context_parts)
                self.results_ready.emit(context, sources)
            else:
                self.results_ready.emit("", [])
        except Exception as e:
            self.search_error.emit(str(e))


class _OllamaCheckThread(QThread):
    """Проверяет доступность Ollama в фоне и возвращает список моделей.

    Два сигнала:
      * ``result(bool)`` — обратная совместимость для ``ChatPanel``
        (ему нужна только доступность бэкенда);
      * ``models_ready(bool, list)`` — расширение для ``MainWindow``,
        которому нужен список моделей для combo-бокса и статус-бара.

    Раньше ``MainWindow._do_check_ollama`` делал
    ``new_event_loop().run_until_complete(...)`` прямо на GUI-потоке —
    это блокировало окно на всё время ``is_available()`` (до 3 с таймаута
    при недоступном сервере) и ``list_models()``. Теперь оба запроса
    выполняются здесь, а ``MainWindow`` слушает ``models_ready``.
    """

    result = Signal(bool)
    models_ready = Signal(bool, list)

    def __init__(self, client: OllamaClient):
        super().__init__()
        self.client = client

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            try:
                ok = loop.run_until_complete(self.client.is_available())
                models: list[str] = []
                if ok:
                    try:
                        raw = loop.run_until_complete(self.client.list_models())
                        models = [m.name for m in raw]
                    except Exception as e:
                        logger.debug(f"list_models failed: {e}")
                self.result.emit(ok)
                self.models_ready.emit(ok, models)
            finally:
                loop.close()
        except Exception as e:
            logger.debug(f"Ollama check failed: {e}")
            self.result.emit(False)
            self.models_ready.emit(False, [])


class _SLMLoadThread(QThread):
    """Загружает SLM в фоне."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        chat_path: Path,
        embed_path: Path | None,
        n_ctx: int = 4096,
        n_threads: int = 4,
        n_batch: int = 512,
        n_gpu_layers: int = 0,
        max_tokens: int = 1024,
        params: object | None = None,  # OptimizedParams | None
    ):
        super().__init__()
        self.chat_path = chat_path
        self.embed_path = embed_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_batch = n_batch
        self.n_gpu_layers = n_gpu_layers
        self.max_tokens = max_tokens
        self.params = params

    def run(self):
        try:
            client = LocalLLMClient(
                model_path=self.chat_path,
                embed_model_path=self.embed_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                n_batch=self.n_batch,
                n_gpu_layers=self.n_gpu_layers,
                max_tokens=self.max_tokens,
                params=self.params,
            )
            self.finished.emit(client)
        except Exception as e:
            logger.exception("Ошибка загрузки SLM")
            self.error.emit(str(e))


class _SLMDownloadThread(QThread):
    """Скачивает выбранную chat-модель и общую embedding-модель."""

    finished = Signal(str, str)
    progress = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        model_repo: str,
        model_file: str,
        embed_repo: str,
        embed_file: str,
    ):
        super().__init__()
        self.model_repo = model_repo
        self.model_file = model_file
        self.embed_repo = embed_repo
        self.embed_file = embed_file

    def run(self):
        try:
            parts = get_model_parts(self.model_repo, self.model_file)
            if parts > 1:
                self.progress.emit(f"Скачивание {self.model_file} ({parts} частей)...")
            else:
                self.progress.emit(f"Скачивание chat-модели {self.model_file}...")

            chat = download_model(self.model_repo, self.model_file)

            embed_path = ""
            try:
                self.progress.emit(f"Скачивание embedding-модели {self.embed_file}...")
                embed = download_model(self.embed_repo, self.embed_file)
                embed_path = str(embed)
            except Exception as e:
                logger.warning(f"Embedding-модель не скачана: {e}")

            self.finished.emit(str(chat), embed_path)
        except Exception as e:
            logger.exception("Ошибка скачивания SLM")
            self.error.emit(str(e))


class WebSearchThread(QThread):
    """Поток для интернет-поиска и генерации ответа через router."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        router: ModelRouter,
        model: str,
        query: str,
        messages_history: list[ChatMessage],
        search_cache=None,
    ):
        super().__init__()
        self.router = router
        self.model = model
        self.query = query
        self.messages_history = messages_history
        self.search_cache = search_cache

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        try:
            from tools.web_search import web_search

            search_results = await web_search(
                query=self.query,
                max_results=5,
                search_cache=self.search_cache,
            )

            if not search_results or "Ничего не найдено" in search_results:
                self.error_occurred.emit("Поиск не дал результатов. Попробуйте уточнить запрос.")
                return

            system_prompt = (
                "Ты — поисковый ассистент. Используй результаты поиска для ответа. "
                "Отвечай на русском языке, будь лаконичен и полезен."
            )
            user_content = f"Вопрос: {self.query}\n\nРезультаты поиска:\n{search_results}"

            messages = [
                ChatMessage(role="system", content=system_prompt),
                *self.messages_history,
                ChatMessage(role="user", content=user_content),
            ]

            async for chunk in self.router.chat(model=self.model, messages=messages, stream=True):
                if chunk.reasoning_content:
                    self.thinking_received.emit(chunk.reasoning_content)
                if chunk.content:
                    self.chunk_received.emit(chunk.content)
                if chunk.done:
                    self.finish_received.emit()

        except Exception as e:
            self.error_occurred.emit(f"Ошибка: {e}")


class _ModelPoolLoadThread(QThread):
    """Загружает обе модели (planner + analyst) ПОСЛЕДОВАТЕЛЬНО.

    Изменения по сравнению с предыдущей версией (после инцидента, когда
    две qwen2.5-1.5b грузились параллельно на GPU и вешали поток):

    * **Последовательная загрузка** вместо ``asyncio.gather`` — вторая
      модель не пытается занять VRAM, пока первая не отпустила контекст.
    * **Per-role timeout 90 с** — если llama.cpp завис в нативном коде
      (OOM на CUDA), поток всё равно выходит, UI получает ``error`` и
      показывает «⚠️ 2-model: недоступен», а не «⏳» вечно.
    * **`device` из `RoleSpec` учитывается** — analyst с ``device="CPU"``
      жёстко получает ``n_gpu_layers=0`` (в ``optimize()`` и в factory
      как safety net).
    * **Fail-fast при одинаковых моделях** — если planner и analyst
      указывают на один файл, выдаём понятную ошибку, а не OOM.
    """

    finished = Signal(object)  # ModelPool | None
    error = Signal(str)
    progress = Signal(str)

    _ROLE_LOAD_TIMEOUT = 90.0

    def run(self):
        try:
            from agents.download_slm import get_model_path
            from agents.local_llm_client import LocalLLMClient
            from agents.model_pool import ModelPool, Role, RoleSpec
            from agents.slm_optimizer import OptimizedParams, optimize
            from agents.slm_selector import select_pair

            pair = select_pair()
            planner_info = pair.get("planner")
            analyst_info = pair.get("analyst")
            if not planner_info or not analyst_info:
                self.error.emit(
                    "Двухмодельный пайплайн недоступен. Нужны ДВЕ разные "
                    "модели: planner (3B или 1.5B) и analyst (7B или 14B). "
                    "Скачайте недостающую через «View → Локальные модели»."
                )
                return

            if planner_info.id == analyst_info.id:
                self.error.emit(
                    f"planner и analyst указывают на одну модель "
                    f"({planner_info.id}). Скачайте вторую модель "
                    f"(например, 7B) и повторите."
                )
                return

            planner_path = get_model_path(planner_info.file)
            if planner_path is None:
                self.error.emit(f"Файл planner не найден: {planner_info.file}")
                return
            analyst_path = get_model_path(analyst_info.file)
            if analyst_path is None:
                self.error.emit(f"Файл analyst не найден: {analyst_info.file}")
                return

            # --- Оптимизация: устройство задаётся ЯВНО ---
            # planner на GPU (пытаемся уместить целиком), analyst на CPU
            # (никогда не делит VRAM с planner).
            self.progress.emit(f"Оптимизация planner: {planner_info.display_name}")
            try:
                planner_params = optimize(
                    planner_path,
                    target_n_ctx=8192,
                    device="GPU",
                )
            except Exception as e:
                logger.exception("planner optimize failed")
                self.error.emit(f"planner optimize failed: {e}")
                return

            self.progress.emit(f"Оптимизация analyst: {analyst_info.display_name}")
            try:
                analyst_params = optimize(
                    analyst_path,
                    target_n_ctx=8192,
                    device="CPU",
                )
            except Exception as e:
                logger.exception("analyst optimize failed")
                self.error.emit(f"analyst optimize failed: {e}")
                return

            specs: dict[Role, RoleSpec] = {
                Role.PLANNER: RoleSpec(
                    role=Role.PLANNER,
                    model_id=planner_info.id,
                    device="GPU",
                    n_ctx=planner_params.n_ctx,
                    kv_cache=planner_params.kv_cache_type,
                    n_gpu_layers=planner_params.n_gpu_layers,
                    temperature=0.0,
                    file=str(planner_path),
                ),
                Role.ANALYST: RoleSpec(
                    role=Role.ANALYST,
                    model_id=analyst_info.id,
                    device="CPU",
                    n_ctx=analyst_params.n_ctx,
                    kv_cache=analyst_params.kv_cache_type,
                    n_gpu_layers=analyst_params.n_gpu_layers,
                    temperature=0.35,
                    file=str(analyst_path),
                ),
            }

            def factory(spec: RoleSpec) -> LocalLLMClient:
                path = Path(spec.file) if spec.file else None
                if path is None or not path.exists():
                    raise FileNotFoundError(f"Model file missing: {spec.model_id}")
                base = planner_params if spec.role is Role.PLANNER else analyst_params
                # Safety net: если device=CPU, но n_gpu_layers≠0 —
                # жёстко обнуляем и выключаем FlashAttention.
                n_gpu_layers = spec.n_gpu_layers
                flash = base.flash_attn
                if spec.device == "CPU":
                    if n_gpu_layers != 0:
                        logger.warning(
                            f"SLM {spec.role.value}: device=CPU, но n_gpu_layers={n_gpu_layers} → 0"
                        )
                        n_gpu_layers = 0
                    flash = False
                params = OptimizedParams(
                    n_ctx=spec.n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    n_threads=base.n_threads,
                    n_batch=base.n_batch,
                    flash_attn=flash,
                    use_mmap=base.use_mmap,
                    use_mlock=base.use_mlock,
                    kv_cache_type=spec.kv_cache,
                    vram_used_mb=base.vram_used_mb if n_gpu_layers != 0 else 0,
                    ram_used_mb=(
                        base.ram_used_mb if n_gpu_layers != 0 else base.ram_used_mb + base.vram_used_mb
                    ),
                    notes=base.notes,
                )
                return LocalLLMClient(model_path=path, params=params, max_tokens=1024)

            # --- Последовательная загрузка с per-role timeout ---
            pool = ModelPool()
            loop = asyncio.new_event_loop()
            try:
                for role, spec in specs.items():
                    self.progress.emit(
                        f"⏳ Загрузка {role.value}: {spec.model_id} "
                        f"(device={spec.device}, "
                        f"n_gpu_layers={spec.n_gpu_layers})…"
                    )
                    try:
                        loop.run_until_complete(
                            asyncio.wait_for(
                                pool.add_role(spec, factory),
                                timeout=self._ROLE_LOAD_TIMEOUT,
                            )
                        )
                    except TimeoutError:
                        logger.error(
                            f"SLM pool: таймаут загрузки {role.value} "
                            f"({spec.model_id}) за "
                            f"{self._ROLE_LOAD_TIMEOUT} с"
                        )
                        self.error.emit(
                            f"Таймаут загрузки {role.value} "
                            f"({spec.model_id}) за "
                            f"{self._ROLE_LOAD_TIMEOUT:.0f} с. "
                            f"Скорее всего не хватает VRAM/RAM или модель "
                            f"битая. Проверьте свободную память и повторите."
                        )
                        return
                    except Exception as e:
                        logger.exception(f"SLM pool: ошибка загрузки {role.value}")
                        self.error.emit(f"Ошибка загрузки {role.value} ({spec.model_id}): {e}")
                        return
                    logger.info(
                        f"SLM pool: {role.value} загружен "
                        f"(model={spec.model_id}, device={spec.device}, "
                        f"n_gpu_layers={spec.n_gpu_layers}, "
                        f"n_ctx={spec.n_ctx}, kv={spec.kv_cache})"
                    )
            finally:
                loop.close()

            # Финальная проверка, что обе роли на месте.
            try:
                pool.client(Role.PLANNER)
                pool.client(Role.ANALYST)
            except RuntimeError as e:
                self.error.emit(f"ModelPool incomplete: {e}")
                return

            self.progress.emit("✅ Двухмодельный пайплайн готов")
            self.finished.emit(pool)

        except Exception as e:
            logger.exception("ModelPool load failed")
            self.error.emit(str(e))

class _BenchmarkThread(QThread):
    """Фоновый прогон замеров производительности (4.7.B).

    Прогоняет модели последовательно через `agents.benchmark.run_benchmark`.
    Каждая модель загружается → меряются `load_ms` × repeats + промпты ×
    repeats → выгружается. GUI не блокируется.
    """

    progress = Signal(str)
    finished = Signal(object)  # BenchmarkResult
    error = Signal(str)

    def __init__(self, models: list[dict], *, prompts=None, repeats: int = 2):
        super().__init__()
        self._models = models
        self._prompts = prompts
        self._repeats = repeats

    def run(self):
        from agents.benchmark import run_benchmark

        async def _run():
            return await run_benchmark(
                self._models,
                prompts=self._prompts,
                repeats=self._repeats,
                on_progress=self.progress.emit,
            )

        try:
            result = asyncio.run(_run())
            self.finished.emit(result)
        except Exception as e:
            logger.exception("Benchmark failed")
            self.error.emit(str(e))