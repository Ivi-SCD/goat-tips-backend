"""
Database Connection Layer
=========================
Provides:
  - Async pool (asyncpg) for FastAPI endpoints
  - Sync connection (psycopg2) for scripts and Azure Functions

The pool is created lazily on first use and reused for the app lifetime.
"""

import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator

import asyncpg
import psycopg2
import psycopg2.extras

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


# ── Async pool (FastAPI / asyncio) ────────────────────────────────────────────

async def get_pool() -> asyncpg.Pool:
    """Returns (or creates) the shared async connection pool."""
    global _pool
    if _pool is None:
        settings = get_settings()
        if not settings.SUPABASE_DB_URL:
            raise RuntimeError("SUPABASE_DB_URL is not configured")
        _pool = await asyncpg.create_pool(
            dsn=settings.SUPABASE_DB_URL,
            min_size=1,
            max_size=5,
            command_timeout=30,
            statement_cache_size=0,  # required for Supabase pooler (transaction mode)
        )
        logger.info("DB: async pool created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB: async pool closed")


@asynccontextmanager
async def acquire() -> AsyncGenerator[asyncpg.Connection, None]:
    """Context manager that acquires a connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


# ── Sync connection (scripts / Azure Functions) ───────────────────────────────

@contextmanager
def get_sync_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager for a synchronous psycopg2 connection."""
    settings = get_settings()
    if not settings.SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL is not configured")
    conn = psycopg2.connect(settings.SUPABASE_DB_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── FastAPI lifespan helpers ──────────────────────────────────────────────────

async def on_startup() -> None:
    """Call from FastAPI lifespan to warm up the pool."""
    settings = get_settings()
    if settings.SUPABASE_DB_URL:
        await get_pool()
    else:
        logger.warning("DB: SUPABASE_DB_URL not set — database features disabled")


async def on_shutdown() -> None:
    await close_pool()
