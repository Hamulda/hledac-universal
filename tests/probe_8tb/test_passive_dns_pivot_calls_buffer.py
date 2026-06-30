"""
Sprint 8TB probe tests — PassiveDNSClient pivot_domain.
Sprint: 8TB
Area: PassiveDNS Client
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.intelligence.network_reconnaissance import PassiveDNSClient


class TestPassiveDNSPivotCallsBuffer:
    """pivot_domain calls buffer_ioc on the IOC graph."""

    @pytest.mark.asyncio
    async def test_pivot_domain_calls_buffer_ioc(self):
        """resolve returns IPs → buffer_ioc called for each IP + reverse."""
        client = PassiveDNSClient()

        mock_graph = AsyncMock()

        # Mock resolve_domain to return IPs
        with patch.object(client, "resolve_domain", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ["1.2.3.4", "5.6.7.8"]

            # Mock reverse_lookup to return hostnames
            with patch.object(client, "reverse_lookup", new_callable=AsyncMock) as mock_rev:
                mock_rev.side_effect = [
                    ["host1.example.com"],
                    ["host2.example.com", "host3.example.com"],
                ]

                count = await client.pivot_domain("evil.com", mock_graph)

                # 2 IPs + up to 3 hostnames = 5 total buffered
                assert count == 5
                # Check IPv4 buffers
                calls = mock_graph.buffer_ioc.call_args_list
                assert mock_graph.buffer_ioc.call_count == 5
                # First IPCall: ipv4, "1.2.3.4"
                assert calls[0][0][0] == "ipv4"
                assert calls[0][0][1] == "1.2.3.4"
