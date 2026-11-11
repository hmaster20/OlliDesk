"""Тесты 2.5: QtEventBus + LiveEventsPanel."""

from __future__ import annotations

import os

import pytest

# Без QApplication в headless-CI Qt-сигналы не работают.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.event_bus import EventBus, emit_event  # noqa: E402
from core.events import EventKind  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_bus():
    EventBus.instance().clear()
    yield
    EventBus.instance().clear()


# ---------------------------------------------------------------------------
# QtEventBus
# ---------------------------------------------------------------------------


def test_qt_bus_forwards_event(qapp) -> None:
    from ui.widgets.live_events import QtEventBus

    received: list[tuple[str, dict]] = []
    bus = QtEventBus()
    bus.event_emitted.connect(lambda k, p: received.append((k, p)))

    emit_event(EventKind.TOOL_STARTED.value, {"name": "read_file", "args_hash": "abc"})
    qapp.processEvents()

    assert received == [
        (EventKind.TOOL_STARTED.value, {"name": "read_file", "args_hash": "abc"}),
    ]
    bus.shutdown()


def test_qt_bus_shutdown_unsubscribes(qapp) -> None:
    from ui.widgets.live_events import QtEventBus

    received: list[tuple[str, dict]] = []
    bus = QtEventBus()
    bus.event_emitted.connect(lambda k, p: received.append((k, p)))
    bus.shutdown()

    emit_event(EventKind.TOOL_STARTED.value, {"name": "x", "args_hash": "y"})
    qapp.processEvents()

    assert received == []


# ---------------------------------------------------------------------------
# LiveEventsPanel
# ---------------------------------------------------------------------------


def test_panel_appends_event(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel.append_event(
        EventKind.TOOL_STARTED.value,
        {"name": "read_file", "args_hash": "abc"},
    )
    assert panel.event_count == 1
    assert panel.latest_kind == EventKind.TOOL_STARTED.value


def test_panel_clear_resets_count(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel.append_event(EventKind.TOOL_STARTED.value, {"name": "a", "args_hash": "b"})
    panel.append_event(EventKind.TOOL_FINISHED.value, {"name": "a", "duration_ms": 5, "is_error": False})
    assert panel.event_count == 2
    panel.clear()
    assert panel.event_count == 0


def test_panel_emits_latest_event(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    received: list[tuple[str, dict]] = []
    panel.latest_event.connect(lambda k, p: received.append((k, p)))

    panel.append_event(
        EventKind.RAG_SEARCH_FOUND.value,
        {"query": "x", "n_results": 3, "duration_ms": 12.5},
    )
    assert received == [
        (EventKind.RAG_SEARCH_FOUND.value, {"query": "x", "n_results": 3, "duration_ms": 12.5}),
    ]


def test_panel_important_filter_hides_info_log(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel._important_check.setChecked(True)  # noqa: SLF001
    panel.append_event(
        EventKind.LOG.value,
        {"level": "INFO", "message": "hello", "source": "m:f", "timestamp": "00:00:00"},
    )
    assert panel.event_count == 0


def test_panel_important_filter_keeps_warning(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel._important_check.setChecked(True)  # noqa: SLF001
    panel.append_event(
        EventKind.LOG.value,
        {"level": "WARNING", "message": "careful", "source": "m:f", "timestamp": "00:00:00"},
    )
    assert panel.event_count == 1


def test_panel_important_filter_keeps_tool_error(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel._important_check.setChecked(True)  # noqa: SLF001
    panel.append_event(
        EventKind.TOOL_ERROR.value,
        {"name": "read_file", "error_category": "different_path", "message": "no file"},
    )
    assert panel.event_count == 1


# ---------------------------------------------------------------------------
# StatusDashboard — пульсация + tooltip
# ---------------------------------------------------------------------------


def test_dashboard_activity_pulse_on_thinking(qapp) -> None:
    from ui.widgets.status_dashboard import StatusDashboard

    dash = StatusDashboard()
    dash.set_activity_state("thinking")
    # Приватный флаг — проверяем, что пульсация включена (тест на behavior,
    # а не на внутреннее состояние — метод set_pulse не имеет public getter'а).
    assert dash.activity._pulse_active is True  # noqa: SLF001
    dash.set_activity_state("idle")
    assert dash.activity._pulse_active is False  # noqa: SLF001


def test_dashboard_tooltip_shows_last_event(qapp) -> None:
    from ui.widgets.status_dashboard import StatusDashboard

    dash = StatusDashboard()
    dash.set_last_event_tooltip(
        EventKind.TOOL_STARTED.value,
        {"name": "read_file", "args_hash": "abc"},
    )
    assert "read_file" in dash.activity.toolTip()


# ---------------------------------------------------------------------------
# Полный сценарий: EventBus → QtEventBus → LiveEventsPanel + StatusDashboard
# ---------------------------------------------------------------------------


def test_full_pipeline(qapp) -> None:
    from ui.widgets.live_events import LiveEventsPanel, QtEventBus
    from ui.widgets.status_dashboard import StatusDashboard

    panel = LiveEventsPanel()
    dash = StatusDashboard()
    bus = QtEventBus()
    bus.event_emitted.connect(panel.append_event)
    bus.event_emitted.connect(dash.set_last_event_tooltip)

    emit_event(
        EventKind.TOOL_STARTED.value,
        {"name": "search_codebase", "args_hash": "h"},
    )
    qapp.processEvents()

    assert panel.event_count == 1
    assert "search_codebase" in dash.activity.toolTip()
    bus.shutdown()


def test_panel_domain_filter_hides_other_kinds(qapp) -> None:
    """2.5: фильтр домена скрывает события из других доменов."""
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    # Выбираем домен "budget".
    idx = panel._domain_combo.findData("budget")
    panel._domain_combo.setCurrentIndex(idx)

    panel.append_event(
        EventKind.TOOL_STARTED.value,
        {"name": "read_file", "args_hash": "abc"},
    )
    panel.append_event(
        EventKind.BUDGET_WARNING.value,
        {
            "iterations": 8,
            "iterations_limit": 10,
            "tokens": 800,
            "tokens_limit": 1000,
            "time_s": 10.0,
            "time_limit_s": 100.0,
            "status": "warning",
        },
    )

    assert panel.event_count == 1
    assert panel.latest_kind == EventKind.BUDGET_WARNING.value


def test_panel_domain_filter_all_shows_everything(qapp) -> None:
    """2.5: "all" пропускает все домены."""
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel.append_event(
        EventKind.TOOL_STARTED.value,
        {"name": "a", "args_hash": "b"},
    )
    panel.append_event(
        EventKind.BUDGET_WARNING.value,
        {
            "iterations": 1,
            "iterations_limit": 10,
            "tokens": 1,
            "tokens_limit": 1000,
            "time_s": 1.0,
            "time_limit_s": 100.0,
            "status": "warning",
        },
    )
    assert panel.event_count == 2


def test_panel_formats_agent_finished(qapp) -> None:
    """2.5: agent.finished показывает причину и итерации."""
    from ui.widgets.live_events import LiveEventsPanel

    panel = LiveEventsPanel()
    panel.append_event(
        EventKind.AGENT_FINISHED.value,
        {
            "finish_reason": "budget.exceeded",
            "iterations": 42,
            "elapsed_s": 123.4,
            "budget": {"status": "exceeded"},
        },
    )
    assert panel.event_count == 1
    assert panel.latest_kind == EventKind.AGENT_FINISHED.value
