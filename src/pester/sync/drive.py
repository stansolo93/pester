"""Google Drive sync — download and convert files from configured folders."""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pester.core.config import get_config_value
from pester.core.vault import atomic_write
from pester.sync.sync_state import (
    get_drive_folder_state,
    load_sync_state,
    save_sync_state,
    set_drive_folder_state,
    state_lock,
)

log = logging.getLogger(__name__)

# Google MIME types for export
_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
_GOOGLE_FOLDER = "application/vnd.google-apps.folder"
_PAGE_SIZE = 100


@dataclass
class SyncResult:
    """Result of a sync operation."""

    files_added: int = 0
    files_updated: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: SyncResult) -> None:
        """Merge another SyncResult into this one."""
        self.files_added += other.files_added
        self.files_updated += other.files_updated
        self.files_skipped += other.files_skipped
        self.files_failed += other.files_failed
        self.errors.extend(other.errors)


@dataclass
class DriveFile:
    """Metadata for a file in Google Drive."""

    id: str
    name: str
    mime_type: str
    modified_time: str
    size: int | None = None


def build_drive_service(credentials_dir: Path):
    """Build an authenticated Google Drive API service.

    Raises:
        FileNotFoundError: if token.json is missing (need to run --setup)
    """
    token_path = credentials_dir / "token.json"
    if not token_path.is_file():
        raise FileNotFoundError(
            f"No Drive token found at {token_path}. Run: pester sync drive --setup"
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path))

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        atomic_write(token_path, creds.to_json())

    return build("drive", "v3", credentials=creds)


def list_folder_files(service, folder_id: str, since: str | None = None) -> list[DriveFile]:
    """List files in a Drive folder, optionally filtered by modifiedTime."""
    query = f"'{folder_id}' in parents and trashed = false"
    if since:
        query += f" and modifiedTime > '{since}'"

    files: list[DriveFile] = []
    page_token = None

    while True:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=_PAGE_SIZE,
                pageToken=page_token,
                orderBy="modifiedTime desc",
            )
            .execute()
        )

        for f in resp.get("files", []):
            files.append(
                DriveFile(
                    id=f["id"],
                    name=f["name"],
                    mime_type=f["mimeType"],
                    modified_time=f["modifiedTime"],
                    size=f.get("size"),
                )
            )

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return files


def _sanitize_filename(name: str) -> str:
    """Convert a Drive filename to a filesystem-safe slug."""
    name = re.sub(r"[^\w\s\-.]", "", name)
    name = re.sub(r"\s+", "-", name.strip())
    return name.lower() or "untitled"


def _yaml_escape(value: str) -> str:
    """Escape a string for safe use in YAML frontmatter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _add_frontmatter(content: str, file_meta: DriveFile) -> str:
    """Add YAML frontmatter to synced content."""
    now = datetime.now(timezone.utc).isoformat()
    fm = (
        "---\n"
        "type: reference\n"
        "source: google-drive\n"
        f"drive_id: {_yaml_escape(file_meta.id)}\n"
        f"original_name: {_yaml_escape(file_meta.name)}\n"
        f"synced_at: {now}\n"
        "---\n\n"
    )
    return fm + content


def _export_google_doc(service, file_meta: DriveFile) -> str:
    """Export a Google Doc as plain text, wrap as markdown with frontmatter."""
    content = service.files().export(fileId=file_meta.id, mimeType="text/plain").execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    title = file_meta.name
    body = f"# {title}\n\n{content}"
    return _add_frontmatter(body, file_meta)


def _csv_to_markdown_table(csv_text: str) -> str:
    """Convert CSV text to a markdown table."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return ""

    header = rows[0]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows[1:]:
        # Pad or truncate to match header length
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")

    return "\n".join(lines)


def _export_google_sheet(service, file_meta: DriveFile) -> str:
    """Export a Google Sheet as CSV, convert to markdown table."""
    content = service.files().export(fileId=file_meta.id, mimeType="text/csv").execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    title = file_meta.name
    table = _csv_to_markdown_table(content)
    body = f"# {title}\n\n{table}"
    return _add_frontmatter(body, file_meta)


