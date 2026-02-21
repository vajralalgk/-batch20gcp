"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Custom Exception Hierarchy
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Enterprise applications need a structured exception hierarchy to:
    1. Distinguish between client errors (4xx) and server errors (5xx)
    2. Provide consistent error responses across all API endpoints
    3. Enable specific error handling per integration (ServiceNow, Ellucian, AWS)
    4. Support error tracking and metrics (e.g., count ServiceNow failures)
    5. Avoid leaking internal details to API consumers

DESIGN DECISIONS:
    - All exceptions inherit from ECTPBaseError for catch-all handling
    - Each exception maps to a specific HTTP status code
    - Error codes are machine-readable (for client-side handling)
    - Error messages are human-readable (for debugging)
    - Integration-specific exceptions allow targeted retry logic

SECURITY IMPLICATIONS:
    - Internal error details are logged but NOT returned to clients
    - Stack traces are never exposed in API responses
    - Error messages avoid revealing system architecture details

ALTERNATIVES CONSIDERED:
    - HTTP exceptions only (FastAPI): Too generic, no business context
    - Error codes as strings: Less type-safe than exception classes
    - Single exception class with error codes: Harder to catch specifically
============================================================================
"""

from typing import Any, Dict, Optional  # Type hints for error metadata


class ECTPBaseError(Exception):
    """
    Base exception class for all ECTP platform errors.

    WHY: Provides a common ancestor for all custom exceptions, enabling:
    1. Catch-all error handling: except ECTPBaseError catches everything
    2. Consistent structure: all errors have code, message, and status_code
    3. Serialization: to_dict() produces consistent API error responses

    All ECTP-specific exceptions MUST inherit from this class.
    """

    def __init__(
        self,
        message: str,  # Human-readable error description
        error_code: str = "ECTP_ERROR",  # Machine-readable error code
        status_code: int = 500,  # HTTP status code to return
        details: Optional[Dict[str, Any]] = None,  # Additional context
    ):
        """
        Initialize the base exception.

        Args:
            message: Human-readable description of what went wrong.
                    This is logged and may be returned to API clients.
            error_code: Machine-readable code (e.g., 'AUTH_FAILED').
                       Clients use this to handle specific errors programmatically.
            status_code: HTTP status code for the API response.
                        4xx for client errors, 5xx for server errors.
            details: Optional dictionary with additional context.
                    Logged for debugging but NOT returned to clients in production.
        """
        # Call the parent Exception constructor with the message.
        # WHY: Ensures standard exception behavior (str(error) returns message).
        super().__init__(message)

        # Store all error attributes as instance variables.
        # WHY: These are used by the global exception handler to build
        # consistent API error responses.
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes the exception to a dictionary for API responses.

        WHY: Ensures every error returned to clients has the same structure.
        Clients can always expect {error_code, message, details} in error responses.

        SECURITY: The 'details' field should be filtered in production
        to avoid leaking internal implementation details.

        Returns:
            Dictionary with error_code, message, and details.
        """
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class NotFoundError(ECTPBaseError):
    """
    Raised when a requested resource does not exist.

    WHY: Maps to HTTP 404. Used when:
    - A database query returns no results for the given ID
    - An AWS resource doesn't exist
    - A ServiceNow record is not found

    EXAMPLE:
        raise NotFoundError("Migration plan", "PLAN-001")
        # Returns: {"error_code": "NOT_FOUND", "message": "Migration plan 'PLAN-001' not found"}
    """

    def __init__(
        self,
        resource_type: str,  # What kind of resource (e.g., "Migration Plan")
        resource_id: str,  # The identifier that was not found
        details: Optional[Dict[str, Any]] = None,  # Additional context
    ):
        # Construct a descriptive message that helps debugging.
        # WHY: Generic "Not Found" messages are useless for troubleshooting.
        super().__init__(
            message=f"{resource_type} '{resource_id}' not found",
            error_code="NOT_FOUND",
            status_code=404,  # HTTP 404 Not Found
            details=details,
        )


class AuthenticationError(ECTPBaseError):
    """
    Raised when authentication fails (invalid credentials, expired token).

    WHY: Maps to HTTP 401. Distinguishes "who are you?" (401) from
    "you don't have permission" (403). This distinction is important for:
    - Client-side handling (redirect to login vs. show access denied)
    - Security monitoring (track authentication failures for brute-force detection)

    SECURITY: Error message must NOT reveal whether the username or password
    was wrong (prevents user enumeration attacks).
    """

    def __init__(
        self,
        message: str = "Authentication failed",  # Generic by default (security)
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            error_code="AUTH_FAILED",
            status_code=401,  # HTTP 401 Unauthorized
            details=details,
        )


class AuthorizationError(ECTPBaseError):
    """
    Raised when an authenticated user lacks permission for an action.

    WHY: Maps to HTTP 403. The user IS authenticated but does NOT have
    the required role or permission. Used for RBAC enforcement.

    EXAMPLE:
        # Developer trying to deploy to production
        raise AuthorizationError(
            "Insufficient permissions",
            required_permission="deploy:production"
        )
    """

    def __init__(
        self,
        message: str = "Insufficient permissions",
        required_permission: Optional[str] = None,  # The permission they need
        details: Optional[Dict[str, Any]] = None,
    ):
        # Include the required permission in details for logging.
        # WHY: Helps admins understand what permission to grant.
        _details = details or {}
        if required_permission:
            _details["required_permission"] = required_permission

        super().__init__(
            message=message,
            error_code="FORBIDDEN",
            status_code=403,  # HTTP 403 Forbidden
            details=_details,
        )


