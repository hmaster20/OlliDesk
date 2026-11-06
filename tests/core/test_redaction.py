"""Тесты `core.redaction` (0.1.2)."""

from __future__ import annotations

import pytest

from core.redaction import (
    contains_secret,
    is_secret,
    redact,
    redact_dict,
    redact_exc,
    redact_only,
)

# ---------------------------------------------------------------------------
# API-ключи
# ---------------------------------------------------------------------------


def test_redacts_openai_api_key() -> None:
    secret = "sk-proj-" + "a" * 30
    cleaned, hits = redact(f"Use {secret} as key")
    assert secret not in cleaned
    assert "[REDACTED:api_key]" in cleaned
    assert "api_key" in hits


def test_redacts_hf_token() -> None:
    secret = "hf_" + "A" * 30
    cleaned, hits = redact(f"token={secret}")
    assert secret not in cleaned
    assert "hf_token" in hits


def test_redacts_github_token() -> None:
    secret = "ghp_" + "b" * 30
    cleaned, _ = redact(f"gh {secret} used")
    assert secret not in cleaned


def test_redacts_github_pat() -> None:
    secret = "github_pat_" + "C" * 30
    cleaned, _ = redact(f"auth: {secret}")
    assert secret not in cleaned


def test_redacts_aws_access_key() -> None:
    secret = "AKIA" + "A" * 16
    cleaned, _ = redact(f"aws={secret}")
    assert secret not in cleaned


def test_redacts_slack_token() -> None:
    secret = "xoxb-1234567890-abcdef"
    cleaned, _ = redact(f"slack: {secret}")
    assert secret not in cleaned


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def test_redacts_jwt() -> None:
    secret = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghij"
    cleaned, _ = redact(f"Bearer {secret}")
    assert secret not in cleaned
    assert "jwt" in cleaned


def test_does_not_redact_short_eyj() -> None:
    text = "eyJhbGci.eyJzdWIi.abc"
    cleaned, _ = redact(text)
    assert cleaned == text  # слишком короткие куски — не JWT


# ---------------------------------------------------------------------------
# Private keys
# ---------------------------------------------------------------------------


def test_redacts_rsa_private_key() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
    cleaned, hits = redact(f"Ключ:\n{pem}\nконец")
    assert "MIIEpAIBAAKCAQEA" not in cleaned
    assert "[REDACTED:private_key]" in cleaned
    assert "private_key" in hits


def test_redacts_ec_private_key() -> None:
    pem = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEE...\n-----END EC PRIVATE KEY-----"
    cleaned, _ = redact(pem)
    assert "MHcCAQEE" not in cleaned


# ---------------------------------------------------------------------------
# URL / DSN с паролем
# ---------------------------------------------------------------------------


def test_redacts_password_in_dsn() -> None:
    dsn = "postgres://user:s3cr3tpass@db.example.com:5432/mydb"
    cleaned, hits = redact(f"DATABASE_URL={dsn}")
    assert "s3cr3tpass" not in cleaned
    assert "user" in cleaned
    assert "db.example.com" in cleaned
    assert "url_password" in hits


def test_redacts_password_in_https_url() -> None:
    url = "https://admin:Pa$$w0rd@internal.example.com/api"
    cleaned, _ = redact(url)
    assert "Pa$$w0rd" not in cleaned
    assert "internal.example.com" in cleaned


# ---------------------------------------------------------------------------
# Git token в URL
# ---------------------------------------------------------------------------


def test_redacts_git_token_in_url() -> None:
    url = "https://ghp_abcdef123456@github.com/org/repo.git"
    cleaned, hits = redact(f"remote {url}")
    assert "ghp_abcdef123456" not in cleaned
    assert "github.com" in cleaned
    assert "git_token" in hits or "gh_token" in hits


# ---------------------------------------------------------------------------
# Env-переменные
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("TOKEN=abcdef123456", "abcdef123456"),
        ("PASSWORD=hunter2hunter2", "hunter2hunter2"),
        ("SECRET_KEY=verylongsecretvalue", "verylongsecretvalue"),
        ("API_KEY: abcdefghij", "abcdefghij"),
        ('"API_KEY": "abcdefghij"', "abcdefghij"),
        ("CLIENT_SECRET=abcdef123", "abcdef123"),
    ],
)
def test_redacts_env_secret_values(line: str, secret: str) -> None:
    cleaned, hits = redact(f"log: {line}")
    assert secret not in cleaned
    assert "env_secret" in hits


def test_does_not_redact_short_env_value() -> None:
    text = "TOKEN=abc"
    cleaned, _ = redact(text)
    assert cleaned == text  # 3 символа — не секрет


def test_does_not_redact_non_secret_env() -> None:
    text = "task_id=abcdef123456"
    cleaned, _ = redact(text)
    assert cleaned == text  # task_id не в списке секретных имён


# ---------------------------------------------------------------------------
# Ложные срабатывания / не-секреты
# ---------------------------------------------------------------------------


def test_does_not_touch_plain_text() -> None:
    text = "Обычный текст без секретов: hello, world"
    assert redact(text) == (text, [])


