# Vault Structure

This document describes the directory layout created by `pester init`, the `pester.yaml` configuration schema, and the state directory.

## Directory Layout

Running `pester init my-vault` creates this structure:

```
my-vault/
├── .gitignore
├── CLAUDE.md                          # Decision frameworks for AI-assisted work
├── .mcp.json                          # MCP server configuration (for Claude Code)
├── pester.yaml                          # Vault configuration
├── actions/                           # Action items (managed by pester)
├── decisions/                         # Decision records
├── journal/                           # Daily and weekly journals
├── meetings/                          # Meeting notes
├── people/                            # Person profiles
├── projects/                          # Project documents
├── goals/                            # Goal tracking (OKRs, milestones)
├── reference/                         # Reference materials
│   ├── assets/                        # Images, attachments
│   ├── drive/                         # Google Drive synced files
│   ├── telegram/                      # Telegram synced messages
│   ├── transcripts/                   # Meeting transcripts
│   └── inbox/                         # Unsorted incoming files
└── _system/
    ├── profile.md                     # Personal profile for coaching
    ├── prompts/                       # Coaching prompt templates
    │   ├── copilot.md
    │   ├── daily_context.md
    │   ├── daily_reflection.md
    │   ├── evening_review.md
    │   ├── monthly_review.md
    │   ├── morning_focus.md
    │   ├── provocateur.md
    │   ├── quarterly_strategy.md
    │   ├── weekend_evening.md
    │   ├── weekend_morning.md
    │   ├── weekend_planning.md
    │   └── weekly_analysis.md
    └── templates/                     # Document templates
        ├── action.md
        ├── decision.md
        ├── journal-daily.md
        ├── journal-weekly.md
        ├── meeting.md
        ├── person.md
        └── project.md
```

## Folder Purposes

### `actions/`
Action items tracked by pester. Files are created by `pester actions add` and managed by the tracking system. Each action file contains: owner, description, due date, status, and related links.

### `decisions/`
Decision records documenting key choices. Use the `_system/templates/decision.md` template. Include context, options considered, outcome, and review date.

### `journal/`
Daily and weekly journal entries. pester's health check will flag stale journals (configurable via `health.journal_stale_days` in pester.yaml).

### `meetings/`
Meeting notes. Use `[[wikilinks]]` to link attendees to their profiles in `people/`. Run `pester actions extract` to pull action items from meetings automatically.

### `people/`
Profiles for people you interact with. Link to meetings, actions, and projects via wikilinks. Use `pester briefing <slug>` to compile all related information about a person.

### `projects/`
Project documents and plans. Link to related decisions, actions, and people. Use `pester briefing <slug>` to generate a project overview.

### `reference/`
Reference materials organized by source:
- `assets/` — Images, PDFs, and other attachments
- `drive/` — Files synced from Google Drive
- `telegram/` — Messages synced from Telegram
- `transcripts/` — Meeting transcripts (audio → text)
- `inbox/` — Unsorted incoming files for triage

### `goals/`
Goal files for OKR and milestone tracking. Each goal is a markdown file with YAML frontmatter (`title`, `target_date`, tags). pester computes progress from tagged actions and uses goals in coaching prompts.

### `_system/profile.md`
Personal profile used by the coaching system for prompt personalization. Frontmatter fields include name, role, company, values, priorities, and timezone.

### `_system/prompts/`
Coaching prompt templates for scheduled daily, weekly, monthly, and quarterly cycles. Templates use variables populated by coaching data functions (action counts, overdue items, goal progress, energy budget).

### `_system/templates/`
Document templates used by pester and available for manual use. Each template has frontmatter fields and a suggested structure.

## pester.yaml Configuration

The `pester.yaml` file at the vault root controls all pester behavior:

```yaml
# Vault identity
vault:
  name: "Acme"
  language: en                          # Used for extraction keyword defaults
  owner: "Your Name"

# Action extraction settings
extraction:
  keywords:                             # Keywords that trigger action extraction
    en:
      - "TODO"
      - "action item"
      - "deadline"
      - "assigned to"
      - "due by"
      - "follow up"
    ru:
      - "нужно"
      - "сделать"
      - "дедлайн"
      - "задача"
      - "до"
  patterns:                             # Action line patterns
    - "- [ ] @{owner} — {desc} — by {date}"
    - "- [ ] @{owner}: {desc} (due: {date})"

# Priority tracking
priorities:
  - name: "Matching Engine v2"
    deadline: 2026-03-20

# Alert thresholds
alerts:
  burn_rate_warning: 500000             # Monthly burn rate alert ($)
  runway_warning: 90                    # Runway alert (days)

# Semantic search settings (requires pester[search])
search:
  model: intfloat/multilingual-e5-base  # Embedding model
  transcript_score_factor: 0.85         # Score weight for transcript results

# Health check settings
health:
  journal_stale_days: 3                 # Days without journal = stale
  decision_review_days: 60              # Days until decision review reminder

# Sync settings
sync:
  drive:
    enabled: true
    folders:
      - id: "1a2b3c..."                # Google Drive folder ID
        vault_dir: reference/drive      # Local destination in vault
  telegram:
    enabled: true
    chats:
      - name: "Team Chat"
        id: -1001234567890
        vault_dir: reference/telegram

# LLM extraction (requires: pip install pester[llm])
llm:
  provider: openai                      # or "anthropic"
  model: gpt-5.4-mini                   # Anthropic default: claude-sonnet-4-6-20250217
  api_key_env: OPENAI_API_KEY           # or ANTHROPIC_API_KEY
  temperature: 0.3
  max_tokens: 2048
  timeout_seconds: 30

# Bot agent (requires: pip install pester[bot])
bot:
  enabled: false
  provider: openai                      # or "anthropic"
  name: pester
  persona: ""                           # Custom persona text for system prompt
  model: o4-mini                        # Anthropic default: claude-sonnet-4-6-20250217
  api_key_env: OPENAI_API_KEY           # or ANTHROPIC_API_KEY
  groq_api_key_env: GROQ_API_KEY        # For voice transcription
  temperature: 0.7
  max_tokens: 4096
  max_history: 20                       # Messages kept in conversation
  timeout_seconds: 30
  allowed_users: []                     # Telegram user IDs (empty = deny all)
  default_mode: auto                    # auto, copilot, or provocateur

# Notifications
notifications:
  telegram:
    enabled: false
    bot_token_env: TELEGRAM_BOT_TOKEN
    chat_id: null                       # Destination chat ID

# Scheduled coaching prompts (under scheduler section)
scheduler:
  scheduled_prompts:
    morning_focus:
      time: "08:00"
      template: morning_focus
    evening_review:
      time: "21:00"
      template: evening_review
```

