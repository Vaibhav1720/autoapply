"""Super-admin dashboard endpoints.

Access control: caller MUST present a Bearer JWT whose `email` claim is in
SUPER_ADMIN_EMAILS (default `<your-admin-email>`). The JWT is the same one
issued by /api/v1/auth/google. There is intentionally no token-based bypass
here — this surface exposes per-user data.

All endpoints are READ-ONLY GETs and return JSON suitable for direct render
in the Flutter admin dashboard.
"""
from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared.auth_v2 import get_user_claims, get_user_id
from shared.cosmos_client import query_items, upsert_item
from shared.exceptions import AppException, AuthorizationError, NotFoundError, ValidationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)

from services._runtime import SUPER_ADMIN_EMAILS, logger

bp = func.Blueprint()


# ── Authorization ───────────────────────────────────────────────────────────
def _require_super_admin(req: func.HttpRequest) -> dict:
    """Decode JWT, then enforce email allowlist. Raises AuthorizationError."""
    claims = get_user_claims(req)
    email = (claims.get("email") or "").strip().lower()
    if not email or email not in SUPER_ADMIN_EMAILS:
        raise AuthorizationError("Super-admin access required")
    return claims


# ── Helpers ─────────────────────────────────────────────────────────────────
def _parse_iso(ts) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _days_param(req: func.HttpRequest, default: int = 7) -> int:
    try:
        n = int(req.params.get("days") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 90))


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ── /admin/dashboard/summary ─────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/summary", methods=["GET"])
def admin_dashboard_summary(req: func.HttpRequest) -> func.HttpResponse:
    """High-level KPIs for the top of the dashboard.

    GET /api/v1/admin/dashboard/summary?days=7
    """
    try:
        _require_super_admin(req)
        days = _days_param(req)
        since = _since(days)
        since_iso = since.isoformat()

        # Total users
        users = query_items(
            "profiles",
            "SELECT c.id, c.email, c.tier, c.createdAt, c.updatedAt, c.usageCounters FROM c",
        )
        total_users = len(users)
        new_users = sum(1 for u in users if (_parse_iso(u.get("createdAt")) or since) >= since)

        # Active users from match_events in window
        evts = query_items(
            "match_events",
            "SELECT c.userId, c.timestamp, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion FROM c WHERE c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )
        active_user_set = set()
        api_calls = 0
        total_scraped = 0
        total_filtered = 0
        total_matched = 0
        total_duration_ms = 0
        errors = 0
        for e in evts:
            uid = e.get("userId")
            if uid:
                active_user_set.add(uid)
            api_calls += 1
            total_scraped += int(e.get("scrapedCount") or 0)
            total_filtered += int(e.get("filteredCount") or 0)
            total_matched += int(e.get("matchedCount") or 0)
            total_duration_ms += int(e.get("durationMs") or 0)
            mv = (e.get("modelVersion") or "")
            if mv.startswith("err:") or mv.startswith("unhandled:"):
                errors += 1

        return success_response({
            "windowDays": days,
            "users": {
                "total": total_users,
                "new": new_users,
                "active": len(active_user_set),
            },
            "discoveryFunnel": {
                "discoverCalls": api_calls,
                "totalScraped": total_scraped,
                "totalFiltered": total_filtered,
                "totalReturned": total_matched,
                "errorEvents": errors,
                "avgDurationMs": (total_duration_ms // api_calls) if api_calls else 0,
            },
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin summary failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/users ──────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/users", methods=["GET"])
def admin_dashboard_users(req: func.HttpRequest) -> func.HttpResponse:
    """Per-user table: email, tier, last activity, API calls in window,
    matched jobs surfaced, errors, total time spent (sum of durationMs).
    """
    try:
        _require_super_admin(req)
        days = _days_param(req, 30)
        since_iso = _since(days).isoformat()

        users = query_items(
            "profiles",
            "SELECT c.id, c.email, c.name, c.tier, c.createdAt, c.updatedAt, c.usageCounters, c.preferences.locations AS locations FROM c",
        )

        evts = query_items(
            "match_events",
            "SELECT c.userId, c.timestamp, c.companyId, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion FROM c WHERE c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )
        per_user: dict[str, dict] = defaultdict(lambda: {
            "calls": 0,
            "scraped": 0,
            "matched": 0,
            "errors": 0,
            "durationMs": 0,
            "lastSeen": None,
            "companies": set(),
        })
        for e in evts:
            uid = e.get("userId") or "unknown"
            s = per_user[uid]
            s["calls"] += 1
            s["scraped"] += int(e.get("scrapedCount") or 0)
            s["matched"] += int(e.get("matchedCount") or 0)
            s["durationMs"] += int(e.get("durationMs") or 0)
            ts = e.get("timestamp")
            if ts and (s["lastSeen"] is None or ts > s["lastSeen"]):
                s["lastSeen"] = ts
            mv = e.get("modelVersion") or ""
            if mv.startswith("err:") or mv.startswith("unhandled:"):
                s["errors"] += 1
            cid = e.get("companyId")
            if cid:
                s["companies"].add(cid)

        rows = []
        for u in users:
            uid = u.get("id")
            stats = per_user.get(uid, {
                "calls": 0, "scraped": 0, "matched": 0, "errors": 0,
                "durationMs": 0, "lastSeen": None, "companies": set(),
            })
            rows.append({
                "userId": uid,
                "email": u.get("email"),
                "name": u.get("name"),
                "tier": u.get("tier") or "free",
                "createdAt": u.get("createdAt"),
                "updatedAt": u.get("updatedAt"),
                "locations": u.get("locations") or [],
                "windowDays": days,
                "apiCalls": stats["calls"],
                "totalScraped": stats["scraped"],
                "totalMatched": stats["matched"],
                "errorCount": stats["errors"],
                "totalDurationMs": stats["durationMs"],
                "uniqueCompanies": len(stats["companies"]),
                "lastSeen": stats["lastSeen"],
                "todayDiscoverCount": (u.get("usageCounters") or {}).get("dailyDiscover", 0),
            })

        rows.sort(key=lambda r: r.get("lastSeen") or "", reverse=True)
        return success_response({"users": rows, "windowDays": days, "total": len(rows)})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin users failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/usage ──────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/usage", methods=["GET"])
def admin_dashboard_usage(req: func.HttpRequest) -> func.HttpResponse:
    """Time-series usage: per-day discover calls, scraped, matched, errors.
    Also returns top companies by call volume.
    """
    try:
        _require_super_admin(req)
        days = _days_param(req)
        since_iso = _since(days).isoformat()

        evts = query_items(
            "match_events",
            "SELECT c.userId, c.timestamp, c.companyId, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion FROM c WHERE c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )

        # Per-day buckets
        per_day: dict[str, dict] = defaultdict(lambda: {
            "discoverCalls": 0,
            "scraped": 0,
            "filtered": 0,
            "matched": 0,
            "errors": 0,
            "uniqueUsers": set(),
        })
        per_company: Counter = Counter()
        per_company_matched: Counter = Counter()
        per_user_calls: Counter = Counter()

        for e in evts:
            ts = _parse_iso(e.get("timestamp"))
            if not ts:
                continue
            day = ts.date().isoformat()
            b = per_day[day]
            b["discoverCalls"] += 1
            b["scraped"] += int(e.get("scrapedCount") or 0)
            b["filtered"] += int(e.get("filteredCount") or 0)
            b["matched"] += int(e.get("matchedCount") or 0)
            mv = e.get("modelVersion") or ""
            if mv.startswith("err:") or mv.startswith("unhandled:"):
                b["errors"] += 1
            uid = e.get("userId")
            if uid:
                b["uniqueUsers"].add(uid)
                per_user_calls[uid] += 1
            cid = e.get("companyId")
            if cid:
                per_company[cid] += 1
                per_company_matched[cid] += int(e.get("matchedCount") or 0)

        series = []
        for day in sorted(per_day):
            b = per_day[day]
            series.append({
                "day": day,
                "discoverCalls": b["discoverCalls"],
                "scraped": b["scraped"],
                "filtered": b["filtered"],
                "matched": b["matched"],
                "errors": b["errors"],
                "uniqueUsers": len(b["uniqueUsers"]),
            })

        top_companies = [
            {"companyId": cid, "calls": n, "totalMatched": per_company_matched[cid]}
            for cid, n in per_company.most_common(20)
        ]
        top_users = [
            {"userId": uid, "calls": n}
            for uid, n in per_user_calls.most_common(20)
        ]

        return success_response({
            "windowDays": days,
            "series": series,
            "topCompanies": top_companies,
            "topUsers": top_users,
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin usage failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/errors ─────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/errors", methods=["GET"])
def admin_dashboard_errors(req: func.HttpRequest) -> func.HttpResponse:
    """Recent error events: any match_events row whose modelVersion starts
    with `err:` or `unhandled:`. Plus companies with elevated zero-scrape
    rates (likely broken scrapers).
    """
    try:
        _require_super_admin(req)
        days = _days_param(req)
        since_iso = _since(days).isoformat()

        evts = query_items(
            "match_events",
            "SELECT c.userId, c.timestamp, c.companyId, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion FROM c WHERE c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )

        errors = []
        per_company_total: Counter = Counter()
        per_company_zero: Counter = Counter()
        per_kind: Counter = Counter()

        for e in evts:
            mv = e.get("modelVersion") or ""
            cid = e.get("companyId") or "unknown"
            per_company_total[cid] += 1
            if (e.get("scrapedCount") or 0) == 0:
                per_company_zero[cid] += 1
            if mv.startswith("err:") or mv.startswith("unhandled:"):
                errors.append({
                    "userId": e.get("userId"),
                    "companyId": cid,
                    "timestamp": e.get("timestamp"),
                    "kind": mv,
                    "durationMs": e.get("durationMs") or 0,
                })
                per_kind[mv] += 1

        errors.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        broken_scrapers = []
        for cid, total in per_company_total.most_common():
            zeros = per_company_zero[cid]
            if total >= 3 and zeros / total >= 0.8:
                broken_scrapers.append({
                    "companyId": cid,
                    "attempts": total,
                    "zeroScrapes": zeros,
                    "zeroRate": round(zeros / total, 3),
                })

        return success_response({
            "windowDays": days,
            "errors": errors[:200],
            "errorsByKind": [{"kind": k, "count": v} for k, v in per_kind.most_common()],
            "brokenScrapers": broken_scrapers,
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin errors failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/funnel ─────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/funnel", methods=["GET"])
def admin_dashboard_funnel(req: func.HttpRequest) -> func.HttpResponse:
    """Per-company funnel breakdown: scraped -> filtered -> matched, with
    drop reasons inferred from the recorded counts.
    """
    try:
        _require_super_admin(req)
        days = _days_param(req)
        since_iso = _since(days).isoformat()
        user_id = req.params.get("userId")  # optional filter

        q = "SELECT c.companyId, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion, c.userId FROM c WHERE c.timestamp >= @since"
        params = [{"name": "@since", "value": since_iso}]
        if user_id:
            q += " AND c.userId = @uid"
            params.append({"name": "@uid", "value": user_id})

        evts = query_items("match_events", q, params)

        per_company: dict[str, dict] = defaultdict(lambda: {
            "attempts": 0,
            "scraped": 0,
            "filtered": 0,
            "matched": 0,
            "durationMs": 0,
            "zeroScraped": 0,
            "filterKilled": 0,
            "rerankKilled": 0,
            "withResults": 0,
            "errors": 0,
        })
        for e in evts:
            cid = e.get("companyId") or "unknown"
            r = per_company[cid]
            r["attempts"] += 1
            sc = int(e.get("scrapedCount") or 0)
            fl = int(e.get("filteredCount") or 0)
            mc = int(e.get("matchedCount") or 0)
            r["scraped"] += sc
            r["filtered"] += fl
            r["matched"] += mc
            r["durationMs"] += int(e.get("durationMs") or 0)
            mv = e.get("modelVersion") or ""
            if mv.startswith("err:") or mv.startswith("unhandled:"):
                r["errors"] += 1
            elif sc == 0:
                r["zeroScraped"] += 1
            elif fl == 0:
                r["filterKilled"] += 1
            elif mc == 0:
                r["rerankKilled"] += 1
            else:
                r["withResults"] += 1

        funnel = []
        for cid, r in per_company.items():
            funnel.append({
                "companyId": cid,
                "attempts": r["attempts"],
                "totalScraped": r["scraped"],
                "totalFiltered": r["filtered"],
                "totalMatched": r["matched"],
                "avgDurationMs": (r["durationMs"] // r["attempts"]) if r["attempts"] else 0,
                "zeroScraped": r["zeroScraped"],
                "filterKilled": r["filterKilled"],
                "rerankKilled": r["rerankKilled"],
                "withResults": r["withResults"],
                "errors": r["errors"],
                "successRate": round(r["withResults"] / r["attempts"], 3) if r["attempts"] else 0,
            })

        funnel.sort(key=lambda x: x["attempts"], reverse=True)

        # Totals across all companies
        totals = {
            "attempts": sum(f["attempts"] for f in funnel),
            "scraped": sum(f["totalScraped"] for f in funnel),
            "filtered": sum(f["totalFiltered"] for f in funnel),
            "matched": sum(f["totalMatched"] for f in funnel),
            "withResults": sum(f["withResults"] for f in funnel),
            "zeroScraped": sum(f["zeroScraped"] for f in funnel),
            "filterKilled": sum(f["filterKilled"] for f in funnel),
            "rerankKilled": sum(f["rerankKilled"] for f in funnel),
            "errors": sum(f["errors"] for f in funnel),
        }

        return success_response({
            "windowDays": days,
            "userId": user_id,
            "totals": totals,
            "perCompany": funnel,
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin funnel failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/costs ──────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/costs", methods=["GET"])
def admin_dashboard_costs(req: func.HttpRequest) -> func.HttpResponse:
    """Cost estimates derived from telemetry.

    We don't have direct access to the Azure Cost Management API from inside
    the Function App without a managed-identity grant, so we surface
    actionable PROXIES instead:
      - LLM call count per model (rerank/embed) × token-price estimate.
      - Per-user, per-day spend estimate.
      - Scrape call volume (Cosmos read RU rough estimate).

    The unit prices below are taken from the configured Azure OpenAI
    deployment sku rates and can be overridden by env vars without a
    redeploy. They are intentionally rough; the goal is to spot abusive
    users and runaway spend, not to replace Azure billing.
    """
    try:
        _require_super_admin(req)
        days = _days_param(req)
        since_iso = _since(days).isoformat()

        # Pricing knobs (USD per 1K tokens) — defaults match gpt-4o-mini /
        # text-embedding-3-small. Override per environment.
        price_rerank_in = float(os.environ.get("PRICE_RERANK_IN_PER_1K", "0.00015"))
        price_rerank_out = float(os.environ.get("PRICE_RERANK_OUT_PER_1K", "0.0006"))
        price_embed = float(os.environ.get("PRICE_EMBED_PER_1K", "0.00002"))
        # Average tokens per discover (very rough)
        avg_rerank_in = float(os.environ.get("AVG_RERANK_IN_TOKENS", "2500"))
        avg_rerank_out = float(os.environ.get("AVG_RERANK_OUT_TOKENS", "300"))
        avg_embed_tokens = float(os.environ.get("AVG_EMBED_TOKENS_PER_CALL", "900"))

        evts = query_items(
            "match_events",
            "SELECT c.userId, c.timestamp, c.matchedCount, c.scrapedCount, c.rerankModel FROM c WHERE c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )

        per_day: dict[str, dict] = defaultdict(lambda: {
            "rerank": 0.0, "embed": 0.0, "discovers": 0,
        })
        per_user: dict[str, float] = defaultdict(float)
        per_service = {"rerank": 0.0, "embed": 0.0, "scrape": 0.0}

        for e in evts:
            ts = _parse_iso(e.get("timestamp"))
            if not ts:
                continue
            day = ts.date().isoformat()
            uid = e.get("userId") or "unknown"
            scraped = int(e.get("scrapedCount") or 0)

            # Rerank cost: 1 LLM call per discover (when matched window
            # had enough candidates).
            rerank_in_tokens = avg_rerank_in if scraped > 0 else 0
            rerank_out_tokens = avg_rerank_out if scraped > 0 else 0
            rerank_cost = (
                rerank_in_tokens * price_rerank_in / 1000.0
                + rerank_out_tokens * price_rerank_out / 1000.0
            )
            # Embedding cost scales with scraped jobs (per-job embedding).
            embed_tokens = avg_embed_tokens * max(scraped, 0)
            embed_cost = embed_tokens * price_embed / 1000.0

            per_day[day]["rerank"] += rerank_cost
            per_day[day]["embed"] += embed_cost
            per_day[day]["discovers"] += 1
            per_user[uid] += rerank_cost + embed_cost
            per_service["rerank"] += rerank_cost
            per_service["embed"] += embed_cost

        series = []
        for day in sorted(per_day):
            b = per_day[day]
            total = b["rerank"] + b["embed"]
            series.append({
                "day": day,
                "rerank": round(b["rerank"], 4),
                "embed": round(b["embed"], 4),
                "total": round(total, 4),
                "discovers": b["discovers"],
            })

        top_users = [
            {"userId": uid, "estUsd": round(cost, 4)}
            for uid, cost in sorted(per_user.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ]

        return success_response({
            "windowDays": days,
            "currency": "USD",
            "estimateOnly": True,
            "perDay": series,
            "perService": {k: round(v, 4) for k, v in per_service.items()},
            "topUsers": top_users,
            "pricing": {
                "rerankInPer1K": price_rerank_in,
                "rerankOutPer1K": price_rerank_out,
                "embedPer1K": price_embed,
                "avgRerankIn": avg_rerank_in,
                "avgRerankOut": avg_rerank_out,
                "avgEmbedPerCall": avg_embed_tokens,
            },
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin costs failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/user/<userId> ──────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/user/{userId}", methods=["GET"])
def admin_dashboard_user_detail(req: func.HttpRequest) -> func.HttpResponse:
    """Drill into a single user: profile snapshot + last N events."""
    try:
        _require_super_admin(req)
        uid = req.route_params.get("userId")
        if not uid:
            raise ValidationError("userId is required")
        days = _days_param(req, 30)
        since_iso = _since(days).isoformat()

        profile_rows = query_items(
            "profiles",
            "SELECT TOP 1 * FROM c WHERE c.id = @uid",
            [{"name": "@uid", "value": uid}],
        )
        if not profile_rows:
            raise NotFoundError("User not found")
        profile = profile_rows[0]

        # Strip large fields before returning
        for k in ("profileEmbedding", "resumeRaw"):
            profile.pop(k, None)

        evts = query_items(
            "match_events",
            "SELECT TOP 500 c.timestamp, c.companyId, c.scrapedCount, c.filteredCount, c.matchedCount, c.durationMs, c.modelVersion, c.searchId FROM c WHERE c.userId = @uid AND c.timestamp >= @since ORDER BY c.timestamp DESC",
            [{"name": "@uid", "value": uid}, {"name": "@since", "value": since_iso}],
        )

        return success_response({
            "userId": uid,
            "profile": profile,
            "windowDays": days,
            "events": evts,
            "totals": {
                "calls": len(evts),
                "scraped": sum(int(e.get("scrapedCount") or 0) for e in evts),
                "filtered": sum(int(e.get("filteredCount") or 0) for e in evts),
                "matched": sum(int(e.get("matchedCount") or 0) for e in evts),
                "errors": sum(
                    1 for e in evts
                    if (e.get("modelVersion") or "").startswith(("err:", "unhandled:"))
                ),
            },
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin user detail failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/runs ────────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/runs", methods=["GET"])
def admin_dashboard_runs(req: func.HttpRequest) -> func.HttpResponse:
    """List discover runs with full funnel breakdown.

    GET /api/v1/admin/dashboard/runs?days=7&userId=user-aaaa1111

    Each run is one bulk/company/linkedin search invocation with per-company
    funnel: scraped → locFiltered → matched → vectorScored → reranked → displayed.
    """
    try:
        _require_super_admin(req)
        days = _days_param(req, 7)
        since_iso = _since(days).isoformat()
        user_id = req.params.get("userId")
        run_type = req.params.get("type")  # "bulk", "company", "linkedin"

        q = "SELECT * FROM c WHERE c.kind = 'discover_run' AND c.timestamp >= @since"
        params = [{"name": "@since", "value": since_iso}]
        if user_id:
            q += " AND c.userId = @uid"
            params.append({"name": "@uid", "value": user_id})
        if run_type:
            q += " AND c.runType = @rt"
            params.append({"name": "@rt", "value": run_type})

        runs = query_items("match_events", q, params)

        # Sort newest first
        runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

        # Build summary for each run
        rows = []
        for r in runs[:100]:
            per_co = r.get("perCompany") or []
            rows.append({
                "runId": r.get("id"),
                "userId": r.get("userId"),
                "email": r.get("email"),
                "runType": r.get("runType"),
                "timestamp": r.get("timestamp"),
                "queries": r.get("queries", []),
                "locations": r.get("locations", []),
                "durationMs": r.get("durationMs", 0),
                "companiesRequested": r.get("companiesRequested", 0),
                "companiesWithResults": r.get("companiesWithResults", 0),
                "totalScraped": r.get("totalScraped", 0),
                "totalMatched": r.get("totalMatched", 0),
                "totalDisplayed": r.get("totalDisplayed", 0),
                "keepPct": r.get("keepPct", 0),
                "linkedInPoolSize": r.get("linkedInPoolSize", 0),
                "perCompany": per_co,
            })

        return success_response({
            "windowDays": days,
            "userId": user_id,
            "runType": run_type,
            "total": len(rows),
            "runs": rows,
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin runs failed: %s", e)
        return internal_error_response(str(e))


# ── /api/v1/feedback (user-facing — any logged-in user) ─────────────────────
@bp.route(route="api/v1/feedback", methods=["POST"])
def submit_feedback(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/feedback — Submit feedback or feature request."""
    try:
        import uuid
        user_id = get_user_id(req)
        body = req.get_json() or {}
        text = (body.get("text") or "").strip()
        category = (body.get("category") or "feedback").strip()
        page = (body.get("page") or "").strip()

        if not text:
            raise ValidationError("Feedback text is required")
        if len(text) > 5000:
            raise ValidationError("Feedback too long (max 5000 chars)")

        # Look up email from profile
        email = ""
        try:
            from shared.cosmos_client import read_item
            profile = read_item("profiles", user_id, user_id)
            if profile:
                email = profile.get("email", "")
        except Exception:
            pass

        now = datetime.now(timezone.utc)
        doc = {
            "id": f"fb-{int(now.timestamp())}-{uuid.uuid4().hex[:6]}",
            "userId": user_id,
            "email": email,
            "kind": "feedback",
            "category": category,  # "feedback", "bug", "feature"
            "page": page,
            "text": text,
            "timestamp": now.isoformat(),
            "status": "new",
            "ttl": 365 * 24 * 60 * 60,  # 1 year
        }
        upsert_item("match_events", doc)
        logger.info("[FEEDBACK] %s (%s): %s", email or user_id, category, text[:80])
        return success_response({"message": "Thanks for your feedback!", "id": doc["id"]})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("feedback submit failed: %s", e)
        return internal_error_response(str(e))


# ── /admin/dashboard/feedback ────────────────────────────────────────────────
@bp.route(route="api/v1/admin/dashboard/feedback", methods=["GET"])
def admin_dashboard_feedback(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/admin/dashboard/feedback — List all user feedback."""
    try:
        _require_super_admin(req)
        days = _days_param(req, 90)
        since_iso = _since(days).isoformat()

        rows = query_items(
            "match_events",
            "SELECT c.id, c.userId, c.email, c.category, c.page, c.text, c.timestamp, c.status FROM c WHERE c.kind = 'feedback' AND c.timestamp >= @since",
            [{"name": "@since", "value": since_iso}],
        )
        rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

        return success_response({
            "windowDays": days,
            "total": len(rows),
            "feedback": rows[:200],
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("admin feedback failed: %s", e)
        return internal_error_response(str(e))


# ── /api/v1/contact (public — no auth) ───────────────────────────────────────
@bp.route(route="api/v1/contact", methods=["POST"])
def submit_contact(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/contact — Public contact form. No auth required.

    Body: { "name": str, "email": str, "subject": str, "message": str }
    Stores in match_events with kind='contact' so admin dashboard can see it.
    """
    try:
        import uuid as _uuid
        body = req.get_json() or {}
        name = (body.get("name") or "").strip()
        email = (body.get("email") or "").strip()
        subject = (body.get("subject") or "General enquiry").strip()
        message = (body.get("message") or "").strip()

        if not message:
            raise ValidationError("Message is required")
        if len(message) > 5000:
            raise ValidationError("Message too long (max 5000 characters)")
        if email and "@" not in email:
            raise ValidationError("Invalid email address")

        # Basic spam guard — block obviously empty or very short messages.
        if len(message) < 5:
            raise ValidationError("Message is too short")

        now = datetime.now(timezone.utc)
        doc = {
            "id": f"ct-{int(now.timestamp())}-{_uuid.uuid4().hex[:6]}",
            "kind": "contact",
            "name": name[:200],
            "email": email[:200],
            "subject": subject[:300],
            "message": message[:5000],
            "timestamp": now.isoformat(),
            "status": "new",
            "ttl": 365 * 24 * 60 * 60,  # 1 year
            # IP for rate-limit audit — not stored in user-facing profile.
            "ip": req.headers.get("X-Forwarded-For", "")[:60],
        }
        upsert_item("match_events", doc)
        logger.info("[CONTACT] from=%s subject=%s", email or "anon", subject[:60])
        return success_response({
            "message": (
                "Thank you for reaching out! We\u2019ll get back to you "
                "at the email you provided within 2 business days."
            ),
            "id": doc["id"],
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("contact submit failed: %s", e)
        return internal_error_response(str(e))

