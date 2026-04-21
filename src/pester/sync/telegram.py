"""Telegram sync — listen for messages via Bot API and write to vault."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from pester.core.config import get_config_value
from pester.core.vault import atomic_write
from pester.sync.sync_state import (
    add_extraction_pending,
    get_extraction_pending,
    get_telegram_chat_state,
    load_sync_state,
    remove_extraction_pending,
    save_sync_state,
    set_telegram_chat_state,
)

log = logging.getLogger(__name__)

# Conditional T3 tracking import
try:
    from pester.tracking.extractor import extract_actions

    HAS_TRACKING = True
except ImportError:
    HAS_TRACKING = False

# Maximum recent message IDs to keep for dedup across restarts
_DEDUP_WINDOW = 200


@dataclass
class TelegramSyncResult:
    """Result of a Telegram sync operation."""

    messages_fetched: int = 0
    files_created: int = 0
    files_updated: int = 0
    media_downloaded: int = 0
    actions_extracted: int = 0
    transcriptions_completed: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: TelegramSyncResult) -> None:
        """Merge another result into this one."""
        self.messages_fetched += other.messages_fetched
        self.files_created += other.files_created
        self.files_updated += other.files_updated
        self.media_downloaded += other.media_downloaded
        self.actions_extracted += other.actions_extracted
        self.transcriptions_completed += other.transcriptions_completed
        self.errors.extend(other.errors)


def messages_to_daily_digest(
    messages: list[dict],
    chat_name: str,
    chat_id: int,
) -> dict[date, str]:
    """Group messages by date and format as daily markdown digests."""
    from collections import defaultdict

    by_date: dict[date, list[dict]] = defaultdict(list)
    for msg in messages:
        msg_date = msg["date"].date() if isinstance(msg["date"], datetime) else msg["date"]
        by_date[msg_date].append(msg)

    digests: dict[date, str] = {}
    now = datetime.now(timezone.utc).isoformat()

    for day, day_messages in sorted(by_date.items()):
        safe_name = chat_name.replace("\\", "\\\\").replace('"', '\\"')
        lines = [
            "---",
            "type: reference",
            "source: telegram",
            f'chat: "{safe_name}"',
            f"chat_id: {chat_id}",
            f"synced_at: {now}",
            f"message_count: {len(day_messages)}",
            "---",
            "",
            f"# {chat_name} — {day.isoformat()}",
            "",
        ]

        for msg in day_messages:
            lines.extend(_format_message_block(msg))

        digests[day] = "\n".join(lines)

    return digests


def _format_message_block(msg: dict) -> list[str]:
    """Format a single message into markdown lines."""
    time_str = msg["date"].strftime("%H:%M") if isinstance(msg["date"], datetime) else ""
    sender = msg["sender"] or "Unknown"
    text = msg["text"]

    lines = [f"**{sender}** ({time_str}):"]
    if text:
        lines.append(text)
    if msg.get("media_path"):
        lines.append(f"![{msg.get('media_type', 'media')}]({msg['media_path']})")
    lines.append("")
    return lines


def _try_extract_actions(
    content: str,
    config: dict,
    chat_name: str,
) -> int:
    """Attempt to extract action items from message content.

    Uses tracking.extractor if available (T3 dependency).
    Returns count of actions found.
    """
    if not HAS_TRACKING:
        return 0

    try:
        actions = extract_actions(content, config)
        return len(actions)
    except Exception as e:
        log.debug("Action extraction failed for %s: %s", chat_name, e)
        return 0


class TelegramBotListener:
    """Real-time Telegram message listener using Bot API long-polling."""

    def __init__(
        self,
        bot_token: str,
        chat_configs: list[dict],
        vault_path: Path,
        config: dict,
        state_dir: Path,
        *,
        dry_run: bool = False,
        agent: object | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_configs = chat_configs
        self._chat_config_by_id: dict[int, dict] = {c["id"]: c for c in chat_configs}
        self._vault_path = vault_path
        self._config = config
        self._state_dir = state_dir
        self._dry_run = dry_run
        self._agent = agent
        self._result = TelegramSyncResult()
        self._processed_ids: set[tuple[int, int]] = set()  # (chat_id, msg_id) pairs
        self._sync_state: dict = {}

    def run(self) -> TelegramSyncResult:
        """Start polling. Blocks until Ctrl+C. Returns result summary."""
        from telegram.ext import ApplicationBuilder, MessageHandler, filters

        self._sync_state = load_sync_state(self._state_dir)
        self._load_processed_ids()
        self._retry_pending_extractions()

        allowed_ids = list(self._chat_config_by_id.keys())
        app = ApplicationBuilder().token(self._bot_token).build()
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=allowed_ids) & ~filters.COMMAND,
                self._handle_message,
            )
        )

        # Private message handler for interactive bot agent
        if self._agent is not None:
            from telegram import BotCommand
            from telegram.ext import CallbackQueryHandler, CommandHandler

            app.add_handler(CommandHandler("start", self._handle_start))
            app.add_handler(CommandHandler("help", self._handle_help))
            app.add_handler(CommandHandler("reset", self._handle_reset))
            app.add_handler(CommandHandler("copilot", self._handle_mode_copilot))
            app.add_handler(CommandHandler("coach", self._handle_mode_provocateur))
            # Inline-keyboard callbacks (language picker + "try X" buttons)
            app.add_handler(CallbackQueryHandler(self._handle_callback_query))
            app.add_handler(
                MessageHandler(
                    filters.ChatType.PRIVATE & ~filters.COMMAND,
                    self._handle_private_message,
                )
            )

            # Populate the native Telegram "/" menu so all commands are
            # discoverable without reading /help first.
            async def _populate_menu(application):
                try:
                    await application.bot.set_my_commands(
                        [
                            BotCommand("start", "Welcome / Приветствие"),
                            BotCommand("help", "Full reference / Полный справочник"),
                            BotCommand("copilot", "Directive mode / Режим Копилот"),
                            BotCommand("coach", "Reflective mode / Режим Провокатор"),
                            BotCommand("reset", "Clear history / Очистить историю"),
                        ]
                    )
                except Exception as e:
                    log.warning("set_my_commands failed: %s", e)

            app.post_init = _populate_menu
            log.info("Bot agent enabled for private messages")

        log.info("Starting Telegram bot listener for %d chat(s)", len(allowed_ids))

        try:
            app.run_polling(drop_pending_updates=False)
        except KeyboardInterrupt:
            pass
        finally:
            self._save_processed_ids()
            if not self._dry_run:
                save_sync_state(self._state_dir, self._sync_state)

        return self._result

    async def _handle_message(self, update, context) -> None:
        """Process each incoming message from Telegram."""
        message = update.effective_message
        if message is None or update.effective_chat is None:
            return

        chat_id = update.effective_chat.id
        chat_config = self._chat_config_by_id.get(chat_id)
        if chat_config is None:
            return

        msg_id = message.message_id
        if (chat_id, msg_id) in self._processed_ids:
            return

        chat_name = chat_config.get("name", str(chat_id))

        # Build sender name
        sender = ""
        if message.from_user:
            sender = message.from_user.first_name or ""
            if message.from_user.last_name:
                sender = f"{sender} {message.from_user.last_name}".strip()

        # Determine media type and download
        media_type = None
        media_path = None
        if message.photo:
            media_type = "photo"
        elif message.document:
            media_type = "document"
        elif message.voice:
            media_type = "voice"
        elif message.audio:
            media_type = "audio"
        elif message.video_note:
            media_type = "video_note"

        if media_type and not self._dry_run:
            try:
                media_path = await self._download_media(message, chat_name)
                if media_path:
                    self._result.media_downloaded += 1
                    # Process media for text extraction (PDF + image OCR)
                    if media_type in ("photo", "document"):
                        try:
                            from pester.sync.media import process_media

                            # Resolve absolute path from relative ../assets/filename
                            abs_media_path = (
                                self._vault_path / "reference" / "telegram" / media_path
                            ).resolve()
                            m_type = (
                                "pdf" if str(abs_media_path).lower().endswith(".pdf") else "photo"
                            )
                            # Track as pending before attempting extraction
                            add_extraction_pending(
                                self._sync_state,
                                chat_id,
                                msg_id,
                                str(abs_media_path),
                                m_type,
                            )
                            stub_path = process_media(
                                file_path=abs_media_path,
                                media_type=m_type,
                                msg_id=msg_id,
                                vault_path=self._vault_path,
                                config=self._config,
                                state_dir=self._state_dir,
                            )
                            if stub_path:
                                remove_extraction_pending(self._sync_state, chat_id, msg_id)
                                log.info(
                                    "Media extracted: %s -> %s",
                                    abs_media_path.name,
                                    stub_path.name,
                                )
                        except Exception:
                            log.warning(
                                "Media extraction failed for %s",
                                media_path,
                                exc_info=True,
                            )
            except Exception as e:
                log.warning("Media download failed for message %d: %s", msg_id, e)

        # Transcribe voice/audio messages
        text = message.text or message.caption or ""
        if media_type in ("voice", "audio", "video_note") and not self._dry_run:
            transcript = await self._transcribe_audio(message)
            if transcript:
                text = f"[Транскрипт голосового сообщения]\n{transcript}"
                self._result.transcriptions_completed += 1
            elif not text:
                text = "[голосовое сообщение — расшифровка недоступна]"

        msg_dict = {
            "id": msg_id,
            "date": message.date or datetime.now(timezone.utc),
            "sender": sender,
            "text": text,
            "media_type": media_type,
            "media_path": media_path,
        }

        if self._dry_run:
            log.info("[DRY RUN] %s | %s: %s", chat_name, sender, msg_dict["text"][:100])
            self._result.messages_fetched += 1
            self._processed_ids.add((chat_id, msg_id))
            return

        # Append to daily digest
        try:
            created = self._append_to_digest(msg_dict, chat_config)
        except OSError as e:
            log.warning("Failed to write digest for message %d: %s", msg_id, e)
            self._result.errors.append(f"message {msg_id}: {e}")
            return

        self._result.messages_fetched += 1
        if created:
            self._result.files_created += 1
        else:
            self._result.files_updated += 1

        # Action extraction
        if chat_config.get("extract_actions", False) and msg_dict["text"]:
            self._result.actions_extracted += _try_extract_actions(
                msg_dict["text"], self._config, chat_name
            )

        # Update state — merge into existing chat state to preserve processed_ids
        self._processed_ids.add((chat_id, msg_id))
        chat_state = get_telegram_chat_state(self._sync_state, chat_id)
        chat_state["last_message_id"] = max(chat_state.get("last_message_id", 0), msg_id)
        chat_state["last_sync"] = datetime.now(timezone.utc).isoformat()
        set_telegram_chat_state(self._sync_state, chat_id, chat_state)

        log.info("Received: %s | %s: %s", chat_name, sender, msg_dict["text"][:80])

    def _retry_pending_extractions(self) -> None:
        """Retry any pending media extractions from previous runs."""
        for chat_id in self._chat_config_by_id:
            pending = get_extraction_pending(self._sync_state, chat_id)
            if not pending:
                continue
            log.info("Retrying %d pending extraction(s) for chat %d", len(pending), chat_id)
            for item in pending:
                try:
                    from pester.sync.media import process_media

                    file_path = Path(item["file_path"])
                    if not file_path.exists():
                        log.warning("Pending extraction file missing: %s", file_path)
                        remove_extraction_pending(self._sync_state, chat_id, item["msg_id"])
                        continue
                    stub_path = process_media(
                        file_path=file_path,
                        media_type=item["media_type"],
                        msg_id=item["msg_id"],
                        vault_path=self._vault_path,
                        config=self._config,
                        state_dir=self._state_dir,
                    )
                    if stub_path:
                        remove_extraction_pending(self._sync_state, chat_id, item["msg_id"])
                        log.info("Retry succeeded: %s -> %s", file_path.name, stub_path.name)
                except Exception:
                    log.warning(
                        "Retry failed for pending extraction: %s",
                        item["msg_id"],
                        exc_info=True,
                    )

    def _append_to_digest(self, msg_dict: dict, chat_config: dict) -> bool:
        """Append a single message to today's digest file.

        Returns True if a new file was created, False if appended to existing.
        """
        chat_id = chat_config["id"]
        chat_name = chat_config.get("name", str(chat_id))
        # Accept `vault_path` as an alias of `vault_dir` (older configs used this name).
        vault_subdir = chat_config.get("vault_dir") or chat_config.get(
            "vault_path", "reference/telegram"
        )
        if "vault_path" in chat_config and "vault_dir" not in chat_config:
            log.warning("Telegram chat %s uses 'vault_path:'; rename to 'vault_dir:'.", chat_id)
        vault_dir = self._vault_path / vault_subdir
        vault_dir.mkdir(parents=True, exist_ok=True)

        msg_date = msg_dict["date"]
        if isinstance(msg_date, datetime):
            day = msg_date.date()
        else:
            day = msg_date

        out_path = vault_dir / f"{day.isoformat()}.md"
        message_block = "\n".join(_format_message_block(msg_dict))

        if out_path.exists():
            content = out_path.read_text(encoding="utf-8")
            # Update message_count in frontmatter
            content = re.sub(
                r"^(message_count: )(\d+)",
                lambda m: f"{m.group(1)}{int(m.group(2)) + 1}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            # Update synced_at
            now = datetime.now(timezone.utc).isoformat()
            content = re.sub(
                r"^(synced_at: ).+$",
                f"\\g<1>{now}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            content = content.rstrip("\n") + "\n" + message_block
            atomic_write(out_path, content)
            return False
        else:
            # Create new digest with full frontmatter
            digests = messages_to_daily_digest([msg_dict], chat_name, chat_id)
            content = digests[day]
            atomic_write(out_path, content)
            return True

    async def _download_media(self, message, chat_name: str) -> str | None:
        """Download media via Bot API. Returns relative path or None."""
        assets_dir = self._vault_path / "reference" / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        safe_chat = chat_name.lower().replace(" ", "-")
        msg_id = message.message_id

        try:
            if message.photo:
                photo = message.photo[-1]  # Largest size
                tg_file = await photo.get_file()
                filename = f"telegram-{safe_chat}-{msg_id}.jpg"
                target = assets_dir / filename
                await tg_file.download_to_drive(target)
                return f"../assets/{filename}"

            elif message.document:
                tg_file = await message.document.get_file()
                ext = (
                    Path(message.document.file_name).suffix
                    if message.document.file_name
                    else ".bin"
                )
                filename = f"telegram-{safe_chat}-{msg_id}{ext}"
                target = assets_dir / filename
                await tg_file.download_to_drive(target)
                return f"../assets/{filename}"

            elif message.voice:
                tg_file = await message.voice.get_file()
                filename = f"telegram-{safe_chat}-{msg_id}.ogg"
                target = assets_dir / filename
                await tg_file.download_to_drive(target)
                return f"../assets/{filename}"

            elif message.audio:
                tg_file = await message.audio.get_file()
                ext = Path(message.audio.file_name).suffix if message.audio.file_name else ".mp3"
                filename = f"telegram-{safe_chat}-{msg_id}{ext}"
                target = assets_dir / filename
                await tg_file.download_to_drive(target)
                return f"../assets/{filename}"

            elif message.video_note:
                tg_file = await message.video_note.get_file()
                filename = f"telegram-{safe_chat}-{msg_id}.mp4"
                target = assets_dir / filename
                await tg_file.download_to_drive(target)
                return f"../assets/{filename}"
        except Exception as e:
            log.warning("Failed to download media for message %d: %s", msg_id, e)

        return None

    async def _transcribe_audio(self, message) -> str:
        """Download voice/audio from Telegram, transcribe via Groq Whisper."""
        try:
            from pester.bot import HAS_GROQ

            if not HAS_GROQ:
                return ""

            audio = message.voice or message.audio or message.video_note
            if not audio:
                return ""

            voice_file = await audio.get_file()
            ogg_data = await voice_file.download_as_bytearray()

            import asyncio

            from pester.bot.agent import transcribe_voice

            text = await asyncio.to_thread(transcribe_voice, bytes(ogg_data), self._config)
            return text
        except Exception as e:
            log.warning("Voice transcription failed: %s", e)
            return ""

    def _default_language(self) -> str:
        """Return the bot's default UI language derived from vault.language.

        Returns "ru" only when vault.language == "ru". Anything else (including
        "mixed", "de", missing config) collapses to "en" so a freshly initialized
        English vault never surfaces Russian text to its operator.
        """
        from pester.core.config import get_config_value

        lang = get_config_value(self._config, "vault.language", "en")
        return "ru" if lang == "ru" else "en"

    def _user_language(self, user_id: int) -> str:
        """Return the user's saved language preference, defaulting to vault.language."""
        default = self._default_language()
        if not user_id:
            return default
        try:
            from pester.coaching.modes import load_language_preferences

            saved = load_language_preferences(self._state_dir).get(user_id)
        except Exception:
            saved = None
        if saved == "ru":
            return "ru"
        if saved == "en":
            return "en"
        return default

    async def _handle_start(self, update, context) -> None:
        """Respond to /start: skip picker if language is already saved."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        user_id = update.effective_user.id if update.effective_user else 0
        if user_id:
            try:
                from pester.coaching.modes import load_language_preferences

                existing = load_language_preferences(self._state_dir).get(user_id)
            except Exception:
                existing = None
            if existing in ("ru", "en"):
                # Returning user — skip picker, go straight to welcome
                await self._send_welcome(update, context, existing)
                return

        name = self._config.get("bot", {}).get("name", "pester")
        text = (
            f"👋 Привет, я *{name}*.\n"
            f"Hi, I'm *{name}*.\n\n"
            "Я AI-ассистент для твоего markdown vault'а — задачи, поиск, сводки, "
            "голос, пересылки.\n"
            "I'm an AI assistant for your markdown vault — tasks, search, digests, "
            "voice, forwards.\n\n"
            "Выбери язык / Choose language:"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
                    InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
                ]
            ]
        )
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    async def _send_welcome(self, update, context, lang: str) -> None:
        """Send the post-language welcome with CTA buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        name = self._config.get("bot", {}).get("name", "pester")
        # Collapse unknown locales (e.g. "de", "mixed") to English UI.
        if lang != "ru":
            lang = "en"
        if lang == "en":
            text = (
                f"🎉 Language set to English.\n\n"
                f"I'm *{name}* — your AI knowledge-vault assistant.\n\n"
                "*✨ What I can do:*\n"
                "• 📋 Actions: add, list, complete, reschedule\n"
                "• 🔍 Semantic search across vault\n"
                "• 📊 Health: overdue, journal gaps, broken links\n"
                "• 📝 Briefings & weekly digests\n"
                "• 🎙 Voice messages (transcribed + processed)\n"
                "• 📎 Forwarded content (saved + actions extracted)\n\n"
                "Type /help any time for the full reference.\n\n"
                "*Try one to get started:*"
            )
            btn_health = "📊 Vault health"
            btn_actions = "📋 What's overdue?"
            btn_add = "➕ Add an action"
            btn_focus = "🎯 Morning focus"
        else:
            text = (
                f"🎉 Язык: русский.\n\n"
                f"Я — *{name}*, AI-ассистент для твоего knowledge vault'а.\n\n"
                "*✨ Что умею:*\n"
                "• 📋 Задачи: добавить, список, закрыть, перенести\n"
                "• 🔍 Семантический поиск по vault'у\n"
                "• 📊 Здоровье: что просрочено, дыры в journal, битые ссылки\n"
                "• 📝 Briefings + weekly digest\n"
                "• 🎙 Голосовые (транскрибирую + обрабатываю)\n"
                "• 📎 Пересланное (сохраняю + вытаскиваю actions)\n\n"
                "В любой момент — /help для полного справочника.\n\n"
                "*Попробуй один из вариантов чтобы начать:*"
            )
            btn_health = "📊 Здоровье vault'а"
            btn_actions = "📋 Что просрочено?"
            btn_add = "➕ Добавить задачу"
            btn_focus = "🎯 Фокус на сегодня"

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(btn_health, callback_data="try:health")],
                [InlineKeyboardButton(btn_actions, callback_data="try:overdue")],
                [InlineKeyboardButton(btn_add, callback_data="try:add_action")],
                [InlineKeyboardButton(btn_focus, callback_data="try:morning_focus")],
            ]
        )
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    async def _handle_callback_query(self, update, context) -> None:
        """Handle inline-keyboard button presses (lang:* and try:*)."""
        import asyncio

        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()  # clear the loading spinner on the button

        user_id = query.from_user.id if query.from_user else 0
        allowed = getattr(self._agent, "_allowed_users", []) if self._agent else []
        if not allowed or user_id not in allowed:
            return

        data = query.data

        # Language pick — save preference and send the main welcome
        if data.startswith("lang:"):
            raw_lang = data.split(":", 1)[1]
            # Constrain to known locales before saving or rendering the welcome.
            lang = raw_lang if raw_lang in ("ru", "en") else self._default_language()
            if raw_lang in ("ru", "en"):
                from pester.coaching.modes import save_language_preference

                save_language_preference(self._state_dir, user_id, lang)
            # Replace the picker with a confirmation, then send welcome
            try:
                ack = "✓ Язык сохранён." if lang == "ru" else "✓ Language saved."
                await query.edit_message_text(ack)
            except Exception:
                pass

            class _Shim:
                def __init__(self, msg):
                    self.effective_message = msg

            await self._send_welcome(_Shim(query.message), context, lang)
            return

        # "Try X" buttons — send the corresponding question to the agent
        if data.startswith("try:") and self._agent is not None:
            kind = data.split(":", 1)[1]
            user_lang = self._user_language(user_id)
            prompts = {
                "ru": {
                    "health": "Покажи здоровье vault'а.",
                    "overdue": "Что у меня просрочено?",
                    "add_action": ("Объясни как добавить задачу — покажи 2-3 формата на примерах."),
                    "morning_focus": "Morning focus: какие 3 приоритета на сегодня?",
                },
                "en": {
                    "health": "Show me the vault health report.",
                    "overdue": "What's overdue?",
                    "add_action": ("Explain how to add an action — show 2-3 example formats."),
                    "morning_focus": "Morning focus: what are today's top 3 priorities?",
                },
            }
            prompt = prompts.get(user_lang, prompts["en"]).get(kind)
            if not prompt:
                return

            sender = query.from_user.first_name if query.from_user else ""
            chat_id = str(query.message.chat.id) if query.message else ""
            mode = self._resolve_mode(user_id)

            await context.bot.send_chat_action(chat_id=query.message.chat.id, action="typing")
            try:
                response = await asyncio.to_thread(
                    self._agent.process_message, prompt, sender, user_id, chat_id, mode
                )
            except Exception as e:
                log.error("Agent error on try-button: %s", e)
                response = (
                    "Ошибка при обработке. Попробуйте ещё раз."
                    if user_lang == "ru"
                    else "Something went wrong. Please try again."
                )
            for chunk in _split_message(response, 4096):
                try:
                    await query.message.reply_text(chunk, parse_mode="Markdown")
                except Exception:
                    await query.message.reply_text(chunk)

    async def _handle_help(self, update, context) -> None:
        """Respond to /help in the user's saved language (default: Russian)."""
        user_id = update.effective_user.id if update.effective_user else 0
        lang = self._user_language(user_id)
        if lang == "en":
            text = (
                "📘 *pester bot — Reference*\n\n"
                "*━━ Commands ━━*\n"
                "/start    — welcome\n"
                "/help     — this help\n"
                "/reset    — clear conversation history\n"
                "/copilot  — directive mode (task-focused, crisp answers)\n"
                "/coach    — reflective mode (deeper questions)\n\n"
                "*━━ Modes ━━*\n"
                "• *Auto* (default): Copilot during work hours, Coach evenings/weekends\n"
                "• *Copilot*: crisp, actionable, task-first\n"
                "• *Coach*: asks deep questions, helps you think\n\n"
                "*━━ Capabilities ━━*\n"
                "📋 *Actions*: add, list (open/overdue/by owner), complete, reschedule\n"
                "🔍 *Search*: semantic search across all vault documents\n"
                "📊 *Health*: overdue, journal gaps, broken wikilinks, freshness\n"
                "📝 *Briefings*: summary for a person, project, or topic\n"
                "📈 *Digests*: weekly digest, daily standup, morning focus\n"
                "🎯 *Goals*: OKR / milestone progress\n"
                "🎙 *Voice*: send a voice note → transcribed + processed\n"
                "📎 *Forwarded*: forward anything → saved + actions extracted\n\n"
                "*━━ Example prompts ━━*\n"
                "• «what's overdue?»\n"
                "• «add action @diana — prepare pitch — by 2026-04-22»\n"
                "• «search: decisions about pricing»\n"
                "• «briefing on Alice»\n"
                "• «health check»\n"
                "• «weekly digest»\n"
                "• «morning focus»\n\n"
                "*━━ Language ━━*\n"
                "I respond in your saved language. Run /start to change it.\n\n"
                "*━━ Docs ━━*\n"
                "github.com/stansolo93/pester"
            )
        else:
            text = (
                "📘 *pester bot — Справочник*\n\n"
                "*━━ Команды ━━*\n"
                "/start    — приветствие\n"
                "/help     — эта справка\n"
                "/reset    — очистить историю диалога\n"
                "/copilot  — режим Копилот (директивный, task-focused)\n"
                "/coach    — режим Провокатор (рефлексивные вопросы)\n\n"
                "*━━ Режимы ━━*\n"
                "• *Auto* (по умолчанию): Копилот днём, Провокатор вечером/выходные\n"
                "• *Копилот*: чётко, коротко, по задачам\n"
                "• *Провокатор*: задаёт вопросы, помогает думать глубже\n\n"
                "*━━ Что я могу ━━*\n"
                "📋 *Задачи*: добавить, список (открытые/overdue/by owner), закрыть, перенести\n"
                "🔍 *Поиск*: семантический поиск по всем документам vault'а\n"
                "📊 *Health*: overdue, дыры в journal, битые wikilinks, свежесть\n"
                "📝 *Briefings*: сводка по человеку, проекту или теме\n"
                "📈 *Digests*: weekly digest, daily standup, morning focus\n"
                "🎯 *Goals*: прогресс по OKR / milestones\n"
                "🎙 *Голосовые*: отправь voice → транскрибирую + обработаю\n"
                "📎 *Пересылки*: перешли что угодно → сохраню + выну actions\n\n"
                "*━━ Примеры запросов ━━*\n"
                "• «что у меня overdue?»\n"
                "• «добавь action @diana — подготовить питч — до 2026-04-22»\n"
                "• «search: decisions about pricing»\n"
                "• «briefing на Алису»\n"
                "• «health check»\n"
                "• «weekly digest»\n"
                "• «morning focus»\n\n"
                "*━━ Язык ━━*\n"
                "Я отвечаю на сохранённом языке. Чтобы сменить — /start.\n\n"
                "*━━ Docs ━━*\n"
                "github.com/stansolo93/pester"
            )
        await update.effective_message.reply_text(text, parse_mode="Markdown")

    async def _handle_reset(self, update, context) -> None:
        """Clear conversation history for this user."""
        user_id = update.effective_user.id if update.effective_user else 0
        if self._agent is not None and hasattr(self._agent, "_store") and self._agent._store:
            if user_id:
                self._agent._store.clear(user_id)
        lang = self._user_language(user_id)
        msg = "History cleared." if lang == "en" else "История очищена."
        await update.effective_message.reply_text(msg)

    def _resolve_mode(self, user_id: int) -> str:
        """Resolve coaching mode for a user from overrides + config + time."""
        from pester.coaching.modes import get_mode, load_mode_overrides

        overrides = load_mode_overrides(self._state_dir)
        user_override = overrides.get(user_id)
        tz = self._config.get("scheduler", {}).get("timezone")
        return get_mode(self._config, user_override, tz)

    async def _handle_mode_copilot(self, update, context) -> None:
        """Switch to copilot mode."""
        if update.effective_message is None:
            return
        from pester.coaching.modes import save_mode_override

        user_id = update.effective_user.id if update.effective_user else 0
        if user_id:
            save_mode_override(self._state_dir, user_id, "copilot")
        lang = self._user_language(user_id)
        msg = "Mode: Copilot ⚔️" if lang == "en" else "Режим: Копилот \u2694\ufe0f"
        await update.effective_message.reply_text(msg)

    async def _handle_mode_provocateur(self, update, context) -> None:
        """Switch to provocateur mode."""
        if update.effective_message is None:
            return
        from pester.coaching.modes import save_mode_override

        user_id = update.effective_user.id if update.effective_user else 0
        if user_id:
            save_mode_override(self._state_dir, user_id, "provocateur")
        lang = self._user_language(user_id)
        msg = "Mode: Coach 🧠" if lang == "en" else "Режим: Провокатор \U0001f9e0"
        await update.effective_message.reply_text(msg)

    async def _handle_private_message(self, update, context) -> None:
        """Handle private messages: forwards saved to vault, direct text to agent."""
        import asyncio

        message = update.effective_message
        if message is None or self._agent is None:
            return

        sender = ""
        user_id = 0
        if message.from_user:
            sender = message.from_user.first_name or ""
            user_id = message.from_user.id

        # Access control — must match VaultAgent.allowed_users
        allowed = getattr(self._agent, "_allowed_users", [])
        if not allowed or user_id not in allowed:
            log.warning("Private message rejected: unauthorized user_id=%s", user_id)
            return

        # Forwarded messages — save to vault AND route through the agent so
        # the LLM can intelligently extract actions (via vault_add_action) and
        # return a useful summary, not just a dry "saved" confirmation.
        if message.forward_origin is not None:
            text = message.text or message.caption or ""
            if message.voice or message.audio or message.video_note:
                transcript = await self._transcribe_audio(message)
                if transcript:
                    text = transcript
            if not text.strip():
                return

            # Archive the forwarded content in the vault
            saved = False
            msg_dict = {
                "id": message.message_id,
                "date": message.date or datetime.now(timezone.utc),
                "sender": f"[переслано] {sender}",
                "text": text,
                "media_type": None,
                "media_path": None,
            }
            fwd_config = {
                "id": 0,
                "name": "Forwarded",
                "vault_dir": "reference/telegram/forwarded",
            }
            try:
                self._append_to_digest(msg_dict, fwd_config)
                saved = True
            except OSError as e:
                log.warning("Failed to save forwarded message: %s", e)

            # Show typing while the agent works
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action="typing",
            )

            # Wrap the forwarded content in an agent prompt so it knows this is
            # content to triage, not a direct question from the user.
            forward_prompt = (
                "Пользователь переслал следующий контент "
                "(уже сохранён в базу знаний):\n\n"
                f"---\n{text}\n---\n\n"
                "Если в тексте есть явные задачи, обязательства или договорённости — "
                "добавь их через vault_add_action (owner = author если упомянут, "
                "иначе оставь пустым; due_date если есть явная дата). "
                "Затем дай короткую сводку (2-3 предложения) что было важного. "
                "Если ничего не требует действия — одной фразой опиши что это."
            )

            mode = self._resolve_mode(user_id)
            try:
                chat_id = str(update.effective_chat.id)
                agent_response = await asyncio.to_thread(
                    self._agent.process_message,
                    forward_prompt,
                    sender,
                    user_id,
                    chat_id,
                    mode,
                )
            except Exception as e:
                log.error("Agent error on forwarded message: %s", e)
                agent_response = ""

            prefix = "✓ Сохранено в базу знаний.\n\n" if saved else ""
            body = agent_response.strip() if agent_response else ""
            final = (prefix + body).strip() or "Сохранено в базу знаний."

            for chunk in _split_message(final, 4096):
                try:
                    await message.reply_text(chunk, parse_mode="Markdown")
                except Exception:
                    await message.reply_text(chunk)
            return

        # Voice messages — transcribe first
        if message.voice or message.audio or message.video_note:
            text = await self._transcribe_audio(message)
            if not text:
                await message.reply_text("Не удалось распознать голосовое сообщение.")
                return
        else:
            text = message.text or message.caption or ""

        if not text.strip():
            return

        # Send typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing",
        )

        # Resolve coaching mode
        mode = self._resolve_mode(user_id)

        # Route through VaultAgent
        try:
            chat_id = str(update.effective_chat.id)
            response = await asyncio.to_thread(
                self._agent.process_message, text, sender, user_id, chat_id, mode
            )
        except Exception as e:
            log.error("Agent error: %s", e)
            response = "Произошла ошибка при обработке сообщения."

        for chunk in _split_message(response, 4096):
            try:
                await message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await message.reply_text(chunk)

    def _load_processed_ids(self) -> None:
        """Load recently processed message IDs from state for dedup."""
        for chat_id in self._chat_config_by_id:
            chat_state = get_telegram_chat_state(self._sync_state, chat_id)
            ids = chat_state.get("processed_ids", [])
            self._processed_ids.update((chat_id, mid) for mid in ids)

    def _save_processed_ids(self) -> None:
        """Persist recent processed message IDs to state, per chat."""
        for chat_id in self._chat_config_by_id:
            chat_state = get_telegram_chat_state(self._sync_state, chat_id)
            # Collect IDs belonging to this chat
            chat_ids = {mid for cid, mid in self._processed_ids if cid == chat_id}
            existing = set(chat_state.get("processed_ids", []))
            merged = existing | chat_ids
            # Trim to window size, keeping the highest IDs
            trimmed = sorted(merged, reverse=True)[:_DEDUP_WINDOW]
            chat_state["processed_ids"] = trimmed
            set_telegram_chat_state(self._sync_state, chat_id, chat_state)


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split long text into Telegram-safe chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find last newline within limit
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len  # fallback: hard split
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _resolve_bot_token(state_dir: Path) -> str:
    """Resolve bot token: env var first, then bot_config.json file."""
    # Environment variable (matches daemon notification behaviour)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token

    # File-based credential from --setup
    config_path = state_dir / "credentials" / "telegram" / "bot_config.json"
    if config_path.is_file():
        with open(config_path) as f:
            data = json.load(f)
        token = data.get("bot_token")
        if token:
            return token

    # Migration hint
    old_config = state_dir / "credentials" / "telegram" / "api_config.json"
    if old_config.is_file():
        raise FileNotFoundError(
            "Old Telethon credentials found but no bot token. "
            "pester now uses the Bot API. Run: pester sync telegram --setup"
        )

    raise FileNotFoundError(
        "No Telegram bot token found. Set TELEGRAM_BOT_TOKEN or run: pester sync telegram --setup"
    )


