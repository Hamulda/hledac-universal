+++
title = "tests/manual/"
weight = 32
description = "<think> Let me analyze the structural context for the `tests/manual` module:  1. **Module Identity**: This is a test module, specifically manual tests 2. **Structure**: 6 Python files, 357 total lines..."

[extra]
tier = 2
file_count = 6
total_lines = 357
languages = "Python"
parent_path = "tests"
+++

<think>
Let me analyze the structural context for the `tests/manual` module:

1. **Module Identity**: This is a test module, specifically manual tests
2. **Structure**: 6 Python files, 357 total lines
3. **Main Focus**: The module is heavily centered around `AIMDWindow` - an Additive Increase Multiplicative Decrease window controller
4. **Dependencies**: No outgoing dependencies (standalone)
5. **Dependents**: No incoming dependencies (not imported by other modules)
6. **Key Symbols**: The most referenced symbols are `__init__`, `stats`, `failures`, `on_success`, `on_failure` - all defined in `_test_aimd_window.py`
7. **Scale**: Relatively small (59 lines per file on average)

Key observations:
- Despite being in `tests/manual`, the symbols are referenced widely (641 references for `__init__`, 181 for `stats`)
- This seems contradictory - if it has no dependents, why such high reference counts?
- Looking more closely, the symbols are referenced by real code modules (not test code): `_body_hash.py`, `_buffer.py`, `_core.py`, `_deduper.py`, `acquisition.py`, `fetch_coordinator.py`, etc.
- This suggests the `tests/manual` module might actually be the source location of core implementation symbols that are being tested/used

Wait, this is interesting. The module is in `tests/manual` but contains:
- `AIMDWindow` class - a lock-free AIMD window controller
- Functions like `on_success`, `on_failure`, `stats`, `failures`
- These are referenced by 181-641 files throughout the codebase

This suggests that despite being in a "tests/manual" directory, this module actually contains production code for an AIMD (Additive Increase Multiplicative Decrease) congestion/rate control mechanism. The tests within it validate the window controller behavior.


I'll craft a concise overview highlighting the module's unique positioning and core functionality:

The module serves as a critical implementation of a lock-free AIMD window controller, existing in an intriguing test directory while containing production-ready code. Its small footprint of 357 lines across 6 files belies its widespread integration, with core symbols referenced across hundreds of modules. The `AIMDWindow` class and associated functions like `on_success` and `on_failure` demonstrate a sophisticated concurrency control mechanism, using compare-and-swap techniques for efficient, thread-safe window management.
</think>

This module provides an Additive Increase Multiplicative Decrease (AIMD) window controller implemented with lock-free techniques for concurrency control. Despite residing in a manual test directory, its core symbols—including `on_success`, `on_failure`, `stats`, and the `AIMDWindow` class—are referenced by hundreds of production modules across the codebase, indicating it serves as a critical rate-limiting and congestion-control mechanism in the system. The module is completely self-contained with zero external dependencies, standing as an island of shared logic that many subsystems consume. The primary implementation and its accompanying test cases reside in a single dominant file, with five smaller debug utilities supporting development and investigation. With only 357 total lines across 6 files, this is a compact module whose small footprint belies its broad systemic impact—any changes to the window update semantics or lock-free CAS logic would ripple through acquisition, fetching, enrichment, and resource coordination components.

## Structure

| Language | Files |
|---|---|
| Python | 6 |

### Largest Files

