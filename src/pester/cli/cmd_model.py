"""CLI commands for model management: pester model download, pester model status."""

from __future__ import annotations

import click


@click.group()
def model():
    """Manage the embedding model."""


@model.command()
@click.option("--model-name", default=None, help="HuggingFace model name to download.")
@click.pass_context
def download(ctx: click.Context, model_name: str | None):
    """Download the ONNX embedding model."""
    from pester.rag import require_search

    require_search()

    from pester.rag.embeddings import DEFAULT_MODEL, download_model, get_models_dir

    name = model_name or DEFAULT_MODEL
    target = get_models_dir()

    click.echo(f"Downloading model: {name}")
    click.echo(f"Target: {target}")

    try:
        download_model(model_name=name, target_dir=target)
        click.echo("Download complete.")
    except KeyboardInterrupt:
        click.echo("\nDownload interrupted. Partial files cleaned up.", err=True)
        raise SystemExit(1)
    except Exception as e:
        raise click.ClickException(f"Download failed: {e}") from e


@model.command()
def status():
    """Check if the embedding model is downloaded."""
    from pester.rag.embeddings import model_info

    info = model_info()
    if info["exists"]:
        click.echo("Model: downloaded")
        click.echo(f"Path:  {info['path']}")
        click.echo(f"Size:  {info['size_mb']} MB")
    else:
        click.echo("Model: not downloaded")
        click.echo(f"Expected at: {info['path']}")
        click.echo("Run: pester model download")
