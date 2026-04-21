"""MCP server exposing pester vault tools for Claude Code integration."""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import os
from pathlib import Path

from pester.core.vault import make_serializable

logger = logging.getLogger(__name__)


_CANONICAL_PRIORITIES = {
    "must": "Must",
    "should": "Should",
    "could": "Could",
    "won't": "Won't",
    "wont": "Won't",
}


class VaultTools:
    """Tool implementations for the MCP server.

    Separated from FastMCP wiring so they can be tested without the mcp package.
    Embedder and store are lazily cached to avoid re-loading ONNX model per call.
    """

    def __init__(self, vault_path: Path, config: dict, state_dir: Path) -> None:
        self.vault_path = vault_path
        self.config = config
        self.state_dir = state_dir
        self._embedder = None
        self._store = None

    @staticmethod
    def _normalize_priority(priority: str | None) -> str | None:
        """Return canonical 'Must'/'Should'/'Could'/'Won\\'t' or None if unknown."""
        if not priority:
            return None
        return _CANONICAL_PRIORITIES.get(priority.strip().lower())

    @staticmethod
    def _normalize_due(due: str | None) -> str | None:
        """Return ISO 8601 date string (YYYY-MM-DD) or None if unparseable."""
        from pester.tracking.actions import to_date

        if not due:
            return None
        parsed = to_date(due.strip())
        return parsed.isoformat() if parsed else None

    def _check_capacity(self, priority: str, due: str, exclude_slug: str | None = None) -> None:
        """Raise CapacityExceededError if adding `priority` on `due` would exceed
        per-day limit. `exclude_slug` is the slug being rescheduled (don't count itself).
        """
        from pester.tracking.actions import (
            PRIORITY_CONFIG,
            CapacityExceededError,
            list_actions,
            to_date,
        )

        cfg = PRIORITY_CONFIG.get(priority, {})
        if cfg.get("excluded"):
            return
        limit = cfg.get("max_per_day")
        if not limit:
            return
        target = to_date(due)
        if target is None:
            return  # date validation already happened upstream
        same_day = [
            a
            for a in list_actions(self.vault_path, status="open")
            if a.get("priority") == priority
            and to_date(a.get("due")) == target
            and a.get("slug") != exclude_slug
        ]
        if len(same_day) >= limit:
            existing = [
                {
                    "slug": a.get("slug"),
                    "description": a.get("desc") or a.get("slug", ""),
                    "due": str(a.get("due", "")),
                }
                for a in same_day
            ]
            raise CapacityExceededError(
                priority=priority,
                due=due,
                current_count=len(same_day),
                limit=limit,
                existing=existing,
            )

    def _get_embedder(self):
        """Lazily create and cache the embedder (ONNX load is ~2s)."""
        if self._embedder is None:
            from pester.rag.embeddings import create_embedder

            self._embedder = create_embedder(self.config)
        return self._embedder

    def _get_store(self):
        """Lazily create and cache the ChromaDB store."""
        if self._store is None:
            from pester.core.config import get_config_value
            from pester.rag.store import VaultStore

            score_factor = get_config_value(self.config, "search.transcript_score_factor", 0.85)
            self._store = VaultStore(
                self.state_dir / "cache" / "chroma", transcript_score_factor=score_factor
            )
        return self._store

    def vault_search(
        self,
        query: str,
        top_k: int = 5,
        doc_type: str | None = None,
        status: str | None = None,
    ) -> str:
        """Search the vault using semantic similarity.

        Args:
            query: Search query text.
            top_k: Number of results to return (default: 5).
            doc_type: Filter by document type (e.g., "decision", "meeting").
            status: Filter by document status.
        """
        from pester.rag import HAS_SEARCH

        if not HAS_SEARCH:
            return "Error: Search requires: pip install pester[search]"

        from pester.rag.embeddings import ModelNotFoundError

        try:
            embedder = self._get_embedder()
            store = self._get_store()
            query_emb = embedder.embed_query(query)
        except ConnectionError as e:
            return json.dumps({"error": str(e)})
        except ModelNotFoundError as e:
            return f"Error: {e}"

        # Build metadata filter
        where: dict | None = None
        conditions = []
        if doc_type:
            conditions.append({"type": doc_type})
        if status:
            conditions.append({"status": status})
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        results = store.search(query_emb, top_k=top_k, where=where)
        output = []
        for item in results:
            output.append(
                {
                    "score": round(item["score"], 3),
                    "path": item["metadata"]["file_path"],
                    "title": item["metadata"].get("title", ""),
                    "snippet": item["text"][:500],
                }
            )
        return json.dumps(output, indent=2, ensure_ascii=False)

    def vault_get_document(self, path: str) -> str:
        """Read the full content of a vault document.

        Args:
            path: Relative path from vault root (e.g., "decisions/pricing.md").
        """
        full_path = self.vault_path / path
        # Security: ensure path is within vault
        try:
            full_path.resolve().relative_to(self.vault_path.resolve())
        except ValueError:
            return f"Error: Path is outside the vault: {path}"
        if not full_path.is_file():
            return f"Error: File not found: {path}"
        try:
            return full_path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error reading file: {e}"

    def vault_reindex(self, force: bool = False) -> str:
        """Reindex the vault for semantic search.

        Args:
            force: If True, drop and rebuild the entire index. If False, incremental.
        """
        from pester.rag import HAS_SEARCH

        if not HAS_SEARCH:
            return "Error: Search requires: pip install pester[search]"

        from pester.core.config import get_config_value
        from pester.rag.embeddings import ModelNotFoundError, create_embedder
        from pester.rag.indexer import VaultIndexer
        from pester.rag.store import VaultStore

        language = get_config_value(self.config, "vault.language", "en")
        table_full_files = get_config_value(self.config, "search.table_full_files", [])
        score_factor = get_config_value(self.config, "search.transcript_score_factor", 0.85)
        chunk_size = get_config_value(self.config, "search.chunk_size")

        try:
            embedder = create_embedder(self.config)
            store = VaultStore(
                self.state_dir / "cache" / "chroma", transcript_score_factor=score_factor
            )
            indexer = VaultIndexer(
                self.vault_path,
                self.state_dir,
                embedder=embedder,
                store=store,
                language=language,
                table_full_files=table_full_files,
                transcript_score_factor=score_factor,
                chunk_size=chunk_size,
            )
            stats = indexer.index_vault(force=force)
        except ConnectionError as e:
            return json.dumps({"error": str(e)})
        except ModelNotFoundError as e:
            return f"Error: {e}"
        except (ImportError, OSError, ValueError) as e:
            return f"Indexing failed: {e}"

        return json.dumps(stats, indent=2)

    def vault_actions(
        self,
        status: str = "open",
        owner: str | None = None,
        overdue: bool = False,
        due: str | None = None,
    ) -> str:
        """List action items from the vault.

        Args:
            status: Filter by status ("open" or "done"). Default: "open".
            owner: Filter by owner slug (e.g., "stan").
            overdue: If True, only return overdue actions.
            due: ISO date (YYYY-MM-DD) to filter actions due exactly that day.
        """
        from pester.tracking.actions import list_actions

        items = list_actions(self.vault_path, status=status, owner=owner, overdue=overdue, due=due)
        output = []
        for a in items:
            body = a.get("body", "")
            desc = body.strip().lstrip("# ").split("\n")[0] if body else a.get("slug", "")
            output.append(
                {
                    "slug": a.get("slug"),
                    "owner": a.get("owner"),
                    "status": a.get("status"),
                    "due": str(a.get("due", "")),
                    "priority": a.get("priority", ""),
                    "description": desc,
                }
            )
        return json.dumps(output, indent=2, ensure_ascii=False)

    def vault_add_action(
        self,
        owner: str,
        description: str,
        due: str,
        priority: str = "Should",
        source: str = "manual",
    ) -> str:
        """Create a new action item in the vault.

        Args:
            owner: Person slug (e.g., "stan").
            description: Action description.
            due: Due date in ISO format (YYYY-MM-DD).
            priority: Priority level (Must, Should, Could, Won't). Default: "Should".
            source: Action source (manual, meeting, telegram). Default: "manual".
        """
        from pester.tracking.actions import CapacityExceededError, create_action

        canonical_priority = self._normalize_priority(priority)
        if canonical_priority is None:
            return json.dumps(
                {
                    "created": False,
                    "error": "unknown_priority",
                    "message": f"Unknown priority {priority!r}. Use: Must, Should, Could, Won't.",
                    "received": priority,
                }
            )
        canonical_due = self._normalize_due(due)
        if canonical_due is None:
            return json.dumps(
                {
                    "created": False,
                    "error": "invalid_due",
                    "message": f"Cannot parse due date {due!r}. Use ISO format (YYYY-MM-DD).",
                    "received": due,
                }
            )

        try:
            self._check_capacity(canonical_priority, canonical_due)
        except CapacityExceededError as e:
            return json.dumps(
                {
                    "created": False,
                    "error": f"{e.priority.lower()}_capacity_full",
                    "message": str(e),
                    "priority": e.priority,
                    "due": e.due,
                    "current_count": e.current_count,
                    "limit": e.limit,
                    "existing": e.existing,
                },
                ensure_ascii=False,
            )

        try:
            slug = create_action(
                self.vault_path,
                description=description,
                owner=owner,
                due=canonical_due,
                source=source,
                priority=canonical_priority,
            )
            return json.dumps({"created": True, "slug": slug, "path": f"actions/{slug}.md"})
        except (OSError, ValueError, TypeError) as e:
            return json.dumps({"created": False, "error": str(e)})

    def vault_complete_action(self, slug: str) -> str:
        """Mark an action as done.

        Args:
            slug: Action slug (filename without .md extension).
        """
        from pester.tracking.actions import complete_action

        try:
            complete_action(self.vault_path, slug)
            return json.dumps({"completed": True, "slug": slug})
        except (FileNotFoundError, ValueError) as e:
            return json.dumps({"completed": False, "error": str(e)})

    def vault_health(self) -> str:
        """Run a vault health check and return the report.

        Returns a JSON report with status (green/yellow/red), overdue count,
        journal gaps, stale decisions, and broken wikilinks.
        """
        from pester.tracking.health import get_health_report
        from pester.tracking.wikilinks import build_slug_index

        slug_index = build_slug_index(self.vault_path)
        report = get_health_report(self.vault_path, self.config, slug_index)
        return json.dumps(make_serializable(report), indent=2, ensure_ascii=False)

    def vault_goals(self) -> str:
        """List all goals from the vault.

        Returns a JSON list of goal dicts sorted by target_date.
        """
        from pester.tracking.goals import list_goals

        goals = list_goals(self.vault_path)
        output = []
        for g in goals:
            output.append(
                {
                    "slug": g.get("slug"),
                    "title": g.get("title"),
                    "status": g.get("status", ""),
                    "target_date": str(g.get("target_date", "")),
                    "tags": g.get("tags", []),
                }
            )
        return json.dumps(output, indent=2, ensure_ascii=False)

    def vault_goal_progress(self, goal_slug: str) -> str:
        """Get progress stats for a specific goal.

        Args:
            goal_slug: Goal slug (filename without .md extension).
        """
        from pester.tracking.goals import goal_progress

        result = goal_progress(self.vault_path, goal_slug)
        return json.dumps(result, indent=2, ensure_ascii=False)

    def vault_audit_action(self, description: str) -> str:
        """Check whether a new action aligns with active goals.

        Args:
            description: Action description to audit.
        """
        from pester.coaching.audit import audit_new_action
        from pester.tracking.goals import list_goals

        try:
            goals = list_goals(self.vault_path)
            result = audit_new_action(description, goals, self.config)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except (OSError, ValueError) as e:
            return json.dumps({"error": str(e)})

    def vault_reschedule(self, slug: str, new_due: str) -> str:
        """Reschedule an action to a new due date.

        Args:
            slug: Action slug (filename without .md extension).
            new_due: New due date in ISO format (YYYY-MM-DD).
        """
        from pester.tracking.actions import (
            CapacityExceededError,
            parse_action_file,
            reschedule_action,
        )

        canonical_due = self._normalize_due(new_due)
        if canonical_due is None:
            return json.dumps(
                {
                    "error": "invalid_due",
                    "message": f"Cannot parse due date {new_due!r}. Use ISO format (YYYY-MM-DD).",
                    "received": new_due,
                }
            )

        action_path = self.vault_path / "actions" / f"{slug}.md"
        if not action_path.exists():
            return json.dumps({"error": f"Action not found: {slug}"})
        parsed = parse_action_file(action_path)
        if parsed is None:
            return json.dumps({"error": f"Cannot parse action file: {slug}"})
        priority = self._normalize_priority(parsed.get("priority")) or parsed.get("priority", "")

        if priority:
            try:
                self._check_capacity(priority, canonical_due, exclude_slug=slug)
            except CapacityExceededError as e:
                return json.dumps(
                    {
                        "rescheduled": False,
                        "error": f"{e.priority.lower()}_capacity_full",
                        "message": str(e),
                        "priority": e.priority,
                        "due": e.due,
                        "current_count": e.current_count,
                        "limit": e.limit,
                        "existing": e.existing,
                    },
                    ensure_ascii=False,
                )

        try:
            count = reschedule_action(self.vault_path, slug, canonical_due)
            return json.dumps({"slug": slug, "new_due": canonical_due, "postponed_count": count})
        except (FileNotFoundError, ValueError) as e:
            return json.dumps({"error": str(e)})

    def vault_briefing(self, slug: str) -> str:
        """Get a compiled briefing for a person or project.

        Args:
            slug: Person or project slug.
        """
        from pester.dashboard.data import get_briefing_data

        result = get_briefing_data(self.vault_path, self.config, slug)
        if result is None:
            return json.dumps({"error": f"Not found: {slug}"})
        data = dataclasses.asdict(result)
        return json.dumps(make_serializable(data), indent=2, ensure_ascii=False)

    def vault_dashboard(self) -> str:
        """Get full dashboard data for the vault."""
        from pester.dashboard.data import get_dashboard_data

        result = get_dashboard_data(self.vault_path, self.config)
        data = dataclasses.asdict(result)
        return json.dumps(make_serializable(data), indent=2, ensure_ascii=False)

    def vault_morning_focus(self) -> str:
        """Get morning focus data: today's actions, goals, priorities."""
        from pester.coaching.data_fns import morning_focus_data

        result = morning_focus_data(self.vault_path, self.config)
        return json.dumps(result, indent=2, ensure_ascii=False)

    def vault_weekly_summary(self) -> str:
        """Get weekly analysis: completion rate, goal progress."""
        from pester.coaching.data_fns import weekly_analysis_data

        result = weekly_analysis_data(self.vault_path, self.config)
        return json.dumps(result, indent=2, ensure_ascii=False)

    def vault_overdue_summary(self) -> str:
        """Get overdue actions grouped by owner with urgency ranking.

        Returns a JSON object with overdue actions grouped by owner,
        sorted by days overdue (most urgent first).
        """
        from datetime import date

        from pester.tracking.actions import list_actions

        items = list_actions(self.vault_path, status="open", overdue=True)
        today = date.today()

        by_owner: dict[str, list] = {}
        for a in items:
            owner = a.get("owner", "unassigned")
            body = a.get("body", "")
            desc = body.strip().lstrip("# ").split("\n")[0] if body else a.get("slug", "")
            due_str = str(a.get("due", ""))
            try:
                days_overdue = (today - date.fromisoformat(due_str)).days
            except (ValueError, TypeError):
                days_overdue = 0
            by_owner.setdefault(owner, []).append(
                {
                    "slug": a.get("slug"),
                    "description": desc,
                    "due": due_str,
                    "days_overdue": days_overdue,
                    "priority": a.get("priority", ""),
                }
            )

        # Sort each owner's actions by days overdue
        for actions in by_owner.values():
            actions.sort(key=lambda x: x["days_overdue"], reverse=True)

        return json.dumps(
            {"total_overdue": len(items), "by_owner": by_owner}, indent=2, ensure_ascii=False
        )

    def vault_standup(self) -> str:
        """Get standup data: yesterday's completed + today's planned actions.

        Returns a JSON object with done_yesterday, due_today, and overdue lists.
        """
        from datetime import date, timedelta

        from pester.tracking.actions import list_actions

        today = date.today()
        yesterday = today - timedelta(days=1)

        done_actions = list_actions(self.vault_path, status="done")
        done_yesterday = [
            a for a in done_actions if str(a.get("completed", "")) == yesterday.isoformat()
        ]

        open_actions = list_actions(self.vault_path, status="open")
        due_today = [a for a in open_actions if str(a.get("due", "")) == today.isoformat()]
        overdue = list_actions(self.vault_path, status="open", overdue=True)

        def _fmt(a: dict) -> dict:
            body = a.get("body", "")
            desc = body.strip().lstrip("# ").split("\n")[0] if body else a.get("slug", "")
            return {"slug": a.get("slug"), "description": desc, "owner": a.get("owner")}

        return json.dumps(
            {
                "date": today.isoformat(),
                "done_yesterday": [_fmt(a) for a in done_yesterday],
                "due_today": [{**_fmt(a), "priority": a.get("priority", "")} for a in due_today],
                "overdue": [{**_fmt(a), "due": str(a.get("due", ""))} for a in overdue],
            },
            indent=2,
            ensure_ascii=False,
        )