def sync_all_telegram(
    vault_path: Path,
    config: dict,
    state_dir: Path,
    *,
    dry_run: bool = False,
) -> TelegramSyncResult:
    """Start bot listener for all configured Telegram chats.

    Blocks until Ctrl+C. Main entry point for CLI.
    """
    bot_token = _resolve_bot_token(state_dir)

    chats = get_config_value(config, "sync.telegram.chats", [])
    bot_enabled = get_config_value(config, "bot.enabled", False)
    if not chats and not bot_enabled:
        log.info("No Telegram chats configured and bot not enabled in pester.yaml")
        return TelegramSyncResult()
    if not chats:
        log.info("No Telegram chats configured, running in bot-only mode")

    # Create bot agent if enabled
    agent = None
    if get_config_value(config, "bot.enabled", False):
        try:
            from pester.bot.agent import VaultAgent
            from pester.bot.conversation import ConversationStore
            from pester.mcp.server import VaultTools

            tools = VaultTools(vault_path, config, state_dir)
            store = ConversationStore(
                state_dir,
                max_history=get_config_value(config, "bot.max_history", 20),
            )
            agent = VaultAgent(tools, config, conversation_store=store)
            log.info("Bot agent enabled for interactive private messages")
        except Exception as e:
            log.warning("Bot agent unavailable: %s", e)

    listener = TelegramBotListener(
        bot_token=bot_token,
        chat_configs=chats,
        vault_path=vault_path,
        config=config,
        state_dir=state_dir,
        dry_run=dry_run,
        agent=agent,
    )
    return listener.run()
