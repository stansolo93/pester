"""PID file management with stale-PID detection."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PID_FILENAME = "daemon.pid"


def write_pid(state_dir: Path) -> Path:
    """Write the current process PID to *state_dir*/daemon.pid."""
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_path = state_dir / _PID_FILENAME
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    logger.info("Wrote PID %d to %s", os.getpid(), pid_path)
    return pid_path


def read_pid(state_dir: Path) -> int | None:
    """Read the PID from daemon.pid, or return None if missing/invalid."""
    pid_path = state_dir / _PID_FILENAME
    if not pid_path.is_file():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def remove_pid(state_dir: Path) -> None:
    """Remove the PID file if it exists."""
    pid_path = state_dir / _PID_FILENAME
    try:
        pid_path.unlink(missing_ok=True)
        logger.info("Removed PID file %s", pid_path)
    except OSError as exc:
        logger.warning("Failed to remove PID file %s: %s", pid_path, exc)


def check_stale_pid(state_dir: Path) -> bool:
    """Return True if a PID file exists but the process is dead (stale).

    If stale, the PID file is removed automatically.
    Returns False when no PID file exists or the process is still alive.
    """
    pid = read_pid(state_dir)
    if pid is None:
        return False

    if _is_process_alive(pid):
        return False

    logger.warning("Stale PID %d detected — removing PID file", pid)
    remove_pid(state_dir)
    return True


def _is_process_alive(pid: int) -> bool:
    """Check whether a process with *pid* is running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission to signal it
        return True
    return True
