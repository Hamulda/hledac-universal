"""
Circuit breaker metrics integration tests.

Verifies:
1. 3 failures → OPEN state + circuit_breaker_open_count incremented
2. Recovery timeout → HALF_OPEN state + circuit_breaker_half_open_count incremented
3. Success in HALF_OPEN → CLOSED state + circuit_breaker_recovery_success incremented
"""
import time

from transport.circuit_breaker import (
    CIRCUIT_FAILURE_THRESHOLD,
    _metrics_safe_increment,
    clear_all_breakers,
    get_breaker,
    per_domain_stats,
)


class TestCircuitBreakerMetrics:
    """Test circuit breaker state transitions and metrics wiring."""

    def setup_method(self):
        """Reset global breaker state before each test."""
        clear_all_breakers()

    def test_failure_threshold_opens_circuit_and_increments_metric(self):
        """3 consecutive failures → OPEN, circuit_breaker_open_count incremented."""
        domain = "test-metrics-failure.example.com"
        cb = get_breaker(domain)

        incremental_calls = []
        original = _metrics_safe_increment
        def track(name):
            incremental_calls.append(name)
        import transport.circuit_breaker as cb_mod
        cb_mod._metrics_safe_increment = track

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")

        cb_mod._metrics_safe_increment = original

        assert cb.get_state() == "open"
        assert "circuit_breaker_state_transitions" in incremental_calls
        assert "circuit_breaker_open_count" in incremental_calls

    def test_half_open_transition_after_recovery_timeout(self):
        """Recovery timeout expires → HALF_OPEN, circuit_breaker_half_open_count incremented."""
        domain = "test-metrics-recovery.example.com"
        cb = get_breaker(domain)

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        assert cb.get_state() == "open"

        incremental_calls = []
        original = _metrics_safe_increment
        def track(name):
            incremental_calls.append(name)
        import transport.circuit_breaker as cb_mod
        cb_mod._metrics_safe_increment = track

        cb.recovery_timeout = 0.05  # 50ms
        time.sleep(0.06)
        decision = cb.check_circuit()

        cb_mod._metrics_safe_increment = original

        assert decision.state == "half_open"
        assert "circuit_breaker_state_transitions" in incremental_calls
        assert "circuit_breaker_half_open_count" in incremental_calls

    def test_success_in_half_open_closes_circuit_and_recovery_success_metric(self):
        """Success in HALF_OPEN → CLOSED, circuit_breaker_recovery_success incremented."""
        domain = "test-metrics-success.example.com"
        cb = get_breaker(domain)

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        cb.recovery_timeout = 0.05
        time.sleep(0.06)
        cb.check_circuit()
        assert cb.get_state() == "half_open"

        incremental_calls = []
        original = _metrics_safe_increment
        def track(name):
            incremental_calls.append(name)
        import transport.circuit_breaker as cb_mod
        cb_mod._metrics_safe_increment = track

        cb.record_success()

        cb_mod._metrics_safe_increment = original

        assert cb.get_state() == "closed"
        assert "circuit_breaker_state_transitions" in incremental_calls
        assert "circuit_breaker_recovery_success" in incremental_calls

    def test_half_open_max_probes_returns_to_open(self):
        """In HALF_OPEN, probe failure → returns to OPEN."""
        domain = "test-metrics-probe-fail.example.com"
        cb = get_breaker(domain)

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        cb.recovery_timeout = 0.05
        time.sleep(0.06)
        cb.check_circuit()
        assert cb.get_state() == "half_open"

        incremental_calls = []
        original = _metrics_safe_increment
        def track(name):
            incremental_calls.append(name)
        import transport.circuit_breaker as cb_mod
        cb_mod._metrics_safe_increment = track

        cb.record_failure(is_timeout=False, failure_kind="probe_failure")

        cb_mod._metrics_safe_increment = original

        assert cb.get_state() == "open"
        assert "circuit_breaker_open_count" in incremental_calls

    def test_per_domain_stats_returns_dict(self):
        """per_domain_stats() returns well-formed dict for debug dashboard."""
        clear_all_breakers()
        domain = "test-per-domain-stats.example.com"
        cb = get_breaker(domain)
        cb.record_failure(is_timeout=False, failure_kind="test")

        stats = per_domain_stats()
        assert domain in stats
        entry = stats[domain]
        assert "state" in entry
        assert "failure_count" in entry
        assert "last_failure_time" in entry
        assert "opened_at_monotonic" in entry
        assert "last_failure_kind" in entry
        assert "recovery_timeout_s" in entry

    def test_metrics_fire_and_forget_never_blocks_circuit_logic(self):
        """Metric increment failure must not affect CB state or logic."""
        domain = "test-fire-and-forget.example.com"
        cb = get_breaker(domain)

        import transport.circuit_breaker as cb_mod
        original = cb_mod._metrics_safe_increment

        def fail_incr(_n: str) -> None:
            raise RuntimeError("metrics unavailable")
        cb_mod._metrics_safe_increment = fail_incr

        cb.record_failure(is_timeout=False, failure_kind="test")
        cb.record_failure(is_timeout=False, failure_kind="test")
        cb.record_failure(is_timeout=False, failure_kind="test")

        cb_mod._metrics_safe_increment = original
        assert cb.get_state() == "open"

