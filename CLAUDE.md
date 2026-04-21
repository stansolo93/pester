# pester — Contributor Guide

## Project Structure

```
src/pester/
├── cli/          # Click CLI commands (cmd_*.py)
│   └── main.py   # Click group + preamble hook + logging
├── core/         # Foundation modules (always installed)
│   ├── vault.py   # Vault discovery, file walking, atomic_write
│   ├── config.py  # Load pester.yaml, defaults
│   ├── state.py   # ~/.pester/ management, slug generation
│   ├── adopt.py   # Vault adoption (import external markdown trees)
│   ├── audit.py   # Append-only JSONL audit trail
│   ├── metrics.py # Shared metrics (overdue count, freshness)
│   ├── preamble.py # CEO status line (cached 60s)
│   ├── extras.py  # Optional-extra availability checks
│   └── colors.py  # ANSI terminal color constants
├── rag/          # Semantic search [search] extra
├── tracking/     # Actions, wikilinks, extractor, LLM extractor, goals, profile
├── dashboard/    # HTML + terminal dashboard
├── daemon/       # Background daemon [daemon] extra
│   ├── bus.py         # EventBus (async ThreadPoolExecutor dispatch)
│   ├── manager.py     # DaemonManager (lifecycle orchestrator)
│   ├── watcher.py     # FileWatcher (watchdog + debounce)
│   ├── scheduler.py   # SchedulerComponent (schedule library)
│   ├── escalation.py  # EscalationChecker (level-change suppression)
│   ├── handlers.py    # Event handlers (extract, index, audit)
│   ├── notifications.py # NotificationRouter (file + Telegram)
│   ├── telegram_bot.py  # Telegram Bot API delivery [bot]
│   ├── pid.py         # PID file management
│   ├── protocol.py    # DaemonComponent Protocol
│   └── events.py      # Event vocabulary (StrEnum + TypedDict)
├── bot/          # Interactive Telegram bot [bot] extra
│   ├── agent.py       # GPT/Claude function-calling agent
│   └── conversation.py # Persistent per-user JSONL history
├── coaching/     # Coaching system
│   ├── modes.py       # Copilot + provocateur mode switching
│   ├── runner.py      # Execute scheduled coaching prompts
│   ├── energy.py      # Energy budget + capacity enforcement
│   ├── prompts.py     # Prompt template loading
│   ├── data_fns.py    # Data functions for template variables
│   ├── calendar.py    # Day/time-aware scheduling helpers
│   └── audit.py       # Goal alignment auditing
├── llm/          # LLM provider abstraction [llm] extra
│   ├── _shared.py          # Client creation, model resolution
│   ├── chat_openai.py      # OpenAI chat adapter
│   ├── chat_anthropic.py   # Anthropic chat adapter
│   ├── extract_openai.py   # OpenAI structured extraction
│   ├── extract_anthropic.py # Anthropic extraction
│   └── tools.py            # Provider-agnostic tool definitions
├── sync/         # Drive + Telegram sync
├── mcp/          # MCP server for Claude Code
└── templates/    # Vault scaffolding for pester init
```

## Core Module Dependency Order

```
vault.py          (no core deps)
config.py         (no core deps)
state.py          (no core deps)
adopt.py          (imports config, vault)
audit.py          (imports state)
metrics.py        (imports config, vault)
preamble.py       (imports colors, metrics, state, vault)

colors.py         (standalone, no deps)
extras.py         (standalone, no deps)
```

**Rule:** Never create circular imports between core modules.

## Running Tests

```bash
make test          # Fast tests only (skip @slow, @search)
make test-all      # All tests including slow + search
make lint          # Ruff check + format check
```

Test marker `@pytest.mark.llm` marks tests that need `openai` or `anthropic` SDKs.
Excluded from default `make test`. Run with: `python -m pytest -m llm`

## Import Conventions

Optional extras use the centralized factory in `core/extras.py`:
```python
from pester.core.extras import make_optional_check

HAS_SEARCH, require_search = make_optional_check("chromadb", "search")
```

Commands call `require_search()` at the top — it raises `SystemExit` with a
`pip install` hint if the extra is missing.

## Adding CLI Commands

Each task (T2-T8) adds its own `cmd_*.py` file and registers it in `cli/main.py`:
```python
from pester.cli.cmd_search import search
cli.add_command(search)
```

## Vault Discovery

3-tier lookup: `--vault` flag → `$PESTER_VAULT` env var → walk up from CWD for `pester.yaml`.
