"""
Feature Engine for Netflix Real-Time LLM Personalization Platform.

Real-time feature computation service that derives personalisation signals
from user behaviour, temporal patterns, regional trends, and content
metadata.  All feature paths are optimised for sub-10 ms latency so they
can be evaluated inline during every recommendation request.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENRE_LIST: List[str] = [
    "action",
    "adventure",
    "animation",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "family",
    "fantasy",
    "horror",
    "mystery",
    "romance",
    "sci-fi",
    "thriller",
    "western",
]
"""Canonical genre taxonomy used for affinity scoring."""

RECENCY_HALF_LIFE_HOURS: float = 48.0
"""Exponential decay half-life for the recency feature."""

POPULARITY_SMOOTHING: float = 1.0
"""Laplace smoothing constant for popularity scores."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class TimeBucket(str, Enum):
    """Coarse time-of-day bucketing for temporal features."""

    EARLY_MORNING = "early_morning"   # 05:00 - 08:59
    MORNING = "morning"               # 09:00 - 11:59
    AFTERNOON = "afternoon"           # 12:00 - 16:59
    EVENING = "evening"               # 17:00 - 20:59
    NIGHT = "night"                   # 21:00 - 00:59
    LATE_NIGHT = "late_night"         # 01:00 - 04:59


class DayOfWeek(str, Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


@dataclass(slots=True)
class UserPreferences:
    """Materialised user preference profile."""

    user_id: str
    genre_affinity: Dict[str, float] = field(default_factory=dict)
    avg_session_duration_min: float = 0.0
    preferred_time_buckets: List[TimeBucket] = field(default_factory=list)
    content_maturity_preference: str = "all"
    language_preferences: List[str] = field(default_factory=lambda: ["en"])
    last_active_at: float = 0.0


@dataclass(slots=True)
class FeatureContext:
    """Caller-supplied context for a single feature computation request."""

    region: str = "us-east-1"
    device_type: str = "smart_tv"
    session_duration_s: float = 0.0
    query: Optional[str] = None
    candidate_content_ids: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FeatureVector:
    """Computed feature vector for a single user + context pair."""

    user_id: str
    time_of_day_bucket: str
    day_of_week: str
    hour_of_day: int
    is_weekend: bool
    region_trending: Dict[str, float]
    genre_affinity: Dict[str, float]
    recency_score: float
    popularity_score: float
    session_depth: float
    device_type: str
    computation_time_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "time_of_day_bucket": self.time_of_day_bucket,
            "day_of_week": self.day_of_week,
            "hour_of_day": self.hour_of_day,
            "is_weekend": self.is_weekend,
            "region_trending": self.region_trending,
            "genre_affinity": self.genre_affinity,
            "recency_score": self.recency_score,
            "popularity_score": self.popularity_score,
            "session_depth": self.session_depth,
            "device_type": self.device_type,
            "computation_time_ms": self.computation_time_ms,
        }


# ---------------------------------------------------------------------------
# Data providers (pluggable)
# ---------------------------------------------------------------------------

class TrendingProvider:
    """Interface for fetching regional trending signals.

    The default implementation returns empty data.  In production this is
    backed by a Flink streaming job writing to Redis.
    """

    def get_trending(self, region: str) -> Dict[str, float]:
        """Return genre -> trending score mapping for *region*."""
        return {}


class UserProfileProvider:
    """Interface for fetching pre-computed user profiles.

    The default implementation returns a neutral profile.  In production
    this is backed by the user-profile service / DynamoDB.
    """

    def get_profile(self, user_id: str) -> UserPreferences:
        return UserPreferences(user_id=user_id)


