"""Индексатор файлов проекта."""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pathspec
from loguru import logger
from pydantic import BaseModel, Field

from core.config import AppConfig

from fs.document_extractors import (
    SUPPORTED_EXTENSIONS as DOCUMENT_EXTENSIONS,
    extract_document,
    is_document,
    language_for_document,
)

class FileMetadata(BaseModel):
    """Метаданные файла."""

    path: str  # относительно корня проекта
    absolute_path: Path
    sha256: str
    mtime: float
    size_bytes: int
    extension: str


class TextChunk(BaseModel):
    """Чанк текста из файла."""

    id: str  # hash(file_path + chunk_index)
    file_path: str
    content: str
    start_line: int
    end_line: int
    metadata: dict[str, Any] = Field(default_factory=dict)


BINARY_EXTENSIONS: set[str] = {
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.bmp',
    '.ico',
    '.webp',
    '.tiff',
    '.tif',
    '.psd',
    '.mp3',
    '.mp4',
    '.wav',
    '.ogg',
    '.flac',
    '.avi',
    '.mkv',
    '.mov',
    '.wmv',
    '.webm',
    '.m4a',
    '.aac',
    '.ttf',
    '.otf',
    '.woff',
    '.woff2',
    '.eot',
    '.zip',
    '.tar',
    '.gz',
    '.bz2',
    '.xz',
    '.7z',
    '.rar',
    '.zst',
    '.lz',
    '.lzma',
    '.exe',
    '.dll',
    '.so',
    '.dylib',
    '.o',
    '.obj',
    '.lib',
    '.pyc',
    '.pyo',
    '.pyd',
    '.class',
    '.jar',
    '.war',
    '.bin',
    '.dat',
    '.db',
    '.sqlite',
    '.sqlite3',
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.deb',
    '.rpm',
    '.apk',
    '.ipa',
    '.iso',
    '.img',
    '.pak',
    '.vpk',
}

ARCHIVE_EXTENSIONS: set[str] = {
    '.zip',
    '.tar',
    '.gz',
    '.bz2',
    '.xz',
    '.7z',
    '.rar',
    '.zst',
    '.lz',
    '.lzma',
}

# [9.4] Лимит на извлечённый текст документа (после OCR/парсинга). Защита
# от 300-страничных PDF, которые забьют ChromaDB десятками тысяч чанков.
_MAX_DOCUMENT_TEXT_CHARS = 2_000_000  # ~2 МБ текста ≈ 500 страниц

_APP_DIR = ".ollidesk"


