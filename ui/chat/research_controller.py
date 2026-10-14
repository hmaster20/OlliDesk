# ui/chat/research_controller.py
"""Управление research-режимом агента.

Инкапсулирует всё, что связано с исследовательским прогоном:

* серый поток мыслей (`ChatThoughtStream`);
* виджеты гипотез (`ChatHypothesisItem`);
* буфер финального ответа (идёт в паспорт, а не в ленту);
* сборку паспорта исследования (`ChatResearchPassport`);
* сброс состояния между запросами и при отмене.

Контроллер НЕ вставляет виджеты в layout сам — он эмитит сигнал
`insert_widget`, а панель решает, куда и как это поместить. Так модуль
не зависит от конкретной компоновки чата.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ui.chat.message_item import ChatHypothesisItem
from ui.chat.research_passport import ChatResearchPassport
from ui.chat.thought_stream import ChatThoughtStream


class ResearchController(QObject):
    """Состояние research-прогона и виджеты для его отображения.

    Сигналы:
        insert_widget(widget): просьба вставить виджет в ленту.
        scroll_to_bottom(): прокрутить чат вниз.
    """

    insert_widget = Signal(QWidget)
    scroll_to_bottom = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._research_mode_active: bool = False
        self._research_cancelled: bool = False
        self._pending_final_answer: str = ""
        self._last_research_summary: dict[str, Any] | None = None
        self._current_thought_stream: ChatThoughtStream | None = None
        self._hypothesis_widgets: dict[str, ChatHypothesisItem] = {}

    # -------------------------------------------------------------
    # Состояние
    # -------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True, если текущий запрос — research."""
        return self._research_mode_active

    @property
    def is_cancelled(self) -> bool:
        """True, если пользователь отменил генерацию."""
        return self._research_cancelled

    @property
    def thought_stream(self) -> ChatThoughtStream | None:
        """Активный серый поток мыслей (или ``None``)."""
        return self._current_thought_stream

    def reset(self) -> None:
        """Сбрасывает состояние между запросами.

        Виджеты в ленте остаются (это визуальный архив), но ссылки на
        активные объекты обнуляются. `_hypothesis_widgets` НЕ очищается —
        иначе повторный `update_hypothesis` для той же гипотезы создал бы
        второй виджет. Полная очистка — в `clear()`.
        """
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._current_thought_stream = None

    def clear(self) -> None:
        """Полная очистка при «Очистить чат»."""
        self._research_mode_active = False
        self._research_cancelled = False
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._current_thought_stream = None
        self._hypothesis_widgets.clear()

    # -------------------------------------------------------------
    # Обработчики сигналов AgentChatThread
    # -------------------------------------------------------------

    def on_research_mode_detected(self, is_research: bool) -> None:
        """Запоминает режим прогона."""
        self._research_mode_active = is_research
        if is_research:
            logger.info("Research-режим активен: серый поток мыслей включён")

    def on_thought_chunk(self, token: str) -> None:
        """Токен мысли — в серый поток в реальном времени."""
        if self._current_thought_stream is None:
            self._current_thought_stream = ChatThoughtStream()
            self.insert_widget.emit(self._current_thought_stream)
        self._current_thought_stream.append_thought_token(token)
        self.scroll_to_bottom.emit()

    def on_final_answer_started(self) -> None:
        """Начался финальный ответ — сбрасываем буфер паспорта."""
        self._pending_final_answer = ""

    def on_final_chunk(self, token: str) -> None:
        """Токен финального ответа — в буфер паспорта, НЕ в ленту."""
        self._pending_final_answer += token

    def on_research_summary_ready(self, summary: dict[str, Any]) -> None:
        """Запоминает сводку до `finish_received`."""
        self._last_research_summary = summary
        logger.debug(
            "Research-сводка: файлов прочитано "
            f"{len(summary.get('files_read') or [])}, "
            f"гипотез {len(summary.get('hypotheses') or [])}"
        )

    # -------------------------------------------------------------
    # Обновление виджетов гипотез
    # -------------------------------------------------------------

    def update_hypothesis_widget(self, content: str) -> bool:
        """Разбирает JSON-ответ тула гипотезы и обновляет/создаёт виджет.

        Args:
            content: JSON-строка из `record_hypothesis` / `update_hypothesis`.

        Returns:
            True, если виджет был обновлён (или создан). False — если
            контент не является ожидаемым JSON или не содержит гипотезы.
        """
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(data, dict) or not data.get("ok"):
            return False
        h = data.get("hypothesis")
        if not isinstance(h, dict):
            return False
        hid = str(h.get("id") or "")
        if not hid:
            return False

        widget = self._hypothesis_widgets.get(hid)
        if widget is None:
            widget = ChatHypothesisItem(
                hypothesis_id=hid,
                statement=str(h.get("statement") or ""),
            )
            self._hypothesis_widgets[hid] = widget
            self.insert_widget.emit(widget)

        widget.update_state(
            status=str(h.get("status") or "open"),
            evidence=str(h.get("evidence") or ""),
            verification_method=str(h.get("verification_method") or ""),
        )

        # Дублируем в серый поток компактной строкой.
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_hypothesis(
                hypothesis_id=hid,
                status=str(h.get("status") or "open"),
                statement=str(h.get("statement") or ""),
                evidence=str(h.get("evidence") or ""),
            )

        self.scroll_to_bottom.emit()
        return True

    # -------------------------------------------------------------
    # Завершение прогона
    # -------------------------------------------------------------

    def finalize_research(self) -> dict[str, Any] | None:
        """Возвращает готовую сводку с финальным ответом, если есть что показать.

        Вызывается из `_on_agent_finish` в research-режиме. Возвращает
        ``None``, если показывать паспорт не нужно (нет данных).

        Побочный эффект: вставляет виджет паспорта в ленту через сигнал
        и сбрасывает внутреннее состояние.

        Returns:
            Сводку (для отладки/логов) или ``None``.
        """
        if self._research_cancelled:
            self._reset_after_run()
            return None

        summary = self._last_research_summary
        result: dict[str, Any] | None = None

        if summary:
            summary["final_answer"] = self._pending_final_answer
            if self._should_show_passport(summary):
                try:
                    widget = ChatResearchPassport(summary)
                    self.insert_widget.emit(widget)
                    self.scroll_to_bottom.emit()
                except Exception as e:
                    logger.warning(f"Не удалось построить паспорт: {e}")
            result = summary

        self._reset_after_run()
        return result

    def _reset_after_run(self) -> None:
        """Внутренний сброс после завершения research-прогона."""
        self._research_mode_active = False
        self._pending_final_answer = ""
        self._last_research_summary = None
        self._current_thought_stream = None

    @staticmethod
    def _should_show_passport(summary: dict[str, Any] | None) -> bool:
        """Показывать ли паспорт по этой сводке.

        Правило: прочитано ≥2 файла, или есть хотя бы одна гипотеза, или
        есть непустой финальный текст. Пустой паспорт (0 файлов, 0 гипотез,
        без выводов) — шум.
        """
        if not summary:
            return False
        files = summary.get("files_read") or []
        hypotheses = summary.get("hypotheses") or []
        final_answer = (summary.get("final_answer") or "").strip()
        return bool(len(files) >= 2 or hypotheses or final_answer)

    # -------------------------------------------------------------
    # Отмена
    # -------------------------------------------------------------

    def mark_cancelled(self, reason: str = "") -> None:
        """Помечает прогон как отменённый пользователем.

        Помечает серый поток как прерванный, но не удаляет его из ленты —
        это визуальный архив для ознакомления.
        """
        self._research_cancelled = True
        if self._current_thought_stream is not None:
            self._current_thought_stream.mark_interrupted(
                reason or "Рассуждение прервано пользователем."
            )

    # -------------------------------------------------------------
    # Вспомогательные методы для серого потока
    # -------------------------------------------------------------

    def append_action_to_stream(
        self, name: str, arguments: dict[str, Any] | None
    ) -> None:
        """Добавляет строку действия (вызов инструмента) в серый поток."""
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_action(name, arguments)

    def append_result_to_stream(
        self, name: str, ok: bool, preview: str
    ) -> None:
        """Добавляет строку результата в серый поток."""
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_result(
                name, ok=ok, preview=preview
            )

    def append_nudge_to_stream(self, text: str) -> None:
        """Добавляет строку нуджа в серый поток."""
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_nudge(text)

    def append_phase_to_stream(self, text: str) -> None:
        """Добавляет строку фазы в серый поток."""
        if self._current_thought_stream is not None:
            self._current_thought_stream.append_phase(text)

    # -------------------------------------------------------------
    # Восстановление для истории
    # -------------------------------------------------------------

    @property
    def hypothesis_widgets(self) -> dict[str, ChatHypothesisItem]:
        """Прямой доступ к словарю виджетов (для тестов и отладки)."""
        return self._hypothesis_widgets