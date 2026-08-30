"""Optional Redis cache. No-op when REDIS_URL is unset or Redis is unreachable."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_client_failed = False


def get_redis() -> Any | None:
    """Return a Redis client, or None if caching is disabled / unavailable."""
    global _client, _client_failed

    if not settings.REDIS_URL:
        return None
    if _client_failed:
        return None
    if _client is not None:
        return _client

    try:
        import redis

        client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
        _client = client
        return _client
    except Exception as exc:
        logger.warning("Redis unavailable (%s); dashboard cache disabled", exc)
        _client_failed = True
        return None


def cache_get(key: str) -> str | None:
    client = get_redis()
    if client is None:
        return None
    try:
        value = client.get(key)
        return value if isinstance(value, str) else None
    except Exception as exc:
        logger.warning("Redis GET %s failed: %s", key, exc)
        return None


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        client.setex(key, ttl_seconds, value)
    except Exception as exc:
        logger.warning("Redis SETEX %s failed: %s", key, exc)


def cache_ttl(key: str) -> int | None:
    """Return remaining TTL in seconds, or None if key missing / Redis off.

    Redis TTL: -2 key does not exist, -1 key exists with no expire.
    """
    client = get_redis()
    if client is None:
        return None
    try:
        ttl = int(client.ttl(key))
        if ttl < 0:
            return None
        return ttl
    except Exception as exc:
        logger.warning("Redis TTL %s failed: %s", key, exc)
        return None


def cache_delete(key: str) -> bool:
    """Delete a cache key. Returns True if a key was removed."""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.delete(key))
    except Exception as exc:
        logger.warning("Redis DEL %s failed: %s", key, exc)
        return False


def redis_configured() -> bool:
    return bool(settings.REDIS_URL)


def redis_reachable() -> bool:
    return get_redis() is not None
