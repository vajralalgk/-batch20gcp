"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Model Registry & Versioning
============================================================================

Provides model version management, deployment tracking, S3 artifact
storage integration, A/B testing with traffic splitting, and rich
metadata storage (model size, quantization type, performance benchmarks).

Supports an in-memory backend for development and testing as well as
a production path backed by S3 + an external metadata store.

Usage:
    from src.inference.model_registry import ModelRegistry

    registry = ModelRegistry(s3_bucket="netflix-ml-models")
    version = await registry.register_model(
        model_name="netflix_llm",
        version="2.1.0",
        artifact_path="s3://netflix-ml-models/llm/v2.1.0/",
    )
    active = await registry.get_active_model("netflix_llm")
============================================================================
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ModelStatus(str, Enum):
    """Lifecycle status of a model version."""
    REGISTERED = "REGISTERED"
    VALIDATING = "VALIDATING"
    DEPLOYING = "DEPLOYING"
    ACTIVE = "ACTIVE"
    CANARY = "CANARY"
    SHADOW = "SHADOW"
    DRAINING = "DRAINING"
    ROLLED_BACK = "ROLLED_BACK"
    ARCHIVED = "ARCHIVED"


class DeploymentStrategy(str, Enum):
    """Deployment roll-out strategy."""
    FULL = "FULL"
    CANARY = "CANARY"
    BLUE_GREEN = "BLUE_GREEN"
    SHADOW = "SHADOW"
    AB_TEST = "AB_TEST"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ModelBenchmark:
    """Performance benchmarks captured during model validation."""
    throughput_rps: float = 0.0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    tokens_per_second: float = 0.0
    memory_gb: float = 0.0
    accuracy_score: float = 0.0
    benchmark_dataset: str = ""
    benchmark_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "throughput_rps": round(self.throughput_rps, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "p99_latency_ms": round(self.p99_latency_ms, 3),
            "tokens_per_second": round(self.tokens_per_second, 1),
            "memory_gb": round(self.memory_gb, 3),
            "accuracy_score": round(self.accuracy_score, 6),
            "benchmark_dataset": self.benchmark_dataset,
            "benchmark_date": self.benchmark_date,
        }


@dataclass
class ABTestConfig:
    """Configuration for an A/B test between two model versions."""
    experiment_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    control_version: str = ""
    treatment_version: str = ""
    traffic_split_pct: float = 50.0  # percentage routed to treatment
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    target_metric: str = "click_through_rate"
    min_sample_size: int = 10_000
    is_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "control_version": self.control_version,
            "treatment_version": self.treatment_version,
            "traffic_split_pct": self.traffic_split_pct,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "target_metric": self.target_metric,
            "min_sample_size": self.min_sample_size,
            "is_active": self.is_active,
        }


@dataclass
class ModelVersion:
    """Complete metadata for a registered model version."""
    model_name: str
    version: str
    status: ModelStatus = ModelStatus.REGISTERED
    artifact_path: str = ""
    artifact_hash: str = ""
    model_size_gb: float = 0.0
    quantization_type: str = "INT8"
    framework: str = "tensorrt-llm"
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    benchmarks: Optional[ModelBenchmark] = None
    deployment_strategy: DeploymentStrategy = DeploymentStrategy.FULL
    traffic_pct: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    created_by: str = ""
    deployment_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "version": self.version,
            "status": self.status.value,
            "artifact_path": self.artifact_path,
            "artifact_hash": self.artifact_hash,
            "model_size_gb": round(self.model_size_gb, 3),
            "quantization_type": self.quantization_type,
            "framework": self.framework,
            "description": self.description,
            "tags": self.tags,
            "benchmarks": self.benchmarks.to_dict() if self.benchmarks else None,
            "deployment_strategy": self.deployment_strategy.value,
            "traffic_pct": round(self.traffic_pct, 2),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "deployment_id": self.deployment_id,
        }


# ---------------------------------------------------------------------------
# Registry implementation
# ---------------------------------------------------------------------------

