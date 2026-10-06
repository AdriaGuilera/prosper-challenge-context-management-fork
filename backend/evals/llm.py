#
# One OpenAI client for the eval: agent, caller simulator and LLM judge. Every call
# is metered (tokens, latency) into the conversation's event log, so accuracy is
# never reported without its cost.
#

import asyncio
import os
import time

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

# USD per 1M tokens: (input, cached input, output). Models without a published price
# in this table report tokens only.
PRICES = {
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
}

_client = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=90)
    return _client


def cost_usd(model: str, prompt: int, cached: int, completion: int):
    if model not in PRICES:
        return None
    i, c, o = PRICES[model]
    return ((prompt - cached) * i + cached * c + completion * o) / 1_000_000


async def chat(model: str, messages: list, tools=None, extra=None, meter=None, role="agent", **kw):
    """Chat completion with retries on transient errors. Returns the message."""
    params = {"model": model, "messages": messages, **(extra or {}), **kw}
    if tools:
        params["tools"] = tools
    delay = 2.0
    for attempt in range(7):
        started = time.perf_counter()
        try:
            resp = await client().chat.completions.create(**params)
            break
        except (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError):
            if attempt == 6:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    ms = (time.perf_counter() - started) * 1000
    u = resp.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    if meter is not None:
        meter({"type": "llm_call", "role": role, "model": model, "ms": round(ms, 1),
               "prompt_tokens": u.prompt_tokens, "cached_tokens": cached, "completion_tokens": u.completion_tokens})
    return resp.choices[0].message
