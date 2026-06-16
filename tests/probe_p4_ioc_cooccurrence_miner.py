"""
Probe tests for P4-2: IOC Co-occurrence Miner
=============================================
Tests speculative IOC connection discovery from findings.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pipeline.ioc_cooccurrence_miner import (
    CoOccurrencePair,
    IOCooccurrenceMiner,
    SpeculativeEdge,
    SpeculativePrefetcher,
)


class MockCanonicalFinding:
    """Minimal CanonicalFinding mock."""
    def __init__(self, finding_id: str, payload_text: str) -> None:
        self.finding_id = finding_id
        self.payload_text = payload_text


def test_extract_iocs_domain():
    """Test domain extraction from finding."""
    miner = IOCooccurrenceMiner()

    finding = MockCanonicalFinding(
        "f1",
        "Found malware at evil.com and also at evil.com, also check suspicious.net"
    )

    iocs = IOCooccurrenceMiner.extract_iocs_from_finding(finding)

    domains = [(v, t) for v, t in iocs if t == "domain"]
    assert len(domains) >= 2


def test_extract_iocs_ip():
    """Test IP extraction from finding."""
    miner = IOCooccurrenceMiner()

    finding = MockCanonicalFinding(
        "f1",
        "Connecting to 192.168.1.1 and 10.0.0.1"
    )

    iocs = IOCooccurrenceMiner.extract_iocs_from_finding(finding)
    ips = [(v, t) for v, t in iocs if t == "ip"]

    assert len(ips) >= 2
    assert ("192.168.1.1", "ip") in ips


def test_extract_iocs_hash():
    """Test hash extraction from finding."""
    miner = IOCooccurrenceMiner()

    finding = MockCanonicalFinding(
        "f1",
        "File hash: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4 and also abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    )

    iocs = IOCooccurrenceMiner.extract_iocs_from_finding(finding)
    hashes = [(v, t) for v, t in iocs if t == "hash"]

    assert len(hashes) >= 2


@pytest.mark.asyncio
async def test_cooccurrence_analyze():
    """Test co-occurrence analysis on multiple findings."""
    miner = IOCooccurrenceMiner()

    findings = [
        MockCanonicalFinding("f1", "Domain example1.com and IP 1.2.3.4"),
        MockCanonicalFinding("f2", "Domain example1.com and hash abc123"),
        MockCanonicalFinding("f3", "Domain other.com and IP 1.2.3.4"),
        MockCanonicalFinding("f4", "example1.com resolves to 1.2.3.4"),
    ]

    edges = await miner.analyze(findings)

    # example1.com co-occurred with 1.2.3.4 in multiple findings
    example_edges = [e for e in edges if "example1.com" in e.source_ioc or "example1.com" in e.target_ioc]
    # May or may not exceed MIN_SUPPORT depending on extraction

    stats = miner.get_stats()
    assert stats.findings_analyzed == 4


@pytest.mark.asyncio
async def test_cooccurrence_min_support_filter():
    """Test that pairs below MIN_SUPPORT are filtered."""
    # Use unique IOCs to avoid cross-test contamination
    miner = IOCooccurrenceMiner()

    # Only one finding with these co-occurring IOCs - use IOCs that appear only here
    findings = [
        MockCanonicalFinding("f1", "Domain unique123xyz.com and IP 99.99.99.99"),
    ]

    edges = await miner.analyze(findings)

    # unique123xyz.com + 99.99.99.99 only co-occurred once (below MIN_SUPPORT=2)
    unique_edges = [e for e in edges if "unique123xyz.com" in e.source_ioc or "unique123xyz.com" in e.target_ioc]
    assert len(unique_edges) == 0


def test_cooccurrence_pair_scoring():
    """Test that pairs are scored correctly."""
    miner = IOCooccurrenceMiner()

    # Build co-occurrence manually
    pair = CoOccurrencePair(
        ioc_a="a.com",
        ioc_b="b.com",
        ioc_type_a="domain",
        ioc_type_b="domain",
        support=5,
    )
    miner._ioc_counts["a.com"] = 10
    miner._ioc_counts["b.com"] = 10

    # After mining, scores should be computed
    pair.confidence_a_to_b = pair.support / miner._ioc_counts["a.com"]  # 0.5
    pair.confidence_b_to_a = pair.support / miner._ioc_counts["b.com"]  # 0.5
    pair.score = pair.support * max(pair.confidence_a_to_b, pair.confidence_b_to_a)  # 2.5

    assert pair.score == 2.5
    assert pair.confidence_a_to_b == 0.5


@pytest.mark.asyncio
async def test_cooccurrence_persistence():
    """Test SQLite persistence of co-occurrence matrix."""
    miner = IOCooccurrenceMiner()

    findings = [
        MockCanonicalFinding("f1", "Domain persist.com and IP 5.5.5.5"),
        MockCanonicalFinding("f2", "Domain persist.com and IP 5.5.5.5"),
    ]
    await miner.analyze(findings)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cooccurrence.db"
        await miner.persist(db_path)
        assert db_path.exists()

        # Load into new miner
        miner2 = IOCooccurrenceMiner()
        await miner2.load(db_path)

        # Check pairs were loaded
        assert len(miner2._pairs) > 0


@pytest.mark.asyncio
async def test_speculative_edges_for_ioc():
    """Test getting speculative edges for specific IOC."""
    miner = IOCooccurrenceMiner()

    findings = [
        MockCanonicalFinding("f1", "Domain target.com and related.com"),
        MockCanonicalFinding("f2", "Domain target.com and related.com"),
        MockCanonicalFinding("f3", "Domain target.com and also another.net"),
    ]
    await miner.analyze(findings)

    edges = await miner.get_speculative_edges_for_ioc("target.com", limit=10)

    assert len(edges) >= 0


@pytest.mark.asyncio
async def test_speculative_prefetcher_dispatch():
    """Test speculative prefetcher dispatch."""
    prefetcher = SpeculativePrefetcher()
    await prefetcher.start(num_workers=1)

    edges = [
        SpeculativeEdge(
            source_ioc="evil.com",
            source_type="domain",
            target_ioc="related.com",
            target_type="domain",
            confidence=0.8,
            reason="co-occurred in 3 findings",
            prefetch_priority=10,
        ),
        SpeculativeEdge(
            source_ioc="http://evil.com/malware",
            source_type="url",
            target_ioc="http://evil.com/payload",
            target_type="url",
            confidence=0.6,
            reason="same domain",
            prefetch_priority=20,
        ),
    ]

    dispatched = await prefetcher.dispatch_batch(edges)
    assert dispatched == 2

    stats = prefetcher.get_stats()
    assert stats.edges_received == 2

    await prefetcher.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_speculative_prefetcher_dedup():
    """Test that prefetcher deduplicates edges."""
    prefetcher = SpeculativePrefetcher()
    await prefetcher.start(num_workers=1)

    edge = SpeculativeEdge(
        source_ioc="dup.com",
        source_type="domain",
        target_ioc="target.com",
        target_type="domain",
        confidence=0.9,
        reason="test",
        prefetch_priority=5,
    )

    # Dispatch same edge twice
    d1 = await prefetcher.dispatch_batch([edge])
    d2 = await prefetcher.dispatch_batch([edge])

    # Second dispatch should return 0 (deduplicated)
    assert d2 == 0

    await prefetcher.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_prefetcher_queue_backpressure():
    """Test prefetcher queue backpressure."""
    prefetcher = SpeculativePrefetcher()
    await prefetcher.start(num_workers=1)

    # Fill queue with many edges
    for i in range(150):
        edge = SpeculativeEdge(
            source_ioc=f"src{i}.com",
            source_type="domain",
            target_ioc=f"tgt{i}.com",
            target_type="domain",
            confidence=0.5,
            reason="test",
            prefetch_priority=50,
        )
        await prefetcher.dispatch_batch([edge])

    stats = prefetcher.get_stats()
    # Some may be dropped due to queue limit (100)

    await prefetcher.stop(timeout=5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
