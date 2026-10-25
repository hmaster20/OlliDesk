"""Скачивает mermaid.min.js из jsDelivr в ui/web_editor/vendor/."""

import urllib.request
from pathlib import Path

MERMAID_VERSION = "10.9.1"
URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
VENDOR_DIR = Path(__file__).parent / "vendor"
TARGET = VENDOR_DIR / "mermaid.min.js"


def main() -> None:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю Mermaid {MERMAID_VERSION}...")
    with urllib.request.urlopen(URL) as resp:
        data: bytes = resp.read()
    TARGET.write_bytes(data)
    print(f"  Сохранено: {TARGET} ({len(data) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()