class ValidationError(ECTPBaseError):
    """
    Raised when input data fails validation rules.

    WHY: Maps to HTTP 422. Used when Pydantic validation passes (correct types)
    but business logic validation fails (e.g., start_date > end_date).

    ALTERNATIVE: FastAPI's built-in RequestValidationError handles Pydantic
    validation. This class handles business logic validation that Pydantic can't.
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,  # The field that failed validation
        details: Optional[Dict[str, Any]] = None,
    ):
        _details = details or {}
        if field:
            _details["field"] = field

        super().__init__(
            message=message,
            error_code="VALIDATION_ERROR",
            status_code=422,  # HTTP 422 Unprocessable Entity
            details=_details,
        )


class ExternalServiceError(ECTPBaseError):
    """
    Base exception for all external service integration failures.

    WHY: External services (ServiceNow, Ellucian, AWS) can fail independently.
    This base class enables:
    1. Catch-all handling for any external failure
    2. Circuit breaker pattern implementation
    3. Specific retry logic per service type

    Maps to HTTP 502 (Bad Gateway) — our service is fine but the
    upstream service failed.
    """

    def __init__(
        self,
        service_name: str,  # Which service failed (for metrics)
        message: str,
        error_code: str = "EXTERNAL_SERVICE_ERROR",
        status_code: int = 502,  # HTTP 502 Bad Gateway
        details: Optional[Dict[str, Any]] = None,
    ):
        _details = details or {}
        _details["service"] = service_name

        super().__init__(
            message=f"[{service_name}] {message}",
            error_code=error_code,
            status_code=status_code,
            details=_details,
        )


class ServiceNowError(ExternalServiceError):
    """
    Raised when ServiceNow API calls fail.

    WHY: ServiceNow has specific error patterns:
    - OAuth token expiry
    - Rate limiting
    - Instance maintenance windows

    Having a dedicated exception allows:
    - Specific retry logic (refresh token on 401)
    - Circuit breaker tuning for ServiceNow specifically
    - Metrics tracking for ServiceNow availability
    """

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            service_name="ServiceNow",
            message=message,
            error_code="SERVICENOW_ERROR",
            status_code=502,
            details=details,
        )


class EllucianError(ExternalServiceError):
    """
    Raised when Ellucian Ethos API calls fail.

    WHY: Ellucian APIs have unique characteristics:
    - Ethos API key expiry
    - Rate limits per tenant
    - Specific error codes for data validation failures

    Having a dedicated exception enables:
    - Automatic API key refresh
    - Tenant-specific error handling
    - Tracking Ellucian integration health separately
    """

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            service_name="Ellucian",
            message=message,
            error_code="ELLUCIAN_ERROR",
            status_code=502,
            details=details,
        )


class AWSServiceError(ExternalServiceError):
    """
    Raised when AWS API calls fail.

    WHY: AWS services can fail due to:
    - Throttling (rate limits)
    - Service outages
    - IAM permission issues
    - Resource limits

    The boto3 SDK raises botocore.exceptions, but we wrap them in
    AWSServiceError to maintain our exception hierarchy and add context.
    """

    def __init__(
        self,
        message: str,
        aws_service: Optional[str] = None,  # e.g., "EC2", "S3", "RDS"
        details: Optional[Dict[str, Any]] = None,
    ):
        _details = details or {}
        if aws_service:
            _details["aws_service"] = aws_service

        super().__init__(
            service_name="AWS",
            message=message,
            error_code="AWS_ERROR",
            status_code=502,
            details=_details,
        )


class RateLimitError(ECTPBaseError):
    """
    Raised when a client exceeds the API rate limit.

    WHY: Maps to HTTP 429. Rate limiting protects the platform from:
    - Accidental infinite loops in client code
    - Denial of service (DoS) attacks
    - Unfair resource consumption by a single tenant

    The retry_after field tells the client how long to wait,
    which is the standard behavior per RFC 6585.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int = 60,  # Seconds to wait before retrying
        details: Optional[Dict[str, Any]] = None,
    ):
        _details = details or {}
        _details["retry_after_seconds"] = retry_after

        super().__init__(
            message=message,
            error_code="RATE_LIMIT_EXCEEDED",
            status_code=429,  # HTTP 429 Too Many Requests
            details=_details,
        )


class ConfigurationError(ECTPBaseError):
    """
    Raised when a required configuration value is missing or invalid.

    WHY: Configuration errors should be caught at startup, not at runtime.
    Maps to HTTP 500 because it's a server-side issue (misconfiguration).

    EXAMPLE:
        if not settings.jwt_secret_key or settings.jwt_secret_key == "CHANGE-ME":
            raise ConfigurationError("JWT secret key is not configured")
    """

    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,  # Which config value is problematic
        details: Optional[Dict[str, Any]] = None,
    ):
        _details = details or {}
        if config_key:
            _details["config_key"] = config_key

        super().__init__(
            message=f"Configuration error: {message}",
            error_code="CONFIG_ERROR",
            status_code=500,  # HTTP 500 Internal Server Error
            details=_details,
        )
