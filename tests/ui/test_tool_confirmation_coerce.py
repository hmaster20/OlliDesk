"""Тесты ToolConfirmationDialog._coerce_value — сохранение типов.

Редактирование аргументов (React.md 9.2): значения вводятся текстом, но
исходный тип восстанавливается. При некорректном вводе значение остаётся
строкой — никогда не падаем.

Полноценный UI-тест (QTableWidget, нажатия кнопок) без pytest-qt не сделать,
а `_coerce_value` — статический метод, тестируется без QApplication.
"""

from __future__ import annotations

import pytest

from ui.dialogs.tool_confirmation_dialog import ToolConfirmationDialog


class TestCoerceInt:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("42", 42),
            ("-7", -7),
            ("0", 0),
            ("  15  ", 15),
        ],
    )
    def test_valid(self, raw: str, expected: int) -> None:
        assert ToolConfirmationDialog._coerce_value(raw, "int") == expected

    @pytest.mark.parametrize("raw", ["abc", "3.14", "", "0x10"])
    def test_invalid_falls_back_to_string(self, raw: str) -> None:
        result = ToolConfirmationDialog._coerce_value(raw, "int")
        assert isinstance(result, str)
        assert result == raw


class TestCoerceFloat:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("3.14", 3.14),
            ("-0.5", -0.5),
            ("1e-3", 0.001),
            ("0", 0.0),
        ],
    )
    def test_valid(self, raw: str, expected: float) -> None:
        assert ToolConfirmationDialog._coerce_value(raw, "float") == expected

    @pytest.mark.parametrize("raw", ["abc", "", "3,14"])  # запятая — не точка
    def test_invalid_falls_back_to_string(self, raw: str) -> None:
        result = ToolConfirmationDialog._coerce_value(raw, "float")
        assert isinstance(result, str)


class TestCoerceBool:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_valid(self, raw: str, expected: bool) -> None:
        assert ToolConfirmationDialog._coerce_value(raw, "bool") is expected

    @pytest.mark.parametrize("raw", ["maybe", "2", ""])
    def test_invalid_falls_back_to_string(self, raw: str) -> None:
        result = ToolConfirmationDialog._coerce_value(raw, "bool")
        assert isinstance(result, str)


class TestCoerceList:
    def test_valid(self) -> None:
        assert ToolConfirmationDialog._coerce_value("[1, 2, 3]", "list") == [1, 2, 3]

    def test_empty(self) -> None:
        assert ToolConfirmationDialog._coerce_value("[]", "list") == []

    def test_nested(self) -> None:
        result = ToolConfirmationDialog._coerce_value(
            '[{"a": 1}, [2, 3]]', "list"
        )
        assert result == [{"a": 1}, [2, 3]]

    def test_invalid_falls_back_to_string(self) -> None:
        result = ToolConfirmationDialog._coerce_value("[broken", "list")
        assert isinstance(result, str)
        assert result == "[broken"


class TestCoerceDict:
    def test_valid(self) -> None:
        assert ToolConfirmationDialog._coerce_value('{"path": "a.py"}', "dict") == {
            "path": "a.py"
        }

    def test_empty(self) -> None:
        assert ToolConfirmationDialog._coerce_value("{}", "dict") == {}

    def test_unicode_kept(self) -> None:
        result = ToolConfirmationDialog._coerce_value('{"key": "значение"}', "dict")
        assert result == {"key": "значение"}

    def test_invalid_falls_back_to_string(self) -> None:
        result = ToolConfirmationDialog._coerce_value("{bad json", "dict")
        assert isinstance(result, str)


class TestCoerceNone:
    def test_null(self) -> None:
        assert ToolConfirmationDialog._coerce_value("null", "NoneType") is None

    def test_invalid_falls_back_to_string(self) -> None:
        result = ToolConfirmationDialog._coerce_value("not-null", "NoneType")
        assert isinstance(result, str)


class TestCoerceOtherTypes:
    """str и неизвестные типы — значение остаётся строкой как есть."""

    def test_str(self) -> None:
        assert ToolConfirmationDialog._coerce_value("hello", "str") == "hello"

    def test_str_with_json_like_content(self) -> None:
        # Строка "не трогается", даже если похожа на JSON.
        assert ToolConfirmationDialog._coerce_value('{"x": 1}', "str") == '{"x": 1}'

    def test_unknown_type(self) -> None:
        assert ToolConfirmationDialog._coerce_value("value", "custom") == "value"