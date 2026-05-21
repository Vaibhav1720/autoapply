"""Health, admin, and timer endpoints."""

from datetime import datetime, timezone

import azure.functions as func

from shared.cosmos_client import query_items
from shared.exceptions import AppException, AuthorizationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from shared.career_scraper import _API_SCRAPERS
from shared.embeddings import generate_embeddings_batch, job_to_text

from services._runtime import (
    ADMIN_API_TOKEN,
    PREWARM_CRON,
    _cache_job_embeddings,
    _get_cached_job_embeddings,
    logger,
)

bp = func.Blueprint()


@bp.route(route="api/v1/health", methods=["GET"])
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    return success_response({
        "status": "healthy",
        "service": "autoapply-v2",
        "version": "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@bp.schedule(schedule=PREWARM_CRON, arg_name="timer", run_on_startup=False,
             use_monitor=True)
def prewarm_job_embeddings(timer: func.TimerRequest) -> None:
    """Nightly sweep that pre-populates the job embedding cache."""
    logger.info("[PREWARM] Starting nightly job-embedding prewarm")
    started = datetime.now(timezone.utc)
    total_jobs = 0
    total_new = 0
    total_companies = 0
    total_failed = 0

    for company_id, scraper in _API_SCRAPERS.items():
        try:
            jobs = scraper("", "")
        except Exception as e:
            logger.warning("[PREWARM] %s scrape failed: %s", company_id, e)
            total_failed += 1
            continue
        if not jobs:
            continue
        total_jobs += len(jobs)
        total_companies += 1

        jobs = jobs[:50]

        try:
            cached = _get_cached_job_embeddings(company_id, jobs)
        except Exception as e:
            logger.warning("[PREWARM] %s cache-read failed: %s", company_id, e)
            cached = {}

        missing_idx = [i for i in range(len(jobs)) if i not in cached]
        if not missing_idx:
            logger.info("[PREWARM] %s: %d jobs all cached, skipping", company_id, len(jobs))
            continue

        try:
            missing_texts = [job_to_text(jobs[i]) for i in missing_idx]
            new_embeddings = generate_embeddings_batch(missing_texts)
            _cache_job_embeddings(company_id, [jobs[i] for i in missing_idx], new_embeddings)
            total_new += sum(1 for e in new_embeddings if e)
            logger.info("[PREWARM] %s: %d new embeddings cached (%d already cached)",
                        company_id, len(missing_idx), len(cached))
        except Exception as e:
            logger.warning("[PREWARM] %s embed/cache failed: %s", company_id, e)
            total_failed += 1

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info("[PREWARM] Complete in %.1fs — companies=%d jobs=%d new_embeddings=%d failures=%d",
                elapsed, total_companies, total_jobs, total_new, total_failed)


@bp.route(route="api/v1/admin/prewarm", methods=["POST"])
def trigger_prewarm(req: func.HttpRequest) -> func.HttpResponse:
    """Admin-only manual trigger for the embedding prewarm."""
    if not ADMIN_API_TOKEN or req.headers.get("X-Admin-Token") != ADMIN_API_TOKEN:
        return error_response("UNAUTHORIZED", "Invalid admin token", 401)
    try:
        prewarm_job_embeddings(None)  # type: ignore[arg-type]
        return success_response({"status": "prewarm_complete"})
    except Exception as e:
        return internal_error_response(e)


@bp.route(route="api/v1/admin/reports/skills", methods=["POST"])
def admin_skills_report(req: func.HttpRequest) -> func.HttpResponse:
    """B2B anonymized aggregate report. Requires X-Admin-Token header."""
    try:
        token = req.headers.get("X-Admin-Token", "")
        if not ADMIN_API_TOKEN or token != ADMIN_API_TOKEN:
            raise AuthorizationError("Admin token required")

        body = req.get_json() if req.get_body() else {}
        city_filter = (body.get("city") or "").strip().lower()
        min_exp = int(body.get("minExp") or 0)
        max_exp = int(body.get("maxExp") or 99)
        top_n = int(body.get("topN") or 100)

        rows = query_items("profiles", "SELECT c.skills, c.experience, c.preferences FROM c")

        from collections import Counter
        skill_counter: Counter = Counter()
        title_counter: Counter = Counter()
        city_counter: Counter = Counter()
        sample_size = 0

        for r in rows:
            prefs = r.get("preferences") or {}
            exp_years = prefs.get("experienceYears", 0) or 0
            if exp_years < min_exp or exp_years > max_exp:
                continue
            locs = [str(l).lower() for l in (prefs.get("locations") or [])]
            if city_filter and not any(city_filter in l for l in locs):
                continue
            sample_size += 1
            for s in (r.get("skills") or {}).get("technical", []) or []:
                if s:
                    skill_counter[str(s).strip().lower()] += 1
            for e in r.get("experience") or []:
                if isinstance(e, dict) and e.get("title"):
                    title_counter[str(e["title"]).strip().lower()] += 1
            for l in locs:
                city_counter[l] += 1

        return success_response({
            "report": "candidate-skills-aggregate",
            "filter": {"city": city_filter or None, "minExp": min_exp, "maxExp": max_exp},
            "sampleSize": sample_size,
            "topSkills": skill_counter.most_common(top_n),
            "topTitles": title_counter.most_common(top_n),
            "topCities": city_counter.most_common(50),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "license": "Internal use only — selling without prior agreement is prohibited.",
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Skills report error")
        return internal_error_response(str(e))
