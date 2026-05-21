"""Lemon Squeezy REST API client.

Thin wrapper around the v1 JSON:API. Only implements the methods we need:
  - create_checkout()
  - get_subscription()
  - cancel_subscription()
  - get_customer_portal_url()
  - verify_webhook_signature()

Docs: https://docs.lemonsqueezy.com/api
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

LS_API_BASE = "https://api.lemonsqueezy.com/v1"


# ── Config ──────────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("LEMONSQUEEZY_API_KEY", "")
    if not key:
        raise RuntimeError("LEMONSQUEEZY_API_KEY is not configured")
    return key


def _store_id() -> str:
    sid = os.environ.get("LEMONSQUEEZY_STORE_ID", "")
    if not sid:
        raise RuntimeError("LEMONSQUEEZY_STORE_ID is not configured")
    return sid


def _webhook_secret() -> str:
    secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("LEMONSQUEEZY_WEBHOOK_SECRET is not configured")
    return secret


# Variant id lookup. Returns the LS variant id for ("pro","monthly")
# or ("pro","yearly"). Falls back to "" if the env var is missing.
def variant_id(tier: str, interval: str) -> str:
    tier = (tier or "").lower()
    interval = (interval or "").lower()
    key = f"LEMONSQUEEZY_VARIANT_{tier.upper()}_{interval.upper()}"
    return os.environ.get(key, "").strip()


# ── HTTP helpers ────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated JSON:API request. Returns parsed JSON or {}."""
    url = f"{LS_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_api_key()}")
    req.add_header("Accept", "application/vnd.api+json")
    req.add_header("Content-Type", "application/vnd.api+json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        logger.warning("LS HTTP %s on %s %s — %s", e.code, method, path, err_body[:500])
        raise
    except Exception as e:
        logger.exception("LS request failed: %s %s", method, path)
        raise


# ── Public methods ──────────────────────────────────────────────────────────

def create_checkout(
    *,
    variant_id_str: str,
    user_id: str,
    user_email: str,
    user_name: str = "",
    success_redirect_url: str = "",
) -> dict:
    """Create a hosted checkout link for the given variant.

    The returned dict contains a top-level "url" the client should open in
    a new tab. We pass `user_id` and `user_email` as custom data so the
    webhook handler can recognise the buyer when the order_created event
    arrives.
    """
    body: dict[str, Any] = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": user_email,
                    "name": user_name or user_email.split("@")[0],
                    "custom": {
                        "user_id": user_id,
                    },
                },
                # Convenience flags for a SaaS subscription:
                "checkout_options": {
                    "embed": False,
                    "media": False,
                    "logo": True,
                    "desc": True,
                    "discount": True,
                    "dark": False,
                    "subscription_preview": True,
                    "button_color": "#7C3AED",
                },
            },
            "relationships": {
                "store": {"data": {"type": "stores", "id": _store_id()}},
                "variant": {"data": {"type": "variants", "id": str(variant_id_str)}},
            },
        }
    }
    if success_redirect_url:
        body["data"]["attributes"]["product_options"] = {
            "redirect_url": success_redirect_url,
        }
    res = _request("POST", "/checkouts", body)
    attrs = (res.get("data") or {}).get("attributes") or {}
    return {
        "url": attrs.get("url", ""),
        "expires_at": attrs.get("expires_at"),
        "raw": res,
    }


def get_subscription(subscription_id: str) -> dict:
    res = _request("GET", f"/subscriptions/{subscription_id}")
    return (res.get("data") or {}).get("attributes") or {}


def cancel_subscription(subscription_id: str) -> dict:
    """Cancel at end-of-period (LS default — user keeps access until renew date)."""
    res = _request("DELETE", f"/subscriptions/{subscription_id}")
    return (res.get("data") or {}).get("attributes") or {}


def get_customer_portal_url(subscription_id: str) -> str:
    """Return the LS-hosted "manage subscription" URL for the given sub."""
    res = _request("GET", f"/subscriptions/{subscription_id}")
    attrs = (res.get("data") or {}).get("attributes") or {}
    urls = attrs.get("urls") or {}
    return urls.get("customer_portal") or urls.get("update_payment_method") or ""


# ── Webhook signature verification ──────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, header_signature: str) -> bool:
    """HMAC-SHA256 comparison. Returns True iff the signatures match.

    Lemon Squeezy sends the hex digest of HMAC-SHA256(body, secret) in
    the X-Signature header. Constant-time comparison avoids timing
    side-channels.
    """
    if not header_signature:
        return False
    secret = _webhook_secret().encode("utf-8")
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_signature.strip())
