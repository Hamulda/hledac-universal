"""
tests/test_bgp_ripe_live.py

F234: Tests for enrich_ip_as_finding() with live RIPE Stat API.

Tests:
  1. RFC1918 / private IPs are filtered (return [])
  2. Valid public IPs produce CanonicalFinding with correct fields
  3. RIPE API fields (ASN, prefix, holder, country, org_name) are extracted
  4. Fail-soft: network errors return []

Invariant (F234):
  - extract_public_ips_from_text() is the canonical RFC1918 gate
  - Max 20 IPs per sprint — enforced by caller, not this function
  - 30s timeout per IP
"""

from __future__ import annotations

import pytest
from unittest.mock import patch


# ── Fake aiohttp (bypass AsyncMock parent issues) ─────────────────────────────────

class FakeResponse:
    """Fake aiohttp.ClientResponse — async context manager + json()."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:  # pragma: no cover
        pass

    async def json(self) -> dict:  # pragma: no cover — real code awaits this
        return self._payload


class FakeClientTimeout:
    """Fake aiohttp.ClientTimeout — must be callable (real aiohttp is a class)."""

    def __init__(
        self,
        *,
        total: float | None = None,
        connect: float | None = None,
        sock_read: float | None = None,
        sock_connect: float | None = None,
    ) -> None:
        self.total = total
        self.connect = connect
        self.sock_read = sock_read
        self.sock_connect = sock_connect


class FakeAiohttpModule:
    """Fake aiohttp module — replaces _get_aiohttp() return value."""

    ClientTimeout = FakeClientTimeout

    class ClientSession:
        """Fake ClientSession that dequeues from the global _RIPE_RESPONSES queue."""

        def __init__(self, *, timeout: object = None) -> None:
            self._timeout = timeout

        async def __aenter__(self) -> "FakeAiohttpModule.ClientSession":
            return self

        async def __aexit__(self, *args: object) -> None:  # pragma: no cover
            pass

        def get(self, url: str, **kwargs: object) -> FakeResponse:
            if not _RIPE_RESPONSES:
                raise Exception("No responses configured")
            return _RIPE_RESPONSES.pop(0)


# Global response queue — populated by each test, consumed by the fake session
_RIPE_RESPONSES: list[FakeResponse] = []


# ── Tests ────────────────────────────────────────────────────────────────────────

class TestEnrichIpAsFindingRfc1918:
    """RFC1918 / private IPs are NEVER sent to RIPE (F234 invariant)."""

    @pytest.mark.asyncio
    async def test_private_192_168_returns_empty(self) -> None:
        """192.168.x.x is RFC1918 private — must return [] without any HTTP call."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        result = await enrich_ip_as_finding("192.168.1.100")
        assert result == []

    @pytest.mark.asyncio
    async def test_private_10_range_returns_empty(self) -> None:
        """10.x.x.x is RFC1918 private — must return [] without any HTTP call."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        result = await enrich_ip_as_finding("10.0.5.1")
        assert result == []

    @pytest.mark.asyncio
    async def test_private_172_16_range_returns_empty(self) -> None:
        """172.16-31.x.x is RFC1918 private — must return [] without any HTTP call."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        result = await enrich_ip_as_finding("172.20.100.50")
        assert result == []

    @pytest.mark.asyncio
    async def test_loopback_127_returns_empty(self) -> None:
        """127.x.x.x is loopback — must return [] without any HTTP call."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        result = await enrich_ip_as_finding("127.0.0.1")
        assert result == []


class TestEnrichIpAsFindingCanonicalFinding:
    """enrich_ip_as_finding returns valid CanonicalFinding from RIPE API."""

    @pytest.mark.asyncio
    async def test_returns_single_finding_on_success(self) -> None:
        """RIPE returns ASN → one CanonicalFinding with bgp_ripe_stat source."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        global _RIPE_RESPONSES
        _RIPE_RESPONSES = [
            FakeResponse({
                "data": {
                    "prefixes": [
                        {"asn": "15169", "prefix": "8.8.8.0/24", "holder": "GOOGLE, US"}
                    ]
                }
            }),
            FakeResponse({
                "data": {
                    "country": "US",
                    "objects": {
                        "object": [
                            {
                                "attributes": {
                                    "attribute": [
                                        {"name": "org-name", "value": "Google LLC"},
                                        {"name": "abuse-mailbox", "value": "abuse@example.com"},
                                    ]
                                }
                            }
                        ]
                    }
                }
            }),
        ]

        fake_module = FakeAiohttpModule()
        with patch(
            "hledac.universal.network.bgp_monitor._get_aiohttp",
            return_value=fake_module,
        ):
            findings = await enrich_ip_as_finding("8.8.8.8")

        assert len(findings) == 1
        f = findings[0]
        assert f.source_type == "bgp_ripe_stat"
        assert f.confidence == 0.88
        assert f.query == "bgp_ripe:8.8.8.8"
        assert "15169" in f.payload_text
        assert "8.8.8.0/24" in f.payload_text
        assert "GOOGLE" in f.payload_text
        assert "US" in f.payload_text
        assert "Google LLC" in f.payload_text

    @pytest.mark.asyncio
    async def test_returns_empty_on_session_error(self) -> None:
        """Fail-soft: ClientSession construction error → return []."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        fake_module = FakeAiohttpModule()

        # Make ClientSession raise on construction
        with patch.object(
            FakeAiohttpModule,
            "ClientSession",
            side_effect=Exception("DNS failure"),
        ):
            findings = await enrich_ip_as_finding("1.1.1.1")

        assert findings == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_asn_prefix(self) -> None:
        """RIPE returns empty prefixes → return [] (fail-soft)."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        global _RIPE_RESPONSES
        _RIPE_RESPONSES = [
            FakeResponse({"data": {"prefixes": []}}),
        ]

        fake_module = FakeAiohttpModule()
        with patch(
            "hledac.universal.network.bgp_monitor._get_aiohttp",
            return_value=fake_module,
        ):
            findings = await enrich_ip_as_finding("8.8.8.8")

        assert findings == []
