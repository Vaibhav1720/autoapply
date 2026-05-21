"""Job-board harvester: timer-triggered cache pre-warming.

Why this exists
---------------
Every user-facing discover request used to fan out to every careers site
live, multiplied by (queries x locations). At scale that gets us blocked
and burns money. This harvester runs on a timer and pre-fills the same
Cosmos scrape cache used by `_scrape_company_cached`, so the request
path becomes a Cosmos point-read instead of an outbound HTTP call.

Run cadence
-----------
Default cron is every 30 min, well inside the 2h cache TTL so a refresh
always lands before the previous entry expires. Tunable via
`HARVEST_CRON` env var. Also exposed manually at
`POST /api/v1/admin/harvest`.

Coverage policy
---------------
We don't (and cannot) harvest every possible (company, query, location)
combo — there are millions. Instead we cover the head of the
distribution: every selected company x a small list of popular role
queries x a small list of popular locations. Long-tail user queries
still fall through to the live scraper if `SCRAPE_LIVE_FETCH_ENABLED`
is true.
"""

from __future__ import annotations

import concurrent.futures
import os
import time
from datetime import datetime, timezone

import azure.functions as func

from shared.career_scraper import COMPANIES, scrape_company
from shared.cosmos_client import upsert_item
from shared.exceptions import AuthorizationError
from shared.response_helpers import error_response, internal_error_response, success_response

from services._runtime import (
    ADMIN_API_TOKEN,
    _SCRAPE_CACHE_TTL_SECONDS,
    _scrape_cache_key,
    logger,
)

bp = func.Blueprint()


# ── Tunables ────────────────────────────────────────────────────────────────
# Cron expression — Azure Functions NCRONTAB format ("sec min hour ..").
# Default = every 30 min, on the minute.
HARVEST_CRON = os.environ.get("HARVEST_CRON", "0 */30 * * * *")

# Concurrency for the harvest fan-out. Keep modest so we don't hammer
# upstream sites or blow the Function App's outbound SNAT pool.
HARVEST_MAX_WORKERS = int(os.environ.get("HARVEST_MAX_WORKERS", "4"))

# Per-call sleep to space out outbound requests (seconds).
HARVEST_REQUEST_GAP = float(os.environ.get("HARVEST_REQUEST_GAP", "0.5"))

# Hard wall-clock cap so a slow/blocked site can't run the whole harvest
# past the next scheduled tick.
HARVEST_BUDGET_SECONDS = int(os.environ.get("HARVEST_BUDGET_SECONDS", "1500"))  # 25 min


# Top role queries — head of distribution across our user base. Override via
# `HARVEST_QUERIES` env var (comma-separated) without redeploy.
_DEFAULT_QUERIES = [
    "software engineer",
    "data scientist",
    "machine learning",
    "product manager",
    "frontend",
    "backend",
    "fullstack",
    "devops",
    "android",
    "ios",
    "sre",
    "security engineer",
]
HARVEST_QUERIES: list[str] = [
    q.strip() for q in os.environ.get(
        "HARVEST_QUERIES", ",".join(_DEFAULT_QUERIES)
    ).split(",") if q.strip()
]

# Top locations. Empty string is intentional — many APIs use it as
# "any location" and most of our cached entries today are keyed on "".
_DEFAULT_LOCATIONS = [
    "",
    "Bangalore",
    "Hyderabad",
    "India",
    "Mumbai",
    "Pune",
    "Remote",
    "San Francisco",
    "New York",
    "Seattle",
    "London",
    "Toronto",
]
HARVEST_LOCATIONS: list[str] = [
    l.strip() for l in os.environ.get(
        "HARVEST_LOCATIONS", ",".join(_DEFAULT_LOCATIONS)
    ).split(",")
]


# ── Internal helpers ────────────────────────────────────────────────────────

