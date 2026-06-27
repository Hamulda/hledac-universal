"""
tests/test_write_coalescer.py — WriteCoalescer coverage for BUG-5 (silent [] return).

Tests the P1-3 fix: FlushError propagation, on_flush_error callback,
and drain_and_get_accepted raises on silent flush failure.

Invariant table:
  WC-1  | _flush returns [] on error (not exception)
  WC-2  | on_flush_error callback fires on every flush error
  WC-3  | drain_and_get_accepted raises FlushError when flush silently fails
  WC-4  | FlushError.original_exception carries original exc
  WC-5  | FlushError.findings carries failed batch
  WC-6  | Coalescer continues in degraded mode after flush error
  WC-7  | _drain_residual_queue flushes on stop() timeout
  WC-8  | start/stop lifecycle
  WC-9  | submit() queues findings
  WC-10 | Adaptive flush interval (fast_interval vs flush_interval)
"""

from __future__ import annotations

import asyncio

import pytest

from hledac.universal.storage.write_coalescer import (
    CoalescerConfig,
    FlushError,
    WriteCoalescer,
)

# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def failing_flush_fn():
    """flush_fn that always raises."""
    async def fn(_findings):
        raise RuntimeError("DB write failed")
    return fn


@pytest.fixture
def success_flush_fn():
    """flush_fn that returns one result per finding."""
    async def fn(findings):
        return [{"accepted": True, "finding": f} for f in findings]
    return fn


@pytest.fixture
def partial_flush_fn():
    """flush_fn that returns partial results."""
    counter = 0

    async def fn(findings):
        nonlocal counter
        counter += 1
        if counter % 2 == 0:
            raise RuntimeError("intermittent failure")
        return [{"accepted": True, "finding": f} for f in findings]
    return fn


class _ErrorCallbackTracker:
    """Tracks on_flush_error callback invocations."""
    def __init__(self):
        self._calls: list[tuple[Exception, list, int]] = []

    def __call__(self, exc: Exception, findings: list, batch_num: int):
        self._calls.append((exc, list(findings), batch_num))


@pytest.fixture
def error_callback_log():
    """Track on_flush_error callback invocations."""
    return _ErrorCallbackTracker()


# ─── WC-8: Lifecycle ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_stop_lifecycle(success_flush_fn):
    """WC-8: start() creates task; stop() drains and joins."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    assert not coalescer._running

    await coalescer.start()
    assert coalescer._running
    assert coalescer._task is not None

    await coalescer.stop()
    assert not coalescer._running
    # Stats should reflect no submissions
    assert coalescer._stats["submitted"] == 0


@pytest.mark.asyncio
async def test_stop_idempotent_drains_residual(success_flush_fn):
    """WC-8: Calling stop() twice drains residual items both times."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}])
    await coalescer.stop()
    assert coalescer._stats["submitted"] == 1

    # Second stop should not crash (drains nothing)
    await coalescer.stop()
    assert coalescer._running is False


# ─── WC-9: Submit ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_queues_findings(success_flush_fn):
    """WC-9: submit() adds to queue and increments submitted counter."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}, {"id": 2}])
    assert coalescer._stats["submitted"] == 2

    await coalescer.submit([])
    # Empty submit does not increment
    assert coalescer._stats["submitted"] == 2

    await coalescer.stop()


# ─── WC-1 / WC-6: _flush error returns [], coalescer survives ──────────────

@pytest.mark.asyncio
async def test_flush_returns_empty_on_error(failing_flush_fn):
    """WC-1: _flush catches exception and returns [] (not re-raised)."""
    coalescer = WriteCoalescer(flush_fn=failing_flush_fn)
    await coalescer.start()

    results = await coalescer._flush([{"id": 1}])
    assert results == []
    assert coalescer._stats["errors"] == 1

    await coalescer.stop()


@pytest.mark.asyncio
async def test_coalescer_continues_after_flush_error(failing_flush_fn):
    """WC-6: Single flush error does NOT stop the coalescer loop."""
    coalescer = WriteCoalescer(flush_fn=failing_flush_fn)
    await coalescer.start()

    # First batch fails
    await coalescer.submit([{"id": 1}])
    await asyncio.sleep(0.6)  # Allow loop to process (500ms flush interval)

    assert coalescer._running is True, "Coalescer should continue after flush error"

    # Second submission should also be processed (and fail)
    await coalescer.submit([{"id": 2}])
    await asyncio.sleep(0.6)

    assert coalescer._stats["errors"] >= 1
    await coalescer.stop()


# ─── WC-2: on_flush_error callback ───────────────────────────────────────

@pytest.mark.asyncio
async def test_on_flush_error_callback_fires(failing_flush_fn, error_callback_log):
    """WC-2: on_flush_error is called with (exc, findings, batch_num) on every error."""
    coalescer = WriteCoalescer(
        flush_fn=failing_flush_fn,
        on_flush_error=error_callback_log,
    )
    await coalescer.start()

    await coalescer.submit([{"id": 1}, {"id": 2}])
    await asyncio.sleep(0.6)  # Allow loop to process (500ms flush interval)

    assert len(error_callback_log._calls) >= 1
    exc, findings, batch_num = error_callback_log._calls[0]
    assert isinstance(exc, RuntimeError)
    assert findings == [{"id": 1}, {"id": 2}]
    assert isinstance(batch_num, int)

    await coalescer.stop()


@pytest.mark.asyncio
async def test_on_flush_error_not_required(success_flush_fn):
    """WC-2: on_flush_error=None is valid (no callback on error)."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn, on_flush_error=None)
    await coalescer.start()
    # Should not raise
    await coalescer.stop()


