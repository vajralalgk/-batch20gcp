"""
Failure Scenario Simulation Engine for Netflix LLM Platform.

Simulates realistic production failure scenarios across GPU infrastructure,
networking, caching, and traffic layers. Each scenario includes detailed
trigger conditions, detection methods, mitigation steps, blast radius
analysis, and recovery timelines calibrated against real-world incidents.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Optional


class FailureScenario(enum.Enum):
    """Production failure scenarios observed in large-scale LLM serving."""

    GPU_OOM = "gpu_oom"
    GPU_THERMAL_THROTTLE = "gpu_thermal_throttle"
    GPU_ECC_ERROR = "gpu_ecc_uncorrectable"
    NVLINK_FAILURE = "nvlink_failure"
    KV_CACHE_OVERFLOW = "kv_cache_overflow"
    TRITON_CRASH = "triton_server_crash"
    REDIS_PARTITION = "redis_network_partition"
    REGION_FAILURE = "region_complete_failure"
    FEATURE_STORE_SLOW = "feature_store_degradation"
    TRAFFIC_SPIKE = "traffic_spike_3x"
    MODEL_CORRUPTION = "model_weight_corruption"
    COLD_START_STORM = "cold_start_storm"
    CASCADING_FAILURE = "cascading_timeout_failure"


@dataclass(frozen=True)
class TimelineEvent:
    """A discrete event within a failure simulation timeline."""

    timestamp_ms: int
    event: str
    component: str
    action: str

    def __repr__(self) -> str:
        return (
            f"[T+{self.timestamp_ms:>6d}ms] "
            f"{self.component:<24s} | {self.event}: {self.action}"
        )


@dataclass
class ImpactAssessment:
    """Quantified impact of a failure on system SLOs."""

    latency_p50_increase_pct: float = 0.0
    latency_p99_increase_pct: float = 0.0
    throughput_reduction_pct: float = 0.0
    availability_loss_pct: float = 0.0
    affected_request_count: int = 0
    affected_user_tiers: list[str] = field(default_factory=list)
    error_rate_increase_pct: float = 0.0


@dataclass
class BlastRadius:
    """Defines which components and user segments are affected."""

    affected_components: list[str] = field(default_factory=list)
    affected_regions: list[str] = field(default_factory=list)
    affected_user_tiers: list[str] = field(default_factory=list)
    affected_models: list[str] = field(default_factory=list)
    cascading_risk: bool = False
    cascading_targets: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    """Complete result of a failure scenario simulation."""

    scenario: FailureScenario
    trigger_conditions: str
    detection_method: str
    detection_time_ms: int
    impact_assessment: ImpactAssessment
    mitigation_steps: list[str]
    recovery_time_ms: int
    blast_radius: BlastRadius
    sla_impact_minutes: float
    timeline: list[TimelineEvent] = field(default_factory=list)
    simulation_wall_time_ms: float = 0.0

    def summary(self) -> str:
        """Return a human-readable summary of the simulation result."""
        lines = [
            f"{'=' * 72}",
            f"Scenario   : {self.scenario.value}",
            f"Trigger    : {self.trigger_conditions}",
            f"Detection  : {self.detection_method} ({self.detection_time_ms}ms)",
            f"Recovery   : {self.recovery_time_ms}ms total",
            f"SLA Impact : {self.sla_impact_minutes:.2f} minutes of error budget",
            f"Blast      : {', '.join(self.blast_radius.affected_components)}",
            f"{'=' * 72}",
            "Timeline:",
        ]
        for evt in self.timeline:
            lines.append(f"  {evt!r}")
        lines.append(f"{'=' * 72}")
        return "\n".join(lines)


class FailureSimulator:
    """
    Simulation engine that models production failure scenarios end-to-end.

    Each scenario is fully deterministic; no randomness. The timelines
    reflect median observed values from comparable GPU serving platforms
    running A100/H100 clusters behind Triton Inference Server.
    """

    _SCENARIO_HANDLERS: dict[FailureScenario, str] = {}

    def __init__(self, cluster_size: int = 256, gpus_per_node: int = 8) -> None:
        self._cluster_size = cluster_size
        self._gpus_per_node = gpus_per_node
        self._total_gpus = cluster_size * gpus_per_node
        # Register scenario handlers by convention: _simulate_<enum_value>
        self._SCENARIO_HANDLERS = {
            scenario: f"_simulate_{scenario.value}"
            for scenario in FailureScenario
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(self, scenario: FailureScenario) -> SimulationResult:
        """Run a single failure scenario and return the full result."""
        handler_name = self._SCENARIO_HANDLERS.get(scenario)
        if handler_name is None:
            raise ValueError(f"No handler registered for {scenario}")
        handler = getattr(self, handler_name, None)
        if handler is None:
            raise NotImplementedError(
                f"Scenario {scenario.value} handler not yet implemented"
            )
        t0 = time.monotonic()
        result: SimulationResult = handler()
        result.simulation_wall_time_ms = (time.monotonic() - t0) * 1000.0
        return result

    def run_all_scenarios(self) -> dict[str, SimulationResult]:
        """Execute every registered scenario and return results keyed by name."""
        results: dict[str, SimulationResult] = {}
        for scenario in FailureScenario:
            handler_name = self._SCENARIO_HANDLERS.get(scenario)
            if handler_name and hasattr(self, handler_name):
                results[scenario.value] = self.simulate(scenario)
        return results

    # ------------------------------------------------------------------
    # Scenario: GPU OOM during batch inference
    # ------------------------------------------------------------------

    def _simulate_gpu_oom(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "inference_engine",
                "Batch size 256 submitted with avg seq_len 4096 tokens"
            ),
            TimelineEvent(
                200, "MEMORY_PRESSURE", "gpu_memory_allocator",
                "KV cache allocation requests 78 GiB on 80 GiB A100; "
                "headroom drops below 2 GiB threshold"
            ),
            TimelineEvent(
                800, "EVICTION_START", "kv_cache_manager",
                "LRU eviction triggered; 12,400 cached prefixes released"
            ),
            TimelineEvent(
                2000, "ALERT_FIRED", "dcgm_exporter",
                "gpu_memory_used_bytes > 95% threshold; PagerDuty alert"
            ),
            TimelineEvent(
                2200, "BATCH_SHRINK", "adaptive_batcher",
                "Dynamic batch size reduced 256 -> 32; reject overflow to queue"
            ),
            TimelineEvent(
                2500, "LOAD_SHED", "request_router",
                "Best-effort tier requests shed; premium tier unaffected"
            ),
            TimelineEvent(
                4000, "MEMORY_STABLE", "gpu_memory_allocator",
                "Memory utilization stabilized at 68%; allocations succeeding"
            ),
            TimelineEvent(
                6000, "BATCH_RECOVER", "adaptive_batcher",
                "Batch size incrementally growing: 32 -> 64 -> 128"
            ),
            TimelineEvent(
                8000, "RESOLVED", "health_monitor",
                "All GPU memory metrics nominal; batch size restored to 192"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.GPU_OOM,
            trigger_conditions=(
                "Batch size 256 with average sequence length 4096 tokens "
                "exhausts KV cache on A100-80GB; memory allocator cannot "
                "satisfy new page requests"
            ),
            detection_method="DCGM Exporter memory utilization alert (>95%)",
            detection_time_ms=2000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=15.0,
                latency_p99_increase_pct=340.0,
                throughput_reduction_pct=75.0,
                availability_loss_pct=0.0,
                affected_request_count=8400,
                affected_user_tiers=["best_effort"],
                error_rate_increase_pct=12.0,
            ),
            mitigation_steps=[
                "1. Evict LRU KV cache entries to reclaim 14 GiB headroom",
                "2. Shrink dynamic batch size from 256 to 32",
                "3. Shed best-effort tier traffic at request router",
                "4. Incrementally restore batch size as memory stabilizes",
                "5. Resume best-effort traffic once batch >= 128",
            ],
            recovery_time_ms=8000,
            blast_radius=BlastRadius(
                affected_components=[
                    "kv_cache_manager", "adaptive_batcher",
                    "request_router", "inference_engine",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["best_effort"],
                affected_models=["llm-personalization-70b"],
                cascading_risk=True,
                cascading_targets=["request_queue", "upstream_microservices"],
            ),
            sla_impact_minutes=0.13,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Region complete failure
    # ------------------------------------------------------------------

    def _simulate_region_complete_failure(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "aws_infrastructure",
                "us-east-1 AZ-a and AZ-b simultaneous power event"
            ),
            TimelineEvent(
                500, "CONNECTION_DROP", "load_balancer",
                "Active connections to us-east-1 endpoints timing out"
            ),
            TimelineEvent(
                5000, "HEALTH_CHECK_FAIL", "route53_health_check",
                "3 consecutive health check failures on us-east-1 endpoint"
            ),
            TimelineEvent(
                15000, "DNS_FAILOVER", "route53",
                "DNS failover initiated; CNAME updated to us-west-2 endpoint "
                "with 15s TTL propagation"
            ),
            TimelineEvent(
                16000, "CONNECTION_DRAIN", "envoy_proxy",
                "Draining 34,200 in-flight connections; retry with backoff"
            ),
            TimelineEvent(
                20000, "TRAFFIC_SHIFT", "global_load_balancer",
                "100% traffic now routed to us-west-2; GPU utilization 78% -> 94%"
            ),
            TimelineEvent(
                25000, "AUTOSCALE_TRIGGER", "capacity_controller",
                "us-west-2 autoscaler adding 32 nodes from warm pool"
            ),
            TimelineEvent(
                30000, "WARM_POOL_READY", "fleet_scheduler",
                "32 warm-pool nodes online; GPU utilization back to 82%"
            ),
            TimelineEvent(
                45000, "RESOLVED", "health_monitor",
                "All SLOs met in us-west-2; region failover complete"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.REGION_FAILURE,
            trigger_conditions=(
                "us-east-1 availability zone failure affecting multiple AZs; "
                "all GPU nodes, Redis clusters, and load balancers in region "
                "become unreachable simultaneously"
            ),
            detection_method="Route53 health check failure (3 consecutive failures)",
            detection_time_ms=15000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=45.0,
                latency_p99_increase_pct=180.0,
                throughput_reduction_pct=50.0,
                availability_loss_pct=100.0,
                affected_request_count=142000,
                affected_user_tiers=["premium", "standard", "best_effort"],
                error_rate_increase_pct=100.0,
            ),
            mitigation_steps=[
                "1. Route53 automatic DNS failover to us-west-2 (15s TTL)",
                "2. Drain in-flight connections with graceful retry",
                "3. Shift 100% traffic to surviving region",
                "4. Activate warm pool nodes in us-west-2 to absorb load",
                "5. Monitor us-west-2 GPU utilization; shed best-effort if >92%",
                "6. Re-establish us-east-1 when AZ recovers; gradual traffic return",
            ],
            recovery_time_ms=45000,
            blast_radius=BlastRadius(
                affected_components=[
                    "inference_engine", "kv_cache_manager", "redis_cluster",
                    "triton_server", "load_balancer", "feature_store_cache",
                    "model_registry_cache",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["premium", "standard", "best_effort"],
                affected_models=[
                    "llm-personalization-70b", "llm-search-13b",
                    "llm-content-understanding-7b",
                ],
                cascading_risk=True,
                cascading_targets=[
                    "recommendation_service", "search_service",
                    "content_understanding_pipeline",
                ],
            ),
            sla_impact_minutes=0.75,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Cascading timeout failure
    # ------------------------------------------------------------------

    def _simulate_cascading_timeout_failure(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "feature_store",
                "Feature store Cassandra node GC pause; read latency spikes "
                "from 5ms to 1200ms"
            ),
            TimelineEvent(
                3000, "QUEUE_BACKUP", "request_queue",
                "Inference queue depth grows from 50 to 4,800; requests "
                "waiting on feature hydration"
            ),
            TimelineEvent(
                8000, "GPU_STARVATION", "inference_engine",
                "GPU batch formation stalled; utilization drops from 85% to 12% "
                "as pipeline bubbles propagate"
            ),
            TimelineEvent(
                15000, "TIMEOUT_CASCADE", "envoy_proxy",
                "Upstream timeout (2s) exceeded; 68% of in-flight requests "
                "returning HTTP 504"
            ),
            TimelineEvent(
                30000, "ALERT_FIRED", "prometheus_alertmanager",
                "p99 latency > 5000ms for 30s; cascading_failure runbook triggered"
            ),
            TimelineEvent(
                31000, "CIRCUIT_OPEN", "circuit_breaker",
                "Circuit breaker opens on feature_store dependency; "
                "requests routed to stale cache fallback"
            ),
            TimelineEvent(
                32000, "CACHE_FALLBACK", "feature_cache",
                "Serving from Redis feature cache (staleness < 5min); "
                "accuracy impact ~0.3% on recommendations"
            ),
            TimelineEvent(
                34000, "QUEUE_DRAIN", "request_queue",
                "Queue depth dropping: 4,800 -> 1,200 -> 300"
            ),
            TimelineEvent(
                38000, "GPU_RECOVERY", "inference_engine",
                "GPU utilization recovering: 12% -> 55% -> 78%"
            ),
            TimelineEvent(
                40000, "CIRCUIT_HALFOPEN", "circuit_breaker",
                "Circuit half-open; probing feature store with 5% traffic"
            ),
            TimelineEvent(
                42000, "FEATURE_STORE_RECOVERED", "feature_store",
                "Cassandra GC completed; read latency back to 5ms"
            ),
            TimelineEvent(
                45000, "RESOLVED", "health_monitor",
                "Circuit closed; full feature store traffic restored; "
                "all SLOs nominal"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.CASCADING_FAILURE,
            trigger_conditions=(
                "Feature store Cassandra cluster experiences long GC pause "
                "(1200ms read latency); pending requests accumulate in "
                "inference queue, starving GPUs of work and causing upstream "
                "timeouts to cascade through the serving stack"
            ),
            detection_method="Prometheus p99 latency alert (>5000ms for 30s)",
            detection_time_ms=30000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=280.0,
                latency_p99_increase_pct=1500.0,
                throughput_reduction_pct=85.0,
                availability_loss_pct=68.0,
                affected_request_count=52000,
                affected_user_tiers=["premium", "standard", "best_effort"],
                error_rate_increase_pct=68.0,
            ),
            mitigation_steps=[
                "1. Circuit breaker opens on feature store dependency",
                "2. Fall back to Redis-cached features (staleness < 5min)",
                "3. Shrink inference batch size to drain queue backlog",
                "4. Shed best-effort traffic if queue depth > 2000",
                "5. Half-open circuit; probe feature store with 5% traffic",
                "6. Gradually restore full feature store traffic on recovery",
            ],
            recovery_time_ms=45000,
            blast_radius=BlastRadius(
                affected_components=[
                    "feature_store", "request_queue", "inference_engine",
                    "envoy_proxy", "circuit_breaker", "feature_cache",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["premium", "standard", "best_effort"],
                affected_models=["llm-personalization-70b", "llm-search-13b"],
                cascading_risk=True,
                cascading_targets=[
                    "recommendation_service", "search_ranking",
                    "homepage_personalization",
                ],
            ),
            sla_impact_minutes=0.75,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Traffic spike 3x (Friday 8 PM)
    # ------------------------------------------------------------------

    def _simulate_traffic_spike_3x(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "traffic_ingress",
                "Friday 20:00 UTC; request rate surges from 45k RPS to 138k RPS "
                "in 5 minutes (popular new show release)"
            ),
            TimelineEvent(
                5000, "QUEUE_GROWTH", "request_queue",
                "Queue depth exceeds 10,000; queue wait time > 200ms"
            ),
            TimelineEvent(
                10000, "ALERT_FIRED", "prometheus_alertmanager",
                "queue_depth > 8000 for 10s; autoscale policy triggered"
            ),
            TimelineEvent(
                10500, "AUTOSCALE_START", "capacity_controller",
                "Requesting 48 nodes from warm pool (pre-loaded model snapshots)"
            ),
            TimelineEvent(
                14200, "WARM_POOL_ONLINE", "fleet_scheduler",
                "48 warm-pool nodes online in 4.2s (snapshot clone from NVMe); "
                "Triton health checks passing"
            ),
            TimelineEvent(
                15000, "BATCH_GROW", "adaptive_batcher",
                "Dynamic batch size increased: 64 -> 128 -> 192 to maximize "
                "GPU throughput on existing nodes"
            ),
            TimelineEvent(
                18000, "RATE_LIMIT", "request_router",
                "Best-effort tier rate-limited to 60% of normal allocation; "
                "premium tier unthrottled"
            ),
            TimelineEvent(
                25000, "QUEUE_STABILIZE", "request_queue",
                "Queue depth declining: 10,000 -> 3,200 -> 800"
            ),
            TimelineEvent(
                40000, "CAPACITY_NOMINAL", "capacity_controller",
                "Total cluster: 304 nodes (256 base + 48 warm pool); "
                "GPU utilization 82% across fleet"
            ),
            TimelineEvent(
                60000, "RESOLVED", "health_monitor",
                "All SLOs met; p99 latency < 150ms; queue depth < 100; "
                "rate limits relaxed"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.TRAFFIC_SPIKE,
            trigger_conditions=(
                "3x traffic surge in 5 minutes driven by popular new content "
                "release on Friday 20:00 UTC; request rate jumps from 45k to "
                "138k RPS across all model endpoints"
            ),
            detection_method="Queue depth alert (>8000 for 10s)",
            detection_time_ms=10000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=65.0,
                latency_p99_increase_pct=420.0,
                throughput_reduction_pct=0.0,
                availability_loss_pct=0.0,
                affected_request_count=28000,
                affected_user_tiers=["best_effort"],
                error_rate_increase_pct=4.0,
            ),
            mitigation_steps=[
                "1. Autoscale 48 nodes from warm pool (4.2s with snapshot clone)",
                "2. Increase dynamic batch size to maximize GPU throughput",
                "3. Rate-limit best-effort tier to protect premium SLOs",
                "4. Monitor GPU utilization; add more nodes if >90%",
                "5. Gradually relax rate limits as capacity stabilizes",
                "6. Post-spike: keep 24 warm-pool nodes for 2h cooldown",
            ],
            recovery_time_ms=60000,
            blast_radius=BlastRadius(
                affected_components=[
                    "request_queue", "adaptive_batcher", "request_router",
                    "capacity_controller", "fleet_scheduler",
                ],
                affected_regions=["us-east-1", "us-west-2"],
                affected_user_tiers=["best_effort"],
                affected_models=[
                    "llm-personalization-70b", "llm-search-13b",
                    "llm-content-understanding-7b",
                ],
                cascading_risk=False,
                cascading_targets=[],
            ),
            sla_impact_minutes=0.0,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Model weight corruption (bit flip)
    # ------------------------------------------------------------------

    def _simulate_model_weight_corruption(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "gpu_memory",
                "Uncorrectable bit flip in GPU HBM3 on node gpu-a100-147; "
                "affects attention layer 38 weight matrix"
            ),
            TimelineEvent(
                500, "ANOMALY_START", "inference_engine",
                "Output logits show increased entropy; top-k distribution "
                "diverges from baseline by 34%"
            ),
            TimelineEvent(
                10000, "QUALITY_DRIFT", "accuracy_monitor",
                "Rolling NDCG@10 drops from 0.72 to 0.58 on shadow traffic; "
                "below 0.65 alert threshold"
            ),
            TimelineEvent(
                60000, "ALERT_FIRED", "model_quality_service",
                "Accuracy degradation alert: NDCG@10 < 0.65 for 60s on "
                "node gpu-a100-147"
            ),
            TimelineEvent(
                61000, "NODE_ISOLATION", "fleet_scheduler",
                "Node gpu-a100-147 removed from serving pool; traffic "
                "redistributed to remaining 255 nodes"
            ),
            TimelineEvent(
                62000, "ROLLBACK_START", "model_deployer",
                "Initiating weight reload from last known-good checkpoint "
                "(v2.3.1-rc4) stored in S3"
            ),
            TimelineEvent(
                75000, "WEIGHT_DOWNLOAD", "model_loader",
                "Downloading 140 GiB model weights via S3 Express One Zone; "
                "throughput 11.2 GiB/s"
            ),
            TimelineEvent(
                85000, "WEIGHT_LOADED", "triton_server",
                "Model weights loaded into GPU memory; warm-up inference pass "
                "running on validation set"
            ),
            TimelineEvent(
                88000, "VALIDATION_PASS", "model_quality_service",
                "NDCG@10 = 0.73 on validation set; node cleared for serving"
            ),
            TimelineEvent(
                90000, "RESOLVED", "health_monitor",
                "Node gpu-a100-147 returned to serving pool; all quality "
                "metrics nominal"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.MODEL_CORRUPTION,
            trigger_conditions=(
                "Uncorrectable bit flip in GPU HBM3 memory corrupts a weight "
                "matrix in attention layer 38; ECC correction exhausted; "
                "inference outputs diverge from expected distribution"
            ),
            detection_method=(
                "Model accuracy monitoring service: rolling NDCG@10 drops "
                "below 0.65 threshold on shadow traffic evaluation (60s window)"
            ),
            detection_time_ms=60000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=0.0,
                latency_p99_increase_pct=0.0,
                throughput_reduction_pct=0.4,
                availability_loss_pct=0.0,
                affected_request_count=18000,
                affected_user_tiers=["premium", "standard", "best_effort"],
                error_rate_increase_pct=0.0,
            ),
            mitigation_steps=[
                "1. Isolate affected node from serving pool immediately",
                "2. Redistribute traffic to remaining healthy nodes",
                "3. Download last known-good checkpoint from S3 Express",
                "4. Reload model weights into GPU memory",
                "5. Run validation inference pass against golden dataset",
                "6. Return node to serving pool only after NDCG > 0.70",
                "7. File GPU RMA with NVIDIA if ECC errors recur",
            ],
            recovery_time_ms=90000,
            blast_radius=BlastRadius(
                affected_components=[
                    "inference_engine", "model_quality_service",
                    "model_deployer", "model_loader",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["premium", "standard", "best_effort"],
                affected_models=["llm-personalization-70b"],
                cascading_risk=False,
                cascading_targets=[],
            ),
            sla_impact_minutes=0.30,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Cold start storm
    # ------------------------------------------------------------------

    def _simulate_cold_start_storm(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "kubernetes",
                "Node replacement event: 8 GPU nodes (64 GPUs) cycling "
                "simultaneously due to kernel security patch rollout"
            ),
            TimelineEvent(
                1000, "PODS_TERMINATING", "kubelet",
                "64 Triton server pods entering Terminating state; "
                "graceful shutdown draining in-flight requests"
            ),
            TimelineEvent(
                3000, "CAPACITY_DROP", "capacity_controller",
                "Serving capacity reduced by 25% (64 of 256 nodes offline); "
                "queue depth rising"
            ),
            TimelineEvent(
                5000, "READINESS_FAIL", "kubernetes",
                "Readiness probes failing on new pods; model loading in progress"
            ),
            TimelineEvent(
                5500, "WARM_POOL_ACTIVATE", "fleet_scheduler",
                "Activating 16 warm-pool standby nodes to absorb displaced load"
            ),
            TimelineEvent(
                7000, "TRAFFIC_SHIFT", "global_load_balancer",
                "Shifting 15% traffic to us-west-2 to reduce us-east-1 pressure"
            ),
            TimelineEvent(
                10000, "SNAPSHOT_CLONE", "model_loader",
                "New pods loading model via NVMe snapshot clone (140 GiB in 4.8s "
                "per node); bypassing S3 download entirely"
            ),
            TimelineEvent(
                15000, "PARTIAL_RECOVERY", "triton_server",
                "32 of 64 pods ready; serving at 75% original capacity"
            ),
            TimelineEvent(
                22000, "WARMUP_COMPLETE", "inference_engine",
                "CUDA graph capture and KV cache pre-allocation complete on "
                "48 of 64 pods"
            ),
            TimelineEvent(
                30000, "RESOLVED", "health_monitor",
                "All 64 replacement pods serving; warm-pool nodes retained as "
                "buffer for 30min cooldown; traffic rebalanced"
            ),
        ]
        cold_load_timeline = [
            TimelineEvent(
                10000, "S3_DOWNLOAD_START", "model_loader",
                "(COLD PATH) Downloading 140 GiB from S3 standard; "
                "throughput 4.2 GiB/s; ETA 33s"
            ),
            TimelineEvent(
                43000, "S3_DOWNLOAD_COMPLETE", "model_loader",
                "(COLD PATH) Download complete; beginning weight deserialization"
            ),
            TimelineEvent(
                50000, "DESERIALIZATION", "triton_server",
                "(COLD PATH) Model deserialized and loaded to GPU memory"
            ),
            TimelineEvent(
                55000, "WARMUP", "inference_engine",
                "(COLD PATH) CUDA graph capture + KV cache allocation"
            ),
            TimelineEvent(
                58000, "COLD_RESOLVED", "health_monitor",
                "(COLD PATH) All pods ready; 58s total vs 30s snapshot path"
            ),
        ]
        # Include cold path events as reference in timeline
        full_timeline = timeline + cold_load_timeline
        full_timeline.sort(key=lambda e: e.timestamp_ms)

        return SimulationResult(
            scenario=FailureScenario.COLD_START_STORM,
            trigger_conditions=(
                "Kubernetes rolling node replacement cycles 8 GPU nodes "
                "(64 GPUs / 25% of cluster) simultaneously due to kernel "
                "security patch; all affected pods restart concurrently"
            ),
            detection_method="Kubernetes readiness probe failures (5s)",
            detection_time_ms=5000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=35.0,
                latency_p99_increase_pct=190.0,
                throughput_reduction_pct=25.0,
                availability_loss_pct=0.0,
                affected_request_count=32000,
                affected_user_tiers=["standard", "best_effort"],
                error_rate_increase_pct=2.0,
            ),
            mitigation_steps=[
                "1. Activate warm-pool standby nodes (16 nodes, pre-loaded)",
                "2. Shift 15% traffic to secondary region (us-west-2)",
                "3. Load models via NVMe snapshot clone (30s vs 58s cold)",
                "4. Stagger pod restarts to avoid thundering herd",
                "5. Pre-capture CUDA graphs during warm-up phase",
                "6. Retain warm-pool nodes as buffer for 30min cooldown",
            ],
            recovery_time_ms=30000,
            blast_radius=BlastRadius(
                affected_components=[
                    "kubernetes", "triton_server", "model_loader",
                    "inference_engine", "capacity_controller",
                    "fleet_scheduler", "global_load_balancer",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["standard", "best_effort"],
                affected_models=[
                    "llm-personalization-70b", "llm-search-13b",
                    "llm-content-understanding-7b",
                ],
                cascading_risk=True,
                cascading_targets=["request_queue", "adaptive_batcher"],
            ),
            sla_impact_minutes=0.50,
            timeline=full_timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: NVLink failure
    # ------------------------------------------------------------------

    def _simulate_nvlink_failure(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "nvlink_interconnect",
                "NVLink 3.0 lane 5 between GPU-0 and GPU-4 reports CRC errors; "
                "bandwidth degrades from 600 GB/s to 150 GB/s"
            ),
            TimelineEvent(
                100, "TENSOR_PARALLEL_SLOW", "inference_engine",
                "Tensor-parallel all-reduce latency spikes 4x; batch "
                "completion time exceeds SLO"
            ),
            TimelineEvent(
                500, "NCCL_WARNING", "nccl_runtime",
                "NCCL WARN: NVLink connection unstable; falling back to "
                "PCIe for GPU-0 <-> GPU-4 communication"
            ),
            TimelineEvent(
                2000, "ALERT_FIRED", "dcgm_exporter",
                "nvlink_bandwidth_bytes_per_second < 200GB/s threshold; "
                "GPU topology degradation alert"
            ),
            TimelineEvent(
                3000, "NODE_DRAIN", "fleet_scheduler",
                "Node gpu-h100-042 drained from serving pool; in-flight "
                "requests completed with extended deadline"
            ),
            TimelineEvent(
                5000, "REPLACEMENT_ACTIVATE", "fleet_scheduler",
                "Warm-pool replacement node gpu-h100-spare-03 activated; "
                "model loading via snapshot clone"
            ),
            TimelineEvent(
                12000, "RESOLVED", "health_monitor",
                "Replacement node serving; degraded node queued for "
                "hardware diagnostics and NVLink repair"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.NVLINK_FAILURE,
            trigger_conditions=(
                "NVLink 3.0 interconnect between GPU-0 and GPU-4 in an "
                "8-GPU HGX node reports uncorrectable CRC errors; bandwidth "
                "degrades to PCIe fallback levels disrupting tensor parallelism"
            ),
            detection_method="DCGM NVLink bandwidth monitoring (<200 GB/s threshold)",
            detection_time_ms=2000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=8.0,
                latency_p99_increase_pct=85.0,
                throughput_reduction_pct=3.0,
                availability_loss_pct=0.0,
                affected_request_count=4200,
                affected_user_tiers=["premium", "standard"],
                error_rate_increase_pct=0.5,
            ),
            mitigation_steps=[
                "1. Detect NVLink degradation via DCGM bandwidth metrics",
                "2. Drain affected node from serving pool gracefully",
                "3. Activate warm-pool replacement with snapshot clone",
                "4. Queue degraded node for hardware diagnostics",
                "5. File RMA if NVLink errors persist after driver reset",
            ],
            recovery_time_ms=12000,
            blast_radius=BlastRadius(
                affected_components=[
                    "nvlink_interconnect", "nccl_runtime",
                    "inference_engine", "fleet_scheduler",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["premium", "standard"],
                affected_models=["llm-personalization-70b"],
                cascading_risk=False,
                cascading_targets=[],
            ),
            sla_impact_minutes=0.05,
            timeline=timeline,
        )

    # ------------------------------------------------------------------
    # Scenario: Redis network partition
    # ------------------------------------------------------------------

    def _simulate_redis_network_partition(self) -> SimulationResult:
        timeline = [
            TimelineEvent(
                0, "TRIGGER", "network_fabric",
                "Network partition isolates Redis primary from 2 of 3 replicas; "
                "split-brain condition emerges in us-east-1a"
            ),
            TimelineEvent(
                500, "WRITE_DIVERGENCE", "redis_cluster",
                "Writes succeeding on isolated primary but not replicating; "
                "feature cache and session state diverging"
            ),
            TimelineEvent(
                3000, "SENTINEL_DETECT", "redis_sentinel",
                "Redis Sentinel detects primary unreachable from quorum; "
                "initiating failover election"
            ),
            TimelineEvent(
                5000, "FAILOVER_ELECT", "redis_sentinel",
                "Replica redis-replica-02 promoted to primary; "
                "old primary fenced off"
            ),
            TimelineEvent(
                6000, "CLIENT_REDIRECT", "redis_client_pool",
                "Connection pool draining old primary; redirecting 12,400 "
                "connections to new primary"
            ),
            TimelineEvent(
                8000, "CACHE_MISS_SPIKE", "feature_cache",
                "Cache miss rate spikes from 2% to 38% on diverged keys; "
                "feature store fallback activated"
            ),
            TimelineEvent(
                12000, "CACHE_WARM", "feature_cache",
                "Active cache warming from feature store; miss rate "
                "declining 38% -> 15% -> 5%"
            ),
            TimelineEvent(
                18000, "PARTITION_HEAL", "network_fabric",
                "Network partition healed; old primary re-syncing as replica"
            ),
            TimelineEvent(
                22000, "RESOLVED", "health_monitor",
                "Redis cluster fully converged; cache miss rate nominal at 2%; "
                "no data loss confirmed"
            ),
        ]
        return SimulationResult(
            scenario=FailureScenario.REDIS_PARTITION,
            trigger_conditions=(
                "Network partition isolates Redis primary node from quorum "
                "of replicas in us-east-1a; split-brain condition causes "
                "write divergence on feature cache and session state"
            ),
            detection_method="Redis Sentinel quorum detection (3s)",
            detection_time_ms=3000,
            impact_assessment=ImpactAssessment(
                latency_p50_increase_pct=25.0,
                latency_p99_increase_pct=150.0,
                throughput_reduction_pct=10.0,
                availability_loss_pct=0.0,
                affected_request_count=22000,
                affected_user_tiers=["premium", "standard", "best_effort"],
                error_rate_increase_pct=3.0,
            ),
            mitigation_steps=[
                "1. Redis Sentinel promotes healthy replica to primary (5s)",
                "2. Client connection pool redirects to new primary",
                "3. Activate feature store fallback for cache misses",
                "4. Warm cache from feature store for diverged keys",
                "5. Fence old primary to prevent stale writes",
                "6. Re-sync old primary as replica after partition heals",
            ],
            recovery_time_ms=22000,
            blast_radius=BlastRadius(
                affected_components=[
                    "redis_cluster", "redis_sentinel", "feature_cache",
                    "session_store", "redis_client_pool",
                ],
                affected_regions=["us-east-1"],
                affected_user_tiers=["premium", "standard", "best_effort"],
                affected_models=["llm-personalization-70b", "llm-search-13b"],
                cascading_risk=True,
                cascading_targets=["feature_hydration", "session_management"],
            ),
            sla_impact_minutes=0.10,
            timeline=timeline,
        )


def main() -> None:
    """Run all failure simulations and print summaries."""
    simulator = FailureSimulator(cluster_size=256, gpus_per_node=8)
    results = simulator.run_all_scenarios()

    print(f"\nNetflix LLM Platform - Failure Simulation Report")
    print(f"Cluster: 256 nodes x 8 GPUs = 2,048 GPUs\n")

    total_sla_minutes = 0.0
    for name, result in results.items():
        print(result.summary())
        print()
        total_sla_minutes += result.sla_impact_minutes

    print(f"{'=' * 72}")
    print(f"Total SLA impact across all scenarios: {total_sla_minutes:.2f} minutes")
    print(
        f"Monthly error budget (99.99% SLA): {30 * 24 * 60 * 0.0001:.2f} minutes "
        f"-> {total_sla_minutes / (30 * 24 * 60 * 0.0001) * 100:.1f}% consumed"
    )
    print(f"Scenarios simulated: {len(results)}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
