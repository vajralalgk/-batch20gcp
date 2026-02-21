# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Production Dockerfile
# =============================================================================
# Author: Gopi Krishna Vajrala
# Description: Multi-stage Dockerfile for the ECTP FastAPI application.
#              Optimized for production with security hardening, non-root user,
#              health checks, and minimal attack surface. Uses multi-stage
#              builds to separate build dependencies from the runtime image,
#              resulting in a smaller, more secure production image.
#
# Stages:
#   1. builder  - Install Python dependencies and compile wheels
#   2. production - Minimal runtime image with only necessary components
#
# Build: docker build -t ectp-api:latest -f deployment/docker/Dockerfile .
# Run:   docker run -p 8000:8000 ectp-api:latest
# =============================================================================

# =============================================================================
# STAGE 1: BUILDER
# =============================================================================
# Purpose: Install all Python dependencies and build compiled wheels.
#          This stage includes build tools (gcc, etc.) that are NOT needed
#          at runtime. By isolating them here, the production image stays small.
# =============================================================================

# Use the official Python 3.11 slim image as the builder base.
# Slim variant is based on Debian and includes minimal OS packages.
# Pinning the exact Python version ensures reproducible builds.
FROM python:3.11-slim AS builder

# Set metadata labels following the OCI Image Spec for image identification.
# These labels provide information about the image for registry catalogs.
LABEL maintainer="Gopi Krishna Vajrala" \
      # Application name for identification in container registries
      org.opencontainers.image.title="ECTP API Builder" \
      # Brief description of the image purpose
      org.opencontainers.image.description="Builder stage for ECTP FastAPI application" \
      # Source repository URL for traceability back to the codebase
      org.opencontainers.image.source="https://github.com/ectp/ectp-api"

# Set environment variables that control Python and pip behavior during the build.
# These are optimized for Docker container builds, not interactive development.
ENV PYTHONDONTWRITEBYTECODE=1 \
    # Prevent Python from writing .pyc bytecode files to disk (reduces image size)
    PYTHONUNBUFFERED=1 \
    # Force Python stdout/stderr to be unbuffered for real-time log output in Docker
    PIP_NO_CACHE_DIR=1 \
    # Disable pip's cache directory to reduce image layer size
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Skip pip version check to speed up installation and avoid network calls
    PIP_DEFAULT_TIMEOUT=100
    # Set a generous timeout for pip downloads to handle slow network conditions

# Set the working directory for the builder stage.
# All subsequent commands will be relative to this directory.
WORKDIR /build

# Install system-level build dependencies required for compiling Python packages.
# Many Python packages (e.g., psycopg2, cryptography) need C/C++ compilation.
# These packages are only needed during the build, not at runtime.
RUN apt-get update && \
    # Install gcc and g++ for compiling C/C++ extensions in Python packages
    apt-get install -y --no-install-recommends \
        gcc \
        # GNU C compiler needed for building psycopg2 and other C extensions
        g++ \
        # GNU C++ compiler for packages requiring C++ compilation
        libpq-dev \
        # PostgreSQL development headers needed for psycopg2 binary compilation
        libffi-dev \
        # Foreign Function Interface library headers for cffi-based packages
        libssl-dev \
        # OpenSSL development headers for cryptography package compilation
        make \
        # Make build tool used by some package build systems
    && \
    # Clean up apt cache to reduce image layer size
    # This removes cached package lists that are no longer needed
    rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first to leverage Docker's layer caching.
# If requirements.txt hasn't changed, Docker reuses the cached layer and
# skips the expensive pip install step. This dramatically speeds up rebuilds.
COPY requirements.txt .

# Install Python dependencies as compiled wheel packages.
# Using --prefix=/install creates an isolated installation that can be
# cleanly copied to the production stage without dragging in build tools.
RUN pip install --upgrade pip && \
    # Upgrade pip to the latest version for best compatibility and performance
    pip install \
        # Install all packages listed in requirements.txt
        --prefix=/install \
        # Install to /install prefix so we can copy just the packages later
        --no-warn-script-location \
        # Suppress warnings about scripts not being on PATH (not relevant here)
        -r requirements.txt
        # Read package requirements from the requirements file

# =============================================================================
# STAGE 2: PRODUCTION
# =============================================================================
# Purpose: Create the minimal runtime image with only what's needed to run
#          the ECTP FastAPI application. No build tools, no dev packages,
#          no unnecessary system utilities. This reduces the attack surface
#          and keeps the image as small as possible.
# =============================================================================

# Start from a fresh Python 3.11 slim image for the production stage.
# This does NOT inherit any layers from the builder stage.
FROM python:3.11-slim AS production

