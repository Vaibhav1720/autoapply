"""Shared runtime: globals, helpers, and constants used across service blueprints.

This module contains code that was previously inlined at the top of function_app.py.
Nothing here has been logically modified — only relocated. Everything is
imported on demand by the service blueprints in services/<domain>/routes.py.
"""

import hashlib
import json
import logging
import math as _math
import os
import re
import uuid
from datetime import date, datetime, timezone

from shared.career_scraper import (
    _API_SCRAPERS,
    bulk_linkedin_for_companies,
    scrape_company,
)
from shared.cosmos_client import query_items, upsert_item
from shared.exceptions import AuthorizationError, ValidationError
from shared.embeddings import (
    cosine_similarity,
    generate_embedding,
    generate_embeddings_batch,
    generate_profile_summary,
    job_to_text,
    profile_to_text,
)

logger = logging.getLogger(__name__)

# ── LLM cost-control config ─────────────────────────────────────────────────
AI_RERANK_MODEL = os.environ.get("AI_RERANK_MODEL", "o4mini")
AI_PARSE_MODEL = os.environ.get("AI_PARSE_MODEL", "gpt41")
AI_REVIEW_MODEL = os.environ.get("AI_REVIEW_MODEL", "gpt4omini")


def _is_modern_model(model_name: str) -> bool:
    """True for o-series and gpt-5-family deployments.

    These models use ``max_completion_tokens`` instead of ``max_tokens`` and
    do not accept a custom ``temperature`` on the chat-completions API.
    Detection is name-based because Azure deployment names are user-chosen
    (e.g. ``gpt5mini``, ``gpt-5-mini``, ``o4mini``).
    """
    n = (model_name or "").lower().replace("-", "").replace("_", "")
    if n.startswith("o") and len(n) >= 2 and n[1].isdigit():
        return True
    if n.startswith("gpt5"):
        return True
    return False

RERANK_SKIP_GAP = int(os.environ.get("RERANK_SKIP_GAP", "999"))
FREE_TIER_DAILY_DISCOVER_LIMIT = int(os.environ.get("FREE_TIER_DAILY_DISCOVER_LIMIT", "2"))
FREE_TIER_DAILY_LINKEDIN_LIMIT = int(os.environ.get("FREE_TIER_DAILY_LINKEDIN_LIMIT", "2"))
FREE_TIER_DAILY_AUTOFILL_LIMIT = int(os.environ.get("FREE_TIER_DAILY_AUTOFILL_LIMIT", "5"))
FREE_TIER_DAILY_TAILOR_LIMIT = int(os.environ.get("FREE_TIER_DAILY_TAILOR_LIMIT", "1"))
FREE_TIER_DAILY_RESUME_UPLOAD_LIMIT = int(
    os.environ.get("FREE_TIER_DAILY_RESUME_UPLOAD_LIMIT", "2")
)
FREE_TIER_COMPANY_LIMIT = int(os.environ.get("FREE_TIER_COMPANY_LIMIT", "5"))
_UPGRADE_MESSAGE_INR = (
    "You've reached your daily free limit. "
    "Upgrade to Premium for just \u20b9199/month \u2014 less than \u20b97/day \u2014 "
    "and unlock unlimited job searches, AI autofill, and resume tailoring. "
    "A small step that could help you land your dream job and change your career path forever."
)
_UPGRADE_MESSAGE_USD = (
    "You've reached your daily free limit. "
    "Upgrade to Premium for just $0.99/week \u2014 "
    "and unlock unlimited job searches, AI autofill, and resume tailoring. "
    "A small step that could help you land your dream job and change your career path forever."
)
# Default (backwards-compat) — code that imports UPGRADE_MESSAGE directly
# gets the INR string so Indian users (majority of current users) see ₹199.
UPGRADE_MESSAGE = _UPGRADE_MESSAGE_INR


def get_upgrade_message(country: str | None = None) -> str:
    """Return the localised upgrade message for the user's country."""
    c = (country or "").strip().upper()
    if c in ("IN", "IND", "INDIA"):
        return _UPGRADE_MESSAGE_INR
    # Unknown or non-India country → USD
    return _UPGRADE_MESSAGE_USD


def get_upgrade_price(country: str | None = None) -> dict:
    """Return localised upgrade price dict for the user's country."""
    c = (country or "").strip().upper()
    if c in ("IN", "IND", "INDIA"):
        return {"amount": 199, "currency": "INR", "period": "month", "provider": "razorpay"}
    return {"amount": 0.99, "currency": "USD", "period": "week", "provider": "lemonsqueezy"}


def get_country_for_billing(req, profile: dict | None = None) -> str:
    """Resolve the user's country for pricing / rate-limit messages.

    Priority:
      1. IP geo-lookup  (authoritative — cannot be spoofed via profile settings)
      2. Profile country (fallback when IP lookup fails, e.g. private/LAN IP in dev)

    Returns an uppercased ISO-3166-1 alpha-2 code ("IN", "US", …) or "".
    """
    try:
        from shared.geoip import country_for_request as _geo
        ip_country = _geo(req)
        if ip_country:
            return ip_country
    except Exception:
        pass

    # Fallback: saved profile country (display preference, NOT authoritative).
    if profile:
        saved = (
            (profile.get("applicationDetails") or {}).get("country") or ""
        ).strip().upper()
        # Normalise long names ("India" → "IN")
        if saved in ("IND", "INDIA"):
            return "IN"
        return saved

    return ""


def get_usage_summary(profile: dict, req=None) -> dict:
    """Return usage info for the frontend (counts + limits + upgrade CTA).

    Pass *req* so the country can be resolved by IP (most accurate) rather than
    the profile field (which the user could set arbitrarily).
    Reads live counts from usage_events container.
    """
    tier = _get_user_tier(profile)
    if _is_premium(profile):
        return {"tier": tier, "limits": None, "usage": {}, "upgradeMessage": None}

    user_id = profile.get("id", "")
    country = get_country_for_billing(req, profile) if req is not None else (
        (profile.get("applicationDetails") or {}).get("country") or ""
    ).strip().upper()

    return {
        "tier": "free",
        "limits": {
            "discovers": FREE_TIER_DAILY_DISCOVER_LIMIT,
            "linkedin": FREE_TIER_DAILY_LINKEDIN_LIMIT,
            "autofill_ai": FREE_TIER_DAILY_AUTOFILL_LIMIT,
            "resume_tailor": FREE_TIER_DAILY_TAILOR_LIMIT,
            "resume_upload": FREE_TIER_DAILY_RESUME_UPLOAD_LIMIT,
            "companies": FREE_TIER_COMPANY_LIMIT,
        },
        "usage": {
            "discovers": _count_usage_events(user_id, "discover"),
            "linkedin": _count_usage_events(user_id, "linkedin"),
            "autofill_ai": _count_usage_events(user_id, "autofill"),
            "resume_tailor": _count_usage_events(user_id, "tailor"),
            "resume_upload": _count_usage_events(user_id, "resume_upload"),
        },
        "upgradeMessage": get_upgrade_message(country),
        "upgradePrice": get_upgrade_price(country),
        "country": country,
    }
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")
# Comma-separated list of emails that get super-admin access to the
# /api/v1/admin/dashboard/* endpoints. Email is matched case-insensitively.
# Configure via the SUPER_ADMIN_EMAILS environment variable (no default).
SUPER_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("SUPER_ADMIN_EMAILS", "").split(",")
    if e.strip()
}
_DISPLAY_GAMMA = float(os.environ.get("DISPLAY_SCORE_GAMMA", "0.5"))
_SCRAPE_CACHE_TTL_SECONDS = int(os.environ.get("SCRAPE_CACHE_TTL_SECONDS", "7200"))
# When True, _scrape_company_cached will NEVER fire a live scrape — misses
# return []. The harvester fills the cache so user requests stay cheap and
# never hit the upstream careers sites. Per-call cache_only=True overrides.
_SCRAPE_LIVE_FETCH_ENABLED = os.environ.get("SCRAPE_LIVE_FETCH_ENABLED", "true").lower() == "true"
PREWARM_CRON = os.environ.get("PREWARM_CRON", "0 0 8 * * *")


