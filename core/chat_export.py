"""Экспорт чата в Markdown и HTML (9.6.3).

Собирает историю из `ChatMessage` (session_store) или из списка сообщений
ChatPanel. Не зависит от UI: чистые функции, тестируемые без Qt.

Markdown — читаемый «как есть», с фронтматтером (дата, модель, профиль).
HTML — автономный файл (inline CSS), пригодный для печати/архива.
"""

from __future__ import annotations

from datetime import datetime
from html import escape as html_escape

from agents.ollama_client import ChatMessage


def export_to_markdown(
    messages: list[ChatMessage],
    *,
    title: str = "OlliDesk — чат",
    model: str = "",
    mode: str = "",
    profile: str = "",
    project: str = "",
) -> str:
    """Собирает Markdown-экспорт чата.

    Args:
        messages: История сообщений (роли user/assistant/tool/system).
        title: Заголовок документа (H1).
        model: Имя модели (в шапке).
        mode: Режим (chat/plan/agent).
        profile: Профиль (Разработка/Исследование/Обучение).
        project: Имя проекта (если есть).

    Returns:
        Markdown-строка.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"# {title}", ""]

    meta_bits: list[str] = [f"Экспорт: {now}"]
    if project:
        meta_bits.append(f"Проект: `{project}`")
    if model:
        meta_bits.append(f"Модель: `{model}`")
    if mode:
        meta_bits.append(f"Режим: {mode}")
    if profile:
        meta_bits.append(f"Профиль: {profile}")
    lines.extend([" · ".join(meta_bits), ""])
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg.role
        content = (msg.content or "").strip()
        if not content and role != "tool":
            continue

        if role == "user":
            lines.append("### 👤 Пользователь")
            lines.append("")
            lines.append(content)
            lines.append("")
        elif role == "assistant":
            lines.append("### 🤖 Ассистент")
            lines.append("")
            lines.append(content)
            lines.append("")
            # Tool calls — краткая сводка (без аргументов, чтобы не
            # раздувать файл; модель видит их в диалоге, экспорт — для
            # архива).
            if msg.tool_calls:
                lines.append("_Вызовы инструментов:_")
                for tc in msg.tool_calls:
                    lines.append(f"- `{tc.name}`")
                lines.append("")
        elif role == "tool":
            # Tool-сообщения — в сворачиваемый блок.
            name = msg.name or "tool"
            preview = content[:2000]
            if len(content) > 2000:
                preview += f"\n… [ещё {len(content) - 2000} символов]"
            lines.append(f"<details><summary>🔧 {name}</summary>")
            lines.append("")
            lines.append("```")
            lines.append(preview)
            lines.append("```")
            lines.append("</details>")
            lines.append("")
        elif role == "system":
            # Системные — выделяем как блок.
            preview = content[:500]
            if len(content) > 500:
                preview += "…"
            lines.append(f"> _Система:_ {preview}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_HTML_STYLE = """
body { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
       max-width: 900px; margin: 24px auto; padding: 0 16px;
       color: #1e1e1e; background: #fafafa; line-height: 1.55; }
h1 { font-size: 24px; border-bottom: 2px solid #d4a373; padding-bottom: 8px; }
h3 { font-size: 15px; margin: 18px 0 6px; }
.meta { color: #666; font-size: 12px; margin-bottom: 16px; }
.msg { margin: 12px 0; padding: 10px 14px; border-radius: 8px; }
.msg.user { background: #f0f4f8; border-left: 3px solid #1976d2; }
.msg.assistant { background: #fdfaf5; border-left: 3px solid #d4a373; }
.msg.tool { background: #f5f5f5; border-left: 3px solid #888;
            font-family: 'Consolas', monospace; font-size: 12px; }
.msg.system { background: #fff8e1; border-left: 3px solid #e5c07b;
              font-style: italic; font-size: 12px; color: #555; }
.role { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
pre { background: #2b2b2b; color: #eee; padding: 10px;
      border-radius: 6px; overflow-x: auto; font-size: 12px; }
code { background: #eee; padding: 1px 4px; border-radius: 3px;
       font-family: 'Consolas', monospace; font-size: 13px; }
pre code { background: transparent; padding: 0; color: inherit; }
details { margin: 8px 0; }
summary { cursor: pointer; color: #6ba3d6; }
"""


def _plain_to_html(text: str) -> str:
    """Минимальный text→HTML (абзацы + pre-блоки по ```).

    Без markdown-парсера: экспорт должен работать даже если pygments
    недоступен. Код — в `<pre>`, остальное — в `<p>` с `<br>`.
    """
    if not text:
        return ""
    parts: list[str] = []
    in_code = False
    code_buf: list[str] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        if not para_buf:
            return
        joined = "<br>".join(html_escape(line) for line in para_buf)
        parts.append(f"<p>{joined}</p>")
        para_buf.clear()

    def flush_code() -> None:
        if not code_buf:
            return
        joined = "\n".join(code_buf)
        parts.append(f"<pre><code>{html_escape(joined)}</code></pre>")
        code_buf.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_para()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
        else:
            if not stripped:
                flush_para()
            else:
                para_buf.append(line)

    if in_code:
        flush_code()
    flush_para()
    return "".join(parts)


def export_to_html(
    messages: list[ChatMessage],
    *,
    title: str = "OlliDesk — чат",
    model: str = "",
    mode: str = "",
    profile: str = "",
    project: str = "",
) -> str:
    """Собирает автономный HTML-экспорт чата."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta_bits: list[str] = [f"Экспорт: {now}"]
    if project:
        meta_bits.append(f"Проект: {html_escape(project)}")
    if model:
        meta_bits.append(f"Модель: {html_escape(model)}")
    if mode:
        meta_bits.append(f"Режим: {html_escape(mode)}")
    if profile:
        meta_bits.append(f"Профиль: {html_escape(profile)}")

    roles = {
        "user": ("👤 Пользователь", "user"),
        "assistant": ("🤖 Ассистент", "assistant"),
        "tool": ("🔧 Инструмент", "tool"),
        "system": ("⚙ Система", "system"),
    }

    body: list[str] = []
    for msg in messages:
        content = (msg.content or "").strip()
        if not content:
            continue
        label, cls = roles.get(msg.role, (msg.role, "assistant"))
        if msg.role == "tool":
            name = msg.name or "tool"
            label = f"🔧 {html_escape(name)}"
        body.append(
            f'<div class="msg {cls}">'
            f'<div class="role">{label}</div>'
            f"{_plain_to_html(content)}"
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{html_escape(title)}</title>
  <style>{_HTML_STYLE}</style>
</head>
<body>
  <h1>{html_escape(title)}</h1>
  <div class="meta">{' · '.join(meta_bits)}</div>
  {''.join(body)}
</body>
</html>
"""


__all__ = ["export_to_html", "export_to_markdown"]