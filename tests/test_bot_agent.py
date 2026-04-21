"""Tests for the VaultAgent bot module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pester.core.config import load_config
from pester.core.state import ensure_state_dir


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    # Default fixture allows common test user_ids so most tests pass.
    # user_id=0 is the default when process_message is called without user_id.
    (vault / "pester.yaml").write_text(
        "vault:\n  name: Test\n  owner: TestOwner\nbot:\n  enabled: true\n  allowed_users: [0, 1, 42, 77]\n"
    )
    (vault / "actions").mkdir()
    return vault


@pytest.fixture
def mock_openai():
    """Install a fake openai module in sys.modules for testing."""
    mock_mod = MagicMock()
    with patch.dict(sys.modules, {"openai": mock_mod}):
        yield mock_mod


@pytest.fixture
def state_dir(vault_dir: Path) -> Path:
    return ensure_state_dir(vault_dir)


@pytest.fixture
def agent(vault_dir: Path, state_dir: Path, mock_openai):
    from pester.bot.agent import VaultAgent
    from pester.bot.conversation import ConversationStore
    from pester.mcp.server import VaultTools

    config = load_config(vault_dir)
    tools = VaultTools(vault_dir, config, state_dir)
    store = ConversationStore(state_dir, max_history=20)
    return VaultAgent(tools, config, conversation_store=store)


def _mock_response(content: str = "Привет!", tool_calls=None):
    """Create a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_tool_call(name: str, arguments: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return tc


class TestVaultAgent:
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_simple_text_response(self, agent, mock_openai):
        """Agent returns text when no tool calls."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("Привет! Я pester.")

        result = agent.process_message("привет", "Стас")

        assert "Привет" in result
        mock_client.chat.completions.create.assert_called_once()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_tool_call_list_actions(self, agent, mock_openai):
        """Agent dispatches list_actions tool call."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        tc = _mock_tool_call("list_actions", {"status": "open"})

        mock_client.chat.completions.create.side_effect = [
            _mock_response(tool_calls=[tc]),
            _mock_response("У вас нет открытых задач."),
        ]

        result = agent.process_message("что просрочено?", "Стас")

        assert "задач" in result
        assert mock_client.chat.completions.create.call_count == 2

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_tool_call_add_action(self, agent, mock_openai):
        """Agent dispatches add_action tool call."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        tc = _mock_tool_call(
            "add_action",
            {
                "description": "Позвонить инвесторам",
                "owner": "Стас",
                "due": "2026-04-10",
            },
        )

        mock_client.chat.completions.create.side_effect = [
            _mock_response(tool_calls=[tc]),
            _mock_response("Задача создана."),
        ]

        result = agent.process_message("напомни позвонить инвесторам до 10 апреля", "Стас")

        assert "создана" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_tool_call_get_health(self, agent, mock_openai):
        """Agent dispatches get_health tool call."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        tc = _mock_tool_call("get_health", {})

        mock_client.chat.completions.create.side_effect = [
            _mock_response(tool_calls=[tc]),
            _mock_response("Vault в хорошем состоянии."),
        ]

        result = agent.process_message("как дела?", "Стас")

        assert result  # Non-empty response

    def test_missing_api_key(self, agent):
        """Returns Russian error when API key not set."""
        with patch.dict("os.environ", {}, clear=True):
            result = agent.process_message("привет", "Стас")

        assert "Ошибка" in result
        assert "API" in result

    def test_dispatch_all_tools(self, agent):
        """All tool names dispatch without KeyError."""
        tool_names = [
            "list_actions",
            "add_action",
            "complete_action",
            "search_vault",
            "get_document",
            "get_health",
        ]
        for name in tool_names:
            result = agent._dispatch_tool(name, {})
            assert isinstance(result, str)

    def test_dispatch_unknown_tool(self, agent):
        """Unknown tool name returns error JSON."""
        result = agent._dispatch_tool("nonexistent_tool", {})
        data = json.loads(result)
        assert "error" in data


class TestTranscribeVoice:
    def test_transcribe_no_groq(self):
        from pester.bot.agent import transcribe_voice

        with patch("pester.bot.HAS_GROQ", False):
            result = transcribe_voice(b"fake-ogg-data", {})
        assert result == ""

    def test_transcribe_no_api_key(self):
        from pester.bot.agent import transcribe_voice

        with patch("pester.bot.HAS_GROQ", True), patch.dict("os.environ", {}, clear=True):
            result = transcribe_voice(b"fake-ogg-data", {})
        assert result == ""

    def test_transcribe_success(self):
        from pester.bot.agent import transcribe_voice

        mock_groq = MagicMock()
        mock_client = MagicMock()
        mock_groq.Groq.return_value = mock_client
        mock_client.audio.transcriptions.create.return_value = MagicMock(text="Привет, это тест.")

        with (
            patch("pester.bot.HAS_GROQ", True),
            patch.dict(sys.modules, {"groq": mock_groq}),
            patch.dict("os.environ", {"GROQ_API_KEY": "gsk-test"}),
        ):
            result = transcribe_voice(
                b"fake-ogg-data", {"bot": {"groq_api_key_env": "GROQ_API_KEY"}}
            )

        assert result == "Привет, это тест."
        # Whisper must auto-detect — no language= kwarg pinning to a single locale.
        call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
        assert "language" not in call_kwargs


class TestAccessControl:
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_allowed_users_reject(self, vault_dir: Path, state_dir: Path, mock_openai):
        """Unauthorized user_id is rejected."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["bot"]["allowed_users"] = [111]
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        result = agent.process_message("привет", "Стас", user_id=999)
        assert "Access denied" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_allowed_users_allow(self, vault_dir: Path, state_dir: Path, mock_openai):
        """Authorized user_id gets a response."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["bot"]["allowed_users"] = [111]
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        result = agent.process_message("привет", "Стас", user_id=111)
        assert "OK" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_allowed_users_empty_denies_all(self, vault_dir: Path, state_dir: Path, mock_openai):
        """Empty allowed_users list denies everyone (fail-closed)."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["bot"]["allowed_users"] = []
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        result = agent.process_message("привет", "Стас", user_id=42)
        assert "Access denied" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_access_denied_in_russian_when_vault_language_ru(
        self, vault_dir: Path, state_dir: Path, mock_openai
    ):
        """vault.language=ru produces the Russian access-denied message."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["vault"]["language"] = "ru"
        config["bot"]["allowed_users"] = [111]
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        result = agent.process_message("привет", "Стас", user_id=999)
        assert "Доступ запрещён" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_access_denied_falls_back_to_english_for_unknown_language(
        self, vault_dir: Path, state_dir: Path, mock_openai
    ):
        """Unknown vault.language (e.g. 'de') falls back to English, NOT Russian."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["vault"]["language"] = "de"
        config["bot"]["allowed_users"] = [111]
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        result = agent.process_message("hello", "Stan", user_id=999)
        assert "Access denied" in result
        assert "Доступ" not in result


class TestClientCaching:
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_client_reused(self, agent, mock_openai):
        """OpenAI client is created once and reused across messages."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        agent.process_message("msg1", "Стас", user_id=1)
        agent.process_message("msg2", "Стас", user_id=1)

        # OpenAI() should be called only once (lazy init)
        mock_openai.OpenAI.assert_called_once()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_call_llm_passes_correct_params(self, agent, mock_openai):
        """_call_llm sends model, temperature, max_completion_tokens."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        agent.process_message("test", "Стас", user_id=1)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "max_completion_tokens" in call_kwargs
        assert "max_tokens" not in call_kwargs
        assert call_kwargs["model"] == "gpt-5.4-mini"

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_max_completion_tokens_used(self, agent, mock_openai):
        """API call uses max_completion_tokens, NOT max_tokens."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        agent.process_message("test", "Стас", user_id=1)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_completion_tokens"] == 4096


