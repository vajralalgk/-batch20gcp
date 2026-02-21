"""Tests for src.personalization -- embedding store, LLM reranker, session memory."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.personalization.embedding_store import (
    EMBEDDING_DIM,
    EmbeddingRecord,
    EmbeddingStore,
)
from src.personalization.llm_reranker import (
    Candidate,
    LLMReranker,
    RerankResult,
    UserContext,
)
from src.personalization.session_memory import (
    Interaction,
    InteractionType,
    SessionData,
    SessionMemory,
)


# =========================================================================
# EmbeddingStore
# =========================================================================

class TestEmbeddingStoreGetUser:
    """Test user embedding retrieval."""

    def test_embedding_store_get_user(self, sample_user_embedding):
        store = EmbeddingStore(mock_mode=True)
        store.update_embedding("user:u1", sample_user_embedding)
        emb = store.get_user_embedding("u1")
        assert emb is not None
        assert emb.shape == (EMBEDDING_DIM,)
        np.testing.assert_allclose(emb, sample_user_embedding, atol=1e-6)

    def test_get_user_missing(self):
        store = EmbeddingStore(mock_mode=True)
        assert store.get_user_embedding("nonexistent") is None

    def test_get_content_embeddings(self, sample_content_embeddings):
        store = EmbeddingStore(mock_mode=True)
        for i, emb in enumerate(sample_content_embeddings):
            store.update_embedding(f"content:c{i}", emb)
        result = store.get_content_embeddings([f"c{i}" for i in range(5)])
        assert len(result) == 5
        for k, v in result.items():
            assert v.shape == (EMBEDDING_DIM,)


class TestEmbeddingStoreSimilarity:
    """Test cosine similarity computation."""

    def test_embedding_store_similarity(self, sample_user_embedding, sample_content_embeddings):
        store = EmbeddingStore(mock_mode=True)
        content_map = {f"c{i}": emb for i, emb in enumerate(sample_content_embeddings)}
        results = store.compute_similarity(sample_user_embedding, content_map)
        assert len(results) == len(content_map)
        # Results should be sorted descending by similarity
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)
        # Cosine similarity should be in [-1, 1]
        for _, score in results:
            assert -1.0 <= score <= 1.0 + 1e-6

    def test_similarity_identical_vectors(self):
        store = EmbeddingStore(mock_mode=True)
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        results = store.compute_similarity(vec, {"same": vec})
        assert len(results) == 1
        assert abs(results[0][1] - 1.0) < 1e-5

    def test_similarity_zero_vector(self):
        store = EmbeddingStore(mock_mode=True)
        zero = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        content = {"c1": np.random.randn(EMBEDDING_DIM).astype(np.float32)}
        results = store.compute_similarity(zero, content)
        assert results[0][1] == 0.0

    def test_update_wrong_dimension_raises(self):
        store = EmbeddingStore(mock_mode=True)
        wrong = np.random.randn(256).astype(np.float32)
        with pytest.raises(ValueError):
            store.update_embedding("user:bad", wrong)


# =========================================================================
# LLMReranker
# =========================================================================

def _make_candidates(n=5):
    return [
        Candidate(
            content_id=f"c{i}",
            title=f"Title {i}",
            genres=["drama", "action"],
            similarity_score=1.0 - i * 0.1,
        )
        for i in range(n)
    ]


def _make_user_context():
    return UserContext(
        user_id="u1",
        watch_history=["m1", "m2"],
        time_of_day="evening",
        region="us-east-1",
        genre_preferences=["drama"],
    )


class TestLLMRerankerBuildPrompt:
    """Test prompt construction."""

    def test_llm_reranker_build_prompt(self):
        reranker = LLMReranker(mock_mode=True)
        ctx = _make_user_context()
        candidates = _make_candidates()
        prompt = reranker.build_rerank_prompt(ctx, candidates)
        assert "Netflix" in prompt
        assert "u1" in prompt
        assert "c0" in prompt
        assert "ranking" in prompt


class TestLLMRerankerParseResponse:
    """Test response parsing."""

    def test_llm_reranker_parse_response(self):
        reranker = LLMReranker(mock_mode=True)
        candidates = _make_candidates(3)
        response = json.dumps({
            "ranking": [2, 0, 1],
            "scores": {"0": 0.7, "1": 0.5, "2": 0.9}
        })
        result = reranker.parse_rerank_response(response, candidates)
        assert result.ranked_ids[0] == "c2"
        assert result.used_llm is True
        assert result.scores["c2"] == 0.9

    def test_parse_invalid_json_falls_back(self):
        reranker = LLMReranker(mock_mode=True)
        candidates = _make_candidates(3)
        result = reranker.parse_rerank_response("NOT JSON", candidates)
        assert result.used_llm is False
        assert result.error is not None

    def test_parse_with_code_fences(self):
        reranker = LLMReranker(mock_mode=True)
        candidates = _make_candidates(2)
        response = '```json\n{"ranking":[1,0],"scores":{"0":0.4,"1":0.9}}\n```'
        result = reranker.parse_rerank_response(response, candidates)
        assert result.ranked_ids[0] == "c1"


class TestLLMRerankerTimeoutFallback:
    """Test timeout and fallback behavior."""

    @pytest.mark.asyncio
    async def test_llm_reranker_timeout_fallback(self):
        # Create a backend that always times out
        slow_backend = AsyncMock()
        slow_backend.invoke = AsyncMock(side_effect=asyncio.TimeoutError)

        reranker = LLMReranker(
            inference_backend=slow_backend,
            timeout_ms=10,
            mock_mode=False,
        )
        ctx = _make_user_context()
        candidates = _make_candidates(3)
        result = await reranker.rerank(ctx, candidates)
        assert result.used_llm is False
        assert result.error == "timeout"
        # Fallback preserves embedding-similarity order
        assert result.ranked_ids == ["c0", "c1", "c2"]

    @pytest.mark.asyncio
    async def test_mock_rerank_reverses_order(self):
        reranker = LLMReranker(mock_mode=True)
        ctx = _make_user_context()
        candidates = _make_candidates(3)
        result = await reranker.rerank(ctx, candidates)
        assert result.used_llm is True
        # Mock reverses order
        assert result.ranked_ids[0] == "c2"


# =========================================================================
# SessionMemory
# =========================================================================

class TestSessionMemoryCreate:
    """Test session creation."""

    def test_session_memory_create(self):
        mem = SessionMemory(mock_mode=True)
        sid = mem.create_session("user-1")
        assert sid.startswith("user-1:")
        ctx = mem.get_session_context(sid)
        assert ctx is not None
        assert ctx["user_id"] == "user-1"

    def test_create_multiple_sessions(self):
        mem = SessionMemory(mock_mode=True)
        s1 = mem.create_session("user-1")
        s2 = mem.create_session("user-1")
        assert s1 != s2  # Each call produces a unique ID


class TestSessionMemoryUpdate:
    """Test session updates."""

    def test_session_memory_update(self):
        mem = SessionMemory(mock_mode=True)
        sid = mem.create_session("user-1")
        interaction = Interaction(
            interaction_type=InteractionType.CLICK,
            content_id="movie-42",
            dwell_time_ms=5000,
        )
        result = mem.update_session(sid, interaction)
        assert result is True
        ctx = mem.get_session_context(sid)
        assert ctx["interaction_count"] == 1
        assert ctx["total_dwell_time_ms"] == 5000
        assert "movie-42" in ctx["click_pattern"]

    def test_update_nonexistent_session(self):
        mem = SessionMemory(mock_mode=True)
        interaction = Interaction(interaction_type=InteractionType.BROWSE)
        result = mem.update_session("nonexistent", interaction)
        assert result is False

    def test_browse_updates_history(self):
        mem = SessionMemory(mock_mode=True)
        sid = mem.create_session("user-2")
        interaction = Interaction(
            interaction_type=InteractionType.BROWSE,
            content_id="show-7",
        )
        mem.update_session(sid, interaction)
        ctx = mem.get_session_context(sid)
        assert "show-7" in ctx["browsing_history"]


class TestSessionMemoryExpire:
    """Test session expiration."""

    def test_session_memory_expire(self):
        mem = SessionMemory(mock_mode=True)
        sid = mem.create_session("user-1")
        result = mem.expire_session(sid)
        assert result is True
        assert mem.get_session_context(sid) is None

    def test_expire_nonexistent(self):
        mem = SessionMemory(mock_mode=True)
        assert mem.expire_session("nonexistent") is False

    def test_active_sessions_count(self):
        mem = SessionMemory(mock_mode=True)
        mem.create_session("u1")
        mem.create_session("u2")
        assert mem.get_active_sessions_count() == 2


# =========================================================================
# Feature Engine (simulated)
# =========================================================================

class TestFeatureEngineTimeFeatures:
    """Test time-of-day feature computation."""

    def test_feature_engine_time_features(self):
        """Compute time-based features from hour of day."""
        import math
        hour = 21  # 9 PM
        # Cyclic encoding
        sin_hour = math.sin(2 * math.pi * hour / 24)
        cos_hour = math.cos(2 * math.pi * hour / 24)
        assert -1 <= sin_hour <= 1
        assert -1 <= cos_hour <= 1
        # Evening detection
        is_evening = 18 <= hour <= 23
        assert is_evening is True


class TestFeatureEngineTrending:
    """Test trending signal computation."""

    def test_feature_engine_trending(self):
        """Compute trending score from view velocity."""
        views_last_hour = 50_000
        views_previous_hour = 30_000
        if views_previous_hour > 0:
            trending_ratio = views_last_hour / views_previous_hour
        else:
            trending_ratio = float("inf")
        assert trending_ratio > 1.0  # Trending upward
        trending_score = min(trending_ratio / 3.0, 1.0)  # Normalize to [0, 1]
        assert 0 <= trending_score <= 1.0
