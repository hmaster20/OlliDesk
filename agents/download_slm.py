"""Скачивание и управление локальными SLM-моделями (GGUF, llama.cpp).

Принцип: локальные проверки (установлена ли модель, сколько частей) НЕ
обращаются к HuggingFace. HF используется только в момент скачивания.
Это устраняет фризы UI при открытии диалога SLM, где раньше каждый
показ таблицы дёргал HF для каждой из ~30 моделей.
"""

import contextlib
import re
from pathlib import Path
from loguru import logger
from collections.abc import Callable

DEFAULT_MODELS_DIR = Path.home() / ".ollidesk" / "models"

# [UI-2025] Порог «битого» файла: реальные GGUF-модели весят сотни МБ.
# Если файл < 1 МБ — скачивание прервалось или файл повреждён.
_MIN_MODEL_SIZE_BYTES = 1024 * 1024

# [UI-2025] Маркер незавершённого скачивания. Создаётся перед стримом,
# удаляется после успешной SHA256-проверки. Если процесс убили — маркер
# останется, и `get_file_status` вернёт «broken» (а не «installed» для
# случайно недокачанного файла).
_PARTIAL_SUFFIX = ".partial"

# Embedding-модель общая, не участвует в выборе chat-модели.
EMBED_MODEL_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
EMBED_MODEL_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"

_LEGACY_CHAT_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

# filename → количество частей. Fallback только при скачивании, когда HF
# недоступен. В нормальной работе число частей определяется по имени файла
# первой части (см. _detect_parts_from_local).
_MANUAL_PARTS_OVERRIDE: dict[str, int] = {}

# (repo_id, filename) → количество частей (кэш на сессию). Заполняется
# только во время скачивания, не при UI-проверках.
_HF_PARTS_CACHE: dict[tuple[str, str], int] = {}


# ---------------------------------------------------------------------------
# Имена split-частей
# ---------------------------------------------------------------------------

_SPLIT_PART_RE = re.compile(
    r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$",
    re.IGNORECASE,
)


def _split_part_name(stem: str, index: int, total: int) -> str:
    """Имя части разбитой GGUF-модели (как в HF-репозитории)."""
    return f"{stem}-{index:05d}-of-{total:05d}.gguf"


# ---------------------------------------------------------------------------
# Локальное определение частей (без сети!)
# ---------------------------------------------------------------------------


def _detect_parts_from_local(filename: str, target_dir: Path) -> int:
    """Сколько частей модели лежит локально.

    Возможные результаты:
        0 — ни цельного файла, ни частей
        1 — цельный файл <filename>
        N — N частей разбитой модели

    Не обращается в сеть.
    """
    if (target_dir / filename).exists():
        return 1

    stem = filename.removesuffix(".gguf")
    total = 0
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            total = max(total, int(m.group("total")))
    return total


def _detect_parts_via_hf(repo_id: str, filename: str) -> int | None:
    """Возвращает число частей модели, обращаясь к HuggingFace.

    Используется ТОЛЬКО при скачивании. Возможные результаты:
        1     — в репозитории лежит единый файл <filename>
        N > 1 — в репозитории лежат части <stem>-NNNNN-of-MMMMM.gguf
        None  — не удалось определить (нет сети / HfApi упал)
    """
    key = (repo_id, filename)
    if key in _HF_PARTS_CACHE:
        return _HF_PARTS_CACHE[key]

    try:
        from huggingface_hub import HfApi

        api = HfApi()
        files = api.list_repo_files(repo_id=repo_id)
    except Exception as e:
        logger.warning(f"Не удалось получить список файлов {repo_id}: {e}")
        return None

    if filename in files:
        _HF_PARTS_CACHE[key] = 1
        logger.debug(f"HF: {repo_id}/{filename} — цельный файл")
        return 1

    stem = filename.removesuffix(".gguf")
    pattern = re.compile(rf"^{re.escape(stem)}-(\d{{5}})-of-(\d{{5}})\.gguf$")
    max_total = 0
    for f in files:
        m = pattern.match(f)
        if m:
            total = int(m.group(2))
            if total > max_total:
                max_total = total

    if max_total > 0:
        _HF_PARTS_CACHE[key] = max_total
        logger.info(f"HF: {repo_id}/{filename} разбит на {max_total} частей (определено автоматически)")
        return max_total

    logger.debug(f"HF: {repo_id}/{filename} — не найден")
    return None


