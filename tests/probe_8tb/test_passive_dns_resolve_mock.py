"""
Sprint 8TB probe tests — PassiveDNSClient resolve_domain.
Sprint: 8TB
Area: PassiveDNS Client
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.intelligence.network_reconnaissance import PassiveDNSClient


class TestPassiveDNSResolveMock:
    """resolve_domain returns mocked IPs without real DNS calls."""

    @pytest.mark.asyncio
    async def test_resolve_domain_returns_ips(self):
        """Mock resolver returns IPs → resolve_domain returns list."""
        client = PassiveDNSClient()

        mock_answer = MagicMock()
        mock_answer.__iter__ = MagicMock(return_value=iter(["1.2.3.4", "5.6.7.8"]))

        with patch.object(client._resolver, "resolve", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = mock_answer

            result = await client.resolve_domain("example.com")

            assert result == ["1.2.3.4", "5.6.7.8"]
            mock_resolve.assert_called_once_with("example.com", "A")

    @pytest.mark.asyncio
    async def test_resolve_aaaa_returns_ipv6(self):
        """Mock AAAA resolver → resolve_aaaa returns IPv6 list."""
        client = PassiveDNSClient()

        mock_answer = MagicMock()
        mock_answer.__iter__ = MagicMock(return_value=iter(["::1", "fe80::1"]))

        with patch.object(client._resolver, "resolve", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = mock_answer

            result = await client.resolve_aaaa("example.com")

            assert result == ["::1", "fe80::1"]
            mock_resolve.assert_called_once_with("example.com", "AAAA")
