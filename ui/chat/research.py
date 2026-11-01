"""Research-режим и двухмодельный пайплайн (вынесено из panel.py, 2.4).

`ResearchMixin` содержит методы ChatPanel, отвечающие за:
  * research-прогон: мысленный поток (`ChatThoughtStream`),
    финальный ответ, паспорт исследования;
  * ленивую загрузку двухмодельного пайплайна (planner + analyst);
  * создание `Orchestrator` и обработку его событий/результата;
  * индикатор пайплайна (`_pipeline_status_label`, `_update_pipeline_status`).

Поля объявлены в ChatPanel.__init__ (`_current_thought_stream`,
`_research_mode_active`, `_pending_final_answer`, `_last_research_summary`,
`_model_pool`, `_orchestrator`, `_pool_load_thread`, `_pool_load_failed`,
`_pipeline_selectors`, `_dashboard` и др.). Миксин использует их через self.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QTimer, Slot

from agents.model_pool import ModelPool, Role
from agents.ollama_client import MessagePart, TextPart
from agents.orchestrator import Orchestrator, RunResult
from core.budget import ApproxCounter

if TYPE_CHECKING:
    from agents.ollama_client import ChatMessage
    from ui.chat.message_item import ChatMessageItem

class ResearchMixin:
    """Методы ChatPanel для research-режима и двухмодельного пайплайна."""

    # ------------------------------------------------------------------
    # Поля ChatPanel (class-level для mypy).
    # ------------------------------------------------------------------
    _current_thought_stream: Any  # ChatThoughtStream | None
    _research_mode_active: bool
    _research_cancelled: bool
    _pending_final_answer: str
    _last_research_summary: dict | None
    _model_pool: ModelPool | None
    _orchestrator: Orchestrator | None
    _pool_load_thread: Any  # _ModelPoolLoadThread | None
    _pool_load_failed: bool
    _pipeline_selectors: Any  # PipelineSelectors | None
    _dashboard: Any  # StatusDashboard | None
    _profile: Any  # ProfileConfig | None
    vector_store: Any
    project_root: str
    status_label: Any  # QLabel

    # --- NEW: поля, использованные в _reset_research_state / _on_orchestrator_result ---
    _current_assistant_parts: list[MessagePart]
    _current_assistant_text: str
    _current_assistant_reasoning: str
    _current_assistant_content: str
    _current_thinking_content: str
    _last_assistant_widget: ChatMessageItem | None
    _session_id: str | None
    _messages: list[ChatMessage]
    session_store: Any
    scroll_layout: Any

    # --- NEW: методы ChatPanel, которые вызывает ResearchMixin ---
    _scroll_to_bottom: Callable[[], None]
    _sync_dashboard: Callable[[], None]
    _reset_send_button: Callable[[], None]
    pipeline_status_changed: Any  # Signal(str)

    # ------------------------------------------------------------------
    # Сброс research-состояния (вызывается перед каждым запросом).
    # ------------------------------------------------------------------

    def _reset_research_state(self) -> None:
        """Сбрасывает state текущего research-прогона перед новым запросом."""
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._current_thought_stream = None
        # 0.1.3b: буферы частей должны быть пусты к началу нового прогона —
        # иначе parts предыдущего ответа «прилипнут» к следующему.
        self._current_assistant_parts = []
        self._current_assistant_text = ""
        self._current_assistant_reasoning = ""

    # ------------------------------------------------------------------
    # Слоты research-режима (подключаются в _connect_agent_thread_signals).
    # ------------------------------------------------------------------

    @Slot(bool)
    def _on_research_mode_detected(self, is_research: bool) -> None:
        """Запоминает, research-ли этот прогон.

        Если да — `chunk_received` для отображения не используется;
        серый поток идёт через `thought_chunk`, а финал — через
        `final_chunk` (в буфер паспорта).
        """
        self._research_mode_active = is_research
        if is_research:
            logger.info("Research-режим активен: серый поток мыслей включён")

    @Slot(str)
    def _on_thought_chunk(self, token: str) -> None:
        """Токен мысли — в серый поток.

        [M4] looks_like_ascii_junk рассчитан на блоки ≥20 символов, поэтому
        на каждом отдельном токене не срабатывает. Проверяем накопленный
        буфер (до 500 символов): если в нём уже накопилось ≥3 «каракулей» —
        показываем вместо мыслей строку «резервный сбор данных».
        """
        from agents.tool_grammar import looks_like_ascii_junk
        from ui.chat.thought_stream import ChatThoughtStream

        if self._current_thought_stream is None:
            self._current_thought_stream = ChatThoughtStream()
            self.scroll_layout.insertWidget(
                self.scroll_layout.count() - 1,
                self._current_thought_stream,
            )

        self._current_thought_stream.append_thought_token(token)

        # Периодически проверяем накопленный буфер (после ~5 токенов
        # по 4+ символа он станет ≥20 и детектор начнёт работать).
        buf = self._current_thought_stream._current_thought_buffer
        if len(buf) >= 40 and looks_like_ascii_junk(buf):
            # Сворачиваем «мысли» и оставляем только пометку.
            self._current_thought_stream._current_thought_buffer = ""
            self._current_thought_stream.append_nudge(
                "⚙️ Резервный сбор данных (модель не вызвала инструмент)"
            )

        QTimer.singleShot(0, self._scroll_to_bottom)

    @Slot()
    def _on_final_answer_started(self) -> None:
        """Начался финальный ответ — сбрасываем буфер паспорта."""
        self._pending_final_answer = ""

    @Slot(str)
    def _on_final_chunk(self, token: str) -> None:
        """Токен финального ответа — в буфер паспорта, НЕ в ленту."""
        self._pending_final_answer += token

    @Slot(object)
    def _on_research_summary_ready(self, summary: dict) -> None:
        """Запоминает сводку research-прогона (до finish_received)."""
        self._last_research_summary = summary
        logger.debug(
            "Research-сводка: файлов прочитано "
            f"{len(summary.get('files_read') or [])}, "
            f"гипотез {len(summary.get('hypotheses') or [])}"
        )

    # ------------------------------------------------------------------
    # Паспорт исследования
    # ------------------------------------------------------------------

    @staticmethod
    def _should_show_passport(summary: dict | None) -> bool:
        """Показывать ли паспорт по этой сводке.

        Правило: показываем, если прочитано ≥2 файла или есть хотя бы одна
        гипотеза. Пустой паспорт (0 файлов, 0 гипотез, без выводов) — шум.
        """
        if not summary:
            return False
        files = summary.get("files_read") or []
        hypotheses = summary.get("hypotheses") or []
        final_answer = (summary.get("final_answer") or "").strip()
        return bool(len(files) >= 2 or hypotheses or final_answer)

    def _show_research_passport(self, summary: dict) -> None:
        """Вставляет виджет «Паспорт исследования» под финальным ответом."""
        from ui.chat.research_passport import ChatResearchPassport

        try:
            widget = ChatResearchPassport(summary)
        except Exception as e:
            logger.warning(f"Не удалось построить паспорт исследования: {e}")
            return
        self.scroll_layout.insertWidget(
            self.scroll_layout.count() - 1,
            widget,
        )
        QTimer.singleShot(0, self._scroll_to_bottom)

    # ------------------------------------------------------------------
    # Индикатор пайплайна
    # ------------------------------------------------------------------

    def _pipeline_status_label(self) -> str:
        """Короткое описание состояния двухмодельного пайплайна."""
        if self._model_pool is not None and self._orchestrator is not None:
            try:
                planner_id = self._model_pool.spec(Role.PLANNER).model_id
                analyst_id = self._model_pool.spec(Role.ANALYST).model_id
                return f"⚡ 2-model: {planner_id} (GPU) + {analyst_id} (CPU)"
            except Exception:
                return "⚡ 2-model: загружен"

        if self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            return "⏳ 2-model: загрузка planner+analyst…"

        if self._pool_load_failed:
            return "⚠️ 2-model: недоступен (fallback → 1 модель)"

        if self._model_pool is not None:
            return "⏸ 2-model: модели загружены, ждут проект"

        if not self.project_root:
            return "💤 2-model: не активирован (нет проекта)"

        return "⏸ 2-model: ожидание запуска"

    def _update_pipeline_status(self) -> None:
        """Обновляет индикатор пайплайна.

        Пишет в `_pipeline_selectors` (новый виджет 9.3) и в
        `pipeline_status_label` (для обратной совместимости, если виджет
        не создан). Эмитит `pipeline_status_changed` для приборной панели.
        """
        text = self._pipeline_status_label()

        # Старый pipeline_status_label (может отсутствовать).
        old_label = getattr(self, "pipeline_status_label", None)
        if old_label is not None:
            old_label.setText(text)

        # Новый виджет-селектор (9.3).
        if self._pipeline_selectors is not None:
            if self._model_pool is not None and self._orchestrator is not None:
                icon, color = "⚡", "#98c379"
                tooltip = self._pipeline_tooltip()
            elif self._pool_load_thread is not None and self._pool_load_thread.isRunning():
                icon, color = "⏳", "#e5c07b"
                tooltip = "Загрузка planner + analyst…"
            elif self._pool_load_failed:
                icon, color = "⚠️", "#e06c75"
                tooltip = "Пайплайн недоступен — fallback на одиночную модель"
            elif not self.project_root:
                icon, color = "💤", "#888"
                tooltip = "Откройте проект, чтобы активировать пайплайн"
            else:
                icon, color = "⏸", "#888"
                tooltip = "Ожидание запуска"
            self._pipeline_selectors.set_state(icon, text, tooltip, color)

        # Приборная панель.
        if self._dashboard is not None:
            self._sync_dashboard()

        # Сигнал наружу (MainWindow).
        self.pipeline_status_changed.emit(text)

    def _pipeline_tooltip(self) -> str:
        """Подробности о загруженных planner/analyst для tooltip."""
        if self._model_pool is None or self._orchestrator is None:
            return ""
        try:
            ps = self._model_pool.spec(Role.PLANNER)
            an = self._model_pool.spec(Role.ANALYST)
            return (
                f"planner={ps.model_id} (device={ps.device}, "
                f"n_ctx={ps.n_ctx}, kv={ps.kv_cache}, layers={ps.n_gpu_layers})\n"
                f"analyst={an.model_id} (device={an.device}, "
                f"n_ctx={an.n_ctx}, kv={an.kv_cache}, layers={an.n_gpu_layers})"
            )
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Orchestrator lifecycle
    # ------------------------------------------------------------------

    def _ensure_orchestrator(self) -> None:
        """Ленивая загрузка двух моделей + Orchestrator (в фоне).

        Вызывается при открытии проекта (с задержкой), из меню
        «View → Предзагрузить 2-model pipeline» и при research-вопросе.
        Если уже загружен/грузится — no-op. Ошибка →
        ``_pool_load_failed=True``, orchestrator остаётся None.
        """
        if self._orchestrator is not None:
            return

        # Профиль «Обучение» не использует двухмодельный пайплайн —
        # одна analyst-модель, tools off. Не грузим лишнюю память.
        if self._profile is not None and self._profile.id == "learning":
            logger.debug("Orchestrator: профиль «Обучение» — пайплайн не требуется")
            return

        if self._pool_load_failed:
            return
        if self._pool_load_thread is not None and self._pool_load_thread.isRunning():
            return

        from ui.chat.threads import _ModelPoolLoadThread

        self._pool_load_thread = _ModelPoolLoadThread()
        self._pool_load_thread.progress.connect(self._on_pool_progress)
        self._pool_load_thread.finished.connect(self._on_pool_loaded)
        self._pool_load_thread.error.connect(self._on_pool_error)
        self._pool_load_thread.start()
        self.status_label.setText("⏳ Загрузка двух моделей (3B GPU + 7B CPU)…")
        self._update_pipeline_status()

    def reset_pipeline(self) -> None:
        """Сбрасывает двухмодельный пайплайн (после смены моделей в UI).

        Вызывается из `reload_slm()` и из MainWindow после закрытия
        SLMModelsDialog с изменениями. Следующий `_ensure_orchestrator()`
        пересоздаст пайплайн с новыми моделями из конфига.

        Также сбрасывает `_pool_load_failed`, чтобы «⚠️ недоступен» не
        блокировал повторную попытку после исправления конфига
        (например, скачивания недостающей модели).
        """
        import asyncio

        if self._model_pool is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._model_pool.aclose())
                loop.close()
            except Exception:
                logger.debug("ModelPool.aclose failed", exc_info=True)

        self._model_pool = None
        self._orchestrator = None
        self._pool_load_failed = False
        self._update_pipeline_status()
        logger.info("SLM pipeline сброшен — будет пересоздан при следующем запросе")

    @Slot(object)
    def _on_pool_loaded(self, pool: ModelPool | None) -> None:
        """Колбэк загрузки пула (в GUI-потоке)."""
        if pool is None:
            self._on_pool_error("ModelPool вернул None")
            return

        intel_cache_dir: Path | None = None
        if self.project_root:
            intel_cache_dir = Path(self.project_root) / ".ollidesk" / "graph"
            intel_cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._model_pool = pool
            self._orchestrator = Orchestrator(
                pool=pool,
                intel_cache_dir=intel_cache_dir,
                vector_store=self.vector_store,
                counter=ApproxCounter(),
            )
            logger.info(f"Orchestrator готов: planner+analyst загружены, intel_cache={intel_cache_dir}")
            self.status_label.setText("✅ Двухмодельный пайплайн готов")
            self._update_pipeline_status()
            # Обновляем селекторы Planner/Analyst (могли измениться id).
            refresh = getattr(self, "refresh_pipeline_selectors", None)
            if callable(refresh):
                refresh()
        except Exception as e:
            logger.exception("Не удалось создать Orchestrator")
            self._on_pool_error(f"Orchestrator init: {e}")

    @Slot(str)
    def _on_pool_error(self, error_msg: str) -> None:
        """Ошибка загрузки пайплайна (в GUI-потоке)."""
        logger.warning(f"ModelPool/Orchestrator недоступен: {error_msg}")
        self._pool_load_failed = True
        self._orchestrator = None
        self._model_pool = None
        self.status_label.setText(f"⚠️ Оркестратор недоступен (ReAct продолжит работу): {error_msg[:100]}")
        self._update_pipeline_status()

    @Slot(object)
    def _on_orchestrator_result(self, result: RunResult) -> None:
        """Обрабатывает полный RunResult: ответ + схема + evidence.

        Вызывается из AgentChatThread после `chunk_received` с полным
        текстом ответа. Здесь добавляем диаграмму и краткую сводку
        evidence, затем cleanup.
        """
        from agents.ollama_client import ChatMessage
        from ui.chat.message_item import ChatDiagramItem

        try:
            if self._last_assistant_widget is not None:
                self._last_assistant_widget.finalize()

            # Сохраняем assistant-сообщение в историю (0.1.3b).
            if result.answer:
                assistant_message = ChatMessage(
                    role="assistant",
                    parts=[TextPart(text=result.answer)],
                )
                self._messages.append(assistant_message)
                if self._session_id:
                    self.session_store.save_message(
                        self._session_id,
                        assistant_message,
                    )

            # Диаграмма компонентов.
            if result.ascii_diagram or result.mermaid_diagram:
                diagram_item = ChatDiagramItem(
                    result.ascii_diagram or "",
                    result.mermaid_diagram or "",
                )
                self.scroll_layout.insertWidget(
                    self.scroll_layout.count() - 1,
                    diagram_item,
                )

            # Evidence pack — короткая строка.
            if result.evidence_ids:
                verify_mark = "✅" if result.verify_ok else "⚠️"
                critique_mark = "✅" if result.critique_ok else "⚠️"
                unsupported = result.unsupported_claims
                summary = (
                    f"📚 Источников: {len(result.evidence_ids)} · "
                    f"покрытие {result.coverage:.0%} · "
                    f"verify {verify_mark} · critique {critique_mark}"
                )
                if unsupported:
                    summary += f" · без доказательств: {unsupported}"
            # [10.7] Coding-флоу: показываем прогресс тестов и патчи.
            if result.coding_flow:
                tests_ok = (
                    result.tests_failed == 0 and result.tests_errors == 0
                )
                icon = "✅" if tests_ok else "❌"
                summary = (
                    f"{icon} Coding: {result.tests_passed} passed"
                )
                if result.tests_failed:
                    summary += f", {result.tests_failed} failed"
                if result.tests_errors:
                    summary += f", {result.tests_errors} errors"
                summary += f" · итераций: {result.iterations}"
                if result.patches:
                    applied_ok = sum(
                        1 for p in result.patches if p.get("status") == "ok"
                    )
                    summary += f" · патчей: {applied_ok}/{len(result.patches)}"
                self._add_message("system", summary)  # type: ignore[attr-defined]

                # Патчи с ошибками — отдельным блоком.
                failed_patches = [
                    p for p in result.patches if p.get("status") == "error"
                ]
                if failed_patches:
                    lines = ["⚠️ Не применены патчи:"]
                    for p in failed_patches:
                        lines.append(
                            f"  • {p.get('path', '?')}: {p.get('error', '')}"
                        )
                    self._add_message("system", "\n".join(lines))  # type: ignore[attr-defined]

            QTimer.singleShot(0, self._scroll_to_bottom)
        except Exception:
            logger.exception("_on_orchestrator_result failed")
        finally:
            self._reset_send_button()
            self._current_assistant_content = ""
            self._current_thinking_content = ""
            self._last_assistant_widget = None
            self._current_thought_stream = None
            self._pending_final_answer = ""
            self._last_research_summary = None
            self._research_mode_active = False
            self.status_label.setText("Готов к работе")

    @Slot(str, dict)
    def _on_orchestrator_event(self, kind: str, data: dict) -> None:
        """Пробрасывает событие Orchestrator'а в UI (статус + thought)."""
        if kind == "classify":
            self.status_label.setText("🧠 Классификация…")
        elif kind == "plan":
            task = data.get("task", "?")
            files = data.get("files", 0)
            deep = data.get("deep", False)
            self.status_label.setText(f"📋 План: {task}, файлов={files}" + (" (deep)" if deep else ""))
        elif kind == "coverage":
            val = float(data.get("value", 0.0))
            self.status_label.setText(f"📊 Покрытие: {val:.0%}")
        elif kind == "synthesize":
            self.status_label.setText("✍️ Синтез (7B CPU)…")
        elif kind == "regen":
            self.status_label.setText(f"🔄 Регенерация: {str(data.get('reason', ''))[:80]}")
        elif kind == "fallback":
            self._add_message(  # type: ignore[attr-defined]
                "system",
                f"⚠️ Оркестратор упал, использую старый ReAct: {data.get('reason', '')[:120]}",
            )
        elif kind == "done":
            elapsed = data.get("elapsed_s")
            if elapsed is not None:
                logger.info(f"Orchestrator done за {elapsed} с")

    @Slot(str)
    def _on_pool_progress(self, message: str) -> None:
        """Прогресс загрузки двух моделей — в status + pipeline label."""
        self.status_label.setText(f"⚙️ {message}")
        old_label = getattr(self, "pipeline_status_label", None)
        if old_label is not None:
            old_label.setText(f"⏳ {message}")
            old_label.setStyleSheet(
                "font-size: 11px; padding: 2px 6px; color: #e5c07b;"
                " background: rgba(128,128,128,0.08); border-radius: 3px;"
            )


__all__ = ["ResearchMixin"]