def _should_skip_rerank(top_jobs: list[dict]) -> bool:
    """Return True when vector ranking is already confident enough to skip the LLM rerank."""
    scored = [j.get("vectorScore", 0) for j in top_jobs[:5] if j.get("vectorScore") is not None]
    if len(scored) < 5:
        return False
    return (scored[0] - scored[-1]) > RERANK_SKIP_GAP


def _calibrate_score(raw: float) -> int:
    """Apply gamma curve to a 0-100 score for friendlier display."""
    if raw is None or raw <= 0:
        return 0
    if raw >= 100:
        return 100
    return int(round(100 * (raw / 100.0) ** _DISPLAY_GAMMA))


def _check_daily_quota(profile: dict, search_id: str | None = None) -> tuple[bool, int]:
    """Returns (allowed, remaining). Tracks each call in usage_events with 24h TTL."""
    user_id = profile.get("id", "")
    if FREE_TIER_DAILY_DISCOVER_LIMIT <= 0:
        if user_id:
            _record_usage_event(user_id, "discover", search_id)
        return True, -1
    if _is_premium(profile):
        if user_id:
            _record_usage_event(user_id, "discover", search_id)
        return True, -1
    override = profile.get("dailyDiscoverLimit")
    try:
        limit = int(override) if override and int(override) > 0 \
                else FREE_TIER_DAILY_DISCOVER_LIMIT
    except (TypeError, ValueError):
        limit = FREE_TIER_DAILY_DISCOVER_LIMIT

    used = _count_usage_events(user_id, "discover")

    if used >= limit:
        return False, 0

    # Record this call
    _record_usage_event(user_id, "discover", search_id)
    return True, limit - (used + 1)


def _get_user_tier(profile: dict) -> str:
    tier = (
        (profile.get("subscription") or {}).get("tier")
        or profile.get("tier")
        or "free"
    ).lower()
    if tier in ("premium", "pro", "lifetime", "career_plus", "admin"):
        return tier
    email = (profile.get("email") or "").strip().lower()
    if email and email in SUPER_ADMIN_EMAILS:
        return "admin"
    return tier


def _is_premium(profile: dict) -> bool:
    return _get_user_tier(profile) in ("premium", "pro", "lifetime", "career_plus", "admin")


def _check_daily_linkedin_quota(profile: dict) -> tuple[bool, int]:
    """Returns (allowed, remaining) for LinkedIn searches. TTL-based tracking."""
    user_id = profile.get("id", "")
    if FREE_TIER_DAILY_LINKEDIN_LIMIT <= 0 or _is_premium(profile):
        if user_id:
            _record_usage_event(user_id, "linkedin")
        return True, -1
    used = _count_usage_events(user_id, "linkedin")
    if used >= FREE_TIER_DAILY_LINKEDIN_LIMIT:
        return False, 0
    _record_usage_event(user_id, "linkedin")
    return True, FREE_TIER_DAILY_LINKEDIN_LIMIT - (used + 1)


def _check_daily_autofill_quota(profile: dict) -> tuple[bool, int]:
    """Returns (allowed, remaining) for AI autofill suggestions. TTL-based tracking."""
    user_id = profile.get("id", "")
    if FREE_TIER_DAILY_AUTOFILL_LIMIT <= 0 or _is_premium(profile):
        if user_id:
            _record_usage_event(user_id, "autofill")
        return True, -1
    used = _count_usage_events(user_id, "autofill")
    if used >= FREE_TIER_DAILY_AUTOFILL_LIMIT:
        return False, 0
    _record_usage_event(user_id, "autofill")
    return True, FREE_TIER_DAILY_AUTOFILL_LIMIT - (used + 1)


def _check_daily_event_quota(
    profile: dict,
    event_type: str,
    limit: int,
) -> tuple[bool, int]:
    """Generic 24h quota check (usage_events). Records one event when allowed."""
    user_id = profile.get("id", "")
    if limit <= 0 or _is_premium(profile):
        if user_id:
            _record_usage_event(user_id, event_type)
        return True, -1
    used = _count_usage_events(user_id, event_type)
    if used >= limit:
        return False, 0
    _record_usage_event(user_id, event_type)
    return True, limit - (used + 1)


def _check_daily_tailor_quota(profile: dict) -> tuple[bool, int]:
    return _check_daily_event_quota(profile, "tailor", FREE_TIER_DAILY_TAILOR_LIMIT)


def _check_daily_resume_upload_quota(profile: dict) -> tuple[bool, int]:
    return _check_daily_event_quota(
        profile, "resume_upload", FREE_TIER_DAILY_RESUME_UPLOAD_LIMIT
    )


def _check_company_selection_limit(profile: dict, company_ids: list) -> None:
    """Reject free-tier saves above FREE_TIER_COMPANY_LIMIT companies."""
    if _is_premium(profile):
        return
    n = len(company_ids or [])
    if n > FREE_TIER_COMPANY_LIMIT:
        raise ValidationError(
            f"Free accounts can track up to {FREE_TIER_COMPANY_LIMIT} companies. "
            f"You selected {n}. Remove some or upgrade to Pro for unlimited tracking."
        )


def _require_pro_or_paid_resume_review(profile: dict) -> None:
    """Human+AI resume review is Pro-only until paid checkout is wired."""
    if _is_premium(profile):
        return
    raise AuthorizationError(
        "Professional resume review is available on Pro or with a paid review. "
        "Upgrade to Pro from the Pricing page to unlock this feature."
    )


# ── TTL-based usage tracking ────────────────────────────────────────────────

def _record_usage_event(user_id: str, event_type: str, search_id: str | None = None):
    """Insert a usage event doc with 24h TTL into the usage_events container."""
    try:
        doc = {
            "id": str(uuid.uuid4()),
            "userId": user_id,
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "ttl": 86400,  # 24 hours — Cosmos auto-deletes after this
        }
        if search_id:
            doc["searchId"] = search_id
        from shared.cosmos_client import create_item
        create_item("usage_events", doc)
        logger.info("[USAGE] recorded %s for user %s", event_type, user_id[:8])
    except Exception as e:
        logger.warning("[USAGE] failed to record %s event: %s", event_type, e)


def _count_usage_events(user_id: str, event_type: str) -> int:
    """Count usage events for a user+type in the last 24 hours."""
    try:
        results = query_items(
            "usage_events",
            "SELECT VALUE COUNT(1) FROM c WHERE c.userId = @uid AND c.type = @t",
            parameters=[
                {"name": "@uid", "value": user_id},
                {"name": "@t", "value": event_type},
            ],
            partition_key=user_id,
        )
        return results[0] if results else 0
    except Exception as e:
        logger.warning("[USAGE] count query failed for %s/%s: %s", user_id[:8], event_type, e)
        return 0


