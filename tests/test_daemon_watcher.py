"""Tests for the FileWatcher component (mock watchdog)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from pester.daemon.bus import EventBus
from pester.daemon.events import ComponentEvent


class TestDebounce:
    def test_debounce_same_file(self, tmp_path: Path):
        """Rapid changes to the same file produce only one event after debounce."""
        bus = EventBus()
        received = []
        bus.subscribe(ComponentEvent.FILE_CHANGED, lambda p: received.append(p))

        vault = tmp_path / "vault"
        vault.mkdir()

        config = {"watcher": {"debounce_seconds": 0.1}}

        # Mock watchdog so we don't need the real package
        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer
        mock_observer.is_alive.return_value = True

        with (
            patch("pester.daemon.watcher.require_daemon"),
            patch.dict(
                "sys.modules",
                {
                    "watchdog": MagicMock(),
                    "watchdog.events": MagicMock(),
                    "watchdog.observers": MagicMock(),
                },
            ),
        ):
            from pester.daemon.watcher import FileWatcher

            with (
                patch("pester.daemon.watcher.Observer", mock_observer_cls, create=True),
            ):
                watcher = FileWatcher(vault, bus, config)
                # Manually set running state without calling start() (avoids watchdog)
                watcher._running = True
                watcher._observer = mock_observer

                # Simulate 3 rapid changes to the same file
                md_file = vault / "test.md"
                md_file.touch()
                for _ in range(3):
                    watcher._on_md_change(str(md_file), "modified")

                # Wait for debounce + dispatch
                time.sleep(0.3)

        bus.shutdown()

        # Only 1 event should be emitted despite 3 rapid changes
        assert len(received) == 1
        assert received[0]["change_type"] == "modified"


class TestMdOnlyFiltering:
    def test_md_only_filtering(self, tmp_path: Path):
        """The _MarkdownHandler only dispatches events for .md files."""
        with (
            patch("pester.daemon.watcher.require_daemon"),
            patch.dict(
                "sys.modules",
                {
                    "watchdog": MagicMock(),
                    "watchdog.events": MagicMock(),
                    "watchdog.observers": MagicMock(),
                },
            ),
        ):
            from pester.daemon.watcher import FileWatcher, _MarkdownHandler

            bus = EventBus()
            vault = tmp_path / "vault"
            vault.mkdir()
            config = {"watcher": {"debounce_seconds": 0.1}}

            mock_observer = MagicMock()
            mock_observer.is_alive.return_value = True

            watcher = FileWatcher(vault, bus, config)
            watcher._running = True
            watcher._observer = mock_observer

            handler = _MarkdownHandler(watcher)

            # Track calls to _on_md_change
            calls = []
            watcher._on_md_change = lambda src, ct: calls.append(src)

            # .md file — should be dispatched
            md_event = MagicMock()
            md_event.is_directory = False
            md_event.src_path = str(vault / "note.md")
            md_event.event_type = "modified"
            handler.dispatch(md_event)

            # .txt file — should be ignored
            txt_event = MagicMock()
            txt_event.is_directory = False
            txt_event.src_path = str(vault / "note.txt")
            txt_event.event_type = "modified"
            handler.dispatch(txt_event)

            # directory — should be ignored
            dir_event = MagicMock()
            dir_event.is_directory = True
            dir_event.src_path = str(vault / "subdir")
            dir_event.event_type = "created"
            handler.dispatch(dir_event)

            bus.shutdown()

            assert len(calls) == 1
            assert calls[0].endswith("note.md")
