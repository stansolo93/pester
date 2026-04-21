"""Tests for pester.sync — Drive and Telegram sync modules."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pester.sync.sync_state import (
    get_drive_folder_state,
    get_telegram_chat_state,
    load_sync_state,
    save_sync_state,
    set_drive_folder_state,
    set_telegram_chat_state,
)


# ── Sync state tests ─────────────────────────────────────────────────


class TestSyncState:
    """Tests for sync_state.py — state persistence."""

    def test_load_empty_state(self, tmp_path: Path):
        """load_sync_state returns {} when no file exists."""
        state = load_sync_state(tmp_path)
        assert state == {}

    def test_load_existing_state(self, tmp_path: Path):
        """load_sync_state reads and parses sync_state.json."""
        data = {"drive": {"folder1": {"last_sync": "2026-03-18T10:00:00Z"}}}
        (tmp_path / "sync_state.json").write_text(json.dumps(data), encoding="utf-8")
        state = load_sync_state(tmp_path)
        assert state == data

    def test_load_corrupt_state(self, tmp_path: Path):
        """load_sync_state returns {} on corrupt JSON."""
        (tmp_path / "sync_state.json").write_text("not json{{{", encoding="utf-8")
        state = load_sync_state(tmp_path)
        assert state == {}

    def test_save_and_reload(self, tmp_path: Path):
        """save_sync_state -> load_sync_state roundtrip."""
        data = {"telegram": {"123": {"last_message_id": 42}}}
        save_sync_state(tmp_path, data)
        loaded = load_sync_state(tmp_path)
        assert loaded == data

    def test_drive_folder_state_accessors(self):
        """get/set_drive_folder_state manage nested dict correctly."""
        state: dict = {}
        set_drive_folder_state(state, "folder-abc", {"last_sync": "2026-01-01"})
        assert get_drive_folder_state(state, "folder-abc") == {"last_sync": "2026-01-01"}
        assert get_drive_folder_state(state, "nonexistent") == {}

    def test_telegram_chat_state_accessors(self):
        """get/set_telegram_chat_state manage nested dict correctly."""
        state: dict = {}
        set_telegram_chat_state(state, -100123, {"last_message_id": 99})
        assert get_telegram_chat_state(state, -100123) == {"last_message_id": 99}
        assert get_telegram_chat_state(state, -999) == {}


# ── Drive frontmatter tests ──────────────────────────────────────────


class TestDriveFrontmatter:
    """Tests for frontmatter generation on synced Drive files."""

    def test_adds_source_google_drive(self):
        """Frontmatter includes source: google-drive."""
        from pester.sync.drive import DriveFile, _add_frontmatter

        fm = _add_frontmatter("content", DriveFile("id1", "test", "text/plain", "2026-01-01"))
        assert "source: google-drive" in fm

    def test_includes_drive_id(self):
        """Frontmatter includes the drive file ID."""
        from pester.sync.drive import DriveFile, _add_frontmatter

        fm = _add_frontmatter("content", DriveFile("abc123", "test", "text/plain", "2026-01-01"))
        assert 'drive_id: "abc123"' in fm

    def test_includes_synced_at(self):
        """Frontmatter includes ISO timestamp."""
        from pester.sync.drive import DriveFile, _add_frontmatter

        fm = _add_frontmatter("content", DriveFile("id1", "test", "text/plain", "2026-01-01"))
        assert "synced_at:" in fm


# ── Drive file conversion tests ──────────────────────────────────────


class TestDriveFileConversion:
    """Tests for Google Docs/Sheets/Slides -> markdown conversion."""

    def test_csv_to_markdown_table(self):
        """CSV text converts to markdown table."""
        from pester.sync.drive import _csv_to_markdown_table

        csv_text = "Name,Age\nAlice,30\nBob,25"
        table = _csv_to_markdown_table(csv_text)
        assert "| Name | Age |" in table
        assert "| --- | --- |" in table
        assert "| Alice | 30 |" in table

    def test_csv_to_markdown_empty(self):
        """Empty CSV produces empty string."""
        from pester.sync.drive import _csv_to_markdown_table

        assert _csv_to_markdown_table("") == ""

    def test_sanitize_filename(self):
        """Drive filenames are sanitized for filesystem."""
        from pester.sync.drive import _sanitize_filename

        assert _sanitize_filename("My Document (1)") == "my-document-1"
        assert _sanitize_filename("  spaces  ") == "spaces"
        assert _sanitize_filename("") == "untitled"

    def test_google_doc_download(self, tmp_path: Path):
        """Google Doc export produces markdown with frontmatter."""
        from pester.sync.drive import DriveFile, download_file

        mock_service = MagicMock()
        mock_service.files().export().execute.return_value = b"Hello world"

        file_meta = DriveFile(
            id="doc1",
            name="My Doc",
            mime_type="application/vnd.google-apps.document",
            modified_time="2026-01-01T00:00:00Z",
        )

        target = tmp_path / "drive"
        assets = tmp_path / "assets"
        target.mkdir()
        assets.mkdir()

        path, is_new = download_file(mock_service, file_meta, target, assets)
        assert is_new is True
        assert path.suffix == ".md"
        content = path.read_text()
        assert "source: google-drive" in content
        assert "Hello world" in content


# ── Drive sync tests ─────────────────────────────────────────────────


class TestDriveSync:
    """Tests for sync_drive_folder and sync_all_drive."""

    def _make_service(self, files: list[dict] | None = None):
        """Create a mock Drive service."""
        service = MagicMock()
        resp = {"files": files or [], "nextPageToken": None}
        service.files().list().execute.return_value = resp
        return service

    def test_sync_empty_folder(self, tmp_path: Path):
        """No files to sync returns zero counts."""
        from pester.sync.drive import sync_drive_folder

        service = self._make_service([])
        folder_config = {"id": "f1", "vault_dir": "reference/drive/test"}
        sync_state: dict = {}
        result = sync_drive_folder(service, folder_config, tmp_path, sync_state)
        assert result.files_added == 0
        assert result.files_updated == 0

    def test_sync_dry_run(self, tmp_path: Path):
        """Dry run reports counts but writes nothing."""
        from pester.sync.drive import sync_drive_folder

        files = [
            {"id": "f1", "name": "test.doc", "mimeType": "text/plain", "modifiedTime": "2026-01-01"}
        ]
        service = self._make_service(files)
        folder_config = {"id": "folder1", "vault_dir": "reference/drive/test"}
        sync_state: dict = {}
        result = sync_drive_folder(service, folder_config, tmp_path, sync_state, dry_run=True)
        assert result.files_added == 1
        # No files actually written
        drive_dir = tmp_path / "reference" / "drive" / "test"
        assert not drive_dir.exists()

    def test_sync_updates_state(self, tmp_path: Path):
        """sync_drive_folder updates sync state with last_sync timestamp."""
        from pester.sync.drive import sync_drive_folder

        service = MagicMock()
        files_resp = {
            "files": [
                {"id": "f1", "name": "doc", "mimeType": "text/plain", "modifiedTime": "2026-01-01"}
            ],
            "nextPageToken": None,
        }
        service.files().list().execute.return_value = files_resp
        service.files().get_media().execute.return_value = b"content"

        folder_config = {"id": "folder1", "vault_dir": "reference/drive/test"}
        sync_state: dict = {}
        sync_drive_folder(service, folder_config, tmp_path, sync_state)

        assert "drive" in sync_state
        assert "folder1" in sync_state["drive"]
        assert "last_sync" in sync_state["drive"]["folder1"]

    def test_single_file_failure_doesnt_abort(self, tmp_path: Path):
        """If one file fails download, others still sync."""
        from pester.sync.drive import sync_drive_folder

        service = MagicMock()
        files_resp = {
            "files": [
                {
                    "id": "f1",
                    "name": "good.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-01-01",
                },
                {
                    "id": "f2",
                    "name": "bad.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-01-01",
                },
            ],
            "nextPageToken": None,
        }
        service.files().list().execute.return_value = files_resp

        # First call succeeds, second fails
        call_count = 0

        def mock_get_media(**kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            if call_count == 1:
                mock.execute.return_value = b"good content"
            else:
                mock.execute.side_effect = Exception("download failed")
            return mock

        service.files().get_media = mock_get_media

        folder_config = {"id": "folder1", "vault_dir": "reference/drive/test"}
        sync_state: dict = {}
        result = sync_drive_folder(service, folder_config, tmp_path, sync_state)

        assert result.files_added == 1
        assert result.files_failed == 1
        assert len(result.errors) == 1

    def test_missing_credentials_raises(self, tmp_path: Path):
        """build_drive_service raises FileNotFoundError without credentials."""
        from pester.sync.drive import build_drive_service

        with pytest.raises(FileNotFoundError):
            build_drive_service(tmp_path / "nonexistent")

    @patch("pester.sync.drive.build_drive_service")
    def test_sync_all_drive_disabled(self, mock_build, tmp_path: Path):
        """sync_all_drive with no folders returns empty result."""
        from pester.sync.drive import sync_all_drive

        mock_build.return_value = MagicMock()
        config = {"sync": {"drive": {"enabled": True, "folders": []}}}
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        result = sync_all_drive(tmp_path, config, state_dir)
        assert result.files_added == 0

    @patch("pester.sync.drive.build_drive_service")
    @patch("pester.sync.drive.sync_drive_folder")
    def test_sync_all_drive_saves_state(self, mock_sync_folder, mock_build, tmp_path: Path):
        """sync_all_drive saves state after syncing."""
        from pester.sync.drive import SyncResult, sync_all_drive

        mock_build.return_value = MagicMock()
        mock_sync_folder.return_value = SyncResult(files_added=2)

        config = {
            "sync": {
                "drive": {"enabled": True, "folders": [{"id": "f1", "vault_dir": "ref/drive"}]}
            }
        }
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        sync_all_drive(tmp_path, config, state_dir)

        # State file should exist after sync
        assert (state_dir / "sync_state.json").is_file()


# ── Telegram digest tests ────────────────────────────────────────────


class TestTelegramDigest:
    """Tests for message -> daily digest markdown conversion."""

    def _make_messages(self, dates_and_texts: list[tuple[datetime, str, str]]) -> list[dict]:
        """Create message dicts from (datetime, sender, text) tuples."""
        return [
            {"id": i + 1, "date": dt, "sender": sender, "text": text, "media_type": None}
            for i, (dt, sender, text) in enumerate(dates_and_texts)
        ]

    def test_groups_by_date(self):
        """Messages from different dates produce separate digests."""
        from pester.sync.telegram import messages_to_daily_digest

        msgs = self._make_messages(
            [
                (datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc), "Alice", "Hello"),
                (datetime(2026, 3, 19, 11, 0, tzinfo=timezone.utc), "Bob", "Hi"),
            ]
        )
        digests = messages_to_daily_digest(msgs, "Test Chat", -100)
        assert date(2026, 3, 18) in digests
        assert date(2026, 3, 19) in digests
        assert len(digests) == 2

    def test_digest_frontmatter(self):
        """Digest includes type, source, chat metadata."""
        from pester.sync.telegram import messages_to_daily_digest

        msgs = self._make_messages(
            [
                (datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc), "Alice", "Hello"),
            ]
        )
        digests = messages_to_daily_digest(msgs, "Team Chat", -100123)
        content = digests[date(2026, 3, 18)]
        assert "source: telegram" in content
        assert 'chat: "Team Chat"' in content
        assert "chat_id: -100123" in content

    def test_message_formatting(self):
        """Messages include sender, time, and text."""
        from pester.sync.telegram import messages_to_daily_digest

        msgs = self._make_messages(
            [
                (datetime(2026, 3, 18, 14, 30, tzinfo=timezone.utc), "Alice", "Important update"),
            ]
        )
        digests = messages_to_daily_digest(msgs, "Chat", -1)
        content = digests[date(2026, 3, 18)]
        assert "**Alice**" in content
        assert "14:30" in content
        assert "Important update" in content

    def test_media_link_in_digest(self):
        """Media messages include relative link to asset."""
        from pester.sync.telegram import messages_to_daily_digest

        msgs = [
            {
                "id": 1,
                "date": datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc),
                "sender": "Alice",
                "text": "",
                "media_type": "photo",
                "media_path": "../assets/telegram-chat-1.jpg",
            }
        ]
        digests = messages_to_daily_digest(msgs, "Chat", -1)
        content = digests[date(2026, 3, 18)]
        assert "![photo](../assets/telegram-chat-1.jpg)" in content


# ── Telegram sync tests ──────────────────────────────────────────────


class TestTelegramSync:
    """Tests for Telegram sync functions."""

    def test_resolve_token_missing_raises(self, tmp_path: Path):
        """_resolve_bot_token raises FileNotFoundError without token."""
        from pester.sync.telegram import _resolve_bot_token

        with pytest.raises(FileNotFoundError):
            _resolve_bot_token(tmp_path)

    def test_resolve_token_from_file(self, tmp_path: Path):
        """_resolve_bot_token reads from bot_config.json."""
        from pester.sync.telegram import _resolve_bot_token

        cred_dir = tmp_path / "credentials" / "telegram"
        cred_dir.mkdir(parents=True)
        (cred_dir / "bot_config.json").write_text(json.dumps({"bot_token": "123:ABC"}))
        assert _resolve_bot_token(tmp_path) == "123:ABC"

    def test_resolve_token_env_takes_precedence(self, tmp_path: Path):
        """Environment variable takes precedence over file."""
        from pester.sync.telegram import _resolve_bot_token

        cred_dir = tmp_path / "credentials" / "telegram"
        cred_dir.mkdir(parents=True)
        (cred_dir / "bot_config.json").write_text(json.dumps({"bot_token": "file-token"}))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env-token"}):
            assert _resolve_bot_token(tmp_path) == "env-token"

    def test_resolve_token_migration_hint(self, tmp_path: Path):
        """Old Telethon config triggers migration message."""
        from pester.sync.telegram import _resolve_bot_token

        cred_dir = tmp_path / "credentials" / "telegram"
        cred_dir.mkdir(parents=True)
        (cred_dir / "api_config.json").write_text("{}")
        with pytest.raises(FileNotFoundError, match="Bot API"):
            _resolve_bot_token(tmp_path)

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token"})
    def test_sync_all_telegram_no_chats(self, tmp_path: Path):
        """sync_all_telegram with no chats returns empty result."""
        from pester.sync.telegram import sync_all_telegram

        config = {"sync": {"telegram": {"enabled": True, "chats": []}}}
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        result = sync_all_telegram(tmp_path, config, state_dir)
        assert result.messages_fetched == 0

    def test_action_extraction_skipped_when_no_tracking(self):
        """Without tracking module, _try_extract_actions returns 0."""
        from pester.sync.telegram import _try_extract_actions

        count = _try_extract_actions("TODO: test", {}, "chat")
        # HAS_TRACKING is False since tracking module doesn't exist yet
        assert count == 0

    def test_telegram_sync_result_merge(self):
        """TelegramSyncResult.merge aggregates correctly."""
        from pester.sync.telegram import TelegramSyncResult

        r1 = TelegramSyncResult(messages_fetched=10, files_created=2)
        r2 = TelegramSyncResult(messages_fetched=5, files_created=1, errors=["err1"])
        r1.merge(r2)
        assert r1.messages_fetched == 15
        assert r1.files_created == 3
        assert r1.errors == ["err1"]


class TestTelegramBotListener:
    """Tests for the TelegramBotListener class."""

    def _make_listener(self, tmp_path: Path, *, dry_run: bool = False):
        """Create a TelegramBotListener with test config."""
        from pester.sync.telegram import TelegramBotListener

        chat_configs = [
            {"id": -100123, "name": "Test Chat", "vault_dir": "reference/telegram"},
        ]
        state_dir = tmp_path / "state"
        state_dir.mkdir(exist_ok=True)
        return TelegramBotListener(
            bot_token="test-token",
            chat_configs=chat_configs,
            vault_path=tmp_path / "vault",
            config={},
            state_dir=state_dir,
            dry_run=dry_run,
        )

    def _make_update(self, chat_id: int, msg_id: int, text: str, sender: str = "Alice"):
        """Create a mock telegram Update."""
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.effective_message.message_id = msg_id
        update.effective_message.text = text
        update.effective_message.caption = None
        update.effective_message.date = datetime(2026, 3, 18, 14, 30, tzinfo=timezone.utc)
        update.effective_message.from_user.first_name = sender
        update.effective_message.from_user.last_name = None
        update.effective_message.photo = None
        update.effective_message.document = None
        return update

    def test_handle_message_writes_digest(self, tmp_path: Path):
        """Incoming message creates a daily digest file."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")
        update = self._make_update(-100123, 1, "Hello world")
        context = MagicMock()

        asyncio.run(listener._handle_message(update, context))

        digest = tmp_path / "vault" / "reference" / "telegram" / "2026-03-18.md"
        assert digest.exists()
        content = digest.read_text()
        assert "source: telegram" in content
        assert "**Alice**" in content
        assert "Hello world" in content
        assert listener._result.messages_fetched == 1
        assert listener._result.files_created == 1

    def test_handle_message_ignores_unknown_chat(self, tmp_path: Path):
        """Messages from unconfigured chats are ignored."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")
        update = self._make_update(-999999, 1, "Should be ignored")
        context = MagicMock()

        asyncio.run(listener._handle_message(update, context))

        assert listener._result.messages_fetched == 0

    def test_handle_message_dedup(self, tmp_path: Path):
        """Same message ID is not processed twice."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")
        update = self._make_update(-100123, 42, "First time")
        context = MagicMock()

        asyncio.run(listener._handle_message(update, context))
        asyncio.run(listener._handle_message(update, context))

        assert listener._result.messages_fetched == 1

    def test_digest_append(self, tmp_path: Path):
        """Two messages on the same day both appear in one file."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")
        context = MagicMock()

        update1 = self._make_update(-100123, 1, "First message")
        update2 = self._make_update(-100123, 2, "Second message")

        asyncio.run(listener._handle_message(update1, context))
        asyncio.run(listener._handle_message(update2, context))

        digest = tmp_path / "vault" / "reference" / "telegram" / "2026-03-18.md"
        content = digest.read_text()
        assert "First message" in content
        assert "Second message" in content
        assert "message_count: 2" in content
        assert listener._result.files_created == 1
        assert listener._result.files_updated == 1

    def test_dry_run_no_write(self, tmp_path: Path):
        """Dry run mode does not create files."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path, dry_run=True)
        listener._sync_state = load_sync_state(tmp_path / "state")
        update = self._make_update(-100123, 1, "Should not be written")
        context = MagicMock()

        asyncio.run(listener._handle_message(update, context))

        digest_dir = tmp_path / "vault" / "reference" / "telegram"
        assert not digest_dir.exists()
        assert listener._result.messages_fetched == 1

    def test_download_media_photo(self, tmp_path: Path):
        """Photo message triggers media download via Bot API."""
        from unittest.mock import AsyncMock

        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")

        # Build update with photo (async mocks for get_file/download_to_drive)
        update = self._make_update(-100123, 10, "")
        mock_photo = MagicMock()
        mock_file = MagicMock()
        mock_photo.get_file = AsyncMock(return_value=mock_file)
        mock_file.download_to_drive = AsyncMock()
        update.effective_message.photo = [MagicMock(), mock_photo]  # last is largest
        context = MagicMock()

        asyncio.run(listener._handle_message(update, context))

        mock_photo.get_file.assert_called_once()
        mock_file.download_to_drive.assert_called_once()
        assert listener._result.media_downloaded == 1

    def test_processed_ids_persist(self, tmp_path: Path):
        """Processed IDs survive save/load cycle."""
        from pester.sync.sync_state import load_sync_state, save_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")
        context = MagicMock()

        update = self._make_update(-100123, 77, "Track this")
        asyncio.run(listener._handle_message(update, context))

        # Save state
        listener._save_processed_ids()
        save_sync_state(tmp_path / "state", listener._sync_state)

        # Create new listener, load state
        listener2 = self._make_listener(tmp_path)
        listener2._sync_state = load_sync_state(tmp_path / "state")
        listener2._load_processed_ids()

        assert (-100123, 77) in listener2._processed_ids

    def test_private_message_rejects_unauthorized_user(self, tmp_path: Path):
        """Private messages from unauthorized users are silently dropped."""
        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")

        # Set up a mock agent with allowed_users (user 999 is NOT allowed)
        mock_agent = MagicMock()
        mock_agent._allowed_users = [111, 222]
        listener._agent = mock_agent

        update = MagicMock()
        update.effective_message.from_user.first_name = "Attacker"
        update.effective_message.from_user.id = 999
        update.effective_message.forward_origin = MagicMock()  # forwarded msg
        update.effective_message.text = "Injected content"
        update.effective_message.caption = None
        update.effective_message.voice = None
        update.effective_message.audio = None
        update.effective_message.video_note = None
        update.effective_chat.id = 999
        context = MagicMock()

        asyncio.run(listener._handle_private_message(update, context))

        # Vault should NOT have been written to
        fwd_dir = tmp_path / "vault" / "reference" / "telegram" / "forwarded"
        assert not fwd_dir.exists()

    def test_private_message_allows_authorized_user(self, tmp_path: Path):
        """Private forwarded messages from authorized users are saved and routed to the agent."""
        from unittest.mock import AsyncMock

        from pester.sync.sync_state import load_sync_state

        listener = self._make_listener(tmp_path)
        listener._sync_state = load_sync_state(tmp_path / "state")

        # Mock agent — allowed_users lets user 111 through, process_message
        # returns a short summary.
        mock_agent = MagicMock()
        mock_agent._allowed_users = [111, 222]
        mock_agent.process_message = MagicMock(return_value="Added 1 action.")
        listener._agent = mock_agent

        update = MagicMock()
        update.effective_message.from_user.first_name = "Owner"
        update.effective_message.from_user.id = 111
        update.effective_message.forward_origin = MagicMock()  # forwarded msg
        update.effective_message.text = "Important forwarded note"
        update.effective_message.caption = None
        update.effective_message.voice = None
        update.effective_message.audio = None
        update.effective_message.video_note = None
        update.effective_message.reply_text = AsyncMock()
        update.effective_chat.id = 111
        update.effective_message.date = datetime(2026, 3, 18, 14, 30, tzinfo=timezone.utc)
        context = MagicMock()
        # send_chat_action is awaited from the forwarded branch now that the
        # agent is invoked (typing indicator while it thinks).
        context.bot.send_chat_action = AsyncMock()

        asyncio.run(listener._handle_private_message(update, context))

        # Vault SHOULD have been written to
        fwd_dir = tmp_path / "vault" / "reference" / "telegram" / "forwarded"
        assert fwd_dir.exists()
        # Agent was invoked with the forwarded content wrapped in a triage prompt
        mock_agent.process_message.assert_called_once()
        prompt_arg = mock_agent.process_message.call_args[0][0]
        assert "Important forwarded note" in prompt_arg
        # Reply includes both the saved-confirmation prefix and the agent output
        reply_arg = update.effective_message.reply_text.call_args[0][0]
        assert "Сохранено" in reply_arg
        assert "Added 1 action." in reply_arg


