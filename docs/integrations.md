# Integrations

pester syncs content from external sources and exposes vault capabilities via MCP for AI-assisted workflows.

Status for 1.0.0 soft launch: the core CLI is the stable surface. Google Drive sync, Telegram
sync, bot/daemon automation, and remote self-hosted MCP should be treated as beta. Local stdio
MCP for Claude Code / Claude Desktop is supported today.

## Google Drive (Beta)

**Requires:** `pip install pester[drive]`

Status: beta surface for the 1.0.0 soft launch.

Sync files from Google Drive folders into your vault. Useful for pulling shared documents, spreadsheets, and slides into your knowledge base.

### Setup

1. Install the Drive extra:

   ```bash
   pip install pester[drive]
   ```

2. Run the interactive setup:

   ```bash
   pester sync drive --setup
   ```

   This walks you through:
   - Creating a Google Cloud project (or using an existing one)
   - Enabling the Drive API
   - Creating OAuth credentials
   - Authorizing pester to access your Drive

3. Configure sync folders in `pester.yaml`:

   ```yaml
   sync:
     drive:
       enabled: true
       folders:
         - id: "1a2b3c4d5e6f..."        # Google Drive folder ID
           vault_dir: reference/drive    # Local folder in vault
         - id: "7g8h9i0j1k2l..."
           vault_dir: reference/drive/board
   ```

4. Run sync:

   ```bash
   pester sync drive
   ```

### How It Works

