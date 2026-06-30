"""
Sprint 8TB probe tests — PassiveDNSClient reverse_lookup NXDOMAIN.
Sprint: 8TB
Area: PassiveDNS Client
"""

from unittest.mock import AsyncMock, patch

import pytest

from hledac.universal.intelligence.network_reconnaissance import PassiveDNSClient


class TestPassiveDNSReverseEmpty:
    """NXDOMAIN on reverse lookup returns empty list, no exception."""

    @pytest.mark.asyncio
    async def test_reverse_lookup_nxdomain_returns_empty(self):
        """dns.exception.NXDOMAIN → reverse_lookup returns []. No exception raised."""
        client = PassiveDNSClient()

        nx_domain = Exception("NXDOMAIN")

        with patch.object(client._resolver, "resolve", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.side_effect = nx_domain

            result = await client.reverse_lookup("1.2.3.4")

            assert result == []
