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


from unittest.mock import AsyncMock, patch

import pytest


# ── Fake httpx Response ─────────────────────────────────────────────────────────

class FakeHttpxResponse:
    """Fake httpx.Response — status_code + async json()."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    async def json(self) -> dict:
        return self._payload


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

        # Mock _ipfs_checked_get to return canned RIPE responses
        prefix_resp = FakeHttpxResponse({
            "data": {
                "prefixes": [
                    {"asn": "15169", "prefix": "8.8.8.0/24", "holder": "GOOGLE, US"}
                ]
            }
        })
        whois_resp = FakeHttpxResponse({
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
        })

        async def fake_checked_get(session, url, *, timeout=None, failure_kind=None):
            if "prefix-overview" in url:
                return prefix_resp, None
            elif "whois" in url:
                return whois_resp, None
            return None, "unknown_url"

        # Also patch circuit breaker to allow stat.ripe.net
        breaker_mock = AsyncMock()
        breaker_mock.allowed = True
        breaker_mock.reason = "test"

        with patch(
            "hledac.universal.network.ipfs_client._ipfs_checked_get",
            side_effect=fake_checked_get,
        ), patch(
            "hledac.universal.transport.circuit_breaker.domain_breaker_check",
            return_value=breaker_mock,
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
        """Fail-soft: session error → return []."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        async def fake_fail(session, url, *, timeout=None, failure_kind=None):
            return None, "session_error"

        with patch(
            "hledac.universal.network.ipfs_client._ipfs_checked_get",
            side_effect=fake_fail,
        ):
            findings = await enrich_ip_as_finding("1.1.1.1")

        assert findings == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_asn_prefix(self) -> None:
        """RIPE returns empty prefixes → return [] (fail-soft)."""
        from hledac.universal.network.bgp_monitor import enrich_ip_as_finding

        async def fake_empty_prefix(session, url, *, timeout=None, failure_kind=None):
            return FakeHttpxResponse({"data": {"prefixes": []}}), None

        with patch(
            "hledac.universal.network.ipfs_client._ipfs_checked_get",
            side_effect=fake_empty_prefix,
        ):
            findings = await enrich_ip_as_finding("8.8.8.8")

        assert findings == []
