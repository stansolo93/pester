# Architecture

High-level overview of pester's codebase for contributors.

## Module Map

```
src/pester/
├── cli/           Entry point. Click commands, one per file.
├── core/          Foundation. Vault discovery, config, state, audit.
├── tracking/      Action extraction, wikilinks, goals, health scoring.
├── dashboard/     HTML + terminal dashboard rendering.
├── rag/           Semantic search (ChromaDB + embeddings). [search] extra.
├── coaching/      Scheduled coaching prompts, energy tracking, modes.
├── llm/           LLM provider abstraction (OpenAI + Anthropic). [llm] extra.
├── daemon/        Background file watcher, scheduler, escalation. [daemon] extra.
├── bot/           Interactive Telegram bot with function calling. [bot] extra.
├── sync/          Google Drive + Telegram message sync.
├── mcp/           MCP server for Claude Code integration. [mcp] extra.
└── templates/     Vault scaffolding copied by `pester init`.
```

## Data Flow

```
                    ┌─────────────┐
                    │  Vault      │
                    │  (markdown  │
                    │   files)    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Tracking │ │   RAG    │ │  Sync    │
        │ extract  │ │  index   │ │  ingest  │
        │ actions, │ │  chunks, │ │  Drive,  │
        │ wikilinks│ │  embed   │ │  Telegram │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             ▼            ▼            │
        ┌──────────┐ ┌──────────┐     │
        │ Dashboard│ │  Search  │     │
        │ HTML +   │ │  query   │     │
        │ terminal │ │  results │     │
        └──────────┘ └──────────┘     │
                                      │
              ┌───────────────────────┘
              ▼
        ┌──────────┐     ┌──────────┐
        │  Daemon  │────▶│  Notify  │
        │  watch,  │     │  file,   │
        │  schedule│     │  Telegram│
        └──────────┘     └──────────┘
```

## Core Module Dependencies

```
vault.py          (no core deps)
config.py         (no core deps)
state.py          (no core deps)
adopt.py          (imports config, vault)
audit.py          (imports state)
metrics.py        (imports config, vault)
preamble.py       (imports colors, metrics, state, vault)

colors.py         (standalone)
extras.py         (standalone)
```

## Optional Extras

pester uses Python optional dependencies to keep the base install lightweight:

| Extra | Packages | Enables |
|-------|----------|---------|
| `[search]` | chromadb, onnxruntime | Semantic search, indexing |
| `[daemon]` | watchdog, schedule | Background file watching |
| `[bot]` | python-telegram-bot | Interactive Telegram bot |
| `[llm]` | openai, anthropic | LLM-powered extraction |
| `[mcp]` | mcp | MCP server for Claude Code |
| `[all]` | All of the above | Everything |

Extras are guarded by `core/extras.py`. Commands that need an extra call
`require_<extra>()` at the top, which exits with a `pip install` hint if missing.

## CLI Structure

Each command lives in `cli/cmd_<name>.py` and registers with the Click group
in `cli/main.py`. The `--vault` flag, `$PESTER_VAULT` env var, or CWD walk
determines the vault path (3-tier lookup in `core/vault.py`).

## Daemon Architecture

The daemon (`pester daemon run`) starts these components:

- **FileWatcher** — watchdog-based, debounced file change detection
- **SchedulerComponent** — cron-like scheduled tasks (briefings, coaching)
- **EscalationChecker** — overdue action escalation with level suppression
- **EventBus** — async dispatch to handlers (extract, index, audit, notify)
- **NotificationRouter** — delivers events to file and/or Telegram

Each component implements the `DaemonComponent` Protocol (start/stop lifecycle).

## Self-Hosting

See `docs/self-hosting.md` for Docker deployment. The `docker-compose.yml`
runs five services: caddy (reverse proxy), mcp, daemon, telegram-sync, and ollama.
