"""Tests for pester.rag.store — VaultStore ChromaDB wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from pester.rag.store import VaultStore  # noqa: E402

pytestmark = pytest.mark.search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768


def _make_chunks() -> list[dict]:
    """Two sample chunks: one document, one transcript."""
    return [
        {
            "id": "file1::0",
            "text": "Hello world",
            "metadata": {"file_path": "file1.md", "source_type": "document"},
        },
        {
            "id": "file2::0",
            "text": "Goodbye",
            "metadata": {"file_path": "file2.md", "source_type": "transcript"},
        },
    ]


def _make_embeddings(n: int, *, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.random((n, EMBEDDING_DIM), dtype=np.float32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVaultStore:
    """Unit tests for VaultStore (backed by real ChromaDB on tmp_path)."""

    def test_store_lazy_init(self, tmp_path: Path) -> None:
        """Creating VaultStore must NOT create the ChromaDB client.

        Accessing .collection should trigger lazy initialization.
        """
        store = VaultStore(tmp_path / "chroma")
        assert store._client is None
        assert store._collection is None

        # Accessing the property triggers init
        _ = store.collection
        assert store._client is not None
        assert store._collection is not None

    def test_store_add_chunks(self, tmp_path: Path) -> None:
        """add_chunks should increase the collection count."""
        store = VaultStore(tmp_path / "chroma")
        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))

        store.add_chunks(chunks, embeddings)

        assert store.collection.count() == 2

    def test_store_search(self, tmp_path: Path) -> None:
        """search returns results with required keys and scores."""
        store = VaultStore(tmp_path / "chroma")
        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))
        store.add_chunks(chunks, embeddings)

        query_emb = embeddings[0]  # search with the first chunk's embedding
        results = store.search(query_emb, top_k=2)

        assert len(results) == 2
        # The closest match to embeddings[0] should be chunks[0]
        assert results[0]["id"] == "file1::0"
        # Every result has the expected keys
        for r in results:
            assert set(r.keys()) == {"id", "text", "metadata", "score", "raw_score"}
            assert isinstance(r["score"], float)
            assert isinstance(r["raw_score"], float)

    def test_store_transcript_score(self, tmp_path: Path) -> None:
        """Transcript chunks get their score multiplied by transcript_score_factor.

        Given two chunks with the *same* embedding (so identical raw_score),
        the transcript chunk should rank lower because its score is reduced by 0.85.
        """
        store = VaultStore(tmp_path / "chroma", transcript_score_factor=0.85)

        # Use the same embedding for both chunks so raw_score is identical
        single_emb = _make_embeddings(1, seed=99)
        shared_emb = np.vstack([single_emb, single_emb])

        chunks = [
            {
                "id": "doc::0",
                "text": "Document text",
                "metadata": {"file_path": "doc.md", "source_type": "document"},
            },
            {
                "id": "transcript::0",
                "text": "Transcript text",
                "metadata": {"file_path": "meeting.md", "source_type": "transcript"},
            },
        ]
        store.add_chunks(chunks, shared_emb)

        results = store.search(single_emb[0], top_k=2)

        # Both have the same raw_score
        assert results[0]["raw_score"] == pytest.approx(results[1]["raw_score"])
        # The document chunk should rank first (higher effective score)
        assert results[0]["metadata"]["source_type"] == "document"
        assert results[1]["metadata"]["source_type"] == "transcript"
        # Transcript score = raw_score * 0.85
        assert results[1]["score"] == pytest.approx(results[1]["raw_score"] * 0.85, rel=1e-5)
        # Document score equals raw_score (no penalty)
        assert results[0]["score"] == pytest.approx(results[0]["raw_score"])

    def test_store_search_filtered(self, tmp_path: Path) -> None:
        """Passing a 'where' filter restricts results to matching metadata."""
        store = VaultStore(tmp_path / "chroma")
        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))
        store.add_chunks(chunks, embeddings)

        # Filter to only transcripts
        results = store.search(
            embeddings[0],
            top_k=5,
            where={"source_type": "transcript"},
        )

        assert len(results) == 1
        assert results[0]["id"] == "file2::0"
        assert results[0]["metadata"]["source_type"] == "transcript"

    def test_store_delete(self, tmp_path: Path) -> None:
        """delete_chunks removes specified chunks by id."""
        store = VaultStore(tmp_path / "chroma")
        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))
        store.add_chunks(chunks, embeddings)
        assert store.collection.count() == 2

        store.delete_chunks(["file1::0"])
        assert store.collection.count() == 1

    def test_store_reset(self, tmp_path: Path) -> None:
        """reset drops and recreates the collection, leaving count at 0."""
        store = VaultStore(tmp_path / "chroma")
        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))
        store.add_chunks(chunks, embeddings)
        assert store.collection.count() == 2

        store.reset()
        assert store.collection.count() == 0

    def test_store_stats(self, tmp_path: Path) -> None:
        """get_stats returns a dict with correct total_chunks."""
        store = VaultStore(tmp_path / "chroma")
        assert store.get_stats() == {"total_chunks": 0}

        chunks = _make_chunks()
        embeddings = _make_embeddings(len(chunks))
        store.add_chunks(chunks, embeddings)
        assert store.get_stats() == {"total_chunks": 2}

    def test_store_require_search(self) -> None:
        """require_search() raises SystemExit when package is missing."""
        from pester.core.extras import make_optional_check

        # When package is present, require_fn is a no-op
        has, require_fn = make_optional_check("os", "search")
        assert has is True
        require_fn()  # should not raise

        # When package is missing, require_fn raises SystemExit
        _, require_fn = make_optional_check("__nonexistent_pkg__", "search")
        with pytest.raises(SystemExit, match="Search requires"):
            require_fn()
