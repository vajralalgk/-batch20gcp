"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
FastAPI Application Entry Point
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    This is the main entry point for the ECTP REST API. It:
    1. Creates and configures the FastAPI application instance
    2. Registers middleware (CORS, logging, error handling)
    3. Mounts all API route modules
    4. Configures the OpenAPI documentation
    5. Sets up application lifecycle events (startup/shutdown)

DESIGN DECISIONS:
    - FastAPI chosen over Flask/Django for: async support, automatic OpenAPI docs,
      Pydantic integration, and superior performance benchmarks
    - Application factory pattern for testability (create_app function)
    - Middleware stack ordered carefully: CORS first, then auth, then logging
    - Health check endpoint at root for ALB/ECS health probes

SECURITY IMPLICATIONS:
    - CORS restricts which frontends can call this API
    - Global exception handler prevents stack trace leakage
    - Request ID middleware enables audit trail correlation

ALTERNATIVES CONSIDERED:
    - Flask: Synchronous, less performant, manual OpenAPI setup
    - Django REST Framework: Heavier, ORM-coupled, slower for APIs
    - Starlette: FastAPI is built on Starlette, provides more features
============================================================================
"""

from contextlib import asynccontextmanager  # Manages app startup/shutdown lifecycle
from typing import AsyncGenerator  # Type hint for async generator functions

from fastapi import FastAPI, Request  # FastAPI framework and request object
from fastapi.middleware.cors import CORSMiddleware  # Cross-Origin Resource Sharing
from fastapi.responses import JSONResponse  # JSON response builder

# Import our core modules
from src.core.config.settings import get_settings  # Centralized configuration
from src.core.logging.logger import (  # Structured logging
    get_logger,
    setup_logging,
    set_correlation_id,
)
from src.core.exceptions.handlers import ECTPBaseError  # Custom exception hierarchy
from src.core.utils.helpers import now_utc  # UTC timestamp utility

# Import API route modules
from src.api.routes import health, migration, servicenow, ellucian, cost_governance


# Initialize the logger for this module.
# WHY: We create the logger at module level so it's available to all functions.
# The name __name__ identifies log entries as coming from "src.api.main".
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Application lifecycle manager — handles startup and shutdown events.

    WHY: Enterprise applications need to:
    - Initialize resources at startup (DB connections, caches, service clients)
    - Clean up resources at shutdown (close connections, flush buffers)

    The @asynccontextmanager pattern is FastAPI's recommended approach
    (replacing the deprecated @app.on_event decorators).

    HOW IT WORKS:
    1. Code before 'yield' runs at startup
    2. The application serves requests while yielded
    3. Code after 'yield' runs at shutdown

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the application to serve requests.
    """
    # ---- STARTUP ----
    settings = get_settings()

    # Initialize structured logging before anything else.
    # WHY: We want all startup logs to be properly formatted.
    setup_logging(settings.log_level)

    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.env,
        host=settings.host,
        port=settings.port,
    )

    # Log configuration (non-sensitive values only)
    logger.info(
        "configuration_loaded",
        environment=settings.env,
        debug=settings.debug,
        log_level=settings.log_level,
        db_host=settings.db_host,
        redis_host=settings.redis_host,
        aws_region=settings.aws_region,
    )

    # TODO: Initialize database connection pool
    # TODO: Initialize Redis connection
    # TODO: Initialize AWS session
    # TODO: Verify external service connectivity

    logger.info("application_started", status="ready")

    # Yield control — the application serves requests here
    yield

    # ---- SHUTDOWN ----
    logger.info("application_shutting_down")

    # TODO: Close database connections
    # TODO: Close Redis connections
    # TODO: Flush log buffers

    logger.info("application_stopped")


