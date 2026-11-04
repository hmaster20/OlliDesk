"""Виджет сообщения чата: композитный пузырь из типизированных частей.

4.8.B: `ChatMessageItem` больше не единый `QTextBrowser`, а набор виджетов:
* `ReasoningWidget` — сворачиваемые размышления модели (`ReasoningPart`);
* `TextWidget` — markdown-текст ответа (`TextPart`);
* `ToolCallCard` — карточка вызова инструмента (`ToolCallPart`).

Старый API сохранён:
* `ChatMessageItem(role, content, thinking=..., mode_name=...)` — конструктор;
* `update_content(str)` / `update_thinking(str)` — заменяют текст/reasoning;
* `.content` / `.thinking` — атрибуты (для копирования/отладки).

Новый API — стриминг:
* `append_text(token)` / `append_reasoning(token)` — доинкрементально;
* `append_tool_call(name, args_json)` — встроить карточку в пузырь;
* `finalize()` — вызывается на finish (сворачивает reasoning).
"""

from __future__ import annotations

import contextlib
import html as html_mod
import json
import re
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.theme import ColorTokens, get_colors


def _theme_colors(dark: bool) -> ColorTokens:
    """Токены текущей темы (единственная точка правды для стилей чата)."""
    return get_colors("dark" if dark else "light")


# [9.5.1] Единый стиль action-кнопок в пузыре и карточках.
# Убраны 6 разных inline-стилей (Copy, «Показать полностью», «Открыть»,
# «Откатить», «Показать diff», «Вернуть к чату»).
_ACTION_BTN_CSS_TEMPLATE = """
QPushButton {{
    background: transparent;
    border: 1px solid {border};
    border-radius: 10px;
    color: {text_muted};
    font-size: 11px;
    padding: 2px 10px;
    margin: 2px 0;
}}
QPushButton:hover {{
    background: {surface_hover};
    color: {text};
}}
QPushButton:pressed {{
    background: {surface_pressed};
}}
QPushButton:disabled {{
    color: {text_disabled};
    border-color: {border};
}}
"""


def _action_btn_css(dark: bool) -> str:
    c = _theme_colors(dark)
    return _ACTION_BTN_CSS_TEMPLATE.format(
        border=c.border,
        text=c.text,
        text_muted=c.text_muted,
        surface_hover=c.surface_hover,
        surface_pressed=c.surface_pressed,
        text_disabled=c.text_disabled,
    )

# ============================================================================
# Markdown → HTML (без изменений; используется research_passport.py)
# ============================================================================

_CODE_FENCE_RE = re.compile(
    r"```[ \t]*(?P<lang>[A-Za-z0-9_+#.-]*)[ \t]*\n?(?P<code>.*?)```",
    re.DOTALL,
)
_CODE_PLACEHOLDER_RE = re.compile(r"^\x00CB(\d+)\x00$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_UL_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)[-*+]\s+(?P<text>.*)$")
_OL_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)\d+[.)]\s+(?P<text>.*)$")
_TASK_ITEM_RE = re.compile(r"^\[(?P<state>[ xX])\]\s+(?P<text>.*)$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _render_code_block(code: str, lang: str, *, dark: bool = True) -> str:
    """Подсвечивает код в блоке (pygments) или экранирует как есть.

    Палитра pygments и header — по текущей теме. На тёмной — `monokai`,
    на светлой — `friendly`. Заголовок-«шапка» с языком показывается
    только если `lang` известен.
    """
    code = code.rstrip("\n")
    highlighted: str
    c = _theme_colors(dark)
    try:
        from pygments import highlight as _highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer

        pyg_style = "monokai" if dark else "friendly"
        formatter = HtmlFormatter(
            style=pyg_style, nowrap=True, noclasses=True
        )
        lexer = (
            get_lexer_by_name(lang, stripall=True)
            if lang
            else guess_lexer(code)
        )
        highlighted = _highlight(code, lexer, formatter).rstrip("\n")
    except Exception:
        highlighted = html_mod.escape(code)

    header = ""
    if lang:
        header = (
            f'<div style="background:{c.code_header_bg}; padding:3px 12px;'
            " font-family:'Cascadia Mono','Consolas','Courier New',monospace;"
            f' font-size:11px; color:{c.code_header_text};'
            f' border-bottom:1px solid {c.border};">'
            f"{html_mod.escape(lang)}</div>"
        )

    return (
        f'<div style="background:{c.code_bg}; border:1px solid {c.border};'
        ' border-radius:8px; margin:8px 0; overflow:hidden;">'
        + header
        + '<pre style="margin:0; padding:8px 12px;'
        " font-family:'Cascadia Mono','Consolas','Courier New',monospace;"
        f' font-size:13px; color:{c.code_text};"><code>'
        + highlighted
        + "</code></pre></div>"
    )


def _inline(text: str) -> str:
    """Инлайн-разметка: код, ссылки, жирный/курсив/зачёркнутый, экранирование."""
    inline_codes: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = html_mod.escape(text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        r'<a href="\2" style="color:#4da3ff; text-decoration:none;">\1</a>',
        text,
    )
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(
        r"==(.+?)==",
        r'<span style="background:#ffd54f; color:#1a1a1a; padding:0 3px;'
        r' border-radius:3px;">\1</span>',
        text,
    )

    def _restore_code(m: re.Match[str]) -> str:
        code = html_mod.escape(inline_codes[int(m.group(1))])
        return (
            '<code style="background:#33333d; color:#e8c07d; padding:1px 5px;'
            " border-radius:3px; font-family:'Cascadia Mono','Consolas',monospace;"
            f' font-size:13px;">{code}</code>'
        )

    return re.sub(r"\x00IC(\d+)\x00", _restore_code, text)


def _indent_width(raw: str) -> int:
    expanded = raw.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _collect_list_items(lines: list[str], start: int) -> tuple[list[tuple[int, bool, str]], int]:
    items: list[tuple[int, bool, str]] = []
    i = start
    n = len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip():
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n and (_OL_ITEM_RE.match(lines[j]) or _UL_ITEM_RE.match(lines[j])):
                i = j
                continue
            break
        m = _OL_ITEM_RE.match(raw)
        if m:
            items.append((_indent_width(m.group("indent")), True, m.group("text").strip()))
            i += 1
            continue
        m = _UL_ITEM_RE.match(raw)
        if m:
            items.append((_indent_width(m.group("indent")), False, m.group("text").strip()))
            i += 1
            continue
        break
    return items, i


def _render_list_frame(frame: dict) -> str:
    tag = "ol" if frame["ordered"] else "ul"
    parts = [f'<{tag} style="margin:6px 0; padding-left:22px;">']
    for text, children in frame["items"]:
        task = _TASK_ITEM_RE.match(text)
        if task:
            box = "☑" if task.group("state").lower() == "x" else "☐"
            inner = f"{box} {_inline(task.group('text'))}"
        else:
            inner = _inline(text)
        for child in children:
            inner += _render_list_frame(child)
        parts.append(f'<li style="margin:4px 0;">{inner}</li>')
    parts.append(f"</{tag}>")
    return "".join(parts)


def _render_list_tree(items: list[tuple[int, bool, str]]) -> str:
    root: list[dict] = []
    stack: list[dict] = []

    def _attach(frame: dict) -> None:
        if stack:
            parent = stack[-1]
            if parent["items"]:
                parent["items"][-1][1].append(frame)
            else:
                parent["items"].append(["", [frame]])
        else:
            root.append(frame)

    for indent, ordered, text in items:
        while stack and indent < stack[-1]["indent"]:
            stack.pop()
        if not stack:
            frame = {"indent": indent, "ordered": ordered, "items": []}
            stack.append(frame)
            root.append(frame)
        elif indent > stack[-1]["indent"]:
            frame = {"indent": indent, "ordered": ordered, "items": []}
            parent = stack[-1]
            if parent["items"]:
                parent["items"][-1][1].append(frame)
            else:
                parent["items"].append(["", [frame]])
            stack.append(frame)
        elif ordered != stack[-1]["ordered"]:
            stack.pop()
            frame = {"indent": indent, "ordered": ordered, "items": []}
            _attach(frame)
            stack.append(frame)
        stack[-1]["items"].append([text, []])

    return "".join(_render_list_frame(frame) for frame in root)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(cell) for cell in cells)


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1] and _is_table_separator(lines[i + 1])
    )


