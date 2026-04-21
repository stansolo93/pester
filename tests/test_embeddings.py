"""Tests for pester.rag.embeddings — model management and E5Embedder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pester.rag.embeddings import (
    E5Embedder,
    ModelNotFoundError,
    OllamaEmbedder,
    create_embedder,
    download_model,
    model_exists,
    model_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_fake_model(base: Path, *, size: int = 200_000) -> None:
    """Create the minimal file structure that model_exists() expects.

    Default size is 200KB so model_info() reports size_mb > 0 after rounding.
    """
    (base / "onnx").mkdir(parents=True, exist_ok=True)
    (base / "onnx" / "model.onnx").write_bytes(b"\x00" * size)
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fast tests (no search/slow markers — no chromadb/onnxruntime needed)
# ---------------------------------------------------------------------------


class TestE5EmbedderLazy:
    def test_embedder_no_model(self, tmp_path: Path):
        """Construction with a nonexistent dir succeeds; accessing session raises."""
        missing = tmp_path / "nonexistent"
        embedder = E5Embedder(model_dir=missing)
        # Object created fine (lazy init)
        assert embedder is not None
        with pytest.raises(ModelNotFoundError):
            _ = embedder.session

    def test_embedder_lazy_init(self, tmp_path: Path):
        """Internal session and tokenizer should be None right after __init__."""
        embedder = E5Embedder(model_dir=tmp_path)
        assert embedder._session is None
        assert embedder._tokenizer is None


class TestModelExists:
    def test_model_exists_false(self, tmp_path: Path):
        """Empty tmp dir has no model files — should return False."""
        assert model_exists(tmp_path) is False

    def test_model_exists_true(self, tmp_path: Path):
        """With the expected file structure, model_exists returns True."""
        _create_fake_model(tmp_path)
        assert model_exists(tmp_path) is True


class TestModelInfo:
    def test_model_info(self, tmp_path: Path):
        """model_info returns correct dict for both exists=True and exists=False."""
        # Case 1: no model
        info_missing = model_info(tmp_path)
        assert info_missing["exists"] is False
        assert info_missing["size_mb"] == 0.0
        assert info_missing["path"] == str(tmp_path)

        # Case 2: model present
        _create_fake_model(tmp_path)
        info_present = model_info(tmp_path)
        assert info_present["exists"] is True
        assert info_present["path"] == str(tmp_path)
        assert isinstance(info_present["size_mb"], float)

    def test_model_status_info(self, tmp_path: Path):
        """model_info with files present reports size_mb > 0."""
        _create_fake_model(tmp_path)
        info = model_info(tmp_path)
        assert info["size_mb"] > 0


# ---------------------------------------------------------------------------
# Download tests (marked @search @slow — they exercise the download flow)
# ---------------------------------------------------------------------------


@pytest.mark.search
@pytest.mark.slow
class TestModelDownload:
    def test_model_download_atomic(self, tmp_path: Path):
        """Mock snapshot_download; verify target dir exists after download."""
        target = tmp_path / "models"

        def fake_download(model_name, *, local_dir, allow_patterns):
            local = Path(local_dir)
            _create_fake_model(local)

        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=fake_download,
        ):
            result = download_model(target_dir=target)

        assert result == target
        assert target.is_dir()
        assert model_exists(target)
        # The temp .downloading dir should be gone (renamed to target)
        assert not target.with_name(target.name + ".downloading").exists()

    def test_model_download_interrupted(self, tmp_path: Path):
        """If snapshot_download raises, the .downloading temp dir is cleaned up."""
        target = tmp_path / "models"

        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("network error"),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                download_model(target_dir=target)

        # Temp dir must have been cleaned up
        downloading_dir = target.with_name(target.name + ".downloading")
        assert not downloading_dir.exists()
        # Target should not exist either
        assert not target.exists()


# ---------------------------------------------------------------------------
# OllamaEmbedder tests
# ---------------------------------------------------------------------------


class TestOllamaEmbedder:
    def test_embed_documents_happy_path(self):
        """Mock requests.post to return embeddings, verify list returned."""
        embedder = OllamaEmbedder(model="test-model", base_url="http://localhost:11434")
        fake_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"embeddings": fake_embeddings}

        mock_requests = MagicMock()
        mock_requests.post.return_value = mock_response
        mock_requests.ConnectionError = type("ConnectionError", (Exception,), {})
        mock_requests.Timeout = type("Timeout", (Exception,), {})

        import sys

        with patch.dict(sys.modules, {"requests": mock_requests}):
            result = embedder.embed_documents(["hello", "world"])

        assert result == fake_embeddings
        mock_requests.post.assert_called_once()
        call_args = mock_requests.post.call_args
        assert call_args[1]["json"]["model"] == "test-model"
        assert call_args[1]["json"]["input"] == ["hello", "world"]

    def test_embed_query_connection_failure(self):
        """Mock requests.post to raise ConnectionError, verify ConnectionError raised."""
        embedder = OllamaEmbedder(base_url="http://localhost:99999")

        mock_requests = MagicMock()
        conn_err = type("ConnectionError", (Exception,), {})
        timeout_err = type("Timeout", (Exception,), {})
        mock_requests.ConnectionError = conn_err
        mock_requests.Timeout = timeout_err
        mock_requests.post.side_effect = conn_err("refused")

        import sys

        with patch.dict(sys.modules, {"requests": mock_requests}):
            with pytest.raises(ConnectionError, match="Search unavailable"):
                embedder.embed_query("test query")


# ---------------------------------------------------------------------------
# create_embedder factory tests
# ---------------------------------------------------------------------------


class TestCreateEmbedder:
    def test_factory_default_returns_e5(self):
        """Config with no provider returns E5Embedder instance."""
        config = {"search": {"model": "intfloat/multilingual-e5-base"}}
        embedder = create_embedder(config)
        assert isinstance(embedder, E5Embedder)

    def test_factory_ollama_returns_ollama(self):
        """Config with provider='ollama' returns OllamaEmbedder instance."""
        config = {
            "search": {
                "provider": "ollama",
                "model": "nomic-embed-text",
                "ollama_url": "http://myhost:11434",
            }
        }
        embedder = create_embedder(config)
        assert isinstance(embedder, OllamaEmbedder)
        assert embedder.model == "nomic-embed-text"
        assert embedder.base_url == "http://myhost:11434"

    def test_factory_unknown_provider_raises(self):
        """Config with provider='unknown' raises ValueError."""
        config = {"search": {"provider": "unknown"}}
        with pytest.raises(ValueError, match="Unknown search.provider"):
            create_embedder(config)
