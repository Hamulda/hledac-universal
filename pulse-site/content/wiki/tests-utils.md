+++
title = "tests/utils/"
weight = 33
description = "<think> The user wants me to create a module overview for the `tests/utils` module in a codebase wiki. I need to follow the critical rules:  1. NEVER start with 'The X module consists of...', 'This mo..."

[extra]
tier = 2
file_count = 3
total_lines = 1358
languages = "Python"
parent_path = "tests"
+++

<think>
The user wants me to create a module overview for the `tests/utils` module in a codebase wiki. I need to follow the critical rules:

1. NEVER start with 'The X module consists of...', 'This module contains...', or any variant
2. First sentence MUST state what the module DOES or what PURPOSE it serves
3. Focus on PURPOSE, RESPONSIBILITIES, and ARCHITECTURAL ROLE
4. Describe the module's architectural role
5. Explain how this module fits into the larger system
6. Note if high fan-in (many dependents) - but this module has NO incoming dependencies
7. Note complexity: file count, line count, symbol density
8. Do NOT enumerate specific file names, class names, or function names unless they represent a truly central abstraction
9. Vary sentence structure
10. Write 4-8 sentences
11. Be specific about what the module does and its scale
12. Do NOT speculate about design intent or add information not in the context
13. NEVER leave missing spaces between words

