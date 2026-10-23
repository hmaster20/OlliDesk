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

import html as html_mod
import json
import re

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
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


def _render_code_block(code: str, lang: str) -> str:
    """Подсвечивает код в блоке (pygments) или экранирует как есть."""
    code = code.rstrip("\n")
    highlighted: str
    try:
        from pygments import highlight as _highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer

        formatter = HtmlFormatter(style="monokai", nowrap=True, noclasses=True)
        lexer = get_lexer_by_name(lang, stripall=True) if lang else guess_lexer(code)
        highlighted = _highlight(code, lexer, formatter).rstrip("\n")
    except Exception:
        highlighted = html_mod.escape(code)

    header = ""
    if lang:
        header = (
            '<div style="background:#2f2f3c; padding:3px 12px;'
            " font-family:'Cascadia Mono','Consolas','Courier New',monospace;"
            ' font-size:11px; color:#9a9ab0; border-bottom:1px solid #3a3a48;">'
            f"{html_mod.escape(lang)}</div>"
        )

    return (
        '<div style="background:#23232c; border:1px solid #3a3a48; border-radius:8px;'
        ' margin:8px 0; overflow:hidden;">' + header + '<pre style="margin:0; padding:8px 12px;'
        " font-family:'Cascadia Mono','Consolas','Courier New',monospace;"
        ' font-size:13px; color:#e0e0e0;"><code>' + highlighted + "</code></pre></div>"
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


def _collect_list_items(
    lines: list[str], start: int
) -> tuple[list[tuple[int, bool, str]], int]:
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
        "|" in lines[i]
        and i + 1 < len(lines)
        and "|" in lines[i + 1]
        and _is_table_separator(lines[i + 1])
    )


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    width = len(header)
    parts = [
        '<table border="1" cellspacing="0" cellpadding="5"'
        ' style="border-collapse:collapse; margin:10px 0; border-color:#4a4a55;">'
    ]
    parts.append("<tr>")
    for cell in header:
        parts.append(
            '<th style="background:#2f2f3c; color:#e0e0e0; padding:5px 8px;'
            f' text-align:left;">{_inline(cell)}</th>'
        )
    parts.append("</tr>")
    for row in rows:
        padded = (row + [""] * width)[:width]
        parts.append("<tr>")
        for cell in padded:
            parts.append(f'<td style="padding:5px 8px;">{_inline(cell)}</td>')
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


