"""
Local Vector Store for CodeChakra (100% offline, $0 token cost).

What this module actually does, per configuration
-------------------------------------------------
There are exactly two retrieval backends, and :attr:`LocalVectorStore.backend`
always names the one that is really live. Nothing here ever silently claims a
capability it does not have -- run ``codechakra doctor`` to see the truth.

``tfidf``   (the default, and the only backend with no extra dependencies)
    Pure TF-IDF cosine over Python dicts. No model, no ONNX, no numpy. This is
    genuinely excellent at *exact identifier retrieval* -- resolving the string
    ``"ApplicationsService"`` or ``"calc.ts"`` to the node that owns it -- which
    is what bridge resolution in ``graph_loader`` and ``cli`` depends on.

``hybrid``  (opt-in; needs the ``embeddings`` extra AND a locally present model)
    TF-IDF **plus** dense embeddings from a FastEmbed (ONNX) model, fused into a
    single score. TF-IDF is kept as the lexical half on purpose: dense vectors
    are *worse* than TF-IDF at exact symbol lookup, and their cosine scores live
    in a completely different distribution. The dense half exists to answer
    natural-language intent queries that share no tokens with their target.

Fusion (only in ``hybrid``)
---------------------------
::

    dense_norm = clamp((cosine - DENSE_BASELINE) / (1 - DENSE_BASELINE), 0, 1)
    w          = DENSE_WEIGHT_PROSE if query looks like a sentence
                 else DENSE_WEIGHT_IDENTIFIER
    fused      = (1 - w) * tfidf + w * dense_norm

Two things are load-bearing here.

**The affine rescale.** Raw cosines from a sentence embedding model sit around
0.45-0.65 even for *unrelated* text, so fusing them directly would add a large
constant to every document and destroy the meaning of any absolute score floor.
``DENSE_BASELINE`` is the measured "unrelated text" cosine for this corpus;
subtracting it puts the dense half back on a 0..1 scale where 0 really does mean
"no signal".

**The weight is chosen per query shape.** A bare identifier and an English
question are different retrieval problems, and one fixed blend serves neither.
Identifier queries stay lexical-dominant so exact symbol lookup -- and therefore
bridge resolution -- behaves exactly as it always did; prose queries go
dense-dominant, which is the only configuration where embeddings actually earn
their keep. See :data:`DENSE_WEIGHT_IDENTIFIER` / :data:`DENSE_WEIGHT_PROSE`.

Because the fused score is a different quantity from a raw TF-IDF cosine, the
score floor used for bridge resolution is **per backend** -- see
:data:`SCORE_FLOORS`. A single shared floor would be meaningless.

Enablement policy
-----------------
Controlled by ``CODECHAKRA_EMBEDDINGS`` (or the ``embeddings=`` constructor
argument):

``off`` (default)
    Pure TF-IDF. Never imports fastembed, never touches the network.

``auto``
    Use dense embeddings only if fastembed imports **and** the model is already
    present in the local cache. Never downloads anything. Falls back to
    ``tfidf`` silently and completely if either condition fails.

``on``
    Force dense embeddings, permitting a one-time model download (~67 MB). Still
    falls back to ``tfidf`` if the model cannot be obtained.

The default is ``off`` rather than ``auto`` deliberately: enabling fusion changes
the ranking every existing caller sees, so turning it on is a decision the
operator makes, not a side effect of having a package installed.
"""

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Index format
# --------------------------------------------------------------------------- #

#: Bumped whenever the on-disk shape of ``vector_index.json`` changes.
#: v1 = {documents, idf, doc_vectors}. v2 adds format_version + embedding
#: metadata and a sidecar ``*.embeddings.npz``. An index written by an older
#: version is discarded and rebuilt rather than raising.
INDEX_FORMAT_VERSION = 2

# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

#: Lexical only. No optional dependency, no model.
BACKEND_TFIDF = "tfidf"
#: Lexical + dense, fused. Requires the ``embeddings`` extra and a local model.
BACKEND_HYBRID = "hybrid"

