"""CLI commands for RAG search and indexing: pester search and pester index."""

from __future__ import annotations

from pathlib import Path

import click

from pester.core.config import get_config_value, load_config
from pester.core.state import ensure_state_dir
from pester.core.vault import find_vault_root
from pester.rag import require_search


@click.command()
@click.argument("query")
@click.option("--top-k", "-k", default=5, help="Number of results to return.")
@click.option("--type", "doc_type", default=None, help="Filter by document type.")
@click.option("--status", default=None, help="Filter by document status.")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    top_k: int,
    doc_type: str | None,
    status: str | None,
    json_out: bool,
):
    """Search the vault using semantic similarity."""
    require_search()

    from pester.rag.embeddings import ModelNotFoundError, create_embedder
    from pester.rag.store import VaultStore

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)
    score_factor = get_config_value(config, "search.transcript_score_factor", 0.85)

    try:
        embedder = create_embedder(config)
    except (ModelNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    store = VaultStore(state_dir / "cache" / "chroma", transcript_score_factor=score_factor)

    try:
        query_emb = embedder.embed_query(query)
    except ConnectionError as e:
        raise click.ClickException(str(e)) from e
    except ModelNotFoundError as e:
        raise click.ClickException(str(e)) from e

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

    if json_out:
        import json

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
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if not results:
        click.echo("No results found.")
        if store.get_stats()["total_chunks"] == 0:
            click.echo("Index is empty. Run: pester index")
        return

    for i, item in enumerate(results, 1):
        score = item["score"]
        path = item["metadata"]["file_path"]
        title = item["metadata"].get("title", "") or ""
        text = item["text"]

        # The chunker prepends the title to chunk text for embedding context.
        # Strip it from the displayed snippet to avoid showing the slug twice.
        for prefix in (f"{title}. ", f"{title} "):
            if title and text.startswith(prefix):
                text = text[len(prefix) :]
                break
        snippet = text[:200].replace("\n", " ")

        click.echo(f"\n{i}. [{score:.3f}] {path}")
        # Only show title line if it adds info beyond the path's stem.
        path_stem = Path(path).stem
        if title and title.lower() != path_stem.lower():
            click.echo(f"   {title}")
        click.echo(f"   {snippet}...")


@click.command()
@click.option("--force", is_flag=True, help="Force full reindex (drop existing index).")
@click.pass_context
def index(ctx: click.Context, force: bool):
    """Index (or re-index) vault content for semantic search."""
    require_search()

    from pester.rag.embeddings import ModelNotFoundError, create_embedder
    from pester.rag.indexer import VaultIndexer
    from pester.rag.store import VaultStore

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)

    language = get_config_value(config, "vault.language", "en")
    table_full_files = get_config_value(config, "search.table_full_files", [])
    score_factor = get_config_value(config, "search.transcript_score_factor", 0.85)
    chunk_size = get_config_value(config, "search.chunk_size")

    try:
        embedder = create_embedder(config)
        store = VaultStore(state_dir / "cache" / "chroma", transcript_score_factor=score_factor)
        indexer = VaultIndexer(
            vault_path,
            state_dir,
            embedder=embedder,
            store=store,
            language=language,
            table_full_files=table_full_files,
            transcript_score_factor=score_factor,
            chunk_size=chunk_size,
        )
    except (ModelNotFoundError, ConnectionError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    click.echo("Indexing vault..." + (" (force)" if force else ""))

    try:
        stats = indexer.index_vault(force=force)
    except ModelNotFoundError as e:
        raise click.ClickException(str(e)) from e

    click.echo(
        f"Done: +{stats['files_added']} added, ~{stats['files_updated']} updated, "
        f"-{stats['files_deleted']} deleted, ={stats['files_unchanged']} unchanged"
    )
    click.echo(f"Total: {stats['total_chunks']} chunks indexed")
