"""DaemonComponent protocol — lifecycle contract for daemon subsystems."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DaemonComponent(Protocol):
    """Interface that every daemon component must satisfy.

    Lifecycle: create → start() → is_alive() → stop()

    ┌─────────┐   start()   ┌─────────┐   stop()   ┌─────────┐
    │ CREATED ├────────────►│ RUNNING ├───────────►│ STOPPED │
    └─────────┘             └─────────┘            └─────────┘
         │                       │                      │
         │  start() again        │  start() again       │  stop() again
         │  → normal start       │  → no-op             │  → no-op
         │                       │                      │
         │  stop()               │  is_alive()          │  is_alive()
         │  → no-op              │  → True              │  → False
         └───────────────────────┴──────────────────────┘
    """

    name: str
    """Human-readable component name (e.g., 'file-watcher', 'scheduler')."""

    def start(self) -> None:
        """Start the component.

        Idempotent: no-op if already running.
        Raises RuntimeError if the component cannot start (e.g., missing
        config, port in use). The caller (DaemonManager) decides whether
        to abort or skip.
        """
        ...

    def stop(self) -> None:
        """Stop the component and release resources.

        Idempotent: safe to call on an unstarted or already-stopped component.
        Must not raise — log errors internally if cleanup fails.
        """
        ...

    def is_alive(self) -> bool:
        """Return True if the component is running and healthy.

        Returns False before start() is called and after stop() completes.
        """
        ...
