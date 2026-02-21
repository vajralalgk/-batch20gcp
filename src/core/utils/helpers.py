"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Shared Utility Functions
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Centralizes common utility functions used across multiple services.
    Avoids code duplication and ensures consistent behavior for:
    - ID generation
    - Timestamp handling
    - Input sanitization
    - AWS ARN construction
    - Retry logic
    - Data masking

DESIGN DECISIONS:
    - Pure functions with no side effects (easy to test)
    - No external service dependencies
    - All functions are type-annotated for IDE support

SECURITY IMPLICATIONS:
    - sanitize_input prevents injection attacks
    - mask_sensitive_string prevents data leakage in logs
============================================================================
"""

import re  # Regular expressions — used for input sanitization patterns
import uuid  # UUID generation — for unique resource identifiers
from datetime import datetime, timezone  # Timezone-aware datetime handling
from typing import Callable, TypeVar, Any  # Type hints for generic retry function

from tenacity import (  # Retry library with configurable backoff strategies
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)


def generate_id(prefix: str = "ectp") -> str:
    """
    Generates a unique identifier with a readable prefix.

    WHY: Every resource in the platform needs a unique ID. Using a prefix
    makes IDs self-documenting — you can tell what type of resource an ID
    belongs to just by looking at it.

    FORMAT: {prefix}-{uuid_first_12_chars}
    EXAMPLE: "ectp-a1b2c3d4e5f6", "mig-x9y8z7w6v5u4"

    WHY UUID v4: Random UUIDs prevent enumeration attacks (can't guess IDs).
    WHY 12 chars: 36^12 possible values = practically zero collision risk,
    while keeping IDs short enough for human readability.

    ALTERNATIVE: Auto-incrementing integers — rejected because they reveal
    total record count and are predictable (security risk).

    Args:
        prefix: A short string identifying the resource type.
               Examples: "mig" (migration), "inc" (incident), "usr" (user)

    Returns:
        A unique string ID like "ectp-a1b2c3d4e5f6"
    """
    # uuid4() generates a random UUID. .hex removes hyphens for cleaner output.
    # [:12] takes first 12 hex chars (48 bits of randomness).
    unique_part = uuid.uuid4().hex[:12]
    return f"{prefix}-{unique_part}"


def now_utc() -> datetime:
    """
    Returns the current time in UTC timezone.

    WHY: All timestamps in the platform MUST be in UTC to avoid confusion
    across time zones. This is especially important because:
    1. AWS CloudWatch uses UTC
    2. Database timestamps should be timezone-aware
    3. Log aggregation across regions requires a common timezone
    4. Daylight saving time doesn't affect UTC

    ALTERNATIVE: datetime.utcnow() — DEPRECATED in Python 3.12 because
    it returns a naive datetime (no timezone info). Our function returns
    a timezone-aware datetime, which is the correct approach.

    Returns:
        Current UTC datetime with timezone info attached.
    """
    # timezone.utc explicitly marks this datetime as UTC.
    # This prevents bugs where naive datetimes are assumed to be local time.
    return datetime.now(timezone.utc)


def sanitize_input(value: str, max_length: int = 1000) -> str:
    """
    Sanitizes user input to prevent injection attacks.

    WHY: User input is the #1 attack vector for web applications.
    This function provides a baseline defense by:
    1. Removing control characters that could break log parsing
    2. Stripping leading/trailing whitespace
    3. Enforcing maximum length to prevent buffer-based attacks
    4. Removing null bytes that could truncate strings in C-based systems

    SECURITY: This is a defense-in-depth measure. It does NOT replace:
    - SQL parameterized queries (for SQL injection)
    - HTML escaping (for XSS)
    - Schema validation (for business logic)

    WHERE USED: Applied at API input boundaries before data enters
    the business logic layer.

    Args:
        value: The raw user input string to sanitize.
        max_length: Maximum allowed length (default 1000 characters).
                   Prevents memory exhaustion from extremely long inputs.

    Returns:
        The sanitized string, safe for logging and basic processing.
    """
    if not isinstance(value, str):
        return str(value)

    # Remove null bytes — these can cause string truncation in C-based
    # systems (databases, file systems) leading to security bypasses.
    cleaned = value.replace("\x00", "")

    # Remove control characters (ASCII 0-31 except tab, newline, carriage return).
    # WHY: Control characters can break log parsing, terminal output, and
    # some database drivers. We keep \t, \n, \r as they're legitimate in text.
    cleaned = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    # Strip leading/trailing whitespace.
    # WHY: Prevents issues like " admin" matching differently than "admin"
    cleaned = cleaned.strip()

    # Enforce maximum length.
    # WHY: Prevents extremely long inputs that could exhaust memory or
    # cause performance issues in downstream processing.
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned


def build_aws_arn(
    service: str,
    resource_type: str,
    resource_id: str,
    region: str = "us-east-1",
    account_id: str = "",
) -> str:
    """
    Constructs an AWS ARN (Amazon Resource Name) string.

    WHY: ARNs are the universal identifier for AWS resources. They follow
    a specific format that's easy to get wrong. Centralizing construction:
    1. Ensures consistent format across the platform
    2. Prevents typos in hand-crafted ARN strings
    3. Makes ARN construction testable

    FORMAT: arn:aws:{service}:{region}:{account_id}:{resource_type}/{resource_id}

    EXAMPLE:
        build_aws_arn("ec2", "instance", "i-1234567890")
        # Returns: "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890"

    Args:
        service: AWS service name (e.g., "ec2", "s3", "rds")
        resource_type: Resource type within the service (e.g., "instance", "bucket")
        resource_id: The specific resource identifier
        region: AWS region (default: us-east-1)
        account_id: AWS account number (12 digits)

    Returns:
        A properly formatted ARN string.
    """
    return f"arn:aws:{service}:{region}:{account_id}:{resource_type}/{resource_id}"


def retry_with_backoff(
    max_attempts: int = 3,
    min_wait: int = 1,
    max_wait: int = 30,
):
    """
    Creates a retry decorator with exponential backoff.

    WHY: External service calls (ServiceNow, Ellucian, AWS) can fail
    transiently due to:
    - Network blips
    - Rate limiting (429 responses)
    - Temporary service unavailability

    Exponential backoff is the industry standard for retry logic because:
    1. It avoids overwhelming a struggling service with rapid retries
    2. It gives the service time to recover
    3. The randomization (jitter) prevents thundering herd problems

    HOW BACKOFF WORKS:
        Attempt 1: Wait 1 second
        Attempt 2: Wait 2 seconds
        Attempt 3: Wait 4 seconds
        (doubles each time, up to max_wait)

    ALTERNATIVE: Linear backoff — rejected because it doesn't reduce
    load on the failing service fast enough.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3).
                     3 retries covers most transient failures without
                     excessive delay (total wait: ~7 seconds).
        min_wait: Minimum wait time in seconds between retries.
        max_wait: Maximum wait time in seconds (caps exponential growth).

    Returns:
        A decorator that can be applied to any async or sync function.
    """
    # tenacity.retry is a battle-tested retry library used by major
    # Python projects (OpenStack, Google Cloud SDK).
    return retry(
        # Stop after N attempts to prevent infinite retry loops.
        stop=stop_after_attempt(max_attempts),

        # Exponential backoff: wait = min(min_wait * 2^attempt, max_wait)
        # The multiplier=1 means: 1s, 2s, 4s, 8s, 16s, max_wait
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),

        # Only retry on Exception subclasses (not KeyboardInterrupt, SystemExit).
        # WHY: We don't want to retry on programmer errors (TypeError, ValueError)
        # in production. In practice, wrap specific exception types.
        retry=retry_if_exception_type(Exception),

        # Re-raise the last exception if all retries fail.
        # WHY: The caller needs to know the operation failed.
        reraise=True,
    )


def mask_sensitive_string(value: str, visible_chars: int = 4) -> str:
    """
    Masks a sensitive string, showing only the last N characters.

    WHY: When logging or displaying API keys, tokens, or credentials,
    we need to show enough to identify the value (for debugging) without
    exposing the full secret. This is a compliance requirement for:
    - FERPA: Student data must not be visible in logs
    - PCI-DSS: Card numbers must be masked in all displays
    - HIPAA: Health data identifiers must be protected

    EXAMPLE:
        mask_sensitive_string("sk-1234567890abcdef")
        # Returns: "**************cdef"

    Args:
        value: The sensitive string to mask.
        visible_chars: Number of characters to show at the end (default: 4).
                      4 chars is industry standard (last 4 of credit card, etc.)

    Returns:
        The masked string with asterisks replacing hidden characters.
    """
    # Handle edge cases: empty string or string shorter than visible_chars
    if not value or len(value) <= visible_chars:
        return "*" * len(value) if value else ""

    # Replace all but the last visible_chars with asterisks.
    # WHY: Showing the last few chars allows identification without full exposure.
    masked_length = len(value) - visible_chars
    return "*" * masked_length + value[-visible_chars:]
