"""Тесты инструментов работы с файлами: `read_file`, `list_directory`, `write_file`.

Главный акцент — на песочницу `_resolve_path`: инструмент не должен давать модели
читать и писать ничего вне `project_root`.
"""

from pathlib import Path

import pytest

from tools.file_ops import _resolve_path, list_directory, read_file, write_file


class TestResolvePath:
    """Песочница: путь за пределами project_root не резолвится."""

    def test_relative_path_inside_root(self, temp_project: Path):
        assert _resolve_path("src/main.py", str(temp_project)) == (temp_project / "src/main.py")

    def test_absolute_path_inside_root(self, temp_project: Path):
        target = temp_project / "src" / "main.py"
        assert _resolve_path(str(target), str(temp_project)) == target

    def test_absolute_path_outside_root_returns_none(self, temp_project: Path, tmp_path: Path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        assert _resolve_path(str(outside), str(temp_project)) is None

    def test_parent_traversal_returns_none(self, temp_project: Path):
        assert _resolve_path("..", str(temp_project)) is None

    def test_nested_parent_traversal_returns_none(self, temp_project: Path):
        assert _resolve_path("src/../../escape.py", str(temp_project)) is None

    def test_prefix_collision_is_not_inside_root(self, temp_project: Path):
        """Каталог `test_project_backup` не считается внутри `test_project`."""
        sibling = temp_project.parent / f"{temp_project.name}_backup"
        sibling.mkdir()
        assert _resolve_path(str(sibling), str(temp_project)) is None

    def test_empty_root_allows_any_path(self, tmp_path: Path):
        """Без project_root песочница выключена — резолвится всё."""
        target = tmp_path / "any.txt"
        assert _resolve_path(str(target), "") == target

    def test_dot_segments_are_normalized(self, temp_project: Path):
        assert _resolve_path("./src/../src/main.py", str(temp_project)) == (temp_project / "src" / "main.py")


class TestReadFile:
    def test_reads_existing_file(self, temp_project: Path):
        result = read_file("src/main.py", project_root=str(temp_project))
        assert result == "print('hello')"

    def test_reads_file_in_root(self, temp_project: Path):
        (temp_project / "root.txt").write_text("данные", encoding="utf-8")
        assert read_file("root.txt", project_root=str(temp_project)) == "данные"

    def test_missing_file_returns_message(self, temp_project: Path):
        result = read_file("нет_такого.py", project_root=str(temp_project))
        assert result.startswith("Файл не найден")

    def test_directory_instead_of_file(self, temp_project: Path):
        result = read_file("src", project_root=str(temp_project))
        assert result.startswith("Это не файл")

    def test_outside_root_is_rejected(self, temp_project: Path, tmp_path: Path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        result = read_file(str(outside), project_root=str(temp_project))
        assert result == "Ошибка: путь вне допустимой директории"

    def test_undecodable_bytes_do_not_raise(self, temp_project: Path):
        """Битый UTF-8 не должен ронять инструмент."""
        (temp_project / "binary.bin").write_bytes(b"\xff\xfe\x00\x01")
        result = read_file("binary.bin", project_root=str(temp_project))
        assert not result.startswith("Ошибка")

    def test_read_error_is_returned_as_string(self, temp_project: Path, monkeypatch):
        (temp_project / "root.txt").write_text("данные", encoding="utf-8")

        def boom(*args, **kwargs):
            raise OSError("диск сломался")

        monkeypatch.setattr(Path, "read_text", boom)
        result = read_file("root.txt", project_root=str(temp_project))
        assert "Ошибка чтения файла" in result


class TestListDirectory:
    def test_lists_dirs_and_files(self, temp_project: Path):
        result = list_directory(".", project_root=str(temp_project))
        assert "Каталоги:" in result
        assert "  src/" in result
        assert "  tests/" in result

    def test_dirs_listed_before_files(self, temp_project: Path):
        (temp_project / "aaa.txt").write_text("x")
        result = list_directory(".", project_root=str(temp_project))
        assert result.index("Каталоги:") < result.index("Файлы:")
        assert result.index("  src/") < result.index("  aaa.txt")

    def test_service_dirs_are_hidden(self, temp_project: Path):
        (temp_project / "bin").mkdir()
        (temp_project / "node_modules").mkdir()
        result = list_directory(".", project_root=str(temp_project))
        assert "  bin/" not in result
        assert "  node_modules/" not in result
        assert "служебных элементов пропущено: 2" in result

    def test_explicit_service_dir_is_listed(self, temp_project: Path):
        bin_dir = temp_project / "bin"
        bin_dir.mkdir()
        (bin_dir / "app.dll").write_bytes(b"x")
        result = list_directory("bin", project_root=str(temp_project))
        assert "  app.dll" in result

    def test_file_size_is_shown(self, temp_project: Path):
        (temp_project / "data.txt").write_bytes(b"0" * 2048)
        result = list_directory(".", project_root=str(temp_project))
        assert "  data.txt — 2.0 KB" in result

    def test_dir_shows_entry_count(self, temp_project: Path):
        result = list_directory(".", project_root=str(temp_project))
        assert "  src/ — " in result

    def test_empty_directory(self, temp_project: Path):
        empty = temp_project / "empty"
        empty.mkdir()
        result = list_directory("empty", project_root=str(temp_project))
        assert result.startswith("Директория пуста")

    def test_missing_directory(self, temp_project: Path):
        result = list_directory("нет_такой", project_root=str(temp_project))
        assert result.startswith("Директория не найдена")

    def test_file_instead_of_directory(self, temp_project: Path):
        result = list_directory("src/main.py", project_root=str(temp_project))
        assert result.startswith("Это не директория")

    def test_outside_root_is_rejected(self, temp_project: Path):
        result = list_directory(str(temp_project.parent), project_root=str(temp_project))
        assert result == "Ошибка: путь вне допустимой директории"

    def test_read_error_is_returned_as_string(self, temp_project: Path, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("не читается")

        monkeypatch.setattr(Path, "iterdir", boom)
        result = list_directory(".", project_root=str(temp_project))
        assert "Ошибка чтения директории" in result


class TestWriteFile:
    @pytest.fixture(autouse=True)
    def _no_snapshot(self, monkeypatch):
        """Снапшоты выключены: git-репозитория в тестах нет."""
        monkeypatch.setattr("tools.file_ops.create_snapshot", lambda *a, **kw: None)

    def test_creates_new_file_with_parents(self, temp_project: Path):
        result = write_file("deep/nested/new.txt", "данные", project_root=str(temp_project))
        assert result.startswith("Файл успешно обновлён")
        assert (temp_project / "deep" / "nested" / "new.txt").read_text(encoding="utf-8") == "данные"

    def test_overwrites_existing_file(self, temp_project: Path):
        (temp_project / "src" / "main.py").write_text("старое", encoding="utf-8")
        write_file("src/main.py", "новое", project_root=str(temp_project))
        assert (temp_project / "src" / "main.py").read_text(encoding="utf-8") == "новое"

    def test_outside_root_is_rejected_and_nothing_written(self, temp_project: Path, tmp_path: Path):
        outside = tmp_path / "evil.txt"
        result = write_file(str(outside), "взлом", project_root=str(temp_project))
        assert result == "Ошибка: путь вне допустимой директории"
        assert not outside.exists()

    def test_directory_target_is_rejected(self, temp_project: Path):
        result = write_file("src", "данные", project_root=str(temp_project))
        assert result.startswith("Ошибка:")
        assert "является директорией" in result

    def test_reports_snapshot_created(self, temp_project: Path, monkeypatch):
        monkeypatch.setattr("tools.file_ops.create_snapshot", lambda *a, **kw: "снапшот")
        result = write_file("a.txt", "x", project_root=str(temp_project))
        assert "Git-снапшот для отката" in result

    def test_without_root_no_snapshot_attempted(self, temp_project: Path, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("снапшот не должен создаваться без project_root")

        monkeypatch.setattr("tools.file_ops.create_snapshot", fail)
        target = temp_project / "no_root.txt"
        result = write_file(str(target), "данные")
        assert result.startswith("Файл успешно обновлён")

    def test_write_error_is_returned_as_string(self, temp_project: Path, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("диск заполнен")

        monkeypatch.setattr(Path, "write_text", boom)
        result = write_file("a.txt", "x", project_root=str(temp_project))
        assert "Ошибка записи файла" in result