class TestConversationStoreIntegration:
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_history_loads_from_conversation_store(self, agent, mock_openai):
        """Agent loads history from ConversationStore."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("Reply 1")

        agent.process_message("msg1", "Стас", user_id=1)

        mock_client.chat.completions.create.return_value = _mock_response("Reply 2")
        agent.process_message("msg2", "Стас", user_id=1)

        # Second call should include history from first call
        second_call = mock_client.chat.completions.create.call_args_list[-1]
        messages = second_call[1]["messages"]
        # Should have: system + user("msg1") + assistant("Reply 1") + user("msg2")
        contents = [m.get("content", "") for m in messages if isinstance(m, dict)]
        assert "msg1" in contents
        assert "Reply 1" in contents

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_history_writes_user_and_assistant(self, agent, mock_openai, state_dir):
        """Both user and assistant messages are persisted to JSONL."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("Response")

        agent.process_message("Question", "Стас", user_id=42)

        jsonl_path = state_dir / "bot_history" / "42.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "user"
        assert json.loads(lines[1])["role"] == "assistant"

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_user_id_param_used(self, agent, mock_openai, state_dir):
        """user_id determines the history file, not sender name."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        agent.process_message("test", "SenderName", user_id=77)

        assert (state_dir / "bot_history" / "77.jsonl").exists()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_persona_in_system_prompt(self, vault_dir, state_dir, mock_openai):
        """Custom persona appears in the system prompt."""
        from pester.bot.agent import VaultAgent
        from pester.mcp.server import VaultTools

        config = load_config(vault_dir)
        config["bot"]["persona"] = "Ты очень дружелюбный."
        tools = VaultTools(vault_dir, config, state_dir)
        agent = VaultAgent(tools, config)

        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("OK")

        agent.process_message("test", "Стас", user_id=1)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        system_msg = call_kwargs["messages"][0]["content"]
        assert "дружелюбный" in system_msg

    def test_missing_api_key_still_returns(self, agent):
        """Missing API key returns error message without crashing."""
        with patch.dict("os.environ", {}, clear=True):
            result = agent.process_message("test", "Стас", user_id=1)
        assert "Ошибка" in result

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
    def test_api_timeout_returns_error(self, agent, mock_openai):
        """API timeout returns Russian error message."""
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("Timeout")

        result = agent.process_message("test", "Стас", user_id=1)
        assert "Ошибка" in result


class TestLanguageDetection:
    """Tests for the per-turn language hint heuristic."""

    def test_russian_text(self):
        from pester.bot.agent import _detect_language

        assert _detect_language("Привет, добавь задачу") == "ru"

    def test_english_text(self):
        from pester.bot.agent import _detect_language

        assert _detect_language("Do you speak English?") == "en"

    def test_mixed_with_cyrillic_majority_is_russian(self):
        """Russian text with borrowed tech terms still detects as Russian."""
        from pester.bot.agent import _detect_language

        assert _detect_language("Добавь action в vault про MCP") == "ru"

    def test_short_text_is_mixed(self):
        """Messages shorter than 3 letters are ambiguous."""
        from pester.bot.agent import _detect_language

        assert _detect_language("ok") == "mixed"
        assert _detect_language("") == "mixed"
        assert _detect_language("!?") == "mixed"

    def test_transliterated_latin_is_english(self):
        """Latin-only text is treated as English even if it's transliterated Russian."""
        from pester.bot.agent import _detect_language

        # User wrote Latin chars — respond in English.
        assert _detect_language("Mazafaka, check the dashboard") == "en"

    def test_language_hint_ru(self):
        from pester.bot.agent import _build_language_hint

        hint = _build_language_hint("ru")
        assert "Russian" in hint
        assert "MUST respond" in hint

    def test_language_hint_en(self):
        from pester.bot.agent import _build_language_hint

        hint = _build_language_hint("en")
        assert "English" in hint

    def test_language_hint_mixed_empty(self):
        from pester.bot.agent import _build_language_hint

        assert _build_language_hint("mixed") == ""
