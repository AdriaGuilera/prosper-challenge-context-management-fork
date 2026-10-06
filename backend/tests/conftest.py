import sys
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv
from pgvector.asyncpg import register_vector

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env", override=True)

from store.db import dsn  # noqa: E402


@pytest.fixture(scope="session")
async def conn():
    try:
        c = await asyncpg.connect(dsn())
    except OSError:
        pytest.skip("Postgres is not running: `make db-up && make ingest`")
    await register_vector(c)
    yield c
    await c.close()


@pytest.fixture(scope="session")
async def ingested(conn):
    """{name: (catalog_version, calendar_anchor)} for every catalog in the store."""
    rows = await conn.fetch("SELECT name, catalog_version, calendar_anchor FROM catalogs ORDER BY name")
    if not rows:
        pytest.skip("No catalog ingested: `make ingest`")
    return {r["name"]: (r["catalog_version"], r["calendar_anchor"]) for r in rows}