def create_app() -> FastAPI:
    """
    Application factory — creates and configures the FastAPI application.

    WHY: The factory pattern enables:
    1. Multiple app instances for testing (each test gets a fresh app)
    2. Different configurations per environment
    3. Clean separation of app creation from app running
    4. Dependency injection for testing

    ALTERNATIVE: Module-level app = FastAPI() — works for simple apps,
    but prevents creating isolated test instances.

    Returns:
        A fully configured FastAPI application instance.
    """
    settings = get_settings()

    # Create the FastAPI application with metadata for OpenAPI docs.
    # WHY: OpenAPI docs are auto-generated from this metadata and serve as
    # living API documentation accessible at /docs (Swagger UI) and /redoc.
    app = FastAPI(
        title="Enterprise Cloud Transformation Platform",
        description=(
            "Organization-wide platform for cloud migration, ServiceNow ITSM "
            "integration, Ellucian Higher Ed modernization, and enterprise "
            "DevOps automation. Author: Gopi Krishna Vajrala"
        ),
        version=settings.app_version,
        # Disable docs in production to avoid information disclosure.
        # WHY: OpenAPI docs reveal all endpoints, parameters, and response schemas.
        # In production, this is a security risk (information disclosure).
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ---- MIDDLEWARE REGISTRATION ----
    # Middleware is processed in REVERSE order of registration.
    # So the LAST registered middleware runs FIRST on the request.

    # 1. CORS Middleware — Must be first (outermost) to handle preflight requests.
    # WHY: Browsers send OPTIONS preflight requests before actual API calls.
    # Without CORS middleware, the browser blocks the actual request.
    # SECURITY: Only configured origins can make cross-origin requests.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,  # Explicit origin list
        allow_credentials=True,  # Allow cookies/auth headers
        allow_methods=["*"],  # Allow all HTTP methods
        allow_headers=["*"],  # Allow all headers
    )

    # 2. Request correlation middleware
    # WHY: Every request gets a unique ID for tracing through logs.
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        """
        Assigns a correlation ID to every incoming request.

        HOW: Checks for X-Correlation-ID header (from upstream services).
        If not present, generates a new UUID. This ID is:
        1. Added to all log entries for this request
        2. Returned in the response headers
        3. Passed to downstream service calls

        WHY: Essential for distributed tracing. When a single user action
        triggers calls across multiple services, the correlation ID links
        all related log entries together.
        """
        # Check if upstream service provided a correlation ID
        correlation_id = request.headers.get("X-Correlation-ID")

        # Set the correlation ID (generates new one if none provided)
        cid = set_correlation_id(correlation_id)

        # Log the incoming request
        logger.info(
            "request_received",
            method=request.method,
            path=str(request.url.path),
            client_ip=request.client.host if request.client else "unknown",
        )

        # Process the request through the rest of the middleware stack
        response = await call_next(request)

        # Add correlation ID to response headers for client-side tracing
        response.headers["X-Correlation-ID"] = cid

        # Log the response
        logger.info(
            "request_completed",
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
        )

        return response

    # ---- GLOBAL EXCEPTION HANDLER ----
    # WHY: Catches all unhandled exceptions and returns consistent JSON errors.
    # Without this, FastAPI returns HTML error pages for unhandled exceptions.
    @app.exception_handler(ECTPBaseError)
    async def ectp_exception_handler(request: Request, exc: ECTPBaseError):
        """
        Handles all ECTP custom exceptions and returns JSON error responses.

        WHY: Ensures every error response has the same structure:
        {"error_code": "...", "message": "...", "details": {...}}

        This consistency is critical for API consumers — they can always
        parse errors the same way regardless of what went wrong.
        """
        # Log the error with full context for debugging
        logger.error(
            "request_error",
            error_code=exc.error_code,
            message=exc.message,
            status_code=exc.status_code,
            path=str(request.url.path),
        )

        # Return a structured JSON error response
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """
        Catches any unhandled exception as a last resort.

        WHY: Prevents stack traces from being returned to API consumers.
        Stack traces reveal internal code structure, file paths, and
        potentially sensitive data — all useful to attackers.

        SECURITY: Returns a generic message to the client while logging
        the full exception details for the development team.
        """
        # Log the full exception for debugging
        logger.exception(
            "unhandled_exception",
            error=str(exc),
            path=str(request.url.path),
        )

        # Return a generic error — NEVER expose internal details
        return JSONResponse(
            status_code=500,
            content={
                "error_code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Please try again later.",
                "details": {},
            },
        )

    # ---- ROUTE REGISTRATION ----
    # Mount all API route modules with their URL prefixes.
    # WHY: Organizing routes by domain (migration, servicenow, etc.)
    # keeps the codebase maintainable as the API grows.
    app.include_router(
        health.router,
        tags=["Health"],  # Groups endpoints in OpenAPI docs
    )
    app.include_router(
        migration.router,
        prefix="/api/v1/migration",  # URL prefix for all migration endpoints
        tags=["Cloud Migration"],
    )
    app.include_router(
        servicenow.router,
        prefix="/api/v1/servicenow",
        tags=["ServiceNow Integration"],
    )
    app.include_router(
        ellucian.router,
        prefix="/api/v1/ellucian",
        tags=["Ellucian Integration"],
    )
    app.include_router(
        cost_governance.router,
        prefix="/api/v1/cost",
        tags=["Cost Governance"],
    )

    return app


# Create the application instance.
# WHY: Uvicorn needs a module-level application object to serve.
# The factory function is called once at import time.
app = create_app()
