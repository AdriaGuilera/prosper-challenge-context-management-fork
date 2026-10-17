#
# Embeddings for the meaning layer: text-embedding-3-small.
#
# Postgres is the cache (embedding_cache): the API is called only for text it hasn't seen.
#

import os

import numpy as np
from openai import AsyncOpenAI

MODEL = "text-embedding-3-small"

_client = None


def _openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


async def embed(conn, texts: list) -> dict:
    """text -> unit vector, from embedding_cache or else the API."""
    rows = await conn.fetch(
        "SELECT text, embedding FROM embedding_cache WHERE model = $1 AND text = ANY($2::text[])", MODEL, texts)
    out = {r["text"]: np.asarray(r["embedding"].to_numpy(), dtype=np.float32) for r in rows}
    missing = [t for t in dict.fromkeys(texts) if t not in out]
    if missing:
        resp = await _openai().embeddings.create(model=MODEL, input=missing)
        fresh = {t: np.asarray(d.embedding, dtype=np.float32) for t, d in zip(missing, resp.data)}
        await conn.executemany(
            "INSERT INTO embedding_cache (model, text, embedding) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            [(MODEL, t, e) for t, e in fresh.items()])
        out |= fresh
    return out
