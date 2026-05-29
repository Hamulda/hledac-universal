import pytest
from unittest.mock import patch

from hledac.universal.network.ipfs_client import extract_cids_from_text


def test_extract_ipfs_cid_from_finding_content():
    """IPFSClient extrahuje CID z finding content."""
    test_content = "Found reference to ipfs://QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
    matches = extract_cids_from_text(test_content)
    assert len(matches) == 1
    assert matches[0] == "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"


def test_bafy_cid_extracted():
    """bafy CID variant is extracted correctly."""
    test_content = "IPFS path: ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    matches = extract_cids_from_text(test_content)
    assert len(matches) == 1
    assert matches[0] == "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"


def test_no_false_positive_cid():
    """Krátké hash strings nejsou detekovány jako CID."""
    test_content = "SHA256: abc123def456 — not a CID"
    matches = extract_cids_from_text(test_content)
    assert len(matches) == 0


def test_no_false_positive_short_qm():
    """Qm hash len < 44 not matched."""
    test_content = "QmABC not a real cid"
    matches = extract_cids_from_text(test_content)
    assert len(matches) == 0


def test_multiple_cids_extracted():
    """Multiple CIDs in content are all extracted."""
    test_content = (
        "First: QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG "
        "Second: bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    )
    matches = extract_cids_from_text(test_content)
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_fetch_findings_from_cids_empty_input():
    """Prázdný CID list vrací [] bez I/O."""
    import os
    from hledac.universal.network.ipfs_client import fetch_findings_from_cids

    with patch.dict(os.environ, {"HLEDAC_ENABLE_IPFS": "1"}):
        result = await fetch_findings_from_cids([], query="test")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_findings_from_cids_deduplication():
    """Duplicitní CID se fetchne pouze jednou."""
    import os
    from unittest.mock import AsyncMock, patch, MagicMock

    from hledac.universal.network import ipfs_client

    cid = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
    mock_governor = MagicMock()
    mock_governor.evaluate = AsyncMock(return_value=MagicMock(uma_state="ok"))

    with patch.dict(os.environ, {"HLEDAC_ENABLE_IPFS": "1"}):
        with patch(
            "hledac.universal.runtime.resource_governor.get_governor",
            return_value=mock_governor,
        ):
            with patch.object(
                ipfs_client, "ipfs_fetch_as_findings", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = []
                await ipfs_client.fetch_findings_from_cids([cid, cid, cid], query="test")
                assert mock_fetch.call_count == 1  # dedup funguje


def test_canonical_finding_payload_text_has_cid():
    """CanonicalFinding.payload_text can contain IPFS CID."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    finding = CanonicalFinding(
        finding_id="test-find-ipfs-1",
        query="ipfs test",
        source_type="web_fetch",
        confidence=0.75,
        ts=0.0,
        provenance=("ipfs://QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG",),
        payload_text="Visit ipfs://QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG for full data",
    )
    cids = extract_cids_from_text(finding.payload_text or "")
    assert len(cids) == 1
    assert cids[0] == "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
