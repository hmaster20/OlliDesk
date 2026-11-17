"""Единая приборная панель для статус-бара (9.7).

Заменяет `ollama_status_label` + `backend_status_label` +
`pipeline_status_label` из MainWindow. Все статусы — в одном месте,
с tooltip'ами. Элементы:

  * `backend` — SLM / Ollama / Резерв (кликабельно для переключения);
  * `planner` — id модели + device;
  * `analyst` — id модели + device;
  * `pipeline` — ⏳/⚡/⚠️/💤;
  * `tokens_per_sec` — из `last_stats` после генерации;
  * `rag` — количество чанков / статус;
  * `tools` — активность инструментов текущего запроса.

Встраивается в `MainWindow._setup_status_bar` через
`self.status_bar.addPermanentWidget(self.dashboard)`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget, QMenu

# Цвета семантических индикаторов (в стиле «приборной панели»).
_C_OK = "#98c379"
_C_WARN = "#e5c07b"
_C_ERR = "#e06c75"
_C_OFF = "#7a7a7a"
_C_INFO = "#d4a373"


class _PillLabel(QLabel):
    """Кликабельный статус-индикатор в стиле «pill».

    2.5: `set_pulse(True)` включает мигание точки (QTimer 500 мс,
    toggle между двумя оттенками) — визуальный признак «программа
    работает». Tooltip можно переопределить независимо от текста.
    """

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        # [Объединяем производительность]
        self.perf = _PillLabel("perf")
        self.perf.setToolTip("Производительность: tok/s и prefix caching")
        layout.addWidget(self.perf)

        self.rag = _PillLabel("RAG")
        self.rag.setToolTip("Индекс RAG: количество чанков / статус")
        layout.addWidget(self.rag)

        # ... tools, budget, coder ...

        # [Объединяем сетевые возможности]
        self.net = _PillLabel("net")
        self.net.setToolTip("Сетевые возможности: браузер и поиск")
        layout.addWidget(self.net)

        # ... git_branch, memory, mcp, freedom, inbox ...

        # Словарь для управления видимостью
        self._pills = {
            "activity": self.activity,
            "backend": self.backend,
            "pipeline": self.pipeline,
            "perf": self.perf,
            "rag": self.rag,
            "tools": self.tools,
            "budget": self.budget,
            "coder": self.coder,
            "net": self.net,
            "git_branch": self.git_branch,
            "memory": self.memory,
            "mcp": self.mcp,
            "freedom": self.freedom,
            "inbox": self.inbox,
        }

        # Включаем контекстное меню на пустой области
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Инициализация начальных значений (заменяем старые set_tok_per_sec и т.д.)
        self.set_perf(0.0, 0, 0)
        self.set_net("isolated", False, "", False)

    def set_state(self, text: str, color: str, tooltip: str = "") -> None:
        """Обновляет текст и цвет индикатора (цвет — как маркер)."""
        self._current_text = text
        self._current_color = color
        self._apply()

    def set_tooltip(self, tooltip: str) -> None:
        """Только tooltip (текст/цвет не трогаются)."""
        if tooltip:
            self.setToolTip(tooltip)

    def apply_visibility_settings(self, hidden_pills: list[str]) -> None:
        """Скрывает/показывает плитки на основе конфига."""
        for name, pill in self._pills.items():
            pill.setVisible(name not in hidden_pills)

    def set_pulse(self, active: bool) -> None:
        """Включает/выключает мигание точки."""
        if active == self._pulse_active:
            return
        self._pulse_active = active
        if active:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse_on = False
            self._apply()

    def _on_pulse_tick(self) -> None:
        self._pulse_on = not self._pulse_on
        self._apply()

    def _apply(self) -> None:
        color = self._current_color
        if self._pulse_active and self._pulse_on:
            color = "#f0d494"  # осветлённый цвет — «вспышка»
        self.setText(f'<span style="color:{color};">●</span> {self._current_text}')
        self.setTextFormat(Qt.TextFormat.RichText)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatusDashboard(QWidget):
    """Приборная панель статусов: backend, planner, analyst, pipeline, tok/s, RAG, tools."""

    backend_toggle_requested = Signal()
    slm_models_requested = Signal()
    pill_visibility_changed = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.activity = _PillLabel("💤 idle")
        self.activity.setToolTip("Активность: ожидание")
        layout.addWidget(self.activity)

        self.backend = _PillLabel("backend")
        self.backend.setToolTip("Активный LLM-бэкенд")
        self.backend.clicked.connect(self.backend_toggle_requested.emit)
        layout.addWidget(self.backend)

        self.pipeline = _PillLabel("pipeline")
        self.pipeline.setToolTip("Двухмодельный пайплайн (planner + analyst)")
        self.pipeline.clicked.connect(self.slm_models_requested.emit)
        layout.addWidget(self.pipeline)

        self.tok_per_sec = _PillLabel("tok/s")
        self.tok_per_sec.setToolTip("Скорость генерации (обновляется после каждого ответа)")
        layout.addWidget(self.tok_per_sec)

        self.rag = _PillLabel("RAG")
        self.rag.setToolTip("Индекс RAG: количество чанков / статус")
        layout.addWidget(self.rag)

        self.tools = _PillLabel("tools")
        self.tools.setToolTip("Активность инструментов текущего запроса")
        layout.addWidget(self.tools)

        self.budget = _PillLabel("budget")
        self.budget.setToolTip("Мягкий бюджет агента (итерации · токены · время)")
        layout.addWidget(self.budget)

        self.cache = _PillLabel("cache")
        self.cache.setToolTip("Prefix caching: reads/writes в KV-кэше")
        layout.addWidget(self.cache)

        self.set_backend("slm", "SLM", "—")
        self.set_pipeline("💤", "не активирован")
        self.set_rag(0, "не проиндексирован")
        self.set_tools_active(False)
        self.coder = _PillLabel("coder")
        self.coder.setToolTip("Coder-модель (выбирается профилем)")
        layout.addWidget(self.coder)

        self.browser = _PillLabel("browser")
        self.browser.setToolTip("Режим browser_control: isolated / extension / full / off")
        layout.addWidget(self.browser)

        self.search = _PillLabel("search")
        self.search.setToolTip("Цепочка поиска: первый доступный провайдер")
        layout.addWidget(self.search)

        self.git_branch = _PillLabel("git")
        self.git_branch.setToolTip("Текущая git-ветка и hash")
        layout.addWidget(self.git_branch)

        self.memory = _PillLabel("mem")
        self.memory.setToolTip("Память: рефлексии по завершённым задачам")
        layout.addWidget(self.memory)

        # [7] MCP-серверы.
        self.mcp = _PillLabel("mcp")
        self.mcp.setToolTip("MCP: подключённые серверы")
        layout.addWidget(self.mcp)

        self.freedom = _PillLabel("profile")
        self.freedom.setToolTip("freedom_level активного профиля")
        layout.addWidget(self.freedom)

        self.inbox = _PillLabel("inbox")
        self.inbox.setToolTip("Durable inbox: запросы в очереди")
        layout.addWidget(self.inbox)

        self.set_budget(None)
        self.set_coder("⚡", "coder", "—")
        self.set_git_branch("", "")  # [9.4]
        self.set_memory(0, [])  # [4.3]
        self.set_mcp(0, [])  # [7]
        self.set_freedom("development")  # [9.12]
        self.set_inbox(0)

    _ACTIVITY_LABELS: dict[str, tuple[str, str]] = {
        "idle": ("💤 idle", _C_OFF),
        "thinking": ("🤔 думает", _C_WARN),
        "tool": ("🔧 tool", _C_INFO),
        "streaming": ("✍️ стрим", _C_OK),
        "error": ("❌ ошибка", _C_ERR),
        "cancelled": ("⏹ стоп", _C_OFF),
    }

    def set_perf(self, tps: float, cache_reads: int, cache_writes: int) -> None:
        """Обновляет объединенную плитку производительности."""
        tps_str = f"{tps:.1f} t/s" if tps > 0 else "— t/s"
        cache_str = ""
        if cache_reads > 0 or cache_writes > 0:
            total = max(1, cache_reads + cache_writes)
            hit_pct = int(100 * cache_reads / total)
            cache_str = f" (cache {hit_pct}%)"
            tooltip = f"Скорость: {tps_str}\nPrefix caching: {cache_reads} reads / {cache_writes} writes ({hit_pct}% hit rate)"
        else:
            tooltip = f"Скорость: {tps_str}\nPrefix caching не активен"

        color = _C_OK if tps > 0 else _C_OFF
        self.perf.set_state(f"⚡ {tps_str}{cache_str}", color, tooltip)

    def set_net(self, browser_mode: str, browser_avail: bool, provider: str, search_avail: bool) -> None:
        """Обновляет объединенную сетевую плитку (browser + search)."""
        parts = []
        tooltips = []

        # Браузер
        if browser_avail:
            b_icon = {"isolated": "🔒", "extension": "🔗", "full": "⚠️"}.get(browser_mode, "🌐")
            parts.append(f"{b_icon}{browser_mode}")
            tooltips.append(f"Браузер: {browser_mode} (доступен)")
        else:
            tooltips.append("Браузер: отключен (нет зависимостей)")

        # Поиск
        if search_avail and provider:
            parts.append(f"🔍{provider}")
            tooltips.append(f"Поиск: {provider}")
        else:
            tooltips.append("Поиск: не настроен")

        text = " | ".join(parts) if parts else "— net"
        color = _C_OK if (browser_avail or search_avail) else _C_OFF
        self.net.set_state(text, color, "\n".join(tooltips))

    @Slot(object)
    def _show_context_menu(self, pos) -> None:
        """Показывает меню скрытия/отображения плиток."""
        menu = QMenu(self)

        # Группируем для красоты
        groups = {
            "Основное": ["activity", "backend", "pipeline", "freedom"],
            "Модели и производительность": ["perf", "coder", "rag", "tools", "budget", "cache"],
            "Сеть и среда": ["net", "git_branch", "mcp", "memory", "inbox"],
        }

        labels = {
            "activity": "Активность", "backend": "Бэкенд LLM", "pipeline": "Пайплайн",
            "freedom": "Уровень свободы", "perf": "Скорость (tok/s)",
            "coder": "Coder-модель", "rag": "RAG-индекс", "tools": "Инструменты",
            "budget": "Бюджет", "net": "Сеть (браузер/поиск)", "git_branch": "Git-ветка",
            "mcp": "MCP-серверы", "memory": "Память", "inbox": "Очередь запросов"
        }

        for g_name, keys in groups.items():
            menu.addSection(g_name)
            for k in keys:
                if k not in self._pills:
                    continue
                act = menu.addAction(labels.get(k, k))
                act.setCheckable(True)
                act.setChecked(self._pills[k].isVisible())
                # По клику эмитим сигнал наверх (в MainWindow), чтобы он сохранил конфиг
                act.triggered.connect(lambda checked, p=k: self.pill_visibility_changed.emit(p, checked))

        menu.exec(self.mapToGlobal(pos))

    def apply_visibility_settings(self, hidden_pills: list[str]) -> None:
        for name, pill in self._pills.items():
            pill.setVisible(name not in hidden_pills)

    def set_coder(self, icon: str, label: str, model_name: str) -> None:
        """Показывает coder-модель.

        Args:
            icon: ``"⚡"`` (coder) | ``"⚠️"`` (fallback / нет модели).
            label: ``"coder"`` | ``"coder (fallback)"`` | …
            model_name: Человекочитаемое имя модели.
        """
        color = _C_OK if icon == "⚡" else _C_WARN
        text = f"{icon} {label}"
        tooltip = f"Модель: {model_name}"
        if icon != "⚡":
            tooltip += "\nСкачайте coder-модель через «View → Локальные модели»."
        self.coder.set_state(text, color, tooltip)

    def set_activity_state(self, state: str) -> None:
        """Обновляет индикатор активности (2.5)."""
        label, color = self._ACTIVITY_LABELS.get(state, ("●", _C_OFF))
        self.activity.set_state(label, color)
        self.activity.set_pulse(state in ("thinking", "tool", "streaming"))
        if state == "idle":
            self.activity.set_tooltip("Активность: ожидание")
        elif state == "cancelled":
            self.activity.set_tooltip("Остановлено пользователем")

    def set_last_event_tooltip(self, kind: str, payload: dict) -> None:
        """Tooltip индикатора активности — последнее событие (2.5)."""
        text = f"{kind}"
        msg = payload.get("message")
        if isinstance(msg, str) and msg:
            text += f": {msg[:120]}"
        else:
            for key in ("query", "name", "model_id", "model", "error"):
                if payload.get(key):
                    text += f": {str(payload[key])[:120]}"
                    break
        self.activity.set_tooltip(text)

    def set_backend(self, kind: str, display: str, model: str) -> None:
        """kind ∈ {'slm','ollama','rule','none'}."""
        color = {
            "slm": _C_OK,
            "ollama": _C_INFO,
            "rule": _C_WARN,
            "none": _C_ERR,
        }.get(kind, _C_OFF)
        tooltip = f"Бэкенд: {kind}\nМодель: {model}\nКлик — переключить"
        self.backend.set_state(display, color, tooltip)

    def set_pipeline(self, icon: str, description: str, tooltip: str = "") -> None:
        color = _C_OK if icon == "⚡" else _C_WARN if icon in ("⚠️", "⏳") else _C_OFF
        self.pipeline.set_state(f"{icon} {description}", color, tooltip or description)

    def set_pipeline_loading(self, message: str) -> None:
        self.pipeline.set_state(f"⏳ {message}", _C_WARN, message)

    def set_rag(self, chunks: int, status: str) -> None:
        if chunks > 0:
            color = _C_OK
            text = f"RAG: {chunks} чанков"
        elif status.startswith("индекс"):
            color = _C_WARN
            text = f"RAG: {status}"
        else:
            color = _C_OFF
            text = "RAG: —"
        self.rag.set_state(text, color, f"RAG-индекс: {status}, чанков: {chunks}")

    def set_tools_active(self, active: bool, count: int = 0) -> None:
        if active:
            self.tools.set_state(f"🔧 tools ({count})", _C_OK, "Инструменты активны")
        else:
            self.tools.set_state("🔧 tools", _C_OFF, "Инструменты не используются")

    def set_budget(self, snapshot: dict | None) -> None:
        """Обновляет индикатор бюджета.
        Args:
            snapshot: ``SoftBudget.snapshot()`` или ``None`` (сброс).
                Ключи: ``iterations``, ``iterations_limit``, ``tokens``,
                ``tokens_limit``, ``time_s``, ``time_limit_s``, ``status``.
        """
        if snapshot is None:
            self.budget.set_state("— budget", _C_OFF, "Бюджет не активен")
            return

        status = str(snapshot.get("status", "ok"))
        iterations = int(snapshot.get("iterations", 0))
        iterations_limit = int(snapshot.get("iterations_limit", 0))
        tokens = int(snapshot.get("tokens", 0))
        time_s = float(snapshot.get("time_s", 0.0))

        color = _C_OK if status == "ok" else _C_WARN if status == "warning" else _C_ERR
        icon = "" if status == "ok" else "⚠️ " if status == "warning" else "⛔ "
        text = f"{icon}{iterations}/{iterations_limit} ит."
        tooltip = (
            f"Итерации: {iterations}/{iterations_limit}\n"
            f"Токены: {tokens}/{int(snapshot.get('tokens_limit', 0))}\n"
            f"Время: {time_s:.0f}с/{int(snapshot.get('time_limit_s', 0))}с"
        )
        self.budget.set_state(text, color, tooltip)

    def set_finish_reason(self, reason: str | None) -> None:
        """Показывает причину остановки последнего прогона.

        Args:
            reason: ``"stop"`` | ``"length"`` | ``"budget.exceeded"`` |
                ``"user_cancel"`` | ``None``.
        """
        if not reason:
            return
        labels = {
            "stop": ("✅ завершено", _C_OK),
            "length": ("✂️ обрезано (max_tokens)", _C_WARN),
            "tool_calls": ("🔧 вызовы инструментов", _C_INFO),
            "budget.exceeded": ("⛔ бюджет исчерпан", _C_ERR),
            "user_cancel": ("⏹ отменено пользователем", _C_OFF),
        }
        text, color = labels.get(reason, (reason, _C_OFF))
        self.budget.set_state(text, color, f"finish_reason={reason}")

    def set_memory(self, count: int, recent: list[str] | None = None) -> None:
        """Показывает количество рефлексий в памяти.

        Args:
            count: Сколько записей в `MemoryService`.
            recent: Последние N summary (для tooltip, до 3-х).
        """
        if count <= 0:
            self.memory.set_state("— mem", _C_OFF, "Память пуста")
            return
        text = f"🧠 mem: {count}"
        tooltip = f"Рефлексий в памяти: {count}"
        if recent:
            tooltip += "\n\nПоследние:\n" + "\n".join(f"  • {r[:80]}" for r in recent[:3])
        self.memory.set_state(text, _C_INFO, tooltip)

    def set_mcp(self, connected: int, servers: list[str] | None = None) -> None:
        """Показывает количество подключённых MCP-серверов.
        Args:
            connected: Сколько серверов ответили на ``initialize``.
            servers: Имена серверов (для tooltip).
        """
        if connected <= 0:
            self.mcp.set_state("— mcp", _C_OFF, "MCP не подключён")
            return
        text = f"🔌 mcp: {connected}"
        tooltip = f"Подключено MCP-серверов: {connected}"
        if servers:
            tooltip += "\n\n" + "\n".join(f"  • {s}" for s in servers[:5])
            if len(servers) > 5:
                tooltip += f"\n  … ещё {len(servers) - 5}"
        self.mcp.set_state(text, _C_OK, tooltip)

    def set_freedom(self, level: str) -> None:
        """Показывает freedom_level активного профиля.
        Args:
            level: ``"safe"`` | ``"research"`` | ``"development"`` | ``"open"``.
        """
        icons = {
            "safe": "🔒",
            "research": "🔍",
            "development": "🛠",
            "open": "🌐",
        }
        colors = {
            "safe": _C_OFF,
            "research": _C_INFO,
            "development": _C_OK,
            "open": _C_ERR,  # «open» — максимальная свобода, красный
        }
        short = {
            "safe": "safe",
            "research": "research",
            "development": "dev",
            "open": "OPEN",
        }
        tooltips = {
            "safe": "safe — только чтение; shell/git/browser запрещены",
            "research": "research — чтение + web; shell/git/browser запрещены",
            "development": "development — запись auto; shell/git ask_first",
            "open": "open — всё auto (shell ask_first, secrets никогда)",
        }
        if level not in icons:
            self.freedom.set_state("— profile", _C_OFF, "freedom_level не задан")
            return
        self.freedom.set_state(
            f"{icons[level]} {short[level]}",
            colors[level],
            tooltips[level],
        )

    def set_tools_from_label(self, label: str) -> None:
        """Совместимость с текущим `tool_status_label.setText(...)`."""
        self.tools.set_state(label, _C_OK if "актив" in label.lower() else _C_WARN, label)

    def set_inbox(self, pending: int) -> None:
        """Показывает счётчик pending-запросов.

        Args:
            pending: Сколько запросов в состоянии ``pending``.
        """
        if pending <= 0:
            self.inbox.set_state("— inbox", _C_OFF, "Durable inbox пуст")
            return
        color = _C_WARN if pending >= 5 else _C_INFO
        text = f"📥 inbox: {pending}"
        tooltip = (
            f"{pending} незавершённых запросов.\n"
            f"«running» при упавшем процессе автоматически "
            f"переходит в «pending» при старте."
        )
        self.inbox.set_state(text, color, tooltip)

    def set_git_branch(self, branch: str, commit_hash: str) -> None:
        """Показывает текущую git-ветку и hash.

        Args:
            branch: Имя ветки (пусто — не git-репозиторий).
            commit_hash: Короткий hash HEAD.
        """
        if not branch:
            self.git_branch.set_state("— git", _C_OFF, "Проект не под git")
            return
        text = f"🌿 {branch}"
        if commit_hash:
            text += f"@{commit_hash}"
        tooltip = f"Ветка: {branch}"
        if commit_hash:
            tooltip += f"\nhash: {commit_hash}"
        self.git_branch.set_state(text, _C_INFO, tooltip)


__all__ = ["StatusDashboard"]
