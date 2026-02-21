"""Multi-layer health checking for the Netflix LLM Inference Platform.

Performs liveness, readiness, and deep-dependency checks across every
platform component -- API gateway, Triton Inference Server, GPU health,
Redis KV-cache, DynamoDB feature store, and inter-service networking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ComponentStatus(str, Enum):
    """Health status of an individual component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class OverallStatus(str, Enum):
    """Aggregate health status for the platform."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Health report for a single component."""

    name: str
    status: ComponentStatus
    latency_ms: float = 0.0
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    last_checked: float = field(default_factory=time.time)


@dataclass
class HealthReport:
    """Aggregate health report for the entire platform."""

    status: OverallStatus
    components: Dict[str, ComponentHealth]
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"

    @property
    def is_healthy(self) -> bool:
        return self.status == OverallStatus.HEALTHY

    @property
    def is_ready(self) -> bool:
        """Ready if no component is UNHEALTHY."""
        return all(
            c.status != ComponentStatus.UNHEALTHY
            for c in self.components.values()
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "version": self.version,
            "components": {
                name: {
                    "status": comp.status.value,
                    "latency_ms": comp.latency_ms,
                    "message": comp.message,
                    "details": comp.details,
                    "last_checked": comp.last_checked,
                }
                for name, comp in self.components.items()
            },
        }


