"""ONNX-based embeddings for multilingual-e5-base.

Lazy initialization: model loads on first embed call, not on import.
Atomic download: tmp dir + rename, cleanup on interrupt.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_MAX_LENGTH = 512
DEFAULT_MODEL = "intfloat/multilingual-e5-base"
_EMBEDDING_DIM = 768


class ModelNotFoundError(Exception):
    """Raised when the ONNX model is not downloaded."""


def get_models_dir() -> Path:
    """Return the default models directory: ~/.pester/models/."""
    return Path.home() / ".pester" / "models"


def model_exists(model_dir: Path | None = None) -> bool:
    """Check if the ONNX model files exist at the given directory."""
    d = model_dir or get_models_dir()
    return (d / "onnx" / "model.onnx").is_file() and (d / "tokenizer.json").is_file()


def model_info(model_dir: Path | None = None) -> dict:
    """Return model status info (path, exists, size)."""
    d = model_dir or get_models_dir()
    exists = model_exists(d)
    size_mb = 0.0
    if exists:
        for f in d.rglob("*"):
            if f.is_file():
                size_mb += f.stat().st_size / (1024 * 1024)
    return {
        "path": str(d),
        "exists": exists,
        "size_mb": round(size_mb, 1),
    }


def download_model(
    model_name: str = DEFAULT_MODEL,
    target_dir: Path | None = None,
) -> Path:
    """Download the ONNX model atomically.

    Downloads to a temp directory, then renames to target.
    Cleans up on interrupt or failure.
    """
    from huggingface_hub import snapshot_download

    target = target_dir or get_models_dir()
    if model_exists(target):
        log.info("Model already downloaded at %s", target)
        return target

    tmp_dir = target.with_name(target.name + ".downloading")
    tmp_dir.parent.mkdir(parents=True, exist_ok=True)

    # Clean up any previous failed download
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        log.info("Downloading %s to %s", model_name, tmp_dir)
        # Download only the files E5Embedder actually loads. The HF repo ships
        # multiple ONNX variants (model.onnx, model_O4.onnx, quantized, ARM, etc.)
        # totaling ~2GB; we only use model.onnx (~1.1GB).
        # Suppress the cosmetic HF "no token / anonymous download" notice so
        # first-time users don't think the install is broken.
        import warnings

        hf_logger = logging.getLogger("huggingface_hub")
        prev_level = hf_logger.level
        hf_logger.setLevel(logging.ERROR)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                snapshot_download(
                    model_name,
                    local_dir=str(tmp_dir),
                    allow_patterns=[
                        "onnx/model.onnx",
                        "tokenizer.json",
                        "tokenizer_config.json",
                    ],
                )
        finally:
            hf_logger.setLevel(prev_level)
        # Atomic rename
        if target.exists():
            shutil.rmtree(target)
        tmp_dir.rename(target)
        log.info("Model downloaded to %s", target)
        return target
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


class E5Embedder:
    """Multilingual E5-base embedder via ONNX Runtime (no PyTorch).

    Lazy initialization: ONNX session and tokenizer are loaded on first use.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir or get_models_dir()
        self._session = None
        self._tokenizer = None

    @property
    def session(self):
        """ONNX Runtime inference session (lazy loaded)."""
        if self._session is None:
            self._init_model()
        return self._session

    @property
    def tokenizer(self):
        """HuggingFace tokenizer (lazy loaded)."""
        if self._tokenizer is None:
            self._init_model()
        return self._tokenizer

    def _init_model(self) -> None:
        """Load model and tokenizer from disk. Raises ModelNotFoundError if missing."""
        if not model_exists(self._model_dir):
            raise ModelNotFoundError(
                f"Model not found at {self._model_dir}.\nRun: pester model download"
            )

        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = str(self._model_dir / "onnx" / "model.onnx")
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

        tok_path = str(self._model_dir / "tokenizer.json")
        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_truncation(max_length=_MAX_LENGTH)

    def embed_documents(self, texts: list[str]):
        """Embed documents. Adds 'passage: ' prefix per E5 spec."""
        prefixed = [f"passage: {t}" for t in texts]
        return self._embed(prefixed)

    def embed_query(self, text: str):
        """Embed a single query. Adds 'query: ' prefix per E5 spec."""
        return self._embed([f"query: {text}"])

    def _embed(self, texts: list[str]):
        """Tokenize → ONNX inference → mean pooling → L2-normalize."""
        import numpy as np

        encoded = self.tokenizer.encode_batch(texts)
        max_len = max(len(e.ids) for e in encoded)
        self.tokenizer.enable_padding(pad_id=0, pad_token="<pad>", length=max_len)
        encoded = self.tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        (hidden_state,) = self.session.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )

        # Mean pooling with attention mask
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        pooled = (hidden_state * mask).sum(axis=1) / mask.sum(axis=1)

        # L2-normalize
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        embeddings = pooled / norms

        assert embeddings.shape == (len(texts), _EMBEDDING_DIM)
        return embeddings


class OllamaEmbedder:
    """Embedding via Ollama HTTP API (out-of-process)."""

    def __init__(
        self,
        model: str = "qwen3-embedding:0.6b",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts via Ollama /api/embed endpoint."""
        import requests

        resp = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=600,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise ValueError(f"Ollama returned {len(embeddings)} embeddings for {len(texts)} texts")
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query. Raises on connection failure for search."""
        import requests

        try:
            resp = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": [text]},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if not embeddings:
                raise ValueError("Ollama returned no embeddings")
            return embeddings[0]
        except (requests.ConnectionError, requests.Timeout) as e:
            raise ConnectionError(
                f"Search unavailable: embedding service (Ollama) not reachable at {self.base_url}"
            ) from e


def create_embedder(config: dict) -> E5Embedder | OllamaEmbedder:
    """Create the appropriate embedder based on config."""
    from pester.core.config import get_config_value

    provider = get_config_value(config, "search.provider", "e5")
    if provider == "ollama":
        model = get_config_value(config, "search.model", "qwen3-embedding:0.6b")
        base_url = get_config_value(config, "search.ollama_url", "http://localhost:11434")
        return OllamaEmbedder(model=model, base_url=base_url)
    elif provider == "e5":
        return E5Embedder()
    else:
        raise ValueError(f"Unknown search.provider: {provider!r}. Use 'e5' or 'ollama'.")
