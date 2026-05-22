"""Billing HTTP routes — Lemon Squeezy (international) + Razorpay (India).

Exposes:
  - GET  /api/v1/billing/plans              country-aware catalogue
  - POST /api/v1/billing/checkout           Lemon Squeezy (international)
  - POST /api/v1/billing/razorpay/checkout  Razorpay Payment Link / Subscription (India)
  - POST /api/v1/billing/razorpay/create-order   Standard Checkout — create order
  - POST /api/v1/billing/razorpay/verify-payment Standard Checkout — verify signature
  - POST /api/v1/billing/create-order            alias → create-order
  - POST /api/v1/billing/verify-payment            alias → verify-payment
  - GET  /api/v1/billing/subscription
  - POST /api/v1/billing/cancel
  - GET  /api/v1/billing/portal
  - POST /api/v1/webhooks/lemonsqueezy      (no auth — HMAC verified)
  - POST /api/v1/webhooks/razorpay          (no auth — HMAC verified)
  - POST /api/v1/billing/razorpay/webhook   (alias → webhooks/razorpay)

Country routing:
  country == "IN"  →  Razorpay (INR, ₹199/mo, ₹1799/yr)
  everything else  →  Lemon Squeezy (USD, $9.99/mo, $89.99/yr)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.cosmos_client import (
    get_container,
    read_item,
    upsert_item,
    query_items,
)
from shared.exceptions import (
    AppException,
    NotFoundError,
    ValidationError,
)
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)

from shared.geoip import country_for_request

from . import lemonsqueezy_client as ls
from . import razorpay_client as rp

logger = logging.getLogger(__name__)
bp = func.Blueprint()

# ── Plans (public catalogue) ────────────────────────────────────────────────

_FREE_PLAN: dict = {
    "id": "free",
    "name": "Free",
    "priceUsd": 0,
    "priceInr": 0,
    "interval": "—",
    "tagline": "Try HirePanda with limited daily quotas",
    "features": [
        "2 Discover searches / day",
        "2 LinkedIn searches / day",
        "5 AI autofill suggestions / day",
        "5 companies tracked",
        "Basic resume tailoring",
    ],
    "ctaLabel": "Current plan",
}

# International (USD) — Lemon Squeezy (subscription variants only)
PLANS_USD: list[dict] = [
    _FREE_PLAN,
    {
        "id": "pro_weekly",
        "name": "Pro Weekly",
        "tier": "pro",
        "priceUsd": 3.49,
        "interval": "week",
        "tagline": "Full Pro access, billed every week",
        "features": [
            "Unlimited Discover searches",
            "Unlimited LinkedIn searches",
            "Unlimited AI autofill",
            "Track up to 50 companies",
            "Advanced AI resume tailoring",
            "Priority support",
            "Cancel anytime",
        ],
        "ctaLabel": "Upgrade — $3.49/wk",
        "paymentProvider": "lemonsqueezy",
        "lsVariantEnv": "LEMONSQUEEZY_VARIANT_PRO_WEEKLY",
    },
    {
        "id": "pro_monthly",
        "name": "Pro Monthly",
        "tier": "pro",
        "priceUsd": 9.99,
        "interval": "month",
        "tagline": "Unlimited everything, billed monthly",
        "features": [
            "Unlimited Discover searches",
            "Unlimited LinkedIn searches",
            "Unlimited AI autofill",
            "Track up to 50 companies",
            "Advanced AI resume tailoring",
            "Priority support",
            "Cancel anytime",
        ],
        "ctaLabel": "Upgrade — $9.99/mo",
        "paymentProvider": "lemonsqueezy",
        "lsVariantEnv": "LEMONSQUEEZY_VARIANT_PRO_MONTHLY",
    },
    {
        "id": "pro_yearly",
        "name": "Pro Yearly",
        "tier": "pro",
        "priceUsd": 89.99,
        "interval": "year",
        "tagline": "Save 25% — best value",
        "features": [
            "Everything in Pro Monthly",
            "Save $30 vs monthly billing",
            "Best for active job seekers",
            "Cancel anytime, refund pro-rated",
        ],
        "ctaLabel": "Upgrade — $89.99/yr",
        "paymentProvider": "lemonsqueezy",
        "lsVariantEnv": "LEMONSQUEEZY_VARIANT_PRO_YEARLY",
        "highlight": True,
    },
]

# India (INR) — Razorpay
PLANS_INR: list[dict] = [
    _FREE_PLAN,
    {
        "id": "pro_monthly",
        "name": "Pro Monthly",
        "tier": "pro",
        "priceInr": 199,
        "amountPaise": 19900,
        "interval": "month",
        "tagline": "Unlimited everything, billed monthly",
        "features": [
            "Unlimited Discover searches",
            "Unlimited LinkedIn searches",
            "Unlimited AI autofill",
            "Track up to 50 companies",
            "Advanced AI resume tailoring",
            "Priority support",
            "Cancel anytime",
        ],
        "ctaLabel": "Upgrade — \u20b9199/mo",
        "paymentProvider": "razorpay",
        "rzpPlanEnv": "RAZORPAY_PLAN_PRO_MONTHLY",
    },
    {
        "id": "pro_yearly",
        "name": "Pro Yearly",
        "tier": "pro",
        "priceInr": 1799,
        "amountPaise": 179900,
        "interval": "year",
        "tagline": "Save 25% — best value",
        "features": [
            "Everything in Pro Monthly",
            "Save \u20b9589 vs monthly billing",
            "Best for active job seekers",
            "Cancel anytime, refund pro-rated",
        ],
        "ctaLabel": "Upgrade — \u20b91,799/yr",
        "paymentProvider": "razorpay",
        "rzpPlanEnv": "RAZORPAY_PLAN_PRO_YEARLY",
        "highlight": True,
    },
]

# Keep a flat PLANS alias so existing code (_plan_by_id) works for USD path.
PLANS = PLANS_USD


def _is_india_country(country: str) -> bool:
    """Return True for any India country code / name variant."""
    c = (country or "").strip().upper()
    return c in ("IN", "IND", "INDIA")


def _normalise_country(country: str) -> str:
    """Normalise India variants to 'IN'; return the uppercased input for others."""
    c = (country or "").strip().upper()
    if c in ("IND", "INDIA"):
        return "IN"
    return c


def _resolve_country(req: func.HttpRequest) -> str:
    """Authoritative country code for the request.

    Resolution order:
      1. IP geo (cannot be spoofed via profile/params)         ← preferred
      2. ?country=XX query param                                ← dev/test fallback
      3. Authenticated user's profile.applicationDetails.country ← last resort
         (only used when IP geo returns "" — typically when ipinfo.io is
          unreachable or X-Forwarded-For is missing in front of the Function App)

    Profile fallback is safe because:
      * Razorpay only accepts INR cards, so a non-India user cannot complete
        a fraudulent INR checkout even if they spoof profile.country = "India".
      * The reverse (India user wanting USD/Lemon Squeezy at $9.99 ≈ ₹830)
        is more expensive than ₹199 INR — no economic incentive to game.
    """
    from shared.geoip import country_for_request as _geo
    ip_country = _geo(req)
    if ip_country:
        return _normalise_country(ip_country)

    # Dev / local traffic (private IP) — accept the query param as fallback.
    param_country = (req.params.get("country") or "").strip().upper()
    if param_country:
        return _normalise_country(param_country)

    # Last resort — read profile country only if the user is authenticated.
    # We swallow auth errors here so unauthenticated /plans calls still work.
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id) or {}
        profile_country = (
            (profile.get("applicationDetails") or {}).get("country") or ""
        ).strip().upper()
        if profile_country:
            return _normalise_country(profile_country)
    except Exception:
        pass
    return ""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _renews_at_iso(interval: str, from_dt: datetime | None = None) -> str:
    """Next renewal/end timestamp for Razorpay (no recurring webhook metadata)."""
    base = from_dt or datetime.now(timezone.utc)
    if (interval or "").lower() == "year":
        return (base + timedelta(days=365)).isoformat()
    return (base + timedelta(days=30)).isoformat()


def _notify_payment_receipt(
    user_id: str,
    *,
    plan_name: str,
    amount_display: str,
    payment_id: str,
    provider: str,
    interval: str = "month",
) -> None:
    """Send payment receipt email (best-effort)."""
    try:
        from shared.email_service import send_payment_receipt, _user_email_from_profile

        profile = read_item("profiles", user_id, user_id) or {}
        email, name = _user_email_from_profile(profile)
        if not email:
            user = read_item("users", user_id, user_id) or {}
            email = (user.get("email") or "").strip()
            name = user.get("name", "")
        if email:
            send_payment_receipt(
                user_id=user_id,
                email=email,
                name=name,
                plan_name=plan_name,
                amount_display=amount_display,
                payment_id=payment_id,
                provider=provider,
                interval=interval,
            )
    except Exception as e:
        logger.warning("[BILLING] payment receipt email failed user=%s: %s", user_id, e)


def _upgrade_rzp_one_time_pro(
    user_id: str,
    *,
    plan_id: str,
    payment_id: str,
    order_id: str = "",
    raw_payload: dict | None = None,
) -> None:
    """Mirror webhook one-time upgrade after Standard Checkout verification."""
    interval = "year" if "yearly" in plan_id else "month"
    plan = next((p for p in PLANS_INR if p["id"] == plan_id), {})
    price_inr = plan.get("priceInr")
    plan_name = plan.get("name") or plan_id or "Pro"
    renews_at = _renews_at_iso(interval)
    now = _now_iso()
    _ensure_subscriptions_container()
    doc = {
        "id": f"sub-rzp-{payment_id}",
        "userId": user_id,
        "provider": "razorpay",
        "paymentType": "one_time",
        "rzpPaymentId": payment_id,
        "rzpOrderId": order_id,
        "planId": plan_id,
        "interval": interval,
        "priceInr": price_inr,
        "currency": "INR",
        "status": "active",
        "renewsAt": renews_at,
        "createdAt": now,
        "updatedAt": now,
        "raw": raw_payload or {},
    }
    upsert_item("subscriptions", doc)
    profile = read_item("profiles", user_id, user_id)
    if profile:
        profile["subscription"] = {
            "tier": "pro",
            "status": "active",
            "interval": interval,
            "paymentType": "one_time",
            "priceInr": price_inr,
            "provider": "razorpay",
            "rzpPaymentId": payment_id,
            "renewsAt": renews_at,
            "updatedAt": now,
        }
        profile["tier"] = "pro"
        upsert_item("profiles", profile)
    amount_display = f"₹{price_inr}" if price_inr else "₹—"
    _notify_payment_receipt(
        user_id,
        plan_name=plan_name,
        amount_display=amount_display,
        payment_id=payment_id,
        provider="Razorpay",
        interval=interval,
    )


def _public_sub(profile: dict | None) -> dict:
    """Compact, JSON-safe view of `profile.subscription` for the client."""
    if not profile:
        return {"tier": "free", "status": "none"}
    sub = (profile.get("subscription") or {})
    provider = sub.get("provider") or ("razorpay" if sub.get("rzpPaymentId") else "lemonsqueezy")
    status = (sub.get("status") or "none").lower()
    ends_at = sub.get("endsAt")
    # Cancelled subs often keep renewsAt as the paid-through date; expose it as endsAt.
    if status in ("cancelled", "expired") and not ends_at:
        ends_at = sub.get("renewsAt")
    return {
        "tier": (sub.get("tier") or profile.get("tier") or "free").lower(),
        "status": sub.get("status") or "none",
        "interval": sub.get("interval"),
        "renewsAt": sub.get("renewsAt"),
        "endsAt": ends_at,
        "cancelledAt": sub.get("cancelledAt"),
        "priceUsd": sub.get("priceUsd"),
        "priceInr": sub.get("priceInr"),
        "provider": provider,
        "paymentType": sub.get("paymentType") or "recurring",
        "lsSubscriptionId": sub.get("lsSubscriptionId"),
        "rzpPaymentId": sub.get("rzpPaymentId"),
        "rzpSubscriptionId": sub.get("rzpSubscriptionId"),
        "manageUrl": sub.get("urls", {}).get("customer_portal") if isinstance(sub.get("urls"), dict) else None,
    }


def _plan_by_id(plan_id: str) -> dict | None:
    for p in PLANS:
        if p["id"] == plan_id:
            return p
    return None


def _variant_for_plan(plan: dict) -> str:
    env = plan.get("lsVariantEnv")
    if not env:
        return ""
    return os.environ.get(env, "").strip()


# Resolve Cosmos doc id for a Lemon Squeezy subscription. We use a stable
# id per LS subscription id so re-deliveries idempotently overwrite the
# same row.
def _sub_doc_id(ls_subscription_id: str | int) -> str:
    return f"sub-ls-{ls_subscription_id}"


def _find_real_ls_subscription(user_id: str) -> dict | None:
    """Find the user's authoritative LS subscription doc (one with `customer_portal`).

    Walks the `subscriptions` container for any doc owned by `user_id` and
    returns the most recently-updated one whose `urls` contains a real
    `customer_portal` link (i.e. NOT an invoice-only doc). Used to self-heal
    profiles that have an invoice id stored as `lsSubscriptionId`.
    """
    try:
        from shared.cosmos_client import get_cosmos_client
        db = get_cosmos_client()
        container = db.get_container_client("subscriptions")
        items = list(container.query_items(
            query="SELECT * FROM c WHERE c.userId = @uid",
            parameters=[{"name": "@uid", "value": user_id}],
            enable_cross_partition_query=True,
        ))
        # Prefer docs that have a customer_portal URL; sort by updatedAt desc
        candidates = [
            s for s in items
            if isinstance(s.get("urls"), dict)
            and (s["urls"].get("customer_portal") or s["urls"].get("update_payment_method"))
        ]
        candidates.sort(key=lambda s: str(s.get("updatedAt") or ""), reverse=True)
        if candidates:
            return candidates[0]
    except Exception:
        logger.exception("_find_real_ls_subscription failed")
    return None


def _ensure_subscriptions_container():
    """Lazy create the `subscriptions` container if missing.

    Safe to call repeatedly. Only creates on first 404.
    """
    try:
        from shared.cosmos_client import get_cosmos_client
        from azure.cosmos import PartitionKey, exceptions

        db = get_cosmos_client()
        try:
            db.create_container_if_not_exists(
                id="subscriptions",
                partition_key=PartitionKey(path="/userId"),
                offer_throughput=None,
            )
        except Exception as e:
            logger.debug("subscriptions container ensure: %s", e)
    except Exception:
        logger.exception("Failed to ensure subscriptions container")


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route(route="api/v1/billing/plans", methods=["GET"])
def list_plans(req: func.HttpRequest) -> func.HttpResponse:
    """Country-aware plan catalogue. No auth required (but uses profile if available).

    Country resolution: IP geo → ?country param → profile.country (last resort).
    See _resolve_country() for details.

    India → INR / Razorpay   |   Everything else → USD / Lemon Squeezy
    """
    try:
        country = _resolve_country(req)
        is_india = _is_india_country(country)

        # Diagnostic logging: surface IP vs profile mismatch for debugging
        ip_country = country_for_request(req)
        if ip_country and country != _normalise_country(ip_country):
            logger.info(
                "[BILLING] Resolved country=%s differs from IP=%s (lookup OK)",
                country, ip_country,
            )
        elif not ip_country:
            logger.info("[BILLING] IP geo returned empty — resolved via fallback to %s", country)

        plans = PLANS_INR if is_india else PLANS_USD
        return success_response({
            "plans": plans,
            "country": country,
            "currency": "INR" if is_india else "USD",
        })
    except Exception as e:
        logger.exception("list_plans failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/billing/checkout", methods=["POST"])
def create_checkout(req: func.HttpRequest) -> func.HttpResponse:
    """Create a hosted Lemon Squeezy checkout (international / non-India only).

    Body:
      { "planId": "pro_weekly" | "pro_monthly" | "pro_yearly" }

    Lemon Squeezy checkout is subscription-only (auto-renews each period).

    Rejects requests from Indian IPs — they must use /billing/razorpay/checkout.
    """
    try:
        user_id = get_user_id(req)

        # Block India IPs from accessing the USD checkout.
        ip_country = _resolve_country(req)
        if _is_india_country(ip_country):
            raise ValidationError(
                "Indian users must use the Razorpay checkout (/billing/razorpay/checkout). "
                "If you believe this is a mistake, contact support."
            )

        body = req.get_json() if req.get_body() else {}
        plan_id = (body.get("planId") or "").strip()
        if not plan_id:
            raise ValidationError("planId is required")

        plan = _plan_by_id(plan_id)
        if not plan or plan["id"] == "free":
            raise ValidationError(f"Unknown or non-purchasable plan: {plan_id}")

        variant = _variant_for_plan(plan)
        if not variant:
            raise ValidationError(
                "Plan is not configured for purchase — missing variant id "
                "(set the matching LEMONSQUEEZY_VARIANT_* env var)"
            )

        profile = read_item("profiles", user_id, user_id) or {}
        personal = profile.get("personal") or {}
        email = (profile.get("email") or personal.get("email") or "").strip()
        name = (
            (personal.get("firstName") or "") + " " + (personal.get("lastName") or "")
        ).strip()
        if not email:
            user = read_item("users", user_id, user_id) or {}
            email = (user.get("email") or "").strip()
        if not email:
            raise ValidationError("User has no email on file — cannot start checkout")

        success_redirect = (
            (body.get("successUrl") or "").strip()
            or os.environ.get("BILLING_SUCCESS_URL", "")
        )

        result = ls.create_checkout(
            variant_id_str=variant,
            user_id=user_id,
            user_email=email,
            user_name=name,
            success_redirect_url=success_redirect,
        )
        if not result.get("url"):
            raise AppException(
                "Failed to create checkout URL",
                code="CHECKOUT_ERROR",
                status_code=502,
            )

        return success_response({
            "url": result["url"],
            "expiresAt": result.get("expires_at"),
            "paymentType": "recurring",
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("create_checkout failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/billing/razorpay/checkout", methods=["POST"])
def razorpay_checkout(req: func.HttpRequest) -> func.HttpResponse:
    """Create a Razorpay checkout session (India IPs only).

    Body:
      {
        "planId":      "pro_monthly" | "pro_yearly",
        "paymentType": "one_time" | "recurring"   (default: "recurring")
      }

    paymentType = "recurring"  → Razorpay Subscription (auto-renews each period)
    paymentType = "one_time"   → Standard Checkout order (single period, no auto-renewal)
                                  Requires RAZORPAY_PLAN_PRO_MONTHLY / _YEARLY env vars.

    Returns: { "url": "...", "paymentProvider": "razorpay", "paymentType": "..." }
    """
    try:
        user_id = get_user_id(req)

        # Only allow Indian IPs to use Razorpay.
        ip_country = _resolve_country(req)
        if ip_country and not _is_india_country(ip_country):
            raise ValidationError(
                "Razorpay is only available for users in India. "
                "Please use the international checkout instead."
            )

        body = req.get_json() if req.get_body() else {}
        plan_id = (body.get("planId") or "").strip()
        payment_type = (body.get("paymentType") or "recurring").strip().lower()
        if payment_type not in ("one_time", "recurring"):
            raise ValidationError("paymentType must be 'one_time' or 'recurring'")
        if not plan_id:
            raise ValidationError("planId is required")

        plan = next((p for p in PLANS_INR if p["id"] == plan_id), None)
        if not plan or plan["id"] == "free":
            raise ValidationError(f"Unknown or non-purchasable plan: {plan_id}")

        if not rp.is_configured():
            raise AppException(
                "Razorpay is not yet configured. Please try again later.",
                code="PAYMENT_UNAVAILABLE",
                status_code=503,
            )

        profile = read_item("profiles", user_id, user_id) or {}
        personal = profile.get("personal") or {}
        email = (profile.get("email") or personal.get("email") or "").strip()
        name = (
            (personal.get("firstName") or "") + " " + (personal.get("lastName") or "")
        ).strip() or "AutoApply User"
        if not email:
            user = read_item("users", user_id, user_id) or {}
            email = (user.get("email") or "").strip()
        if not email:
            raise ValidationError("User has no email on file — cannot start checkout")

        success_url = (
            (body.get("successUrl") or "").strip()
            or os.environ.get("BILLING_SUCCESS_URL", "")
        )
        interval = plan.get("interval", "month")

        if payment_type == "recurring":
            # Razorpay Subscriptions — requires RAZORPAY_PLAN_PRO_* env vars
            try:
                result = rp.create_subscription(
                    interval=interval,
                    user_id=user_id,
                    customer_email=email,
                    customer_name=name,
                    callback_url=success_url,
                )
            except RuntimeError as e:
                raise AppException(str(e), code="PAYMENT_UNAVAILABLE", status_code=503)

            url = result.get("short_url") or ""
            if not url:
                raise AppException(
                    "Failed to create Razorpay subscription URL",
                    code="PAYMENT_ERROR", status_code=502,
                )
            return success_response({
                "url": url,
                "paymentProvider": "razorpay",
                "paymentType": "recurring",
                "currency": "INR",
                "amountPaise": plan["amountPaise"],
                "rzpSubscriptionId": result.get("id"),
            })

        else:
            # One-time Payment Link
            result = rp.create_payment_link(
                amount_paise=plan["amountPaise"],
                description=f"AutoApply {plan['name']} (one-time)",
                customer_email=email,
                customer_name=name,
                notes={"user_id": user_id, "plan_id": plan_id, "payment_type": "one_time"},
                callback_url=success_url,
            )
            url = result.get("short_url") or result.get("id") or ""
            if not url:
                raise AppException(
                    "Failed to create Razorpay payment link",
                    code="PAYMENT_ERROR", status_code=502,
                )
            return success_response({
                "url": url,
                "paymentProvider": "razorpay",
                "paymentType": "one_time",
                "currency": "INR",
                "amountPaise": plan["amountPaise"],
            })

    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("razorpay_checkout failed")
        return internal_error_response(str(e))


def _razorpay_create_order_impl(req: func.HttpRequest) -> func.HttpResponse:
    """Standard Checkout — create Razorpay order (amount in paise)."""
    try:
        user_id = get_user_id(req)
        ip_country = _resolve_country(req)
        if ip_country and not _is_india_country(ip_country):
            raise ValidationError(
                "Razorpay is only available for users in India."
            )

        body = req.get_json() if req.get_body() else {}
        plan_id = (body.get("planId") or "").strip()
        if not plan_id:
            raise ValidationError("planId is required")

        plan = next((p for p in PLANS_INR if p["id"] == plan_id), None)
        if not plan or plan["id"] == "free":
            raise ValidationError(f"Unknown or non-purchasable plan: {plan_id}")

        amount_paise = int(plan.get("amountPaise") or 0)
        if amount_paise < 100:
            raise ValidationError("amount must be at least 100 paise")

        if not rp.is_configured():
            raise AppException(
                "Razorpay is not yet configured.",
                code="PAYMENT_UNAVAILABLE",
                status_code=503,
            )

        receipt = f"hp_{user_id[:8]}_{int(time.time())}"
        try:
            order = rp.create_order(
                amount_paise=amount_paise,
                currency="INR",
                receipt=receipt,
                notes={"user_id": user_id, "plan_id": plan_id},
            )
        except PermissionError:
            return func.HttpResponse(
                json.dumps({"error": {"message": "Razorpay authentication failed"}}),
                status_code=401,
                mimetype="application/json",
            )
        except Exception as exc:
            logger.error("[RZP] create_order API error: %s", exc)
            raise AppException(
                "Failed to create Razorpay order",
                code="PAYMENT_ERROR",
                status_code=500,
            ) from exc

        order_id = order.get("id") or ""
        if not order_id:
            raise AppException(
                "Razorpay order id missing",
                code="PAYMENT_ERROR",
                status_code=502,
            )

        key_id = rp.public_key_id()
        return success_response({
            "order_id": order_id,
            "amount": amount_paise,
            "currency": order.get("currency") or "INR",
            "key_id": key_id,
            "planId": plan_id,
            "testMode": key_id.startswith("rzp_test_"),
        })
    except AppException as e:
        return error_response(e)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        logger.exception("razorpay_create_order failed")
        return internal_error_response(str(e))


def _razorpay_verify_payment_impl(req: func.HttpRequest) -> func.HttpResponse:
    """Standard Checkout — verify payment signature and upgrade to Pro."""
    try:
        user_id = get_user_id(req)
        body = req.get_json() if req.get_body() else {}
        order_id = (body.get("razorpay_order_id") or body.get("order_id") or "").strip()
        payment_id = (body.get("razorpay_payment_id") or body.get("payment_id") or "").strip()
        signature = (body.get("razorpay_signature") or body.get("signature") or "").strip()
        plan_id = (body.get("planId") or "").strip()

        if not order_id or not payment_id or not signature:
            raise ValidationError(
                "razorpay_order_id, razorpay_payment_id, and razorpay_signature are required"
            )

        if not rp.is_configured():
            raise AppException(
                "Razorpay is not yet configured.",
                code="PAYMENT_UNAVAILABLE",
                status_code=503,
            )

        if not rp.verify_checkout_signature(order_id, payment_id, signature):
            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "error": {"message": "Payment signature verification failed"},
                }),
                status_code=400,
                mimetype="application/json",
            )

        if not plan_id:
            try:
                pay = rp.fetch_payment(payment_id)
                notes = pay.get("notes") or {}
                if isinstance(notes, list):
                    notes = {}
                plan_id = (notes.get("plan_id") or "").strip()
                notes_user = (notes.get("user_id") or "").strip()
                if notes_user and notes_user != user_id:
                    raise ValidationError("Payment does not belong to this user")
            except ValidationError:
                raise
            except Exception:
                pass

        if not plan_id:
            raise ValidationError("planId is required for verification")

        plan = next((p for p in PLANS_INR if p["id"] == plan_id), None)
        if not plan or plan["id"] == "free":
            raise ValidationError(f"Unknown plan: {plan_id}")

        _upgrade_rzp_one_time_pro(
            user_id,
            plan_id=plan_id,
            payment_id=payment_id,
            order_id=order_id,
            raw_payload={"source": "standard_checkout", "verified": True},
        )
        logger.info(
            "[RZP] standard checkout verified user=%s payment=%s",
            user_id,
            payment_id,
        )
        return success_response({
            "success": True,
            "tier": "pro",
            "payment_id": payment_id,
            "order_id": order_id,
        })
    except AppException as e:
        return error_response(e)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        logger.exception("razorpay_verify_payment failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/billing/razorpay/create-order", methods=["POST"])
def razorpay_create_order(req: func.HttpRequest) -> func.HttpResponse:
    return _razorpay_create_order_impl(req)


@bp.route(route="api/v1/billing/create-order", methods=["POST"])
def billing_create_order_alias(req: func.HttpRequest) -> func.HttpResponse:
    return _razorpay_create_order_impl(req)


@bp.route(route="api/v1/billing/razorpay/verify-payment", methods=["POST"])
def razorpay_verify_payment(req: func.HttpRequest) -> func.HttpResponse:
    return _razorpay_verify_payment_impl(req)


@bp.route(route="api/v1/billing/verify-payment", methods=["POST"])
def billing_verify_payment_alias(req: func.HttpRequest) -> func.HttpResponse:
    return _razorpay_verify_payment_impl(req)


@bp.route(route="api/v1/billing/subscription", methods=["GET"])
def get_subscription(req: func.HttpRequest) -> func.HttpResponse:
    """Return the signed-in user's current subscription summary."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")
        return success_response(_public_sub(profile))
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("get_subscription failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/billing/cancel", methods=["POST"])
def cancel_subscription(req: func.HttpRequest) -> func.HttpResponse:
    """Cancel the user's active subscription at the end of the current period.

    Provider-aware:
      - Lemon Squeezy  → call LS cancel API (keeps Pro until period ends)
      - Razorpay recurring → call Razorpay cancel; mark locally
      - Razorpay one-time  → non-renewable; just mark locally as cancelled
    """
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")
        sub = profile.get("subscription") or {}
        tier = (sub.get("tier") or profile.get("tier") or "free").lower()
        if tier not in ("pro", "lifetime", "admin"):
            raise ValidationError("No active Pro subscription to cancel")

        provider = sub.get("provider") or (
            "razorpay" if sub.get("rzpPaymentId") else "lemonsqueezy"
        )
        payment_type = sub.get("paymentType") or "recurring"
        now = _now_iso()

        if provider == "lemonsqueezy":
            ls_sub_id = sub.get("lsSubscriptionId")

            # Self-heal: profile may have invoice id instead of real sub id
            # (legacy webhook bug). Look up real sub before calling LS.
            real_sub = _find_real_ls_subscription(user_id)
            if real_sub:
                real_id = real_sub.get("lsSubscriptionId")
                if real_id and real_id != ls_sub_id:
                    logger.info(
                        "[BILLING] Cancel: repairing profile %s lsSubscriptionId %s → %s",
                        user_id, ls_sub_id, real_id,
                    )
                    ls_sub_id = real_id
                    sub["lsSubscriptionId"] = real_id
                    if isinstance(real_sub.get("urls"), dict):
                        sub["urls"] = real_sub["urls"]

            if not ls_sub_id:
                raise ValidationError("No Lemon Squeezy subscription ID found")
            try:
                attrs = ls.cancel_subscription(str(ls_sub_id))
                sub.update({
                    "status": attrs.get("status") or "cancelled",
                    "cancelledAt": attrs.get("cancelled") or now,
                    "endsAt": attrs.get("ends_at") or sub.get("endsAt"),
                })
            except Exception as ls_err:
                logger.warning("LS cancel API failed (%s) — marking locally", ls_err)
                access_until = sub.get("endsAt") or sub.get("renewsAt")
                sub.update({
                    "status": "cancelled",
                    "cancelledAt": now,
                    "endsAt": access_until,
                })

        elif provider == "razorpay" and payment_type == "recurring":
            rzp_sub_id = sub.get("rzpSubscriptionId")
            if rzp_sub_id:
                try:
                    import requests as _req
                    _req.post(
                        f"https://api.razorpay.com/v1/subscriptions/{rzp_sub_id}/cancel",
                        json={"cancel_at_cycle_end": 1},
                        auth=(rp._KEY_ID(), rp._KEY_SECRET()),
                        timeout=10,
                    )
                except Exception as rzp_err:
                    logger.warning("Razorpay cancel API failed (%s) — marking locally", rzp_err)
            access_until = sub.get("endsAt") or sub.get("renewsAt")
            sub.update({
                "status": "cancelled",
                "cancelledAt": now,
                "endsAt": access_until,
            })

        else:
            # Razorpay one-time — no recurring charge to stop; just mark as cancelled
            access_until = sub.get("endsAt") or sub.get("renewsAt")
            sub.update({
                "status": "cancelled",
                "cancelledAt": now,
                "endsAt": access_until,
            })

        profile["subscription"] = sub
        upsert_item("profiles", profile)
        return success_response(_public_sub(profile))
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("cancel_subscription failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/billing/portal", methods=["GET"])
def get_portal(req: func.HttpRequest) -> func.HttpResponse:
    """Return the payment provider's self-service portal URL.

    Lemon Squeezy → customer portal (manage card, invoices)
    Razorpay recurring → Razorpay dashboard deep link
    Razorpay one-time  → no recurring billing, returns 'not_applicable'
    """
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id) or {}
        sub = profile.get("subscription") or {}
        tier = (sub.get("tier") or profile.get("tier") or "free").lower()

        provider = sub.get("provider") or (
            "razorpay" if sub.get("rzpPaymentId") else "lemonsqueezy"
        )
        payment_type = sub.get("paymentType") or "recurring"

        if tier not in ("pro", "lifetime", "admin"):
            raise NotFoundError("No active subscription found")

        if provider == "razorpay" and payment_type == "one_time":
            # One-time payment — no portal (nothing to manage, no auto-renewal)
            return success_response({
                "url": "",
                "notApplicable": True,
                "message": (
                    "Your one-time payment has no auto-renewal. "
                    "No billing management is needed."
                ),
            })

        if provider == "razorpay":
            # Recurring Razorpay subscription — link to Razorpay dashboard
            rzp_sub_id = sub.get("rzpSubscriptionId") or ""
            if rzp_sub_id:
                url = f"https://dashboard.razorpay.com/app/subscriptions/{rzp_sub_id}"
            else:
                url = "https://dashboard.razorpay.com/"
            return success_response({"url": url, "provider": "razorpay"})

        # Lemon Squeezy path — prefer URL saved by webhook (no extra API call)
        urls = sub.get("urls") if isinstance(sub.get("urls"), dict) else {}
        url = (
            urls.get("customer_portal")
            or urls.get("update_payment_method")
            or ""
        ).strip()

        ls_sub_id = sub.get("lsSubscriptionId")

        # Self-heal: if the URL is missing OR only an invoice link, look up the
        # real subscription doc from the `subscriptions` container by userId.
        # This rescues profiles corrupted by the old payment_success webhook
        # bug that stored the INVOICE id as `lsSubscriptionId`.
        if not url:
            real_sub = _find_real_ls_subscription(user_id)
            if real_sub:
                real_urls = real_sub.get("urls") or {}
                url = (
                    real_urls.get("customer_portal")
                    or real_urls.get("update_payment_method")
                    or ""
                ).strip()
                real_id = real_sub.get("lsSubscriptionId")
                # Repair profile in-place so future calls hit the cached URL
                if real_id and real_id != ls_sub_id:
                    logger.info(
                        "[BILLING] Repairing profile %s: lsSubscriptionId %s → %s",
                        user_id, ls_sub_id, real_id,
                    )
                    sub["lsSubscriptionId"] = real_id
                    sub["urls"] = real_urls
                    profile["subscription"] = sub
                    upsert_item("profiles", profile)
                    ls_sub_id = real_id

        if not url and ls_sub_id:
            try:
                url = ls.get_customer_portal_url(str(ls_sub_id))
            except Exception as ls_err:
                logger.warning("LS portal URL failed: %s", ls_err)
                raise AppException(
                    "Could not fetch the Lemon Squeezy portal. "
                    "Please try again in a moment or contact support.",
                    code="PORTAL_UNAVAILABLE",
                    status_code=502,
                )

        if not url:
            raise NotFoundError(
                "No billing portal link found. "
                "If you just subscribed, wait a minute and refresh this page."
            )
        return success_response({"url": url, "provider": "lemonsqueezy"})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("get_portal failed")
        return internal_error_response(str(e))


