"""
TF-IDF lexical text processing and vector calculations for LocalVectorStore.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Set, Tuple


def tokenize_code(text: str) -> List[str]:
    """Tokenizes code identifiers (CamelCase, snake_case, paths)."""
    words = re.findall(r"[A-Za-z0-9_]+", text)
    split_words = []
    for w in words:
        sub = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", w)
        split_words.extend([s.lower() for s in sub if len(s) > 1])
        split_words.append(w.lower())
    return split_words


def doc_text(doc: Dict[str, Any]) -> str:
    """Searchable text for a document."""
    input_fields = doc.get("input_fields") or []
    output_fields = doc.get("output_fields") or []
    legacy_fields = doc.get("fields") or []
    calls = doc.get("calls") or []
    all_fields = list(input_fields) + list(output_fields) + (list(legacy_fields) if not input_fields and not output_fields else [])
    fields_str = " ".join(str(f) for f in all_fields)
    calls_str = " ".join(str(c) for c in calls)
    return " ".join(str(part) for part in (
        doc.get("label", ""),
        doc.get("layer", ""),
        doc.get("file", ""),
        doc.get("content", ""),
        doc.get("summary", ""),
        doc.get("intent", ""),
        fields_str,
        calls_str,
    ) if part)


def humanize_identifier(identifier: str) -> str:
    """CaseWorkflowService -> Case Workflow Service."""
    parts = re.findall(r"[A-Za-z0-9_]+", identifier or "")
    words: List[str] = []
    for part in parts:
        words.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", part) or [part])
    return " ".join(words)


def embed_text(doc: Dict[str, Any]) -> str:
    """Prose-like text for dense sentence embedder."""
    label = doc.get("label") or ""
    where = doc.get("file") or ""
    intent = (doc.get("intent") or "").strip()
    summary = (doc.get("summary") or "").strip()
    input_fields = doc.get("input_fields") or []
    output_fields = doc.get("output_fields") or []
    legacy_fields = doc.get("fields") or []
    calls = doc.get("calls") or []
    all_fields = list(input_fields) + list(output_fields) + (list(legacy_fields) if not input_fields and not output_fields else [])
    fields = ", ".join(str(f) for f in all_fields)
    calls_str = ", ".join(str(c) for c in calls[:6])

    parts = [p for p in (
        humanize_identifier(label),
        f"defined in {where}" if where else "",
        intent or summary,
        f"fields: {fields}" if fields else "",
        f"calls: {calls_str}" if calls_str else "",
    ) if p]
    return ". ".join(parts)



def compute_idf(documents: List[Dict[str, Any]]) -> Tuple[Dict[str, float], List[List[str]]]:
    doc_count = len(documents)
    if doc_count == 0:
        return {}, []

    term_doc_freq: Dict[str, int] = {}
    tokenized_docs = []

    for doc in documents:
        tokens = tokenize_code(doc_text(doc))
        tokenized_docs.append(tokens)
        for t in set(tokens):
            term_doc_freq[t] = term_doc_freq.get(t, 0) + 1

    idf = {t: math.log((doc_count + 1) / (df + 1)) + 1 for t, df in term_doc_freq.items()}
    return idf, tokenized_docs


def build_doc_vectors(tokenized_docs: List[List[str]], idf: Dict[str, float]) -> List[Dict[str, float]]:
    doc_vectors = []
    for tokens in tokenized_docs:
        vec: Dict[str, float] = {}
        for t in tokens:
            vec[t] = vec.get(t, 0.0) + 1.0
        length = 0.0
        for t, count in vec.items():
            tf = 1 + math.log(count)
            weight = tf * idf.get(t, 1.0)
            vec[t] = weight
            length += weight * weight
        length = math.sqrt(length) or 1.0
        for t in vec:
            vec[t] /= length
        doc_vectors.append(vec)
    return doc_vectors


def query_vector(query_text: str, idf: Dict[str, float]) -> Dict[str, float]:
    tokens = tokenize_code(query_text)
    if not tokens:
        return {}
    q_vec: Dict[str, float] = {}
    for t in tokens:
        q_vec[t] = q_vec.get(t, 0.0) + 1.0
    length = 0.0
    for t, count in q_vec.items():
        tf = 1 + math.log(count)
        weight = tf * idf.get(t, 1.0)
        q_vec[t] = weight
        length += weight * weight
    length = math.sqrt(length) or 1.0
    for t in q_vec:
        q_vec[t] /= length
    return q_vec


def cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    if len(v1) > len(v2):
        v1, v2 = v2, v1
    return sum(weight * v2.get(t, 0.0) for t, weight in v1.items())
