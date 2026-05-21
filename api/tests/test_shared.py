"""Tests for exceptions and response helpers."""

import json
from shared.exceptions import (
    ValidationError,
    AuthenticationError,
    NotFoundError,
    ConflictError,
    RateLimitError,
)
from shared.response_helpers import (
    success_response,
    created_response,
    error_response,
    internal_error_response,
)


def test_validation_error():
    err = ValidationError("Bad input", details=["field 'name' required"])
    assert err.status_code == 400
    assert err.code == "VALIDATION_ERROR"
    assert len(err.details) == 1


def test_authentication_error():
    err = AuthenticationError()
    assert err.status_code == 401
    assert err.code == "AUTHENTICATION_ERROR"


def test_not_found_error():
    err = NotFoundError("Profile not found")
    assert err.status_code == 404
    assert err.message == "Profile not found"


def test_conflict_error():
    err = ConflictError()
    assert err.status_code == 409


def test_rate_limit_error():
    err = RateLimitError()
    assert err.status_code == 429


def test_success_response():
    resp = success_response({"key": "value"})
    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert body["key"] == "value"


def test_created_response():
    resp = created_response({"id": "123"})
    assert resp.status_code == 201


def test_error_response():
    err = ValidationError("Bad", details=["x"])
    resp = error_response(err)
    assert resp.status_code == 400
    body = json.loads(resp.get_body())
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"] == ["x"]


def test_internal_error_response():
    resp = internal_error_response()
    assert resp.status_code == 500
    body = json.loads(resp.get_body())
    assert body["error"]["code"] == "INTERNAL_ERROR"
