"""Тесты парсера текстовых вызовов инструментов."""

from agents.tool_call_parser import extract_tool_calls, schema_param_names


def _schemas(*tools: tuple[str, list[str], list[str]]) -> list[dict]:
    """Собирает список схем вида (name, required, optional)."""
    result = []
    for name, required, optional in tools:
        props = {p: {"type": "string"} for p in [*required, *optional]}
        schema: dict = {
            "name": name,
            "description": "",
            "parameters": {"type": "object", "properties": props},
        }
        if required:
            schema["parameters"]["required"] = required
        result.append({"type": "function", "function": schema})
    return result


READ_FILE = _schemas(("read_file", ["path"], []))
LIST_DIR = _schemas(("list_directory", ["path"], []))
SEARCH = _schemas(("search_codebase", ["query"], ["n_results", "language"]))


class TestSchemaParamNames:
    def test_required_first_then_optional(self):
        names = schema_param_names(SEARCH)
        assert names["search_codebase"] == ["query", "n_results", "language"]

    def test_empty_tools(self):
        assert schema_param_names(None) == {}
        assert schema_param_names([]) == {}


class TestJsonParsing:
    def test_simple_json(self):
        calls = extract_tool_calls('{"name": "read_file", "arguments": {"path": "a.txt"}}')
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].arguments == {"path": "a.txt"}

    def test_json_in_code_fence(self):
        text = '```json\n{"name": "read_file", "arguments": {"path": "a.txt"}}\n```'
        calls = extract_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].arguments == {"path": "a.txt"}

    def test_json_without_arguments(self):
        calls = extract_tool_calls('{"name": "get_current_datetime"}')
        assert len(calls) == 1
        assert calls[0].arguments == {}

    def test_json_filtered_by_known_tools(self):
        text = '{"name": "unknown_tool", "arguments": {}}'
        assert extract_tool_calls(text, READ_FILE) == []


class TestFunctionCallParsing:
    def test_positional(self):
        calls = extract_tool_calls('list_directory("D:\\project")', LIST_DIR)
        assert len(calls) == 1
        assert calls[0].name == "list_directory"
        assert calls[0].arguments == {"path": "D:\\project"}

    def test_keyword_quoted(self):
        calls = extract_tool_calls('read_file(path="a.txt")', READ_FILE)
        assert calls[0].arguments == {"path": "a.txt"}

    def test_keyword_unquoted(self):
        calls = extract_tool_calls("read_file(path=a.txt)", READ_FILE)
        assert calls[0].arguments == {"path": "a.txt"}

    def test_multiple_positional(self):
        calls = extract_tool_calls('search_codebase("фикс", 5)', SEARCH)
        assert calls[0].arguments == {"query": "фикс", "n_results": 5}

    def test_empty_arguments(self):
        calls = extract_tool_calls("list_directory()", LIST_DIR)
        assert calls[0].arguments == {}

    def test_react_action_prefix(self):
        text = '## Action\nlist_directory("D:\\project")'
        calls = extract_tool_calls(text, LIST_DIR)
        assert len(calls) == 1
        assert calls[0].name == "list_directory"

    def test_windows_path_double_backslash(self):
        calls = extract_tool_calls('read_file("D:\\\\new\\\\test")', READ_FILE)
        assert calls[0].arguments == {"path": "D:\\new\\test"}

    def test_windows_path_single_backslash_preserved(self):
        calls = extract_tool_calls('read_file("D:\\new\\test")', READ_FILE)
        assert calls[0].arguments == {"path": "D:\\new\\test"}

    def test_unknown_name_ignored(self):
        assert extract_tool_calls('other_tool("x")', READ_FILE) == []

    def test_no_tools_no_function_parsing(self):
        assert extract_tool_calls('read_file("a.txt")', None) == []

    def test_not_confused_by_longer_name(self):
        calls = extract_tool_calls('read_file_extra("a.txt")', READ_FILE)
        assert calls == []


class TestMultipleCalls:
    def test_two_calls(self):
        text = 'list_directory("a")\nread_file("b.txt")'
        tools = _schemas(("list_directory", ["path"], []), ("read_file", ["path"], []))
        calls = extract_tool_calls(text, tools)
        assert [c.name for c in calls] == ["list_directory", "read_file"]

    def test_json_wins_over_function_call(self):
        text = '{"name": "read_file", "arguments": {"path": "json.txt"}}\nread_file("func.txt")'
        calls = extract_tool_calls(text, READ_FILE)
        assert len(calls) == 1
        assert calls[0].arguments == {"path": "json.txt"}
