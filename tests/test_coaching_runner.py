"""Tests for pester.coaching.runner — scheduled prompt execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pester.coaching.prompts import ScheduledPrompt
from pester.coaching.runner import run_prompt_job


def _make_prompt(name: str = "test", mode: str = "copilot") -> ScheduledPrompt:
    return ScheduledPrompt(
        name=name,
        schedule="09:00",
        prompt_path="_system/prompts/test.md",
        data_fn=lambda vp, cfg: {"today": "2026-04-02"},
        mode=mode,
    )


class TestRunPromptJob:
    def test_success_emits_event(self, tmp_path: Path):
        # Create template file
        prompts_dir = tmp_path / "_system" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "test.md").write_text("Hello {today}")

        bus = MagicMock()
        prompt = _make_prompt()
        state_dir = tmp_path / ".state"
        state_dir.mkdir()

        with patch("pester.coaching.runner._call_agent", return_value="AI response"):
            run_prompt_job(prompt, tmp_path, {}, state_dir, bus, "12345", 100)

        bus.emit.assert_called_once()
        payload = bus.emit.call_args[0][1]
        assert payload["response"] == "AI response"
        assert payload["prompt_name"] == "test"

    def test_missing_template_uses_fallback(self, tmp_path: Path):
        bus = MagicMock()
        prompt = ScheduledPrompt(
            name="test",
            schedule="09:00",
            prompt_path="_system/prompts/missing.md",
            data_fn=lambda vp, cfg: {},
            mode="copilot",
            fallback_template="Fallback prompt",
        )
        state_dir = tmp_path / ".state"
        state_dir.mkdir()

        with patch("pester.coaching.runner._call_agent", return_value="Fallback response"):
            run_prompt_job(prompt, tmp_path, {}, state_dir, bus, "12345", 100)

        bus.emit.assert_called_once()

    def test_failure_no_crash(self, tmp_path: Path):
        bus = MagicMock()
        prompt = _make_prompt()
        # data_fn raises
        prompt.data_fn = lambda vp, cfg: (_ for _ in ()).throw(RuntimeError("boom"))
        state_dir = tmp_path / ".state"
        state_dir.mkdir()

        # Should not raise
        run_prompt_job(prompt, tmp_path, {}, state_dir, bus, "12345", 100)
        bus.emit.assert_not_called()
