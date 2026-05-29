#!/usr/bin/env python3
"""
Benchmark: Python set vs Rust UrlSet (FNV-1a) on 10 000 URL operations.

Measures: add (unique), add (duplicate), contains (hit), contains (miss), clear.
"""

import os
import sys
import time

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from tools.url_dedup import _RUST_URL_DEDUP_AVAILABLE


def benchmark_python_set(urls: list[str], n_ops: int) -> dict:
    s: set[str] = set()
    t0 = time.monotonic()
    for i in range(n_ops):
        url = urls[i % len(urls)]
        s.add(url)
    add_time = time.monotonic() - t0

    t0 = time.monotonic()
    for i in range(n_ops):
        url = urls[i % len(urls)]
        _ = url in s
    contains_time = time.monotonic() - t0

    t0 = time.monotonic()
    for i in range(n_ops):
        url = f"https://fake-{i}.com/path"
        _ = url in s
    miss_time = time.monotonic() - t0

    t0 = time.monotonic()
    s.clear()
    clear_time = time.monotonic() - t0

    return {
        "add_unique": add_time,
        "contains_hit": contains_time,
        "contains_miss": miss_time,
        "clear": clear_time,
        "final_len": len(s),
    }


def benchmark_rust_url_set(urls: list[str], n_ops: int) -> dict:
    from hledac_rust_extensions import UrlSet as RustUrlSet

    s = RustUrlSet()
    t0 = time.monotonic()
    for i in range(n_ops):
        url = urls[i % len(urls)]
        s.add(url)
    add_time = time.monotonic() - t0

    t0 = time.monotonic()
    for i in range(n_ops):
        url = urls[i % len(urls)]
        _ = s.contains(url)
    contains_time = time.monotonic() - t0

    t0 = time.monotonic()
    for i in range(n_ops):
        url = f"https://fake-{i}.com/path"
        _ = s.contains(url)
    miss_time = time.monotonic() - t0

    t0 = time.monotonic()
    s.clear()
    clear_time = time.monotonic() - t0

    return {
        "add_unique": add_time,
        "contains_hit": contains_time,
        "contains_miss": miss_time,
        "clear": clear_time,
        "final_len": s.len(),
    }


def run_benchmark(n_urls: int = 1000, n_ops: int = 10000) -> dict:
    print(f"Generating {n_urls} URLs...")
    urls = [f"https://example{i}.com/path/to/resource?param={j}" for i in range(n_urls) for j in range(10)]

    print(f"\nBenchmarking {n_ops:,} operations on {len(urls):,} unique URLs...")
    print("=" * 60)

    py = benchmark_python_set(urls, n_ops)
    print("\nPython set:")
    for k, v in py.items():
        print(f"  {k:20s}: {v*1000:.3f} ms")

    if _RUST_URL_DEDUP_AVAILABLE:
        rs = benchmark_rust_url_set(urls, n_ops)
        print("\nRust UrlSet (FNV-1a):")
        for k, v in rs.items():
            print(f"  {k:20s}: {v*1000:.3f} ms")

        print("\nSpeedup ratio (Python/Rust):")
        for op in ["add_unique", "contains_hit", "contains_miss", "clear"]:
            ratio = py[op] / rs[op]
            print(f"  {op:20s}: {ratio:.2f}x")

        return {"python": py, "rust": rs, "ratio": {op: py[op] / rs[op] for op in py if op != "final_len"}}
    else:
        print("\nRust UrlSet not available — skipping Rust benchmark")
        return {"python": py, "rust": None, "ratio": None}


if __name__ == "__main__":
    from pathlib import Path

    result = run_benchmark()
    print("\n" + "=" * 60)
    print("BENCHMARK COMPLETE")

    # Save results
    results_file = Path(__file__).parent / "RUST_BENCHMARK_RESULTS_2026.md"
    timestamp = "2026-05-26"

    md = f"""# Rust Benchmark Results — {timestamp}

## UrlSet (FNV-1a Hash Dedup)

**Test config**: 1 000 URLs × 10 variations = 10 000 unique URLs, 10 000 operations

| Operation | Python set | Rust UrlSet | Speedup |
|-----------|------------|-------------|---------|
| add_unique | {result['python']['add_unique']*1000:.3f} ms | {result['rust']['add_unique']*1000:.3f} ms | {result['ratio']['add_unique']:.2f}x |
| contains_hit | {result['python']['contains_hit']*1000:.3f} ms | {result['rust']['contains_hit']*1000:.3f} ms | {result['ratio']['contains_hit']:.2f}x |
| contains_miss | {result['python']['contains_miss']*1000:.3f} ms | {result['rust']['contains_miss']*1000:.3f} ms | {result['ratio']['contains_miss']:.2f}x |
| clear | {result['python']['clear']*1000:.3f} ms | {result['rust']['clear']*1000:.3f} ms | {result['ratio']['clear']:.2f}x |

## Notes

- Rust implementation: `rust_extensions/src/url_set.rs` — pure FNV-1a hashing, no external deps
- Python fallback: built-in `set` with full URL strings
- Rust advantage: stores 64-bit hashes instead of full URL strings (memory) + native speed

## Invariants

| Test | Description |
|------|-------------|
| `test_rust_url_set_basic` | add/contains/clear/len — Rust |
| `test_rust_url_set_python_fallback` | ImportError → Python set fallback |
| `test_rust_url_set_api_compliance` | RustUrlSet satisfies DeduplicationStrategy |
"""

    results_file.write_text(md)
    print(f"\nResults saved to {results_file}")
