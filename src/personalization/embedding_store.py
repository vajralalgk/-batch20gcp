"""
Embedding Store for Netflix Real-Time LLM Personalization Platform.

Manages user and content embeddings for recommendation with DynamoDB-backed
persistent storage and ElastiCache (Redis) for hot embedding caching.
Supports 768-dimensional sentence-transformer compatible embeddings,
cosine similarity computation, and a mock mode for testing.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_DIM: int = 768
"""Dimensionality of embeddings (sentence-transformer compatible)."""

DEFAULT_CACHE_TTL_SECONDS: int = 300
"""Default time-to-live for cached embeddings (5 minutes)."""

DYNAMODB_TABLE_NAME: str = "netflix-embedding-store"
"""DynamoDB table used for persistent embedding storage."""

BATCH_READ_MAX: int = 100
"""Maximum number of items per DynamoDB BatchGetItem call."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    """Immutable wrapper around a stored embedding vector."""

    entity_id: str
    embedding: np.ndarray
    updated_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class EmbeddingStore:
    """DynamoDB + ElastiCache backed store for user / content embeddings.

    Parameters
    ----------
    dynamodb_client:
        A ``boto3`` DynamoDB client (or compatible mock).
    redis_client:
        A ``redis.Redis`` instance pointed at ElastiCache (or compatible mock).
    table_name:
        DynamoDB table name.  Defaults to ``DYNAMODB_TABLE_NAME``.
    cache_ttl:
        TTL in seconds for the Redis caching layer.
    mock_mode:
        When *True* all I/O is replaced by deterministic in-memory storage.
        Useful for unit tests and local development.
    embedding_dim:
        Expected dimensionality of embedding vectors.
    """

    def __init__(
        self,
        dynamodb_client: Any = None,
        redis_client: Any = None,
        *,
        table_name: str = DYNAMODB_TABLE_NAME,
        cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
        mock_mode: bool = False,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        self._dynamodb = dynamodb_client
        self._redis = redis_client
        self._table_name = table_name
        self._cache_ttl = cache_ttl
        self._mock_mode = mock_mode
        self._embedding_dim = embedding_dim

        # In-memory mock storage: entity_id -> EmbeddingRecord
        self._mock_store: Dict[str, EmbeddingRecord] = {}

        # Metrics
        self._stats: Dict[str, int] = {
            "cache_hits": 0,
            "cache_misses": 0,
            "dynamo_reads": 0,
            "dynamo_writes": 0,
            "similarity_computations": 0,
        }

        if not mock_mode:
            if dynamodb_client is None:
                raise ValueError(
                    "dynamodb_client is required when mock_mode is False"
                )
            if redis_client is None:
                raise ValueError(
                    "redis_client is required when mock_mode is False"
                )

        logger.info(
            "EmbeddingStore initialised (mock=%s, dim=%d, ttl=%ds)",
            mock_mode,
            embedding_dim,
            cache_ttl,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_user_embedding(self, user_id: str) -> Optional[np.ndarray]:
        """Retrieve the embedding vector for a single user.

        Checks the cache first, then falls back to DynamoDB.  Returns
        ``None`` when the user has no stored embedding.
        """
        record = self._get_record(f"user:{user_id}")
        if record is None:
            return None
        return record.embedding

    def get_content_embeddings(
        self, content_ids: Sequence[str]
    ) -> Dict[str, np.ndarray]:
        """Batch-retrieve content embeddings.

        Returns a mapping from *content_id* to its embedding vector.
        Missing content IDs are silently omitted from the result.
        """
        keys = [f"content:{cid}" for cid in content_ids]
        records = self._batch_get_records(keys)
        return {
            rec.entity_id.removeprefix("content:"): rec.embedding
            for rec in records
        }

    def update_embedding(
        self,
        entity_id: str,
        embedding: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert or update an embedding for *entity_id*.

        The embedding is written to DynamoDB and the cache is
        populated/refreshed atomically.

        Raises
        ------
        ValueError
            If the embedding dimensionality does not match ``embedding_dim``.
        """
        embedding = np.asarray(embedding, dtype=np.float32)
        if embedding.shape != (self._embedding_dim,):
            raise ValueError(
                f"Expected embedding of shape ({self._embedding_dim},), "
                f"got {embedding.shape}"
            )

        record = EmbeddingRecord(
            entity_id=entity_id,
            embedding=embedding,
            updated_at=time.time(),
            metadata=metadata or {},
        )

        self._put_record(record)
        logger.debug("Updated embedding for %s", entity_id)

    def compute_similarity(
        self,
        user_embedding: np.ndarray,
        content_embeddings: Dict[str, np.ndarray],
    ) -> List[tuple[str, float]]:
        """Compute cosine similarity between a user and multiple content items.

        Returns a list of ``(content_id, similarity)`` tuples sorted in
        descending similarity order.
        """
        self._stats["similarity_computations"] += 1

        user_vec = np.asarray(user_embedding, dtype=np.float32)
        user_norm = np.linalg.norm(user_vec)
        if user_norm == 0.0:
            logger.warning("User embedding is a zero vector; returning empty similarities")
            return [(cid, 0.0) for cid in content_embeddings]

        user_unit = user_vec / user_norm

        results: List[tuple[str, float]] = []
        for content_id, content_vec in content_embeddings.items():
            content_vec = np.asarray(content_vec, dtype=np.float32)
            content_norm = np.linalg.norm(content_vec)
            if content_norm == 0.0:
                similarity = 0.0
            else:
                similarity = float(np.dot(user_unit, content_vec / content_norm))
            results.append((content_id, similarity))

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def batch_lookup(self, ids: Sequence[str]) -> Dict[str, np.ndarray]:
        """Generic batch lookup by raw entity IDs (no prefix added).

        Convenience wrapper around the internal batch retrieval that
        returns a simple mapping of ``entity_id -> embedding``.
        """
        records = self._batch_get_records(list(ids))
        return {rec.entity_id: rec.embedding for rec in records}

    @property
    def stats(self) -> Dict[str, int]:
        """Return a snapshot of internal metrics counters."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, entity_id: str) -> str:
        """Deterministic cache key derivation."""
        digest = hashlib.sha256(entity_id.encode()).hexdigest()[:16]
        return f"emb:{digest}"

    def _cache_get(self, entity_id: str) -> Optional[bytes]:
        if self._mock_mode:
            rec = self._mock_store.get(entity_id)
            if rec is not None:
                self._stats["cache_hits"] += 1
                return rec.embedding.tobytes()
            self._stats["cache_misses"] += 1
            return None

        try:
            raw: Optional[bytes] = self._redis.get(self._cache_key(entity_id))
            if raw is not None:
                self._stats["cache_hits"] += 1
            else:
                self._stats["cache_misses"] += 1
            return raw
        except Exception:
            logger.exception("Redis GET failed for %s", entity_id)
            self._stats["cache_misses"] += 1
            return None

    def _cache_set(self, entity_id: str, embedding: np.ndarray) -> None:
        if self._mock_mode:
            return  # mock store already holds the record

        try:
            self._redis.setex(
                self._cache_key(entity_id),
                self._cache_ttl,
                embedding.tobytes(),
            )
        except Exception:
            logger.exception("Redis SETEX failed for %s", entity_id)

    # ------------------------------------------------------------------
    # DynamoDB helpers
    # ------------------------------------------------------------------

    def _serialize_embedding(self, embedding: np.ndarray) -> str:
        """Serialize an ndarray to a base-64 encoded string for DynamoDB."""
        import base64

        return base64.b64encode(embedding.tobytes()).decode("ascii")

    def _deserialize_embedding(self, data: str) -> np.ndarray:
        """Deserialize a base-64 encoded string back to an ndarray."""
        import base64

        raw = base64.b64decode(data)
        return np.frombuffer(raw, dtype=np.float32).copy()

    def _get_record(self, entity_id: str) -> Optional[EmbeddingRecord]:
        """Fetch a single record, checking cache first."""
        # Try cache
        cached = self._cache_get(entity_id)
        if cached is not None:
            embedding = np.frombuffer(cached, dtype=np.float32).copy()
            return EmbeddingRecord(
                entity_id=entity_id,
                embedding=embedding,
                updated_at=0.0,
            )

        # Fall back to DynamoDB / mock store
        if self._mock_mode:
            return self._mock_store.get(entity_id)

        self._stats["dynamo_reads"] += 1
        try:
            response = self._dynamodb.get_item(
                TableName=self._table_name,
                Key={"entity_id": {"S": entity_id}},
            )
            item = response.get("Item")
            if item is None:
                return None

            embedding = self._deserialize_embedding(item["embedding"]["S"])
            updated_at = float(item.get("updated_at", {}).get("N", 0))
            record = EmbeddingRecord(
                entity_id=entity_id,
                embedding=embedding,
                updated_at=updated_at,
            )
            # Warm cache
            self._cache_set(entity_id, embedding)
            return record
        except Exception:
            logger.exception("DynamoDB GetItem failed for %s", entity_id)
            return None

    def _put_record(self, record: EmbeddingRecord) -> None:
        """Persist a record to DynamoDB and warm the cache."""
        if self._mock_mode:
            self._mock_store[record.entity_id] = record
            return

        self._stats["dynamo_writes"] += 1
        try:
            self._dynamodb.put_item(
                TableName=self._table_name,
                Item={
                    "entity_id": {"S": record.entity_id},
                    "embedding": {"S": self._serialize_embedding(record.embedding)},
                    "updated_at": {"N": str(record.updated_at)},
                },
            )
            self._cache_set(record.entity_id, record.embedding)
        except Exception:
            logger.exception("DynamoDB PutItem failed for %s", record.entity_id)
            raise

    def _batch_get_records(
        self, entity_ids: List[str]
    ) -> List[EmbeddingRecord]:
        """Fetch multiple records, resolving from cache then DynamoDB."""
        results: List[EmbeddingRecord] = []
        uncached_ids: List[str] = []

        # Phase 1: resolve from cache
        for eid in entity_ids:
            cached = self._cache_get(eid)
            if cached is not None:
                embedding = np.frombuffer(cached, dtype=np.float32).copy()
                results.append(
                    EmbeddingRecord(entity_id=eid, embedding=embedding, updated_at=0.0)
                )
            else:
                uncached_ids.append(eid)

        if not uncached_ids:
            return results

        # Phase 2: batch fetch from DynamoDB / mock
        if self._mock_mode:
            for eid in uncached_ids:
                rec = self._mock_store.get(eid)
                if rec is not None:
                    results.append(rec)
            return results

        # Chunk into BATCH_READ_MAX-sized slices
        for start in range(0, len(uncached_ids), BATCH_READ_MAX):
            chunk = uncached_ids[start : start + BATCH_READ_MAX]
            self._stats["dynamo_reads"] += 1
            try:
                response = self._dynamodb.batch_get_item(
                    RequestItems={
                        self._table_name: {
                            "Keys": [
                                {"entity_id": {"S": eid}} for eid in chunk
                            ]
                        }
                    }
                )
                items = response.get("Responses", {}).get(self._table_name, [])
                for item in items:
                    eid = item["entity_id"]["S"]
                    embedding = self._deserialize_embedding(item["embedding"]["S"])
                    updated_at = float(item.get("updated_at", {}).get("N", 0))
                    rec = EmbeddingRecord(
                        entity_id=eid,
                        embedding=embedding,
                        updated_at=updated_at,
                    )
                    results.append(rec)
                    self._cache_set(eid, embedding)
            except Exception:
                logger.exception(
                    "DynamoDB BatchGetItem failed for chunk starting at %d", start
                )

        return results
