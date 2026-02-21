"""Geo-aware request routing for multi-region LLM inference deployments.

Routes incoming inference requests to the closest healthy AWS region
based on client geo-location, with automatic failover to the next-nearest
region when the primary is degraded or unreachable.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RegionStatus(str, Enum):
    """Operational status of a deployment region."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"


@dataclass
class RegionConfig:
    """Static and dynamic configuration for a deployment region."""

    name: str
    latitude: float
    longitude: float
    endpoint: str
    weight: float = 1.0  # relative traffic weight
    status: RegionStatus = RegionStatus.HEALTHY
    current_load: float = 0.0       # 0.0 - 1.0
    avg_latency_ms: float = 0.0
    capacity: int = 1000            # max concurrent requests
    active_requests: int = 0
    last_health_check: float = field(default_factory=time.time)


@dataclass
class RouteDecision:
    """The result of a routing decision."""

    target_region: str
    target_endpoint: str
    estimated_latency_ms: float
    is_failover: bool = False
    failover_reason: str = ""
    alternatives: List[str] = field(default_factory=list)


# Default region configurations -- coordinates are approximate data centre locations.
_DEFAULT_REGIONS: Dict[str, RegionConfig] = {
    "us-east-1": RegionConfig(
        name="us-east-1",
        latitude=39.0438,
        longitude=-77.4874,
        endpoint="https://us-east-1.inference.netflix.internal",
    ),
    "us-west-2": RegionConfig(
        name="us-west-2",
        latitude=45.5945,
        longitude=-122.1562,
        endpoint="https://us-west-2.inference.netflix.internal",
    ),
    "eu-west-1": RegionConfig(
        name="eu-west-1",
        latitude=53.3331,
        longitude=-6.2489,
        endpoint="https://eu-west-1.inference.netflix.internal",
    ),
}

# Rough speed-of-light RTT factor: ~0.06 ms per km (fibre, one-way)
_MS_PER_KM = 0.06


