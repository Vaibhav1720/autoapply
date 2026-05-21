# Payments Integration Guide — Lemon Squeezy (USD) + Razorpay (INR)

> Audience: any new agent / engineer picking up payment work on this repo with **zero prior context**. Read top-to-bottom; everything needed is here.
>
> Scope:
> - **Lemon Squeezy** — already wired end-to-end. This doc explains the existing code, configuration, and how to enable it.
> - **Razorpay** — NOT yet implemented. This doc gives a complete, file-by-file blueprint a new agent can execute without further design input.
>
> Out of scope: tax/GST handling, invoicing, PCI scope (both providers are PCI-DSS Level 1 — we never touch card data).

---

## 0. Decision matrix — which provider does the user see?

| User location | Default provider | Why |
|---|---|---|
| India (currency = INR, country = IN) | **Razorpay** | Native UPI, RuPay, NetBanking; lower fees in INR; no FX cost for the buyer |
| Everyone else | **Lemon Squeezy** | Merchant-of-Record handles VAT/sales tax in 100+ jurisdictions; card + PayPal |

The frontend should detect the user's country (IP geolocation, browser locale, or an explicit selector on the pricing page) and call the matching `/api/v1/billing/checkout/{provider}` endpoint. Both providers update the **same** `profile.subscription` and `subscriptions` Cosmos container — downstream tier checks don't care which processor was used.

---

## 1. Architecture overview

```
                ┌──────────────┐
                │  Pricing UI  │  (Flutter web — pricing_screen.dart)
                └──────┬───────┘
                       │ POST /api/v1/billing/checkout
                       ▼
                ┌──────────────┐         ┌──────────────────┐
                │  Function    │────────▶│ Lemon Squeezy    │  (USD users)
                │  App         │         │ hosted checkout  │
                │  (Python)    │────────▶│ Razorpay Checkout│  (INR users)
                └──────┬───────┘         └────────┬─────────┘
                       │                          │
                       │ POST  /api/v1/webhooks/  │
                       │  ←───── lemonsqueezy ────┘
                       │  ←───── razorpay ────────┘
                       ▼
                ┌──────────────┐
                │  Cosmos DB   │   profiles.subscription
                │              │   subscriptions container
                └──────────────┘
```

**State convention** (single source of truth used by tier-gated endpoints):

```jsonc
// profile document, "subscription" sub-object
{
  "tier": "pro",                     // "free" | "pro"
  "provider": "lemonsqueezy",        // "lemonsqueezy" | "razorpay"
  "status": "active",                // active | past_due | cancelled | expired
  "interval": "month",               // month | year
  "priceMinorUnits": 999,            // 999 = $9.99 or ₹9.99 — unit comes from currency
  "currency": "USD",                 // ISO 4217
  "renewsAt": "2026-06-15T00:00:00Z",
  "endsAt": null,                    // set when status=cancelled
  "providerSubscriptionId": "abc123",// LS subscription_id OR Razorpay subscription_id
  "providerCustomerId": "cust_xxx"   // optional, for portal links
}
```

The `subscriptions` Cosmos container holds an **append-friendly audit doc** per subscription, partition key `userId`, doc id `sub-{provider}-{providerSubscriptionId}`. Webhook handlers are **idempotent** — re-deliveries overwrite the same doc.

---

## 2. Lemon Squeezy — the existing implementation

### 2.1 Code map

| File | Purpose |
|---|---|
| [api/services/billing/lemonsqueezy_client.py](../api/services/billing/lemonsqueezy_client.py) | Thin REST wrapper. Functions: `create_checkout()`, `get_subscription()`, `cancel_subscription()`, `get_customer_portal_url()`, `verify_webhook_signature()`. |
| [api/services/billing/routes.py](../api/services/billing/routes.py) | HTTP routes: plans catalogue, checkout, subscription view, cancel, portal, webhook receiver. |
| [api/services/billing/__init__.py](../api/services/billing/__init__.py) | Blueprint export — registered in `function_app.py`. |

