"""Переиспользуемые виджеты OlliDesk (9.x)."""

from ui.widgets.auto_scroll import AutoScrollArea
from ui.widgets.command_completer import CommandCompleter
from ui.widgets.command_palette import CommandPalette
from ui.widgets.live_events import LiveEventsPanel, QtEventBus
from ui.widgets.pipeline_selectors import PipelineSelectors
from ui.widgets.profiles_selector import ProfilesSelector
from ui.widgets.status_dashboard import StatusDashboard

__all__ = [
    "AutoScrollArea",
    "CommandCompleter",
    "CommandPalette",
    "LiveEventsPanel",
    "PipelineSelectors",
    "ProfilesSelector",
    "QtEventBus",
    "StatusDashboard",
]