def get_model_parts(repo_id: str, filename: str) -> int:
    """Число частей модели. Обращается к HF только если локально ничего нет.

    Приоритет:
      1. Ручной override в _MANUAL_PARTS_OVERRIDE.
      2. Локальная ФС (если модель уже скачана).
      3. HF API (при скачивании).
      4. Fallback = 1.
    """
    if filename in _MANUAL_PARTS_OVERRIDE:
        return _MANUAL_PARTS_OVERRIDE[filename]

    # Локальная проверка — мгновенная.
    local = _detect_parts_from_local(filename, get_models_dir())
    if local > 0:
        return local

    hf_parts = _detect_parts_via_hf(repo_id, filename)
    if hf_parts is not None:
        return hf_parts

    return 1


def is_model_split(repo_id: str, filename: str) -> bool:
    """True, если модель разбита на части."""
    return get_model_parts(repo_id, filename) > 1


# ---------------------------------------------------------------------------
# Пути и базовые утилиты
# ---------------------------------------------------------------------------


def get_models_dir() -> Path:
    """Каталог моделей (~/.ollidesk/models), создаёт при необходимости."""
    d = DEFAULT_MODELS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

def _partial_path_for_file(target: Path) -> Path:
    """``<filename>.partial`` — маркер для цельной модели."""
    return target.with_name(target.name + _PARTIAL_SUFFIX)


def _partial_path_for_split(stem: str, target_dir: Path) -> Path:
    """``<stem>.split.partial`` — маркер для split-модели."""
    return target_dir / f"{stem}{_PARTIAL_SUFFIX.replace('.partial', '.split.partial')}"

def _resolve_model_path(filename: str, target_dir: Path | None = None) -> Path | None:
    """Путь к модели: цельный файл или первая часть разбитой.

    Работает только с локальной ФС. Для split-моделей приоритет отдаём
    частям, а не «merged»-файлу: llama.cpp нативно работает со split GGUF,
    если передать первую часть.
    """
    target_dir = target_dir or get_models_dir()

    # 1. Split-модель: ищем первую часть.
    stem = filename.removesuffix(".gguf")
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem and m.group("idx") == "00001":
            return entry

    # 2. Цельный файл.
    merged = target_dir / filename
    if merged.exists():
        return merged
    return None

def get_file_status(
    filename: str,
    target_dir: Path | None = None,
    *,
    declared_size_mb: int = 0,
) -> str:
    """Статус файла модели: ``installed`` | ``broken`` | ``absent``.

    * ``installed`` — файл есть, размер ≥ 1 МБ и ≥ 50% от заявленного
      (если ``declared_size_mb`` задан), маркер ``.partial`` отсутствует;
    * ``broken`` — файл есть, но:
        * размер < 1 МБ (обрыв в начале);
        * размер < 50% от ``declared_size_mb`` (обрыв в середине);
        * маркер ``.partial`` присутствует (скачивание не завершено);
    * ``absent`` — файла нет.

    Args:
        filename: Имя файла модели (для split — первой части).
        target_dir: Каталог моделей.
        declared_size_mb: Ожидаемый размер из ``SLMModelInfo.size_mb``.
            ``0`` — не проверять пропорцию (только ≥ 1 МБ).

    Не обращается к HuggingFace — проверка чисто локальная.
    """
    target_dir = target_dir or get_models_dir()

    # 1. Маркер незавершённого скачивания.
    # Для split-модели проверяем `<stem>.split.partial`, для цельной —
    # `<filename>.partial`.
    path = _resolve_model_path(filename, target_dir)
    if path is None:
        # Даже если файла нет — маркер мог остаться после kill.
        if _detect_parts_from_local(filename, target_dir) > 1:
            stem = filename.removesuffix(".gguf")
            if _partial_path_for_split(stem, target_dir).exists():
                return "broken"
        if _partial_path_for_file(target_dir / filename).exists():
            return "broken"
        return "absent"

    # 2. Проверка маркера для существующего файла.
    is_split = _detect_parts_from_local(filename, target_dir) > 1
    if is_split:
        stem = filename.removesuffix(".gguf")
        if _partial_path_for_split(stem, target_dir).exists():
            return "broken"
    else:
        if _partial_path_for_file(path).exists():
            return "broken"

    # 3. Размер.
    try:
        size_mb = get_local_size_mb(filename, target_dir)
    except Exception:
        return "absent"

    if size_mb < 1:
        return "broken"

    if declared_size_mb > 0 and size_mb < declared_size_mb * 0.5:
        # Меньше половины заявленного — явный обрыв.
        return "broken"

    return "installed"

