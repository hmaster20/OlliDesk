"""Permissions Dashboard (9.10).

Диалог `View → Разрешения…`. Четыре вкладки:

1. **По профилям** — матрица профиль × инструмент: `auto`/`ask_first`/
   `excluded` + reason. Источник политики (`security` / `freedom` /
   `profile` / `session` / `config` / `decorator`) — в tooltip.

2. **По capabilities** — какие инструменты используют `fs.read`,
   `fs.write`, `net`, `shell`, `git`, `ui`, `secrets`, `browser`;
   разрешено ли каждое capability в активном профиле.

3. **Последние вызовы** — из `core/audit.py`: timestamp, tool, policy,
   approved, result_hash (8 hex), error, dangerous.

4. **Почему заблокировано** — выбор инструмента → `explain_policy()`
   показывает ВСЕ слои пошагово; видно, какой слой и почему
   заблокировал.

В шапке — активные `session_overrides` (2.7) + кнопка «Сбросить».
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from agents.tool_registry import ToolRegistry
from core.config import AgentMode, AppConfig, ToolPolicy
from core.event_bus import emit_event
from core.events import EventKind
from core.policy import effective_policy, explain_policy
from core.profiles import ProfileConfig

# Цвета для policy.
_COLOR_AUTO = "#98c379"
_COLOR_ASK = "#e5c07b"
_COLOR_EXCL = "#e06c75"


def _policy_color(policy: str) -> QColor:
    if policy == "auto":
        return QColor(_COLOR_AUTO)
    if policy == "ask_first":
        return QColor(_COLOR_ASK)
    if policy == "excluded":
        return QColor(_COLOR_EXCL)
    return QColor("#888888")


def _policy_label(policy: str) -> str:
    """Компактная метка для матрицы."""
    return {
        "auto": "auto",
        "ask_first": "ask",
        "excluded": "✗",
    }.get(policy, policy)


class PermissionsDashboard(QDialog):
    """Единый экран разрешений (9.10)."""

    def __init__(
        self,
        registry: ToolRegistry,
        config: AppConfig,
        *,
        active_profile: ProfileConfig | None = None,
        current_mode: AgentMode | None = None,
        session_overrides: dict[str, ToolPolicy] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._config = config
        self._active_profile = active_profile
        self._current_mode = current_mode or AgentMode.AGENT
        self._session_overrides: dict[str, ToolPolicy] = dict(
            session_overrides or {}
        )

        self.setWindowTitle("Разрешения (Permissions)")
        self.setMinimumSize(1100, 720)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Шапка: активные override + кнопка сброса.
        header_row = QHBoxLayout()
        self._overrides_label = QLabel("")
        self._overrides_label.setStyleSheet(
            "color: #888; font-size: 11px; padding: 2px 0;"
        )
        header_row.addWidget(self._overrides_label, stretch=1)

        self._reset_overrides_btn = QPushButton("✕ Сбросить session overrides")
        self._reset_overrides_btn.setToolTip(
            "Убрать все per-session overrides (2.7). Дальше — снова "
            "действуют профиль и config."
        )
        self._reset_overrides_btn.clicked.connect(self._on_reset_overrides)
        header_row.addWidget(self._reset_overrides_btn)
        layout.addLayout(header_row)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_matrix_tab(), "По профилям")
        self._tabs.addTab(self._build_capabilities_tab(), "По capabilities")
        self._tabs.addTab(self._build_audit_tab(), "Последние вызовы")
        self._tabs.addTab(self._build_explain_tab(), "Почему заблокировано")
        layout.addWidget(self._tabs, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._refresh_overrides_label()

    # ------------------------------------------------------------------
    # Вкладка 1: матрица профиль × инструмент
    # ------------------------------------------------------------------

    def _build_matrix_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Матрица «инструмент × профиль». Ячейка — итоговая политика "
            "(`auto` / `ask` / `✗ excluded`). Tooltip — источник политики "
            "и причинда. Источники: security → secrets → mode → freedom → "
            "profile → session → config → decorator (2.7)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0;")
        layout.addWidget(info)

        profiles = list(self._config.profiles)
        specs = sorted(
            self._registry.get_all_specs(), key=lambda s: s.name
        )

        self._matrix = QTableWidget()
        self._matrix.setColumnCount(2 + len(profiles))
        headers = ["Инструмент", "Capabilities"] + [p.name for p in profiles]
        self._matrix.setHorizontalHeaderLabels(headers)
        self._matrix.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._matrix.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        for i in range(2, 2 + len(profiles)):
            self._matrix.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.Stretch
            )
        self._matrix.setRowCount(len(specs))
        self._matrix.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        for row, spec in enumerate(specs):
            self._matrix.setItem(row, 0, QTableWidgetItem(spec.name))
            caps_text = ", ".join(sorted(spec.capabilities or ["fs.read"]))
            caps_item = QTableWidgetItem(caps_text)
            caps_item.setForeground(QColor("#888"))
            self._matrix.setItem(row, 1, caps_item)

            for col_i, profile in enumerate(profiles):
                eff = effective_policy(
                    spec,
                    profile=profile,
                    security=None,  # security проверяется отдельно
                    config=self._config,
                    session_overrides=self._session_overrides,
                    current_mode=None,  # режимы не учитываем в матрице
                )
                cell = QTableWidgetItem(_policy_label(eff.policy.value))
                cell.setForeground(_policy_color(eff.policy.value))
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )
                cell.setToolTip(
                    f"Профиль: {profile.name}\n"
                    f"Политика: {eff.policy.value}\n"
                    f"Источник: {eff.reason}\n"
                    f"Allowed: {eff.allowed}"
                )
                self._matrix.setItem(row, 2 + col_i, cell)

        layout.addWidget(self._matrix, stretch=1)
        return tab

    # ------------------------------------------------------------------
    # Вкладка 2: по capabilities
    # ------------------------------------------------------------------

    def _build_capabilities_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Capabilities: какие инструменты входят в каждое capability и "
            "как оно разрешено в активном профиле. Матрица `capability × "
            "инструмент` — какие инструменты реально используют каждый "
            "capability."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0;")
        layout.addWidget(info)

        specs = self._registry.get_all_specs()
        # Группируем: capability → [имена инструментов].
        groups: dict[str, list[str]] = {}
        for spec in specs:
            for cap in spec.capabilities or {"fs.read"}:
                groups.setdefault(cap, []).append(spec.name)

        active_profile = self._active_profile
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(
            ["Capability", "Инструменты", "Политика (текущий профиль)"]
        )
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setRowCount(len(groups))
        for row, (cap, names) in enumerate(sorted(groups.items())):
            table.setItem(row, 0, QTableWidgetItem(cap))
            table.setItem(
                row, 1, QTableWidgetItem(", ".join(sorted(names)))
            )
            # Политика для capability в активном профиле.
            policy = "—"
            if active_profile is not None:
                from core.policy import _FREEDOM_MATRIX

                level = getattr(active_profile, "freedom_level", "")
                if level in _FREEDOM_MATRIX:
                    policy = _FREEDOM_MATRIX[level].get(cap, "—")
            cell = QTableWidgetItem(policy)
            cell.setForeground(_policy_color(policy))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if cap == "secrets":
                cell.setToolTip(
                    "secrets всегда excluded, независимо от профиля."
                )
            table.setItem(row, 2, cell)

        layout.addWidget(table, stretch=1)
        return tab

    # ------------------------------------------------------------------
    # Вкладка 3: последние вызовы (audit log)
    # ------------------------------------------------------------------

    def _build_audit_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Последние вызовы инструментов из `~/.ollidesk/audit.db` "
            "(0.1.1). Хеши аргументов и результата (не сами данные), "
            "политика, approved, dangerous."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0;")
        layout.addWidget(info)

        self._audit_table = QTableWidget()
        self._audit_table.setColumnCount(7)
        self._audit_table.setHorizontalHeaderLabels(
            ["Время", "Инструмент", "Политика", "Approved", "Result hash", "Error", "Dangerous"]
        )
        self._audit_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._audit_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._audit_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._audit_table, stretch=1)

        refresh_btn = QPushButton("🔄 Обновить")
        refresh_btn.clicked.connect(self._refresh_audit)
        layout.addWidget(refresh_btn)

        self._refresh_audit()
        return tab

    def _refresh_audit(self) -> None:
        try:
            from core.audit import get_audit_log

            entries = get_audit_log().get_recent(limit=200)
        except Exception:
            logger.exception("audit refresh failed")
            entries = []

        self._audit_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._audit_table.setItem(
                row, 0, QTableWidgetItem(e.timestamp.replace("T", " "))
            )
            self._audit_table.setItem(row, 1, QTableWidgetItem(e.tool))
            self._audit_table.setItem(row, 2, QTableWidgetItem(e.policy))
            self._audit_table.setItem(
                row, 3, QTableWidgetItem("✓" if e.approved else "✗")
            )
            self._audit_table.setItem(
                row, 4, QTableWidgetItem(e.result_hash[:8] if e.result_hash else "")
            )
            self._audit_table.setItem(row, 5, QTableWidgetItem(e.error))
            self._audit_table.setItem(
                row, 6, QTableWidgetItem("⚠️" if e.dangerous else "")
            )

    # ------------------------------------------------------------------
    # Вкладка 4: почему заблокировано
    # ------------------------------------------------------------------

    def _build_explain_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel(
            "Выберите инструмент → пошаговое объяснение: какой слой "
            "(security / secrets / mode / freedom / profile / session / "
            "config / decorator) принял финальное решение."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; padding: 4px 0;")
        layout.addWidget(info)

        self._explain_combo = QComboBox()
        for spec in sorted(
            self._registry.get_all_specs(), key=lambda s: s.name
        ):
            self._explain_combo.addItem(spec.name, spec.name)
        self._explain_combo.currentIndexChanged.connect(
            self._on_explain_changed
        )
        layout.addWidget(self._explain_combo)

        self._explain_view = QTextBrowser()
        self._explain_view.setOpenExternalLinks(False)
        self._explain_view.setStyleSheet(
            "QTextBrowser { background: #1e1e2e; color: #d4d4d4;"
            " border: 1px solid #3a3a48; border-radius: 4px; padding: 8px; }"
        )
        layout.addWidget(self._explain_view, stretch=1)

        if self._explain_combo.count() > 0:
            self._on_explain_changed(0)
        return tab

    @Slot(int)
    def _on_explain_changed(self, _index: int) -> None:
        name = self._explain_combo.currentData()
        if not isinstance(name, str) or not name:
            return
        spec = self._registry.try_get_spec(name)
        if spec is None:
            self._explain_view.setHtml(
                '<p style="color:#e06c75;">Инструмент не найден</p>'
            )
            return

        effective, steps = explain_policy(
            spec,
            profile=self._active_profile,
            config=self._config,
            session_overrides=self._session_overrides,
            current_mode=self._current_mode,
        )

        html = [
            f"<h3>{_escape(spec.name)}</h3>",
            f"<p><b>Capabilities:</b> "
            f"{', '.join(sorted(spec.capabilities or ['fs.read']))}<br>"
            f"<b>Decorator policy:</b> {spec.policy.value}<br>"
            f"<b>Текущий режим:</b> {self._current_mode.value}<br>"
            f"<b>Активный профиль:</b> "
            f"{self._active_profile.name if self._active_profile else '—'} "
            f"({getattr(self._active_profile, 'freedom_level', '—') if self._active_profile else '—'})"
            f"</p>",
            "<hr>",
            "<h4>Пошаговое применение</h4>",
            "<ol>",
        ]
        for _i, step in enumerate(steps, 1):
            color = (
                _COLOR_EXCL
                if step.policy == "excluded"
                else _COLOR_ASK
                if step.policy == "ask_first"
                else _COLOR_AUTO
                if step.policy == "auto"
                else "#888"
            )
            marker = "🛑" if step.blocking else "→"
            applied_mark = "✓" if step.applied else "—"
            html.append(
                f"<li>{marker} <b>{step.layer}</b> "
                f"[{applied_mark}] "
                f'<span style="color:{color};">{step.policy}</span><br>'
                f'<span style="color:#888; font-size:11px;">'
                f"{_escape(step.reason)}</span></li>"
            )
        html.append("</ol>")
        html.append("<hr>")
        color = (
            _COLOR_EXCL
            if effective.policy.value == "excluded"
            else _COLOR_ASK
            if effective.policy.value == "ask_first"
            else _COLOR_AUTO
        )
        html.append(
            f"<h4>Итог</h4>"
            f'<p style="color:{color}; font-size:14px;">'
            f"<b>Policy:</b> {effective.policy.value}<br>"
            f"<b>Allowed:</b> {'да' if effective.allowed else 'нет'}<br>"
            f"<b>Reason:</b> {_escape(effective.reason)}</p>"
        )
        self._explain_view.setHtml("".join(html))

    # ------------------------------------------------------------------
    # Session overrides
    # ------------------------------------------------------------------

    def _refresh_overrides_label(self) -> None:
        if not self._session_overrides:
            self._overrides_label.setText(
                "Session overrides: — (нет)"
            )
            self._reset_overrides_btn.setEnabled(False)
            return
        pairs = ", ".join(
            f"{k}={v.value}" for k, v in self._session_overrides.items()
        )
        self._overrides_label.setText(
            f"Session overrides: {pairs}"
        )
        self._reset_overrides_btn.setEnabled(True)

    @Slot()
    def _on_reset_overrides(self) -> None:
        """9.10: сбрасывает session-overrides и сообщает в EventBus."""
        if not self._session_overrides:
            return
        cleared = list(self._session_overrides.keys())
        self._session_overrides.clear()
        # Применяем к конфигу (если передан).
        import contextlib
        with contextlib.suppress(Exception):
            self._config.session_overrides.clear()
        emit_event(
            EventKind.PERMISSIONS_RESET.value,
            {"tools": cleared},
        )
        logger.info(f"permissions: session overrides сброшены ({cleared})")
        self._refresh_overrides_label()
        # Перерисовать матрицу и explain.
        self._rebuild_matrix()
        self._on_explain_changed(self._explain_combo.currentIndex())

    def _rebuild_matrix(self) -> None:
        """Пересобирает матрицу после сброса overrides."""
        # Просто пересоздаём вкладку через смену индекса.
        try:
            idx = self._tabs.currentIndex()
            self._tabs.removeTab(0)
            self._tabs.insertTab(0, self._build_matrix_tab(), "По профилям")
            self._tabs.setCurrentIndex(idx)
        except Exception:
            logger.debug("matrix rebuild failed", exc_info=True)


# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


__all__ = ["PermissionsDashboard"]