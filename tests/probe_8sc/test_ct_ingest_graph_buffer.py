"""Sprint 8SC: CT ingest to graph buffer."""

import pytest

from hledac.universal.intelligence.ct_log_client import CTLogClient


@pytest.mark.asyncio
async def test_ct_ingest_graph_buffer():
    """ingest_to_graph() calls buffer_ioc for each SAN."""
    ct = CTLogClient.__new__(CTLogClient)

    ct_result = {
        "domain": "example.com",
        "san_names": ["sa.example.com", "sb.example.com", "sc.example.com"],
        "issuers": ["DigiCert"],
        "first_cert": 1.0,
        "last_cert": 2.0,
        "cert_count": 3,
    }

    class MockGraph:
        def __init__(self):
            self.calls = []
        async def buffer_ioc(self, ioc_type, value, confidence=0.5):
            self.calls.append((ioc_type, value, confidence))

    mock_graph = MockGraph()
    count = await ct.ingest_to_graph(ct_result, mock_graph)

    assert count == 3
    assert len(mock_graph.calls) == 3
    assert mock_graph.calls[0] == ("domain", "sa.example.com", 0.75)
    assert mock_graph.calls[1] == ("domain", "sb.example.com", 0.75)
    assert mock_graph.calls[2] == ("domain", "sc.example.com", 0.75)
