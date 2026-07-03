# Issue 8.2: pytest-benchmark smoke for top 5 hot-path functions
# M1 8GB: pytest-xdist -n 4 --dist=loadscope is the primary optimization.
# Benchmark thresholds: adjust via HLEDAC_BENCH_* env vars.
# Run: pytest tests/test_benchmark_hotpaths.py -q

import asyncio
import os
import shutil
import tempfile
import time

import lmdb
import pytest

# Baseline thresholds (M1 MacBook Air 8GB, measured 2026-07-02)
# If hardware differs, set env vars to override.
_BASELINES = {
    "duckdb_ingest_batch": float(os.environ.get("HLEDAC_BENCH_BATCH", "45.0")),
    "lmdb_put_many": float(os.environ.get("HLEDAC_BENCH_LMDB", "12.0")),
    "rotating_bloom_add": float(os.environ.get("HLEDAC_BENCH_BLOOM", "0.15")),
    "mx_eval_barrier": float(os.environ.get("HLEDAC_BENCH_MX", "8.0")),
    "fetch_via_curl": float(os.environ.get("HLEDAC_BENCH_FETCH", "85.0")),
}


def _check_regression(name: str, measured_ms: float) -> None:
    """Fail if measurement exceeds baseline by >10%."""
    baseline = _BASELINES.get(name, measured_ms)
    threshold = baseline * 1.10
    if measured_ms > threshold:
        pytest.fail(
            f"BENCHMARK REGRESSION: {name} took {measured_ms:.2f}ms "
            f"(baseline {baseline:.2f}ms, +{((measured_ms/baseline)-1)*100:.1f}%)"
        )


# ---------------------------------------------------------------------------
# DuckDB batch ingest
# ---------------------------------------------------------------------------

def test_benchmark_duckdb_ingest_batch(session_duckdb_store):
    """Time: DuckDBShadowStore.async_ingest_findings_batch (100 findings)."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    if session_duckdb_store is None:
        pytest.skip("DuckDB not available")

    findings = [
        CanonicalFinding(
            finding_id=f"bench_finding_{i}",
            query="benchmark_query",
            source_type="test",
            confidence=0.9,
            ts=time.time(),
            provenance=("benchmark",),
        )
        for i in range(100)
    ]

    loop = getattr(session_duckdb_store, "_loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    t0 = time.perf_counter()
    loop.run_until_complete(session_duckdb_store.async_ingest_findings_batch(findings))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _check_regression("duckdb_ingest_batch", elapsed_ms)


# ---------------------------------------------------------------------------
# LMDB bulk write (put_many)
# ---------------------------------------------------------------------------

def test_benchmark_lmdb_put_many():
    """Time: LMDB cursor.putmany() for 500 key-value pairs."""
    tmp = tempfile.mkdtemp(prefix="bench_lmdb_")
    env = lmdb.open(tmp, map_size=10 * 1024 * 1024, subdir=True)
    data = [(f"key_{i}".encode(), f"value_{i}".encode() * 10) for i in range(500)]

    t0 = time.perf_counter()
    with env.begin(write=True) as txn:
        with txn.cursor() as cur:
            cur.putmulti(data)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    env.close()
    shutil.rmtree(tmp, ignore_errors=True)
    _check_regression("lmdb_put_many", elapsed_ms)


# ---------------------------------------------------------------------------
# RotatingBloomFilter add
# ---------------------------------------------------------------------------

def test_benchmark_rotating_bloom_add():
    """Time: RotatingBloomFilter.add() x100."""
    try:
        from pybloom_live import RotatingBloomFilter
    except ImportError:
        pytest.skip("pybloom-live not available")

    bf = RotatingBloomFilter(capacity=10000, error_rate=0.001)

    t0 = time.perf_counter()
    for i in range(100):
        bf.add(f"https://example.com/item_{i}")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _check_regression("rotating_bloom_add", elapsed_ms)


# ---------------------------------------------------------------------------
# MLX eval barrier
# ---------------------------------------------------------------------------

def test_benchmark_mx_eval_barrier():
    """Time: mx.eval([]) + metal.clear_cache() barrier."""
    try:
        import mlx.core as mx
    except ImportError:
        pytest.skip("MLX not available")

    t0 = time.perf_counter()
    mx.eval([])
    try:
        mx.metal.clear_cache()
    except Exception:
        pass
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _check_regression("mx_eval_barrier", elapsed_ms)


# ---------------------------------------------------------------------------
# curl_cffi fetch
# ---------------------------------------------------------------------------

def test_benchmark_fetch_via_curl():
    """Time: curl_cffi fetch with JA3 fingerprint."""
    if os.environ.get("HLEDAC_ENABLE_CURL_CFFI") != "1":
        pytest.skip("curl_cffi not enabled (HLEDAC_ENABLE_CURL_CFFI=1)")

    try:
        from hledac.universal.transport.curl_cffi_fetch import curl_fetch_bytes
    except ImportError:
        pytest.skip("curl_cffi_fetch not available")

    t0 = time.perf_counter()
    try:
        curl_fetch_bytes("https://example.com", timeout=5.0)
    except Exception:
        pass
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _check_regression("fetch_via_curl", elapsed_ms)