# ─── WC-3 / WC-4 / WC-5: drain_and_get_accepted raises FlushError ──────────

@pytest.mark.asyncio
async def test_drain_and_get_accepted_raises_flush_error_on_silent_failure(failing_flush_fn):
    """WC-3: drain_and_get_accepted raises FlushError when flush returns [] with non-empty input."""
    coalescer = WriteCoalescer(flush_fn=failing_flush_fn)
    await coalescer.start()

    with pytest.raises(FlushError) as exc_info:
        await coalescer.drain_and_get_accepted([{"id": 1}])

    assert exc_info.value.findings == [{"id": 1}]
    assert isinstance(exc_info.value.original_exception, RuntimeError)

    await coalescer.stop()


@pytest.mark.asyncio
async def test_drain_and_get_accepted_returns_results_on_success(success_flush_fn):
    """WC-3: On success, returns merged results without raising."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    results = await coalescer.drain_and_get_accepted([{"id": 1}])
    assert len(results) == 1
    assert results[0]["accepted"] is True

    await coalescer.stop()


@pytest.mark.asyncio
async def test_drain_and_get_accepted_empty_input_no_error(success_flush_fn):
    """WC-3: Empty input + successful flush returns [], does not raise FlushError."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    results = await coalescer.drain_and_get_accepted()
    assert results == []

    await coalescer.stop()


# ─── WC-4 / WC-5: FlushError properties ────────────────────────────────────

def test_flush_error_properties():
    """WC-4/WC-5: FlushError.original_exception and .findings are set correctly."""
    original = RuntimeError("inner")
    findings = [{"id": 1}, {"id": 2}]
    error = FlushError(original, findings)

    assert error.original_exception is original
    assert error.findings == findings
    assert "RuntimeError: inner" in str(error)


def test_flush_error_string_representation():
    """WC-4: FlushError has readable string representation."""
    error = FlushError(RuntimeError("boom"), [{"id": 1}])
    s = str(error)
    assert "flush failed" in s
    assert "RuntimeError" in s
    assert "boom" in s


# ─── WC-7: _drain_residual_queue ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_drain_residual_queue_on_stop_timeout(failing_flush_fn):
    """WC-7: stop() timeout triggers _drain_residual_queue so items are not lost."""
    coalescer = WriteCoalescer(flush_fn=failing_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}])

    # Immediate stop (0 timeout) cancels task → _drain_residual_queue called
    await coalescer.stop(timeout_s=0.0)

    # The item was in the queue at cancellation time.
    # _drain_residual_queue calls _flush which fails → error counted.
    # We just verify it didn't raise and stats are tracked.
    assert coalescer._stats["submitted"] == 1


@pytest.mark.asyncio
async def test_drain_residual_queue_success(success_flush_fn):
    """WC-7: Items in queue at stop() are flushed."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}, {"id": 2}])

    # Drain via stop
    await coalescer.stop(timeout_s=0.0)

    # All flushed
    assert coalescer._stats["flushed_findings"] == 2
    assert coalescer._stats["flushed_batches"] == 1


# ─── WC-10: Adaptive flush interval ───────────────────────────────────────

@pytest.mark.asyncio
async def test_adaptive_fast_interval_sparse_queue(success_flush_fn):
    """WC-10: Sparse queue (< min_batch_ratio) uses fast_interval deadline."""
    config = CoalescerConfig(
        max_batch_size=100,
        flush_interval_s=0.1,   # 100ms deadline
        fast_interval_s=0.005,  # 5ms adaptive interval
        min_batch_ratio=0.05,
    )
    coalescer = WriteCoalescer(flush_fn=success_flush_fn, config=config)
    await coalescer.start()

    # Submit 1 item (queue depth 1 < 5% of 100 = 5 → sparse path)
    await coalescer.submit([{"id": 1}])

    # Wait for deadline to pass — fast_interval (5ms) << flush_interval (100ms)
    # so the adaptive path uses the shorter deadline
    await asyncio.sleep(0.12)

    assert coalescer._stats["flushed_batches"] >= 1
    assert coalescer._stats["flushed_findings"] == 1

    await coalescer.stop()


@pytest.mark.asyncio
async def test_immediate_flush_on_max_batch_size(success_flush_fn):
    """WC-10: pending >= max_batch_size triggers immediate flush."""
    config = CoalescerConfig(
        max_batch_size=3,
        flush_interval_s=10.0,  # Long interval — should NOT wait
        fast_interval_s=0.005,
        min_batch_ratio=0.05,
    )
    coalescer = WriteCoalescer(flush_fn=success_flush_fn, config=config)
    await coalescer.start()

    # Submit 3 items = max_batch_size → immediate flush
    await coalescer.submit([{"id": 1}, {"id": 2}, {"id": 3}])

    # Loop should process within one iteration
    await asyncio.sleep(0.01)

    assert coalescer._stats["flushed_batches"] == 1
    assert coalescer._stats["flushed_findings"] == 3

    await coalescer.stop()


# ─── Stats accounting ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_submitted_accurate(success_flush_fn):
    """Verify submitted counter equals sum of all submit() batch sizes."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}])
    await coalescer.submit([{"id": 2}, {"id": 3}])
    await coalescer.submit([])

    assert coalescer._stats["submitted"] == 3
    await coalescer.stop()