# Set metadata labels for the production image following OCI conventions.
# These labels help identify and manage the image in registries and orchestrators.
LABEL maintainer="Gopi Krishna Vajrala" \
      # Application name for the production image
      org.opencontainers.image.title="ECTP API" \
      # Description of the production image
      org.opencontainers.image.description="Enterprise Cloud Transformation Platform - FastAPI Application" \
      # Source repository URL for traceability
      org.opencontainers.image.source="https://github.com/ectp/ectp-api" \
      # Vendor organization name
      org.opencontainers.image.vendor="ECTP" \
      # License identifier for the application
      org.opencontainers.image.licenses="Proprietary"

# Build arguments that can be passed at build time for version tracking.
# These are set by the CI pipeline to embed build metadata into the image.
ARG BUILD_DATE
# Timestamp of when the image was built, used for tracking and auditing
ARG VCS_REF
# Git commit SHA that this image was built from, for traceability
ARG VERSION
# Application version string for identification

# Add build metadata as labels using the build arguments.
# These labels enable querying image metadata without running the container.
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      # Timestamp when this image was created (set by CI pipeline)
      org.opencontainers.image.revision="${VCS_REF}" \
      # Git commit SHA for exact source code traceability
      org.opencontainers.image.version="${VERSION}"
      # Application version for deployment tracking

# Set production environment variables for Python runtime behavior.
# These ensure optimal performance and logging in containerized environments.
ENV PYTHONDONTWRITEBYTECODE=1 \
    # Prevent Python from writing .pyc files (not needed in containers)
    PYTHONUNBUFFERED=1 \
    # Ensure all Python output is sent directly to the terminal without buffering
    PYTHONPATH="/app/src" \
    # Add the application source directory to Python's module search path
    APP_HOME="/app" \
    # Define the application home directory for consistent path references
    PORT=8000 \
    # Default port the FastAPI application listens on (can be overridden)
    WORKERS=4 \
    # Number of Uvicorn worker processes (tune based on CPU cores)
    ENVIRONMENT="production"
    # Runtime environment identifier (production/staging/development)