class TestCircuitBreakerFSMTransitions:
    """FSM state transition tests: CLOSED→OPEN→HALF_OPEN→CLOSED with transition counter."""

    def setup_method(self):
        clear_all_breakers()
        self._recorded_transitions: list[str] = []

        import transport.circuit_breaker as cb_mod
        self._orig_incr = cb_mod._metrics_safe_increment

        def track(name: str) -> None:
            if name == "circuit_breaker_state_transitions":
                self._recorded_transitions.append(name)
            self._orig_incr(name)

        cb_mod._metrics_safe_increment = track

    def teardown_method(self):
        import transport.circuit_breaker as cb_mod
        cb_mod._metrics_safe_increment = self._orig_incr
        clear_all_breakers()

    def test_n_consecutive_failures_opens_breaker(self):
        """N consecutive failures → breaker enters OPEN state."""
        domain = "test-fsm-failures.example.com"
        cb = get_breaker(domain)
        assert cb.get_state() == "closed"

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")

        assert cb.get_state() == "open"
        # One transition: CLOSED → OPEN
        count = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert count == 1

    def test_cooldown_expires_goes_to_half_open(self):
        """After recovery_timeout expires → breaker enters HALF_OPEN state."""
        domain = "test-fsm-cooldown.example.com"
        cb = get_breaker(domain)
        cb.recovery_timeout = 0.05  # 50ms

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        assert cb.get_state() == "open"

        self._recorded_transitions.clear()  # Reset after OPEN transition
        time.sleep(0.06)  # Wait for recovery timeout

        cb.check_circuit()
        assert cb.get_state() == "half_open"
        # Second transition: OPEN → HALF_OPEN
        count = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert count == 1

    def test_success_in_half_open_closes_breaker(self):
        """Success in HALF_OPEN → breaker returns to CLOSED state."""
        domain = "test-fsm-success.example.com"
        cb = get_breaker(domain)
        cb.recovery_timeout = 0.05

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        assert cb.get_state() == "open"

        time.sleep(0.06)
        cb.check_circuit()
        assert cb.get_state() == "half_open"

        self._recorded_transitions.clear()
        cb.record_success()
        assert cb.get_state() == "closed"
        # Third transition: HALF_OPEN → CLOSED
        count = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert count == 1

    def test_full_cycle_transition_counter_total(self):
        """Complete CLOSED→OPEN→HALF_OPEN→CLOSED cycle yields 3 transition events."""
        domain = "test-fsm-full-cycle.example.com"
        cb = get_breaker(domain)
        cb.recovery_timeout = 0.05

        # 1. Trigger failures → OPEN (transition 1)
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        assert cb.get_state() == "open"

        # 2. Wait + check → HALF_OPEN (transition 2)
        time.sleep(0.06)
        cb.check_circuit()
        assert cb.get_state() == "half_open"

        # 3. Success → CLOSED (transition 3)
        cb.record_success()
        assert cb.get_state() == "closed"

        total = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert total == 3

    def test_success_in_open_transitions_to_closed(self):
        """Success while OPEN immediately transitions to CLOSED (standard CB behavior)."""
        domain = "test-fsm-open-noop.example.com"
        cb = get_breaker(domain)

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        assert cb.get_state() == "open"

        self._recorded_transitions.clear()
        cb.record_success()
        # record_success() unconditionally sets CLOSED (no transition metric for OPEN→CLOSED)
        assert cb.get_state() == "closed"
        count = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert count == 0  # Transition metric only fires for HALF_OPEN→CLOSED

    def test_failure_in_half_open_immediately_opens(self):
        """Failure in HALF_OPEN → immediate OPEN (no HALF_OPEN→CLOSED first)."""
        domain = "test-fsm-halfopen-fail.example.com"
        cb = get_breaker(domain)
        cb.recovery_timeout = 0.05

        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(is_timeout=False, failure_kind="test_failure")
        time.sleep(0.06)
        cb.check_circuit()
        assert cb.get_state() == "half_open"

        self._recorded_transitions.clear()
        cb.record_failure(is_timeout=False, failure_kind="half_open_failure")
        assert cb.get_state() == "open"
        # CLOSED→OPEN(1) + OPEN→HALF_OPEN(2) + HALF_OPEN→OPEN(3)
        total = sum(1 for t in self._recorded_transitions if t == "circuit_breaker_state_transitions")
        assert total == 1

    # === Warmup vs Production Separation Tests ===

    def test_warmup_failures_do_not_trip_production_circuit(self):
        """3 warmup failures → circuit stays CLOSED (separate tracking)."""
        domain = "warmup-test.example.com"
        cb = get_breaker(domain)

        # Record 3 warmup failures (threshold is 3 for production)
        for _ in range(3):
            cb.record_failure(is_timeout=True, failure_kind="warmup_timeout", is_warmup=True)

        # Circuit should still be CLOSED — warmup failures don't affect production
        assert cb.get_state() == "closed"
        # But warmup counter should be incremented
        assert cb._warmup_failure_count == 3

    def test_production_failures_after_warmup_trip_normally(self):
        """Warmup failures + production failures → trips at correct threshold."""
        domain = "mixed-test.example.com"
        cb = get_breaker(domain)

        # Record 2 warmup failures
        cb.record_failure(is_warmup=True, failure_kind="warmup_err_1")
        cb.record_failure(is_warmup=True, failure_kind="warmup_err_2")
        assert cb._warmup_failure_count == 2
        assert cb._failure_count == 0
        assert cb.get_state() == "closed"

        # Record 3 production failures (threshold = 3)
        cb.record_failure(is_timeout=False, failure_kind="prod_err_1")
        cb.record_failure(is_timeout=False, failure_kind="prod_err_2")
        cb.record_failure(is_timeout=False, failure_kind="prod_err_3")

        # Now circuit should be OPEN
        assert cb.get_state() == "open"
        assert cb._failure_count == 3
        assert cb._warmup_failure_count == 2  # warmup unchanged

    def test_mark_warmup_done_resets_warmup_counter(self):
        """mark_warmup_done() resets warmup_failure_count to 0."""
        domain = "mark-done-test.example.com"
        cb = get_breaker(domain)

        # Accumulate some warmup failures
        cb.record_failure(is_warmup=True, failure_kind="err_1")
        cb.record_failure(is_warmup=True, failure_kind="err_2")
        assert cb._warmup_failure_count == 2

        # Mark warmup done
        cb.mark_warmup_done()

        # Warmup counter reset, production counter unchanged
        assert cb._warmup_failure_count == 0
        assert cb._failure_count == 0

    def test_warmup_failure_recorded_in_snapshot(self):
        """Snapshot includes warmup_failure_count."""
        domain = "snapshot-test.example.com"
        cb = get_breaker(domain)

        cb.record_failure(is_warmup=True, failure_kind="warmup_probe_fail")
        cb.record_failure(is_warmup=True, failure_kind="warmup_timeout")

        snapshot = cb.get_snapshot()
        assert snapshot.warmup_failure_count == 2
        assert snapshot.failure_count == 0  # production unaffected

    def test_warmup_failure_kind_logged(self):
        """Warmup failures set last_failure_kind to warmup_* prefix."""
        domain = "kind-log-test.example.com"
        cb = get_breaker(domain)

        cb.record_failure(is_warmup=True, failure_kind="probe_failed")
        assert cb._last_failure_kind == "probe_failed"

        cb.record_failure(is_warmup=True, failure_kind="")
        assert cb._last_failure_kind == "warmup_error"

        cb.record_failure(is_warmup=True, is_timeout=True, failure_kind="")
        assert cb._last_failure_kind == "warmup_timeout"

    def test_warmup_and_production_counters_independent(self):
        """Warmup and production failure counters are fully independent."""
        domain = "independent-test.example.com"
        cb = get_breaker(domain)

        # Interleave warmup and production failures
        cb.record_failure(is_warmup=True, failure_kind="wu1")
        cb.record_failure(is_warmup=False, failure_kind="prod1")
        cb.record_failure(is_warmup=True, failure_kind="wu2")
        cb.record_failure(is_warmup=False, failure_kind="prod2")

        assert cb._warmup_failure_count == 2
        assert cb._failure_count == 2
        assert cb.get_state() == "closed"  # 2 < 3 threshold

        # One more production failure trips the circuit
        cb.record_failure(is_warmup=False, failure_kind="prod3")
        assert cb.get_state() == "open"

        # Warmup counter still intact (not reset by trip)
        assert cb._warmup_failure_count == 2

    def test_per_domain_stats_includes_warmup_count(self):
        """per_domain_stats() includes warmup_failure_count."""
        domain = "stats-warmup.example.com"
        cb = get_breaker(domain)

        cb.record_failure(is_warmup=True, failure_kind="wu_fail")
        cb.record_failure(is_warmup=True, failure_kind="wu_timeout")

        stats = per_domain_stats()
        assert domain in stats
        assert stats[domain]["warmup_failure_count"] == 2
        assert stats[domain]["failure_count"] == 0


