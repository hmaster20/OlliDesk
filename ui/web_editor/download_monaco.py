"""Скачивает и распаковывает Monaco Editor из npm registry.

Использует только стандартную библиотеку Python (urllib + tarfile).
Не требует npm или Node.js.
"""

import io
import shutil
import tarfile
import urllib.request
from pathlib import Path

MONACO_VERSION = "0.45.0"
MONACO_URL = (
    f"https://registry.npmjs.org/monaco-editor/-/monaco-editor-{MONACO_VERSION}.tgz"
)
VENDOR_DIR = Path(__file__).parent / "vendor"
TARGET_DIR = VENDOR_DIR / "monaco"


def download_tarball(url: str) -> bytes:
    """Скачивает tarball по URL."""
    print(f"Скачиваю Monaco Editor v{MONACO_VERSION}...")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    print(f"  Загружено {len(data) / 1024 / 1024:.1f} MB")
    return data


def extract_tarball(data: bytes, target: Path) -> None:
    """Распаковывает tarball и перемещает package/ -> target."""
    if target.exists():
        print(f"  Удаляю существующий {target}")
        shutil.rmtree(target)

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(path=VENDOR_DIR)

    extracted = VENDOR_DIR / "package"
    if extracted.exists():
        extracted.rename(target)
        print(f"  Распаковано в {target}")
    else:
        raise FileNotFoundError("Внутри tarball не найден каталог package/")


def main() -> None:
    """Основная функция: скачать и распаковать Monaco Editor."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    data = download_tarball(MONACO_URL)
    extract_tarball(data, TARGET_DIR)
    print("Готово! Monaco Editor установлен локально.")


if __name__ == "__main__":
    main()