def _job_cache_key(company_id: str, job: dict) -> str:
    raw = f"{company_id}|{job.get('id','')}|{job.get('title','')}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"emb-{h}"


def _get_cached_job_embeddings(company_id: str, jobs: list[dict]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    if not jobs:
        return out
    keys = [_job_cache_key(company_id, j) for j in jobs]
    try:
        ids_list = ",".join(f"'{k}'" for k in keys)
        rows = query_items(
            "jobs",
            f"SELECT c.id, c.jobEmbedding FROM c WHERE c.id IN ({ids_list})",
            partition_key=company_id,
        )
        by_id = {r["id"]: r.get("jobEmbedding") for r in rows if r.get("jobEmbedding")}
        for i, k in enumerate(keys):
            if k in by_id:
                out[i] = by_id[k]
    except Exception as e:
        logger.warning("[JOB_EMB_CACHE] Read failed for %s: %s", company_id, e)
    return out


def _cache_job_embeddings(company_id: str, jobs: list[dict], embeddings: list[list[float]]) -> None:
    for j, emb in zip(jobs, embeddings):
        if not emb:
            continue
        try:
            upsert_item("jobs", {
                "id": _job_cache_key(company_id, j),
                "companyId": company_id,
                "jobId": j.get("id"),
                "title": j.get("title"),
                "jobEmbedding": emb,
                "cachedAt": datetime.now(timezone.utc).isoformat(),
                "ttl": 86400,
            })
        except Exception as e:
            logger.warning("[JOB_EMB_CACHE] Write failed: %s", e)


def _scrape_cache_key(company_id: str, query: str, location: str) -> str:
    raw = f"scrape|{company_id}|{(query or '').lower().strip()}|{(location or '').lower().strip()}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"scrape-{h}"


def _scrape_company_cached(company_id: str, query: str = "", location: str = "",
                            cache_only: bool | None = None) -> list[dict]:
    """Drop-in replacement for `scrape_company` with a Cosmos cache.

    Cache-first behaviour: a HIT short-circuits and returns the cached jobs.
    On MISS:
      * if ``cache_only`` is True (or the global ``SCRAPE_LIVE_FETCH_ENABLED``
        env flag is false), return [] without touching the upstream careers
        site — the timer-triggered harvester is responsible for keeping the
        cache warm.
      * otherwise, fire the live scraper and write the result to cache.

    The harvester itself bypasses this guard by calling ``scrape_company``
    directly; this helper is only for user-facing request paths.
    """
    key = _scrape_cache_key(company_id, query, location)
    try:
        rows = query_items(
            "jobs",
            f"SELECT TOP 1 c.jobs, c.cachedAt FROM c WHERE c.id = '{key}'",
            partition_key=company_id,
        )
        if rows:
            cached = rows[0].get("jobs")
            if isinstance(cached, list):
                logger.info("[SCRAPE_CACHE] HIT %s q='%s' loc='%s' n=%d",
                            company_id, query, location, len(cached))
                return cached
    except Exception as e:
        logger.warning("[SCRAPE_CACHE] Read failed %s: %s", company_id, e)

    # Miss path — honour cache-only mode so we don't hammer upstream sites.
    effective_cache_only = cache_only if cache_only is not None else not _SCRAPE_LIVE_FETCH_ENABLED
    if effective_cache_only:
        logger.info("[SCRAPE_CACHE] MISS %s q='%s' loc='%s' (cache_only, skipped live)",
                    company_id, query, location)
        return []

    jobs = scrape_company(company_id, query=query, location=location)

    try:
        upsert_item("jobs", {
            "id": key,
            "companyId": company_id,
            "kind": "scrape_cache",
            "query": query,
            "location": location,
            "jobs": jobs,
            "cachedAt": datetime.now(timezone.utc).isoformat(),
            "ttl": _SCRAPE_CACHE_TTL_SECONDS,
        })
    except Exception as e:
        logger.warning("[SCRAPE_CACHE] Write failed %s: %s", company_id, e)
    return jobs


def _ai_rerank_top_jobs(jobs: list[dict], profile: dict, company_id: str) -> list[dict]:
    """Use o-series reasoning model to deeply evaluate top vector-matched jobs."""
    import json as json_mod

    ai_key = os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", ""))
    ai_endpoint = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", ""))
    if not ai_key or not ai_endpoint:
        return jobs

    import openai
    client = openai.AzureOpenAI(
        api_key=ai_key, api_version="2024-12-01-preview",
        azure_endpoint=ai_endpoint, timeout=45.0, max_retries=2)

    skills = (profile.get("skills") or {}).get("technical", [])
    experience = profile.get("experience") or []
    education = profile.get("education") or []
    prefs = profile.get("preferences") or {}
    ai_summary = profile.get("aiSummary", "")
    industry = (prefs.get("industry") or "tech").lower()

    # Resolve effective years of experience for the LLM prompt.
    # Mirrors the logic in match_jobs_to_profile so the rerank model and the
    # filter agree on what level of role to consider. Without this, a user
    # whose UI default `experienceYears=0` was never overridden gets all
    # their mid-level matches HARD DROPPED by the LLM ("0-1 yr -> drop II").
    parsed_resume = (profile.get("documents") or {}).get("parsedResumeData") or {}
    parsed_years = parsed_resume.get("totalYearsExperience") or 0
    explicit_years = prefs.get("experienceYears")
    _real_jobs = [
        e for e in experience
        if isinstance(e, dict) and e.get("title")
        and "intern" not in (e.get("title") or "").lower()
    ]
    if isinstance(explicit_years, (int, float)) and explicit_years > 0:
        effective_years = explicit_years
    elif parsed_years and parsed_years > 0:
        effective_years = parsed_years
    elif _real_jobs:
        effective_years = max(1, int(len(_real_jobs) * 1.5))
    else:
        effective_years = 0

    exp_text = "\n".join(
        f"  - {e.get('title','')} at {e.get('company','')} ({e.get('from','')}-{e.get('to','')}) — {e.get('description','')[:120]}"
        for e in experience[:5] if isinstance(e, dict))
    edu_text = "\n".join(
        f"  - {e.get('degree','')} from {e.get('university','')} ({e.get('year','')})"
        for e in education[:3] if isinstance(e, dict))

    profile_block = (
        f"CANDIDATE PROFILE:\n"
        f"Summary: {ai_summary}\n"
        f"Skills: {', '.join(skills[:40])}\n"
        f"Years of experience: {effective_years}\n"
        f"Looking for: {', '.join(prefs.get('keywords', []))}\n"
        f"Preferred locations: {', '.join(prefs.get('locations', []))}\n"
        f"Experience:\n{exp_text}\n"
        f"Education:\n{edu_text}"
    )

    job_lines = []
    for i, j in enumerate(jobs):
        job_skills = ', '.join(j.get('skills', [])[:12])
        desc_snippet = (j.get('description') or '')[:200]
        line = (
            f"{i+1}. [{j.get('id','')}] {j.get('title','')} at {j.get('company','')}\n"
            f"   Location: {j.get('location','N/A')} | Skills: {job_skills or 'N/A'}\n"
            f"   Vector Score: {j.get('vectorScore',0)} | Keyword Score: {j.get('matchScore',0)}\n"
            f"   Description: {desc_snippet}"
        )
        job_lines.append(line)

    # Industry-aware "off-discipline" rule. The defaults below assume the
    # candidate is in tech (the original behaviour); for finance / healthcare /
    # etc. we invert the list so we drop tech jobs instead of accounting jobs.
    _OFF_DISCIPLINE = {
        "tech": (
            "non-engineering / non-technical roles such as: 'People Partner', "
            "'HR Business Partner', 'Recruiter', 'Talent Acquisition', "
            "'Accountant', 'Controller', 'Auditor', 'Legal Counsel', "
            "'Paralegal', 'Compliance Officer', 'Marketing Manager', "
            "'Brand Manager', 'Sales Executive', 'Account Executive', "
            "'Operations Manager', 'Supply Chain', 'Customer Support', "
            "'Help Desk', 'Technical Writer'"
        ),
        "data_ai": (
            "non-data / non-ML roles, frontend-only roles, sales/marketing, "
            "HR, accounting, customer support"
        ),
        "product_design": (
            "engineering IC roles, sales, accounting, HR, support roles "
            "(unless explicitly hybrid PM/eng or PM/design)"
        ),
        "finance": (
            "software engineering, machine learning engineering, devops, "
            "marketing, sales, HR, customer support, design"
        ),
        "consulting": (
            "narrow IC engineering or design roles, customer support, sales "
            "development reps, HR coordinators"
        ),
        "marketing": (
            "engineering, accounting, finance analyst, legal, HR, supply chain"
        ),
        "healthcare": (
            "software engineering, marketing, sales, finance analyst, HR, legal"
        ),
        "legal": (
            "engineering, marketing, sales, finance analyst, HR, operations"
        ),
        "operations": (
            "software engineering, design, marketing, sales, legal, HR"
        ),
        "hr": (
            "engineering, design, marketing, sales, finance analyst, legal"
        ),
        "education": (
            "sales, marketing, accounting, customer support, supply chain"
        ),
        "manufacturing": (
            "software engineering (web/backend), marketing, sales, HR, legal"
        ),
        "media": (
            "engineering, accounting, finance analyst, supply chain, legal"
        ),
        "government": (
            "sales, marketing, customer support, accounting"
        ),
        "other": (
            "roles clearly outside the candidate's experience as captured in "
            "the resume profile above"
        ),
    }
    off_discipline_text = _OFF_DISCIPLINE.get(industry, _OFF_DISCIPLINE["tech"])

    prompt = (
        f"You are a STRICT senior recruiter screening jobs for ONE candidate "
        f"who is looking for {industry.upper().replace('_', '/')} roles. "
        f"Your job is to be MORE conservative than a junior recruiter — when in doubt, drop. "
        f"It is FAR worse to recommend a job the candidate cannot get than to drop a borderline match.\n\n"
        f"HARD-DROP RULES (set drop=true and score<30):\n"
        f"1. SENIORITY MISMATCH (most common error — be vigilant):\n"
        f"   - Candidate has <8 yrs experience and the title contains 'Architect', 'Principal', "
        f"'Distinguished', 'Fellow', 'Director', 'VP', 'Head of', 'Chief' — HARD DROP.\n"
        f"   - Candidate has <5 yrs and the title contains 'Senior', 'Sr.', 'Staff', 'Lead' — DROP unless "
        f"the description explicitly says '3+ years' or similar.\n"
        f"   - Candidate has 0-1 yr and title indicates a level 2/3 / mid role — HARD DROP.\n"
        f"   - The DESCRIPTION states 'X+ years required' / 'minimum X years' / 'at least X years' "
        f"and (X - candidate_years) > 2 — HARD DROP. Read the description carefully for this.\n"
        f"   - Title is junior/I/entry-level for a candidate with 6+ yrs — HARD DROP "
        f"(below their level).\n"
        f"2. WRONG COUNTRY: candidate has explicit country/city prefs and job is elsewhere "
        f"(unless job says Remote/Multiple/Global).\n"
        f"3. WRONG CITY in same country when candidate listed specific cities. "
        f"Example: candidate wants ONLY Bangalore -> drop Pune/Chennai/Hyderabad/Gurugram. "
        f"Candidate wants ONLY San Francisco/Seattle -> drop Cupertino/Santa Clara/Austin.\n"
        f"4. WRONG DISCIPLINE within the candidate's industry (e.g. Frontend role for a "
        f"backend candidate, Equity Research for an FP&A candidate, ICU Nurse for a "
        f"Pharmacist). Match the resume's actual specialization.\n"
        f"5. MANAGEMENT for IC: 'Manager', 'Director', 'Head of' for a candidate with "
        f"zero management experience in their work history.\n"
        f"6. SPECIALIZED DOMAIN (highly niche tools/protocols/systems) when candidate has "
        f"zero matching skills.\n"
        f"7. OFF-INDUSTRY role: candidate's industry is {industry.upper().replace('_', '/')}. "
        f"HARD DROP {off_discipline_text}. "
        f"Drop these even if the company is a top-tier brand.\n\n"
        f"SCORING (only after passing hard-drop):\n"
        f"  GOOD MATCH (80-95): same discipline, level fits within +/- 1 band, location works "
        f"(preferred city or remote), skills overlap.\n"
        f"  DECENT (60-79): minor stretch on level OR location, but discipline + skills align.\n"
        f"  WEAK (40-59): two weak signals — e.g. right country but wrong city.\n"
        f"  POOR (<30, drop=true): see hard-drop rules.\n\n"
        f"CANDIDATE WAS LOOKING FOR: {', '.join(prefs.get('keywords', [])) or 'unspecified'}.\n"
        f"CANDIDATE PREFERRED LOCATIONS: {', '.join(prefs.get('locations', [])) or 'any'}.\n\n"
        f"{profile_block}\n\n"
        f"JOBS:\n" + "\n".join(job_lines) + "\n\n"
        f"Return ONLY a JSON array. For each job:\n"
        f"[{{\"index\": 1, \"score\": 87, \"drop\": false, \"reason\": \"Strong fit: skills align; mid level matches 4y exp.\"}}]\n"
        f"For each job, your reason MUST mention: (a) the candidate's years vs the role's level, "
        f"(b) skill/discipline alignment, (c) location verdict. "
        f"Be strict. When in doubt, drop. "
        f"Wrong-city same-country jobs MUST score <=40 and drop=true. "
        f"Architect/Principal/Director/VP titles for sub-8yr candidates MUST drop=true."
    )

    logger.info("[AI_RERANK] Sending %d jobs to %s for reasoning re-rank", len(jobs), AI_RERANK_MODEL)

    create_kwargs = {
        "model": AI_RERANK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    if _is_modern_model(AI_RERANK_MODEL):
        create_kwargs["max_completion_tokens"] = 12000
        if AI_RERANK_MODEL.lower().replace("-", "").startswith("gpt5"):
            create_kwargs["reasoning_effort"] = "low"
    else:
        create_kwargs["max_tokens"] = 2000
        create_kwargs["temperature"] = 0.2
    resp = client.chat.completions.create(**create_kwargs)

    raw = resp.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    rankings = json_mod.loads(raw)
    scored_indices: set[int] = set()
    for r in rankings:
        idx = r.get("index", 0) - 1
        if 0 <= idx < len(jobs):
            jobs[idx]["aiReasoningScore"] = r.get("score", 50)
            jobs[idx]["aiReason"] = r.get("reason", "")
            jobs[idx]["aiDrop"] = bool(r.get("drop", False))
            scored_indices.add(idx)

    for i, j in enumerate(jobs):
        if i not in scored_indices and j.get("aiReasoningScore") is None:
            j["aiReasoningScore"] = 30
            # Internal sentinel only — never surface to UI. Flutter falls
            # back to `matchReason` when aiReason is empty, which gives the
            # user a meaningful "Why" string instead of "AI: rerank-missed".
            j["aiReason"] = ""
            j["_aiReasonInternal"] = "rerank-missed"
            j["aiDrop"] = True

    logger.info("[AI_RERANK] %s scored %d/%d jobs, usage: %s",
                AI_RERANK_MODEL, len(rankings), len(jobs), resp.usage)
    return jobs


def _extract_multipart_file(body: bytes, content_type: str) -> bytes:
    """Extract file data from multipart/form-data body."""
    match = re.search(r'boundary=([^\s;]+)', content_type)
    if not match:
        return body

    boundary = match.group(1).encode()
    parts = body.split(b'--' + boundary)

    for part in parts:
        if b'filename=' in part:
            header_end = part.find(b'\r\n\r\n')
            if header_end == -1:
                header_end = part.find(b'\n\n')
                if header_end == -1:
                    continue
                file_content = part[header_end + 2:]
            else:
                file_content = part[header_end + 4:]

            if file_content.endswith(b'\r\n'):
                file_content = file_content[:-2]
            if file_content.endswith(b'--\r\n'):
                file_content = file_content[:-4]
            if file_content.endswith(b'--'):
                file_content = file_content[:-2]

            return file_content

    return body


def _empty_parse_result(reason: str) -> dict:
    logger.warning("[RESUME_PARSE] Returning empty result, reason: %s", reason)
    return {
        "extractedSkills": [],
        "extractedEducation": [],
        "extractedExperience": [],
        "extractedEmail": "",
        "extractedPhone": "",
        "extractedLinkedin": "",
        "extractedGithub": "",
        "totalYearsExperience": 0,
        "parsedAt": datetime.now(timezone.utc).isoformat(),
        "method": f"failed-{reason}",
    }


def _regex_fallback_parse(text: str) -> dict:
    """Last-resort regex extraction when AI credentials are not configured.
    Pulls email/phone/github/linkedin/years of experience using simple patterns.
    Skills/education/experience lists are left empty -- the AI path is the
    only sensible source for those."""
    import re
    blob = text or ""
    email = ""
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob)
    if m:
        email = m.group(0)
    phone = ""
    m = re.search(r"(?:\+\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}", blob)
    if m:
        phone = m.group(0)
    github = ""
    m = re.search(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.-]+", blob, re.I)
    if m:
        github = m.group(0)
    linkedin = ""
    m = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_.-]+", blob, re.I)
    if m:
        linkedin = m.group(0)
    years = 0
    yrs_candidates = []
    for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:years|yrs)\b", blob, re.I):
        try:
            n = int(m.group(1))
            if 0 < n <= 50:
                yrs_candidates.append(n)
        except (ValueError, TypeError):
            pass
    if yrs_candidates:
        years = max(yrs_candidates)
    return {
        "extractedSkills": [],
        "extractedEducation": [],
        "extractedExperience": [],
        "extractedEmail": email,
        "extractedPhone": phone,
        "extractedLinkedin": linkedin,
        "extractedGithub": github,
        "totalYearsExperience": years,
        "parsedAt": datetime.now(timezone.utc).isoformat(),
        "method": "regex-fallback",
    }


