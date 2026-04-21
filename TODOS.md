# TODOS

This is pester's active roadmap. Each item is a concrete scoped improvement.
PRs welcome on any of them — open an issue first if the approach needs discussion.
Items land here when they're worth doing and leave when they ship.

## Roadmap

### OAuth 2.1 for claude.ai web Custom Connector

claude.ai web's "Add custom connector" UI expects OAuth 2.1 with PKCE and
dynamic client registration (RFC 7591). pester's MCP server today only supports
static bearer-token auth (which works for Claude Code, MCP Inspector, and any
client that lets you set an `Authorization: Bearer` header).

Two approaches under consideration:
1. **OAuth proxy sidecar** (e.g., oauth2-proxy in front of Caddy) — fastest path,
   ~half a day. Trades simplicity of one container for separation of concerns.
2. **Native OAuth in pester MCP** — wait for FastMCP to ship OAuth support
   (tracked upstream), or implement a minimal `/oauth/authorize` + `/oauth/token`
   in our own middleware. ~2-3 days.

Until this lands, claude.ai web Custom Connector returns 401 because OAuth
discovery (`/.well-known/oauth-authorization-server`) is also gated by the bearer
check. Caddy could expose those discovery paths publicly returning 404 (cleaner
"OAuth not supported" signal) as a small interim improvement.

### Real integration tests for Drive + Telegram sync

Add integration tests against real (sandboxed) Google Drive and Telegram APIs.
Current tests use mocks, which cover sync logic but don't catch API contract drift.
Set up as weekly CI job with test credentials stored in GitHub Actions secrets.

**Depends on:** CI infrastructure and API credentials.

### `pester upgrade` self-update command

Add `pester upgrade` as a convenience wrapper around `pip install --upgrade pester`.
Could check PyPI for new versions and show changelog.

### Multi-vault dashboard

Aggregate dashboard across multiple vaults. Show combined action counts,
health status, and overdue items from all vaults in a single view.

### Search quality validation (`pester validate`)

Implement `pester validate` to run known queries against the index and verify
expected documents appear in results. Catches regressions in chunking strategy,
embedding model changes, or config drift. Needs a `search.validation` section
in pester.yaml mapping queries to expected file paths.

Now urgent: Ollama embedder (Qwen3-0.6b) is an alternative to E5/ONNX. Need a
validation set of queries with expected results to verify search quality before
and after embedding switches.

**Depends on:** Search module complete. Ollama embedder makes this urgent.

### Embedding cache for incremental re-indexing

Cache embeddings keyed by chunk content hash. When a file changes, only
re-embed chunks whose content actually differs (not all chunks in the file).
Saves 50-80% of ONNX inference time on typical single-section edits.

### Global user config (~/.pester/config.yaml)

Implement `~/.pester/config.yaml` loading for global user preferences (default
language, editor, theme). `config.py` would need a merge step: load global config,
then overlay vault-specific `pester.yaml` on top.

### Plugin system for custom sync sources

Add a plugin interface for registering custom sync sources beyond Drive and
Telegram (e.g., Notion, Slack, email). Would use entry points or a simple
hook system. Better to add sources directly and extract a plugin interface
once patterns emerge.

### Dashboard server mtime-based caching

Upgrade from full-scan TTL cache to mtime-based incremental scan. Check vault
file mtimes before re-scanning, only regenerate HTML when files have actually
changed. Reduces CPU on idle vaults.

### Refactor dashboard/data.py action queries to use tracking modules

Consider delegating action queries to `tracking/actions.py` for richer
lifecycle management (by-owner grouping, completion timestamps). Requires
architectural decision on scan strategy.

### `--name/--owner` CLI flags for pester init

Add `--name` and `--owner` flags to `pester init` that pre-populate pester.yaml
with user-provided values instead of placeholder defaults.

### Action archival/cleanup command

`pester actions archive` to move completed actions older than N days to
`actions/_archive/`. Keeps git log signal high.

### Wikilink graph visualization

`pester wikilinks graph` to output DOT/Mermaid graph of all vault connections.
The data infrastructure exists; this is a presentation feature.

### Template upgrade command for existing vaults

Add `pester templates update` to refresh `_system/templates/` from the installed
pester package without touching user content or config files.

### Slack/email delivery channels

Add Slack and email as event bus delivery channels alongside Telegram and stdout.
Adding a new channel is a new handler (~50-100 LOC each).

### Russian language quality evaluation set

Create a manual eval set of 10-15 representative Russian user messages with
expected tool calls and response quality criteria. Run against both OpenAI and
Anthropic to compare quality. Catches regressions before they reach production.

### Shared ChromaDB via HTTP server

Run ChromaDB as a shared HTTP service (`chroma run --path`) instead of loading
it in-process in both the MCP server and daemon. Saves ~200MB RAM, eliminates
potential index corruption from concurrent writes.
