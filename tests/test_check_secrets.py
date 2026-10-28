"""The repository secret scan walks directories and never prints matched values."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_secrets.py"
_spec = importlib.util.spec_from_file_location("check_secrets", _SCRIPT)
check_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_secrets)

# Fictional Groq-shaped key, assembled at runtime so the scan does not flag this file.
FAKE_KEY = "gsk" + "_" + "Fake0" * 6


def test_directory_argument_scans_files_inside(tmp_path: Path, capsys):
    nested = tmp_path / "pkg" / "conf"
    nested.mkdir(parents=True)
    (nested / "settings.env").write_text(f"GROQ_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    assert check_secrets.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "settings.env:1: Groq API key" in out
    assert FAKE_KEY not in out


def test_line_with_two_secrets_prints_neither(tmp_path: Path, capsys):
    openai_key = "sk" + "-" + "Fake1" * 6
    (tmp_path / "keys.env").write_text(f"A={openai_key} B={FAKE_KEY}\n", encoding="utf-8")
    assert check_secrets.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "keys.env:1: OpenAI API key" in out
    assert "keys.env:1: Groq API key" in out
    assert openai_key not in out
    assert FAKE_KEY not in out


def test_clean_directory_passes_and_counts_files(tmp_path: Path, capsys):
    (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("GROQ_API_KEY=gsk_...\n", encoding="utf-8")
    assert check_secrets.main([str(tmp_path)]) == 0
    assert "2 files" in capsys.readouterr().out


def test_skips_git_and_virtualenv_dirs(tmp_path: Path):
    for skipped in (".git", ".venv"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "leak.txt").write_text(f"{FAKE_KEY}\n", encoding="utf-8")
    assert check_secrets.main([str(tmp_path)]) == 0


def test_missing_path_is_an_error(tmp_path: Path):
    with pytest.raises(SystemExit, match="no such file or directory"):
        check_secrets.main([str(tmp_path / "missing")])
