"""Менеджер ролей и системных промтов."""
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel

from core.utils import get_app_data_dir


class RoleDefinition(BaseModel):
    """Описание роли."""
    id: str
    name: str
    icon: str = "🤖"
    description: str = ""
    system_prompt: str = ""

class RoleManager:
    """Управляет загрузкой ролей из ~/.ollidesk/roles/."""
    def __init__(self):
        self.roles_dir = get_app_data_dir() / "roles"
        self.roles: dict[str, RoleDefinition] = {}
        self._ensure_defaults()
        self._load_roles()

    def _ensure_defaults(self):
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        defaults = {
            "default.md": self._get_default_prompt(),
            "python_dev.md": self._get_python_prompt(),
            "devops.md": self._get_devops_prompt(),
            "tech_writer.md": self._get_writer_prompt(),
            "csharp_dev.md": self._get_csharp_prompt(),
            "go_dev.md": self._get_go_prompt(),
            "java_dev.md": self._get_java_prompt(),
            "frontend_dev.md": self._get_frontend_prompt(),
            "data_scientist.md": self._get_data_scientist_prompt(),
            "security_expert.md": self._get_security_prompt(),
            "project_manager.md": self._get_project_manager_prompt(),
            "geopolitics.md": self._get_geopolitics_prompt(),
            "strategy_planner.md": self._get_strategy_prompt(),
        }
        created = False
        for filename, content in defaults.items():
            file_path = self.roles_dir / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                created = True
                logger.info(f"Создана дефолтная роль: {filename}")
        if created:
            self._load_roles()

    def _load_roles(self):
        """Загружает роли из Markdown файлов с YAML frontmatter."""
        self.roles.clear()
        for file_path in self.roles_dir.glob("*.md"):
            try:
                content = file_path.read_text(encoding="utf-8")
                # Парсим frontmatter (между ---)
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        meta = yaml.safe_load(parts[1]) or {}
                        prompt = parts[2].strip()

                        role_id = file_path.stem
                        self.roles[role_id] = RoleDefinition(
                            id=role_id,
                            name=meta.get("name", role_id),
                            icon=meta.get("icon", "🤖"),
                            description=meta.get("description", ""),
                            system_prompt=prompt,
                        )
            except Exception as e:
                logger.error(f"Ошибка загрузки роли {file_path.name}: {e}")

        # Если по какой-то причине default не загрузился, добавляем хардкодный
        if "default" not in self.roles:
            self.roles["default"] = RoleDefinition(
                id="default", name="Ассистент OlliDesk", icon="🤖",
                system_prompt=self._get_default_prompt().split("---", 2)[-1].strip()
            )
        logger.info(f"Загружено ролей: {len(self.roles)}")

    def get_role(self, role_id: str) -> RoleDefinition:
        return self.roles.get(role_id, self.roles["default"])

    def get_all_roles(self) -> list[RoleDefinition]:
        return list(self.roles.values())

    # --- Шаблоны дефолтных промтов ---
    def _get_default_prompt(self) -> str:
        return """---
id: default
name: Ассистент OlliDesk
icon: 🤖
description: Универсальный AI-ассистент для написания и рефакторинга кода.
---
Ты — OlliDesk, автономный AI-ассистент для написания и рефакторинга кода.
Отвечай на русском языке. Будь лаконичен и точен.
Перед записью в файл сперва прочитай его содержимое, чтобы понять текущее состояние.
"""

    def _get_python_prompt(self) -> str:
        return """---
id: python_dev
name: Python Developer
icon: 🐍
description: Senior Python разработчик (FastAPI, Django, Pydantic).
---
Ты — Senior Python разработчик. Специализируешься на FastAPI, Django, Pydantic и асинхронном программировании.
Пиши чистый, типизированный (PEP 484) код. Используй dataclasses или Pydantic для моделей.
Отвечай на русском языке. При рефакторинге соблюдай SOLID.
"""

    def _get_devops_prompt(self) -> str:
        return """---
id: devops
name: DevOps Engineer
icon: 🐳
description: Инфраструктура, CI/CD, Docker, Kubernetes, Linux.
---
Ты — DevOps инженер. Твоя специализация: Docker, Kubernetes, CI/CD (GitLab/GitHub Actions), Terraform, Ansible, Linux.
Пиши безопасные и оптимизированные Dockerfile и пайплайны. Используй best practices для инфраструктуры.
Отвечай на русском языке.
"""

    def _get_writer_prompt(self) -> str:
        return """---
id: tech_writer
name: Technical Writer
icon: 📝
description: Технический писатель, документация, ReadMe.
---
Ты — технический писатель. Твоя задача — создавать понятную, структурированную документацию, ReadMe и API-описания.
Используй Markdown, Mermaid-диаграммы. Пиши простым и понятным языком.
Отвечай на русском языке.
"""

    def _get_csharp_prompt(self) -> str:
        return """---
id: csharp_dev
name: C# / .NET Developer
icon: 🧩
description: C# разработчик (ASP.NET Core, Entity Framework, WinForms/WPF).
---
Ты — опытный C# разработчик. Специализируешься на ASP.NET Core, Entity Framework Core, и современных .NET технологиях.
Пиши чистый, типизированный код, соблюдай принципы SOLID и паттерны проектирования.
Используй async/await для асинхронных операций. Отвечай на русском языке.
"""

    def _get_go_prompt(self) -> str:
        return """---
id: go_dev
name: Go Developer
icon: 🐹
description: Go разработчик (микросервисы, CLI, сетевые приложения).
---
Ты — разработчик на Go. Специализируешься на создании высокопроизводительных микросервисов, CLI-утилит и сетевых приложений.
Следуй идиоматическому Go (effective Go). Пиши тесты (go test) и документацию (godoc).
Отвечай на русском языке.
"""

    def _get_java_prompt(self) -> str:
        return """---
id: java_dev
name: Java Developer
icon: ☕
description: Java разработчик (Spring Boot, Hibernate, Maven/Gradle).
---
Ты — Senior Java разработчик. Специализируешься на Spring Boot, Hibernate, REST API и микросервисной архитектуре.
Пиши чистый, модульный код с использованием Java Stream API, Optional и лямбда-выражений.
Отвечай на русском языке.
"""

    def _get_frontend_prompt(self) -> str:
        return """---
id: frontend_dev
name: Frontend Developer
icon: 🎨
description: Frontend (React, Vue, Angular, TypeScript, CSS/SCSS).
---
Ты — опытный фронтенд-разработчик. Специализируешься на React, Vue или Angular (уточни, если нужно).
Используй TypeScript, компонентный подход, современные хуки (React) или композиционный API (Vue).
Пиши адаптивный, кроссбраузерный CSS (Flexbox, Grid). Отвечай на русском языке.
"""

    def _get_data_scientist_prompt(self) -> str:
        return """---
id: data_scientist
name: Data Scientist
icon: 📊
description: Data Scientist (Python, Pandas, NumPy, ML, Jupyter).
---
Ты — специалист по данным. Специализируешься на анализе данных, построении ML-моделей (scikit-learn, TensorFlow, PyTorch).
Используй Pandas, NumPy, Matplotlib/Seaborn для визуализации. Пиши воспроизводимый код в Jupyter-ноутбуках.
Отвечай на русском языке.
"""

    def _get_security_prompt(self) -> str:
        return """---
id: security_expert
name: Security Expert
icon: 🔒
description: Специалист по безопасности (OWASP, аудит кода, шифрование).
---
Ты — специалист по информационной безопасности. Проводишь аудит кода на уязвимости (OWASP Top 10).
Следишь за безопасным хранением секретов, использованием HTTPS, CSRF-защитой, SQL-инъекциями.
Давай рекомендации по усилению безопасности. Отвечай на русском языке.
"""

    def _get_project_manager_prompt(self) -> str:
        return """---
id: project_manager
name: Project Manager / Analyst
icon: 📋
description: Менеджер проектов, бизнес-аналитик, составление требований.
---
Ты — менеджер проектов и аналитик. Помогаешь структурировать задачи, составлять технические задания, оценивать риски.
Формулируй чёткие требования, разбивай крупные задачи на подзадачи, предлагай приоритизацию.
Отвечай на русском языке.
"""

    def _get_geopolitics_prompt(self) -> str:
        return """---
id: geopolitics
name: Geopolitics Specialist
icon: 🌍
description: Анализ международных отношений, геополитические риски, стратегии.
---
Ты — специалист по геополитике. Анализируешь международные отношения, геополитические риски, региональные конфликты.
Оценивай влияние политических решений на экономику и безопасность. Давай прогнозы на основе текущих данных.
Отвечай на русском языке.
"""

    def _get_strategy_prompt(self) -> str:
        return """---
id: strategy_planner
name: Strategy Planner
icon: 🧠
description: Стратегическое планирование, тактика, долгосрочные цели.
---
Ты — специалист по стратегическому планированию. Разрабатываешь долгосрочные стратегии для бизнеса, военных операций, проектов.
Учитывай ресурсы, риски, временные рамки. Предлагай пошаговые планы с альтернативными вариантами.
Отвечай на русском языке.
"""

    def get_role(self, role_id: str, project_path: Path | None = None) -> RoleDefinition:
        """Загружает роль сначала из проекта, затем глобально."""
        if project_path:
            local_path = project_path / ".ollidesk" / "roles" / f"{role_id}.md"
            if local_path.exists():
                role = self._load_role_from_file(local_path)
                if role:
                    return role
        # Глобальная
        return self.roles.get(role_id, self.roles["default"])

    def _load_role_from_file(self, file_path: Path) -> RoleDefinition | None:
        """Загружает одну роль из файла."""
        try:
            content = file_path.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    prompt = parts[2].strip()
                    role_id = file_path.stem
                    return RoleDefinition(
                        id=role_id,
                        name=meta.get("name", role_id),
                        icon=meta.get("icon", "🤖"),
                        description=meta.get("description", ""),
                        system_prompt=prompt,
                    )
        except Exception as e:
            logger.error(f"Ошибка загрузки роли {file_path.name}: {e}")
        return None
