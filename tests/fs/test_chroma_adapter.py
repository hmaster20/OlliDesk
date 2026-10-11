"""Тесты сборки `where`-фильтров chromadb (fs/chroma_adapter.py)."""

from fs.chroma_adapter import build_where, where_file_paths


class TestBuildWhere:
    def test_no_conditions_returns_none(self):
        assert build_where() is None
        assert build_where(None, None) is None

    def test_file_filter_only(self):
        assert build_where(["a.py", "b.py"]) == where_file_paths(["a.py", "b.py"])

    def test_language_only(self):
        assert build_where(language="python") == {"language": {"$eq": "python"}}

    def test_both_combined_with_and(self):
        where = build_where(["a.py"], "python")

        assert where == {"$and": [{"file_path": {"$in": ["a.py"]}}, {"language": {"$eq": "python"}}]}

    def test_empty_conditions_are_ignored(self):
        assert build_where([], None) is None
        assert build_where([], "") is None
