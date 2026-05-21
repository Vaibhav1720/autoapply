"""Usage tracking & free-tier rate limiting.

Tracks daily usage per user in Cosmos ``profiles`` (inline field) to avoid
creating a separate container. Checks are cheap (single read that's already
done by get_user_id/profile load) and writes are best-effort upserts.

Free-tier limits (server-enforced):
    discovers       2 / day
    linkedin        2 / day
    autofill_ai     5 / day   (AI suggestions only; rule-based fill is free)
    resume_tailor   blur      (computed server-side, first 3 lines returned)
    companies       5

Premium users (tier == "premium") bypass all limits.
"""

import logging
from datetime import datetime, timezone

from shared.cosmos_client import read_item, upsert_item
from shared.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# ── Free-tier daily limits ────────────────────────────────────────────────
FREE_LIMITS: dict[str, int] = {
    "discovers": 2,
    "linkedin": 2,
    "autofill_ai": 5,
    "companies": 5,
}

UPGRADE_MESSAGE = (
    "You've reached your daily free limit. "
    "Upgrade to Premium for just ₹99/month — less than ₹3.50/day — "
    "and unlock unlimited job searches, AI autofill, and resume tailoring. "
    "A small investment that could help you land your dream job and change your career path."
)


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_usage(profile: dict) -> dict:
    """Return today's usage counters from the profile, resetting if stale."""
    usage = profile.get("dailyUsage") or {}
    if usage.get("date") != _today_key():
        usage = {"date": _today_key(), "discovers": 0, "linkedin": 0, "autofill_ai": 0}
    return usage


def get_tier(profile: dict) -> str:
    """Return the user's subscription tier (default 'free')."""
    return (profile.get("subscription") or {}).get("tier", "free")


def is_premium(profile: dict) -> bool:
    return get_tier(profile) != "free"


def check_limit(profile: dict, feature: str) -> dict:
    """Check if the user can use ``feature``. Returns usage dict.

    Raises RateLimitError with upgrade message if the free limit is hit.
    Premium users always pass.
    """
    if is_premium(profile):
        return _get_usage(profile)

    usage = _get_usage(profile)
    limit = FREE_LIMITS.get(feature)
    if limit is None:
        return usage

    current = usage.get(feature, 0)
    if current >= limit:
        logger.info("[USAGE] user=%s hit free limit %s=%d/%d",
                    profile.get("id", "?"), feature, current, limit)
        raise RateLimitError(UPGRADE_MESSAGE)

    return usage


def increment(profile: dict, feature: str) -> None:
    """Increment the counter for ``feature`` and persist to Cosmos.

    Best-effort — never raises on write failure.
    """
    usage = _get_usage(profile)
    usage[feature] = usage.get(feature, 0) + 1
    usage["date"] = _today_key()
    profile["dailyUsage"] = usage

    try:
        upsert_item("profiles", profile)
    except Exception as e:
        logger.warning("[USAGE] failed to persist usage for user=%s: %s",
                       profile.get("id", "?"), e)


def get_usage_summary(profile: dict) -> dict:
    """Return usage info for the frontend (current counts + limits)."""
    tier = get_tier(profile)
    usage = _get_usage(profile)

    if tier != "free":
        return {
            "tier": tier,
            "limits": None,
            "usage": usage,
            "upgradeMessage": None,
        }

    return {
        "tier": "free",
        "limits": FREE_LIMITS,
        "usage": {
            "discovers": usage.get("discovers", 0),
            "linkedin": usage.get("linkedin", 0),
            "autofill_ai": usage.get("autofill_ai", 0),
        },
        "remaining": {
            k: max(0, FREE_LIMITS[k] - usage.get(k, 0))
            for k in FREE_LIMITS if k != "companies"
        },
        "upgradeMessage": UPGRADE_MESSAGE,
        "upgradePrice": {"amount": 99, "currency": "INR", "period": "month"},
    }


def check_company_limit(profile: dict) -> None:
    """Check if free user has exceeded the company selection limit."""
    if is_premium(profile):
        return
    selected = profile.get("selectedCompanies") or []
    if len(selected) > FREE_LIMITS["companies"]:
        raise RateLimitError(
            f"Free accounts can select up to {FREE_LIMITS['companies']} companies. "
            + UPGRADE_MESSAGE
        )
