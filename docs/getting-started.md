# Getting Started

This guide walks you through setting up pester and using its core features.

## 1. Install

**Minimal install** (actions, health, dashboard — no search or sync):

```bash
pip install pester
```

**Full install** (everything including search, sync, and MCP):

```bash
pip install pester[all]
```

**From source** (for development):

```bash
git clone https://github.com/stansolo93/pester.git
cd pester
pip install -e ".[all,dev]"
```

## 2. Initialize a Vault

```bash
pester init my-vault
cd my-vault
```

This creates a complete vault structure with:

- Folders for `actions/`, `decisions/`, `journal/`, `meetings/`, `people/`, `projects/`, `reference/`
- An `pester.yaml` configuration file
- A `CLAUDE.md` with decision frameworks for AI-assisted work
- Templates in `_system/templates/` for common document types
- A `goals/` folder for OKR and milestone tracking
- A `_system/profile.md` for coaching personalization
- Coaching prompt templates in `_system/prompts/`
- A `.gitignore` configured for vault use

See [vault-structure.md](vault-structure.md) for details on each folder and file.

## 3. Add Documents

Drop markdown files into the appropriate folders:

| Folder | What goes here | Examples |
|--------|---------------|----------|
| `journal/` | Daily and weekly journal entries | `2026-03-18.md`, `week-12.md` |
| `meetings/` | Meeting notes | `board-review-2026-03-15.md` |
| `people/` | Person profiles and notes | `jane-doe.md` |
| `projects/` | Project documents | `matching-engine-v2.md` |
| `decisions/` | Decision records | `switch-to-postgres.md` |
| `reference/` | Reference materials, transcripts, uploads | `competitor-analysis.md` |
| `goals/` | Goal definitions (OKRs, milestones) | `ship-mvp.md`, `q2-revenue.md` |
| `actions/` | Auto-generated action files (managed by pester) | `ship-v1.md` |

Use `[[wikilinks]]` to link between documents. pester validates these links via `pester wikilinks validate`.

> **Tip on file names.** Use ASCII characters in filenames (`jane-doe.md`, `q1-launch.md`). Some shells, especially zsh on macOS, choke on Cyrillic, Chinese, or other non-Latin characters in filenames. Vault content can be in any language — only the filename needs to be ASCII.

## 4. Track Actions

Add an action item:

```bash
pester actions add --owner stan --due 2026-03-25 --desc "Ship v1"
```

List open actions:

```bash
pester actions
```

List as JSON (for scripting):

```bash
pester actions --json
```

Mark an action done:

```bash
pester actions done ship-v1
```

Extract actions from a meeting file. First, a meeting file with action lines:

```markdown
# Board Review 2026-04-17

## Action Items
- [ ] @stan — Ship launch posts — by 2026-05-01
- TODO @diana — Finalize first 50 targets by 2026-04-30
- action item: Review pricing — assigned to @stan, due 2026-05-05
```

Both the strict checkbox form (`- [ ] @owner — desc — by date`) and the natural
keyword form (`- TODO @owner — desc by date`) are recognized. Then:

```bash
pester actions extract meetings/board-review.md
```

In non-TTY contexts (scripts, CI), add `--yes` to auto-confirm or `--dry-run` to
preview without creating files.

Generate standup notes:

```bash
pester standup
```

The extractor looks for configurable keywords like "TODO", "action item", "deadline", "assigned to" in your documents. See [vault-structure.md](vault-structure.md) for customizing extraction keywords.

## 5. Check Vault Health

```bash
pester health
```

The health check reports:

- Overdue actions and who owns them
- Stale journal entries (no entry in X days)
- Decisions due for review
- Broken wikilinks
- Overall vault freshness score

## 6. Search Your Vault

Search requires the `[search]` extra:

```bash
pip install pester[search]
```

Download the embedding model (one-time, ~1.1GB):

```bash
pester model download
```

Build the search index:

```bash
pester index
```

Search by meaning:

```bash
pester search "what did we decide about the database migration"
```

The search engine uses multilingual E5 embeddings and ChromaDB for semantic similarity. It chunks your markdown documents intelligently (respecting headers, paragraphs, and code blocks) and returns the most relevant passages.

Check the model status or run a quick search smoke test:

```bash
pester model status
pester search "launch plan"
```

## 7. View the Dashboard

**HTML dashboard** (opens in your default browser):

```bash
pester dashboard
```

**Terminal dashboard** (ANSI output, great for tmux):

```bash
pester dashboard --terminal
```

**Live dashboard** (local web server with auto-refresh):

```bash
pester dashboard --serve
```

The dashboard shows:

- Open actions by owner with due dates
- Overdue items highlighted
- Vault health summary
- Priority tracking against deadlines
- Recent activity

## 8. Generate Briefings and Digests

Compile a briefing for a person or project (pulls all related documents):

```bash
pester briefing jane-doe
pester briefing matching-engine-v2
```

Generate a weekly digest:

```bash
pester digest
pester digest --week 2026-03-09
```

## 9. Set Up Sync

See [integrations.md](integrations.md) for setting up Google Drive and Telegram sync.

## 10. Enable Debug Logging

Add `-v` to any command for debug output:

```bash
pester -v search "test query"
pester -v health
```

## Next Steps

- [Vault Structure](vault-structure.md) — Full directory layout and pester.yaml reference
- [Integrations](integrations.md) — Google Drive, Telegram, Bot Agent, and MCP setup