def _extract_skills_from_resume(file_data: bytes) -> dict:
    """Extract structured data from resume using AI (GPT-4.1). No regex fallback."""
    import io

    logger.info("[RESUME_PARSE] Starting resume parsing, file size: %d bytes", len(file_data))

    text = ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_data))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages_text)
        logger.info("[RESUME_PARSE] Extracted text from %d PDF pages", len(reader.pages))
    except Exception as e:
        logger.warning("[RESUME_PARSE] PDF parsing failed (%s), falling back to raw decode", e)
        try:
            text = file_data.decode("utf-8", errors="ignore")
        except Exception:
            text = str(file_data)

    text_trimmed = text[:15000].strip()
    logger.info("[RESUME_PARSE] Decoded text length: %d chars, trimmed to: %d chars", len(text), len(text_trimmed))
    logger.info("[RESUME_PARSE] First 200 chars of resume: %s", text_trimmed[:200].replace("\n", " "))

    if len(text_trimmed) < 20:
        logger.warning("[RESUME_PARSE] Resume text too short (%d chars), cannot parse", len(text_trimmed))
        return _empty_parse_result("text_too_short")

    openai_key = os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", ""))
    openai_endpoint = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", ""))

    logger.info("[RESUME_PARSE] AI endpoint: %s", openai_endpoint[:50] if openai_endpoint else "NOT SET")
    logger.info("[RESUME_PARSE] AI key present: %s", "YES" if openai_key else "NO")

    if not openai_key or not openai_endpoint:
        logger.error("[RESUME_PARSE] AI credentials not configured! AZURE_AI_KEY=%s, AZURE_AI_ENDPOINT=%s",
                     "set" if openai_key else "MISSING", openai_endpoint or "MISSING")
        return _regex_fallback_parse(text_trimmed)

    try:
        import openai
        logger.info("[RESUME_PARSE] Creating Azure OpenAI client with endpoint: %s, model: %s",
                    openai_endpoint, AI_PARSE_MODEL)

        client = openai.AzureOpenAI(
            api_key=openai_key,
            api_version="2024-12-01-preview",
            azure_endpoint=openai_endpoint,
        )

        system_prompt = (
            "You are an expert resume parser. Your job is to extract COMPLETE structured data from resume text. "
            "You MUST extract ALL of the following:\n"
            "1. SKILLS: Every technical skill, programming language, framework, tool, platform, database, "
            "cloud service, methodology, and soft skill mentioned anywhere in the resume.\n"
            "2. EDUCATION: Every degree, diploma, or certification. For each, extract the degree name "
            "(include field of study in the degree, e.g. 'B.Tech Computer Science'), the university or "
            "college name, and the graduation year. Do NOT skip any education entry.\n"
            "3. EXPERIENCE: Every job or role. For each, extract the exact job title, company name, "
            "start date (month/year or just year), and end date (month/year, year, or 'Present' if current). "
            "Do NOT skip any experience entry. List them in reverse chronological order.\n"
            "4. PERSONAL: firstName, lastName (split the candidate's full name), email, phone, "
            "linkedin URL, github URL, portfolio URL.\n"
            "5. LOCATION: address (street if shown), city, state, country (FULL country name like 'India' "
            "or 'United States', not codes), zip/postal code. Infer from any address line on the resume.\n"
            "6. SUMMARY: a 2-3 sentence professional summary in the candidate's voice.\n"
            "7. totalYears: Total years of professional work experience (integer).\n"
            "8. coverLetter: A reusable 4-6 sentence default cover letter / 'Why are you a good fit?' "
            "answer in the candidate's voice. Highlight their top skills, years of experience, and "
            "what they bring to a role. Keep it generic enough to reuse across applications — do NOT "
            "mention a specific company or role title.\n\n"
            "Return ONLY valid JSON. No markdown, no explanation, no extra text."
        )

        user_prompt = f"""Parse this resume thoroughly. Extract ALL fields below.
Return ONLY valid JSON in this exact format:
{{
  "firstName": "...",
  "lastName": "...",
  "skills": ["Python", "React", "AWS", ...],
  "education": [
    {{"degree": "B.Tech Computer Science", "university": "IIT Delhi", "year": "2020"}}
  ],
  "experience": [
    {{"title": "Senior Software Engineer", "company": "Google", "from": "Jan 2022", "to": "Present"}}
  ],
  "email": "user@email.com",
  "phone": "+1-555-1234",
  "linkedin": "https://linkedin.com/in/...",
  "github": "https://github.com/...",
  "portfolio": "https://...",
  "address": "",
  "city": "Bangalore",
  "state": "Karnataka",
  "country": "India",
  "zip": "560001",
  "summary": "Software engineer with 5 years building...",
  "totalYears": 5,
  "coverLetter": "I'm a software engineer with 5 years of experience..."
}}

IMPORTANT: Do NOT leave education or experience arrays empty if the resume mentions any degrees or jobs.
IMPORTANT: Always return the FULL country name (e.g. 'India'), never a 2-letter code.
IMPORTANT: If the candidate's name is e.g. 'Vaibhav Badguzar', firstName='Vaibhav' and lastName='Badguzar'.

Resume text:
{text_trimmed}"""

        logger.info("[RESUME_PARSE] Sending AI request to %s...", AI_PARSE_MODEL)
        logger.info("[RESUME_PARSE] System prompt length: %d chars", len(system_prompt))
        logger.info("[RESUME_PARSE] User prompt length: %d chars", len(user_prompt))

        parse_kwargs: dict = {
            "model": AI_PARSE_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if _is_modern_model(AI_PARSE_MODEL):
            parse_kwargs["max_completion_tokens"] = 12000
            if AI_PARSE_MODEL.lower().replace("-", "").startswith("gpt5"):
                parse_kwargs["reasoning_effort"] = "low"
        else:
            parse_kwargs["max_tokens"] = 3000
            parse_kwargs["temperature"] = 0
        resp = client.chat.completions.create(**parse_kwargs)

        raw_response = resp.choices[0].message.content.strip()
        logger.info("[RESUME_PARSE] AI response received, length: %d chars", len(raw_response))
        logger.info("[RESUME_PARSE] AI raw response (first 500 chars): %s", raw_response[:500])

        if "```" in raw_response:
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]
            raw_response = raw_response.strip()
            logger.info("[RESUME_PARSE] Cleaned markdown, now: %d chars", len(raw_response))

        parsed = json.loads(raw_response)

        skills = parsed.get("skills", [])
        education = parsed.get("education", [])
        experience = parsed.get("experience", [])
        email = parsed.get("email", "")
        phone = parsed.get("phone", "")

        logger.info("[RESUME_PARSE] === AI PARSE RESULTS ===")
        logger.info("[RESUME_PARSE] Skills (%d): %s", len(skills), skills)
        logger.info("[RESUME_PARSE] Education (%d): %s", len(education), education)
        logger.info("[RESUME_PARSE] Experience (%d): %s", len(experience), experience)
        logger.info("[RESUME_PARSE] Email: %s, Phone: %s", email, phone)
        logger.info("[RESUME_PARSE] LinkedIn: %s", parsed.get("linkedin", ""))
        logger.info("[RESUME_PARSE] GitHub: %s", parsed.get("github", ""))
        logger.info("[RESUME_PARSE] Total years: %s", parsed.get("totalYears", 0))
        logger.info("[RESUME_PARSE] Method: ai-%s SUCCESS", AI_PARSE_MODEL)

        result = {
            "extractedSkills": skills,
            "extractedEducation": education,
            "extractedExperience": experience,
            "extractedEmail": email,
            "extractedPhone": phone,
            "extractedLinkedin": parsed.get("linkedin", ""),
            "extractedGithub": parsed.get("github", ""),
            "extractedPortfolio": parsed.get("portfolio", ""),
            "extractedFirstName": parsed.get("firstName", ""),
            "extractedLastName": parsed.get("lastName", ""),
            "extractedAddress": parsed.get("address", ""),
            "extractedCity": parsed.get("city", ""),
            "extractedState": parsed.get("state", ""),
            "extractedCountry": parsed.get("country", ""),
            "extractedZip": parsed.get("zip", ""),
            "extractedSummary": parsed.get("summary", ""),
            "extractedCoverLetter": parsed.get("coverLetter", ""),
            "totalYearsExperience": parsed.get("totalYears", 0),
            "parsedAt": datetime.now(timezone.utc).isoformat(),
            "method": f"ai-{AI_PARSE_MODEL}",
        }
        return result

    except json.JSONDecodeError as e:
        logger.error("[RESUME_PARSE] Failed to parse AI response as JSON: %s", e)
        logger.error("[RESUME_PARSE] Raw response was: %s", raw_response[:1000] if 'raw_response' in dir() else "N/A")
        return _empty_parse_result("json_parse_error")

    except Exception as e:
        logger.error("[RESUME_PARSE] AI call failed with error: %s", str(e))
        logger.error("[RESUME_PARSE] Error type: %s", type(e).__name__)
        import traceback
        logger.error("[RESUME_PARSE] Traceback: %s", traceback.format_exc())
        return _empty_parse_result(f"ai_error: {str(e)[:100]}")