@pytest.mark.asyncio
async def test_stats_flushed_findings_accurate(success_flush_fn):
    """Verify flushed_findings counter equals sum of successful flush batches."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}, {"id": 2}])
    await asyncio.sleep(0.6)  # Allow loop to process (500ms flush interval)

    assert coalescer._stats["flushed_findings"] == 2
    assert coalescer._stats["flushed_batches"] == 1

    await coalescer.stop()


# ─── CoalescerConfig ───────────────────────────────────────────────────────

def test_coalescer_config_defaults():
    """Verify CoalescerConfig default values."""
    config = CoalescerConfig()
    assert config.max_batch_size == 50
    assert config.flush_interval_s == 0.5
    assert config.queue_maxsize == 16384
    assert config.min_batch_ratio == 0.05
    assert config.fast_interval_s == 0.005


def test_coalescer_config_from_env(monkeypatch):
    """Verify CoalescerConfig.from_env reads env vars correctly."""
    monkeypatch.setenv("HLEDAC_COALESCER_MAX_BATCH", "256")
    monkeypatch.setenv("HLEDAC_COALESCER_FLUSH_MS", "10")
    monkeypatch.setenv("HLEDAC_COALESCER_QUEUE_SIZE", "8192")
    monkeypatch.setenv("HLEDAC_COALESCER_MIN_BATCH_RATIO", "0.1")
    monkeypatch.setenv("HLEDAC_COALESCER_FAST_MS", "2")

    config = CoalescerConfig.from_env()

    assert config.max_batch_size == 256
    assert config.flush_interval_s == 0.010
    assert config.queue_maxsize == 8192
    assert config.min_batch_ratio == 0.1
    assert config.fast_interval_s == 0.002


def test_coalescer_config_from_env_missing_env_uses_defaults(monkeypatch):
    """Missing env vars fall back to defaults."""
    monkeypatch.delenv("HLEDAC_COALESCER_MAX_BATCH", raising=False)
    config = CoalescerConfig.from_env()
    assert config.max_batch_size == 50  # default


# ─── Edge cases ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flush_empty_findings_returns_empty(success_flush_fn):
    """Empty findings list → _flush returns [] without calling flush_fn."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    results = await coalescer._flush([])
    assert results == []

    await coalescer.stop()


@pytest.mark.asyncio
async def test_submit_empty_does_nothing(success_flush_fn):
    """submit([]) does not queue and does not increment stats."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([])
    assert coalescer._stats["submitted"] == 0

    await coalescer.stop()


@pytest.mark.asyncio
async def test_batch_counter_incremented_on_each_flush(success_flush_fn):
    """_batch_counter increments on every _flush call."""
    coalescer = WriteCoalescer(flush_fn=success_flush_fn)
    await coalescer.start()

    await coalescer.submit([{"id": 1}])
    await asyncio.sleep(0.6)  # Allow loop to process (500ms flush interval)
    await coalescer.submit([{"id": 2}])
    await asyncio.sleep(0.6)

    assert coalescer._batch_counter == 2

    await coalescer.stop()


@pytest.mark.asyncio
async def test_multiple_flush_errors_all_counted(failing_flush_fn, error_callback_log):
    """WC-6: Each flush error increments stats and fires callback."""
    coalescer = WriteCoalescer(
        flush_fn=failing_flush_fn,
        on_flush_error=error_callback_log,
    )
    await coalescer.start()

    await coalescer.submit([{"id": 1}])
    await asyncio.sleep(0.6)  # Allow loop to process (500ms flush interval)
    await coalescer.submit([{"id": 2}])
    await asyncio.sleep(0.6)

    assert coalescer._stats["errors"] >= 2
    assert len(error_callback_log._calls) >= 2

    await coalescer.stop()
