# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [1.0.0] - 2026-04-17

Initial public release.

### Added

- Action tracking with hybrid AI + regex extraction, deduplication, owner/due-date parsing.
- Semantic search using ONNX E5 multilingual embeddings and ChromaDB.
- Vault health checks: stale journals, broken wikilinks, missing frontmatter, overdue actions.
- HTML dashboard, ANSI terminal output, and live web server with auto-refresh.
- Background daemon: file watcher with auto-extraction of high-confidence candidates and notifications.
- MCP server (stdio and streamable HTTP beta) with 17 tools for Claude Code, Claude Desktop, and other bearer-auth MCP clients. The web Custom Connector remains pending OAuth support.
- Multi-source sync beta: Google Drive (incremental) and Telegram listener.
- Pluggable LLM providers: OpenAI, Anthropic, Groq.
- Self-host deploy scripts and Docker Compose beta stack (caddy, mcp, daemon, telegram-sync, ollama).
- Vault adoption (`pester adopt`) for importing existing markdown trees.
- Wikilink parsing, completion, and broken-link detection.
