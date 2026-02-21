"""Netflix LLM Platform - Structured Logging.

Provides JSON-formatted structured logging via ``structlog`` with
automatic injection of GPU context fields (device ID, memory usage,
batch metrics) alongside standard request metadata.

Usage::

    from src.core.logging import get_logger, setup_logging

    setup_logging(environment="production")
    logger = get_logger(__name__)
    logger.info("inference_complete", model="llama-3-70b", latency_ms=42.3)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, Optional

import structlog


# ---------------------------------------------------------------------------
# Custom structlog processors
# ---------------------------------------------------------------------------

def _add_gpu_context(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Inject GPU-related context fields when running on a GPU host.

    Fields added (when available):
      - ``gpu_device_id``: Active CUDA device ordinal
      - ``gpu_memory_used_mb``: Current GPU memory usage in MiB
      - ``gpu_memory_total_mb``: Total GPU memory in MiB
      - ``gpu_utilization_pct``: GPU compute utilisation percentage
    """
    try:
        import torch

        if torch.cuda.is_available():
            device_id = torch.cuda.current_device()
            mem_info = torch.cuda.mem_get_info(device_id)
            free_mb = mem_info[0] / (1024 * 1024)
            total_mb = mem_info[1] / (1024 * 1024)
            used_mb = total_mb - free_mb

            event_dict["gpu_device_id"] = device_id
            event_dict["gpu_memory_used_mb"] = round(used_mb, 1)
            event_dict["gpu_memory_total_mb"] = round(total_mb, 1)
            event_dict["gpu_utilization_pct"] = round(
                (used_mb / total_mb) * 100, 1
            ) if total_mb > 0 else 0.0
    except (ImportError, RuntimeError):
        # torch unavailable or CUDA not initialised -- skip silently.
        pass

    return event_dict


def _add_service_context(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Inject service identity fields into every log event."""
    event_dict.setdefault("service", os.getenv("APP_NAME", "netflix-llm-platform"))
    event_dict.setdefault("environment", os.getenv("APP_ENVIRONMENT", "development"))
    event_dict.setdefault("region", os.getenv("REGION_PRIMARY_REGION", "us-east-1"))
    event_dict.setdefault("pid", os.getpid())
    return event_dict


def _add_timestamp(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Add an ISO-8601 timestamp to the event dict."""
    event_dict["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime())
    return event_dict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    environment: Optional[str] = None,
    log_level: Optional[str] = None,
    enable_gpu_context: bool = True,
) -> None:
    """Configure the global logging pipeline.

    Args:
        environment: Runtime environment (``development`` | ``staging`` |
            ``production``). Defaults to the ``APP_ENVIRONMENT`` env var.
        log_level: Python log level name (e.g. ``DEBUG``, ``INFO``).
            Defaults to ``DEBUG`` in development, ``INFO`` otherwise.
        enable_gpu_context: When ``True``, GPU memory/utilisation fields
            are injected into every log event.
    """
    if environment is None:
        environment = os.getenv("APP_ENVIRONMENT", "development")

    if log_level is None:
        log_level = "DEBUG" if environment == "development" else "INFO"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors applied to every event.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _add_timestamp,
        _add_service_context,
    ]

    if enable_gpu_context:
        shared_processors.append(_add_gpu_context)

    shared_processors.append(structlog.stdlib.PositionalArgumentsFormatter())
    shared_processors.append(structlog.stdlib.StackInfoRenderer())
    shared_processors.append(structlog.stdlib.UnicodeDecoder())

    # Choose renderer based on environment.
    if environment == "development":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=True,
        )
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    # Suppress noisy third-party loggers in production.
    for noisy in ("urllib3", "botocore", "boto3", "grpc", "triton"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for the given module name.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A ``BoundLogger`` instance with structured context binding.
    """
    return structlog.get_logger(name)
