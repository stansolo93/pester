"""Tests for daemon install / uninstall CLI commands."""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pester.cli.cmd_daemon import daemon


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Create a minimal vault with pester.yaml."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "pester.yaml").write_text("vault:\n  name: Test\n")
    return vault


@pytest.fixture
def _mock_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect plist and log paths to tmp_path."""
    import pester.cli.cmd_daemon as mod

    plist_dir = tmp_path / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.pester.daemon.plist"
    log_dir = tmp_path / "Logs" / "pester"

    monkeypatch.setattr(mod, "_PLIST_DIR", plist_dir)
    monkeypatch.setattr(mod, "_PLIST_PATH", plist_path)
    monkeypatch.setattr(mod, "_LOG_DIR", log_dir)

    return plist_path, log_dir


@pytest.fixture
def _mock_systemd_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect systemd unit paths to tmp_path."""
    import pester.cli.cmd_daemon as mod

    systemd_dir = tmp_path / "systemd" / "user"
    systemd_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "_SYSTEMD_USER_DIR", systemd_dir)

    return systemd_dir


class TestDaemonInstall:
    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_generates_plist(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_paths,
    ):
        mock_sys.platform = "darwin"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        plist_path, log_dir = _mock_paths

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert plist_path.exists()

        # Parse the plist and verify content
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)

        assert plist["Label"] == "com.pester.daemon"
        assert plist["ProgramArguments"][0] == "/usr/bin/python3"
        assert "--vault" in plist["ProgramArguments"]
        assert plist["KeepAlive"] is True
        assert plist["RunAtLoad"] is True
        assert "daemon-stdout.log" in plist["StandardOutPath"]
        assert "daemon-stderr.log" in plist["StandardErrorPath"]

        # launchctl load was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "launchctl"
        assert args[1] == "load"

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_plist_vault_before_daemon(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_paths,
    ):
        """--vault and vault path must come before 'daemon run' in ProgramArguments."""
        mock_sys.platform = "darwin"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        plist_path, log_dir = _mock_paths

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output

        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)

        args = plist["ProgramArguments"]
        vault_idx = args.index("--vault")
        daemon_idx = args.index("daemon")
        run_idx = args.index("run")

        # --vault must come before daemon run (it's a global Click option)
        assert vault_idx < daemon_idx, f"--vault at {vault_idx} must precede daemon at {daemon_idx}"
        assert daemon_idx < run_idx, f"daemon at {daemon_idx} must precede run at {run_idx}"

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_force_overwrites(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_paths,
    ):
        mock_sys.platform = "darwin"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        plist_path, _ = _mock_paths
        plist_path.write_text("old content")

        result = runner.invoke(
            daemon,
            ["install", "--force"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        # Should have been overwritten with valid plist
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        assert plist["Label"] == "com.pester.daemon"

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_refuses_without_force(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_paths,
    ):
        mock_sys.platform = "darwin"
        mock_sys.executable = "/usr/bin/python3"

        plist_path, _ = _mock_paths
        plist_path.write_text("existing plist")

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
        )

        assert result.exit_code != 0
        assert "already exists" in result.output
        # launchctl should NOT have been called
        mock_run.assert_not_called()

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_unsupported_platform(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_paths,
    ):
        mock_sys.platform = "win32"

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
        )

        assert result.exit_code != 0
        assert "Unsupported platform" in result.output
        mock_run.assert_not_called()


