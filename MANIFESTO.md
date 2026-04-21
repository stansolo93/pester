# The pester Manifesto

## The Problem

Every founder drowns the same way.

Meeting notes live in Notion. Action items live in Linear. Decisions live in Google Docs. Transcripts live in Otter. Investor updates live in email drafts. Weekly reflections live nowhere.

Six tools. Six logins. Six search bars. The one question that matters — who owes what by when — requires checking all of them.

So you don't check. You trust memory. Memory fails. You find out something was three weeks late at the board meeting.

This is not a tooling problem. A seventh tool won't fix it. You need one layer that holds the whole picture.

## The Job

The founder's real job is accountability.

Not strategy decks. Not culture docs. Not OKR frameworks. Those are inputs. The output is simple: the right things getting done by the right people at the right time.

Accountability is invisible work. There's no app for "did we actually follow through on the thing we agreed to three weeks ago." There's no dashboard for "which decisions are overdue for review." There's no alert for "the journal hasn't been updated in nine days, something is wrong."

Most productivity tools are built around capture. Write it down. Tag it. Organize it. The assumption is that the hard part is getting information into the system.

Wrong. The hard part is getting information back out at the right time, in the right context, pointed at the right person.

pester reads the markdown you already write and extracts the accountability layer: who committed to what, by when, and whether it happened. Run it on demand, or opt into the background daemon and let it watch the vault for you. When deadlines slip, it escalates. When the vault goes quiet, it says so.

Write the meeting notes. pester does the rest.

## Why Markdown

Markdown is the cockroach of file formats. It survives everything.

Your Notion export is markdown. Your Obsidian vault is markdown. Your Confluence export is markdown. When the next productivity tool arrives (and it will), you'll export markdown again.

Every SaaS product eventually shuts down, pivots, raises prices, or gets acquired by someone who doesn't care about your workflow. Your `.md` files will still be there, readable by any text editor on any operating system, fifty years from now.

Markdown is also the native language of LLMs. They parse frontmatter, follow wikilinks, understand heading hierarchies. Build on markdown and every AI tool — today's and tomorrow's — can read your data without an integration layer.

We don't lock you in. We read your files and tell you what they say.

## Why CLI

CLIs compose. That's it. That's the reason.

`pester actions --overdue --json | jq '.[] | .owner'` gives you every person who's behind, in one line. Try that in a web dashboard.

CLIs are fast. No loading spinners. No JavaScript bundles. No React hydration before you see five items.

CLIs work with AI natively. When Claude Code connects to pester over MCP, it calls structured tools, not screenshot parsers. `vault_overdue_summary()` returns JSON. The AI reads, reasons, acts. No scraping.

And CLIs work over SSH. Your laptop, your server, your CI pipeline — same tool everywhere.

## The Vision

A founder opens their laptop. Before email, before Slack, they run `pester status`. One line tells them where they stand: 3 actions open, 1 overdue, health 8/10.

They run `pester standup`. Yesterday's completions and today's priorities, generated from the vault. No manual standup notes ever again.

Their AI agent connects over MCP. "What's overdue?" returns structured data, not a grep. "Add an action for Alex to review the pitch deck by Friday" creates a tracked, extractable, escalatable item.

The vault is the single source of truth. The markdown files are the database. The CLI is the interface. The AI agent is the copilot.

No subscription. No vendor lock-in. No data leaving your machine unless you say so.

Just a folder of markdown files and a tool that makes sure things actually get done.

That's pester.

---

*Read more at [github.com/stansolo93/pester](https://github.com/stansolo93/pester)*
