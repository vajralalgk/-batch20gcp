"""
LLM Re-ranker for Netflix Real-Time LLM Personalization Platform.

Takes the top-N candidates produced by embedding similarity and re-ranks
them using an LLM that considers rich user context (watch history, time of
day, region, trending signals).  Includes strict latency budgeting with
automatic fallback to the embedding-only ranking when the LLM exceeds the
configured timeout (default 150 ms).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_MS: int = 150
"""Maximum wall-clock milliseconds allowed for a single LLM inference call."""

DEFAULT_MAX_CANDIDATES: int = 50
"""Upper bound on candidates sent to the LLM prompt to control token usage."""

DEFAULT_MODEL_ID: str = "netflix-reranker-v2"
"""Default model identifier used when invoking the inference backend."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class UserContext:
    """Rich context describing the user's current session and preferences."""

    user_id: str
    watch_history: List[str] = field(default_factory=list)
    time_of_day: str = "evening"  # morning | afternoon | evening | night
    region: str = "us-east-1"
    trending_signals: Dict[str, float] = field(default_factory=dict)
    genre_preferences: List[str] = field(default_factory=list)
    device_type: str = "smart_tv"
    language: str = "en"


@dataclass(slots=True)
class Candidate:
    """A single content candidate with its embedding similarity score."""

    content_id: str
    title: str
    genres: List[str] = field(default_factory=list)
    similarity_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RerankResult:
    """Container for re-ranking output and diagnostics."""

    ranked_ids: List[str]
    scores: Dict[str, float]
    used_llm: bool
    latency_ms: float
    model_id: str = DEFAULT_MODEL_ID
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Inference backend protocol
# ---------------------------------------------------------------------------

