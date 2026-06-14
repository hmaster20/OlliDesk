import pytest

from core.config import AppConfig
from fs.indexer import FileIndexer


@pytest.fixture
def config():
    return AppConfig(
        default_model="test",
        embed_model="test",
        ignored_patterns=[".git", "node_modules", "__pycache__", "venv", "*.pyc"],
        index_extensions=[".py", ".js", ".ts", ".md"],
        max_file_size_kb=10,
        chunk_size_tokens=512,
        chunk_overlap_tokens=50,
    )


@pytest.fixture
def indexer(config):
    return FileIndexer(config)


@pytest.mark.asyncio
class TestFileIndexer:
    async def test_scan_project_finds_py_files(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def util(): pass")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 2
        paths = {f.path for f in files}
        assert "main.py" in paths
        assert "utils.py" in paths

    async def test_scan_project_ignores_node_modules(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")
        node_mods = tmp_path / "node_modules"
        node_mods.mkdir()
        (node_mods / "lodash.js").write_text("module.exports = {}")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 1
        assert "node_modules/lodash.js" not in {f.path for f in files}

    async def test_scan_project_ignores_git(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("some git data")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 1
        assert ".git/config" not in {f.path for f in files}

    async def test_scan_project_ignores_venv(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "activate.py").write_text("activate")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 1
        assert "venv/activate.py" not in {f.path for f in files}

    async def test_scan_project_ignores_pyc(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "compiled.pyc").write_text("bytes")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 1
        assert "compiled.pyc" not in {f.path for f in files}

    async def test_scan_project_filters_by_extension(self, tmp_path, indexer):
        (tmp_path / "script.py").write_text("print(1)")
        (tmp_path / "styles.css").write_text("body {}")
        (tmp_path / "readme.md").write_text("# Docs")
        (tmp_path / "data.json").write_text('{"key": "val"}')

        files = await indexer.scan_project(tmp_path)

        paths = {f.path for f in files}
        assert "script.py" in paths
        assert "styles.css" not in paths
        assert "readme.md" in paths
        assert "data.json" not in paths

    async def test_scan_project_ignores_large_files(self, tmp_path, indexer):
        content = "x" * (11 * 1024)  # 11 KB > max 10 KB
        (tmp_path / "large.py").write_text(content)
        (tmp_path / "small.py").write_text("small")

        files = await indexer.scan_project(tmp_path)

        assert len(files) == 1
        assert files[0].path == "small.py"

    async def test_chunk_file_single_chunk(self, indexer):
        content = "line1\nline2\nline3"
        chunks = indexer.chunk_file(content, "test.py")

        assert len(chunks) == 1
        assert chunks[0].content == content
        assert chunks[0].file_path == "test.py"
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3

    async def test_chunk_file_multiple_chunks(self, indexer):
        # chunk_size_tokens=512, token/line~15 => ~34 lines per chunk
        lines = [f"line{i}" for i in range(100)]
        content = "\n".join(lines)

        chunks = indexer.chunk_file(content, "test.py")

        assert len(chunks) > 1
        # Проверяем, что первый чанк начинается с line1
        assert chunks[0].start_line == 1
        # Последний чанк заканчивается на line100
        assert chunks[-1].end_line == 100

    async def test_chunk_file_with_overlap(self, indexer):
        # 60 lines, chunk_size_lines ~34, overlap ~3 lines
        lines = [f"line{i}" for i in range(60)]
        content = "\n".join(lines)

        chunks = indexer.chunk_file(content, "test.py")
        if len(chunks) > 1:
            # Проверяем перекрытие: следующий чанк начинается до конца предыдущего
            assert chunks[1].start_line <= chunks[0].end_line

    async def test_incremental_scan_new_file(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")
        current = await indexer.scan_project(tmp_path)
        current_state = {f.path: f for f in current}

        (tmp_path / "new.py").write_text("y = 2")
        new_or_mod, deleted = await indexer.incremental_scan(tmp_path, current_state)

        assert len(new_or_mod) == 1
        assert new_or_mod[0].path == "new.py"
        assert len(deleted) == 0

    async def test_incremental_scan_modified_file(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("v1")
        current = await indexer.scan_project(tmp_path)
        current_state = {f.path: f for f in current}

        (tmp_path / "main.py").write_text("v2")
        new_or_mod, deleted = await indexer.incremental_scan(tmp_path, current_state)

        assert len(new_or_mod) == 1
        assert new_or_mod[0].path == "main.py"
        assert len(deleted) == 0

    async def test_incremental_scan_deleted_file(self, tmp_path, indexer):
        file1 = tmp_path / "main.py"
        file1.write_text("x = 1")
        (tmp_path / "old.py").write_text("old code")

        current = await indexer.scan_project(tmp_path)
        current_state = {f.path: f for f in current}

        (tmp_path / "old.py").unlink()
        new_or_mod, deleted = await indexer.incremental_scan(tmp_path, current_state)

        assert len(new_or_mod) == 0
        assert len(deleted) == 1
        assert "old.py" in deleted

    async def test_incremental_scan_no_changes(self, tmp_path, indexer):
        (tmp_path / "main.py").write_text("x = 1")

        current = await indexer.scan_project(tmp_path)
        current_state = {f.path: f for f in current}

        new_or_mod, deleted = await indexer.incremental_scan(tmp_path, current_state)

        assert len(new_or_mod) == 0
        assert len(deleted) == 0


class TestFileIndexerSync:
    """Sync tests (not using asyncio)."""

    def test_detect_language(self, indexer):
        assert indexer._detect_language("main.py") == "python"
        assert indexer._detect_language("app.js") == "javascript"
        assert indexer._detect_language("component.tsx") == "typescript"
        assert indexer._detect_language("style.css") == "css"

    def test_detect_language_unknown(self, indexer):
        assert indexer._detect_language("data.xyz") == "unknown"

    def test_chunk_id_uniqueness(self, indexer):
        content = "\n".join([f"line{i}" for i in range(100)])
        chunks = indexer.chunk_file(content, "test.py")

        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids)), "Chunk IDs must be unique"