### 2.2 HTTP routes (already live)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/billing/plans` | none | Public catalogue (free / pro_monthly / pro_yearly) |
| POST | `/api/v1/billing/checkout` | JWT | Returns hosted-checkout URL for `{ "planId": "pro_monthly" }` |
| GET | `/api/v1/billing/subscription` | JWT | Current user's compact subscription view |
| POST | `/api/v1/billing/cancel` | JWT | Cancel at end of period |
| GET | `/api/v1/billing/portal` | JWT | LS-hosted "manage subscription" URL |
| POST | `/api/v1/webhooks/lemonsqueezy` | **HMAC** | Mirror events into Cosmos |

### 2.3 Required environment variables

Set on the Function App (`az functionapp config appsettings set …`) **and** in `api/local.settings.json` for local testing:

| Variable | Where to find it | Example shape |
|---|---|---|
| `LEMONSQUEEZY_API_KEY` | LS dashboard → Settings → API → "Create API key" | `eyJ0eXAiOi…` (long JWT-style string) |
| `LEMONSQUEEZY_STORE_ID` | Settings → General → "Store ID" | `12345` (numeric) |
| `LEMONSQUEEZY_WEBHOOK_SECRET` | Generated on the webhook page (step 2.4 below) | random 40-char string |
| `LEMONSQUEEZY_VARIANT_PRO_MONTHLY` | The variant ID of your monthly product | `67890` |
| `LEMONSQUEEZY_VARIANT_PRO_YEARLY` | The variant ID of your yearly product | `67891` |
| `BILLING_SUCCESS_URL` *(optional)* | Where to redirect after checkout | `https://<your-static-web-app>.azurestaticapps.net/billing/success` |

### 2.4 Lemon Squeezy dashboard setup (one-time, ~15 min)

1. Sign up at <https://app.lemonsqueezy.com/register>, verify email, complete the merchant onboarding (tax info, payout bank account).
2. **Create a Store** → Settings → General. Note the numeric **Store ID**.
3. **Create a Product**:
   - Type: **Subscription**
   - Add **two variants**:
     - "Pro Monthly" — $9.99 / month
     - "Pro Yearly" — $89.99 / year
   - On each variant page, copy the numeric **Variant ID** from the URL (`/variants/<id>/edit`).
4. **Create an API key**: Settings → API → "Create API key". Copy once — it cannot be re-shown.
5. **Create a Webhook**:
   - Settings → Webhooks → "+ Add"
   - URL: `https://<your-function-app>.azurewebsites.net/api/v1/webhooks/lemonsqueezy`
   - Signing secret: click **Generate**, copy
   - Subscribe to events:
     - `subscription_created`
     - `subscription_updated`
     - `subscription_cancelled`
     - `subscription_resumed`
     - `subscription_expired`
     - `subscription_paused`
     - `subscription_unpaused`
     - `subscription_payment_success`
     - `subscription_payment_failed`
     - `order_created` *(for one-time products like the $19 resume review)*

### 2.5 Webhook signature verification (already implemented)

Lemon Squeezy signs the **raw request body** with HMAC-SHA256 using the webhook secret and sends the hex digest in the `X-Signature` header. The client does:

```python
expected = hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()
return hmac.compare_digest(expected, header_signature.strip())
```

`hmac.compare_digest` is constant-time. Always verify **before** parsing JSON.

### 2.6 Tier sync model (already implemented)

`_handle_event` in [routes.py](../api/services/billing/routes.py) maps LS events → state mutations:

| LS event | `subscription.status` | `tier` | Notes |
|---|---|---|---|
| `subscription_created`, `_updated`, `_resumed`, `_unpaused`, `_payment_success` | `active` | `pro` | Sets `renewsAt` from `attrs.renews_at` |
| `subscription_payment_failed` | `past_due` | `pro` | Keeps Pro access for 1 grace period |
| `subscription_cancelled` | `cancelled` | `pro` | User keeps Pro until `endsAt` |
| `subscription_expired`, `_paused` | `expired` | `free` | Downgrade |

The custom `user_id` we pass into the LS checkout (`checkout_data.custom.user_id`) is echoed back in `meta.custom_data.user_id` on every webhook — that's how we route the event to a profile.

### 2.7 Test mode

Lemon Squeezy has a **Test Mode** toggle (top-right of dashboard). Test-mode purchases use Stripe's test card numbers (e.g., `4242 4242 4242 4242`, any CVC, any future expiry). Test webhooks have `meta.test_mode = true`. The client code does **not** branch on this — production and test events use the same code path, which is what you want.

---

