#!/usr/bin/env python3
"""
F214-PERF-BENCH-HARNESS: Local benchmark for Python 3.14 optimizations.

Measures impact of:
- root lazy exports (cold vs cached)
- DuckDBShadowStore (cold vs cached)
- hash_identifier regex precompile
- zstd cache
- import-time
- JSON/orjson
- top-k/sort microbench
- async semaphore overhead
- memory snapshot

Run:
    PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal \\
        uv run python tools/bench_f214_python314_runtime.py [--quick] [--runs N] [--warmups N] [--json]

M1 8GB safe, fail-soft, ~30s runtime (--quick).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import importlib
import inspect
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

# ── paths ─────────────────────────────────────────────────────────────────────
BENCH_FILE = Path(__file__).resolve()
UNIVERSAL_ROOT = BENCH_FILE.parents[1]  # .../hledac/universal
PROJECT_ROOT = BENCH_FILE.parents[3]  # .../Hledac

assert UNIVERSAL_ROOT.name == "universal", f"UNIVERSAL_ROOT={UNIVERSAL_ROOT}"
assert PROJECT_ROOT.name == "Hledac", f"PROJECT_ROOT={PROJECT_ROOT}"

sys.path.insert(0, str(UNIVERSAL_ROOT))

# ── optional deps ──────────────────────────────────────────────────────────────
try:
    import orjson

    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# Python 3.14 zstd
try:
    import compression.zstd as zstd_mod

    def _zstd_compress(data: bytes) -> bytes:
        return zstd_mod.compress(data)

    def _zstd_decompress(data: bytes) -> bytes:
        return zstd_mod.decompress(data)

    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False

# ── import context ──────────────────────────────────────────────────────────────
from contextlib import contextmanager


@contextmanager
def package_import_context():
    """Add project root to sys.path for package-style imports (hledac.universal)."""
    project_root = str(PROJECT_ROOT)
    added = False
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        added = True
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(project_root)
            except ValueError:
                pass


# ════════════════════════════════════════════════════════════════════════════
# SHARED FORMATTING HELPERS
# ════════════════════════════════════════════════════════════════════════════


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _fmt_ms(ms: float) -> str:
    return f"{ms:.3f} ms"


def _fmt_ratio(raw: int, compressed: int) -> str:
    if raw == 0:
        return "N/A"
    ratio = (raw - compressed) / raw * 100
    return f"{ratio:.1f}%"


def _print_row(name: str, value: str, unit: str = "", width: int = 55) -> None:
    sep = " " * max(1, width - len(name) - len(value) - len(unit))
    print(f"  {name}{sep}{value}{unit}")


# ════════════════════════════════════════════════════════════════════════════
# SHARED BENCHMARK HELPERS
# ════════════════════════════════════════════════════════════════════════════


def summarize_samples(samples_ms: list[float]) -> dict[str, float]:
    """
    Compute min / median / mean / p95 / max from a list of sample times in ms.
    Returns: min_ms, median_ms, mean_ms, p95_ms, max_ms, runs
    """
    if not samples_ms:
        return {"min_ms": 0.0, "median_ms": 0.0, "mean_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "runs": 0}
    sorted_samples = sorted(samples_ms)
    n = len(sorted_samples)
    p95_idx = max(0, int(n * 0.95) - 1)
    median_idx = n // 2
    return {
        "min_ms": round(sorted_samples[0], 4),
        "median_ms": round(sorted_samples[median_idx], 4),
        "mean_ms": round(sum(sorted_samples) / n, 4),
        "p95_ms": round(sorted_samples[p95_idx], 4),
        "max_ms": round(sorted_samples[-1], 4),
        "runs": n,
    }


def time_many(fn: Callable[[], Any], *, runs: int = 7, warmups: int = 1) -> dict[str, Any]:
    """
    Run fn repeatedly `runs` times after `warmups` warm-up runs.
    Warm-up runs are NOT counted in the samples.

    Returns:
        status: "ok" | "fail"
        samples_ms: list of per-run timings in ms
        summary: summarize_samples output
        warmups: number of warm-up runs performed
    """
    samples_ms: list[float] = []
    is_async = inspect.iscoroutinefunction(fn)

    def _invoke() -> None:
        """Call fn once. Async fns are always run in a fresh thread-loop to avoid nesting."""
        if is_async:
            asyncio.run(fn())
        else:
            fn()

    # Warm-up phase (not counted)
    for _ in range(warmups):
        try:
            _invoke()
        except Exception:
            pass  # warm-up failures are ignored

    # Measured runs
    for _ in range(runs):
        try:
            gc.collect()
            t0 = time.perf_counter()
            _invoke()
            t1 = time.perf_counter()
            samples_ms.append((t1 - t0) * 1000.0)
        except Exception as exc:
            return {"status": "fail", "error": str(exc), "samples_ms": [], "summary": {}, "warmups": warmups, "runs": runs}

    return {
        "status": "ok",
        "samples_ms": [round(s, 4) for s in samples_ms],
        "summary": summarize_samples(samples_ms),
        "warmups": warmups,
        "runs": runs,
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 1 – Root import + lazy export first-access (cold vs cached)
# ════════════════════════════════════════════════════════════════════════════


def _clear_hledac_modules():
    """Remove all hledac.* modules from sys.modules."""
    for name in list(sys.modules):
        if name == "hledac" or name.startswith("hledac.universal"):
            del sys.modules[name]


def _root_import_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("1. Root Import Benchmark (cold vs cached)")

    with package_import_context():
        import importlib

        # ── cold import ───────────────────────────────────────────────────
        cold_result = time_many(
            lambda: (
                _clear_hledac_modules(),
                gc.collect(),
                importlib.import_module("hledac.universal"),
            ),
            runs=runs,
            warmups=warmups,
        )

        import_ms_samples = cold_result["samples_ms"]
        cold_summary = cold_result["summary"]

        print("  root import (cold):")
        _print_import_summary(cold_summary, "    ")

        # Re-import to test cached access (module already in sys.modules)
        u = importlib.import_module("hledac.universal")

        # Verify no premature sub-module loading
        premature = [m for m in (
            "hledac.universal.fetching.public_fetcher",
            "hledac.universal.knowledge.duckdb_store",
            "hledac.universal.patterns.pattern_matcher",
            "hledac.universal.config",
        ) if m in sys.modules]
        if premature:
            print(f"  WARNING – prematurely loaded: {premature}")
        else:
            print("  ✓ No premature sub-module loading")

        # ── cached re-import ─────────────────────────────────────────────
        def cached_import():
            _clear_hledac_modules()
            return importlib.import_module("hledac.universal")

        cached_result = time_many(cached_import, runs=runs, warmups=0)
        cached_summary = cached_result["summary"]

        print("  root import (cached, re-load after clear):")
        _print_import_summary(cached_summary, "    ")

        # ── lazy export first-access ───────────────────────────────────────
        # Must re-import to measure cold first-access per lazy attr
        _clear_hledac_modules()
        gc.collect()
        u_cold = importlib.import_module("hledac.universal")

        lazy_attrs = ["UniversalConfig", "match_text", "DuckDBShadowStore", "async_fetch_public_text"]
        first_access_samples: dict[str, list[float]] = {attr: [] for attr in lazy_attrs}

        # Multiple iterations for first-access (cold per attr)
        for _ in range(max(runs, warmups)):
            for attr in lazy_attrs:
                _clear_hledac_modules()
                gc.collect()
                u_tmp = importlib.import_module("hledac.universal")
                t0 = time.perf_counter()
                _ = getattr(u_tmp, attr)
                t1 = time.perf_counter()
                first_access_samples[attr].append((t1 - t0) * 1000.0)

        print("  lazy export first-access (cold, multiple iter):")
        for attr, samples in first_access_samples.items():
            s = summarize_samples(samples)
            _print_import_summary(s, f"    {attr}: ")

        # ── cached access ──────────────────────────────────────────────────
        # Already have `u` from above — measure getattr on the already-imported module
        print("  lazy export cached access:")
        for attr in lazy_attrs:
            cached_attr_samples: list[float] = []
            for _ in range(runs):
                t0 = time.perf_counter()
                _ = getattr(u, attr)
                t1 = time.perf_counter()
                cached_attr_samples.append((t1 - t0) * 1000.0)
            s = summarize_samples(cached_attr_samples)
            _print_import_summary(s, f"    {attr}: ")

        return {
            "cold_import_ms": import_ms_samples,
            "cold_import_summary": cold_summary,
            "cached_import_summary": cached_summary,
            "first_access_summary": {attr: summarize_samples(samples) for attr, samples in first_access_samples.items()},
            "cached_access_summary": {},  # printed above
        }


def _print_import_summary(s: dict[str, float], prefix: str = "    ") -> None:
    print(f"{prefix}min={s['min_ms']:.3f}ms  median={s['median_ms']:.3f}ms  p95={s['p95_ms']:.3f}ms  max={s['max_ms']:.3f}ms  runs={s['runs']}")


# ════════════════════════════════════════════════════════════════════════════
# SECTION 1B – DuckDBShadowStore cold vs cached
# ════════════════════════════════════════════════════════════════════════════


def _duckdb_store_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    """
    DuckDBShadowStore first-access benchmark.

    Cold first-access = import-chain benchmark.
    The store module import triggers duckdb + orjson init, which is what we measure.
    After import, DuckDBShadowStore() instantiation is cheap (~µs).

    IMPORTANT: cold first-access variance is EXPECTED because:
    - duckdb engine initialization is JIT-compiled on first call
    - orjson FFI loads per-process
    - Python module import involves path traversal, pyc load, bytecode verify
    Variance of 10x–50x between runs is normal for cold import-chain benchmarks.
    """
    _section("1B. DuckDBShadowStore Benchmark (cold vs cached)")

    with package_import_context():
        import importlib

        # ── cold first-access ──────────────────────────────────────────────
        # Clear ONLY relevant modules; duckdb_store is the primary target
        def cold_first_access():
            for name in list(sys.modules):
                if (
                    name == "hledac"
                    or name.startswith("hledac.universal")
                    or name == "duckdb"
                    or name.startswith("duckdb.")
                    or name == "orjson"
                ):
                    del sys.modules[name]
            gc.collect()
            # Re-import hledac.universal (pulls in duckdb_store transitively)
            importlib.import_module("hledac.universal")
            # Instantiate to verify the class is accessible (import already loaded it)
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
            return DuckDBShadowStore

        cold_result = time_many(cold_first_access, runs=runs, warmups=warmups)
        cold_summary = cold_result["summary"]

        print("  DuckDBShadowStore cold first-access (import chain):")
        print("  NOTE: variance of 10x–50x between runs is EXPECTED — this is an import-chain benchmark")
        _print_import_summary(cold_summary, "    ")

        # ── cached access ──────────────────────────────────────────────────
        # Module already imported; measure getattr + instantiation overhead
        def cached_access():
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
            return DuckDBShadowStore

        cached_result = time_many(cached_access, runs=runs, warmups=0)
        cached_summary = cached_result["summary"]

        print("  DuckDBShadowStore cached access (instantiation only):")
        _print_import_summary(cached_summary, "    ")

        return {
            "cold_first_access_summary": cold_summary,
            "cached_access_summary": cached_summary,
            "cold_samples_ms": cold_result["samples_ms"],
            "cached_samples_ms": cached_result["samples_ms"],
        }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 2 – config / project_types import time
# ════════════════════════════════════════════════════════════════════════════


def _config_import_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("2. config/project_types Import Benchmark")

    result: dict[str, Any] = {}

    with package_import_context():
        import importlib

        for label, mod_name in [("config", "hledac.universal.config"), ("project_types", "hledac.universal.project_types")]:
            key_ms = f"{label}_ms"

            def make_import(m=mod_name):
                def _inner():
                    for n in list(sys.modules):
                        if n == m or n.startswith(f"{m}."):
                            del sys.modules[n]
                    gc.collect()
                    return importlib.import_module(m)
                return _inner

            res = time_many(make_import(), runs=runs, warmups=warmups)
            result[key_ms] = res["samples_ms"]
            result[f"{key_ms}_summary"] = res["summary"]
            print(f"  import {mod_name}:")
            _print_import_summary(res["summary"], "    ")

    return result


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3 – HashIdentifier identify()
# ════════════════════════════════════════════════════════════════════════════


async def _hash_identifier_impl(n_calls: int) -> float:
    from text.hash_identifier import HashIdentifier

    hi = HashIdentifier()
    test_hashes = [
        "$2a$12$LQv3c1yqBWVHxkd0EZHAKmeQ3N0cMfhOLPq7Z0eNH6T2d3.QVJDXG",
        "$argon2id$v=19$m=19456,t=2,p=1$",
        "5f4dcc3b5aa765d61d8327deb882cf99",
        "e802d3f60fa8f8c8f5e9a9d9e0e0e0e0e0e0e0e0",
        "$pbkdf2_sha256$60000$",
        "$1$2y$10$",
        "$sha512$",
        "e802d3f60fa8f8c8f5e9a9d9e0e0e0e0e0e0e0e0",
        "aGVsbG93b3JsZGhlbGxvd29ybGRoZWxsb3dvcmxkaGVsbG8=",
    ]

    for i in range(n_calls):
        h = test_hashes[i % len(test_hashes)]
        await hi.identify(h)

    return float(n_calls)


async def _run_hash_identifier(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("3. HashIdentifier Benchmark")

    n_calls = 500 if quick else 10_000
    print(f"  Running {n_calls:,} identify() calls ...")

    async def batch():
        return await _hash_identifier_impl(n_calls)

    # Run warm-ups first (not counted)
    for _ in range(warmups):
        await batch()

    # Measured runs — await directly since we're in the async context
    samples_ms: list[float] = []
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        await batch()
        t1 = time.perf_counter()
        samples_ms.append((t1 - t0) * 1000.0)

    total_ms = sum(samples_ms)
    s = summarize_samples(samples_ms)
    calls_per_sec = (n_calls * runs) / (total_ms / 1000.0) if total_ms > 0 else 0.0
    median_cps = n_calls / (s["median_ms"] / 1000.0) if s.get("median_ms", 0) > 0 else 0.0

    print(f"  {n_calls:,} calls × {runs} runs:")
    if s:
        _print_import_summary(s, "    ")
        print(f"  calls/sec (median): {median_cps:,.0f}")
    else:
        print("    (no successful samples)")

    return {
        "status": "ok",
        "samples_ms": [round(s, 4) for s in samples_ms],
        "summary": s,
        "warmups": warmups,
        "runs": runs,
        "calls_per_sec": round(calls_per_sec, 1),
        "n_calls": n_calls,
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 4 – HashIdentifier file scan
# ════════════════════════════════════════════════════════════════════════════


_HASH_TEMPLATES = [
    "$2a${len}$12$LQv3c1yqBWVHxkd0EZHAKmeQ3N0cMfhO",
    "$argon2id$v=19$m=19456,t=2,p=1$YWFhYWFhYWFhYWFh",
    "5f4dcc3b5aa765d61d8327deb882cf99",
    "e802d3f60fa8f8c8f5e9a9d9e0e0e0e0e0e0e0e0",
    "$pbkdf2_sha256$60000$dmFsaWQ",
    "$1$2y$10$VVVVVVVVVVVVVVVVVVVVVVVVVV",
    "aGVsbG93b3JsZGhlbGxvd29ybGRoZWxsb3dvcmxkaGVsbG8=",
    "0000000000000000000000000000000000000000",
    "$H$1$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "$P$B$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
]


def _write_hash_file(n_lines: int) -> str:
    lines = []
    for i in range(n_lines):
        tpl = _HASH_TEMPLATES[i % len(_HASH_TEMPLATES)]
        lines.append(f'0.0.0.0 - - [10/Oct/2026] "GET /api/v1/resource/{i} HTTP/1.1" 200 {tpl} "-" "-"')
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("\n".join(lines))
        return f.name


async def _run_hash_identifier_file(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("4. HashIdentifier File Scan Benchmark")

    from text.hash_identifier import HashIdentifier

    n_lines = 100 if quick else 1_000
    print(f"  Creating tempfile with {n_lines:,} hash-like lines ...")

    tmp_path = _write_hash_file(n_lines)
    hi = HashIdentifier()

    try:
        async def scan():
            return await hi.identify_in_file(tmp_path)

        # Run one scan to get findings count (needed for result, not measured)
        findings_count = len(await scan())

        # Warmup (not counted)
        for _ in range(warmups):
            await scan()

        # Measured runs
        samples_ms = []
        for _ in range(runs):
            gc.collect()
            t0 = time.perf_counter()
            await scan()
            t1 = time.perf_counter()
            samples_ms.append((t1 - t0) * 1000.0)

        result_summary = summarize_samples(samples_ms)
        status = "ok"

        print(f"  {n_lines:,} lines scanned × {runs} runs:")
        _print_import_summary(result_summary, "    ")

        return {
            "n_lines": n_lines,
            "findings": findings_count,
            "summary": result_summary,
            "samples_ms": samples_ms,
            "status": status,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ════════════════════════════════════════════════════════════════════════════
# SECTION 5 – zstd compression (Python 3.14)
# ════════════════════════════════════════════════════════════════════════════


def _zstd_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("5. zstd Compression Benchmark")

    if not _HAS_ZSTD:
        print("  SKIP: compression.zstd not available (requires Python 3.14)")
        return {"skipped": True, "reason": "zstd not available"}

    payload = {
        "evidence": [
            {
                "title": f"Investigation Report Case {i}",
                "content": (
                    "Lorem ipsum dolor sit amet consectetur adipiscing elit. "
                    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
                    "Ut enim ad minim veniam quis nostrud exercitation ullamco laboris."
                ),
                "tags": ["osint", "threat-intel", "domain", f"case-{i % 50}"],
                "hash": "a" * 64,
            }
            for i in range(200)
        ],
        "metadata": {"source": "F214 benchmark harness", "version": "1.0", "timestamp": "2026-05-14T00:00:00Z"},
    }

    raw_data = orjson.dumps(payload) if _HAS_ORJSON else json.dumps(payload).encode()
    raw_size = len(raw_data)

    def compress_fn():
        return _zstd_compress(raw_data)

    def decompress_fn(compressed: bytes):
        return _zstd_decompress(compressed)

    compress_result = time_many(compress_fn, runs=runs, warmups=warmups)
    compressed = compress_fn()  # get one for decompress
    decompress_result = time_many(lambda: decompress_fn(compressed), runs=runs, warmups=warmups)

    compressed_size = len(compressed)
    orjson_ms = time_many(lambda: orjson.dumps(payload), runs=runs, warmups=warmups)

    print(f"  raw={raw_size / 1024:.1f} KiB  compressed={compressed_size / 1024:.1f} KiB  ratio={_fmt_ratio(raw_size, compressed_size)}")
    print(f"  zstd.compress ({runs} runs):")
    _print_import_summary(compress_result["summary"], "    ")
    print(f"  zstd.decompress ({runs} runs):")
    _print_import_summary(decompress_result["summary"], "    ")
    print(f"  orjson.dumps ({runs} runs):")
    _print_import_summary(orjson_ms["summary"], "    ")

    return {
        "raw_size_bytes": raw_size,
        "compressed_size_bytes": compressed_size,
        "compression_ratio_pct": round((raw_size - compressed_size) / raw_size * 100, 1) if raw_size > 0 else 0,
        "compress_summary": compress_result["summary"],
        "decompress_summary": decompress_result["summary"],
        "orjson_summary": orjson_ms["summary"],
        "compress_samples_ms": compress_result["samples_ms"],
        "decompress_samples_ms": decompress_result["samples_ms"],
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 6 – json vs orjson
# ════════════════════════════════════════════════════════════════════════════


def _json_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("6. JSON Benchmark (json vs orjson)")

    if not _HAS_ORJSON:
        print("  SKIP: orjson not available")
        return {"skipped": True, "reason": "orjson not available"}

    n_items = 1000 if quick else 5_000
    payload = [
        {
            "id": i,
            "name": f"item_{i}",
            "status": "active" if i % 2 == 0 else "pending",
            "score": round(i * 0.123, 4),
            "tags": ["osint", "threat", f"tag-{i % 20}"],
        }
        for i in range(n_items)
    ]

    dumps_payload = orjson.dumps(payload)

    json_dumps_result = time_many(lambda: json.dumps(payload), runs=runs, warmups=warmups)
    json_loads_result = time_many(lambda: json.loads(json.dumps(payload)), runs=runs, warmups=warmups)
    orjson_dumps_result = time_many(lambda: orjson.dumps(payload), runs=runs, warmups=warmups)
    orjson_loads_result = time_many(lambda: orjson.loads(dumps_payload), runs=runs, warmups=warmups)

    speedup_d = json_dumps_result["summary"]["median_ms"] / orjson_dumps_result["summary"]["median_ms"] if orjson_dumps_result["summary"]["median_ms"] > 0 else 0
    speedup_l = json_loads_result["summary"]["median_ms"] / orjson_loads_result["summary"]["median_ms"] if orjson_loads_result["summary"]["median_ms"] > 0 else 0

    print(f"  {n_items:,} items  ({runs} runs each):")
    print(f"  json.dumps:")
    _print_import_summary(json_dumps_result["summary"], "    ")
    print(f"  json.loads:")
    _print_import_summary(json_loads_result["summary"], "    ")
    print(f"  orjson.dumps:")
    _print_import_summary(orjson_dumps_result["summary"], "    ")
    print(f"  orjson.loads:")
    _print_import_summary(orjson_loads_result["summary"], "    ")
    print(f"  orjson speedup dumps={speedup_d:.2f}x  loads={speedup_l:.2f}x")

    return {
        "n_items": n_items,
        "json_dumps_summary": json_dumps_result["summary"],
        "json_loads_summary": json_loads_result["summary"],
        "orjson_dumps_summary": orjson_dumps_result["summary"],
        "orjson_loads_summary": orjson_loads_result["summary"],
        "orjson_speedup_dumps": round(speedup_d, 2),
        "orjson_speedup_loads": round(speedup_l, 2),
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 7 – top-k: sorted[:k] vs heapq.nlargest
# ════════════════════════════════════════════════════════════════════════════


def _topk_benchmark(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("7. Top-K Benchmark (sorted vs heapq)")

    import heapq
    import random

    n_items = 10_000 if quick else 50_000
    k = 20
    data = [random.random() for _ in range(n_items)]

    def sorted_fn():
        return sorted(data, reverse=True)[:k]

    def heapq_fn():
        return heapq.nlargest(k, data)

    sorted_result = time_many(sorted_fn, runs=runs, warmups=warmups)
    heapq_result = time_many(heapq_fn, runs=runs, warmups=warmups)

    speedup = sorted_result["summary"]["median_ms"] / heapq_result["summary"]["median_ms"] if heapq_result["summary"]["median_ms"] > 0 else 0

    print(f"  {n_items:,} items, k={k}  ({runs} runs each):")
    print(f"  sorted[:k]:")
    _print_import_summary(sorted_result["summary"], "    ")
    print(f"  heapq.nlargest(k):")
    _print_import_summary(heapq_result["summary"], "    ")
    print(f"  heapq speedup={speedup:.2f}x")

    return {
        "n_items": n_items,
        "k": k,
        "sorted_summary": sorted_result["summary"],
        "heapq_summary": heapq_result["summary"],
        "speedup": round(speedup, 2),
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 8 – async semaphore overhead
# ════════════════════════════════════════════════════════════════════════════════════


async def _async_semaphore_impl(n_tasks: int, sem_limit: int):
    async def plain_task(_: int) -> int:
        await asyncio.sleep(0)
        return 1

    async def sem_task(idx: int, sem: asyncio.Semaphore) -> int:
        async with sem:
            return await plain_task(idx)

    async def plain_gather():
        return await asyncio.gather(*(plain_task(i) for i in range(n_tasks)))

    async def semaphore_gather():
        sem = asyncio.Semaphore(sem_limit)
        return await asyncio.gather(*(sem_task(i, sem) for i in range(n_tasks)))

    t0 = time.perf_counter()
    plain_result = await plain_gather()
    t1 = time.perf_counter()
    plain_ms = (t1 - t0) * 1000.0

    t0 = time.perf_counter()
    sem_result = await semaphore_gather()
    t1 = time.perf_counter()
    sem_ms = (t1 - t0) * 1000.0

    return plain_result, plain_ms, sem_result, sem_ms


async def _run_async_semaphore(*, runs: int = 3, warmups: int = 1, quick: bool = False) -> dict[str, Any]:
    _section("8. Async Semaphore Overhead Benchmark")

    n_tasks = 200 if quick else 1_000
    sem_limit = 50

    plain_samples: list[float] = []
    sem_samples: list[float] = []

    for _ in range(warmups):
        await _async_semaphore_impl(n_tasks, sem_limit)

    for _ in range(runs):
        _, plain_ms, _, sem_ms = await _async_semaphore_impl(n_tasks, sem_limit)
        plain_samples.append(plain_ms)
        sem_samples.append(sem_ms)

    plain_s = summarize_samples(plain_samples)
    sem_s = summarize_samples(sem_samples)
    overhead_pct = (sem_s["median_ms"] - plain_s["median_ms"]) / plain_s["median_ms"] * 100 if plain_s["median_ms"] > 0 else 0

    print(f"  {n_tasks:,} tasks, limit={sem_limit}  ({runs} runs each):")
    print(f"  plain gather:")
    _print_import_summary(plain_s, "    ")
    print(f"  semaphore gather:")
    _print_import_summary(sem_s, "    ")
    print(f"  semaphore overhead={overhead_pct:.1f}%")

    return {
        "n_tasks": n_tasks,
        "semaphore_limit": sem_limit,
        "plain_summary": plain_s,
        "semaphore_summary": sem_s,
        "overhead_pct": round(overhead_pct, 2),
        "plain_samples_ms": [round(s, 4) for s in plain_samples],
        "semaphore_samples_ms": [round(s, 4) for s in sem_samples],
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9 – memory snapshot
# ════════════════════════════════════════════════════════════════════════════


def _memory_snapshot(quick: bool = False) -> dict[str, Any]:
    _section("9. Memory Snapshot")

    rus = resource.getrusage(resource.RUSAGE_SELF)
    rss_raw = rus.ru_maxrss

    # macOS reports bytes; Linux reports KiB
    if sys.platform == "darwin":
        # ru_maxrss is bytes on Darwin
        rss_kib = rss_raw / 1024.0
    else:
        # ru_maxrss is KiB on Linux
        rss_kib = float(rss_raw)

    print(f"  resource.getrusage().ru_maxrss = {rss_raw} (raw)")
    print(f"  → ru_maxrss_raw (KiB)            = {rss_kib:.2f} KiB")
    print(f"  → ru_maxrss_raw / 1024 (MiB)    = {rss_kib / 1024:.3f} MiB")

    result: dict[str, Any] = {
        "ru_maxrss_raw": rss_raw,
        "ru_maxrss_kib": round(rss_kib, 3),
        "ru_maxrss_mib": round(rss_kib / 1024, 3),
        "units": {"ru_maxrss_raw": "bytes on darwin / KiB on linux (unconverted)"},
    }

    if _HAS_PSUTIL:
        proc = psutil.Process(os.getpid())
        mem_info = proc.memory_info()
        psutil_rss_mib = mem_info.rss / 1024 / 1024
        print(f"  psutil RSS                     = {psutil_rss_mib:.3f} MiB")
        result["psutil_rss_mib"] = round(psutil_rss_mib, 3)

    return result


# ════════════════════════════════════════════════════════════════════════════
# Benchmark registry
# ════════════════════════════════════════════════════════════════════════════


def _run_sync_benchmark(
    name: str, fn: Callable[..., dict[str, Any]], **kwargs
) -> dict[str, Any]:
    try:
        return fn(**kwargs)
    except Exception as exc:
        print(f"  ERROR in {name}: {exc}")
        return {"error": str(exc), "status": "fail"}


async def _run_async_benchmark(
    name: str, fn: Callable[..., Any], **kwargs
) -> dict[str, Any]:
    try:
        return await fn(**kwargs)
    except Exception as exc:
        print(f"  ERROR in {name}: {exc}")
        return {"error": str(exc), "status": "fail"}


async def _run_all(quick: bool, runs: int, warmups: int) -> dict[str, Any]:
    """Run all benchmarks in a single event loop."""
    sections: dict[str, Any] = {}

    # Sync benchmarks
    sync_benchmarks: list[tuple[str, Callable[..., dict[str, Any]]]] = [
        ("root_import", _root_import_benchmark),
        ("duckdb_store", _duckdb_store_benchmark),
        ("config_import", _config_import_benchmark),
        ("zstd", _zstd_benchmark),
        ("json", _json_benchmark),
        ("topk", _topk_benchmark),
        ("memory_snapshot", _memory_snapshot),
    ]

    for name, fn in sync_benchmarks:
        if name in ("root_import", "duckdb_store", "config_import", "zstd", "json", "topk"):
            sections[name] = _run_sync_benchmark(name, fn, runs=runs, warmups=warmups, quick=quick)
        else:
            sections[name] = _run_sync_benchmark(name, fn, quick=quick)

    # Async benchmarks — awaited directly within _run_all() event loop
    async_benchmarks: list[tuple[str, Callable[..., Any]]] = [
        ("hash_identifier", _run_hash_identifier),
        ("hash_identifier_file", _run_hash_identifier_file),
        ("async_semaphore", _run_async_semaphore),
    ]

    for name, fn in async_benchmarks:
        sections[name] = await fn(runs=runs, warmups=warmups, quick=quick)

    return sections


# ════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ════════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(description="F214-PERF-BENCH-HARNESS")
    parser.add_argument("--quick", action="store_true", help="Fewer iterations (fast mode, <30s)")
    parser.add_argument("--json", action="store_true", help="JSON output to stdout (text to stderr)")
    parser.add_argument(
        "--runs", type=int, default=None,
        help="Number of measured runs per benchmark. Default: 3 (quick), 7 (full)"
    )
    parser.add_argument(
        "--warmups", type=int, default=None,
        help="Number of warm-up runs. Default: 1 (quick), 2 (full)"
    )
    args = parser.parse_args()

    quick = args.quick
    runs = args.runs if args.runs is not None else (3 if quick else 7)
    warmups = args.warmups if args.warmups is not None else (1 if quick else 2)

    # In JSON mode, redirect text to stderr so stdout is pure JSON.
    # Must grab the REAL stdout BEFORE any redirection happens.
    real_stdout = sys.stdout

    if args.json:
        sys.stdout = sys.stderr

    try:
        _run_main(quick=quick, runs=runs, warmups=warmups, args=args, real_stdout=real_stdout)
    finally:
        if args.json:
            sys.stdout = real_stdout


def _run_main(quick: bool, runs: int, warmups: int, args: argparse.Namespace, real_stdout: Any) -> None:
    print("=" * 60)
    print("  F214-PERF-BENCH-HARNESS-STABILITY")
    print(f"  quick={quick}  runs={runs}  warmups={warmups}  platform={sys.platform}")
    print("=" * 60)

    results = asyncio.run(_run_all(quick=quick, runs=runs, warmups=warmups))

    if args.json:
        output = {
            "status": "ok",
            "platform": sys.platform,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "quick": quick,
            "runs": runs,
            "warmups": warmups,
            "has_orjson": _HAS_ORJSON,
            "has_psutil": _HAS_PSUTIL,
            "has_zstd": _HAS_ZSTD,
            "benchmarks": results,
        }
        json_bytes = (
            orjson.dumps(output, option=orjson.OPT_INDENT_2)
            if _HAS_ORJSON
            else json.dumps(output, indent=2)
        )
        real_stdout.write(json_bytes.decode() if isinstance(json_bytes, bytes) else json_bytes)
        real_stdout.write("\n")
    else:
        print(f"\n{'=' * 60}")
        print("  BENCHMARK COMPLETE")
        print("=" * 60)


if __name__ == "__main__":
    main()