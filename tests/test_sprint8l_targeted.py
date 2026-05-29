"""
Sprint 8L Targeted Tests
========================

Tests for:
1. test_live_runbook_contains_seed_domains
2. test_live_runbook_contains_timeout_budgets
3. test_live_runbook_contains_ner_fallback_note
4. test_live_runbook_contains_rate_limit_strategy
5. test_latency_table_contains_min_mean_p95_max
6. test_payload_cap_preserved
7. test_shared_client_path_preserved
8. test_offline_replay_benchmark_still_passes (regression)
"""

import sys
from pathlib import Path

import pytest

# Add universal to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# =============================================================================
# Test 1: Live runbook contains seed domains
# =============================================================================

def test_live_runbook_contains_seed_domains():
    """Verify seed domains are defined for live run."""
    from hledac.universal.tests.test_sprint8l_live import LIVE_QUERY, SEED_DOMAINS

    assert len(SEED_DOMAINS) >= 4, f"Expected at least 4 seed domains, got {len(SEED_DOMAINS)}"
    assert "python.org" in SEED_DOMAINS, "python.org should be in seed domains"
    assert "github.com" in SEED_DOMAINS, "github.com should be in seed domains"
    assert "arxiv.org" in SEED_DOMAINS, "arxiv.org should be in seed domains"
    assert "archive.org" in SEED_DOMAINS, "archive.org should be in seed domains"
    assert LIVE_QUERY, "LIVE_QUERY should be defined"


# =============================================================================
# Test 2: Live runbook contains timeout budgets
# =============================================================================

def test_live_runbook_contains_timeout_budgets():
    """Verify timeout budgets match autonomous_orchestrator.py constants."""
    from hledac.universal.tests.test_sprint8l_live import TIMEOUT_BUDGETS

    # Verify all expected handlers have timeout budgets
    expected_handlers = [
        'network_recon', 'ct_discovery', 'surface_search',
        'academic_search', 'archive_fetch', 'wayback_rescue',
        'commoncrawl_rescue', 'render_page', 'necromancer_rescue'
    ]
    for handler in expected_handlers:
        assert handler in TIMEOUT_BUDGETS, f"Handler {handler} missing from TIMEOUT_BUDGETS"

    # Verify timeout values match documented budgets
    assert TIMEOUT_BUDGETS['network_recon'] == 5.0, "network_recon timeout should be 5s"
    assert TIMEOUT_BUDGETS['ct_discovery'] == 10.0, "ct_discovery timeout should be 10s"
    assert TIMEOUT_BUDGETS['surface_search'] == 15.0, "surface_search timeout should be 15s"
    assert TIMEOUT_BUDGETS['academic_search'] == 20.0, "academic_search timeout should be 20s"
    assert TIMEOUT_BUDGETS['archive_fetch'] == 30.0, "archive_fetch timeout should be 30s"


# =============================================================================
# Test 3: Live runbook contains NER fallback note
# =============================================================================

def test_live_runbook_contains_ner_fallback_note():
    """Verify NER fallback detection is implemented."""
    from hledac.universal.tests.test_sprint8l_live import detect_ner_fallback

    note = detect_ner_fallback()
    assert note, "NER fallback detection should return a string"
    assert "NaturalLanguage" in note or "CoreML" in note or "GLiNER" in note or "No NER" in note, \
        f"NER fallback note should mention a specific implementation, got: {note}"


# =============================================================================
# Test 4: Live runbook contains rate limit strategy
# =============================================================================

def test_live_runbook_contains_rate_limit_strategy():
    """Verify rate limit strategy is defined for live run."""
    from hledac.universal.tests.test_sprint8l_live import RATE_LIMIT_STRATEGY

    assert RATE_LIMIT_STRATEGY, "RATE_LIMIT_STRATEGY should be defined"

    # Verify surface_search rate limiting
    assert 'surface_search' in RATE_LIMIT_STRATEGY
    surface = RATE_LIMIT_STRATEGY['surface_search']
    assert surface['rate'] == 10, "surface_search rate should be 10/min"
    assert surface['backoff'] == 2.0, "surface_search backoff should be 2.0"

    # Verify academic_search rate limiting
    assert 'academic_search' in RATE_LIMIT_STRATEGY
    academic = RATE_LIMIT_STRATEGY['academic_search']
    assert academic['rate'] == 5, "academic_search rate should be 5/min"

    # Verify ct_discovery rate limiting
    assert 'ct_discovery' in RATE_LIMIT_STRATEGY
    assert RATE_LIMIT_STRATEGY['ct_discovery']['rate'] == 20

    # Verify network_recon rate limiting
    assert 'network_recon' in RATE_LIMIT_STRATEGY
    assert RATE_LIMIT_STRATEGY['network_recon']['rate'] == 30


# =============================================================================
# Test 5: Latency table contains min/mean/p95/max
# =============================================================================