class FileIndexer:
    """Индексатор файлов проекта."""

    def __init__(self, config: AppConfig, project_path: Path | None = None):
        """
        Инициализация индексатора.

        Args:
            config: Конфигурация приложения
            project_path: Путь к проекту (для загрузки дополнительных игнор-файлов)
        """
        self.config = config
        self.project_path = project_path

        ignored_patterns = list(config.ignored_patterns)

        # Загружаем игнор-паттерны проекта в порядке возрастания приоритета:
        # сначала .gitignore, затем явные файлы OlliDesk. При сборке в один
        # PathSpec последнее совпавшее правило выигрывает, поэтому `!pattern`
        # в .indexignore/.ollideskignore может вернуть файл в индекс.
        if project_path:
            for ignore_file in [
                project_path / ".gitignore",
                project_path / ".ollideskignore",
                project_path / ".indexignore",
                project_path / ".ollidesk" / "indexignore",
            ]:
                lines = self._read_ignore_file(ignore_file)
                if lines:
                    ignored_patterns.extend(lines)
                    logger.info(f"Загружены игнор-паттерны из {ignore_file}: {len(lines)}")

        self.ignored_spec = pathspec.PathSpec.from_lines(
            "gitwildmatch",
            ignored_patterns,
        )
        self.allowed_extensions = set(config.index_extensions)
        self._index_filenames: set[str] = {name.lower() for name in (config.index_filenames or [])}
        self.max_file_size = config.max_file_size_kb * 1024
        self.chunk_size_tokens = config.chunk_size_tokens
        self.chunk_overlap_tokens = config.chunk_overlap_tokens

        # [9.4] Документы: включение + отдельный лимит + опция OCR.
        self._documents_enabled = bool(getattr(config, "index_documents", True))
        self.max_document_size = (
            int(getattr(config, "max_document_size_mb", 50)) * 1024 * 1024
        )
        self._document_ocr = bool(getattr(config, "document_ocr", False))

    def _read_ignore_file(self, path: Path) -> list[str]:
        """Читает игнор-файл и возвращает значимые строки.

        Пустые строки и комментарии (`#`) отбрасываются. Ошибка чтения не
        прерывает индексацию — файл просто пропускается.

        Args:
            path: Путь к файлу игнор-паттернов.

        Returns:
            list[str]: Строки-паттерны (без пустых и комментариев).
        """
        try:
            if not path.exists():
                return []
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug(f"Не удалось прочитать {path}: {e}")
            return []

        return [
            stripped
            for line in content.splitlines()
            if (stripped := line.strip()) and not stripped.startswith("#")
        ]

    def _iter_visible_files(self, project_path: Path) -> Iterator[tuple[Path, str]]:
        """Перебирает файлы проекта, прошедшие фильтры (без чтения содержимого)."""
        for file_path in project_path.rglob("*"):
            if not file_path.is_file():
                continue

            rel_path_str = str(file_path.relative_to(project_path)).replace("\\", "/")

            # Служебный каталог OlliDesk не индексируется.
            if rel_path_str.split("/", 1)[0] == _APP_DIR:
                continue

            if self.ignored_spec.match_file(rel_path_str):
                logger.debug(f"Игнорируется: {rel_path_str}")
                continue

            # [D6] Фильтр: расширение ИЛИ имя (без расширения).
            suffix = file_path.suffix.lower()
            by_name = file_path.name.lower() in self._index_filenames
            by_ext = suffix in self.allowed_extensions
            # [9.4] Документы (PDF/DOCX/…) индексируются, даже если
            # расширения нет в `index_extensions`: это делает профиль
            # «Исследование» рабочим.
            by_document = is_document(file_path) and self._documents_enabled
            if not (by_name or by_ext or by_document):
                logger.debug(f"Неподдерживаемое расширение: {rel_path_str}")
                continue

            if not (by_name or by_document) and suffix in BINARY_EXTENSIONS:
                logger.debug(f"Бинарный файл: {rel_path_str}")
                continue

            try:
                size = file_path.stat().st_size
            except OSError as e:
                logger.debug(f"Не удалось получить размер {rel_path_str}: {e}")
                continue

            # [9.4] Для документов — отдельный лимит (PDF/DOCX обычно крупнее
            # текстовых файлов). Для остальных — обычный.
            limit = (
                self.max_document_size
                if (by_document or suffix in DOCUMENT_EXTENSIONS)
                else self.max_file_size
            )
            if size > limit:
                logger.debug(
                    f"Файл слишком большой: {rel_path_str} ({size} bytes > {limit})"
                )
                continue

            yield file_path, rel_path_str

    def list_project_files(self, project_path: Path) -> list[str]:
        """Возвращает относительные пути файлов проекта без хеширования.

        Нужен для построения карты структуры (`fs.project_map`): она должна
        показывать ровно те файлы, что попадают в индекс.

        Args:
            project_path: Путь к проекту.

        Returns:
            Отсортированный список относительных путей (со слэшами).
        """
        paths = sorted(rel for _, rel in self._iter_visible_files(project_path))
        logger.info(f"Видимых файлов проекта: {len(paths)}")
        return paths

    async def scan_project(self, project_path: Path) -> list[FileMetadata]:
        """
        Обходит проект и возвращает метаданные файлов.

        Args:
            project_path: Путь к проекту

        Returns:
            list[FileMetadata]: Список метаданных файлов
        """
        files = []

        for file_path, rel_path_str in self._iter_visible_files(project_path):
            sha256 = self._compute_sha256(file_path)
            mtime = file_path.stat().st_mtime

            metadata = FileMetadata(
                path=rel_path_str,
                absolute_path=file_path,
                sha256=sha256,
                mtime=mtime,
                size_bytes=file_path.stat().st_size,
                extension=file_path.suffix.lower(),
            )
            files.append(metadata)

        logger.info(f"Найдено файлов: {len(files)}")
        return files

    def _compute_sha256(self, file_path: Path) -> str:
        """
        Вычисляет SHA256 хэш файла.

        Args:
            file_path: Путь к файлу

        Returns:
            str: SHA256 хэш
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def read_file_content(self, path: Path, *, with_ocr: bool | None = None) -> str:
        """[9.4] Читает содержимое файла: текст или извлечённый из документа.

        Для документов (PDF/DOCX/PPTX/XLSX/RTF) вызывает `extract_document`,
        для остальных — обычный `read_text(utf-8, errors='replace')`.
        Извлечённый текст обрезается до `_MAX_DOCUMENT_TEXT_CHARS`.

        Args:
            path: Путь к файлу.
            with_ocr: Переопределить настройку OCR из конфига (None = конфиг).

        Returns:
            Текст. Для документа с ошибкой извлечения — пустая строка
            (индексация продолжается, но чанк пустой — пропускается выше).
        """
        if is_document(path):
            use_ocr = self._document_ocr if with_ocr is None else bool(with_ocr)
            result = extract_document(path, use_ocr=use_ocr)
            if result.error and not result.text:
                logger.info(f"Документ {path.name}: {result.error}")
                return ""
            text = result.text
            if len(text) > _MAX_DOCUMENT_TEXT_CHARS:
                logger.warning(
                    f"Документ {path.name}: текст {len(text)} символов "
                    f"> лимита {_MAX_DOCUMENT_TEXT_CHARS}, обрезаю"
                )
                text = text[:_MAX_DOCUMENT_TEXT_CHARS] + "\n… [документ обрезан]"
            return text
        return path.read_text(encoding="utf-8", errors="replace")

    def chunk_file(self, content: str, file_path: str, mtime: float | None = None) -> list[TextChunk]:
        """
        Разбивает файл на чанки.

        Args:
            content: Содержимое файла
            file_path: Путь к файлу (относительный)
            mtime: Время изменения файла (unix). Попадает в метаданные чанка,
                чтобы `VectorStore` мог фильтровать выдачу по дате.

        Returns:
            list[TextChunk]: Список чанков
        """
        lines = content.split("\n")
        chunks = []

        # Простое разбиение по строкам (без учета границ функций)
        # TODO: Улучшить разбиение с учетом синтаксиса (AST для Python, JS и т.д.)
        chunk_size_lines = self.chunk_size_tokens // 15  # Примерно 15 токенов на строку
        overlap_lines = self.chunk_overlap_tokens // 15

        start_line = 0
        chunk_index = 0

        while start_line < len(lines):
            end_line = min(start_line + chunk_size_lines, len(lines))
            chunk_content = "\n".join(lines[start_line:end_line])

            # Вычисление ID чанка
            chunk_id = hashlib.md5(f"{file_path}:{chunk_index}".encode()).hexdigest()

            chunk = TextChunk(
                id=chunk_id,
                file_path=file_path,
                content=chunk_content,
                start_line=start_line + 1,
                end_line=end_line,
                metadata={
                    "language": self._detect_language(file_path),
                    **({"mtime": float(mtime)} if mtime is not None else {}),
                },
            )
            chunks.append(chunk)

            start_line += chunk_size_lines - overlap_lines
            chunk_index += 1

        logger.debug(f"Файл {file_path} разбит на {len(chunks)} чанков")
        return chunks

    def _detect_language(self, file_path: str) -> str:
        """
        Определяет язык программирования по расширению.

        Args:
            file_path: Путь к файлу

        Returns:
            str: Язык программирования
        """
        # [9.4] Документы (PDF/DOCX/…) — отдельный «язык» для RAG-категории docs.
        if is_document(file_path):
            return language_for_document(file_path)
        ext_to_lang = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".vue": "vue",
            ".svelte": "svelte",
            ".astro": "astro",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".scss": "scss",
            ".sass": "scss",
            ".less": "css",
            ".md": "markdown",
            ".markdown": "markdown",
            ".rst": "rst",
            ".txt": "text",
            ".csv": "csv",
            ".json": "json",
            ".jsonc": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".ini": "ini",
            ".cfg": "ini",
            ".xml": "xml",
            ".properties": "properties",
            ".cs": "csharp",
            ".csproj": "xml",
            ".sln": "text",
            ".fs": "fsharp",
            ".vb": "vbnet",
            ".xaml": "xml",
            ".cshtml": "csharp",
            ".razor": "csharp",
            ".config": "xml",
            ".resx": "xml",
            ".java": "java",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".scala": "scala",
            ".groovy": "groovy",
            ".gradle": "groovy",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".pl": "perl",
            ".lua": "lua",
            ".r": "r",
            ".dart": "dart",
            ".swift": "swift",
            ".c": "c",
            ".h": "c",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".hpp": "cpp",
            ".hh": "cpp",
            ".m": "objectivec",
            ".mm": "objectivec",
            ".sh": "shell",
            ".bash": "shell",
            ".zsh": "shell",
            ".ps1": "powershell",
            ".bat": "batch",
            ".cmd": "batch",
            ".sql": "sql",
        }
        ext = Path(file_path).suffix.lower()
        return ext_to_lang.get(ext, "unknown")

    async def incremental_scan(
        self,
        project_path: Path,
        previous_state: dict[str, FileMetadata],
    ) -> tuple[list[FileMetadata], list[str]]:
        """
        Инкрементальный скан проекта.

        Args:
            project_path: Путь к проекту
            previous_state: Предыдущее состояние (путь -> метаданные)

        Returns:
            tuple: (новые/измененные файлы, удаленные файлы)
        """
        current_files = await self.scan_project(project_path)
        current_state = {f.path: f for f in current_files}

        new_or_modified = []
        deleted = []

        # Находим новые и измененные файлы
        for path, metadata in current_state.items():
            if path not in previous_state:
                new_or_modified.append(metadata)
                logger.info(f"Новый файл: {path}")
            elif previous_state[path].sha256 != metadata.sha256:
                new_or_modified.append(metadata)
                logger.debug(f"Изменен файл: {path}")

        # Находим удаленные файлы
        for path in previous_state:
            if path not in current_state:
                deleted.append(path)
                logger.debug(f"Удален файл: {path}")

        logger.info(
            f"Инкрементальный скан: {len(new_or_modified)} новых/измененных, {len(deleted)} удаленных"
        )
        return new_or_modified, deleted