# ── Autofill helpers ────────────────────────────────────────────────────────


def _normalize_label(label: str) -> str:
    s = re.sub(r"\s+", " ", (label or "").lower()).strip()
    s = re.sub(r"[*•:?!]+", "", s).strip()
    return s[:120]


_OPTIONAL_QUESTION_KEYS = {
    "address",
    "githubUrl",
    "portfolioUrl",
    "coverLetter",
    "remoteWork",
    "gender",
    "ethnicity",
    "veteranStatus",
    "disability",
    # Not strictly required for job search OR autofill of most forms.
    # Keep them in the form (helpful when present) but don't count them
    # as "missing" against profile completeness.
    "salaryExpectation",
    "noticePeriod",
    "linkedinUrl",
}
_COMMON_QUESTIONS = [
    {"key": "country", "label": "Country of residence", "type": "text"},
    {"key": "city", "label": "City", "type": "text"},
    {"key": "state", "label": "State / Province", "type": "text"},
    {"key": "zip", "label": "Zip / Postal code", "type": "text"},
    {"key": "address", "label": "Street address (optional)", "type": "text"},
    {"key": "visaStatus", "label": "Work authorization status",
     "type": "select", "options": [
         "Citizen", "Permanent Resident", "Work Visa (H1B/L1/etc)",
         "Need Sponsorship", "Student Visa (F1/OPT)", "Other"
     ]},
    {"key": "willingToRelocate", "label": "Willing to relocate?",
     "type": "select", "options": ["Yes", "No", "Maybe"]},
    {"key": "remoteWork", "label": "Open to fully remote work?",
     "type": "select", "options": ["Yes", "No", "Hybrid only"]},
    {"key": "salaryExpectation", "label": "Expected salary (e.g. $120k or 25 LPA)", "type": "text"},
    {"key": "noticePeriod", "label": "Notice period / earliest start date",
     "type": "select", "options": [
         "Immediately", "2 weeks", "1 month", "2 months", "3 months", "Other"
     ]},
    {"key": "gender", "label": "Gender",
     "type": "select", "options": [
         "Male", "Female", "Non-binary", "Prefer not to say"
     ]},
    {"key": "ethnicity", "label": "Race / Ethnicity (US-style forms)",
     "type": "select", "options": [
         "Asian", "Black or African American", "Hispanic or Latino",
         "Native American", "White", "Two or more races", "Prefer not to say"
     ]},
    {"key": "veteranStatus", "label": "Veteran status (US-style forms)",
     "type": "select", "options": [
         "I am not a protected veteran", "I am a protected veteran", "Prefer not to say"
     ]},
    {"key": "disability", "label": "Disability status (US-style forms)",
     "type": "select", "options": [
         "No", "Yes", "Prefer not to say"
     ]},
    {"key": "linkedinUrl", "label": "LinkedIn URL", "type": "text"},
    {"key": "githubUrl", "label": "GitHub URL", "type": "text"},
    {"key": "portfolioUrl", "label": "Portfolio / personal website", "type": "text"},
    {"key": "coverLetter", "label": "Default cover letter / 'Why this role'", "type": "textarea"},
]


