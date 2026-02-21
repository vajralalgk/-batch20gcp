"""Real-time cost monitoring with budget alerts.

Tracks GPU-hour and token consumption, projects future spend, and
fires alerts when configurable budget thresholds (50 %, 75 %, 90 %,
100 %) are breached. Supports per-region spend breakdowns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Severity level of a budget alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class BudgetPeriod(Enum):
    """Time period for budget limits."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# Alert thresholds expressed as (fraction_of_budget, severity).
_DEFAULT_ALERT_THRESHOLDS: list[tuple[float, AlertSeverity]] = [
    (0.50, AlertSeverity.INFO),
    (0.75, AlertSeverity.WARNING),
    (0.90, AlertSeverity.CRITICAL),
    (1.00, AlertSeverity.EMERGENCY),
]

# Approximate hours per period.
_HOURS_PER_PERIOD: dict[BudgetPeriod, float] = {
    BudgetPeriod.DAILY: 24.0,
    BudgetPeriod.WEEKLY: 168.0,
    BudgetPeriod.MONTHLY: 730.0,
}


@dataclass
class BudgetLimit:
    """A budget cap for a given time period.

    Attributes:
        period: The time period this limit covers.
        limit_usd: Maximum allowed spend in USD.
    """

    period: BudgetPeriod
    limit_usd: float


@dataclass
class UsageRecord:
    """A single usage data point.

    Attributes:
        gpu_hours: GPU-hours consumed.
        tokens: Number of tokens processed.
        cost_usd: Derived cost in USD.
        region: Region where the usage occurred.
        timestamp: Epoch timestamp of the recording.
    """

    gpu_hours: float
    tokens: int
    cost_usd: float
    region: str = "us-east-1"
    timestamp: float = field(default_factory=time.time)


@dataclass
class BudgetAlert:
    """An alert triggered by a budget threshold breach.

    Attributes:
        severity: Alert severity level.
        period: Budget period that was breached.
        threshold_pct: The threshold percentage that was crossed.
        current_spend: Current spend at the time of the alert.
        budget_limit: The budget cap for the period.
        message: Human-readable description.
        timestamp: When the alert was generated.
    """

    severity: AlertSeverity
    period: BudgetPeriod
    threshold_pct: float
    current_spend: float
    budget_limit: float
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class SpendForecast:
    """Projected spend for a given budget period.

    Attributes:
        period: The forecast period.
        current_spend: Spend accumulated so far.
        projected_spend: Estimated total spend by end of period.
        budget_limit: The cap for this period.
        on_track: Whether the projection stays within budget.
        burn_rate_per_hour: Current hourly spend rate.
        hours_remaining: Hours left in the period.
    """

    period: BudgetPeriod
    current_spend: float
    projected_spend: float
    budget_limit: float
    on_track: bool
    burn_rate_per_hour: float
    hours_remaining: float
    timestamp: float = field(default_factory=time.time)


