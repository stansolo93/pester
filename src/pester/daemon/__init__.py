"""Daemon module — long-running background components."""

from __future__ import annotations

from pester.core.extras import make_optional_check

HAS_DAEMON, require_daemon = make_optional_check("watchdog", "daemon")
