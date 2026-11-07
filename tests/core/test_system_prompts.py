"""Тесты менеджера системных промптов: шаблонизация и приоритет проектного файла."""

from pathlib import Path

from core.system_prompts import SystemPromptManager


def _manager(tmp_path: Path, global_text: str) -> SystemPromptManager:
    prompts_dir = tmp_path / "system_prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "chat.md").write_text(global_text, encoding="utf-8")
    return SystemPromptManager(prompts_dir=prompts_dir)


class TestTemplateRendering:
    def test_variables_substituted(self, tmp_path):
        manager = _manager(tmp_path, "Проект {{project_name}}, язык {{language}}")

        result = manager.get_prompt("chat", context={"project_name": "OlliDesk", "language": "русский"})

        assert result == "Проект OlliDesk, язык русский"

    def test_unfilled_variables_kept(self, tmp_path):
        manager = _manager(tmp_path, "{{project_name}} / {{missing}}")

        result = manager.get_prompt("chat", context={"project_name": "OlliDesk"})

        assert result == "OlliDesk / {{missing}}"

    def test_no_context_leaves_placeholders(self, tmp_path):
        manager = _manager(tmp_path, "{{project_name}}")

        result = manager.get_prompt("chat")

        assert result == "{{project_name}}"


class TestProjectOverride:
    def test_project_prompt_wins_over_global(self, tmp_path):
        manager = _manager(tmp_path, "global {{project_name}}")
        project = tmp_path / "proj"
        prompts_dir = project / ".ollidesk" / "system_prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "chat.md").write_text("project {{project_name}}", encoding="utf-8")

        result = manager.get_prompt("chat", project_root=project, context={"project_name": "P"})

        assert result == "project P"


class TestDefaults:
    def test_default_prompt_known_mode(self):
        assert SystemPromptManager.default_prompt("chat")
        assert SystemPromptManager.default_prompt("agent")

    def test_default_prompt_unknown_mode(self):
        assert SystemPromptManager.default_prompt("nope") == ""

    def test_ensure_defaults_writes_all_modes(self, tmp_path):
        prompts_dir = tmp_path / "sp"
        prompts_dir.mkdir()

        SystemPromptManager(prompts_dir=prompts_dir)

        for mode in ("chat", "plan", "agent"):
            assert (prompts_dir / f"{mode}.md").exists()

    def test_render_template_substitutes_and_keeps_unknown(self):
        result = SystemPromptManager.render_template("{{a}} / {{b}}", {"a": "1"})
        assert result == "1 / {{b}}"