class ModelRegistry:
    """Versioned model registry with S3 artifact storage and A/B testing.

    Parameters
    ----------
    s3_bucket:
        S3 bucket for model artifact storage.
    s3_prefix:
        Key prefix under the bucket (e.g. ``"models/"``).
    aws_region:
        AWS region for S3 operations.
    mock_mode:
        When ``True``, S3 interactions are simulated in memory.
    max_versions_per_model:
        Maximum number of versions retained per model name before the
        oldest archived versions are pruned.
    """

    def __init__(
        self,
        s3_bucket: str = "netflix-ml-models",
        s3_prefix: str = "models/",
        aws_region: str = "us-west-2",
        mock_mode: bool = True,
        max_versions_per_model: int = 50,
    ) -> None:
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix
        self._aws_region = aws_region
        self._mock_mode = mock_mode
        self._max_versions = max_versions_per_model

        # In-memory stores
        self._models: Dict[str, Dict[str, ModelVersion]] = {}  # {model_name: {version: ModelVersion}}
        self._active_versions: Dict[str, str] = {}  # {model_name: version}
        self._ab_tests: Dict[str, ABTestConfig] = {}  # {experiment_id: ABTestConfig}
        self._rollback_history: Dict[str, List[Dict[str, Any]]] = {}  # {model_name: [...]}

        self._lock = asyncio.Lock()

        # Lazy-initialized S3 client
        self._s3_client: Optional[Any] = None

        logger.info(
            "model_registry_init",
            extra={
                "s3_bucket": s3_bucket,
                "s3_prefix": s3_prefix,
                "mock_mode": mock_mode,
            },
        )

    # ------------------------------------------------------------------
    # S3 client
    # ------------------------------------------------------------------

    async def _get_s3_client(self) -> Any:
        """Return a (lazily-initialised) S3 client."""
        if self._s3_client is not None:
            return self._s3_client

        if self._mock_mode:
            self._s3_client = _MockS3Client()
            return self._s3_client

        try:
            import aiobotocore  # type: ignore[import-untyped]
            from aiobotocore.session import get_session  # type: ignore[import-untyped]

            session = get_session()
            ctx = session.create_client(
                "s3", region_name=self._aws_region
            )
            self._s3_client = await ctx.__aenter__()
            return self._s3_client
        except ImportError:
            logger.warning("aiobotocore_not_installed_using_mock_s3")
            self._s3_client = _MockS3Client()
            return self._s3_client

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_model(
        self,
        model_name: str,
        version: str,
        artifact_path: str = "",
        model_size_gb: float = 0.0,
        quantization_type: str = "INT8",
        framework: str = "tensorrt-llm",
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
        benchmarks: Optional[ModelBenchmark] = None,
        created_by: str = "",
        auto_activate: bool = False,
    ) -> ModelVersion:
        """Register a new model version in the registry.

        Parameters
        ----------
        model_name:
            Logical name of the model family (e.g. ``"netflix_llm"``).
        version:
            Semver or arbitrary version string.
        artifact_path:
            S3 URI or local path to the model artifact.
        model_size_gb:
            Size of the model on disk in GB.
        quantization_type:
            Quantization scheme applied (e.g. ``"INT8"``, ``"FP16"``).
        framework:
            Inference framework (e.g. ``"tensorrt-llm"``).
        description:
            Human-readable description of the version.
        tags:
            Arbitrary key-value metadata.
        benchmarks:
            Optional performance benchmarks.
        created_by:
            Identity of the registrant.
        auto_activate:
            When ``True`` the version is immediately promoted to ACTIVE.

        Returns
        -------
        ModelVersion
            The newly registered model version metadata.
        """
        async with self._lock:
            if model_name not in self._models:
                self._models[model_name] = {}

            if version in self._models[model_name]:
                raise ValueError(
                    f"Version {version!r} already registered for model {model_name!r}"
                )

            # Compute artifact hash for integrity verification
            artifact_hash = hashlib.sha256(
                f"{model_name}:{version}:{artifact_path}".encode()
            ).hexdigest()[:16]

            model_version = ModelVersion(
                model_name=model_name,
                version=version,
                artifact_path=artifact_path or self._default_artifact_path(model_name, version),
                artifact_hash=artifact_hash,
                model_size_gb=model_size_gb,
                quantization_type=quantization_type,
                framework=framework,
                description=description,
                tags=tags or {},
                benchmarks=benchmarks,
                created_by=created_by,
            )

            # Upload artifact metadata to S3
            await self._store_artifact_metadata(model_version)

            self._models[model_name][version] = model_version

            if auto_activate:
                await self._activate_version(model_name, version)

            # Prune old archived versions if over limit
            self._prune_versions(model_name)

            logger.info(
                "model_registered",
                extra={
                    "model_name": model_name,
                    "version": version,
                    "artifact_path": model_version.artifact_path,
                    "auto_activate": auto_activate,
                },
            )
            return model_version

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def get_active_model(self, model_name: str) -> Optional[ModelVersion]:
        """Return the currently active version for *model_name*.

        Returns ``None`` if no version is active.
        """
        async with self._lock:
            version_str = self._active_versions.get(model_name)
            if version_str is None:
                return None
            versions = self._models.get(model_name, {})
            return versions.get(version_str)

    async def get_model_version(
        self, model_name: str, version: str
    ) -> Optional[ModelVersion]:
        """Retrieve metadata for a specific model version."""
        async with self._lock:
            return self._models.get(model_name, {}).get(version)

    async def list_versions(
        self,
        model_name: str,
        status_filter: Optional[ModelStatus] = None,
    ) -> List[ModelVersion]:
        """List all registered versions for a model, optionally filtered by status.

        Returns versions sorted newest-first by ``created_at``.
        """
        async with self._lock:
            versions = list(self._models.get(model_name, {}).values())
            if status_filter is not None:
                versions = [v for v in versions if v.status == status_filter]
            versions.sort(key=lambda v: v.created_at, reverse=True)
            return versions

    async def list_models(self) -> List[Dict[str, Any]]:
        """Return a summary for every registered model family."""
        async with self._lock:
            summaries: List[Dict[str, Any]] = []
            for model_name, versions in self._models.items():
                active = self._active_versions.get(model_name)
                summaries.append({
                    "model_name": model_name,
                    "total_versions": len(versions),
                    "active_version": active,
                    "latest_version": max(versions.keys()) if versions else None,
                })
            return summaries

    # ------------------------------------------------------------------
    # Activation & rollback
    # ------------------------------------------------------------------

    async def activate_version(
        self,
        model_name: str,
        version: str,
        strategy: DeploymentStrategy = DeploymentStrategy.FULL,
        traffic_pct: float = 100.0,
    ) -> ModelVersion:
        """Promote a version to ACTIVE and begin serving traffic.

        Parameters
        ----------
        model_name:
            The model family.
        version:
            The version to activate.
        strategy:
            Deployment strategy (full, canary, blue-green, etc.).
        traffic_pct:
            Percentage of traffic to direct to this version
            (relevant for canary / A/B).

        Returns
        -------
        ModelVersion
            The activated version with updated status.
        """
        async with self._lock:
            model_version = self._require_version(model_name, version)

            # Record the previous active version for rollback
            prev_active = self._active_versions.get(model_name)
            if prev_active and prev_active != version:
                prev_mv = self._models[model_name][prev_active]
                if strategy == DeploymentStrategy.FULL:
                    prev_mv.status = ModelStatus.DRAINING
                    prev_mv.traffic_pct = 0.0
                    prev_mv.updated_at = _now_iso()

            model_version.status = ModelStatus.ACTIVE
            model_version.deployment_strategy = strategy
            model_version.traffic_pct = traffic_pct
            model_version.updated_at = _now_iso()

            self._active_versions[model_name] = version

            # Rollback history
            self._rollback_history.setdefault(model_name, []).append({
                "action": "activate",
                "version": version,
                "previous_version": prev_active,
                "strategy": strategy.value,
                "timestamp": _now_iso(),
            })

            logger.info(
                "model_activated",
                extra={
                    "model_name": model_name,
                    "version": version,
                    "strategy": strategy.value,
                    "traffic_pct": traffic_pct,
                },
            )
            return model_version

    async def rollback(
        self,
        model_name: str,
        target_version: Optional[str] = None,
        reason: str = "",
    ) -> ModelVersion:
        """Roll back to a previous model version.

        When *target_version* is ``None`` the most recent previously-active
        version is restored from the rollback history.

        Parameters
        ----------
        model_name:
            The model family to roll back.
        target_version:
            Explicit version to roll back to.  Inferred from history when
            omitted.
        reason:
            Human-readable reason for the rollback (stored in history).

        Returns
        -------
        ModelVersion
            The now-active version after rollback.
        """
        async with self._lock:
            current_active = self._active_versions.get(model_name)

            if target_version is None:
                target_version = self._find_rollback_target(model_name)
                if target_version is None:
                    raise ValueError(
                        f"No rollback target found for model {model_name!r}"
                    )

            target_mv = self._require_version(model_name, target_version)

            # Deactivate current
            if current_active and current_active in self._models.get(model_name, {}):
                current_mv = self._models[model_name][current_active]
                current_mv.status = ModelStatus.ROLLED_BACK
                current_mv.traffic_pct = 0.0
                current_mv.updated_at = _now_iso()

            # Activate target
            target_mv.status = ModelStatus.ACTIVE
            target_mv.traffic_pct = 100.0
            target_mv.deployment_strategy = DeploymentStrategy.FULL
            target_mv.updated_at = _now_iso()
            self._active_versions[model_name] = target_version

            self._rollback_history.setdefault(model_name, []).append({
                "action": "rollback",
                "from_version": current_active,
                "to_version": target_version,
                "reason": reason,
                "timestamp": _now_iso(),
            })

            logger.info(
                "model_rolled_back",
                extra={
                    "model_name": model_name,
                    "from": current_active,
                    "to": target_version,
                    "reason": reason,
                },
            )
            return target_mv

    # ------------------------------------------------------------------
    # A/B testing
    # ------------------------------------------------------------------

    async def create_ab_test(
        self,
        model_name: str,
        control_version: str,
        treatment_version: str,
        traffic_split_pct: float = 50.0,
        target_metric: str = "click_through_rate",
        min_sample_size: int = 10_000,
    ) -> ABTestConfig:
        """Create an A/B test between two model versions.

        Parameters
        ----------
        model_name:
            Model family under test.
        control_version:
            The baseline version.
        treatment_version:
            The challenger version.
        traffic_split_pct:
            Percentage of traffic routed to the treatment arm.
        target_metric:
            Primary evaluation metric.
        min_sample_size:
            Minimum observations before the test can be evaluated.

        Returns
        -------
        ABTestConfig
            The created experiment configuration.
        """
        async with self._lock:
            # Validate both versions exist
            self._require_version(model_name, control_version)
            self._require_version(model_name, treatment_version)

            ab_config = ABTestConfig(
                control_version=control_version,
                treatment_version=treatment_version,
                traffic_split_pct=traffic_split_pct,
                start_time=_now_iso(),
                target_metric=target_metric,
                min_sample_size=min_sample_size,
                is_active=True,
            )

            # Update version traffic splits
            control_mv = self._models[model_name][control_version]
            treatment_mv = self._models[model_name][treatment_version]

            control_mv.status = ModelStatus.ACTIVE
            control_mv.deployment_strategy = DeploymentStrategy.AB_TEST
            control_mv.traffic_pct = 100.0 - traffic_split_pct
            control_mv.updated_at = _now_iso()

            treatment_mv.status = ModelStatus.CANARY
            treatment_mv.deployment_strategy = DeploymentStrategy.AB_TEST
            treatment_mv.traffic_pct = traffic_split_pct
            treatment_mv.updated_at = _now_iso()

            self._ab_tests[ab_config.experiment_id] = ab_config

            logger.info(
                "ab_test_created",
                extra={
                    "experiment_id": ab_config.experiment_id,
                    "control": control_version,
                    "treatment": treatment_version,
                    "split_pct": traffic_split_pct,
                },
            )
            return ab_config

    async def resolve_model_for_request(
        self,
        model_name: str,
        user_id: Optional[str] = None,
    ) -> Optional[ModelVersion]:
        """Determine which model version should serve a given request.

        When an A/B test is active, the *user_id* is hashed to
        deterministically assign the request to either the control or
        treatment arm, ensuring user-level consistency.

        Parameters
        ----------
        model_name:
            Model family to resolve.
        user_id:
            End-user identifier for deterministic bucketing.

        Returns
        -------
        ModelVersion | None
            The version to serve, or ``None`` if no version is active.
        """
        async with self._lock:
            # Check for active A/B test first
            for ab_config in self._ab_tests.values():
                if not ab_config.is_active:
                    continue
                control = self._models.get(model_name, {}).get(ab_config.control_version)
                treatment = self._models.get(model_name, {}).get(ab_config.treatment_version)
                if control is None or treatment is None:
                    continue

                # Deterministic bucketing using user_id hash
                if user_id:
                    hash_val = int(
                        hashlib.md5(user_id.encode()).hexdigest(), 16
                    ) % 100
                else:
                    import random
                    hash_val = random.randint(0, 99)

                if hash_val < ab_config.traffic_split_pct:
                    return copy.deepcopy(treatment)
                return copy.deepcopy(control)

            # No A/B test -- return active version
            version_str = self._active_versions.get(model_name)
            if version_str is None:
                return None
            mv = self._models.get(model_name, {}).get(version_str)
            return copy.deepcopy(mv) if mv else None

    async def stop_ab_test(self, experiment_id: str, winner: str = "control") -> ABTestConfig:
        """Conclude an A/B test and promote the winning variant.

        Parameters
        ----------
        experiment_id:
            The experiment to conclude.
        winner:
            ``"control"`` or ``"treatment"``.

        Returns
        -------
        ABTestConfig
            The concluded experiment configuration.
        """
        async with self._lock:
            ab_config = self._ab_tests.get(experiment_id)
            if ab_config is None:
                raise ValueError(f"A/B test {experiment_id!r} not found")

            ab_config.is_active = False
            ab_config.end_time = _now_iso()

            winning_version = (
                ab_config.control_version if winner == "control"
                else ab_config.treatment_version
            )
            losing_version = (
                ab_config.treatment_version if winner == "control"
                else ab_config.control_version
            )

            # Find the model name by looking up the control version
            model_name: Optional[str] = None
            for mname, versions in self._models.items():
                if ab_config.control_version in versions:
                    model_name = mname
                    break

            if model_name is not None:
                winning_mv = self._models[model_name].get(winning_version)
                losing_mv = self._models[model_name].get(losing_version)
                if winning_mv:
                    winning_mv.status = ModelStatus.ACTIVE
                    winning_mv.traffic_pct = 100.0
                    winning_mv.deployment_strategy = DeploymentStrategy.FULL
                    winning_mv.updated_at = _now_iso()
                    self._active_versions[model_name] = winning_version
                if losing_mv:
                    losing_mv.status = ModelStatus.ARCHIVED
                    losing_mv.traffic_pct = 0.0
                    losing_mv.updated_at = _now_iso()

            logger.info(
                "ab_test_concluded",
                extra={
                    "experiment_id": experiment_id,
                    "winner": winner,
                    "winning_version": winning_version,
                },
            )
            return ab_config

    async def list_ab_tests(
        self, active_only: bool = False
    ) -> List[Dict[str, Any]]:
        """List all A/B test experiments."""
        async with self._lock:
            tests = list(self._ab_tests.values())
            if active_only:
                tests = [t for t in tests if t.is_active]
            return [t.to_dict() for t in tests]

    # ------------------------------------------------------------------
    # S3 artifact helpers
    # ------------------------------------------------------------------

    async def _store_artifact_metadata(self, model_version: ModelVersion) -> None:
        """Persist model version metadata to S3."""
        s3 = await self._get_s3_client()
        key = (
            f"{self._s3_prefix}{model_version.model_name}/"
            f"{model_version.version}/metadata.json"
        )

        import json
        body = json.dumps(model_version.to_dict(), indent=2).encode()

        try:
            await s3.put_object(
                Bucket=self._s3_bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            logger.debug(
                "model_metadata_stored",
                extra={"bucket": self._s3_bucket, "key": key},
            )
        except Exception as exc:
            logger.error(
                "model_metadata_store_failed",
                extra={"error": str(exc), "key": key},
            )

    async def download_artifact(
        self, model_name: str, version: str, local_path: str
    ) -> str:
        """Download model artifacts from S3 to a local path.

        Returns the local filesystem path to the downloaded artifact.
        """
        async with self._lock:
            mv = self._require_version(model_name, version)

        s3 = await self._get_s3_client()
        s3_key = f"{self._s3_prefix}{model_name}/{version}/model.tar.gz"

        try:
            resp = await s3.get_object(Bucket=self._s3_bucket, Key=s3_key)
            body = await resp["Body"].read() if hasattr(resp.get("Body"), "read") else b""

            import os
            os.makedirs(local_path, exist_ok=True)
            dest = os.path.join(local_path, "model.tar.gz")
            with open(dest, "wb") as f:
                f.write(body)

            logger.info(
                "artifact_downloaded",
                extra={"model": model_name, "version": version, "path": dest},
            )
            return dest
        except Exception as exc:
            logger.error("artifact_download_failed", extra={"error": str(exc)})
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_version(self, model_name: str, version: str) -> ModelVersion:
        """Look up a version or raise ValueError."""
        versions = self._models.get(model_name, {})
        mv = versions.get(version)
        if mv is None:
            raise ValueError(
                f"Model {model_name!r} version {version!r} not found"
            )
        return mv

    async def _activate_version(self, model_name: str, version: str) -> None:
        """Internal activation (already under lock)."""
        mv = self._require_version(model_name, version)
        prev = self._active_versions.get(model_name)
        if prev and prev in self._models.get(model_name, {}):
            self._models[model_name][prev].status = ModelStatus.DRAINING
            self._models[model_name][prev].traffic_pct = 0.0

        mv.status = ModelStatus.ACTIVE
        mv.traffic_pct = 100.0
        self._active_versions[model_name] = version

    def _find_rollback_target(self, model_name: str) -> Optional[str]:
        """Walk rollback history to find the last non-current active version."""
        history = self._rollback_history.get(model_name, [])
        for entry in reversed(history):
            prev = entry.get("previous_version") or entry.get("from_version")
            if prev and prev != self._active_versions.get(model_name):
                return prev
        return None

    def _prune_versions(self, model_name: str) -> None:
        """Remove oldest archived versions beyond the retention limit."""
        versions = self._models.get(model_name, {})
        if len(versions) <= self._max_versions:
            return

        archived = sorted(
            [v for v in versions.values() if v.status == ModelStatus.ARCHIVED],
            key=lambda v: v.created_at,
        )
        to_remove = len(versions) - self._max_versions
        for mv in archived[:to_remove]:
            del versions[mv.version]
            logger.info(
                "model_version_pruned",
                extra={"model": model_name, "version": mv.version},
            )

    def _default_artifact_path(self, model_name: str, version: str) -> str:
        return f"s3://{self._s3_bucket}/{self._s3_prefix}{model_name}/{version}/"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._models.values())
        return (
            f"ModelRegistry(bucket={self._s3_bucket!r}, "
            f"models={len(self._models)}, "
            f"total_versions={total}, "
            f"mock={self._mock_mode})"
        )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Mock S3 client