def is_model_installed(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,  # оставлен для совместимости, не используется
) -> bool:
    """True, если модель скачана И НЕ битая (размер ≥ 1 МБ).

    ВАЖНО: не обращается к HuggingFace. Проверка чисто локальная.
    Битые файлы (0 байт, обрыв) возвращают False — автовыбор моделей
    их не выберет, а UI покажет «⚠️ Битый файл».
    """
    return get_file_status(filename, target_dir) == "installed"
def get_model_path(
    filename: str,
    repo_id: str | None = None,  # оставлен для совместимости
) -> Path | None:
    """Путь к модели (для split — к первой части). Не ходит в сеть."""
    return _resolve_model_path(filename, None)


def get_local_size_mb(filename: str, target_dir: Path | None = None) -> int:
    """Размер модели на диске в МБ (сумма всех частей). 0, если нет."""
    target_dir = target_dir or get_models_dir()
    merged = target_dir / filename
    if merged.exists():
        try:
            return merged.stat().st_size // (1024 * 1024)
        except OSError:
            return 0

    stem = filename.removesuffix(".gguf")
    total = 0
    for entry in target_dir.iterdir():
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            with contextlib.suppress(OSError):
                total += entry.stat().st_size
    return total // (1024 * 1024)


# ---------------------------------------------------------------------------
# Скачивание разбитой модели
# ---------------------------------------------------------------------------


def _download_split_model(
    repo_id: str,
    filename: str,
    parts: int,
    target_dir: Path,
) -> Path:
    """Скачивает разбитую на N частей модель и возвращает путь к первой части.

    Части НЕ склеиваются. llama.cpp читает split GGUF сам — достаточно
    передать ему путь к первой части, остальные он найдёт в той же папке.
    """
    from huggingface_hub import snapshot_download

    stem = filename.removesuffix(".gguf")
    pattern = f"{stem}-*-of-{parts:05d}.gguf"

    logger.info(f"Скачивание разбитой модели {filename} ({parts} частей) из {repo_id}...")

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        allow_patterns=[pattern],
    )

    first_part: Path | None = None
    for i in range(1, parts + 1):
        name = _split_part_name(stem, i, parts)
        part_path = target_dir / name
        if not part_path.exists():
            raise FileNotFoundError(f"Часть модели не найдена после скачивания: {name}")
        try:
            part_size = part_path.stat().st_size
        except OSError as e:
            raise ValueError(f"Не удалось прочитать {name}: {e}") from e
        if part_size < _MIN_MODEL_SIZE_BYTES:
            raise ValueError(
                f"Часть {name} слишком мала ({part_size} байт < "
                f"{_MIN_MODEL_SIZE_BYTES}). Скачивание прервалось."
            )
        if i == 1:
            first_part = part_path
        logger.info(f"  ✓ Часть {i}/{parts}: {name}")

    assert first_part is not None
    logger.info(f"Разбитая модель готова: {first_part.name} (+ ещё {parts - 1} частей в той же папке)")
    return first_part

def compute_sha256(path: Path) -> str:
    """Считает SHA256 файла. Используется и при скачивании, и при проверке.

    Args:
        path: Путь к файлу.

    Returns:
        Hex-строка SHA256 (64 символа).

    Raises:
        OSError: Если файл не читается.
    """
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_expected_sha256(repo_id: str, filename: str) -> str | None:
    """Возвращает ожидаемый SHA256 файла из HF API (``siblings[].lfs.sha256``).
    Args:
        repo_id: HF-репозиторий.
        filename: Имя файла.
    Returns:
        Hex-строка SHA256 или ``None``, если HF недоступен / файл не LFS /
        SHA256 в карточке нет.
    Note:
        ``HfApi.model_info`` синхронный и может блокировать. Вызывающий
        код должен обернуть в ``asyncio.to_thread`` или вызвать из фонового
        потока.
    """
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        info = api.model_info(repo_id=repo_id, files_metadata=True)
        for sib in getattr(info, "siblings", None) or []:
            if getattr(sib, "rfilename", "") != filename:
                continue
            lfs = getattr(sib, "lfs", None)
            if isinstance(lfs, dict):
                return lfs.get("sha256")  # type: ignore[return-value]
            if lfs is not None:
                return getattr(lfs, "sha256", None)  # type: ignore[return-value]
            return None
    except Exception as e:
        logger.warning(f"Не удалось получить SHA256 для {repo_id}/{filename}: {e}")
        return None
    return None