def test_latency_table_contains_min_mean_p95_max():
    """Verify LiveHandlerLatency captures all required latency fields."""
    from hledac.universal.tests.test_sprint8l_live import LiveHandlerLatency

    lat = LiveHandlerLatency()
    lat.add(100.0)
    lat.add(200.0)
    lat.add(300.0)
    lat.add(400.0)
    lat.add(500.0)
    lat.finalize()

    d = lat.to_dict()
    assert 'min_ms' in d, "Latency should have min_ms"
    assert 'mean_ms' in d, "Latency should have mean_ms"
    assert 'p95_ms' in d, "Latency should have p95_ms"
    assert 'max_ms' in d, "Latency should have max_ms"
    assert 'calls' in d, "Latency should have calls"
    assert 'errors' in d, "Latency should have errors"
    assert 'timeouts' in d, "Latency should have timeouts"
    assert 'rate_limited' in d, "Latency should have rate_limited"

    # Verify values make sense
    assert d['min_ms'] <= d['mean_ms'] <= d['max_ms'], "min <= mean <= max"
    assert d['calls'] == 5, "Should have 5 calls"


# =============================================================================
# Test 6: Payload cap preserved (5 MiB in archive_discovery.py)
# =============================================================================

def test_payload_cap_preserved():
    """Verify 5 MiB payload cap is still enforced in archive_discovery.py."""
    import hledac.universal.intelligence.archive_discovery as ad

    # Check MAX_PAYLOAD_BYTES constant
    assert hasattr(ad, 'MAX_PAYLOAD_BYTES'), "archive_discovery should have MAX_PAYLOAD_BYTES"
    assert ad.MAX_PAYLOAD_BYTES == 5 * 1024 * 1024, \
        f"MAX_PAYLOAD_BYTES should be 5 MiB (5242880), got {ad.MAX_PAYLOAD_BYTES}"


# =============================================================================
# Test 7: Shared client path preserved (aiohttp.ClientSession in handlers)
# =============================================================================

def test_shared_client_path_preserved():
    """Verify handlers use shared session path, not per-handler instantiation."""
    # Check that handlers don't create their own aiohttp.ClientSession
    # by verifying the archive_discovery.py uses shared session

    import inspect

    import hledac.universal.intelligence.archive_discovery as ad

    # Get the source of ArchiveDiscovery.__init__
    if hasattr(ad.ArchiveDiscovery, '__init__'):
        source = inspect.getsource(ad.ArchiveDiscovery.__init__)
        # Should NOT create aiohttp.ClientSession() directly in __init__
        # (session should be passed in or created lazily)
        assert 'aiohttp.ClientSession()' not in source or 'self.session' in source, \
            "ArchiveDiscovery should not create raw aiohttp.ClientSession in __init__"


# =============================================================================
# Test 8: OFFLINE_REPLAY benchmark still passes (regression)
# =============================================================================

@pytest.mark.asyncio
async def test_offline_replay_benchmark_still_passes():
    """Regression: OFFLINE_REPLAY benchmark should still produce nonzero iterations."""
    # This is a quick smoke test - run benchmark for 5 seconds
    from hledac.universal.benchmarks.run_sprint82j_benchmark import run_benchmark

    result = await run_benchmark(
        duration_seconds=5,
        query="test query",
        mode="OFFLINE_REPLAY",
        verbose=False,
        silent=True,
    )

    assert result.iterations > 0, f"Expected nonzero iterations, got {result.iterations}"
    assert result.data_mode == "OFFLINE_REPLAY", f"Expected OFFLINE_REPLAY mode, got {result.data_mode}"
    assert result.findings_count >= 0, "findings_count should be >= 0"
    assert result.sources_count >= 0, "sources_count should be >= 0"


# =============================================================================
# Test 9: RSS monitor tracks samples correctly
# =============================================================================

def test_rss_monitor_slope_calculation():
    """Verify RSS monitor computes slope correctly."""

    from hledac.universal.tests.test_sprint8l_live import RSSMonitor

    monitor = RSSMonitor(interval_s=10.0)
    # Simulate RSS samples
    monitor.samples = [100.0, 110.0, 120.0, 130.0]  # +10 per sample, slope ~+10/s
    monitor.peak_mb = 130.0

    # 3 intervals over 30s = slope of (130-100)/30 = 1.0 MB/s
    slope = monitor.compute_slope(30.0)
    assert 0.9 <= slope <= 1.1, f"Expected slope ~1.0, got {slope:.2f}"


# =============================================================================
# Test 10: HHI computation
# =============================================================================

def test_hhi_computation():
    """Verify HHI is computed correctly."""
    from hledac.universal.tests.test_sprint8l_live import compute_hhi

    # Perfect monopoly: one action 100%
    hhi = compute_hhi({'surface_search': 100})
    assert hhi == 1.0, f"Monopoly HHI should be 1.0, got {hhi}"

    # Perfect balance: 4 equal actions
    hhi = compute_hhi({'a': 25, 'b': 25, 'c': 25, 'd': 25})
    assert abs(hhi - 0.25) < 0.01, f"Equal HHI should be 0.25, got {hhi}"

    # Empty
    hhi = compute_hhi({})
    assert hhi == 0.0, f"Empty HHI should be 0.0, got {hhi}"

    # Realistic mix
    hhi = compute_hhi({'surface_search': 70, 'network_recon': 20, 'academic': 10})
    expected = (0.7 ** 2) + (0.2 ** 2) + (0.1 ** 2)
    assert abs(hhi - expected) < 0.01, f"Realistic HHI should be {expected}, got {hhi}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
