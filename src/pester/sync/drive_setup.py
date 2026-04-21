"""Interactive Google Drive setup wizard."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import click

from pester.core.vault import atomic_write

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def run_drive_setup(state_dir: Path) -> None:
    """Interactive setup: guide user through OAuth2 credential creation."""
    credentials_dir = state_dir / "credentials" / "drive"
    token_path = credentials_dir / "token.json"
    creds_path = credentials_dir / "credentials.json"

    if token_path.is_file():
        if not click.confirm("Drive credentials already exist. Re-authorize?"):
            click.echo("Setup cancelled.")
            return

    click.echo()
    click.echo("=== Google Drive Setup ===")
    click.echo()
    click.echo("To sync files from Google Drive, you need OAuth2 credentials.")
    click.echo()
    click.echo("Steps:")
    click.echo("  1. Go to https://console.cloud.google.com/")
    click.echo("  2. Create a project (or select an existing one)")
    click.echo("  3. Enable the Google Drive API:")
    click.echo("     APIs & Services > Library > search 'Google Drive API' > Enable")
    click.echo("  4. Create OAuth credentials:")
    click.echo("     APIs & Services > Credentials > Create Credentials > OAuth client ID")
    click.echo("     Application type: Desktop app")
    click.echo("  5. Download the JSON file (client_secret_*.json)")
    click.echo()

    source_path = click.prompt(
        "Path to downloaded credentials JSON file",
        type=click.Path(exists=True, dir_okay=False),
    )

    # Validate it's a proper Google OAuth credentials file
    try:
        with open(source_path) as f:
            data = json.load(f)
        if "installed" not in data and "web" not in data:
            raise click.ClickException(
                "Invalid credentials file. Expected Google OAuth client JSON "
                "(should contain 'installed' or 'web' key)."
            )
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON file: {e}") from e

    # Copy credentials to state dir
    credentials_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, creds_path)
    click.echo(f"Credentials saved to {creds_path}")

    # Run OAuth flow
    click.echo()
    click.echo("Opening browser for authorization...")
    _run_oauth_flow(creds_path, token_path)

    # Verify
    if _verify_connection(credentials_dir):
        click.echo()
        click.echo("Drive setup complete!")
        click.echo()
        click.echo("Next steps:")
        click.echo("  1. Add folder IDs to pester.yaml under sync.drive.folders")
        click.echo("  2. Run: pester sync drive")
    else:
        click.echo("Drive verification failed. Try running setup again.", err=True)


def _run_oauth_flow(credentials_path: Path, token_path: Path) -> None:
    """Run the OAuth2 installed-app flow."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), _SCOPES)
    creds = flow.run_local_server(port=0)
    atomic_write(token_path, creds.to_json())
    click.echo("Authorization successful.")


def _verify_connection(credentials_dir: Path) -> bool:
    """Verify Drive access by listing 1 file."""
    try:
        from pester.sync.drive import build_drive_service

        service = build_drive_service(credentials_dir)
        service.files().list(pageSize=1, fields="files(id, name)").execute()
        click.echo("Verified: successfully connected to Google Drive.")
        return True
    except Exception as e:
        log.warning("Drive verification failed: %s", e)
        return False