#: Default FastEmbed model. 384-dim, ~67 MB quantized ONNX.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

#: Env var selecting the enablement policy: off | auto | on.
EMBEDDINGS_ENV_VAR = "CODECHAKRA_EMBEDDINGS"

#: Where the ONNX model is cached. A stable per-user path, because fastembed's
#: own default lives in the system temp directory and evaporates on reboot.
MODEL_CACHE_ENV_VAR = "CODECHAKRA_MODEL_CACHE"

POLICY_OFF = "off"
POLICY_AUTO = "auto"
POLICY_ON = "on"

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSEY = {"0", "false", "no", "off", "disable", "disabled", ""}

# --------------------------------------------------------------------------- #
# Fusion constants
# --------------------------------------------------------------------------- #

#: A query of this many whitespace-separated words or more is treated as prose.
#: Bridge resolution always passes a bare symbol / filename / table name, so it
#: lands on the identifier side by construction.
PROSE_MIN_WORDS = 3

#: Weight of the dense half, BY QUERY SHAPE. The two retrieval jobs this store
#: serves are different problems and a single blend serves neither well:
#:
#:   identifier ("ApplicationsService", "calc.ts", "pension_cases")
#:       Exact symbol lookup. TF-IDF is the better retriever here and must stay
#:       authoritative; dense only nudges otherwise-tied candidates. Measured on
#:       the real repository, 0.15 leaves all 20 distinct agent call targets
#:       resolving to the byte-identical node TF-IDF picks.
#:
#:   prose ("where is the commutation amount calculated")
#:       Intent matching. TF-IDF here scores on incidental stopword-ish overlap
#:       ("where" -> caseScopeWhere(), "amount" -> amountInWords()); dense is the
#:       only half that understands the question. 0.60 was chosen from a sweep of
#:       0.30 / 0.60 / 0.75: 0.30 barely moves the ranking, 0.75 starts washing
#:       out genuine lexical anchors.
DENSE_WEIGHT_IDENTIFIER = 0.15
DENSE_WEIGHT_PROSE = 0.60

#: Cosine below which dense similarity is treated as no signal at all.
#:
#: MEASURED, not guessed. On a 2403-node real repository with this model:
#:   * 99% of all query-document cosines fall at or below 0.570
#:     (p50 0.467, p95 0.536, p99 0.570) for deliberately off-domain queries;
#:   * the median off-domain query's BEST document still scores 0.572;
#:   * real agent call targets reach a best-document cosine of 0.671 (min) /
#:     0.768 (median), and in-domain natural-language queries 0.626 / 0.677.
#: 0.60 sits above the bulk of the noise and below every real match, so an
#: unrelated document contributes exactly zero rather than a large constant.
#:
#: The distributions do still OVERLAP in the tail: a well-formed but off-domain
#: query can reach 0.655 on its best document. That is precisely why the dense
#: half is weight-capped (see DENSE_WEIGHT_IDENTIFIER / DENSE_WEIGHT_PROSE) and
#: is never allowed to decide a match on its own.
DENSE_BASELINE = 0.60

#: Minimum score a match must reach before it may create a cross-layer bridge
#: edge, PER BACKEND. A floor tuned for TF-IDF cosines is meaningless against a
#: fused score built from a differently-scaled quantity, so the two are separate.
#:
#: The hybrid floor is *derived*, not tuned. Bridge resolution always issues an
#: identifier-shaped query, so its lexical weight is ``1 - DENSE_WEIGHT_IDENTIFIER``.
#: A document that exactly meets the TF-IDF floor while receiving zero dense
#: signal therefore fuses to ``0.85 * 0.35 = 0.297``. Cutting there makes hybrid
#: neither more nor less permissive than TF-IDF in the worst case, and strictly
#: less permissive for anything the dense half also dislikes.
#:
#: Verified against measured top-1 scores on the real 2403-node repository:
#:   true agent call targets  lexical min 0.427 -> hybrid min 0.363  (passes)
#:   gibberish / off-domain   lexical 0.000     -> hybrid 0.000      (rejected)
SCORE_FLOORS = {
    BACKEND_TFIDF: 0.35,
}
SCORE_FLOORS[BACKEND_HYBRID] = round(
    (1.0 - DENSE_WEIGHT_IDENTIFIER) * SCORE_FLOORS[BACKEND_TFIDF], 3
)