class TestModelCircuitBreakerReset:
    """Tests for ModelCircuitBreaker.reset() — GAP-3/1 fix."""

    def test_reset_closes_open_breaker(self):
        """After 3 failures (OPEN), reset() → is_open() == False."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-model")
        for _ in range(3):
            breaker.record_failure(kind="test_oom")
        assert breaker.is_open() is True

        breaker.reset()
        assert breaker.is_open() is False

    def test_reset_clears_failure_count(self):
        """reset() zeroes _failure_count."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-count")
        breaker.record_failure(kind="fail1")
        breaker.record_failure(kind="fail2")
        assert breaker._failure_count == 2

        breaker.reset()
        assert breaker._failure_count == 0

    def test_reset_clears_last_failure_time(self):
        """reset() zeroes _last_failure_time."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-time")
        breaker.record_failure(kind="metal_crash")
        assert breaker._last_failure_time > 0

        breaker.reset()
        assert breaker._last_failure_time == 0.0

    def test_reset_clears_last_failure_kind(self):
        """reset() empties _last_failure_kind."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-kind")
        breaker.record_failure(kind="gpu_timeout")
        assert breaker._last_failure_kind == "gpu_timeout"

        breaker.reset()
        assert breaker._last_failure_kind == ""

    def test_reset_idempotent(self):
        """Calling reset() twice on already-closed breaker is safe."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-idempotent")
        breaker.reset()
        breaker.reset()
        assert breaker.is_open() is False
        assert breaker._failure_count == 0

    def test_reset_thread_safe_no_attribute_error(self):
        """reset() is callable without AttributeError — the original GAP-3/1 bug."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-no-error")
        # record some failures first
        breaker.record_failure(kind="oom1")
        breaker.record_failure(kind="oom2")
        breaker.record_failure(kind="oom3")
        assert breaker.is_open() is True

        # reset() must not raise AttributeError — was the original bug
        breaker.reset()
        assert breaker.is_open() is False

    def test_reset_all_fields_cleared(self):
        """After reset(), all failure-tracking fields are cleared."""
        from transport.circuit_breaker import ModelCircuitBreaker

        breaker = ModelCircuitBreaker(model_id="test-reset-all-fields")
        breaker.record_failure(kind="metal_crash")
        assert breaker._failure_count == 1
        assert breaker._last_failure_kind == "metal_crash"
        assert breaker._last_failure_time > 0

        breaker.reset()
        assert breaker._failure_count == 0
        assert breaker._last_failure_kind == ""
        assert breaker._last_failure_time == 0.0
        assert breaker.is_open() is False
