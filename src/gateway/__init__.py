"""Netflix Real-Time LLM Personalization & Inference Platform - Gateway Package.

Provides geo-aware routing, load balancing, rate limiting, and request
hedging for multi-region inference deployments.
"""

try:
    from src.gateway.geo_router import GeoRouter
except ImportError:
    GeoRouter = None  # type: ignore[assignment,misc]

try:
    from src.gateway.load_balancer import InferenceLoadBalancer
except ImportError:
    InferenceLoadBalancer = None  # type: ignore[assignment,misc]

try:
    from src.gateway.rate_limiter import AdaptiveRateLimiter
except ImportError:
    AdaptiveRateLimiter = None  # type: ignore[assignment,misc]

try:
    from src.gateway.request_hedger import RequestHedger
except ImportError:
    RequestHedger = None  # type: ignore[assignment,misc]

__all__ = [
    "GeoRouter",
    "InferenceLoadBalancer",
    "AdaptiveRateLimiter",
    "RequestHedger",
]