_YESNO_LABEL_RE = re.compile(
    r"\b(do you|are you|will you|have you|can you|would you|did you|is there|"
    r"plan to|willing to|able to|require|need)\b", re.IGNORECASE)
_PDF_GARBAGE_RE = re.compile(r"^\s*[\d\.]+(\s+[\d\.]+){2,}\s*$")


def _validate_ai_answer(ans: dict, field: dict) -> dict:
    val = (ans.get("value") or "").strip()
    if not val:
        return ans
    label = (field.get("label") or "").lower()
    ftype = (field.get("type") or "text").lower()
    options = field.get("options") or []

    is_yesno = label.endswith("?") or bool(_YESNO_LABEL_RE.search(label))
    looks_like_pdf_box = bool(_PDF_GARBAGE_RE.match(val))

    if looks_like_pdf_box and ftype not in ("number",):
        ans["value"] = ""
        ans["confidence"] = 0.0
        ans["reasoning"] = "Filtered: looked like resume parsing artifact, not a real answer."
        return ans

    if is_yesno and ftype not in ("number",):
        low = val.lower()
        if low in ("yes", "y", "true", "1"):
            ans["value"] = "Yes"
        elif low in ("no", "n", "false", "0"):
            ans["value"] = "No"
        elif looks_like_pdf_box or any(c.isdigit() for c in val) and not any(c.isalpha() for c in val):
            ans["value"] = ""
            ans["confidence"] = 0.0
            ans["reasoning"] = "Filtered: numeric value supplied for yes/no question."

    if options and val:
        match = None
        low = val.lower()
        for opt in options:
            if opt and opt.lower() == low:
                match = opt
                break
        if not match:
            for opt in options:
                if opt and (low in opt.lower() or opt.lower() in low):
                    match = opt
                    break
        if match:
            ans["value"] = match
        else:
            ans["value"] = ""
            ans["confidence"] = min(ans.get("confidence", 0.5), 0.3)
            ans["reasoning"] = "Filtered: AI answer didn't match any select option."

    return ans


