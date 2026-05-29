#!/usr/bin/env python3
"""Benchmark Rust vs Python implementations for hledac OSINT platform.

Tests:
- AhoCorasick: 10000 patterns vs 1MB text
- BloomFilter: 1M inserts + 1M lookups
- RollingHash: 10MB sliding window

Results saved to benchmark_results/rust_python_comparison.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add hledac to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hledac"))

# Benchmark results directory
RESULTS_DIR = Path(__file__).parent / "benchmark_results"
RESULTS_DIR.mkdir(exist_ok=True)


def benchmark_aho_corasick() -> dict:
    """Benchmark AhoCorasick: Rust vs pyahocorasick."""
    results = {"name": "AhoCorasick", "patterns": 10000, "text_size_mb": 1}

    # Generate 10000 patterns (realistic IOC patterns)
    patterns = [f"pattern_{i}" for i in range(5000)]
    patterns += [f"malware_{i}" for i in range(3000)]
    patterns += ["cve-2024-", "phishing", "suspicious", "credential", "password"]
    # Add some realistic IOC patterns
    patterns += ["github.com/malware/", "raw.githubusercontent.com/", "cdn.jsdelivr.net/"]
    patterns = patterns[:10000]

    # Generate 1MB text with some matches
    text = ("Lorem ipsum dolor sit amet consectetur adipiscing elit " * 2000)[:1_000_000]
    text += " pattern_1234 malware_5678 cve-2024-12345 suspicious_content"

    # Rust benchmark
    try:
        from hledac.universal.rust_extensions import AhoCorasickMatcher

        rust_matcher = AhoCorasickMatcher(patterns)
        start = time.perf_counter()
        for _ in range(10):
            rust_matcher.scan(text)
        rust_time = (time.perf_counter() - start) / 10
        results["rust_time_ms"] = round(rust_time * 1000, 3)
        results["rust_available"] = True
    except ImportError as e:
        results["rust_available"] = False
        results["rust_error"] = str(e)
        results["rust_time_ms"] = None

    # Python fallback benchmark
    try:
        import ahocorasick

        # Build Python automaton
        automaton = ahocorasick.Automaton()
        for i, pattern in enumerate(patterns):
            automaton.add_word(pattern, (i, pattern))
        automaton.make_automaton()

        start = time.perf_counter()
        for _ in range(10):
            list(automaton.iter_longest(text))
        py_time = (time.perf_counter() - start) / 10
        results["python_time_ms"] = round(py_time * 1000, 3)
        results["python_available"] = True
    except ImportError as e:
        results["python_available"] = False
        results["python_error"] = str(e)
        results["python_time_ms"] = None

    # Speedup calculation
    if results.get("rust_time_ms") and results.get("python_time_ms"):
        results["speedup"] = round(results["python_time_ms"] / results["rust_time_ms"], 2)

    return results


def benchmark_bloom_filter() -> dict:
    """Benchmark BloomFilter: Rust vs Python probables."""
    results = {"name": "BloomFilter", "inserts": 1_000_000, "lookups": 1_000_000}

    # Generate test URLs
    urls = [f"https://example{i}.com/path/to/resource" for i in range(1_000_000)]

    # Rust benchmark
    try:
        from hledac.universal.rust_extensions import BloomFilter

        bf = BloomFilter(capacity=1_000_000, fp_rate=0.01)
        start = time.perf_counter()
        for url in urls:
            bf.add(url)
        insert_time = time.perf_counter() - start

        start = time.perf_counter()
        for url in urls:
            _ = bf.contains(url)
        lookup_time = time.perf_counter() - start

        results["rust_insert_ms"] = round(insert_time * 1000, 3)
        results["rust_lookup_ms"] = round(lookup_time * 1000, 3)
        results["rust_available"] = True
    except ImportError as e:
        results["rust_available"] = False
        results["rust_error"] = str(e)
        results["rust_insert_ms"] = None
        results["rust_lookup_ms"] = None

    # Python fallback benchmark (using probables)
    try:
        from probables import BloomFilter as PyBloomFilter

        bf = PyBloomFilter(capacity=1_000_000, error=0.01)
        start = time.perf_counter()
        for url in urls:
            bf.add(url)
        insert_time = time.perf_counter() - start

        start = time.perf_counter()
        for url in urls:
            _ = bf.check(url)
        lookup_time = time.perf_counter() - start

        results["python_insert_ms"] = round(insert_time * 1000, 3)
        results["python_lookup_ms"] = round(lookup_time * 1000, 3)
        results["python_available"] = True
    except ImportError as e:
        results["python_available"] = False
        results["python_error"] = str(e)
        results["python_insert_ms"] = None
        results["python_lookup_ms"] = None

    # Speedup calculation
    if results.get("rust_insert_ms") and results.get("python_insert_ms"):
        results["insert_speedup"] = round(results["python_insert_ms"] / results["rust_insert_ms"], 2)
    if results.get("rust_lookup_ms") and results.get("python_lookup_ms"):
        results["lookup_speedup"] = round(results["python_lookup_ms"] / results["rust_lookup_ms"], 2)

    return results


def benchmark_rolling_hash() -> dict:
    """Benchmark RollingHash: Rust vs Python implementation."""
    results = {"name": "RollingHash", "data_size_mb": 10, "window_size": 8}

    # Generate 10MB of URL-like data
    data = b"https://example" + b".com/path/to/resource/" * 200_000
    data = data[:10_000_000]

    # Rust benchmark
    try:
        from hledac.universal.rust_extensions import RollingHashEngine

        rh = RollingHashEngine(base=256, modulus=2**64, window_size=8)

        # Test hashing
        start = time.perf_counter()
        for _ in range(100):
            hashes = rh.hashes(data)
        hash_time = (time.perf_counter() - start) / 100

        results["rust_hash_ms"] = round(hash_time * 1000, 3)
        results["rust_hash_count"] = len(hashes)
        results["rust_available"] = True
    except ImportError as e:
        results["rust_available"] = False
        results["rust_error"] = str(e)
        results["rust_hash_ms"] = None

    # Python fallback benchmark
    try:
        from hledac.universal.tools.rolling_hash_engine import RollingHashPython

        rh = RollingHashPython(base=256, modulus=2**64)

        start = time.perf_counter()
        for _ in range(100):
            hashes = rh.hashes(data, window_size=8)
        hash_time = (time.perf_counter() - start) / 100

        results["python_hash_ms"] = round(hash_time * 1000, 3)
        results["python_hash_count"] = len(hashes)
        results["python_available"] = True
    except ImportError as e:
        results["python_available"] = False
        results["python_error"] = str(e)
        results["python_hash_ms"] = None

    # Speedup calculation
    if results.get("rust_hash_ms") and results.get("python_hash_ms"):
        results["speedup"] = round(results["python_hash_ms"] / results["rust_hash_ms"], 2)

    return results


def run_all_benchmarks() -> dict:
    """Run all benchmarks and save results."""
    print("=" * 60)
    print("Rust vs Python Benchmark for hledac OSINT platform")
    print("=" * 60)

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": "darwin" if sys.platform == "darwin" else sys.platform,
        "benchmarks": {},
    }

    print("\n[1/3] AhoCorasick benchmark...")
    results["benchmarks"]["aho_corasick"] = benchmark_aho_corasick()
    ac = results["benchmarks"]["aho_corasick"]
    print(f"  Rust: {ac.get('rust_time_ms', 'N/A')} ms")
    print(f"  Python: {ac.get('python_time_ms', 'N/A')} ms")
    if ac.get("speedup"):
        print(f"  Speedup: {ac['speedup']}x")

    print("\n[2/3] BloomFilter benchmark...")
    results["benchmarks"]["bloom_filter"] = benchmark_bloom_filter()
    bf = results["benchmarks"]["bloom_filter"]
    print(f"  Rust insert: {bf.get('rust_insert_ms', 'N/A')} ms")
    print(f"  Python insert: {bf.get('python_insert_ms', 'N/A')} ms")
    if bf.get("insert_speedup"):
        print(f"  Insert speedup: {bf['insert_speedup']}x")
    if bf.get("lookup_speedup"):
        print(f"  Lookup speedup: {bf['lookup_speedup']}x")

    print("\n[3/3] RollingHash benchmark...")
    results["benchmarks"]["rolling_hash"] = benchmark_rolling_hash()
    rh = results["benchmarks"]["rolling_hash"]
    print(f"  Rust: {rh.get('rust_hash_ms', 'N/A')} ms")
    print(f"  Python: {rh.get('python_hash_ms', 'N/A')} ms")
    if rh.get("speedup"):
        print(f"  Speedup: {rh['speedup']}x")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_speedup = 0
    count = 0
    for name, data in results["benchmarks"].items():
        if "speedup" in data and data["speedup"]:
            print(f"  {name}: {data['speedup']}x faster")
            total_speedup += data["speedup"]
            count += 1
        elif name == "bloom_filter":
            if data.get("insert_speedup"):
                print(f"  {name} insert: {data['insert_speedup']}x faster")
                total_speedup += data["insert_speedup"]
                count += 1
            if data.get("lookup_speedup"):
                print(f"  {name} lookup: {data['lookup_speedup']}x faster")
                total_speedup += data["lookup_speedup"]
                count += 1

    if count > 0:
        results["average_speedup"] = round(total_speedup / count, 2)
        print(f"\nAverage speedup: {results['average_speedup']}x")

    # Save results
    output_file = RESULTS_DIR / "rust_python_comparison.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

    return results


if __name__ == "__main__":
    run_all_benchmarks()
