"""Standard HTTP response builders for the AutoApply API."""

import json
import azure.functions as func
from shared.exceptions import AppException


def success_response(data: dict | list, status_code: int = 200) -> func.HttpResponse:
    """Build a successful JSON response."""
    return func.HttpResponse(
        json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json",
    )


def created_response(data: dict) -> func.HttpResponse:
    """Build a 201 Created JSON response."""
    return success_response(data, 201)


def accepted_response(data: dict) -> func.HttpResponse:
    """Build a 202 Accepted JSON response."""
    return success_response(data, 202)


def no_content_response() -> func.HttpResponse:
    """Build a 204 No Content response."""
    return func.HttpResponse(status_code=204)


def error_response(exc: AppException) -> func.HttpResponse:
    """Build an error JSON response from an AppException."""
    body = {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    }
    return func.HttpResponse(
        json.dumps(body),
        status_code=exc.status_code,
        mimetype="application/json",
    )


def internal_error_response(message: str = "Internal server error") -> func.HttpResponse:
    """Build a generic 500 response for unhandled exceptions."""
    body = {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": message,
            "details": [],
        }
    }
    return func.HttpResponse(
        json.dumps(body),
        status_code=500,
        mimetype="application/json",
    )