def create_mcp_server(vault_path: Path):
    """Create and configure the MCP server with all vault tools.

    Vault path is bound at creation time (long-lived process).
    """
    from mcp.server.fastmcp import FastMCP

    from pester.core.audit import log_event
    from pester.core.config import load_config
    from pester.core.state import ensure_state_dir

    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)
    tools = VaultTools(vault_path, config, state_dir)

    server = FastMCP("pester Vault")

    @server.tool()
    def vault_search(
        query: str,
        top_k: int = 5,
        doc_type: str | None = None,
        status: str | None = None,
    ) -> str:
        """Search the vault using semantic similarity."""
        log_event(vault_path, "mcp_tool", tool="vault_search", query=query)
        return tools.vault_search(query, top_k=top_k, doc_type=doc_type, status=status)

    @server.tool()
    def vault_get_document(path: str) -> str:
        """Read the full content of a vault document."""
        log_event(vault_path, "mcp_tool", tool="vault_get_document", path=path)
        return tools.vault_get_document(path)

    @server.tool()
    def vault_reindex(force: bool = False) -> str:
        """Reindex the vault for semantic search."""
        log_event(vault_path, "mcp_tool", tool="vault_reindex", force=force)
        return tools.vault_reindex(force=force)

    @server.tool()
    def vault_actions(
        status: str = "open",
        owner: str | None = None,
        overdue: bool = False,
        due: str | None = None,
    ) -> str:
        """List action items from the vault."""
        log_event(vault_path, "mcp_tool", tool="vault_actions", status=status)
        return tools.vault_actions(status=status, owner=owner, overdue=overdue, due=due)

    @server.tool()
    def vault_add_action(
        owner: str,
        description: str,
        due: str,
        priority: str = "Should",
        source: str = "manual",
    ) -> str:
        """Create a new action item in the vault."""
        log_event(vault_path, "mcp_tool", tool="vault_add_action", owner=owner)
        return tools.vault_add_action(
            owner=owner, description=description, due=due, priority=priority, source=source
        )

    @server.tool()
    def vault_complete_action(slug: str) -> str:
        """Mark an action as done."""
        log_event(vault_path, "mcp_tool", tool="vault_complete_action", slug=slug)
        return tools.vault_complete_action(slug=slug)

    @server.tool()
    def vault_health() -> str:
        """Run a vault health check and return the report."""
        log_event(vault_path, "mcp_tool", tool="vault_health")
        return tools.vault_health()

    @server.tool()
    def vault_goals() -> str:
        """List all goals from the vault."""
        log_event(vault_path, "mcp_tool", tool="vault_goals")
        return tools.vault_goals()

    @server.tool()
    def vault_goal_progress(goal_slug: str) -> str:
        """Get progress stats for a specific goal."""
        log_event(vault_path, "mcp_tool", tool="vault_goal_progress", goal_slug=goal_slug)
        return tools.vault_goal_progress(goal_slug=goal_slug)

    @server.tool()
    def vault_audit_action(description: str) -> str:
        """Check whether a new action aligns with active goals."""
        log_event(vault_path, "mcp_tool", tool="vault_audit_action")
        return tools.vault_audit_action(description=description)

    @server.tool()
    def vault_reschedule(slug: str, new_due: str) -> str:
        """Reschedule an action to a new due date."""
        log_event(vault_path, "mcp_tool", tool="vault_reschedule", slug=slug)
        return tools.vault_reschedule(slug=slug, new_due=new_due)

    @server.tool()
    def vault_briefing(slug: str) -> str:
        """Get a compiled briefing for a person or project."""
        log_event(vault_path, "mcp_tool", tool="vault_briefing", slug=slug)
        return tools.vault_briefing(slug=slug)

    @server.tool()
    def vault_dashboard() -> str:
        """Get full dashboard data for the vault."""
        log_event(vault_path, "mcp_tool", tool="vault_dashboard")
        return tools.vault_dashboard()

    @server.tool()
    def vault_morning_focus() -> str:
        """Get morning focus data: today's actions, goals, priorities."""
        log_event(vault_path, "mcp_tool", tool="vault_morning_focus")
        return tools.vault_morning_focus()

    @server.tool()
    def vault_weekly_summary() -> str:
        """Get weekly analysis: completion rate, goal progress."""
        log_event(vault_path, "mcp_tool", tool="vault_weekly_summary")
        return tools.vault_weekly_summary()

    @server.tool()
    def vault_overdue_summary() -> str:
        """Get overdue actions grouped by owner with urgency ranking."""
        log_event(vault_path, "mcp_tool", tool="vault_overdue_summary")
        return tools.vault_overdue_summary()

    @server.tool()
    def vault_standup() -> str:
        """Get standup data: yesterday's completed + today's planned actions."""
        log_event(vault_path, "mcp_tool", tool="vault_standup")
        return tools.vault_standup()

    return server


