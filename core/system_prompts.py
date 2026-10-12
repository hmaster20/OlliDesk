"""Менеджер системных промптов режимов (chat / plan / agent).

Промпт берётся из `<проект>/.ollidesk/system_prompts/<mode>.md`, иначе из
`~/.ollidesk/system_prompts/<mode>.md`. Поддерживается подстановка переменных
вида `{{key}}`. Панель чата передаёт такие значения (`ChatPanel._template_context`):

- `{{project_name}}` — имя открытого проекта (каталога);
- `{{language}}` — язык ответа (по умолчанию «русский»);
- `{{project_root}}` — абсолютный путь к корню проекта;
- `{{project_map}}` — карта структуры проекта (дерево каталогов и файлов).
"""

import re
from pathlib import Path

from loguru import logger

from core.utils import get_app_data_dir

DEFAULT_PROMPTS: dict[str, str] = {
    "chat.md": (
        "Ты — полезный AI-ассистент проекта. Отвечай на русском языке,\n"
        "структурированно (Markdown: заголовки, списки, блоки кода), а не сплошным текстом."
    ),
    "plan.md": (
        "Ты — AI-агент, работающий в режиме Плана.\n"
        "Тебе доступны только read-only инструменты (чтение файлов, поиск).\n"
        "Сначала изучи структуру и код проекта инструментами (list_directory, read_file,\n"
        "search_codebase), опирайся на свои знания, и только при сомнении используй web_search.\n"
        "Не выводи JSON-план текстом — сразу вызывай нужные инструменты через API.\n"
        "Итоговый план оформляй в Markdown: заголовки, нумерованные списки, блоки кода.\n"
        "Отвечай на русском языке."
    ),
    "agent.md": (
        "Ты — AI-агент, работающий в режиме Агента.\n"
        "Тебе доступны все инструменты: чтение, запись, Git, поиск.\n"
        "Изучай код проекта инструментами, применяй свои знания для анализа,\n"
        "а web_search используй только для внешних/публичных сведений и при сомнении.\n"
        "Отвечай на русском языке, структурированно (Markdown), с блоком кода ```язык."
    ),
}


class SystemPromptManager:
    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir = prompts_dir or (get_app_data_dir() / "system_prompts")
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()
        self._cache: dict[str, str] = {}

    def _ensure_defaults(self) -> None:
        # Создать файлы с дефолтными промтами, если их нет
        for filename, content in DEFAULT_PROMPTS.items():
            file_path = self.prompts_dir / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                logger.info(f"Создан дефолтный системный промт: {filename}")

    def clear_cache(self) -> None:
        """Сбрасывает кэш промптов (после правки файлов на диске)."""
        self._cache.clear()

    @staticmethod
    def default_prompt(mode: str) -> str:
        """Возвращает заводской текст промпта для режима или пустую строку."""
        return DEFAULT_PROMPTS.get(f"{mode}.md", "")

    @staticmethod
    def render_template(template: str, context: dict) -> str:
        """Публичная шаблонизация {{key}} (для превью в редакторе промптов)."""
        return SystemPromptManager._render(template, context)

    def get_prompt(self, mode: str, project_root: Path | None = None, context: dict | None = None) -> str:
        """
        Возвращает системный промт для режима (chat, plan, agent).
        Сначала ищет в проекте (если указан), затем глобально.
        Поддерживает шаблонизацию переменных вида {{key}}.
        """
        if project_root is not None and not isinstance(project_root, Path):
            project_root = Path(project_root)
        # 1. Попытка загрузить из проекта
        prompt = self._load_prompt_from_path(mode, project_root) if project_root else None
        if prompt is None:
            # 2. Глобальный
            prompt = self._load_prompt_from_path(mode, None) or ""
        if not prompt:
            # 3. Fallback (дефолт из кэша или встроенный)
            prompt = self._cache.get(mode, "")
        # 4. Рендеринг шаблона
        if prompt and context:
            prompt = self._render(prompt, context)
        return prompt

    def _load_prompt_from_path(self, mode: str, project_root: Path | None) -> str | None:
        """Загружает промт из указанной папки (глобальной или проектной)."""
        if project_root is not None and not isinstance(project_root, Path):
            project_root = Path(project_root)
        if project_root:
            dir_path = project_root / ".ollidesk" / "system_prompts"
        else:
            dir_path = self.prompts_dir
        file_path = dir_path / f"{mode}.md"
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8").strip()
            # Кэшируем по полному пути для производительности
            root_str = str(project_root) if project_root is not None else "global"
            cache_key = f"{root_str}:{mode}"
            self._cache[cache_key] = content
            return content
        return None

    @staticmethod
    def _render(template: str, context: dict) -> str:
        """Заменяет {{key}} на значения из контекста."""

        def repl(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            return str(context.get(key, match.group(0)))

        return re.sub(r'\{\{\s*(\w+)\s*\}\}', repl, template)