def _render_table(header: list[str], rows: list[list[str]], *, dark: bool = True) -> str:
    """[9.6.6] Рендерит таблицу с учётом темы.

    Тёмная: заголовок #2f2f3c / текст #e0e0e0 (monokai-стиль).
    Светлая: заголовок #e8e8e8 / текст #1e1e1e.
    """
    if dark:
        head_bg = "#2f2f3c"
        head_color = "#e0e0e0"
        border_color = "#4a4a55"
        cell_bg = "transparent"
        cell_color = "inherit"
    else:
        head_bg = "#e8e8e8"
        head_color = "#1e1e1e"
        border_color = "#c0c0c0"
        cell_bg = "transparent"
        cell_color = "inherit"

    width = len(header)
    parts = [
        f'<table border="1" cellspacing="0" cellpadding="5"'
        f' style="border-collapse:collapse; margin:10px 0;'
        f' border-color:{border_color};">'
    ]
    parts.append("<tr>")
    for cell in header:
        parts.append(
            f'<th style="background:{head_bg}; color:{head_color};'
            f' padding:5px 8px; text-align:left;">{_inline(cell)}</th>'
        )
    parts.append("</tr>")
    for row in rows:
        padded = (row + [""] * width)[:width]
        parts.append("<tr>")
        for cell in padded:
            parts.append(
                f'<td style="background:{cell_bg}; color:{cell_color}; padding:5px 8px;">{_inline(cell)}</td>'
            )
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _is_block_start(lines: list[str], i: int) -> bool:
    line = lines[i]
    stripped = line.strip()
    if not stripped:
        return True
    return bool(
        _HEADING_RE.match(stripped)
        or _HR_RE.match(stripped)
        or _UL_RE.match(line)
        or _OL_RE.match(line)
        or stripped.startswith(">")
        or _CODE_PLACEHOLDER_RE.match(stripped)
        or _is_table_start(lines, i)
    )


