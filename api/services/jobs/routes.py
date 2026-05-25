"""Jobs — discover (bulk + per-company), LinkedIn search, results retrieval."""

import os
import re
import time
from datetime import datetime, timezone

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.career_scraper import (
    COMPANIES,
    _API_SCRAPERS,
    _attribute_employer,
    _extract_skills,
    _li_bulk_fetch,
    bulk_linkedin_for_companies,
    match_jobs_to_profile,
)
from shared.cosmos_client import read_item, upsert_item, query_items
from shared.telemetry import record as record_match_event
from shared.embeddings import (
    cosine_similarity,
    generate_embedding,
    generate_embeddings_batch,
    generate_profile_summary,
    job_to_text,
    profile_to_text,
)
from shared.exceptions import (
    AppException,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import (
    FREE_TIER_COMPANY_LIMIT,
    FREE_TIER_DAILY_DISCOVER_LIMIT,
    get_upgrade_message,
    get_country_for_billing,
    _ai_rerank_top_jobs,
    _cache_job_embeddings,
    _calibrate_score,
    _check_daily_quota,
    _check_daily_linkedin_quota,
    _get_cached_job_embeddings,
    _is_premium,
    _scrape_company_cached,
    _should_skip_rerank,
    get_usage_summary,
    logger,
)

bp = func.Blueprint()


# ── Search-query fallbacks ─────────────────────────────────────────────────
# A regex of role tokens that strongly imply an engineering search. We use
# this to decide whether to broaden a query with the generic
# "engineer"/"developer" fan-out terms. Without this gate, a search for
# "Product Designer" or "Investment Banking Analyst" gets polluted with
# Software Engineer postings (because the bare fallbacks always get
# appended), which then dominate the matched top-N for engineering-flavored
# resumes.
_ENG_QUERY_RE = re.compile(
    r'\b(engineer|engineering|developer|sde|swe|sdet|programmer|software|'
    r'backend|frontend|fullstack|full[\s\-]?stack|web\s+developer|'
    r'mobile|ios|android|devops|sre|platform|cloud|infrastructure|'
    r'data\s+engineer|machine\s+learning|ml\s+engineer|ai\s+engineer|'
    r'qa\s+engineer|test\s+engineer|automation\s+engineer|'
    r'embedded|firmware|security\s+engineer|systems\s+engineer)\b',
    re.I,
)
# Industries whose users are ALSO engineering candidates (so a missing /
# blank query can safely default to engineer/developer fallbacks).
_ENG_INDUSTRIES = {"", "tech", "data_ai", "manufacturing"}


def _maybe_add_eng_fallbacks(search_queries: list[str], industry: str = "") -> None:
    """Append ('engineer','developer') only when the existing queries already
    look engineering-y, OR when no industry hint distinguishes the search.

    Mutates `search_queries` in place. Safe to call when the list is empty
    (caller is expected to ensure at least one real query exists first).
    """
    industry_eng = (industry or "").strip().lower() in _ENG_INDUSTRIES
    queries_eng = any(_ENG_QUERY_RE.search(q or "") for q in search_queries)
    if not (industry_eng and queries_eng):
        return
    have_lower = {(q or "").lower() for q in search_queries}
    for fallback in ("engineer", "developer"):
        if fallback not in have_lower:
            search_queries.append(fallback)
            have_lower.add(fallback)


# ── Resume-title-based search expansion ────────────────────────────────────
# Instead of hardcoding seniority prefixes (which break across companies —
# Oracle uses SMTS, Google uses SWE III, others use MTS/IC4/etc.), we pull
# the user's ACTUAL job titles from their resume and add them as search
# queries. A 15-YOE person whose resume says "Staff Engineer" — that IS
# the best search term. No nomenclature guessing needed.
#
# This handles every discipline naturally: PM, finance, design, engineering.

def _extract_resume_titles(profile: dict) -> list[str]:
    """Pull distinct job titles from the user's work experience, most
    recent first. Skips internships and deduplicates."""
    experience = profile.get("experience") or []
    titles: list[str] = []
    seen: set[str] = set()
    for e in experience:
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip()
        if not title:
            continue
        tl = title.lower()
        # Skip internships — they're not useful search terms for experienced users
        if "intern" in tl:
            continue
        if tl not in seen:
            seen.add(tl)
            titles.append(title)
    return titles


def _level_qualify_queries(search_queries: list[str], profile: dict,
                           pivot: bool = False) -> bool:
    """Broaden search queries using the user's actual resume titles.

    Strategy:
      1. Pull the user's real job titles from their work history.
      2. Add any that aren't already in the search queries.
      3. This naturally handles all nomenclatures (SMTS, SWE III, MTS,
         IC4, Group PM, etc.) because it uses exactly what the user
         held — no guessing.
      4. Cap total queries to avoid excessive fan-out.

    Career-pivot detection
    ─────────────────────
    When the user's typed query shares NO meaningful token with any resume
    title, they're searching off-discipline (e.g. an SDE typing "phd" or
    "Research Scientist"). Adding resume titles to such a search dilutes
    the pool with irrelevant results that then get dropped by the rerank.
    In that case we silently leave ``search_queries`` alone.

    Set ``pivot=True`` to force-skip resume-title expansion (when the UI
    sends an explicit pivot flag, e.g. industry differs from resume's
    implied industry).

    Mutates ``search_queries`` in place.
    """
    # Source 1: actual job titles from work experience (most recent first)
    resume_titles = _extract_resume_titles(profile)

    # Pivot auto-detection: any meaningful token overlap between
    # user-typed queries and resume titles? If not, the user is searching
    # off-discipline — don't pollute their intent.
    if not pivot and resume_titles and search_queries:
        # Min length 3 so short acronyms (PhD, MBA, CFA, CPA, RN) count.
        _word_re = re.compile(r"[A-Za-z]{3,}")
        # ONLY strip pure English connectives + seniority words on both
        # sides. We deliberately KEEP discipline-defining nouns like
        # "software", "research", "data", "engineer" so that overlapping
        # disciplines (Software Engineer ↔ Senior Software Engineer) still
        # match, but truly different disciplines (Software Engineer ↔ Phd /
        # Research Scientist / Lecturer / Nurse) do not.
        _stop = {
            "the", "and", "for", "with", "from", "into",
            "senior", "junior", "staff", "lead", "principal",
            "intern", "internship", "associate",
            "year", "years", "team",
        }
        q_tokens: set[str] = set()
        for q in search_queries:
            q_tokens.update(w.lower() for w in _word_re.findall(q or ""))
        q_tokens -= _stop
        t_tokens: set[str] = set()
        for t in resume_titles:
            t_tokens.update(w.lower() for w in _word_re.findall(t or ""))
        t_tokens -= _stop
        if q_tokens and t_tokens and not (q_tokens & t_tokens):
            pivot = True
            logger.info(
                "[PIVOT_DETECT] queries=%s share no token with resume titles=%s "
                "— skipping title expansion",
                search_queries, resume_titles,
            )

    if pivot:
        return True

    have_lower = {(q or "").lower() for q in search_queries}
    added: list[str] = []
    for t in resume_titles:
        tl = t.lower()
        if tl not in have_lower:
            added.append(t)
            have_lower.add(tl)

    # Source 2: parsed resume headline / current title (if different)
    parsed = (profile.get("documents") or {}).get("parsedResumeData") or {}
    parsed_title = (parsed.get("currentTitle") or parsed.get("headline") or "").strip()
    if parsed_title:
        ptl = parsed_title.lower()
        if ptl not in have_lower:
            added.append(parsed_title)
            have_lower.add(ptl)

    # Cap: keep the user's explicit queries first, then resume titles.
    # Too many queries = too many scrape pairs = slow. 8 total is generous.
    max_total = int(os.environ.get("MAX_SEARCH_QUERIES", "8"))
    room = max(0, max_total - len(search_queries))
    if added and room > 0:
        search_queries.extend(added[:room])
        logger.info("[RESUME_TITLES] added %d resume-title queries: %s",
                    min(len(added), room), added[:room])
    return pivot


def _resolve_exp_years(profile: dict) -> int | float:
    """Resolve effective experience years from profile, using all available signals."""
    prefs = profile.get("preferences") or {}
    explicit = prefs.get("experienceYears", 0)
    if isinstance(explicit, (int, float)) and explicit > 0:
        return explicit
    parsed = (profile.get("documents") or {}).get("parsedResumeData", {}).get("totalYearsExperience", 0)
    if parsed and parsed > 0:
        return parsed
    exp = profile.get("experience") or []
    real_jobs = [e for e in exp if isinstance(e, dict) and e.get("title")
                 and "intern" not in (e.get("title") or "").lower()]
    if real_jobs:
        return max(1, int(len(real_jobs) * 1.5))
    return 0


# ── Display-name resolver ──────────────────────────────────────────────────
# Single source of truth for converting a `comp-<slug>` id into a string the
# user sees. Any response that ships `company` to the client must run through
# this — historically a few zero-match / scrape-failed paths leaked the raw
# `comp-amazon` id which looks like a bug to end users.
def _company_display_name(company_id: str) -> str:
    """Resolve the friendliest display name for a company id.

    Order of preference:
      1. `COMPANIES[<id>].name` — the canonical brand string from the registry
         (e.g. "Morgan Stanley", "Lowe's India", proper apostrophes & casing).
      2. Slug-derived title-cased fallback for ids the registry doesn't know
         about (`comp-some-newcomer` → "Some Newcomer").
      3. Empty string when the input is empty / non-string / a degenerate
         `comp-` with no slug. Never return a raw `comp-…` to the caller.
    """
    if not company_id or not isinstance(company_id, str):
        return ""
    info = COMPANIES.get(company_id) or {}
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    slug = company_id[5:] if company_id.startswith("comp-") else company_id
    cleaned = slug.replace("-", " ").replace("_", " ").strip().title()
    if cleaned:
        return cleaned
    # Degenerate input (e.g. literal "comp-" or "comp-   "). Don't echo back
    # the raw id — that's exactly the bug we're guarding against.
    return ""


# ── Job results persistence ────────────────────────────────────────────────
# Discover scans hundreds of jobs but historically returned them only in the
# response. We now also persist the scored jobs into Cosmos `job_results` so
# downstream features (resume tailor, insights) can pull from a much larger
# pool than what's currently on the user's screen.

# Cosmos has a 2MB doc limit. Embeddings are 3072 floats (~30KB each) so the
# `job_results` summary doc keeps metadata only. The actual per-job embeddings
# go into the `job_vectors` container (one doc per job, with a Cosmos vector
# index on /embedding) so the resume tailor can use VectorDistance() instead
# of re-embedding everything in Python.
_PERSIST_JOB_FIELDS = (
    "id", "title", "company", "companyId", "location", "url", "applyUrl",
    "snippet", "summary", "description", "requirements", "skills",
    "matchScore", "vectorScore", "aiScore", "aiReasoningScore",
    "aiReasons", "recencyScore", "postedAt", "source",
)
_PERSIST_MAX_JOBS = 250
_PERSIST_DESC_CHARS = 2000


def _slim_job_for_cache(j: dict) -> dict:
    out: dict = {}
    for k in _PERSIST_JOB_FIELDS:
        if k in j and j[k] is not None:
            v = j[k]
            if isinstance(v, str) and len(v) > _PERSIST_DESC_CHARS:
                v = v[:_PERSIST_DESC_CHARS]
            out[k] = v
    return out


def _persist_discover_results(user_id: str, grouped: list[dict], merge: bool = False) -> None:
    """Write a flat, slim list of all scored jobs into `job_results`.

    Best-effort — never raises. Stores only metadata + scores (no embeddings).
    When `merge=True`, merges the new groups into the existing cached doc
    (replacing the matching companyIds) instead of overwriting everything.
    """
    try:
        existing_groups: list[dict] = []
        if merge:
            try:
                prev = read_item("job_results", f"results-{user_id}", user_id) or {}
                existing_groups = prev.get("grouped") or []
            except Exception:
                existing_groups = []
            new_ids = {g.get("companyId") for g in (grouped or []) if g.get("companyId")}
            existing_groups = [g for g in existing_groups
                               if g.get("companyId") not in new_ids]
        merged_groups = existing_groups + list(grouped or [])

        flat: list[dict] = []
        for g in merged_groups:
            for j in (g.get("jobs") or []):
                if isinstance(j, dict):
                    flat.append(_slim_job_for_cache(j))
        flat.sort(key=lambda x: x.get("aiScore", x.get("matchScore", 0)), reverse=True)
        flat = flat[:_PERSIST_MAX_JOBS]
        doc = {
            "id": f"results-{user_id}",
            "userId": user_id,
            "grouped": [
                {
                    "company": g.get("company"),
                    "companyId": g.get("companyId"),
                    "count": g.get("count", 0),
                    "jobs": [_slim_job_for_cache(j) for j in (g.get("jobs") or [])][:50],
                }
                for g in merged_groups
            ],
            "jobs": flat,
            "totalCached": len(flat),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        upsert_item("job_results", doc)
        logger.info("[PERSIST] cached %d jobs across %d companies for %s (merge=%s)",
                    len(flat), len(merged_groups), user_id, merge)
    except Exception as e:
        logger.warning("[PERSIST] job_results upsert failed: %s", e)


# ── Discover run telemetry ─────────────────────────────────────────────────
# One doc per search action (bulk, per-company, LinkedIn). Captures the
# full funnel per company so the admin dashboard can show exact breakdown.
# Stored in `match_events` container with kind="discover_run".
# TTL = 90 days.

def _record_discover_run(
    *,
    user_id: str,
    email: str = "",
    run_type: str,  # "bulk", "company", "linkedin"
    queries: list[str],
    locations: list[str],
    duration_ms: int,
    companies: list[dict],  # [{companyId, company, scraped, locFiltered, matched, vectorScored, reranked, displayed, noResultsReason, error}]
    linkedin_pool_size: int = 0,
) -> None:
    """Fire-and-forget. Never raises."""
    try:
        import uuid
        now = datetime.now(timezone.utc)
        run_id = f"run-{int(now.timestamp())}-{uuid.uuid4().hex[:6]}"
        total_scraped = sum(c.get("scraped", 0) for c in companies)
        total_displayed = sum(c.get("displayed", 0) for c in companies)
        total_matched = sum(c.get("matched", 0) for c in companies)

        doc = {
            "id": run_id,
            "userId": user_id,
            "email": email,
            "kind": "discover_run",
            "runType": run_type,
            "timestamp": now.isoformat(),
            "queries": queries[:10],
            "locations": locations[:10],
            "durationMs": duration_ms,
            "companiesRequested": len(companies),
            "companiesWithResults": sum(1 for c in companies if c.get("displayed", 0) > 0),
            "totalScraped": total_scraped,
            "totalMatched": total_matched,
            "totalDisplayed": total_displayed,
            "keepPct": round(total_displayed / total_scraped * 100, 1) if total_scraped else 0,
            "linkedInPoolSize": linkedin_pool_size,
            "perCompany": [
                {
                    "companyId": c.get("companyId", ""),
                    "company": c.get("company", ""),
                    "scraped": c.get("scraped", 0),
                    "locFiltered": c.get("locFiltered", 0),
                    "matched": c.get("matched", 0),
                    "vectorScored": c.get("vectorScored", 0),
                    "reranked": c.get("reranked", 0),
                    "displayed": c.get("displayed", 0),
                    "error": c.get("error"),
                    "noResultsReason": c.get("noResultsReason"),
                    "durationMs": c.get("durationMs", 0),
                }
                for c in companies[:200]
            ],
            "ttl": 90 * 24 * 60 * 60,  # 90 days
        }
        upsert_item("match_events", doc)
        logger.info("[RUN_TELEMETRY] %s %s user=%s companies=%d scraped=%d displayed=%d in %dms",
                    run_id, run_type, email or user_id, len(companies),
                    total_scraped, total_displayed, duration_ms)
    except Exception as e:
        logger.warning("[RUN_TELEMETRY] write failed: %s", e)


# ── Per-job vector persistence (job_vectors container) ────────────────────
# 30-day TTL — old jobs decay automatically so the vector store stays fresh.
_VECTOR_TTL_SECONDS = 30 * 24 * 60 * 60


def _job_vector_id(user_id: str, job: dict) -> str:
    """Stable doc id per (user, job)."""
    import hashlib
    raw = f"{user_id}|{job.get('companyId','')}|{job.get('id', job.get('url', job.get('title','')))}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"j-{h}"


def _persist_job_vectors(user_id: str, jobs_with_emb: list[tuple[dict, list[float]]]) -> None:
    """Upsert one slim doc per (user, job) into `job_vectors` for vector search.

    Best-effort, never raises. Skips jobs without embeddings.
    """
    written = 0
    for j, emb in jobs_with_emb:
        if not emb or not isinstance(emb, list):
            continue
        try:
            slim = _slim_job_for_cache(j)
            slim.update({
                "id": _job_vector_id(user_id, j),
                "userId": user_id,
                "embedding": emb,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "ttl": _VECTOR_TTL_SECONDS,
            })
            upsert_item("job_vectors", slim)
            written += 1
        except Exception as e:
            logger.warning("[VEC] write failed for job %s: %s", j.get("id"), e)
    if written:
        logger.info("[VEC] persisted %d job vectors for %s", written, user_id)


@bp.route(route="api/v1/jobs/discover/bulk", methods=["POST"])
def discover_bulk(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/jobs/discover/bulk — Discover jobs for ALL selected companies in parallel."""
    import concurrent.futures

    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        # ── Free-tier rate limit ──
        allowed, remaining = _check_daily_quota(profile)
        if not allowed:
            country = get_country_for_billing(req, profile)
            raise RateLimitError(get_upgrade_message(country))

        selected = profile.get("selectedCompanies") or []
        if not selected:
            raise ValidationError("No companies selected")

        # ── Free-tier company cap ──
        if not _is_premium(profile) and len(selected) > FREE_TIER_COMPANY_LIMIT:
            selected = selected[:FREE_TIER_COMPANY_LIMIT]
            logger.info("[BULK] free user %s: capped companies from %d to %d",
                        user_id, len(profile.get("selectedCompanies") or []), FREE_TIER_COMPANY_LIMIT)

        # ── Quarantine known-broken scrapers (env-driven, see docs/BROKEN_SCRAPERS.md) ──
        # Comma-separated list of company ids that consistently fail or hang.
        # Default: comp-meta (metacareers.com static HTML no longer contains
        # job JSON, and the LinkedIn fallback for company 10667 returns [] but
        # blocks a worker for 70-250s, blowing past the Y1 230s response cap).
        _blacklist = {
            cid.strip()
            for cid in os.environ.get("DISCOVER_BLACKLIST", "comp-meta").split(",")
            if cid.strip()
        }
        if _blacklist:
            _before = len(selected)
            selected = [c for c in selected if c not in _blacklist]
            if _before != len(selected):
                logger.info(
                    "[BULK] quarantine: skipped %d blacklisted companies (%s)",
                    _before - len(selected),
                    ",".join(sorted(_blacklist)),
                )

        # ── Skip companies without a native scraper ──
        # Companies that only had LinkedIn-by-name scrapers are now served
        # exclusively via the LinkedIn search tile. Trying to scrape them
        # here would either produce 0 jobs (no _API_SCRAPERS entry) or
        # fire slow per-company LinkedIn fetches that blow the budget.
        _before_native = len(selected)
        selected = [c for c in selected if c in _API_SCRAPERS]
        if _before_native != len(selected):
            logger.info("[BULK] skipped %d companies without native scraper (served via LinkedIn tile)",
                        _before_native - len(selected))

        body = req.get_json() if req.get_body() else {}
        query = body.get("query", "")
        body_queries = [str(q).strip() for q in (body.get("queries") or []) if str(q).strip()]
        body_locations = [str(l).strip() for l in (body.get("locations") or []) if str(l).strip()]
        # Pivot mode: user is intentionally searching off-resume (career
        # change). When set, we skip resume-title query expansion so the
        # pool isn't polluted with the user's old discipline.
        pivot_mode = bool(body.get("pivot"))

        # ── Bulk-level deadline (under Y1 230s response cap) ──
        # We stop dispatching new work and cancel pending futures once this
        # elapses, then persist whatever has finished. Per-company timeout
        # below ensures no single scraper monopolizes a worker.
        bulk_deadline_s = float(os.environ.get("BULK_DEADLINE_S", "190"))
        per_company_timeout_s = float(os.environ.get("BULK_PER_COMPANY_TIMEOUT_S", "45"))
        max_workers = int(os.environ.get("BULK_MAX_WORKERS", "16"))
        bulk_started_at = time.time()

        # ── Pre-warm LinkedIn shared cache (skippable) ──
        # Default OFF: pre-warm is synchronous and historically consumed
        # 30-180s of the 230s budget before any per-company scrape began.
        # Set BULK_DISABLE_PREWARM=0 to re-enable.
        if os.environ.get("BULK_DISABLE_PREWARM", "1") != "1":
            try:
                prefs = profile.get("preferences") or {}
                _locs = [l.strip() for l in (prefs.get("locations") or [])
                         if l and l.strip().lower() not in ("remote", "anywhere")][:3] or [""]
                _qs: list[str] = []
                if query:
                    _qs.append(query)
                for kw in (prefs.get("keywords") or [])[:3]:
                    if kw not in _qs:
                        _qs.append(kw)
                _maybe_add_eng_fallbacks(_qs, industry=(prefs.get("industry") or ""))
                prewarm_pairs = [(q, l) for q in _qs[:3] for l in _locs][:6]
                if prewarm_pairs and selected:
                    logger.info("[BULK] pre-warming LinkedIn cache: %d pairs for %d companies",
                                len(prewarm_pairs), len(selected))
                    for sq, lq in prewarm_pairs:
                        if time.time() - bulk_started_at > bulk_deadline_s * 0.25:
                            logger.warning("[BULK] pre-warm budget exceeded; aborting pre-warm")
                            break
                        try:
                            bulk_linkedin_for_companies(selected, sq, lq)
                        except Exception as e:
                            logger.warning("[BULK] pre-warm failed (%s,%s): %s", sq, lq, e)
            except Exception as e:
                logger.warning("[BULK] pre-warm setup error: %s", e)
        else:
            logger.info("[BULK] pre-warm disabled (BULK_DISABLE_PREWARM=1)")

        def _discover_one(cid):
            _t_one = time.time()
            try:
                prefs = profile.get("preferences") or {}
                locations = prefs.get("locations", []) or []
                if body_locations:
                    locations = body_locations
                location_queries = [l.strip() for l in locations
                                    if l and l.strip().lower() not in ("remote", "anywhere")]
                location_queries = _expand_country_to_cities(location_queries)
                if not location_queries:
                    location_queries = [""]
                location_queries = location_queries[:3]
                keywords = prefs.get("keywords", [])

                search_queries = []
                for q in body_queries:
                    if q not in search_queries:
                        search_queries.append(q)
                if query and query not in search_queries:
                    search_queries.append(query)
                if keywords:
                    for kw in keywords[:3]:
                        if kw not in search_queries:
                            search_queries.append(kw)
                if not search_queries:
                    user_exp = profile.get("experience") or []
                    if user_exp and isinstance(user_exp[0], dict):
                        search_queries.append(user_exp[0].get("title", "software engineer"))
                    else:
                        search_queries.append("software engineer")
                _maybe_add_eng_fallbacks(
                    search_queries, industry=(prefs.get("industry") or ""))
                pivot = _level_qualify_queries(search_queries, profile, pivot=pivot_mode)

                raw_jobs = []
                seen_ids = set()
                for sq in search_queries:
                    for lq in location_queries:
                        try:
                            jobs = _scrape_company_cached(cid, query=sq, location=lq)
                            for j in jobs:
                                jid = j.get("id", j.get("title", ""))
                                if jid not in seen_ids:
                                    seen_ids.add(jid)
                                    raw_jobs.append(j)
                        except Exception:
                            pass

                if locations:
                    raw_jobs = _filter_jobs_by_location(raw_jobs, locations, f"[BULK][{cid}]")

                matched = match_jobs_to_profile(
                    raw_jobs, profile,
                    search_queries=search_queries, pivot=pivot,
                )
                # ── Per-company minimum-yield floor ──
                # Goal: a user who deliberately selected this company should
                # see at least MIN_KEEP_PCT of what we scraped, even when the
                # deterministic filters are aggressive (Google/Uber were
                # consistently under 10%). When matched < target, top up with
                # the highest-matchScore raw jobs that the filter discarded.
                try:
                    min_keep_pct = float(os.environ.get("BULK_MIN_KEEP_PCT", "25"))
                except Exception:
                    min_keep_pct = 25.0
                target = int(round(len(raw_jobs) * (min_keep_pct / 100.0)))
                if target > len(matched) and raw_jobs:
                    have_ids = {j.get("id") for j in matched}
                    extras = [j for j in raw_jobs if j.get("id") not in have_ids]
                    extras.sort(
                        key=lambda x: x.get("matchScore", 0) or 0,
                        reverse=True,
                    )
                    needed = target - len(matched)
                    if extras and needed > 0:
                        for j in extras[:needed]:
                            j.setdefault("matchScore", 0)
                            matched.append(j)
                        logger.info(
                            "[BULK_MIN_KEEP] %s topped up %d -> %d (target %d, scraped %d)",
                            cid, len(matched) - needed, len(matched), target, len(raw_jobs),
                        )
                # Always resolve through the registry so users never see a raw
                # `comp-...` id, even when scraped jobs lack a `company` field.
                company_name = (matched[0].get("company") if matched else None) or _company_display_name(cid)

                from shared.embeddings import EMBEDDING_DIMS
                profile_embedding = profile.get("profileEmbedding", [])
                top_jobs = matched[:50]
                job_embeddings: list[list[float]] = []

                if profile_embedding and len(profile_embedding) == EMBEDDING_DIMS:
                    # 24h embedding cache (job_to_text is deterministic per
                    # company+id+title) — cuts bulk discover embedding cost
                    # roughly proportional to the harvest cache hit rate.
                    cached_emb = _get_cached_job_embeddings(cid, top_jobs)
                    job_embeddings = [cached_emb.get(i) for i in range(len(top_jobs))]
                    missing_idx = [i for i, e in enumerate(job_embeddings) if not e]
                    if missing_idx:
                        try:
                            new_texts = [job_to_text(top_jobs[i]) for i in missing_idx]
                            new_emb = generate_embeddings_batch(new_texts)
                            for i, e in zip(missing_idx, new_emb):
                                job_embeddings[i] = e
                            _cache_job_embeddings(cid,
                                                   [top_jobs[i] for i in missing_idx],
                                                   new_emb)
                        except Exception as e:
                            logger.warning("[BULK] embedding miss-fill failed for %s: %s", cid, e)
                    logger.info("[BULK_EMB] %s: %d cached + %d new embeddings",
                                cid, len(top_jobs) - len(missing_idx), len(missing_idx))

                    for job, emb in zip(top_jobs, job_embeddings):
                        if emb:
                            sim = cosine_similarity(profile_embedding, emb)
                            job["vectorScore"] = round(sim * 100)
                            raw = sim * 100 * 0.5 + job.get("matchScore", 0) * 0.3 + job.get("recencyScore", 50) * 0.2
                            job["aiScore"] = _calibrate_score(raw)
                        else:
                            job["vectorScore"] = 0
                            job["aiScore"] = _calibrate_score(job.get("matchScore", 0))
                    top_jobs.sort(key=lambda x: x.get("aiScore", 0), reverse=True)

                try:
                    _bulk_display_cap = int(os.environ.get("BULK_DISPLAY_CAP", "60"))
                except (TypeError, ValueError):
                    _bulk_display_cap = 60
                display = top_jobs[:_bulk_display_cap]
                # Strip internal-only sentinel before returning to client.
                for _j in display:
                    _j.pop("_aiReasonInternal", None)
                # Surface a friendly reason when this company yielded nothing
                # so the UI can show "No matching openings found at <Company>"
                # instead of a silent empty card.
                no_results_reason = None
                if not display:
                    if not raw_jobs:
                        no_results_reason = "No open roles found for your queries on this company's careers site."
                    elif not matched:
                        no_results_reason = f"Found {len(raw_jobs)} openings, but none matched your profile / location preferences."
                    else:
                        no_results_reason = f"Filtered {len(matched)} candidates but none cleared the relevance threshold."
                # Persist the embeddings of what we showed for vector search later.
                if job_embeddings:
                    display_ids = {id(j) for j in display}
                    pairs = [(j, e) for j, e in zip(top_jobs, job_embeddings)
                             if id(j) in display_ids and e]
                    if pairs:
                        _persist_job_vectors(user_id, pairs)
                record_match_event(
                    user_id=user_id,
                    company_id=cid,
                    matches=display,
                    scraped_count=len(raw_jobs),
                    filtered_count=len(matched),
                    duration_ms=int((time.time() - _t_one) * 1000),
                    rerank_model=os.environ.get("AI_RERANK_MODEL"),
                    weights={
                        "skill":      float(os.environ.get("MATCH_W_SKILL", 0.18)),
                        "title":      float(os.environ.get("MATCH_W_TITLE", 0.20)),
                        "location":   float(os.environ.get("MATCH_W_LOC",   0.15)),
                        "experience": float(os.environ.get("MATCH_W_EXP",   0.32)),
                        "recency":    float(os.environ.get("MATCH_W_REC",   0.15)),
                    },
                )
                return {
                    "company": company_name,
                    "companyId": cid,
                    "jobs": display,
                    "count": len(matched),
                    "scraped": len(raw_jobs),
                    "noResultsReason": no_results_reason,
                }
            except Exception as e:
                logger.warning("[BULK] Failed %s: %s", cid, e)
                record_match_event(
                    user_id=user_id,
                    company_id=cid,
                    matches=[],
                    scraped_count=0,
                    filtered_count=0,
                    duration_ms=int((time.time() - _t_one) * 1000),
                    model_version=f"err:{type(e).__name__}",
                )
                return {
                    "company": _company_display_name(cid),
                    "companyId": cid,
                    "jobs": [],
                    "count": 0,
                    "error": str(e),
                }

        results = []
        # ── Bounded executor with deadline + per-future timeout ──
        # We dispatch every selected company up-front (executor queue does the
        # work) but we cap how long we *wait* for each future and the overall
        # bulk so a stuck scraper can't blow past Y1's 230s response cap.
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {pool.submit(_discover_one, cid): cid for cid in selected}
            for fut in concurrent.futures.as_completed(future_map):
                remaining = bulk_deadline_s - (time.time() - bulk_started_at)
                if remaining <= 0:
                    logger.warning(
                        "[BULK] deadline %.0fs reached; cancelling %d pending futures",
                        bulk_deadline_s,
                        sum(1 for f in future_map if not f.done()),
                    )
                    for f in future_map:
                        if not f.done():
                            f.cancel()
                            cid_pending = future_map[f]
                            try:
                                record_match_event(
                                    user_id=user_id,
                                    company_id=cid_pending,
                                    matches=[],
                                    scraped_count=0,
                                    filtered_count=0,
                                    duration_ms=int(bulk_deadline_s * 1000),
                                    model_version="err:DeadlineExceeded",
                                )
                            except Exception:
                                pass
                    break
                try:
                    results.append(fut.result(timeout=min(per_company_timeout_s, remaining)))
                except concurrent.futures.TimeoutError:
                    cid_to = future_map[fut]
                    logger.warning(
                        "[BULK] per-company timeout %.0fs hit for %s",
                        per_company_timeout_s, cid_to,
                    )
                    fut.cancel()
                    try:
                        record_match_event(
                            user_id=user_id,
                            company_id=cid_to,
                            matches=[],
                            scraped_count=0,
                            filtered_count=0,
                            duration_ms=int(per_company_timeout_s * 1000),
                            model_version="err:CompanyTimeout",
                        )
                    except Exception:
                        pass
                    results.append({
                        "company": _company_display_name(cid_to),
                        "companyId": cid_to,
                        "jobs": [],
                        "count": 0,
                        "error": f"timeout>{per_company_timeout_s}s",
                    })
                except Exception as e:
                    cid_err = future_map[fut]
                    logger.warning("[BULK] future raised for %s: %s", cid_err, e)
                    results.append({
                        "company": _company_display_name(cid_err),
                        "companyId": cid_err,
                        "jobs": [],
                        "count": 0,
                        "error": str(e),
                    })

        # ── GLOBAL LLM RERANK ──────────────────────────────────────────────
        # Bulk discover used to skip the strict-recruiter LLM rerank entirely,
        # so VP/Director/Architect titles, off-discipline (Tax, Sales, Legal,
        # HR), and geo-gated remote (Remote-US for India users) leaked into
        # the dashboard. We now run the same `_ai_rerank_top_jobs` used by
        # the per-company path on the GLOBAL top-N across all companies.
        # One LLM call per discover (cheap), catches everything the
        # deterministic filters miss.
        try:
            rerank_n = int(os.environ.get("BULK_RERANK_TOP_N", "30"))
            flat: list[dict] = []
            for r in results:
                for j in r.get("jobs") or []:
                    flat.append(j)
            flat.sort(key=lambda x: x.get("aiScore", x.get("matchScore", 0)), reverse=True)
            slice_to_rerank = flat[:rerank_n]
            if len(slice_to_rerank) >= 3 and not _should_skip_rerank(slice_to_rerank):
                _ai_rerank_top_jobs(slice_to_rerank, profile, "bulk")
                # Recompute aiScore using the LLM signal where present.
                for j in slice_to_rerank:
                    rs = j.get("aiReasoningScore")
                    if rs is not None:
                        raw = (
                            rs * 0.60
                            + j.get("vectorScore", 0) * 0.25
                            + j.get("matchScore", 0) * 0.15
                        )
                        j["aiScore"] = _calibrate_score(raw)
                # Drop jobs the LLM flagged as poor matches (and that didn't
                # have a strong vector signal to override).
                drop_ids = {
                    id(j) for j in slice_to_rerank
                    if j.get("aiDrop")
                    and j.get("aiReasoningScore", 100) < 20
                    and j.get("vectorScore", 0) < 55
                }
                if drop_ids:
                    for r in results:
                        r["jobs"] = [j for j in r.get("jobs") or [] if id(j) not in drop_ids]
                        r["count"] = len(r["jobs"])
                    logger.info("[BULK_RERANK] dropped %d jobs from global top-%d",
                                len(drop_ids), rerank_n)
                # Re-sort each company's jobs by the new aiScore.
                for r in results:
                    r["jobs"].sort(key=lambda x: x.get("aiScore", 0), reverse=True)
                logger.info("[BULK_RERANK] applied to top-%d (companies=%d)",
                            rerank_n, len(results))
            else:
                logger.info("[BULK_RERANK] skipped (n=%d, gap-skip=%s)",
                            len(slice_to_rerank), _should_skip_rerank(slice_to_rerank))
        except Exception as e:
            logger.warning("[BULK_RERANK] failed (keeping vector order): %s", e)

        results.sort(key=lambda r: max((j.get("aiScore", j.get("matchScore", 0)) for j in r["jobs"]), default=0), reverse=True)
        total = sum(r["count"] for r in results)

        _persist_discover_results(user_id, results)

        # ── Record full funnel telemetry ──
        try:
            prefs = profile.get("preferences") or {}
            _run_companies = []
            for r in results:
                _run_companies.append({
                    "companyId": r.get("companyId", ""),
                    "company": r.get("company", ""),
                    "scraped": r.get("scraped", 0),
                    "matched": r.get("count", 0),
                    "displayed": len(r.get("jobs") or []),
                    "error": r.get("error"),
                    "noResultsReason": r.get("noResultsReason"),
                })
            _record_discover_run(
                user_id=user_id,
                email=profile.get("email", ""),
                run_type="bulk",
                queries=(body_queries or ([query] if query else []) or (prefs.get("keywords") or [])[:5]),
                locations=(body_locations or prefs.get("locations", [])[:5]),
                duration_ms=int((time.time() - bulk_started_at) * 1000),
                companies=_run_companies,
            )
        except Exception:
            pass

        return success_response({
            "results": results,
            "totalFound": total,
            "companiesScraped": len(results),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Bulk discover error")
        return internal_error_response(str(e))


# Comprehensive country/city map used by the per-company discover filter.
# Defined at module scope so we don't rebuild it on every request.
_COUNTRY_MAP = {
    "india": {"india", "in", "ind"}, "bangalore": {"india", "in", "ind", "blr", "bengaluru", "karnataka"},
    "bengaluru": {"india", "in", "ind", "blr", "bengaluru", "karnataka"},
    "mumbai": {"india", "in", "ind", "mum", "bom", "maharashtra"}, "delhi": {"india", "in", "ind", "del", "ncr", "new delhi"},
    "hyderabad": {"india", "in", "ind", "hyd", "telangana"}, "pune": {"india", "in", "ind", "maharashtra"},
    "chennai": {"india", "in", "ind", "maa", "tamil nadu"}, "kolkata": {"india", "in", "ind", "ccu", "west bengal"},
    "noida": {"india", "in", "ind", "ncr", "uttar pradesh"}, "gurgaon": {"india", "in", "ind", "ncr", "haryana", "gurugram"},
    "gurugram": {"india", "in", "ind", "ncr", "haryana", "gurgaon"}, "ahmedabad": {"india", "in", "ind", "gujarat"},
    "jaipur": {"india", "in", "ind"}, "kochi": {"india", "in", "ind", "kerala"},
    "chandigarh": {"india", "in", "ind"}, "indore": {"india", "in", "ind"}, "nagpur": {"india", "in", "ind"},
    "coimbatore": {"india", "in", "ind"}, "thiruvananthapuram": {"india", "in", "ind", "trivandrum"},
    "lucknow": {"india", "in", "ind"}, "bhopal": {"india", "in", "ind"},
    "usa": {"usa", "us", "united states", "america"}, "united states": {"usa", "us", "united states", "america"},
    "san francisco": {"usa", "us", "california", "ca"}, "new york": {"usa", "us", "ny", "nyc"},
    "seattle": {"usa", "us", "washington", "wa"}, "austin": {"usa", "us", "texas", "tx"},
    "chicago": {"usa", "us", "illinois", "il"}, "boston": {"usa", "us", "massachusetts", "ma"},
    "los angeles": {"usa", "us", "california", "ca"}, "denver": {"usa", "us", "colorado", "co"},
    "atlanta": {"usa", "us", "georgia", "ga"}, "dallas": {"usa", "us", "texas", "tx"},
    "houston": {"usa", "us", "texas", "tx"}, "san jose": {"usa", "us", "california", "ca"},
    "miami": {"usa", "us", "florida", "fl"}, "portland": {"usa", "us", "oregon", "or"},
    "phoenix": {"usa", "us", "arizona", "az"}, "philadelphia": {"usa", "us", "pennsylvania", "pa"},
    "washington dc": {"usa", "us", "dc"}, "san diego": {"usa", "us", "california", "ca"},
    "raleigh": {"usa", "us", "north carolina", "nc"}, "pittsburgh": {"usa", "us", "pennsylvania", "pa"},
    "uk": {"uk", "gb", "gbr", "united kingdom", "england", "britain"},
    "united kingdom": {"uk", "gb", "gbr", "united kingdom", "england"},
    "london": {"uk", "gb", "gbr", "england"}, "manchester": {"uk", "gb", "gbr", "england"},
    "edinburgh": {"uk", "gb", "gbr", "scotland"}, "cambridge": {"uk", "gb", "gbr", "england"},
    "oxford": {"uk", "gb", "gbr", "england"}, "bristol": {"uk", "gb", "gbr", "england"},
    "birmingham": {"uk", "gb", "gbr", "england"}, "glasgow": {"uk", "gb", "gbr", "scotland"},
    "canada": {"canada", "ca", "can"}, "toronto": {"canada", "ca", "can", "ontario", "on"},
    "vancouver": {"canada", "ca", "can", "british columbia", "bc"}, "montreal": {"canada", "ca", "can", "quebec", "qc"},
    "ottawa": {"canada", "ca", "can", "ontario"}, "calgary": {"canada", "ca", "can", "alberta", "ab"},
    "germany": {"germany", "de", "deu", "deutschland"}, "berlin": {"germany", "de", "deu"},
    "munich": {"germany", "de", "deu", "münchen"}, "frankfurt": {"germany", "de", "deu"},
    "hamburg": {"germany", "de", "deu"}, "düsseldorf": {"germany", "de", "deu"},
    "france": {"france", "fr", "fra"}, "paris": {"france", "fr", "fra"},
    "netherlands": {"netherlands", "nl", "nld", "holland"}, "amsterdam": {"netherlands", "nl", "nld"},
    "ireland": {"ireland", "ie", "irl"}, "dublin": {"ireland", "ie", "irl"},
    "switzerland": {"switzerland", "ch", "che", "swiss"}, "zurich": {"switzerland", "ch", "che", "zürich"},
    "sweden": {"sweden", "se", "swe"}, "stockholm": {"sweden", "se", "swe"},
    "spain": {"spain", "es", "esp"}, "madrid": {"spain", "es", "esp"}, "barcelona": {"spain", "es", "esp"},
    "italy": {"italy", "it", "ita"}, "milan": {"italy", "it", "ita", "milano"},
    "poland": {"poland", "pl", "pol"}, "warsaw": {"poland", "pl", "pol"}, "krakow": {"poland", "pl", "pol", "kraków"},
    "portugal": {"portugal", "pt", "prt"}, "lisbon": {"portugal", "pt", "prt"},
    "denmark": {"denmark", "dk", "dnk"}, "copenhagen": {"denmark", "dk", "dnk"},
    "finland": {"finland", "fi", "fin"}, "helsinki": {"finland", "fi", "fin"},
    "norway": {"norway", "no", "nor"}, "oslo": {"norway", "no", "nor"},
    "austria": {"austria", "at", "aut"}, "vienna": {"austria", "at", "aut", "wien"},
    "belgium": {"belgium", "be", "bel"}, "brussels": {"belgium", "be", "bel"},
    "czech": {"czech", "cz", "cze", "czech republic"}, "prague": {"czech", "cz", "cze", "czech republic"},
    "singapore": {"singapore", "sg", "sgp"},
    "uae": {"uae", "ae", "are", "united arab emirates"}, "dubai": {"uae", "ae", "are", "united arab emirates"},
    "abu dhabi": {"uae", "ae", "are", "united arab emirates"},
    "saudi arabia": {"saudi arabia", "sa", "sau", "ksa"}, "saudi": {"saudi arabia", "sa", "sau", "ksa"},
    "riyadh": {"saudi arabia", "sa", "sau", "ksa"}, "jeddah": {"saudi arabia", "sa", "sau", "ksa"},
    "qatar": {"qatar", "qa", "qat"}, "doha": {"qatar", "qa", "qat"},
    "bahrain": {"bahrain", "bh", "bhr"},
    "kuwait": {"kuwait", "kw", "kwt"},
    "oman": {"oman", "om", "omn"}, "muscat": {"oman", "om", "omn"},
    "china": {"china", "cn", "chn", "prc"}, "beijing": {"china", "cn", "chn"},
    "shanghai": {"china", "cn", "chn"}, "shenzhen": {"china", "cn", "chn"},
    "hangzhou": {"china", "cn", "chn"}, "guangzhou": {"china", "cn", "chn"},
    "hong kong": {"hong kong", "hk", "hkg"},
    "taiwan": {"taiwan", "tw", "twn"}, "taipei": {"taiwan", "tw", "twn"},
    "japan": {"japan", "jp", "jpn"}, "tokyo": {"japan", "jp", "jpn"}, "osaka": {"japan", "jp", "jpn"},
    "south korea": {"south korea", "kr", "kor", "korea"}, "korea": {"south korea", "kr", "kor"},
    "seoul": {"south korea", "kr", "kor", "korea"},
    "australia": {"australia", "au", "aus"}, "sydney": {"australia", "au", "aus", "nsw"},
    "melbourne": {"australia", "au", "aus", "vic"}, "brisbane": {"australia", "au", "aus", "qld"},
    "perth": {"australia", "au", "aus"},
    "new zealand": {"new zealand", "nz", "nzl"}, "auckland": {"new zealand", "nz", "nzl"},
    "israel": {"israel", "il", "isr"}, "tel aviv": {"israel", "il", "isr"},
    "brazil": {"brazil", "br", "bra"}, "sao paulo": {"brazil", "br", "bra"},
    "mexico": {"mexico", "mx", "mex"}, "mexico city": {"mexico", "mx", "mex"},
    "south africa": {"south africa", "za", "zaf"}, "johannesburg": {"south africa", "za", "zaf"},
    "cape town": {"south africa", "za", "zaf"},
}


# Regional aliases — map a regional name to the list of constituent
# countries (each must be a key in `_COUNTRY_MAP` so the codes resolve).
# Used by `_filter_jobs_by_location` (location keep/drop) and by
# `_expand_country_to_cities` (fan-out search queries to the region's
# metros). Without these, a user typing "Europe" gets 0 matches because
# the filter treats it as an unknown city.
_REGION_ALIASES: dict[str, list[str]] = {
    "europe": [
        "uk", "germany", "france", "netherlands", "ireland", "switzerland",
        "spain", "italy", "poland", "portugal", "denmark", "finland",
        "norway", "sweden", "austria", "belgium", "czech",
    ],
    "eu": [
        "germany", "france", "netherlands", "ireland", "spain", "italy",
        "poland", "portugal", "denmark", "finland", "sweden", "austria",
        "belgium", "czech",
    ],
    "emea": [
        "uk", "germany", "france", "netherlands", "ireland", "switzerland",
        "spain", "italy", "poland", "portugal", "denmark", "finland",
        "norway", "sweden", "austria", "belgium", "czech",
        "uae", "saudi arabia", "qatar", "south africa", "israel",
    ],
    "apac": [
        "singapore", "australia", "japan", "south korea", "hong kong",
        "taiwan", "china", "india", "new zealand",
    ],
    "asia": [
        "india", "singapore", "japan", "south korea", "china", "hong kong",
        "taiwan",
    ],
    "americas": ["usa", "canada", "brazil", "mexico"],
    "north america": ["usa", "canada", "mexico"],
    "latam": ["brazil", "mexico"],
    "middle east": ["uae", "saudi arabia", "qatar", "bahrain", "kuwait", "oman"],
    "gulf": ["uae", "saudi arabia", "qatar", "bahrain", "kuwait", "oman"],
    "anywhere in europe": [
        "uk", "germany", "france", "netherlands", "ireland", "switzerland",
        "spain", "italy", "poland", "portugal", "denmark", "finland",
        "norway", "sweden", "austria", "belgium", "czech",
    ],
}


# Country -> top metro cities to fan-scrape when the user only picks the
# country. Many career boards (Workday, Greenhouse, Lever) bias their search
# rankings toward exact-city matches and will return MORE relevant jobs when
# asked for a specific city than when given a country name. We still include
# the bare country name as one of the queries so country-aware boards keep
# working, and the post-scrape `_filter_jobs_by_location` ensures we never
# leak jobs from the wrong country regardless.
_COUNTRY_TO_CITIES: dict[str, list[str]] = {
    "india":          ["Bangalore", "Hyderabad", "Pune", "Mumbai", "Delhi", "Chennai", "Gurgaon", "Noida"],
    "usa":            ["San Francisco", "New York", "Seattle", "Austin", "Boston", "Los Angeles", "Chicago", "Atlanta"],
    "united states":  ["San Francisco", "New York", "Seattle", "Austin", "Boston", "Los Angeles", "Chicago", "Atlanta"],
    "uk":             ["London", "Manchester", "Edinburgh", "Cambridge", "Bristol", "Birmingham"],
    "united kingdom": ["London", "Manchester", "Edinburgh", "Cambridge", "Bristol", "Birmingham"],
    "canada":         ["Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary"],
    "germany":        ["Berlin", "Munich", "Frankfurt", "Hamburg"],
    "france":         ["Paris", "Lyon", "Toulouse"],
    "netherlands":    ["Amsterdam", "Rotterdam", "Eindhoven"],
    "ireland":        ["Dublin", "Cork", "Galway"],
    "switzerland":    ["Zurich", "Geneva", "Basel"],
    "spain":          ["Madrid", "Barcelona", "Valencia"],
    "italy":          ["Milan", "Rome", "Turin"],
    "poland":         ["Warsaw", "Krakow", "Wroclaw"],
    "australia":      ["Sydney", "Melbourne", "Brisbane", "Perth"],
    "singapore":      ["Singapore"],
    "japan":          ["Tokyo", "Osaka"],
    "south korea":    ["Seoul"],
    "china":          ["Beijing", "Shanghai", "Shenzhen", "Hangzhou"],
    "uae":            ["Dubai", "Abu Dhabi"],
    "brazil":         ["Sao Paulo", "Rio de Janeiro"],
    "mexico":         ["Mexico City", "Guadalajara"],
}


def _expand_country_to_cities(locations: list[str], cap: int | None = None) -> list[str]:
    """Expand pure-country picks (e.g. ["India"]) into the country plus its
    top metros (["India", "Bangalore", "Hyderabad", "Pune", ...]).

    Regional aliases ("Europe", "APAC", "EMEA", "Asia", "Americas", "Middle
    East", etc.) are expanded into their constituent countries first, each
    of which then gets a (smaller, per-country) metro fan-out so we don't
    blow up the scrape budget. A user typing "Europe" therefore ends up
    with roughly 15 queries: 7-8 countries + 1-2 top metros each.

    Already-specific cities pass through untouched. Order is preserved so
    user-pinned cities take priority. Cap controls how many cities to add per
    country; defaults to the SCRAPE_CITY_FANOUT env knob (6).
    """
    if cap is None:
        try:
            cap = int(os.environ.get("SCRAPE_CITY_FANOUT", "6"))
        except (TypeError, ValueError):
            cap = 6
    # Regions fan-out to many countries; keep the per-country metro
    # expansion small so the total stays within scrape budget.
    region_metro_cap = max(1, min(cap, 1))
    # Cap how many countries we expand a region into (otherwise "Europe"
    # alone produces 16 countries × 1 metro = 32 entries, blowing the
    # scrape-pair cap downstream and starving each pair of time).
    try:
        region_country_cap = int(os.environ.get("REGION_COUNTRY_CAP", "8"))
    except (TypeError, ValueError):
        region_country_cap = 8

    out: list[str] = []
    seen: set[str] = set()
    for raw in locations:
        loc = (raw or "").strip()
        if not loc:
            continue
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)

        # Region first — preserves the literal phrase (some boards understand
        # "Europe"), then explodes into top countries + 1 metro each.
        region_countries = _REGION_ALIASES.get(key)
        if region_countries:
            out.append(loc)
            for country in region_countries[:region_country_cap]:
                ck = country.lower()
                if ck in seen:
                    continue
                seen.add(ck)
                out.append(country.title() if country.isalpha() else country)
                for c in (_COUNTRY_TO_CITIES.get(ck) or [])[:region_metro_cap]:
                    cck = c.lower()
                    if cck not in seen:
                        seen.add(cck)
                        out.append(c)
            continue

        out.append(loc)
        cities = _COUNTRY_TO_CITIES.get(key)
        if cities:
            for c in cities[:cap]:
                ck = c.lower()
                if ck not in seen:
                    seen.add(ck)
                    out.append(c)
    return out


def _filter_jobs_by_location(jobs: list[dict], locations: list[str], log_tag: str = "") -> list[dict]:
    """Drop jobs whose `location` field doesn't match the user's filter.

    Rules:
      * City substring match always passes (when no remote-only flag).
      * Country code/name match passes (when no remote-only flag).
      * If EVERY user-supplied location starts with "remote" (e.g.
        "Remote, India"), enforce remote-only: the job's location MUST
        contain a remote/hybrid/wfh token AND, if a country was pinned,
        also match that country.
      * Pure "Remote / Anywhere / Global" jobs pass when the user did NOT
        pin any country.
      * Unknown-location jobs are kept only when no country is pinned and
        remote-only is not set.
    """
    if not jobs or not locations:
        return jobs

    import re as _re

    loc_lower = [l.lower().strip() for l in locations if l and l.strip()]
    if not loc_lower:
        return jobs
    countries: set[str] = set()
    cities: set[str] = set()
    has_country_filter = False
    # Remote-only is requested when EVERY filter entry begins with "remote"
    # (e.g. "remote", "remote, india", "remote (us)"). If even one entry is
    # a plain city like "Bengaluru" we fall back to the looser behaviour.
    remote_only = all(l.startswith("remote") for l in loc_lower)
    pure_remote_terms = (
        "remote", "hybrid", "anywhere", "global", "worldwide",
        "flexible", "wfh", "work from home", "distributed",
    )
    for loc in loc_lower:
        # Don't treat "remote" itself as a free-typed city — otherwise
        # cities_safe gets polluted with "remote, india" which then
        # substring-matches every India job.
        if not loc.startswith("remote"):
            cities.add(loc)
        matched_country = False
        # Regional aliases ("europe", "apac", "emea", "asia", "americas",
        # "middle east", "latam", "gulf"…) expand to every constituent
        # country's codes so a job in "London, UK" matches a "Europe" filter.
        for region_key, region_countries in _REGION_ALIASES.items():
            if region_key in loc:
                for country_name in region_countries:
                    country_codes = _COUNTRY_MAP.get(country_name)
                    if country_codes:
                        countries.update(country_codes)
                matched_country = True
        for keyword, codes in _COUNTRY_MAP.items():
            if keyword in loc:
                countries.update(codes)
                matched_country = True
        if matched_country:
            has_country_filter = True

    countries_safe = {c for c in countries if len(c) >= 3}
    cities_safe = {c for c in cities if len(c) >= 4}

    def _country_match(jl: str) -> bool:
        for code in countries_safe:
            if len(code) <= 3:
                if _re.search(r'\b' + _re.escape(code) + r'\b', jl):
                    return True
            else:
                if code in jl:
                    return True
        return False

    def _city_match(jl: str) -> bool:
        return any(city in jl for city in cities_safe)

    def _is_remote(jl: str) -> bool:
        return any(tok in jl for tok in pure_remote_terms)

    def _matches(job_loc: str) -> bool:
        jl = job_loc.lower()
        if remote_only:
            # Job must be remote.
            if not _is_remote(jl):
                return False
            # If user pinned a country, the remote job must also be in it
            # (or be country-less / global).
            if has_country_filter:
                if _country_match(jl):
                    return True
                # Allow truly global / anywhere remote postings.
                if any(t in jl for t in ("anywhere", "global", "worldwide")):
                    return True
                return False
            return True
        # Non-remote-only branch.
        if _city_match(jl):
            return True
        if _country_match(jl):
            return True
        if not has_country_filter and _is_remote(jl):
            return True
        return False

    out: list[dict] = []
    for job in jobs:
        jl = (job.get("location") or "").lower()
        if not jl:
            # Unknown location: only keep when filter is unrestrictive.
            if not has_country_filter and not remote_only:
                out.append(job)
            continue
        if _matches(jl):
            out.append(job)

    if log_tag:
        logger.info("%s LOC_FILTER %d/%d passed (remote_only=%s, countries=%s, cities=%s)",
                    log_tag, len(out), len(jobs), remote_only,
                    sorted(countries_safe), list(cities_safe)[:3])
    return out


@bp.route(route="api/v1/jobs/discover/company", methods=["POST"])
def discover_company_jobs(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/jobs/discover/company — Discover jobs for a single company."""
    _t0 = time.time()
    try:
        user_id = get_user_id(req)

        body = req.get_json()
        company_id = body.get("companyId", "")
        query = body.get("query", "")
        body_queries = [str(q).strip() for q in (body.get("queries") or []) if str(q).strip()]
        body_locations = [str(l).strip() for l in (body.get("locations") or []) if str(l).strip()]
        search_id = (body.get("searchId") or "").strip() or None
        body_industry = (body.get("industry") or "").strip().lower() or None
        pivot_mode = bool(body.get("pivot"))

        if not company_id:
            raise ValidationError("companyId is required")

        # Companies without a native scraper are served via LinkedIn tile
        if company_id not in _API_SCRAPERS:
            return success_response({
                "company": COMPANIES.get(company_id, {}).get("name", company_id),
                "companyId": company_id,
                "jobs": [], "count": 0, "scraped": 0,
                "noResultsReason": "This company's jobs are available in the LinkedIn Jobs section. Tap the LinkedIn search button to find them.",
            })

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        # Persist the industry choice on the profile so subsequent calls
        # (rerank, tailor, insights) all see the same industry hint.
        if body_industry:
            prefs = profile.setdefault("preferences", {})
            if prefs.get("industry") != body_industry:
                prefs["industry"] = body_industry
                try:
                    upsert_item("profiles", profile)
                except Exception as e:
                    logger.warning("[INDUSTRY] persist failed: %s", e)

        allowed, remaining = _check_daily_quota(profile, search_id)
        if not allowed:
            country = get_country_for_billing(req, profile)
            raise RateLimitError(get_upgrade_message(country))

        user_email = profile.get("email", user_id)
        log_tag = f"[DISCOVER][{user_email}][{company_id}] quota_remaining={remaining}"

        prefs = profile.get("preferences") or {}
        locations = prefs.get("locations", []) or []
        if body_locations:
            locations = body_locations
        location_queries: list[str] = []
        for loc in locations:
            loc = (loc or "").strip()
            if loc and loc.lower() not in ("remote", "anywhere"):
                location_queries.append(loc)
        # Country expansion: if the user picked "India" (no specific city),
        # fan out to top Indian metros so career boards that bias toward
        # exact-city matches return a fuller set. The country itself stays
        # in the list so country-aware boards keep working, and the
        # post-scrape `_filter_jobs_by_location` strips any cross-country
        # leaks.
        location_queries = _expand_country_to_cities(location_queries)
        if not location_queries:
            location_queries = [""]
        # Cap how many distinct location strings we send PER COMPANY.
        # 4 was the original limit (single-city assumption); for country
        # picks we now want enough to cover the metro fan-out. Tunable via
        # SCRAPE_LOC_PER_COMPANY env (default 6 = country + 5 metros).
        try:
            _loc_cap = int(os.environ.get("SCRAPE_LOC_PER_COMPANY", "6"))
        except (TypeError, ValueError):
            _loc_cap = 6
        location_queries = location_queries[:_loc_cap]
        location_query = location_queries[0]
        exp_years = prefs.get("experienceYears", 0)
        keywords = prefs.get("keywords", [])

        search_queries = []
        for q in body_queries:
            if q not in search_queries:
                search_queries.append(q)
        if query and query not in search_queries:
            search_queries.append(query)
        if not search_queries and keywords and "queries" not in body:
            for kw in keywords[:2]:
                if kw not in search_queries:
                    search_queries.append(kw)
        if not search_queries:
            user_exp = profile.get("experience") or []
            if user_exp and isinstance(user_exp[0], dict):
                search_queries.append(user_exp[0].get("title", "software engineer"))
            else:
                search_queries.append("software engineer")

        # Broaden ONLY engineering-style searches with generic fallbacks. For
        # "Product Designer", "Investment Banking Analyst", "Registered Nurse"
        # etc. this leaves search_queries untouched -- so the scrapers don't
        # also pull every Software Engineer posting.
        _maybe_add_eng_fallbacks(
            search_queries,
            industry=(body_industry or prefs.get("industry") or ""),
        )

        if not exp_years:
            parsed_years = (profile.get("documents") or {}).get("parsedResumeData", {}).get("totalYearsExperience", 0)
            if parsed_years and parsed_years > 0:
                exp_years = parsed_years
            elif len(profile.get("experience") or []) > 0:
                exp_years = len(profile.get("experience") or []) * 2

        pivot_mode = _level_qualify_queries(search_queries, profile, pivot=pivot_mode)

        logger.info("%s SEARCH queries=%s locations=%s exp=%s pivot=%s",
                    log_tag, search_queries, location_queries, exp_years,
                    pivot_mode)
        raw_jobs = []
        seen_ids = set()

        from concurrent.futures import ThreadPoolExecutor, as_completed
        scrape_pairs = []
        for sq in search_queries:
            for lq in location_queries:
                scrape_pairs.append((sq, lq))
        # Cap total parallel scrapes per company. Default 16 = 2 queries x
        # ~8 locations. Tunable via SCRAPE_PAIRS_PER_COMPANY. Higher values
        # find more jobs but increase first-run latency for cold caches.
        try:
            _pairs_cap = int(os.environ.get("SCRAPE_PAIRS_PER_COMPANY", "24"))
        except (TypeError, ValueError):
            _pairs_cap = 24
        scrape_pairs = scrape_pairs[:_pairs_cap]

        def _scrape_one(pair):
            sq, lq = pair
            return _scrape_company_cached(company_id, query=sq, location=lq)

        try:
            _max_workers_cap = int(os.environ.get("SCRAPE_WORKERS_PER_COMPANY", "10"))
        except (TypeError, ValueError):
            _max_workers_cap = 10
        with ThreadPoolExecutor(max_workers=min(len(scrape_pairs), _max_workers_cap)) as pool:
            futures = {pool.submit(_scrape_one, p): p for p in scrape_pairs}
            for fut in as_completed(futures):
                sq, lq = futures[fut]
                try:
                    jobs = fut.result()
                    for j in jobs:
                        jid = j.get("id", j.get("title", ""))
                        if jid not in seen_ids:
                            seen_ids.add(jid)
                            raw_jobs.append(j)
                except Exception as e:
                    logger.warning("%s Scrape failed query='%s' loc='%s': %s",
                                   log_tag, sq, lq, e)

        logger.info("%s SCRAPED %d unique jobs from %d queries", log_tag, len(raw_jobs), len(search_queries))
        for j in raw_jobs:
            logger.info("%s SCRAPED_JOB id=%s title='%s' location='%s'", log_tag, j.get('id', ''), j.get('title', '')[:50], j.get('location', '')[:40])

        # ── Country filter ──
        if locations:
            raw_jobs = _filter_jobs_by_location(raw_jobs, locations, log_tag)

        matched = match_jobs_to_profile(
            raw_jobs, profile,
            search_queries=search_queries, pivot=pivot_mode,
        )
        logger.info("%s MATCHED %d jobs (from %d filtered)", log_tag, len(matched), len(raw_jobs))
        for i, j in enumerate(matched):
            logger.info("%s SCORED #%d id=%s title='%s' matchScore=%d skillScore=%d expScore=%d",
                        log_tag, i + 1, j.get('id', ''), j.get('title', '')[:40],
                        j.get('matchScore', 0), j.get('skillScore', 0), j.get('experienceScore', 0))

        if not matched:
            logger.info("%s RESULT: 0 jobs returned", log_tag)
            record_match_event(
                user_id=user_id,
                company_id=company_id,
                matches=[],
                scraped_count=len(raw_jobs),
                filtered_count=len(raw_jobs),
                duration_ms=int((time.time() - _t0) * 1000),
                rerank_model=os.environ.get("AI_RERANK_MODEL"),
                weights={
                    "skill":      float(os.environ.get("MATCH_W_SKILL", 0.18)),
                    "title":      float(os.environ.get("MATCH_W_TITLE", 0.20)),
                    "location":   float(os.environ.get("MATCH_W_LOC",   0.15)),
                    "experience": float(os.environ.get("MATCH_W_EXP",   0.32)),
                    "recency":    float(os.environ.get("MATCH_W_REC",   0.15)),
                },
                region=(locations[0] if locations else None),
                search_id=search_id,
            )
            return success_response({
                "company": _company_display_name(company_id),
                "companyId": company_id,
                "jobs": [], "count": 0,
                "scraped": len(raw_jobs),
                "noResultsReason": (
                    "No open roles found for your queries on this company's careers site."
                    if not raw_jobs else
                    f"Found {len(raw_jobs)} openings, but none matched your profile / location preferences."
                ),
            })

        from shared.embeddings import EMBEDDING_DIMS
        profile_embedding = profile.get("profileEmbedding", [])
        if profile_embedding and len(profile_embedding) != EMBEDDING_DIMS:
            logger.info("%s Stale embedding (%d dims, expected %d) — regenerating", log_tag, len(profile_embedding), EMBEDDING_DIMS)
            profile_embedding = []
        # Widen the funnel: keep more candidates through every stage so users
        # see more options. The LLM rerank still trims obviously-poor matches.
        if not profile_embedding:
            if not profile.get("aiSummary"):
                ai_summary = generate_profile_summary(profile)
                if ai_summary:
                    profile["aiSummary"] = ai_summary
            profile_text = profile_to_text(profile)
            if profile_text:
                profile_embedding = generate_embedding(profile_text)
                if profile_embedding:
                    profile["profileEmbedding"] = profile_embedding
                    profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
                    upsert_item("profiles", profile)
                    logger.info("%s Generated AI summary + cached embedding (%d dims)", log_tag, len(profile_embedding))

        # Vector-rank window. Bumped from 50 -> 200 (configurable) so the
        # deeper scrape pool is actually scored. Ada-style embeddings are
        # cheap; the rerank stage further down still trims to the top
        # AI_RERANK window before hitting the more expensive reasoning model.
        try:
            _vec_window = int(os.environ.get("VECTOR_RANK_WINDOW", "200"))
        except (TypeError, ValueError):
            _vec_window = 200
        top_jobs = matched[:_vec_window]
        if profile_embedding:
            cached = _get_cached_job_embeddings(company_id, top_jobs)
            missing_idx = [i for i in range(len(top_jobs)) if i not in cached]
            logger.info("%s JOB_EMB_CACHE hits=%d misses=%d", log_tag, len(cached), len(missing_idx))
            new_embeddings: list[list[float]] = []
            if missing_idx:
                missing_texts = [job_to_text(top_jobs[i]) for i in missing_idx]
                new_embeddings = generate_embeddings_batch(missing_texts)
                _cache_job_embeddings(company_id, [top_jobs[i] for i in missing_idx], new_embeddings)
            job_embeddings: list[list[float]] = [None] * len(top_jobs)  # type: ignore
            for i, emb in cached.items():
                job_embeddings[i] = emb
            for i, emb in zip(missing_idx, new_embeddings):
                job_embeddings[i] = emb

            for job, emb in zip(top_jobs, job_embeddings):
                if emb:
                    job["_emb"] = emb
                    sim = cosine_similarity(profile_embedding, emb)
                    job["vectorScore"] = round(sim * 100)
                    raw = (
                        sim * 100 * 0.5 +
                        job.get("matchScore", 0) * 0.3 +
                        job.get("recencyScore", 50) * 0.2
                    )
                    job["aiScore"] = _calibrate_score(raw)
                else:
                    job["vectorScore"] = 0
                    job["aiScore"] = _calibrate_score(job.get("matchScore", 0))

            top_jobs.sort(key=lambda x: x.get("aiScore", 0), reverse=True)

        try:
            try:
                _rerank_cap = int(os.environ.get("COMPANY_RERANK_TOP_N", "50"))
            except (TypeError, ValueError):
                _rerank_cap = 50
            rerank_n = min(_rerank_cap, len(top_jobs))
            if rerank_n >= 3 and not _should_skip_rerank(top_jobs):
                rerank_slice = top_jobs[:rerank_n]
                _ai_rerank_top_jobs(rerank_slice, profile, company_id)
                for j in rerank_slice:
                    rs = j.get("aiReasoningScore")
                    if rs is not None:
                        raw = (
                            rs * 0.60 +
                            j.get("vectorScore", 0) * 0.25 +
                            j.get("matchScore", 0) * 0.15
                        )
                        j["aiScore"] = _calibrate_score(raw)
                pre = len(top_jobs)
                # Keep the funnel wide: only drop jobs the LLM explicitly
                # flagged as poor matches AND that the vector model also
                # didn't like. A strong vector hit (>= 55) is enough signal
                # to keep the candidate visible even if the LLM was lukewarm.
                _gate_loose = int(os.environ.get("RERANK_GATE_LOOSE", "20"))
                _vec_floor  = int(os.environ.get("RERANK_VECTOR_FLOOR", "55"))
                # User-tunable lenient floor: any job whose blended aiScore
                # is at or above this threshold (default 70) is ALWAYS
                # included in the final result, regardless of whether the
                # LLM flagged it as a poor match. This guarantees that
                # high-confidence jobs from companies like Amazon/Oracle/
                # Zomato aren't silently filtered out.
                _score_keep = int(os.environ.get("SCORE_KEEP_FLOOR", "70"))
                # Snapshot the full reranked pool BEFORE we apply the drop
                # filter, so we can rescue the highest-scored "would-be
                # dropped" jobs if the company ends up below the floor.
                _pool_before_drop = list(top_jobs)
                top_jobs = [
                    j for j in top_jobs
                    if j.get("aiScore", 0) >= _score_keep
                    or (
                        not j.get("aiDrop")
                        and (
                            j.get("aiReasoningScore", 100) >= _gate_loose
                            or j.get("vectorScore", 0) >= _vec_floor
                        )
                    )
                ]
                if pre != len(top_jobs):
                    logger.info("%s LLM_DROP removed %d jobs flagged as poor matches (kept aiScore>=%d as override)",
                                log_tag, pre - len(top_jobs), _score_keep)

                # ── Min-results rescue ───────────────────────────────────
                # If the LLM was overly aggressive and we ended up with very
                # few jobs for a company (common when the candidate's
                # preferred city differs from where the company is hiring),
                # restore the highest-scored DROPPED jobs so the user still
                # sees some options instead of "0 jobs at <Company>".
                # These are clearly labelled with "below threshold" reasons
                # so the UI can still surface them with a softer match
                # badge, while letting the strict drops happen for the
                # majority of companies that returned plenty.
                try:
                    _min_results = int(os.environ.get("MIN_RESULTS_PER_COMPANY", "3"))
                except (TypeError, ValueError):
                    _min_results = 3
                if _min_results > 0 and len(top_jobs) < _min_results:
                    kept_ids = {id(j) for j in top_jobs}
                    rescue_pool = [
                        j for j in _pool_before_drop if id(j) not in kept_ids
                    ]
                    # Sort by reasoning score desc, then vector desc, so the
                    # least-bad of the dropped jobs come first.
                    rescue_pool.sort(
                        key=lambda j: (
                            j.get("aiReasoningScore", 0),
                            j.get("vectorScore", 0),
                        ),
                        reverse=True,
                    )
                    needed = _min_results - len(top_jobs)
                    rescued = rescue_pool[:needed]
                    if rescued:
                        for j in rescued:
                            j["softMatch"] = True
                        top_jobs.extend(rescued)
                        logger.info("%s LLM_RESCUE restored %d soft-match jobs (had=%d min=%d)",
                                    log_tag, len(rescued), len(top_jobs) - len(rescued), _min_results)
                top_jobs.sort(key=lambda x: x.get("aiScore", 0), reverse=True)
                logger.info("%s LLM_RERANK applied to top %d jobs", log_tag, rerank_n)
            else:
                logger.info("%s LLM_RERANK skipped (vector ranking already confident)", log_tag)
        except Exception as e:
            logger.warning("%s LLM rerank failed (keeping vector order): %s", log_tag, e)

        # Resolve through the registry so users never see `comp-amazon` even if
        # the first scraped job didn't carry a `company` field.
        company_name = (top_jobs[0].get("company") if top_jobs else None) or _company_display_name(company_id)

        for i, j in enumerate(top_jobs):
            logger.info("%s VECTOR_RANKED #%d id=%s vectorScore=%d aiScore=%d title='%s'",
                        log_tag, i + 1, j.get('id', ''), j.get('vectorScore', 0), j.get('aiScore', 0), j.get('title', '')[:40])

        try:
            _display_cap = int(os.environ.get("COMPANY_DISPLAY_CAP", "60"))
        except (TypeError, ValueError):
            _display_cap = 60
        display_jobs = top_jobs[:_display_cap]
        # Strip internal-only fields before returning to client.
        for _j in display_jobs:
            _j.pop("_aiReasonInternal", None)
        returned_ids = [j.get('id', '') for j in display_jobs]
        logger.info("%s RESULT: returning %d/%d jobs, total_matched=%d, ids=%s",
                    log_tag, len(display_jobs), len(top_jobs), len(matched), returned_ids)
        # Persist per-job embeddings for vector search (tailor flow).
        emb_pairs = [(j, j.get("_emb")) for j in display_jobs if j.get("_emb")]
        if emb_pairs:
            _persist_job_vectors(user_id, emb_pairs)
        for j in display_jobs:
            j.pop("_emb", None)
        _persist_discover_results(user_id, [{
            "company": company_name,
            "companyId": company_id,
            "count": len(matched),
            "jobs": display_jobs,
        }], merge=True)
        record_match_event(
            user_id=user_id,
            company_id=company_id,
            matches=display_jobs,
            scraped_count=len(raw_jobs),
            filtered_count=len(matched),
            duration_ms=int((time.time() - _t0) * 1000),
            rerank_model=os.environ.get("AI_RERANK_MODEL"),
            weights={
                "skill":      float(os.environ.get("MATCH_W_SKILL", 0.18)),
                "title":      float(os.environ.get("MATCH_W_TITLE", 0.20)),
                "location":   float(os.environ.get("MATCH_W_LOC",   0.15)),
                "experience": float(os.environ.get("MATCH_W_EXP",   0.32)),
                "recency":    float(os.environ.get("MATCH_W_REC",   0.15)),
            },
            region=(locations[0] if locations else None),
            search_id=search_id,
        )
        # ── Record per-company run telemetry ──
        try:
            _record_discover_run(
                user_id=user_id,
                email=profile.get("email", ""),
                run_type="company",
                queries=search_queries[:5],
                locations=location_queries[:5],
                duration_ms=int((time.time() - _t0) * 1000),
                companies=[{
                    "companyId": company_id,
                    "company": company_name,
                    "scraped": len(raw_jobs),
                    "matched": len(matched),
                    "vectorScored": len(top_jobs),
                    "displayed": len(display_jobs),
                }],
            )
        except Exception:
            pass
        return success_response({
            "company": company_name,
            "companyId": company_id,
            "jobs": display_jobs,
            "count": len(matched),
            "analyzed": len(top_jobs),
            "scraped": len(raw_jobs),
            "noResultsReason": (
                None if display_jobs else
                f"Found {len(raw_jobs)} openings, but none cleared the relevance threshold for your profile."
            ),
        })
    except AppException as e:
        # Visibility: persist a lightweight failure event so the funnel
        # accounts for every selected company in a 150-company run, even
        # when the call failed validation/quota/notfound. Without this,
        # 150-company runs silently show ~8 distinct match_events because
        # most error paths bypass the success-branch recorder.
        try:
            record_match_event(
                user_id=locals().get('user_id', 'unknown'),
                company_id=locals().get('company_id', '') or 'unknown',
                matches=[],
                scraped_count=0,
                filtered_count=0,
                duration_ms=int((time.time() - _t0) * 1000),
                rerank_model=os.environ.get("AI_RERANK_MODEL"),
                weights={},
                region=os.environ.get("AZURE_REGION", "centralus"),
                search_id=locals().get('search_id'),
                model_version=f"err:{type(e).__name__}",
            )
        except Exception:
            pass
        return error_response(e)
    except Exception as e:
        try:
            record_match_event(
                user_id=locals().get('user_id', 'unknown'),
                company_id=locals().get('company_id', '') or 'unknown',
                matches=[],
                scraped_count=0,
                filtered_count=0,
                duration_ms=int((time.time() - _t0) * 1000),
                rerank_model=os.environ.get("AI_RERANK_MODEL"),
                weights={},
                region=os.environ.get("AZURE_REGION", "centralus"),
                search_id=locals().get('search_id'),
                model_version=f"unhandled:{type(e).__name__}",
            )
        except Exception:
            pass
        logger.exception("Error discovering company jobs: %s", company_id if 'company_id' in dir() else '?')
        return internal_error_response(str(e))


@bp.route(route="api/v1/jobs/linkedin/search", methods=["POST"])
def discover_linkedin_jobs(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/jobs/linkedin/search — Search LinkedIn jobs, grouped by employer."""
    try:
        user_id = get_user_id(req)
        body = req.get_json() or {}
        req_queries = [str(q).strip() for q in (body.get("queries") or []) if str(q).strip()]
        req_locations = [str(l).strip() for l in (body.get("locations") or []) if str(l).strip()]
        search_id = (body.get("searchId") or "").strip() or None
        body_industry = (body.get("industry") or "").strip().lower() or None
        pivot_mode = bool(body.get("pivot"))

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        if body_industry:
            prefs_p = profile.setdefault("preferences", {})
            if prefs_p.get("industry") != body_industry:
                prefs_p["industry"] = body_industry
                try:
                    upsert_item("profiles", profile)
                except Exception as e:
                    logger.warning("[INDUSTRY] persist failed: %s", e)

        allowed, remaining = _check_daily_linkedin_quota(profile)
        if not allowed:
            country = get_country_for_billing(req, profile)
            raise RateLimitError(get_upgrade_message(country))

        prefs = profile.get("preferences") or {}
        client_sent_queries = "queries" in body
        if not req_queries:
            if client_sent_queries:
                # UI sent an explicit (possibly empty) query list — do not
                # resurrect stale profile keywords from a prior search.
                user_exp = profile.get("experience") or []
                if user_exp and isinstance(user_exp[0], dict):
                    req_queries = [user_exp[0].get("title", "software engineer")]
                else:
                    req_queries = ["software engineer"]
            else:
                req_queries = [k for k in (prefs.get("keywords") or []) if k][:2]
        if not req_queries:
            user_exp = profile.get("experience") or []
            if user_exp and isinstance(user_exp[0], dict):
                req_queries = [user_exp[0].get("title", "software engineer")]
            else:
                req_queries = ["software engineer"]
        if not req_locations:
            req_locations = [
                l for l in (prefs.get("locations") or [])
                if l and l.lower() not in ("remote", "anywhere")
            ][:3]
        if not req_locations:
            req_locations = [""]

        req_queries = req_queries[:3]
        req_locations = req_locations[:3]

        # Level-qualify: a 15-YOE user searching "Product Manager" should
        # also search "VP of Product Manager", "Director of Product Manager" etc.
        # Skipped automatically (or via pivot=true) when the search is
        # off-discipline from the user's resume — see _level_qualify_queries.
        pivot_mode = _level_qualify_queries(req_queries, profile, pivot=pivot_mode)

        log_tag = f"[LI-SEARCH][{profile.get('email', user_id)}]"
        logger.info("%s queries=%s locations=%s pivot=%s",
                    log_tag, req_queries, req_locations, pivot_mode)

        # ── 1-day Cosmos cache for LinkedIn pool ──────────────────────
        import hashlib
        _cache_sig = hashlib.sha1(
            f"{sorted(req_queries)}|{sorted(req_locations)}".encode()
        ).hexdigest()[:16]
        _cache_key = f"li-pool-{_cache_sig}"
        _cache_ttl = int(os.environ.get("LI_POOL_CACHE_TTL_S", str(24 * 3600)))
        cached_pool: list[dict] | None = None
        try:
            rows = query_items(
                "jobs",
                f"SELECT TOP 1 c.cards, c.cachedAt FROM c WHERE c.id = '{_cache_key}'",
                partition_key="linkedin-pool",
            )
            if rows:
                _cached = rows[0].get("cards")
                if isinstance(_cached, list) and _cached:
                    cached_pool = _cached
                    logger.info("%s POOL_CACHE HIT key=%s n=%d", log_tag, _cache_key, len(cached_pool))
                elif isinstance(_cached, list):
                    logger.info("%s POOL_CACHE empty for key=%s — refetching", log_tag, _cache_key)
        except Exception as e:
            logger.warning("%s POOL_CACHE read failed: %s", log_tag, e)

        # ── Fetch from LinkedIn if no cache ───────────────────────────
        if cached_pool is None:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time as _time

            # Pull up to 1000 jobs per (query, location) pair
            _li_pool_max = int(os.environ.get("LI_POOL_MAX_RESULTS", "1000"))
            pairs = [(q, l) for q in req_queries for l in req_locations]
            max_pairs = int(os.environ.get("LI_SEARCH_MAX_PAIRS", "6"))
            if max_pairs > 0 and len(pairs) > max_pairs:
                pairs = pairs[:max_pairs]
            search_deadline_s = float(os.environ.get("LI_SEARCH_DEADLINE_S", "150"))
            t_search_start = _time.time()
            all_cards: list[dict] = []
            seen_jids: set[str] = set()
            with ThreadPoolExecutor(max_workers=min(len(pairs), 4)) as pool:
                futs = {pool.submit(_li_bulk_fetch, q, l, _li_pool_max): (q, l) for q, l in pairs}
                for f in as_completed(futs):
                    q, l = futs[f]
                    try:
                        cards = f.result() or []
                    except Exception as e:
                        logger.warning("%s fetch failed (%s, %s): %s", log_tag, q, l, e)
                        cards = []
                    for c in cards:
                        jid = c.get("_li_jid")
                        if jid and jid not in seen_jids:
                            seen_jids.add(jid)
                            all_cards.append(c)
                    if _time.time() - t_search_start > search_deadline_s:
                        logger.warning("%s search deadline exceeded, cancelling remaining", log_tag)
                        for x in futs:
                            if not x.done():
                                x.cancel()
                        break
            cached_pool = all_cards
            logger.info("%s fetched %d LinkedIn cards across %d pairs", log_tag, len(cached_pool), len(pairs))
            # Write to Cosmos cache (skip empty pools — a transient LinkedIn
            # failure should not poison the cache for 24h).
            if cached_pool:
                try:
                    upsert_item("jobs", {
                        "id": _cache_key,
                        "companyId": "linkedin-pool",
                        "kind": "li_pool_cache",
                        "cards": cached_pool,
                        "queries": req_queries,
                        "locations": req_locations,
                        "cachedAt": datetime.now(timezone.utc).isoformat(),
                        "ttl": _cache_ttl,
                    })
                    logger.info("%s POOL_CACHE WRITE key=%s n=%d ttl=%ds", log_tag, _cache_key, len(cached_pool), _cache_ttl)
                except Exception as e:
                    logger.warning("%s POOL_CACHE write failed: %s", log_tag, e)

        # ── Convert cards to job dicts, auto-group by employer ────────
        scrape_now = datetime.now(timezone.utc).isoformat()
        jobs: list[dict] = []
        for c in cached_pool:
            employer = (c.get("employer") or "").strip()
            company_name = employer or "Other"
            jobs.append({
                "id": f"li-{c['_li_jid']}",
                "company": company_name,
                "companyId": "linkedin",
                "title": c.get("title", ""),
                "location": c.get("location", ""),
                "url": c.get("url", ""),
                "skills": _extract_skills(c.get("title", "")),
                "postedAt": None,
                "firstSeenAt": scrape_now,
                "source": "linkedin",
            })

        if not jobs:
            return success_response({
                "groups": [], "totalFound": 0, "source": "linkedin",
            })

        # Location filter
        if req_locations and any(l.strip() for l in req_locations):
            jobs = _filter_jobs_by_location(jobs, req_locations, log_tag)

        matched = match_jobs_to_profile(
            jobs, profile,
            search_queries=req_queries, pivot=pivot_mode,
        )
        logger.info("%s MATCHED %d/%d jobs", log_tag, len(matched), len(jobs))

        # ── Embedding + scoring ───────────────────────────────────────
        from shared.embeddings import EMBEDDING_DIMS
        profile_embedding = profile.get("profileEmbedding", [])
        if profile_embedding and len(profile_embedding) != EMBEDDING_DIMS:
            profile_embedding = []
        if not profile_embedding:
            if not profile.get("aiSummary"):
                ai_summary = generate_profile_summary(profile)
                if ai_summary:
                    profile["aiSummary"] = ai_summary
            profile_text = profile_to_text(profile)
            if profile_text:
                profile_embedding = generate_embedding(profile_text)
                if profile_embedding:
                    profile["profileEmbedding"] = profile_embedding
                    profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
                    try:
                        upsert_item("profiles", profile)
                    except Exception:
                        pass

        try:
            _li_vec_window = int(os.environ.get("LI_VECTOR_WINDOW", "200"))
        except (TypeError, ValueError):
            _li_vec_window = 200
        top_jobs = matched[:_li_vec_window]
        if profile_embedding:
            texts = [job_to_text(j) for j in top_jobs]
            embs = generate_embeddings_batch(texts)
            for job, emb in zip(top_jobs, embs):
                if emb:
                    job["_emb"] = emb
                    sim = cosine_similarity(profile_embedding, emb)
                    job["vectorScore"] = round(sim * 100)
                    raw = (sim * 100 * 0.5
                           + job.get("matchScore", 0) * 0.3
                           + job.get("recencyScore", 50) * 0.2)
                    job["aiScore"] = _calibrate_score(raw)
                else:
                    job["vectorScore"] = 0
                    job["aiScore"] = _calibrate_score(job.get("matchScore", 0))
            top_jobs.sort(key=lambda x: x.get("aiScore", 0), reverse=True)

        # ── Rerank ────────────────────────────────────────────────────
        try:
            try:
                _li_rerank_cap = int(os.environ.get("LI_RERANK_TOP_N", "50"))
            except (TypeError, ValueError):
                _li_rerank_cap = 50
            rerank_n = min(_li_rerank_cap, len(top_jobs))
            if rerank_n >= 3 and not _should_skip_rerank(top_jobs):
                rerank_slice = top_jobs[:rerank_n]
                _ai_rerank_top_jobs(rerank_slice, profile, "linkedin")
                for j in rerank_slice:
                    rs = j.get("aiReasoningScore")
                    if rs is not None:
                        raw = (rs * 0.60
                               + j.get("vectorScore", 0) * 0.25
                               + j.get("matchScore", 0) * 0.15)
                        j["aiScore"] = _calibrate_score(raw)
                top_jobs = [j for j in top_jobs
                            if not j.get("aiDrop")
                            and j.get("aiReasoningScore", 100) >= int(os.environ.get("RERANK_GATE_SECONDARY", "35"))]
                top_jobs.sort(key=lambda x: x.get("aiScore", 0), reverse=True)
        except Exception as e:
            logger.warning("%s LLM rerank failed: %s", log_tag, e)

        # ── Group by employer ─────────────────────────────────────────
        try:
            _li_display_cap = int(os.environ.get("LI_DISPLAY_CAP", "200"))
        except (TypeError, ValueError):
            _li_display_cap = 200
        display_jobs = top_jobs[:_li_display_cap]
        for _j in display_jobs:
            _j.pop("_aiReasonInternal", None)
            _j.pop("_emb", None)

        # Build per-employer groups
        from collections import OrderedDict
        employer_groups: dict[str, list[dict]] = OrderedDict()
        for j in display_jobs:
            emp = j.get("company") or "Other"
            employer_groups.setdefault(emp, []).append(j)

        groups: list[dict] = []
        for emp, emp_jobs in employer_groups.items():
            groups.append({
                "company": emp,
                "companyId": "linkedin",
                "jobs": emp_jobs,
                "count": len(emp_jobs),
                "source": "linkedin",
            })
        # Sort groups: most jobs first
        groups.sort(key=lambda g: max((j.get("aiScore", 0) for j in g["jobs"]), default=0), reverse=True)

        logger.info("%s RESULT: %d groups, %d total jobs across %d employers",
                    log_tag, len(groups), len(display_jobs), len(employer_groups))

        emb_pairs = [(j, j.get("_emb")) for j in display_jobs if j.get("_emb")]
        if emb_pairs:
            _persist_job_vectors(user_id, emb_pairs)
        _persist_discover_results(user_id, groups, merge=True)

        # ── Record LinkedIn run telemetry ──
        try:
            import time as _t2
            _li_run_companies = []
            for g in groups:
                _li_run_companies.append({
                    "companyId": "linkedin",
                    "company": g.get("company", ""),
                    "scraped": g.get("count", 0),
                    "displayed": len(g.get("jobs") or []),
                })
            _record_discover_run(
                user_id=user_id,
                email=profile.get("email", ""),
                run_type="linkedin",
                queries=req_queries[:5],
                locations=req_locations[:5],
                duration_ms=int((time.time() - time.time()) * 1000) if not locals().get("_t0") else 0,
                companies=_li_run_companies,
                linkedin_pool_size=len(cached_pool),
            )
        except Exception:
            pass

        # Also return flat list for backward compat with Flutter
        return success_response({
            "groups": groups,
            "jobs": display_jobs,
            "count": len(matched),
            "totalFound": len(matched),
            "analyzed": len(top_jobs),
            "companiesFound": len(employer_groups),
            "poolSize": len(cached_pool),
            "source": "linkedin",
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error in LinkedIn search")
        return internal_error_response(str(e))


@bp.route(route="api/v1/jobs/external/search", methods=["POST"])
def discover_external_jobs(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/jobs/external/search — Neutral-named alias for LinkedIn search.

    Some browser ad/tracker blockers silently drop POST requests whose URL
    contains 'linkedin'. Exposing the same handler under a non-branded path
    bypasses those blockers while keeping the original route alive.
    """
    return discover_linkedin_jobs(req)


@bp.route(route="api/v1/jobs/results", methods=["GET"])
def get_job_results(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/jobs/results — Get cached job discovery results."""
    try:
        user_id = get_user_id(req)

        result_doc = read_item("job_results", f"results-{user_id}", user_id)
        if not result_doc:
            return success_response({"jobs": [], "totalFound": 0, "message": "No results yet. Tap Discover Jobs."})

        return success_response({
            "jobs": result_doc.get("jobs", []),
            "grouped": result_doc.get("grouped", []),
            "totalFound": result_doc.get("totalFound", 0),
            "companiesScraped": result_doc.get("companiesScraped", {}),
            "scrapedAt": result_doc.get("scrapedAt", ""),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error getting results")
        return internal_error_response(str(e))
