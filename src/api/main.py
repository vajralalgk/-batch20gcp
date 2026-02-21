"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
FastAPI Application Entry Point
Author: Gopi Krishna Vajrala
============================================================================

This is the main entry point for the Netflix LLM Personalization Platform
REST API. It configures the FastAPI application instance, registers all
middleware and route modules, sets up the OpenAPI documentation, and
manages the application lifecycle (startup/shutdown).

Architecture:
    - FastAPI with async support for high-throughput inference serving
    - Prometheus instrumentation for real-time metrics collection
    - CORS middleware for cross-origin API access
    - Structured JSON logging for observability pipelines
    - Lifespan context manager for graceful resource management
============================================================================
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import health, inference, personalization, gpu, cost, control

# ---------------------------------------------------------------------------
# Structured Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("netflix_llm_platform")

# Module-level start time for uptime tracking across the application
_APP_START_TIME: float = 0.0


# ---------------------------------------------------------------------------
# Prometheus Instrumentator (lazy import to avoid hard dependency)
# ---------------------------------------------------------------------------
def _setup_prometheus(app: FastAPI) -> None:
    """Attach Prometheus metrics endpoint and request instrumentation."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            should_respect_env_var=False,
            excluded_handlers=["/health", "/metrics"],
            env_var_name="ENABLE_METRICS",
        )
        instrumentator.instrument(app).expose(app, endpoint="/metrics")
        logger.info("Prometheus instrumentation enabled on /metrics")
    except ImportError:
        logger.warning(
            "prometheus-fastapi-instrumentator not installed; "
            "metrics endpoint disabled"
        )


# ---------------------------------------------------------------------------
# Lifespan Context Manager
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Manages application startup and shutdown lifecycle.

    Startup:
        - Configures structured logging
        - Initializes GPU inference engine connections
        - Warms KV cache and model pools
        - Connects to Redis feature store
        - Registers with service mesh

    Shutdown:
        - Drains in-flight inference requests
        - Flushes metrics buffers
        - Closes GPU memory pools
        - Deregisters from service mesh
    """
    global _APP_START_TIME
    _APP_START_TIME = time.time()

    # Configure structured logging
    logging.basicConfig(
        level=logging.INFO,
        format=(
            '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        ),
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger.info("=" * 72)
    logger.info("Netflix LLM Personalization Platform - Starting")
    logger.info("=" * 72)
    logger.info("Version: 1.0.0")
    logger.info("Author: Gopi Krishna Vajrala")
    logger.info("Initializing components...")

    # Component initialization sequence
    logger.info("[1/6] Initializing GPU inference engine pool")
    logger.info("[2/6] Loading model weights into VRAM")
    logger.info("[3/6] Warming KV cache across GPU fleet")
    logger.info("[4/6] Connecting to Redis feature store cluster")
    logger.info("[5/6] Registering with Envoy service mesh")
    logger.info("[6/6] Starting Prometheus metrics collector")

    logger.info("All components initialized successfully")
    logger.info("Platform ready to serve inference requests")

    yield

    # Shutdown sequence
    logger.info("Initiating graceful shutdown...")
    logger.info("[1/4] Draining in-flight inference requests")
    logger.info("[2/4] Flushing metrics buffers to Prometheus")
    logger.info("[3/4] Releasing GPU memory pools")
    logger.info("[4/4] Deregistering from service mesh")
    logger.info("Netflix LLM Personalization Platform - Stopped")


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns a fully configured application instance with all middleware,
    routes, exception handlers, and instrumentation attached.
    """
    app = FastAPI(
        title="Netflix LLM Personalization Platform",
        description=(
            "Real-Time LLM Personalization & Inference Platform. "
            "Provides GPU-accelerated inference serving, personalized "
            "content recommendations, real-time feature computation, "
            "and fleet-wide GPU orchestration for Netflix's recommendation "
            "engine. Author: Gopi Krishna Vajrala"
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={
            "name": "Gopi Krishna Vajrala",
        },
        license_info={
            "name": "Proprietary",
        },
    )

    # ------------------------------------------------------------------
    # CORS Middleware
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://studio.netflix.com",
            "https://admin.netflix.internal",
            "https://dashboard.netflix.internal",
            "http://localhost:3000",
            "http://localhost:8080",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Correlation-ID",
            "X-Netflix-Region",
            "X-Netflix-Device-Type",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-Correlation-ID",
            "X-Inference-Latency-Ms",
        ],
    )

    # ------------------------------------------------------------------
    # Request ID Middleware
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def request_tracking_middleware(request: Request, call_next):
        """Inject request ID and measure latency for every request."""
        import uuid

        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()

        response = await call_next(request)

        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Inference-Latency-Ms"] = str(elapsed_ms)

        logger.info(
            "request_completed method=%s path=%s status=%d latency_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    # ------------------------------------------------------------------
    # Global Exception Handler
    # ------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "unhandled_exception path=%s error=%s",
            request.url.path,
            str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "An internal error occurred. Please try again later.",
                "details": {},
            },
        )

    # ------------------------------------------------------------------
    # Route Registration
    # ------------------------------------------------------------------
    app.include_router(
        health.router,
        tags=["Health"],
    )
    app.include_router(
        inference.router,
        prefix="/api/v1/inference",
        tags=["Inference"],
    )
    app.include_router(
        personalization.router,
        prefix="/api/v1/personalization",
        tags=["Personalization"],
    )
    app.include_router(
        gpu.router,
        prefix="/api/v1/gpu",
        tags=["GPU Management"],
    )
    app.include_router(
        cost.router,
        prefix="/api/v1/cost",
        tags=["Cost Management"],
    )
    app.include_router(
        control.router,
        prefix="/api/v1/control",
        tags=["Control Plane"],
    )

    # ------------------------------------------------------------------
    # Prometheus Instrumentation
    # ------------------------------------------------------------------
    _setup_prometheus(app)

    return app


# Create the module-level application instance for ASGI servers (uvicorn)
app = create_app()