## 3. Razorpay — implementation blueprint

> Razorpay is **not yet implemented**. Everything below is the spec for an agent to build it. Implementation effort: ~1 focused day including tests.

### 3.1 What Razorpay gives us

- **Subscriptions API** — recurring billing on saved cards / UPI mandates
- **Checkout.js** — drop-in payment modal (no PCI scope for us)
- **Webhooks** — HMAC-SHA256 signed (header `X-Razorpay-Signature`)
- **Settlement** — INR payout to a verified Indian bank account in T+2 days
- **Test mode** — `rzp_test_*` keys, sandbox dashboard, no real money moves

Docs: <https://razorpay.com/docs/api/payments/subscriptions/>, <https://razorpay.com/docs/webhooks/>.

### 3.2 Pricing parity

| Plan | USD (LS) | INR (Razorpay) | Razorpay plan_id env var |
|---|---|---|---|
| Pro Monthly | $9.99 | ₹799 | `RAZORPAY_PLAN_PRO_MONTHLY` |
| Pro Yearly | $89.99 | ₹6 999 | `RAZORPAY_PLAN_PRO_YEARLY` |

(Adjust the INR values to your final pricing — they are illustrative.)

### 3.3 Files to create

```
api/services/billing/
├── razorpay_client.py          ← NEW (mirror of lemonsqueezy_client.py)
└── routes.py                   ← MODIFY (add Razorpay routes + webhook)
```

No new Bicep resources needed — reuses the existing Function App, Cosmos DB, and Key Vault.

### 3.4 Required environment variables

| Variable | Where to find it | Example |
|---|---|---|
| `RAZORPAY_KEY_ID` | Razorpay dashboard → Settings → API Keys → "Generate Key" | `rzp_live_xxx` (or `rzp_test_xxx`) |
| `RAZORPAY_KEY_SECRET` | Shown once at key creation — store in Key Vault | 32-char string |
| `RAZORPAY_WEBHOOK_SECRET` | Settings → Webhooks → "+ Add" → set your own secret | any random 32+ char string you choose |
| `RAZORPAY_PLAN_PRO_MONTHLY` | Plan ID created in step 3.7.3 | `plan_xxxxxxxxxxxxxx` |
| `RAZORPAY_PLAN_PRO_YEARLY` | Plan ID created in step 3.7.3 | `plan_xxxxxxxxxxxxxx` |
| `RAZORPAY_CURRENCY` | Always `INR` | `INR` |

### 3.5 `razorpay_client.py` — full implementation

> Razorpay's Python SDK exists (`pip install razorpay`) but `urllib` + `hmac` keeps the function cold-start fast and avoids one more dependency. Use the SDK if you prefer — both work.

