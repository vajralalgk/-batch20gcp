"""
Session Memory for Netflix Real-Time LLM Personalization Platform.

Redis-backed, context-aware session tracking that maintains per-user
browsing history, click patterns, dwell times, and search queries.
Session data is serialised with ``orjson`` for speed and stored with a
token-level TTL aligned with the KV-cache lifecycle.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

try:
    import orjson

    def _dumps(obj: Any) -> bytes:
        return orjson.dumps(obj, option=orjson.OPT_SERIALIZE_NUMPY)

    def _loads(raw: bytes) -> Any:
        return orjson.loads(raw)

except ImportError:  # pragma: no cover – graceful fallback
    import json as _json

    def _dumps(obj: Any) -> bytes:  # type: ignore[misc]
        return _json.dumps(obj, default=str).encode("utf-8")

    def _loads(raw: bytes) -> Any:  # type: ignore[misc]
        return _json.loads(raw)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SESSION_TTL_SECONDS: int = 1800
"""Default session expiration (30 minutes of inactivity)."""

DEFAULT_KV_CACHE_TTL_SECONDS: int = 600
"""TTL for the KV-cache-aligned token-level data (10 minutes)."""

SESSION_KEY_PREFIX: str = "sess"
"""Redis key prefix for session hashes."""

ACTIVE_SET_KEY: str = "sessions:active"
"""Redis sorted-set tracking active session IDs scored by last update time."""

MAX_INTERACTIONS_PER_SESSION: int = 500
"""Hard cap on stored interactions to bound memory."""

MAX_SEARCH_QUERIES_PER_SESSION: int = 100
"""Hard cap on stored search queries."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class InteractionType(str, Enum):
    """Enumeration of tracked user interaction types."""

    BROWSE = "browse"
    CLICK = "click"
    PLAY = "play"
    PAUSE = "pause"
    SEARCH = "search"
    ADD_TO_LIST = "add_to_list"
    RATE = "rate"
    SKIP = "skip"
    SCROLL = "scroll"


@dataclass(slots=True)
class Interaction:
    """A single user interaction within a session."""

    interaction_type: InteractionType
    content_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    dwell_time_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.interaction_type.value,
            "content_id": self.content_id,
            "ts": self.timestamp,
            "dwell_ms": self.dwell_time_ms,
            "meta": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Interaction:
        return cls(
            interaction_type=InteractionType(data["type"]),
            content_id=data.get("content_id"),
            timestamp=data.get("ts", 0.0),
            dwell_time_ms=data.get("dwell_ms", 0),
            metadata=data.get("meta", {}),
        )


