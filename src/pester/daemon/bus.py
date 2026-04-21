"""Thread-safe pub/sub event bus with async dispatch and subscriber isolation."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe event bus with per-subscriber crash isolation.

    Subscribers are dispatched via a ThreadPoolExecutor so that slow
    handlers do not block the emitter.  Each handler is wrapped in
    try/except: a crashing subscriber is logged but never prevents
    other subscribers from receiving the event.
    """

    def __init__(self, *, max_workers: int = 4) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    # ── Public API ────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register *handler* to be called when *event_type* is emitted."""
        with self._lock:
            self._subscribers[event_type].append(handler)

    def emit(self, event_type: str, payload: dict) -> None:
        """Dispatch *payload* to all subscribers of *event_type*.

        Each subscriber runs in the thread-pool.  If a subscriber
        raises, the exception is logged and other subscribers still
        fire.
        """
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))

        for handler in handlers:
            self._executor.submit(self._safe_call, event_type, handler, payload)

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscribers.clear()

    def shutdown(self) -> None:
        """Shut down the executor and clear subscriptions."""
        self.clear()
        self._executor.shutdown(wait=True)

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _safe_call(event_type: str, handler: Callable, payload: dict) -> None:
        """Invoke *handler* with crash isolation."""
        try:
            handler(payload)
        except Exception:
            logger.warning(
                "Subscriber %s crashed on event %r — continuing",
                getattr(handler, "__name__", repr(handler)),
                event_type,
                exc_info=True,
            )