def _match_select_option(value: str, options: list[str]) -> str:
    val_lower = value.lower().strip()
    for opt in options:
        if opt.lower().strip() == val_lower:
            return opt
    for opt in options:
        if val_lower in opt.lower() or opt.lower() in val_lower:
            return opt
    return value


_COUNTRY_CODES = {
    "in": "India", "us": "United States", "usa": "United States", "uk": "United Kingdom",
    "gb": "United Kingdom", "ca": "Canada", "au": "Australia", "de": "Germany",
    "fr": "France", "es": "Spain", "it": "Italy", "nl": "Netherlands", "se": "Sweden",
    "no": "Norway", "dk": "Denmark", "fi": "Finland", "ie": "Ireland", "pt": "Portugal",
    "ch": "Switzerland", "at": "Austria", "be": "Belgium", "pl": "Poland", "cz": "Czechia",
    "jp": "Japan", "cn": "China", "hk": "Hong Kong", "sg": "Singapore", "kr": "South Korea",
    "tw": "Taiwan", "th": "Thailand", "vn": "Vietnam", "ph": "Philippines", "my": "Malaysia",
    "id": "Indonesia", "ae": "United Arab Emirates", "sa": "Saudi Arabia", "il": "Israel",
    "tr": "Turkey", "za": "South Africa", "ng": "Nigeria", "eg": "Egypt", "ke": "Kenya",
    "br": "Brazil", "mx": "Mexico", "ar": "Argentina", "cl": "Chile", "co": "Colombia",
    "nz": "New Zealand", "ru": "Russia", "ua": "Ukraine",
}