def _verify_model_file(
    path: Path,
    repo_id: str,
    filename: str,
    *,
    verify_sha256: bool = False,
) -> None:
    """Проверяет, что скачанный файл — целый.

    Всегда:
      * размер ≥ ``_MIN_MODEL_SIZE_BYTES`` (ловит 0-байтные и обрывы).

    Опционально (``verify_sha256=True``):
      * SHA256 совпадает с ``siblings[].lfs.sha256`` из HF API.

    Raises:
        ValueError: файл битый (размер или SHA256).
    """
    if not path.exists():
        raise ValueError(f"Файл не найден после скачивания: {path}")

    try:
        size = path.stat().st_size
    except OSError as e:
        raise ValueError(f"Не удалось прочитать {path.name}: {e}") from e

    if size < _MIN_MODEL_SIZE_BYTES:
        raise ValueError(
            f"Файл {path.name} слишком мал ({size} байт < {_MIN_MODEL_SIZE_BYTES}). "
            f"Скачивание прервалось — удалите файл и повторите."
        )

    if not verify_sha256:
        return

    expected_sha = fetch_expected_sha256(repo_id, filename)
    if not expected_sha:
        logger.debug(f"SHA256 для {filename} не найден в HF — пропускаю сверку")
        return

    try:
        actual = compute_sha256(path)
    except OSError as e:
        logger.warning(f"SHA256 для {filename}: не удалось прочитать файл: {e}")
        return

    if actual != expected_sha:
        with contextlib.suppress(OSError):
            path.unlink()
        raise ValueError(
            f"SHA256 mismatch для {filename}: "
            f"ожидалось {expected_sha[:12]}…, получено {actual[:12]}…. "
            f"Файл удалён — повторите скачивание."
        )
    logger.info(f"SHA256 для {filename}: OK")

ProgressCb = Callable[[int, str], None]
"""Callback прогресса: (percent 0-100, message)."""


def _hf_file_url(repo_id: str, filename: str) -> str:
    """Прямой URL файла на HF (`https://huggingface.co/<repo>/resolve/main/<file>`)."""
    from huggingface_hub import hf_hub_url

    return hf_hub_url(repo_id=repo_id, filename=filename)