```python
"""Razorpay REST API client (no SDK — urllib + hmac)."""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, urllib.request, urllib.error
from typing import Any

logger = logging.getLogger(__name__)
RZP_API_BASE = "https://api.razorpay.com/v1"


def _key_id() -> str:
    v = os.environ.get("RAZORPAY_KEY_ID", "")
    if not v:
        raise RuntimeError("RAZORPAY_KEY_ID is not configured")
    return v


def _key_secret() -> str:
    v = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not v:
        raise RuntimeError("RAZORPAY_KEY_SECRET is not configured")
    return v


def _webhook_secret() -> str:
    v = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not v:
        raise RuntimeError("RAZORPAY_WEBHOOK_SECRET is not configured")
    return v


def plan_id(tier: str, interval: str) -> str:
    key = f"RAZORPAY_PLAN_{tier.upper()}_{interval.upper()}"
    return os.environ.get(key, "").strip()


def _basic_auth_header() -> str:
    raw = f"{_key_id()}:{_key_secret()}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{RZP_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", _basic_auth_header())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = ""
        logger.warning("RZP HTTP %s on %s %s — %s", e.code, method, path, err[:500])
        raise


# ── Public API ─────────────────────────────────────────────────────────────

def create_subscription(
    *,
    plan_id_str: str,
    user_id: str,
    user_email: str,
    user_name: str = "",
    total_count: int = 12,         # 12 months for monthly plan, 5 years for yearly
    notify_email: bool = True,
) -> dict:
    """Create a subscription. Returns dict with `id`, `short_url`, `status`."""
    body: dict[str, Any] = {
        "plan_id": plan_id_str,
        "total_count": total_count,
        "quantity": 1,
        "customer_notify": 1 if notify_email else 0,
        "notes": {"user_id": user_id, "user_email": user_email},
    }
    if user_name:
        body["notes"]["user_name"] = user_name
    return _request("POST", "/subscriptions", body)


def fetch_subscription(subscription_id: str) -> dict:
    return _request("GET", f"/subscriptions/{subscription_id}")


def cancel_subscription(subscription_id: str, cancel_at_cycle_end: bool = True) -> dict:
    body = {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0}
    return _request("POST", f"/subscriptions/{subscription_id}/cancel", body)


# ── Signature verification ─────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, header_signature: str) -> bool:
    """Razorpay sends HMAC-SHA256(body, webhook_secret) hex digest in
    X-Razorpay-Signature. Constant-time compare."""
    if not header_signature:
        return False
    expected = hmac.new(
        _webhook_secret().encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature.strip())


def verify_payment_signature(
    *, order_id: str, payment_id: str, signature: str
) -> bool:
    """Used for one-time orders (not subscriptions). Format:
       HMAC-SHA256("{order_id}|{payment_id}", key_secret)."""
    if not signature:
        return False
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(
        _key_secret().encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def verify_subscription_payment_signature(
    *, subscription_id: str, payment_id: str, signature: str
) -> bool:
    """For subscription auth-confirmation step. Format:
       HMAC-SHA256("{payment_id}|{subscription_id}", key_secret)."""
    if not signature:
        return False
    msg = f"{payment_id}|{subscription_id}".encode("utf-8")
    expected = hmac.new(
        _key_secret().encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())
```

### 3.6 Routes to add to `routes.py`

Add these alongside the existing LS routes:

```python
from . import razorpay_client as rzp

# Updated PLANS catalogue — each pro plan now carries BOTH provider hints.
# Add to the existing dicts in PLANS:
#   "rzpPlanEnv": "RAZORPAY_PLAN_PRO_MONTHLY",
#   "priceInr":   799,
# (and the yearly equivalents)


@bp.route(route="api/v1/billing/checkout/razorpay", methods=["POST"])
def create_razorpay_checkout(req: func.HttpRequest) -> func.HttpResponse:
    """Body: { "planId": "pro_monthly" | "pro_yearly" }
       Returns: { "subscriptionId": "sub_xxx", "shortUrl": "https://rzp.io/i/...",
                  "keyId": "rzp_live_xxx" }   ← keyId so the frontend can mount Checkout.js
    """
    try:
        user_id = get_user_id(req)
        body = req.get_json() if req.get_body() else {}
        plan_id_input = (body.get("planId") or "").strip()
        plan = _plan_by_id(plan_id_input)
        if not plan or plan["id"] == "free":
            raise ValidationError(f"Unknown or non-purchasable plan: {plan_id_input}")

        rzp_plan_env = plan.get("rzpPlanEnv", "")
        rzp_plan = os.environ.get(rzp_plan_env, "").strip() if rzp_plan_env else ""
        if not rzp_plan:
            raise ValidationError(
                "Plan is not configured for Razorpay — missing plan id "
                f"(set {rzp_plan_env or 'RAZORPAY_PLAN_*'} env var)"
            )

        profile = read_item("profiles", user_id, user_id) or {}
        personal = profile.get("personal") or {}
        email = (profile.get("email") or personal.get("email") or "").strip()
        name = ((personal.get("firstName") or "") + " " +
                (personal.get("lastName") or "")).strip()
        if not email:
            user = read_item("users", user_id, user_id) or {}
            email = (user.get("email") or "").strip()
        if not email:
            raise ValidationError("User has no email on file")

        # 12 months for monthly, 5 years for yearly (Razorpay requires total_count)
        total = 12 if plan.get("interval") == "month" else 5

        result = rzp.create_subscription(
            plan_id_str=rzp_plan,
            user_id=user_id,
            user_email=email,
            user_name=name,
            total_count=total,
        )
        sub_id = result.get("id")
        short_url = result.get("short_url")
        if not sub_id:
            raise AppException("Failed to create Razorpay subscription", status_code=502)

        return success_response({
            "subscriptionId": sub_id,
            "shortUrl": short_url,                     # for redirect-based flow
            "keyId": os.environ.get("RAZORPAY_KEY_ID", ""),  # for Checkout.js modal
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("create_razorpay_checkout failed")
        return internal_error_response(str(e))


@bp.route(route="api/v1/webhooks/razorpay", methods=["POST"])
def razorpay_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Razorpay → AutoApply webhook.

    Verifies HMAC, mirrors subscription state into the same containers used
    by the LS handler. Always returns 200 to stop retries on internal error.
    """
    try:
        raw = req.get_body() or b""
        sig = (req.headers.get("X-Razorpay-Signature")
               or req.headers.get("x-razorpay-signature") or "")
        if not rzp.verify_webhook_signature(raw, sig):
            logger.warning("RZP webhook signature mismatch")
            return func.HttpResponse(
                json.dumps({"error": "invalid signature"}),
                status_code=401, mimetype="application/json")

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return func.HttpResponse(
                json.dumps({"error": "invalid json"}),
                status_code=400, mimetype="application/json")

        event = payload.get("event") or ""
        # Razorpay nests the entity under payload.payload.<entity>.entity
        entities = payload.get("payload") or {}
        sub_entity = (entities.get("subscription") or {}).get("entity") or {}
        pay_entity = (entities.get("payment") or {}).get("entity") or {}
        notes = sub_entity.get("notes") or pay_entity.get("notes") or {}
        user_id = (notes.get("user_id") or "").strip()
        rzp_sub_id = sub_entity.get("id") or pay_entity.get("subscription_id") or ""

        if not user_id:
            logger.info("RZP webhook %s w/o user_id, ignoring", event)
            return func.HttpResponse("ok", status_code=200)

        _ensure_subscriptions_container()
        _handle_razorpay_event(event, user_id, sub_entity, pay_entity, rzp_sub_id, payload)
        return func.HttpResponse("ok", status_code=200)
    except Exception as e:
        logger.exception("RZP webhook handling failed: %s", e)
        return func.HttpResponse("ok", status_code=200)


def _handle_razorpay_event(
    event: str, user_id: str, sub: dict, pay: dict, rzp_sub_id: str, raw: dict
) -> None:
    # Activation / renewal
    if event in (
        "subscription.activated",
        "subscription.charged",
        "subscription.resumed",
        "subscription.updated",
    ):
        _upsert_razorpay_subscription(user_id, sub, rzp_sub_id, raw,
                                      set_active=True, force_status="active")
        return

    if event == "subscription.pending":
        _upsert_razorpay_subscription(user_id, sub, rzp_sub_id, raw,
                                      set_active=True, force_status="past_due")
        return

    if event in ("subscription.halted", "payment.failed"):
        _upsert_razorpay_subscription(user_id, sub, rzp_sub_id, raw,
                                      set_active=True, force_status="past_due")
        return

    if event == "subscription.cancelled":
        # cancel_at_cycle_end=1 → user keeps Pro until current_end
        _upsert_razorpay_subscription(user_id, sub, rzp_sub_id, raw,
                                      set_active=True, force_status="cancelled")
        return

    if event in ("subscription.completed", "subscription.expired"):
        _upsert_razorpay_subscription(user_id, sub, rzp_sub_id, raw,
                                      set_active=False, force_status="expired")
        return

    logger.info("RZP event %s ignored", event)


def _upsert_razorpay_subscription(
    user_id: str, sub: dict, rzp_sub_id: str, raw: dict,
    *, set_active: bool, force_status: str
) -> None:
    """Mirror Razorpay subscription state into Cosmos."""
    from datetime import datetime, timezone
    profile = read_item("profiles", user_id, user_id) or {"id": user_id, "userId": user_id}

    interval = "month"  # Razorpay plan period — fetch from plan if needed
    # current_end is a Unix timestamp
    current_end_ts = sub.get("current_end")
    renews_at = (datetime.fromtimestamp(current_end_ts, tz=timezone.utc).isoformat()
                 if current_end_ts else None)

    profile["subscription"] = {
        "tier": "pro" if set_active and force_status in ("active", "cancelled", "past_due") else "free",
        "provider": "razorpay",
        "status": force_status,
        "interval": interval,
        "currency": "INR",
        "renewsAt": renews_at,
        "endsAt": renews_at if force_status == "cancelled" else None,
        "providerSubscriptionId": rzp_sub_id,
        "providerCustomerId": sub.get("customer_id", ""),
    }
    profile["tier"] = profile["subscription"]["tier"]  # legacy mirror
    upsert_item("profiles", profile)

    # Audit doc
    audit = {
        "id": f"sub-razorpay-{rzp_sub_id}",
        "userId": user_id,
        "provider": "razorpay",
        "providerSubscriptionId": rzp_sub_id,
        "status": force_status,
        "raw": raw,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    upsert_item("subscriptions", audit)
```