- Files are downloaded from the specified Drive folders into target directories
- Google Docs are exported as markdown
- Sheets and Slides are exported as PDF into `reference/assets/`
- Only changed files are re-downloaded (uses Drive's change detection)
- Synced files can be searched with `pester search` and have actions extracted with `pester actions extract`

## Telegram (Beta)

**Requires:** `pip install pester[telegram]`

Status: beta surface for the 1.0.0 soft launch.

Sync messages from Telegram chats and channels into your vault in real time. The bot listens for incoming messages via the Bot API and writes them as daily digest files.

### Setup

1. Install the Telegram extra:

   ```bash
   pip install pester[telegram]
   ```

2. Run the interactive setup:

   ```bash
   pester sync telegram --setup
   ```

   This walks you through:
   - Creating a bot via [@BotFather](https://t.me/BotFather) on Telegram
   - Entering the bot token

3. Add the bot to your group or channel as an admin so it can receive messages.

4. Configure sync chats in `pester.yaml`:

   ```yaml
   sync:
     telegram:
       enabled: true
       chats:
         - name: "Team Chat"
           id: -1001234567890
           vault_dir: reference/telegram
         - name: "Founder Updates"
           id: -1009876543210
           vault_dir: reference/telegram/updates
   ```

5. Start the listener:

   ```bash
   pester sync telegram
   ```

   Press Ctrl+C to stop.

### How It Works

- The bot runs in long-polling mode and processes messages as they arrive
- Each message is appended to a daily markdown digest file (e.g. `2026-03-18.md`)
- Media attachments (photos, documents up to 20 MB) are saved to `reference/assets/`
- Messages are grouped by date for easy browsing
- Synced messages can be searched and have actions extracted
- The bot token can also be set via the `TELEGRAM_BOT_TOKEN` environment variable

### Notifications

The same `[telegram]` extra powers push notifications from the daemon. Configure under `notifications.telegram` in `pester.yaml`:

```yaml
notifications:
  telegram:
    enabled: true
    bot_token_env: TELEGRAM_BOT_TOKEN
    chat_id: -1001234567890
```

## Bot Agent (Beta)

**Requires:** `pip install pester[bot]`

Status: beta surface for the 1.0.0 soft launch.

The bot agent turns pester into an interactive Telegram assistant. DM the bot to manage tasks, search the vault, check health, and get coaching prompts. Supports OpenAI and Anthropic as LLM providers.

### Setup

1. Install the bot extra:

   ```bash
   pip install pester[bot]
   ```

2. Use the same bot token from your Telegram sync setup (or create a new bot via [@BotFather](https://t.me/BotFather)).

3. Configure in `pester.yaml`:

   ```yaml
   bot:
     enabled: true
     provider: openai          # or "anthropic"
     model: o4-mini
     api_key_env: OPENAI_API_KEY
     allowed_users: [123456789]  # Your Telegram user ID
     default_mode: auto
   ```

4. Set environment variables:

   ```bash
   export OPENAI_API_KEY="sk-..."
   export TELEGRAM_BOT_TOKEN="..."
   export GROQ_API_KEY="..."  # For voice transcription
   ```

5. The bot runs as a separate service. With Docker Compose, it runs as the `telegram-sync` service (see `docker-compose.yml`).

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Onboarding message |
| `/help` | Show available commands |
| `/reset` | Clear conversation history |
| `/copilot` | Switch to copilot (directive) mode |
| `/coach` | Switch to provocateur (reflective) mode |

### Coaching System

The bot supports two coaching modes that shape how it responds:

- **Copilot** (directive) — task-focused, helps you execute. Default during work hours.
- **Provocateur** (reflective) — asks challenging questions, pushes you to think deeper. Default evenings and weekends.

In `auto` mode, the bot switches between modes based on time of day. Override anytime with `/copilot` or `/coach`.

### Scheduled Coaching Prompts

Configure the daemon to send coaching prompts on a schedule:

```yaml
scheduler:
  scheduled_prompts:
    morning_focus:
      time: "08:00"
      template: morning_focus
    evening_review:
      time: "21:00"
      template: evening_review
    weekly_analysis:
      time: "18:00"
      day_of_week: friday
      template: weekly_analysis
```

Templates live in `_system/prompts/` in your vault. The coaching system populates them with real data: open actions, overdue items, goal progress, and energy budget.

### Voice Transcription

Send voice, audio, or video note messages to the bot. They are transcribed via Groq Whisper (whisper-large-v3) and processed as text. Requires `GROQ_API_KEY`.

## Sync All Sources

Run all configured sync sources at once:

```bash
pester sync
```

Without a subcommand, this runs Drive sync. Telegram sync runs in listener mode and must be started separately with `pester sync telegram`.

## MCP Server

**Requires:** `pip install pester[mcp]`

The MCP (Model Context Protocol) server exposes your vault to AI tools like Claude Code. This lets Claude search your documents, check action items, and run health checks directly.

Status: local stdio MCP for Claude Code / Claude Desktop is supported today. Remote
streamable-http deployments are beta, and the web Custom Connector is not supported yet because
it requires OAuth 2.1.

### Setup

1. Install the MCP extra:

   ```bash
   pip install pester[mcp]
   ```

2. Add pester to your Claude Code MCP config (`.mcp.json` in your vault root):

   ```json
   {
     "mcpServers": {
       "pester": {
         "command": "pester",
         "args": ["mcp"],
         "cwd": "/path/to/your/vault"
       }
     }
   }
   ```

3. Restart Claude Code. The pester tools will be available automatically.

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `vault_search` | Semantic search across vault documents |
| `vault_get_document` | Retrieve a specific document by path |
| `vault_reindex` | Rebuild the search index (incremental or force) |
| `vault_actions` | List action items (filter by status, owner, overdue) |
| `vault_add_action` | Create a new action item |
| `vault_complete_action` | Mark an action as done |
| `vault_reschedule` | Reschedule an action to a new due date |
| `vault_health` | Run vault health checks |
| `vault_goals` | List all goals from the vault |
| `vault_goal_progress` | Get progress stats for a specific goal |
| `vault_audit_action` | Check if a new action aligns with active goals |
| `vault_briefing` | Get a compiled briefing for a person or project |
| `vault_dashboard` | Get full dashboard data for the vault |
| `vault_morning_focus` | Morning focus: today's actions, goals, priorities |
| `vault_weekly_summary` | Weekly analysis: completion rate, goal progress |
| `vault_overdue_summary` | Overdue actions grouped by owner with urgency |
| `vault_standup` | Standup data: yesterday completed + today planned |

### Example Usage in Claude Code

Once configured, you can ask Claude:

- "Search my vault for decisions about the database migration"
- "What actions are overdue?"
- "Add an action for @jane to review the Q1 budget by Friday"
- "Run a health check on my vault"

### claude.ai Web (Custom Connector) — coming soon

claude.ai's web "Add custom connector" UI currently expects OAuth 2.1 with PKCE / dynamic client registration. pester's MCP server today supports bearer-token auth (used by Claude Code, MCP Inspector, and any client that lets you set an `Authorization: Bearer` header), but does not yet ship an OAuth issuer. We're tracking the work in [TODOS.md](../TODOS.md); pull requests welcome.

In the meantime, use Claude Code (above) or any MCP client that supports static bearer auth.

## Optional Dependencies

| Extra | Packages | Purpose |
|-------|----------|---------|
| `[search]` | chromadb, onnxruntime, huggingface_hub, tokenizers, numpy | Semantic search engine |
| `[mcp]` | mcp | MCP server for AI tool integration; remote streamable-http is beta |
| `[drive]` | google-api-python-client, google-auth-oauthlib | Google Drive sync (beta) |
| `[telegram]` | python-telegram-bot | Telegram sync + notifications (beta) |
| `[daemon]` | watchdog, schedule | Background file watching (beta) |
| `[llm]` | openai, anthropic | LLM-powered extraction (OpenAI + Anthropic) |
| `[bot]` | python-telegram-bot, openai, anthropic, groq | Interactive Telegram bot + voice transcription (beta) |
| `[all]` | All of the above | Everything |
| `[dev]` | pytest, pytest-cov, ruff | Development and testing |

Install multiple extras at once:

```bash
pip install pester[search,drive]
pip install pester[all]
```

Missing extras produce a clear error message:

```
$ pester search "test"
Search requires: pip install pester[search]
```
