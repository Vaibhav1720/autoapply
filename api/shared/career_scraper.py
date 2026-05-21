"""Career scraper — fetches real jobs via internal APIs with deep links.

Companies with working JSON APIs return real job URLs.
Companies without APIs get search-page URLs with job title pre-filled.
"""

import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

# ── Tunable scrape limits ────────────────────────────────────────────────────
# Per-company cap for results returned by each scraper. Override via env var
# at runtime (no redeploy needed). Most JSON ATSes (Greenhouse/Lever/Ashby)
# return ALL jobs in one call so this just controls how many we keep; for
# paginated APIs (Amazon, Microsoft, JPMorgan, Netflix, Uber) we loop until
# we've collected up to this many.
_SCRAPE_MAX = int(os.environ.get("SCRAPE_MAX_PER_COMPANY", "100"))
# Cap for the shared LinkedIn bulk-search (one call covers many companies).
_LI_BULK_MAX = int(os.environ.get("LI_BULK_MAX_RESULTS", "200"))


# ── Tunable matcher knobs ────────────────────────────────────────────────────
# All scoring weights, the discipline-mismatch penalty, and the LinkedIn
# cache parameters are exposed as env vars so we can A/B without redeploy.
# The defaults reproduce the behaviour shipped at v16 / v21 exactly.
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Final blend weights. Sum should ~=1.0 (we round to int after).
_W_SKILL = _envf("MATCH_W_SKILL", 0.18)
_W_TITLE = _envf("MATCH_W_TITLE", 0.20)
_W_LOC   = _envf("MATCH_W_LOC",   0.15)
_W_EXP   = _envf("MATCH_W_EXP",   0.32)
_W_REC   = _envf("MATCH_W_REC",   0.15)
# Soft demotion when the job's discipline doesn't overlap with the user's.
# 0.6 = keep 60% of the skill score; LLM rerank still gets a vote.
_DISC_PENALTY = _envf("MATCH_DISC_PENALTY", 0.6)
# LinkedIn cache: 30 min default (was 10 min). Acceptable staleness for job
# search; meaningfully reduces LinkedIn 429s under concurrent load.
_LI_CACHE_TTL_S_DEFAULT = _envi("LI_CACHE_TTL_S", 1800)
_LI_CACHE_MAX = _envi("LI_CACHE_MAX_ENTRIES", 200)


# ── Region-aware seniority mapping ──────────────────────────────────────────
# Loaded once from shared/data/level_mappings.json. Each region key (IN/UK/DE
# /etc, matching _CITY_TO_COUNTRY bucket strings) supplies overrides on top of
# the "default" entry. Hard kill-switch: LEVEL_MAPPINGS_DISABLE=1 forces every
# region back to the default (used for emergency rollback without redeploy).
_LEVEL_MAPPINGS: dict[str, dict[str, int]] = {}


def _load_level_mappings() -> dict[str, dict[str, int]]:
    """Load level_mappings.json once. Returns {} on any failure so the caller
    falls back to the in-code default. Strips any keys starting with '_'
    (used for inline JSON comments)."""
    if os.environ.get("LEVEL_MAPPINGS_DISABLE", "").strip() in ("1", "true", "yes"):
        return {}
    try:
        import json
        path = os.path.join(os.path.dirname(__file__), "data", "level_mappings.json")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out: dict[str, dict[str, int]] = {}
        for region, mapping in raw.items():
            if region.startswith("_") or not isinstance(mapping, dict):
                continue
            clean = {k: int(v) for k, v in mapping.items()
                     if not k.startswith("_") and isinstance(v, (int, float))}
            if clean:
                out[region] = clean
        return out
    except Exception as e:
        logger.warning("level_mappings.json load failed (%s) — using in-code default only", e)
        return {}


_LEVEL_MAPPINGS = _load_level_mappings()


def _user_region(user_country_buckets: set[str]) -> str:
    """Pick a single region key from the user's country buckets. Returns
    'default' if no buckets or no override exists. Order matters: India is
    checked first (largest non-US user base), then EU pool, then UK, else
    US/default. We deliberately don't compose multiple region overrides (a
    user listing both Bangalore and London still gets one bucket); pick the
    most distinctive one to avoid silently merging conflicting tables."""
    if not user_country_buckets:
        return "default"
    # IN dominates if present (Indian leveling is most aggressive shift).
    if "IN" in user_country_buckets and "IN" in _LEVEL_MAPPINGS:
        return "IN"
    # Then any continental EU bucket.
    for cc in ("DE", "FR", "NL", "IE", "CH", "SE", "ES", "IT", "PL", "PT"):
        if cc in user_country_buckets and cc in _LEVEL_MAPPINGS:
            return cc
    if "UK" in user_country_buckets and "UK" in _LEVEL_MAPPINGS:
        return "UK"
    return "default"


def _resolve_level_min_years(base: dict[str, int], region: str) -> dict[str, int]:
    """Merge region overrides onto the base mapping. Region 'default' or
    unknown returns base unchanged. Pure function; safe to call per request."""
    if region == "default" or region not in _LEVEL_MAPPINGS:
        return base
    merged = dict(base)
    merged.update(_LEVEL_MAPPINGS[region])
    return merged


_H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _now_iso() -> str:
    """Single source of truth for "when did the scraper run"."""
    return datetime.now(timezone.utc).isoformat()


