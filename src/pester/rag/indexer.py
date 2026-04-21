"""Incremental vault indexer with manifest-based change detection.

Uses per-file chunking (not full-vault re-chunk) for efficiency.
Atomic manifest saves with corruption recovery.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pester.core.config import EXCLUDE_DIRS, SKIP_FILES
from pester.core.vault import atomic_write

from .chunker import chunk_file
from .embeddings import E5Embedder
from .store import VaultStore

log = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 64


class VaultIndexer:
    """Incremental indexer: chunk → embed → store, with SHA256 manifest.

    Uses per-file chunking for O(changed) instead of O(all) performance.
    """

    def __init__(
        self,
        vault_path: Path,
        state_dir: Path,
        *,
        embedder: E5Embedder | None = None,
        store: VaultStore | None = None,
        language: str = "en",
        table_full_files: list[str] | None = None,
        transcript_score_factor: float = 0.85,
        chunk_size: int | None = None,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.state_dir = Path(state_dir)
        self.manifest_path = self.state_dir / "manifest.json"
        self.language = language
        self.table_full_files = table_full_files
        self.chunk_size = chunk_size

        chroma_path = self.state_dir / "cache" / "chroma"
        self.embedder = embedder or E5Embedder()
        self.store = store or VaultStore(chroma_path, transcript_score_factor)

    # ── Public API ────────────────────────────────────────────────────────────

    def index_vault(self, force: bool = False) -> dict:
        """Incremental (or forced full) indexing."""
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if force:
            log.info("Force reindex — resetting collection")
            self.store.reset()
            manifest: dict = {}
        else:
            manifest = self._load_manifest()

        current_files = self._scan_vault()

        old_paths = set(manifest.keys())
        new_paths = set(current_files.keys())

        added_paths = new_paths - old_paths
        deleted_paths = old_paths - new_paths
        common_paths = new_paths & old_paths
        changed_paths = {p for p in common_paths if current_files[p] != manifest[p]["hash"]}
        unchanged_paths = common_paths - changed_paths

        # Delete removed/changed files' chunks
        ids_to_delete: list[str] = []
        for p in deleted_paths | changed_paths:
            ids_to_delete.extend(manifest[p].get("chunk_ids", []))
        if ids_to_delete:
            self.store.delete_chunks(ids_to_delete)
            log.info(
                "Deleted %d chunks from %d files",
                len(ids_to_delete),
                len(deleted_paths | changed_paths),
            )

        # Chunk and embed new/changed files (per-file, not full-vault)
        paths_to_index = added_paths | changed_paths
        new_manifest: dict = {}
        total_new_chunks = 0

        if paths_to_index:
            all_chunks = self._chunk_files(paths_to_index)
            if all_chunks:
                try:
                    embeddings = self._embed_chunks(all_chunks)
                    self.store.add_chunks(all_chunks, embeddings)
                    total_new_chunks = len(all_chunks)
                except Exception as e:
                    log.error("Embedding failed, skipping index update: %s", e)
                    all_chunks = []

            # Build manifest entries for indexed files
            chunks_by_file: dict[str, list[str]] = {}
            for c in all_chunks:
                fp = c["metadata"]["file_path"]
                chunks_by_file.setdefault(fp, []).append(c["id"])

            for p in paths_to_index:
                new_manifest[p] = {
                    "hash": current_files[p],
                    "chunk_ids": chunks_by_file.get(p, []),
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                }

        # Carry over unchanged entries
        for p in unchanged_paths:
            new_manifest[p] = manifest[p]

        self._save_manifest(new_manifest)

        stats = {
            "files_added": len(added_paths),
            "files_updated": len(changed_paths),
            "files_deleted": len(deleted_paths),
            "files_unchanged": len(unchanged_paths),
            "chunks_added": total_new_chunks,
            "total_chunks": self.store.get_stats()["total_chunks"],
        }
        log.info(
            "Index complete: +%d ~%d -%d =%d files, %d total chunks",
            stats["files_added"],
            stats["files_updated"],
            stats["files_deleted"],
            stats["files_unchanged"],
            stats["total_chunks"],
        )
        return stats

    def get_status(self) -> dict:
        """Current index status."""
        manifest = self._load_manifest()
        store_stats = self.store.get_stats()

        manifest_mtime = None
        if self.manifest_path.exists():
            ts = self.manifest_path.stat().st_mtime
            manifest_mtime = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        return {
            "files_indexed": len(manifest),
            "total_chunks": store_stats["total_chunks"],
            "manifest_path": str(self.manifest_path),
            "manifest_mtime": manifest_mtime,
            "chroma_path": str(self.state_dir / "cache" / "chroma"),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _scan_vault(self) -> dict[str, str]:
        """Scan vault for .md files, return {rel_path: sha256_hash}."""
        skip_set = set(SKIP_FILES)
        result: dict[str, str] = {}

        for md_file in sorted(self.vault_path.rglob("*.md")):
            rel = str(md_file.relative_to(self.vault_path))
            if any(part in EXCLUDE_DIRS for part in md_file.relative_to(self.vault_path).parts):
                continue
            if rel in skip_set:
                continue
            try:
                result[rel] = self._compute_file_hash(md_file)
            except OSError as e:
                log.warning("Cannot hash %s: %s", rel, e)

        return result

    def _compute_file_hash(self, file_path: Path) -> str:
        """SHA256 of file contents."""
        h = hashlib.sha256()
        h.update(file_path.read_bytes())
        return h.hexdigest()

    def _chunk_files(self, rel_paths: set[str]) -> list[dict]:
        """Chunk only the specified files using per-file chunk_file()."""
        all_chunks: list[dict] = []
        for rel in sorted(rel_paths):
            full_path = self.vault_path / rel
            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                log.warning("Cannot read %s: %s", rel, e)
                continue
            chunks = chunk_file(
                rel,
                content,
                language=self.language,
                table_full_files=self.table_full_files,
                chunk_size=self.chunk_size,
            )
            all_chunks.extend(chunks)
        return all_chunks

    def _embed_chunks(self, chunks: list[dict]):
        """Embed chunks in batches."""
        import numpy as np

        texts = [c["text"] for c in chunks]
        all_embeddings: list = []

        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            end = min(start + _EMBED_BATCH_SIZE, len(texts))
            batch = texts[start:end]
            emb = self.embedder.embed_documents(batch)
            all_embeddings.append(emb)
            log.info("Embedded batch %d–%d of %d", start, end, len(texts))

        return np.vstack(all_embeddings)

    def _load_manifest(self) -> dict:
        """Load manifest with corruption recovery."""
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Corrupt manifest at %s: %s — starting fresh", self.manifest_path, e)
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        """Save manifest atomically."""
        content = json.dumps(manifest, ensure_ascii=False, indent=2)
        atomic_write(self.manifest_path, content)
