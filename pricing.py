# Anthropic API price table — the ONLY place prices live.
# Source: https://platform.claude.com/docs/en/about-claude/pricing
#         (docs.claude.com/en/docs/about-claude/pricing redirects here)
# Date checked: 2026-10-04
# Web search: "$10 per 1,000 searches" on the same page; failed searches are not billed.
# Cache write prices are the 5-minute TTL rate (1.25x input). The code does not use
# prompt caching today; if 1h caching is ever used, add a separate rate.

USD_TO_AUD = 1.55  # conversion for display only — all ledger maths is in USD

# USD per million tokens
PRICES = {
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10,
    },
}

WEB_SEARCH_USD_PER_REQUEST = 10.00 / 1000


class UnknownModelError(KeyError):
    pass


def compute_cost(model, input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
                 cache_read_input_tokens=0, web_search_requests=0):
    """Exact USD cost from an API response's usage numbers. Raises on unknown model."""
    p = PRICES.get(model)
    if p is None:
        raise UnknownModelError(
            f"No price for model {model!r} in pricing.py — add it from the official pricing page.")
    usd = (input_tokens * p["input"]
           + output_tokens * p["output"]
           + cache_creation_input_tokens * p["cache_write"]
           + cache_read_input_tokens * p["cache_read"]) / 1_000_000
    usd += web_search_requests * WEB_SEARCH_USD_PER_REQUEST
    return usd