class GeoRouter:
    """Geo-aware, latency-based request router.

    Parameters
    ----------
    regions:
        Mapping of region name to :class:`RegionConfig`.  Defaults to the
        three standard Netflix inference regions.
    health_check_interval_s:
        Seconds between region health re-evaluation (default 30).
    max_load_threshold:
        Region load ratio above which traffic will be diverted (default
        0.85).
    """

    def __init__(
        self,
        regions: Optional[Dict[str, RegionConfig]] = None,
        health_check_interval_s: float = 30.0,
        max_load_threshold: float = 0.85,
    ) -> None:
        self._regions: Dict[str, RegionConfig] = (
            {k: RegionConfig(**v.__dict__) for k, v in _DEFAULT_REGIONS.items()}
            if regions is None
            else dict(regions)
        )
        self._health_interval = health_check_interval_s
        self._max_load = max_load_threshold
        self._lock = threading.Lock()

        # IP-to-geo lookup cache (LRU-ish, bounded)
        self._geo_cache: Dict[str, Tuple[float, float]] = {}
        self._geo_cache_max = 10_000

        logger.info(
            "GeoRouter initialised with regions: %s",
            list(self._regions.keys()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route_request(
        self,
        client_ip: str,
        request: Optional[Dict[str, Any]] = None,
    ) -> RouteDecision:
        """Select the best region for a given client request.

        Parameters
        ----------
        client_ip:
            Client's source IP address, used for geo-lookup.
        request:
            Optional request metadata (unused in the default strategy but
            available for subclass overrides).
        """
        lat, lon = self._resolve_geo(client_ip)
        ranked = self._rank_regions(lat, lon)

        primary = ranked[0]
        alternatives = [r.name for r in ranked[1:]]

        with self._lock:
            cfg = self._regions[primary.name]

        # Attempt primary
        if self._is_routable(cfg):
            est_latency = self._estimate_latency(lat, lon, cfg)
            return RouteDecision(
                target_region=cfg.name,
                target_endpoint=cfg.endpoint,
                estimated_latency_ms=est_latency,
                alternatives=alternatives,
            )

        # Failover
        reason = self._failover_reason(cfg)
        for fallback_cfg in ranked[1:]:
            with self._lock:
                fb = self._regions[fallback_cfg.name]
            if self._is_routable(fb):
                est_latency = self._estimate_latency(lat, lon, fb)
                return RouteDecision(
                    target_region=fb.name,
                    target_endpoint=fb.endpoint,
                    estimated_latency_ms=est_latency,
                    is_failover=True,
                    failover_reason=reason,
                    alternatives=[
                        r.name for r in ranked if r.name != fb.name
                    ],
                )

        # All regions down -- return primary anyway so the caller can
        # surface a meaningful error.
        logger.error("All regions unhealthy; returning primary as last resort")
        return RouteDecision(
            target_region=primary.name,
            target_endpoint=self._regions[primary.name].endpoint,
            estimated_latency_ms=-1.0,
            is_failover=True,
            failover_reason="all_regions_unhealthy",
            alternatives=alternatives,
        )

    def get_nearest_region(
        self, latitude: float, longitude: float
    ) -> str:
        """Return the name of the geographically nearest **healthy** region."""
        ranked = self._rank_regions(latitude, longitude)
        for r in ranked:
            with self._lock:
                cfg = self._regions[r.name]
            if self._is_routable(cfg):
                return cfg.name
        # Fall back to closest regardless of health.
        return ranked[0].name

    def get_region_health(self) -> Dict[str, Dict[str, Any]]:
        """Return a snapshot of every region's health and load."""
        with self._lock:
            return {
                name: {
                    "status": cfg.status.value,
                    "current_load": cfg.current_load,
                    "avg_latency_ms": cfg.avg_latency_ms,
                    "active_requests": cfg.active_requests,
                    "capacity": cfg.capacity,
                }
                for name, cfg in self._regions.items()
            }

    def failover(self, from_region: str) -> Optional[str]:
        """Manually trigger failover from *from_region*.

        Marks the region as UNHEALTHY and returns the next best region
        name, or *None* if no healthy alternative exists.
        """
        with self._lock:
            if from_region not in self._regions:
                logger.warning("Unknown region for failover: %s", from_region)
                return None
            self._regions[from_region].status = RegionStatus.UNHEALTHY
            self._regions[from_region].last_health_check = time.time()

        # Pick the fallback with lowest load among healthy regions.
        with self._lock:
            candidates = [
                cfg
                for name, cfg in self._regions.items()
                if name != from_region and self._is_routable(cfg)
            ]

        if not candidates:
            logger.error("No healthy region available for failover from %s", from_region)
            return None

        best = min(candidates, key=lambda c: c.current_load)
        logger.info(
            "Failover from %s -> %s (load=%.2f)",
            from_region,
            best.name,
            best.current_load,
        )
        return best.name

    # ------------------------------------------------------------------
    # Region management
    # ------------------------------------------------------------------

    def update_region(
        self,
        region: str,
        *,
        status: Optional[RegionStatus] = None,
        load: Optional[float] = None,
        latency_ms: Optional[float] = None,
        active_requests: Optional[int] = None,
    ) -> None:
        """Update live operational metrics for a region."""
        with self._lock:
            cfg = self._regions.get(region)
            if cfg is None:
                logger.warning("update_region: unknown region %s", region)
                return
            if status is not None:
                cfg.status = status
            if load is not None:
                cfg.current_load = load
            if latency_ms is not None:
                cfg.avg_latency_ms = latency_ms
            if active_requests is not None:
                cfg.active_requests = active_requests
            cfg.last_health_check = time.time()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_geo(self, ip: str) -> Tuple[float, float]:
        """Map an IP address to (latitude, longitude).

        In production this would integrate with a GeoIP service (e.g.
        MaxMind).  Here we use a deterministic hash for reproducibility.
        """
        if ip in self._geo_cache:
            return self._geo_cache[ip]

        # Deterministic pseudo-geo from IP hash
        h = hash(ip)
        lat = ((h % 18000) / 100.0) - 90.0
        lon = (((h >> 16) % 36000) / 100.0) - 180.0

        # Bound cache size
        if len(self._geo_cache) >= self._geo_cache_max:
            # Evict oldest half (simple strategy)
            keys = list(self._geo_cache.keys())
            for k in keys[: len(keys) // 2]:
                del self._geo_cache[k]

        self._geo_cache[ip] = (lat, lon)
        return lat, lon

    def _rank_regions(
        self, latitude: float, longitude: float
    ) -> List[RegionConfig]:
        """Return regions sorted by estimated network latency (ascending)."""
        with self._lock:
            configs = list(self._regions.values())

        def _score(cfg: RegionConfig) -> float:
            distance_km = self._haversine(
                latitude, longitude, cfg.latitude, cfg.longitude
            )
            network_ms = distance_km * _MS_PER_KM * 2  # RTT
            # Blend in current load as a penalty (up to +50 ms at 100% load)
            load_penalty = cfg.current_load * 50.0
            return network_ms + load_penalty

        configs.sort(key=_score)
        return configs

    def _estimate_latency(
        self, lat: float, lon: float, cfg: RegionConfig
    ) -> float:
        """Estimate RTT to a region in milliseconds."""
        distance_km = self._haversine(lat, lon, cfg.latitude, cfg.longitude)
        return round(distance_km * _MS_PER_KM * 2, 2)

    def _is_routable(self, cfg: RegionConfig) -> bool:
        """Determine whether a region should receive traffic."""
        if cfg.status in (RegionStatus.UNHEALTHY, RegionStatus.DRAINING):
            return False
        if cfg.current_load > self._max_load and cfg.status != RegionStatus.HEALTHY:
            return False
        return True

    @staticmethod
    def _failover_reason(cfg: RegionConfig) -> str:
        if cfg.status == RegionStatus.UNHEALTHY:
            return "region_unhealthy"
        if cfg.status == RegionStatus.DRAINING:
            return "region_draining"
        if cfg.current_load > 0.85:
            return "region_overloaded"
        return "unknown"

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance between two points in kilometres."""
        R = 6371.0  # Earth radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
