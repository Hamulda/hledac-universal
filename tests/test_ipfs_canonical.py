"""
Tests for IPFS canonical finding integration.

Sprint F196: IPFS → CanonicalFinding persistence.
Sprint F218Z: IPFS via gateway fetch (Tor optional).
Sprint F250F: IPFS discovery wired to sidecar orchestrator.

Invariant tests:
- ipfs_content_to_finding_dict produces valid CanonicalFinding-compatible dict
- source_type is "ipfs_fetch" or "ipfs_search" (not bare "ipfs")
- provenance tuple is ("ipfs://{cid}",)
- payload_text bounded to 4096 chars
- 10MB cap enforced by fetch_ipfs
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hledac.universal.network.ipfs_client import (
    MAX_FILE_SIZE_BYTES,
    ipfs_content_to_finding_dict,
    ipfs_fetch_as_findings,
    ipfs_search_as_findings,
)


class TestIpfsContentToFindingDict:
    """Test transform from IPFS content to CanonicalFinding-compatible dict."""

    def test_produces_valid_dict_structure(self):
        """Dict has all required CanonicalFinding fields."""
        result = ipfs_content_to_finding_dict(
            cid="QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX",
            content=b"Hello IPFS world",
            query="test_query",
            source_type="ipfs_fetch",
        )

        assert "finding_id" in result
        assert "query" in result
        assert "source_type" in result
        assert result["source_type"] == "ipfs_fetch"
        assert "confidence" in result
        assert "ts" in result
        assert "provenance" in result
        assert isinstance(result["provenance"], tuple)
        assert "payload_text" in result

    def test_source_type_is_ipfs_fetch(self):
        """source_type is 'ipfs_fetch' for direct CID fetch."""
        result = ipfs_content_to_finding_dict(
            cid="QmABC",
            content=b"data1",
            query="q",
            source_type="ipfs_fetch",
        )
        assert result["source_type"] == "ipfs_fetch"

    def test_source_type_is_ipfs_search(self):
        """source_type is 'ipfs_search' for search-based discovery."""
        result = ipfs_content_to_finding_dict(
            cid="QmDEF",
            content=b"data2",
            query="q",
            source_type="ipfs_search",
        )
        assert result["source_type"] == "ipfs_search"

    def test_provenance_tuple_is_ipfs_uri(self):
        """Provenance is a 1-tuple: ("ipfs://{cid}",)."""
        result = ipfs_content_to_finding_dict(
            cid="QmTestCID123",
            content=b"content",
            query="my_query",
            source_type="ipfs_fetch",
        )

        provenance = result["provenance"]
        assert len(provenance) == 1
        assert provenance[0] == "ipfs://QmTestCID123"

    def test_payload_text_bounded_to_4096_chars(self):
        """payload_text is truncated to 4096 chars (LMDB WAL limit)."""
        long_content = b"x" * 5000
        result = ipfs_content_to_finding_dict(
            cid="QmLong",
            content=long_content,
            query="q",
            source_type="ipfs_fetch",
        )

        assert result["payload_text"] is not None
        assert len(result["payload_text"]) <= 4096

    def test_finding_id_is_unique_per_content(self):
        """Different content produces different finding_id."""
        cid = "QmTest"
        ts = time.time()

        result1 = ipfs_content_to_finding_dict(
            cid=cid, content=b"content1", query="q", source_type="ipfs_fetch"
        )
        result2 = ipfs_content_to_finding_dict(
            cid=cid, content=b"content2", query="q", source_type="ipfs_fetch"
        )

        assert result1["finding_id"] != result2["finding_id"]

    def test_bytes_and_str_content_both_accepted(self):
        """Content can be bytes or str."""
        result_bytes = ipfs_content_to_finding_dict(
            cid="QmTest", content=b"hello", query="q", source_type="ipfs_fetch"
        )
        result_str = ipfs_content_to_finding_dict(
            cid="QmTest", content="hello", query="q", source_type="ipfs_fetch"
        )

        assert result_bytes["payload_text"] == result_str["payload_text"]

    def test_confidence_is_075_for_ipfs_fetch(self):
        """Direct CID fetch has confidence 0.75."""
        result = ipfs_content_to_finding_dict(
            cid="QmX", content=b"x", query="q", source_type="ipfs_fetch"
        )
        assert result["confidence"] == 0.75

    def test_query_field_preserved(self):
        """Query field is preserved from input."""
        result = ipfs_content_to_finding_dict(
            cid="QmX", content=b"x", query="myioc", source_type="ipfs_fetch"
        )
        assert result["query"] == "myioc"


class TestFetchIpfsSizeCap:
    """Test 10MB size cap enforcement."""

    def test_max_file_size_constant_is_10mb(self):
        """MAX_FILE_SIZE_BYTES is exactly 10 MB."""
        assert MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024


class TestIpfsFetchAsFindings:
    """Test ipfs_fetch_as_findings returns CanonicalFinding list."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_fetch_failure(self):
        """Fail-soft: returns [] when fetch_ipfs returns None."""
        with patch(
            "hledac.universal.network.ipfs_client.fetch_ipfs",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await ipfs_fetch_as_findings("QmTest", "test_query", timeout=30)
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_exception(self):
        """Fail-soft: returns [] when fetch_ipfs raises."""
        with patch(
            "hledac.universal.network.ipfs_client.fetch_ipfs",
            new_callable=AsyncMock,
            side_effect=Exception("network error"),
        ):
            result = await ipfs_fetch_as_findings("QmTest", "test_query", timeout=30)
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_finding_on_success(self):
        """Returns CanonicalFinding list when fetch succeeds."""
        with patch(
            "hledac.universal.network.ipfs_client.fetch_ipfs",
            new_callable=AsyncMock,
            return_value=b"IPFS content here",
        ):
            result = await ipfs_fetch_as_findings("QmTest", "test_query", timeout=30)
            assert len(result) == 1
            assert result[0].source_type == "ipfs_fetch"
            assert result[0].query == "test_query"


class TestIpfsSearchAsFindings:
    """Test ipfs_search_as_findings returns CanonicalFinding list."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_search_failure(self):
        """Fail-soft: returns [] when search_ipfs raises."""
        with patch(
            "hledac.universal.network.ipfs_client.search_ipfs",
            new_callable=AsyncMock,
            side_effect=Exception("search error"),
        ):
            result = await ipfs_search_as_findings("malware", timeout_per_result=30)
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_cids(self):
        """Fail-soft: returns [] when search_ipfs returns empty list."""
        with patch(
            "hledac.universal.network.ipfs_client.search_ipfs",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await ipfs_search_as_findings("malware", timeout_per_result=30)
            assert result == []

    @pytest.mark.asyncio
    async def test_caps_at_20_cids(self):
        """Search caps at 20 CIDs for M1 safety."""
        many_cids = [f"Qm{i:046d}" for i in range(30)]
        with patch(
            "hledac.universal.network.ipfs_client.search_ipfs",
            new_callable=AsyncMock,
            return_value=many_cids,
        ):
            with patch(
                "hledac.universal.network.ipfs_client.fetch_ipfs",
                new_callable=AsyncMock,
                return_value=b"content",
            ):
                result = await ipfs_search_as_findings("malware", timeout_per_result=30)
                assert len(result) <= 20
