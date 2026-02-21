"""
Canary Deployment Engine for Netflix LLM Platform.

Provides progressive model rollouts with automated health evaluation
and auto-rollback capabilities.  Supports multi-stage traffic shifting,
metric comparison against a baseline, and configurable rollback thresholds.

Author: Gopi Krishna Vajrala
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CanaryStage(Enum):
    """Lifecycle stages for a canary deployment."""
    INITIAL = "INITIAL"
    RAMPING = "RAMPING"
    BAKING = "BAKING"
    PROMOTING = "PROMOTING"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"


class MetricVerdict(Enum):
    """Verdict for a single metric comparison."""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class OverallVerdict(Enum):
    """Overall canary health verdict."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


@dataclass
class CanaryConfig:
    """Configuration for canary deployments.

    Attributes:
        initial_traffic_pct: Starting traffic percentage for the canary.
        stages: Progressive traffic percentages for each rollout stage.
        stage_duration_minutes: How long to bake at each stage.
        rollback_thresholds: Metric regression limits that trigger rollback.
        min_requests_per_stage: Minimum sample size before evaluating a stage.
        consecutive_warn_limit: How many consecutive WARN checks before rollback.
    """
    initial_traffic_pct: float = 5.0
    stages: list = field(default_factory=lambda: [5, 10, 25, 50, 75, 100])
    stage_duration_minutes: int = 10
    rollback_thresholds: dict = field(default_factory=lambda: {
        "p95_latency_regression_pct": 10.0,
        "error_rate_increase_pct": 1.0,
        "accuracy_regression_pct": 2.0,
        "gpu_memory_increase_pct": 15.0,
    })
    min_requests_per_stage: int = 1000
    consecutive_warn_limit: int = 2


@dataclass
class MetricSnapshot:
    """Point-in-time snapshot of model performance metrics.

    Attributes:
        p95_latency_ms: 95th-percentile latency in milliseconds.
        error_rate_pct: Percentage of requests resulting in errors.
        accuracy_score: Model accuracy or quality score (0.0 - 1.0).
        gpu_memory_mb: GPU memory consumption in megabytes.
        request_count: Total number of requests observed.
        timestamp: When these metrics were collected.
    """
    p95_latency_ms: float = 0.0
    error_rate_pct: float = 0.0
    accuracy_score: float = 1.0
    gpu_memory_mb: float = 0.0
    request_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class MetricComparison:
    """Result of comparing a single metric between canary and baseline.

    Attributes:
        metric_name: Name of the metric being compared.
        baseline_value: Value from the baseline model.
        canary_value: Value from the canary model.
        delta_pct: Percentage change (positive means regression for latency/error).
        threshold_pct: Configured threshold for this metric.
        verdict: PASS, WARN, or FAIL.
        is_critical: Whether a failure triggers immediate rollback.
    """
    metric_name: str
    baseline_value: float
    canary_value: float
    delta_pct: float
    threshold_pct: float
    verdict: MetricVerdict = MetricVerdict.PASS
    is_critical: bool = False


@dataclass
class CanaryHealthCheck:
    """Aggregated health check result for a canary deployment.

    Attributes:
        deployment_id: ID of the canary deployment.
        timestamp: When the health check was performed.
        metric_comparisons: Individual metric comparison results.
        overall_verdict: HEALTHY, DEGRADED, or UNHEALTHY.
        should_rollback: Whether auto-rollback should be triggered.
        rollback_reason: Human-readable reason if rollback is recommended.
    """
    deployment_id: str
    timestamp: float = field(default_factory=time.time)
    metric_comparisons: list = field(default_factory=list)
    overall_verdict: OverallVerdict = OverallVerdict.HEALTHY
    should_rollback: bool = False
    rollback_reason: str = ""


@dataclass
class CanaryStageResult:
    """Result of advancing to the next canary stage.

    Attributes:
        deployment_id: ID of the canary deployment.
        previous_stage_idx: Index of the stage we just left.
        current_stage_idx: Index of the stage we are now in.
        current_traffic_pct: Traffic percentage at the new stage.
        health_check: Health check performed before advancing.
        advanced: Whether the stage was successfully advanced.
        message: Human-readable status message.
    """
    deployment_id: str
    previous_stage_idx: int
    current_stage_idx: int
    current_traffic_pct: float
    health_check: Optional[CanaryHealthCheck] = None
    advanced: bool = True
    message: str = ""


