"""Transactional email via SMTP (Gmail / Google Workspace).

Configure in Azure Function App settings:
  SMTP_HOST          — default smtp.gmail.com
  SMTP_PORT          — default 587
  SMTP_USER          — default techvibeapps.ai@gmail.com
  SMTP_PASSWORD      — Gmail App Password (required to send)
  SMTP_FROM          — default SMTP_USER
  SUPPORT_EMAIL      — default techvibeapps.ai@gmail.com
  APP_NAME           — default AutoApply
  APP_URL            — default https://autoapplynow.in

Idempotency: each send is logged in match_events (kind=email_sent) so
webhooks/retries do not duplicate emails.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from shared.cosmos_client import query_items, upsert_item

logger = logging.getLogger(__name__)

SMTP_HOST = lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = lambda: int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = lambda: os.environ.get("SMTP_USER", "techvibeapps.ai@gmail.com").strip()
SMTP_PASSWORD = lambda: os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM = lambda: (os.environ.get("SMTP_FROM") or SMTP_USER()).strip()
SUPPORT_EMAIL = lambda: os.environ.get("SUPPORT_EMAIL", "techvibeapps.ai@gmail.com").strip()
APP_NAME = lambda: os.environ.get("APP_NAME", "ApplyRight").strip()
APP_URL = lambda: os.environ.get("APP_URL", "https://autoapplynow.in").strip()


def is_configured() -> bool:
    return bool(SMTP_USER() and SMTP_PASSWORD())


def _html_wrap(body: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,sans-serif;line-height:1.5;color:#1a1a2e;max-width:560px;margin:0 auto;padding:24px">
<div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:20px;border-radius:12px 12px 0 0">
  <h1 style="color:#fff;margin:0;font-size:22px">{APP_NAME()}</h1>
</div>
<div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
{body}
<p style="margin-top:24px;font-size:12px;color:#6b7280">
Questions? Reply to this email or write to <a href="mailto:{SUPPORT_EMAIL()}">{SUPPORT_EMAIL()}</a>.
</p>
</div>
</body></html>"""


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
) -> bool:
    """Send one email. Returns True on success, False if SMTP not configured or send fails."""
    to = (to or "").strip()
    if not to or "@" not in to:
        logger.warning("[EMAIL] skip — invalid recipient %r", to)
        return False
    if not is_configured():
        logger.warning(
            "[EMAIL] SMTP_PASSWORD not set — cannot send %r to %s. "
            "Set SMTP_USER/SMTP_PASSWORD in Azure App Settings.",
            subject, to,
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{APP_NAME()} <{SMTP_FROM()}>"
    msg["To"] = to
    plain = text_body or _strip_html(html_body)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(_html_wrap(html_body), "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST(), SMTP_PORT(), timeout=20) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(SMTP_USER(), SMTP_PASSWORD())
            server.sendmail(SMTP_FROM(), [to], msg.as_string())
        logger.info("[EMAIL] sent %r → %s", subject, to)
        return True
    except Exception as e:
        logger.exception("[EMAIL] failed %r → %s: %s", subject, to, e)
        return False


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _was_sent(dedupe_key: str) -> bool:
    rows = query_items(
        "match_events",
        "SELECT VALUE COUNT(1) FROM c WHERE c.kind = 'email_sent' AND c.dedupeKey = @k",
        [{"name": "@k", "value": dedupe_key}],
    )
    return bool(rows and rows[0] > 0)


def _record_sent(
    *,
    dedupe_key: str,
    template: str,
    user_id: str,
    email: str,
    meta: dict | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    doc = {
        "id": f"em-{int(now.timestamp())}-{dedupe_key[:24]}",
        "kind": "email_sent",
        "dedupeKey": dedupe_key,
        "template": template,
        "userId": user_id,
        "email": email,
        "timestamp": now.isoformat(),
        "meta": meta or {},
        "ttl": 90 * 24 * 60 * 60,
    }
    try:
        upsert_item("match_events", doc)
    except Exception as e:
        logger.warning("[EMAIL] could not record send log: %s", e)


def send_once(
    *,
    dedupe_key: str,
    template: str,
    user_id: str,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    meta: dict | None = None,
) -> bool:
    """Idempotent send — skips if dedupe_key already logged."""
    if _was_sent(dedupe_key):
        logger.debug("[EMAIL] skip duplicate %s", dedupe_key)
        return True
    ok = send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
    if ok:
        _record_sent(
            dedupe_key=dedupe_key,
            template=template,
            user_id=user_id,
            email=to,
            meta=meta,
        )
    return ok


# ── Templates ───────────────────────────────────────────────────────────────

def send_welcome(*, user_id: str, email: str, name: str) -> bool:
    display = (name or email.split("@")[0]).strip() or "there"
    subject = f"Welcome to {APP_NAME()} — you're all set"
    html = f"""
<p>Hi {display},</p>
<p>Thanks for signing up for <strong>{APP_NAME()}</strong>. Your account is ready.</p>
<p>Here's what you can do next:</p>
<ul>
  <li>Upload your resume and complete your profile</li>
  <li>Select target companies and run <strong>Discover</strong> for AI-matched roles</li>
  <li>Use the browser extension to autofill applications faster</li>
</ul>
<p><a href="{APP_URL()}" style="display:inline-block;background:#6366f1;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Open {APP_NAME()}</a></p>
<p>We're glad you're here.</p>
"""
    return send_once(
        dedupe_key=f"welcome-{user_id}",
        template="welcome",
        user_id=user_id,
        to=email,
        subject=subject,
        html_body=html,
    )


def send_payment_receipt(
    *,
    user_id: str,
    email: str,
    name: str,
    plan_name: str,
    amount_display: str,
    payment_id: str,
    provider: str,
    interval: str = "month",
) -> bool:
    display = (name or email.split("@")[0]).strip() or "there"
    subject = f"Payment receipt — {APP_NAME()} {plan_name}"
    html = f"""
<p>Hi {display},</p>
<p>We've received your payment. Thank you for upgrading to <strong>{plan_name}</strong>.</p>
<table style="width:100%;border-collapse:collapse;margin:16px 0">
  <tr><td style="padding:8px 0;color:#6b7280">Plan</td><td style="padding:8px 0"><strong>{plan_name}</strong> ({interval}ly)</td></tr>
  <tr><td style="padding:8px 0;color:#6b7280">Amount</td><td style="padding:8px 0"><strong>{amount_display}</strong></td></tr>
  <tr><td style="padding:8px 0;color:#6b7280">Payment ID</td><td style="padding:8px 0;font-family:monospace;font-size:13px">{payment_id}</td></tr>
  <tr><td style="padding:8px 0;color:#6b7280">Processor</td><td style="padding:8px 0">{provider}</td></tr>
</table>
<p>Your Pro features are active now. Manage your subscription anytime from your profile.</p>
<p><a href="{APP_URL()}/subscription" style="color:#6366f1">View subscription</a></p>
"""
    dedupe = f"payment-{payment_id}" if payment_id else f"payment-{user_id}-{plan_name}"
    return send_once(
        dedupe_key=dedupe,
        template="payment_receipt",
        user_id=user_id,
        to=email,
        subject=subject,
        html_body=html,
        meta={"plan": plan_name, "provider": provider, "paymentId": payment_id},
    )


def send_expiry_reminder(
    *,
    user_id: str,
    email: str,
    name: str,
    expiry_date_display: str,
    days_left: int,
    is_renewal: bool,
) -> bool:
    display = (name or email.split("@")[0]).strip() or "there"
    if is_renewal:
        subject = f"Your {APP_NAME()} subscription renews in {days_left} day(s)"
        action = "renews"
    else:
        subject = f"Your {APP_NAME()} Pro access ends in {days_left} day(s)"
        action = "ends"
    html = f"""
<p>Hi {display},</p>
<p>This is a friendly reminder that your <strong>{APP_NAME()}</strong> Pro subscription <strong>{action}</strong> on <strong>{expiry_date_display}</strong> ({days_left} day(s) from now).</p>
<p>To keep unlimited Discover searches, AI autofill, and all Pro features, make sure your payment method is up to date before that date.</p>
<p><a href="{APP_URL()}/subscription" style="display:inline-block;background:#6366f1;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Manage subscription</a></p>
<p>If you've already updated your billing, you can ignore this email.</p>
"""
    dedupe = f"expiry-{user_id}-{expiry_date_display}"
    return send_once(
        dedupe_key=dedupe,
        template="expiry_reminder",
        user_id=user_id,
        to=email,
        subject=subject,
        html_body=html,
        meta={"expiry": expiry_date_display, "daysLeft": days_left},
    )


def _user_email_from_profile(profile: dict) -> tuple[str, str]:
    """Return (email, display_name) from a profile dict."""
    personal = profile.get("personal") or {}
    email = (profile.get("email") or personal.get("email") or "").strip()
    first = (personal.get("firstName") or "").strip()
    last = (personal.get("lastName") or "").strip()
    name = f"{first} {last}".strip() or profile.get("name", "")
    return email, name
