"""Tests for pester.llm._shared — client creation, model resolution, token logging."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from pester.llm._shared import create_client, log_token_usage, resolve_model

try:
    import openai  # noqa: F401

    _has_openai = True
except ImportError:
    _has_openai = False

try:
    import anthropic  # noqa: F401

    _has_anthropic = True
except ImportError:
    _has_anthropic = False


class TestResolveModel:
    """resolve_model validates provider/model compatibility and falls back."""

    def test_openai_model_with_openai_provider(self):
        assert resolve_model("openai", "o4-mini", "bot") == "o4-mini"

    def test_anthropic_model_with_anthropic_provider(self):
        assert (
            resolve_model("anthropic", "claude-sonnet-4-6-20250217") == "claude-sonnet-4-6-20250217"
        )

    def test_gpt_model_with_anthropic_provider_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = resolve_model("anthropic", "gpt-4.1-nano", "bot")
        assert result == "claude-sonnet-4-6-20250217"
        assert "not valid for provider 'anthropic'" in caplog.text

    def test_o4_model_with_anthropic_provider_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = resolve_model("anthropic", "o4-mini", "bot")
        assert result == "claude-sonnet-4-6-20250217"

    def test_claude_model_with_openai_provider_falls_back_bot(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = resolve_model("openai", "claude-sonnet-4-6-20250217", "bot")
        assert result == "gpt-5.4-mini"

    def test_claude_model_with_openai_provider_falls_back_llm(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = resolve_model("openai", "claude-haiku-4-5-20251001", "llm")
        assert result == "gpt-5.4-mini"

    def test_unknown_model_passes_through(self):
        assert resolve_model("openai", "custom-model-v1") == "custom-model-v1"

    def test_role_defaults_to_llm(self):
        with patch("pester.llm._shared.logger"):
            result = resolve_model("openai", "claude-sonnet-4-6-20250217")
        assert result == "gpt-5.4-mini"  # llm default


class TestCreateClient:
    """create_client creates SDK clients or returns None."""

    @pytest.mark.llm
    @pytest.mark.skipif(not _has_openai, reason="openai SDK not installed")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_creates_openai_client(self):
        client = create_client("openai", "OPENAI_API_KEY")
        assert client is not None

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_none_when_key_missing(self):
        client = create_client("openai", "OPENAI_API_KEY")
        assert client is None

    @pytest.mark.llm
    @pytest.mark.skipif(not _has_anthropic, reason="anthropic SDK not installed")
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"})
    def test_creates_anthropic_client(self):
        client = create_client("anthropic", "ANTHROPIC_API_KEY")
        assert client is not None

    @pytest.mark.llm
    @pytest.mark.skipif(not _has_openai, reason="openai SDK not installed")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_max_retries_passed(self):
        import openai as openai_mod

        with patch.object(openai_mod, "OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            create_client("openai", "OPENAI_API_KEY", max_retries=5)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["max_retries"] == 5

    def test_returns_none_when_sdk_missing(self):
        """Gracefully returns None when the provider SDK is not importable."""
        with patch.dict("os.environ", {"FAKE_KEY": "sk-test"}):
            # Even if env var is set, a bogus provider falls through to openai
            # which may or may not be installed — but missing key returns None
            client = create_client("openai", "NONEXISTENT_KEY_VAR")
            assert client is None


class TestLogTokenUsage:
    """log_token_usage extracts tokens from both provider formats."""

    def test_openai_usage_format(self, caplog):
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        with caplog.at_level(logging.INFO):
            p, c = log_token_usage(usage, model="o4-mini", provider="openai")
        assert p == 100
        assert c == 50
        assert "100 prompt + 50 completion" in caplog.text

    def test_anthropic_usage_format(self):
        usage = MagicMock(spec=[])
        usage.input_tokens = 200
        usage.output_tokens = 75
        # No prompt_tokens/completion_tokens attributes
        delattr(usage, "prompt_tokens") if hasattr(usage, "prompt_tokens") else None
        p, c = log_token_usage(usage, model="claude-sonnet", provider="anthropic")
        assert p == 200
        assert c == 75

    def test_none_usage_returns_zeros(self):
        p, c = log_token_usage(None)
        assert p == 0
        assert c == 0
