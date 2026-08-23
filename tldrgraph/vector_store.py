"""
Local Vector Store for TLDRGraph: TF-IDF (default) and FastEmbed hybrid retrieval (optional opt-in, falls back to TF-IDF, 100% offline, $0 token cost).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .dense_embedder import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDINGS_ENV_VAR,
    MODEL_CACHE_ENV_VAR,
    POLICY_AUTO,
    POLICY_OFF,
    POLICY_ON,
    DenseEmbedder,
    _MODEL_CACHE,
    default_model_cache_dir,
    resolve_policy,
)
from .vector_tfidf import (
    build_doc_vectors,
    compute_idf,
    cosine_similarity,
    doc_text,
    humanize_identifier,
    query_vector,
    tokenize_code,
)

INDEX_FORMAT_VERSION = 2
BACKEND_TFIDF = "tfidf"
BACKEND_HYBRID = "hybrid"

PROSE_MIN_WORDS = 3
DENSE_WEIGHT_IDENTIFIER = 0.15
DENSE_WEIGHT_PROSE = 0.60
DENSE_BASELINE = 0.55

SCORE_FLOORS: Dict[str, float] = {
    BACKEND_TFIDF: 0.35,
    BACKEND_HYBRID: round((1.0 - DENSE_WEIGHT_IDENTIFIER) * 0.35, 3),
}

_resolve_policy = resolve_policy


def is_prose_query(query: str) -> bool:
    return len((query or "").split()) >= PROSE_MIN_WORDS


def clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, val))


class LocalVectorStore:
    """Offline vector store supporting TF-IDF and dense embeddings."""

    def __init__(
        self,
        index_path: str = ".tldrgraph/vector_index.json",
        embeddings: Optional[str] = None,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        model_cache_dir: Optional[str] = None,
    ):
        self.index_path = index_path
        os.makedirs(os.path.dirname(self.index_path) or ".", exist_ok=True)
        self.documents: List[Dict[str, Any]] = []
        self.vocabulary: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_vectors: List[Dict[str, float]] = []

        self.policy = resolve_policy(embeddings)
        self.model_name = model_name
        self.model_cache_dir = model_cache_dir or default_model_cache_dir()

        self._vector_cache: Dict[str, Any] = {}
        self.doc_embeddings = None
        self._embedder: Optional[DenseEmbedder] = None
        self._encoded_last_run = 0
        self._reused_last_run = 0
        self._loaded_format_version = None

        self._load()

    @property
    def embedder(self) -> Optional[DenseEmbedder]:
        if self.policy == POLICY_OFF:
            return None
        if self._embedder is None:
            self._embedder = DenseEmbedder(
                model_name=self.model_name,
                cache_dir=self.model_cache_dir,
                allow_download=(self.policy == POLICY_ON),
            )
        return self._embedder

    @property
    def backend(self) -> str:
        embedder = self.embedder
        if embedder is None or not embedder.available:
            return BACKEND_TFIDF
        if self.doc_embeddings is None or len(self.doc_embeddings) != len(self.documents):
            return BACKEND_TFIDF
        return BACKEND_HYBRID

    @property
    def score_floor(self) -> float:
        return SCORE_FLOORS[self.backend]

    def embeddings_sidecar_path(self) -> str:
        base, _ = os.path.splitext(self.index_path)
        return f"{base}.embeddings.npz"

    def _tokenize(self, text: str) -> List[str]:
        return tokenize_code(text)

    @staticmethod
    def _doc_text(doc: Dict[str, Any]) -> str:
        return doc_text(doc)

    @staticmethod
    def _humanize(identifier: str) -> str:
        return humanize_identifier(identifier)

    def _embed_text(self, doc: Dict[str, Any]) -> str:
        label = self._humanize(str(doc.get("label", "")))
        layer = str(doc.get("layer", ""))
        file_path = str(doc.get("file", ""))
        where = self._humanize(os.path.splitext(file_path)[0].replace("/", " "))
        intent = str(doc.get("intent") or "")
        summary = "" if intent else str(doc.get("summary") or "")
        fields = doc.get("fields") or []
        if isinstance(fields, (list, tuple)):
            fields = ", ".join(self._humanize(str(f)) for f in fields)
        parts = [p for p in (
            label,
            f"in {layer}" if layer else "",
            f"defined in {where}" if where else "",
            intent or summary,
            f"fields: {fields}" if fields else "",
        ) if p]
        return ". ".join(parts)

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _dense_weight(query: str) -> float:
        words = (query or "").split()
        return DENSE_WEIGHT_PROSE if len(words) >= PROSE_MIN_WORDS else DENSE_WEIGHT_IDENTIFIER

    def diagnostics(self) -> Dict[str, Any]:
        embedder = self.embedder
        sidecar = self.embeddings_sidecar_path()
        covered = 0 if self.doc_embeddings is None else int(len(self.doc_embeddings))
        return {
            "backend": self.backend,
            "policy": self.policy,
            "policy_env_var": EMBEDDINGS_ENV_VAR,
            "index_path": os.path.abspath(self.index_path),
            "index_exists": os.path.exists(self.index_path),
            "index_bytes": os.path.getsize(self.index_path) if os.path.exists(self.index_path) else 0,
            "index_format_version": self._loaded_format_version,
            "expected_format_version": INDEX_FORMAT_VERSION,
            "document_count": len(self.documents),
            "score_floor": self.score_floor,
            "score_floors": dict(SCORE_FLOORS),
            "fusion": {
                "dense_weight_identifier": DENSE_WEIGHT_IDENTIFIER,
                "dense_weight_prose": DENSE_WEIGHT_PROSE,
                "prose_min_words": PROSE_MIN_WORDS,
                "dense_baseline": DENSE_BASELINE,
            },
            "fastembed_version": DenseEmbedder.fastembed_version(),
            "model_name": self.model_name,
            "model_repo": DenseEmbedder.model_repo(self.model_name),
            "model_cache_dir": self.model_cache_dir,
            "model_present": DenseEmbedder.model_present(self.model_name, self.model_cache_dir),
            "embedder_available": bool(embedder and embedder.available),
            "embedder_reason": (embedder.reason if embedder else f"{EMBEDDINGS_ENV_VAR}=off (default): dense embeddings disabled"),
            "embedding_dim": (embedder.dim if embedder else None),
            "embedding_coverage": covered,
            "embedding_cache_size": len(self._vector_cache),
            "embeddings_sidecar": sidecar,
            "embeddings_sidecar_exists": os.path.exists(sidecar),
            "embeddings_sidecar_bytes": os.path.getsize(sidecar) if os.path.exists(sidecar) else 0,
            "encoded_last_run": self._encoded_last_run,
            "reused_last_run": self._reused_last_run,
        }

    def add_documents(self, docs: List[Dict[str, Any]]) -> None:
        self.documents = docs
        self.idf, tokenized_docs = compute_idf(self.documents)
        self.doc_vectors = build_doc_vectors(tokenized_docs, self.idf)
        self._build_embeddings()
        self._save()

    def _build_embeddings(self) -> None:
        self._encoded_last_run = 0
        self._reused_last_run = 0
        self.doc_embeddings = None

        embedder = self.embedder
        if embedder is None or not embedder.available or not self.documents:
            return
        np = embedder._np

        texts = [self._embed_text(doc) for doc in self.documents]
        hashes = [self._content_hash(t) for t in texts]

        missing_idx = [i for i, h in enumerate(hashes) if h not in self._vector_cache]
        self._reused_last_run = len(hashes) - len(missing_idx)

        if missing_idx:
            encoded = embedder.encode([texts[i] for i in missing_idx])
            if encoded is None:
                return
            for slot, i in enumerate(missing_idx):
                self._vector_cache[hashes[i]] = encoded[slot]
            self._encoded_last_run = len(missing_idx)

        try:
            self.doc_embeddings = np.vstack([self._vector_cache[h] for h in hashes]).astype(np.float32)
        except Exception:
            self.doc_embeddings = None
            return

        live = set(hashes)
        self._vector_cache = {h: v for h, v in self._vector_cache.items() if h in live}

    def _dense_scores(self, query: str):
        embedder = self.embedder
        if embedder is None or not embedder.available or self.doc_embeddings is None:
            return None
        if len(self.doc_embeddings) != len(self.documents):
            return None
        q = embedder.encode([query])
        if q is None:
            return None
        np = embedder._np
        cosines = np.dot(self.doc_embeddings, q[0])
        rescaled = (cosines - DENSE_BASELINE) / (1.0 - DENSE_BASELINE)
        return np.clip(rescaled, 0.0, 1.0)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        if not self.documents or not query:
            return []

        q_vec = query_vector(query, self.idf)
        tfidf_scores = [cosine_similarity(q_vec, d_vec) for d_vec in self.doc_vectors]
        dense_scores = self._dense_scores(query)

        if dense_scores is None or self.backend != BACKEND_HYBRID:
            scored = list(zip(self.documents, tfidf_scores))
        else:
            w = self._dense_weight(query)
            scored = []
            for i, doc in enumerate(self.documents):
                fused = (1.0 - w) * tfidf_scores[i] + w * float(dense_scores[i])
                scored.append((doc, fused))

        scored.sort(key=lambda item: -item[1])
        return [(doc, score) for doc, score in scored[:top_k] if score > 0.0]

    def _save(self) -> None:
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump({
                "format_version": INDEX_FORMAT_VERSION,
                "documents": self.documents,
                "idf": self.idf,
                "doc_vectors": self.doc_vectors,
                "embeddings": {
                    "model": self.model_name,
                    "dim": (self.embedder.dim if self.embedder else None),
                    "count": len(self._vector_cache),
                    "sidecar": os.path.basename(self.embeddings_sidecar_path()),
                },
            }, f)
        self._loaded_format_version = INDEX_FORMAT_VERSION
        self._save_embeddings()

    def _save_embeddings(self) -> None:
        if self.policy == POLICY_OFF:
            return
        path = self.embeddings_sidecar_path()
        if not self._vector_cache:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        embedder = self.embedder
        if embedder is None or embedder._np is None:
            return
        np = embedder._np
        hashes = sorted(self._vector_cache)
        try:
            matrix = np.vstack([self._vector_cache[h] for h in hashes]).astype(np.float32)
            np.savez_compressed(
                path,
                hashes=np.array(hashes),
                vectors=matrix,
                model=np.array(self.model_name),
            )
        except Exception:
            pass

    def _load_embeddings(self, expected_model: Optional[str]) -> None:
        if self.policy == POLICY_OFF:
            return
        path = self.embeddings_sidecar_path()
        if not os.path.exists(path):
            return
        if expected_model and expected_model != self.model_name:
            return
        embedder = self.embedder
        if embedder is None or embedder._np is None:
            return
        np = embedder._np
        try:
            with np.load(path, allow_pickle=False) as data:
                hashes = [str(h) for h in data["hashes"]]
                vectors = data["vectors"].astype(np.float32)
            if len(hashes) != len(vectors):
                return
            self._vector_cache = {h: vectors[i] for i, h in enumerate(hashes)}
        except Exception:
            self._vector_cache = {}

    def _load(self) -> None:
        self._loaded_format_version = None
        if not os.path.exists(self.index_path):
            return
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return

        version = data.get("format_version")
        if version != INDEX_FORMAT_VERSION:
            self._loaded_format_version = version if isinstance(version, int) else 1
            return

        self._loaded_format_version = version
        self.documents = data.get("documents", [])
        self.idf = data.get("idf", {})
        self.doc_vectors = data.get("doc_vectors", [])

        meta = data.get("embeddings") or {}
        self._load_embeddings(meta.get("model") if isinstance(meta, dict) else None)
        if self._vector_cache and self.documents:
            self._build_embeddings_from_cache()

    def _build_embeddings_from_cache(self) -> None:
        embedder = self.embedder
        if embedder is None or not embedder.available:
            return
        np = embedder._np
        hashes = [self._content_hash(self._embed_text(doc)) for doc in self.documents]
        if any(h not in self._vector_cache for h in hashes):
            return
        try:
            self.doc_embeddings = np.vstack([self._vector_cache[h] for h in hashes]).astype(np.float32)
        except Exception:
            self.doc_embeddings = None