class TestTelegramSetup:
    """Tests for the Telegram bot setup wizard."""

    @patch("pester.sync.telegram_setup._validate_token")
    @patch("click.prompt", return_value="123:ABC-TEST")
    @patch("click.echo")
    def test_setup_saves_bot_config(self, mock_echo, mock_prompt, mock_validate, tmp_path: Path):
        """Setup saves bot_config.json with the entered token."""
        from pester.sync.telegram_setup import run_telegram_setup

        mock_validate.return_value = "TestBot"
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        run_telegram_setup(state_dir)

        config_path = state_dir / "credentials" / "telegram" / "bot_config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["bot_token"] == "123:ABC-TEST"


# ── CLI tests ─────────────────────────────────────────────────────────


class TestSyncCLI:
    """Tests for cmd_sync.py CLI commands using Click CliRunner."""

    def test_sync_help(self):
        """pester sync --help shows drive/telegram subcommands."""
        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["sync", "--help"])
        assert result.exit_code == 0
        assert "drive" in result.output
        assert "telegram" in result.output

    def test_sync_drive_no_extra(self, tmp_vault: Path):
        """pester sync drive without [drive] extra shows install message."""
        from pester.cli.main import cli

        def _raise_drive():
            raise SystemExit("Drive sync requires: pip install pester[drive]")

        runner = CliRunner()
        with patch("pester.sync.require_drive", _raise_drive):
            result = runner.invoke(cli, ["--vault", str(tmp_vault), "sync", "drive"])
        assert result.exit_code != 0
        assert "pip install pester[drive]" in result.output or "pip install pester[drive]" in str(
            result.exception
        )

    def test_sync_telegram_no_extra(self, tmp_vault: Path):
        """pester sync telegram without [telegram] extra shows install message."""
        from pester.cli.main import cli

        def _raise_telegram():
            raise SystemExit("Telegram sync requires: pip install pester[telegram]")

        runner = CliRunner()
        with patch("pester.sync.require_telegram", _raise_telegram):
            result = runner.invoke(cli, ["--vault", str(tmp_vault), "sync", "telegram"])
        assert result.exit_code != 0
        assert (
            "pip install pester[telegram]" in result.output
            or "pip install pester[telegram]" in str(result.exception)
        )

    def test_sync_no_config(self, empty_vault: Path):
        """pester sync with no sync config shows helpful message."""
        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(empty_vault), "sync"])
        assert "No sync sources enabled" in result.output

    def test_sync_all_skips_missing_drive_and_shows_telegram_hint(self, tmp_vault: Path):
        """pester sync skips Drive if missing extra, shows listener hint for Telegram."""
        from pester.cli.main import cli

        # Write config enabling both drive and telegram
        config_path = tmp_vault / "pester.yaml"
        config_path.write_text(
            "sync:\n  drive:\n    enabled: true\n  telegram:\n    enabled: true\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        with patch(
            "pester.cli.cmd_sync._do_drive_sync",
            side_effect=SystemExit("Drive sync requires: pip install pester[drive]"),
        ):
            result = runner.invoke(cli, ["--vault", str(tmp_vault), "sync"])

        # Drive should be skipped with clean message
        assert "Skipping Drive:" in result.output or "Skipping Drive:" in (
            result.stderr if hasattr(result, "stderr") else ""
        )
        # Telegram shows listener mode hint instead of running
        assert "listener mode" in result.output
        assert result.exit_code == 0


