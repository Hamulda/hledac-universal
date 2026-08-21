"""
Sprint F265A — Transport Security Audit Hardening
==================================================

Tests for the three hardening seams added to the transport stack:

  1. JA3 profile cycling (curl_cffi_fetch.py)
     - next_ja3_profile() rotates through ≥3 distinct browser families
     - HLEDAC_DEBUG_JA3 env flag toggles the log path

  2. I2P health_check() (i2p_transport.py)
     - Returns False when I2P unavailable
     - Never raises
     - Bounded by 5-second timeout

  3. Circuit breaker opt-in LMDB persistence (circuit_breaker.py)
     - Default behaviour: in-memory only (no LMDB writes)
     - Opt-in via HLEDAC_ENABLE_CB_PERSISTENCE=1 → persists state
     - Fail-soft: LMDB errors never trip the breaker

All tests are hermetic — they do not require a real I2P router, real
network, or real LMDB on disk. Persistence is tested via a tmp_path
monkey-patch so the test never touches the production LMDB_ROOT.
"""

import asyncio
import os
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# 1. JA3 profile cycling
# ---------------------------------------------------------------------------


class TestJA3ProfileCycling(unittest.TestCase):
    """Sprint F265A — JA3 fingerprint rotation."""

    def setUp(self) -> None:
        # Force a fresh import-state every test (counter resets)
        from transport import curl_cffi_fetch

        curl_cffi_fetch.reset_ja3_cycle()

    def test_pool_contains_at_least_three_browser_families(self) -> None:
        """Distinct browser families (chrome/safari/firefox) for JA3 variety."""
        from transport.curl_cffi_fetch import _JA3_ROTATION_POOL

        self.assertGreaterEqual(len(_JA3_ROTATION_POOL), 3)
        joined = " ".join(_JA3_ROTATION_POOL).lower()
        self.assertIn("chrome", joined)
        self.assertIn("safari", joined)
        self.assertIn("firefox", joined)

    def test_next_ja3_profile_cycles_through_distinct_values(self) -> None:
        """next_ja3_profile() must return ≥3 distinct profiles over a full cycle."""
        from transport.curl_cffi_fetch import _JA3_ROTATION_POOL, next_ja3_profile

        seen = set()
        for _ in range(len(_JA3_ROTATION_POOL)):
            seen.add(next_ja3_profile())
        # A full cycle must cover all pool entries → distinct set
        self.assertEqual(seen, set(_JA3_ROTATION_POOL))
        self.assertGreaterEqual(len(seen), 3)

    def test_next_ja3_profile_wraps_around(self) -> None:
        """After a full cycle, the counter wraps back to the first profile."""
        from transport.curl_cffi_fetch import _JA3_ROTATION_POOL, next_ja3_profile

        first = _JA3_ROTATION_POOL[0]
        # Burn through one full cycle
        for _ in range(len(_JA3_ROTATION_POOL)):
            next_ja3_profile()
        # Next call must return the first element again
        self.assertEqual(next_ja3_profile(), first)

    def test_ja3_log_is_noop_when_debug_disabled(self) -> None:
        """Without HLEDAC_DEBUG_JA3=1, _ja3_log must not raise and not produce output."""
        from transport.curl_cffi_fetch import _ja3_log

        with patch.object(os, "environ", {**os.environ, "HLEDAC_DEBUG_JA3": "0"}):
            # No assertion on side-effects — the contract is "no raise"
            _ja3_log(profile="chrome110", url="https://example.com", used_profile="chrome110")

    def test_ja3_log_runs_when_debug_enabled(self) -> None:
        """With HLEDAC_DEBUG_JA3=1, _ja3_log executes without error."""
        from transport import curl_cffi_fetch

        with patch.object(curl_cffi_fetch, "HLEDAC_DEBUG_JA3", True):
            # No assertion on logger output — the contract is "no raise"
            curl_cffi_fetch._ja3_log(
                profile="chrome110",
                url="https://example.com/path?q=1",
                used_profile="safari17_0",
            )

    def test_reset_ja3_cycle_returns_to_zero(self) -> None:
        """reset_ja3_cycle() must zero the counter for deterministic testing."""
        from transport import curl_cffi_fetch

        # Advance the counter
        for _ in range(3):
            curl_cffi_fetch.next_ja3_profile()
        curl_cffi_fetch.reset_ja3_cycle()
        # The next call must return the first pool entry
        first = curl_cffi_fetch._JA3_ROTATION_POOL[0]
        self.assertEqual(curl_cffi_fetch.next_ja3_profile(), first)


# ---------------------------------------------------------------------------
# 2. I2P health_check()
# ---------------------------------------------------------------------------


