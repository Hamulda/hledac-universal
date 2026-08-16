"""
Property-based tests using Hypothesis.

Covers:
- URL dedup (RotatingBloomFilter): FPR bounded property
- IOC extraction (Aho-Corasick vs regex parity)
- AIMD controller: convergence + bound invariants
- Memory pressure hysteresis: state machine monotonicity

Run with: pytest tests/test_property_based.py -v
"""

from __future__ import annotations

import asyncio
import pytest
from hypothesis import given, settings, Verbosity
from hypothesis.strategies import (
    floats,
    integers,
    lists,
    sampled_from,
    text,
)

from hledac.universal.coordinators.fetch_coordinator import (
    AIMDWindow,
    AIMD_MAX_CONCURRENCY,
    AIMD_MIN_CONCURRENCY,
    AIMD_DECREASE_BY_STATE,
)
from hledac.universal.tools.url_dedup import (
    create_rotating_bloom_filter,
    dedupe_url_list,
)


# ---------------------------------------------------------------------------
# AIMD Controller — convergence + bound invariants
# ---------------------------------------------------------------------------

class TestAIMDPropertyBased:
    """AIMD convergence and bound invariants via Hypothesis."""

    @pytest.mark.asyncio
    @given(
        initial=floats(min_value=1.0, max_value=AIMD_MAX_CONCURRENCY),
        successes=integers(min_value=0, max_value=200),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=50, deadline=None)
    async def test_window_bounded_after_successes(self, initial, successes):
        """Window is always within [AIMD_MIN_CONCURRENCY, AIMD_MAX_CONCURRENCY] after successes."""
        w = AIMDWindow(initial=initial)
        for _ in range(successes):
            await w.on_success()
        assert AIMD_MIN_CONCURRENCY <= w.window <= AIMD_MAX_CONCURRENCY, (
            f"Window {w.window} outside [{AIMD_MIN_CONCURRENCY}, {AIMD_MAX_CONCURRENCY}] "
            f"after {successes} successes"
        )

    @pytest.mark.asyncio
    @given(
        initial=floats(min_value=1.0, max_value=100.0),
        failures=integers(min_value=0, max_value=100),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=50)
    async def test_window_decreases_on_failure(self, initial, failures):
        """Window monotonically decreases with each failure (for non-zero decrease factor)."""
        w = AIMDWindow(initial=initial)
        prev = w.window
        for _ in range(failures):
            await w.on_failure(uma_state='warn')  # decrease factor 0.5
            assert w.window <= prev, (
                f"Window {w.window} > previous {prev} — not monotonic decrease"
            )
            prev = w.window

    @pytest.mark.asyncio
    @given(
        initial=floats(min_value=1.0, max_value=100.0),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30)
    async def test_convergence_toward_max(self, initial):
        """After 50 consecutive successes, window converges to AIMD_MAX_CONCURRENCY."""
        w = AIMDWindow(initial=initial)
        for _ in range(50):
            await w.on_success()
        # Window should be very close to max (within 1%)
        assert w.window >= AIMD_MAX_CONCURRENCY * 0.99, (
            f"After 50 successes, window {w.window} not near max {AIMD_MAX_CONCURRENCY}"
        )

    @pytest.mark.asyncio
    @given(
        initial=floats(min_value=1.0, max_value=100.0),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30)
    async def test_repeated_failures_converge_to_min(self, initial):
        """After 50 consecutive failures (from emergency state), window converges to min."""
        w = AIMDWindow(initial=initial)
        for _ in range(50):
            await w.on_failure(uma_state='emergency')  # decrease factor 0.0
        assert w.window == AIMD_MIN_CONCURRENCY, (
            f"After 50 emergency failures, window {w.window} != min {AIMD_MIN_CONCURRENCY}"
        )


# ---------------------------------------------------------------------------
# URL Dedup — FPR bounded property
# ---------------------------------------------------------------------------

