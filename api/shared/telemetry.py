"""Phase 7 — match_events telemetry.

Fire-and-forget recorder for every discover invocation. Failures here MUST
NEVER affect the user response: this is observability, not a transaction.

Schema (one doc per discover call):
    {
      id:           "<userId>__<companyId>__<unix_ms>",
      userId:       str,                    # partition key
      companyId:    str,
      searchId:     str | None,
      timestamp:    ISO8601 UTC,
      scrapedCount: int,
      filteredCount: int,
      matchedCount:  int,
      topJobIds:    [str, ...]   # at most 10
      topScores:    [int, ...]   # at most 10
      rerankModel:  str | None,
      durationMs:   int,
      weights:      { skill, title, location, experience, recency, semantic },
      region:       str | None,
      modelVersion: str | None,
      ttl:          int (seconds, defaults to 30 days)
    }

Disable globally with env var: MATCH_EVENTS_DISABLE=1
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

CONTAINER_NAME = "match_events"
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _disabled() -> bool:
    return os.environ.get("MATCH_EVENTS_DISABLE", "").strip() in {"1", "true", "TRUE", "yes"}


def _safe_str(v: Any) -> str:
    try:
        s = str(v)
    except Exception:
        return ""
    return s[:128]


def _safe_list(items: Iterable[Any], cap: int = 10) -> list:
    out = []
    for i, v in enumerate(items or []):
        if i >= cap:
            break
        out.append(v)
    return out


def build_event(
    *,
    user_id: str,
    company_id: str,
    matches: list[dict] | None,
    scraped_count: int = 0,
    filtered_count: int = 0,
    duration_ms: int = 0,
    rerank_model: str | None = None,
    weights: dict | None = None,
    region: str | None = None,
    search_id: str | None = None,
    model_version: str | None = None,
) -> dict:
    """Pure builder — no I/O, easy to unit-test."""
    matches = matches or []
    top = matches[:10]
    now_ms = int(time.time() * 1000)
    iso_ts = datetime.now(timezone.utc).isoformat()

    # id collision-resistant even if same (user, company, ms) hits twice
    short = uuid.uuid4().hex[:6]
    doc_id = f"{_safe_str(user_id)}__{_safe_str(company_id)}__{now_ms}__{short}"

    return {
        "id": doc_id,
        "userId": _safe_str(user_id),
        "companyId": _safe_str(company_id),
        "searchId": _safe_str(search_id) if search_id else None,
        "timestamp": iso_ts,
        "scrapedCount": int(scraped_count or 0),
        "filteredCount": int(filtered_count or 0),
        "matchedCount": len(matches),
        "topJobIds": _safe_list([m.get("id") or m.get("jobId") or "" for m in top]),
        "topScores": _safe_list(
            [int(round(m.get("score", 0))) if isinstance(m.get("score"), (int, float)) else 0 for m in top]
        ),
        "rerankModel": _safe_str(rerank_model) if rerank_model else None,
        "durationMs": int(duration_ms or 0),
        "weights": dict(weights or {}),
        "region": _safe_str(region) if region else None,
        "modelVersion": _safe_str(model_version) if model_version else None,
        "ttl": DEFAULT_TTL_SECONDS,
    }


def record(**kwargs) -> None:
    """Fire-and-forget. Never raises. Returns None.

    Accepts the same kwargs as build_event(). Logs at WARNING on failure
    so we can spot Cosmos issues in App Insights without breaking discover.
    """
    if _disabled():
        return
    try:
        evt = build_event(**kwargs)
    except Exception:
        log.warning("match_events: build failed", exc_info=True)
        return

    try:
        # Local import — avoid pulling cosmos at module load (keeps tests offline-fast).
        from .cosmos_client import upsert_item

        upsert_item(CONTAINER_NAME, evt)
    except Exception:
        log.warning(
            "match_events: write failed (user=%s company=%s)",
            evt.get("userId"),
            evt.get("companyId"),
            exc_info=True,
        )
