"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
AWS Client Manager
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Centralizes AWS service client creation and management. Instead of
    every module creating its own boto3 clients (which wastes connections
    and makes configuration inconsistent), this module provides:
    1. Singleton client instances per service (connection reuse)
    2. Consistent configuration (region, retry settings, timeouts)
    3. Centralized error handling for AWS API failures
    4. Easy mocking for unit tests

DESIGN DECISIONS:
    - Factory pattern for client creation (get_client("s3"))
    - Clients are cached per service name (singleton per service)
    - Retry configuration applied globally (3 retries, exponential backoff)
    - Session management handles credential refresh automatically

SECURITY IMPLICATIONS:
    - Uses IAM roles (not access keys) when running in AWS
    - Credentials are never logged or stored in memory beyond boto3's cache
    - STS AssumeRole is used for cross-account access
    - All API calls are logged for CloudTrail audit

ALTERNATIVES CONSIDERED:
    - Direct boto3.client() calls everywhere: Inconsistent config, no reuse
    - AWS SDK for Pandas (awswrangler): Too data-focused, not general purpose
    - aiobotocore: Async boto3, but less mature and fewer features
============================================================================
"""

import boto3  # AWS SDK for Python — the official way to interact with AWS services
from botocore.config import Config as BotoConfig  # Configures retry behavior, timeouts
from typing import Any, Dict, Optional  # Type hints for function signatures

from src.core.config.settings import get_settings  # Our centralized configuration
from src.core.logging.logger import get_logger  # Structured logging
from src.core.exceptions.handlers import AWSServiceError  # Custom AWS exception

# Logger for this module — all AWS client operations are logged
logger = get_logger(__name__)

# Cache for boto3 clients — prevents creating duplicate clients for the same service.
# WHY: Each boto3 client maintains an HTTP connection pool. Creating multiple
# clients for the same service wastes connections and can hit OS file descriptor limits.
# Dict key is service name (e.g., "s3", "ec2"), value is the client object.
_client_cache: Dict[str, Any] = {}

# Cache for boto3 resource objects (higher-level API than clients)
_resource_cache: Dict[str, Any] = {}


def _get_boto_config() -> BotoConfig:
    """
    Creates the standard boto3 configuration used by all AWS clients.

    WHY: Consistent retry and timeout settings across all AWS API calls.
    Without this, some calls might timeout while others hang indefinitely,
    making the system unpredictable.

    CONFIGURATION EXPLAINED:
    - retries: 3 attempts with adaptive backoff (handles throttling automatically)
    - connect_timeout: 5 seconds to establish TCP connection
    - read_timeout: 30 seconds to receive a response
    - max_pool_connections: 25 concurrent connections per service

    Returns:
        A BotoConfig object with enterprise-standard settings.
    """
    return BotoConfig(
        # Retry configuration for transient failures.
        # 'adaptive' mode automatically adjusts retry delay based on
        # the type of error (throttling gets longer backoff).
        # WHY: AWS APIs have rate limits. Adaptive retries handle throttling
        # gracefully without overwhelming the service.
        retries={
            "max_attempts": 3,  # 3 attempts total (1 initial + 2 retries)
            "mode": "adaptive",  # Smart backoff for throttling errors
        },

        # Connection timeout: How long to wait for TCP handshake.
        # WHY: 5 seconds is generous enough for cross-region calls
        # but prevents hanging indefinitely on network issues.
        connect_timeout=5,

        # Read timeout: How long to wait for the API response.
        # WHY: Most AWS API calls respond within 1-5 seconds.
        # 30 seconds allows for slow operations (e.g., large S3 listings)
        # without blocking the event loop forever.
        read_timeout=30,

        # Connection pool size per service.
        # WHY: Limits concurrent connections to prevent overwhelming
        # the AWS endpoint or exhausting local file descriptors.
        # 25 is sufficient for most enterprise workloads.
        max_pool_connections=25,
    )


def get_session() -> boto3.Session:
    """
    Creates or returns a boto3 Session configured for ECTP.

    WHY: A Session is the starting point for all AWS interactions.
    It handles credential resolution in this order:
    1. Environment variables (AWS_ACCESS_KEY_ID, etc.)
    2. AWS config/credentials files (~/.aws/)
    3. IAM instance role (EC2/ECS task role) — preferred for production
    4. SSO credentials

    In production (ECS Fargate), the task IAM role provides credentials
    automatically — no access keys needed.

    Returns:
        A configured boto3 Session.
    """
    settings = get_settings()

    # Create a session with our configured region.
    # WHY: The region determines which AWS API endpoint is called.
    # us-east-1 is default for ECTP but configurable per environment.
    return boto3.Session(
        region_name=settings.aws_region,
    )


def get_client(service_name: str) -> Any:
    """
    Returns a cached boto3 client for the specified AWS service.

    WHY: Client creation involves credential resolution, HTTP connection
    setup, and endpoint discovery. Caching avoids repeating this work
    for every API call, improving performance significantly.

    USAGE:
        s3_client = get_client("s3")
        s3_client.list_buckets()

        ec2_client = get_client("ec2")
        ec2_client.describe_instances()

    Args:
        service_name: AWS service name (e.g., "s3", "ec2", "rds", "sts",
                     "cloudwatch", "secretsmanager", "sqs", "sns")

    Returns:
        A configured boto3 client for the specified service.

    Raises:
        AWSServiceError: If client creation fails (invalid service name,
                        credential issues, etc.)
    """
    # Check the cache first — return existing client if available.
    # WHY: Avoids creating duplicate clients with separate connection pools.
    if service_name in _client_cache:
        return _client_cache[service_name]

    try:
        # Create a new client with our standard configuration.
        session = get_session()
        client = session.client(
            service_name,
            config=_get_boto_config(),
        )

        # Cache the client for future use.
        _client_cache[service_name] = client

        logger.info(
            "aws_client_created",
            service=service_name,
            region=session.region_name,
        )

        return client

    except Exception as e:
        # Wrap the exception in our custom AWSServiceError for consistent handling.
        # WHY: Callers can catch AWSServiceError without knowing boto3 internals.
        logger.error(
            "aws_client_creation_failed",
            service=service_name,
            error=str(e),
        )
        raise AWSServiceError(
            message=f"Failed to create AWS client for {service_name}: {str(e)}",
            aws_service=service_name,
        )


def get_resource(service_name: str) -> Any:
    """
    Returns a cached boto3 resource for the specified AWS service.

    WHY: Resources are a higher-level API than clients. They provide
    object-oriented access to AWS services. For example:
    - Client: s3_client.get_object(Bucket="my-bucket", Key="file.txt")
    - Resource: s3_resource.Bucket("my-bucket").Object("file.txt").get()

    Resources are preferred for operations on individual AWS objects
    because the code is more readable and Pythonic.

    Args:
        service_name: AWS service name (e.g., "s3", "ec2", "dynamodb")

    Returns:
        A configured boto3 resource for the specified service.
    """
    if service_name in _resource_cache:
        return _resource_cache[service_name]

    try:
        session = get_session()
        resource = session.resource(
            service_name,
            config=_get_boto_config(),
        )

        _resource_cache[service_name] = resource

        logger.info(
            "aws_resource_created",
            service=service_name,
        )

        return resource

    except Exception as e:
        logger.error(
            "aws_resource_creation_failed",
            service=service_name,
            error=str(e),
        )
        raise AWSServiceError(
            message=f"Failed to create AWS resource for {service_name}: {str(e)}",
            aws_service=service_name,
        )


def verify_aws_connectivity() -> Dict[str, Any]:
    """
    Verifies AWS connectivity and returns caller identity.

    WHY: Called during application startup and health checks to ensure:
    1. AWS credentials are valid and not expired
    2. The correct IAM role is assumed
    3. The correct AWS account is being accessed
    4. Network connectivity to AWS APIs is working

    This prevents silent failures where the app starts successfully
    but can't actually call any AWS services.

    Returns:
        Dictionary with account ID, ARN, and user ID from STS.

    Raises:
        AWSServiceError: If AWS is unreachable or credentials are invalid.
    """
    try:
        # STS GetCallerIdentity is the lightest-weight AWS API call.
        # WHY: It requires no specific permissions, doesn't modify anything,
        # and confirms both credentials and network connectivity.
        sts_client = get_client("sts")
        identity = sts_client.get_caller_identity()

        result = {
            "account_id": identity["Account"],
            "arn": identity["Arn"],
            "user_id": identity["UserId"],
        }

        logger.info(
            "aws_connectivity_verified",
            account_id=result["account_id"],
            arn=result["arn"],
        )

        return result

    except Exception as e:
        logger.error("aws_connectivity_failed", error=str(e))
        raise AWSServiceError(
            message=f"AWS connectivity check failed: {str(e)}",
            aws_service="sts",
        )


def clear_client_cache() -> None:
    """
    Clears all cached boto3 clients and resources.

    WHY: Used for:
    1. Testing: Ensures each test gets fresh clients
    2. Credential rotation: Forces new clients with updated credentials
    3. Error recovery: If a client enters a bad state

    CAUTION: All subsequent AWS calls will create new clients,
    which is slower than using cached ones. Only call when necessary.
    """
    _client_cache.clear()
    _resource_cache.clear()
    logger.info("aws_client_cache_cleared")
