"""Phase 6 — embedding-based discipline classifier (opt-in fallback).

The keyword maps in `_DISCIPLINE_TITLE_TOKENS` and `_DISCIPLINE_SKILL_TOKENS`
in `career_scraper.py` work well for ~90% of titles, but they miss generic
phrases like "Software Specialist II" or "Member of Technical Staff" and
they can't catch domain pivots in the JD body.

This module provides `disciplines_for_text(text) -> set[str]` which:
  1. Lazily computes per-discipline anchor embeddings on first call (cached
     for the process lifetime — anchors are static).
  2. Returns disciplines whose cosine similarity to the input text exceeds
     `DISCIPLINE_EMBED_THRESHOLD` (default 0.55).
  3. Returns the empty set on any failure — callers MUST treat this as a
     soft hint, never a hard truth.

OFF BY DEFAULT in production. Enable with `DISCIPLINE_EMBED_ENABLE=1`.
The keyword path is always primary; embeddings only augment when keywords
return nothing.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "data" / "discipline_anchors.json"
_CACHE_LOCK = threading.Lock()
_anchor_vectors: dict[str, list[float]] | None = None
_anchor_load_failed = False


def _enabled() -> bool:
    return os.environ.get("DISCIPLINE_EMBED_ENABLE", "").strip() in {"1", "true", "TRUE", "yes"}


def _threshold() -> float:
    try:
        return float(os.environ.get("DISCIPLINE_EMBED_THRESHOLD", "0.55"))
    except ValueError:
        return 0.55


def _load_anchor_texts() -> dict[str, str]:
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        anchors = raw.get("anchors", {}) or {}
        return {k: v for k, v in anchors.items() if isinstance(v, str) and v.strip()}
    except FileNotFoundError:
        log.warning("discipline_embeddings: anchors file missing at %s", _DATA_PATH)
        return {}
    except Exception:
        log.warning("discipline_embeddings: anchor load failed", exc_info=True)
        return {}


def _ensure_anchor_vectors() -> dict[str, list[float]] | None:
    """Lazy, thread-safe. Returns None on any failure."""
    global _anchor_vectors, _anchor_load_failed
    if _anchor_vectors is not None:
        return _anchor_vectors
    if _anchor_load_failed:
        return None
    with _CACHE_LOCK:
        if _anchor_vectors is not None:
            return _anchor_vectors
        if _anchor_load_failed:
            return None
        anchors = _load_anchor_texts()
        if not anchors:
            _anchor_load_failed = True
            return None
        try:
            # Local import keeps this module testable without pulling Azure SDKs.
            from .embeddings import generate_embeddings_batch

            keys = list(anchors.keys())
            texts = [anchors[k] for k in keys]
            vectors = generate_embeddings_batch(texts, model="text-embedding-3-small")
            if not vectors or len(vectors) != len(keys):
                raise RuntimeError(f"anchor embed returned {len(vectors) if vectors else 0}/{len(keys)}")
            _anchor_vectors = {k: v for k, v in zip(keys, vectors) if v}
            log.info("discipline_embeddings: cached %d anchor vectors", len(_anchor_vectors))
            return _anchor_vectors
        except Exception:
            log.warning("discipline_embeddings: anchor embed failed", exc_info=True)
            _anchor_load_failed = True
            return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


def disciplines_for_text(text: str, threshold: float | None = None) -> set[str]:
    """Return set of disciplines whose anchor cosine ≥ threshold.

    Returns empty set when:
      - feature flag off,
      - text is empty,
      - anchor load/embed failed,
      - text embedding fails.

    Callers MUST treat this as a HINT to layer on top of keyword detection.
    """
    if not _enabled():
        return set()
    text = (text or "").strip()
    if not text:
        return set()
    anchors = _ensure_anchor_vectors()
    if not anchors:
        return set()
    try:
        from .embeddings import generate_embedding

        vec = generate_embedding(text[:2000], model="text-embedding-3-small")
        if not vec:
            return set()
    except Exception:
        log.warning("discipline_embeddings: text embed failed", exc_info=True)
        return set()

    th = threshold if threshold is not None else _threshold()
    out = set()
    for disc, av in anchors.items():
        if _cosine(vec, av) >= th:
            out.add(disc)
    return out


def reset_cache_for_tests() -> None:
    """Test-only helper — clears the anchor cache so tests can re-stub."""
    global _anchor_vectors, _anchor_load_failed
    with _CACHE_LOCK:
        _anchor_vectors = None
        _anchor_load_failed = False