# ── Init module tests ─────────────────────────────────────────────────


class TestSyncInit:
    """Tests for sync/__init__.py — optional extra detection."""

    def test_require_drive_flag_exists(self):
        """HAS_DRIVE flag is defined."""
        from pester.sync import HAS_DRIVE

        assert isinstance(HAS_DRIVE, bool)

    def test_require_telegram_flag_exists(self):
        """HAS_TELEGRAM flag is defined."""
        from pester.sync import HAS_TELEGRAM

        assert isinstance(HAS_TELEGRAM, bool)

    def test_require_drive_raises_without_extra(self):
        """require_drive() raises SystemExit when [drive] not installed."""
        from pester.core.extras import make_optional_check

        _, require_fn = make_optional_check("__nonexistent_pkg__", "drive", label="Drive sync")
        with pytest.raises(SystemExit, match="pip install pester\\[drive\\]"):
            require_fn()

    def test_require_telegram_raises_without_extra(self):
        """require_telegram() raises SystemExit when [telegram] not installed."""
        from pester.core.extras import make_optional_check

        _, require_fn = make_optional_check(
            "__nonexistent_pkg__", "telegram", label="Telegram sync"
        )
        with pytest.raises(SystemExit, match="pip install pester\\[telegram\\]"):
            require_fn()


