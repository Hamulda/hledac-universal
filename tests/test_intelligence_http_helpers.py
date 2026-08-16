"""Tests for intelligence/_http_helpers.py - shared httpx session resolver."""

from unittest.mock import AsyncMock, patch

import pytest

# Lazy import: httpx loaded only when tests run (skip entire module if unavailable)
pytest.importorskip("httpx")

from hledac.universal.recon._http_helpers import get_intelligence_session  # noqa: E402
from hledac.universal.recon.exposure_clients import (  # noqa: E402
    CensysClient,
    CVIntelligenceClient,
    ShodanClient,
)


@pytest.mark.asyncio
async def test_get_intelligence_session_returns_httpx_client():
    """Helper returns an httpx.AsyncClient instance."""
    import httpx  # noqa: F401 - lazy import

    result = await get_intelligence_session()
    assert isinstance(result, httpx.AsyncClient)


@pytest.mark.asyncio
async def test_get_intelligence_session_returns_fresh_client():
    """Each call returns a distinct client instance."""
    client1 = await get_intelligence_session()
    client2 = await get_intelligence_session()
    assert client1 is not client2


# === Client smoke tests using fixture ===
# Patch at the usage site: exposure_clients module where get_intelligence_session is imported.


@pytest.mark.asyncio
async def test_shodan_client_uses_shared_helper():
    """ShodanClient._get_session routes through get_intelligence_session."""
    async with httpx.AsyncClient() as fake_session:
        async with httpx.AsyncClient() as injected:
            with patch(
                "hledac.universal.recon.exposure_clients.get_intelligence_session",
                new=AsyncMock(return_value=fake_session),
            ):
                # Case 1: no injected session -> helper called
                client = ShodanClient()
                got = await client._get_session()
                assert got is fake_session

                # Case 2: injected session present -> helper NOT called
                client_with = ShodanClient(session=injected)
                got2 = await client_with._get_session()
                assert got2 is injected


@pytest.mark.asyncio
async def test_censys_client_uses_shared_helper():
    """CensysClient._get_session routes through get_intelligence_session."""
    async with httpx.AsyncClient() as fake_session:
        async with httpx.AsyncClient() as injected:
            with patch(
                "hledac.universal.recon.exposure_clients.get_intelligence_session",
                new=AsyncMock(return_value=fake_session),
            ):
                # Case 1: no injected session -> helper called
                client = CensysClient()
                got = await client._get_session()
                assert got is fake_session

                # Case 2: injected session present -> helper NOT called
                client_with = CensysClient(session=injected)
                got2 = await client_with._get_session()
                assert got2 is injected


@pytest.mark.asyncio
async def test_cv_intelligence_client_uses_shared_helper():
    """CVIntelligenceClient._get_session is a pure passthrough to the helper."""
    async with httpx.AsyncClient() as fake_session:
        with patch(
            "hledac.universal.recon.exposure_clients.get_intelligence_session",
            new=AsyncMock(return_value=fake_session),
        ):
            client = CVIntelligenceClient()
            got = await client._get_session()
            assert got is fake_session


def test_helper_module_exports_session_symbol():
    """_http_helpers exports get_intelligence_session."""
    import hledac.universal.recon._http_helpers as helpers_mod

    assert hasattr(helpers_mod, "get_intelligence_session")
    assert not hasattr(helpers_mod, "async_get_aiohttp_session")
