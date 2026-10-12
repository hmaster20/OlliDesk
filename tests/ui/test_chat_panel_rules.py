"""Тесты чтения правил проекта панелью чата."""

from ui.chat.panel import read_project_rules


class TestReadProjectRules:
    def test_reads_rules_file(self, tmp_path):
        rules_dir = tmp_path / ".ollidesk"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("Не коммить без просьбы.\n", encoding="utf-8")

        assert read_project_rules(str(tmp_path)) == "Не коммить без просьбы."

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_project_rules(str(tmp_path)) == ""

    def test_no_project_returns_empty(self):
        assert read_project_rules(None) == ""
        assert read_project_rules("") == ""
