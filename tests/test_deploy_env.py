"""deploy/check_env.py: deploy/.env read the way Docker Compose reads it.

The cases follow the dotenv parser of compose-spec/compose-go (dotenv/parser.go).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_env", _ROOT / "deploy" / "check_env.py")
check_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_env)

TOKEN = "Ab0-._~+/" * 4  # 36 characters of the RFC 6750 alphabet
DOMAIN = "MCP_DOMAIN=mcp.example.org\n"


class TestToken:
    @pytest.mark.parametrize(
        "text",
        [
            f"MCP_BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN={TOKEN} # generated 2026-10-29\n",
            f"MCP_BEARER_TOKEN={TOKEN}   \n",
            f"MCP_BEARER_TOKEN = {TOKEN}\n",
            f"export MCP_BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN: {TOKEN}\n",
            f'MCP_BEARER_TOKEN="{TOKEN}"\n',
            f"MCP_BEARER_TOKEN='{TOKEN}'\n",
            f'MCP_BEARER_TOKEN="{TOKEN}"  # comment\n',
            f"MCP_BEARER_TOKEN=short\nMCP_BEARER_TOKEN={TOKEN}\n",
            f'MCP_BEARER_TOKEN="{TOKEN[:-1]}\\0123"\n',
            f"MCP_BEARER_TOKEN={TOKEN}",
            f"MCP_BEARER_TOKEN={TOKEN}\r\n",
        ],
        ids=[
            "plain",
            "inline comment",
            "trailing spaces",
            "spaces around =",
            "export",
            "yaml colon",
            "double quotes",
            "single quotes",
            "quoted with comment",
            "last assignment wins",
            "octal escape in double quotes",
            "no final newline",
            "crlf",
        ],
    )
    def test_valid_token_passes(self, text: str):
        assert check_env.problems(DOMAIN + text) == []

    @pytest.mark.parametrize(
        "text",
        [
            f"MCP_BEARER_TOKEN={TOKEN}\nMCP_BEARER_TOKEN = short\n",
            f"MCP_BEARER_TOKEN={TOKEN}\nexport MCP_BEARER_TOKEN=short\n",
            f"MCP_BEARER_TOKEN={TOKEN}\n  MCP_BEARER_TOKEN: short\n",
            f"MCP_BEARER_TOKEN={TOKEN}\t# tab is not a comment marker\n",
            f'MCP_BEARER_TOKEN=" {TOKEN}"\n',
            f'MCP_BEARER_TOKEN="{TOKEN}\\n"\n',
            "MCP_BEARER_TOKEN=\n",
            "MCP_BEARER_TOKEN= # empty\n",
            "MCP_BEARER_TOKEN\n",
            f"MCP_BEARER_TOKEN={TOKEN}\nMCP_BEARER_TOKEN\n",
            "MCP_BEARER_TOKEN=${OTHER}\n",
            f'MCP_BEARER_TOKEN="{TOKEN}$SUFFIX"\n',
            f'NOTE="see below\nMCP_BEARER_TOKEN={TOKEN}\n"\n',
            "",
            f'MCP_BEARER_TOKEN="{TOKEN}\n',
            f"MCP BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN={'é' * 32}\n",
        ],
        ids=[
            "later spaced assignment",
            "later export",
            "later yaml colon",
            "tab before #",
            "space inside quotes",
            "escape inside double quotes",
            "empty",
            "only a comment",
            "inherited from the server",
            "later inherited",
            "interpolated",
            "interpolated in quotes",
            "inside another quoted value",
            "missing",
            "unterminated quote",
            "space in a key",
            "non-ASCII",
        ],
    )
    def test_invalid_token_refused(self, text: str):
        found = check_env.problems(DOMAIN + text)
        assert found
        assert all(TOKEN not in problem for problem in found)


class TestInterpolation:
    """Compose interpolates every assignment in order; a bad one stops it."""

    @pytest.mark.parametrize(
        "text",
        [
            f"MCP_BEARER_TOKEN=${{UNFINISHED\nMCP_BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN=${{X:?set it}}\nMCP_BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN=${{OTHER}}\nMCP_BEARER_TOKEN={TOKEN}\n",
            f"MCP_BEARER_TOKEN\nMCP_BEARER_TOKEN={TOKEN}\n",
            f"NOTE=${{\nMCP_BEARER_TOKEN={TOKEN}\n",
            f'NOTE="a ${{}} b"\nMCP_BEARER_TOKEN={TOKEN}\n',
            f"NOTE=${{HOME?}}\nMCP_BEARER_TOKEN={TOKEN}\n",
            f"NOTE=${{HOME:?missing}}\nMCP_BEARER_TOKEN={TOKEN}\n",
        ],
        ids=[
            "overwritten invalid template",
            "overwritten required variable",
            "overwritten interpolation",
            "overwritten inherited",
            "invalid template in another key",
            "empty braces in another key",
            "required in another key",
            "required non-empty in another key",
        ],
    )
    def test_refused(self, text: str):
        assert check_env.problems(DOMAIN + text)

    @pytest.mark.parametrize(
        "text",
        [
            "NOTE=$HOME/x\n",
            "NOTE=${HOME:-/root}\n",
            "NOTE=cost $$5\n",
            "NOTE=a $ b\n",
            "NOTE='${UNFINISHED'\n",
            'NOTE="\\${x"\n',
        ],
        ids=[
            "named",
            "default",
            "escaped dollar",
            "lone dollar",
            "single quotes",
            "escaped in double quotes",
        ],
    )
    def test_other_keys_may_interpolate(self, text: str):
        assert check_env.problems(DOMAIN + text + f"MCP_BEARER_TOKEN={TOKEN}\n") == []


class TestEscapes:
    """expandEscapes in parser.go, checked against `docker-compose config` 5.4.0."""

    @pytest.mark.parametrize(
        "raw, expanded",
        [
            ("\\0123", "S"),
            ("\\012", "\\12"),
            ("\\0400", "\\400"),
            ("\\0", "\\"),
            ("\\c", "\\c"),
            ("\\n", "\n"),
            ("\\$x", "$$x"),
            ('\\"', '"'),
        ],
    )
    def test_expand_escapes(self, raw: str, expanded: str):
        assert check_env._expand_escapes(raw) == expanded


class TestDomain:
    @pytest.mark.parametrize(
        "text",
        ["", "MCP_DOMAIN=\n", "MCP_DOMAIN= # later\n", "MCP_DOMAIN=${HOST}\n", "MCP_DOMAIN\n"],
        ids=["missing", "empty", "only a comment", "interpolated", "inherited"],
    )
    def test_missing_domain_refused(self, text: str):
        assert check_env.problems(text + f"MCP_BEARER_TOKEN={TOKEN}\n")

    def test_quoted_domain_with_comment_passes(self):
        text = 'MCP_DOMAIN="mcp.example.org" # prod\n' + f"MCP_BEARER_TOKEN={TOKEN}\n"
        assert check_env.problems(text) == []


class TestMain:
    def test_exit_codes_and_no_value_printed(self, tmp_path: Path, capsys):
        env = tmp_path / ".env"
        env.write_text(DOMAIN + f"MCP_BEARER_TOKEN={TOKEN}\n", encoding="utf-8")
        assert check_env.main([str(env)]) == 0
        bad = "x" * 40 + "!"
        env.write_text(DOMAIN + f"MCP_BEARER_TOKEN={bad}\n", encoding="utf-8")
        assert check_env.main([str(env)]) == 1
        out = capsys.readouterr()
        assert "MCP_BEARER_TOKEN" in out.out
        assert bad not in out.out + out.err

    def test_utf8_bom_is_skipped(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_bytes(b"\xef\xbb\xbf" + (DOMAIN + f"MCP_BEARER_TOKEN={TOKEN}\n").encode())
        assert check_env.main([str(env)]) == 0

    def test_missing_or_undecodable_file_refused(self, tmp_path: Path):
        assert check_env.main([str(tmp_path / "missing")]) == 1
        env = tmp_path / ".env"
        env.write_bytes(b"MCP_BEARER_TOKEN=\xff\n")
        assert check_env.main([str(env)]) == 1


class TestDeployPreflight:
    """deploy.sh runs the check before anything leaves the machine."""

    _DEPLOY = _ROOT / "deploy" / "deploy.sh"

    def _preflight(self, tmp_path: Path, env_text: str) -> subprocess.CompletedProcess:
        """Run only the pre-flight block of deploy.sh (no ssh, rsync or scp)."""
        text = self._DEPLOY.read_text(encoding="utf-8")
        block = text[text.index('echo "=== [0/7]') : text.index('echo "=== [1/7]')]
        assert "ssh" not in block and "rsync" not in block and "scp" not in block
        (tmp_path / "deploy").mkdir()
        shutil.copy(_ROOT / "deploy" / "check_env.py", tmp_path / "deploy" / "check_env.py")
        (tmp_path / "deploy" / ".env").write_text(env_text, encoding="utf-8")
        script = 'set -euo pipefail\nREPO_ROOT="$1"\n' + block + "echo PREFLIGHT-OK\n"
        return subprocess.run(
            ["bash", "-c", script, "preflight", str(tmp_path)], capture_output=True, text=True
        )

    def test_comment_after_valid_token_passes(self, tmp_path: Path):
        result = self._preflight(tmp_path, DOMAIN + f"MCP_BEARER_TOKEN={TOKEN} # note\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PREFLIGHT-OK" in result.stdout

    def test_overriding_short_token_stops_the_deploy(self, tmp_path: Path):
        text = DOMAIN + f"MCP_BEARER_TOKEN={TOKEN}\nMCP_BEARER_TOKEN = short\n"
        result = self._preflight(tmp_path, text)
        assert result.returncode != 0
        assert "PREFLIGHT-OK" not in result.stdout
        assert "MCP_BEARER_TOKEN" in result.stdout

    def test_check_runs_before_any_remote_step(self):
        text = self._DEPLOY.read_text(encoding="utf-8")
        check = text.index('python3 "$REPO_ROOT/deploy/check_env.py" "$REPO_ROOT/deploy/.env"')
        assert check < min(text.index("ssh "), text.index("rsync "), text.index("scp "))
        assert "MCP_BEARER_TOKEN=//p" not in text  # the old line-based reading is gone