From the structural context:
- Module: tests/utils
- Language: Python
- Files: 3 (spec_mocks.py 697 lines, memory_profiler.py 659 lines, __init__.py 2 lines)
- Total lines: 1358
- Avg lines/file: 452
- No outgoing dependencies
- No incoming dependencies
- Tier: 2 (which means it's a test utility module)

Key symbols:
- Snapshot class - bookend snapshot for RSS delta measurement, referenced in 23 files
- Memory Tracker - combined RSS + tracemalloc context manager
- Tracemalloc Snapshot - tracemalloc-based snapshot
- Tracemalloc Stats - lightweight tracemalloc stats
- make_governor_mock - function referenced in 3 files
- Various mock creation functions
- Memory profiling utilities

The module provides testing utilities for memory profiling and mock creation. It has no external dependencies but provides infrastructure used by other test modules.


The module stands as a self-contained testing support system with a dual focus on memory monitoring and test fixture generation. Its internal architecture revolves around sophisticated memory profiling techniques, capturing both system-level RSS and Python-specific object allocation patterns through lightweight tracking mechanisms. By offering zero-overhead memory snapshot capabilities and flexible mock generation, it enables precise test isolation and leak detection without introducing external dependencies.

Despite being referenced across 23 files, the module operates as a peripheral testing utility rather than a core system component. With 45 functions and 4 specialized classes, it maintains a focused, utilitarian design centered on two primary responsibilities: memory measurement infrastructure and mock creation. The module's compact implementation of 1358 lines across 3 files demonstrates a high-density approach to testing support, providing targeted utilities that integrate seamlessly into the broader test ecosystem.
</think>

This module provides memory profiling and mock creation utilities for the test suite, enabling leak detection and test isolation without introducing external runtime dependencies. The memory profiling subsystem offers both system-level RSS tracking via `Snapshot` and granular Python object allocation analysis through `TracemallocSnapshot` and `TracemallocStats`, with the latter designed as a lightweight zero-overhead alternative to full tracemalloc snapshots to avoid the 15-20 MB allocation cost per snapshot in large test runs. Mock utilities handle creation and deep cleanup of `MagicMock` and `AsyncMock` instances, including thread-safe context management and automatic garbage collection to prevent mock objects from leaking between tests. The module sits at Tier 2 with no incoming dependencies recorded in the structural graph, indicating it serves as supporting infrastructure consumed directly by test code rather than being a central hub; changes here would have localized impact on test reliability rather than application correctness. With 1358 lines across three files and 49 total symbols, the module exhibits high symbol density typical of utility code that prioritizes composability over abstraction depth.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `spec_mocks.py` (697 lines)
- `memory_profiler.py` (659 lines)
- `__init__.py` (2 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>Snapshot</code> (Class) in memory_profiler.py — referenced in 23 files</p>
<details><summary>Bookend snapshot for RSS delta measurement.</summary>
<div class="doc-comment">
<p>Bookend snapshot for RSS delta measurement.</p>
<p></p>
<p>Takes a RSS snapshot on creation. Call `delta_mb()` after the code</p>
<p>under test runs to get the RSS change.</p>
<p></p>
<p>Example:</p>
<p>snap = Snapshot()</p>
<p># ... test code ...</p>
<p>delta = snap.delta_mb()</p>
<p>assert delta &lt; 50, f"Memory leak: {delta:.1f} MB"</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition_strategy.py, archive_discovery.py, bench_f214_python314_runtime.py, conditional_cache.py +15 more</li></ul>
</li>
<li>
<p><code>MemoryTracker</code> (Class) in memory_profiler.py — referenced in 3 files</p>
<details><summary>Combined RSS + tracemalloc context manager for sprint cycle testing.</summary>
<div class="doc-comment">
<p>Combined RSS + tracemalloc context manager for sprint cycle testing.</p>
<p></p>
<p>Use as a context manager around a single sprint cycle to capture</p>
<p>memory state before/after and produce a detailed leak report.</p>
<p></p>
<p>Example:</p>
<p>with MemoryTracker() as tracker:</p>
<p>await run_one_sprint_cycle()</p>
<p>tracker.assert_leak_threshold(50)</p>
<p></p>
<p>Or for CI automation:</p>
<p>tracker = MemoryTracker(threshold_mb=50)</p>
<p>tracker.__enter__()</p>
<p>try:</p>
<p>await run_one_sprint_cycle()</p>
<p>finally:</p>
<p>tracker.__exit__(None, None, None)</p>
<p># raises AssertionError on leak</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: conftest.py, test_sprint_memory_profiling.py</li></ul>
</li>
<li>
<p><code>TracemallocSnapshot</code> (Class) in memory_profiler.py — referenced in 3 files</p>
<details><summary>tracemalloc-based snapshot for Python object allocation tracking.</summary>
<div class="doc-comment">
<p>tracemalloc-based snapshot for Python object allocation tracking.</p>
<p></p>
<p>More precise than RSS for detecting Python object leaks (e.g., lists</p>
<p>accumulating in module globals, forgotten callbacks, etc.)</p>
<p></p>
<p>Example:</p>
<p>snap = TracemallocSnapshot()</p>
<p># ... test code ...</p>
<p>top_deltas = snap.compare_top_n(5)</p>
<p>for stat in top_deltas:</p>
<p>print(f"  {stat}: {stat.size_diff/1024:.1f} KB")</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: conftest.py, test_sprint_memory_profiling.py</li></ul>
</li>
<li>
<p><code>make_governor_mock</code> (Function) in spec_mocks.py — referenced in 3 files</p>
<ul><li class="ref-list">Referenced by: conftest.py, migrate_test_mocks.py</li></ul>
</li>
<li>
<p><code>TracemallocStats</code> (Class) in memory_profiler.py — referenced in 2 files</p>
<details><summary>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</summary>
<div class="doc-comment">
<p>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</p>
<p></p>
<p>F350M-R fix: tracemalloc.take_snapshot() allocates a full Python object graph</p>
<p>(~15-20 MB per snapshot on a 181-test suite). get_traced_memory() returns</p>
<p>only two integers: current bytes and peak bytes — zero allocation overhead.</p>
<p></p>
<p>For detailed per-site breakdown (when needed), call take_snapshot() separately</p>
<p>and use compare_top_n() on TracemallocSnapshot.</p>
<p></p>
<p>Example:</p>
<p>stats = TracemallocStats.take()</p>
<p># ... test code ...</p>
<p>delta = stats.delta_bytes()</p>
<p>assert delta &lt; 10 * 1024 * 1024, f"Python allocations grew {delta / 1024 / 1024:.1f} MB"</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: test_sprint_memory_profiling.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (45)</summary>
<ul>
<li><code>make_sprint_scheduler_mock</code> (spec_mocks.py)</li>
<li><code>make_governor_mock</code> (spec_mocks.py)</li>
<li><code>make_storage_mock</code> (spec_mocks.py)</li>
<li><code>compare_top_n</code> (memory_profiler.py)</li>
<li><code>make_duckdb_store_mock_full</code> (spec_mocks.py)</li>
<li><code>_deep_cleanup_mock</code> (spec_mocks.py)
<details><summary>Recursively clean a MagicMock and its _mock_children.</summary>
<div class="doc-comment">
<p>Recursively clean a MagicMock and its _mock_children.</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to clean</p>
<p></p>
<p>Clears:</p>
<p>• _mock_children dict</p>
<p>• _mock_sealed state</p>
<p>• call_args_list</p>
</div>
</details>
</li>
<li><code>make_lmdb_mock</code> (spec_mocks.py)</li>
<li><code>make_duckdb_store_mock</code> (spec_mocks.py)</li>
<li><code>make_async_mock</code> (spec_mocks.py)</li>
<li><code>make_sync_mock</code> (spec_mocks.py)</li>
<li><code>joinable_threads</code> (spec_mocks.py)
<details><summary>Context manager: start daemon threads, join on exit with timeout.</summary>
<div class="doc-comment">
<p>Context manager: start daemon threads, join on exit with timeout.</p>
<p></p>
<p>Prevents thread leaks between tests:</p>
<p>• daemon=True — threads don't block pytest cleanup</p>
<p>• join(timeout) — catches threads that crash before completion</p>
<p>• join on exit — ensures cleanup even if test body raises</p>
<p></p>
<p>Usage:</p>
<p>with joinable_threads([worker_factory(i) for i in range(8)]) as threads:</p>
<p># threads are running</p>
<p>pass</p>
<p># all threads joined (or killed after timeout)</p>
<p></p>
<p>Args:</p>
<p>targets: Sequence of callables to run in separate daemon threads.</p>
<p></p>
<p>Yields:</p>
<p>List of started threading.Thread instances (daemon=True).</p>
</div>
</details>
</li>
<li><code>assert_no_leak</code> (memory_profiler.py)</li>
<li><code>count_mock_methods</code> (spec_mocks.py)
<details><summary>Count configured vs auto-created mock methods.</summary>
<div class="doc-comment">
<p>Count configured vs auto-created mock methods.</p>
<p></p>
<p>Useful for identifying overly-permissive mocks:</p>
<p>info = count_mock_methods(test_mock)</p>
<p>assert info['configured'] &gt; info['auto_created'], "Mock too permissive"</p>
<p></p>
<p>Returns:</p>
<p>dict with 'configured' (explicitly set) and 'auto_created' counts</p>
</div>
</details>
</li>
<li><code>mock_cleanup</code> (spec_mocks.py)
<details><summary>Context manager: collect and reset MagicMock instances on exit.</summary>
<div class="doc-comment">
<p>Context manager: collect and reset MagicMock instances on exit.</p>
<p></p>
<p>Fixes mock memory leaks in pytest:</p>
<p>• Clears _mock_children dicts (each holds 50-100 KB unreferenced)</p>
<p>• Resets call counts and side effects</p>
<p>• Triggers gc.collect() to free mock object memory</p>
<p></p>
<p>Usage:</p>
<p>def test_something():</p>
<p>with mock_cleanup():</p>
<p>scheduler = _make_scheduler_base()</p>
<p># ... test code ...</p>
<p># All mocks cleaned up after</p>
<p></p>
<p>Args:</p>
<p>*mocks: MagicMock/AsyncMock instances to clean up</p>
<p></p>
<p>M1 8GB impact:</p>
<p>• 30+ mock instances per test → 1.5-3 MB freed on exit</p>
<p>• gc.collect() clears ~5-10 MB of accumulated _mock_children</p>
</div>
</details>
</li>
<li><code>assert_leak_threshold</code> (memory_profiler.py)
<details><summary>Assert RSS delta is below threshold, with detailed failure message.</summary>
<div class="doc-comment">
<p>Assert RSS delta is below threshold, with detailed failure message.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Override instance threshold. Uses instance value if None.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If RSS grows beyond threshold, with detailed</p>
<p>breakdown showing tracemalloc allocation growth.</p>
</div>
</details>
</li>
<li><code>init_session_tracer</code> (memory_profiler.py)
<details><summary>Start the session-scoped tracemalloc tracer (idempotent).</summary>
<div class="doc-comment">
<p>Start the session-scoped tracemalloc tracer (idempotent).</p>
<p></p>
<p>Call once at pytest session start (e.g. in conftest.py session fixture).</p>
<p>Does NOT stop the tracer — use stop_session_tracer() at teardown.</p>
<p></p>
<p>Args:</p>
<p>nframes: Number of stack frames to trace (default: _TM_NFRAMES).</p>
<p>Higher = more detail, more memory (~8 KB per frame).</p>
<p></p>
<p>Returns:</p>
<p>True if tracer is now active.</p>
</div>
</details>
</li>
<li><code>_domain_in_traceback</code> (memory_profiler.py)
<details><summary>Return True if traceback contains any of the given domain prefixes.</summary>
<div class="doc-comment">
<p>Return True if traceback contains any of the given domain prefixes.</p>
<p></p>
<p>Matches three formats tracemalloc can produce:</p>
<p>1. Absolute paths:  /Users/.../hledac/brain/engine.py:123</p>
<p>2. Relative paths:  core/resource_governor.py:200, tests/utils/memory_profiler.py:160</p>
<p>3. Dot-notation:    hledac.universal.core.resource_governor:42</p>
<p></p>
<p>Filters noisy third-party allocations (psutil, tracemalloc internals, etc.)</p>
<p>while keeping only allocations from the project's modules.</p>
</div>
</details>
</li>
<li><code>stop</code> (memory_profiler.py)
<details><summary>Stop tracemalloc — ONLY in legacy (non-session) mode.</summary>
<div class="doc-comment">
<p>Stop tracemalloc — ONLY in legacy (non-session) mode.</p>
<p></p>
<p>In session-scoped tracer mode, this is a no-op: the session tracer</p>
<p>is owned by init_session_tracer() / stop_session_tracer() and must</p>
<p>not be stopped by individual snapshot instances.</p>
<p></p>
<p>Safe to call multiple times (idempotent in both modes).</p>
</div>
</details>
</li>
<li><code>_get_psutil_process</code> (memory_profiler.py)
<details><summary>Get or create a cached psutil.Process for the current PID.</summary>
<div class="doc-comment">
<p>Get or create a cached psutil.Process for the current PID.</p>
<p></p>
<p>Avoids 50 KB allocation per get_rss_mb() call by caching the Process object.</p>
<p>Refreshes the cached object every 10s or when PID changes.</p>
</div>
</details>
</li>
<li><code>register_allocation</code> (memory_profiler.py)
<details><summary>Register a large object for weakref-based finalization safety net.</summary>
<div class="doc-comment">
<p>Register a large object for weakref-based finalization safety net.</p>
<p></p>
<p>Issue #12 fix: pytest fixtures can crash before __exit__ cleanup, leaving</p>
<p>large objects pinned in memory. weakref.finalize() guarantees __del__ runs</p>
<p>even if the fixture crashes mid-test.</p>
<p></p>
<p>Args:</p>
<p>obj: Large object to track.</p>
<p>name: Optional name for debugging.</p>
<p></p>
<p>Returns:</p>
<p>weakref.finalize object — call .detach() to cancel tracking.</p>
</div>
</details>
</li>
<li><code>__exit__</code> (memory_profiler.py)</li>
<li><code>weak_mock</code> (spec_mocks.py)
<details><summary>Wrap mock in weakref to detect when all references are gone.</summary>
<div class="doc-comment">
<p>Wrap mock in weakref to detect when all references are gone.</p>
<p></p>
<p>Usage:</p>
<p>ref = weak_mock(some_mock)</p>
<p>del some_mock</p>
<p>gc.collect()</p>
<p>assert ref() is None, "Mock still referenced!"</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to wrap</p>
<p></p>
<p>Returns:</p>
<p>Weak reference to the mock</p>
</div>
</details>
</li>
<li><code>assert_no_leak</code> (memory_profiler.py)
<details><summary>Assert that RSS delta from snapshot is below threshold.</summary>
<div class="doc-comment">
<p>Assert that RSS delta from snapshot is below threshold.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Maximum acceptable RSS growth in MB.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If delta exceeds threshold.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (memory_profiler.py)</li>
<li><code>reset_mock_deep</code> (spec_mocks.py)
<details><summary>Deep reset: reset_mock() + clear _mock_children.</summary>
<div class="doc-comment">
<p>Deep reset: reset_mock() + clear _mock_children.</p>
<p></p>
<p>Equivalent to mock.reset_mock() but also clears the _mock_children</p>
<p>dict that accumulates on chained attribute access.</p>
<p></p>
<p>Usage:</p>
<p>mock_foo.bar.baz.qux()  # creates _mock_children entries</p>
<p>reset_mock_deep(mock_foo)  # clears everything</p>
<p></p>
<p>Args:</p>
<p>mock: MagicMock/AsyncMock to reset</p>
</div>
</details>
</li>
<li><code>format_top_deltas</code> (memory_profiler.py)
<details><summary>Format top N allocation deltas as a readable string.</summary>
<div class="doc-comment">
<p>Format top N allocation deltas as a readable string.</p>
<p></p>
<p>Returns:</p>
<p>Multi-line string suitable for assertion messages.</p>
</div>
</details>
</li>
<li><code>delta_mb</code> (memory_profiler.py)
<details><summary>Return RSS delta from snapshot to now.</summary>
<div class="doc-comment">
<p>Return RSS delta from snapshot to now.</p>
<p></p>
<p>Args:</p>
<p>force_gc: If True (default), run gc.collect() before measuring</p>
<p>to exclude unreachable Python objects from the delta.</p>
<p>Pass False for raw measured delta.</p>
<p></p>
<p>Returns:</p>
<p>RSS delta in MB (positive = growth, negative = freed).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a lightweight snapshot of Python object allocations.</span></li>
<li><code>_cleanup_mocks</code> (spec_mocks.py)
<details><summary>Clean up mock instances: clear _mock_children, reset call counts.</summary>
<div class="doc-comment">
<p>Clean up mock instances: clear _mock_children, reset call counts.</p>
<p></p>
<p>Args:</p>
<p>mocks: Tuple of mock instances to clean</p>
<p></p>
<p>Always-on, fail-safe: errors are swallowed to not break test teardown.</p>
</div>
</details>
</li>
<li><code>has_leak</code> (memory_profiler.py)
<details><summary>Check if any allocation grew by more than threshold_kb.</summary>
<div class="doc-comment">
<p>Check if any allocation grew by more than threshold_kb.</p>
<p></p>
<p>Args:</p>
<p>threshold_kb: Threshold in KB per allocation site.</p>
<p></p>
<p>Returns:</p>
<p>True if any single allocation site grew beyond threshold_kb.</p>
</div>
</details>
</li>
<li><code>stop_session_tracer</code> (memory_profiler.py)
<details><summary>Stop the session-scoped tracemalloc tracer (idempotent).</summary>
<div class="doc-comment">
<p>Stop the session-scoped tracemalloc tracer (idempotent).</p>
<p></p>
<p>Call once at pytest session teardown. Safe to call even if not started.</p>
</div>
</details>
</li>
<li><code>get_rss_mb</code> (memory_profiler.py)
<details><summary>Get current process RSS in MB.</summary>
<div class="doc-comment">
<p>Get current process RSS in MB.</p>
<p></p>
<p>Fail-safe: returns 0.0 on any error (permission, process terminated, etc.)</p>
<p>This ensures CI never fails due to measurement error.</p>
<p></p>
<p>Uses a cached psutil.Process object (~50 KB saved per call).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a baseline snapshot (call before code under test).</span></li>
<li><code>_rebuild_snapshot_domains</code> (memory_profiler.py)
<details><summary>Parse HLEDAC_TEST_TM_DOMAINS env var into a tuple of domain prefixes.</summary>
<div class="doc-comment">
<p>Parse HLEDAC_TEST_TM_DOMAINS env var into a tuple of domain prefixes.</p>
<p></p>
<p>Example: HLEDAC_TEST_TM_DOMAINS="hledac,brain,knowledge"</p>
<p>Results in domain prefixes used to filter comparison results.</p>
<p></p>
<p>Falls back to all domains (empty tuple) if env var is not set.</p>
</div>
</details>
</li>
<li><code>delta_bytes</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in bytes from baseline to now.</span></li>
<li><code>peak_delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return peak memory growth in MB from baseline.</span></li>
<li><code>make_mock_backend</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_storage_mock() instead.</span></li>
<li><code>make_mock_governor</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_governor_mock() instead.</span></li>
<li><code>make_mock_store</code> (spec_mocks.py) — <span class="doc-comment-inline">Deprecated: Use make_duckdb_store_mock() instead.</span></li>
<li><code>__enter__</code> (memory_profiler.py)</li>
<li><code>_get_snapshot_domains</code> (memory_profiler.py) — <span class="doc-comment-inline">Return cached domain prefixes, rebuilding only when TTL expires.</span></li>
<li><code>is_tracing</code> (memory_profiler.py) — <span class="doc-comment-inline">Return True if tracemalloc is currently active (session or otherwise).</span></li>
<li><code>delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in MB.</span></li>
<li><code>stop</code> (memory_profiler.py) — <span class="doc-comment-inline">Stop tracemalloc if not in session mode (no-op in session mode).</span></li>
<li><code>__del__</code> (memory_profiler.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (4)</summary>
<ul>
<li><code>TracemallocSnapshot</code> (memory_profiler.py)
<details><summary>tracemalloc-based snapshot for Python object allocation tracking.</summary>
<div class="doc-comment">
<p>tracemalloc-based snapshot for Python object allocation tracking.</p>
<p></p>
<p>More precise than RSS for detecting Python object leaks (e.g., lists</p>
<p>accumulating in module globals, forgotten callbacks, etc.)</p>
<p></p>
<p>Example:</p>
<p>snap = TracemallocSnapshot()</p>
<p># ... test code ...</p>
<p>top_deltas = snap.compare_top_n(5)</p>
<p>for stat in top_deltas:</p>
<p>print(f"  {stat}: {stat.size_diff/1024:.1f} KB")</p>
</div>
</details>
</li>
<li><code>MemoryTracker</code> (memory_profiler.py)
<details><summary>Combined RSS + tracemalloc context manager for sprint cycle testing.</summary>
<div class="doc-comment">
<p>Combined RSS + tracemalloc context manager for sprint cycle testing.</p>
<p></p>
<p>Use as a context manager around a single sprint cycle to capture</p>
<p>memory state before/after and produce a detailed leak report.</p>
<p></p>
<p>Example:</p>
<p>with MemoryTracker() as tracker:</p>
<p>await run_one_sprint_cycle()</p>
<p>tracker.assert_leak_threshold(50)</p>
<p></p>
<p>Or for CI automation:</p>
<p>tracker = MemoryTracker(threshold_mb=50)</p>
<p>tracker.__enter__()</p>
<p>try:</p>
<p>await run_one_sprint_cycle()</p>
<p>finally:</p>
<p>tracker.__exit__(None, None, None)</p>
<p># raises AssertionError on leak</p>
</div>
</details>
</li>
<li><code>TracemallocStats</code> (memory_profiler.py)
<details><summary>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</summary>
<div class="doc-comment">
<p>Lightweight tracemalloc stats using get_traced_memory() — 2 numbers, no snapshot.</p>
<p></p>
<p>F350M-R fix: tracemalloc.take_snapshot() allocates a full Python object graph</p>
<p>(~15-20 MB per snapshot on a 181-test suite). get_traced_memory() returns</p>
<p>only two integers: current bytes and peak bytes — zero allocation overhead.</p>
<p></p>
<p>For detailed per-site breakdown (when needed), call take_snapshot() separately</p>
<p>and use compare_top_n() on TracemallocSnapshot.</p>
<p></p>
<p>Example:</p>
<p>stats = TracemallocStats.take()</p>
<p># ... test code ...</p>
<p>delta = stats.delta_bytes()</p>
<p>assert delta &lt; 10 * 1024 * 1024, f"Python allocations grew {delta / 1024 / 1024:.1f} MB"</p>
</div>
</details>
</li>
<li><code>Snapshot</code> (memory_profiler.py)
<details><summary>Bookend snapshot for RSS delta measurement.</summary>
<div class="doc-comment">
<p>Bookend snapshot for RSS delta measurement.</p>
<p></p>
<p>Takes a RSS snapshot on creation. Call `delta_mb()` after the code</p>
<p>under test runs to get the RSS change.</p>
<p></p>
<p>Example:</p>
<p>snap = Snapshot()</p>
<p># ... test code ...</p>
<p>delta = snap.delta_mb()</p>
<p>assert delta &lt; 50, f"Memory leak: {delta:.1f} MB"</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (18)</summary>
<ul>
<li><code>compare_top_n</code> (memory_profiler.py)</li>
<li><code>assert_leak_threshold</code> (memory_profiler.py)
<details><summary>Assert RSS delta is below threshold, with detailed failure message.</summary>
<div class="doc-comment">
<p>Assert RSS delta is below threshold, with detailed failure message.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Override instance threshold. Uses instance value if None.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If RSS grows beyond threshold, with detailed</p>
<p>breakdown showing tracemalloc allocation growth.</p>
</div>
</details>
</li>
<li><code>stop</code> (memory_profiler.py)
<details><summary>Stop tracemalloc — ONLY in legacy (non-session) mode.</summary>
<div class="doc-comment">
<p>Stop tracemalloc — ONLY in legacy (non-session) mode.</p>
<p></p>
<p>In session-scoped tracer mode, this is a no-op: the session tracer</p>
<p>is owned by init_session_tracer() / stop_session_tracer() and must</p>
<p>not be stopped by individual snapshot instances.</p>
<p></p>
<p>Safe to call multiple times (idempotent in both modes).</p>
</div>
</details>
</li>
<li><code>register_allocation</code> (memory_profiler.py)
<details><summary>Register a large object for weakref-based finalization safety net.</summary>
<div class="doc-comment">
<p>Register a large object for weakref-based finalization safety net.</p>
<p></p>
<p>Issue #12 fix: pytest fixtures can crash before __exit__ cleanup, leaving</p>
<p>large objects pinned in memory. weakref.finalize() guarantees __del__ runs</p>
<p>even if the fixture crashes mid-test.</p>
<p></p>
<p>Args:</p>
<p>obj: Large object to track.</p>
<p>name: Optional name for debugging.</p>
<p></p>
<p>Returns:</p>
<p>weakref.finalize object — call .detach() to cancel tracking.</p>
</div>
</details>
</li>
<li><code>__exit__</code> (memory_profiler.py)</li>
<li><code>assert_no_leak</code> (memory_profiler.py)
<details><summary>Assert that RSS delta from snapshot is below threshold.</summary>
<div class="doc-comment">
<p>Assert that RSS delta from snapshot is below threshold.</p>
<p></p>
<p>Args:</p>
<p>threshold_mb: Maximum acceptable RSS growth in MB.</p>
<p></p>
<p>Raises:</p>
<p>AssertionError: If delta exceeds threshold.</p>
</div>
</details>
</li>
<li><code>__post_init__</code> (memory_profiler.py)</li>
<li><code>format_top_deltas</code> (memory_profiler.py)
<details><summary>Format top N allocation deltas as a readable string.</summary>
<div class="doc-comment">
<p>Format top N allocation deltas as a readable string.</p>
<p></p>
<p>Returns:</p>
<p>Multi-line string suitable for assertion messages.</p>
</div>
</details>
</li>
<li><code>delta_mb</code> (memory_profiler.py)
<details><summary>Return RSS delta from snapshot to now.</summary>
<div class="doc-comment">
<p>Return RSS delta from snapshot to now.</p>
<p></p>
<p>Args:</p>
<p>force_gc: If True (default), run gc.collect() before measuring</p>
<p>to exclude unreachable Python objects from the delta.</p>
<p>Pass False for raw measured delta.</p>
<p></p>
<p>Returns:</p>
<p>RSS delta in MB (positive = growth, negative = freed).</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a lightweight snapshot of Python object allocations.</span></li>
<li><code>has_leak</code> (memory_profiler.py)
<details><summary>Check if any allocation grew by more than threshold_kb.</summary>
<div class="doc-comment">
<p>Check if any allocation grew by more than threshold_kb.</p>
<p></p>
<p>Args:</p>
<p>threshold_kb: Threshold in KB per allocation site.</p>
<p></p>
<p>Returns:</p>
<p>True if any single allocation site grew beyond threshold_kb.</p>
</div>
</details>
</li>
<li><code>take</code> (memory_profiler.py) — <span class="doc-comment-inline">Take a baseline snapshot (call before code under test).</span></li>
<li><code>delta_bytes</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in bytes from baseline to now.</span></li>
<li><code>peak_delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return peak memory growth in MB from baseline.</span></li>
<li><code>__enter__</code> (memory_profiler.py)</li>
<li><code>delta_mb</code> (memory_profiler.py) — <span class="doc-comment-inline">Return delta in MB.</span></li>
<li><code>stop</code> (memory_profiler.py) — <span class="doc-comment-inline">Stop tracemalloc if not in session mode (no-op in session mode).</span></li>
<li><code>__del__</code> (memory_profiler.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (5)</summary>
<ul>
<li><code>_JOIN_TIMEOUT_S</code> (spec_mocks.py)</li>
<li><code>_TM_NFRAMES</code> (memory_profiler.py)
<details><summary>Number of stack frames tracked by tracemalloc (default 10, ~80 KB ring buffer).</summary>
<div class="doc-comment">
<p>Number of stack frames tracked by tracemalloc (default 10, ~80 KB ring buffer).</p>
<p></p>
<p>F350M-R fix: reduced from 25 → 10 frames (~80 KB vs ~200 KB per test session).</p>
<p>25 frames × ~8 KB/frame × 100 tests = ~20 MB retained. 10 frames × 100 = ~8 MB.</p>
</div>
</details>
</li>
<li><code>_SESSION_TRACER_STARTED</code> (memory_profiler.py)</li>
<li><code>_SESSION_TRACER_N_FRAMES</code> (memory_profiler.py)</li>
<li><code>LEAK_THRESHOLD_MB</code> (memory_profiler.py) — <span class="doc-comment-inline">Default leak threshold in MB per sprint cycle.</span></li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 1358 |
| Avg lines/file | 452 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