def _render_blocks(text: str, code_blocks: list[str], *, dark: bool = True) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()

        m = _CODE_PLACEHOLDER_RE.match(stripped)
        if m:
            out.append(code_blocks[int(m.group(1))])
            i += 1
            continue
        if not stripped:
            i += 1
            continue
        if _HR_RE.match(stripped):
            out.append('<hr style="border:none; border-top:1px solid #4a4a55; margin:12px 0;">')
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            size = {1: 20, 2: 17, 3: 15}.get(level, 14)
            out.append(
                f'<h{level} style="font-size:{size}px; font-weight:600; margin:16px 0 6px 0;">'
                f"{_inline(m.group(2).strip())}</h{level}>"
            )
            i += 1
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]).strip())
                i += 1
            out.append(
                '<blockquote style="margin:8px 0; padding:4px 12px;'
                ' border-left:3px solid #5a5a66; color:#b8b8c0;">'
                + "<br>".join(_inline(q) for q in quote)
                + "</blockquote>"
            )
            continue

        if _UL_RE.match(lines[i]) or _OL_RE.match(lines[i]):
            items, i = _collect_list_items(lines, i)
            out.append(_render_list_tree(items))
            continue

        if _is_table_start(lines, i):
            header = _split_table_row(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_table_row(lines[i]))
                i += 1
            out.append(_render_table(header, rows, dark=dark))
            continue

        para: list[str] = []
        while i < n and lines[i].strip() and not _is_block_start(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append('<p style="margin:8px 0;">' + "<br>".join(_inline(p) for p in para) + "</p>")

    return "\n".join(out)


def render_markdown(text: str, *, dark: bool = True) -> str:
    """Преобразует markdown-ответ модели в HTML для QTextBrowser.

    Args:
        text: Markdown.
        dark: [9.6.6] Тёмная тема (по умолчанию True — как раньше).
            Влияет только на таблицы.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.count("```") % 2 == 1:
        text += "\n```"
    code_blocks: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        lang = (m.group("lang") or "").strip()
        # [9.5.1] Палитра кода — по теме.
        code_blocks.append(
            _render_code_block(m.group("code"), lang, dark=dark)
        )
        return f"\x00CB{len(code_blocks) - 1}\x00"

    stashed = _CODE_FENCE_RE.sub(_stash, text)
    body = _render_blocks(stashed, code_blocks, dark=dark)
    body = re.sub(r"\x00CB(\d+)\x00", lambda m: code_blocks[int(m.group(1))], body)
    return f'<div style="line-height:1.5;">{body}</div>'


# ============================================================================
# Вспомогательные элементы
# ============================================================================


class ChatActivityItem(QWidget):
    """Серая строка-событие о ходе работы агента (журнал фаз)."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 1, 8, 1)
        layout.setSpacing(0)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet("color: #8f8f8f; font-size: 12px; font-style: italic; background: transparent;")
        layout.addWidget(label, 1)
        self.label = label


class ChatSourcesItem(QWidget):
    """Сворачиваемый блок источников RAG."""

    from PySide6.QtCore import Signal  # локально — не тянем на модульном уровне

    source_activated = Signal(str, int)

    def __init__(
        self,
        sources: list[tuple[str, int, int]],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._sources = list(sources)
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 2, 8, 2)
        outer.setSpacing(2)

        self._header_btn = QPushButton(f"📚 Источники проекта ({len(self._sources)}) · ▼ развернуть")
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setFlat(True)
        self._header_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header_btn.setStyleSheet(
            "QPushButton { color: #8f8f8f; font-size: 12px; font-style: italic;"
            " text-align: left; border: none; background: transparent;"
            " padding: 0; }"
            " QPushButton:hover { color: #ccc; }"
        )
        self._header_btn.clicked.connect(self._toggle_body)
        outer.addWidget(self._header_btn)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 0, 0, 0)
        body_layout.setSpacing(1)

        for path, start, end in self._sources:
            btn = QPushButton(format_source_label(path, start, end))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFlat(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(
                "QPushButton { color: #6ba3d6; font-size: 12px;"
                " text-align: left; border: none; background: transparent;"
                " padding: 0; }"
                "QPushButton:hover { text-decoration: underline;"
                " color: #8bc0f0; }"
            )
            btn.clicked.connect(lambda _=False, p=path, s=start: self.source_activated.emit(p, s))
            body_layout.addWidget(btn)

        self._body.setVisible(False)
        outer.addWidget(self._body)

    def _toggle_body(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        arrow = "▲ свернуть" if self._expanded else "▼ развернуть"
        self._header_btn.setText(f"📚 Источники проекта ({len(self._sources)}) · {arrow}")


def format_source_label(file_path: str, start: int, end: int) -> str:
    """Подпись источника RAG вида ``file:строка`` или ``file:start-end``."""
    return f"{file_path}:{start}-{end}" if end != start else f"{file_path}:{start}"


# ============================================================================
# 4.8.B: части сообщения
# ============================================================================


class ReasoningWidget(QWidget):
    """Сворачиваемый блок размышлений модели (ReasoningPart) — 0.1.3a.

    Авто-высота: тело растёт по контенту до потолка (~40 % высоты чата),
    дальше — внутренний скролл. При разворачивании/сворачивании —
    ``QPropertyAnimation`` на ``maximumHeight`` (150 мс). При ``append_text``
    — внутренний авто-скролл к последнему токену, чтобы новые токены
    были видны без ручного скролла.

    Контракт не меняется: ``set_text`` / ``append_text`` / ``collapse``.
    """

    # Fallback-потолок, если QScrollArea не нашлась и окно ещё не показано.
    _FALLBACK_MAX_HEIGHT = 480
    # Доля высоты viewport чата, под которую пускаем reasoning.
    _MAX_HEIGHT_RATIO = 0.4
    # Минимальная полезная высота тела.
    _MIN_BODY_HEIGHT = 40
    # Длительность анимации раскрытия/сворачивания.
    _ANIM_DURATION_MS = 150

    def __init__(
        self,
        initial_text: str = "",
        parent: QWidget | None = None,
        *,
        dark: bool = True,
        images: list[str] | None = None,
    ):
        super().__init__(parent)
        self._dark = dark
        self._text = initial_text or ""
        self._expanded = True
        self._anim: QPropertyAnimation | None = None

        c = _theme_colors(dark)
        container = QFrame(self)
        container.setObjectName("_reasoning_widget")
        self._container = container       # ← теперь container уже существует
        container.setStyleSheet(
            "QFrame#_reasoning_widget {"
            f" background: {c.reasoning_bg};"
            f" border: 1px solid {c.bubble_border};"
            " border-radius: 6px;"
            " padding: 4px 10px;"
            " margin: 2px 0;"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(container)

        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        self._header_label = QLabel()
        self._header_label.setTextFormat(Qt.TextFormat.RichText)
        header_row.addWidget(self._header_label, stretch=1)

        self._toggle_btn = QPushButton("▲ Свернуть" if self._expanded else "▼ Развернуть")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._toggle_btn.setStyleSheet(_action_btn_css(dark))
        self._toggle_btn.clicked.connect(self._toggle)
        header_row.addWidget(self._toggle_btn)

        body.addLayout(header_row)

        self._browser = QTextBrowser()
        self._browser.setReadOnly(True)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.setStyleSheet(
            "QTextBrowser {"
            f" background: transparent; border: none; color: {c.reasoning_text};"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 12px; padding: 2px 0; }"
        )
        self._browser.setMinimumHeight(0)
        self._browser.setMaximumHeight(self._compute_max_height())
        body.addWidget(self._browser)

        self._refresh_browser()
        self._apply_visibility()

    # ---------------------------------------------------------------
    # Высота
    # ---------------------------------------------------------------

    def _compute_max_height(self) -> int:
        """Потолок высоты: 40 % viewport ближайшего QScrollArea, иначе окна.

        Ищем ``QScrollArea`` вверх по иерархии: ``ChatPanel`` использует
        ``AutoScrollArea`` (наследник ``QScrollArea``), поэтому обычно
        находим именно его. Если виджет ещё не встроен — берём высоту
        окна; если и её нет (headless) — ``_FALLBACK_MAX_HEIGHT``.
        """
        p: QWidget | None = self.parent()  # type: ignore[assignment]
        while p is not None:
            if isinstance(p, QScrollArea):
                vh = p.viewport().height()
                if vh > 0:
                    return max(self._MIN_BODY_HEIGHT * 2, int(vh * self._MAX_HEIGHT_RATIO))
                break
            p = p.parent()  # type: ignore[assignment]
        try:
            wh = self.window().height()
        except Exception:
            wh = 0
        if wh > 0:
            return max(self._MIN_BODY_HEIGHT * 2, int(wh * self._MAX_HEIGHT_RATIO))
        return self._FALLBACK_MAX_HEIGHT

    def _natural_height(self) -> int:
        """Естественная высота документа при текущей ширине viewport."""
        vw = self._browser.viewport().width()
        if vw <= 0:
            return self._MIN_BODY_HEIGHT
        doc: QTextDocument = self._browser.document()
        doc.setTextWidth(vw)
        return max(self._MIN_BODY_HEIGHT, int(doc.size().height()) + 8)

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        """Пересчитываем потолок при изменении ширины/высоты (0.1.3a).

        Если идёт анимация — не мешаем ей; иначе сразу применяем cap,
        чтобы при ресайзе окна reasoning не «залипал» на старой высоте.
        """
        super().resizeEvent(event)
        if self._expanded and self._anim is None:
            self._browser.setMaximumHeight(self._compute_max_height())

    # ---------------------------------------------------------------
    # Содержимое
    # ---------------------------------------------------------------

    def _refresh_browser(self) -> None:
        """Пересобирает HTML из `_text`; высота — по контенту с потолком."""
        c = _theme_colors(self._dark)
        safe = (
            self._text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        )
        self._browser.setHtml(
            f'<div style="color:{c.reasoning_text}; font-style:italic;">{safe}</div>'
        )
        cap = self._compute_max_height()
        self._browser.setMaximumHeight(cap)
        # Внутренний авто-скролл к последнему токену: пользователь видит
        # свежие мысли, не прокручивая вручную.
        if self._expanded:
            QTimer.singleShot(0, self._scroll_body_to_end)
        self._refresh_header()

    def _scroll_body_to_end(self) -> None:
        """Скроллит внутренний QTextBrowser к последнему токену."""
        sb = self._browser.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_header(self) -> None:
        n = len(self._text)
        unit = (
            "символ"
            if n % 10 == 1 and n % 100 != 11
            else ("символа" if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14) else "символов")
        )
        self._header_label.setText(
            f'<span style="color: #7a7a8a; font-size: 11px; font-weight: 600;">'
            f"💭 Размышления</span> "
            f'<span style="color: #6a6a7a; font-size: 11px;">· {n} {unit}</span>'
        )

    # ---------------------------------------------------------------
    # Видимость и анимация
    # ---------------------------------------------------------------

    def _apply_visibility(self) -> None:
        """Мгновенное состояние (без анимации) — для __init__ и set_text."""
        self._browser.setVisible(self._expanded)
        if self._expanded:
            self._browser.setMaximumHeight(self._compute_max_height())
        else:
            self._browser.setMaximumHeight(0)
        self._toggle_btn.setText("▲ Свернуть" if self._expanded else "▼ Развернуть")

    def _stop_anim(self) -> None:
        """Останавливает текущую анимацию (если идёт)."""
        if self._anim is not None:
            with contextlib.suppress(Exception):
                self._anim.stop()
            self._anim = None

    def _animate_height(self, *, expand: bool) -> None:
        """Плавно меняет maximumHeight: 0 ⇄ cap, 150 мс (0.1.3a).

        Вырожденный случай (``start == target``) обрабатываем явно: в
        PySide6 ``QPropertyAnimation.finished`` не гарантирован для no-op
        анимаций, а нам критично вызвать ``hide()`` после сворачивания.
        Для collapse дополнительно ставим ``QTimer.singleShot`` — safety
        net на случай, если ``finished`` всё равно не сработает.
        """
        self._stop_anim()
        self._browser.setVisible(True)
        # ``height()`` до первого layout может быть 0. ``max(1, ...)``
        # гарантирует, что анимация не вырожденная и ``finished`` сработает.
        start = max(1, self._browser.height())
        target = self._compute_max_height() if expand else 0
        if start == target:
            self._browser.setMaximumHeight(target)
            if not expand:
                self._browser.hide()
            return
        anim = QPropertyAnimation(self._browser, b"maximumHeight", self)
        anim.setDuration(self._ANIM_DURATION_MS)
        anim.setStartValue(start)
        anim.setEndValue(target)
        if not expand:
            anim.finished.connect(self._on_collapse_finished)
            # Safety net: даже если finished не придёт — тело скроется.
            QTimer.singleShot(
                self._ANIM_DURATION_MS + 20,
                self._finalize_collapse_if_still_collapsed,
            )
        anim.start()
        self._anim = anim

    def _on_collapse_finished(self) -> None:
        """Сработал ``finished`` анимации сворачивания — скрываем тело."""
        self._anim = None
        self._browser.hide()

    def _finalize_collapse_if_still_collapsed(self) -> None:
        """Safety net: гарантированно скрыть тело после collapse.

        Если ``finished`` уже отработал — no-op. Если пользователь успел
        развернуть обратно (``_expanded=True``) — не трогаем, чтобы не
        «моргнуть» открытым блоком.
        """
        if not self._expanded:
            self._browser.hide()
            self._browser.setMaximumHeight(0)

    def _toggle(self) -> None:
        """Разворачивает/сворачивает тело с анимацией."""
        self._expanded = not self._expanded
        self._toggle_btn.setText("▲ Свернуть" if self._expanded else "▼ Развернуть")
        self._animate_height(expand=self._expanded)
        if self._expanded:
            QTimer.singleShot(self._ANIM_DURATION_MS, self._scroll_body_to_end)

    # ---------------------------------------------------------------
    # Публичный API (контракт не меняется)
    # ---------------------------------------------------------------

    def set_text(self, text: str) -> None:
        self._text = text or ""
        if self._text and not self._expanded:
            # Если пришёл непустой текст, а блок свёрнут — разворачиваем.
            self._expanded = True
            self._apply_visibility()
        self._refresh_browser()

    def append_text(self, token: str) -> None:
        if not token:
            return
        was_empty = not self._text
        self._text += token
        if was_empty and not self._expanded:
            # Первый токен — развернуть.
            self._expanded = True
            self._apply_visibility()
        self._refresh_browser()

    def collapse(self) -> None:
        """Свернуть блок (вызывается в ChatMessageItem.finalize)."""
        if self._expanded:
            self._expanded = False
            self._toggle_btn.setText("▼ Развернуть")
            self._animate_height(expand=False)

    def _rebuild_diff_mode(self) -> None:
        """Пересобирает UI diff-режима (кнопки, стили) — при смене темы."""
        # Удаляем все layout-элементы, кроме header (index 0).
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(1)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._toggle_btn = None
        self._render_diff_mode()

    def apply_theme(self, dark: bool) -> None:
        """[9.5.1] Перекрашивает виджет под тему."""
        self._dark = dark
        c = _theme_colors(dark)
        container = self._container  # см. `self._container = container` в __init__
        container.setStyleSheet(
            "QFrame#_reasoning_widget {"
            f" background: {c.reasoning_bg};"
            f" border: 1px solid {c.bubble_border};"
            " border-radius: 6px;"
            " padding: 4px 10px;"
            " margin: 2px 0;"
            "}"
        )
        self._toggle_btn.setStyleSheet(_action_btn_css(dark))
        self._browser.setStyleSheet(
            "QTextBrowser {"
            f" background: transparent; border: none; color: {c.reasoning_text};"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 12px; padding: 2px 0; }"
        )
        self._refresh_browser()

