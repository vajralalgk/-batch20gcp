# =============================================================================
# Netflix Real-Time LLM Personalization & Inference Platform - Dockerfile
# =============================================================================
# Multi-stage Docker build for the FastAPI inference gateway.
# Stage 1: Build Python dependencies in a lightweight builder image.
# Stage 2: Runtime on NVIDIA CUDA base for GPU-accelerated inference.
#
# Build: docker build -t netflix-llm-api:latest -f deployment/docker/Dockerfile .
# Run:   docker run --gpus all -p 8000:8000 netflix-llm-api:latest
# =============================================================================

# =============================================================================
# STAGE 1: BUILDER
# =============================================================================
# Install and compile all Python dependencies. Build tools are isolated here
# and excluded from the final production image.
# =============================================================================
FROM python:3.11-slim AS builder

LABEL maintainer="Netflix ML Platform" \
      org.opencontainers.image.title="Netflix LLM Platform Builder" \
      org.opencontainers.image.description="Builder stage for Netflix Real-Time LLM Inference Platform"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /build

# Install system-level build dependencies required for compiling Python packages
# with C/C++ extensions (e.g., grpcio, tritonclient, numpy).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libpq-dev \
        libffi-dev \
        libssl-dev \
        make \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching. If requirements.txt
# has not changed, the expensive pip install step is skipped on rebuilds.
COPY requirements.txt .

# Install Python dependencies into an isolated prefix so they can be cleanly
# copied into the production image without dragging along build tooling.
RUN pip install --upgrade pip && \
    pip install \
        --prefix=/install \
        --no-warn-script-location \
        -r requirements.txt

# =============================================================================
# STAGE 2: RUNTIME (NVIDIA CUDA)
# =============================================================================
# Minimal CUDA-enabled runtime image. Contains only the libraries needed to
# run the FastAPI inference gateway alongside NVIDIA Triton client calls.
# =============================================================================
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04 AS runtime

LABEL maintainer="Netflix ML Platform" \
      org.opencontainers.image.title="Netflix LLM Inference Platform" \
      org.opencontainers.image.description="Real-Time LLM Personalization & Inference Platform" \
      org.opencontainers.image.vendor="Netflix" \
      org.opencontainers.image.licenses="Proprietary"

ARG BUILD_DATE
ARG VCS_REF
ARG VERSION

LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${VERSION}"

# Install Python 3.11 and minimal runtime dependencies on the CUDA base image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3-pip \
        libpq5 \
        curl \
        tini \
        ca-certificates \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get purge -y --auto-remove \
    && rm -rf /tmp/* /var/tmp/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    APP_HOME="/app" \
    PORT=8000 \
    WORKERS=4 \
    ENVIRONMENT="production" \
    TRITON_GRPC_URL="triton:8001" \
    TRITON_HTTP_URL="triton:8002" \
    REDIS_URL="redis://redis:6379/0" \
    MODEL_REPOSITORY="/models" \
    CUDA_VISIBLE_DEVICES="all" \
    NVIDIA_VISIBLE_DEVICES="all" \
    NVIDIA_DRIVER_CAPABILITIES="compute,utility"

# Create non-root user for running the application process.
RUN groupadd --system --gid 1001 netflix && \
    useradd \
        --system \
        --uid 1001 \
        --gid netflix \
        --home-dir /app \
        --no-create-home \
        --shell /usr/sbin/nologin \
        netflix

RUN mkdir -p /app /models /app/logs /app/tmp && \
    chown -R netflix:netflix /app /models

WORKDIR /app

# Copy pre-built Python packages from the builder stage.
COPY --from=builder /install/lib/python3.11/site-packages /usr/local/lib/python3.11/dist-packages
COPY --from=builder /install/bin /usr/local/bin

# Copy application source code with proper ownership.
COPY --chown=netflix:netflix src/ /app/src/
COPY --chown=netflix:netflix pyproject.toml /app/pyproject.toml

# Security hardening: strip setuid/setgid bits and make source read-only.
RUN find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true
RUN chmod -R 555 /app/src/

# Expose ports:
#   8000 - FastAPI application (HTTP)
#   8001 - Triton gRPC (forwarded)
#   8002 - Triton HTTP (forwarded)
#   9090 - Prometheus metrics
EXPOSE 8000 8001 8002 9090

# Switch to non-root user before running the application.
USER netflix

# Health check verifies the FastAPI /health endpoint is responsive.
HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --retries=3 \
    --start-period=40s \
    CMD curl --fail --silent --show-error http://localhost:${PORT}/health || exit 1

# Use tini as PID 1 for proper signal handling and zombie process reaping.
ENTRYPOINT ["tini", "--"]

# Start the FastAPI application with uvicorn. Shell form allows environment
# variable substitution for PORT and WORKERS at runtime.
CMD ["sh", "-c", \
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
