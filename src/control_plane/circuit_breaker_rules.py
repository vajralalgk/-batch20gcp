"""Circuit breaker trigger/action rules for the Netflix LLM Platform.

Production-level rule definitions complementing circuit_breaker.py. Each rule
specifies a Prometheus metric trigger, sustained-duration threshold, ordered
fallback chain, and recovery parameters.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .circuit_breaker import CircuitState

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerRule:
    """Declarative definition of a single circuit breaker rule."""

    name: str
    service: str  # triton, redis, dynamodb, feature_store, cross_region
    trigger_metric: str  # Prometheus metric name
    trigger_condition: str  # e.g., "p95 > 200ms sustained 30s"
    threshold_value: float
    sustained_seconds: int
    action: str  # What happens when the breaker trips
    fallback_chain: list[str]  # Ordered fallback options
    recovery_timeout_s: int
    half_open_probe_count: int


@dataclass
class ActiveFallback:
    """Tracks which fallback level is currently active for a given rule."""

    rule_name: str
    service: str
    fallback_level: int
    fallback_name: str
    activated_at: float = field(default_factory=time.time)
    requests_served: int = 0


@dataclass
class FallbackResponse:
    """Response wrapper returned by the fallback chain."""

    success: bool
    data: Any = None
    fallback_level: int = -1
    fallback_name: str = ""
    latency_ms: float = 0.0
    error: Optional[str] = None


# -- Production rule definitions --------------------------------------------

CIRCUIT_BREAKER_RULES: list[CircuitBreakerRule] = [
    CircuitBreakerRule(
        name="triton_inference_slow", service="triton",
        trigger_metric="triton_inference_latency_p95_ms",
        trigger_condition="p95 > 200ms sustained 30s", threshold_value=200.0,
        sustained_seconds=30, recovery_timeout_s=60, half_open_probe_count=5,
        action="Fallback to smaller model, then cached recs, then embedding similarity",
        fallback_chain=["smaller_model", "cached_recommendations", "embedding_similarity"],
    ),
    CircuitBreakerRule(
        name="triton_5xx_rate", service="triton",
        trigger_metric="triton_request_5xx_rate_percent",
        trigger_condition="5xx rate > 5% sustained 60s", threshold_value=5.0,
        sustained_seconds=60, recovery_timeout_s=120, half_open_probe_count=10,
        action="Open circuit; hedge all requests to secondary region",
        fallback_chain=["hedge_secondary_region", "cached_recommendations", "static_fallback"],
    ),
    CircuitBreakerRule(
        name="redis_session_timeout", service="redis",
        trigger_metric="redis_session_latency_p95_ms",
        trigger_condition="p95 > 50ms sustained 15s", threshold_value=50.0,
        sustained_seconds=15, recovery_timeout_s=30, half_open_probe_count=3,
        action="Switch to embedded LRU cache; serve degraded session context",
        fallback_chain=["embedded_lru_cache", "degraded_session", "static_fallback"],
    ),
    CircuitBreakerRule(
        name="dynamodb_feature_slow", service="dynamodb",
        trigger_metric="dynamodb_feature_latency_p95_ms",
        trigger_condition="p95 > 100ms sustained 30s", threshold_value=100.0,
        sustained_seconds=30, recovery_timeout_s=90, half_open_probe_count=5,
        action="Use cached features; stale features acceptable for 5 minutes",
        fallback_chain=["cached_features", "stale_features_5min", "popular_content"],
    ),
    CircuitBreakerRule(
        name="gpu_queue_overflow", service="triton",
        trigger_metric="triton_gpu_queue_depth",
        trigger_condition="queue depth > 200 sustained 10s", threshold_value=200.0,
        sustained_seconds=10, recovery_timeout_s=45, half_open_probe_count=3,
        action="Shed best-effort traffic; shrink batch size; emergency scale-up",
        fallback_chain=["shed_best_effort", "shrink_batch", "emergency_scale"],
    ),
    CircuitBreakerRule(
        name="cross_region_hedge_fail", service="cross_region",
        trigger_metric="cross_region_hedge_failure_count",
        trigger_condition="> 3 hedge failures in 10s", threshold_value=3.0,
        sustained_seconds=10, recovery_timeout_s=60, half_open_probe_count=2,
        action="Disable cross-region hedging; route all traffic to primary only",
        fallback_chain=["disable_hedging", "primary_only", "static_fallback"],
    ),
    CircuitBreakerRule(
        name="kv_cache_pressure", service="triton",
        trigger_metric="kv_cache_utilization_percent",
        trigger_condition="utilization > 95% sustained 30s", threshold_value=95.0,
        sustained_seconds=30, recovery_timeout_s=60, half_open_probe_count=5,
        action="Aggressive KV-cache eviction; reduce max_seq_len to free capacity",
        fallback_chain=["aggressive_eviction", "reduce_max_seq_len", "shed_long_sequences"],
    ),
    CircuitBreakerRule(
        name="model_accuracy_drop", service="feature_store",
        trigger_metric="model_accuracy_vs_baseline_pct",
        trigger_condition="accuracy < baseline - 5% sustained 300s", threshold_value=-5.0,
        sustained_seconds=300, recovery_timeout_s=600, half_open_probe_count=20,
        action="Auto-rollback to previous model version; alert on-call",
        fallback_chain=["auto_rollback_previous", "cached_recommendations", "popular_content"],
    ),
]


# -- Fallback chain executor ------------------------------------------------

class FallbackChain:
    """Executes an ordered chain of fallback strategies until one succeeds.

    Strategies are registered via ``register_strategy``.  Built-in names:
    llm_reranking, cached_recommendations, embedding_similarity,
    popular_content, static_fallback.
    """

    _STRATEGY_REGISTRY: dict[str, Any] = {}

    def __init__(self) -> None:
        self._execution_counts: dict[str, int] = {}

    @classmethod
    def register_strategy(cls, name: str, handler: Any) -> None:
        cls._STRATEGY_REGISTRY[name] = handler
        logger.info("Registered fallback strategy: %s", name)

    @classmethod
    def unregister_strategy(cls, name: str) -> None:
        cls._STRATEGY_REGISTRY.pop(name, None)

    def execute_fallback(self, chain: list[str], request: Any) -> FallbackResponse:
        """Try each fallback in *chain* order; return first success."""
        for level, name in enumerate(chain):
            handler = self._STRATEGY_REGISTRY.get(name)
            if handler is None:
                logger.warning("No handler for fallback '%s'; skipping", name)
                continue
            start = time.monotonic()
            try:
                result = handler(request)
                elapsed = (time.monotonic() - start) * 1000.0
                self._execution_counts[name] = self._execution_counts.get(name, 0) + 1
                logger.info("Fallback '%s' succeeded (level %d, %.1f ms)", name, level, elapsed)
                return FallbackResponse(
                    success=True, data=result, fallback_level=level,
                    fallback_name=name, latency_ms=elapsed,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed = (time.monotonic() - start) * 1000.0
                logger.warning("Fallback '%s' failed (level %d): %s", name, level, exc)
        return FallbackResponse(success=False, error="All fallbacks exhausted",
                                fallback_level=len(chain), fallback_name="none")

    def get_execution_counts(self) -> dict[str, int]:
        return dict(self._execution_counts)


# -- Orchestrator -----------------------------------------------------------

class CircuitBreakerOrchestrator:
    """Manages all circuit breakers across services, evaluates metrics, and
    coordinates state transitions with fallback activation."""

    def __init__(self, rules: Optional[list[CircuitBreakerRule]] = None) -> None:
        self._rules = rules or list(CIRCUIT_BREAKER_RULES)
        self._states: dict[str, CircuitState] = {r.name: CircuitState.CLOSED for r in self._rules}
        self._sustained_since: dict[str, Optional[float]] = {r.name: None for r in self._rules}
        self._active_fallbacks: dict[str, ActiveFallback] = {}
        self._transition_log: list[dict[str, Any]] = []
        self._fallback_chain = FallbackChain()

    def evaluate_all(self, current_metrics: Optional[dict[str, float]] = None) -> dict[str, CircuitState]:
        """Evaluate every rule against *current_metrics* and return states."""
        if current_metrics is None:
            return dict(self._states)
        now = time.time()
        for rule in self._rules:
            val = current_metrics.get(rule.trigger_metric)
            if val is None:
                continue
            breached = self._is_threshold_breached(rule, val)
            state = self._states[rule.name]
            if state == CircuitState.CLOSED:
                if breached:
                    if self._sustained_since[rule.name] is None:
                        self._sustained_since[rule.name] = now
                    elif now - self._sustained_since[rule.name] >= rule.sustained_seconds:
                        self._transition(rule.name, CircuitState.OPEN, now)
                else:
                    self._sustained_since[rule.name] = None
            elif state == CircuitState.OPEN:
                opened = self._last_transition_time(rule.name)
                if opened and now - opened >= rule.recovery_timeout_s:
                    self._transition(rule.name, CircuitState.HALF_OPEN, now)
            elif state == CircuitState.HALF_OPEN:
                if breached:
                    self._transition(rule.name, CircuitState.OPEN, now)
                else:
                    self._transition(rule.name, CircuitState.CLOSED, now)
                    self._sustained_since[rule.name] = None
                    self._active_fallbacks.pop(rule.name, None)
        return dict(self._states)

    def get_active_fallbacks(self) -> list[ActiveFallback]:
        """Return currently active fallbacks across all rules."""
        return list(self._active_fallbacks.values())

    def execute_fallback_for_rule(self, rule_name: str, request: Any) -> FallbackResponse:
        """Run the fallback chain for *rule_name*."""
        rule = self._rule_by_name(rule_name)
        if rule is None:
            return FallbackResponse(success=False, error=f"Unknown rule: {rule_name}")
        resp = self._fallback_chain.execute_fallback(rule.fallback_chain, request)
        if resp.success:
            self._active_fallbacks[rule_name] = ActiveFallback(
                rule_name=rule_name, service=rule.service,
                fallback_level=resp.fallback_level, fallback_name=resp.fallback_name,
            )
        return resp

    def get_transition_log(self) -> list[dict[str, Any]]:
        return list(self._transition_log)

    def get_state(self, rule_name: str) -> Optional[CircuitState]:
        return self._states.get(rule_name)

    def get_rules_for_service(self, service: str) -> list[CircuitBreakerRule]:
        return [r for r in self._rules if r.service == service]

    @staticmethod
    def _is_threshold_breached(rule: CircuitBreakerRule, value: float) -> bool:
        if rule.threshold_value < 0:
            return value < rule.threshold_value
        return value > rule.threshold_value

    def _transition(self, rule_name: str, new_state: CircuitState, ts: float) -> None:
        old = self._states[rule_name]
        self._states[rule_name] = new_state
        self._transition_log.append(
            {"rule": rule_name, "from": old.value, "to": new_state.value, "timestamp": ts})
        logger.info("Circuit [%s]: %s -> %s", rule_name, old.value, new_state.value)

    def _last_transition_time(self, rule_name: str) -> Optional[float]:
        for entry in reversed(self._transition_log):
            if entry["rule"] == rule_name:
                return entry["timestamp"]
        return None

    def _rule_by_name(self, name: str) -> Optional[CircuitBreakerRule]:
        for r in self._rules:
            if r.name == name:
                return r
        return None