def test_does_not_touch_tokenizer() -> None:
    # «tokenizer» начинается с «token», но \bTOKEN\b не матчит «tokenizer».
    text = "tokenizer=bert-base"
    assert redact(text)[0] == text


def test_does_not_touch_api_key_count() -> None:
    text = "api_key_count=42"
    assert redact(text)[0] == text


# ---------------------------------------------------------------------------
# contains_secret / redact_only
# ---------------------------------------------------------------------------


def test_contains_secret_true() -> None:
    assert contains_secret("token=sk-" + "x" * 30) is True


def test_contains_secret_false() -> None:
    assert contains_secret("hello world") is False


def test_contains_secret_does_not_mutate() -> None:
    text = "sk-" + "x" * 30
    original = str(text)
    contains_secret(text)
    assert text == original  # входная строка не изменилась


def test_redact_only_returns_str() -> None:
    cleaned = redact_only("token=sk-" + "x" * 30)
    assert isinstance(cleaned, str)
    assert "sk-" not in cleaned


# ---------------------------------------------------------------------------
# Идемпотентность
# ---------------------------------------------------------------------------


def test_redact_is_idempotent() -> None:
    text = "TOKEN=abcdef123456 and sk-" + "x" * 30 + " and postgres://u:p@host/db"
    once, _ = redact(text)
    twice, hits2 = redact(once)
    assert once == twice
    assert hits2 == []  # второй проход не находит секретов


def test_redact_empty_string() -> None:
    assert redact("") == ("", [])


def test_redact_only_empty_string() -> None:
    assert redact_only("") == ""


# ---------------------------------------------------------------------------
# redact_dict
# ---------------------------------------------------------------------------


def test_redact_dict_simple() -> None:
    d = {"TOKEN": "abcdef123456", "name": "test"}
    out = redact_dict(d)
    assert "abcdef123456" not in out["TOKEN"]
    assert out["name"] == "test"


def test_redact_dict_nested() -> None:
    d = {"outer": {"inner": "PASSWORD=hunter2hunter2"}}
    out = redact_dict(d)
    assert "hunter2hunter2" not in out["outer"]["inner"]


def test_redact_dict_list() -> None:
    d = {"items": ["TOKEN=abcdef123456", "clean value"]}
    out = redact_dict(d)
    assert "abcdef123456" not in out["items"][0]
    assert out["items"][1] == "clean value"


def test_redact_dict_preserves_keys() -> None:
    """Ключи не редактируются — это структура."""
    d = {"PASSWORD": "abcdef123456"}
    out = redact_dict(d)
    assert "PASSWORD" in out


def test_redact_dict_non_str_values_unchanged() -> None:
    d = {"count": 42, "flag": True, "nothing": None, "ratio": 3.14}
    out = redact_dict(d)
    assert out == d


def test_redact_dict_top_level_string() -> None:
    """redact_dict работает и для строки верхнего уровня."""
    out = redact_dict("TOKEN=abcdef123456")
    assert "abcdef123456" not in out


# ---------------------------------------------------------------------------
# redact_exc
# ---------------------------------------------------------------------------


def test_redact_exc_redacts_secret() -> None:
    e = ValueError("connection failed: token=abcdef123456")
    text = redact_exc(e)
    assert "abcdef123456" not in text
    assert "[REDACTED" in text


def test_redact_exc_no_secret() -> None:
    e = ValueError("simple error")
    assert redact_exc(e) == "simple error"


def test_redact_exc_empty_message() -> None:
    e = ValueError()
    assert redact_exc(e) == "ValueError"


def test_redact_exc_broken_str() -> None:
    class BrokenError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("boom")

    e = BrokenError()
    # Не падаем — fallback на type name.
    assert redact_exc(e) == "BrokenError"


# ---------------------------------------------------------------------------
# is_secret
# ---------------------------------------------------------------------------


def test_is_secret_true_for_sk_key() -> None:
    assert is_secret("sk-" + "x" * 30) is True


def test_is_secret_true_for_jwt() -> None:
    assert is_secret("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghij") is True


def test_is_secret_true_for_long_random_token() -> None:
    # 32+ chars, [A-Za-z0-9_-], mixed case + digits, unique
    token = "aB3-xY9_zP2-qR7-sT4-vW1-uK8-nM5"
    assert is_secret(token) is True


def test_is_secret_false_for_model_name() -> None:
    # Имя модели содержит точки — не проходит по алфавиту.
    assert is_secret("qwen2.5-coder-7b-instruct") is False


def test_is_secret_false_for_path() -> None:
    assert is_secret("agents/agent_loop.py") is False


def test_is_secret_false_for_hex_commit_hash() -> None:
    # Hex-хеш без букв в верхнем регистре не проходит по has_alpha.
    assert is_secret("a" * 40) is False


def test_is_secret_false_for_plain_text() -> None:
    assert is_secret("Hello world, this is a normal sentence.") is False


def test_is_secret_false_for_empty() -> None:
    assert is_secret("") is False


def test_is_secret_false_for_short_value() -> None:
    assert is_secret("abc123") is False