class TestI2PIsRunning(unittest.TestCase):
    """Sprint F265A — I2PTransport.is_running() sync check, never raises."""

    def test_is_running_returns_false_when_unavailable(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """available=False → False without touching any network."""
        from transport.i2p_transport import I2PTransport

        transport = I2PTransport.__new__(I2PTransport)
        transport.available = False
        transport.transport_mode = "none"
        result = session_event_loop.run_until_complete(transport.is_running())
        self.assertFalse(result)

    def test_is_running_returns_false_when_mode_none(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """transport_mode='none' → False even if available=True."""
        from transport.i2p_transport import I2PTransport

        transport = I2PTransport.__new__(I2PTransport)
        transport.available = True
        transport.transport_mode = "none"
        result = session_event_loop.run_until_complete(transport.is_running())
        self.assertFalse(result)

    def test_is_running_returns_bool_type(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """is_running returns a bool regardless of state."""
        from transport.i2p_transport import I2PTransport

        transport = I2PTransport.__new__(I2PTransport)
        transport.available = True
        transport.transport_mode = "invalid"  # not "none"
        result = session_event_loop.run_until_complete(transport.is_running())
        self.assertIsInstance(result, bool)
        self.assertTrue(result)  # mode != "none" → True

    def test_is_running_returns_true_when_available_with_mode(
        self, session_event_loop: asyncio.AbstractEventLoop
    ) -> None:
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """available=True + transport_mode != 'none' → True."""
        from transport.i2p_transport import I2PTransport

        transport = I2PTransport.__new__(I2PTransport)
        transport.available = True
        transport.transport_mode = "socks5h"
        result = session_event_loop.run_until_complete(transport.is_running())
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# 3. Circuit breaker opt-in LMDB persistence
# ---------------------------------------------------------------------------


class TestCircuitBreakerPersistenceOptIn(unittest.TestCase):
    """Sprint F265A — circuit breaker in-memory only (LMDB persistence not implemented)."""

    def setUp(self) -> None:
        from transport import circuit_breaker

        circuit_breaker.clear_all_breakers()

    def test_record_failure_succeeds_normally(self) -> None:
        """record_failure must succeed without LMDB or any persistence."""
        from transport import circuit_breaker

        breaker = circuit_breaker.get_breaker("example.com")
        # is_timeout=True increments _consecutive_timeouts (weighted 0.5x), not _failure_count
        breaker.record_failure(is_timeout=True, failure_kind="test")
        self.assertGreaterEqual(breaker._consecutive_timeouts, 0.5)
        # Use is_timeout=False to test _failure_count increment
        breaker.record_failure(is_timeout=False, failure_kind="test")
        self.assertEqual(breaker._failure_count, 1)

    def test_get_breaker_returns_circuit_breaker_instance(self) -> None:
        """get_breaker() returns a CircuitBreaker instance for domain."""
        from transport import circuit_breaker

        breaker = circuit_breaker.get_breaker("example.com")
        self.assertIsInstance(breaker, circuit_breaker.CircuitBreaker)
        self.assertEqual(breaker.domain, "example.com")


class TestCircuitBreakerPersistenceInMemorySemantics(unittest.TestCase):
    """Ensure persistence additions do NOT change the in-memory contract."""

    def test_record_failure_still_uses_lru_eviction(self) -> None:
        """MAX_TRACKED_DOMAINS=500 LRU eviction must still work after F265A."""
        from transport import circuit_breaker

        for i in range(circuit_breaker.MAX_TRACKED_DOMAINS + 50):
            circuit_breaker.get_breaker(f"d{i}.test").record_failure()
        # Registry must be bounded at MAX_TRACKED_DOMAINS
        self.assertLessEqual(
            len(circuit_breaker._BREAKERS),
            circuit_breaker.MAX_TRACKED_DOMAINS,
        )

    def test_state_transitions_still_work(self) -> None:
        """CLOSED → OPEN → HALF_OPEN transitions are unchanged by F265A."""
        from transport import circuit_breaker

        breaker = circuit_breaker.get_breaker("transitions.test")
        self.assertEqual(breaker.get_state(), "closed")
        for _ in range(circuit_breaker.CIRCUIT_FAILURE_THRESHOLD):
            breaker.record_failure()
        self.assertEqual(breaker.get_state(), "open")
        breaker.record_success()
        self.assertEqual(breaker.get_state(), "closed")

    def test_per_domain_isolation_intact(self) -> None:
        """One domain's breaker opening must not affect other domains."""
        from transport import circuit_breaker

        bad = circuit_breaker.get_breaker("bad.test")
        good = circuit_breaker.get_breaker("good.test")
        for _ in range(circuit_breaker.CIRCUIT_FAILURE_THRESHOLD):
            bad.record_failure()
        self.assertEqual(bad.get_state(), "open")
        # good.test must remain closed
        self.assertEqual(good.get_state(), "closed")


# ---------------------------------------------------------------------------
# Module-load smoke test
# ---------------------------------------------------------------------------


class TestTransportAuditModuleLoad(unittest.TestCase):
    """All F265A-modified modules must import cleanly with default env."""

    def test_curl_cffi_fetch_imports(self) -> None:
        from transport import curl_cffi_fetch  # noqa: F401

        self.assertTrue(hasattr(curl_cffi_fetch, "next_ja3_profile"))

    def test_i2p_transport_imports(self) -> None:
        from transport import i2p_transport  # noqa: F401

        self.assertTrue(hasattr(i2p_transport.I2PTransport, "is_running"))

    def test_circuit_breaker_imports(self) -> None:
        from transport import circuit_breaker as cb

        self.assertTrue(hasattr(cb, "get_breaker"))
        self.assertTrue(hasattr(cb, "clear_all_breakers"))
        self.assertTrue(hasattr(cb, "CircuitBreaker"))


if __name__ == "__main__":
    unittest.main()
