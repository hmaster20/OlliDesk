"""Тесты менеджера ролей: дефолты и перезагрузка."""

from pathlib import Path

from core.roles import RoleManager


class TestDefaults:
    def test_ensure_defaults_writes_files(self, tmp_path: Path):
        manager = RoleManager(roles_dir=tmp_path / "roles")

        assert (manager.roles_dir / "default.md").exists()
        assert "default" in manager.roles

    def test_default_templates_are_wellformed(self, tmp_path: Path):
        templates = RoleManager(roles_dir=tmp_path / "roles").default_templates()

        assert "default" in templates
        assert "python_dev" in templates
        assert len(templates) >= 13
        for content in templates.values():
            assert content.startswith("---")


class TestReload:
    def test_reload_picks_up_new_role(self, tmp_path: Path):
        manager = RoleManager(roles_dir=tmp_path / "roles")
        (manager.roles_dir / "custom.md").write_text(
            "---\nname: Custom\nicon: 🧪\ndescription: test\n---\nТы тестовая роль.\n",
            encoding="utf-8",
        )

        manager.reload()

        assert manager.roles["custom"].name == "Custom"
        assert manager.roles["custom"].system_prompt == "Ты тестовая роль."

    def test_get_role_falls_back_to_default(self, tmp_path: Path):
        manager = RoleManager(roles_dir=tmp_path / "roles")

        role = manager.get_role("does_not_exist")

        assert role.id == "default"
