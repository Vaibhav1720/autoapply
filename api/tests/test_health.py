"""Tests for the health check endpoint."""

import json
from tests.conftest import build_request
from function_app import health_check


def test_health_check_returns_200():
    req = build_request(method="GET", url="/api/v1/health")
    resp = health_check(req)
    assert resp.status_code == 200


def test_health_check_returns_json():
    req = build_request(method="GET", url="/api/v1/health")
    resp = health_check(req)
    body = json.loads(resp.get_body())
    assert body["status"] == "healthy"
    # Service identifier evolved to autoapply-v2 in the v2 split.
    assert body["service"] == "autoapply-v2"
    # Version surface follows the v2 release line.
    assert body["version"] == "2.0.0"
    assert "timestamp" in body