def _harvest_one(company_id: str, query: str, location: str) -> tuple[str, int, str | None]:
    """Run one (company, query, location) harvest and write the result to
    Cosmos. Returns (company_id, n_jobs, error_or_none).
    """
    try:
        jobs = scrape_company(company_id, query=query, location=location)
    except Exception as e:  # scraping is best-effort; log + continue.
        return (company_id, 0, str(e))

    try:
        upsert_item("jobs", {
            "id": _scrape_cache_key(company_id, query, location),
            "companyId": company_id,
            "kind": "scrape_cache",
            "query": query,
            "location": location,
            "jobs": jobs,
            "cachedAt": datetime.now(timezone.utc).isoformat(),
            "harvestedBy": "timer",
            "ttl": _SCRAPE_CACHE_TTL_SECONDS,
        })
    except Exception as e:
        return (company_id, len(jobs), f"cache_write_failed: {e}")
    return (company_id, len(jobs), None)


def _run_harvest(company_ids: list[str] | None = None,
                 queries: list[str] | None = None,
                 locations: list[str] | None = None) -> dict:
    """Core harvest loop. Returns a summary dict suitable for logging or
    HTTP response.
    """
    started = datetime.now(timezone.utc)
    deadline = time.time() + HARVEST_BUDGET_SECONDS

    cids = company_ids or list(COMPANIES.keys())
    qs = queries or HARVEST_QUERIES
    locs = locations or HARVEST_LOCATIONS

    tasks = [(cid, q, l) for cid in cids for q in qs for l in locs]
    total = len(tasks)
    logger.info("[HARVEST] starting — %d companies x %d queries x %d locs = %d tasks",
                len(cids), len(qs), len(locs), total)

    completed = 0
    errors = 0
    jobs_written = 0
    skipped_budget = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=HARVEST_MAX_WORKERS) as pool:
        # Submit gradually with a small inter-submission gap so we don't
        # punch the upstream sites with a single thunderclap of requests.
        futures = []
        for cid, q, loc in tasks:
            if time.time() >= deadline:
                skipped_budget = total - len(futures)
                logger.warning("[HARVEST] budget exhausted; skipping %d remaining tasks",
                               skipped_budget)
                break
            futures.append(pool.submit(_harvest_one, cid, q, loc))
            if HARVEST_REQUEST_GAP > 0:
                time.sleep(HARVEST_REQUEST_GAP)

        for fut in concurrent.futures.as_completed(futures):
            try:
                cid, n, err = fut.result()
            except Exception as e:
                errors += 1
                logger.warning("[HARVEST] task crashed: %s", e)
                continue
            completed += 1
            jobs_written += n
            if err:
                errors += 1
                logger.info("[HARVEST] %s err=%s", cid, err)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    summary = {
        "companies": len(cids),
        "queries": len(qs),
        "locations": len(locs),
        "tasks_total": total,
        "tasks_completed": completed,
        "tasks_skipped_budget": skipped_budget,
        "jobs_written": jobs_written,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info("[HARVEST] done — %s", summary)
    return summary


# ── Triggers ────────────────────────────────────────────────────────────────

@bp.schedule(schedule=HARVEST_CRON, arg_name="timer", run_on_startup=False,
             use_monitor=True)
def harvest_scrape_cache(timer: func.TimerRequest) -> None:
    """Timer-triggered harvest of the scrape cache."""
    try:
        _run_harvest()
    except Exception as e:  # never let the function crash the host
        logger.exception("[HARVEST] unhandled error: %s", e)


@bp.route(route="api/v1/admin/harvest", methods=["POST"])
def trigger_harvest(req: func.HttpRequest) -> func.HttpResponse:
    """Manual trigger. Body (all optional):
        { "companyIds": [...], "queries": [...], "locations": [...] }
    """
    try:
        if not ADMIN_API_TOKEN or req.headers.get("X-Admin-Token") != ADMIN_API_TOKEN:
            raise AuthorizationError("Admin token required")
        body = req.get_json() if req.get_body() else {}
        summary = _run_harvest(
            company_ids=body.get("companyIds"),
            queries=body.get("queries"),
            locations=body.get("locations"),
        )
        return success_response(summary)
    except AuthorizationError as e:
        return error_response(e)
    except Exception as e:
        logger.exception("[HARVEST] manual trigger failed")
        return internal_error_response(str(e))
