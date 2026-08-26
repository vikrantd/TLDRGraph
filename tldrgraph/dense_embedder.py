"""
Dense Embeddings Backend for TLDRGraph LocalVectorStore.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDINGS_ENV_VAR = "TLDRGRAPH_EMBEDDINGS"
MODEL_CACHE_ENV_VAR = "TLDRGRAPH_MODEL_CACHE"

POLICY_OFF = "off"
POLICY_AUTO = "auto"
POLICY_ON = "on"

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSEY = {"0", "false", "no", "off", "disable", "disabled", ""}

_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}


def resolve_policy(requested: Optional[str] = None) -> str:
    """Resolves policy from explicit argument or environment."""
    raw = (requested if requested is not None else os.environ.get(EMBEDDINGS_ENV_VAR, "")).strip().lower()
    if not raw:
        return POLICY_OFF
    if raw in (POLICY_OFF, POLICY_AUTO, POLICY_ON):
        return raw
    if raw in _TRUTHY:
        return POLICY_ON
    if raw in _FALSEY:
        return POLICY_OFF
    return POLICY_OFF


def default_model_cache_dir() -> str:
    """Stable, per-user cache directory for the ONNX model."""
    override = os.environ.get(MODEL_CACHE_ENV_VAR) or os.environ.get("FASTEMBED_CACHE_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "tldrgraph", "models")


class DenseEmbedder:
    """Thin wrapper over fastembed.TextEmbedding."""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: Optional[str] = None,
        allow_download: bool = False,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir or default_model_cache_dir()
        self.allow_download = allow_download
        self.reason = ""

        self.dim: Optional[int] = None
        self._model = None
        self._np = None
        self._load()

    @staticmethod
    def fastembed_version() -> Optional[str]:
        try:
            import fastembed
            return getattr(fastembed, "__version__", "unknown")
        except Exception:
            return None

    @classmethod
    def model_repo(cls, model_name: str = DEFAULT_EMBEDDING_MODEL) -> Optional[str]:
        try:
            from fastembed import TextEmbedding
            for desc in TextEmbedding._list_supported_models():
                if desc.model == model_name:
                    return getattr(desc.sources, "hf", None)
        except Exception:
            return None
        return None

    @classmethod
    def model_present(cls, model_name: str = DEFAULT_EMBEDDING_MODEL, cache_dir: Optional[str] = None) -> bool:
        cache_dir = cache_dir or default_model_cache_dir()
        if not os.path.isdir(cache_dir):
            return False
        repo = cls.model_repo(model_name)
        candidates = []
        if repo:
            candidates.append("models--" + repo.replace("/", "--"))
            candidates.append(repo.replace("/", "-"))
        for entry in os.listdir(cache_dir):
            if repo and entry not in candidates:
                continue
            path = os.path.join(cache_dir, entry)
            for _, _, files in os.walk(path):
                if any(f.endswith(".onnx") for f in files):
                    return True
        return False

    def _load(self) -> None:
        try:
            import numpy
            self._np = numpy
        except Exception as exc:
            self.reason = f"numpy unavailable: {exc}"
            return

        try:
            from fastembed import TextEmbedding
        except Exception:
            self.reason = "fastembed not installed (pip install 'codechakra[embeddings]')"
            return

        present = self.model_present(self.model_name, self.cache_dir)
        if not present and not self.allow_download:
            self.reason = f"model {self.model_name} not in {self.cache_dir} and download disabled"
            return

        os.makedirs(self.cache_dir, exist_ok=True)
        key = (self.model_name, self.cache_dir)
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            self._model = cached
        else:
            try:
                kwargs: Dict[str, Any] = {"model_name": self.model_name, "cache_dir": self.cache_dir}
                if not self.allow_download:
                    kwargs["local_files_only"] = True
                self._model = TextEmbedding(**kwargs)
            except Exception as exc:
                self.reason = f"could not load {self.model_name}: {exc}"
                self._model = None
                return
            _MODEL_CACHE[key] = self._model

        try:
            self.dim = int(self._model.embedding_size)
        except Exception:
            self.dim = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def encode(self, texts: Sequence[str]):
        if not self.available or not texts:
            return None
        np = self._np
        try:
            vectors = np.asarray(list(self._model.embed(list(texts))), dtype=np.float32)
        except Exception as exc:
            self.reason = f"encode failed: {exc}"
            return None
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            return None
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        if self.dim is None:
            self.dim = int(vectors.shape[1])
        return (vectors / norms).astype(np.float32)
