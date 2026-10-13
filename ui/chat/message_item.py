"""Виджет одного сообщения в чате."""

import html as html_mod
import re

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

_CODE_FENCE_RE = re.compile(r"```[ \t]*(?P<lang>[A-Za-z0-9_+#.-]*)[ \t]*\n?(?P<code>.*?)```", re.DOTALL)
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
    """Ширина отступа строки в пробелах (табы раскрываются по 4)."""
    expanded = raw.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _collect_list_items(lines: list[str], start: int) -> tuple[list[tuple[int, bool, str]], int]:
    """Собирает подряд идущие пункты списка: (отступ, нумерованный, текст).

    Пустые строки между пунктами не прерывают список (loose list), если за ними
    снова идёт пункт списка — иначе нумерация сбрасывалась бы на «1».
    """
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
    """Рендерит один уровень списка, разворачивая вложенные уровни в <li>."""
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
    """Строит вложенные <ul>/<ol> из плоского списка с отступами."""
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
    """Разбивает строку markdown-таблицы на ячейки (без крайних `|`)."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    """True если строка — разделитель markdown-таблицы (`|---|:--:|`)."""
    cells = _split_table_row(line)
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(cell) for cell in cells)


def _is_table_start(lines: list[str], i: int) -> bool:
    """True если с позиции `i` начинается таблица (заголовок + разделитель)."""
    return (
        "|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1] and _is_table_separator(lines[i + 1])
    )


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    """Рендерит markdown-таблицу в HTML `<table>` для QTextBrowser."""
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
    """Разбирает текст на блочные элементы: заголовки, списки, цитаты, абзацы."""
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
            i += 2  # пропускаем заголовок и разделитель
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
    """Преобразует markdown-ответ модели в HTML для QTextBrowser.

    Поддерживает заголовки, списки (в т.ч. чек-листы), таблицы, цитаты, hr,
    блоки кода с подсветкой, инлайн-код, ссылки, жирный/курсив/зачёркнутый и
    абзацные отступы. Незакрытый блок кода (типичный при стриминге)
    достраивается до конца текста, чтобы вывод не «ломался» на каждом токене.
    """
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
    # Блоки кода, оказавшиеся внутри строки (не на отдельной строке).
    body = re.sub(r"\x00CB(\d+)\x00", lambda m: code_blocks[int(m.group(1))], body)
    return f'<div style="line-height:1.5;">{body}</div>'


class ChatActivityItem(QWidget):
    """Серая строка-событие о ходе работы агента (журнал фаз).

    Отдельный немаркированный элемент ленты: «🤔 Модель думает…»,
    «🔍 Ищу в кодовой базе…», «🔧 Чтение файла: path=…» и т. п.
    """

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
    """Блок источников RAG с кликабельными ссылками.

    Каждый источник — кнопка-ссылка вида ``file:строка``; клик испускает
    ``source_activated`` (относительный путь, строка), а панель открывает
    файл в редакторе.
    """

    source_activated = Signal(str, int)

    def __init__(self, sources: list[tuple[str, int, int]], parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 2, 8, 2)
        layout.setSpacing(0)

        header = QLabel("📚 Источники из проекта (нажмите, чтобы открыть):")
        header.setStyleSheet("color: #8f8f8f; font-size: 12px; font-style: italic; background: transparent;")
        layout.addWidget(header)

        for path, start, end in sources:
            btn = QPushButton(format_source_label(path, start, end))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton { color: #6ba3d6; font-size: 12px; text-align: left; "
                "border: none; background: transparent; padding: 0; }"
                "QPushButton:hover { text-decoration: underline; color: #8bc0f0; }"
            )
            btn.clicked.connect(lambda _=False, p=path, s=start: self.source_activated.emit(p, s))
            layout.addWidget(btn)


def format_source_label(file_path: str, start: int, end: int) -> str:
    """Формирует подпись источника RAG вида ``file:строка`` или ``file:start-end``."""
    return f"{file_path}:{start}-{end}" if end != start else f"{file_path}:{start}"


class ChatMessageItem(QWidget):
    """Виджет одного сообщения в чате.

    Текст рендерится в QTextBrowser (выделение мышью, Ctrl+C, кликабельные
    ссылки, встроенное контекстное меню). Кнопка 📋 под текстом сообщения
    копирует исходный markdown в буфер обмена.
    """

    def __init__(
        self,
        role: str,
        content: str,
        thinking: str = "",
        mode_name: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.thinking = thinking
        self.think_label: QLabel | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("_bubble")
        is_user = role == "user"
        is_assistant = role == "assistant"
        is_tool = role in ("tool", "system")

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
        bubble_layout.setSpacing(2)
        self.bubble_layout = bubble_layout
        self.text_color = text_color

        # --- Заголовок: имя роли (для assistant/tool) ---
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        if not is_tool and not is_user:
            header = f"Ассистент ({mode_name})" if is_assistant else role
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

        if thinking:
            think_label = QLabel(thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            bubble_layout.addWidget(think_label)
            self.think_label = think_label

        # --- Текст сообщения ---
        self.content_browser = QTextBrowser()
        self.content_browser.setOpenExternalLinks(True)
        self.content_browser.setReadOnly(True)
        self.content_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.content_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.content_browser.document().setDocumentMargin(0)
        self.content_browser.setStyleSheet(
            f"QTextBrowser {{"
            f" font-family: 'Segoe UI', Roboto, sans-serif;"
            f" font-size: 14px; color: {text_color};"
            f" background: transparent; border: none; padding: 0;"
            f" }}"
        )
        bubble_layout.addWidget(self.content_browser)

        # --- Кнопка «копировать» под текстом сообщения ---
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

        self._set_content(content)

        inner = QHBoxLayout()
        inner.setContentsMargins(6, 0, 6, 0)
        if is_user:
            inner.addStretch()
            inner.addWidget(bubble, 1)
            inner.addStretch()
        else:
            inner.addWidget(bubble, 1)

        outer.addLayout(inner)
        self.bubble = bubble

    # ---------------------------------------------------------
    # Контент и высота
    # ---------------------------------------------------------

    def _set_content(self, text: str):
        html = self._render_markdown_to_html(text)
        self.content_browser.setHtml(html)
        # Обновляем высоту после смены контента.
        self._adjust_browser_height()

    def _adjust_browser_height(self):
        """Подгоняет высоту QTextBrowser под содержимое при текущей ширине."""
        doc: QTextDocument = self.content_browser.document()
        viewport_width = self.content_browser.viewport().width()
        if viewport_width <= 0:
            # Виджет ещё не отрисован — выйдем, resizeEvent вызовет повторно.
            return
        doc.setTextWidth(viewport_width)
        height = int(doc.size().height()) + 4
        self.content_browser.setFixedHeight(max(height, 22))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_browser_height()

    # ---------------------------------------------------------
    # Копирование
    # ---------------------------------------------------------

    def _copy_to_clipboard(self):
        QApplication.clipboard().setText(self.content)

    # ---------------------------------------------------------
    # Обновление во время стриминга
    # ---------------------------------------------------------

    def update_content(self, new_content: str):
        self.content = new_content
        self._set_content(new_content)

    def update_thinking(self, new_thinking: str):
        self.thinking = new_thinking
        if self.think_label is None:
            think_label = QLabel(new_thinking)
            think_label.setStyleSheet(
                f"font-size: 12px; color: {self.text_color}; opacity: 0.6; font-style: italic;"
            )
            think_label.setWordWrap(True)
            index = self.bubble_layout.indexOf(self.content_browser)
            self.bubble_layout.insertWidget(index, think_label)
            self.think_label = think_label
        else:
            self.think_label.setText(new_thinking)

    # ---------------------------------------------------------
    # Markdown → HTML
    # ---------------------------------------------------------

    def _render_markdown_to_html(self, text: str) -> str:
        return render_markdown(text)

class ChatHypothesisItem(QWidget):
    """Виджет отображения гипотезы исследования в ленте чата.

    Создаётся при `record_hypothesis` и **обновляется на месте** при
    `update_hypothesis`. Панель держит словарь `{hypothesis_id: widget}`
    для in-place обновления — иначе лента засорилась бы парными
    «создана/обновлена».

    Стиль: компактная строка с эмодзи 💡, id гипотезы, статусным бейджем
    и подписью (метод проверки для открытых, доказательство для закрытых).
    """

    _STATUS_STYLES: dict[str, tuple[str, str]] = {
        "open": ("#4a90e2", "🟦 открыта"),
        "verified": ("#4caf50", "✅ подтверждена"),
        "refuted": ("#e57373", "❌ опровергнута"),
        "partial": ("#ffb74d", "🟨 частично"),
        "inconclusive": ("#888888", "⚪ не проверена"),
    }

    def __init__(
        self,
        hypothesis_id: str,
        statement: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.hypothesis_id = hypothesis_id
        self._status = "open"
        self._verification_method = ""
        self._evidence = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 4, 8, 4)
        layout.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        self.header_label = QLabel()
        self.header_label.setTextFormat(Qt.TextFormat.RichText)
        header_row.addWidget(self.header_label)
        header_row.addStretch()
        layout.addLayout(header_row)

        self.body_label = QLabel(statement)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.body_label.setStyleSheet(
            "color: #d4d4d4; font-size: 13px; font-weight: 600; padding: 0;"
        )
        layout.addWidget(self.body_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_label.setStyleSheet(
            "color: #9a9a9a; font-size: 12px; font-style: italic; padding: 0;"
        )
        layout.addWidget(self.detail_label)

        self._refresh()

    def update_state(
        self,
        status: str,
        evidence: str = "",
        verification_method: str = "",
    ) -> None:
        """Обновляет статус/доказательство и перерисовывает виджет."""
        self._status = status or self._status
        if evidence:
            self._evidence = evidence
        if verification_method:
            self._verification_method = verification_method
        self._refresh()

    def _refresh(self) -> None:
        color, label = self._STATUS_STYLES.get(
            self._status, ("#888888", self._status)
        )
        self.header_label.setText(
            f'<span style="color: #c0c0c0; font-size: 12px; font-weight: 600;">'
            f"💡 Гипотеза [{self.hypothesis_id}]</span> "
            f'<span style="color: {color}; font-size: 12px;">· {label}</span>'
        )

        if self._status == "open":
            text = (
                f"Проверить: {self._verification_method}"
                if self._verification_method
                else ""
            )
        else:
            text = (
                f"Доказательство: {self._evidence}" if self._evidence else ""
            )
        self.detail_label.setText(text)
        self.detail_label.setVisible(bool(text))