class TestDaemonInstallSystemd:
    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_generates_both_units(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_systemd_paths,
    ):
        mock_sys.platform = "linux"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        systemd_dir = _mock_systemd_paths

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output

        daemon_unit = systemd_dir / "pester-daemon.service"
        tg_unit = systemd_dir / "pester-telegram.service"
        assert daemon_unit.exists()
        assert tg_unit.exists()

        # Check daemon unit content
        content = daemon_unit.read_text()
        assert "daemon run" in content
        assert "Restart=on-failure" in content
        assert "After=network-online.target" in content
        assert str(vault_dir) in content

        # Check telegram unit content
        tg_content = tg_unit.read_text()
        assert "sync telegram" in tg_content

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_systemd_env_vars(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_systemd_paths,
        monkeypatch,
    ):
        mock_sys.platform = "linux"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")

        systemd_dir = _mock_systemd_paths

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output

        content = (systemd_dir / "pester-daemon.service").read_text()
        assert "Environment=OPENAI_API_KEY=sk-test" in content
        assert "Environment=TELEGRAM_BOT_TOKEN=123:ABC" in content

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_systemd_force_overwrites(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_systemd_paths,
    ):
        mock_sys.platform = "linux"
        mock_sys.executable = "/usr/bin/python3"
        mock_run.return_value = MagicMock(returncode=0)

        systemd_dir = _mock_systemd_paths
        (systemd_dir / "pester-daemon.service").write_text("old")
        (systemd_dir / "pester-telegram.service").write_text("old")

        result = runner.invoke(
            daemon,
            ["install", "--force"],
            obj={"vault_override": str(vault_dir)},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        content = (systemd_dir / "pester-daemon.service").read_text()
        assert "daemon run" in content

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_install_systemd_refuses_without_force(
        self,
        mock_sys,
        mock_run,
        runner,
        vault_dir,
        _mock_systemd_paths,
    ):
        mock_sys.platform = "linux"
        mock_sys.executable = "/usr/bin/python3"

        systemd_dir = _mock_systemd_paths
        (systemd_dir / "pester-daemon.service").write_text("existing")

        result = runner.invoke(
            daemon,
            ["install"],
            obj={"vault_override": str(vault_dir)},
        )

        assert result.exit_code != 0
        assert "already exists" in result.output


class TestDaemonUninstall:
    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_uninstall_removes_plist(
        self,
        mock_sys,
        mock_run,
        runner,
        _mock_paths,
    ):
        mock_sys.platform = "darwin"
        mock_run.return_value = MagicMock(returncode=0)

        plist_path, _ = _mock_paths
        plist_path.write_text("dummy plist content")

        result = runner.invoke(
            daemon,
            ["uninstall"],
            obj={},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert not plist_path.exists()
        assert "uninstalled" in result.output.lower()

        # launchctl unload was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "launchctl"
        assert args[1] == "unload"

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_uninstall_no_plist_is_noop(
        self,
        mock_sys,
        mock_run,
        runner,
        _mock_paths,
    ):
        mock_sys.platform = "darwin"

        plist_path, _ = _mock_paths
        # Ensure it does NOT exist
        if plist_path.exists():
            plist_path.unlink()

        result = runner.invoke(
            daemon,
            ["uninstall"],
            obj={},
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "nothing to uninstall" in result.output.lower()
        mock_run.assert_not_called()


class TestDaemonUninstallSystemd:
    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_uninstall_removes_both_units(
        self,
        mock_sys,
        mock_run,
        runner,
        _mock_systemd_paths,
    ):
        mock_sys.platform = "linux"
        mock_run.return_value = MagicMock(returncode=0)

        systemd_dir = _mock_systemd_paths
        (systemd_dir / "pester-daemon.service").write_text("unit")
        (systemd_dir / "pester-telegram.service").write_text("unit")

        result = runner.invoke(
            daemon,
            ["uninstall"],
            obj={},
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert not (systemd_dir / "pester-daemon.service").exists()
        assert not (systemd_dir / "pester-telegram.service").exists()
        assert "uninstalled" in result.output.lower()

    @patch("pester.cli.cmd_daemon.subprocess.run")
    @patch("pester.cli.cmd_daemon.sys")
    def test_uninstall_no_units_is_noop(
        self,
        mock_sys,
        mock_run,
        runner,
        _mock_systemd_paths,
    ):
        mock_sys.platform = "linux"

        result = runner.invoke(
            daemon,
            ["uninstall"],
            obj={},
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "nothing to uninstall" in result.output.lower()
        mock_run.assert_not_called()