# ── Webhook ─────────────────────────────────────────────────────────────────

@bp.route(route="api/v1/webhooks/lemonsqueezy", methods=["POST"])
def lemonsqueezy_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Lemon Squeezy → AutoApply webhook.

    Verifies HMAC signature, then mirrors subscription state into:
      - `subscriptions` container (full audit trail)
      - `profile.subscription`   (compact view used by tier checks)
      - `profile.tier`           (legacy mirror, also used by quota helpers)

    Idempotent: re-deliveries simply overwrite the same `sub-ls-<id>` doc.
    """
    try:
        raw = req.get_body() or b""
        sig = (
            req.headers.get("X-Signature")
            or req.headers.get("x-signature")
            or req.headers.get("X-Hub-Signature")
            or ""
        )
        if not ls.verify_webhook_signature(raw, sig):
            logger.warning("LS webhook signature mismatch")
            return func.HttpResponse(
                json.dumps({"error": "invalid signature"}),
                status_code=401,
                mimetype="application/json",
            )

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return func.HttpResponse(
                json.dumps({"error": "invalid json"}),
                status_code=400,
                mimetype="application/json",
            )

        meta = payload.get("meta") or {}
        event = meta.get("event_name") or ""
        custom = (meta.get("custom_data") or {}) if isinstance(meta.get("custom_data"), dict) else {}
        data = (payload.get("data") or {})
        attrs = (data.get("attributes") or {})
        ls_obj_id = data.get("id") or ""

        # The user_id we passed at checkout. Without it we cannot route to a
        # profile — log and accept (200) so LS doesn't keep retrying.
        user_id = (
            custom.get("user_id")
            or (attrs.get("first_subscription_item") or {}).get("user_id")
            or ""
        )
        if not user_id:
            logger.info("LS webhook %s w/o user_id, ignoring (id=%s)", event, ls_obj_id)
            return func.HttpResponse("ok", status_code=200)

        logger.info("LS webhook %s for user=%s id=%s", event, user_id, ls_obj_id)

        # Lazy-create container on first webhook so we don't need to update
        # the Bicep before the first sale rolls in.
        _ensure_subscriptions_container()

        _handle_event(event, user_id, attrs, ls_obj_id, payload)

        return func.HttpResponse("ok", status_code=200)
    except Exception as e:
        # Always 200 on internal failures — LS will keep retrying for hours
        # otherwise. Log loudly and move on; the next event for the same
        # subscription will repair state.
        logger.exception("LS webhook handling failed: %s", e)
        return func.HttpResponse("ok", status_code=200)


def _real_subscription_id(event: str, attrs: dict, ls_obj_id: str) -> str:
    """Return the actual LS subscription ID for any event type.

    For `subscription_*` events the data.id IS the subscription ID.
    For `subscription_payment_*` (invoice) events, data.id is the INVOICE id;
    the real subscription id lives in attrs.subscription_id. Using data.id
    here corrupts profile.subscription.lsSubscriptionId and breaks portal/cancel.
    """
    if event.startswith("subscription_payment"):
        sub_id = attrs.get("subscription_id") or attrs.get("subscriptionId")
        if sub_id:
            return str(sub_id)
    return str(ls_obj_id)


def _handle_event(event: str, user_id: str, attrs: dict, ls_obj_id: str, raw_payload: dict) -> None:
    sub_id = _real_subscription_id(event, attrs, ls_obj_id)

    # Subscription events
    if event in (
        "subscription_created",
        "subscription_updated",
        "subscription_resumed",
        "subscription_unpaused",
    ):
        _upsert_subscription(user_id, attrs, sub_id, raw_payload, set_active=True)
        return

    if event == "subscription_payment_success":
        # For payment events, attrs is the INVOICE — it lacks `urls`,
        # `renews_at`, etc. We must fetch the subscription separately
        # so the profile retains the real subscription metadata.
        sub_attrs = attrs
        try:
            sub_attrs = ls.get_subscription(sub_id) or attrs
        except Exception as fetch_err:
            logger.warning(
                "LS get_subscription(%s) failed after payment_success: %s",
                sub_id, fetch_err,
            )
        _upsert_subscription(user_id, sub_attrs, sub_id, raw_payload, set_active=True)

        variant = sub_attrs.get("variant_name") or attrs.get("variant_name") or "Pro"
        total = attrs.get("total_formatted") or attrs.get("subtotal_formatted") or ""
        if not total and attrs.get("subtotal") is not None:
            total = f"${round(int(attrs['subtotal']) / 100, 2)}"
        vlow = (variant or "").lower()
        if "year" in vlow:
            interval = "year"
        elif "week" in vlow:
            interval = "week"
        else:
            interval = "month"
        _notify_payment_receipt(
            user_id,
            plan_name=variant,
            amount_display=total or "See Lemon Squeezy receipt",
            payment_id=str(ls_obj_id),
            provider="Lemon Squeezy",
            interval=interval,
        )
        return

    if event in ("subscription_payment_failed",):
        _upsert_subscription(user_id, attrs, sub_id, raw_payload, set_active=True, force_status="past_due")
        return

    if event in ("subscription_cancelled",):
        # User keeps Pro until ends_at. Mirror status, don't downgrade yet.
        _upsert_subscription(user_id, attrs, sub_id, raw_payload, set_active=True, force_status="cancelled")
        return

    if event in ("subscription_expired", "subscription_paused"):
        _upsert_subscription(user_id, attrs, sub_id, raw_payload, set_active=False)
        return

    # One-time purchases (e.g. resume review at $19) — log only for now.
    if event == "order_created":
        logger.info("LS order_created for user=%s id=%s amount=%s",
                    user_id, ls_obj_id, attrs.get("total_formatted"))
        return

    logger.info("LS event %s ignored", event)


def _upsert_subscription(
    user_id: str,
    attrs: dict,
    ls_subscription_id: str,
    raw_payload: dict,
    *,
    set_active: bool,
    force_status: str | None = None,
) -> None:
    """Write the subscription doc and mirror into profile."""
    status = (force_status or attrs.get("status") or "").lower()
    interval = (attrs.get("billing_anchor") and "month") or None
    # Prefer first_subscription_item for interval/price detail
    item = attrs.get("first_subscription_item") or {}
    variant_name = (attrs.get("variant_name") or "").lower()
    if "year" in variant_name:
        interval = "year"
    elif "week" in variant_name:
        interval = "week"
    elif "month" in variant_name:
        interval = "month"

    price_usd = None
    try:
        cents = attrs.get("subtotal") or item.get("subtotal")
        if cents is not None:
            price_usd = round(int(cents) / 100, 2)
    except Exception:
        pass

    now = _now_iso()
    doc = {
        "id": _sub_doc_id(ls_subscription_id),
        "userId": user_id,
        "lsSubscriptionId": str(ls_subscription_id),
        "lsCustomerId": str(attrs.get("customer_id") or ""),
        "lsOrderId": str(attrs.get("order_id") or ""),
        "lsProductId": str(attrs.get("product_id") or ""),
        "lsVariantId": str(attrs.get("variant_id") or ""),
        "productName": attrs.get("product_name"),
        "variantName": attrs.get("variant_name"),
        "interval": interval,
        "priceUsd": price_usd,
        "status": status or "active",
        "renewsAt": attrs.get("renews_at"),
        "endsAt": attrs.get("ends_at"),
        "trialEndsAt": attrs.get("trial_ends_at"),
        "createdAt": attrs.get("created_at") or now,
        "updatedAt": now,
        "urls": attrs.get("urls") or {},
        "raw": raw_payload,
    }
    upsert_item("subscriptions", doc)

    # Mirror into profile
    profile = read_item("profiles", user_id, user_id)
    if not profile:
        logger.warning("LS webhook: no profile for user_id=%s", user_id)
        return
    sub_summary = {
        "tier": "pro" if set_active else "free",
        "status": doc["status"],
        "interval": interval,
        "priceUsd": price_usd,
        "renewsAt": doc["renewsAt"],
        "endsAt": doc["endsAt"],
        "provider": "lemonsqueezy",
        "paymentType": "recurring",
        "lsSubscriptionId": doc["lsSubscriptionId"],
        "lsCustomerId": doc["lsCustomerId"],
        "urls": doc["urls"],
        "updatedAt": now,
    }
    if not set_active:
        sub_summary["downgradedAt"] = now
    profile["subscription"] = sub_summary
    profile["tier"] = sub_summary["tier"]
    upsert_item("profiles", profile)


# ── Razorpay Webhook ─────────────────────────────────────────────────────────

@bp.route(route="api/v1/webhooks/razorpay", methods=["POST"])
def razorpay_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Razorpay → AutoApply webhook.

    Verifies HMAC-SHA256 signature, then mirrors payment state into the
    user's profile.tier so tier checks work immediately after payment.

    Idempotent — re-deliveries overwrite the same `sub-rzp-<payment_id>` doc.
    """
    try:
        raw = req.get_body() or b""
        sig = (
            req.headers.get("X-Razorpay-Signature")
            or req.headers.get("x-razorpay-signature")
            or ""
        )
        if not rp.verify_webhook_signature(raw, sig):
            logger.warning("[RZP] webhook signature mismatch")
            return func.HttpResponse(
                json.dumps({"error": "invalid signature"}),
                status_code=401,
                mimetype="application/json",
            )

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return func.HttpResponse(
                json.dumps({"error": "invalid json"}),
                status_code=400,
                mimetype="application/json",
            )

        event = payload.get("event") or ""
        pl = payload.get("payload") or {}
        pay_entity = (pl.get("payment") or {}).get("entity") or {}
        link_entity = (pl.get("payment_link") or {}).get("entity") or {}

        # Notes are on the payment link at creation; payment entity may mirror them.
        notes = pay_entity.get("notes") or link_entity.get("notes") or {}
        if isinstance(notes, list):
            notes = {}
        user_id = (notes.get("user_id") or "").strip()
        plan_id = (notes.get("plan_id") or "").strip()

        payment_id = pay_entity.get("id") or ""
        if not payment_id and link_entity.get("payments"):
            payments = link_entity.get("payments") or []
            if payments:
                payment_id = payments[-1] if isinstance(payments[-1], str) else (
                    (payments[-1] or {}).get("payment_id") or (payments[-1] or {}).get("id") or ""
                )

        logger.info("[RZP] webhook %s user=%s payment=%s plan=%s",
                    event, user_id, payment_id, plan_id)

        if not user_id:
            logger.info("[RZP] webhook %s without user_id, ignoring", event)
            return func.HttpResponse("ok", status_code=200)

        _ensure_subscriptions_container()

        if event in ("payment.captured", "payment_link.paid"):
            # One-time payment (Payment Link or Standard Checkout)
            _upgrade_rzp_one_time_pro(
                user_id,
                plan_id=plan_id or "pro_monthly",
                payment_id=payment_id,
                order_id=pay_entity.get("order_id") or "",
                raw_payload=payload,
            )
            logger.info("[RZP] (one-time) upgraded user %s to pro (plan=%s)", user_id, plan_id)

        elif event == "subscription.charged":
            # Recurring subscription charged (auto-renewal or first charge)
            sub_entity = (pl.get("subscription") or {}).get("entity") or {}
            rzp_sub_id = sub_entity.get("id") or ""
            sub_notes = sub_entity.get("notes") or {}
            if isinstance(sub_notes, list):
                sub_notes = {}
            sub_user_id = (sub_notes.get("user_id") or user_id).strip()
            interval = (sub_notes.get("interval") or "month").strip()
            plan = next(
                (p for p in PLANS_INR
                 if p.get("interval") == interval and p.get("id") != "free"),
                {},
            )
            price_inr = plan.get("priceInr")
            plan_id_resolved = plan.get("id") or ("pro_yearly" if interval == "year" else "pro_monthly")
            plan_name = plan.get("name") or "Pro"
            renews_at = _renews_at_iso(interval)
            now = _now_iso()
            doc = {
                "id": f"sub-rzp-{rzp_sub_id or payment_id}",
                "userId": sub_user_id,
                "provider": "razorpay",
                "paymentType": "recurring",
                "rzpSubscriptionId": rzp_sub_id,
                "rzpPaymentId": payment_id,
                "planId": plan_id_resolved,
                "interval": interval,
                "priceInr": price_inr,
                "currency": "INR",
                "status": "active",
                "renewsAt": renews_at,
                "createdAt": now,
                "updatedAt": now,
                "raw": payload,
            }
            upsert_item("subscriptions", doc)

            profile = read_item("profiles", sub_user_id, sub_user_id)
            if profile:
                profile["subscription"] = {
                    "tier": "pro",
                    "status": "active",
                    "interval": interval,
                    "paymentType": "recurring",
                    "priceInr": price_inr,
                    "provider": "razorpay",
                    "rzpSubscriptionId": rzp_sub_id,
                    "rzpPaymentId": payment_id,
                    "renewsAt": renews_at,
                    "updatedAt": now,
                }
                profile["tier"] = "pro"
                upsert_item("profiles", profile)
                logger.info("[RZP] (recurring) charged user %s sub=%s", sub_user_id, rzp_sub_id)

            amount_display = f"₹{price_inr}" if price_inr else "₹—"
            _notify_payment_receipt(
                sub_user_id,
                plan_name=plan_name,
                amount_display=amount_display,
                payment_id=payment_id,
                provider="Razorpay",
                interval=interval,
            )

        elif event in ("subscription.cancelled", "subscription.completed", "subscription.expired"):
            sub_entity = (pl.get("subscription") or {}).get("entity") or {}
            rzp_sub_id = sub_entity.get("id") or ""
            sub_notes = sub_entity.get("notes") or {}
            if isinstance(sub_notes, list):
                sub_notes = {}
            sub_user_id = (sub_notes.get("user_id") or user_id).strip()
            if sub_user_id:
                now = _now_iso()
                profile = read_item("profiles", sub_user_id, sub_user_id)
                if profile and (profile.get("subscription") or {}).get("rzpSubscriptionId") == rzp_sub_id:
                    psub = profile["subscription"]
                    psub["status"] = "cancelled"
                    psub["cancelledAt"] = now
                    psub["updatedAt"] = now
                    if not psub.get("endsAt"):
                        psub["endsAt"] = (
                            psub.get("renewsAt")
                            or sub_entity.get("current_end")
                            or sub_entity.get("end_at")
                        )
                    if event in ("subscription.completed", "subscription.expired"):
                        profile["subscription"]["tier"] = "free"
                        profile["tier"] = "free"
                    upsert_item("profiles", profile)
                    logger.info("[RZP] sub %s event=%s user=%s", rzp_sub_id, event, sub_user_id)

        elif event == "payment.failed":
            logger.info("[RZP] payment failed for user=%s payment=%s", user_id, payment_id)

        return func.HttpResponse("ok", status_code=200)
    except Exception as e:
        logger.exception("[RZP] webhook handling failed: %s", e)
        return func.HttpResponse("ok", status_code=200)


@bp.route(route="api/v1/billing/razorpay/webhook", methods=["POST"])
def razorpay_webhook_billing_alias(req: func.HttpRequest) -> func.HttpResponse:
    """Alias for dashboard URLs under /billing/razorpay/webhook."""
    return razorpay_webhook(req)
