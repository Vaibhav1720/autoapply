"""Integration tests for Profile API endpoints using dev mode auth."""

import json
import os
import azure.functions as func

# Ensure dev mode (no B2C) for testing
os.environ.pop("B2C_TENANT", None)
os.environ.pop("B2C_CLIENT_ID", None)

# Set Cosmos DB connection for integration tests
# These tests require a running Cosmos DB (either emulator or Azure)
COSMOS_CONFIGURED = bool(os.environ.get("COSMOS_ENDPOINT"))

import pytest

if COSMOS_CONFIGURED:
    from function_app import create_profile, get_profile, update_profile


def _make_request(method="GET", body=None, headers=None, url="/api/v1/profile"):
    h = {"X-Dev-User-Id": "test-user-integration"}
    if headers:
        h.update(headers)
    return func.HttpRequest(
        method=method,
        url=url,
        body=json.dumps(body).encode() if body else b"",
        headers=h,
    )


@pytest.mark.skipif(not COSMOS_CONFIGURED, reason="Cosmos DB not configured")
class TestProfileIntegration:
    """Integration tests that hit real Cosmos DB."""

    def test_create_profile(self):
        # Clean up first (delete if exists)
        from shared.cosmos_client import read_item, delete_item
        existing = read_item("profiles", "test-user-integration", "test-user-integration")
        if existing:
            delete_item("profiles", "test-user-integration", "test-user-integration")

        req = _make_request(method="POST", body={
            "personal": {"firstName": "Test", "lastName": "User"},
            "education": [],
            "certifications": [],
        })
        resp = create_profile(req)
        assert resp.status_code == 201

        body = json.loads(resp.get_body())
        assert body["id"] == "test-user-integration"
        assert body["profile"]["personal"]["firstName"] == "Test"

    def test_create_profile_conflict(self):
        # Try to create again — should return 409
        req = _make_request(method="POST", body={
            "personal": {"firstName": "Test", "lastName": "User"},
        })
        resp = create_profile(req)
        assert resp.status_code == 409

    def test_get_profile(self):
        req = _make_request(method="GET")
        resp = get_profile(req)
        assert resp.status_code == 200

        body = json.loads(resp.get_body())
        assert body["personal"]["firstName"] == "Test"
        assert body["userId"] == "test-user-integration"

    def test_update_profile(self):
        req = _make_request(method="PUT", body={
            "personal": {"phone": "+1234567890"},
            "skills": {"technical": ["Python", "Azure"]},
        })
        resp = update_profile(req)
        assert resp.status_code == 200

        body = json.loads(resp.get_body())
        assert body["personal"]["phone"] == "+1234567890"
        assert "Python" in body["skills"]["technical"]

    def test_get_profile_not_found(self):
        req = func.HttpRequest(
            method="GET",
            url="/api/v1/profile",
            body=b"",
            headers={"X-Dev-User-Id": "nonexistent-user-xyz"},
        )
        resp = get_profile(req)
        assert resp.status_code == 404

    def test_cleanup(self):
        """Clean up test data."""
        from shared.cosmos_client import delete_item
        try:
            delete_item("profiles", "test-user-integration", "test-user-integration")
        except Exception:
            pass
