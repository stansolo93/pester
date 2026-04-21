"""Neutral tool definitions and per-provider format converters.

Neutral format (provider-agnostic):
    {"name": str, "description": str, "parameters": <JSON Schema dict>}

Each converter wraps/rewraps this into the provider's native format.
"""

from __future__ import annotations

from typing import Any

# ── Neutral vault tool definitions ───────────────────────────────

VAULT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_actions",
        "description": "List action items from the vault. Returns open actions by default.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "done"],
                    "description": "Filter by status. Default: open.",
                },
                "owner": {
                    "type": "string",
                    "description": "Filter by owner name.",
                },
                "overdue": {
                    "type": "boolean",
                    "description": "If true, only return overdue actions.",
                },
                "due": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) to filter actions due exactly on that day.",
                },
            },
        },
    },
    {
        "name": "add_action",
        "description": "Create a new action item.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Action description."},
                "owner": {"type": "string", "description": "Person responsible."},
                "due": {"type": "string", "description": "Due date in YYYY-MM-DD format."},
                "priority": {
                    "type": "string",
                    "enum": ["Must", "Should", "Could"],
                    "description": "Priority level. Default: Should.",
                },
            },
            "required": ["description", "owner", "due"],
        },
    },
    {
        "name": "complete_action",
        "description": "Mark an action item as done.",
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The action slug to complete."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "search_vault",
        "description": "Search vault documents by semantic similarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of results. Default: 5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document",
        "description": "Read the full content of a vault document.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from vault root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_health",
        "description": "Get vault health report: overdue count, stale items, broken links.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "reschedule_action",
        "description": "Reschedule an action to a new due date. Increments postponed count.",
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The action slug to reschedule."},
                "new_due": {
                    "type": "string",
                    "description": "New due date in YYYY-MM-DD format.",
                },
            },
            "required": ["slug", "new_due"],
        },
    },
]


# ── Per-provider converters ──────────────────────────────────────


def to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert neutral tool defs to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


def to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert neutral tool defs to Anthropic tool format."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in tools
    ]
