#
# Async Postgres access (no synchronous I/O in the turn path).
#

import os
from pathlib import Path

import asyncpg
from pgvector.asyncpg import register_vector

DEFAULT_DSN = "postgresql://prosper:prosper@localhost:5433/prosper"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_pools: dict = {}


def dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


async def _init(conn) -> None:
    await register_vector(conn)


async def pool() -> asyncpg.Pool:
    """One pool per event loop: the eval runner and the bot each get their own."""
    import asyncio

    loop = asyncio.get_running_loop()
    p = _pools.get(id(loop))
    if p is None:
        p = await asyncpg.create_pool(dsn(), min_size=1, max_size=10, init=_init)
        _pools[id(loop)] = p
    return p


async def close() -> None:
    import asyncio

    p = _pools.pop(id(asyncio.get_running_loop()), None)
    if p is not None:
        await p.close()


async def apply_schema(conn) -> None:
    # Extensions first, on a bare connection, so the vector type exists before register_vector.
    await conn.execute(SCHEMA_PATH.read_text())
