"""Регрессионные тесты verifier.

Ловят реальный ложный срабатыватель: `_PATH_RE` с альтернативой `cs` раньше
`csproj` обрезал `LocalMessenger.Tests.csproj` до `LocalMessenger.Tests.cs` —
verifier объявлял путь «вне индекса», это запускало лишнюю регенерацию
(~4 мин на CPU) и вышибало оркестратор по таймауту.
"""

from __future__ import annotations

from agents.evidence import EvidenceStore
from agents.verifier import verify
from fs.intel.graph import DependencyGraph


def _check(answer: str, known_paths: set[str], known_symbols: set[str] | None = None):
    return verify(
        answer,
        graph=DependencyGraph(),
        evidence=EvidenceStore(),
        known_paths=known_paths,
        known_symbols=known_symbols or set(),
        strict=False,
    )


class TestPathRegex:
    def test_csproj_not_truncated_to_cs(self):
        res = _check(
            "Сборка идёт из LocalMessenger.Tests.csproj",
            known_paths={"LocalMessenger.Tests/LocalMessenger.Tests.csproj"},
        )
        assert res.missing_paths == []

    def test_csproj_bare_name_endswith_match(self):
        res = _check(
            "См. LocalMessenger.csproj и LocalMessenger.Tests.csproj",
            known_paths={
                "LocalMessenger/LocalMessenger.csproj",
                "LocalMessenger.Tests/LocalMessenger.Tests.csproj",
            },
        )
        assert res.missing_paths == []

    def test_plain_cs_file_still_matched(self):
        res = _check("Файл app/main.cs важен", known_paths={"app/main.cs"})
        assert res.missing_paths == []

    def test_unknown_path_flagged(self):
        res = _check("См. ghost/missing.cs", known_paths={"app/main.cs"})
        assert "ghost/missing.cs" in res.missing_paths

    def test_path_with_line_number(self):
        res = _check("См. app/main.cs:42", known_paths={"app/main.cs"})
        assert res.missing_paths == []


class TestSymbolWhitelist:
    def test_nunit_not_flagged(self):
        res = _check("Тесты написаны на NUnit", known_paths=set())
        assert "NUnit" not in res.unknown_symbols

    def test_truncated_prefix_of_known_symbol_passes(self):
        # 7B отдаёт усечённые токены ('SaveSett'); подстрочная сверка
        # с полным именем символа из графа должна их пропускать.
        res = _check(
            "Класс SaveSett отвечает за конфигурацию",
            known_paths=set(),
            known_symbols={"Messenger::SaveSettings"},
        )
        assert "SaveSett" not in res.unknown_symbols

    def test_unknown_symbol_flagged(self):
        res = _check("Класс ZebraProcessor собирает статистику", known_paths=set())
        assert "ZebraProcessor" in res.unknown_symbols
