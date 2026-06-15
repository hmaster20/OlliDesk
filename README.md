# OlliDesk

Local AI coding assistant for Windows 10.

OlliDesk is a desktop IDE-like interface for local AI code assistants running through **Ollama**. It provides a project-aware chat interface, a built-in code browser, and a web-based Monaco editor.

## Quick Start

### From Source

```bash
git clone <repo>
cd ollidesk
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
python main.py
```

### Standalone (Windows)

1. Download the latest `OlliDesk.exe` from Releases.
2. Run it (no Python required).
3. Ensure Ollama is running (`ollama serve`) with a model pulled (e.g. `ollama pull qwen2.5-coder:7b`).

## Requirements

- **Ollama** — running locally with at least one model
- **Python 3.11+** (source only)

## Usage

- **File → Open Project** — select a directory to work on
- **Chat** — ask the LLM questions, generate and refactor code
- **Editor** — browse files, edit with Monaco (VS Code engine)
- **Sessions** — chat history is auto-saved per project

Configuration and data are stored in `~/.ollidesk/`.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for full documentation.

## Build Standalone

```bash
pip install pyinstaller
python scripts/download_monaco.py
pyinstaller --clean ollidesk.spec
```

Or run `build.bat`.

## Tech Stack

- **Python 3.11+** / **PySide6** (Qt6)
- **Monaco Editor** (browser-based via QWebEngineView)
- **Ollama** (local LLM inference)
- **ChromaDB** (vector store for RAG)
- **loguru** / **pydantic** / **httpx**
