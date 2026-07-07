"""
Tests for ``intelligence/_http_helpers.py`` — shared aiohttp session resolver
extracted from the 3× duplicated ``_get_session`` methods in
``intelligence/exposure_clients.py``.

AAA pattern. All tests hermetic — mock the network-layer singleton directly so
no real session is created (avoids polluting the shared session across the
test process).
"""


import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp

from hledac.universal.intelligence._http_helpers import get_intelligence_session
from hledac.universal.intelligence.exposure_clients import (
    CensysClient,
    CVIntelligenceClient,
    ShodanClient,
)

# ---------------------------------------------------------------------------
# get_intelligence_session — direct unit tests
# ---------------------------------------------------------------------------


def test_get_intelligence_session_returns_session() -> None:
    """Helper awaits the shared aiohttp session and returns it unchanged."""
    async def _run() -> None:
        fake_session = aiohttp.ClientSession()
        try:
            with patch(
                "hledac.universal.network.session_runtime.async_get_aiohttp_session",
                new=AsyncMock(return_value=fake_session),
            ):
                result = await get_intelligence_session()
                assert result is fake_session
        finally:
            if not fake_session.closed:
                await fake_session.close()

    asyncio.run(_run())


def test_get_intelligence_session_calls_network_singleton() -> None:
    """Helper must delegate to network/session_runtime::async_get_aiohttp_session — the
    single source of truth for the aiohttp singleton. This is the only call it makes."""
    async def _run() -> None:
        fake_session = aiohttp.ClientSession()
        try:
            with patch(
                "hledac.universal.network.session_runtime.async_get_aiohttp_session",
                new=AsyncMock(return_value=fake_session),
            ) as mocked:
                await get_intelligence_session()
                # Exactly one await, no extra kwargs
                assert mocked.await_count == 1
                assert mocked.await_args is not None
                assert mocked.await_args.args == ()
                assert mocked.await_args.kwargs == {}
        finally:
            if not fake_session.closed:
                await fake_session.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Client smoke tests — verify all 3 classes route through the shared helper
# ---------------------------------------------------------------------------


def test_shodan_client_uses_shared_helper() -> None:
    """ShodanClient._get_session must route through get_intelligence_session
    when no injected session is present. With an injected session, the helper
    must NOT be called (short-circuit preserved)."""
    async def _run() -> None:
        fake_session = aiohttp.ClientSession()
        injected = aiohttp.ClientSession()
        try:
            with patch(
                "hledac.universal.network.session_runtime.async_get_aiohttp_session",
                new=AsyncMock(return_value=fake_session),
            ) as mocked:
                # Case 1: no injected session → helper called
                client = ShodanClient()
                got = await client._get_session()
                assert got is fake_session
                assert mocked.await_count == 1

                # Case 2: injected session present → helper NOT called, short-circuit
                client_with = ShodanClient(session=injected)
                got2 = await client_with._get_session()
                assert got2 is injected
                assert mocked.await_count == 1  # unchanged
        finally:
            if not fake_session.closed:
                await fake_session.close()
            if not injected.closed:
                await injected.close()

    asyncio.run(_run())


def test_censys_client_uses_shared_helper() -> None:
    """CensysClient._get_session must route through get_intelligence_session
    when no injected session is present. Injected-session short-circuit preserved."""
    async def _run() -> None:
        fake_session = aiohttp.ClientSession()
        injected = aiohttp.ClientSession()
        try:
            with patch(
                "hledac.universal.network.session_runtime.async_get_aiohttp_session",
                new=AsyncMock(return_value=fake_session),
            ) as mocked:
                # Case 1: no injected session → helper called
                client = CensysClient()
                got = await client._get_session()
                assert got is fake_session
                assert mocked.await_count == 1

                # Case 2: injected session present → helper NOT called
                client_with = CensysClient(session=injected)
                got2 = await client_with._get_session()
                assert got2 is injected
                assert mocked.await_count == 1
        finally:
            if not fake_session.closed:
                await fake_session.close()
            if not injected.closed:
                await injected.close()

    asyncio.run(_run())


def test_cv_intelligence_client_uses_shared_helper() -> None:
    """CVIntelligenceClient has no injected-session concept — its _get_session
    is a pure passthrough to the helper."""
    async def _run() -> None:
        fake_session = aiohttp.ClientSession()
        try:
            with patch(
                "hledac.universal.network.session_runtime.async_get_aiohttp_session",
                new=AsyncMock(return_value=fake_session),
            ) as mocked:
                client = CVIntelligenceClient()
                got = await client._get_session()
                assert got is fake_session
                assert mocked.await_count == 1
        finally:
            if not fake_session.closed:
                await fake_session.close()

    asyncio.run(_run())


def test_helper_module_does_not_export_session_symbol() -> None:
    """The helper module must not re-export ``async_get_aiohttp_session`` —
    the whole point of the extraction is to keep the network layer private
    to the intelligence layer. Belt-and-suspenders check against accidental
    re-import leakage."""
    import hledac.universal.intelligence._http_helpers as helpers_mod

    assert not hasattr(helpers_mod, "async_get_aiohttp_session"), (
        "_http_helpers must not leak the network-layer symbol"
    )
    assert hasattr(helpers_mod, "get_intelligence_session")