class BudgetTracker:
    """Real-time cost monitoring and budget alerting.

    Records GPU-hour and token usage, computes running costs, and
    checks configurable budget limits at 50 %, 75 %, 90 %, and 100 %
    thresholds. Supports forecasting end-of-period spend and breaking
    costs down by region.

    Args:
        budget_limits: List of budget caps (one per period).
        cost_per_gpu_hour: Default cost per GPU-hour if not supplied
            per record.
        cost_per_1k_tokens: Default marginal cost per 1,000 tokens.
        alert_thresholds: Custom alert thresholds to override defaults.
    """

    def __init__(
        self,
        budget_limits: Optional[list[BudgetLimit]] = None,
        cost_per_gpu_hour: float = 3.50,
        cost_per_1k_tokens: float = 0.002,
        alert_thresholds: Optional[list[tuple[float, AlertSeverity]]] = None,
    ) -> None:
        self._budget_limits: dict[BudgetPeriod, float] = {}
        for bl in budget_limits or []:
            self._budget_limits[bl.period] = bl.limit_usd

        # Fill in defaults when not provided.
        self._budget_limits.setdefault(BudgetPeriod.DAILY, 5_000.0)
        self._budget_limits.setdefault(BudgetPeriod.WEEKLY, 30_000.0)
        self._budget_limits.setdefault(BudgetPeriod.MONTHLY, 120_000.0)

        self._cost_per_gpu_hour = cost_per_gpu_hour
        self._cost_per_1k_tokens = cost_per_1k_tokens
        self._alert_thresholds = alert_thresholds or list(_DEFAULT_ALERT_THRESHOLDS)

        self._usage_records: list[UsageRecord] = []
        self._alerts: list[BudgetAlert] = []
        self._fired_thresholds: dict[BudgetPeriod, set[float]] = {
            p: set() for p in BudgetPeriod
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_usage(
        self,
        gpu_hours: float,
        tokens: int,
        region: str = "us-east-1",
        cost_usd: Optional[float] = None,
    ) -> list[BudgetAlert]:
        """Record a usage event and return any newly triggered alerts.

        If ``cost_usd`` is not provided, it is derived from the default
        cost rates.

        Args:
            gpu_hours: GPU-hours consumed.
            tokens: Number of tokens processed.
            region: Region where the usage occurred.
            cost_usd: Explicit cost override (USD).

        Returns:
            List of alerts triggered by this recording (may be empty).
        """
        if cost_usd is None:
            cost_usd = (
                gpu_hours * self._cost_per_gpu_hour
                + (tokens / 1000.0) * self._cost_per_1k_tokens
            )

        record = UsageRecord(
            gpu_hours=gpu_hours,
            tokens=tokens,
            cost_usd=round(cost_usd, 4),
            region=region,
        )
        self._usage_records.append(record)
        logger.debug(
            "Recorded usage: %.2f GPU-hrs, %d tokens, $%.4f (%s).",
            gpu_hours,
            tokens,
            cost_usd,
            region,
        )

        return self.check_budget_alerts()

    def get_current_spend(self) -> dict[str, float]:
        """Return current spend aggregated by budget period.

        Returns:
            Dictionary keyed by period name with total spend in USD.
        """
        now = time.time()
        result: dict[str, float] = {}
        for period in BudgetPeriod:
            window_seconds = _HOURS_PER_PERIOD[period] * 3600
            cutoff = now - window_seconds
            spend = sum(
                r.cost_usd for r in self._usage_records if r.timestamp >= cutoff
            )
            result[period.value] = round(spend, 4)
        return result

    def get_forecast(self) -> list[SpendForecast]:
        """Project end-of-period spend for all budget periods.

        Uses the current burn rate (cost per hour over the last hour) to
        extrapolate.

        Returns:
            List of ``SpendForecast`` objects.
        """
        now = time.time()
        forecasts: list[SpendForecast] = []

        # Compute burn rate from the last hour of data.
        one_hour_ago = now - 3600.0
        recent_spend = sum(
            r.cost_usd for r in self._usage_records if r.timestamp >= one_hour_ago
        )
        burn_rate = recent_spend  # USD per hour

        for period in BudgetPeriod:
            window_seconds = _HOURS_PER_PERIOD[period] * 3600
            cutoff = now - window_seconds
            current_spend = sum(
                r.cost_usd for r in self._usage_records if r.timestamp >= cutoff
            )

            # Estimate how many hours remain in the period from the
            # earliest record's perspective.  For simplicity we use the
            # full period length minus elapsed time since the oldest
            # record in this window.
            records_in_window = [
                r for r in self._usage_records if r.timestamp >= cutoff
            ]
            if records_in_window:
                elapsed_h = (now - records_in_window[0].timestamp) / 3600.0
            else:
                elapsed_h = 0.0
            hours_remaining = max(0.0, _HOURS_PER_PERIOD[period] - elapsed_h)

            projected = current_spend + burn_rate * hours_remaining
            limit = self._budget_limits[period]

            forecasts.append(
                SpendForecast(
                    period=period,
                    current_spend=round(current_spend, 4),
                    projected_spend=round(projected, 2),
                    budget_limit=limit,
                    on_track=projected <= limit,
                    burn_rate_per_hour=round(burn_rate, 4),
                    hours_remaining=round(hours_remaining, 2),
                )
            )

        return forecasts

    def check_budget_alerts(self) -> list[BudgetAlert]:
        """Evaluate current spend against all budget thresholds.

        Alerts are deduplicated: each (period, threshold) pair fires at
        most once until ``reset_alerts`` is called.

        Returns:
            Newly triggered alerts.
        """
        current_spend = self.get_current_spend()
        new_alerts: list[BudgetAlert] = []

        for period in BudgetPeriod:
            spend = current_spend[period.value]
            limit = self._budget_limits[period]
            if limit <= 0:
                continue

            ratio = spend / limit
            for threshold, severity in self._alert_thresholds:
                if ratio >= threshold and threshold not in self._fired_thresholds[period]:
                    alert = BudgetAlert(
                        severity=severity,
                        period=period,
                        threshold_pct=threshold * 100,
                        current_spend=round(spend, 2),
                        budget_limit=limit,
                        message=(
                            f"{period.value.capitalize()} spend ${spend:,.2f} has "
                            f"reached {threshold:.0%} of ${limit:,.2f} budget."
                        ),
                    )
                    new_alerts.append(alert)
                    self._alerts.append(alert)
                    self._fired_thresholds[period].add(threshold)
                    logger.warning("Budget alert: %s", alert.message)

        return new_alerts

    def get_breakdown_by_region(self) -> dict[str, dict[str, float]]:
        """Break down spend and usage by region for each budget period.

        Returns:
            Nested dictionary: ``{period: {region: spend_usd}}``.
        """
        now = time.time()
        breakdown: dict[str, dict[str, float]] = {}

        for period in BudgetPeriod:
            window_seconds = _HOURS_PER_PERIOD[period] * 3600
            cutoff = now - window_seconds
            region_spend: dict[str, float] = {}
            for r in self._usage_records:
                if r.timestamp >= cutoff:
                    region_spend[r.region] = region_spend.get(r.region, 0.0) + r.cost_usd
            breakdown[period.value] = {
                k: round(v, 4) for k, v in sorted(region_spend.items())
            }

        return breakdown

    def reset_alerts(self) -> None:
        """Clear fired-threshold memory so alerts can re-trigger."""
        self._fired_thresholds = {p: set() for p in BudgetPeriod}
        logger.info("Budget alert thresholds reset.")

    def get_all_alerts(self) -> list[BudgetAlert]:
        """Return every alert that has been fired during this tracker's lifetime."""
        return list(self._alerts)