# Install only the minimal runtime system dependencies needed to run the app.
# These are much fewer than the build dependencies - no compilers or headers.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # libpq5 is the PostgreSQL client library needed at runtime by psycopg2.
        # This is the runtime lib, NOT the -dev headers used during compilation.
        libpq5 \
        # curl is needed for the Docker HEALTHCHECK command to verify the app is alive.
        # It's also useful for debugging inside the container if needed.
        curl \
        # tini is a minimal init system for containers. It properly handles
        # signal forwarding (SIGTERM) and zombie process reaping, which PID 1
        # processes in containers must do but Python doesn't handle natively.
        tini \
    && \
    # Clean up the apt package cache to minimize the image size.
    # /var/lib/apt/lists/ contains cached package index files from apt-get update.
    rm -rf /var/lib/apt/lists/* && \
    # Remove the apt cache directory contents to further reduce size
    apt-get purge -y --auto-remove && \
    # Remove any packages that were installed as dependencies but are no longer needed
    rm -rf /tmp/* /var/tmp/*
    # Clean up temporary files that may have been created during package installation

# Create a non-root user and group for running the application.
# SECURITY: Running as root inside containers is a security anti-pattern.
# If the container is compromised, the attacker gets root-level access.
# Using a dedicated non-root user limits the blast radius of any breach.
RUN groupadd \
        # Create a system group (lower GID range) for the application
        --system \
        # Set the group ID explicitly for consistency across environments
        --gid 1001 \
        # Name the group 'ectp' for identification
        ectp && \
    useradd \
        # Create a system user (lower UID range) for the application
        --system \
        # Set the user ID explicitly for consistency across environments
        --uid 1001 \
        # Assign the user to the ectp group created above
        --gid ectp \
        # Set the home directory to the application directory
        --home-dir /app \
        # Don't create a home directory (we'll create it with proper permissions)
        --no-create-home \
        # Use nologin shell to prevent interactive login to this account
        --shell /usr/sbin/nologin \
        # Name the user 'ectp' for identification
        ectp

# Create the application directory with proper ownership.
# This directory will contain the application code and is owned by the ectp user.
RUN mkdir -p /app && \
    # Set ownership of the app directory to the non-root ectp user and group
    chown ectp:ectp /app

# Set the working directory to the application root.
# All subsequent COPY, RUN, and CMD instructions use this as the base path.
WORKDIR /app

# Copy the pre-built Python packages from the builder stage.
# This is the key multi-stage build optimization: we copy ONLY the installed
# packages, leaving behind all the build tools (gcc, make, headers, etc.).
# This significantly reduces the production image size and attack surface.
COPY --from=builder /install/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
# Copy installed library packages to the system Python's site-packages directory

# Copy any installed executable scripts (CLI tools from packages like uvicorn).
# These scripts are needed to run the application server.
COPY --from=builder /install/bin /usr/local/bin
# Copy executable scripts (uvicorn, gunicorn, etc.) to the system bin directory

# Copy the application source code into the container.
# The .dockerignore file should exclude unnecessary files (tests, docs, .git, etc.)
# to keep the COPY fast and the image small.
COPY --chown=ectp:ectp src/ /app/src/
# Copy the source code directory and set ownership to the non-root ectp user

# Copy the Alembic migration configuration and scripts for database migrations.
# These are needed for running migrations as part of the deployment process.
COPY --chown=ectp:ectp alembic/ /app/alembic/
# Copy the Alembic migrations directory with proper ownership

# Copy the Alembic configuration file for migration settings.
COPY --chown=ectp:ectp alembic.ini /app/alembic.ini
# Copy the Alembic INI config file that specifies migration settings

# Copy the application entry point and configuration files.
COPY --chown=ectp:ectp pyproject.toml /app/pyproject.toml
# Copy the Python project configuration file for metadata and tool settings

# SECURITY HARDENING: Remove unnecessary setuid/setgid binaries.
# These binaries can be exploited for privilege escalation if the container
# is compromised. Removing the setuid/setgid bits prevents this attack vector.
RUN find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true
# Find all files with setuid or setgid bits set and remove those permission bits

# SECURITY HARDENING: Make the application directory read-only where possible.
# The application code should not be modifiable at runtime.
RUN chmod -R 555 /app/src/
# Set source code to read-only and executable (for directory traversal)

# Create a directory for application logs with proper permissions.
# This directory is writable by the ectp user for runtime log output.
RUN mkdir -p /app/logs && \
    # Set ownership so the non-root user can write log files
    chown ectp:ectp /app/logs
    # Grant ownership of the logs directory to the application user

# Create a temporary directory for the application if needed.
# Some libraries require a writable temp directory at runtime.
RUN mkdir -p /app/tmp && \
    # Set ownership to the non-root user for temporary file creation
    chown ectp:ectp /app/tmp
    # Grant ownership of the tmp directory to the application user

# Switch to the non-root user for all subsequent instructions.
# SECURITY: From this point forward, everything runs as the ectp user (UID 1001).
# This includes the CMD/ENTRYPOINT, so the application process runs non-root.
USER ectp

# Expose the port that the FastAPI application listens on.
# This is a documentation hint for container orchestrators and operators.
# It does NOT actually publish the port - that's done with -p at runtime or in K8s.
EXPOSE ${PORT}

# Define the health check for container orchestration platforms.
# Docker, ECS, and Kubernetes use this to determine if the container is healthy.
# If the health check fails repeatedly, the orchestrator restarts the container.
HEALTHCHECK \
    # Check health every 30 seconds to balance responsiveness with overhead
    --interval=30s \
    # Wait up to 10 seconds for the health endpoint to respond
    --timeout=10s \
    # Allow 3 consecutive failures before marking the container as unhealthy
    --retries=3 \
    # Wait 40 seconds after container start before the first health check
    # This gives the application time to initialize and connect to dependencies
    --start-period=40s \
    # Use curl to check the /health endpoint. --fail returns non-zero on HTTP errors.
    CMD curl --fail --silent --show-error http://localhost:${PORT}/health || exit 1

# Use tini as the entrypoint (PID 1 init process).
# tini handles signal forwarding and zombie process reaping correctly.
# Without tini, Python running as PID 1 won't handle SIGTERM gracefully,
# causing slow shutdowns (Docker sends SIGKILL after 10s timeout).
ENTRYPOINT ["tini", "--"]

# Define the default command to start the FastAPI application with Uvicorn.
# Uvicorn is an ASGI server that runs the FastAPI application in production.
CMD ["sh", "-c", \
    # Use shell form to allow environment variable substitution at runtime.
    # This lets operators override WORKERS and PORT without rebuilding the image.
    "uvicorn \
        src.main:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers ${WORKERS} \
        --loop uvloop \
        --http httptools \
        --log-level info \
        --access-log \
        --proxy-headers \
        --forwarded-allow-ips='*'"]
    # src.main:app - Python module path to the FastAPI application instance
    # --host 0.0.0.0 - Listen on all network interfaces (required in containers)
    # --port ${PORT} - Port number from the environment variable (default 8000)
    # --workers ${WORKERS} - Number of worker processes from env var (default 4)
    # --loop uvloop - Use uvloop for faster async I/O (C-based event loop)
    # --http httptools - Use httptools for faster HTTP parsing (C-based parser)
    # --log-level info - Log level for Uvicorn's internal logging
    # --access-log - Enable access logging for request/response tracking
    # --proxy-headers - Trust X-Forwarded-* headers from reverse proxies (ALB/Nginx)
    # --forwarded-allow-ips='*' - Accept forwarded headers from any IP (behind ALB)
