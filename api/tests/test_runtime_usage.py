"""Usage-event recording for quota checks (including Pro/admin telemetry)."""

from unittest.mock import patch

from services._runtime import (
    _check_daily_autofill_quota,
    _check_daily_event_quota,
    _check_daily_linkedin_quota,
    _check_daily_quota,
)


def _admin_profile() -> dict:
    return {"id": "user-admin-1", "tier": "admin", "email": "admin@example.com"}


def _pro_profile() -> dict:
    return {
        "id": "user-pro-1",
        "tier": "pro",
        "subscription": {"tier": "pro", "status": "active"},
    }


@patch("services._runtime._record_usage_event")
def test_premium_discover_records_usage(mock_record):
    allowed, remaining = _check_daily_quota(_pro_profile(), search_id="s1")
    assert allowed is True
    assert remaining == -1
    mock_record.assert_called_once_with("user-pro-1", "discover", "s1")


@patch("services._runtime._record_usage_event")
def test_admin_linkedin_records_usage(mock_record):
    allowed, remaining = _check_daily_linkedin_quota(_admin_profile())
    assert allowed is True
    assert remaining == -1
    mock_record.assert_called_once_with("user-admin-1", "linkedin")


@patch("services._runtime._record_usage_event")
def test_admin_autofill_records_usage(mock_record):
    allowed, remaining = _check_daily_autofill_quota(_admin_profile())
    assert allowed is True
    mock_record.assert_called_once_with("user-admin-1", "autofill")


@patch("services._runtime._record_usage_event")
def test_admin_tailor_records_usage(mock_record):
    allowed, remaining = _check_daily_event_quota(_admin_profile(), "tailor", 1)
    assert allowed is True
    mock_record.assert_called_once_with("user-admin-1", "tailor")