class TextWidget(QTextBrowser):
    """Markdown-текст ответа модели с авто-высотой под содержимое."""

    def __init__(self, initial_text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._raw_text = ""
        # [9.6.6] Тёмная тема (для таблиц в markdown).
        self._dark: bool = True
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.document().setDocumentMargin(0)
        self._apply_style()
        if initial_text:
            self.set_text(initial_text)

    def _apply_style(self) -> None:
        """[9.5.1] Применяет QSS по текущей теме."""
        c = _theme_colors(self._dark)
        self.setStyleSheet(
            "QTextBrowser {"
            " font-family: 'Segoe UI', Roboto, sans-serif;"
            " font-size: 14px;"
            f" color: {c.text};"
            " background: transparent; border: none; padding: 0;"
            " }"
        )

    def set_dark(self, dark: bool) -> None:
        """[9.6.6] Переключает цвет таблиц в markdown."""
        if dark == self._dark:
            return
        self._dark = dark
        self._apply_style()
        if self._raw_text:
            self.set_text(self._raw_text)

    @staticmethod
    def _detect_dark_from_palette(widget: QWidget) -> bool:
        """Определяет тёмная ли тема по текущей палитре окна."""
        try:
            color = widget.palette().color(widget.palette().ColorRole.Window)
            return color.lightness() < 128
        except Exception:
            return True

    def set_text(self, text: str) -> None:
        self._raw_text = text or ""
        # [9.6.6] Авто-детект темы при первом рендере (если не задана явно).
        if self._raw_text and not hasattr(self, "_theme_pinned"):
            self._dark = self._detect_dark_from_palette(self)
            self._theme_pinned = True
        self.setHtml(render_markdown(self._raw_text, dark=self._dark))
        self._adjust_height()

    def append_text(self, token: str) -> None:
        if not token:
            return
        self._raw_text += token
        self.setHtml(render_markdown(self._raw_text, dark=self._dark))
        self._adjust_height()

    def _adjust_height(self) -> None:
        """Подгоняет высоту под содержимое при текущей ширине."""
        doc: QTextDocument = self.document()
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return  # resizeEvent вызовет повторно
        doc.setTextWidth(viewport_width)
        height = int(doc.size().height()) + 4
        self.setFixedHeight(max(height, 22))

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._adjust_height()

    def apply_theme(self, dark: bool) -> None:
        """[9.5.1] Синоним `set_dark` — единый API темы."""
        self.set_dark(dark)


def _truncate_preview(text: str, limit: int = 200) -> tuple[str, bool]:
    """Обрезает текст для preview-строки в карточке tool-call.

    Args:
        text: Полный текст результата.
        limit: Максимальная длина preview (символов).

    Returns:
        (preview, was_truncated). `was_truncated` = True, если текст
        длиннее `limit` (нужно показать кнопку «Показать полностью»).
    """
    if not text:
        return ("", False)
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return (clean, False)
    if limit <= 0:
        return ("…", True)
    return (clean[: limit - 1] + "…", True)

def _make_thumbnail_label(
    data_url: str, max_size: int = 120
) -> QLabel | None:
    """[9.7] Возвращает QLabel с миниатюрой из base64 data-url.

    Клик по миниатюре открывает полный размер в системном просмотрщике
    (через `QDesktopServices.openUrl` — но data-url не открывается;
    вместо этого копируем в temp и открываем).
    """
    if not data_url.startswith("data:image/"):
        return None
    try:
        header, _, b64 = data_url.partition(",")
        import base64

        raw = base64.b64decode(b64)
    except Exception:
        return None
    try:
        from PySide6.QtGui import QPixmap
    except Exception:
        return None
    pix = QPixmap()
    if not pix.loadFromData(raw):
        return None
    pix = pix.scaled(
        max_size, max_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    label = QLabel()
    label.setPixmap(pix)
    label.setStyleSheet(
        "QLabel { border: 1px solid #3a3a48; border-radius: 6px; padding: 2px; }"
    )
    label.setToolTip("Прикреплённое изображение")
    return label
def _escape_diff(text: str) -> str:
    """Экранирует HTML-спецсимволы для diff-сводки."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Лимит полного текста в раскрывающемся QTextBrowser. Больше не нужно:
# полный observation может быть 100 КБ, а UI и так неудобно листать.
_MAX_RESULT_VIEW_CHARS = 20_000


class ToolCallCard(QWidget):
    """Карточка вызова инструмента (ToolCallPart + результат).

    Жизненный цикл:
      1. `__init__` — pending (⏳), показывает `name(args)`.
      2. `set_result(content, is_error, metadata)`:
         * если `metadata` содержит `diff` и `path` (write_file) —
           diff-сводка `+N −M` + кнопки «📂 Открыть» / «↩ Откатить»;
         * иначе — preview первых 200 символов + «▸ Показать полностью»
           (для длинных observation).
    """

    # [9.6.1] Сигналы diff-режима `write_file`.
    open_in_editor_requested = Signal(str)
    revert_requested = Signal(dict)
    # [9.8] Показать diff в Monaco DiffEditor (третья кнопка).
    show_diff_requested = Signal(dict)
    # [10.9] Откат файлового снапшота.
    snapshot_undo_requested = Signal(str)

    _ICONS: dict[str, str] = {
        "read_file": "📖",
        "write_file": "✏️",
        "list_directory": "📁",
        "search_codebase": "🔍",
        "web_search": "🌐",
        "get_weather": "🌤",
        "get_current_datetime": "🕒",
        "get_exchange_rate": "💱",
        "get_exchange_rates_multi": "💱",
        "get_git_status": "🔎",
        "create_snapshot": "📸",
        "undo_last_snapshot": "⏪",
        "read_attached_file": "📄",
        "record_hypothesis": "💡",
        "update_hypothesis": "🔬",
        "list_hypotheses": "📋",
        "run_shell_command": "⚡",
    }

    # [9.5.1] Палитра для тёмной/светлой темы: (bg, border, status_icon).
    # Выбор — в `_apply_state_style` по `self._dark`.
    _STATE_STYLES: dict[
        str, tuple[tuple[str, str, str], tuple[str, str, str]]
    ] = {
        "pending": (
            ("rgba(120,120,140,0.06)", "#3a3a48", "⏳"),
            ("rgba(120,120,140,0.04)", "#d0d0d0", "⏳"),
        ),
        "ok": (
            ("rgba(80,160,80,0.08)", "#4a6a4a", "✓"),
            ("rgba(80,160,80,0.08)", "#a5d6a7", "✓"),
        ),
        "error": (
            ("rgba(200,80,80,0.10)", "#7a3a3a", "✗"),
            ("rgba(200,80,80,0.08)", "#e0a0a0", "✗"),
        ),
    }

    def __init__(
        self,
        name: str,
        arguments_json: str,
        parent: QWidget | None = None,
        *,
        dark: bool = True,
    ):
        super().__init__(parent)
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            args = {}

        self._name = name
        self._arguments_json = arguments_json or "{}"
        self._state = "pending"
        self._result_content = ""
        self._result_is_error = False
        self._result_expanded = False
        # [9.6.1] Структурированный результат (diff write_file).
        self._metadata: dict | None = None

        # Внешний контейнер (для смены стиля без пересоздания).
        self._container = QFrame(self)
        self._container.setObjectName("_tool_card")
        self._dark = dark
        c = _theme_colors(dark)

        # при длинных arguments_json — tooltip с полным текстом.
        if len(arguments_json) > 200:
            self._name_label.setToolTip(arguments_json[:2000])
        # Header — имя инструмента.

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(self._container)

        body = QVBoxLayout(self._container)
        body.setContentsMargins(10, 6, 10, 6)
        body.setSpacing(4)
        self._body_layout = body

        # --- Header: [icon] name(args) ... [status] ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        icon = self._ICONS.get(name, "🔧")
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 14px;")
        header.addWidget(icon_label)

        args_summary = _format_args_short(args)
        text = f"{name}({args_summary})" if args_summary else f"{name}()"
        self._name_label = QLabel(text)
        self._name_label.setTextFormat(Qt.TextFormat.PlainText)
        self._name_label.setWordWrap(True)
        self._name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._name_label.setStyleSheet(
            f"color: {c.info}; font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 12px;"
        )
        header.addWidget(self._name_label, stretch=1)
        # Status — по цвету темы.
        self._status_label = QLabel("⏳")
        self._apply_state_style()  # выставит стиль статуса
        self._status_label.setStyleSheet("color: #7a7a8a; font-size: 13px; padding-left: 4px;")
        self._status_label.setToolTip("Инструмент ещё не выполнен")
        header.addWidget(self._status_label)

        self._toggle_btn: QPushButton | None = None
        body.addLayout(header)

        # --- Preview результата (скрыт до set_result) ---
        self._preview_label = QLabel("")
        self._preview_label.setWordWrap(True)
        self._preview_label.setTextFormat(Qt.TextFormat.PlainText)
        self._preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._preview_label.setStyleSheet(
            "color: #a8a8b8; font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 2px 0 0 20px;"
        )
        # Preview.
        self._preview_label.setStyleSheet(
            f"color: {c.tool_preview_text};"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 4px 0 0 22px;"
        )
        self._preview_label.setVisible(False)
        body.addWidget(self._preview_label)

        # Full browser.
        # --- Полный текст (скрыт, раскрывается кнопкой) ---
        self._full_browser = QTextBrowser()
        self._full_browser.setReadOnly(True)
        self._full_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._full_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._full_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        full_bg = "rgba(0,0,0,0.15)" if dark else "rgba(0,0,0,0.04)"
        self._full_browser.setStyleSheet(
            "QTextBrowser {"
            f" background: {full_bg}; border: none;"
            f" color: {c.text};"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 6px 8px;"
            " border-radius: 4px;"
            "}"
        )
        self._full_browser.setMaximumHeight(220)
        self._full_browser.setVisible(False)
        body.addWidget(self._full_browser)

        self._apply_state_style()

    def _add_lsp_link(self, line: int, col: int, msg: str, path: str) -> None:
        """Кликабельная ссылка на LSP-ошибку (jump-to-line в редакторе)."""
        link = QPushButton(f"  стр. {line}:{col} — {msg}")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        link.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " text-align: left; color: #6ba3d6;"
            " font-family: 'Consolas', monospace; font-size: 11px;"
            " padding: 0 20px; }"
            " QPushButton:hover { text-decoration: underline; color: #8bc0f0; }"
        )
        link.clicked.connect(
            lambda _checked=False, ln=line, p=path:
            self.open_in_editor_requested.emit(f"{p}:{ln}")
        )
        self._body_layout.addWidget(link)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> str:
        """Текущее состояние: pending | ok | error."""
        return self._state

    def set_result(
        self,
        content: str,
        is_error: bool,
        metadata: dict | None = None,
    ) -> None:
        """Обновляет карточку результатом выполнения инструмента.

        Args:
            content: Текст результата (Observation) или "Отклонено пользователем".
            is_error: True — ошибка (красный статус), False — успех.
            metadata: [9.6.1] Структурированные данные от инструмента.
                Для `write_file` содержит `diff`, `path`, `lines_added`,
                `lines_removed`, `original_content`, `is_new_file`,
                `syntax_error`. Если задано — рендерим diff-режим.
        """
        self._result_content = content or ""
        self._result_is_error = bool(is_error)
        self._metadata = metadata
        self._state = "error" if is_error else "ok"

        self._apply_state_style()

        # [9.6.1] Diff-режим для write_file (только при успехе).
        if not is_error and isinstance(metadata, dict) and "diff" in metadata and "path" in metadata:
            self._render_diff_mode()
        else:
            self._update_preview()

    # ------------------------------------------------------------------
    # Внутренние
    # ------------------------------------------------------------------

    def _apply_state_style(self) -> None:
        # [9.5.1] Палитра по теме.
        style_pair = self._STATE_STYLES[self._state]
        bg, border, icon = style_pair[0] if self._dark else style_pair[1]
        self._container.setStyleSheet(
            "QFrame#_tool_card {"
            f" background: {bg};"
            f" border: 1px solid {border};"
            " border-radius: 8px;"
            " padding: 0;"
            "}"
        )
        self._status_label.setText(icon)
        c = _theme_colors(self._dark)
        if self._state == "pending":
            self._status_label.setStyleSheet(
                f"color: {c.text_muted}; font-size: 13px; padding-left: 6px;"
            )
            self._status_label.setToolTip("Инструмент ещё не выполнен")
        elif self._state == "ok":
            self._status_label.setStyleSheet(
                f"color: {c.success}; font-size: 13px;"
                " padding-left: 6px; font-weight: 700;"
            )
            self._status_label.setToolTip("Успешно")
        else:
            self._status_label.setStyleSheet(
                f"color: {c.danger}; font-size: 13px;"
                " padding-left: 6px; font-weight: 700;"
            )
            self._status_label.setToolTip("Ошибка")

    def _update_preview(self) -> None:
        """Собирает preview + опциональную кнопку раскрытия полного текста."""
        content = self._result_content
        if not content:
            self._preview_label.setVisible(False)
            return

        preview, truncated = _truncate_preview(content, limit=200)
        self._preview_label.setText(preview)
        c = _theme_colors(self._dark)
        color = c.danger if self._result_is_error else c.tool_preview_text
        self._preview_label.setStyleSheet(
            f"color: {color};"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 4px 0 0 22px;"
        )
        self._preview_label.setVisible(True)

        if truncated:
            # Создаём/показываем кнопку один раз.
            if self._toggle_btn is None:
                btn = QPushButton("▸ Показать полностью")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setStyleSheet(
                    "QPushButton { background: transparent; border: none;"
                    " color: #6ba3d6; font-size: 11px;"
                    " padding: 2px 0 0 20px; text-align: left; }"
                    " QPushButton:hover { color: #8bc0f0; text-decoration: underline; }"
                )
                btn.clicked.connect(self._toggle_full_text)
                self._body_layout.addWidget(btn)
                self._toggle_btn = btn

            # Заполняем полный текст один раз.
            full = content
            if len(full) > _MAX_RESULT_VIEW_CHARS:
                full = (
                    full[:_MAX_RESULT_VIEW_CHARS] + f"\n\n… [результат обрезан до {_MAX_RESULT_VIEW_CHARS} "
                    f"из {len(content)} символов]"
                )
            self._full_browser.setPlainText(full)
            self._toggle_btn.setVisible(True)
        else:
            if self._toggle_btn is not None:
                self._toggle_btn.setVisible(False)
            self._full_browser.setVisible(False)

    def _toggle_full_text(self) -> None:
        self._result_expanded = not self._result_expanded
        self._full_browser.setVisible(self._result_expanded)
        if self._toggle_btn is not None:
            self._toggle_btn.setText("▾ Свернуть" if self._result_expanded else "▸ Показать полностью")

    # ------------------------------------------------------------------
    # [9.6.1] Diff-режим write_file
    # ------------------------------------------------------------------

    def _render_diff_mode(self) -> None:
        """Рендерит diff-сводку + кнопки (только для write_file)."""
        meta = self._metadata or {}
        path = str(meta.get("path") or "")
        lines_added = int(meta.get("lines_added") or 0)
        lines_removed = int(meta.get("lines_removed") or 0)
        is_new_file = bool(meta.get("is_new_file"))
        syntax_error = str(meta.get("syntax_error") or "")

        # Скрываем preview-элементы.
        self._preview_label.setVisible(False)
        self._full_browser.setVisible(False)
        if self._toggle_btn is not None:
            self._toggle_btn.setVisible(False)

        # Сводка: «📄 file.py · +N −M (новый)».
        parts: list[str] = []
        parts.append(f"<b>📄 {_escape_diff(path)}</b>")
        stat_bits: list[str] = []
        if lines_added:
            stat_bits.append(f'<span style="color:#98c379;">+{lines_added}</span>')
        if lines_removed:
            stat_bits.append(f'<span style="color:#e06c75;">−{lines_removed}</span>')
        if stat_bits:
            parts.append(" ".join(stat_bits))
        if is_new_file:
            parts.append('<span style="color:#6ba3d6;">(новый файл)</span>')
        summary = " · ".join(parts)

        # [9.5.1] Длинные пути — обрезка + tooltip.
        path_display = path
        if len(path_display) > 60:
            path_display = "…" + path_display[-58:]
        parts.append(f"<b>📄 {_escape_diff(path_display)}</b>")
        self._preview_label.setText(summary)
        self._preview_label.setToolTip(path)
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setStyleSheet(
            "color: #d4d4d4; font-family: 'Segoe UI', sans-serif; font-size: 12px; padding: 4px 0 2px 20px;"
        )
        self._preview_label.setVisible(True)

        # Синтаксическая ошибка (если есть) — предупреждение под сводкой.
        if syntax_error:
            err_label = QLabel(f"⚠️ {syntax_error[:200]}")
            err_label.setWordWrap(True)
            err_label.setStyleSheet(
                "color: #e5c07b; font-family: 'Consolas', monospace;"
                " font-size: 11px; padding: 2px 0 2px 20px;"
            )
            self._body_layout.addWidget(err_label)

        # [10.8] LSP-диагностика — красный блок с jump-to-line.
        lsp_errors = meta.get("lsp_errors") or []
        if lsp_errors:
            lsp_header = QLabel(f"⚠️ LSP: {len(lsp_errors)} ошибок")
            lsp_header.setStyleSheet(
                "color: #e06c75; font-weight: 600; font-size: 11px;"
                " padding: 2px 0 2px 20px;"
            )
            self._body_layout.addWidget(lsp_header)
            for err in lsp_errors[:5]:
                line = err.get("line", "?")
                col = err.get("column", "?")
                msg = str(err.get("message", ""))[:200]
                self._add_lsp_link(line, col, msg, path)
            if len(lsp_errors) > 5:
                more = QLabel(f"… ещё {len(lsp_errors) - 5}")
                more.setStyleSheet(
                    "color: #888; font-size: 11px; padding: 0 20px;"
                )
                self._body_layout.addWidget(more)

        # [10.9] Кнопка «↩ Откатить снапшот», если есть snapshot_id.
        snapshot_id = str(meta.get("snapshot_id") or "")
        if snapshot_id:
            snap_btn = QPushButton(f"↩ Откатить (snapshot {snapshot_id[:15]}…)")
            snap_btn.setToolTip(
                "Восстановить файл из снапшота, сделанного перед записью."
            )
            snap_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #444;"
                " border-radius: 10px; color: #e5c07b; font-size: 11px;"
                " padding: 2px 10px; }"
                " QPushButton:hover { background: rgba(229,192,123,0.15); }"
            )
            snap_btn.clicked.connect(
                lambda _checked=False, sid=snapshot_id: self.snapshot_undo_requested.emit(sid)
            )
            self._body_layout.addWidget(snap_btn)

            # Кнопки: «📂 Открыть» / «🔍 Показать diff» / «↩ Откатить».
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 2, 0, 0)
        btn_row.setSpacing(6)

        action_css = _action_btn_css(self._dark)

        open_btn = QPushButton("📂 Открыть в редакторе")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_btn.setStyleSheet(action_css)
        open_btn.clicked.connect(lambda _checked=False, p=path: self.open_in_editor_requested.emit(p))
        btn_row.addWidget(open_btn)

        # [9.8] «Показать diff» — откроет Monaco DiffEditor с original↔modified.
        diff_btn = QPushButton("🔍 Показать diff")
        diff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        diff_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        diff_btn.setStyleSheet(action_css)
        diff_btn.setEnabled(bool(meta.get("original_content")) or is_new_file)
        diff_btn.clicked.connect(lambda _checked=False: self._on_show_diff_clicked())
        btn_row.addWidget(diff_btn)

        revert_btn = QPushButton("↩ Откатить")
        revert_btn.setToolTip("Восстановить исходное содержимое файла (запись была уже сделана)")
        revert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        revert_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        revert_btn.setStyleSheet(action_css)
        revert_btn.clicked.connect(lambda _checked=False: self._on_revert_clicked())
        btn_row.addWidget(revert_btn)

        btn_row.addStretch()
        self._body_layout.addLayout(btn_row)

    def _on_revert_clicked(self) -> None:
        """Эмитит сигнал отката с данными из metadata."""
        if not self._metadata:
            return
        payload = {
            "path": self._metadata.get("path", ""),
            "original_content": self._metadata.get("original_content", ""),
            "is_new_file": bool(self._metadata.get("is_new_file")),
        }
        self.revert_requested.emit(payload)

    def _on_show_diff_clicked(self) -> None:
        """[9.8] Эмитит show_diff_requested с original↔modified."""
        if not self._metadata:
            return
        path = str(self._metadata.get("path") or "")
        original = str(self._metadata.get("original_content") or "")
        # modified — текущее содержимое файла (после записи). Берём из
        # metadata, если инструмент его положил, иначе читаем с диска.
        modified = str(self._metadata.get("written_content") or "")
        if not modified and path:
            try:
                modified = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                modified = ""
        # Язык — по расширению.
        ext = ""
        if path:
            ext = Path(path).suffix.lower().lstrip(".")
        payload = {
            "path": path,
            "original": original,
            "modified": modified,
            "language": ext or "plaintext",
        }
        self.show_diff_requested.emit(payload)


def _format_args_short(args: dict, limit_per_value: int = 60) -> str:
    """Короткое представление аргументов для карточки tool_call."""
    parts: list[str] = []
    for key, value in args.items():
        s = str(value).replace("\n", " ")
        if len(s) > limit_per_value:
            s = s[: limit_per_value - 1] + "…"
        parts.append(f"{key}={s!r}")
    return ", ".join(parts)


# ============================================================================
# ChatMessageItem: композитный пузырь
# ============================================================================


class ChatMessageItem(QWidget):
    """Композитный пузырь сообщения: reasoning + text + tool_calls.

    Для role="user" и role="tool" остаётся одноблочным (простого текста
    достаточно). Для role="assistant" собирается из подвиджетов по мере
    поступления частей через `append_*`.
    """

    def __init__(
        self,
        role: str,
        content: str = "",
        thinking: str = "",
        mode_name: str = "",
        parent: QWidget | None = None,
        *,
        dark: bool = True,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking
        self.mode_name = mode_name
        self._dark = dark

        # Внутреннее состояние — по образцу ChatMessage(parts=[...]).
        self._reasoning_widget: ReasoningWidget | None = None
        self._text_widget: TextWidget | None = None
        self._tool_cards: list[ToolCallCard] = []

        self._build_ui()

        # Инициализация из переданного content/thinking (обратная совместимость).
        if thinking:
            self._ensure_reasoning_widget().set_text(thinking)
        if content:
            self._ensure_text_widget().set_text(content)

    def _build_ui(self) -> None:
        c = _theme_colors(self._dark)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("_bubble")
        is_user = self.role == "user"
        is_assistant = self.role == "assistant"
        is_tool = self.role in ("tool", "system")

        # [9.5.1] Стиль пузыря по токенам:
        # * user — surface_alt, скругление 12, без рамки;
        # * assistant — bubble_bg + тонкая рамка, скругление 10;
        # * tool/system — прозрачный, без рамки.
        if is_user:
            bubble.setStyleSheet(
                "QFrame#_bubble {"
                f" background-color: {c.bubble_user_bg};"
                " border-radius: 12px;"
                " padding: 10px 14px;"
                " border: none;"
                "}"
            )
            self.text_color = c.text
        elif is_assistant:
            bubble.setStyleSheet(
                "QFrame#_bubble {"
                f" background-color: {c.bubble_bg};"
                " border-radius: 10px;"
                " padding: 12px 14px;"
                f" border: 1px solid {c.bubble_border};"
                "}"
            )
            self.text_color = c.text
        else:
            bubble.setStyleSheet(
                "QFrame#_bubble {"
                " background-color: transparent;"
                " border-radius: 0;"
                " padding: 6px 12px;"
                " border: none;"
                "}"
            )
            self.text_color = c.text_muted

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        # [9.5.1] SPACE.md = 8 px между секциями.
        bubble_layout.setSpacing(8)
        self._bubble_layout = bubble_layout
        self.bubble = bubble

        # header (только assistant)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        if not is_tool and not is_user:
            header = (
                f"Ассистент ({self.mode_name})"
                if is_assistant
                else self.role
            )
            role_label = QLabel(header)
            role_label.setStyleSheet(
                "background: transparent;"
                f" color: {c.text_muted};"
                " font-weight: 600;"
                " font-size: 11px;"
                " padding: 0 0 4px 0;"
            )
            header_row.addWidget(role_label)
        header_row.addStretch()
        bubble_layout.addLayout(header_row)

        # [9.7] Миниатюры прикреплённых изображений.
        if self._images and is_user:
            self._image_strip = QWidget()
            img_layout = QHBoxLayout(self._image_strip)
            img_layout.setContentsMargins(0, 4, 0, 4)
            img_layout.setSpacing(6)
            for b64 in self._images:
                thumb = _make_thumbnail_label(b64, max_size=120)
                if thumb is not None:
                    img_layout.addWidget(thumb)
            img_layout.addStretch()
            bubble_layout.addWidget(self._image_strip)
        else:
            self._image_strip = None

        # parts container
        self._parts_container = QWidget()
        parts_layout = QVBoxLayout(self._parts_container)
        parts_layout.setContentsMargins(0, 0, 0, 0)
        # [9.5.1] 8 px между reasoning/text/tool-cards.
        parts_layout.setSpacing(8)
        self._parts_layout = parts_layout
        bubble_layout.addWidget(self._parts_container)

        # copy btn — единый action-стиль.
        if is_user or is_assistant:
            copy_row = QHBoxLayout()
            copy_row.setContentsMargins(0, 4, 0, 0)
            copy_row.setSpacing(4)
            copy_row.addStretch()
            copy_btn = QPushButton("Копировать")
            copy_btn.setToolTip("Копировать сообщение (markdown)")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            copy_btn.setStyleSheet(_action_btn_css(self._dark))
            copy_btn.clicked.connect(self._copy_to_clipboard)
            copy_row.addWidget(copy_btn)
            self.copy_button = copy_btn
            bubble_layout.addLayout(copy_row)

        inner = QHBoxLayout()
        inner.setContentsMargins(8, 0, 8, 0)
        if is_user:
            inner.addStretch()
            inner.addWidget(bubble, 1)
            inner.addStretch()
        else:
            inner.addWidget(bubble, 1)
        outer.addLayout(inner)

    # ---------------------------------------------------------------
    # Ленивое создание частей
    # ---------------------------------------------------------------

    def _ensure_reasoning_widget(self) -> ReasoningWidget:
        if self._reasoning_widget is None:
            self._reasoning_widget = ReasoningWidget(dark=self._dark)
            self._parts_layout.addWidget(self._reasoning_widget)
        return self._reasoning_widget

    def _ensure_text_widget(self) -> TextWidget:
        if self._text_widget is None:
            self._text_widget = TextWidget()
            self._text_widget.set_dark(self._dark)
            # [9.6.5] Убираем лишние вертикальные отступы `<p style="margin:8px 0">`
            # (первый/последний абзац в пузыре). Для assistant это не нужно
            # (там воздух между блоками уместен), а для user/коротких
            # системных сообщений — заметный разрыв до кнопки «Копировать».
            if self.role in ("user", "system"):
                self._text_widget.document().setDefaultStyleSheet(
                    "p { margin: 0; } ul, ol { margin: 2px 0; } pre { margin: 4px 0; }"
                )
                # Пересобираем HTML — defaultStyleSheet применяется только
                # к новому setHtml.
                if self.content:
                    self._text_widget.set_text(self.content)
            self._parts_layout.addWidget(self._text_widget)
        return self._text_widget

    # ---------------------------------------------------------------
    # Стриминг: новые части
    # ---------------------------------------------------------------

    def append_text(self, token: str) -> None:
        self.content += token
        self._ensure_text_widget().append_text(token)

    def append_reasoning(self, token: str) -> None:
        if self.role != "assistant":
            logger.debug(f"append_reasoning для role={self.role!r} — игнорирую")
            return
        self.thinking += token
        self._ensure_reasoning_widget().append_text(token)

    def append_tool_call(self, name: str, arguments_json: str) -> ToolCallCard | None:
        """[9.5.7] Добавляет карточку tool-call, возвращает её.

        Возвращённая карточка используется в `ChatPanel._on_tool_started`
        (сохраняется в `_active_tool_card`), а `_on_tool_executed`
        вызывает у неё `set_result(...)`.
        """
        if self.role != "assistant":
            logger.debug(f"append_tool_call для role={self.role!r} — игнорирую")
            return None
        card = ToolCallCard(name, arguments_json, dark=self._dark)
        self._tool_cards.append(card)
        self._parts_layout.addWidget(card)
        return card

    # ---------------------------------------------------------------
    # Старый API (обратная совместимость)
    # ---------------------------------------------------------------

    def update_content(self, new_content: str) -> None:
        self.content = new_content
        self._ensure_text_widget().set_text(new_content)

    def update_thinking(self, new_thinking: str) -> None:
        self.thinking = new_thinking
        self._ensure_reasoning_widget().set_text(new_thinking)

    def _set_content(self, text: str) -> None:
        """Устаревший приватный метод — оставлен как тонкая обёртка."""
        self.update_content(text)

    # ---------------------------------------------------------------
    # Финализация
    # ---------------------------------------------------------------

    def finalize(self) -> None:
        """Свернуть reasoning после завершения ответа."""
        if self._reasoning_widget is not None:
            self._reasoning_widget.collapse()

    # ---------------------------------------------------------------
    # Копирование
    # ---------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.content)

    # ---------------------------------------------------------------
    # Обёртка для обратной совместимости (используется в тестах)
    # ---------------------------------------------------------------

    def _render_markdown_to_html(self, text: str) -> str:
        return render_markdown(text)


# ============================================================================
# ChatDiagramItem: диаграмма компонентов (ASCII + опциональный Mermaid)
# ============================================================================


class ChatDiagramItem(QWidget):
    """Виджет диаграммы архитектуры: ASCII-схема, опционально Mermaid.

    ASCII генерируется кодом `agents.diagram.render_architecture` — имена
    блоков и связи гарантированно согласованы с графом проекта. Mermaid
    доступен как альтернативный вид (требует QWebEngine + локальный
    vendor/mermaid.min.js; при отсутствии — кнопка отключается).
    """

    def __init__(
        self,
        ascii_diagram: str = "",
        mermaid_diagram: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._ascii = ascii_diagram or ""
        self._mermaid = mermaid_diagram or ""
        self._mermaid_enabled = False

        container = QFrame(self)
        container.setObjectName("_diagram_item")
        container.setStyleSheet(
            "QFrame#_diagram_item {"
            " background: #1b1b22; border: 1px solid #2f2f3c;"
            " border-radius: 8px; padding: 8px 10px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(container)

        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("📊 Схема компонентов")
        title.setStyleSheet("color: #d4d4d4; font-size: 12px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()

        btn_style = (
            "QPushButton { background: transparent; border: 1px solid #444;"
            " border-radius: 10px; color: #888; font-size: 11px;"
            " padding: 2px 10px; }"
            " QPushButton:hover { color: #d4d4d4; }"
            " QPushButton:checked { background: #4a6741; color: #fff;"
            " border-color: #4a6741; }"
            " QPushButton:disabled { color: #555; border-color: #333; }"
        )

        self._ascii_btn = QPushButton("ASCII")
        self._ascii_btn.setCheckable(True)
        self._ascii_btn.setChecked(True)
        self._ascii_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ascii_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._ascii_btn.setStyleSheet(btn_style)
        self._ascii_btn.clicked.connect(lambda: self._switch_view("ascii"))
        header.addWidget(self._ascii_btn)

        self._mermaid_btn = QPushButton("Mermaid")
        self._mermaid_btn.setCheckable(True)
        self._mermaid_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mermaid_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mermaid_btn.setStyleSheet(btn_style)
        self._mermaid_btn.clicked.connect(lambda: self._switch_view("mermaid"))
        header.addWidget(self._mermaid_btn)

        body.addLayout(header)

        # Просмотрщик: DiagramView из ui/mermaid_view.py.
        # show_ascii работает всегда; show_mermaid — только после
        # enable_mermaid() == True.
        from ui.mermaid_view import DiagramView

        self._view = DiagramView()
        self._view.show_ascii(self._ascii)
        body.addWidget(self._view)

        if self._mermaid:
            try:
                self._mermaid_enabled = bool(self._view.enable_mermaid())
            except Exception as e:
                logger.debug(f"ChatDiagramItem: mermaid недоступен: {e}")
                self._mermaid_enabled = False

        if not self._mermaid_enabled:
            self._mermaid_btn.setEnabled(False)
            self._mermaid_btn.setToolTip(
                "Mermaid недоступен: нужен QWebEngine и ui/web_editor/vendor/mermaid.min.js."
            )
        if not self._mermaid_enabled and self._mermaid:
            logger.info(
                "ChatDiagramItem: mermaid не активирован — проверьте "
                "`make download-mermaid` и PySide6.QtWebEngineWidgets"
            )

    def _switch_view(self, kind: str) -> None:
        if kind == "mermaid":
            if not self._mermaid_enabled:
                self._ascii_btn.setChecked(True)
                self._mermaid_btn.setChecked(False)
                return
            self._ascii_btn.setChecked(False)
            self._mermaid_btn.setChecked(True)
            try:
                self._view.show_mermaid(self._mermaid)
            except Exception as e:
                logger.warning(f"ChatDiagramItem: mermaid render failed: {e}")
                self._ascii_btn.setChecked(True)
                self._mermaid_btn.setChecked(False)
                self._view.show_ascii(self._ascii)
        else:
            self._mermaid_btn.setChecked(False)
            self._ascii_btn.setChecked(True)
            self._view.show_ascii(self._ascii)


# ============================================================================
# ChatHypothesisItem: гипотеза исследования (id, statement, статус)
# ============================================================================


_HYPOTHESIS_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "open": ("#4a90e2", "🟦"),
    "verified": ("#4caf50", "✅"),
    "refuted": ("#e57373", "❌"),
    "partial": ("#ffb74d", "🟨"),
    "inconclusive": ("#888888", "⚪"),
}


class ChatHypothesisItem(QWidget):
    """Виджет одной гипотезы исследования.

    Используется в research-режиме: агент записывает гипотезу через
    `record_hypothesis` → в ленте чата появляется виджет; после проверки
    `update_hypothesis` обновляет цвет/иконку/доказательство.
    """

    def __init__(
        self,
        hypothesis_id: str,
        statement: str,
        status: str = "open",
        evidence: str = "",
        verification_method: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._id = hypothesis_id or "?"
        self._statement = statement or ""
        self._status = status or "open"
        self._evidence = evidence or ""
        self._verification = verification_method or ""

        container = QFrame(self)
        container.setObjectName("_hypothesis_item")
        self._container = container

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addWidget(container)

        body = QVBoxLayout(container)
        body.setContentsMargins(10, 6, 10, 6)
        body.setSpacing(3)

        self._header = QLabel()
        self._header.setTextFormat(Qt.TextFormat.RichText)
        self._header.setWordWrap(True)
        self._header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self._header)

        self._detail = QLabel()
        self._detail.setTextFormat(Qt.TextFormat.RichText)
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self._detail)

        self._refresh()

    def update_state(
        self,
        status: str,
        evidence: str = "",
        verification_method: str = "",
    ) -> None:
        """Обновляет статус/доказательство гипотезы (из `update_hypothesis`)."""
        self._status = status or "open"
        if evidence:
            self._evidence = evidence
        if verification_method:
            self._verification = verification_method
        self._refresh()

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _refresh(self) -> None:
        color, icon = _HYPOTHESIS_STATUS_STYLES.get(self._status, ("#888888", "•"))

        stmt = self._statement
        if len(stmt) > 300:
            stmt = stmt[:297] + "…"

        self._header.setText(
            f'<span style="color: {color}; font-weight: 700;">{icon}</span> '
            f'<span style="color: #888; font-size: 11px;">[{self._escape(self._id)}]</span> '
            f'<span style="color: #d4d4d4; font-weight: 600;">{self._escape(stmt)}</span>'
        )

        if self._status == "open" and self._verification:
            verification = self._verification
            if len(verification) > 220:
                verification = verification[:217] + "…"
            self._detail.setText(
                f'<span style="color: #7a7a8a;">Проверить: {self._escape(verification)}</span>'
            )
            self._detail.setVisible(True)
        elif self._evidence:
            evidence = self._evidence
            if len(evidence) > 220:
                evidence = evidence[:217] + "…"
            self._detail.setText(
                f'<span style="color: #9a9a9a;">Доказательство: {self._escape(evidence)}</span>'
            )
            self._detail.setVisible(True)
        else:
            self._detail.setVisible(False)

        self._detail.setStyleSheet(
            "font-size: 11px; font-family: 'Consolas','Cascadia Mono',monospace; padding: 0;"
        )
        self._container.setStyleSheet(
            "QFrame#_hypothesis_item {"
            " background: rgba(120, 120, 140, 0.06);"
            f" border-left: 3px solid {color};"
            " border-radius: 6px;"
            " padding: 0;"
            "}"
        )