def _download_one_file(
    url: str,
    target: Path,
    *,
    progress_cb: ProgressCb | None = None,
    timeout_s: float = 300.0,
) -> None:
    """Скачивает один файл с resume и прогрессом.

    Логика:
      1. HEAD-запрос получает `Content-Length` (итоговый размер).
      2. Если `target` уже существует и меньше — докачиваем через
         `Range: bytes=<size>-`. Это resume после обрыва.
      3. Если `target.size == Content-Length` — файл уже скачан.
      4. Стрим: читаем чанки по 1 МБ, дозаписываем в файл, эмитим percent.

    Args:
        url: Прямой URL.
        target: Куда сохранять.
        progress_cb: (percent, message) — вызывается после каждого чанка.
        timeout_s: Таймаут на один сетевой вызов.

    Raises:
        RuntimeError: если сервер вернул не-200/206.
    """
    import requests

    target.parent.mkdir(parents=True, exist_ok=True)

    # 1. HEAD — Content-Length.
    head = requests.head(url, allow_redirects=True, timeout=timeout_s)
    head.raise_for_status()
    total = int(head.headers.get("Content-Length", 0))

    # 2. Resume: есть уже часть?
    downloaded = target.stat().st_size if target.exists() else 0
    if total > 0 and downloaded == total:
        if progress_cb:
            progress_cb(100, "уже скачана")
        return
    if total > 0 and downloaded > total:
        # Битый хвост больше заявленного — перекачиваем целиком.
        logger.warning(
            f"{target.name}: размер {downloaded} > Content-Length {total} — перекачиваю"
        )
        downloaded = 0
        with contextlib.suppress(OSError):
            target.unlink()

    headers: dict[str, str] = {}
    if downloaded > 0:
        headers["Range"] = f"bytes={downloaded}-"
        logger.info(f"{target.name}: resume с {downloaded} / {total} байт")
    else:
        logger.info(f"{target.name}: старт скачивания, {total} байт")

    mode = "ab" if downloaded > 0 else "wb"
    with requests.get(url, headers=headers, stream=True, timeout=timeout_s) as r:
        if r.status_code == 416 and downloaded > 0:
            # Range вне файла — значит всё скачано.
            if progress_cb:
                progress_cb(100, "уже скачана")
            return
        if r.status_code not in (200, 206):
            raise RuntimeError(
                f"HTTP {r.status_code} при скачивании {target.name} "
                f"(ожидалось 200/206)"
            )

        # Сервер мог проигнорировать Range (200 вместо 206) — начинаем заново.
        if downloaded > 0 and r.status_code == 200:
            logger.warning(
                f"{target.name}: сервер вернул 200 вместо 206 — начинаю заново"
            )
            downloaded = 0
            mode = "wb"

        chunk_size = 1 << 20  # 1 МБ
        last_pct = -1
        with open(target, mode) as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total > 0:
                    pct = min(100, int(100 * downloaded / total))
                    if pct != last_pct:
                        last_pct = pct
                        progress_cb(
                            pct,
                            f"{downloaded // (1 << 20)} / {total // (1 << 20)} МБ",
                        )

    if progress_cb and total > 0:
        # Финальный 100% (если по чанкам не дотянули из-за округления).
        progress_cb(100, "готово")


def download_model_with_progress(
    repo_id: str,
    filename: str,
    target_dir: Path | None = None,
    *,
    progress_cb: ProgressCb | None = None,
    verify_sha256: bool | None = None,
) -> Path:
    """Скачивает модель с прогрессом, resume и финальной проверкой.

    Отличия от `download_model`:
      * отдаёт реальный процент через `progress_cb`;
      * resume: если файл частично скачан — докачивает через `Range`;
      * для split-моделей эмитит прогресс по каждой части;
      * создаёт маркер ``.partial`` перед стримом и удаляет его **только**
        после успешной SHA256-проверки. Если процесс убили — маркер
        останется, и `get_file_status` вернёт ``broken``.

    Args:
        repo_id: HF-репозиторий.
        filename: Имя файла (первой части для split).
        target_dir: Каталог моделей.
        progress_cb: (percent 0-100, message).
        verify_sha256: None → из конфига; True/False — явно.

    Returns:
        Путь к первой части / единственному файлу.
    """
    target_dir = target_dir or get_models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    if verify_sha256 is None:
        try:
            from core.config import load_config

            verify_sha256 = bool(load_config().slm.verify_sha256_on_download)
        except Exception:
            verify_sha256 = True

    parts = get_model_parts(repo_id, filename)
    stem = filename.removesuffix(".gguf")

    if parts > 1:
        # Split-модель.
        split_marker = _partial_path_for_split(stem, target_dir)
        split_marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            split_marker.write_text("downloading", encoding="utf-8")
        except OSError as e:
            logger.warning(f"Не удалось создать маркер {split_marker}: {e}")

        first: Path | None = None
        try:
            for i in range(1, parts + 1):
                part_name = _split_part_name(stem, i, parts)
                url = _hf_file_url(repo_id, part_name)
                part_path = target_dir / part_name

                def _part_cb(
                    pct: int,
                    msg: str,
                    _i: int = i,
                    _n: int = parts,
                    _name: str = part_name,
                ) -> None:
                    if progress_cb is not None:
                        progress_cb(pct, f"часть {_i}/{_n} ({_name}) — {msg}")

                _download_one_file(url, part_path, progress_cb=_part_cb)
                try:
                    if part_path.stat().st_size < _MIN_MODEL_SIZE_BYTES:
                        raise ValueError(
                            f"Часть {part_name} слишком мала — скачивание прервалось"
                        )
                except OSError as e:
                    raise ValueError(f"Не удалось прочитать {part_name}: {e}") from e
                if i == 1:
                    first = part_path

            assert first is not None
            logger.info(f"Split-модель готова: {first.name} (+ ещё {parts - 1} частей)")
            # Удаляем маркер только после успеха.
            with contextlib.suppress(OSError):
                split_marker.unlink()
            return first
        except Exception:
            # При ошибке маркер оставляем — `get_file_status` вернёт «broken».
            raise

    # Цельный файл.
    url = _hf_file_url(repo_id, filename)
    target = target_dir / filename
    file_marker = _partial_path_for_file(target)
    file_marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_marker.write_text("downloading", encoding="utf-8")
    except OSError as e:
        logger.warning(f"Не удалось создать маркер {file_marker}: {e}")

    try:
        _download_one_file(url, target, progress_cb=progress_cb)
        # Финальная проверка целостности. При ошибке маркер НЕ удаляем —
        # `get_file_status` вернёт «broken», пользователь перекачает.
        _verify_model_file(target, repo_id, filename, verify_sha256=verify_sha256)
    except Exception:
        raise

    # Успех — убираем маркер.
    with contextlib.suppress(OSError):
        file_marker.unlink()
    logger.info(f"Модель сохранена: {target}")
    return target

