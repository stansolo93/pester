# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [Unreleased]

### Changed

- The `mcp` extra now requires `mcp>=1.30.0,<2`: mcp 2.x renamed `FastMCP`, and 1.30.0 carries security fixes.
- Development lockfile refreshed for published security advisories (aiohttp, anyio, certifi, cryptography, filelock, mcp, multidict, oauthlib, opentelemetry, pyjwt, urllib3).

## [1.0.2] - 2026-10-28

### Changed

- Test fixtures, documentation examples and bot help text now use fictional names, tasks and figures.

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