def _expand_country(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if len(v) <= 3:
        return _COUNTRY_CODES.get(v.lower(), v)
    return v


def _ai_suggest_fields(profile: dict, fields: list[dict], ai_key: str, ai_endpoint: str) -> list[dict]:
    import openai

    client = openai.AzureOpenAI(
        api_key=ai_key, api_version="2024-12-01-preview",
        azure_endpoint=ai_endpoint)

    personal = profile.get("personal") or {}
    skills = (profile.get("skills") or {}).get("technical", [])
    exp = profile.get("experience") or []
    edu = profile.get("education") or []
    prefs = profile.get("preferences") or {}
    app_det = profile.get("applicationDetails") or {}

    profile_json = json.dumps({
        "name": f"{personal.get('firstName', '')} {personal.get('lastName', '')}".strip(),
        "email": profile.get("email", ""),
        "phone": personal.get("phone", ""),
        "skills": skills[:20],
        "summary": profile.get("aiSummary", "")[:500],
        "experience": [{"title": e.get("title"), "company": e.get("company"), "from": e.get("from"), "to": e.get("to")} for e in exp[:3] if isinstance(e, dict)],
        "education": [{"degree": e.get("degree"), "university": e.get("university"), "year": e.get("year")} for e in edu[:2] if isinstance(e, dict)],
        "preferences": prefs,
        "applicationDetails": app_det,
    }, indent=None)

    fields_desc = json.dumps([
        {"key": f["key"], "label": f.get("label", ""), "type": f.get("type", "text"),
         "options": f.get("options", [])[:20], "maxLength": f.get("maxLength")}
        for f in fields[:20]
    ], indent=None)

    resp = client.chat.completions.create(
        model="gpt41",
        messages=[
            {"role": "system", "content": (
                "You fill job application form fields using ONLY the candidate profile below.\n"
                "Strict rules:\n"
                "  1. Never invent information not in the profile. Set value to '' if unknown.\n"
                "  2. Never return numbers unless the field type is 'number' or the label asks for a count/year/salary.\n"
                "  3. For yes/no questions (label ends with '?' or contains 'do you/are you/will you'), "
                "return exactly 'Yes' or 'No'. Default to 'Yes' for remote-work / relocation / authorization "
                "questions only if the profile preferences clearly support it; otherwise return ''.\n"
                "  4. For <select> fields, the value MUST exactly match one of the provided options.\n"
                "  5. For country fields, return the full country name (e.g. 'India' not 'in').\n"
                "  6. For free-text 'why' / motivation fields, write 1-3 honest sentences from the profile summary; "
                "never repeat the question back as the answer.\n"
                "For EACH field also return a confidence (0.0-1.0) and a one-sentence reasoning. "
                "Use confidence >= 0.8 only when the answer is unambiguously in the profile. "
                "Use confidence < 0.6 (and prefer empty value) when the question is personal/subjective "
                "and not in the profile (e.g. preferred work hours, willingness to commute, salary if not set).\n"
                "Return JSON: {\"answers\": [{\"key\": \"field_key\", \"value\": \"answer\", \"confidence\": 0.0-1.0, \"reasoning\": \"...\"}]}"
            )},
            {"role": "user", "content": f"PROFILE:\n{profile_json}\n\nFIELDS:\n{fields_desc}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=2000, temperature=0)

    raw = resp.choices[0].message.content.strip()
    result = json.loads(raw)
    ai_answers = result.get("answers", [])
    return [{
        "key": a["key"],
        "value": a.get("value", "") or "",
        "source": "openai",
        "confidence": float(a.get("confidence", 0.5) or 0.5),
        "reasoning": a.get("reasoning", "")[:200],
    } for a in ai_answers if isinstance(a, dict) and "key" in a]


def _suggest_answers(profile: dict, fields: list[dict]) -> list[dict]:
    """Generate smart answers for form fields using memory + heuristics + optional AI."""
    personal = profile.get("personal") or {}
    skills = (profile.get("skills") or {}).get("technical", [])
    prefs = profile.get("preferences") or {}
    experience = profile.get("experience") or []
    education = profile.get("education") or []
    app_details = profile.get("applicationDetails") or {}
    custom_answers = app_details.get("customAnswers") or {}

    raw_country = (app_details.get("country", "") or "").strip()
    country_full = _expand_country(raw_country)
    HEURISTIC_MAP = {
        r"first.?name|given.?name|forename": personal.get("firstName", ""),
        r"last.?name|surname|family.?name": personal.get("lastName", ""),
        r"full.?name|^name$": f"{personal.get('firstName', '')} {personal.get('lastName', '')}".strip(),
        r"e.?mail": profile.get("email", ""),
        r"phone|mobile|tel": personal.get("phone", ""),
        r"linkedin": profile.get("linkedinUrl", ""),
        r"github": personal.get("githubUrl", ""),
        r"portfolio|website": personal.get("portfolioUrl", ""),
        r"city": app_details.get("city", ""),
        r"state|province": app_details.get("state", ""),
        r"zip|postal": app_details.get("zip", ""),
        r"country": country_full,
        r"address|street": app_details.get("address", ""),
        r"salary|compensation|pay": app_details.get("salaryExpectation", ""),
        r"notice.?period|start.?date|availability": app_details.get("noticePeriod", ""),
        r"visa|authorization|sponsor": app_details.get("visaStatus", ""),
        r"relocat": app_details.get("willingToRelocate", ""),
        r"gender|sex": app_details.get("gender", ""),
        r"veteran|military": app_details.get("veteranStatus", ""),
        r"disab": app_details.get("disability", ""),
        r"ethni|race": app_details.get("ethnicity", ""),
        r"university|school|college": (education[0].get("university", "") if education and isinstance(education[0], dict) else ""),
        r"degree|qualification": (education[0].get("degree", "") if education and isinstance(education[0], dict) else ""),
        r"graduat|year.*study|completion": str(education[0].get("year", "") if education and isinstance(education[0], dict) else ""),
        r"current.?title|job.?title|position": (experience[0].get("title", "") if experience and isinstance(experience[0], dict) else ""),
        r"current.?company|employer": (experience[0].get("company", "") if experience and isinstance(experience[0], dict) else ""),
        r"years?.?(?:of)?.?experience|experience.?years": str(prefs.get("experienceYears", "")),
        r"skill|technolog|expertise": ", ".join(skills[:20]),
    }

    answers = []
    remaining = []

    _PLACEHOLDER_VALUES = {
        "city", "state", "country", "address", "home", "zip", "postal", "test",
        "n/a", "na", "none", "null", "tbd", "todo", "xxx", "string", "value",
    }

    def _looks_placeholder(v: str) -> bool:
        if not isinstance(v, str):
            return False
        s = v.strip().lower()
        if not s:
            return True
        if s in _PLACEHOLDER_VALUES:
            return True
        return False

    for field in fields:
        key = field.get("key", "")
        label = (field.get("label") or "").lower()
        norm = _normalize_label(label)

        if norm and norm in custom_answers:
            mem_val = custom_answers[norm].get("value", "")
            if mem_val:
                if field.get("options"):
                    mem_val = _match_select_option(mem_val, field["options"])
                answers.append({"key": key, "value": mem_val, "source": "memory",
                                "confidence": 0.99, "reasoning": "You answered this before."})
                continue

        matched = False
        for pattern, value in HEURISTIC_MAP.items():
            if value and re.search(pattern, label):
                if _looks_placeholder(value):
                    break
                if field.get("options") and value:
                    best = _match_select_option(value, field["options"])
                    answers.append({"key": key, "value": best, "source": "heuristic",
                                    "confidence": 0.95, "reasoning": "Matched profile field."})
                else:
                    answers.append({"key": key, "value": value, "source": "heuristic",
                                    "confidence": 0.95, "reasoning": "Matched profile field."})
                matched = True
                break
        if matched:
            continue

        if re.search(r"why|motiv|cover.?letter|interest|about.?you", label):
            cl = app_details.get("coverLetter", profile.get("aiSummary", ""))
            if cl:
                max_len = field.get("maxLength", 2000)
                answers.append({"key": key, "value": cl[:max_len], "source": "heuristic",
                                "confidence": 0.7, "reasoning": "Used your saved cover letter / AI summary."})
                continue
        if re.search(r"summar|overview|objective", label):
            summary = profile.get("aiSummary", "")
            if summary:
                answers.append({"key": key, "value": summary, "source": "heuristic",
                                "confidence": 0.85, "reasoning": "Used your AI summary."})
                continue

        remaining.append(field)

    ai_key = os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", ""))
    ai_endpoint = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", ""))

    if remaining and ai_key and ai_endpoint:
        try:
            ai_answers = _ai_suggest_fields(profile, remaining, ai_key, ai_endpoint)
            field_by_key = {f["key"]: f for f in remaining}
            ai_answers = [_validate_ai_answer(a, field_by_key.get(a["key"], {})) for a in ai_answers]
            answers.extend(ai_answers)
        except Exception as e:
            logger.warning("[AUTOFILL] AI suggest failed: %s", e)
            for f in remaining:
                answers.append({"key": f["key"], "value": "", "source": "none",
                                "confidence": 0.0, "reasoning": "AI unavailable."})
    else:
        for f in remaining:
            answers.append({"key": f["key"], "value": "", "source": "none",
                            "confidence": 0.0, "reasoning": "No matching profile data."})

    for a in answers:
        a.setdefault("confidence", 0.5)
        a.setdefault("reasoning", "")
    return answers
