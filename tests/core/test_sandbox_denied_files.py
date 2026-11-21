"""Тесты 0.1.2: запрет секретных файлов в `SandboxedPathResolver`."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.sandbox import SandboxedPathResolver, is_denied_file


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("TOKEN=abc123", encoding="utf-8")
    (tmp_path / ".env.local").write_text("X=1", encoding="utf-8")
    (tmp_path / "server.pem").write_text("---BEGIN---", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("priv", encoding="utf-8")
    (tmp_path / "id_rsa.pub").write_text("pub", encoding="utf-8")
    (tmp_path / "normal.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "config.key").write_text("k", encoding="utf-8")
    (tmp_path / "sub" / "app.py").write_text("pass", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# is_denied_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".netrc",
        ".pgpass",
        ".npmrc",
        ".pypirc",
        ".htpasswd",
        ".git-credentials",
        "server.pem",
        "private.key",
        "bundle.p12",
        "cert.pfx",
        "key.ppk",
        "pass.kdbx",
        "keystore.jks",
        "app.keystore",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.yaml",
        "secrets.yml",
    ],
)
def test_is_denied_file_true(name: str) -> None:
    assert is_denied_file(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "main.py",
        "README.md",
        "config.yaml",
        "app.settings",
        "env.txt",
        "tokenizer.py",
        "my.env.backup",
    ],
)
def test_is_denied_file_false(name: str) -> None:
    assert is_denied_file(name) is False


# ---------------------------------------------------------------------------
# resolve — блокирует чтение
# ---------------------------------------------------------------------------


def test_resolve_blocks_env(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve(".env") is None


def test_resolve_blocks_env_local(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve(".env.local") is None


def test_resolve_blocks_pem(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve("server.pem") is None


def test_resolve_blocks_id_rsa(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve("id_rsa") is None
    assert r.resolve("id_rsa.pub") is None


def test_resolve_blocks_nested_secret(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve("sub/config.key") is None


def test_resolve_allows_normal_file(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve("normal.py") is not None
    assert r.resolve("sub/app.py") is not None


# ---------------------------------------------------------------------------
# resolve — allow_sensitive=True (для write_file)
# ---------------------------------------------------------------------------


def test_resolve_allows_sensitive_when_asked(project: Path) -> None:
    r = SandboxedPathResolver(project)
    resolved = r.resolve(".env", allow_sensitive=True)
    assert resolved is not None
    assert resolved.name == ".env"


def test_resolve_still_blocks_escape_even_with_allow_sensitive(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve("../etc/passwd", allow_sensitive=True) is None


def test_resolve_still_blocks_dot_git_with_allow_sensitive(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.resolve(".git/config", allow_sensitive=True) is None


# ---------------------------------------------------------------------------
# contains
# ---------------------------------------------------------------------------


def test_contains_blocks_secret(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.contains(project / "server.pem") is False


def test_contains_allows_normal(project: Path) -> None:
    r = SandboxedPathResolver(project)
    assert r.contains(project / "normal.py") is True
