# Architecture

pester is a layered Python package. The CLI is a thin Click shell over a
foundation of always-installed core modules (vault, config, state, audit,
metrics), with optional extras (`[search]`, `[mcp]`, `[drive]`, `[telegram]`,
`[daemon]`, `[bot]`, `[llm]`) stacked on top. State lives in two places:
`~/.pester/` for per-user caches and indices, and your vault directory
(with `pester.yaml`) for content.

```
┌─────────────────────────────────────────────────────────────┐
│                     pester CLI (Click)                         │
│                                                             │
│  search  actions  health  dashboard  briefing  digest        │
│  wikilinks  sync  init  model  diff-scope  mcp  daemon      │
│  config  status  adopt                                       │
├───────────┬───────────┬────────────┬────────────────────────┤
│  RAG      │ Tracking  │ Dashboard  │ Sync                   │
│ [search]  │           │            │ [drive] [telegram]     │
│           │           │            │                        │
│ chunker   │ actions   │ html       │ drive                  │
│ embeddings│ extractor │ terminal   │ telegram               │
│ store     │ wikilinks │ server     │                        │
│ indexer   │ goals     │            │                        │
├───────────┴───────────┴────────────┴────────────────────────┤
│  Bot Agent [bot]        │ Coaching         │ LLM [llm]      │
│  agent  conversation    │ modes  runner    │ chat_openai     │
│                         │ energy  prompts  │ chat_anthropic  │
│                         │ data_fns         │ extract_openai  │
│                         │                  │ extract_anthro  │
│                         │                  │ tools  _shared  │
├─────────────────────────┴──────────────────┴────────────────┤
│  Core (always installed)                                     │
│  config  vault  metrics  preamble  audit  state  extras      │
├─────────────────────────────────────────────────────────────┤
│  Daemon [daemon]                                            │
│  EventBus  FileWatcher  Scheduler  Escalation               │
│  NotificationRouter  DaemonManager  PID                     │
├─────────────────────────────────────────────────────────────┤
│  MCP Server [mcp]                                           │
│  vault_search  vault_actions  vault_health  vault_add_action│
└─────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
  ~/.pester/                        Your Vault (pester.yaml)
  ├── models/                     ├── actions/
  ├── projects/<slug>/            ├── decisions/
  │   ├── cache/chroma/           ├── goals/
  │   ├── manifest.json           ├── journal/
  │   ├── audit.jsonl             ├── meetings/
  │   ├── state.json              ├── people/
  │   └── preamble-cache.json     ├── projects/
  └── config.yaml                 ├── reference/
                                  └── _system/prompts/
```
