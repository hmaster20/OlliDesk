import re
import yaml
from pathlib import Path
from typing import Optional
from loguru import logger
from core.utils import get_app_data_dir

class SystemPromptManager:
    def __init__(self):
        self.prompts_dir = get_app_data_dir() / "system_prompts"
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()
        self._cache = {}

    def _ensure_defaults(self):
        # Создать файлы с дефолтными промтами, если их нет
        defaults = {
            "chat.md": "Ты — полезный AI-ассистент. Отвечай на русском языке, будь лаконичен и полезен.",
            "plan.md": (
                "Ты — AI-агент, работающий в режиме Плана.\n"
                "Тебе доступны только read-only инструменты (чтение файлов, поиск).\n"
                "Не выводи JSON-план текстом — сразу вызывай нужные инструменты через API.\n"
                "Отвечай на русском языке."
            ),
            "agent.md": (
                "Ты — AI-агент, работающий в режиме Агента.\n"
                "Тебе доступны все инструменты: чтение, запись, Git, поиск.\n"
                "Используй их для автономного выполнения задачи.\n"
                "Отвечай на русском языке."
            )
        }
        for filename, content in defaults.items():
            file_path = self.prompts_dir / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                logger.info(f"Создан дефолтный системный промт: {filename}")

    def get_prompt(self, mode: str, project_root: Optional[Path] = None, context: Optional[dict] = None) -> str:
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

    def _load_prompt_from_path(self, mode: str, project_root: Optional[Path]) -> Optional[str]:
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

    def _render(self, template: str, context: dict) -> str:
        """Заменяет {{key}} на значения из контекста."""
        def repl(match):
            key = match.group(1).strip()
            return str(context.get(key, match.group(0)))
        return re.sub(r'\{\{\s*(\w+)\s*\}\}', repl, template)