# ---------------------------------------------------------------------------

class _MockS3Client:
    """In-memory S3 stub for testing without AWS credentials."""

    def __init__(self) -> None:
        self._objects: Dict[str, bytes] = {}

    async def put_object(
        self, Bucket: str, Key: str, Body: bytes, **kwargs: Any
    ) -> Dict[str, Any]:
        full_key = f"{Bucket}/{Key}"
        self._objects[full_key] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    async def get_object(
        self, Bucket: str, Key: str, **kwargs: Any
    ) -> Dict[str, Any]:
        full_key = f"{Bucket}/{Key}"
        body = self._objects.get(full_key, b"")
        return {
            "Body": _MockStreamBody(body),
            "ContentLength": len(body),
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

    async def list_objects_v2(
        self, Bucket: str, Prefix: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        prefix = f"{Bucket}/{Prefix}"
        contents = [
            {"Key": k.split("/", 1)[1], "Size": len(v)}
            for k, v in self._objects.items()
            if k.startswith(prefix)
        ]
        return {"Contents": contents, "KeyCount": len(contents)}

    async def delete_object(
        self, Bucket: str, Key: str, **kwargs: Any
    ) -> Dict[str, Any]:
        full_key = f"{Bucket}/{Key}"
        self._objects.pop(full_key, None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}


class _MockStreamBody:
    """Mimics the async-readable Body returned by aiobotocore."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data