def _make_bearer_middleware(expected_token: str):
    """Create a Starlette ASGI middleware that verifies Bearer tokens.

    Returns 401 for missing/invalid tokens. Only active when MCP_BEARER_TOKEN
    is set in the environment and transport is streamable-http.
    """
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    class BearerTokenMiddleware:
        """ASGI middleware for simple Bearer token authentication."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                request = Request(scope, receive)
                auth_header = request.headers.get("authorization", "")
                if not auth_header.startswith("Bearer ") or not hmac.compare_digest(
                    auth_header[7:], expected_token
                ):
                    response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                    await response(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    return Middleware(BearerTokenMiddleware)


def apply_bearer_auth(server) -> bool:
    """Wrap MCP server's streamable-http app with Bearer token auth.

    Reads MCP_BEARER_TOKEN from the environment. If set, patches the server's
    streamable_http_app method to inject authentication middleware.

    Returns True if auth was applied, False otherwise.
    """
    token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    if not token:
        return False

    original_app_fn = server.streamable_http_app

    def patched_streamable_http_app():
        app = original_app_fn()
        middleware = _make_bearer_middleware(token)
        # Wrap the Starlette app with our middleware
        app.middleware_stack = None  # Force rebuild
        app.user_middleware.insert(0, middleware)
        app.middleware_stack = app.build_middleware_stack()
        return app

    server.streamable_http_app = patched_streamable_http_app
    logger.info("MCP Bearer token authentication enabled")
    return True
