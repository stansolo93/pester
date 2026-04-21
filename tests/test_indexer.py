"""Tests for pester.rag.indexer — VaultIndexer incremental indexing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy")

from pester.rag.indexer import VaultIndexer  # noqa: E402
from pester.rag.store import VaultStore  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MD_CONTENT_A = """\
---
title: Project Alpha
type: reference
tags: [project, alpha]
---

## Overview

Project Alpha is a strategic initiative focused on expanding market share
in the EMEA region. The project was approved in Q1 2026 and is currently
in the execution phase with a target completion date of Q4 2026.

## Goals

- Increase EMEA revenue by 30%
- Establish partnerships with 5 local distributors
- Launch localized marketing campaigns in 3 countries
"""

_MD_CONTENT_B = """\
---
title: Meeting Notes Q1
type: journal
tags: [meeting, quarterly]
---

## Attendees

Stan, Maria, Alex, Jordan

## Key Decisions

1. Budget approved for Project Alpha expansion
2. Hiring freeze lifted for engineering team
3. New vendor onboarding process to start in April

## Action Items

- Stan: finalize vendor shortlist by March 25
- Maria: prepare Q2 OKR draft by April 1
- Alex: schedule architecture review for next sprint
"""

_MD_CONTENT_C = """\
---
title: Financial Summary
type: reference
tags: [finance]
---

## Revenue

Total revenue for Q1 reached 1.2M USD which represents a 15% increase
year-over-year. The growth was primarily driven by the APAC region which
contributed 450K USD, followed by EMEA at 380K USD and Americas at 370K USD.

## Expenses

