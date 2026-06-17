import yaml
from pathlib import Path
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

    def get_prompt(self, mode: str) -> str:
        """Возвращает системный промт для режима (chat, plan, agent)."""
        if mode in self._cache:
            return self._cache[mode]
        file_path = self.prompts_dir / f"{mode}.md"
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8").strip()
            self._cache[mode] = content
            return content
        # fallback на дефолтный (загруженный при создании)
        return self._cache.get(mode, "")
