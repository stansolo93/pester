"""Tests for DaemonManager lifecycle and min-1-component policy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pester.daemon.bus import EventBus
from pester.daemon.manager import DaemonManager


class _FakeComponent:
    """Minimal DaemonComponent for testing."""

    def __init__(self, name: str = "fake", *, fail_start: bool = False) -> None:
        self.name = name
        self._alive = False
        self._fail_start = fail_start
        self.start_called = False
        self.stop_called = False

    def start(self) -> None:
        self.start_called = True
        if self._fail_start:
            raise RuntimeError("cannot start")
        self._alive = True

    def stop(self) -> None:
        self.stop_called = True
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


class TestStartStopLifecycle:
    def test_start_stop_lifecycle(self, tmp_path: Path):
        """Manager starts components and stops them in correct order."""
        bus = EventBus()
        config = {"watcher": {"enabled": False}}

        with patch("pester.daemon.manager.ensure_state_dir", return_value=tmp_path):
            manager = DaemonManager(tmp_path / "vault", config, bus)

        producer = _FakeComponent("producer")
        consumer = _FakeComponent("consumer")
        manager._producers = [producer]
        manager._consumers = [consumer]

        with patch("pester.daemon.manager.write_pid"):
            manager.start()

        assert producer.start_called
        assert consumer.start_called
        assert producer.is_alive()
        assert consumer.is_alive()

        with patch("pester.daemon.manager.remove_pid"):
            manager.stop()

        assert producer.stop_called
        assert consumer.stop_called
        assert not producer.is_alive()
        assert not consumer.is_alive()


class TestZeroComponentExit:
    def test_zero_component_exit(self, tmp_path: Path):
        """Manager raises RuntimeError if no components start."""
        bus = EventBus()
        config = {"watcher": {"enabled": False}}

        with patch("pester.daemon.manager.ensure_state_dir", return_value=tmp_path):
            manager = DaemonManager(tmp_path / "vault", config, bus)

        # Patch _build_components to keep lists empty (override the real builder)
        with (
            patch.object(manager, "_build_components"),
            patch.object(manager, "_register_handlers"),
        ):
            manager._producers = []
            manager._consumers = []

            with pytest.raises(RuntimeError, match="No daemon components could start"):
                manager.start()

        bus.shutdown()

    def test_all_components_fail_to_start(self, tmp_path: Path):
        """RuntimeError when all components fail to start."""
        bus = EventBus()
        config = {"watcher": {"enabled": False}}

        with patch("pester.daemon.manager.ensure_state_dir", return_value=tmp_path):
            manager = DaemonManager(tmp_path / "vault", config, bus)

        def _set_failing_components() -> None:
            manager._producers = [_FakeComponent("p1", fail_start=True)]
            manager._consumers = [_FakeComponent("c1", fail_start=True)]

        with (
            patch.object(manager, "_build_components", side_effect=_set_failing_components),
            patch.object(manager, "_register_handlers"),
        ):
            with pytest.raises(RuntimeError, match="No daemon components could start"):
                manager.start()

        bus.shutdown()


class TestGracefulShutdownOrder:
    def test_graceful_shutdown_order(self, tmp_path: Path):
        """Producers are stopped before consumers."""
        bus = EventBus()
        config = {"watcher": {"enabled": False}}

        with patch("pester.daemon.manager.ensure_state_dir", return_value=tmp_path):
            manager = DaemonManager(tmp_path / "vault", config, bus)

        stop_order: list[str] = []

        producer = _FakeComponent("producer")
        consumer = _FakeComponent("consumer")

        original_producer_stop = producer.stop
        original_consumer_stop = consumer.stop

        def producer_stop() -> None:
            stop_order.append("producer")
            original_producer_stop()

        def consumer_stop() -> None:
            stop_order.append("consumer")
            original_consumer_stop()

        producer.stop = producer_stop
        consumer.stop = consumer_stop

        manager._producers = [producer]
        manager._consumers = [consumer]

        with patch("pester.daemon.manager.write_pid"):
            manager.start()

        with patch("pester.daemon.manager.remove_pid"):
            manager.stop()

        assert stop_order == ["producer", "consumer"]
