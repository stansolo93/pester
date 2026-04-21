"""Tests for LLM-powered action extraction and deduplication."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch


def _make_mock_openai(mock_client):
    """Create a mock openai module that returns mock_client from OpenAI()."""
    mock_mod = MagicMock()
    mock_mod.OpenAI.return_value = mock_client
    return mock_mod


class TestSuccessfulExtraction:
    """LLM extraction returns structured action items."""

    def test_successful_extraction(self):
        """Mock OpenAI response is parsed into extractor-compatible format."""
        config = {
            "llm": {
                "enabled": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY",
                "temperature": 0.3,
                "max_tokens": 2048,
                "timeout_seconds": 30,
            },
        }

        mock_items = [
            {
                "owner": "alice",
                "desc": "Review the Q4 report",
                "due": "2026-04-01",
                "priority": "Must",
            },
            {
                "owner": "bob",
                "desc": "Set up staging environment",
                "due": None,
                "priority": "Should",
            },
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(mock_items)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = _make_mock_openai(mock_client)

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-123"}),
            patch("pester.tracking.llm_extractor.HAS_LLM", True),
            patch.dict(sys.modules, {"openai": mock_openai}),
        ):
            from pester.tracking.llm_extractor import extract_with_llm

            results = extract_with_llm("some meeting text", config)

        assert len(results) == 2
        assert results[0]["owner"] == "alice"
        assert results[0]["desc"] == "Review the Q4 report"
        assert results[0]["due"] == "2026-04-01"
        assert results[0]["source"] == "llm"
        assert results[0]["confidence"] == 0.85
        assert results[1]["owner"] == "bob"
        assert results[1]["priority"] == "Should"


class TestApiTimeoutReturnsEmpty:
    """API timeout should return empty list, not raise."""

    def test_api_timeout_returns_empty(self):
        """When OpenAI times out, return empty list."""
        config = {
            "llm": {
                "enabled": True,
                "api_key_env": "OPENAI_API_KEY",
                "timeout_seconds": 5,
            },
        }

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = TimeoutError("Request timed out")

        mock_openai = _make_mock_openai(mock_client)

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch("pester.tracking.llm_extractor.HAS_LLM", True),
            patch.dict(sys.modules, {"openai": mock_openai}),
        ):
            from pester.tracking.llm_extractor import extract_with_llm

            results = extract_with_llm("meeting text", config)

        assert results == []


class TestInvalidJsonReturnsEmpty:
    """Garbled LLM response should return empty list."""

    def test_invalid_json_returns_empty(self):
        """When LLM returns non-JSON, return empty list."""
        config = {
            "llm": {
                "enabled": True,
                "api_key_env": "OPENAI_API_KEY",
            },
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is not JSON at all!"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = _make_mock_openai(mock_client)

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch("pester.tracking.llm_extractor.HAS_LLM", True),
            patch.dict(sys.modules, {"openai": mock_openai}),
        ):
            from pester.tracking.llm_extractor import extract_with_llm

            results = extract_with_llm("meeting text", config)

        assert results == []


class TestMissingApiKeyReturnsEmpty:
    """Missing API key should return empty list."""

    def test_missing_api_key_returns_empty(self):
        """When OPENAI_API_KEY is not set, return empty list."""
        config = {
            "llm": {
                "enabled": True,
                "api_key_env": "OPENAI_API_KEY",
            },
        }

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("pester.tracking.llm_extractor.HAS_LLM", True),
        ):
            # Ensure OPENAI_API_KEY is NOT in env
            import os

            os.environ.pop("OPENAI_API_KEY", None)

            from pester.tracking.llm_extractor import extract_with_llm

            results = extract_with_llm("meeting text", config)

        assert results == []


class TestDedupeWithRegex:
    """LLM + regex overlap is deduplicated, keeping LLM version."""

    def test_dedupe_with_regex(self):
        """Overlapping actions (same owner + desc substring) keep LLM version."""
        from pester.tracking.llm_extractor import dedupe_actions

        llm_actions = [
            {
                "owner": "alice",
                "desc": "Review the Q4 report and provide feedback",
                "due": "2026-04-01",
                "source": "llm",
                "confidence": 0.85,
            },
            {
                "owner": "bob",
                "desc": "Set up staging environment",
                "due": None,
                "source": "llm",
                "confidence": 0.85,
            },
        ]

        regex_actions = [
            {
                "owner": "alice",
                "desc": "Review the Q4 report",
                "due": "2026-04-01",
                "source": "meeting",
                "confidence": 0.95,
            },
            {
                "owner": "carol",
                "desc": "Send the agenda to team",
                "due": "2026-04-02",
                "source": "meeting",
                "confidence": 0.95,
            },
        ]

        result = dedupe_actions(llm_actions, regex_actions)

        # LLM's alice action kept (regex's alice is a substring match -> deduped)
        # Bob from LLM kept
        # Carol from regex kept (no LLM match)
        assert len(result) == 3

        owners = [r["owner"] for r in result]
        assert "alice" in owners
        assert "bob" in owners
        assert "carol" in owners

        # The alice entry should be the LLM version (source=llm)
        alice_entry = [r for r in result if r["owner"] == "alice"][0]
        assert alice_entry["source"] == "llm"
        assert "provide feedback" in alice_entry["desc"]

    def test_dedupe_empty_llm(self):
        """When LLM returns nothing, all regex results are kept."""
        from pester.tracking.llm_extractor import dedupe_actions

        regex_actions = [
            {"owner": "alice", "desc": "Do something", "source": "meeting"},
        ]

        result = dedupe_actions([], regex_actions)
        assert len(result) == 1
        assert result[0]["source"] == "meeting"

    def test_dedupe_empty_regex(self):
        """When regex returns nothing, all LLM results are kept."""
        from pester.tracking.llm_extractor import dedupe_actions

        llm_actions = [
            {"owner": "alice", "desc": "Do something", "source": "llm"},
        ]

        result = dedupe_actions(llm_actions, [])
        assert len(result) == 1
        assert result[0]["source"] == "llm"

    def test_dedupe_no_owner_match(self):
        """Different owners are never considered duplicates."""
        from pester.tracking.llm_extractor import dedupe_actions

        llm_actions = [
            {"owner": "alice", "desc": "Do the thing", "source": "llm"},
        ]
        regex_actions = [
            {"owner": "bob", "desc": "Do the thing", "source": "meeting"},
        ]

        result = dedupe_actions(llm_actions, regex_actions)
        assert len(result) == 2


class TestDefaultModelUsed:
    """Default model fallback uses gpt-5.4-mini when config omits model key."""

    def test_default_model_used_when_not_in_config(self):
        """When config has llm.enabled but no model key, gpt-5.4-mini is used."""
        config = {
            "llm": {
                "enabled": True,
                "api_key_env": "OPENAI_API_KEY",
            },
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([])
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = _make_mock_openai(mock_client)

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch("pester.tracking.llm_extractor.HAS_LLM", True),
            patch.dict(sys.modules, {"openai": mock_openai}),
        ):
            from pester.tracking.llm_extractor import extract_with_llm

            extract_with_llm("meeting text", config)

        # Verify the model passed to the API is gpt-5.4-mini (the default)
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs is not None
        assert (
            call_kwargs.kwargs.get("model") == "gpt-5.4-mini"
            or call_kwargs[1].get("model") == "gpt-5.4-mini"
        )
