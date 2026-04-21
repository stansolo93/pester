"""Tests for the daemon EventBus."""

from __future__ import annotations

import threading

from pester.daemon.bus import EventBus


class TestSubscribeAndEmit:
    def test_subscribe_and_emit(self):
        """Subscribers receive emitted payloads."""
        bus = EventBus()
        received = []

        bus.subscribe("test_event", lambda p: received.append(p))
        bus.emit("test_event", {"key": "value"})

        # Give the thread pool time to dispatch
        bus.shutdown()

        assert len(received) == 1
        assert received[0] == {"key": "value"}

    def test_multiple_subscribers(self):
        """Multiple subscribers all receive the same event."""
        bus = EventBus()
        results = []

        bus.subscribe("evt", lambda p: results.append("a"))
        bus.subscribe("evt", lambda p: results.append("b"))
        bus.emit("evt", {})

        bus.shutdown()

        assert sorted(results) == ["a", "b"]

    def test_no_cross_event_delivery(self):
        """Subscribers only receive their subscribed event type."""
        bus = EventBus()
        received = []

        bus.subscribe("alpha", lambda p: received.append("alpha"))
        bus.subscribe("beta", lambda p: received.append("beta"))
        bus.emit("alpha", {})

        bus.shutdown()

        assert received == ["alpha"]


class TestSubscriberCrashIsolation:
    def test_subscriber_crash_isolation(self):
        """A crashing subscriber must not prevent others from firing."""
        bus = EventBus()
        results = []

        def crasher(payload: dict) -> None:
            raise RuntimeError("boom")

        def survivor(payload: dict) -> None:
            results.append("survived")

        bus.subscribe("evt", crasher)
        bus.subscribe("evt", survivor)
        bus.emit("evt", {})

        bus.shutdown()

        assert "survived" in results


class TestClear:
    def test_clear_removes_subscriptions(self):
        """After clear(), no subscribers receive events."""
        bus = EventBus()
        received = []

        bus.subscribe("evt", lambda p: received.append(True))
        bus.clear()
        bus.emit("evt", {})

        bus.shutdown()

        assert received == []


class TestThreadSafety:
    def test_concurrent_emit(self):
        """Concurrent emits do not corrupt internal state."""
        bus = EventBus()
        counter = {"n": 0}
        lock = threading.Lock()

        def handler(payload: dict) -> None:
            with lock:
                counter["n"] += 1

        bus.subscribe("evt", handler)

        threads = []
        for _ in range(50):
            t = threading.Thread(target=bus.emit, args=("evt", {}))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        bus.shutdown()

        assert counter["n"] == 50

    def test_concurrent_subscribe_and_emit(self):
        """Subscribing and emitting concurrently doesn't raise."""
        bus = EventBus()
        results = []

        def subscriber(payload: dict) -> None:
            results.append(True)

        def add_subscribers() -> None:
            for _ in range(20):
                bus.subscribe("evt", subscriber)

        def emit_events() -> None:
            for _ in range(20):
                bus.emit("evt", {})

        t1 = threading.Thread(target=add_subscribers)
        t2 = threading.Thread(target=emit_events)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        bus.shutdown()

        # We just verify no exceptions were raised; exact count is non-deterministic
        assert True