def download_model(
    repo_id: str,
    filename: str,
    target_dir: Path | None = None,
    *,
    verify_sha256: bool | None = None,
) -> Path:
    """Скачивает модель из Hugging Face.

    Если локально уже есть файл-«битый» (размер < 1 МБ) — удаляет и
    качает заново. После скачивания проверяет размер (всегда) и
    SHA256 (если ``verify_sha256=True``).

    Для разбитых моделей возвращает путь к ПЕРВОЙ части; остальные
    llama.cpp подтянет сам.

    Args:
        repo_id: HF-репозиторий.
        filename: Имя файла (первой части для split-модели).
        target_dir: Каталог моделей (по умолчанию ~/.ollidesk/models).
        verify_sha256: ``None`` — взять из ``AppConfig.slm.verify_sha256_on_download``;
            ``True`` — сверять SHA256; ``False`` — только размер.

    Raises:
        ValueError: файл битый (размер или SHA256).
    """
    target_dir = target_dir or get_models_dir()
    # [UI-2025] verify_sha256=None — берём из конфига (по умолчанию True).
    if verify_sha256 is None:
        try:
            from core.config import load_config

            verify_sha256 = bool(load_config().slm.verify_sha256_on_download)
        except Exception:
            verify_sha256 = True  # безопасный default

    target_dir.mkdir(parents=True, exist_ok=True)

    parts = get_model_parts(repo_id, filename)

    if parts > 1:
        stem = filename.removesuffix(".gguf")
        first = target_dir / _split_part_name(stem, 1, parts)
        if first.exists():
            # [UI-2025] Битый файл (< 1 МБ) — удаляем и перекачиваем.
            try:
                if first.stat().st_size < _MIN_MODEL_SIZE_BYTES:
                    logger.warning(
                        f"Битый файл {first.name} (< 1 МБ) — удаляю и перекачиваю"
                    )
                    first.unlink()
                else:
                    logger.info(f"Разбитая модель уже скачана: {first}")
                    return first
            except OSError as e:
                logger.warning(f"Не удалось проверить {first}: {e} — перекачиваю")
                with contextlib.suppress(OSError):
                    first.unlink()

        stale = target_dir / filename
        if stale.exists():
            logger.info(
                f"Удаляю устаревший объединённый файл: {stale.name} "
                f"(будет заменён split-частями)"
            )
            with contextlib.suppress(OSError):
                stale.unlink()

        return _download_split_model(repo_id, filename, parts, target_dir)

    # Цельный файл
    target_path = target_dir / filename
    if target_path.exists():
        # [UI-2025] Битый файл — удаляем и перекачиваем.
        try:
            if target_path.stat().st_size < _MIN_MODEL_SIZE_BYTES:
                logger.warning(
                    f"Битый файл {target_path.name} (< 1 МБ) — удаляю и перекачиваю"
                )
                target_path.unlink()
            else:
                logger.info(f"Модель уже скачана: {target_path}")
                if verify_sha256:
                    _verify_model_file(target_path, repo_id, filename, verify_sha256=True)
                return target_path
        except OSError as e:
            logger.warning(f"Не удалось проверить {target_path}: {e} — перекачиваю")
            with contextlib.suppress(OSError):
                target_path.unlink()

    from huggingface_hub import hf_hub_download

    # [UI-2025] Маркер .partial — как в download_model_with_progress.
    file_marker = _partial_path_for_file(target_path)
    try:
        file_marker.parent.mkdir(parents=True, exist_ok=True)
        file_marker.write_text("downloading", encoding="utf-8")
    except OSError as e:
        logger.warning(f"Не удалось создать маркер {file_marker}: {e}")

    try:
        logger.info(f"Скачивание {filename} из {repo_id}...")
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(target_dir),
        )
        downloaded_path = Path(downloaded)
        _verify_model_file(
            downloaded_path,
            repo_id,
            filename,
            verify_sha256=verify_sha256,
        )
    except Exception:
        raise

    with contextlib.suppress(OSError):
        file_marker.unlink()
    logger.info(f"Модель сохранена: {downloaded_path}")
    return downloaded_path

