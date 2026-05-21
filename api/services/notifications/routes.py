"""Scheduled transactional emails — subscription expiry reminders."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared.cosmos_client import query_items
from shared.email_service import (
    _user_email_from_profile,
    is_configured,
    send_expiry_reminder,
)

logger = logging.getLogger(__name__)
bp = func.Blueprint()

# Daily at 09:00 UTC — tune via EXPIRY_REMINDER_CRON (NCRONTAB).
EXPIRY_REMINDER_CRON = os.environ.get("EXPIRY_REMINDER_CRON", "0 0 9 * * *")
# Send reminder when renewal/end is this many days away (inclusive).
EXPIRY_REMINDER_DAYS = int(os.environ.get("EXPIRY_REMINDER_DAYS", "3"))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _run_expiry_reminders() -> dict:
    if not is_configured():
        logger.warning("[EXPIRY_EMAIL] SMTP not configured — skipping run")
        return {"skipped": True, "reason": "smtp_not_configured"}

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=EXPIRY_REMINDER_DAYS + 1)
    sent = 0
    skipped = 0
    errors = 0

    profiles = query_items(
        "profiles",
        "SELECT c.id, c.email, c.personal, c.subscription, c.tier FROM c "
        "WHERE IS_DEFINED(c.subscription) AND c.subscription.tier = 'pro'",
    )

    for profile in profiles:
        user_id = profile.get("id", "")
        sub = profile.get("subscription") or {}
        status = (sub.get("status") or "active").lower()

        renews = _parse_dt(sub.get("renewsAt"))
        ends = _parse_dt(sub.get("endsAt"))

        # Cancelled subs use endsAt; active subs prefer renewsAt then endsAt.
        if status in ("cancelled", "canceled") and ends:
            target = ends
            is_renewal = False
        elif renews:
            target = renews
            is_renewal = True
        elif ends:
            target = ends
            is_renewal = False
        else:
            skipped += 1
            continue

        if target <= now or target > window_end:
            skipped += 1
            continue

        days_left = max(1, (target.date() - now.date()).days)
        email, name = _user_email_from_profile(profile)
        if not email:
            skipped += 1
            continue

        expiry_display = target.strftime("%d %b %Y")
        try:
            if send_expiry_reminder(
                user_id=user_id,
                email=email,
                name=name,
                expiry_date_display=expiry_display,
                days_left=days_left,
                is_renewal=is_renewal,
            ):
                sent += 1
            else:
                errors += 1
        except Exception:
            errors += 1
            logger.exception("[EXPIRY_EMAIL] failed for user=%s", user_id)

    summary = {"sent": sent, "skipped": skipped, "errors": errors, "scanned": len(profiles)}
    logger.info("[EXPIRY_EMAIL] done %s", summary)
    return summary


@bp.schedule(
    schedule=EXPIRY_REMINDER_CRON,
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def subscription_expiry_reminder(timer: func.TimerRequest) -> None:
    try:
        _run_expiry_reminders()
    except Exception as e:
        logger.exception("[EXPIRY_EMAIL] unhandled: %s", e)
