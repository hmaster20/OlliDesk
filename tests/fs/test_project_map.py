"""Тесты карты структуры проекта и определения каталога из запроса."""

from core.config import AppConfig
from fs.project_map import detect_query_scope, list_project_files, render_project_map


class TestRenderProjectMap:
    """Построение дерева каталогов и файлов."""

    def test_empty_paths(self):
        assert render_project_map([]) == ""

    def test_renders_dirs_and_files(self):
        result = render_project_map(["main.py", "src/app.py"])

        assert result.startswith("Структура проекта:")
        assert "main.py" in result
        assert "src/" in result
        assert "  app.py" in result

    def test_dirs_before_files(self):
        result = render_project_map(["z.py", "alpha/a.py"])
        lines = result.splitlines()[1:]
        assert lines.index("alpha/") < lines.index("z.py")

    def test_truncates_at_max_entries(self):
        paths = [f"dir{i}/file.py" for i in range(20)]
        result = render_project_map(paths, max_entries=5)

        assert "… (список усечён)" in result
        assert "dir0/" in result
        assert "dir5/" not in result

    def test_respects_max_depth(self):
        result = render_project_map(["a/b/c/d/e/file.py"], max_depth=1)

        assert "a/" in result
        assert "e" not in result


class TestDetectQueryScope:
    """Определение каталога проекта, упомянутого в запросе."""

    FILES = [
        "cloud/role-a/tasks/main.yml",
        "cloud/role-b/tasks/main.yml",
        "system/certs/tasks/main.yml",
        "soft/postgresql15/tasks/main.yml",
    ]

    def test_finds_directory_case_insensitive(self):
        scope = detect_query_scope("Изучи роли в каталоге Cloud", self.FILES)

        assert scope is not None
        rel, files = scope
        assert rel == "cloud"
        assert files == [
            "cloud/role-a/tasks/main.yml",
            "cloud/role-b/tasks/main.yml",
        ]

    def test_returns_none_for_unrelated_query(self):
        assert detect_query_scope("Расскажи анекдот", self.FILES) is None

    def test_returns_none_for_empty_input(self):
        assert detect_query_scope("", self.FILES) is None
        assert detect_query_scope("cloud", []) is None

    def test_matches_nested_basename(self):
        scope = detect_query_scope("что делают роли в certs", self.FILES)

        assert scope is not None
        rel, _ = scope
        assert rel == "system/certs"


class TestListProjectFiles:
    """Список файлов проекта для карты."""

    def test_filters_by_extension(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "image.png").write_text("binary")
        config = AppConfig(default_model="m", embed_model="e", index_extensions=[".py"])

        files = list_project_files(tmp_path, config)

        assert files == ["main.py"]

    def test_missing_directory_returns_empty(self, tmp_path):
        config = AppConfig(default_model="m", embed_model="e")

        assert list_project_files(tmp_path / "nope", config) == []
