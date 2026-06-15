"""Индексатор файлов проекта."""

import hashlib
from pathlib import Path
from typing import Any

import pathspec
from loguru import logger
from pydantic import BaseModel, Field

from core.config import AppConfig


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
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff', '.tif', '.psd',
    '.mp3', '.mp4', '.wav', '.ogg', '.flac', '.avi', '.mkv', '.mov', '.wmv', '.webm', '.m4a', '.aac',
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.zst', '.lz', '.lzma',
    '.exe', '.dll', '.so', '.dylib', '.o', '.obj', '.lib',
    '.pyc', '.pyo', '.pyd',
    '.class', '.jar', '.war',
    '.bin', '.dat', '.db', '.sqlite', '.sqlite3',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.deb', '.rpm', '.apk', '.ipa',
    '.iso', '.img',
    '.pak', '.vpk',
}

ARCHIVE_EXTENSIONS: set[str] = {
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.zst', '.lz', '.lzma',
}


class FileIndexer:
    """Индексатор файлов проекта."""

    def __init__(self, config: AppConfig):
        """
        Инициализация индексатора.

        Args:
            config: Конфигурация приложения
        """
        self.config = config
        self.ignored_spec = pathspec.PathSpec.from_lines(
            "gitwildmatch",
            config.ignored_patterns,
        )
        self.allowed_extensions = set(config.index_extensions)
        self.max_file_size = config.max_file_size_kb * 1024
        self.chunk_size_tokens = config.chunk_size_tokens
        self.chunk_overlap_tokens = config.chunk_overlap_tokens

    async def scan_project(self, project_path: Path) -> list[FileMetadata]:
        """
        Обходит проект и возвращает метаданные файлов.

        Args:
            project_path: Путь к проекту

        Returns:
            list[FileMetadata]: Список метаданных файлов
        """
        files = []

        for file_path in project_path.rglob("*"):
            if not file_path.is_file():
                continue

            # Относительный путь
            rel_path = file_path.relative_to(project_path)
            rel_path_str = str(rel_path).replace("\\", "/")

            # Проверка на игнорирование
            if self.ignored_spec.match_file(rel_path_str):
                logger.debug(f"Игнорируется: {rel_path_str}")
                continue

            # Проверка расширения
            if file_path.suffix.lower() not in self.allowed_extensions:
                logger.debug(f"Неподдерживаемое расширение: {rel_path_str}")
                continue

            # Пропускаем бинарные файлы и архивы
            if file_path.suffix.lower() in BINARY_EXTENSIONS:
                logger.debug(f"Бинарный файл: {rel_path_str}")
                continue

            # Проверка размера
            size = file_path.stat().st_size
            if size > self.max_file_size:
                logger.debug(f"Файл слишком большой: {rel_path_str} ({size} bytes)")
                continue

            # Вычисление хэша
            sha256 = self._compute_sha256(file_path)
            mtime = file_path.stat().st_mtime

            metadata = FileMetadata(
                path=rel_path_str,
                absolute_path=file_path,
                sha256=sha256,
                mtime=mtime,
                size_bytes=size,
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

    def chunk_file(self, content: str, file_path: str) -> list[TextChunk]:
        """
        Разбивает файл на чанки.

        Args:
            content: Содержимое файла
            file_path: Путь к файлу (относительный)

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
        ext_to_lang = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".html": "html",
            ".css": "css",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
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
                logger.debug(f"Новый файл: {path}")
            elif previous_state[path].sha256 != metadata.sha256:
                new_or_modified.append(metadata)
                logger.debug(f"Изменен файл: {path}")

        # Находим удаленные файлы
        for path in previous_state:
            if path not in current_state:
                deleted.append(path)
                logger.debug(f"Удален файл: {path}")

        logger.info(
            f"Инкрементальный скан: {len(new_or_modified)} новых/измененных, "
            f"{len(deleted)} удаленных"
        )
        return new_or_modified, deleted
