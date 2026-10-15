#
# One OpenAI client for the eval: agent, caller simulator and LLM judge. Every call
# is metered (tokens) into the conversation's event log, so accuracy is
# never reported without its cost.
#

from functools import cache

from openai import AsyncOpenAI

# USD per 1M tokens: (input, cached input, output). Models without a published price
# in this table report tokens only. GPT-5.6 rates are the public list prices after the
# July 2026 cut; no cached-input discount is assumed for them (conservative).
PRICES = {
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-5.6-luna": (0.20, 0.20, 1.20),
    "gpt-5.6-terra": (2.00, 2.00, 12.00),
}

@cache
def client() -> AsyncOpenAI:
    # the SDK retries rate limits, timeouts, connection and 5xx errors with backoff (honouring Retry-After)
    return AsyncOpenAI(max_retries=8, timeout=90)


def cost_usd(model: str, prompt: int, cached: int, completion: int):
    if model not in PRICES:
        return None
    i, c, o = PRICES[model]
    return ((prompt - cached) * i + cached * c + completion * o) / 1_000_000


async def chat(model: str, messages: list, meter=None, role="agent", **params):
    """One chat completion, metered into `meter`. Returns the message."""
    resp = await client().chat.completions.create(model=model, messages=messages, **params)
    if meter is not None:
        u = resp.usage
        details = u.prompt_tokens_details
        meter({"type": "llm_call", "role": role, "model": model, "prompt_tokens": u.prompt_tokens,
               "cached_tokens": (details and details.cached_tokens) or 0, "completion_tokens": u.completion_tokens})
    return resp.choices[0].message
