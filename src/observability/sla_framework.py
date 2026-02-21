"""
SLA/SLO/Error Budget Framework for the Netflix LLM Platform.

Principal-level reliability engineering framework that defines, tracks, and
enforces Service Level Objectives (SLOs) and Service Level Agreements (SLAs)
with full error budget management and deployment gating.

This framework implements Google SRE best practices adapted for GPU-intensive
LLM inference workloads running on GCP, with Netflix-specific operational
policies for error budget consumption and deployment readiness.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SLODefinition:
    """Defines a single Service Level Objective with internal and external targets.

    Internal SLO targets are intentionally stricter than external SLA commitments
    to provide an early-warning buffer before contractual violations occur.

    Prometheus metric (example):
        # slo:availability:ratio_rate30d
        # slo:latency_p99:ratio_rate30d
    """

    name: str
    description: str = ""

    # Availability fields
    target: Optional[float] = None           # Internal SLO percentage (e.g. 99.95)
    sla_target: Optional[float] = None       # External SLA percentage (e.g. 99.9)

    # Latency fields (milliseconds)
    target_ms: Optional[float] = None        # Internal latency target
    sla_target_ms: Optional[float] = None    # External latency commitment

    # Throughput fields
    min_tokens_per_sec: Optional[int] = None
    min_rps: Optional[int] = None

    # Measurement configuration
    measurement_window_hours: int = 720      # 30 days default

    def effective_target_ratio(self) -> float:
        """Return the internal SLO target as a ratio (0.0 - 1.0)."""
        if self.target is not None:
            return self.target / 100.0
        return 1.0

    def effective_sla_ratio(self) -> float:
        """Return the external SLA target as a ratio (0.0 - 1.0)."""
        if self.sla_target is not None:
            return self.sla_target / 100.0
        return 1.0

    def allowed_failure_ratio(self) -> float:
        """Fraction of time/requests that may fail under the internal SLO."""
        return 1.0 - self.effective_target_ratio()


@dataclass
class ErrorBudgetStatus:
    """Snapshot of current error-budget health.

    Prometheus metrics (production):
        # netflix_llm:error_budget:total_minutes
        # netflix_llm:error_budget:consumed_minutes
        # netflix_llm:error_budget:remaining_pct
        # netflix_llm:error_budget:burn_rate_per_hour
    """

    total_budget_minutes: float
    consumed_minutes: float
    remaining_minutes: float
    remaining_pct: float
    burn_rate_per_hour: float
    projected_exhaustion_date: Optional[datetime]
    window_start: datetime
    window_end: datetime
    slo_name: str


class DeploymentGateVerdict(Enum):
    """Outcome of a deployment-readiness evaluation."""

    ALLOW = "allow"
    WARN = "warn"
    FREEZE = "freeze"
    EMERGENCY = "emergency"


@dataclass
class DeploymentGate:
    """Result of an error-budget-aware deployment gate check.

    Prometheus metric:
        # netflix_llm:deployment_gate:verdict
    """

    verdict: DeploymentGateVerdict
    remaining_budget_pct: float
    reason: str
    requires_approval_from: Optional[str] = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SLAViolation:
    """A single SLA/SLO violation event.

    Prometheus metric:
        # netflix_llm:sla_violation_total{slo_name="..."}
    """

    slo_name: str
    timestamp: datetime
    actual_value: float
    target_value: float
    sla_value: float
    is_sla_breach: bool
    duration_seconds: float = 0.0
    details: str = ""


@dataclass
class BurnRateAlert:
    """Alert generated when error-budget burn rate exceeds thresholds.

    Prometheus alerting rules (production):
        # ALERT ErrorBudgetFastBurn
        #   expr: netflix_llm:error_budget:burn_rate_1h > 14.4
        #   for: 2m
        #   labels: { severity: "page" }
        #
        # ALERT ErrorBudgetSlowBurn
        #   expr: netflix_llm:error_budget:burn_rate_6h > 6
        #   for: 1h
        #   labels: { severity: "ticket" }
    """

    alert_type: str            # "fast_burn" or "slow_burn"
    severity: str              # "page" or "ticket"
    burn_rate_multiple: float   # Current burn-rate as a multiple of normal
    threshold_multiple: float   # Threshold that was exceeded
    message: str
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SLOComplianceEntry:
    """Per-SLO compliance result within an SLA report."""

    slo_name: str
    target: float
    sla_target: float
    actual: float
    is_compliant: bool
    is_sla_compliant: bool
    margin_pct: float          # Positive means headroom, negative means breach


@dataclass
class SLAReport:
    """Comprehensive SLA compliance report for a measurement window.

    Prometheus metric:
        # netflix_llm:sla_composite_score
    """

    window_start: datetime
    window_end: datetime
    window_hours: int
    entries: List[SLOComplianceEntry]
    violations: List[SLAViolation]
    composite_sla_score: float
    overall_compliant: bool
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# SLO catalogue - canonical definitions for the Netflix LLM Platform
# ---------------------------------------------------------------------------

# Availability SLO
# Prometheus: sum(rate(http_requests_total{code!~"5.."}[30d])) /
#             sum(rate(http_requests_total[30d]))
AVAILABILITY_SLO = SLODefinition(
    name="availability",
    target=99.95,                  # Internal SLO (stricter than SLA)
    sla_target=99.9,               # External SLA (contractual)
    measurement_window_hours=720,  # 30 days
    description="Percentage of successful requests (non-5xx)",
)

# Latency SLOs
# Prometheus: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[30d]))
LATENCY_P95_SLO = SLODefinition(
    name="latency_p95",
    target_ms=200,
    sla_target_ms=250,
    measurement_window_hours=720,
    description="95th-percentile request latency",
)

# Prometheus: histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[30d]))
LATENCY_P99_SLO = SLODefinition(
    name="latency_p99",
    target_ms=250,
    sla_target_ms=500,
    measurement_window_hours=720,
    description="99th-percentile request latency",
)

# Prometheus: histogram_quantile(0.95, rate(llm_time_to_first_token_seconds_bucket[30d]))
TTFT_SLO = SLODefinition(
    name="time_to_first_token",
    target_ms=100,
    sla_target_ms=150,
    measurement_window_hours=720,
    description="Time to first token (streaming latency)",
)

# Throughput SLO
# Prometheus: rate(llm_tokens_generated_total[5m])
#             rate(http_requests_total[5m])
THROUGHPUT_SLO = SLODefinition(
    name="throughput",
    min_tokens_per_sec=3000,
    min_rps=500,
    measurement_window_hours=720,
    description="Minimum sustained throughput (tokens/s and requests/s)",
)

ALL_SLOS: List[SLODefinition] = [
    AVAILABILITY_SLO,
    LATENCY_P95_SLO,
    LATENCY_P99_SLO,
    TTFT_SLO,
    THROUGHPUT_SLO,
]


# ---------------------------------------------------------------------------
# Error Budget Tracker
# ---------------------------------------------------------------------------

class ErrorBudgetTracker:
    """Tracks error-budget consumption against an availability SLO.

    The error budget is the maximum amount of unreliability the service is
    *allowed* to accumulate during the measurement window without breaching
    its SLO.

    For a 99.95% SLO over 30 days:
        monthly_budget_minutes = (1 - 0.9995) * 30 * 24 * 60 = 21.6 minutes

    Prometheus metrics (production):
        # netflix_llm:error_budget:total_minutes{slo="availability"}
        # netflix_llm:error_budget:consumed_minutes{slo="availability"}
        # netflix_llm:error_budget:remaining_pct{slo="availability"}
        # netflix_llm:error_budget:burn_rate_1h{slo="availability"}
        # netflix_llm:error_budget:burn_rate_6h{slo="availability"}
    """

    # Burn-rate alert thresholds (multiples of normal hourly consumption).
    FAST_BURN_THRESHOLD: float = 14.4   # Page immediately
    SLOW_BURN_THRESHOLD: float = 6.0    # Alert within 1 hour

    def __init__(
        self,
        slo: SLODefinition,
        window_start: Optional[datetime] = None,
    ) -> None:
        self._slo = slo
        self._window_hours = slo.measurement_window_hours
        self._window_start = window_start or datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        self._window_end = self._window_start + timedelta(hours=self._window_hours)

        # Total error budget in minutes for the window
        # For 99.95% SLO: (1 - 0.9995) * 720 * 60 = 21.6 minutes
        self.monthly_budget_minutes: float = (
            slo.allowed_failure_ratio() * self._window_hours * 60
        )

        # Error events: list of (timestamp, duration_minutes)
        self._error_events: List[Tuple[datetime, float]] = []

    # -- Recording errors ----------------------------------------------------

    def record_error_event(
        self,
        timestamp: Optional[datetime] = None,
        duration_minutes: float = 1.0,
    ) -> Optional[BurnRateAlert]:
        """Record an error event and return a BurnRateAlert if thresholds exceeded.

        Args:
            timestamp: When the error occurred. Defaults to now (UTC).
            duration_minutes: Duration of the outage/error in minutes.

        Returns:
            A BurnRateAlert if the current burn rate exceeds fast or slow
            burn thresholds, otherwise None.
        """
        ts = timestamp or datetime.now(timezone.utc)
        self._error_events.append((ts, duration_minutes))
        logger.info(
            "Error event recorded: %.2f min at %s (total consumed: %.2f / %.2f min)",
            duration_minutes,
            ts.isoformat(),
            self._consumed_minutes(),
            self.monthly_budget_minutes,
        )
        return self._evaluate_burn_rate(ts)

    # -- Budget queries -------------------------------------------------------

    def _consumed_minutes(self) -> float:
        """Sum of all error-event durations within the current window."""
        return sum(
            dur for ts, dur in self._error_events
            if self._window_start <= ts <= self._window_end
        )

    def get_remaining_budget(self) -> ErrorBudgetStatus:
        """Return a point-in-time snapshot of the error budget.

        This is the primary interface for dashboards, deployment gates, and
        alerting integrations to query the current budget health.

        Returns:
            ErrorBudgetStatus with all budget metrics computed.
        """
        consumed = self._consumed_minutes()
        remaining = max(0.0, self.monthly_budget_minutes - consumed)
        remaining_pct = (
            (remaining / self.monthly_budget_minutes) * 100.0
            if self.monthly_budget_minutes > 0
            else 0.0
        )
        burn_rate = self._current_burn_rate_per_hour()

        projected_exhaustion: Optional[datetime] = None
        if burn_rate > 0 and remaining > 0:
            hours_left = remaining / (burn_rate * 60)
            projected_exhaustion = datetime.now(timezone.utc) + timedelta(
                hours=hours_left
            )

        return ErrorBudgetStatus(
            total_budget_minutes=self.monthly_budget_minutes,
            consumed_minutes=consumed,
            remaining_minutes=remaining,
            remaining_pct=remaining_pct,
            burn_rate_per_hour=burn_rate,
            projected_exhaustion_date=projected_exhaustion,
            window_start=self._window_start,
            window_end=self._window_end,
            slo_name=self._slo.name,
        )

    # -- Burn-rate analysis ---------------------------------------------------

    def _current_burn_rate_per_hour(self) -> float:
        """Compute the average burn rate (fraction of budget consumed per hour).

        Normal burn rate = 1.0 / window_hours (i.e. even consumption across
        the entire window). A burn rate of 14.4x means the budget would be
        exhausted in ~1/14.4th of the window.
        """
        now = datetime.now(timezone.utc)
        elapsed_hours = max(
            1.0 / 60,  # Floor at 1 minute to avoid division by near-zero
            (now - self._window_start).total_seconds() / 3600,
        )
        consumed = self._consumed_minutes()
        if self.monthly_budget_minutes <= 0:
            return 0.0
        consumed_fraction = consumed / self.monthly_budget_minutes
        return consumed_fraction / elapsed_hours

    def _normal_burn_rate(self) -> float:
        """Expected burn rate if budget is consumed evenly over the window.

        For a 720-hour window: 1/720 ~ 0.00139 per hour.
        """
        if self._window_hours <= 0:
            return 0.0
        return 1.0 / self._window_hours

    def _evaluate_burn_rate(self, event_time: datetime) -> Optional[BurnRateAlert]:
        """Check burn-rate thresholds and return an alert if warranted.

        Fast Burn:  burn rate > 14.4x normal  ->  page immediately
        Slow Burn:  burn rate > 6x normal     ->  alert within 1 hour

        These thresholds are based on Google SRE multiwindow, multi-burn-rate
        alerting methodology adapted for 30-day error budgets.
        """
        current = self._current_burn_rate_per_hour()
        normal = self._normal_burn_rate()

        if normal <= 0:
            return None

        multiple = current / normal

        if multiple > self.FAST_BURN_THRESHOLD:
            alert = BurnRateAlert(
                alert_type="fast_burn",
                severity="page",
                burn_rate_multiple=multiple,
                threshold_multiple=self.FAST_BURN_THRESHOLD,
                message=(
                    f"CRITICAL: Error budget burn rate is {multiple:.1f}x normal "
                    f"(threshold: {self.FAST_BURN_THRESHOLD}x). "
                    f"Page on-call immediately."
                ),
                triggered_at=event_time,
            )
            logger.critical(alert.message)
            return alert

        if multiple > self.SLOW_BURN_THRESHOLD:
            alert = BurnRateAlert(
                alert_type="slow_burn",
                severity="ticket",
                burn_rate_multiple=multiple,
                threshold_multiple=self.SLOW_BURN_THRESHOLD,
                message=(
                    f"WARNING: Error budget burn rate is {multiple:.1f}x normal "
                    f"(threshold: {self.SLOW_BURN_THRESHOLD}x). "
                    f"Alert within 1 hour."
                ),
                triggered_at=event_time,
            )
            logger.warning(alert.message)
            return alert

        return None

    # -- Convenience ----------------------------------------------------------

    def is_budget_exhausted(self) -> bool:
        """Check whether the entire error budget has been consumed."""
        return self._consumed_minutes() >= self.monthly_budget_minutes

    def reset_window(self, new_start: Optional[datetime] = None) -> None:
        """Start a new measurement window (e.g. month rollover).

        Clears all recorded error events and resets the window boundaries.
        """
        self._window_start = new_start or datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        self._window_end = self._window_start + timedelta(hours=self._window_hours)
        self._error_events.clear()
        logger.info(
            "Error budget window reset: %s - %s (budget: %.2f min)",
            self._window_start.isoformat(),
            self._window_end.isoformat(),
            self.monthly_budget_minutes,
        )


# ---------------------------------------------------------------------------
# Error Budget Policy
# ---------------------------------------------------------------------------

class ErrorBudgetPolicy:
    """Deployment gating policy driven by remaining error budget.

    Policy tiers (Netflix LLM Platform operational policy):

        >= 50%  remaining  ->  ALLOW      Normal operations
        >= 25%  remaining  ->  WARN       Deployment freeze for non-critical changes
        >= 10%  remaining  ->  FREEZE     All deployments frozen, incident response
        <  10%  remaining  ->  EMERGENCY  VP approval required for any change

    This implements the "error budget as a deployment gate" pattern from the
    Google SRE book, adapted with Netflix-specific escalation paths.

    Prometheus metric:
        # netflix_llm:deployment_gate:verdict{slo="availability"}
        # netflix_llm:deployment_gate:remaining_budget_pct{slo="availability"}
    """

    THRESHOLD_NORMAL: float = 50.0
    THRESHOLD_WARN: float = 25.0
    THRESHOLD_FREEZE: float = 10.0
    # Below THRESHOLD_FREEZE -> EMERGENCY

    def __init__(self, budget_tracker: ErrorBudgetTracker) -> None:
        self._tracker = budget_tracker

    def evaluate_deployment_readiness(
        self,
        is_critical_fix: bool = False,
    ) -> DeploymentGate:
        """Evaluate whether a deployment should proceed given current budget.

        Args:
            is_critical_fix: If True, critical fixes may bypass the WARN tier
                             but still cannot bypass FREEZE or EMERGENCY.

        Returns:
            A DeploymentGate with the verdict, remaining budget, and reason.
        """
        status = self._tracker.get_remaining_budget()
        pct = status.remaining_pct

        if pct >= self.THRESHOLD_NORMAL:
            return DeploymentGate(
                verdict=DeploymentGateVerdict.ALLOW,
                remaining_budget_pct=pct,
                reason=(
                    f"Error budget healthy at {pct:.1f}% remaining. "
                    f"Normal deployment operations permitted."
                ),
            )

        if pct >= self.THRESHOLD_WARN:
            if is_critical_fix:
                return DeploymentGate(
                    verdict=DeploymentGateVerdict.ALLOW,
                    remaining_budget_pct=pct,
                    reason=(
                        f"Error budget at {pct:.1f}% (WARN zone) but "
                        f"deployment approved as critical fix."
                    ),
                )
            return DeploymentGate(
                verdict=DeploymentGateVerdict.WARN,
                remaining_budget_pct=pct,
                reason=(
                    f"Error budget at {pct:.1f}% remaining. "
                    f"Non-critical deployments are frozen. "
                    f"Only critical fixes may proceed."
                ),
            )

        if pct >= self.THRESHOLD_FREEZE:
            return DeploymentGate(
                verdict=DeploymentGateVerdict.FREEZE,
                remaining_budget_pct=pct,
                reason=(
                    f"Error budget critically low at {pct:.1f}% remaining. "
                    f"ALL deployments are frozen. Incident response required."
                ),
                requires_approval_from="oncall-lead",
            )

        # Budget effectively exhausted (< 10%)
        return DeploymentGate(
            verdict=DeploymentGateVerdict.EMERGENCY,
            remaining_budget_pct=pct,
            reason=(
                f"Error budget exhausted ({pct:.1f}% remaining). "
                f"EMERGENCY mode: all changes require VP approval."
            ),
            requires_approval_from="vp-engineering",
        )


# ---------------------------------------------------------------------------
# SLA Compliance Reporter
# ---------------------------------------------------------------------------

class SLAComplianceReporter:
    """Generates SLA compliance reports across all registered SLOs.

    In production, actual metric values would be fetched from Prometheus/Thanos
    via the Netflix Atlas metrics pipeline. This implementation accepts injected
    values to decouple from the metrics backend and enable deterministic testing.

    Prometheus queries used in production:

        # Availability:
        #   sum(rate(http_requests_total{code!~"5.."}[30d])) /
        #   sum(rate(http_requests_total[30d])) * 100
        #
        # Latency P95:
        #   histogram_quantile(0.95,
        #     sum(rate(http_request_duration_seconds_bucket[30d])) by (le)) * 1000
        #
        # Latency P99:
        #   histogram_quantile(0.99,
        #     sum(rate(http_request_duration_seconds_bucket[30d])) by (le)) * 1000
        #
        # TTFT (Time to First Token):
        #   histogram_quantile(0.95,
        #     sum(rate(llm_time_to_first_token_seconds_bucket[30d])) by (le)) * 1000
        #
        # Throughput:
        #   sum(rate(llm_tokens_generated_total[5m]))
        #   sum(rate(http_requests_total[5m]))
    """

    def __init__(self, slos: Optional[List[SLODefinition]] = None) -> None:
        self._slos = slos or list(ALL_SLOS)
        self._violations: List[SLAViolation] = []

    def record_violation(self, violation: SLAViolation) -> None:
        """Record an SLA/SLO violation event for inclusion in future reports."""
        self._violations.append(violation)
        logger.warning(
            "SLA violation recorded: slo=%s actual=%.3f target=%.3f sla=%.3f breach=%s",
            violation.slo_name,
            violation.actual_value,
            violation.target_value,
            violation.sla_value,
            violation.is_sla_breach,
        )

    def generate_report(
        self,
        actual_values: Dict[str, float],
        window_hours: int = 720,
    ) -> SLAReport:
        """Generate a comprehensive SLA compliance report.

        Args:
            actual_values: Mapping of SLO name -> measured value.
                For availability SLOs, the value is a percentage (e.g. 99.97).
                For latency SLOs, the value is milliseconds (lower is better).
                For throughput SLOs, the value is tokens/sec or rps.
            window_hours: Measurement window in hours (default 30 days).

        Returns:
            An SLAReport summarising compliance across all SLOs.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=window_hours)
        entries: List[SLOComplianceEntry] = []
        window_violations: List[SLAViolation] = []

        for slo in self._slos:
            if slo.name not in actual_values:
                continue

            actual = actual_values[slo.name]
            entry, violation = self._evaluate_slo(slo, actual, now)
            entries.append(entry)
            if violation is not None:
                window_violations.append(violation)

        # Also include any previously recorded violations within the window
        historical = [
            v for v in self._violations
            if window_start <= v.timestamp <= now
        ]
        all_violations = window_violations + historical

        composite = self._compute_composite_score(entries)
        overall = all(e.is_sla_compliant for e in entries) if entries else True

        report = SLAReport(
            window_start=window_start,
            window_end=now,
            window_hours=window_hours,
            entries=entries,
            violations=all_violations,
            composite_sla_score=composite,
            overall_compliant=overall,
        )
        logger.info(
            "SLA report generated: composite=%.2f%% compliant=%s violations=%d",
            composite,
            overall,
            len(all_violations),
        )
        return report

    # -- Internal helpers ----------------------------------------------------

    def _evaluate_slo(
        self,
        slo: SLODefinition,
        actual: float,
        timestamp: datetime,
    ) -> Tuple[SLOComplianceEntry, Optional[SLAViolation]]:
        """Evaluate a single SLO against its actual measured value.

        Handles three SLO types with different comparison semantics:
          - Availability: higher is better (percentage)
          - Latency: lower is better (milliseconds)
          - Throughput: higher is better (tokens/sec)
        """

        # Determine target/sla values and comparison direction
        if slo.target is not None:
            # Availability: higher is better
            target = slo.target
            sla = slo.sla_target if slo.sla_target is not None else slo.target
            is_compliant = actual >= target
            is_sla_compliant = actual >= sla
            margin = actual - target
        elif slo.target_ms is not None:
            # Latency: lower is better
            target = slo.target_ms
            sla = slo.sla_target_ms if slo.sla_target_ms is not None else slo.target_ms
            is_compliant = actual <= target
            is_sla_compliant = actual <= sla
            margin = target - actual  # Positive = headroom
        elif slo.min_tokens_per_sec is not None:
            # Throughput: higher is better
            target = float(slo.min_tokens_per_sec)
            sla = target  # No separate SLA for throughput
            is_compliant = actual >= target
            is_sla_compliant = is_compliant
            margin = actual - target
        else:
            target = 0.0
            sla = 0.0
            is_compliant = True
            is_sla_compliant = True
            margin = 0.0

        margin_pct = (margin / target * 100.0) if target != 0 else 0.0

        entry = SLOComplianceEntry(
            slo_name=slo.name,
            target=target,
            sla_target=sla,
            actual=actual,
            is_compliant=is_compliant,
            is_sla_compliant=is_sla_compliant,
            margin_pct=margin_pct,
        )

        violation: Optional[SLAViolation] = None
        if not is_compliant:
            violation = SLAViolation(
                slo_name=slo.name,
                timestamp=timestamp,
                actual_value=actual,
                target_value=target,
                sla_value=sla,
                is_sla_breach=not is_sla_compliant,
                details=(
                    f"SLO '{slo.name}' violated: actual={actual:.3f}, "
                    f"target={target:.3f}, sla={sla:.3f}"
                ),
            )

        return entry, violation

    @staticmethod
    def _compute_composite_score(entries: List[SLOComplianceEntry]) -> float:
        """Compute a weighted composite SLA score (0 - 100).

        Each SLO contributes equally. For each SLO, the score is:
            - 100  if meeting internal SLO target
            -  50  if meeting SLA but not internal SLO
            -   0  if breaching SLA

        This three-tier scoring highlights the distinction between internal
        operational health (SLO) and contractual obligations (SLA).
        """
        if not entries:
            return 100.0

        total = 0.0
        for entry in entries:
            if entry.is_compliant:
                total += 100.0
            elif entry.is_sla_compliant:
                total += 50.0
            else:
                total += 0.0

        return total / len(entries)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_default_sla_framework() -> Dict[str, object]:
    """Create a fully-wired SLA framework with default Netflix LLM SLOs.

    Returns a dictionary containing:
        - tracker:  ErrorBudgetTracker  (keyed to availability SLO)
        - policy:   ErrorBudgetPolicy
        - reporter: SLAComplianceReporter
        - slos:     list of all SLODefinition instances

    Usage::

        fw = create_default_sla_framework()
        gate = fw["policy"].evaluate_deployment_readiness()
        if gate.verdict == DeploymentGateVerdict.ALLOW:
            deploy()

    Prometheus metric (framework init):
        # netflix_llm:sla_framework:initialised_at
    """
    tracker = ErrorBudgetTracker(slo=AVAILABILITY_SLO)
    policy = ErrorBudgetPolicy(budget_tracker=tracker)
    reporter = SLAComplianceReporter(slos=ALL_SLOS)

    logger.info(
        "SLA framework initialised: budget=%.2f min, window=%dh, SLOs=%d",
        tracker.monthly_budget_minutes,
        AVAILABILITY_SLO.measurement_window_hours,
        len(ALL_SLOS),
    )

    return {
        "tracker": tracker,
        "policy": policy,
        "reporter": reporter,
        "slos": ALL_SLOS,
    }