### Configuration Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `vault.name` | yes | — | Display name for your vault |
| `vault.language` | no | `en` | Language for extraction keywords |
| `vault.owner` | no | — | Owner name (used in preamble) |
| `extraction.keywords` | no | en/ru defaults | Keywords that trigger action extraction |
| `extraction.patterns` | no | built-in | Patterns for parsing action lines |
| `priorities` | no | `[]` | Priority items with deadlines |
| `alerts.burn_rate_warning` | no | — | Monthly burn rate threshold |
| `alerts.runway_warning` | no | — | Runway days threshold |
| `search.model` | no | `intfloat/multilingual-e5-base` | Embedding model name |
| `search.transcript_score_factor` | no | `0.85` | Score factor for transcripts |
| `health.journal_stale_days` | no | `3` | Days before journal is stale |
| `health.decision_review_days` | no | `60` | Days until decision review |
| `llm.provider` | no | `openai` | LLM provider (`openai` or `anthropic`) |
| `llm.model` | no | `gpt-5.4-mini` | Model for action extraction |
| `llm.api_key_env` | no | `OPENAI_API_KEY` | Env var holding the API key |
| `llm.temperature` | no | `0.3` | Sampling temperature (0-2) |
| `llm.max_tokens` | no | `2048` | Max completion tokens |
| `llm.timeout_seconds` | no | `30` | API call timeout |
| `bot.enabled` | no | `false` | Enable interactive Telegram bot |
| `bot.provider` | no | `openai` | Bot LLM provider (`openai` or `anthropic`) |
| `bot.name` | no | `pester` | Bot display name |
| `bot.persona` | no | `""` | Custom persona text for system prompt |
| `bot.model` | no | `o4-mini` | Chat model |
| `bot.api_key_env` | no | `OPENAI_API_KEY` | Bot LLM API key env var |
| `bot.groq_api_key_env` | no | `GROQ_API_KEY` | Groq API key for voice transcription |
| `bot.temperature` | no | `0.7` | Chat temperature (0-2) |
| `bot.max_tokens` | no | `4096` | Max completion tokens for bot |
| `bot.max_history` | no | `20` | Messages kept in conversation |
| `bot.timeout_seconds` | no | `30` | API timeout |
| `bot.allowed_users` | no | `[]` | Telegram user IDs allowed (empty = deny all) |
| `bot.default_mode` | no | `auto` | Coaching mode (`auto`, `copilot`, `provocateur`) |
| `notifications.telegram.enabled` | no | `false` | Enable Telegram push notifications |
| `notifications.telegram.bot_token_env` | no | `TELEGRAM_BOT_TOKEN` | Bot token env var |
| `notifications.telegram.chat_id` | no | `null` | Destination chat ID |
| `scheduler.scheduled_prompts` | no | `{}` | Coaching prompt schedule (time + template) |

## State Directory

pester stores runtime state in `~/.pester/`, organized by vault:

```
~/.pester/
├── models/                             # Downloaded embedding models
│   └── intfloat--multilingual-e5-base/ # Model files
├── projects/
│   └── <vault-slug>/                   # Per-vault state
│       ├── cache/chroma/               # ChromaDB vector store
│       ├── manifest.json               # Index manifest (file hashes)
│       ├── audit.jsonl                 # Append-only audit trail
│       ├── state.json                  # Vault state
│       └── preamble-cache.json         # Cached preamble (60s TTL)
└── config.yaml                         # Global pester config (future)
```

The state directory is:
- **Not in your vault** — it lives in your home directory, separate from vault content
- **Not committed to git** — vault repos stay clean
- **Per-vault** — multiple vaults each get their own state subdirectory
- **Recoverable** — deleting `~/.pester/` and re-running `pester index` rebuilds everything
