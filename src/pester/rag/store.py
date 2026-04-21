"""ChromaDB vector store wrapper for pester RAG.

Lazy initialization: ChromaDB client and collection created on first access.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_BATCH_SIZE = 5000  # ChromaDB upsert limit
_TOP_K_DEFAULT = 5


class VaultStore:
    """Persistent ChromaDB store with lazy initialization.

    The ChromaDB client and 'vault' collection are created on first access,
    not on instantiation.
    """

    def __init__(self, chroma_path: Path, transcript_score_factor: float = 0.85) -> None:
        self._chroma_path = chroma_path
        self._transcript_score_factor = transcript_score_factor
        self._client = None
        self._collection = None

    @property
    def client(self):
        """ChromaDB PersistentClient (lazy loaded)."""
        if self._client is None:
            self._init_store()
        return self._client

    @property
    def collection(self):
        """ChromaDB 'vault' collection (lazy loaded)."""
        if self._collection is None:
            self._init_store()
        return self._collection

    def _init_store(self) -> None:
        """Initialize ChromaDB client and collection."""
        import chromadb

        self._chroma_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_path))
        self._collection = self._client.get_or_create_collection(
            name="vault",
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: list[dict], embeddings) -> None:
        """Upsert chunks with embeddings in batches."""
        n = len(chunks)
        if n == 0:
            return

        ids = [c["id"] for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        emb_list = embeddings.tolist()

        for start in range(0, n, _BATCH_SIZE):
            end = min(start + _BATCH_SIZE, n)
            self.collection.upsert(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                embeddings=emb_list[start:end],
            )
            log.info("Upserted batch %d–%d of %d", start, end, n)

    def search(
        self,
        query_embedding,
        top_k: int = _TOP_K_DEFAULT,
        where: dict | None = None,
    ) -> list[dict]:
        """Search by embedding. Returns list of {id, text, metadata, score, raw_score}."""
        kwargs: dict = {
            "query_embeddings": query_embedding.tolist()
            if hasattr(query_embedding, "tolist")
            else query_embedding,
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        items: list[dict] = []
        for i in range(len(results["ids"][0])):
            distance = results["distances"][0][i]
            raw_score = 1.0 - distance
            metadata = results["metadatas"][0][i]

            score = raw_score
            if metadata.get("source_type") == "transcript":
                score *= self._transcript_score_factor

            items.append(
                {
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": metadata,
                    "score": score,
                    "raw_score": raw_score,
                }
            )

        items.sort(key=lambda x: x["score"], reverse=True)
        return items

    def delete_chunks(self, ids: list[str]) -> None:
        """Delete chunks by id."""
        if not ids:
            return
        for start in range(0, len(ids), _BATCH_SIZE):
            end = min(start + _BATCH_SIZE, len(ids))
            self.collection.delete(ids=ids[start:end])

    def reset(self) -> None:
        """Drop and recreate the collection."""
        self.client.delete_collection("vault")
        self._collection = self.client.get_or_create_collection(
            name="vault",
            metadata={"hnsw:space": "cosine"},
        )

    def get_stats(self) -> dict:
        """Collection statistics."""
        return {"total_chunks": self.collection.count()}
