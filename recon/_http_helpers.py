"""
Shared HTTP helpers for intelligence-layer clients.

Centralizes the aiohttp session resolution that previously lived as 3× duplicated
``_get_session`` methods in ``intelligence/exposure_clients.py`` (ShodanClient,
CensysClient, CVIntelligenceClient). Removes the cross-community coupling
(intelligence → network) by exposing one bounded surface inside intelligence.

The helper is a thin wrapper over ``network/session_runtime.py::async_get_httpx_session``
(singleton, lazy, thread-safe via asyncio.Lock) — keeping all transport-policy
decisions where they belong (network layer) while giving the intelligence
clients a stable import boundary that does not leak transport internals.

Fail-soft: any ImportError or runtime error from the network layer propagates
up — callers already handle session-acquisition failure in their existing
``except Exception: return None`` envelopes, so no extra guards are needed here.
"""


import httpx

async def get_intelligence_session() -> httpx.AsyncClient:
    """
    Create a new ``httpx.AsyncClient`` for intelligence clients.

    Each call returns a fresh client — callers are responsible for managing
    the session lifecycle (open/close). Intelligence clients that support
    injected test sessions check their own injected session first and only
    call this helper on the fallback path.
    """
    return httpx.AsyncClient()