#: Minimum score for a document to appear in results at all.
MIN_RESULT_SCORE = 0.01


def _resolve_policy(value: Optional[str]) -> str:
    """Normalizes an ``embeddings=`` argument / env value to off|auto|on."""
    if value is None:
        value = os.environ.get(EMBEDDINGS_ENV_VAR, POLICY_OFF)
    if isinstance(value, bool):
        return POLICY_ON if value else POLICY_OFF
    text = str(value).strip().lower()
    if text == POLICY_AUTO:
        return POLICY_AUTO
    if text in _TRUTHY:
        return POLICY_ON
    if text in _FALSEY:
        return POLICY_OFF
    return POLICY_OFF


def default_model_cache_dir() -> str:
    """Stable, per-user cache directory for the ONNX model."""
    override = os.environ.get(MODEL_CACHE_ENV_VAR) or os.environ.get("FASTEMBED_CACHE_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "codechakra", "models")


# --------------------------------------------------------------------------- #
# Dense embedding backend (optional)
# --------------------------------------------------------------------------- #

#: Process-wide cache of loaded models, keyed by (model_name, cache_dir). Loading
#: an ONNX session costs ~1s, and several stores may exist in one process.
_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}


class DenseEmbedder:
    """
    Thin wrapper over ``fastembed.TextEmbedding``.

    Every failure mode -- fastembed not installed, numpy not installed, model not
    downloaded, download impossible offline, ONNX session refusing to start --
    resolves to ``available == False`` with a human-readable ``reason``. Nothing
    here ever raises into the caller; the store simply stays on TF-IDF.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL,
                 cache_dir: Optional[str] = None, allow_download: bool = False):
        self.model_name = model_name
        self.cache_dir = cache_dir or default_model_cache_dir()
        self.allow_download = allow_download
        self.reason = ""
        self.dim: Optional[int] = None
        self._model = None
        self._np = None
        self._load()

    # -- capability probes -------------------------------------------------

    @staticmethod
    def fastembed_version() -> Optional[str]:
        try:
            import fastembed  # noqa: F401
        except Exception:
            return None
        return getattr(fastembed, "__version__", "unknown")

    @classmethod
    def model_repo(cls, model_name: str = DEFAULT_EMBEDDING_MODEL) -> Optional[str]:
        """HuggingFace repo id backing *model_name*, or None if unknown."""
        try:
            from fastembed import TextEmbedding
            for desc in TextEmbedding._list_supported_models():
                if desc.model == model_name:
                    return getattr(desc.sources, "hf", None)
        except Exception:
            return None
        return None

    @classmethod
    def model_present(cls, model_name: str = DEFAULT_EMBEDDING_MODEL,
                      cache_dir: Optional[str] = None) -> bool:
        """
        Is the model already on disk? Pure filesystem inspection -- this must
        never hit the network, because it is what the ``auto`` policy uses to
        decide whether it is allowed to proceed.
        """
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
            for root, _dirs, files in os.walk(path):
                if any(f.endswith(".onnx") for f in files):
                    return True
        return False

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        try:
            import numpy
        except Exception as exc:  # pragma: no cover - numpy ships with fastembed
            self.reason = f"numpy unavailable: {exc}"
            return
        self._np = numpy

        try:
            from fastembed import TextEmbedding
        except Exception:
            self.reason = ("fastembed not installed "
                           "(pip install 'codechakra[embeddings]')")
            return

        present = self.model_present(self.model_name, self.cache_dir)
        if not present and not self.allow_download:
            self.reason = (f"model {self.model_name} not in {self.cache_dir} and "
                           f"downloading is disabled under this policy")
            return

        os.makedirs(self.cache_dir, exist_ok=True)
        key = (self.model_name, self.cache_dir)
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            self._model = cached
        else:
            try:
                kwargs: Dict[str, Any] = {
                    "model_name": self.model_name,
                    "cache_dir": self.cache_dir,
                }
                if not self.allow_download:
                    # Hard guarantee against a surprise 67 MB download.
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

    # -- encoding ----------------------------------------------------------

    def encode(self, texts: Sequence[str]):
        """L2-normalized float32 matrix of shape (len(texts), dim), or None."""
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


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #

class LocalVectorStore:
    def __init__(self, index_path: str = ".codechakra/vector_index.json",
                 embeddings: Optional[str] = None,
                 model_name: str = DEFAULT_EMBEDDING_MODEL,
                 model_cache_dir: Optional[str] = None):
        self.index_path = index_path
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        self.documents: List[Dict[str, Any]] = []
        self.vocabulary: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_vectors: List[Dict[str, float]] = []

        self.policy = _resolve_policy(embeddings)
        self.model_name = model_name
        self.model_cache_dir = model_cache_dir or default_model_cache_dir()

        #: hash -> 1-D float32 vector, reused across scans so unchanged nodes are
        #: never re-encoded.
        self._vector_cache: Dict[str, Any] = {}
        #: (n_documents, dim) matrix aligned with ``self.documents``, or None.
        self.doc_embeddings = None
        self._embedder: Optional[DenseEmbedder] = None
        self._encoded_last_run = 0
        self._reused_last_run = 0

        self._load()

    # ------------------------------------------------------------------ #
    # Backend identity
    # ------------------------------------------------------------------ #

    @property
    def embedder(self) -> Optional[DenseEmbedder]:
        """Lazily built. Under ``off`` this stays None and fastembed is never imported."""
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
        """
        The backend that is *actually* live right now -- not the one that was
        requested. ``hybrid`` requires a working embedder AND a populated
        embedding matrix covering the current documents.
        """
        embedder = self.embedder
        if embedder is None or not embedder.available:
            return BACKEND_TFIDF
        if self.doc_embeddings is None or len(self.doc_embeddings) != len(self.documents):
            return BACKEND_TFIDF
        return BACKEND_HYBRID

    @property
    def score_floor(self) -> float:
        """Bridge-resolution score floor calibrated for the live backend."""
        return SCORE_FLOORS[self.backend]

    # ------------------------------------------------------------------ #
    # Text preparation
    # ------------------------------------------------------------------ #

    def _tokenize(self, text: str) -> List[str]:
        # Tokenize code identifiers (CamelCase, snake_case, slash/dot paths).
        # NOTE: the split must happen BEFORE lowercasing -- lowering first destroys
        # the CamelCase boundaries and leaves every identifier as one opaque token.
        words = re.findall(r'[A-Za-z0-9_]+', text)
        split_words = []
        for w in words:
            # [A-Z]+(?![a-z]) keeps acronyms whole: AAO, PPO, DEO, HTTP
            sub = re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+', w)
            split_words.extend([s.lower() for s in sub if len(s) > 1])
            split_words.append(w.lower())
        return split_words

    @staticmethod
    def _doc_text(doc: Dict[str, Any]) -> str:
        """Everything that should be searchable for a document, including the
        LLM/agent supplied intent and fields."""
        fields = doc.get("fields") or []
        if isinstance(fields, (list, tuple)):
            fields = " ".join(str(f) for f in fields)
        return " ".join(str(part) for part in (
            doc.get("label", ""),
            doc.get("layer", ""),
            doc.get("file", ""),
            doc.get("content", ""),
            doc.get("summary", ""),
            doc.get("intent", ""),
            fields,
        ) if part)

    @staticmethod
    def _humanize(identifier: str) -> str:
        """
        ``"CaseWorkflowService"`` -> ``"Case Workflow Service"``.

        A sentence-embedding model was trained on prose, not on
        ``getPensionCaseById``. Splitting identifiers into words before encoding
        is the single largest quality win available on the dense side; without it
        most symbol names tokenize into meaningless subword soup.
        """
        parts = re.findall(r'[A-Za-z0-9_]+', identifier or "")
        words: List[str] = []
        for part in parts:
            words.extend(re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+', part) or [part])
        return " ".join(words)

    def _embed_text(self, doc: Dict[str, Any]) -> str:
        """
        Natural-language rendering of a document, for the dense half only.

        Deliberately different from :meth:`_doc_text`: identifiers and paths are
        broken into words, and the prose (intent / summary) is what dominates.
        """
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

    # ------------------------------------------------------------------ #
    # Indexing
    # ------------------------------------------------------------------ #

    def add_documents(self, docs: List[Dict[str, Any]]):
        """
        doc: { "id": str, "label": str, "layer": str, "file": str, "content": str, "summary": str }
        """
        self.documents = docs
        self._build_index()
        self._build_embeddings()
        self._save()

    def _build_index(self):
        doc_count = len(self.documents)
        if doc_count == 0:
            return

        term_doc_freq = {}
        tokenized_docs = []

        for doc in self.documents:
            full_text = self._doc_text(doc)
            tokens = self._tokenize(full_text)
            tokenized_docs.append(tokens)
            unique_terms = set(tokens)
            for t in unique_terms:
                term_doc_freq[t] = term_doc_freq.get(t, 0) + 1

        self.idf = {t: math.log((doc_count + 1) / (df + 1)) + 1 for t, df in term_doc_freq.items()}

        self.doc_vectors = []
        for tokens in tokenized_docs:
            vec = {}
            for t in tokens:
                vec[t] = vec.get(t, 0) + 1
            # Apply TF-IDF and normalize
            length = 0.0
            for t, count in vec.items():
                tf = 1 + math.log(count)
                weight = tf * self.idf.get(t, 1.0)
                vec[t] = weight
                length += weight * weight
            length = math.sqrt(length) or 1.0
            for t in vec:
                vec[t] /= length
            self.doc_vectors.append(vec)

    def _build_embeddings(self) -> None:
        """
        Fills ``self.doc_embeddings`` for the current documents.

        Vectors are keyed by a hash of the *embedding text*, so a rescan
        re-encodes only the nodes whose searchable content actually changed.
        On a ~2400 node repository a no-op rescan encodes zero documents.
        """
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

        # Bound cache growth: drop vectors for content nobody indexes any more.
        live = set(hashes)
        self._vector_cache = {h: v for h, v in self._vector_cache.items() if h in live}

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def _lexical_scores(self, query: str) -> Dict[int, float]:
        """TF-IDF cosine of *query* against every document, by document index."""
        query_tokens = self._tokenize(query)
        q_vec: Dict[str, float] = {}
        for t in query_tokens:
            q_vec[t] = q_vec.get(t, 0) + 1

        q_length = 0.0
        for t, count in q_vec.items():
            tf = 1 + math.log(count)
            weight = tf * self.idf.get(t, 1.0)
            q_vec[t] = weight
            q_length += weight * weight
        q_length = math.sqrt(q_length) or 1.0
        for t in q_vec:
            q_vec[t] /= q_length

        scores: Dict[int, float] = {}
        for i, doc_vec in enumerate(self.doc_vectors):
            score = sum(val * q_vec.get(t, 0.0) for t, val in doc_vec.items())
            if score > 0.0:
                scores[i] = score
        return scores

    @staticmethod
    def _dense_weight(query: str) -> float:
        """
        Fusion weight for the dense half, from the *shape* of the query.

        Prose ("how does a pension case get approved") gets the dense-dominant
        weight; anything shorter is treated as an identifier lookup and stays
        lexical-dominant. Bridge resolution only ever passes single symbols, so
        it is always on the identifier branch.
        """
        words = (query or "").split()
        return DENSE_WEIGHT_PROSE if len(words) >= PROSE_MIN_WORDS else DENSE_WEIGHT_IDENTIFIER

    def _dense_scores(self, query: str):
        """
        Rescaled dense similarity per document index, or None when not hybrid.

        The rescale maps the measured "unrelated text" cosine to 0 so that the
        dense half contributes real signal rather than a constant offset.
        """
        embedder = self.embedder
        if embedder is None or not embedder.available or self.doc_embeddings is None:
            return None
        if len(self.doc_embeddings) != len(self.documents):
            return None
        q = embedder.encode([query])
        if q is None:
            return None
        np = embedder._np
        cosines = self.doc_embeddings @ q[0]
        span = 1.0 - DENSE_BASELINE
        return np.clip((cosines - DENSE_BASELINE) / span, 0.0, 1.0)

    def search(self, query: str, top_k: int = 8, layer_filter: str = None) -> List[Tuple[Dict[str, Any], float]]:
        """
        Ranked ``(document, score)`` pairs.

        Under ``tfidf`` the score is a plain TF-IDF cosine, exactly as it always
        was. Under ``hybrid`` it is the fused score documented at module level.
        Compare it against :attr:`score_floor`, never against a hardcoded
        constant -- the two backends are not on the same scale.
        """
        if not self.doc_vectors or not self.documents:
            return []

        lexical = self._lexical_scores(query)
        dense = self._dense_scores(query)

        scores: List[Tuple[Dict[str, Any], float]] = []
        if dense is None:
            for i, score in lexical.items():
                doc = self.documents[i]
                if layer_filter and doc.get("layer") != layer_filter:
                    continue
                if score > MIN_RESULT_SCORE:
                    scores.append((doc, score))
        else:
            w_dense = self._dense_weight(query)
            w_lex = 1.0 - w_dense
            for i, doc in enumerate(self.documents):
                if layer_filter and doc.get("layer") != layer_filter:
                    continue
                fused = w_lex * lexical.get(i, 0.0) + w_dense * float(dense[i])
                if fused > MIN_RESULT_SCORE:
                    scores.append((doc, fused))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    # ------------------------------------------------------------------ #
    # Diagnostics (backs `codechakra doctor`)
    # ------------------------------------------------------------------ #

    def embeddings_sidecar_path(self) -> str:
        stem = self.index_path[:-5] if self.index_path.endswith(".json") else self.index_path
        return stem + ".embeddings.npz"

    def diagnostics(self) -> Dict[str, Any]:
        """Everything ``doctor`` needs, with no claim that is not verified here."""
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
            "embedder_reason": (embedder.reason if embedder else
                                f"{EMBEDDINGS_ENV_VAR}=off (default): dense embeddings disabled"),
            "embedding_dim": (embedder.dim if embedder else None),
            "embedding_coverage": covered,
            "embedding_cache_size": len(self._vector_cache),
            "embeddings_sidecar": sidecar,
            "embeddings_sidecar_exists": os.path.exists(sidecar),
            "embeddings_sidecar_bytes": os.path.getsize(sidecar) if os.path.exists(sidecar) else 0,
            "encoded_last_run": self._encoded_last_run,
            "reused_last_run": self._reused_last_run,
        }

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _save(self):
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
        """
        Writes the hash -> vector cache to a float32 ``.npz`` sidecar.

        A sidecar rather than the JSON on purpose: 2400 x 384 float32 is 3.7 MB
        raw, which as JSON floats would be ~20 MB of text to parse on every
        single command.
        """
        if self.policy == POLICY_OFF:
            # A TF-IDF-only run must not destroy vectors a previous hybrid run
            # paid ~11s to compute. Leaving the sidecar in place is safe: it is
            # keyed by content hash, so anything that changed meanwhile simply
            # misses the cache and gets re-encoded when hybrid is next enabled.
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
        """
        Restores the vector cache. Silently gives up on any mismatch -- a cache
        from a different model is worse than no cache, because its vectors are
        not comparable with freshly encoded ones.
        """
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

    def _load(self):
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
            # An index from an older CodeChakra. Do NOT raise and do NOT try to
            # interpret it: leave the store empty so the next add_documents()
            # rebuilds it from scratch.
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
        """Reassembles the document matrix from a restored cache, encoding nothing."""
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