@dataclass(slots=True)
class SessionData:
    """Full materialised session state."""

    session_id: str
    user_id: str
    created_at: float
    updated_at: float
    interactions: List[Interaction] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)
    browsing_history: List[str] = field(default_factory=list)
    click_pattern: List[str] = field(default_factory=list)
    total_dwell_time_ms: int = 0
    token_context_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "interactions": [i.to_dict() for i in self.interactions],
            "search_queries": self.search_queries,
            "browsing_history": self.browsing_history,
            "click_pattern": self.click_pattern,
            "total_dwell_time_ms": self.total_dwell_time_ms,
            "token_context_key": self.token_context_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionData:
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            interactions=[
                Interaction.from_dict(i) for i in data.get("interactions", [])
            ],
            search_queries=data.get("search_queries", []),
            browsing_history=data.get("browsing_history", []),
            click_pattern=data.get("click_pattern", []),
            total_dwell_time_ms=data.get("total_dwell_time_ms", 0),
            token_context_key=data.get("token_context_key"),
        )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class SessionMemory:
    """Redis-backed session memory with KV-cache-aligned TTLs.

    Parameters
    ----------
    redis_client:
        A ``redis.Redis`` (or ``redis.asyncio.Redis``) instance.
    session_ttl:
        Inactivity timeout for sessions in seconds.
    kv_cache_ttl:
        TTL for token-level KV-cache context entries.
    mock_mode:
        When *True* all Redis I/O is replaced by an in-memory dict.
    """

    def __init__(
        self,
        redis_client: Any = None,
        *,
        session_ttl: int = DEFAULT_SESSION_TTL_SECONDS,
        kv_cache_ttl: int = DEFAULT_KV_CACHE_TTL_SECONDS,
        mock_mode: bool = False,
    ) -> None:
        self._redis = redis_client
        self._session_ttl = session_ttl
        self._kv_cache_ttl = kv_cache_ttl
        self._mock_mode = mock_mode

        # In-memory mock stores
        self._mock_sessions: Dict[str, bytes] = {}
        self._mock_active: Dict[str, float] = {}  # session_id -> updated_at
        self._mock_kv_cache: Dict[str, bytes] = {}

        if not mock_mode and redis_client is None:
            raise ValueError(
                "redis_client is required when mock_mode is False"
            )

        logger.info(
            "SessionMemory initialised (mock=%s, session_ttl=%ds, kv_ttl=%ds)",
            mock_mode,
            session_ttl,
            kv_cache_ttl,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self, user_id: str) -> str:
        """Create a new session for *user_id* and return the session ID."""
        session_id = self._generate_session_id(user_id)
        now = time.time()

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            token_context_key=f"kv:{session_id}",
        )

        self._save_session(session)
        self._register_active(session_id, now)
        self._init_kv_cache_entry(session_id)

        logger.info(
            "Created session %s for user %s", session_id, user_id
        )
        return session_id

    def update_session(
        self,
        session_id: str,
        interaction: Interaction,
    ) -> bool:
        """Append an interaction to the session.

        Returns *True* when the update succeeds, *False* if the session
        does not exist or has expired.
        """
        session = self._load_session(session_id)
        if session is None:
            logger.warning(
                "Attempted to update non-existent session %s", session_id
            )
            return False

        now = time.time()
        session.updated_at = now

        # Append interaction (bounded)
        if len(session.interactions) < MAX_INTERACTIONS_PER_SESSION:
            session.interactions.append(interaction)

        # Update derived fields
        session.total_dwell_time_ms += interaction.dwell_time_ms

        if interaction.content_id:
            if interaction.interaction_type == InteractionType.BROWSE:
                session.browsing_history.append(interaction.content_id)
            elif interaction.interaction_type in (
                InteractionType.CLICK,
                InteractionType.PLAY,
            ):
                session.click_pattern.append(interaction.content_id)

        if interaction.interaction_type == InteractionType.SEARCH:
            query = interaction.metadata.get("query", "")
            if query and len(session.search_queries) < MAX_SEARCH_QUERIES_PER_SESSION:
                session.search_queries.append(query)

        self._save_session(session)
        self._register_active(session_id, now)
        self._refresh_kv_cache(session_id)

        return True

    def get_session_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the full session context as a plain dictionary.

        Returns ``None`` when the session does not exist or has expired.
        """
        session = self._load_session(session_id)
        if session is None:
            return None

        context = session.to_dict()

        # Derived analytics
        context["interaction_count"] = len(session.interactions)
        context["unique_content_ids"] = list(
            {
                i.content_id
                for i in session.interactions
                if i.content_id is not None
            }
        )
        context["session_duration_s"] = round(
            session.updated_at - session.created_at, 2
        )

        return context

    def expire_session(self, session_id: str) -> bool:
        """Explicitly expire (delete) a session.

        Returns *True* when the session existed and was removed.
        """
        existed = self._delete_session(session_id)
        self._unregister_active(session_id)
        self._delete_kv_cache_entry(session_id)

        if existed:
            logger.info("Expired session %s", session_id)
        return existed

    def get_active_sessions_count(self) -> int:
        """Return the number of sessions updated within the TTL window."""
        cutoff = time.time() - self._session_ttl

        if self._mock_mode:
            return sum(
                1 for ts in self._mock_active.values() if ts >= cutoff
            )

        try:
            # ZCOUNT on the active sorted set
            return int(
                self._redis.zcount(ACTIVE_SET_KEY, cutoff, "+inf")
            )
        except Exception:
            logger.exception("Failed to count active sessions")
            return 0

    # ------------------------------------------------------------------
    # Session persistence helpers
    # ------------------------------------------------------------------

    def _redis_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}:{session_id}"

    def _kv_cache_key(self, session_id: str) -> str:
        return f"kv:{session_id}"

    def _save_session(self, session: SessionData) -> None:
        raw = _dumps(session.to_dict())

        if self._mock_mode:
            self._mock_sessions[session.session_id] = raw
            return

        try:
            self._redis.setex(
                self._redis_key(session.session_id),
                self._session_ttl,
                raw,
            )
        except Exception:
            logger.exception(
                "Failed to save session %s", session.session_id
            )

    def _load_session(self, session_id: str) -> Optional[SessionData]:
        if self._mock_mode:
            raw = self._mock_sessions.get(session_id)
            if raw is None:
                return None
            data = _loads(raw)
            return SessionData.from_dict(data)

        try:
            raw = self._redis.get(self._redis_key(session_id))
            if raw is None:
                return None
            data = _loads(raw)
            return SessionData.from_dict(data)
        except Exception:
            logger.exception("Failed to load session %s", session_id)
            return None

    def _delete_session(self, session_id: str) -> bool:
        if self._mock_mode:
            return self._mock_sessions.pop(session_id, None) is not None

        try:
            return bool(self._redis.delete(self._redis_key(session_id)))
        except Exception:
            logger.exception("Failed to delete session %s", session_id)
            return False

    # ------------------------------------------------------------------
    # Active-set helpers
    # ------------------------------------------------------------------

    def _register_active(self, session_id: str, ts: float) -> None:
        if self._mock_mode:
            self._mock_active[session_id] = ts
            return

        try:
            self._redis.zadd(ACTIVE_SET_KEY, {session_id: ts})
        except Exception:
            logger.exception(
                "Failed to register session %s in active set", session_id
            )

    def _unregister_active(self, session_id: str) -> None:
        if self._mock_mode:
            self._mock_active.pop(session_id, None)
            return

        try:
            self._redis.zrem(ACTIVE_SET_KEY, session_id)
        except Exception:
            logger.exception(
                "Failed to unregister session %s from active set", session_id
            )

    # ------------------------------------------------------------------
    # KV-cache alignment helpers
    # ------------------------------------------------------------------

    def _init_kv_cache_entry(self, session_id: str) -> None:
        """Create a placeholder for the token-level KV-cache entry."""
        placeholder = _dumps({"session_id": session_id, "tokens": []})

        if self._mock_mode:
            self._mock_kv_cache[session_id] = placeholder
            return

        try:
            self._redis.setex(
                self._kv_cache_key(session_id),
                self._kv_cache_ttl,
                placeholder,
            )
        except Exception:
            logger.exception(
                "Failed to init KV-cache entry for session %s", session_id
            )

    def _refresh_kv_cache(self, session_id: str) -> None:
        """Refresh the TTL on the KV-cache entry to keep it aligned."""
        if self._mock_mode:
            return  # mock has no real TTL

        try:
            self._redis.expire(
                self._kv_cache_key(session_id), self._kv_cache_ttl
            )
        except Exception:
            logger.exception(
                "Failed to refresh KV-cache TTL for session %s", session_id
            )

    def _delete_kv_cache_entry(self, session_id: str) -> None:
        if self._mock_mode:
            self._mock_kv_cache.pop(session_id, None)
            return

        try:
            self._redis.delete(self._kv_cache_key(session_id))
        except Exception:
            logger.exception(
                "Failed to delete KV-cache entry for session %s", session_id
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_session_id(user_id: str) -> str:
        """Produce a globally unique, user-scoped session identifier."""
        unique = uuid.uuid4().hex[:12]
        return f"{user_id}:{unique}"