@dataclass
class RollbackResult:
    """Result of rolling back a canary deployment.

    Attributes:
        deployment_id: ID of the canary deployment.
        success: Whether the rollback succeeded.
        reason: Why the rollback was triggered.
        rolled_back_from_stage_idx: The stage index we rolled back from.
        rolled_back_from_traffic_pct: Traffic pct at time of rollback.
        timestamp: When the rollback occurred.
        duration_seconds: How long the canary was live before rollback.
    """
    deployment_id: str
    success: bool = True
    reason: str = ""
    rolled_back_from_stage_idx: int = 0
    rolled_back_from_traffic_pct: float = 0.0
    timestamp: float = field(default_factory=time.time)
    duration_seconds: float = 0.0


@dataclass
class CanaryStatus:
    """Current status of a canary deployment.

    Attributes:
        deployment_id: ID of the canary deployment.
        model_name: Name of the model being deployed.
        new_version: Version string of the canary model.
        baseline_version: Version string of the baseline model.
        stage: Current lifecycle stage.
        current_stage_idx: Index into the stages list.
        current_traffic_pct: Current traffic percentage for the canary.
        started_at: Timestamp when the canary was started.
        elapsed_seconds: Total time since canary start.
        health_checks_performed: Number of health checks completed.
        last_health_check: Most recent health check result.
    """
    deployment_id: str
    model_name: str = ""
    new_version: str = ""
    baseline_version: str = ""
    stage: CanaryStage = CanaryStage.INITIAL
    current_stage_idx: int = 0
    current_traffic_pct: float = 0.0
    started_at: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    health_checks_performed: int = 0
    last_health_check: Optional[CanaryHealthCheck] = None


@dataclass
class CanaryDeployment:
    """Full lifecycle record for a canary deployment.

    Attributes:
        deployment_id: Unique identifier for this deployment.
        model_name: Name of the model being deployed.
        new_version: Version of the canary model.
        baseline_version: Version of the stable baseline model.
        config: Canary configuration used for this deployment.
        stage: Current lifecycle stage.
        current_stage_idx: Index into config.stages for current traffic %.
        current_traffic_pct: Active traffic percentage for the canary.
        started_at: Timestamp when the deployment was initiated.
        completed_at: Timestamp when the deployment completed or rolled back.
        health_checks: History of all health checks performed.
        consecutive_warn_count: Counter for consecutive WARNING health checks.
        rollback_result: Populated if the deployment was rolled back.
    """
    deployment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_name: str = ""
    new_version: str = ""
    baseline_version: str = ""
    config: CanaryConfig = field(default_factory=CanaryConfig)
    stage: CanaryStage = CanaryStage.INITIAL
    current_stage_idx: int = 0
    current_traffic_pct: float = 0.0
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    health_checks: list = field(default_factory=list)
    consecutive_warn_count: int = 0
    rollback_result: Optional[RollbackResult] = None


