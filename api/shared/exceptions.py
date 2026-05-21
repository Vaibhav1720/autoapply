"""Custom exception classes for the AutoApply API."""


class AppException(Exception):
    """Base exception for all application errors."""

    def __init__(
        self,
        message: str,
        code: str = "APP_ERROR",
        status_code: int = 500,
        details: list | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or []


class BadGatewayError(AppException):
    def __init__(self, message: str = "Upstream service error"):
        super().__init__(message, "BAD_GATEWAY", 502)


class ServiceUnavailableError(AppException):
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(message, "SERVICE_UNAVAILABLE", 503)


class ValidationError(AppException):
    def __init__(self, message: str = "Validation failed", details: list | None = None):
        super().__init__(message, "VALIDATION_ERROR", 400, details)


class AuthenticationError(AppException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, "AUTHENTICATION_ERROR", 401)


class AuthorizationError(AppException):
    def __init__(self, message: str = "Access denied"):
        super().__init__(message, "AUTHORIZATION_ERROR", 403)


class NotFoundError(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, "NOT_FOUND", 404)


class ConflictError(AppException):
    def __init__(self, message: str = "Resource already exists"):
        super().__init__(message, "CONFLICT", 409)


class RateLimitError(AppException):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, "RATE_LIMIT_EXCEEDED", 429)


class InternalError(AppException):
    def __init__(self, message: str = "Internal server error"):
        super().__init__(message, "INTERNAL_ERROR", 500)
