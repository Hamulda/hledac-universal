"""
Shared HTTP helpers for intelligence-layer clients.

Centralizes the aiohttp session resolution that previously lived as 3× duplicated
``_get_session`` methods in ``intelligence/exposure_clients.py`` (ShodanClient,
CensysClient, CVIntelligenceClient). Removes the cross-community coupling
(intelligence → network) by exposing one bounded surface inside intelligence.

The helper is a thin wrapper over ``network/session_runtime.py::async_get_aiohttp_session``
(singleton, lazy, thread-safe via asyncio.Lock) — keeping all transport-policy
decisions where they belong (network layer) while giving the intelligence
clients a stable import boundary that does not leak transport internals.

Fail-soft: any ImportError or runtime error from the network layer propagates
up — callers already handle session-acquisition failure in their existing
``except Exception: return None`` envelopes, so no extra guards are needed here.
"""

from __future__ import annotations

from hledac.universal.network.session_runtime import async_get_aiohttp_session


async def get_intelligence_session():
    """
    Resolve the shared ``aiohttp.ClientSession`` for intelligence clients.

    Thin wrapper over
    ``hledac.universal.network.session_runtime::async_get_aiohttp_session``
    (lazy singleton, [I2] no session until first await, [I3] repeated awaits
    return the same instance). Centralizes the import boundary so the
    intelligence layer does not couple directly to the network layer.

    Clients that support an injected test session keep their own
    short-circuit and call this only on the fallback path.
    """
    return await async_get_aiohttp_session()
