#
# OpenAI Decisions API (POST /v1/decisions): picks from answers we define instead of
# generating text. The SDK has no client for it yet, so this is plain HTTP.
#
# It is on the live voice path, so it fails fast: a short timeout and two retries,
# then the caller of `decide` falls back.
#

import asyncio
import os

import httpx

MODEL = "gpt-6-luna"
TIMEOUT_S = 10
RETRIES = 2

_clients: dict = {}


def _client() -> httpx.AsyncClient:
    """One client per event loop, like the database pool."""
    key = id(asyncio.get_running_loop())
    if key not in _clients:
        _clients[key] = httpx.AsyncClient(
            base_url="https://api.openai.com/v1", timeout=TIMEOUT_S,
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    return _clients[key]


async def decide(text: str, questions: list) -> dict:
    """name -> answer for each question about `text`. Raises after the retries."""
    body = {"model": MODEL, "input": text, "questions": questions}
    for attempt in range(RETRIES + 1):
        try:
            r = await _client().post("/decisions", json=body)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return {a["name"]: a for a in r.json()["answers"]}
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == RETRIES:
                raise
        if attempt == RETRIES:
            r.raise_for_status()
        await asyncio.sleep(0.5 * 2 ** attempt)