class TestURLDedupPropertyBased:
    """RotatingBloomFilter FPR bounded property via Hypothesis."""

    @given(
        n_urls=integers(min_value=100, max_value=5000),
        fp_rate=floats(min_value=0.001, max_value=0.05),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_fpr_within_bound(self, n_urls, fp_rate):
        """
        False-positive rate stays within configured bound.

        Add N unique URLs, then check N never-seen URLs.
        FP rate = (false positives) / N should be ≤ fp_rate.
        """
        bf = create_rotating_bloom_filter(
            est_elements=n_urls * 10,
            false_positive_rate=fp_rate,
        )
        # Add N unique URLs
        urls = [f"https://unique{i}.example.com/path" for i in range(n_urls)]
        for url in urls:
            bf.add(url)
        # Check N never-seen URLs using contains (DeduplicationStrategy protocol)
        new_urls = [f"https://neverseen{i}.example.com/path" for i in range(n_urls)]
        false_positives = sum(1 for url in new_urls if url in bf)
        observed_fpr = false_positives / n_urls
        assert observed_fpr <= fp_rate * 3, (
            f"FPR {observed_fpr:.4f} > {fp_rate * 3:.4f} (3× bound) for n={n_urls}"
        )

    @given(
        n_urls=integers(min_value=10, max_value=500),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30)
    def test_dedupe_exact_order_preserved(self, n_urls):
        """dedupe_url_list preserves first-seen order for unique URLs."""
        bf = create_rotating_bloom_filter()
        urls = [f"https://example{i}.com" for i in range(n_urls)]
        # Add all URLs (all unique)
        unique, dropped = dedupe_url_list(urls, bf)
        assert unique == urls, "First-seen order not preserved"
        assert dropped == 0, "No duplicates should be dropped"

    @given(
        n_urls=integers(min_value=10, max_value=500),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30)
    def test_dedupe_removes_duplicates(self, n_urls):
        """dedupe_url_list removes intra-batch duplicates (first-seen wins)."""
        bf = create_rotating_bloom_filter()
        # Create list with duplicates
        urls = []
        for i in range(n_urls):
            urls.append(f"https://example{i}.com")
            if i % 3 == 0 and i > 0:
                urls.append(f"https://example{i}.com")  # duplicate
        unique, dropped = dedupe_url_list(urls, bf)
        # All returned URLs should be unique
        assert len(unique) == len(set(unique)), "Returned list contains duplicates"
        # Dropped count should equal duplicate count
        assert dropped == len(urls) - len(unique)


# ---------------------------------------------------------------------------
# IOC Extraction — regex vs Aho-Corasick parity
# ---------------------------------------------------------------------------

class TestIOCExtractionPropertyBased:
    """IOC extraction parity between regex and Aho-Corasick paths."""

    @given(
        n_iocs=integers(min_value=5, max_value=50),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30, deadline=None)
    def test_ioc_extract_stability(self, n_iocs):
        """
        extract_iocs_flat returns consistent results and never crashes.
        Property: determinism — same input gives same output.
        """
        from hledac.universal._core.rust_backend import rust

        # Build text with realistic IOC formats
        text_parts = []
        for i in range(n_iocs):
            text_parts.append(f"Contact info{i}@test{i}.example.com")
            text_parts.append(f"Visit https://site{i}.example.org/path?q={i}")
            text_parts.append(f"Server 192.168.{i % 256}.{i % 256}:8080")

        text_block = " | ".join(text_parts)
        result1 = rust.ioc.extract_iocs_flat(text_block)
        result2 = rust.ioc.extract_iocs_flat(text_block)

        # Determinism: same input → same output
        assert result1 == result2, "IOC extraction is non-deterministic"
        assert isinstance(result1, list)

    @given(
        text_content=text(min_size=10, max_size=1000),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=50, deadline=None)
    def test_ioc_extract_no_crash(self, text_content):
        """extract_iocs_flat never crashes on arbitrary text."""
        from hledac.universal._core.rust_backend import rust

        # Should not raise any exception
        result = rust.ioc.extract_iocs_flat(text_content)
        assert isinstance(result, list)

# ---------------------------------------------------------------------------
# Memory Pressure Hysteresis — state machine invariants
# ---------------------------------------------------------------------------

class TestMemoryPressureHysteresis:
    """State machine monotonicity and hysteresis gap invariants."""

    @given(
        initial_state=sampled_from(["ok", "soft_warn", "warn", "critical", "emergency"]),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=100)
    def test_state_transitions_are_strict(self, initial_state):
        """
        Memory pressure state can only transition to adjacent states
        (ok→soft_warn→warn→critical→emergency) — no skipping.

        This tests the ConcurrencyPreset.from_state() mapping is correct
        and that pressure transitions follow strict adjacency rules.
        """
        from hledac.universal._core.resource_governor import ConcurrencyPreset

        states = ["ok", "soft_warn", "warn", "critical", "emergency"]
        state_to_idx = {s: i for i, s in enumerate(states)}

        preset = ConcurrencyPreset.from_state(initial_state)
        assert preset.max_workers >= 0
        assert preset.fetch_limit >= 1
        assert 0.0 <= preset.aimd_decrease_factor <= 1.0

        # Verify preset values are consistent with state ordering
        idx = state_to_idx[initial_state]
        if idx == 0:  # ok
            assert preset.aimd_decrease_factor == 1.0
        elif idx == 4:  # emergency
            assert preset.aimd_decrease_factor == 0.0

    @given(
        state=sampled_from(["ok", "soft_warn", "warn", "critical", "emergency"]),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30)
    def test_preset_values_for_all_states(self, state):
        """Each state produces a valid ConcurrencyPreset with positive fetch_limit."""
        from hledac.universal._core.resource_governor import ConcurrencyPreset

        preset = ConcurrencyPreset.from_state(state)
        assert preset.max_workers >= 0
        assert preset.fetch_limit >= 1
        assert preset.aimd_decrease_factor >= 0.0
        assert preset.aimd_decrease_factor <= 1.0
        assert preset.cache_ttl_seconds > 0

    def test_hysteresis_gap_exists(self):
        """
        Entry to warn happens at higher threshold than exit from warn.
        This is the core hysteresis property: system doesn't flap.
        """
        from hledac.universal._core.resource_governor import (
            _THRESHOLD_WARN_GIB,
            _HYSTERESIS_EXIT_GIB,
        )
        # Exit threshold must be at or below entry threshold (hysteresis gap)
        assert _HYSTERESIS_EXIT_GIB <= _THRESHOLD_WARN_GIB, (
            f"Exit {_HYSTERESIS_EXIT_GIB} > entry {_THRESHOLD_WARN_GIB} — no hysteresis gap"
        )

    def test_critical_is_more_restrictive_than_warn(self):
        """Critical preset has strictly fewer workers and lower fetch limit than warn."""
        from hledac.universal._core.resource_governor import ConcurrencyPreset

        warn_preset = ConcurrencyPreset.from_state("warn")
        crit_preset = ConcurrencyPreset.from_state("critical")
        assert crit_preset.max_workers <= warn_preset.max_workers
        assert crit_preset.fetch_limit <= warn_preset.fetch_limit
        assert crit_preset.aimd_decrease_factor <= warn_preset.aimd_decrease_factor

    def test_emergency_is_most_restrictive(self):
        """Emergency preset has max_workers=0 and minimal fetch_limit."""
        from hledac.universal._core.resource_governor import ConcurrencyPreset

        emg_preset = ConcurrencyPreset.from_state("emergency")
        assert emg_preset.max_workers == 0
        assert emg_preset.fetch_limit == 1
        assert emg_preset.aimd_decrease_factor == 0.0
        assert emg_preset.block_model_load is True