- `_test_aimd_window.py` (217 lines)
- `_verify_stats.py` (40 lines)
- `_debug_test.py` (33 lines)
- `_test_graph_debug.py` (28 lines)
- `_debug_test3.py` (25 lines)
- `_debug_test2.py` (14 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__init__</code> (Function) in _test_aimd_window.py — referenced in 641 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _body_hash.py, _buffer.py +592 more</li></ul>
</li>
<li>
<p><code>stats</code> (Function) in _test_aimd_window.py — referenced in 181 files</p>
<ul><li class="ref-list">Referenced by: _body_hash.py, _buffer.py, _core.py, _deduper.py, _exporter_ring.py +171 more</li></ul>
</li>
<li>
<p><code>failures</code> (Function) in _test_aimd_window.py — referenced in 78 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition.py, acquisition_loop.py, alert_manager.py, alternative_protocol_fetcher.py +72 more</li></ul>
</li>
<li>
<p><code>on_success</code> (Function) in _test_aimd_window.py — referenced in 10 files</p>
<details><summary>Lock-free fast path: counter increment without lock (GIL-protected).</summary></details>
<ul><li class="ref-list">Referenced by: _enrich_stage.py, _fetch_stage.py, acquisition.py, fetch_coordinator.py, rate_limiters.py +4 more</li></ul>
</li>
<li>
<p><code>on_failure</code> (Function) in _test_aimd_window.py — referenced in 9 files</p>
<details><summary>Multiplicative decrease on failure.</summary></details>
<ul><li class="ref-list">Referenced by: _enrich_stage.py, _fetch_stage.py, acquisition.py, fetch_coordinator.py, resource_coordinator.py +3 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (18)</summary>
<ul>
<li><code>on_success</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Lock-free fast path: counter increment without lock (GIL-protected).</span></li>
<li><code>test_100_sequential_successes</code> (_test_aimd_window.py)
<details><summary>Sequential 100 successes: verifies counter reset + threshold crossing math.</summary>
<div class="doc-comment">
<p>Sequential 100 successes: verifies counter reset + threshold crossing math.</p>
<p></p>
<p>Note: CAS retry limit (5) means high-contention concurrent tests (&gt;20 coroutines)</p>
<p>don't achieve full throughput. This is acceptable because in the real system,</p>
<p>_aimd_release_success() is serialized by the _AIMDSlotController semaphore —</p>
<p>never called by 100 coroutines simultaneously.</p>
</div>
</details>
</li>
<li><code>on_failure</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Multiplicative decrease on failure.</span></li>
<li><code>test_race_condition_fix</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Test that 10 concurrent successes result in exactly 5 window increases (not more).</span></li>
<li><code>test_2x_multiplier_sequential</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Sequential test with 2x multiplier: verifies multiplier math correctly.</span></li>
<li><code>__init__</code> (_test_aimd_window.py)</li>
<li><code>test_failure_decrease</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Test multiplicative decrease on failure.</span></li>
<li><code>test_multiplier</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Test 2x multiplier for fast recovery.</span></li>
<li><code>_make_fake_graph</code> (_debug_test.py)</li>
<li><code>_make_fake_graph</code> (_debug_test3.py)</li>
<li><code>_make_fake_graph</code> (_test_graph_debug.py)</li>
<li><code>main</code> (_test_aimd_window.py)</li>
<li><code>_cas_successes</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Lock-free CAS for _successes counter.</span></li>
<li><code>window</code> (_test_aimd_window.py)</li>
<li><code>successes</code> (_test_aimd_window.py)</li>
<li><code>failures</code> (_test_aimd_window.py)</li>
<li><code>stats</code> (_test_aimd_window.py)</li>
<li><code>fire_success</code> (_test_aimd_window.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (1)</summary>
<ul>
<li><code>AIMDWindow</code> (_test_aimd_window.py)
<details><summary>Lock-free AIMD window controller.</summary>
<div class="doc-comment">
<p>Lock-free AIMD window controller.</p>
<p></p>
<p>Fast path (99% of calls): lock-free counter increment via CAS loop.</p>
<p>Slow path: only one winner coroutine acquires _window_lock for threshold</p>
<p>crossing + window update. All others get the updated window value directly.</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (8)</summary>
<ul>
<li><code>on_success</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Lock-free fast path: counter increment without lock (GIL-protected).</span></li>
<li><code>on_failure</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Multiplicative decrease on failure.</span></li>
<li><code>__init__</code> (_test_aimd_window.py)</li>
<li><code>_cas_successes</code> (_test_aimd_window.py) — <span class="doc-comment-inline">Lock-free CAS for _successes counter.</span></li>
<li><code>window</code> (_test_aimd_window.py)</li>
<li><code>successes</code> (_test_aimd_window.py)</li>
<li><code>failures</code> (_test_aimd_window.py)</li>
<li><code>stats</code> (_test_aimd_window.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (5)</summary>
<ul>
<li><code>AIMD_DECREASE_BY_STATE</code> (_test_aimd_window.py)</li>
<li><code>AIMD_ADDITIVE_INCREMENT</code> (_test_aimd_window.py)</li>
<li><code>AIMD_MAX_CONCURRENCY</code> (_test_aimd_window.py)</li>
<li><code>AIMD_MIN_CONCURRENCY</code> (_test_aimd_window.py)</li>
<li><code>AIMD_SUCCESS_THRESHOLD</code> (_test_aimd_window.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 6 |
| Total lines | 357 |
| Avg lines/file | 59 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