def download_file(
    service, file_meta: DriveFile, target_dir: Path, assets_dir: Path
) -> tuple[Path, bool]:
    """Download a Drive file, converting Google formats to markdown.

    Returns:
        (path, is_new) — the path to the created file and whether it's new.
    """
    safe_name = _sanitize_filename(file_meta.name)

    if file_meta.mime_type == _GOOGLE_DOC:
        content = _export_google_doc(service, file_meta)
        out_path = target_dir / f"{safe_name}.md"
        is_new = not out_path.exists()
        atomic_write(out_path, content)
        return out_path, is_new

    if file_meta.mime_type == _GOOGLE_SHEET:
        content = _export_google_sheet(service, file_meta)
        out_path = target_dir / f"{safe_name}.md"
        is_new = not out_path.exists()
        atomic_write(out_path, content)
        return out_path, is_new

    if file_meta.mime_type == _GOOGLE_SLIDES:
        # Download as PDF to assets
        pdf_data = service.files().export(fileId=file_meta.id, mimeType="application/pdf").execute()
        pdf_path = assets_dir / f"{safe_name}.pdf"
        atomic_write(pdf_path, pdf_data)

        # Create markdown stub linking to PDF
        rel_path = f"../assets/{safe_name}.pdf"
        stub = _add_frontmatter(f"# {file_meta.name}\n\n[View slides]({rel_path})\n", file_meta)
        out_path = target_dir / f"{safe_name}.md"
        is_new = not out_path.exists()
        atomic_write(out_path, stub)
        return out_path, is_new

    if file_meta.mime_type == _GOOGLE_FOLDER:
        # Folders are handled by recursive traversal, not download
        return Path(), False

    # Binary / other files: download to assets, create stub
    content_bytes = service.files().get_media(fileId=file_meta.id).execute()
    ext = Path(file_meta.name).suffix or ".bin"
    asset_path = assets_dir / f"{safe_name}{ext}"
    atomic_write(asset_path, content_bytes)

    rel_path = f"../assets/{safe_name}{ext}"
    stub = _add_frontmatter(f"# {file_meta.name}\n\n[Download file]({rel_path})\n", file_meta)
    out_path = target_dir / f"{safe_name}.md"
    is_new = not out_path.exists()
    atomic_write(out_path, stub)
    return out_path, is_new


def sync_drive_folder(
    service,
    folder_config: dict,
    vault_path: Path,
    sync_state: dict,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Sync a single Drive folder to the vault."""
    result = SyncResult()
    folder_id = folder_config["id"]
    # Accept `vault_path` as an alias of `vault_dir` (older configs used this name).
    vault_subdir = folder_config.get("vault_dir") or folder_config.get(
        "vault_path", "reference/drive"
    )
    if "vault_path" in folder_config and "vault_dir" not in folder_config:
        log.warning(
            "Drive folder %s uses 'vault_path:'; rename to 'vault_dir:' "
            "(backward-compat in this release).",
            folder_id,
        )
    vault_dir = vault_path / vault_subdir
    assets_dir = vault_path / "reference" / "assets"
    label = folder_config.get("label", folder_id)

    folder_state = get_drive_folder_state(sync_state, folder_id)
    since = folder_state.get("last_sync")

    log.info("Listing files in Drive folder: %s", label)
    files = list_folder_files(service, folder_id, since=since)

    if not files:
        log.info("No new or updated files in: %s", label)
        return result

    log.info("Found %d file(s) to sync from: %s", len(files), label)

    if dry_run:
        for f in files:
            log.info("[DRY RUN] Would sync: %s (%s)", f.name, f.mime_type)
        result.files_added = len(files)
        return result

    vault_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    for file_meta in files:
        if file_meta.mime_type == _GOOGLE_FOLDER:
            # Recurse into subfolder
            sub_config = {
                "id": file_meta.id,
                "vault_dir": str(vault_dir.relative_to(vault_path)),
                "label": file_meta.name,
            }
            sub_result = sync_drive_folder(
                service, sub_config, vault_path, sync_state, dry_run=dry_run
            )
            result.files_added += sub_result.files_added
            result.files_updated += sub_result.files_updated
            result.files_failed += sub_result.files_failed
            result.errors.extend(sub_result.errors)
            continue
        try:
            _, is_new = download_file(service, file_meta, vault_dir, assets_dir)
            if is_new:
                result.files_added += 1
            else:
                result.files_updated += 1
        except Exception as e:
            log.warning("Failed to sync %s: %s", file_meta.name, e)
            result.files_failed += 1
            result.errors.append(f"{file_meta.name}: {e}")

    # Update sync state with current timestamp
    now = datetime.now(timezone.utc).isoformat()
    set_drive_folder_state(sync_state, folder_id, {"last_sync": now})

    return result


def sync_all_drive(
    vault_path: Path,
    config: dict,
    state_dir: Path,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Sync all configured Drive folders. Main entry point for CLI."""
    credentials_dir = state_dir / "credentials" / "drive"
    service = build_drive_service(credentials_dir)

    folders = get_config_value(config, "sync.drive.folders", [])
    if not folders:
        log.info("No Drive folders configured in pester.yaml")
        return SyncResult()

    with state_lock(state_dir):
        sync_state = load_sync_state(state_dir)
        result = SyncResult()

        for folder_config in folders:
            folder_result = sync_drive_folder(
                service, folder_config, vault_path, sync_state, dry_run=dry_run
            )
            result.merge(folder_result)

        if not dry_run:
            save_sync_state(state_dir, sync_state)

    return result