class CanaryDeployer:
    """Canary deployment engine for progressive model rollouts.

    Manages the lifecycle of canary deployments including traffic shifting,
    health evaluation against a baseline, and automated rollback when
    regressions are detected.
    """

    def __init__(self, config: Optional[CanaryConfig] = None):
        self.config = config or CanaryConfig()
        self._deployments: dict[str, CanaryDeployment] = {}

        # Pluggable metric sources -- callers can override these callables
        self._baseline_metric_source = None
        self._canary_metric_source = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_canary(
        self,
        model_name: str,
        new_version: str,
        baseline_version: str,
    ) -> CanaryDeployment:
        """Initiate a new canary deployment.

        Args:
            model_name: Name of the model to deploy.
            new_version: Version string of the new (canary) model.
            baseline_version: Version string of the current stable model.

        Returns:
            A CanaryDeployment record tracking the full lifecycle.
        """
        deployment = CanaryDeployment(
            model_name=model_name,
            new_version=new_version,
            baseline_version=baseline_version,
            config=self.config,
            stage=CanaryStage.INITIAL,
            current_stage_idx=0,
            current_traffic_pct=self.config.initial_traffic_pct,
        )
        self._deployments[deployment.deployment_id] = deployment
        return deployment

    def advance_stage(self, deployment_id: str) -> CanaryStageResult:
        """Advance the deployment to the next traffic stage.

        Performs a health check before advancing.  If the health check
        indicates the canary is unhealthy, the stage is not advanced and
        auto-rollback may be triggered.

        Args:
            deployment_id: ID of the canary deployment.

        Returns:
            CanaryStageResult describing the outcome.

        Raises:
            KeyError: If the deployment_id is not found.
        """
        deployment = self._get_deployment(deployment_id)

        if deployment.stage in (CanaryStage.COMPLETED, CanaryStage.ROLLED_BACK):
            return CanaryStageResult(
                deployment_id=deployment_id,
                previous_stage_idx=deployment.current_stage_idx,
                current_stage_idx=deployment.current_stage_idx,
                current_traffic_pct=deployment.current_traffic_pct,
                advanced=False,
                message=f"Deployment already in terminal stage: {deployment.stage.value}",
            )

        # Evaluate health before advancing
        health = self.evaluate_health(deployment_id)

        if health.should_rollback:
            rollback = self.auto_rollback(deployment_id, health.rollback_reason)
            return CanaryStageResult(
                deployment_id=deployment_id,
                previous_stage_idx=deployment.current_stage_idx,
                current_stage_idx=deployment.current_stage_idx,
                current_traffic_pct=0.0,
                health_check=health,
                advanced=False,
                message=f"Rolled back: {rollback.reason}",
            )

        previous_idx = deployment.current_stage_idx
        next_idx = previous_idx + 1

        if next_idx >= len(deployment.config.stages):
            # All stages complete -- promote to full traffic
            deployment.stage = CanaryStage.COMPLETED
            deployment.current_traffic_pct = 100.0
            deployment.completed_at = time.time()
            return CanaryStageResult(
                deployment_id=deployment_id,
                previous_stage_idx=previous_idx,
                current_stage_idx=previous_idx,
                current_traffic_pct=100.0,
                health_check=health,
                advanced=True,
                message="Canary promoted to 100% -- deployment complete.",
            )

        deployment.current_stage_idx = next_idx
        deployment.current_traffic_pct = float(deployment.config.stages[next_idx])
        deployment.stage = CanaryStage.RAMPING

        return CanaryStageResult(
            deployment_id=deployment_id,
            previous_stage_idx=previous_idx,
            current_stage_idx=next_idx,
            current_traffic_pct=deployment.current_traffic_pct,
            health_check=health,
            advanced=True,
            message=(
                f"Advanced to stage {next_idx}: "
                f"{deployment.current_traffic_pct}% traffic."
            ),
        )

    def evaluate_health(self, deployment_id: str) -> CanaryHealthCheck:
        """Compare canary metrics against baseline and produce a verdict.

        Args:
            deployment_id: ID of the canary deployment.

        Returns:
            CanaryHealthCheck with per-metric verdicts and overall assessment.

        Raises:
            KeyError: If the deployment_id is not found.
        """
        deployment = self._get_deployment(deployment_id)

        baseline_metrics = self._get_baseline_metrics(deployment)
        canary_metrics = self._get_canary_metrics(deployment)

        comparisons = self._compare_metrics(
            baseline_metrics, canary_metrics, deployment.config
        )

        overall, should_rollback, reason = self._determine_verdict(
            comparisons, deployment
        )

        health = CanaryHealthCheck(
            deployment_id=deployment_id,
            metric_comparisons=comparisons,
            overall_verdict=overall,
            should_rollback=should_rollback,
            rollback_reason=reason,
        )

        deployment.health_checks.append(health)

        # Track consecutive warnings for auto-rollback logic
        if overall == OverallVerdict.DEGRADED:
            deployment.consecutive_warn_count += 1
        elif overall == OverallVerdict.HEALTHY:
            deployment.consecutive_warn_count = 0

        return health

    def auto_rollback(self, deployment_id: str, reason: str) -> RollbackResult:
        """Roll back a canary deployment and restore baseline traffic.

        Args:
            deployment_id: ID of the canary deployment.
            reason: Human-readable reason for the rollback.

        Returns:
            RollbackResult describing the outcome.

        Raises:
            KeyError: If the deployment_id is not found.
        """
        deployment = self._get_deployment(deployment_id)

        result = RollbackResult(
            deployment_id=deployment_id,
            success=True,
            reason=reason,
            rolled_back_from_stage_idx=deployment.current_stage_idx,
            rolled_back_from_traffic_pct=deployment.current_traffic_pct,
            duration_seconds=time.time() - deployment.started_at,
        )

        deployment.stage = CanaryStage.ROLLED_BACK
        deployment.current_traffic_pct = 0.0
        deployment.completed_at = time.time()
        deployment.rollback_result = result

        return result

    def get_deployment_status(self, deployment_id: str) -> CanaryStatus:
        """Return the current status of a canary deployment.

        Args:
            deployment_id: ID of the canary deployment.

        Returns:
            CanaryStatus snapshot.

        Raises:
            KeyError: If the deployment_id is not found.
        """
        deployment = self._get_deployment(deployment_id)

        last_check = (
            deployment.health_checks[-1] if deployment.health_checks else None
        )

        return CanaryStatus(
            deployment_id=deployment_id,
            model_name=deployment.model_name,
            new_version=deployment.new_version,
            baseline_version=deployment.baseline_version,
            stage=deployment.stage,
            current_stage_idx=deployment.current_stage_idx,
            current_traffic_pct=deployment.current_traffic_pct,
            started_at=deployment.started_at,
            elapsed_seconds=time.time() - deployment.started_at,
            health_checks_performed=len(deployment.health_checks),
            last_health_check=last_check,
        )

    # ------------------------------------------------------------------
    # Metric comparison internals
    # ------------------------------------------------------------------

    def _compare_metrics(
        self,
        baseline: MetricSnapshot,
        canary: MetricSnapshot,
        config: CanaryConfig,
    ) -> list[MetricComparison]:
        """Compare canary metrics against baseline using configured thresholds.

        Returns a list of MetricComparison results for each tracked metric.
        """
        comparisons: list[MetricComparison] = []
        thresholds = config.rollback_thresholds

        # --- p95 latency (higher is worse) ---
        lat_threshold = thresholds.get("p95_latency_regression_pct", 10.0)
        lat_delta = self._pct_change(baseline.p95_latency_ms, canary.p95_latency_ms)
        lat_verdict = self._threshold_verdict(lat_delta, lat_threshold)
        comparisons.append(MetricComparison(
            metric_name="p95_latency_ms",
            baseline_value=baseline.p95_latency_ms,
            canary_value=canary.p95_latency_ms,
            delta_pct=lat_delta,
            threshold_pct=lat_threshold,
            verdict=lat_verdict,
            is_critical=True,
        ))

        # --- error rate (higher is worse) ---
        err_threshold = thresholds.get("error_rate_increase_pct", 1.0)
        err_delta = canary.error_rate_pct - baseline.error_rate_pct
        err_verdict = self._threshold_verdict(err_delta, err_threshold)
        comparisons.append(MetricComparison(
            metric_name="error_rate_pct",
            baseline_value=baseline.error_rate_pct,
            canary_value=canary.error_rate_pct,
            delta_pct=err_delta,
            threshold_pct=err_threshold,
            verdict=err_verdict,
            is_critical=True,
        ))

        # --- accuracy (lower is worse, so we negate the delta) ---
        acc_threshold = thresholds.get("accuracy_regression_pct", 2.0)
        acc_delta = self._pct_change(canary.accuracy_score, baseline.accuracy_score)
        acc_verdict = self._threshold_verdict(acc_delta, acc_threshold)
        comparisons.append(MetricComparison(
            metric_name="accuracy_score",
            baseline_value=baseline.accuracy_score,
            canary_value=canary.accuracy_score,
            delta_pct=acc_delta,
            threshold_pct=acc_threshold,
            verdict=acc_verdict,
            is_critical=True,
        ))

        # --- GPU memory (higher is worse, but WARNING-only) ---
        mem_threshold = thresholds.get("gpu_memory_increase_pct", 15.0)
        mem_delta = self._pct_change(baseline.gpu_memory_mb, canary.gpu_memory_mb)
        mem_verdict = self._gpu_mem_verdict(mem_delta, mem_threshold)
        comparisons.append(MetricComparison(
            metric_name="gpu_memory_mb",
            baseline_value=baseline.gpu_memory_mb,
            canary_value=canary.gpu_memory_mb,
            delta_pct=mem_delta,
            threshold_pct=mem_threshold,
            verdict=mem_verdict,
            is_critical=False,
        ))

        return comparisons

    def _determine_verdict(
        self,
        comparisons: list[MetricComparison],
        deployment: CanaryDeployment,
    ) -> tuple[OverallVerdict, bool, str]:
        """Determine overall verdict and whether to rollback.

        Auto-rollback rules:
          - If any CRITICAL metric FAILs: immediate rollback.
          - If any WARNING metric FAILs for consecutive_warn_limit consecutive
            checks: rollback.

        Returns:
            (overall_verdict, should_rollback, rollback_reason)
        """
        has_fail = False
        has_warn = False
        critical_fail_reasons: list[str] = []
        warn_reasons: list[str] = []

        for cmp in comparisons:
            if cmp.verdict == MetricVerdict.FAIL:
                has_fail = True
                if cmp.is_critical:
                    critical_fail_reasons.append(
                        f"{cmp.metric_name}: {cmp.delta_pct:+.2f}% "
                        f"(threshold: {cmp.threshold_pct:.2f}%)"
                    )
            elif cmp.verdict == MetricVerdict.WARN:
                has_warn = True
                warn_reasons.append(
                    f"{cmp.metric_name}: {cmp.delta_pct:+.2f}% "
                    f"(threshold: {cmp.threshold_pct:.2f}%)"
                )

        # Immediate rollback on critical metric failure
        if critical_fail_reasons:
            reason = (
                "Critical metric regression detected: "
                + "; ".join(critical_fail_reasons)
            )
            return OverallVerdict.UNHEALTHY, True, reason

        # Rollback on consecutive warnings exceeding limit
        warn_limit = deployment.config.consecutive_warn_limit
        if has_warn and (deployment.consecutive_warn_count + 1) >= warn_limit:
            reason = (
                f"Warning metrics failed for {warn_limit} consecutive checks: "
                + "; ".join(warn_reasons)
            )
            return OverallVerdict.DEGRADED, True, reason

        if has_warn:
            return OverallVerdict.DEGRADED, False, ""

        if has_fail:
            # Non-critical fail (shouldn't happen with current metric config
            # but kept for extensibility)
            return OverallVerdict.DEGRADED, False, ""

        return OverallVerdict.HEALTHY, False, ""

    # ------------------------------------------------------------------
    # Metric helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pct_change(baseline_val: float, canary_val: float) -> float:
        """Calculate percentage change from baseline to canary.

        Returns 0.0 if baseline is zero to avoid division errors.
        """
        if baseline_val == 0.0:
            return 0.0 if canary_val == 0.0 else 100.0
        return ((canary_val - baseline_val) / abs(baseline_val)) * 100.0

    @staticmethod
    def _threshold_verdict(delta_pct: float, threshold_pct: float) -> MetricVerdict:
        """Return PASS / WARN / FAIL based on delta vs threshold.

        FAIL if delta >= threshold.
        WARN if delta >= 50% of threshold.
        PASS otherwise.
        """
        if delta_pct >= threshold_pct:
            return MetricVerdict.FAIL
        if delta_pct >= threshold_pct * 0.5:
            return MetricVerdict.WARN
        return MetricVerdict.PASS

    @staticmethod
    def _gpu_mem_verdict(delta_pct: float, threshold_pct: float) -> MetricVerdict:
        """GPU memory uses WARN instead of FAIL for threshold breaches."""
        if delta_pct >= threshold_pct:
            return MetricVerdict.WARN
        return MetricVerdict.PASS

    # ------------------------------------------------------------------
    # Deployment lookup
    # ------------------------------------------------------------------

    def _get_deployment(self, deployment_id: str) -> CanaryDeployment:
        """Retrieve a deployment by ID, raising KeyError if not found."""
        deployment = self._deployments.get(deployment_id)
        if deployment is None:
            raise KeyError(f"Canary deployment not found: {deployment_id}")
        return deployment

    # ------------------------------------------------------------------
    # Metric source hooks
    # ------------------------------------------------------------------

    def _get_baseline_metrics(self, deployment: CanaryDeployment) -> MetricSnapshot:
        """Fetch baseline metrics.  Override or plug in a metric source."""
        if self._baseline_metric_source is not None:
            return self._baseline_metric_source(deployment)
        return MetricSnapshot(
            p95_latency_ms=50.0,
            error_rate_pct=0.1,
            accuracy_score=0.95,
            gpu_memory_mb=4096.0,
            request_count=10000,
        )

    def _get_canary_metrics(self, deployment: CanaryDeployment) -> MetricSnapshot:
        """Fetch canary metrics.  Override or plug in a metric source."""
        if self._canary_metric_source is not None:
            return self._canary_metric_source(deployment)
        return MetricSnapshot(
            p95_latency_ms=52.0,
            error_rate_pct=0.12,
            accuracy_score=0.948,
            gpu_memory_mb=4200.0,
            request_count=500,
        )
