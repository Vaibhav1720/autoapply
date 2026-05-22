"""Thin Razorpay API wrapper for AutoApply billing (India).

Uses Razorpay Payment Links API — the hosted checkout URL approach that
mirrors the Lemon Squeezy flow: backend creates a link and returns the URL,
Flutter opens it in a new tab. No JS SDK integration required.

Environment variables:
  RAZORPAY_KEY_ID      — Razorpay API key id  (test: rzp_test_...)
  RAZORPAY_KEY_SECRET  — Razorpay API key secret
  RAZORPAY_WEBHOOK_SECRET — Razorpay webhook signing secret
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

import requests

logger = logging.getLogger(__name__)

_KEY_ID = lambda: os.environ.get("RAZORPAY_KEY_ID", "").strip()
_KEY_SECRET = lambda: os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
_WEBHOOK_SECRET = lambda: os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()

_BASE = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    """True when both key_id and key_secret are present."""
    return bool(_KEY_ID() and _KEY_SECRET())


def public_key_id() -> str:
    """Razorpay Key ID for Standard Checkout (safe to expose to the browser)."""
    return _KEY_ID()


def create_payment_link(
    *,
    amount_paise: int,
    description: str,
    customer_email: str,
    customer_name: str,
    notes: dict | None = None,
    callback_url: str = "",
    expire_by: int | None = None,
) -> dict:
    """Create a Razorpay Payment Link and return its `short_url`.

    amount_paise: amount in paise (₹199 = 19900)
    Returns the full Razorpay API response dict; caller reads `short_url`.
    """
    payload: dict = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "description": description,
        "customer": {
            "email": customer_email,
            "name": customer_name,
        },
        "notify": {"sms": False, "email": True},
        "reminder_enable": False,
        "notes": notes or {},
    }
    if callback_url:
        payload["callback_url"] = callback_url
        payload["callback_method"] = "get"
    if expire_by:
        payload["expire_by"] = expire_by

    resp = requests.post(
        f"{_BASE}/payment_links",
        json=payload,
        auth=(_KEY_ID(), _KEY_SECRET()),
        timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("[RAZORPAY] create_payment_link failed %s: %s", resp.status_code, resp.text[:400])
        raise
    return resp.json()


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify Razorpay webhook HMAC-SHA256 signature.

    Fail-closed: returns False if the secret is not configured or the
    signature header is missing. Never accept unverified webhook payloads.
    """
    secret = _WEBHOOK_SECRET()
    if not secret:
        logger.error(
            "[RAZORPAY] RAZORPAY_WEBHOOK_SECRET is not set — "
            "rejecting all webhook calls. Set the secret in Azure App Settings."
        )
        return False  # fail-closed — never accept unverified payloads
    if not signature:
        logger.warning("[RAZORPAY] webhook missing X-Razorpay-Signature header")
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def create_order(
    *,
    amount_paise: int,
    currency: str = "INR",
    receipt: str,
    notes: dict | None = None,
) -> dict:
    """Create a Razorpay Order for Standard Checkout (POST /v1/orders)."""
    if amount_paise < 100:
        raise ValueError("amount must be at least 100 paise")
    payload: dict = {
        "amount": int(amount_paise),
        "currency": currency,
        "receipt": receipt[:40],
        "notes": notes or {},
    }
    resp = requests.post(
        f"{_BASE}/orders",
        json=payload,
        auth=(_KEY_ID(), _KEY_SECRET()),
        timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        status = resp.status_code
        logger.error(
            "[RAZORPAY] create_order failed %s: %s", status, resp.text[:400]
        )
        if status in (401, 403):
            raise PermissionError("Razorpay authentication failed") from None
        raise
    return resp.json()


def verify_checkout_signature(
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """Verify Standard Checkout signature: HMAC-SHA256(order_id|payment_id)."""
    secret = _KEY_SECRET()
    if not secret or not order_id or not payment_id or not signature:
        return False
    message = f"{order_id}|{payment_id}"
    expected = hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def fetch_payment(payment_id: str) -> dict:
    """Fetch a Razorpay payment object for post-payment verification."""
    resp = requests.get(
        f"{_BASE}/payments/{payment_id}",
        auth=(_KEY_ID(), _KEY_SECRET()),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _rzp_plan_id(interval: str) -> str:
    """Return the Razorpay plan_id for the given interval from env vars.

    These plan IDs are created once in the Razorpay Dashboard:
      Settings → Subscriptions → Plans → + Create Plan
    Then stored as:
      RAZORPAY_PLAN_PRO_MONTHLY  e.g. plan_xxxxxxxxxxxxxxx
      RAZORPAY_PLAN_PRO_YEARLY   e.g. plan_yyyyyyyyyyyyyyy
    """
    key = "RAZORPAY_PLAN_PRO_YEARLY" if interval == "year" else "RAZORPAY_PLAN_PRO_MONTHLY"
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(
            f"{key} is not configured. Create a plan in the Razorpay Dashboard "
            "and set the env var to the plan_id (e.g. plan_xxxxxxxx)."
        )
    return val


def create_subscription(
    *,
    interval: str,
    user_id: str,
    customer_email: str,
    customer_name: str,
    total_count: int = 120,
    notify: bool = True,
    callback_url: str = "",
) -> dict:
    """Create a Razorpay Subscription and return the response dict.

    The subscription checkout URL is in response['short_url'].

    interval    : "month" or "year"
    total_count : max billing cycles (120 months / 10 years is effectively unlimited)
    notify      : whether to notify customer by email/SMS

    Razorpay docs: https://razorpay.com/docs/api/payments/subscriptions/create/
    """
    plan_id = _rzp_plan_id(interval)
    payload: dict = {
        "plan_id": plan_id,
        "total_count": total_count,
        "quantity": 1,
        "customer_notify": 1 if notify else 0,
        "notes": {
            "user_id": user_id,
            "interval": interval,
        },
        "notify_info": {
            "notify_email": customer_email,
        },
    }
    if callback_url:
        payload["callback_url"] = callback_url
        payload["callback_method"] = "get"

    resp = requests.post(
        f"{_BASE}/subscriptions",
        json=payload,
        auth=(_KEY_ID(), _KEY_SECRET()),
        timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        logger.error(
            "[RAZORPAY] create_subscription failed %s: %s",
            resp.status_code, resp.text[:400],
        )
        raise
    return resp.json()


def fetch_subscription(subscription_id: str) -> dict:
    """Fetch a Razorpay Subscription object."""
    resp = requests.get(
        f"{_BASE}/subscriptions/{subscription_id}",
        auth=(_KEY_ID(), _KEY_SECRET()),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
