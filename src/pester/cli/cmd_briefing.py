"""CLI command: pester briefing <slug> — compile a person or project briefing."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from pester.core.config import load_config
from pester.core.vault import find_vault_root
from pester.dashboard.data import get_briefing_data
from pester.dashboard.terminal import render_briefing_markdown, render_briefing_terminal

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


@click.command()
@click.argument("slug")
@click.option("--rag/--no-rag", default=True, help="Include RAG search results.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "markdown"]),
    default="terminal",
    help="Output format.",
)
@click.pass_context
def briefing(ctx: click.Context, slug: str, rag: bool, output_format: str) -> None:
    """Compile a briefing on a person or project.

    SLUG is the filename (without .md) of a person or project document.
    """
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)
    data = get_briefing_data(vault_path, config, slug)

    if data is None:
        raise click.ClickException(f"No person or project found for slug: {slug}")

    # Optionally augment with RAG search results
    if rag:
        data.rag_results = _try_rag_search(vault_path, config, slug, data.target.title)
        if data.rag_results is None and not ctx.obj.get("quiet"):
            click.echo("Tip: install pester[search] for semantic search augmentation.", err=True)

    if output_format == "markdown":
        click.echo(render_briefing_markdown(data))
    else:
        click.echo(render_briefing_terminal(data))


def _try_rag_search(vault_path: Path, config: dict, slug: str, title: str) -> list[dict] | None:
    """Try to get RAG search results. Returns None if [search] not available."""
    try:
        from pester.rag import HAS_SEARCH

        if not HAS_SEARCH:
            return None

        from pester.core.config import get_config_value
        from pester.core.state import ensure_state_dir
        from pester.rag.embeddings import E5Embedder
        from pester.rag.store import VaultStore

        state_dir = ensure_state_dir(vault_path)  # type: ignore[arg-type]
        score_factor = get_config_value(config, "search.transcript_score_factor", 0.85)

        embedder = E5Embedder()
        store = VaultStore(state_dir / "cache" / "chroma", transcript_score_factor=score_factor)

        query = f"{title} {slug}"
        query_emb = embedder.embed_query(query)
        return store.search(query_emb, top_k=5)
    except (ImportError, OSError, ValueError, TypeError) as exc:
        logger.warning("RAG search failed for briefing: %s", exc)
        return None
