"""
Persistent cache layer for FindMyScholarship AI.

Backend: SQLite (default, zero-infra) or Redis (set CACHE_BACKEND=redis).

Usage:
    cache = get_cache()
    await cache.set("key", {"data": ...}, ttl_seconds=86400)
    value = await cache.get("key")   # None if missing or expired

TTL defaults:
    - Crawl results:   7 days  (CRAWL_TTL_SECONDS)
    - Domain lookups:  30 days (DOMAIN_TTL_SECONDS)
    - Keyword results: 1 day   (KEYWORD_TTL_SECONDS)
"""

import asyncio
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from utils.logger import logger

# ── TTL constants ──────────────────────────────────────────────────────────────
CRAWL_TTL_SECONDS = int(os.getenv("CRAWL_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 days
DOMAIN_TTL_SECONDS = int(os.getenv("DOMAIN_TTL_SECONDS", str(30 * 24 * 3600)))  # 30 days
KEYWORD_TTL_SECONDS = int(os.getenv("KEYWORD_TTL_SECONDS", str(24 * 3600)))  # 1 day

# ── Storage path (SQLite) ──────────────────────────────────────────────────────
_DEFAULT_DB_PATH = Path(os.getenv("CACHE_DB_PATH", "cache/findmyscholarship.db"))


# ── Abstract interface ─────────────────────────────────────────────────────────
class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Return the cached value or None if missing/expired."""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store value under key for ttl_seconds."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a key from cache."""

    @abstractmethod
    async def close(self) -> None:
        """Release any resources (connections, event loops)."""


# ── In-memory fallback ─────────────────────────────────────────────────────────
class InMemoryCache(CacheBackend):
    """
    Simple dict-based cache used when neither aiosqlite nor redis is available.
    Data is lost on restart — acceptable as a graceful degradation path.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (value, time.time() + ttl_seconds)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def close(self) -> None:
        self._store.clear()


# ── SQLite backend ─────────────────────────────────────────────────────────────
class SQLiteCache(CacheBackend):
    """
    Async SQLite cache via aiosqlite.

    Schema:
        cache_entries(key TEXT PK, value TEXT, expires_at REAL)
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH):
        self._db_path = db_path
        self._conn = None

    async def _ensure_connected(self):
        if self._conn is None:
            try:
                import aiosqlite
            except ImportError:
                raise RuntimeError(
                    "aiosqlite is required for SQLite cache. Run: pip install aiosqlite"
                )
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            await self._conn.commit()
            logger.info(f"SQLiteCache connected: {self._db_path}")

    async def get(self, key: str) -> Optional[Any]:
        await self._ensure_connected()
        now = time.time()
        async with self._conn.execute(
            "SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value_json, expires_at = row
        if now > expires_at:
            await self.delete(key)
            logger.debug(f"Cache expired: {key}")
            return None
        logger.debug(f"Cache hit: {key}")
        return json.loads(value_json)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        await self._ensure_connected()
        expires_at = time.time() + ttl_seconds
        value_json = json.dumps(value, default=str)
        await self._conn.execute(
            """
            INSERT INTO cache_entries (key, value, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at
            """,
            (key, value_json, expires_at),
        )
        await self._conn.commit()
        logger.debug(f"Cache set: {key} (TTL {ttl_seconds}s)")

    async def delete(self, key: str) -> None:
        await self._ensure_connected()
        await self._conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
        await self._conn.commit()

    async def purge_expired(self) -> int:
        """Delete all expired entries. Returns count removed."""
        await self._ensure_connected()
        cursor = await self._conn.execute(
            "DELETE FROM cache_entries WHERE expires_at < ?", (time.time(),)
        )
        await self._conn.commit()
        removed = cursor.rowcount
        if removed:
            logger.info(f"SQLiteCache purged {removed} expired entries")
        return removed

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


# ── Redis backend ──────────────────────────────────────────────────────────────
class RedisCache(CacheBackend):
    """
    Async Redis cache via redis.asyncio.

    Requires: pip install redis
    Env vars:
        REDIS_URL  (default: redis://localhost:6379)
    """

    def __init__(self, url: Optional[str] = None):
        self._url = url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._client = None

    async def _ensure_connected(self):
        if self._client is None:
            try:
                import redis.asyncio as aioredis
            except ImportError:
                raise RuntimeError(
                    "redis package is required for Redis cache. Run: pip install redis"
                )
            self._client = aioredis.from_url(self._url, decode_responses=True)
            logger.info(f"RedisCache connected: {self._url}")

    async def get(self, key: str) -> Optional[Any]:
        await self._ensure_connected()
        raw = await self._client.get(key)
        if raw is None:
            return None
        logger.debug(f"Cache hit (Redis): {key}")
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        await self._ensure_connected()
        await self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
        logger.debug(f"Cache set (Redis): {key} (TTL {ttl_seconds}s)")

    async def delete(self, key: str) -> None:
        await self._ensure_connected()
        await self._client.delete(key)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# ── Factory ────────────────────────────────────────────────────────────────────
_cache_instance: Optional[CacheBackend] = None


def get_cache() -> CacheBackend:
    """
    Return the singleton cache backend.

    Selects backend based on CACHE_BACKEND env var:
        sqlite  (default) → SQLiteCache  (requires aiosqlite)
        redis             → RedisCache   (requires redis)

    Falls back to InMemoryCache if the required package is not installed,
    so the app stays functional without crashing.
    """
    global _cache_instance
    if _cache_instance is None:
        backend = os.getenv("CACHE_BACKEND", "sqlite").lower()
        if backend == "redis":
            try:
                import redis.asyncio  # noqa: F401

                _cache_instance = RedisCache()
                logger.info("Cache backend: Redis")
            except ImportError:
                logger.warning(
                    "redis package not found — falling back to in-memory cache. "
                    "Run: pip install redis"
                )
                _cache_instance = InMemoryCache()
        else:
            try:
                import aiosqlite  # noqa: F401

                _cache_instance = SQLiteCache()
                logger.info("Cache backend: SQLite")
            except ImportError:
                logger.warning(
                    "aiosqlite not found — falling back to in-memory cache. "
                    "Run: pip install aiosqlite"
                )
                _cache_instance = InMemoryCache()
    return _cache_instance


def reset_cache_instance() -> None:
    """Force a new cache instance on next get_cache() call. Useful in tests."""
    global _cache_instance
    _cache_instance = None


# ── Sync convenience wrapper (for non-async callers) ──────────────────────────


def sync_get(key: str) -> Optional[Any]:
    return asyncio.get_event_loop().run_until_complete(get_cache().get(key))


def sync_set(key: str, value: Any, ttl_seconds: int) -> None:
    asyncio.get_event_loop().run_until_complete(get_cache().set(key, value, ttl_seconds))
