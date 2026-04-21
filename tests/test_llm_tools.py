"""Tests for pester.llm.tools — neutral format and per-provider converters."""

from __future__ import annotations

from pester.llm.tools import VAULT_TOOLS, to_anthropic, to_openai


class TestVaultTools:
    """Neutral VAULT_TOOLS format is valid."""

    def test_all_tools_have_required_keys(self):
        for tool in VAULT_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing 'description'"
            assert "parameters" in tool, f"Tool {tool['name']} missing 'parameters'"

    def test_tool_count(self):
        assert len(VAULT_TOOLS) == 7

    def test_tool_names(self):
        names = {t["name"] for t in VAULT_TOOLS}
        expected = {
            "list_actions",
            "add_action",
            "complete_action",
            "search_vault",
            "get_document",
            "get_health",
            "reschedule_action",
        }
        assert names == expected


class TestToOpenAI:
    """to_openai converts neutral to OpenAI function-calling format."""

    def test_wraps_in_function_type(self):
        result = to_openai(VAULT_TOOLS)
        for tool in result:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_preserves_parameters(self):
        result = to_openai(VAULT_TOOLS)
        add_action = next(t for t in result if t["function"]["name"] == "add_action")
        assert "required" in add_action["function"]["parameters"]
        assert "description" in add_action["function"]["parameters"]["properties"]

    def test_count_preserved(self):
        assert len(to_openai(VAULT_TOOLS)) == 7


class TestToAnthropic:
    """to_anthropic converts neutral to Anthropic tool format."""

    def test_uses_input_schema(self):
        result = to_anthropic(VAULT_TOOLS)
        for tool in result:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "type" not in tool  # No OpenAI "type": "function" wrapper

    def test_preserves_parameters_as_input_schema(self):
        result = to_anthropic(VAULT_TOOLS)
        add_action = next(t for t in result if t["name"] == "add_action")
        assert "required" in add_action["input_schema"]
        assert "description" in add_action["input_schema"]["properties"]

    def test_count_preserved(self):
        assert len(to_anthropic(VAULT_TOOLS)) == 7


class TestRoundTrip:
    """Both conversions produce the same number of tools with same names."""

    def test_same_names(self):
        openai_names = {t["function"]["name"] for t in to_openai(VAULT_TOOLS)}
        anthropic_names = {t["name"] for t in to_anthropic(VAULT_TOOLS)}
        assert openai_names == anthropic_names