Operating expenses remained flat at 800K USD. The largest cost center
continues to be personnel at 520K USD, followed by infrastructure at
180K USD and marketing at 100K USD.
"""


def _make_vault(vault_path: Path) -> None:
    """Create a minimal vault with pester.yaml and three .md files."""
    vault_path.mkdir(parents=True, exist_ok=True)
    (vault_path / "pester.yaml").write_text(
        "vault:\n  name: Test Vault\n  language: en\n",
        encoding="utf-8",
    )
    (vault_path / "project-alpha.md").write_text(_MD_CONTENT_A, encoding="utf-8")
    (vault_path / "meeting-notes.md").write_text(_MD_CONTENT_B, encoding="utf-8")
    (vault_path / "financials.md").write_text(_MD_CONTENT_C, encoding="utf-8")


def _make_mock_embedder(n_chunks: int = 50) -> MagicMock:
    """Return a mock embedder that produces 768-dim float32 vectors.

    The side_effect dynamically sizes the output to match the input batch,
    so it works regardless of the number of chunks passed.
    """
    mock = MagicMock()
    mock.embed_documents.side_effect = lambda texts: np.random.rand(len(texts), 768).astype(
        np.float32
    )
    return mock


def _build_indexer(
    vault_path: Path,
    state_dir: Path,
    embedder: MagicMock | None = None,
) -> VaultIndexer:
    """Build a VaultIndexer with a real ChromaDB store and mock embedder."""
    chroma_path = state_dir / "cache" / "chroma"
    store = VaultStore(chroma_path)
    return VaultIndexer(
        vault_path,
        state_dir,
        embedder=embedder or _make_mock_embedder(),
        store=store,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.search
class TestVaultIndexer:
    """Integration tests for VaultIndexer with real ChromaDB + mock embedder."""

    def test_index_fresh(self, tmp_path: Path) -> None:
        """Index a vault with no existing manifest — all files should be added."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)

        indexer = _build_indexer(vault, state)
        stats = indexer.index_vault()

        assert stats["files_added"] == 3
        assert stats["files_updated"] == 0
        assert stats["files_deleted"] == 0
        assert stats["files_unchanged"] == 0
        assert stats["chunks_added"] > 0
        assert stats["total_chunks"] > 0
        assert stats["total_chunks"] == stats["chunks_added"]

    def test_index_incremental(self, tmp_path: Path) -> None:
        """Index, modify one file, re-index — only the modified file is updated."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)
        embedder = _make_mock_embedder()

        indexer = _build_indexer(vault, state, embedder)
        first_stats = indexer.index_vault()

        # Modify one file
        (vault / "project-alpha.md").write_text(
            _MD_CONTENT_A + "\n## Addendum\n\nNew section added after initial index.\n",
            encoding="utf-8",
        )

        second_stats = indexer.index_vault()

        assert second_stats["files_updated"] == 1
        assert second_stats["files_added"] == 0
        assert second_stats["files_deleted"] == 0
        assert second_stats["files_unchanged"] == first_stats["files_added"] - 1

    def test_index_deletion(self, tmp_path: Path) -> None:
        """Index, delete a file, re-index — the deleted file is reported."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)

        indexer = _build_indexer(vault, state)
        first_stats = indexer.index_vault()
        initial_chunks = first_stats["total_chunks"]

        # Delete one file
        (vault / "financials.md").unlink()

        second_stats = indexer.index_vault()

        assert second_stats["files_deleted"] == 1
        assert second_stats["files_added"] == 0
        assert second_stats["files_updated"] == 0
        assert second_stats["files_unchanged"] == first_stats["files_added"] - 1
        assert second_stats["total_chunks"] < initial_chunks

    def test_index_force(self, tmp_path: Path) -> None:
        """Force reindex processes all files again, even if unchanged."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)
        embedder = _make_mock_embedder()

        indexer = _build_indexer(vault, state, embedder)
        first_stats = indexer.index_vault()

        # Force reindex — everything should be re-added (manifest is wiped)
        second_stats = indexer.index_vault(force=True)

        assert second_stats["files_added"] == first_stats["files_added"]
        assert second_stats["files_unchanged"] == 0
        assert second_stats["chunks_added"] > 0

    def test_index_corrupt_manifest(self, tmp_path: Path) -> None:
        """Corrupt manifest JSON is handled gracefully — treated as fresh index."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)
        state.mkdir(parents=True, exist_ok=True)

        # Write invalid JSON to the manifest path
        manifest_path = state / "manifest.json"
        manifest_path.write_text("{{{not valid json!!!", encoding="utf-8")

        indexer = _build_indexer(vault, state)
        stats = indexer.index_vault()

        # Should recover and treat as a fresh index (all files added)
        assert stats["files_added"] == 3
        assert stats["files_unchanged"] == 0
        assert stats["chunks_added"] > 0

    def test_index_atomic_save(self, tmp_path: Path) -> None:
        """After indexing, the manifest file exists and is valid JSON."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)

        indexer = _build_indexer(vault, state)
        indexer.index_vault()

        manifest_path = state / "manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(manifest, dict)
        assert len(manifest) == 3  # three files indexed

        # Each entry should have hash, chunk_ids, and indexed_at
        for rel_path, entry in manifest.items():
            assert "hash" in entry
            assert "chunk_ids" in entry
            assert "indexed_at" in entry
            assert isinstance(entry["hash"], str)
            assert len(entry["hash"]) == 64  # SHA256 hex digest
            assert isinstance(entry["chunk_ids"], list)

    def test_index_status(self, tmp_path: Path) -> None:
        """After indexing, get_status() returns correct files_indexed and total_chunks."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)

        indexer = _build_indexer(vault, state)
        stats = indexer.index_vault()

        status = indexer.get_status()

        assert status["files_indexed"] == 3
        assert status["total_chunks"] == stats["total_chunks"]
        assert status["total_chunks"] > 0
        assert status["manifest_mtime"] is not None
        assert str(state / "manifest.json") == status["manifest_path"]

    def test_index_per_file_chunk(self, tmp_path: Path) -> None:
        """chunk_file is called per changed file only, not for unchanged files."""
        vault = tmp_path / "vault"
        state = tmp_path / "state"
        _make_vault(vault)

        indexer = _build_indexer(vault, state)

        with patch(
            "pester.rag.indexer.chunk_file",
            wraps=__import__("pester.rag.chunker", fromlist=["chunk_file"]).chunk_file,
        ) as mock_chunk:
            # First index: chunk_file should be called for all 3 files
            indexer.index_vault()
            assert mock_chunk.call_count == 3

        # Modify one file
        (vault / "meeting-notes.md").write_text(
            _MD_CONTENT_B + "\n## Follow-up\n\nScheduled for next week.\n",
            encoding="utf-8",
        )

        with patch(
            "pester.rag.indexer.chunk_file",
            wraps=__import__("pester.rag.chunker", fromlist=["chunk_file"]).chunk_file,
        ) as mock_chunk:
            # Second index: chunk_file should only be called for the changed file
            indexer.index_vault()
            assert mock_chunk.call_count == 1
            assert mock_chunk.call_args_list[0].args[0] == "meeting-notes.md"