class HealthChecker:
    """Multi-layer health checker for all platform subsystems.

    Parameters
    ----------
    api_url:
        Base URL of the FastAPI gateway (default ``http://localhost:8000``).
    triton_url:
        Triton Inference Server gRPC/HTTP endpoint (default
        ``localhost:8001``).
    redis_url:
        Redis connection string for KV-cache health checks.
    dynamodb_table:
        DynamoDB table name for the feature store check.
    timeout_seconds:
        Per-check timeout in seconds (default 5).
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        triton_url: str = "localhost:8001",
        redis_url: str = "redis://localhost:6379",
        dynamodb_table: str = "netflix-feature-store",
        timeout_seconds: float = 5.0,
    ) -> None:
        self._api_url = api_url
        self._triton_url = triton_url
        self._redis_url = redis_url
        self._dynamodb_table = dynamodb_table
        self._timeout = timeout_seconds

        # Optional external dependencies injected at runtime
        self._dcgm_exporter: Optional[Any] = None
        self._metrics_collector: Optional[Any] = None

        # Cache of most recent component results
        self._last_report: Optional[HealthReport] = None

        logger.info("HealthChecker initialised")

    # ------------------------------------------------------------------
    # Dependency injection
    # ------------------------------------------------------------------

    def set_dcgm_exporter(self, exporter: Any) -> None:
        """Inject a :class:`DCGMExporter` for GPU health checks."""
        self._dcgm_exporter = exporter

    def set_metrics_collector(self, collector: Any) -> None:
        """Inject a :class:`MetricsCollector` for metrics-based checks."""
        self._metrics_collector = collector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_health(self) -> HealthReport:
        """Lightweight liveness check.

        Verifies only that the API process is running and able to
        respond.  Suitable for Kubernetes liveness probes.
        """
        api = await self._check_api()
        components = {"api": api}

        status = self._aggregate_status(components)
        report = HealthReport(status=status, components=components)
        self._last_report = report
        return report

    async def check_readiness(self) -> HealthReport:
        """Readiness check including critical dependencies.

        Returns a report that considers the API gateway, inference
        backend, and GPU subsystem.  Suitable for Kubernetes readiness
        probes.
        """
        checks = await asyncio.gather(
            self._check_api(),
            self._check_inference(),
            self._check_gpu(),
            return_exceptions=True,
        )

        names = ["api", "inference", "gpu"]
        components: Dict[str, ComponentHealth] = {}
        for name, result in zip(names, checks):
            if isinstance(result, BaseException):
                components[name] = ComponentHealth(
                    name=name,
                    status=ComponentStatus.UNHEALTHY,
                    message=f"Check raised exception: {result}",
                )
            else:
                components[name] = result

        status = self._aggregate_status(components)
        report = HealthReport(status=status, components=components)
        self._last_report = report
        return report

    async def check_detailed(self) -> HealthReport:
        """Deep health check across every dependency.

        Includes API, inference server, GPU, Redis cache, DynamoDB
        feature store, and inter-service networking.
        """
        checks = await asyncio.gather(
            self._check_api(),
            self._check_inference(),
            self._check_gpu(),
            self._check_cache(),
            self._check_features(),
            self._check_networking(),
            return_exceptions=True,
        )

        names = ["api", "inference", "gpu", "cache", "features", "networking"]
        components: Dict[str, ComponentHealth] = {}
        for name, result in zip(names, checks):
            if isinstance(result, BaseException):
                components[name] = ComponentHealth(
                    name=name,
                    status=ComponentStatus.UNHEALTHY,
                    message=f"Check raised exception: {result}",
                )
            else:
                components[name] = result

        status = self._aggregate_status(components)
        report = HealthReport(status=status, components=components)
        self._last_report = report
        return report

    async def get_dependency_status(self) -> Dict[str, ComponentHealth]:
        """Return the status of every dependency without re-running checks.

        Falls back to a fresh :meth:`check_detailed` if no cached report
        exists.
        """
        if self._last_report is None:
            report = await self.check_detailed()
            return report.components
        return self._last_report.components

    # ------------------------------------------------------------------
    # Individual component checks
    # ------------------------------------------------------------------

    async def _check_api(self) -> ComponentHealth:
        """Check FastAPI gateway responsiveness."""
        start = time.monotonic()
        try:
            import aiohttp  # type: ignore[import-untyped]

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/health",
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    latency = (time.monotonic() - start) * 1000
                    if resp.status == 200:
                        return ComponentHealth(
                            name="api",
                            status=ComponentStatus.HEALTHY,
                            latency_ms=latency,
                            message="API gateway responding normally",
                        )
                    return ComponentHealth(
                        name="api",
                        status=ComponentStatus.DEGRADED,
                        latency_ms=latency,
                        message=f"API returned status {resp.status}",
                    )
        except ImportError:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="api",
                status=ComponentStatus.UNKNOWN,
                latency_ms=latency,
                message="aiohttp not installed -- cannot probe API",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="api",
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"API check failed: {exc}",
            )

    async def _check_inference(self) -> ComponentHealth:
        """Check Triton Inference Server readiness."""
        start = time.monotonic()
        try:
            import aiohttp  # type: ignore[import-untyped]

            # Triton HTTP health endpoint
            triton_http = self._triton_url
            if not triton_http.startswith("http"):
                triton_http = f"http://{triton_http}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{triton_http}/v2/health/ready",
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    latency = (time.monotonic() - start) * 1000
                    if resp.status == 200:
                        return ComponentHealth(
                            name="inference",
                            status=ComponentStatus.HEALTHY,
                            latency_ms=latency,
                            message="Triton Inference Server is ready",
                        )
                    return ComponentHealth(
                        name="inference",
                        status=ComponentStatus.DEGRADED,
                        latency_ms=latency,
                        message=f"Triton returned status {resp.status}",
                    )
        except ImportError:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="inference",
                status=ComponentStatus.UNKNOWN,
                latency_ms=latency,
                message="aiohttp not installed -- cannot probe Triton",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="inference",
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Triton check failed: {exc}",
            )

    async def _check_gpu(self) -> ComponentHealth:
        """Check GPU health via DCGM exporter."""
        start = time.monotonic()
        try:
            if self._dcgm_exporter is None:
                latency = (time.monotonic() - start) * 1000
                return ComponentHealth(
                    name="gpu",
                    status=ComponentStatus.UNKNOWN,
                    latency_ms=latency,
                    message="DCGM exporter not configured",
                )

            gpu_health = self._dcgm_exporter.get_gpu_health()
            latency = (time.monotonic() - start) * 1000

            unhealthy_gpus = [
                gid for gid, h in gpu_health.items()
                if h.status.value == "critical"
            ]
            degraded_gpus = [
                gid for gid, h in gpu_health.items()
                if h.status.value == "degraded"
            ]

            if unhealthy_gpus:
                return ComponentHealth(
                    name="gpu",
                    status=ComponentStatus.UNHEALTHY,
                    latency_ms=latency,
                    message=f"Critical GPU(s): {unhealthy_gpus}",
                    details={
                        "unhealthy_gpus": unhealthy_gpus,
                        "degraded_gpus": degraded_gpus,
                    },
                )
            if degraded_gpus:
                return ComponentHealth(
                    name="gpu",
                    status=ComponentStatus.DEGRADED,
                    latency_ms=latency,
                    message=f"Degraded GPU(s): {degraded_gpus}",
                    details={"degraded_gpus": degraded_gpus},
                )
            return ComponentHealth(
                name="gpu",
                status=ComponentStatus.HEALTHY,
                latency_ms=latency,
                message=f"All {len(gpu_health)} GPU(s) healthy",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="gpu",
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"GPU health check failed: {exc}",
            )

    async def _check_cache(self) -> ComponentHealth:
        """Check Redis KV-cache connectivity and latency."""
        start = time.monotonic()
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]

            client = aioredis.from_url(
                self._redis_url, socket_timeout=self._timeout
            )
            pong = await client.ping()
            await client.close()
            latency = (time.monotonic() - start) * 1000

            if pong:
                return ComponentHealth(
                    name="cache",
                    status=ComponentStatus.HEALTHY,
                    latency_ms=latency,
                    message="Redis PING successful",
                    details={"redis_url": self._redis_url},
                )
            return ComponentHealth(
                name="cache",
                status=ComponentStatus.DEGRADED,
                latency_ms=latency,
                message="Redis PING returned unexpected response",
            )
        except ImportError:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="cache",
                status=ComponentStatus.UNKNOWN,
                latency_ms=latency,
                message="redis.asyncio not installed -- cannot probe cache",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="cache",
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Redis check failed: {exc}",
            )

    async def _check_features(self) -> ComponentHealth:
        """Check DynamoDB feature store connectivity."""
        start = time.monotonic()
        try:
            import aiobotocore.session  # type: ignore[import-untyped]

            session = aiobotocore.session.get_session()
            async with session.create_client("dynamodb") as client:
                resp = await client.describe_table(TableName=self._dynamodb_table)
                latency = (time.monotonic() - start) * 1000
                table_status = resp["Table"]["TableStatus"]
                if table_status == "ACTIVE":
                    return ComponentHealth(
                        name="features",
                        status=ComponentStatus.HEALTHY,
                        latency_ms=latency,
                        message=f"DynamoDB table '{self._dynamodb_table}' is ACTIVE",
                        details={"table_status": table_status},
                    )
                return ComponentHealth(
                    name="features",
                    status=ComponentStatus.DEGRADED,
                    latency_ms=latency,
                    message=f"DynamoDB table status: {table_status}",
                    details={"table_status": table_status},
                )
        except ImportError:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="features",
                status=ComponentStatus.UNKNOWN,
                latency_ms=latency,
                message="aiobotocore not installed -- cannot probe DynamoDB",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="features",
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"DynamoDB check failed: {exc}",
            )

    async def _check_networking(self) -> ComponentHealth:
        """Basic TCP connectivity check to critical internal services."""
        start = time.monotonic()
        targets = [
            ("api", self._api_url.split("//")[-1].split("/")[0]),
            ("triton", self._triton_url.split("//")[-1].split("/")[0]),
        ]
        reachable: Dict[str, bool] = {}

        for label, hostport in targets:
            parts = hostport.rsplit(":", 1)
            host = parts[0]
            port = int(parts[1]) if len(parts) == 2 else 80
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=self._timeout,
                )
                writer.close()
                await writer.wait_closed()
                reachable[label] = True
            except Exception:
                reachable[label] = False

        latency = (time.monotonic() - start) * 1000
        all_ok = all(reachable.values())
        any_ok = any(reachable.values())

        if all_ok:
            status = ComponentStatus.HEALTHY
            msg = "All network endpoints reachable"
        elif any_ok:
            status = ComponentStatus.DEGRADED
            failed = [k for k, v in reachable.items() if not v]
            msg = f"Unreachable endpoints: {failed}"
        else:
            status = ComponentStatus.UNHEALTHY
            msg = "No network endpoints reachable"

        return ComponentHealth(
            name="networking",
            status=status,
            latency_ms=latency,
            message=msg,
            details={"reachable": reachable},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_status(
        components: Dict[str, ComponentHealth],
    ) -> OverallStatus:
        """Derive overall platform status from individual component statuses."""
        statuses = {c.status for c in components.values()}
        if ComponentStatus.UNHEALTHY in statuses:
            return OverallStatus.UNHEALTHY
        if ComponentStatus.DEGRADED in statuses or ComponentStatus.UNKNOWN in statuses:
            return OverallStatus.DEGRADED
        return OverallStatus.HEALTHY