class PopularityProvider:
    """Interface for fetching content popularity counters."""

    def get_popularity(self, content_ids: Sequence[str]) -> Dict[str, float]:
        """Return content_id -> normalised popularity score."""
        return {cid: 0.5 for cid in content_ids}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class FeatureEngine:
    """Real-time feature computation engine (target < 10 ms per call).

    Parameters
    ----------
    trending_provider:
        Source for regional trending data.
    user_profile_provider:
        Source for pre-computed user profiles.
    popularity_provider:
        Source for content popularity counters.
    mock_mode:
        When *True* providers are replaced with deterministic stubs.
    """

    def __init__(
        self,
        trending_provider: Optional[TrendingProvider] = None,
        user_profile_provider: Optional[UserProfileProvider] = None,
        popularity_provider: Optional[PopularityProvider] = None,
        *,
        mock_mode: bool = False,
    ) -> None:
        self._mock_mode = mock_mode

        if mock_mode:
            self._trending = _MockTrendingProvider()
            self._profiles = _MockUserProfileProvider()
            self._popularity = _MockPopularityProvider()
        else:
            self._trending = trending_provider or TrendingProvider()
            self._profiles = user_profile_provider or UserProfileProvider()
            self._popularity = popularity_provider or PopularityProvider()

        # Stats
        self._stats: Dict[str, Any] = {
            "total_computations": 0,
            "avg_computation_ms": 0.0,
            "max_computation_ms": 0.0,
        }
        self._total_time_ms: float = 0.0

        logger.info("FeatureEngine initialised (mock=%s)", mock_mode)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_features(
        self,
        user_id: str,
        context: Optional[FeatureContext] = None,
    ) -> FeatureVector:
        """Compute the full feature vector for *user_id* and *context*.

        Orchestrates temporal, trending, preference, recency, and
        popularity sub-computations and returns a single ``FeatureVector``.
        """
        start = time.perf_counter()
        ctx = context or FeatureContext()

        time_features = self.get_time_based_features()
        trending = self.get_trending_signals(ctx.region)
        prefs = self.get_user_preferences(user_id)

        recency = self._compute_recency_score(prefs.last_active_at)
        popularity = self._compute_avg_popularity(ctx.candidate_content_ids)
        session_depth = self._compute_session_depth(ctx.session_duration_s)

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._record_stats(elapsed_ms)

        return FeatureVector(
            user_id=user_id,
            time_of_day_bucket=time_features["time_of_day_bucket"],
            day_of_week=time_features["day_of_week"],
            hour_of_day=time_features["hour_of_day"],
            is_weekend=time_features["is_weekend"],
            region_trending=trending,
            genre_affinity=prefs.genre_affinity,
            recency_score=recency,
            popularity_score=popularity,
            session_depth=session_depth,
            device_type=ctx.device_type,
            computation_time_ms=round(elapsed_ms, 3),
        )

    def get_trending_signals(self, region: str) -> Dict[str, float]:
        """Fetch and normalise trending signals for *region*."""
        raw = self._trending.get_trending(region)
        if not raw:
            return {}

        max_score = max(raw.values()) if raw else 1.0
        if max_score == 0.0:
            max_score = 1.0

        return {
            genre: round(score / max_score, 4)
            for genre, score in raw.items()
        }

    def get_time_based_features(self) -> Dict[str, Any]:
        """Derive temporal features from the current wall-clock time."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()  # 0=Monday

        bucket = self._hour_to_bucket(hour)
        day_name = list(DayOfWeek)[weekday].value
        is_weekend = weekday >= 5

        return {
            "time_of_day_bucket": bucket.value,
            "day_of_week": day_name,
            "hour_of_day": hour,
            "is_weekend": is_weekend,
        }

    def get_user_preferences(self, user_id: str) -> UserPreferences:
        """Retrieve (and lightly transform) the user preference profile."""
        prefs = self._profiles.get_profile(user_id)

        # Normalise genre affinities to sum to 1
        total = sum(prefs.genre_affinity.values())
        if total > 0:
            prefs.genre_affinity = {
                g: round(s / total, 4)
                for g, s in prefs.genre_affinity.items()
            }

        return prefs

    def aggregate_features(
        self,
        features_list: Sequence[FeatureVector],
    ) -> Dict[str, Any]:
        """Aggregate multiple feature vectors into summary statistics.

        Useful when combining features from several candidate items or
        for batch analytics.
        """
        if not features_list:
            return {"count": 0}

        count = len(features_list)

        # Average numeric scores
        avg_recency = sum(f.recency_score for f in features_list) / count
        avg_popularity = sum(f.popularity_score for f in features_list) / count
        avg_session_depth = sum(f.session_depth for f in features_list) / count
        avg_comp_time = sum(f.computation_time_ms for f in features_list) / count

        # Merge genre affinities (average per genre)
        genre_accum: Dict[str, float] = {}
        genre_count: Dict[str, int] = {}
        for fv in features_list:
            for genre, score in fv.genre_affinity.items():
                genre_accum[genre] = genre_accum.get(genre, 0.0) + score
                genre_count[genre] = genre_count.get(genre, 0) + 1

        merged_genres = {
            g: round(genre_accum[g] / genre_count[g], 4)
            for g in genre_accum
        }

        # Merge trending signals (union, average on overlap)
        trending_accum: Dict[str, float] = {}
        trending_count: Dict[str, int] = {}
        for fv in features_list:
            for genre, score in fv.region_trending.items():
                trending_accum[genre] = trending_accum.get(genre, 0.0) + score
                trending_count[genre] = trending_count.get(genre, 0) + 1

        merged_trending = {
            g: round(trending_accum[g] / trending_count[g], 4)
            for g in trending_accum
        }

        return {
            "count": count,
            "avg_recency_score": round(avg_recency, 4),
            "avg_popularity_score": round(avg_popularity, 4),
            "avg_session_depth": round(avg_session_depth, 4),
            "avg_computation_time_ms": round(avg_comp_time, 3),
            "merged_genre_affinity": merged_genres,
            "merged_trending_signals": merged_trending,
        }

    @property
    def stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal feature computations
    # ------------------------------------------------------------------

    @staticmethod
    def _hour_to_bucket(hour: int) -> TimeBucket:
        if 5 <= hour < 9:
            return TimeBucket.EARLY_MORNING
        if 9 <= hour < 12:
            return TimeBucket.MORNING
        if 12 <= hour < 17:
            return TimeBucket.AFTERNOON
        if 17 <= hour < 21:
            return TimeBucket.EVENING
        if 21 <= hour <= 23 or hour == 0:
            return TimeBucket.NIGHT
        return TimeBucket.LATE_NIGHT

    @staticmethod
    def _compute_recency_score(last_active_at: float) -> float:
        """Exponential-decay recency based on time since last activity.

        Returns a value in (0, 1] where 1 means "just now" and values
        decay with a half-life of ``RECENCY_HALF_LIFE_HOURS``.
        """
        if last_active_at <= 0:
            return 0.0

        hours_elapsed = (time.time() - last_active_at) / 3600.0
        if hours_elapsed < 0:
            hours_elapsed = 0.0

        decay = math.exp(
            -math.log(2) * hours_elapsed / RECENCY_HALF_LIFE_HOURS
        )
        return round(min(max(decay, 0.0), 1.0), 4)

    def _compute_avg_popularity(
        self, content_ids: Sequence[str]
    ) -> float:
        """Average popularity score over a set of candidate content IDs."""
        if not content_ids:
            return 0.0

        scores = self._popularity.get_popularity(content_ids)
        if not scores:
            return 0.0

        total = sum(scores.values())
        return round(total / (len(scores) + POPULARITY_SMOOTHING), 4)

    @staticmethod
    def _compute_session_depth(session_duration_s: float) -> float:
        """Logarithmic session-depth feature.

        Maps session duration in seconds to a 0-1 scale using a
        saturating log curve.  A 30-minute session scores ~0.8.
        """
        if session_duration_s <= 0:
            return 0.0
        # log1p saturates nicely; scale so 1800s (30 min) ~ 0.8
        raw = math.log1p(session_duration_s) / math.log1p(3600.0)
        return round(min(raw, 1.0), 4)

    def _record_stats(self, elapsed_ms: float) -> None:
        self._stats["total_computations"] += 1
        self._total_time_ms += elapsed_ms
        self._stats["avg_computation_ms"] = round(
            self._total_time_ms / self._stats["total_computations"], 3
        )
        if elapsed_ms > self._stats["max_computation_ms"]:
            self._stats["max_computation_ms"] = round(elapsed_ms, 3)


# ---------------------------------------------------------------------------
# Mock providers for testing
# ---------------------------------------------------------------------------

class _MockTrendingProvider(TrendingProvider):
    """Deterministic trending data for unit tests."""

    _REGION_DATA: Dict[str, Dict[str, float]] = {
        "us-east-1": {
            "drama": 0.9,
            "comedy": 0.75,
            "thriller": 0.6,
            "sci-fi": 0.85,
            "documentary": 0.3,
        },
        "eu-west-1": {
            "drama": 0.8,
            "comedy": 0.6,
            "crime": 0.7,
            "romance": 0.5,
            "documentary": 0.65,
        },
        "ap-northeast-1": {
            "animation": 0.95,
            "drama": 0.7,
            "action": 0.8,
            "fantasy": 0.6,
            "horror": 0.4,
        },
    }

    def get_trending(self, region: str) -> Dict[str, float]:
        return dict(self._REGION_DATA.get(region, self._REGION_DATA["us-east-1"]))


class _MockUserProfileProvider(UserProfileProvider):
    """Deterministic user profiles seeded from the user_id hash."""

    def get_profile(self, user_id: str) -> UserPreferences:
        seed = hash(user_id) % 1000
        genre_affinity: Dict[str, float] = {}
        for idx, genre in enumerate(GENRE_LIST):
            genre_affinity[genre] = round(((seed + idx * 7) % 100) / 100.0, 2)

        return UserPreferences(
            user_id=user_id,
            genre_affinity=genre_affinity,
            avg_session_duration_min=20.0 + (seed % 40),
            preferred_time_buckets=[TimeBucket.EVENING, TimeBucket.NIGHT],
            content_maturity_preference="all",
            language_preferences=["en"],
            last_active_at=time.time() - (seed % 7200),
        )


class _MockPopularityProvider(PopularityProvider):
    """Deterministic popularity scores seeded from the content_id hash."""

    def get_popularity(self, content_ids: Sequence[str]) -> Dict[str, float]:
        return {
            cid: round((hash(cid) % 100) / 100.0, 2)
            for cid in content_ids
        }
