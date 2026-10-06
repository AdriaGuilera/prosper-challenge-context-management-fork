#
# Embeddings for the meaning layer (decision 005): text-embedding-3-small, pinned.
#
# Cached in Postgres (embedding_cache) and mirrored to a local JSONL file at ingest,
# so `make db-reset` and every eval rerun hit the API only for new phrasing.
#

import json
import os
from pathlib import Path

import numpy as np
from openai import AsyncOpenAI

MODEL = "text-embedding-3-small"
MODEL_VERSION = "2024-01"          # pinned with the policy set; changing it is a re-verification event
DIM = 1536
FILE_CACHE = Path(__file__).parent.parent / ".cache" / "embeddings.jsonl"

_client = None
_memory: dict = {}


def _openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def load_file_cache() -> None:
    if FILE_CACHE.exists():
        for line in FILE_CACHE.read_text().splitlines():
            row = json.loads(line)
            _memory[row["t"]] = np.asarray(row["e"], dtype=np.float32)


def save_file_cache() -> None:
    FILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with FILE_CACHE.open("w") as f:
        for text, emb in _memory.items():
            f.write(json.dumps({"t": text, "e": [round(float(x), 6) for x in emb]}) + "\n")


async def embed(conn, texts: list) -> dict:
    """text -> unit vector. Memory, then Postgres, then the API (batched)."""
    want = [t for t in dict.fromkeys(texts) if t not in _memory]
    if want and conn is not None:
        rows = await conn.fetch(
            "SELECT text, embedding FROM embedding_cache WHERE model = $1 AND text = ANY($2::text[])", MODEL, want
        )
        for r in rows:
            _memory[r["text"]] = np.asarray(r["embedding"], dtype=np.float32)
        want = [t for t in want if t not in _memory]
    for i in range(0, len(want), 256):
        batch = want[i : i + 256]
        resp = await _openai().embeddings.create(model=MODEL, input=batch)
        fresh = [(t, np.asarray(d.embedding, dtype=np.float32)) for t, d in zip(batch, resp.data)]
        for t, e in fresh:
            _memory[t] = e
        if conn is not None:
            await conn.executemany(
                "INSERT INTO embedding_cache (model, text, embedding) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                [(MODEL, t, e) for t, e in fresh],
            )
    return {t: _memory[t] for t in texts}