class TestSplitMessage:
    """Tests for _split_message paragraph-aware splitting."""

    def test_short_message_single_chunk(self):
        from pester.sync.telegram import _split_message

        result = _split_message("short text", 4096)
        assert result == ["short text"]

    def test_split_at_paragraph_boundary(self):
        from pester.sync.telegram import _split_message

        text = "Line 1\nLine 2\nLine 3\nLine 4"
        result = _split_message(text, max_len=15)
        assert len(result) >= 2
        # Each chunk should end at a newline boundary
        for chunk in result[:-1]:
            assert "\n" not in chunk or chunk.endswith("\n") is False

    def test_hard_fallback_no_newlines(self):
        from pester.sync.telegram import _split_message

        text = "A" * 100  # No newlines
        result = _split_message(text, max_len=30)
        assert len(result) == 4  # 100 / 30 = 3.33 -> 4 chunks
        assert result[0] == "A" * 30


# ── Sync state locking tests ──────────────────────────────────────────


class TestSyncStateLocking:
    """Tests for sync state file locking."""

    def test_state_lock_prevents_concurrent_access(self, tmp_path):
        """state_lock acquires exclusive lock on the lock file."""
        import threading

        from pester.sync.sync_state import save_sync_state, state_lock

        results = []

        def writer(value, delay):
            with state_lock(tmp_path):
                from pester.sync.sync_state import load_sync_state

                state = load_sync_state(tmp_path)
                # Simulate work between load and save
                import time

                time.sleep(delay)
                state["writer"] = value
                save_sync_state(tmp_path, state)
                results.append(value)

        t1 = threading.Thread(target=writer, args=("first", 0.1))
        t2 = threading.Thread(target=writer, args=("second", 0.0))

        t1.start()
        import time

        time.sleep(0.02)  # Ensure t1 gets the lock first
        t2.start()

        t1.join(timeout=3)
        t2.join(timeout=3)

        # Both should complete without error
        assert len(results) == 2
        # Second writer runs after first due to lock
        assert results[0] == "first"
        assert results[1] == "second"

        # Final state should reflect the last writer
        from pester.sync.sync_state import load_sync_state

        final = load_sync_state(tmp_path)
        assert final["writer"] == "second"
