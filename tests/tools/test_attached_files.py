"""Тесты прикреплённых файлов (@file) и инструмента read_attached_file."""

from pathlib import Path

from core.config import AgentMode, ToolPolicy
from core.context_budget import READ_CHUNK_CHARS
from tools.attached_files import (
    AttachedFile,
    AttachedFileStore,
    read_attached_file,
)


def _meta() -> dict:
    return dict(read_attached_file._tool_meta)  # type: ignore[attr-defined]


class TestAttachedFileStore:
    """Хранилище прикреплённых файлов."""

    def test_empty_store_is_falsy(self, tmp_path: Path):
        store = AttachedFileStore()
        assert not store
        assert len(store) == 0
        assert store.render(1000) == ""

    def test_add_and_truthy(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        assert store
        assert len(store) == 1

    def test_add_replaces_same_path(self, tmp_path: Path):
        store = AttachedFileStore()
        path = tmp_path / "a.py"
        store.add(path, "old")
        store.add(path, "new")
        assert len(store) == 1
        assert store.find(str(path)).content == "new"

    def test_clear(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        store.clear()
        assert not store

    def test_find_by_full_path(self, tmp_path: Path):
        store = AttachedFileStore()
        path = tmp_path / "a.py"
        store.add(path, "print(1)")
        assert store.find(str(path)) is not None

    def test_find_by_name(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "nested" / "main.py", "print(1)")
        assert store.find("main.py") is not None

    def test_find_missing(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        assert store.find("other.py") is None
        assert store.find("") is None

    def test_files_returns_copy(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        files = store.files
        files.clear()
        assert len(store) == 1


class TestAttachedFile:
    """Метаданные прикреплённого файла."""

    def test_name(self, tmp_path: Path):
        assert AttachedFile(tmp_path / "main.py", "").name == "main.py"

    def test_size_chars(self):
        assert AttachedFile(Path("a.py"), "12345").size_chars == 5

    def test_lines(self):
        assert AttachedFile(Path("a.py"), "a\nb\nc").lines == 3

    def test_lines_without_newline(self):
        assert AttachedFile(Path("a.py"), "a").lines == 1


class TestRender:
    """Формирование блока контекста."""

    def test_small_file_fits_whole(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)\n")
        block = store.render(5000)
        assert "a.py" in block
        assert "print(1)" in block
        assert "пропущено" not in block

    def test_large_file_is_truncated(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "big.py", "x" * 20000)
        block = store.render(1500)
        assert "пропущено" in block
        assert "read_attached_file" in block
        assert len(block) < 3000

    def test_zero_budget_returns_empty(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        assert store.render(0) == ""

    def test_budget_split_between_files(self, tmp_path: Path):
        store = AttachedFileStore()
        for name in ("a.py", "b.py"):
            store.add(tmp_path / name, "y" * 5000)
        block = store.render(2000)
        assert "a.py" in block
        assert "b.py" in block
        assert len(block) < 3000

    def test_too_small_budget_gives_hint_only(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "big.py", "z" * 10000)
        block = store.render(10)
        assert "read_attached_file" in block
        assert "```" not in block


class TestReadAttachedFile:
    """Инструмент read_attached_file."""

    def test_tool_meta(self):
        meta = _meta()
        assert meta["name"] == "read_attached_file"
        assert meta["policy"] == ToolPolicy.AUTO
        assert set(meta["modes"]) == {
            AgentMode.CHAT,
            AgentMode.PLAN,
            AgentMode.AGENT,
        }

    def test_no_store(self):
        assert "нет" in read_attached_file("a.py")

    def test_empty_store(self, tmp_path: Path):
        assert "@file" in read_attached_file("a.py", attached_files=AttachedFileStore())

    def test_unknown_file_lists_available(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "print(1)")
        result = read_attached_file("b.py", attached_files=store)
        assert "не прикреплён" in result
        assert "a.py" in result

    def test_returns_chunk(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "0123456789" * 10)
        result = read_attached_file("a.py", offset=0, limit=10, attached_files=store)
        assert "0123456789" in result
        assert "offset=10" in result

    def test_offset_is_respected(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "A" * 10 + "B" * 10)
        result = read_attached_file("a.py", offset=10, limit=5, attached_files=store)
        assert "BBBBB" in result
        assert "AAAAA" not in result

    def test_negative_offset_clamped(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "A" * 20)
        result = read_attached_file("a.py", offset=-10, limit=5, attached_files=store)
        assert "AAAAA" in result

    def test_limit_is_capped(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "A" * (READ_CHUNK_CHARS * 2))
        result = read_attached_file(
            "a.py", offset=0, limit=READ_CHUNK_CHARS * 5, attached_files=store
        )
        assert "Это конец файла" not in result

    def test_offset_beyond_eof(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "A" * 10)
        result = read_attached_file("a.py", offset=100, attached_files=store)
        assert "выходит за конец файла" in result

    def test_end_of_file_hint(self, tmp_path: Path):
        store = AttachedFileStore()
        store.add(tmp_path / "a.py", "A" * 10)
        result = read_attached_file("a.py", offset=0, limit=100, attached_files=store)
        assert "Это конец файла" in result