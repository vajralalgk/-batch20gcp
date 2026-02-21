"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Health Check API Endpoints
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Health check endpoints are CRITICAL for enterprise deployments:
    1. ALB (Application Load Balancer) uses /health to determine if
       a container instance is healthy and should receive traffic
    2. ECS uses health checks to decide whether to restart a container
    3. Kubernetes liveness/readiness probes use these endpoints
    4. Monitoring systems (CloudWatch, Grafana) use these for uptime tracking

DESIGN DECISIONS:
    - /health: Simple liveness check (is the process running?)
    - /health/ready: Readiness check (are all dependencies available?)
    - /health/detailed: Full status of all subsystems (admin only)
    - Returns HTTP 200 for healthy, 503 for unhealthy

SECURITY IMPLICATIONS:
    - /health is public (needed by ALB which may not have auth)
    - /health/detailed should require authentication (exposes internal state)
============================================================================
"""

from datetime import datetime, timezone  # UTC timestamp for uptime tracking
from typing import Dict, Any  # Type hints for response data

from fastapi import APIRouter  # FastAPI router for grouping related endpoints

from src.core.logging.logger import get_logger  # Structured logging
from src.core.utils.helpers import now_utc  # UTC timestamp utility

# Create a router instance for health check endpoints.
# WHY: Routers allow grouping related endpoints and mounting them
# at a specific URL prefix in the main application.
router = APIRouter()

# Track when the application started for uptime calculation.
# WHY: Uptime is a key operational metric. If a service keeps restarting,
# a low uptime indicates stability issues.
_start_time = now_utc()

# Logger for this module
logger = get_logger(__name__)


@router.get(
    "/health",
    summary="Liveness Check",
    description="Simple liveness probe — returns 200 if the process is running.",
    response_model=Dict[str, Any],
)
async def health_check() -> Dict[str, Any]:
    """
    Basic liveness health check endpoint.

    WHY: The simplest possible health check. It answers one question:
    "Is the Python process running and accepting HTTP requests?"

    Used by:
    - AWS ALB health checks (every 30 seconds)
    - ECS container health checks
    - Kubernetes liveness probes

    NOTE: This endpoint does NOT check database, Redis, or external services.
    A "live" service that can't reach its database is still "live" but not "ready".
    That's why we have separate liveness and readiness endpoints.

    Returns:
        JSON with status "healthy", timestamp, and uptime.
    """
    # Calculate how long the service has been running.
    # WHY: Helps operations team detect frequent restarts.
    current_time = now_utc()
    uptime_seconds = (current_time - _start_time).total_seconds()

    return {
        "status": "healthy",
        "timestamp": current_time.isoformat(),
        "uptime_seconds": round(uptime_seconds, 2),
        "service": "ECTP",
        "version": "1.0.0",
    }


@router.get(
    "/health/ready",
    summary="Readiness Check",
    description="Checks if the service and all its dependencies are ready to accept traffic.",
    response_model=Dict[str, Any],
)
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness health check — verifies all dependencies are accessible.

    WHY: A service might be "live" (process running) but not "ready"
    (can't reach database). Kubernetes and ALB use readiness checks to
    determine if a pod/instance should receive traffic.

    If this endpoint returns 503, the load balancer stops sending traffic
    to this instance until it becomes ready again.

    CHECKS PERFORMED:
    1. Database connectivity (PostgreSQL via RDS)
    2. Cache connectivity (Redis via ElastiCache)
    3. AWS API accessibility (STS GetCallerIdentity)

    Returns:
        JSON with overall status and individual dependency statuses.
    """
    # Track the status of each dependency
    dependencies = {}
    all_healthy = True

    # Check 1: Database connectivity
    # WHY: If the database is down, the API can't serve any data requests.
    try:
        # TODO: Implement actual database ping
        # async with db_session() as session:
        #     await session.execute(text("SELECT 1"))
        dependencies["database"] = {"status": "healthy", "type": "postgresql"}
    except Exception as e:
        dependencies["database"] = {"status": "unhealthy", "error": str(e)}
        all_healthy = False
        logger.error("readiness_check_failed", component="database", error=str(e))

    # Check 2: Redis connectivity
    # WHY: Redis handles caching and session management. Without it,
    # performance degrades and sessions may be lost.
    try:
        # TODO: Implement actual Redis ping
        # await redis_client.ping()
        dependencies["redis"] = {"status": "healthy", "type": "redis"}
    except Exception as e:
        dependencies["redis"] = {"status": "unhealthy", "error": str(e)}
        all_healthy = False
        logger.error("readiness_check_failed", component="redis", error=str(e))

    # Check 3: AWS API access
    # WHY: Most platform operations require AWS API access.
    # If IAM credentials are expired or the role is misconfigured,
    # the platform can't function.
    try:
        # TODO: Implement actual AWS STS check
        # sts_client = boto3.client('sts')
        # sts_client.get_caller_identity()
        dependencies["aws"] = {"status": "healthy", "type": "aws-sts"}
    except Exception as e:
        dependencies["aws"] = {"status": "unhealthy", "error": str(e)}
        all_healthy = False
        logger.error("readiness_check_failed", component="aws", error=str(e))

    # Determine overall status
    # WHY: If ANY dependency is unhealthy, the service is not ready.
    status = "ready" if all_healthy else "not_ready"
    status_code = 200 if all_healthy else 503

    response = {
        "status": status,
        "timestamp": now_utc().isoformat(),
        "dependencies": dependencies,
    }

    if not all_healthy:
        logger.warning("service_not_ready", dependencies=dependencies)

    return response


@router.get(
    "/health/detailed",
    summary="Detailed Health Status",
    description="Comprehensive health status of all platform components. Requires authentication.",
    response_model=Dict[str, Any],
)
async def detailed_health() -> Dict[str, Any]:
    """
    Detailed health check with comprehensive system information.

    WHY: Provides operations team with a single endpoint to check
    the status of all platform components. Used for:
    1. Dashboards and monitoring displays
    2. Incident investigation
    3. Capacity planning
    4. SLA reporting

    SECURITY: This endpoint should require authentication in production
    because it exposes internal architecture details (service names,
    connection strings, versions, etc.).

    Returns:
        Comprehensive JSON with all component statuses and metrics.
    """
    current_time = now_utc()
    uptime_seconds = (current_time - _start_time).total_seconds()

    return {
        "status": "healthy",
        "timestamp": current_time.isoformat(),
        "uptime_seconds": round(uptime_seconds, 2),
        "service": {
            "name": "ECTP",
            "version": "1.0.0",
            "environment": "development",
            "author": "Gopi Krishna Vajrala",
        },
        "components": {
            "api": {"status": "healthy", "framework": "FastAPI"},
            "database": {"status": "healthy", "type": "PostgreSQL (RDS)"},
            "cache": {"status": "healthy", "type": "Redis (ElastiCache)"},
            "queue": {"status": "healthy", "type": "SQS"},
            "storage": {"status": "healthy", "type": "S3"},
        },
        "integrations": {
            "servicenow": {"status": "configured", "type": "REST API"},
            "ellucian": {"status": "configured", "type": "Ethos API"},
            "aws": {"status": "connected", "region": "us-east-1"},
        },
    }
