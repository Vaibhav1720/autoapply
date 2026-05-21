"""Billing service — Lemon Squeezy integration.

Provides:
  - GET  /api/v1/billing/plans          → public list of available plans
  - POST /api/v1/billing/checkout       → create a Lemon Squeezy hosted checkout
                                          URL for the signed-in user
  - GET  /api/v1/billing/subscription   → current user's subscription summary
  - POST /api/v1/billing/cancel         → cancel the current subscription
  - POST /api/v1/billing/portal         → return the customer portal URL so
                                          the user can self-manage card / sub
  - POST /api/v1/webhooks/lemonsqueezy  → unsigned (HMAC-verified) webhook
                                          endpoint that drives tier flips
"""

from .routes import bp  # noqa: F401
