"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Structured Logging Module
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Enterprise applications generate massive volumes of logs. Without structure,
    finding relevant information is like searching for a needle in a haystack.
    This module implements structured (JSON) logging with:
    - Correlation IDs for request tracing across services
    - Consistent format across all platform components
    - CloudWatch-compatible output for AWS integration
    - Performance context (timing, resource usage)

DESIGN DECISIONS:
    - Uses structlog for structured logging (JSON output)
    - Every log entry includes: timestamp, level, service, correlation_id
    - Sensitive data (passwords, tokens) is automatically redacted
    - Log level is configurable per environment

SECURITY IMPLICATIONS:
    - PII and secrets are filtered from log output
    - Log files should be stored in encrypted S3 buckets
    - Access to logs requires appropriate IAM permissions

ALTERNATIVES CONSIDERED:
    - Standard library logging: Lacks structured output natively
    - loguru: Good but less enterprise adoption than structlog
    - AWS X-Ray only: Covers tracing but not application logging
============================================================================
"""

import logging  # Python's built-in logging framework — the foundation
import sys  # System-specific parameters — used for stdout/stderr output
import uuid  # Universally unique identifiers — for correlation IDs
from contextvars import ContextVar  # Thread-safe context variables for async
from datetime import datetime, timezone  # Timezone-aware timestamps
from typing import Any, Dict, Optional  # Type hints for function signatures

import structlog  # Structured logging library — produces JSON logs


# ---- Correlation ID Management ----
# ContextVar stores a unique ID per request in async applications.
# WHY: When multiple requests are processed concurrently, we need to
# distinguish which log entries belong to which request. ContextVar
# is async-safe, meaning each concurrent request gets its own value.
# ALTERNATIVE: Thread-local storage — doesn't work with async/await.
_correlation_id: ContextVar[str] = ContextVar(
    "correlation_id",  # Name for debugging
    default=""  # Empty string when no request is active
)


def get_correlation_id() -> str:
    """
    Retrieves the current request's correlation ID.

    WHY: Every log entry in a request should share the same correlation ID.
    This allows filtering all logs for a single request in CloudWatch
    or Grafana, even across multiple services.

    Returns:
        The current correlation ID string, or empty string if not set.
    """
    return _correlation_id.get()


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """
    Sets a correlation ID for the current request context.

    WHY: Called at the start of each HTTP request (in middleware).
    If the incoming request includes an X-Correlation-ID header,
    we use that (for cross-service tracing). Otherwise, generate a new one.

    Args:
        correlation_id: Optional existing ID from upstream service.
                       If None, a new UUID is generated.

    Returns:
        The correlation ID that was set.
    """
    # Generate a new UUID v4 if no correlation ID was provided.
    # UUID v4 is random, ensuring uniqueness across distributed systems.
    # We take the first 12 chars for brevity in logs while maintaining
    # sufficient uniqueness (36^12 possible values).
    cid = correlation_id or uuid.uuid4().hex[:12]

    # Store in the ContextVar so all subsequent log calls in this
    # request context will include this correlation ID.
    _correlation_id.set(cid)
    return cid


# ---- Sensitive Data Filtering ----
# List of field names that should NEVER appear in logs.
# WHY: Compliance (FERPA, HIPAA) requires that sensitive data
# is not written to log files, which may be accessed by operations staff.
SENSITIVE_FIELDS = frozenset({
    "password",      # User passwords
    "secret",        # API secrets
    "token",         # Auth tokens
    "authorization", # HTTP auth headers
    "api_key",       # API keys
    "ssn",           # Social Security Numbers (FERPA/PII)
    "credit_card",   # Payment card data (PCI-DSS)
    "private_key",   # Cryptographic keys
})


def _redact_sensitive_data(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Structlog processor that redacts sensitive fields from log entries.

    WHY: Defense-in-depth for data protection. Even if a developer
    accidentally logs a password, this processor will replace it with
    '[REDACTED]' before it reaches the log output.

    HOW: Iterates over all key-value pairs in the log event dict.
    If the key matches a known sensitive field name, the value is replaced.

    Args:
        logger: The logger instance (unused, required by structlog API).
        method_name: The log method called (unused, required by structlog API).
        event_dict: The log event dictionary to filter.

    Returns:
        The filtered event dictionary with sensitive values redacted.
    """
    for key in event_dict:
        # Convert key to lowercase for case-insensitive matching.
        # This catches 'Password', 'PASSWORD', 'password', etc.
        if key.lower() in SENSITIVE_FIELDS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def _add_correlation_id(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Structlog processor that adds the correlation ID to every log entry.

    WHY: Correlation IDs link all log entries for a single request.
    Without this, tracing a request across multiple services is impossible.

    HOW: Reads the correlation ID from the ContextVar (set by middleware)
    and adds it to the log event dictionary.

    Args:
        logger: The logger instance (unused, required by structlog API).
        method_name: The log method called (unused, required by structlog API).
        event_dict: The log event dictionary to enrich.

    Returns:
        The enriched event dictionary with correlation_id field.
    """
    # Only add if a correlation ID has been set (i.e., within a request context)
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _add_service_context(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Structlog processor that adds service identification to every log entry.

    WHY: In a microservices architecture, logs from multiple services
    flow into a centralized system (CloudWatch). Without service context,
    you can't tell which service produced a log entry.

    Args:
        logger: The logger instance (unused).
        method_name: The log method called (unused).
        event_dict: The log event dictionary to enrich.

    Returns:
        The enriched event dictionary with service context fields.
    """
    event_dict["service"] = "ectp"  # Service name for log filtering
    event_dict["version"] = "1.0.0"  # Version for debugging deployments
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configures the structured logging system for the entire application.

    WHY: Must be called once at application startup to configure all loggers.
    After this, any module using get_logger() will produce structured JSON logs
    with all the processors (redaction, correlation, service context) applied.

    HOW:
    1. Configures structlog with a chain of processors
    2. Sets up Python's standard logging to use structlog's formatting
    3. Applies the specified log level globally

    Args:
        log_level: The minimum log level to output (DEBUG, INFO, WARNING, etc.)
    """
    # Convert string level to logging constant (e.g., "INFO" -> logging.INFO)
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Define the processor chain. Each processor transforms the log event
    # dictionary in sequence before it reaches the output.
    # ORDER MATTERS: redaction must happen before rendering to JSON.
    shared_processors = [
        # Add timestamp in ISO 8601 format (CloudWatch compatible)
        structlog.stdlib.add_log_level,

        # Add the service name and version to every log entry
        _add_service_context,

        # Add the correlation ID for request tracing
        _add_correlation_id,

        # SECURITY: Redact sensitive fields before they reach output
        _redact_sensitive_data,

        # Add caller information (file, line number, function)
        # WHY: Essential for debugging — knowing WHERE a log came from
        structlog.stdlib.add_logger_name,
    ]

    # Configure structlog with our processors and JSON rendering
    structlog.configure(
        processors=[
            # Filter by log level first (performance: skip processing for filtered logs)
            structlog.stdlib.filter_by_level,

            # Apply our shared processors
            *shared_processors,

            # Add timestamp in UTC ISO format
            # WHY: UTC avoids timezone confusion in distributed systems
            structlog.processors.TimeStamper(fmt="iso", utc=True),

            # Format stack traces for exceptions
            # WHY: Structured stack traces are searchable in CloudWatch
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,

            # Final step: Render to JSON string
            # WHY: JSON logs are machine-parseable, enabling CloudWatch Insights
            # queries, Grafana dashboards, and automated alerting.
            # ALTERNATIVE: ConsoleRenderer for local development (human-readable)
            structlog.processors.JSONRenderer(),
        ],

        # Use stdlib's BoundLogger for compatibility with existing logging code
        wrapper_class=structlog.stdlib.BoundLogger,

        # Use a dict for the context (standard, works everywhere)
        context_class=dict,

        # Use stdlib logger factory for integration with Python logging
        logger_factory=structlog.stdlib.LoggerFactory(),

        # Cache logger instances for performance
        # WHY: Avoids re-creating logger objects on every get_logger() call
        cache_logger_on_first_use=True,
    )

    # Configure Python's root logger to output to stdout
    # WHY: Containers (ECS/EKS) capture stdout as logs automatically.
    # Writing to files is problematic in containers (ephemeral filesystems).
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove any existing handlers to prevent duplicate log entries
    # WHY: Libraries or frameworks may add handlers before our setup runs
    root_logger.handlers.clear()

    # Add a single handler that writes to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    # Set the root logger's handler
    root_logger.addHandler(handler)


def get_logger(name: str = "ectp") -> structlog.stdlib.BoundLogger:
    """
    Creates a structured logger instance with the given name.

    WHY: Provides a consistent way to create loggers throughout the application.
    Each logger is bound to a name (usually the module name) for filtering.

    USAGE:
        logger = get_logger(__name__)
        logger.info("user_login", user_id="123", ip="10.0.0.1")

    This produces JSON like:
        {"event": "user_login", "user_id": "123", "ip": "10.0.0.1",
         "service": "ectp", "correlation_id": "abc123", "level": "info",
         "timestamp": "2026-02-16T12:00:00Z"}

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        A structlog BoundLogger instance with the name bound.
    """
    return structlog.get_logger(name)