def _render_blocks(text: str, code_blocks: list[str]) -> str:
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
            out.append(_render_table(header, rows))
            continue

        para: list[str] = []
        while i < n and lines[i].strip() and not _is_block_start(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append('<p style="margin:8px 0;">' + "<br>".join(_inline(p) for p in para) + "</p>")

    return "\n".join(out)


def render_markdown(text: str) -> str:
    """Преобразует markdown-ответ модели в HTML для QTextBrowser."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.count("```") % 2 == 1:
        text += "\n```"
    code_blocks: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        lang = (m.group("lang") or "").strip()
        code_blocks.append(_render_code_block(m.group("code"), lang))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    stashed = _CODE_FENCE_RE.sub(_stash, text)
    body = _render_blocks(stashed, code_blocks)
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
        label.setStyleSheet(
            "color: #8f8f8f; font-size: 12px; font-style: italic; background: transparent;"
        )
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
            btn.clicked.connect(
                lambda _=False, p=path, s=start: self.source_activated.emit(p, s)
            )
            body_layout.addWidget(btn)

        self._body.setVisible(False)
        outer.addWidget(self._body)

    def _toggle_body(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        arrow = "▲ свернуть" if self._expanded else "▼ развернуть"
        self._header_btn.setText(
            f"📚 Источники проекта ({len(self._sources)}) · {arrow}"
        )


def format_source_label(file_path: str, start: int, end: int) -> str:
    """Подпись источника RAG вида ``file:строка`` или ``file:start-end``."""
    return f"{file_path}:{start}-{end}" if end != start else f"{file_path}:{start}"


# ============================================================================
# 4.8.B: части сообщения
# ============================================================================


class ReasoningWidget(QWidget):
    """Сворачиваемый блок размышлений модели (ReasoningPart).

    Во время стриминга развёрнут на первом токене и остаётся развёрнутым,
    пока пользователь не свернёт вручную. `collapse()` вызывается в
    `ChatMessageItem.finalize()` — после завершения ответа блок сам
    схлопывается, чтобы не занимать визуальное место.
    """

    _PREVIEW_CHARS = 0  # 0 = показываем весь текст; заголовок только со счётчиком

    def __init__(self, initial_text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = initial_text or ""
        self._expanded = bool(self._text)

        container = QFrame(self)
        container.setObjectName("_reasoning_widget")
        container.setStyleSheet(
            "QFrame#_reasoning_widget {"
            " background: rgba(120, 120, 140, 0.06);"
            " border: 1px solid #3a3a48;"
            " border-radius: 6px;"
            " padding: 4px 8px; }"
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
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: #888; font-size: 11px; padding: 0 4px; }"
            " QPushButton:hover { color: #ccc; text-decoration: underline; }"
        )
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
            " background: transparent; border: none; color: #a8a8b8;"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 12px; padding: 2px 0; }"
        )
        self._browser.setMaximumHeight(240)
        body.addWidget(self._browser)

        self._refresh_browser()
        self._apply_visibility()

    def _refresh_browser(self) -> None:
        """Пересобирает HTML из `_text` (курсив, серый)."""
        safe = (
            self._text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self._browser.setHtml(
            f'<div style="color:#a8a8b8; font-style:italic;">{safe}</div>'
        )
        # Авто-высота: ~16px на строку, потолок 240.
        line_count = self._text.count("\n") + 1
        h = min(max(40, line_count * 16 + 8), 240)
        self._browser.setFixedHeight(h)
        self._refresh_header()

    def _refresh_header(self) -> None:
        n = len(self._text)
        unit = "символ" if n % 10 == 1 and n % 100 != 11 else (
            "символа" if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14) else "символов"
        )
        self._header_label.setText(
            f'<span style="color: #7a7a8a; font-size: 11px; font-weight: 600;">'
            f"💭 Размышления</span> "
            f'<span style="color: #6a6a7a; font-size: 11px;">· {n} {unit}</span>'
        )

    def _apply_visibility(self) -> None:
        self._browser.setVisible(self._expanded)
        self._toggle_btn.setText("▲ Свернуть" if self._expanded else "▼ Развернуть")

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._apply_visibility()

    # --- Публичный API ---

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
        """Свернуть блок (вызывается на finish)."""
        if self._expanded:
            self._expanded = False
            self._apply_visibility()


class TextWidget(QTextBrowser):
    """Markdown-текст ответа модели с авто-высотой под содержимое."""

    def __init__(self, initial_text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._raw_text = ""
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            "QTextBrowser {"
            " font-family: 'Segoe UI', Roboto, sans-serif;"
            " font-size: 14px; color: #e0e0e0;"
            " background: transparent; border: none; padding: 0;"
            " }"
        )
        if initial_text:
            self.set_text(initial_text)

    def set_text(self, text: str) -> None:
        self._raw_text = text or ""
        self.setHtml(render_markdown(self._raw_text))
        self._adjust_height()

    def append_text(self, token: str) -> None:
        if not token:
            return
        self._raw_text += token
        self.setHtml(render_markdown(self._raw_text))
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
    return (clean[: limit - 1] + "…", True)

def _escape_diff(text: str) -> str:
    """Экранирует HTML-спецсимволы для diff-сводки."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

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
    open_in_editor_requested = Signal(str)   # path
    revert_requested = Signal(dict)          # {"path", "original_content", "is_new_file"}

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

    # Палитра состояний (фон/граница/иконка статуса).
    _STATE_STYLES: dict[str, tuple[str, str, str]] = {
        # pending: серый
        "pending": ("rgba(120,120,140,0.06)", "#3a3a48", "⏳"),
        # ok: зелёный
        "ok": ("rgba(80,160,80,0.08)", "#4a6a4a", "✓"),
        # error: красный
        "error": ("rgba(200,80,80,0.10)", "#7a3a3a", "✗"),
    }

    def __init__(
        self,
        name: str,
        arguments_json: str,
        parent: QWidget | None = None,
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
        self._name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._name_label.setStyleSheet(
            "color: #8db1d6; font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 12px;"
        )
        header.addWidget(self._name_label, stretch=1)

        self._status_label = QLabel("⏳")
        self._status_label.setStyleSheet(
            "color: #7a7a8a; font-size: 13px; padding-left: 4px;"
        )
        self._status_label.setToolTip("Инструмент ещё не выполнен")
        header.addWidget(self._status_label)

        self._toggle_btn: QPushButton | None = None
        body.addLayout(header)

        # --- Preview результата (скрыт до set_result) ---
        self._preview_label = QLabel("")
        self._preview_label.setWordWrap(True)
        self._preview_label.setTextFormat(Qt.TextFormat.PlainText)
        self._preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._preview_label.setStyleSheet(
            "color: #a8a8b8; font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 2px 0 0 20px;"
        )
        self._preview_label.setVisible(False)
        body.addWidget(self._preview_label)

        # --- Полный текст (скрыт, раскрывается кнопкой) ---
        self._full_browser = QTextBrowser()
        self._full_browser.setReadOnly(True)
        self._full_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._full_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._full_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._full_browser.setStyleSheet(
            "QTextBrowser {"
            " background: rgba(0,0,0,0.15); border: none;"
            " color: #b8b8c8;"
            " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 4px 6px;"
            "}"
        )
        self._full_browser.setMaximumHeight(220)
        self._full_browser.setVisible(False)
        body.addWidget(self._full_browser)

        self._apply_state_style()

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
        if (
            not is_error
            and isinstance(metadata, dict)
            and "diff" in metadata
            and "path" in metadata
        ):
            self._render_diff_mode()
        else:
            self._update_preview()

    # ------------------------------------------------------------------
    # Внутренние
    # ------------------------------------------------------------------

    def _apply_state_style(self) -> None:
        bg, border, icon = self._STATE_STYLES[self._state]
        self._container.setStyleSheet(
            "QFrame#_tool_card {"
            f" background: {bg};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 0;"
            "}"
        )
        self._status_label.setText(icon)
        if self._state == "pending":
            self._status_label.setStyleSheet(
                "color: #7a7a8a; font-size: 13px; padding-left: 4px;"
            )
            self._status_label.setToolTip("Инструмент ещё не выполнен")
        elif self._state == "ok":
            self._status_label.setStyleSheet(
                "color: #98c379; font-size: 13px; padding-left: 4px; font-weight: 700;"
            )
            self._status_label.setToolTip("Успешно")
        else:
            self._status_label.setStyleSheet(
                "color: #e06c75; font-size: 13px; padding-left: 4px; font-weight: 700;"
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
        self._preview_label.setStyleSheet(
            (
                "color: #e8a8a8;"
                if self._result_is_error
                else "color: #a8a8b8;"
            )
            + " font-family: 'Consolas','Cascadia Mono',monospace;"
            " font-size: 11px; padding: 2px 0 0 20px;"
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
                    full[:_MAX_RESULT_VIEW_CHARS]
                    + f"\n\n… [результат обрезан до {_MAX_RESULT_VIEW_CHARS} "
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
            self._toggle_btn.setText(
                "▾ Свернуть" if self._result_expanded else "▸ Показать полностью"
            )

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

        self._preview_label.setText(summary)
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setStyleSheet(
            "color: #d4d4d4; font-family: 'Segoe UI', sans-serif;"
            " font-size: 12px; padding: 4px 0 2px 20px;"
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

        # Кнопки: «📂 Открыть в редакторе» / «↩ Откатить».
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 2, 0, 0)
        btn_row.setSpacing(6)

        open_btn = QPushButton("📂 Открыть в редакторе")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444;"
            " border-radius: 10px; color: #6ba3d6; font-size: 11px;"
            " padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(107,163,214,0.15);"
            " color: #8bc0f0; }"
        )
        open_btn.clicked.connect(
            lambda _checked=False, p=path: self.open_in_editor_requested.emit(p)
        )
        btn_row.addWidget(open_btn)

        revert_btn = QPushButton("↩ Откатить")
        revert_btn.setToolTip(
            "Восстановить исходное содержимое файла (запись была уже сделана)"
        )
        revert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        revert_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        revert_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444;"
            " border-radius: 10px; color: #e5c07b; font-size: 11px;"
            " padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(229,192,123,0.15);"
            " color: #f0d494; }"
        )
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
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking
        self.mode_name = mode_name

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("_bubble")
        is_user = self.role == "user"
        is_assistant = self.role == "assistant"
        is_tool = self.role in ("tool", "system")

        if is_user:
            bg = "#3d3d3d"
            text_color = "#e0e0e0"
            border = "1px solid #555"
        elif is_assistant:
            bg = "transparent"
            text_color = "#e0e0e0"
            border = "none"
        else:
            bg = "transparent"
            text_color = "#999"
            border = "none"

        bubble.setStyleSheet(f"""
            QFrame#_bubble {{
                background-color: {bg};
                border-radius: 12px;
                padding: 6px 12px;
                border: {border};
            }}
        """)

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(4)
        self._bubble_layout = bubble_layout
        self.bubble = bubble
        self.text_color = text_color

        # Заголовок для assistant
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)
        if not is_tool and not is_user:
            header = f"Ассистент ({self.mode_name})" if is_assistant else self.role
            role_label = QLabel(header)
            role_label.setStyleSheet(f"""
                background-color: {bg};
                color: {text_color};
                font-weight: 600;
                font-size: 11px;
                padding: 2px 0 4px 0;
            """)
            header_row.addWidget(role_label)
        header_row.addStretch()
        bubble_layout.addLayout(header_row)

        # Сюда добавляются части (reasoning/text/tool_call) через _ensure_*.
        self._parts_container = QWidget()
        parts_layout = QVBoxLayout(self._parts_container)
        parts_layout.setContentsMargins(0, 0, 0, 0)
        parts_layout.setSpacing(4)
        self._parts_layout = parts_layout
        bubble_layout.addWidget(self._parts_container)

        # Кнопка «Копировать» под текстом
        if is_user or is_assistant:
            copy_row = QHBoxLayout()
            copy_row.setContentsMargins(0, 2, 0, 0)
            copy_row.setSpacing(4)
            copy_row.addStretch()
            copy_btn = QPushButton("Копировать")
            copy_btn.setToolTip("Копировать сообщение (markdown)")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            copy_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #555;"
                " border-radius: 10px; color: #888; font-size: 11px; padding: 1px 10px; }"
                " QPushButton:hover { background: rgba(128,128,128,0.2); color: #ddd; }"
                " QPushButton:pressed { background: rgba(128,128,128,0.35); }"
            )
            copy_btn.clicked.connect(self._copy_to_clipboard)
            copy_row.addWidget(copy_btn)
            self.copy_button = copy_btn
            bubble_layout.addLayout(copy_row)

        inner = QHBoxLayout()
        inner.setContentsMargins(6, 0, 6, 0)
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
            self._reasoning_widget = ReasoningWidget()
            self._parts_layout.addWidget(self._reasoning_widget)
        return self._reasoning_widget

    def _ensure_text_widget(self) -> TextWidget:
        if self._text_widget is None:
            self._text_widget = TextWidget()
            # [9.6.5] Убираем лишние вертикальные отступы `<p style="margin:8px 0">`
            # (первый/последний абзац в пузыре). Для assistant это не нужно
            # (там воздух между блоками уместен), а для user/коротких
            # системных сообщений — заметный разрыв до кнопки «Копировать».
            if self.role in ("user", "system"):
                self._text_widget.document().setDefaultStyleSheet(
                    "p { margin: 0; }"
                    " ul, ol { margin: 2px 0; }"
                    " pre { margin: 4px 0; }"
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
            logger.debug(
                f"append_reasoning для role={self.role!r} — игнорирую"
            )
            return
        self.thinking += token
        self._ensure_reasoning_widget().append_text(token)

    def append_tool_call(
        self, name: str, arguments_json: str
    ) -> "ToolCallCard | None":
        """[9.5.7] Добавляет карточку tool-call, возвращает её.

        Возвращённая карточка используется в `ChatPanel._on_tool_started`
        (сохраняется в `_active_tool_card`), а `_on_tool_executed`
        вызывает у неё `set_result(...)`.
        """
        if self.role != "assistant":
            logger.debug(
                f"append_tool_call для role={self.role!r} — игнорирую"
            )
            return None
        card = ToolCallCard(name, arguments_json)
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