def _real_posted(value) -> str | None:
    """Normalise a source-supplied posting date into an ISO string, or
    return None if the value is missing / obviously bogus. We never fall
    back to "now" here — callers should set firstSeenAt=_now_iso() and
    leave postedAt=None when the source gives us nothing."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Numeric epoch (ms or s) — some ATSes return millis, some seconds.
    try:
        n = float(s)
        if n <= 0:
            return None
        if n > 10_000_000_000:  # millis
            n = n / 1000.0
        return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    # Already an ISO-ish string — return as-is. Downstream scoring uses
    # `datetime.fromisoformat(...)` which tolerates the common ATS shapes.
    return s


# ── Company Registry ─────────────────────────────────────────────────────────

COMPANIES = {
    "comp-amazon": {
        "id": "comp-amazon", "name": "Amazon", "industry": "Technology",
        "careersUrl": "https://www.amazon.jobs/en/search",
        "searchUrl": "https://www.amazon.jobs/en/search?base_query={query}",
        "description": "E-commerce, AWS cloud, Alexa, logistics, and streaming.",
    },
    "comp-uber": {
        "id": "comp-uber", "name": "Uber", "industry": "Technology",
        "careersUrl": "https://www.uber.com/us/en/careers/list/",
        "searchUrl": "https://www.uber.com/us/en/careers/list/?query={query}",
        "description": "Rides, delivery, freight, and autonomous driving platform.",
    },
    "comp-netflix": {
        "id": "comp-netflix", "name": "Netflix", "industry": "Entertainment",
        "careersUrl": "https://explore.jobs.netflix.net/careers",
        "searchUrl": "https://explore.jobs.netflix.net/careers?query={query}",
        "description": "Streaming, content production, and recommendation AI.",
    },
    "comp-stripe": {
        "id": "comp-stripe", "name": "Stripe", "industry": "Fintech",
        "careersUrl": "https://stripe.com/jobs/search",
        "searchUrl": "https://stripe.com/jobs/search?query={query}",
        "description": "Payment infrastructure, financial APIs, and billing.",
    },
    "comp-google": {
        "id": "comp-google", "name": "Google", "industry": "Technology",
        "careersUrl": "https://www.google.com/about/careers/applications/jobs/results/",
        "searchUrl": "https://www.google.com/about/careers/applications/jobs/results/?q={query}",
        "description": "Search, cloud, AI, Android, and advertising.",
    },
    "comp-microsoft": {
        "id": "comp-microsoft", "name": "Microsoft", "industry": "Technology",
        "careersUrl": "https://jobs.careers.microsoft.com/global/en/search",
        "searchUrl": "https://jobs.careers.microsoft.com/global/en/search?q={query}",
        "description": "Cloud (Azure), Office 365, Windows, Xbox, and Copilot.",
    },
    "comp-meta": {
        "id": "comp-meta", "name": "Meta", "industry": "Technology",
        "careersUrl": "https://www.metacareers.com/jobs/",
        "searchUrl": "https://www.metacareers.com/jobs/?query={query}",
        "description": "Facebook, Instagram, WhatsApp, and AI research.",
    },
    "comp-apple": {
        "id": "comp-apple", "name": "Apple", "industry": "Technology",
        "careersUrl": "https://jobs.apple.com/en-us/search",
        "searchUrl": "https://jobs.apple.com/en-us/search?search={query}",
        "description": "iPhone, Mac, iOS, Apple Intelligence, and services.",
    },
    "comp-salesforce": {
        "id": "comp-salesforce", "name": "Salesforce", "industry": "Enterprise Software",
        "careersUrl": "https://careers.salesforce.com/en/jobs/",
        "searchUrl": "https://careers.salesforce.com/en/jobs/?search={query}",
        "description": "CRM, enterprise cloud, Slack, and Einstein AI.",
    },
    "comp-adobe": {
        "id": "comp-adobe", "name": "Adobe", "industry": "Creative Software",
        "careersUrl": "https://careers.adobe.com/us/en/search-results",
        "searchUrl": "https://careers.adobe.com/us/en/search-results?keywords={query}",
        "description": "Creative Cloud, Photoshop, and Firefly AI.",
    },
    # ── Banks ──
    "comp-goldman": {
        "id": "comp-goldman", "name": "Goldman Sachs", "industry": "Investment Banking",
        "careersUrl": "https://higher.gs.com/roles",
        "searchUrl": "https://higher.gs.com/roles?query={query}",
        "description": "Investment banking, securities, asset management, and fintech.",
    },
    "comp-jpmorgan": {
        "id": "comp-jpmorgan", "name": "JPMorgan Chase", "industry": "Banking",
        "careersUrl": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions",
        "searchUrl": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={query}",
        "description": "Global banking, payments, asset management, and technology.",
    },
    # ── Banks with LinkedIn-only scrapers — REMOVED ──
    # Morgan Stanley, UBS, HSBC, Deutsche Bank are now served exclusively
    # via the LinkedIn search tile.
    "comp-barclays": {
        "id": "comp-barclays", "name": "Barclays", "industry": "Banking",
        "careersUrl": "https://search.jobs.barclays/search-jobs/engineer",
        "searchUrl": "https://search.jobs.barclays/search-jobs/{query}",
        "description": "Consumer banking, corporate banking, and investment banking.",
    },
    "comp-citi": {
        "id": "comp-citi", "name": "Citibank", "industry": "Banking",
        "careersUrl": "https://jobs.citi.com/search-jobs/engineer",
        "searchUrl": "https://jobs.citi.com/search-jobs/{query}",
        "description": "Global banking, markets, consumer banking, and technology.",
    },
    # HSBC, Deutsche Bank: LinkedIn-only — removed; served via LinkedIn tile.
    "comp-bofa": {
        "id": "comp-bofa", "name": "Bank of America", "industry": "Banking",
        "careersUrl": "https://careers.bankofamerica.com/en-us",
        "searchUrl": "https://careers.bankofamerica.com/en-us/search-results?keywords={query}",
        "description": "Consumer banking, wealth management, and corporate banking.",
    },

    # ── Greenhouse-hosted (official career sites, real deep links) ──
    "comp-airbnb": {
        "id": "comp-airbnb", "name": "Airbnb", "industry": "Travel & Hospitality",
        "careersUrl": "https://careers.airbnb.com/", "searchUrl": "https://careers.airbnb.com/?search={query}",
        "description": "Home-sharing marketplace, experiences, and travel platform.",
    },
    "comp-anthropic": {
        "id": "comp-anthropic", "name": "Anthropic", "industry": "AI",
        "careersUrl": "https://www.anthropic.com/careers", "searchUrl": "https://www.anthropic.com/careers?q={query}",
        "description": "AI safety lab — Claude family of large language models.",
    },
    "comp-asana": {
        "id": "comp-asana", "name": "Asana", "industry": "Productivity Software",
        "careersUrl": "https://asana.com/jobs/all", "searchUrl": "https://asana.com/jobs/all?search={query}",
        "description": "Work management platform for teams.",
    },
    "comp-cloudflare": {
        "id": "comp-cloudflare", "name": "Cloudflare", "industry": "Networking & Security",
        "careersUrl": "https://www.cloudflare.com/careers/jobs/", "searchUrl": "https://www.cloudflare.com/careers/jobs/?q={query}",
        "description": "CDN, edge compute, DNS, Zero Trust security.",
    },
    "comp-databricks": {
        "id": "comp-databricks", "name": "Databricks", "industry": "Data & AI",
        "careersUrl": "https://www.databricks.com/company/careers/open-positions",
        "searchUrl": "https://www.databricks.com/company/careers/open-positions?keyword={query}",
        "description": "Lakehouse data platform, MLflow, and Mosaic AI.",
    },
    "comp-discord": {
        "id": "comp-discord", "name": "Discord", "industry": "Communication",
        "careersUrl": "https://discord.com/jobs", "searchUrl": "https://discord.com/jobs?q={query}",
        "description": "Voice, video, and text chat platform for communities.",
    },
    "comp-dropbox": {
        "id": "comp-dropbox", "name": "Dropbox", "industry": "Productivity Software",
        "careersUrl": "https://jobs.dropbox.com/all-jobs", "searchUrl": "https://jobs.dropbox.com/all-jobs?q={query}",
        "description": "Cloud storage, sync, and collaboration tools.",
    },
    "comp-duolingo": {
        "id": "comp-duolingo", "name": "Duolingo", "industry": "Education",
        "careersUrl": "https://careers.duolingo.com/", "searchUrl": "https://careers.duolingo.com/?q={query}",
        "description": "Language-learning app with gamified lessons.",
    },
    "comp-figma": {
        "id": "comp-figma", "name": "Figma", "industry": "Design Software",
        "careersUrl": "https://www.figma.com/careers/", "searchUrl": "https://www.figma.com/careers/?search={query}",
        "description": "Collaborative interface design and prototyping.",
    },
    "comp-gitlab": {
        "id": "comp-gitlab", "name": "GitLab", "industry": "Developer Tools",
        "careersUrl": "https://about.gitlab.com/jobs/", "searchUrl": "https://about.gitlab.com/jobs/?search={query}",
        "description": "End-to-end DevOps platform and source control.",
    },
    "comp-gusto": {
        "id": "comp-gusto", "name": "Gusto", "industry": "HR Tech",
        "careersUrl": "https://gusto.com/about/careers/jobs", "searchUrl": "https://gusto.com/about/careers/jobs?q={query}",
        "description": "Payroll, benefits, and HR for small businesses.",
    },
    "comp-instacart": {
        "id": "comp-instacart", "name": "Instacart", "industry": "Grocery Delivery",
        "careersUrl": "https://instacart.careers/", "searchUrl": "https://instacart.careers/?q={query}",
        "description": "Grocery delivery and pickup marketplace.",
    },
    "comp-lyft": {
        "id": "comp-lyft", "name": "Lyft", "industry": "Transportation",
        "careersUrl": "https://www.lyft.com/careers", "searchUrl": "https://www.lyft.com/careers?q={query}",
        "description": "Ridesharing, bikes, scooters, and autonomous mobility.",
    },
    "comp-mongodb": {
        "id": "comp-mongodb", "name": "MongoDB", "industry": "Databases",
        "careersUrl": "https://www.mongodb.com/careers", "searchUrl": "https://www.mongodb.com/careers?search={query}",
        "description": "Document database and Atlas cloud platform.",
    },
    "comp-pinterest": {
        "id": "comp-pinterest", "name": "Pinterest", "industry": "Social Media",
        "careersUrl": "https://www.pinterestcareers.com/jobs/", "searchUrl": "https://www.pinterestcareers.com/jobs/?keywords={query}",
        "description": "Visual discovery and inspiration platform.",
    },
    "comp-reddit": {
        "id": "comp-reddit", "name": "Reddit", "industry": "Social Media",
        "careersUrl": "https://www.redditinc.com/careers", "searchUrl": "https://www.redditinc.com/careers?q={query}",
        "description": "Front page of the internet — community-driven discussions.",
    },
    "comp-robinhood": {
        "id": "comp-robinhood", "name": "Robinhood", "industry": "Fintech",
        "careersUrl": "https://careers.robinhood.com/", "searchUrl": "https://careers.robinhood.com/?q={query}",
        "description": "Commission-free trading, crypto, and retirement.",
    },
    "comp-scaleai": {
        "id": "comp-scaleai", "name": "Scale AI", "industry": "AI",
        "careersUrl": "https://scale.com/careers", "searchUrl": "https://scale.com/careers?q={query}",
        "description": "Data labeling and evaluation infrastructure for AI.",
    },
    "comp-twilio": {
        "id": "comp-twilio", "name": "Twilio", "industry": "Communications",
        "careersUrl": "https://www.twilio.com/en-us/company/jobs", "searchUrl": "https://www.twilio.com/en-us/company/jobs?keyword={query}",
        "description": "Programmable messaging, voice, and customer engagement.",
    },
    "comp-vercel": {
        "id": "comp-vercel", "name": "Vercel", "industry": "Developer Tools",
        "careersUrl": "https://vercel.com/careers", "searchUrl": "https://vercel.com/careers?search={query}",
        "description": "Front-end cloud — Next.js framework and edge platform.",
    },

    # ── Ashby-hosted (official Ashby job board pages) ──
    "comp-cohere": {
        "id": "comp-cohere", "name": "Cohere", "industry": "AI",
        "careersUrl": "https://cohere.com/careers", "searchUrl": "https://cohere.com/careers?q={query}",
        "description": "Enterprise LLM platform and embedding models.",
    },
    "comp-mistral": {
        "id": "comp-mistral", "name": "Mistral AI", "industry": "AI",
        "careersUrl": "https://mistral.ai/careers/", "searchUrl": "https://mistral.ai/careers/?q={query}",
        "description": "Open-weight large language models from Paris.",
    },
    "comp-perplexity": {
        "id": "comp-perplexity", "name": "Perplexity", "industry": "AI",
        "careersUrl": "https://www.perplexity.ai/hub/careers", "searchUrl": "https://www.perplexity.ai/hub/careers?q={query}",
        "description": "Conversational answer engine.",
    },
    "comp-linear": {
        "id": "comp-linear", "name": "Linear", "industry": "Developer Tools",
        "careersUrl": "https://linear.app/careers", "searchUrl": "https://linear.app/careers?q={query}",
        "description": "Issue tracking and project management for software teams.",
    },
    "comp-supabase": {
        "id": "comp-supabase", "name": "Supabase", "industry": "Developer Tools",
        "careersUrl": "https://supabase.com/careers", "searchUrl": "https://supabase.com/careers?q={query}",
        "description": "Open-source Firebase alternative with Postgres.",
    },
    "comp-posthog": {
        "id": "comp-posthog", "name": "PostHog", "industry": "Developer Tools",
        "careersUrl": "https://posthog.com/careers", "searchUrl": "https://posthog.com/careers?q={query}",
        "description": "Open-source product analytics and feature flags.",
    },
    "comp-ramp": {
        "id": "comp-ramp", "name": "Ramp", "industry": "Fintech",
        "careersUrl": "https://ramp.com/careers", "searchUrl": "https://ramp.com/careers?q={query}",
        "description": "Corporate cards and finance automation.",
    },
    "comp-writer": {
        "id": "comp-writer", "name": "Writer", "industry": "AI",
        "careersUrl": "https://writer.com/careers/", "searchUrl": "https://writer.com/careers/?q={query}",
        "description": "Generative AI platform for the enterprise.",
    },
    "comp-decagon": {
        "id": "comp-decagon", "name": "Decagon", "industry": "AI",
        "careersUrl": "https://decagon.ai/careers", "searchUrl": "https://decagon.ai/careers?q={query}",
        "description": "AI customer service agents for enterprises.",
    },

    # ── Lever-hosted (Indian fintech) ──
    "comp-cred": {
        "id": "comp-cred", "name": "CRED", "industry": "Fintech",
        "careersUrl": "https://jobs.lever.co/cred", "searchUrl": "https://jobs.lever.co/cred?search={query}",
        "description": "Credit-card payments, rewards, and lending in India.",
    },
    # ═════════════════════════════════════════════════════════════════════
    # India-expansion + GCC companies REMOVED from COMPANIES dict.
    # These had no native ATS scraper — only LinkedIn-by-name. Their jobs
    # are now surfaced exclusively via the LinkedIn search tile which
    # fetches 1000 jobs, auto-groups by employer, and caches for 1 day.
    # ═════════════════════════════════════════════════════════════════════
}


# ── Phase 5: Overlay companies.json on top of the embedded COMPANIES dict ──
# This lets ops edit company metadata (URLs, descriptions, names) without a
# code redeploy. The embedded dict above remains the floor: if the JSON file
# is missing, malformed, or kill-switched via `COMPANIES_REGISTRY_DISABLE=1`,
# behavior is identical to pre-Phase-5.
#
# JSON contract (api/shared/data/companies.json):
#   { "_comment": "...", "companies": { "<comp-id>": {<fields>}, ... } }
# Each entry must have at minimum `id`, `name`, `industry`, `careersUrl`,
# `searchUrl`, `description`. The Phase-5 routing fields (`ats`, `atsBoard`,
# `linkedinId`) are tolerated but currently consumed only by the migration
# script, not by the scraper runtime. They exist for the future routing
# refactor (out of scope for this phase).
def _overlay_companies_from_json(base: dict[str, dict]) -> dict[str, dict]:
    """Merge JSON companies on top of the in-code dict. Per-key shallow
    merge so JSON edits to a single field (e.g. `careersUrl`) don't have
    to repeat the whole entry. Returns a new dict; never mutates `base`.
    Failure modes (missing file, parse error, missing top-level
    `companies` key) all return `base` unchanged with a warning."""
    if os.environ.get("COMPANIES_REGISTRY_DISABLE", "").strip() in ("1", "true", "yes"):
        return base
    try:
        import json
        path = os.path.join(os.path.dirname(__file__), "data", "companies.json")
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        overlay = payload.get("companies") if isinstance(payload, dict) else None
        if not isinstance(overlay, dict):
            logger.warning("companies.json missing top-level 'companies' object — using embedded only")
            return base
        merged = {cid: dict(info) for cid, info in base.items()}
        new_count = 0
        for cid, entry in overlay.items():
            if not isinstance(entry, dict) or not isinstance(cid, str):
                continue
            if cid in merged:
                merged[cid].update(entry)  # JSON wins per-field
            else:
                merged[cid] = dict(entry)
                new_count += 1
        if new_count:
            logger.info("companies.json added %d company entries on top of embedded dict", new_count)
        return merged
    except FileNotFoundError:
        # Local dev / fresh checkout where the JSON hasn't been generated —
        # silent; the embedded dict is the source of truth.
        return base
    except Exception as e:
        logger.warning("companies.json overlay failed (%s) — using embedded dict only", e)
        return base


COMPANIES = _overlay_companies_from_json(COMPANIES)


def get_company_list() -> list[dict]:
    """Return companies that have a native scraper (i.e. can be searched
    via per-company discover). LinkedIn-only companies are excluded —
    their jobs are surfaced via the separate LinkedIn search tile."""
    return [
        {"id": v["id"], "name": v["name"], "industry": v["industry"],
         "careersUrl": v["careersUrl"], "description": v["description"]}
        for v in COMPANIES.values()
        if v["id"] in _API_SCRAPERS
    ]


# ── API Scrapers (return real deep links) ────────────────────────────────────

def _api_amazon(query: str = "", location: str = "") -> list[dict]:
    """Amazon Jobs JSON API — returns real per-job URLs. Paginates up to _SCRAPE_MAX results."""
    jobs = []
    seen = set()
    for offset in range(0, _SCRAPE_MAX, 25):
        params = {
            "base_query": query or "software",
            "result_limit": "25",
            "offset": str(offset),
            "sort": "recent",
        }
        if location:
            params["loc_query"] = location
        try:
            resp = requests.get("https://www.amazon.jobs/en/search.json",
                params=params, headers=_H, timeout=15)
            resp.raise_for_status()
            page = resp.json().get("jobs", []) or []
            if not page:
                break
            for j in page:
                jid = j.get("id_icims") or j.get("id")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                jobs.append({
                    "id": f"amz-{jid}", "company": "Amazon", "companyId": "comp-amazon",
                    "title": j.get("title", ""),
                    "location": j.get("normalized_location", j.get("location", "")),
                    "url": f"https://www.amazon.jobs{j.get('job_path','')}",
                    "skills": _extract_skills(j.get("title", "")),
                    "postedAt": _real_posted(j.get("posted_date")),
                    "firstSeenAt": _now_iso(),
                })
            if len(page) < 25:
                break  # fewer than a full page = no more results
        except Exception as e:
            logger.warning("[AMAZON] page offset=%d failed: %s", offset, e)
            break
    return jobs[:_SCRAPE_MAX]


def _flatten_uber_location(raw: object) -> str:
    """Coerce Uber's mixed location payload into a single human-readable
    'City, Region, Country' string.

    Uber returns one of:
      - "" (empty string)
      - "Sunnyvale, California, USA" (already a string)
      - ["Sunnyvale, CA", "San Francisco, CA"] (list of strings)
      - {"city":"Sunnyvale","region":"California","country":"USA"} (dict)
      - [{"city":...,"region":...,"country":...}, ...] (list of dicts)

    Without normalization, `str(dict)` ends up in the location field which
    breaks city matching downstream and renders as a Python literal in the
    UI ("{'country': 'USA', 'region': 'California', 'city': 'Sunnyvale'}").
    """
    def _one(v: object) -> str:
        if isinstance(v, dict):
            parts = [str(v.get(k, "")).strip() for k in ("city", "region", "country")]
            return ", ".join(p for p in parts if p)
        if isinstance(v, str):
            return v.strip()
        return ""
    if not raw:
        return ""
    if isinstance(raw, list) and raw:
        return _one(raw[0])
    return _one(raw)


def _api_uber(query: str = "", location: str = "") -> list[dict]:
    """Uber careers API — returns job IDs (deep link when slug available). Paginates up to _SCRAPE_MAX."""
    jobs = []
    seen = set()
    pages = max(1, (_SCRAPE_MAX + 24) // 25)
    for page in range(pages):
        payload = {"params": {"location": [], "department": [], "team": []}, "limit": 25, "page": page}
        if query:
            payload["params"]["query"] = query
        if location:
            payload["params"]["location"] = [location]
        try:
            resp = requests.post("https://www.uber.com/api/loadSearchJobsResults",
                json=payload, headers={**_H, "x-csrf-token": "x", "Content-Type": "application/json"},
                timeout=15)
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("results", []) or []
        except Exception as e:
            logger.warning("[UBER] page=%d failed: %s", page, e)
            break
        if not results:
            break
        for r in results:
            jid = r.get("id", "")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            url = f"https://www.uber.com/global/en/careers/list/{jid}/"
            # Uber's API returns `location` as either a string, a list of
            # strings, OR a dict like {"country":"USA","region":"California",
            # "city":"Sunnyvale"} (and `allLocations` is a list of such dicts).
            # Without flattening, we get `str({...})` which is unusable for
            # city matching and renders as a Python literal in the UI.
            raw_loc = r.get("location") or r.get("allLocations")
            loc = _flatten_uber_location(raw_loc)
            jobs.append({"id": f"uber-{jid}", "company": "Uber", "companyId": "comp-uber",
                         "title": r.get("title", ""), "location": loc, "url": url,
                         "skills": _extract_skills(r.get("title", "")),
                         "postedAt": _real_posted(r.get("createdAt")),
                         "firstSeenAt": _now_iso()})
        if len(results) < 25 or len(jobs) >= _SCRAPE_MAX:
            break
    return jobs[:_SCRAPE_MAX]


def _api_netflix(query: str = "", location: str = "") -> list[dict]:
    """Netflix Phenom API — returns canonicalPositionUrl deep links."""
    params = {"domain": "netflix.com", "limit": str(_SCRAPE_MAX), "sort_by": "relevance"}
    if query:
        params["query"] = query
    if location:
        params["location"] = location
    resp = requests.get("https://explore.jobs.netflix.net/api/apply/v2/jobs",
        params=params, headers=_H, timeout=15)
    resp.raise_for_status()
    return [
        {"id": f"nflx-{j['id']}", "company": "Netflix", "companyId": "comp-netflix",
         "title": j.get("name", j.get("posting_name", "")),
         "location": j.get("location", ""),
         "url": j.get("canonicalPositionUrl", ""),
         "skills": _extract_skills(j.get("name", "")),
         "postedAt": _real_posted(j.get("t_create")),
         "firstSeenAt": _now_iso()}
        for j in resp.json().get("positions", [])[:_SCRAPE_MAX]
    ]


def _api_stripe(query: str = "", location: str = "") -> list[dict]:
    """Stripe via Greenhouse API — returns absolute_url deep links."""
    return _api_greenhouse_generic("stripe", "Stripe", "comp-stripe", query, location)


# ── Generic ATS scrapers ─────────────────────────────────────────────────────
# Each ATS exposes a public read-only API. The job URL returned by these APIs
# always points to the company's OFFICIAL career site (not the ATS host) — the
# ATS owners include the canonical hosted URL in every response.

def _api_greenhouse_generic(board: str, company_name: str, company_id: str,
                            query: str = "", location: str = "") -> list[dict]:
    """Greenhouse public job board. Returns real `absolute_url` deep links to
    each company's official career site (e.g. https://careers.airbnb.com/positions/{id}).
    Endpoint: https://api.greenhouse.io/v1/boards/{token}/jobs?content=true
    """
    try:
        resp = requests.get(f"https://api.greenhouse.io/v1/boards/{board}/jobs",
            params={"content": "true"}, headers=_H, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[GREENHOUSE] %s board=%s failed: %s", company_name, board, e)
        return []
    jobs = resp.json().get("jobs", []) or []
    if query:
        q = query.lower()
        jobs = [j for j in jobs if q in (j.get("title", "") or "").lower()]
    if location:
        ll = location.lower()
        def _has_loc(j):
            loc_obj = j.get("location") or {}
            if isinstance(loc_obj, dict):
                if ll in (loc_obj.get("name", "") or "").lower():
                    return True
                for sub in loc_obj.get("locations", []) or []:
                    if ll in (sub.get("name", "") or "").lower():
                        return True
            return False
        jobs = [j for j in jobs if _has_loc(j)]

    out = []
    cid_short = company_id.replace("comp-", "")
    for j in jobs[:_SCRAPE_MAX]:
        loc_obj = j.get("location") or {}
        if isinstance(loc_obj, dict):
            sublocs = loc_obj.get("locations", []) or []
            if sublocs:
                loc_str = ", ".join(s.get("name","") for s in sublocs if s.get("name"))
            else:
                loc_str = loc_obj.get("name","") or ""
        else:
            loc_str = str(loc_obj)
        # Some Greenhouse customers configure absolute_url to a generic
        # search/listing page with the job id only in the QUERY STRING (e.g.
        # Stripe -> "https://stripe.com/jobs/search?gh_jid=X", Pinterest ->
        # "https://www.pinterestcareers.com/jobs/?gh_jid=X"). Those land on
        # the listing page, not the actual job. Detect by checking whether
        # the job id appears in the URL path (not just query) and fall back
        # to the canonical Greenhouse hosted URL otherwise.
        absolute = j.get("absolute_url", "") or ""
        canonical = f"https://job-boards.greenhouse.io/{board}/jobs/{j['id']}"
        from urllib.parse import urlparse as _up
        path_only = _up(absolute).path if absolute else ""
        if absolute and str(j["id"]) in path_only:
            url = absolute
        else:
            url = canonical
        out.append({
            "id": f"{cid_short}-{j['id']}",
            "company": company_name, "companyId": company_id,
            "title": j.get("title", ""),
            "location": loc_str,
            "url": url,
            "skills": _extract_skills(j.get("title", "")),
            "postedAt": _real_posted(j.get("updated_at") or j.get("first_published")),
            "firstSeenAt": _now_iso(),
        })
    return out


def _api_lever_generic(board: str, company_name: str, company_id: str,
                       query: str = "", location: str = "") -> list[dict]:
    """Lever public postings. Returns `hostedUrl` deep links to the official
    Lever-hosted job page (used as the company's career site).
    Endpoint: https://api.lever.co/v0/postings/{board}?mode=json
    """
    try:
        resp = requests.get(f"https://api.lever.co/v0/postings/{board}",
            params={"mode": "json"}, headers=_H, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[LEVER] %s board=%s failed: %s", company_name, board, e)
        return []
    data = resp.json()
    items = data if isinstance(data, list) else []
    if query:
        q = query.lower()
        items = [j for j in items if q in (j.get("text", "") or "").lower()]
    if location:
        ll = location.lower()
        items = [j for j in items if ll in str((j.get("categories") or {}).get("location", "")).lower()
                 or any(ll in str(loc).lower() for loc in (j.get("categories") or {}).get("allLocations", []) or [])]

    out = []
    cid_short = company_id.replace("comp-", "")
    for j in items[:_SCRAPE_MAX]:
        cats = j.get("categories") or {}
        loc = cats.get("location", "")
        if cats.get("allLocations"):
            loc = ", ".join(cats["allLocations"][:3])
        title = j.get("text", "")
        out.append({
            "id": f"{cid_short}-{j.get('id','')}",
            "company": company_name, "companyId": company_id,
            "title": title, "location": loc or "",
            "url": j.get("hostedUrl") or j.get("applyUrl") or "",
            "skills": _extract_skills(title),
            "postedAt": _real_posted(j.get("createdAt")),
            "firstSeenAt": _now_iso(),
        })
    return out


def _api_ashby_generic(board: str, company_name: str, company_id: str,
                       query: str = "", location: str = "") -> list[dict]:
    """Ashby public job board. Returns `jobUrl` deep links to the official
    Ashby-hosted job page (the canonical career site for these companies).
    Endpoint: https://api.ashbyhq.com/posting-api/job-board/{board}
    """
    try:
        resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
            headers=_H, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[ASHBY] %s board=%s failed: %s", company_name, board, e)
        return []
    items = resp.json().get("jobs", []) or []
    if query:
        q = query.lower()
        items = [j for j in items if q in (j.get("title", "") or "").lower()]
    if location:
        ll = location.lower()
        items = [j for j in items if ll in (j.get("location", "") or "").lower()
                 or any(ll in (s.get("location","") or "").lower() for s in (j.get("secondaryLocations") or []))]

    out = []
    cid_short = company_id.replace("comp-", "")
    for j in items[:_SCRAPE_MAX]:
        loc = j.get("location", "") or ""
        sublocs = j.get("secondaryLocations") or []
        if sublocs:
            extra = ", ".join(s.get("location","") for s in sublocs[:3] if s.get("location"))
            if extra:
                loc = (loc + ", " + extra) if loc else extra
        out.append({
            "id": f"{cid_short}-{j.get('id','')}",
            "company": company_name, "companyId": company_id,
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "skills": _extract_skills(j.get("title", "")),
            "postedAt": _real_posted(j.get("publishedAt") or j.get("updatedAt")),
            "firstSeenAt": _now_iso(),
        })
    return out


# ── HTML-based scrapers (extract deep links from page) ───────────────────────

_H_HTML = {**_H, "Accept": "text/html,application/xhtml+xml"}


def _scrape_google_html(query: str = "", location: str = "") -> list[dict]:
    """Extract real job deep links from Google Careers <a> tags with numeric IDs."""
    params = {"q": query or "software engineer", "page": "1"}
    if location:
        params["location"] = location
    resp = requests.get(
        "https://www.google.com/about/careers/applications/jobs/results/",
        params=params,
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Match: jobs/results/137173877707940550-staff-software-engineer-...
        match = re.search(r'jobs/results/(\d{10,})-([a-z0-9-]+)', href)
        if match:
            job_num = match.group(1)
            slug = match.group(2)
            title = slug.replace("-", " ").title()
            clean_path = href.split("?")[0]
            if not clean_path.startswith("http"):
                url = f"https://www.google.com/about/careers/applications/{clean_path}"
            else:
                url = clean_path
            # Get location: check parent li, sibling spans, data attributes
            job_loc = ""
            parent = a.find_parent("li")
            container = parent or a.find_parent("div")
            if container:
                for s in container.find_all(["span", "p", "div", "small"]):
                    text = s.get_text(strip=True)
                    if text and len(text) < 60 and text != title:
                        # Look for location patterns: "City, Country" or known countries
                        if "," in text or any(kw in text.lower() for kw in [
                            "india", "usa", "remote", "bangalore", "hyderabad", "mumbai",
                            "singapore", "london", "dublin", "tokyo", "sydney", "berlin",
                            "new york", "mountain view", "sunnyvale", "seattle",
                        ]):
                            job_loc = text
                            break
            # Fallback: use the location search param if we have nothing
            if not job_loc and location:
                job_loc = location
            jobs.append({
                "id": f"google-{job_num}",
                "company": "Google", "companyId": "comp-google",
                "title": title, "location": job_loc, "url": url,
                "skills": _extract_skills(title),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })
    return jobs


def _scrape_salesforce_html(query: str = "", location: str = "") -> list[dict]:
    """Extract job deep links + locations from Salesforce Careers page."""
    params = {"search": query or "engineer", "page": "1"}
    if location:
        params["location"] = location
    resp = requests.get(
        "https://careers.salesforce.com/en/jobs/",
        params=params,
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for card in soup.find_all("div", class_="card-body"):
        a = card.find("a", href=re.compile(r'/en/jobs/jr\d+/'))
        if not a:
            continue
        href = a["href"]
        text = a.get_text(strip=True)
        if not text or len(text) <= 5:
            continue
        url = f"https://careers.salesforce.com{href}" if href.startswith("/") else href
        # Extract location from <ul class="list-inline locations"> inside the card
        loc_parts = []
        loc_ul = card.find("ul", class_="locations")
        if loc_ul:
            for li in loc_ul.find_all("li", class_="list-inline-item"):
                loc_text = li.get_text(strip=True)
                if loc_text and loc_text not in loc_parts:
                    loc_parts.append(loc_text)
        location_str = ", ".join(loc_parts)
        # Extract JR ID from URL
        jr_match = re.search(r'/(jr\d+)/', href)
        jr_id = jr_match.group(1) if jr_match else f"sf-{len(jobs)}"
        jobs.append({
            "id": jr_id, "company": "Salesforce", "companyId": "comp-salesforce",
            "title": text, "location": location_str, "url": url,
            "skills": _extract_skills(text),
            "postedAt": None,
            "firstSeenAt": _now_iso(),
        })
    return jobs


def _scrape_adobe_html(query: str = "", location: str = "") -> list[dict]:
    """Extract job deep links + locations from Adobe Careers page."""
    params = {"keywords": query or "engineer"}
    if location:
        params["location"] = location
    resp = requests.get(
        "https://careers.adobe.com/us/en/search-results",
        params=params,
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    import json as json_mod
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    # Try structured JSON-LD first
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json_mod.loads(script.string or "")
            items = data if isinstance(data, list) else data.get("itemListElement", [data])
            for item in items:
                posting = item.get("item", item) if isinstance(item, dict) else item
                if not isinstance(posting, dict) or posting.get("@type") != "JobPosting":
                    continue
                title = posting.get("title", "")
                url = posting.get("url", "")
                loc_obj = posting.get("jobLocation", {})
                loc_addr = loc_obj.get("address", {}) if isinstance(loc_obj, dict) else {}
                loc_str = f"{loc_addr.get('addressLocality', '')}, {loc_addr.get('addressCountry', {}).get('name', '')}".strip(", ")
                if title and url:
                    jobs.append({
                        "id": f"adobe-{len(jobs)}", "company": "Adobe", "companyId": "comp-adobe",
                        "title": title, "location": loc_str, "url": url,
                        "skills": _extract_skills(title),
                        "postedAt": None,
                        "firstSeenAt": _now_iso(),
                    })
        except (json_mod.JSONDecodeError, TypeError):
            continue
    if jobs:
        return jobs
    # Fallback: parse Workday URLs from script blocks
    for script in soup.find_all("script"):
        c = script.string or ""
        if "jobPosting" not in c or len(c) < 5000:
            continue
        workday_urls = re.findall(r'(https://adobe\.wd\d+\.myworkdayjobs\.com/[^\s"\']+?)/apply', c)
        titles = re.findall(r'"title"\s*:\s*"([^"]+)"', c)
        locations = re.findall(r'"jobLocation"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', c)
        for i, (wurl, title) in enumerate(zip(workday_urls, titles)):
            if title in ("shareInfoText",):
                continue
            loc = locations[i] if i < len(locations) else ""
            jobs.append({
                "id": f"adobe-{i}", "company": "Adobe", "companyId": "comp-adobe",
                "title": title, "location": loc, "url": wurl,
                "skills": _extract_skills(title),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })
        break
    return jobs


def _scrape_barclays(query: str = "", location: str = "") -> list[dict]:
    """Barclays — extract jobs with locations from TalentBrew HTML.
    Job URL pattern: /job/{city}/{slug}/{division}/{jobId}
    Eg: /job/london/staff-engineer/13015/94805410128
    """
    search_term = query or "engineer"
    if location:
        search_term += f" {location}"
    resp = requests.get(f"https://search.jobs.barclays/search-jobs/{quote_plus(search_term)}",
        headers=_H_HTML, timeout=20)
    resp.raise_for_status()
    html = resp.text
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen = set()
    href_re = re.compile(r'^/job/([^/]+)/([^/]+)/\d+/(\d{6,})$')
    for a in soup.find_all("a", href=href_re):
        href = a.get("href", "")
        m = href_re.match(href)
        if not m:
            continue
        city, slug, jid = m.groups()
        if jid in seen:
            continue
        seen.add(jid)
        # Title: prefer the anchor text; fall back to slug
        text = a.get_text(strip=True)
        if not text or len(text) < 4:
            text = slug.replace("-", " ").title()
        # Location: try parent context first, otherwise use the city slug
        loc = city.replace("-", " ").title()
        parent = a.find_parent("li") or a.find_parent("div")
        if parent:
            for span in parent.find_all("span"):
                t = span.get_text(strip=True)
                if t and ("," in t) and len(t) < 80 and t.lower() != text.lower():
                    loc = t
                    break
        jobs.append({
            "id": f"barclays-{jid}", "company": "Barclays", "companyId": "comp-barclays",
            "title": text, "location": loc,
            "url": f"https://search.jobs.barclays{href}",
            "skills": _extract_skills(text),
            "postedAt": None,
            "firstSeenAt": _now_iso(),
        })
    return jobs


def _scrape_citi(query: str = "", location: str = "") -> list[dict]:
    """Citibank — extract jobs with locations from TalentBrew HTML."""
    search_term = query or "engineer"
    if location:
        search_term += f" {location}"
    resp = requests.get(f"https://jobs.citi.com/search-jobs/{quote_plus(search_term)}",
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for li in soup.find_all("li"):
        a = li.find("a", href=re.compile(r'/job/.+/\d+$'))
        if not a:
            continue
        href = a["href"]
        text = a.get_text(strip=True)
        if not text or len(text) <= 5:
            continue
        url = f"https://jobs.citi.com{href}" if href.startswith("/") else href
        loc = ""
        loc_el = li.find("span", class_="job-location")
        if loc_el:
            loc = loc_el.get_text(strip=True)
        if not loc:
            for span in li.find_all("span"):
                t = span.get_text(strip=True)
                if t and ("," in t or "india" in t.lower()) and len(t) < 80 and t != text:
                    loc = t
                    break
        jobs.append({"id": f"citi-{len(jobs)}", "company": "Citibank", "companyId": "comp-citi",
                     "title": text, "location": loc, "url": url,
                     "skills": _extract_skills(text), "postedAt": None, "firstSeenAt": _now_iso()})
    return jobs


def _scrape_apple_html(query: str = "", location: str = "") -> list[dict]:
    """Extract Apple job deep links from embedded JSON in HTML."""
    import json as json_mod
    params = {"search": query or "software engineer", "sort": "relevance"}
    if location:
        params["location"] = location
    resp = requests.get(
        "https://jobs.apple.com/en-us/search",
        params=params,
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for s in soup.find_all("script"):
        c = s.string or ""
        if "staticRouterHydrationData" not in c or len(c) < 10000:
            continue
        match = re.search(r'JSON\.parse\("(.+)"\)', c, re.DOTALL)
        if not match:
            continue
        try:
            raw = match.group(1)
            unescaped = raw.encode().decode('unicode_escape')
            data = json_mod.loads(unescaped)
            results = data.get("loaderData", {}).get("search", {}).get("searchResults", [])
            for r in results[:_SCRAPE_MAX]:
                pid = r.get("positionId", "")
                title = r.get("postingTitle", "")
                loc_list = r.get("locations", [])
                location = loc_list[0].get("name", "") if loc_list and isinstance(loc_list[0], dict) else ""
                url = f"https://jobs.apple.com/en-us/details/{pid}" if pid else ""
                jobs.append({
                    "id": f"apple-{pid}",
                    "company": "Apple", "companyId": "comp-apple",
                    "title": title, "location": location, "url": url,
                    "skills": _extract_skills(title),
                    "postedAt": _real_posted(r.get("postingDate")),
                    "firstSeenAt": _now_iso(),
                })
        except Exception as e:
            logger.warning("Apple JSON parse failed: %s", e)
        break
    return jobs


# ═══════════════════════════════════════════════════════════════════════
# LinkedIn bulk-search + shared TTL cache
# ═══════════════════════════════════════════════════════════════════════
# Strategy:
#   • Instead of N requests (one per company), do ONE LinkedIn search per
#     (query, location) and bucket the result cards by employer name.
#   • Cache each (query, location) result for 10 min so back-to-back
#     scrape_company calls + parallel users hitting the same query share
#     a single upstream request.
#   • Rotate User-Agent and add small jitter to reduce throttle fingerprint.
#   • Use LinkedIn's lightweight `seeMoreJobPostings` JSON-ish endpoint —
#     it returns more jobs per request and is less aggressively rate-limited
#     than the full search HTML page.

import threading
import random
from time import time as _now

_LI_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_LI_CACHE_LOCK = threading.Lock()
_LI_CACHE_TTL_S = _LI_CACHE_TTL_S_DEFAULT   # tunable via LI_CACHE_TTL_S env var (default 30 min)
_LI_LAST_REQUEST_AT: list[float] = [0.0]   # mutable holder
_LI_MIN_INTERVAL_S = 0.8   # min gap between upstream LinkedIn calls
_LI_LOCK = threading.Lock()

_LI_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _li_headers() -> dict:
    return {
        "User-Agent": random.choice(_LI_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def _li_throttle():
    """Enforce a minimum spacing between LinkedIn upstream requests (across
    threads) so a parallel discover-bulk call doesn't fire 8 requests at once."""
    with _LI_LOCK:
        gap = _now() - _LI_LAST_REQUEST_AT[0]
        if gap < _LI_MIN_INTERVAL_S:
            jitter = random.uniform(0, 0.3)
            sleep_for = (_LI_MIN_INTERVAL_S - gap) + jitter
            __import__("time").sleep(sleep_for)
        _LI_LAST_REQUEST_AT[0] = _now()


def _li_cache_key(query: str, location: str, max_results: int | None = None) -> tuple[str, str, int]:
    mr = max_results if max_results is not None else _LI_BULK_MAX
    return ((query or "").strip().lower(), (location or "").strip().lower(), mr)


def _li_cache_get(key: tuple[str, str]) -> list[dict] | None:
    with _LI_CACHE_LOCK:
        hit = _LI_CACHE.get(key)
        if not hit:
            return None
        ts, data = hit
        if _now() - ts > _LI_CACHE_TTL_S:
            _LI_CACHE.pop(key, None)
            return None
        return data


def _li_cache_put(key: tuple[str, str], data: list[dict]):
    with _LI_CACHE_LOCK:
        _LI_CACHE[key] = (_now(), data)
        # Bound cache size — keep the most recent _LI_CACHE_MAX entries.
        if len(_LI_CACHE) > _LI_CACHE_MAX:
            evict = max(1, _LI_CACHE_MAX // 4)
            oldest = sorted(_LI_CACHE.items(), key=lambda kv: kv[1][0])[:evict]
            for k, _ in oldest:
                _LI_CACHE.pop(k, None)


def _normalize_employer(name: str) -> str:
    """Lowercase, collapse whitespace, strip non-alphanumeric (except space).
    NOTE: we do NOT strip 'pvt/ltd/inc' here — those are usually only on the
    employer side (e.g. 'PhonePe Pvt Ltd' → still starts with 'phonepe' which
    is the company name). Stripping noise on the company side caused false
    positives like 'Tech Mahindra' → bare 'mahindra' matching 'Kotak Mahindra
    Bank'."""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


# Employer-side suffix tokens we DO strip (only on the LinkedIn employer line,
# not the canonical company name) so 'PhonePe Pvt Ltd' → 'phonepe'.
_EMPLOYER_SUFFIX_NOISE = {
    "pvt", "ltd", "limited", "inc", "incorporated", "corp", "corporation",
    "llc", "plc", "co", "company", "private",
}


def _strip_employer_suffix(s: str) -> str:
    toks = s.split()
    while toks and toks[-1] in _EMPLOYER_SUFFIX_NOISE:
        toks.pop()
    return " ".join(toks)


def _build_employer_index() -> dict[str, str]:
    """Map normalized canonical company name → company_id."""
    idx: dict[str, str] = {}
    for cid, info in COMPANIES.items():
        norm = _normalize_employer(info.get("name", ""))
        if norm:
            idx[norm] = cid
    return idx


_EMPLOYER_INDEX_CACHE: list[dict[str, str]] = []


def _employer_index() -> dict[str, str]:
    if not _EMPLOYER_INDEX_CACHE:
        _EMPLOYER_INDEX_CACHE.append(_build_employer_index())
    return _EMPLOYER_INDEX_CACHE[0]


def _attribute_employer(employer_text: str) -> str | None:
    """Given LinkedIn's employer line, return matching company_id or None.
    Match rules (in order, first hit wins):
      1. Exact normalized match.
      2. Employer with corporate suffix stripped equals company.
      3. Employer starts with company name + space (e.g. 'Microsoft India').
    Loose substring/token-overlap matching is intentionally NOT used — it
    caused 'Kotak Mahindra Bank' → comp-tech-mahindra and 'Bank of X'
    → comp-bofa false positives."""
    norm = _normalize_employer(employer_text)
    if not norm:
        return None
    idx = _employer_index()
    if norm in idx:
        return idx[norm]
    stripped = _strip_employer_suffix(norm)
    if stripped and stripped in idx:
        return idx[stripped]
    # Prefix match — company canonical name as the first whole-word token(s).
    # Iterate over candidates; prefer the longest matching company name.
    best_cid = None
    best_len = 0
    for cname, cid in idx.items():
        if (norm == cname or norm.startswith(cname + " ") or
            stripped == cname or stripped.startswith(cname + " ")):
            if len(cname) > best_len:
                best_cid, best_len = cid, len(cname)
    return best_cid


def _li_bulk_fetch(query: str, location: str, max_results: int | None = None) -> list[dict]:
    """Fetch up to ``max_results`` LinkedIn job cards for (query, location)
    in a few requests. Returns raw cards with employer name + URL — caller
    decides how to attribute them to companies. Uses TTL cache + throttle.

    Endpoint: LinkedIn's `seeMoreJobPostings` JSON-fragment endpoint
    returns rendered HTML cards but supports start= pagination and is
    much cheaper than the SPA URL.
    """
    if max_results is None:
        max_results = _LI_BULK_MAX
    key = _li_cache_key(query, location, max_results)
    cached = _li_cache_get(key)
    if cached is not None:
        logger.info("[LI-BULK] cache HIT (%s, %s, %d) -> %d jobs", key[0], key[1], key[2], len(cached))
        return cached

    cards: list[dict] = []
    seen_jids: set[str] = set()
    kw = (query or "engineer").strip()

    # Wall-clock guard so a slow upstream can't burn the whole HTTP budget.
    # Scale deadline proportionally: ~30s for 200 cards, ~120s for 1000.
    default_deadline = max(30, int(max_results / 200 * 30))
    deadline_s = float(os.environ.get("LI_BULK_DEADLINE_S", str(default_deadline)))
    req_timeout_s = float(os.environ.get("LI_BULK_REQ_TIMEOUT_S", "8"))
    t_start = _now()

    # 25 cards per page; paginate up to ``max_results``.
    for start in range(0, max_results, 25):
        if len(cards) >= max_results:
            break
        if _now() - t_start > deadline_s:
            logger.warning("[LI-BULK] deadline %.1fs exceeded for (%s, %s) at start=%d, returning %d cards",
                           deadline_s, kw, location, start, len(cards))
            break
        loc_param = f"&location={quote_plus(location)}" if location else ""
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
               f"?keywords={quote_plus(kw)}{loc_param}&start={start}")
        _li_throttle()
        try:
            resp = requests.get(url, headers=_li_headers(), timeout=req_timeout_s)
        except Exception as e:
            logger.warning("[LI-BULK] request failed (%s, %s) start=%d: %s",
                           kw, location, start, e)
            break
        if resp.status_code in (429, 999):
            # Hard throttled — back off and stop paginating.
            logger.warning("[LI-BULK] throttled (%s) status=%d, skipping further pages",
                           kw, resp.status_code)
            break
        if resp.status_code != 200 or not resp.text.strip():
            break
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            break
        page_count = 0
        for li in soup.select("li, div.base-card"):
            link = li.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
            title_el = li.select_one("h3, .base-search-card__title")
            comp_el = li.select_one(".base-search-card__subtitle, .job-search-card__subtitle, h4")
            loc_el = li.select_one(".job-search-card__location, .base-search-card__metadata")
            if not (link and title_el):
                continue
            href = link.get("href", "").strip().replace("\n", "").replace(" ", "")
            if "/jobs/view/" not in href:
                continue
            jid_match = re.search(r'-(\d{8,})(?:[/?]|$)', href) or re.search(r'/jobs/view/(\d+)', href)
            if not jid_match:
                continue
            jid = jid_match.group(1)
            if jid in seen_jids:
                continue
            seen_jids.add(jid)
            cards.append({
                "_li_jid": jid,
                "title": title_el.get_text(strip=True),
                "employer": (comp_el.get_text(strip=True) if comp_el else "").strip(),
                "location": (loc_el.get_text(strip=True) if loc_el else "").strip(),
                "url": href.split("?")[0].strip(),
            })
            page_count += 1
            if len(cards) >= max_results:
                break
        if page_count == 0:
            break

    logger.info("[LI-BULK] fetched %d cards for (%s, %s)", len(cards), kw, location)
    _li_cache_put(key, cards)
    return cards


def bulk_linkedin_for_companies(company_ids: list[str], query: str,
                                  location: str) -> dict[str, list[dict]]:
    """One-shot LinkedIn fetch for a set of companies. Returns
    dict[company_id] -> list[job_dict]. ANY company in `company_ids` whose
    employer name appears in the LinkedIn results is populated; others get
    an empty list. The caller is free to call this once per (query, location)
    pair instead of N times (one per company)."""
    cards = _li_bulk_fetch(query, location)
    targets = set(company_ids)
    out: dict[str, list[dict]] = {cid: [] for cid in company_ids}
    for c in cards:
        cid = _attribute_employer(c.get("employer", ""))
        if not cid or cid not in targets:
            continue
        company = COMPANIES.get(cid) or {}
        job = {
            "id": f"{cid.replace('comp-','')}-li-{c['_li_jid']}",
            "company": company.get("name") or c.get("employer") or cid,
            "companyId": cid,
            "title": c.get("title", ""),
            "location": c.get("location", ""),
            "url": c["url"],
            "skills": _extract_skills(c.get("title", "")),
            "postedAt": None,
            "firstSeenAt": _now_iso(),
        }
        out[cid].append(job)
    # Per-company URL-rewrite annotation (keeps LinkedIn deep link as primary,
    # adds applyUrl for official career site).
    for cid, jobs in out.items():
        if jobs:
            out[cid] = _rewrite_to_official(jobs, cid)
    return out



    try:
        kw = query or "engineer"
        loc_param = f"&location={quote_plus(location)}" if location else ""
        url = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(kw)}&f_C={linkedin_company_id}&position=1&pageNum=0{loc_param}"
        resp = requests.get(url, headers=_H_HTML, timeout=15)
        if resp.status_code != 200:
            raise Exception(f"LinkedIn returned {resp.status_code}")

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        seen_jids = set()

        for card in soup.select(".base-card, .job-search-card, li"):
            link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
            title_el = card.select_one("h3, .base-search-card__title")
            loc_el = card.select_one(".job-search-card__location, .base-search-card__metadata")

            if link and title_el:
                href = link.get("href", "").strip().replace("\n", "").replace(" ", "")
                if "/jobs/view/" not in href:
                    continue
                title = title_el.get_text(strip=True)
                location = loc_el.get_text(strip=True) if loc_el else ""
                # Extract LinkedIn job ID — it's the trailing numeric segment of the URL.
                # URL example: /jobs/view/software-engineer-at-microsoft-4410578286
                jid_match = re.search(r'-(\d{8,})(?:[/?]|$)', href) or re.search(r'/jobs/view/(\d+)', href)
                if not jid_match:
                    continue  # skip if no real ID — prevents dup pollution
                jid = jid_match.group(1)
                if jid in seen_jids:
                    continue  # LinkedIn HTML often repeats the same card across selectors
                seen_jids.add(jid)
                clean_url = href.split("?")[0].strip()
                jobs.append({
                    "id": f"{company_id.replace('comp-','')}-li-{jid}",
                    "company": company_name, "companyId": company_id,
                    "title": title, "location": location,
                    "url": clean_url,
                    "skills": _extract_skills(title),
                    "postedAt": None,
                    "firstSeenAt": _now_iso(),
                })
        return jobs
    except Exception as e:
        logger.warning("LinkedIn scrape failed for %s: %s", company_name, e)
        return []


