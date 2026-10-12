"""Тесты горячей перезагрузки промптов и ролей (watchdog)."""

from pathlib import Path
from unittest.mock import MagicMock

from watchdog.events import DirModifiedEvent, FileModifiedEvent

from core.prompt_watcher import PromptWatcher, _MarkdownHandler
from core.roles import RoleManager
from core.system_prompts import SystemPromptManager


def _managers(tmp_path: Path) -> tuple[SystemPromptManager, RoleManager]:
    prompts_dir = tmp_path / "system_prompts"
    roles_dir = tmp_path / "roles"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    roles_dir.mkdir(parents=True, exist_ok=True)
    return SystemPromptManager(prompts_dir=prompts_dir), RoleManager(roles_dir=roles_dir)


class TestMarkdownHandler:
    def test_md_event_triggers_reload(self):
        watcher = MagicMock()
        handler = _MarkdownHandler(watcher)

        handler.on_any_event(FileModifiedEvent(str(Path("chat.md"))))

        watcher._schedule_reload.assert_called_once()

    def test_directory_event_ignored(self):
        watcher = MagicMock()
        handler = _MarkdownHandler(watcher)

        handler.on_any_event(DirModifiedEvent(str(Path("system_prompts"))))

        watcher._schedule_reload.assert_not_called()

    def test_non_md_event_ignored(self):
        watcher = MagicMock()
        handler = _MarkdownHandler(watcher)

        handler.on_any_event(FileModifiedEvent(str(Path("config.yaml"))))

        watcher._schedule_reload.assert_not_called()


class TestReload:
    def test_reload_clears_prompt_cache_and_roles(self):
        prompt_manager = MagicMock()
        role_manager = MagicMock()
        watcher = PromptWatcher(prompt_manager, role_manager)

        watcher._reload()

        prompt_manager.clear_cache.assert_called_once()
        role_manager.reload.assert_called_once()

    def test_reload_swallows_errors(self):
        prompt_manager = MagicMock()
        prompt_manager.clear_cache.side_effect = RuntimeError("boom")
        watcher = PromptWatcher(prompt_manager, MagicMock())

        watcher._reload()

    def test_role_reload_picks_up_file_changes(self, tmp_path):
        _, role_manager = _managers(tmp_path)
        assert role_manager.roles  # дефолтные роли созданы
        role_file = role_manager.roles_dir / "default.md"
        role_file.write_text(
            "---\nname: Тест\n---\nНовый промпт\n",
            encoding="utf-8",
        )

        role_manager.reload()

        assert role_manager.roles["default"].system_prompt == "Новый промпт"

    def test_prompt_cache_cleared(self, tmp_path):
        prompt_manager, _ = _managers(tmp_path)
        prompt_manager.get_prompt("chat")
        assert prompt_manager._cache

        prompt_manager.clear_cache()

        assert prompt_manager._cache == {}


class TestStartStop:
    def test_start_and_stop(self, tmp_path):
        prompt_manager, role_manager = _managers(tmp_path)
        watcher = PromptWatcher(prompt_manager, role_manager)
        assert not watcher.is_running

        watcher.start()
        assert watcher.is_running

        watcher.stop()
        assert not watcher.is_running

    def test_start_is_idempotent(self, tmp_path):
        prompt_manager, role_manager = _managers(tmp_path)
        watcher = PromptWatcher(prompt_manager, role_manager)
        watcher.start()
        observer = watcher._observer
        watcher.start()
        assert watcher._observer is observer
        watcher.stop()
