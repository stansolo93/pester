"""CLI commands for the pester daemon."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import click

from pester.core.vault import find_vault_root

logger = logging.getLogger(__name__)

# macOS launchd constants
_PLIST_LABEL = "com.pester.daemon"
_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
_PLIST_PATH = _PLIST_DIR / f"{_PLIST_LABEL}.plist"
_LOG_DIR = Path.home() / "Library" / "Logs" / "pester"

# Linux systemd constants
_SYSTEMD_UNIT_NAME = "pester-daemon.service"
_SYSTEMD_TG_UNIT_NAME = "pester-telegram.service"
_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

# Environment variable keys that the daemon may need forwarded.
_ENV_KEY_CONFIG_PATHS = [
    "llm.api_key_env",
    "notifications.telegram.bot_token_env",
    "bot.api_key_env",
    "bot.groq_api_key_env",
]


@click.group()
def daemon() -> None:
    """Manage the pester background daemon."""


@daemon.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run the daemon in the foreground."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))

    from pester.core.config import load_config
    from pester.daemon.bus import EventBus
    from pester.daemon.manager import DaemonManager
    from pester.daemon.pid import check_stale_pid
    from pester.core.state import ensure_state_dir

    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)

    # Handle stale PID
    check_stale_pid(state_dir)

    bus = EventBus()
    manager = DaemonManager(vault_path, config, bus)
    manager.install_signal_handlers()

    try:
        manager.start()
        click.echo(f"Daemon running for vault: {vault_path}")
        click.echo("Press Ctrl+C to stop.")
        manager.wait_for_shutdown()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@daemon.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon status (PID, alive/dead)."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))

    from pester.core.state import ensure_state_dir
    from pester.daemon.pid import check_stale_pid, read_pid

    state_dir = ensure_state_dir(vault_path)
    pid = read_pid(state_dir)

    if pid is None:
        click.echo("Daemon: not running (no PID file)")
        return

    if check_stale_pid(state_dir):
        click.echo(f"Daemon: stale PID {pid} detected and removed")
        return

    # PID is present and process is alive
    pid_path = state_dir / "daemon.pid"
    try:
        import os
        from datetime import datetime

        mtime = os.path.getmtime(pid_path)
        from datetime import timezone

        now = datetime.now(timezone.utc)
        started_utc = datetime.fromtimestamp(mtime, tz=timezone.utc)
        uptime = now - started_utc
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        click.echo(f"Daemon: running (PID {pid}, uptime ~{hours}h{minutes}m)")
    except OSError:
        click.echo(f"Daemon: running (PID {pid})")


def _collect_env_vars(config: dict) -> dict[str, str]:
    """Collect environment variables referenced by *_env config keys."""
    from pester.core.config import get_config_value

    env_vars: dict[str, str] = {}
    for dotted in _ENV_KEY_CONFIG_PATHS:
        env_name = get_config_value(config, dotted)
        if isinstance(env_name, str) and env_name:
            value = os.environ.get(env_name)
            if value:
                env_vars[env_name] = value
    return env_vars


def _build_plist(vault_path: Path, config: dict) -> dict:
    """Build the launchd plist dictionary."""
    python_path = sys.executable
    plist: dict = {
        "Label": _PLIST_LABEL,
        "ProgramArguments": [
            python_path,
            "-m",
            "pester",
            "--vault",
            str(vault_path),
            "daemon",
            "run",
        ],
        "KeepAlive": True,
        "RunAtLoad": True,
        "StandardOutPath": str(_LOG_DIR / "daemon-stdout.log"),
        "StandardErrorPath": str(_LOG_DIR / "daemon-stderr.log"),
    }

    env_vars = _collect_env_vars(config)
    if env_vars:
        plist["EnvironmentVariables"] = env_vars

    return plist


def _build_systemd_unit(
    python_path: str,
    vault_path: Path,
    command_args: list[str],
    env_vars: dict[str, str],
    description: str,
) -> str:
    """Build a systemd unit file string."""
    env_lines = "\n".join(f"Environment={k}={v}" for k, v in env_vars.items())
    exec_start = f"{python_path} -m pester --vault {vault_path} {' '.join(command_args)}"
    return (
        textwrap.dedent(f"""\
        [Unit]
        Description={description}
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={exec_start}
        Restart=on-failure
        RestartSec=10
        {env_lines}
        WorkingDirectory={vault_path}

        [Install]
        WantedBy=default.target
    """).strip()
        + "\n"
    )


def _install_launchd(vault_path: Path, config: dict, force: bool) -> None:
    """Install the daemon as a macOS launchd agent."""
    import plistlib

    if _PLIST_PATH.exists() and not force:
        click.echo(f"Plist already exists at {_PLIST_PATH}. Use --force to overwrite.")
        raise SystemExit(1)

    plist = _build_plist(vault_path, config)

    _PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    with open(_PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    logger.info("Wrote plist to %s", _PLIST_PATH)
    click.echo(f"Plist written to {_PLIST_PATH}")

    subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=True)
    click.echo("Daemon loaded via launchctl.")


def _uninstall_launchd() -> None:
    """Uninstall the daemon launchd agent."""
    if not _PLIST_PATH.exists():
        click.echo(f"No plist found at {_PLIST_PATH}. Nothing to uninstall.")
        return

    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], check=False)
    _PLIST_PATH.unlink()
    click.echo(f"Removed {_PLIST_PATH}. Daemon uninstalled.")


def _install_systemd(vault_path: Path, config: dict, force: bool) -> None:
    """Install daemon + telegram listener as systemd user services."""
    python_path = sys.executable
    env_vars = _collect_env_vars(config)

    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    units = [
        (
            _SYSTEMD_USER_DIR / _SYSTEMD_UNIT_NAME,
            _SYSTEMD_UNIT_NAME,
            ["daemon", "run"],
            "pester Daemon — vault watcher, indexing, scheduling, notifications",
        ),
        (
            _SYSTEMD_USER_DIR / _SYSTEMD_TG_UNIT_NAME,
            _SYSTEMD_TG_UNIT_NAME,
            ["sync", "telegram"],
            "pester Telegram — message sync and interactive bot",
        ),
    ]

    for unit_path, unit_name, cmd_args, desc in units:
        if unit_path.exists() and not force:
            click.echo(f"{unit_name} already exists. Use --force to overwrite.")
            raise SystemExit(1)

        content = _build_systemd_unit(python_path, vault_path, cmd_args, env_vars, desc)
        unit_path.write_text(content)
        click.echo(f"Wrote {unit_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

    for _, unit_name, _, _ in units:
        subprocess.run(["systemctl", "--user", "enable", "--now", unit_name], check=True)
        click.echo(f"Enabled and started {unit_name}")

    click.echo(
        "\nBoth services running. Run 'loginctl enable-linger' to keep them alive after logout."
    )


def _uninstall_systemd() -> None:
    """Uninstall daemon + telegram systemd user services."""
    unit_paths = [
        _SYSTEMD_USER_DIR / _SYSTEMD_UNIT_NAME,
        _SYSTEMD_USER_DIR / _SYSTEMD_TG_UNIT_NAME,
    ]

    found_any = False
    for unit_path in unit_paths:
        if not unit_path.exists():
            continue
        found_any = True
        name = unit_path.name
        subprocess.run(["systemctl", "--user", "stop", name], check=False)
        subprocess.run(["systemctl", "--user", "disable", name], check=False)
        unit_path.unlink()
        click.echo(f"Removed {unit_path}")

    if not found_any:
        click.echo("No systemd units found. Nothing to uninstall.")
        return

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    click.echo("Daemon uninstalled.")


@daemon.command()
@click.option("--force", is_flag=True, help="Overwrite existing service config.")
@click.pass_context
def install(ctx: click.Context, force: bool) -> None:
    """Install the daemon as a system service (launchd on macOS, systemd on Linux)."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))

    from pester.core.config import load_config

    config = load_config(vault_path)

    if sys.platform == "darwin":
        _install_launchd(vault_path, config, force)
    elif sys.platform == "linux":
        _install_systemd(vault_path, config, force)
    else:
        click.echo(f"Unsupported platform: {sys.platform}. Use macOS or Linux.")
        raise SystemExit(1)


@daemon.command()
def uninstall() -> None:
    """Uninstall the daemon system service."""
    if sys.platform == "darwin":
        _uninstall_launchd()
    elif sys.platform == "linux":
        _uninstall_systemd()
    else:
        click.echo(f"Unsupported platform: {sys.platform}. Use macOS or Linux.")
        raise SystemExit(1)