# ── Native scrapers for companies previously on LinkedIn fallback ───────────

def _api_microsoft(query: str = "", location: str = "") -> list[dict]:
    """Microsoft Careers public JSON API → official job page deep links.
    Endpoint: https://gcsservices.careers.microsoft.com/search/api/v1/search
    Job URL pattern: https://jobs.careers.microsoft.com/global/en/job/{jobId}/{slug}
    Paginates up to _SCRAPE_MAX results (50 per page).
    """
    page_size = 50
    pages = max(1, (_SCRAPE_MAX + page_size - 1) // page_size)
    headers = {
        "User-Agent": _H["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://jobs.careers.microsoft.com",
        "Referer": "https://jobs.careers.microsoft.com/",
    }
    jobs: list[dict] = []
    seen: set[str] = set()
    for pg in range(1, pages + 1):
        params = {
            "q": query or "engineer",
            "l": "en_us",
            "pg": pg,
            "pgSz": page_size,
            "o": "Recent",
        }
        if location:
            params["lc"] = location
        try:
            resp = requests.get(
                "https://gcsservices.careers.microsoft.com/search/api/v1/search",
                params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.info("[MSFT] gcsservices returned %d on pg=%d", resp.status_code, pg)
                break
            data = resp.json().get("operationResult", {}).get("result", {})
            page_jobs = data.get("jobs") or []
        except Exception as e:
            logger.warning("[MSFT] page=%d failed: %s", pg, e)
            break
        if not page_jobs:
            break
        for j in page_jobs:
            jid = j.get("jobId", "")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            title = j.get("title", "")
            slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:60] or "job"
            url = f"https://jobs.careers.microsoft.com/global/en/job/{jid}/{slug}"
            props = j.get("properties", {}) or {}
            loc_list = props.get("locations") or []
            primary = props.get("primaryLocation", "") or (loc_list[0] if loc_list else "")
            jobs.append({
                "id": f"msft-{jid}", "company": "Microsoft", "companyId": "comp-microsoft",
                "title": title, "location": primary, "url": url,
                "skills": _extract_skills(title + " " + (props.get("description") or "")),
                "postedAt": _real_posted(j.get("postingDate")),
                "firstSeenAt": _now_iso(),
            })
            if len(jobs) >= _SCRAPE_MAX:
                break
        if len(page_jobs) < page_size or len(jobs) >= _SCRAPE_MAX:
            break
    logger.info("[MSFT] native API returned %d jobs", len(jobs))
    return jobs


def _api_jpmorgan(query: str = "", location: str = "") -> list[dict]:
    """JPMorgan Chase Oracle Cloud Recruiting API → official requisition pages.
    Job URL: https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{id}
    Paginates up to _SCRAPE_MAX results.
    """
    page_size = 25
    headers = {**_H, "REST-Framework-Version": "1"}
    jobs: list[dict] = []
    seen: set[str] = set()
    loc_lower = (location or "").lower()
    for offset in range(0, _SCRAPE_MAX, page_size):
        finder_parts = ["siteNumber=CX_1001",
                        "facetsList=LOCATIONS;WORK_LOCATIONS;CATEGORIES",
                        f"limit={page_size}",
                        f"offset={offset}",
                        "sortBy=POSTING_DATES_DESC"]
        if query:
            finder_parts.append(f'keyword="{query}"')
        finder = ";".join(finder_parts)
        params = {
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
            "finder": f"findReqs;{finder}",
        }
        try:
            resp = requests.get(
                "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            items = resp.json().get("items", []) or []
            req_list = items[0].get("requisitionList", []) if items else []
        except Exception as e:
            logger.warning("[JPMC] offset=%d failed: %s", offset, e)
            break
        if not req_list:
            break
        for r in req_list:
            jid = r.get("Id", "")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            title = r.get("Title", "")
            loc = r.get("PrimaryLocation", "") or ""
            if loc_lower and loc_lower not in loc.lower():
                secondary = r.get("secondaryLocations", []) or []
                if not any(loc_lower in (s.get("Name", "") or "").lower() for s in secondary):
                    continue
            url = f"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{jid}"
            jobs.append({
                "id": f"jpmc-{jid}", "company": "JPMorgan Chase", "companyId": "comp-jpmorgan",
                "title": title, "location": loc, "url": url,
                "skills": _extract_skills(title),
                "postedAt": _real_posted(r.get("PostedDate")),
                "firstSeenAt": _now_iso(),
            })
            if len(jobs) >= _SCRAPE_MAX:
                break
        if len(req_list) < page_size or len(jobs) >= _SCRAPE_MAX:
            break
    return jobs


def _scrape_meta_html(query: str = "", location: str = "") -> list[dict]:
    """Meta Careers public search → official metacareers.com job pages.
    Job URL: https://www.metacareers.com/jobs/{jobId}/
    """
    import json as json_mod
    params = {"q": query or "engineer"}
    if location:
        params["offices[0]"] = location  # offices is the location filter
    resp = requests.get("https://www.metacareers.com/jobs",
        params=params, headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    # Meta embeds job data in script blocks as JSON
    for s in soup.find_all("script"):
        c = s.string or ""
        if '"all_jobs"' not in c and 'job_postings' not in c.lower():
            continue
        # Extract job IDs and titles via robust regex
        # Pattern: {"id":"123456789","title":"Software Engineer"...}
        for m in re.finditer(r'"id"\s*:\s*"(\d{10,})"\s*,\s*"title"\s*:\s*"([^"]+)"', c):
            jid = m.group(1)
            title = m.group(2)
            # Try to get location from nearby JSON (best-effort)
            loc = ""
            jobs.append({
                "id": f"meta-{jid}", "company": "Meta", "companyId": "comp-meta",
                "title": title, "location": loc,
                "url": f"https://www.metacareers.com/jobs/{jid}/",
                "skills": _extract_skills(title),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })
            if len(jobs) >= 25:
                break
        if jobs:
            break
    # Fallback: parse <a href="/jobs/{id}/"> links
    if not jobs:
        for a in soup.find_all("a", href=re.compile(r'/jobs/\d{10,}/?$')):
            href = a["href"]
            jid_m = re.search(r'/jobs/(\d{10,})', href)
            if not jid_m:
                continue
            jid = jid_m.group(1)
            title = a.get_text(strip=True) or "Software Engineer"
            jobs.append({
                "id": f"meta-{jid}", "company": "Meta", "companyId": "comp-meta",
                "title": title, "location": "",
                "url": f"https://www.metacareers.com/jobs/{jid}/",
                "skills": _extract_skills(title),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })
            if len(jobs) >= 25:
                break
    return jobs


def _api_bofa_workday(query: str = "", location: str = "") -> list[dict]:
    """Bank of America Workday CXS API → official BofA careers pages.
    Job URL: https://careers.bankofamerica.com{externalPath}
    """
    body = {
        "appliedFacets": {},
        "limit": 25,
        "offset": 0,
        "searchText": query or "engineer",
    }
    headers = {**_H, "Content-Type": "application/json"}
    # Try several known Workday tenant paths
    tenant_paths = [
        "https://careers.bankofamerica.com/wday/cxs/bankofamerica/Lateral/jobs",
        "https://careers.bankofamerica.com/wday/cxs/bankofamerica/Campus/jobs",
        "https://bofa.wd1.myworkdayjobs.com/wday/cxs/bofa/Lateral/jobs",
    ]
    last_err = None
    for endpoint in tenant_paths:
        try:
            resp = requests.post(endpoint, json=body, headers=headers, timeout=15)
            if resp.status_code != 200:
                last_err = f"{endpoint} → {resp.status_code}"
                continue
            data = resp.json()
            postings = data.get("jobPostings", []) or []
            base = endpoint.split("/wday/cxs/")[0]
            jobs = []
            loc_lower = (location or "").lower()
            for p in postings[:_SCRAPE_MAX]:
                ext = p.get("externalPath", "")
                title = p.get("title", "")
                loc = p.get("locationsText", "") or ""
                if loc_lower and loc_lower not in loc.lower():
                    continue
                url = f"{base}{ext}" if ext.startswith("/") else ext
                # Workday job IDs come from the path (last segment)
                jid_m = re.search(r'_R-?(\d+)', ext) or re.search(r'/([^/]+)$', ext)
                jid = jid_m.group(1) if jid_m else str(len(jobs))
                jobs.append({
                    "id": f"bofa-{jid}", "company": "Bank of America", "companyId": "comp-bofa",
                    "title": title, "location": loc, "url": url,
                    "skills": _extract_skills(title),
                    "postedAt": _real_posted(p.get("postedOn")),
                    "firstSeenAt": _now_iso(),
                })
            if jobs:
                return jobs
        except Exception as e:
            last_err = f"{endpoint} → {e}"
            continue
    if last_err:
        logger.info("[BOFA] all tenants failed, last: %s", last_err)
    return []


def _scrape_goldman_html(query: str = "", location: str = "") -> list[dict]:
    """Goldman Sachs higher.gs.com — extract roles + IDs from public listing HTML.
    Job URL: https://higher.gs.com/roles/{id}
    """
    import json as json_mod
    params = {}
    if query:
        params["query"] = query
    if location:
        params["location"] = location
    resp = requests.get("https://higher.gs.com/roles", params=params,
        headers=_H_HTML, timeout=15)
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    # Look for embedded __NEXT_DATA__ (Next.js apps expose data here)
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        try:
            data = json_mod.loads(next_data.string)
            # Walk the structure to find role/job arrays
            def _walk(obj):
                results = []
                if isinstance(obj, dict):
                    if obj.get("id") and obj.get("title") and (obj.get("primaryLocation") or obj.get("locations") or obj.get("location")):
                        results.append(obj)
                    for v in obj.values():
                        results.extend(_walk(v))
                elif isinstance(obj, list):
                    for v in obj:
                        results.extend(_walk(v))
                return results
            candidates = _walk(data)
            for c in candidates[:_SCRAPE_MAX]:
                jid = str(c.get("id"))
                title = c.get("title", "")
                loc = c.get("primaryLocation") or ""
                if not loc:
                    locs = c.get("locations") or c.get("location") or []
                    if isinstance(locs, list) and locs:
                        loc = locs[0] if isinstance(locs[0], str) else locs[0].get("name", "")
                    elif isinstance(locs, str):
                        loc = locs
                jobs.append({
                    "id": f"gs-{jid}", "company": "Goldman Sachs", "companyId": "comp-goldman",
                    "title": title, "location": loc,
                    "url": f"https://higher.gs.com/roles/{jid}",
                    "skills": _extract_skills(title),
                    "postedAt": _real_posted(c.get("postedDate")),
                    "firstSeenAt": _now_iso(),
                })
        except Exception as e:
            logger.info("[GOLDMAN] __NEXT_DATA__ parse failed: %s", e)
    # Fallback: scan anchor tags
    if not jobs:
        for a in soup.find_all("a", href=re.compile(r'/roles/[a-zA-Z0-9_-]+')):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            jid = href.rstrip("/").split("/")[-1]
            url = f"https://higher.gs.com{href}" if href.startswith("/") else href
            jobs.append({
                "id": f"gs-{jid}", "company": "Goldman Sachs", "companyId": "comp-goldman",
                "title": text, "location": "",
                "url": url,
                "skills": _extract_skills(text),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })
            if len(jobs) >= 25:
                break
    return jobs


def _scrape_via_linkedin(company_name: str, company_id: str, linkedin_company_id: str,
                          query: str = "", location: str = "") -> list[dict]:
    """LEGACY URN-based LinkedIn fallback.
    Now routes through the shared bulk cache so it shares an upstream
    request with every other LinkedIn-fallback company on the same
    (query, location). The `linkedin_company_id` URN is no longer used —
    employer-name attribution from the bulk fetch result handles
    company disambiguation."""
    return _scrape_linkedin_by_name(company_name, company_id, query, location)


def _native_or_linkedin(native_fn, company_name: str, company_id: str,
                        linkedin_id: str, query: str = "", location: str = "") -> list[dict]:
    """Try native API first; fall back to shared LinkedIn bulk cache."""
    try:
        jobs = native_fn(query=query, location=location)
        if jobs:
            logger.info("[NATIVE] %s returned %d jobs from official site", company_name, len(jobs))
            return jobs
        logger.info("[NATIVE] %s returned 0 jobs, trying LinkedIn fallback", company_name)
    except Exception as e:
        logger.warning("[NATIVE] %s failed: %s — trying LinkedIn", company_name, e)
    jobs = _scrape_via_linkedin(company_name, company_id, linkedin_id, query, location)
    return _rewrite_to_official(jobs, company_id)


def _linkedin_only(company_name: str, company_id: str, linkedin_id: str):
    """LinkedIn-only scraper that rewrites URLs to point to the official career site search."""
    def _fn(query: str = "", location: str = "") -> list[dict]:
        jobs = _scrape_via_linkedin(company_name, company_id, linkedin_id, query, location)
        return _rewrite_to_official(jobs, company_id)
    return _fn


def _scrape_linkedin_by_name(company_name: str, company_id: str,
                              query: str = "", location: str = "") -> list[dict]:
    """Per-company LinkedIn lookup.

    Strategy (tunable via ``LI_PER_COMPANY_FETCH``, default ON):
      1. PRIMARY: search LinkedIn with the company name BAKED INTO the
         keyword query (e.g. "Razorpay software engineer") so the result
         set is dominated by that employer. Each company therefore gets
         its own up-to-``LI_PER_COMPANY_MAX`` (default 200) card pool
         instead of fighting for a slot in one global 120-card pool.
      2. FALLBACK: if the per-company search returns nothing for this
         employer, consult the SHARED bulk pool keyed by the generic
         query. Keeps the old bulk-discover behaviour where one global
         LinkedIn call is amortized across many companies.

    All cards are filtered by `_attribute_employer` so we never surface a
    job whose employer line doesn't match the company we're scraping for.
    """
    per_company_on = os.environ.get("LI_PER_COMPANY_FETCH", "1") != "0"
    per_company_max = int(os.environ.get("LI_PER_COMPANY_MAX", "200"))

    out: list[dict] = []
    seen_jids: set[str] = set()

    def _emit(cards: list[dict]) -> None:
        for c in cards:
            if _attribute_employer(c.get("employer", "")) != company_id:
                continue
            jid = c.get("_li_jid", "")
            if jid in seen_jids:
                continue
            seen_jids.add(jid)
            out.append({
                "id": f"{company_id.replace('comp-','')}-li-{jid}",
                "company": company_name, "companyId": company_id,
                "title": c.get("title", ""),
                "location": c.get("location", ""),
                "url": c["url"],
                "skills": _extract_skills(c.get("title", "")),
                "postedAt": None,
                "firstSeenAt": _now_iso(),
            })

    # ── 1. Per-company search ──
    if per_company_on:
        kw = f"{company_name} {query}".strip()
        try:
            per_company_cards = _li_bulk_fetch(kw, location, max_results=per_company_max)
        except Exception as e:
            logger.warning("[LI-BY-NAME] per-company fetch failed for %s: %s", company_id, e)
            per_company_cards = []
        _emit(per_company_cards)

    # ── 2. Shared-pool fallback (only when per-company yielded nothing) ──
    if not out:
        try:
            shared_cards = _li_bulk_fetch(query, location)
        except Exception as e:
            logger.warning("[LI-BY-NAME] shared-pool fetch failed for %s: %s", company_id, e)
            shared_cards = []
        _emit(shared_cards)

    return out


def _linkedin_by_name(company_name: str, company_id: str):
    """Factory: a scraper that searches LinkedIn by company name (no URN
    needed). Scales the company catalog without hardcoding a URN per row."""
    def _fn(query: str = "", location: str = "") -> list[dict]:
        jobs = _scrape_linkedin_by_name(company_name, company_id, query, location)
        return _rewrite_to_official(jobs, company_id)
    return _fn


def _rewrite_to_official(jobs: list[dict], company_id: str) -> list[dict]:
    """Annotate LinkedIn-sourced jobs with an official-career-site search URL.

    Important: we KEEP the LinkedIn deep link as the primary `url` because it
    points to the specific job posting. Overwriting it with the company's
    generic search page lands the user on a results screen with no way to find
    the exact role they clicked. The official search URL is exposed as
    `applyUrl` so the frontend can offer it as an alternative.
    """
    company = COMPANIES.get(company_id)
    if not company:
        return jobs
    for j in jobs:
        url = j.get("url", "") or ""
        if "linkedin.com" in url.lower():
            j["linkedinUrl"] = url  # primary deep link to the actual posting
            j["applyUrl"] = _search_url(company, j.get("title", ""))
            j["sourceNote"] = "Click for the LinkedIn posting; use Apply link for the official career site"
    return jobs


# ── API Registry ─────────────────────────────────────────────────────────────

_API_SCRAPERS = {
    "comp-amazon": _api_amazon,
    "comp-uber": _api_uber,
    "comp-netflix": _api_netflix,
    "comp-stripe": _api_stripe,
    "comp-google": _scrape_google_html,
    "comp-salesforce": _scrape_salesforce_html,
    "comp-adobe": _scrape_adobe_html,
    "comp-apple": _scrape_apple_html,
    # Native API → LinkedIn fallback
    "comp-microsoft": lambda query="", location="": _native_or_linkedin(
        _api_microsoft, "Microsoft", "comp-microsoft", "1035", query, location),
    "comp-meta": lambda query="", location="": _native_or_linkedin(
        _scrape_meta_html, "Meta", "comp-meta", "10667", query, location),
    "comp-jpmorgan": lambda query="", location="": _native_or_linkedin(
        _api_jpmorgan, "JPMorgan Chase", "comp-jpmorgan", "1068", query, location),
    "comp-goldman": lambda query="", location="": _native_or_linkedin(
        _scrape_goldman_html, "Goldman Sachs", "comp-goldman", "1382", query, location),
    "comp-bofa": lambda query="", location="": _native_or_linkedin(
        _api_bofa_workday, "Bank of America", "comp-bofa", "1123", query, location),
    # Banks
    "comp-barclays": _scrape_barclays,
    "comp-citi": _scrape_citi,
    # Still LinkedIn-only (no easy public API found) — REMOVED from company
    # registry. These companies' jobs are surfaced via the separate LinkedIn
    # search tile, not via per-company discover.

    # ── Greenhouse-hosted (returns real official career-site deep links) ──
    "comp-airbnb":      lambda q="", l="": _api_greenhouse_generic("airbnb",      "Airbnb",       "comp-airbnb",      q, l),
    "comp-anthropic":   lambda q="", l="": _api_greenhouse_generic("anthropic",   "Anthropic",    "comp-anthropic",   q, l),
    "comp-asana":       lambda q="", l="": _api_greenhouse_generic("asana",       "Asana",        "comp-asana",       q, l),
    "comp-cloudflare":  lambda q="", l="": _api_greenhouse_generic("cloudflare",  "Cloudflare",   "comp-cloudflare",  q, l),
    "comp-databricks":  lambda q="", l="": _api_greenhouse_generic("databricks",  "Databricks",   "comp-databricks",  q, l),
    "comp-discord":     lambda q="", l="": _api_greenhouse_generic("discord",     "Discord",      "comp-discord",     q, l),
    "comp-dropbox":     lambda q="", l="": _api_greenhouse_generic("dropbox",     "Dropbox",      "comp-dropbox",     q, l),
    "comp-duolingo":    lambda q="", l="": _api_greenhouse_generic("duolingo",    "Duolingo",     "comp-duolingo",    q, l),
    "comp-figma":       lambda q="", l="": _api_greenhouse_generic("figma",       "Figma",        "comp-figma",       q, l),
    "comp-gitlab":      lambda q="", l="": _api_greenhouse_generic("gitlab",      "GitLab",       "comp-gitlab",      q, l),
    "comp-gusto":       lambda q="", l="": _api_greenhouse_generic("gusto",       "Gusto",        "comp-gusto",       q, l),
    "comp-instacart":   lambda q="", l="": _api_greenhouse_generic("instacart",   "Instacart",    "comp-instacart",   q, l),
    "comp-lyft":        lambda q="", l="": _api_greenhouse_generic("lyft",        "Lyft",         "comp-lyft",        q, l),
    "comp-mongodb":     lambda q="", l="": _api_greenhouse_generic("mongodb",     "MongoDB",      "comp-mongodb",     q, l),
    "comp-pinterest":   lambda q="", l="": _api_greenhouse_generic("pinterest",   "Pinterest",    "comp-pinterest",   q, l),
    "comp-reddit":      lambda q="", l="": _api_greenhouse_generic("reddit",      "Reddit",       "comp-reddit",      q, l),
    "comp-robinhood":   lambda q="", l="": _api_greenhouse_generic("robinhood",   "Robinhood",    "comp-robinhood",   q, l),
    "comp-scaleai":     lambda q="", l="": _api_greenhouse_generic("scaleai",     "Scale AI",     "comp-scaleai",     q, l),
    "comp-twilio":      lambda q="", l="": _api_greenhouse_generic("twilio",      "Twilio",       "comp-twilio",      q, l),
    "comp-vercel":      lambda q="", l="": _api_greenhouse_generic("vercel",      "Vercel",       "comp-vercel",      q, l),

    # ── Ashby-hosted (official Ashby career site for these companies) ──
    "comp-cohere":      lambda q="", l="": _api_ashby_generic("cohere",      "Cohere",     "comp-cohere",     q, l),
    "comp-mistral":     lambda q="", l="": _api_ashby_generic("mistral",     "Mistral AI", "comp-mistral",    q, l),
    "comp-perplexity":  lambda q="", l="": _api_ashby_generic("perplexity",  "Perplexity", "comp-perplexity", q, l),
    "comp-linear":      lambda q="", l="": _api_ashby_generic("linear",      "Linear",     "comp-linear",     q, l),
    "comp-supabase":    lambda q="", l="": _api_ashby_generic("supabase",    "Supabase",   "comp-supabase",   q, l),
    "comp-posthog":     lambda q="", l="": _api_ashby_generic("posthog",     "PostHog",    "comp-posthog",    q, l),
    "comp-ramp":        lambda q="", l="": _api_ashby_generic("ramp",        "Ramp",       "comp-ramp",       q, l),
    "comp-writer":      lambda q="", l="": _api_ashby_generic("writer",      "Writer",     "comp-writer",     q, l),
    "comp-decagon":     lambda q="", l="": _api_ashby_generic("decagon",     "Decagon",    "comp-decagon",    q, l),

    # ── Lever-hosted ──
    "comp-cred":        lambda q="", l="": _api_lever_generic("cred",        "CRED",       "comp-cred",       q, l),
    "comp-jupiter":     lambda q="", l="": _api_lever_generic("jupiter",     "Jupiter Money", "comp-jupiter", q, l),

    # ── India expansion companies are NOT in _API_SCRAPERS ────────────
    # They only have LinkedIn as their source; their jobs are surfaced
    # exclusively via the separate LinkedIn search tile (500-1000 jobs,
    # 1-day cache). Keeping them out of _API_SCRAPERS means bulk/company
    # discover never fires per-company LinkedIn fetches that were causing
    # the 220s timeout.
}


# ── Main Entry Point ─────────────────────────────────────────────────────────

def scrape_company(company_id: str, query: str = "", location: str = "") -> list[dict]:
    company = COMPANIES.get(company_id)
    if not company:
        return []

    jobs = []
    # Try real API first
    api_fn = _API_SCRAPERS.get(company_id)
    if api_fn:
        try:
            jobs = api_fn(query, location)
        except Exception as e:
            logger.warning("API failed for %s: %s", company["name"], e)

        # Location-fallback retry: many upstreams (Uber, Adobe, several
        # Workday/Greenhouse boards) reject free-text city names like
        # "Bangalore" / "Hyderabad" and return zero. If we got 0 results
        # with a location filter, retry WITHOUT the filter and let the
        # downstream match_jobs_to_profile() handle city/country filtering
        # client-side. Net effect: a job that exists in upstream's data
        # is no longer hidden just because we used the wrong location key.
        if not jobs and location:
            try:
                fallback = api_fn(query, "")
                if fallback:
                    logger.info("[FALLBACK-LOC] %s: 0 with loc=%r, %d without",
                                company["name"], location, len(fallback))
                    jobs = fallback
            except Exception as e:
                logger.warning("Fallback (no-location) failed for %s: %s",
                               company["name"], e)

        # Query-fallback retry: if a specific query returned 0 but the
        # company has open jobs in general, fetch unfiltered and let the
        # match pipeline rank by relevance.
        if not jobs and query:
            try:
                fallback = api_fn("", location or "")
                if not fallback and location:
                    fallback = api_fn("", "")
                if fallback:
                    logger.info("[FALLBACK-QUERY] %s: 0 with q=%r, %d unfiltered",
                                company["name"], query, len(fallback))
                    jobs = fallback
            except Exception as e:
                logger.warning("Fallback (no-query) failed for %s: %s",
                               company["name"], e)

    # If a real scraper was registered but returned no jobs, do NOT fabricate
    # placeholder roles via the LLM — they pollute the user feed with fake
    # postings whose apply URLs are just the company's search page. Only fall
    # back to AI generation when the company has no scraper at all.
    if not jobs and not api_fn:
        jobs = _generate_jobs(company, query)

    # Ensure all URLs are valid
    for job in jobs:
        url = job.get("url", "")
        if not url or len(url) < 15 or not url.startswith("http"):
            job["url"] = _search_url(company, job.get("title", ""))

    # Deduplicate
    seen = set()
    unique = []
    for j in jobs:
        key = j["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(j)
    # Per-call cap. Bumped from 75 -> 200 (configurable) so vector matching
    # has a deeper candidate pool. Combined with country->city fan-out in
    # routes.py the total raw pool per company is now ~200 * pairs after
    # dedup across pairs (typically 200-400 unique).
    try:
        _cap = int(os.environ.get("SCRAPE_PER_CALL_CAP", "200"))
    except (TypeError, ValueError):
        _cap = 200
    return unique[:_cap]


def _search_url(company: dict, title: str) -> str:
    """Build a career search URL with job title pre-filled."""
    tmpl = company.get("searchUrl", company["careersUrl"])
    return tmpl.replace("{query}", quote_plus(title.split(" - ")[0].strip()))


# ── AI Job Generation (for companies without APIs) ───────────────────────────

def _generate_jobs(company: dict, query: str = "") -> list[dict]:
    name = company["name"]
    cid = company["id"]
    seen_at = _now_iso()

    openai_key = os.environ.get("OPENAI_KEY", "")
    openai_endpoint = os.environ.get("OPENAI_ENDPOINT", "")
    if openai_key and openai_endpoint:
        try:
            import openai, json
            client = openai.AzureOpenAI(api_key=openai_key, api_version="2024-12-01-preview", azure_endpoint=openai_endpoint)
            resp = client.chat.completions.create(
                model="gpt41",
                messages=[{"role": "user", "content":
                    f"List 15 realistic current job openings at {name}. "
                    f"Mix engineering, data, ML, product, design, SRE. "
                    f"{f'Focus on: {query}' if query else ''}\n"
                    f'Return ONLY JSON: [{{"title":"...","location":"City, ST","skills":["s1","s2"]}}]'}],
                max_tokens=800, temperature=0.7)
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            items = json.loads(raw.strip())
            return [{"id": f"{cid.replace('comp-','')}-{i}", "company": name, "companyId": cid,
                     "title": it["title"], "location": it.get("location", ""),
                     "url": _search_url(company, it["title"]),
                     "skills": it.get("skills", _extract_skills(it["title"])),
                     "postedAt": None, "firstSeenAt": seen_at}
                    for i, it in enumerate(items[:15])]
        except Exception as e:
            logger.warning("AI gen failed for %s: %s", name, e)

    roles = ["Software Engineer", "Senior Software Engineer", "Staff Engineer",
             "Backend Engineer", "Frontend Engineer", "Full Stack Engineer",
             "Data Engineer", "ML Engineer", "DevOps Engineer", "Product Manager",
             "Engineering Manager", "Security Engineer", "iOS Engineer", "Android Engineer", "SRE"]
    return [{"id": f"{cid.replace('comp-','')}-{i}", "company": name, "companyId": cid,
             "title": role, "location": "Multiple Locations",
             "url": _search_url(company, role),
             "skills": _extract_skills(role),
             "postedAt": None, "firstSeenAt": seen_at}
            for i, role in enumerate(roles)]


# ── Utils ────────────────────────────────────────────────────────────────────

def _extract_skills(title: str) -> list[str]:
    known = ["Python","Java","Go","C++","C#","Rust","JavaScript","TypeScript",
             "React","Angular","Node.js","SQL","Kubernetes","Docker",
             "AWS","Azure","GCP","Machine Learning","ML","AI",
             "iOS","Android","Swift","Kotlin","Flutter","Backend","Frontend",
             "Full Stack","DevOps","SRE","Security","Infrastructure",
             "Distributed Systems","Microservices","Kafka","Spark",
             "Data Engineering","ETL","Deep Learning","NLP",
             # Finance / business / MBA-relevant keywords so non-eng jobs
             # get meaningful skill metadata.
             "Excel","Financial Modeling","Portfolio Analysis","Equity Research",
             "Investment Banking","Wealth Management","M&A","Corporate Finance",
             "FP&A","Risk Management","Valuation","Trading","Quantitative",
             "Business Intelligence","Tableau","Power BI","SAS",
             "Salesforce","CRM","Marketing","Operations","Strategy",
             "Consulting","Product Management","UX","UI","Figma"]
    import re as _re
    t = title.lower()
    out = []
    for s in known:
        sl = s.lower()
        # For very short/ambiguous tokens (<=3 chars or contains punctuation),
        # require a word-boundary match so "R" / "AI" / "ML" / "Go" / "C#"
        # don't substring-match unrelated job titles like "Senior" or "Manager".
        if len(sl) <= 3 or any(ch in sl for ch in ".+#&"):
            pat = r'(?<![a-z0-9])' + _re.escape(sl) + r'(?![a-z0-9])'
            if _re.search(pat, t):
                out.append(s)
        else:
            if sl in t:
                out.append(s)
    return out


# =====================================================================
# Country/discipline tables — module level so they aren't rebuilt per call.
# =====================================================================
# Map a city or country keyword -> ISO-ish country bucket label.
# We keep the bucket strings opaque; we only need set equality between
# user-pref bucket and job-location bucket to detect cross-country drops.
_CITY_TO_COUNTRY = {
    # India
    "india":"IN","ind":"IN","bangalore":"IN","bengaluru":"IN","mumbai":"IN",
    "delhi":"IN","new delhi":"IN","hyderabad":"IN","pune":"IN","chennai":"IN",
    "kolkata":"IN","noida":"IN","gurgaon":"IN","gurugram":"IN","ahmedabad":"IN",
    "jaipur":"IN","kochi":"IN","chandigarh":"IN","indore":"IN","nagpur":"IN",
    "coimbatore":"IN","trivandrum":"IN","thiruvananthapuram":"IN","lucknow":"IN",
    "bhopal":"IN","telangana":"IN","karnataka":"IN","maharashtra":"IN",
    # USA
    "usa":"US","united states":"US","america":"US",
    "san francisco":"US","new york":"US","seattle":"US","austin":"US","chicago":"US",
    "boston":"US","los angeles":"US","denver":"US","atlanta":"US","dallas":"US",
    "houston":"US","san jose":"US","miami":"US","portland":"US","phoenix":"US",
    "philadelphia":"US","san diego":"US","raleigh":"US","pittsburgh":"US",
    "washington dc":"US","sunnyvale":"US","mountain view":"US","palo alto":"US",
    "east palo alto":"US","cupertino":"US","redmond":"US","bellevue":"US",
    "irvine":"US","detroit":"US","minneapolis":"US","nashville":"US",
    "california":"US","washington":"US","texas":"US","florida":"US","oregon":"US",
    "colorado":"US","massachusetts":"US","illinois":"US","arizona":"US",
    # UK
    "uk":"UK","united kingdom":"UK","england":"UK","britain":"UK","scotland":"UK",
    "london":"UK","manchester":"UK","edinburgh":"UK","cambridge":"UK",
    "oxford":"UK","bristol":"UK","birmingham":"UK","glasgow":"UK",
    # Canada
    "canada":"CA","toronto":"CA","vancouver":"CA","montreal":"CA","ottawa":"CA",
    "calgary":"CA","ontario":"CA","quebec":"CA","british columbia":"CA","alberta":"CA",
    # EU + others
    "germany":"DE","berlin":"DE","munich":"DE","frankfurt":"DE","hamburg":"DE",
    "france":"FR","paris":"FR",
    "netherlands":"NL","amsterdam":"NL","holland":"NL",
    "ireland":"IE","dublin":"IE",
    "switzerland":"CH","zurich":"CH","geneva":"CH",
    "sweden":"SE","stockholm":"SE",
    "spain":"ES","madrid":"ES","barcelona":"ES",
    "italy":"IT","milan":"IT","rome":"IT",
    "poland":"PL","warsaw":"PL","krakow":"PL",
    "portugal":"PT","lisbon":"PT",
    "denmark":"DK","copenhagen":"DK",
    "norway":"NO","oslo":"NO",
    "finland":"FI","helsinki":"FI",
    "austria":"AT","vienna":"AT",
    "belgium":"BE","brussels":"BE",
    "czech":"CZ","prague":"CZ","czech republic":"CZ",
    "singapore":"SG",
    "uae":"AE","dubai":"AE","abu dhabi":"AE","united arab emirates":"AE",
    "saudi arabia":"SA","saudi":"SA","riyadh":"SA","jeddah":"SA",
    "qatar":"QA","doha":"QA",
    "bahrain":"BH",
    "kuwait":"KW",
    "oman":"OM","muscat":"OM",
    "china":"CN","beijing":"CN","shanghai":"CN","shenzhen":"CN","hangzhou":"CN",
    "guangzhou":"CN","prc":"CN",
    "hong kong":"HK",
    "taiwan":"TW","taipei":"TW",
    "japan":"JP","tokyo":"JP","osaka":"JP",
    "south korea":"KR","korea":"KR","seoul":"KR",
    "australia":"AU","sydney":"AU","melbourne":"AU","brisbane":"AU","perth":"AU",
    "new zealand":"NZ","auckland":"NZ",
    "israel":"IL","tel aviv":"IL",
    "brazil":"BR","sao paulo":"BR",
    "mexico":"MX","mexico city":"MX",
    "south africa":"ZA","johannesburg":"ZA","cape town":"ZA",
}

# ISO3 country codes for locations like "Iasi, ROU" / "Bangkok, THA" / "SGP".
_ISO3_TO_COUNTRY = {
    "ind":"IN","usa":"US","gbr":"UK","can":"CA","deu":"DE","fra":"FR","nld":"NL",
    "irl":"IE","che":"CH","swe":"SE","esp":"ES","ita":"IT","prt":"PT","dnk":"DK",
    "fin":"FI","nor":"NO","aut":"AT","bel":"BE","cze":"CZ","sgp":"SG","are":"AE",
    "sau":"SA","qat":"QA","bhr":"BH","kwt":"KW","omn":"OM","chn":"CN","hkg":"HK",
    "twn":"TW","jpn":"JP","kor":"KR","aus":"AU","nzl":"NZ","zaf":"ZA","bra":"BR",
    "mex":"MX","rou":"RO","pol":"PL","isr":"IL","tha":"TH","tur":"TR","grc":"GR",
    "hun":"HU","rom":"RO","ukr":"UA","srb":"RS","bgr":"BG","hrv":"HR","svk":"SK",
    "vnm":"VN","mys":"MY","idn":"ID","phl":"PH","khm":"KH","mmr":"MM","lka":"LK",
    "pak":"PK","bgd":"BD","npl":"NP","egy":"EG","mar":"MA","nga":"NG","ken":"KE",
    "arg":"AR","chl":"CL","col":"CO","per":"PE","ury":"UY","ecu":"EC",
}

# Tokens that mean "could be anywhere" — we don't use them to drop jobs.
_AMBIGUOUS_LOC_TOKENS = (
    "remote","hybrid","anywhere","multiple","various","global","worldwide",
    "emea","apac","americas","latam","flexible","work from home","wfh",
    "distributed",
)

# "Remote-Friendly" / "Remote Friendly" tokens that companies use to mean
# "sometimes-remote, mostly tied to a named office". These should NOT pass
# the country filter on their own — the named office in the same string
# is the actual location. Detected separately so we don't widen the
# ambiguous-token set.
_REMOTE_FRIENDLY_RE = __import__("re").compile(r'remote[\s\-]?friendly', __import__("re").I)

def _country_buckets(loc_text: str) -> set[str]:
    """Return set of country buckets a location string refers to."""
    if not loc_text:
        return set()
    s = loc_text.lower()
    out: set[str] = set()
    import re as _re_iso
    for kw, code in _CITY_TO_COUNTRY.items():
        # Word-boundary match so "wales" doesn't match "new south wales" only
        # when the word "wales" appears as a real token (which it does — but
        # the country filter handles it correctly via AU detection too). The
        # bigger win is preventing things like "in" matching inside words.
        if _re_iso.search(r'\b' + _re_iso.escape(kw) + r'\b', s):
            out.add(code)
    # ISO3 codes detected as separate tokens.
    for tok in _re_iso.findall(r'\b([a-z]{3})\b', s):
        c = _ISO3_TO_COUNTRY.get(tok)
        if c:
            out.add(c)
    return out

# Disciplines used for cross-discipline mismatch penalty.
_DISCIPLINE_TITLE_TOKENS = {
    "frontend":   {"frontend","front-end","front end","ui engineer","ux engineer","web developer","react","angular","vue"},
    "backend":    {"backend","back-end","back end","server","api engineer"},
    "fullstack":  {"fullstack","full-stack","full stack"},
    "mobile":     {"android","ios engineer","mobile engineer","kotlin","swift engineer","flutter"},
    "ml":         {"ml","machine learning","applied ai","applied scientist","research scientist","ai engineer","deep learning","nlp engineer"},
    "data":       {"data engineer","etl","analytics engineer","bi","data analyst","data scientist"},
    "devops":     {"devops","sre","site reliability","reliability engineer","platform engineer","infrastructure engineer"},
    "security":   {"security engineer","appsec","infosec","cybersecurity","penetration"},
    "qa":         {"qa","sdet","test engineer","quality engineer","quality assurance"},
    "embedded":   {"firmware","embedded","hardware engineer","fpga","electronics engineer","asic","silicon","bsp","board support"},
    "robotics":   {"robotics","robot engineer","actuator","motion planning","perception engineer"},
    "network":    {"network engineer","wifi","wi-fi","wireless","5g engineer","rf engineer","radio frequency","telecom"},
    "voice":      {"alexa","voice assistant","speech engineer","asr engineer"},
    "game":       {"game engineer","game developer","gameplay engineer","unity engineer","unreal engineer"},
    "manager":    {"engineering manager","manager","director","head of"},
    "product":    {"product manager","pm","program manager","project manager"},
    "design":     {"designer","design engineer","ux designer","ui designer"},
    "sales":      {"sales","account executive","solutions architect","customer success","delivery consultant","professional services"},
    "finance":    {"finance","financial analyst","investment banking","investment banker",
                   "wealth management","portfolio manager","portfolio analyst","equity research",
                   "credit analyst","quantitative analyst","quant","risk analyst","actuarial",
                   "treasury","corporate finance","fp&a","trader","trading","valuation",
                   "investor relations","capital markets","asset management"},
    # Non-engineering buckets — to keep HR/legal/ops/accounting/marketing roles
    # from leaking into engineering candidates' top-10. When the user's
    # discipline is engineering (or any NARROW_DISC eng spec), these titles
    # are dropped by the discipline-conflict filter.
    "hr":         {"people partner","people operations","hr ","human resources","talent acquisition",
                   "recruiter","recruiting","talent partner","hrbp"},
    "legal":      {"legal counsel","attorney","paralegal","compliance officer","general counsel",
                   "legal operations"},
    "operations": {"operations manager","business operations","biz ops","operations analyst",
                   "supply chain","logistics manager","warehouse","fulfillment center"},
    "accounting": {"accountant","accounting","controller","auditor","bookkeeper","tax analyst",
                   "tax manager","tax compliance","indirect tax","tax associate","tax consultant",
                   "tax director","tax accountant","internal audit","external audit"},
    "marketing":  {"marketing manager","brand manager","content marketing","seo specialist",
                   "growth marketing","marketing analyst","social media manager","copywriter"},
    "support":    {"customer support","technical support","support engineer","help desk",
                   "service desk"},
}
_DISCIPLINE_SKILL_TOKENS = {
    "frontend":   {"react","angular","vue","javascript","typescript","html","css","tailwind","next.js"},
    "backend":    {"go","golang","java","spring","node.js","django","flask","fastapi","rails","postgres","mysql","redis","kafka","grpc"},
    "mobile":     {"android","kotlin","swift","ios","flutter","react native"},
    "ml":         {"pytorch","tensorflow","sklearn","mlops","huggingface","spark","numpy","pandas"},
    "data":       {"airflow","spark","etl","snowflake","dbt","redshift","bigquery"},
    "devops":     {"terraform","ansible","kubernetes","helm","prometheus","grafana"},
    "security":   {"owasp","penetration","burp","metasploit","wireshark"},
    "embedded":   {"verilog","vhdl","arduino","raspberry pi","freertos","zephyr","yocto"},
    # Non-engineering disciplines so the matcher correctly handles MBA /
    # finance / sales / design / product candidates.
    "finance":    {"excel","financial modeling","portfolio analysis","portfolio performance analysis",
                   "valuation","dcf","equity research","investment banking","wealth management",
                   "client reporting","business intelligence","market research","risk management",
                   "corporate finance","accounting","tax","financial analyst"},
    "product":    {"roadmapping","product management","jira","confluence","user research"},
    "design":     {"figma","sketch","adobe xd","photoshop","illustrator","prototyping"},
    "sales":      {"salesforce","crm","pipeline management","cold outreach"},
}

# Engineering disciplines — used so we only "default to backend/fullstack"
# for users who are actually engineers.
_ENGINEERING_DISC = {"frontend","backend","fullstack","mobile","ml","data","devops",
                      "security","qa","embedded","robotics","network","voice","game"}

# Map the explicit `preferences.industry` chosen by the user in the discover
# UI to the discipline buckets we should treat them as. Used to OVERRIDE the
# resume-inferred disciplines so a SWE-flavored resume that explicitly opts
# into "Product / Design" stops being filtered as an engineering candidate
# (and stops having product/design jobs dropped as "non-engineering noise").
# An empty set means "no opinion" -- fall back to resume inference.
# "tech" stays empty intentionally: the existing inference already captures
# the engineering sub-discipline (frontend/backend/ml/etc) more precisely
# than a flat industry tag could.
_INDUSTRY_TO_DISCIPLINES = {
    "tech":           set(),
    "data_ai":        {"ml", "data"},
    "product_design": {"product", "design"},
    "finance":        {"finance"},
    "marketing":      {"sales"},
    "healthcare":     set(),
    "legal":          set(),
    "operations":     set(),
    "hr":             set(),
    "education":      set(),
    "manufacturing":  set(),
    "media":          set(),
    "government":     set(),
    "consulting":     set(),
    "other":          set(),
}


def _user_disciplines(
    skills: set[str],
    role_phrases: list[str],
    industry: str | None = None,
) -> set[str]:
    """Infer which disciplines the user actually works in, from skills,
    full role-title strings (NOT individual words), and the explicit
    industry hint they selected in the discover UI.

    Industry override (when supplied and mapped to a non-empty bucket set)
    DOMINATES the resume-derived inference -- this is the user explicitly
    saying "I am pivoting / searching as X". Without this override the
    matcher's downstream filters (v20/v21 in match_jobs_to_profile) treat a
    SWE-resume + product/design search as an engineering candidate and drop
    every Product Designer / UX Designer title as non-engineering noise.
    """
    import re as _re
    if industry:
        explicit = _INDUSTRY_TO_DISCIPLINES.get(industry.strip().lower())
        if explicit:
            return set(explicit)
    out = set()
    for disc, tokens in _DISCIPLINE_SKILL_TOKENS.items():
        if skills & tokens:
            out.add(disc)
    for phrase in role_phrases:
        for disc, tokens in _DISCIPLINE_TITLE_TOKENS.items():
            for t in tokens:
                if _re.search(r'\b' + _re.escape(t) + r'\b', phrase):
                    out.add(disc); break
    if out:
        return out
    # If we still can't tell, only default to a generic engineering bucket
    # when the role phrases look engineering-y. Otherwise leave empty so the
    # discipline filter is a no-op (no false positives for MBA/finance/etc).
    eng_hint = any(_re.search(r'\b(engineer|developer|programmer|sde|swe|software)\b', p)
                    for p in role_phrases)
    if eng_hint:
        return {"backend", "fullstack"}
    return set()


def match_jobs_to_profile(jobs: list[dict], profile: dict) -> list[dict]:
    user_skills = set()
    for s in (profile.get("skills") or {}).get("technical", []): user_skills.add(s.lower())
    for exp in profile.get("experience") or []:
        if isinstance(exp, dict):
            for s in exp.get("skills", []): user_skills.add(s.lower())
    parsed = (profile.get("documents") or {}).get("parsedResumeData") or {}
    for s in parsed.get("extractedSkills", []): user_skills.add(s.lower())

    user_locs = set(l.lower() for l in (profile.get("preferences") or {}).get("locations", []))

    # Compute the set of country buckets the user is willing to work in.
    # This drives a hard cross-country drop below.
    user_country_buckets: set[str] = set()
    for ul in user_locs:
        user_country_buckets |= _country_buckets(ul)

    # Seniority/level words must NOT leak into role-keyword matching, otherwise a
    # user titled "Senior Software Developer" automatically scores well against
    # "Senior Staff Engineer" via the shared word "senior".
    _LEVEL_WORDS = {
        "intern","internship","entry","junior","jr","graduate","associate","trainee",
        "mid","mid-level","senior","sr","sr.","lead","staff","principal",
        "distinguished","fellow","architect","director","vp","head","manager",
        "i","ii","iii","iv","v","1","2","3","4","5",
        "sde","swe","sde-i","sde-ii","sde-iii","sde-1","sde-2","sde-3",
    }
    # Generic English/HR/company-name noise that pollutes title-keyword matching.
    # Anything in the user's experience that is a company brand or a stop word.
    _ROLE_STOP = {
        "the","a","an","and","or","of","for","in","at","to","with","on","by",
        "intern","co-op","contract","contractor","freelance","consultant",
        # common employer brands so titles like "Amazon Engineer" don't false-match
        "amazon","aws","google","meta","facebook","microsoft","apple","uber",
        "netflix","stripe","airbnb","tesla","oracle","ibm","intel","nvidia",
        "salesforce","adobe","cisco","walmart","target","disney","paypal",
        "linkedin","twitter","snap","snapchat","tiktok","bytedance","zoom",
        "shopify","ebay","yahoo","reddit","pinterest","spotify","square",
        "block","robinhood","coinbase","cred","razorpay","paytm","flipkart",
        "swiggy","zomato","ola","myntra","phonepe","bugsmirror","jpmc",
        "jpmorgan","chase","goldman","sachs","morgan","stanley","barclays",
        "citi","citibank","hsbc","ubs","deutsche","bofa",
        # generic position scaffolding
        "research","platform","team","group","division","department",
        "one","two","co","corp","ltd","inc","llc",
    }
    user_roles = set()
    user_role_phrases: list[str] = []  # full title strings, lowered, for discipline detection
    for exp in profile.get("experience") or []:
        if isinstance(exp, dict):
            t = (exp.get("title") or "").lower()
            if t:
                user_role_phrases.append(t)
            for w in t.split():
                w = w.strip(",.()-")
                if len(w) > 2 and w not in _LEVEL_WORDS and w not in _ROLE_STOP:
                    user_roles.add(w)
    # Always include a reasonable engineering core so generic titles match.
    user_roles.update({"software","engineer","developer","backend","frontend","fullstack","full-stack"})

    # Resolve effective years of experience.
    # Priority:
    #   1. Explicit positive preference (user actively set N > 0).
    #   2. Resume parser's totalYearsExperience.
    #   3. Count of non-intern experience entries × 1.5y.
    #   4. Explicit 0 — only honored when the resume agrees (no parser years
    #      AND no non-intern jobs). This avoids treating a 4-year SDE as a
    #      fresher just because the onboarding UI defaulted experienceYears
    #      to 0 and the user never overrode it.
    prefs = profile.get("preferences") or {}
    parsed_years = parsed.get("totalYearsExperience") or 0
    explicit_years = prefs.get("experienceYears")
    _real_jobs = [
        e for e in (profile.get("experience") or [])
        if isinstance(e, dict)
        and e.get("title")
        and "intern" not in (e.get("title") or "").lower()
    ]
    if isinstance(explicit_years, (int, float)) and explicit_years > 0:
        user_exp_years = explicit_years
    elif parsed_years and parsed_years > 0:
        # Trust the parser over a stale "0" default. A genuine fresher's
        # parser output will also be 0 and fall through to the next branch.
        user_exp_years = parsed_years
    elif _real_jobs:
        # No parser data but real (non-intern) jobs present — estimate.
        user_exp_years = max(1, int(len(_real_jobs) * 1.5))
    else:
        # Genuine fresher: no real jobs, parser found 0 years, and the
        # explicit value (if any) is 0.
        user_exp_years = 0
    logger.info("[MATCH] resolved user_exp_years=%s (explicit=%s parsed=%s real_jobs=%d)",
                user_exp_years, explicit_years, parsed_years, len(_real_jobs))

    # Level keywords for experience-based relevance
    _junior = {"intern", "internship", "entry", "junior", "jr", "graduate", "new grad", "associate"}
    _mid = {"mid", "mid-level"}
    _senior = {"senior", "sr", "sr.", "lead"}
    _staff_plus = {"staff", "principal", "distinguished", "fellow", "architect", "director", "vp", "head"}

    # Map title seniority to minimum years typically required.
    # SDE/SWE numbering (I/II/III/1/2/3) IS now mapped because real Amazon/Meta
    # postings rely on these qualifiers exclusively (e.g. "SDE II, AWS Identity").
    _LEVEL_MIN_YEARS = {
        "intern": 0, "internship": 0, "graduate": 0,
        "junior": 0, "jr": 0, "associate": 0, "entry": 0,
        "mid": 3, "mid-level": 3,
        "senior": 5, "sr": 5, "sr.": 5, "lead": 5,
        "staff": 8,
        "principal": 10, "architect": 8, "distinguished": 12, "fellow": 15,
        "director": 10, "vp": 12, "head": 10,
    }
    # Region-aware overrides (Phase 4): India "Senior" lands at 3yr, not 5yr;
    # EU sits between US and IN. Falls back to the in-code default if either
    # the user has no country preference, the JSON config is missing, or the
    # LEVEL_MAPPINGS_DISABLE env flag is set.
    _LEVEL_MIN_YEARS = _resolve_level_min_years(_LEVEL_MIN_YEARS, _user_region(user_country_buckets))
    # Numerical level qualifiers that are valid ONLY when paired with an
    # engineering noun (sde / swe / engineer / developer). Detected by regex
    # below to avoid false matches against unrelated words.
    _NUM_LEVEL_MIN_YEARS = {
        # Roman / arabic forms
        "i": 0, "1": 0,
        "ii": 3, "2": 3,
        "iii": 6, "3": 6,
        "iv": 9, "4": 9,
        "v": 12, "5": 12,
    }

    now = datetime.now(timezone.utc)
    filler = {"the","a","an","and","or","of","for","in","at","ii","iii","iv","sr","sr."}

    # Soft filter: drop jobs that are clearly out of band for the user's level.
    #  - 0–1 yr  → drop staff/principal/director/vp/head/distinguished/fellow/architect
    #  - 2–5 yr  → drop only principal/distinguished/fellow/director/vp/head
    #              ("staff" is intentionally NOT dropped — many AI labs title every
    #               engineer "Member of Technical Staff" and there are also "Staff
    #               Engineer" roles that 4–5 yr engineers legitimately apply to.)
    #  - 6+ yr   → keep everything (only prune by explicit "N+ years" requirement)
    if user_exp_years is not None and user_exp_years >= 0:
        if user_exp_years <= 1:
            _hard_drop_levels = {"senior","sr","sr.","lead","staff","principal","distinguished","fellow","architect","director","vp","head","manager",
                                  # Finance/business mid-senior tokens — a 0-yr
                                  # MBA fresher is not an "Associate" or "VP".
                                  "associate","vice","president"}
            # 0-1y also hard-drops numbered mid roles (II, III, 2, 3)
            _hard_drop_num = {"ii","iii","iv","v","2","3","4","5"}
        elif user_exp_years <= 3:
            # 2-3y: drop principal/distinguished/fellow/architect/director/vp/head/staff/lead.
            # Hard-drop III/3 and above (real SDE-III roles assume 5+ yrs).
            _hard_drop_levels = {"principal","distinguished","fellow","architect",
                                  "director","vp","head","staff","lead",
                                  "vice","president"}
            _hard_drop_num = {"iii","iv","v","3","4","5"}
        elif user_exp_years <= 5:
            # 4-5y: drop principal/distinguished/fellow/architect/director/vp/head.
            # We DROP "staff" too unless paired with "Member of Technical Staff"
            # (which is special-cased below). Real "Staff Engineer" roles at
            # FAANG/Uber/Stripe assume 8+ yrs and should not be in a 4y dashboard.
            _hard_drop_levels = {"principal","distinguished","fellow","architect",
                                  "director","vp","head","staff",
                                  # "Lead Engineer" / "Lead Solution Engineer"
                                  # imply ~6+ yrs at most companies. Drop for
                                  # 4y unless the description explicitly says
                                  # otherwise (years-required check above).
                                  "lead",
                                  # multi-word VP -- a title can split into
                                  # ["vice","president"] without containing "vp".
                                  "vice","president"}
            # Hard-drop IV/4 and V/5; SDE-III is borderline at 5y so left to scoring.
            _hard_drop_num = {"iv","v","4","5"}
        elif user_exp_years <= 7:
            # 6-7y: still drop the very senior tokens that imply 8+/10+ years.
            _hard_drop_levels = {"principal","distinguished","fellow","director","vp","head"}
            # Don't hard-drop numbered roles — 6-7y candidates legitimately
            # span SDE II / III / IV depending on the team's leveling.
            _hard_drop_num = set()
        else:
            # 8+ yr seniors: only hard-drop the obviously-junior I/1 titles.
            # II/2 is left to exp_score so we don't lose every team-anchored
            # requisition that uses generic numbering.
            _hard_drop_levels = set()
            _hard_drop_num = {"i","1"}

        # Detect IC vs management background. If the user has NEVER held a
        # title containing manager/director/vp/head, drop people-management roles.
        _is_ic = True
        for exp in profile.get("experience") or []:
            if isinstance(exp, dict):
                t = (exp.get("title") or "").lower()
                if any(w in t for w in ("manager","director","vp"," head ","head of","cto","cio")):
                    _is_ic = False; break
        if _is_ic:
            _hard_drop_levels = _hard_drop_levels | {"manager"}

        filtered = []
        dropped = 0
        for job in jobs:
            title_lower = job.get("title", "").lower()
            # Strip punctuation so "Associate," / "-Associate" / "VP." / "Sr.Staff" etc.
            # tokenize correctly. We also strip periods so "sr.staff" splits
            # into ["sr", "staff"] — critical for catching the senior-level
            # tokens when scrapers emit dotted abbreviations without spacing.
            import re as _re
            title_clean = _re.sub(r'[^a-z0-9+#\s]', ' ', title_lower)
            title_words = set(title_clean.split())
            # v21: also strip trailing "+" from level tokens so "Staff+" /
            # "Senior+" / "Sr+" tokenize like their bare counterparts. We
            # keep "+" in the regex above for "c++", "go+" etc. but for the
            # set membership check we want the bare level word.
            title_words |= {w.rstrip("+") for w in title_words if w.endswith("+")}

            # Don't drop "Member of Technical Staff" / "Technical Staff Engineer"
            # at AI labs — these are level-equivalent to "Software Engineer".
            is_member_of_staff = ("member" in title_words and "staff" in title_words) \
                              or ("technical" in title_words and "staff" in title_words)

            # Extract explicit "N+ years" requirement from title
            years_match = _re.search(r'(\d+)\+?\s*(?:years|yrs)', title_lower)
            if years_match:
                required_years = int(years_match.group(1))
                if required_years - user_exp_years > 2:
                    dropped += 1
                    continue

            # PhD/doctorate requirement — a candidate without a doctoral
            # degree on file should not see PhD-required listings even when
            # the title says "Early Career". Cross-check the user's education.
            _user_has_phd = any(
                isinstance(e, dict) and any(
                    kw in (e.get("degree") or "").lower()
                    for kw in ("phd", "ph.d", "doctor", "dphil")
                )
                for e in (profile.get("education") or [])
            )
            if not _user_has_phd:
                # "PhD Early Career" / "PhD Researcher" / Title strings
                if _re.search(r'\b(ph\.?\s*d\.?|phd|doctorate|dphil)\b', title_lower):
                    dropped += 1
                    continue

            # Defense-in-depth: scan the FIRST ~800 chars of the description
            # for an explicit minimum-years requirement. Many JDs put the
            # senior-only signal in the body ("8+ years required",
            # "minimum of 10 years", "at least 7+ years of experience") even
            # when the title is generic. We pick the LARGEST "N years" number
            # we find in the first ~800 chars (recruiters usually state the
            # higher bar early). If user_exp + 2 < N, drop the job.
            desc_blob = (job.get("description") or "")[:800].lower()
            if desc_blob:
                req_years_candidates = []
                # "8+ years", "8 years", "at least 8 years", "minimum 8 years",
                # "8-10 years", "8 to 10 years"
                for m in _re.finditer(
                    r'(?:at\s*least\s*|minimum\s*(?:of\s*)?|min\.?\s*|over\s*|more\s*than\s*)?'
                    r'(\d{1,2})\s*\+?\s*(?:to\s*\d{1,2}\s*)?(?:years|yrs)\b',
                    desc_blob,
                ):
                    try:
                        n = int(m.group(1))
                        if 1 <= n <= 25:  # sanity bounds
                            req_years_candidates.append(n)
                    except (ValueError, TypeError):
                        pass
                if req_years_candidates:
                    required_years = max(req_years_candidates)
                    if required_years - user_exp_years > 2:
                        dropped += 1
                        continue

            if not is_member_of_staff and _hard_drop_levels and (title_words & _hard_drop_levels):
                dropped += 1
                continue

            # Hard-drop numbered SDE/Engineer roles above the user's band.
            # Match titles where a role word appears AND a roman/arabic level
            # appears anywhere (e.g. "Software Development Engineer Test II, REX").
            if _hard_drop_num:
                if _re.search(r'\b(?:sde|swe|engineer|developer|programmer|scientist|analyst|sdet|architect)\b', title_lower):
                    lvl_m = _re.search(r'\b(i{1,3}|iv|v|[1-5])\b', title_lower)
                    if lvl_m and lvl_m.group(1) in _hard_drop_num:
                        dropped += 1
                        continue

            filtered.append(job)
        logger.info("[MATCH] Experience filter: %d/%d kept, %d dropped (user has %s years, drop=%s)",
                    len(filtered), len(jobs), dropped, user_exp_years, sorted(_hard_drop_levels))
        jobs = filtered

    # =================================================================
    # STRICT CROSS-COUNTRY DROP (defense-in-depth — also applies when
    # the function-app-level country filter wasn't run, e.g. regression
    # harness or legacy callers).
    # If user has explicit countries set, drop any job whose location
    # resolves to a different known country bucket. Ambiguous locations
    # (remote / multiple / global / etc.) pass through.
    # =================================================================
    if user_country_buckets:
        import re as _re_loc
        # "Remote-US", "Remote (EMEA)", "Remote, Mexico only", etc. — these
        # are geo-gated; the bare token "remote" must NOT make them ambiguous.
        # We strip out "remote" / "hybrid" / "wfh" before checking ambiguity.
        _GATED_REMOTE_RE = _re_loc.compile(
            r'(?:'
            # "Remote - US", "Remote in Mexico", "Remote (EMEA)", "Remote - Ireland"
            r'remote[\s\-:,()/]+(?:in\s+)?'
            r'(us|usa|united\s+states|americas|north\s+america|na|emea|eu|europe|uk|'
            r'canada|mexico|brazil|latam|japan|china|singapore|apac\s*(?:excl|excluding)|'
            # v21: include the EU country names + APAC + ANZ in forward direction
            r'switzerland|germany|france|netherlands|ireland|poland|sweden|spain|italy|'
            r'portugal|denmark|finland|norway|austria|belgium|israel|australia|new\s+zealand|'
            r'south\s+africa|south\s+korea|korea|taiwan|vietnam|thailand|philippines|malaysia|indonesia)'
            r'|'
            # Reverse: "US-Remote", "CAN-Remote", "EMEA Remote", "Switzerland - Remote"
            r'\b(us|usa|united\s+states|can|canada|emea|eu|europe|uk|americas|na|'
            r'mexico|brazil|latam|japan|china|singapore|apac|'
            r'switzerland|germany|france|netherlands|ireland|poland|sweden|spain|italy|'
            r'portugal|denmark|finland|norway|austria|belgium|israel|australia|new\s+zealand)'
            r'[\s\-:,()/]+remote'
            r')',
            _re_loc.I,
        )
        kept, dropped = [], 0
        for job in jobs:
            jl = (job.get("location") or "").lower()
            if not jl:
                kept.append(job); continue
            # "Remote-Friendly" only — strip the friendly token, then re-check
            # for a real geographic anchor. "Remote-Friendly, San Francisco"
            # is really a San Francisco role; "Remote-Friendly" alone with
            # named non-India offices means non-India.
            if _REMOTE_FRIENDLY_RE.search(jl):
                jl_stripped = _REMOTE_FRIENDLY_RE.sub("", jl).strip(" ,;()-")
                jb_strip = _country_buckets(jl_stripped)
                if jb_strip:
                    if jb_strip & user_country_buckets:
                        kept.append(job)
                    else:
                        dropped += 1
                    continue
                # No anchor at all — treat as ambiguous-pass.
                kept.append(job); continue
            # Multi-location semicolon strings ("London, UK; Ontario, CAN; Remote")
            # — split on `;` and check each segment. Keep the job if ANY segment
            # resolves to the user's countries.
            if ";" in jl:
                segs = [s.strip() for s in jl.split(";") if s.strip()]
                seg_buckets = set()
                any_ambig_in_user_country = False
                for seg in segs:
                    sb = _country_buckets(seg)
                    seg_buckets |= sb
                    if any(t in seg for t in _AMBIGUOUS_LOC_TOKENS) and not sb:
                        # Bare "Remote" segment with no country marker
                        # — only counts as user-country if other segments
                        # already include user-country. Don't auto-pass.
                        pass
                if seg_buckets:
                    if seg_buckets & user_country_buckets:
                        kept.append(job)
                    else:
                        dropped += 1
                    continue
            # Geo-gated remote ("Remote - US", "Remote in Mexico") — use the
            # gated region as the actual location for bucket comparison.
            gated_m = _GATED_REMOTE_RE.search(jl)
            if gated_m:
                # The gated region is whichever group matched.
                region = next((g for g in gated_m.groups() if g), "")
                jb = _country_buckets(region)
                if jb & user_country_buckets:
                    kept.append(job)
                else:
                    dropped += 1
                continue
            if any(t in jl for t in _AMBIGUOUS_LOC_TOKENS):
                kept.append(job); continue
            jb = _country_buckets(jl)
            if not jb:
                # Unknown country marker. If the location text doesn't
                # contain any of the user's country/city tokens either,
                # treat it as cross-country and drop. This catches things
                # like "Iasi, ROU" that don't resolve cleanly.
                if not any(u in jl for u in user_locs):
                    dropped += 1
                else:
                    kept.append(job)
                continue
            if jb & user_country_buckets:
                kept.append(job)
            else:
                dropped += 1
        logger.info("[MATCH] Country filter: %d/%d kept, %d dropped (user countries=%s)",
                    len(kept), len(jobs), dropped, sorted(user_country_buckets))
        jobs = kept

    # =================================================================
    # CITY PREFERENCE: when the user lists *specific cities*, do NOT
    # hard-drop other cities in the same country (Amazon may not have
    # any Bangalore openings, etc.). Same-country sister cities are
    # demoted via loc score below; the LLM rerank decides drop=true.
    # =================================================================
    _COUNTRY_NAMES = {"india","usa","united states","america","uk","united kingdom","england","britain",\
                      "canada","germany","france","netherlands","holland","ireland","switzerland","sweden","spain",\
                      "italy","poland","portugal","denmark","finland","norway","austria","belgium","czech",\
                      "czech republic","singapore","uae","united arab emirates","saudi arabia","saudi","qatar",\
                      "bahrain","kuwait","oman","china","prc","hong kong","taiwan","japan","south korea","korea",\
                      "australia","new zealand","israel","brazil","mexico","south africa"}
    user_city_prefs = {ul for ul in user_locs\
                       if ul in _CITY_TO_COUNTRY and ul not in _COUNTRY_NAMES}
    # NOTE: City preferences are applied via loc score demotion + LLM
    # rerank drop, not as a hard filter. This avoids zero-result matches
    # when the company has no openings in the preferred city.

    # Detect user disciplines once for discipline-mismatch penalty.
    # Pass the explicit industry from preferences -- when the user picked a
    # non-tech industry tag in the discover UI we want that to dominate the
    # resume-derived inference (e.g. SWE resume + product/design tag must
    # NOT be treated as an engineering candidate, otherwise the v20 filter
    # below would drop every Product Designer / UX Designer title).
    _industry_hint = (profile.get("preferences") or {}).get("industry") or ""
    user_disc = _user_disciplines(user_skills, user_role_phrases, industry=_industry_hint)

    # Helper: does the job title clearly conflict with user's disciplines?
    import re as _re_disc
    from .discipline_embeddings import disciplines_for_text as _disc_emb

    def _job_conflicts_discipline(title_lower: str) -> bool:
        if not user_disc:
            return False
        jd: set[str] = set()
        for d, toks in _DISCIPLINE_TITLE_TOKENS.items():
            for t in toks:
                if _re_disc.search(r'\b' + _re_disc.escape(t) + r'\b', title_lower):
                    jd.add(d); break
        # Embedding fallback (opt-in via DISCIPLINE_EMBED_ENABLE=1).
        # Only invoked when keyword tokens found NOTHING — keeps latency
        # and cost predictable. Acts as a soft hint: empty result = no
        # extra signal, conflict logic stays the same.
        if not jd:
            jd |= _disc_emb(title_lower)
        if not jd or (jd & user_disc):
            return False
        # Allow fullstack bridge
        if ("fullstack" in user_disc and (jd & {"frontend","backend"})) or \
           ("fullstack" in jd and (user_disc & {"frontend","backend"})):
            return False
        return True

    # Drop jobs at ambiguous locations (Multiple Locations / Remote / Global)
    # whose title clearly belongs to a discipline the user does NOT work in.
    # These are pure noise from the LLM/vector point of view.
    pre = len(jobs)
    jobs = [j for j in jobs
            if not (any(t in (j.get("location") or "").lower() for t in _AMBIGUOUS_LOC_TOKENS)
                    and _job_conflicts_discipline((j.get("title") or "").lower()))]
    if len(jobs) != pre:
        logger.info("[MATCH] Discipline x ambiguous-loc filter: %d/%d kept", len(jobs), pre)

    # Additionally: if the user's disciplines are NARROW (no backend/fullstack
    # bridge), drop discipline-conflicting titles at ANY location. A frontend
    # newgrad shouldn't see WiFi/BSP/Embedded roles even in their preferred city.
    _NARROW_DISC = {"frontend","mobile","ml","security","qa","robotics","network","voice","game","embedded","data"}
    if user_disc and user_disc.issubset(_NARROW_DISC | {"design"}):
        pre2 = len(jobs)
        jobs = [j for j in jobs if not _job_conflicts_discipline((j.get("title") or "").lower())]
        if len(jobs) != pre2:
            logger.info("[MATCH] Narrow-discipline filter: %d/%d kept (user_disc=%s)",
                        len(jobs), pre2, sorted(user_disc))

    # ALWAYS-on filter: when the user is clearly an engineering / data /
    # design candidate (any engineering token present), strip jobs whose
    # title belongs ONLY to a non-engineering discipline (HR, Legal, Sales,
    # Finance, Accounting, Marketing, Operations, Support, Manager, Product).
    # This catches "People Partner", "Crypto Product Accountant", "Recruiter"
    # leaks even when the user's discipline set also includes broad tags
    # like "devops" (which would bypass the NARROW filter above).
    #
    # v20: re-enabled with a tighter exclusion set after accuracy testing
    # showed Stripe Tax/Account Executive, Barclays Legal Counsel, Databricks
    # AE titles leaking into engineering candidates' top-100 with scores 65-78.
    # Kept off-limits to NON-engineering candidates (finance/sales/HR users
    # SHOULD see those titles).
    _PURE_NON_ENG_DISC = {"sales","finance","accounting","legal","hr","marketing",
                          "support","operations",
                          # v21: "Product Manager" / "PM" / "Program Manager" titles
                          # leak into engineering candidates' top-50. PM is its own
                          # discipline -- engineers should not be ranked PM jobs.
                          "product"}
    if user_disc and (user_disc & _ENGINEERING_DISC):
        pre3 = len(jobs)
        def _pure_non_eng(title_lower: str) -> bool:
            jd: set[str] = set()
            for d, toks in _DISCIPLINE_TITLE_TOKENS.items():
                for t in toks:
                    if _re_disc.search(r'\b' + _re_disc.escape(t) + r'\b', title_lower):
                        jd.add(d); break
            return bool(jd) and jd.issubset(_PURE_NON_ENG_DISC)
        jobs = [j for j in jobs if not _pure_non_eng((j.get("title") or "").lower())]
        if len(jobs) != pre3:
            logger.info("[MATCH] Eng-candidate non-eng filter: %d/%d kept (dropped %d business titles)",
                        len(jobs), pre3, pre3 - len(jobs))

    # v21: SYMMETRIC filter — when the user is a NON-engineering candidate
    # (finance/sales/marketing/PM/legal/HR/operations etc., with NO
    # engineering token in their inferred disc set), strip jobs whose title
    # is *purely* engineering. A finance intern should not see "Software
    # Engineer - Kafka", a Product Manager should not see "Backend Engineer
    # II". Roles that are partially engineering+manager (e.g. "Engineering
    # Manager", "Solutions Architect", "Sales Engineer") have a non-eng
    # token too, so they survive — which is the correct behavior.
    if user_disc and not (user_disc & _ENGINEERING_DISC):
        pre4 = len(jobs)
        # Generic engineering signal — catches "Software Engineer",
        # "Developer", "SDE", "SWE", "Programmer" titles that don't match
        # any specific sub-discipline in _DISCIPLINE_TITLE_TOKENS.
        _GENERIC_ENG_RE = _re_disc.compile(
            r'\b(software\s+engineer|software\s+developer|software\s+development\s+engineer|'
            r'sde|swe|sdet|programmer|software\s+programmer|'
            r'cloud\s+engineer|systems\s+engineer|systems\s+development\s+engineer|'
            r'application\s+engineer|application\s+developer|applications\s+developer|'
            r'engineering\s+intern|software\s+intern)\b', _re_disc.I)
        # Non-eng signal — these tokens prevent a generic-eng title from
        # being dropped (e.g. "Engineering Manager" has "manager" → keep).
        _NON_ENG_QUALIFIER_RE = _re_disc.compile(
            r'\b(manager|director|lead|head|principal\s+pm|product|sales|account|'
            r'finance|legal|hr|recruit|talent|marketing|operations|support|'
            r'analyst|consultant|partner|strategy|business)\b', _re_disc.I)
        def _pure_eng(title_lower: str) -> bool:
            jd: set[str] = set()
            for d, toks in _DISCIPLINE_TITLE_TOKENS.items():
                for t in toks:
                    if _re_disc.search(r'\b' + _re_disc.escape(t) + r'\b', title_lower):
                        jd.add(d); break
            # If sub-discipline detection found something, use the strict rule:
            # drop only when EVERY matched discipline is engineering AND none
            # overlap with the user's actual disciplines.
            if jd:
                if not jd.issubset(_ENGINEERING_DISC):
                    return False
                if jd & user_disc:
                    return False
                return True
            # Fallback: generic engineering signal with no non-eng qualifier.
            if _GENERIC_ENG_RE.search(title_lower) and not _NON_ENG_QUALIFIER_RE.search(title_lower):
                return True
            return False
        jobs = [j for j in jobs if not _pure_eng((j.get("title") or "").lower())]
        if len(jobs) != pre4:
            logger.info("[MATCH] Non-eng-candidate eng filter: %d/%d kept (dropped %d eng titles, user_disc=%s)",
                        len(jobs), pre4, pre4 - len(jobs), sorted(user_disc))

    scored = []
    for job in jobs:
        js = set(s.lower() for s in job.get("skills", []))
        # When the JD lists no skills (common: most career sites don't expose
        # them), treat skill score as neutral rather than near-zero so a
        # title-and-YoE-aligned job isn't rejected for missing data we never
        # had to begin with. The LLM rerank still inspects the description.
        skill = round((len(user_skills & js) / max(len(js),1)) * 100) if js else 35
        if not js:
            m = sum(1 for s in user_skills if s in job.get("title","").lower())
            skill = min(100, max(skill, m*20))

        loc_s = (job.get("location") or "").lower()
        # Location scoring: 100=preferred city, 30=same country diff city,
        # 40=ambiguous (remote/multiple/global), 0=different country.
        # Same-country sister-city is harshly penalised because most candidates
        # apply to a SPECIFIC city for relocation/family reasons.
        if not user_locs:
            loc = 50
        elif any(u in loc_s for u in user_locs):
            loc = 100
        elif any(t in loc_s for t in _AMBIGUOUS_LOC_TOKENS):
            loc = 40   # ambiguous — neither rewarded nor harshly penalised
        else:
            jb = _country_buckets(loc_s)
            if jb and user_country_buckets and (jb & user_country_buckets):
                loc = 10   # same country, different city — heavy demerit
            else:
                loc = 0

        tw = set(job.get("title","").lower().split()) - filler
        title = min(100, round((len(tw & user_roles)/max(len(tw),1))*100)) if tw and user_roles else 30

        rec = 50
        p = job.get("postedAt","")
        if p:
            try:
                d = (now - datetime.fromisoformat(p.replace("Z","+00:00"))).days
                rec = 100 if d<=1 else 90 if d<=3 else 75 if d<=7 else 60 if d<=14 else 40 if d<=30 else 20
            except: pass

        # Experience level scoring — strict: penalize mismatches heavily
        exp_score = 50  # neutral if no preference
        if user_exp_years:
            title_lower = job.get("title", "").lower()
            title_words = set(title_lower.split())

            # Check specific level keywords
            max_level_min = 0
            matched_level = False
            for word in title_words:
                min_years = _LEVEL_MIN_YEARS.get(word)
                if min_years is not None:
                    matched_level = True
                    max_level_min = max(max_level_min, min_years)

            # SDE/SWE/Engineer/Developer numbering: "SDE II", "Engineer III",
            # "Software Development Engineer 2", "SDE-3" — only when bound to
            # an engineering noun (so "Workday HCM 2.0" isn't misread).
            import re as _re2
            num_match = _re2.search(
                r'\b(?:sde|swe|engineer|developer|programmer|scientist|analyst)'
                r'[\s\-]*(i{1,3}|iv|v|[1-5])\b',
                title_lower,
            )
            if num_match:
                lvl = num_match.group(1)
                ny = _NUM_LEVEL_MIN_YEARS.get(lvl)
                if ny is not None:
                    matched_level = True
                    max_level_min = max(max_level_min, ny)

            if matched_level:
                diff = user_exp_years - max_level_min
                if diff >= 0 and diff <= 3:
                    exp_score = 100
                elif diff > 3:
                    exp_score = 60
                elif diff >= -1:
                    exp_score = 40
                else:
                    exp_score = 10
            elif user_exp_years <= 2:
                if title_words & _junior: exp_score = 100
                else: exp_score = 60
            elif user_exp_years <= 5:
                if title_words & _junior: exp_score = 20
                else: exp_score = 80
            else:
                # 6+ years. A title with NO level marker (e.g. plain
                # "Software Engineer", "Backend Engineer") is common for
                # team-anchored reqs that span mid-to-senior bands. Keep a
                # neutral score so they aren't crushed; the LLM rerank then
                # decides based on the description.
                if title_words & _junior: exp_score = 5
                elif not matched_level:
                    exp_score = 55
                else: exp_score = 70

        # Discipline-mismatch penalty: if the job title strongly implies a
        # discipline that the user does NOT work in, dampen skill score.
        title_lower = job.get("title","").lower()
        job_disc = set()
        for disc, tokens in _DISCIPLINE_TITLE_TOKENS.items():
            for t in tokens:
                if t in title_lower:
                    job_disc.add(disc); break
        # Only apply if job has a clear discipline AND user has a clear discipline
        # AND there is no overlap. Generic "software engineer" titles -> no job_disc -> no penalty.
        discipline_mismatch = False
        if job_disc and user_disc and not (job_disc & user_disc):
            # Allow fullstack to bridge frontend/backend
            if not (("fullstack" in user_disc and (job_disc & {"frontend","backend"})) or
                    ("fullstack" in job_disc and (user_disc & {"frontend","backend"}))):
                discipline_mismatch = True
                # Soft demotion only — the LLM rerank still gets to decide.
                # 0.3x previously crushed scores below the rerank cutoff and
                # killed otherwise-valid jobs whose JDs we just couldn't tag.
                # Multiplier is env-tunable via MATCH_DISC_PENALTY.
                skill = round(skill * _DISC_PENALTY)

        # Final blend — YoE-fit + title match weigh more than raw skill
        # overlap, since most JDs don't expose a clean skills list and we
        # don't want to reject a level-appropriate job for that alone.
        # Weights are env-tunable via MATCH_W_SKILL/TITLE/LOC/EXP/REC.
        ov = round(skill*_W_SKILL + title*_W_TITLE + loc*_W_LOC + exp_score*_W_EXP + rec*_W_REC)

        # Build a human-readable reason from the sub-scores so EVERY job has an
        # explanation (not only the top-N that go through the LLM rerank).
        reason_parts = []
        if user_skills and js:
            overlap = sorted(user_skills & js)
            if overlap:
                shown = ", ".join(overlap[:3])
                more = f" +{len(overlap)-3}" if len(overlap) > 3 else ""
                reason_parts.append(f"matches {len(overlap)} of your skills ({shown}{more})")
            else:
                reason_parts.append("no listed skill overlap")
        if loc == 100:
            reason_parts.append("preferred location")
        elif loc == 70:
            reason_parts.append("same country, different city")
        elif loc == 40:
            reason_parts.append("location TBD / multiple")
        elif loc == 0 and user_locs:
            reason_parts.append("outside preferred countries")
        if discipline_mismatch:
            reason_parts.append(f"different discipline ({sorted(job_disc)[0]} role)")
        if user_exp_years:
            if exp_score >= 90:
                reason_parts.append(f"level fits your {user_exp_years} yrs")
            elif exp_score <= 20:
                reason_parts.append("requires more years than you have")
            elif exp_score <= 40:
                reason_parts.append("stretch role above your level")
            elif exp_score <= 60:
                reason_parts.append("you may be overqualified")
        if rec >= 90:
            reason_parts.append("posted in last 3 days")
        elif rec >= 75:
            reason_parts.append("posted this week")
        match_reason = "; ".join(reason_parts).capitalize() if reason_parts else ""

        scored.append({**job, "matchScore": ov, "skillScore": skill,
                       "titleScore": title, "locationScore": loc, "recencyScore": rec,
                       "experienceScore": exp_score, "matchReason": match_reason})
    scored.sort(key=lambda x: x["matchScore"], reverse=True)
    return scored