### 3.7 Razorpay dashboard setup (one-time, ~30 min)

1. Sign up at <https://dashboard.razorpay.com/signup>. KYC takes ~1 business day for INR settlements (PAN, GST, bank account proof).
2. **Generate Test API keys** first: Settings → API Keys → "Generate Test Key". You can build and test the integration entirely in test mode.
3. **Create plans**: Subscriptions → Plans → "+ Create Plan":
   - Plan name: `Pro Monthly`
   - Billing period: `monthly`, frequency `1`
   - Amount: `79900` (Razorpay uses paise — multiply rupees by 100)
   - Currency: `INR`
   - Save and copy the `plan_id` → set `RAZORPAY_PLAN_PRO_MONTHLY`
   - Repeat for `Pro Yearly` (`amount: 699900`) → `RAZORPAY_PLAN_PRO_YEARLY`
4. **Create webhook**:
   - Settings → Webhooks → "+ Add New Webhook"
   - URL: `https://<your-function-app>.azurewebsites.net/api/v1/webhooks/razorpay`
   - Active events:
     - `subscription.activated`
     - `subscription.charged`
     - `subscription.pending`
     - `subscription.halted`
     - `subscription.cancelled`
     - `subscription.completed`
     - `subscription.updated`
     - `subscription.resumed`
     - `subscription.expired`
     - `payment.failed`
   - Secret: pick a 32+ char random string → set `RAZORPAY_WEBHOOK_SECRET`
5. After KYC, regenerate **Live keys** and swap `rzp_test_*` → `rzp_live_*` in env vars.

### 3.8 Frontend wiring (Flutter)

There are two flows. **Pick one**:

#### Option A — Redirect to Razorpay-hosted page (simpler, recommended first)

```dart
// app/lib/screens/billing/upgrade_screen.dart
final res = await api.post('/api/v1/billing/checkout/razorpay',
                            body: {'planId': 'pro_monthly'});
final shortUrl = res['shortUrl'] as String;
html.window.open(shortUrl, '_self');   // user lands on rzp.io/i/<token>
```

User completes payment, Razorpay redirects to your `callback_url` (set on the plan or passed at subscription creation). The webhook fires asynchronously and is the **authoritative** source of truth — don't grant Pro on the redirect alone.

#### Option B — In-page Checkout.js modal (more polished)

Add to `app/web/index.html`:

```html
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
```

Then from Dart via `package:js`:

```dart
@JS('Razorpay')
class Razorpay {
  external Razorpay(dynamic options);
  external void open();
}

// after POST /api/v1/billing/checkout/razorpay:
final options = js.JsObject.jsify({
  'key': res['keyId'],
  'subscription_id': res['subscriptionId'],
  'name': 'AutoApply Pro',
  'description': 'Monthly subscription',
  'handler': allowInterop((response) {
    // response has razorpay_payment_id, razorpay_subscription_id, razorpay_signature
    // OPTIONAL: POST to /api/v1/billing/razorpay/verify to double-check signature
    // BUT the webhook is what actually flips the tier — show "processing…" UI here.
  }),
  'prefill': {'email': user.email, 'name': user.name},
  'theme': {'color': '#7C3AED'},
});
js.context.callMethod('Razorpay', [options]).callMethod('open');
```

### 3.9 Idempotency, retries, race conditions

- **Webhook re-delivery:** Razorpay retries on non-2xx for ~24 h. Always return 200 once you've stored the event. The audit doc id (`sub-razorpay-<id>`) ensures upsert-overwrite semantics.
- **Out-of-order events:** `subscription.cancelled` may arrive before the final `subscription.charged` for the cycle. Use the event's `created_at` timestamp to ignore older events that would regress state. Simplest version: trust last-write-wins; the next genuine state change repairs.
- **Webhook before redirect:** common in test mode — webhook lands in <1 s, faster than the user's redirect. Frontend should poll `GET /api/v1/billing/subscription` after the redirect lands and show "activating…" until `tier == "pro"`.