class InferenceBackend(Protocol):
    """Minimal contract for the LLM inference service."""

    async def invoke(
        self, prompt: str, *, model_id: str, timeout_ms: int
    ) -> str: ...


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class LLMReranker:
    """LLM-powered re-ranker with latency-bounded inference and fallback.

    Parameters
    ----------
    inference_backend:
        Object implementing :class:`InferenceBackend`.
    timeout_ms:
        Hard wall-clock budget for the LLM call.
    max_candidates:
        Maximum number of candidates included in the prompt.
    model_id:
        Model identifier passed to the inference backend.
    mock_mode:
        When *True* the LLM call is replaced by a deterministic stub.
    """

    def __init__(
        self,
        inference_backend: Any = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        model_id: str = DEFAULT_MODEL_ID,
        mock_mode: bool = False,
    ) -> None:
        self._backend = inference_backend
        self._timeout_ms = timeout_ms
        self._max_candidates = max_candidates
        self._model_id = model_id
        self._mock_mode = mock_mode

        # Cumulative stats
        self._stats: Dict[str, Any] = {
            "total_requests": 0,
            "llm_successes": 0,
            "llm_timeouts": 0,
            "llm_errors": 0,
            "fallback_used": 0,
            "avg_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
        }
        self._latencies: List[float] = []

        if not mock_mode and inference_backend is None:
            raise ValueError(
                "inference_backend is required when mock_mode is False"
            )

        logger.info(
            "LLMReranker initialised (mock=%s, timeout=%dms, model=%s)",
            mock_mode,
            timeout_ms,
            model_id,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def rerank(
        self,
        user_context: UserContext,
        candidates: Sequence[Candidate],
    ) -> RerankResult:
        """Re-rank *candidates* using the LLM with automatic fallback.

        If the LLM call exceeds ``timeout_ms`` or raises an error the
        candidates are returned in their original embedding-similarity
        order.
        """
        self._stats["total_requests"] += 1
        start = time.perf_counter()

        # Truncate to max_candidates (already sorted by similarity_score desc)
        truncated = sorted(
            candidates, key=lambda c: c.similarity_score, reverse=True
        )[: self._max_candidates]

        # Attempt LLM re-ranking
        try:
            result = await self._invoke_llm(user_context, truncated)
            elapsed_ms = (time.perf_counter() - start) * 1000
            result.latency_ms = elapsed_ms
            self._record_latency(elapsed_ms)
            self._stats["llm_successes"] += 1
            return result
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "LLM re-rank timed out after %.1fms for user %s; "
                "falling back to embedding order",
                elapsed_ms,
                user_context.user_id,
            )
            self._stats["llm_timeouts"] += 1
            self._stats["fallback_used"] += 1
            return self._fallback_result(truncated, elapsed_ms, error="timeout")
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "LLM re-rank failed for user %s: %s",
                user_context.user_id,
                exc,
            )
            self._stats["llm_errors"] += 1
            self._stats["fallback_used"] += 1
            return self._fallback_result(
                truncated, elapsed_ms, error=str(exc)
            )

    def build_rerank_prompt(
        self,
        user_context: UserContext,
        candidates: Sequence[Candidate],
    ) -> str:
        """Build the structured prompt sent to the LLM for re-ranking.

        The prompt encodes user context and candidate metadata in a
        compact JSON-based format designed for minimal token usage while
        retaining the information the model needs to make preference-aware
        ranking decisions.
        """
        user_block = {
            "user_id": user_context.user_id,
            "watch_history_recent": user_context.watch_history[-20:],
            "time_of_day": user_context.time_of_day,
            "region": user_context.region,
            "trending": user_context.trending_signals,
            "genre_preferences": user_context.genre_preferences,
            "device": user_context.device_type,
            "language": user_context.language,
        }

        candidate_rows = []
        for idx, c in enumerate(candidates):
            candidate_rows.append(
                {
                    "idx": idx,
                    "id": c.content_id,
                    "title": c.title,
                    "genres": c.genres,
                    "sim": round(c.similarity_score, 4),
                }
            )

        prompt = (
            "You are the Netflix recommendation re-ranker. Given the user "
            "context and candidate list below, return a JSON array of "
            "candidate indices ordered from most to least relevant.\n\n"
            "### User Context\n"
            f"```json\n{json.dumps(user_block, separators=(',', ':'))}\n```\n\n"
            "### Candidates\n"
            f"```json\n{json.dumps(candidate_rows, separators=(',', ':'))}\n```\n\n"
            "### Instructions\n"
            "- Consider watch history, genre preferences, time of day, "
            "trending signals, and similarity scores.\n"
            "- Return ONLY a JSON object with key \"ranking\" containing an "
            "ordered array of candidate indices (integers) and key \"scores\" "
            'mapping each index to a relevance score in [0,1].\n'
            "- Example: {\"ranking\":[2,0,1],\"scores\":{\"0\":0.7,\"1\":0.5,\"2\":0.9}}\n"
        )
        return prompt

    def parse_rerank_response(
        self,
        response: str,
        candidates: Sequence[Candidate],
    ) -> RerankResult:
        """Parse the raw LLM JSON response into a ``RerankResult``.

        Falls back to the embedding-similarity order when parsing fails.
        """
        try:
            # Strip markdown code fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                # Remove opening and closing fences
                json_lines = [
                    l for l in lines if not l.strip().startswith("```")
                ]
                cleaned = "\n".join(json_lines)

            payload = json.loads(cleaned)
            ranking_indices: List[int] = payload["ranking"]
            raw_scores: Dict[str, float] = payload.get("scores", {})

            valid_range = set(range(len(candidates)))
            ranked_ids: List[str] = []
            scores: Dict[str, float] = {}

            for idx in ranking_indices:
                if idx in valid_range:
                    cid = candidates[idx].content_id
                    ranked_ids.append(cid)
                    score = raw_scores.get(str(idx), candidates[idx].similarity_score)
                    scores[cid] = float(score)

            # Append any candidates that were missing from the ranking
            seen = set(ranked_ids)
            for c in candidates:
                if c.content_id not in seen:
                    ranked_ids.append(c.content_id)
                    scores[c.content_id] = c.similarity_score

            return RerankResult(
                ranked_ids=ranked_ids,
                scores=scores,
                used_llm=True,
                latency_ms=0.0,  # caller fills this in
                model_id=self._model_id,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "Failed to parse LLM rerank response: %s. "
                "Falling back to embedding order.",
                exc,
            )
            return self._fallback_result(
                candidates, latency_ms=0.0, error=f"parse_error: {exc}"
            )

    def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of cumulative re-ranking statistics."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _invoke_llm(
        self,
        user_context: UserContext,
        candidates: Sequence[Candidate],
    ) -> RerankResult:
        """Build the prompt, call the backend, and parse the result."""
        prompt = self.build_rerank_prompt(user_context, candidates)

        if self._mock_mode:
            response_text = self._mock_inference(candidates)
        else:
            timeout_sec = self._timeout_ms / 1000.0
            response_text = await asyncio.wait_for(
                self._backend.invoke(
                    prompt,
                    model_id=self._model_id,
                    timeout_ms=self._timeout_ms,
                ),
                timeout=timeout_sec,
            )

        return self.parse_rerank_response(response_text, candidates)

    def _mock_inference(self, candidates: Sequence[Candidate]) -> str:
        """Deterministic mock that reverses the candidate order.

        This makes it trivially verifiable in tests while still exercising
        the full parse path.
        """
        indices = list(range(len(candidates)))
        indices.reverse()
        scores = {str(i): round(1.0 - i * 0.05, 4) for i in indices}
        return json.dumps({"ranking": indices, "scores": scores})

    def _fallback_result(
        self,
        candidates: Sequence[Candidate],
        latency_ms: float,
        *,
        error: Optional[str] = None,
    ) -> RerankResult:
        """Produce a result using the original embedding-similarity order."""
        sorted_candidates = sorted(
            candidates, key=lambda c: c.similarity_score, reverse=True
        )
        ranked_ids = [c.content_id for c in sorted_candidates]
        scores = {c.content_id: c.similarity_score for c in sorted_candidates}
        return RerankResult(
            ranked_ids=ranked_ids,
            scores=scores,
            used_llm=False,
            latency_ms=latency_ms,
            model_id=self._model_id,
            error=error,
        )

    def _record_latency(self, latency_ms: float) -> None:
        """Update rolling latency statistics."""
        self._latencies.append(latency_ms)
        # Keep a bounded window
        if len(self._latencies) > 10_000:
            self._latencies = self._latencies[-5_000:]

        self._stats["avg_latency_ms"] = round(
            sum(self._latencies) / len(self._latencies), 2
        )
        sorted_lat = sorted(self._latencies)
        p99_idx = int(len(sorted_lat) * 0.99)
        self._stats["p99_latency_ms"] = round(sorted_lat[min(p99_idx, len(sorted_lat) - 1)], 2)