# ---------------------------------------------------------------------------
# Удаление
# ---------------------------------------------------------------------------


def delete_model(
    filename: str,
    target_dir: Path | None = None,
    repo_id: str | None = None,  # совместимость
) -> bool:
    """Удаляет модель (цельный файл и/или все части разбитой). Не ходит в сеть."""
    target_dir = target_dir or get_models_dir()
    deleted_any = False

    merged = target_dir / filename
    if merged.exists():
        try:
            merged.unlink()
            logger.info(f"Модель удалена: {merged}")
            deleted_any = True
        except OSError as e:
            logger.error(f"Ошибка удаления {merged}: {e}")

    stem = filename.removesuffix(".gguf")
    for entry in list(target_dir.iterdir()):
        m = _SPLIT_PART_RE.match(entry.name)
        if m and m.group("stem") == stem:
            try:
                entry.unlink()
                logger.info(f"Удалена часть: {entry.name}")
                deleted_any = True
            except OSError as e:
                logger.error(f"Ошибка удаления части {entry}: {e}")

    return deleted_any


# ---------------------------------------------------------------------------
# Embedding-модель
# ---------------------------------------------------------------------------


def download_embed_model(target_dir: Path | None = None) -> Path | None:
    """Скачивает embedding-модель (общую для RAG)."""
    try:
        return download_model(EMBED_MODEL_REPO, EMBED_MODEL_FILE, target_dir)
    except Exception as e:
        logger.warning(f"Не удалось скачать embedding-модель: {e}")
        return None


# ---------------------------------------------------------------------------
# Получение путей для текущей активной SLM
# ---------------------------------------------------------------------------


def get_model_paths() -> tuple[Path | None, Path | None]:
    """Возвращает (chat_path, embed_path) для активной SLM."""
    chat_path: Path | None = None
    embed_path: Path | None = None

    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info is not None:
            chat_path = _resolve_model_path(info.file)
    except Exception as e:
        logger.debug(f"Не удалось получить конфиг для SLM: {e}")

    if chat_path is None:
        legacy = get_models_dir() / _LEGACY_CHAT_MODEL_FILE
        if legacy.exists():
            logger.info(f"Использую legacy SLM: {legacy.name}")
            chat_path = legacy

    embed = get_models_dir() / EMBED_MODEL_FILE
    if embed.exists():
        embed_path = embed

    return chat_path, embed_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI: скачать активную chat-модель + embedding."""
    try:
        from core.config import load_config

        cfg = load_config()
        info = cfg.slm.get_selected_info()
        if info is not None:
            path = download_model(info.repo, info.file)
            print(f"Chat-модель ({info.display_name}): {path}")
        else:
            print("Активная SLM-модель не выбрана в конфиге.")
    except Exception as e:
        print(f"Ошибка загрузки chat-модели: {e}")

    embed = download_embed_model()
    if embed:
        print(f"Embedding-модель: {embed}")
    else:
        print("Embedding-модель не скачана (опционально).")


if __name__ == "__main__":
    main()