### 3.10 Test plan

| Scenario | How to trigger | Expected |
|---|---|---|
| Happy path activation | Test card `4111 1111 1111 1111`, OTP `123456` | Webhook `subscription.activated` → profile.tier=`pro` |
| Authentication failed | Test card `5104 0600 0000 0008` | Webhook `payment.failed` → status=`past_due` |
| Renewal success | Razorpay Test → "Force charge" on subscription | Webhook `subscription.charged` → renewsAt updated |
| Cancellation (cycle end) | `POST /api/v1/billing/cancel` | `subscription.cancelled` event → status=`cancelled`, tier still `pro` until `endsAt` |
| Expiry | Wait past `endsAt` (or use Razorpay's "Force expire") | `subscription.expired` → tier=`free` |
| Webhook signature tamper | Manually `curl` with wrong `X-Razorpay-Signature` | 401 + `invalid signature` |

Add unit tests in `api/tests/test_razorpay_webhook.py` covering:
- Valid signature → 200
- Invalid signature → 401
- Missing `user_id` in notes → 200 with no profile mutation
- Each event type → expected `subscription.status` and `tier`

---

## 4. Common concerns

### 4.1 Where do secrets live?

- **Local dev:** `api/local.settings.json` (gitignored)
- **Production:** Function App settings, ideally backed by Key Vault references:
  ```
  @Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/RAZORPAY-KEY-SECRET/)
  ```
- **Never** commit a real key. The webhook secret is the most-used and most-leaked — rotate it any time you suspect exposure (regenerate in dashboard, push new env var, restart function).

### 4.2 Refunds

- **Lemon Squeezy:** initiate from LS dashboard → Orders → Refund. The `subscription_payment_refunded` webhook fires; we currently log only. If you want auto-revoke, add it to `_handle_event`.
- **Razorpay:** initiate from dashboard → Transactions → Refund. The `refund.processed` event is **not** in our subscribe list above — add it if you want auto-revoke.

### 4.3 Currency display on the pricing page

The frontend should fetch `/api/v1/billing/plans` and pick the price based on the user's region. Recommended: extend `PLANS` so each pro variant has both `priceUsd` and `priceInr`, then the UI shows whichever matches the chosen provider. Keep `priceUsd` as the canonical reporting unit so existing analytics queries don't break.

### 4.4 Tax / GST

- **Lemon Squeezy** is Merchant of Record — handles all VAT/sales tax automatically. Nothing to do.
- **Razorpay** is a **payment gateway**, not MoR. You are responsible for GST invoicing in India. Use Razorpay Invoices (`/v1/invoices` API) or wire ZohoBooks/Sleek for compliant GST invoices. Out of scope for this guide.

### 4.5 PCI scope

Both providers handle card data on their domain (LS hosted page, Razorpay Checkout.js iframe). **We never touch PAN/CVV** → we stay out of PCI scope (SAQ A only). Do not build a custom card form.

### 4.6 Switching a user from one provider to another

If a user moves countries, just let their current subscription run to end-of-cycle on the original provider. New subscription on the new provider creates a fresh `providerSubscriptionId`. The latest webhook wins — no manual reconciliation needed.

---

## 5. Quick checklist for "is payments working?"

```text
[ ] LEMONSQUEEZY_API_KEY set on Function App
[ ] LEMONSQUEEZY_WEBHOOK_SECRET set + matches dashboard
[ ] LEMONSQUEEZY_VARIANT_PRO_{MONTHLY,YEARLY} set
[ ] LS webhook URL points at the live function
[ ] LS test purchase shows up in subscriptions container within 5 s

[ ] RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET set
[ ] RAZORPAY_WEBHOOK_SECRET set + matches dashboard
[ ] RAZORPAY_PLAN_PRO_{MONTHLY,YEARLY} set
[ ] RZP webhook URL points at the live function
[ ] RZP test purchase (test card 4111…) flips tier to "pro"
[ ] /api/v1/billing/cancel transitions both providers correctly
```

When all 11 lines are checked, payments are production-ready.
