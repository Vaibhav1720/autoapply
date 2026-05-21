"""Pytest fixtures for AutoApply API tests."""

import json
import azure.functions as func


def build_request(
    method: str = "GET",
    url: str = "/api/v1/health",
    body: dict | None = None,
    headers: dict | None = None,
    params: dict | None = None,
) -> func.HttpRequest:
    """Build a mock HttpRequest for testing."""
    return func.HttpRequest(
        method=method,
        url=url,
        body=json.dumps(body).encode() if body else b"",
        headers=headers or {},
        params=params or {},
    )
