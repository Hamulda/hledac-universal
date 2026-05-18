#!/usr/bin/env python3
"""
F226 — Python 3.14 / M1 Benchmark Gates
=======================================

ROLE: Measure-only instrument for Python 3.14 + M1 safe experiments.
NO runtime changes. NO production code paths modified.

Benchmarks:
  1. body_limiter throughput         (transport/body_limiter.py)
  2. selectolax vs bs4 characterization (utils/html_text_fast.py)
  3. msgspec DTO serialization       (msgspec, shadow_dtos)
  4. WALManager single write smoke   (knowledge/wal.py)
  5. BatchScheduler queue flush smoke (brain/batch_scheduler.py)

Measurements:
  - wall time (time.perf_counter)
  - peak RSS via psutil (where available)
  - Python version / platform
  - free-threaded / JIT flag detection

Output: JSONL → reports/benchmarks/bench_m1_runtime_gates_<ts>.jsonl
Forbidden: network, browser, OCR, model load, live DB destructive writes

Run:
    PYTHONPATH=hledac/universal python tools/bench_m1_runtime_gates.py [--quick]

Exit codes:
    0  = complete (results in JSONL file)
    64 = no psutil (continues, reports wall-time only)
    65 = benchmark error
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import resource
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

# ── paths ────────────────────────────────────────────────────────────────────────
BENCH_FILE = Path(__file__).resolve()
UNIVERSAL_ROOT = BENCH_FILE.parents[1]
REPORTS_DIR = UNIVERSAL_ROOT / "reports" / "benchmarks"

assert UNIVERSAL_ROOT.name == "universal", f"UNIVERSAL_ROOT={UNIVERSAL_ROOT}"

sys.path.insert(0, str(UNIVERSAL_ROOT))

# ── optional deps ────────────────────────────────────────────────────────────
_HAS_PSUTIL = False
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    pass

_HAS_SELECTOLAX = False
try:
    from selectolax.parser import HTMLParser as _SelectoLaxParser  # noqa: F401
    _HAS_SELECTOLAX = True
except ImportError:
    pass

_HAS_BS4 = False
try:
    from bs4 import BeautifulSoup as _BS4Parser  # noqa: F401
    _HAS_BS4 = True
except ImportError:
    pass

# ── platform / interpreter detection ─────────────────────────────────────────


def _detect_interpreter_flags() -> dict[str, Any]:
    """Detect free-threaded / JIT flags without importing heavy modules."""
    flags: dict[str, Any] = {
        "free_threaded": False,
        "free_threaded_reason": "N/A",
        "jit_available": False,
        "jit_reason": "N/A",
        "jit_active": False,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "executable": sys.executable,
    }

    # Free-threaded (PEP 703) — check _is_gil_disabled
    try:
        is_gil_disabled_fn = getattr(sys, "_is_gil_disabled", None)
        if is_gil_disabled_fn is not None:
            val = is_gil_disabled_fn()
            flags["free_threaded"] = bool(val)
            flags["free_threaded_reason"] = (
                "GIL disabled (free-threaded build)"
                if val
                else "GIL enabled (standard build)"
            )
        else:
            flags["free_threaded_reason"] = "attribute _is_gil_disabled not present"
    except AttributeError:
        flags["free_threaded_reason"] = "attribute _is_gil_disabled not present"
    except Exception as e:
        flags["free_threaded_reason"] = f"check failed: {e}"

    # JIT (PEP 744) — check sys.jit attribute
    try:
        jit_attr = getattr(sys, "jit", None)
        flags["jit_available"] = jit_attr is not None
        if jit_attr is not None:
            flags["jit_active"] = bool(jit_attr)
            flags["jit_reason"] = f"sys.jit={jit_attr}"
        else:
            flags["jit_reason"] = "sys.jit attribute not present (Python built without --with-jit)"
    except AttributeError:
        flags["jit_reason"] = "sys.jit attribute not present"
    except Exception as e:
        flags["jit_reason"] = f"JIT detection failed: {e}"

    return flags


# ── RSS helpers ───────────────────────────────────────────────────────────────


def _rss_kb() -> int:
    """Peak RSS in KB via resource.getrusage."""
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS: bytes; Linux: KiB
        if sys.platform == "darwin":
            return rss // 1024
        return rss
    except Exception:
        return 0


def _rss_psutil() -> float | None:
    """Current RSS in MiB via psutil (or None)."""
    if not _HAS_PSUTIL:
        return None
    try:
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        return None


# ── timing helper ──────────────────────────────────────────────────────────────


def _time_it(fn, *, runs: int = 7, warmups: int = 2) -> dict[str, Any]:
    """
    Run fn `runs` times (after `warmups` uncounted warm-ups).
    Returns {wall_s, samples_ms, summary, status}.
    """
    samples: list[float] = []

    def _invoke() -> None:
        fn()

    for _ in range(warmups):
        try:
            gc.collect()
            _invoke()
        except Exception:
            pass

    for _ in range(runs):
        try:
            gc.collect()
            t0 = time.perf_counter()
            _invoke()
            t1 = time.perf_counter()
            samples.append((t1 - t0) * 1000.0)
        except Exception as exc:
            return {"status": "fail", "error": str(exc), "wall_s": 0.0, "samples_ms": [], "summary": {}}

    if not samples:
        return {"status": "fail", "wall_s": 0.0, "samples_ms": [], "summary": {}}

    sorted_s = sorted(samples)
    n = len(sorted_s)
    return {
        "status": "ok",
        "wall_s": round(sum(samples) / n / 1000.0, 6),
        "samples_ms": [round(s, 4) for s in samples],
        "summary": {
            "min_ms": round(sorted_s[0], 4),
            "median_ms": round(sorted_s[n // 2], 4),
            "mean_ms": round(sum(sorted_s) / n, 4),
            "p95_ms": round(sorted_s[max(0, int(n * 0.95) - 1)], 4),
            "max_ms": round(sorted_s[-1], 4),
            "runs": n,
        },
    }


async def _time_it_async(fn, *, runs: int = 7, warmups: int = 2) -> dict[str, Any]:
    """Async version of _time_it."""
    samples: list[float] = []

    for _ in range(warmups):
        try:
            gc.collect()
            await fn()
        except Exception:
            pass

    for _ in range(runs):
        try:
            gc.collect()
            t0 = time.perf_counter()
            await fn()
            t1 = time.perf_counter()
            samples.append((t1 - t0) * 1000.0)
        except Exception as exc:
            return {"status": "fail", "error": str(exc), "wall_s": 0.0, "samples_ms": [], "summary": {}}

    if not samples:
        return {"status": "fail", "wall_s": 0.0, "samples_ms": [], "summary": {}}

    sorted_s = sorted(samples)
    n = len(sorted_s)
    return {
        "status": "ok",
        "wall_s": round(sum(samples) / n / 1000.0, 6),
        "samples_ms": [round(s, 4) for s in samples],
        "summary": {
            "min_ms": round(sorted_s[0], 4),
            "median_ms": round(sorted_s[n // 2], 4),
            "mean_ms": round(sum(samples) / n, 4),
            "p95_ms": round(sorted_s[max(0, int(n * 0.95) - 1)], 4),
            "max_ms": round(sorted_s[-1], 4),
            "runs": n,
        },
    }


# ── benchmark 1: body_limiter throughput ──────────────────────────────────────


def bench_body_limiter_throughput() -> dict[str, Any]:
    """
    Measure read_body_with_cap throughput.

    Fixture: synthetic async chunk stream (100× 1KB chunks).
    NO network, NO browser, NO OCR, NO model load.
    """
    from transport.body_limiter import read_body_with_cap

    TOTAL_BYTES = 100 * 1024  # 100 KB
    CHUNK_SIZE = 1024
    N_CHUNKS = TOTAL_BYTES // CHUNK_SIZE

    async def chunk_stream() -> AsyncIterator[bytes]:
        for _ in range(N_CHUNKS):
            yield b"x" * CHUNK_SIZE

    async def one_read() -> tuple[bytes, bool]:
        chunks = chunk_stream()
        return await read_body_with_cap(chunks, max_bytes=TOTAL_BYTES)

    # Warm
    asyncio.run(one_read())

    # Measure
    samples: list[float] = []
    for _ in range(7):
        gc.collect()
        t0 = time.perf_counter()
        asyncio.run(one_read())
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1000.0)

    sorted_s = sorted(samples)
    n = len(sorted_s)
    median_ms = sorted_s[n // 2]
    throughput_mb_s = (TOTAL_BYTES / 1024 / 1024) / (median_ms / 1000.0)

    return {
        "name": "body_limiter_throughput",
        "status": "ok",
        "wall_s": round(sum(samples) / n / 1000.0, 6),
        "samples_ms": [round(s, 4) for s in samples],
        "summary": {
            "min_ms": round(sorted_s[0], 4),
            "median_ms": round(median_ms, 4),
            "mean_ms": round(sum(sorted_s) / n, 4),
            "p95_ms": round(sorted_s[max(0, int(n * 0.95) - 1)], 4),
            "max_ms": round(sorted_s[-1], 4),
            "runs": n,
        },
        "throughput_mb_s": round(throughput_mb_s, 3),
        "fixture": {"total_bytes": TOTAL_BYTES, "chunk_size": CHUNK_SIZE, "n_chunks": N_CHUNKS},
    }


# ── benchmark 2: selectolax vs bs4 characterization ───────────────────────────


def bench_html_parser_characterization() -> dict[str, Any]:
    """
    Characterize selectolax vs bs4 on a fixed HTML fixture.

    Fixture: mixed real-world HTML (title, links, paragraphs).
    Measures parse time only — NO network, NO browser, NO OCR.

    Returns both selectolax and bs4 results (when available) so the
    characterization can be used for migration validation.
    """
    # Fixed HTML fixture — realistic mixed content
    HTML_FIXTURE = """<!DOCTYPE html>
<html lang="en">
<head><title>Test Page — Example Domain</title></head>
<body>
<h1>Welcome to Example</h1>
<p>This is a test paragraph with <a href="https://example.com/link1">link one</a>
   and <a href="https://example.com/link2">link two</a>.</p>
<p>Second paragraph with <a href="/relative/path">relative link</a>.</p>
<div class="nav"><a href="#section">anchor</a></div>
</body>
</html>"""

    result: dict[str, Any] = {
        "name": "html_parser_characterization",
        "status": "ok",
        "selectolax": None,
        "bs4": None,
        "fixture": {"html_size_bytes": len(HTML_FIXTURE.encode())},
    }

    # selectolax
    if _HAS_SELECTOLAX:
        from utils.html_text_fast import html_to_text_fast

        def sel_parse() -> str:
            return html_to_text_fast(HTML_FIXTURE)

        t_result = _time_it(sel_parse, runs=7, warmups=3)
        result["selectolax"] = t_result

    # bs4 (html.parser — slowest but always available)
    if _HAS_BS4:
        def bs4_parse() -> str:
            soup = _BS4Parser(HTML_FIXTURE, "html.parser")
            return soup.get_text(separator=" ", strip=True)

        t_result = _time_it(bs4_parse, runs=7, warmups=3)
        result["bs4"] = t_result

    if not _HAS_SELECTOLAX and not _HAS_BS4:
        result["status"] = "skip"
        result["error"] = "neither selectolax nor bs4 available"

    return result


# ── benchmark 3: msgspec DTO serialization ──────────────────────────────────────


def bench_msgspec_dto_serialization() -> dict[str, Any]:
    """
    Measure msgspec encode/decode throughput for a CanonicalFinding-like DTO.

    NO live DB writes. Uses msgspec.Convert (lightweight Struct).
    """
    try:
        import msgspec
    except ImportError:
        return {"name": "msgspec_dto_serialization", "status": "skip", "error": "msgspec not available"}

    # Struct mirroring CanonicalFinding fields (lightweight)
    class FindingStruct(msgspec.Struct):
        finding_id: str
        source_type: str
        query: str
        confidence: float
        payload_text: str
        timestamp: str

    FIXTURE_DATA = {
        "finding_id": "test-finding-00001",
        "source_type": "test_source",
        "query": "example domain investigation",
        "confidence": 0.85,
        "payload_text": "Lorem ipsum dolor sit amet consectetur adipiscing elit. "
        * 10,
        "timestamp": "2026-05-18T00:00:00Z",
    }

    instance = FindingStruct(**FIXTURE_DATA)

    def encode_fn() -> bytes:
        return msgspec.encode(instance)

    def decode_fn(data: bytes) -> FindingStruct:
        return msgspec.decode(data, type=FindingStruct)

    enc_result = _time_it(encode_fn, runs=7, warmups=3)
    encoded_bytes = msgspec.encode(instance)
    dec_result = _time_it(lambda: decode_fn(encoded_bytes), runs=7, warmups=3)

    return {
        "name": "msgspec_dto_serialization",
        "status": "ok",
        "encode": enc_result,
        "decode": dec_result,
        "payload_bytes": len(encoded_bytes),
        "fixture_fields": list(FIXTURE_DATA.keys()),
    }


# ── benchmark 4: WALManager single write smoke ────────────────────────────────


def bench_wal_manager_single_write_smoke() -> dict[str, Any]:
    """
    Smoke test: WALManager.wal_write_finding() × 1 in a temp LMDB env.

    NO live DuckDB writes. Uses tempfile for LMDB path.
    Skips if imports fail due to missing deps (aiohttp, etc.).
    """
    try:
        from knowledge.wal import WALManager
    except ImportError as e:
        return {
            "name": "wal_manager_single_write_smoke",
            "status": "skip",
            "error": f"import failed: {e}",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = os.path.join(tmpdir, "wal_test.lmdb")
        manager = WALManager(wal_path=wal_path, map_size=4 * 1024 * 1024)
        manager.initialize()

        def one_write() -> bool:
            return manager.wal_write_finding(
                finding_id="bench-f226-001",
                query="bench_query",
                source_type="bench_source",
                confidence=0.75,
            )

        t_result = _time_it(one_write, runs=7, warmups=2)

        manager.close()

        return {
            "name": "wal_manager_single_write_smoke",
            "status": "ok" if t_result["status"] == "ok" else "fail",
            "timing": t_result,
            "fixture": {"finding_id": "bench-f226-001", "wal_path": wal_path},
        }


# ── benchmark 5: BatchScheduler queue flush smoke ────────────────────────────


def bench_batch_scheduler_queue_flush_smoke() -> dict[str, Any]:
    """
    Smoke test: BatchScheduler flush with 1 item in queue.

    NO MLX, NO model load. Mock execute callback (no-op async).
    """
    from brain.batch_scheduler import BatchScheduler

    execution_log: list[dict] = []

    async def mock_execute(payload: dict) -> dict:
        execution_log.append(payload)
        await asyncio.sleep(0)  # yield once
        return {"ok": True, "payload": payload}

    scheduler = BatchScheduler(
        execute_callback=mock_execute,
        max_size=8,
        max_queue=256,
        default_flush_interval=2.0,
    )

    async def run_flush() -> int:
        await scheduler.start()
        await asyncio.sleep(0.05)  # let worker spin up
        # Submit 1 item then drain
        future = await scheduler.submit(
            prompt="test prompt",
            response_model=str,  # simplest schema key
            priority=1.0,
        )
        # Consume future result to suppress "never retrieved" warnings
        future.add_done_callback(lambda f: None)
        await asyncio.sleep(0.05)
        drained = await scheduler.flush(timeout=2.0)
        await scheduler.shutdown(timeout=1.0)
        return drained

    # Warm
    asyncio.run(run_flush())
    execution_log.clear()

    # Measure
    t_result = asyncio.run(_time_it_async(run_flush, runs=7, warmups=2))

    return {
        "name": "batch_scheduler_queue_flush_smoke",
        "status": "ok" if t_result["status"] == "ok" else "fail",
        "timing": t_result,
        "fixture": {"queue_depth": 1, "flush_timeout_s": 2.0},
    }


# ── output ───────────────────────────────────────────────────────────────────


def _write_jsonl(record: dict[str, Any], path: Path) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_record(
    name: str,
    bench_result: dict[str, Any],
    interpreter_flags: dict[str, Any],
    rss_start_kb: int,
    rss_psutil_start: float | None,
    quick: bool,
) -> dict[str, Any]:
    """Build a JSONL record with metadata header."""
    return {
        # metadata
        "type": "benchmark_record",
        "name": name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python_version": interpreter_flags["python_version"],
        "platform": interpreter_flags["platform"],
        "free_threaded": interpreter_flags["free_threaded"],
        "jit_available": interpreter_flags["jit_available"],
        "jit_active": interpreter_flags["jit_active"],
        "rss_start_kb": rss_start_kb,
        "rss_psutil_start_mib": rss_psutil_start,
        "has_psutil": _HAS_PSUTIL,
        "has_selectolax": _HAS_SELECTOLAX,
        "has_bs4": _HAS_BS4,
        "quick": quick,
        # benchmark result
        "result": bench_result,
    }


# ── main ──────────────────────────────────────────────────────────────────────


BENCHMARKS: list[tuple[str, callable]] = [
    ("body_limiter_throughput", bench_body_limiter_throughput),
    ("html_parser_characterization", bench_html_parser_characterization),
    ("msgspec_dto_serialization", bench_msgspec_dto_serialization),
    ("wal_manager_single_write_smoke", bench_wal_manager_single_write_smoke),
    ("batch_scheduler_queue_flush_smoke", bench_batch_scheduler_queue_flush_smoke),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="F226 — M1 Python 3.14 Benchmark Gates")
    parser.add_argument("--quick", action="store_true", help="Fewer iterations")
    args = parser.parse_args()

    # Ensure output dir
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"bench_m1_runtime_gates_{ts}.jsonl"

    interpreter_flags = _detect_interpreter_flags()

    print("=" * 60)
    print("  F226 — M1 Python 3.14 Benchmark Gates")
    print(f"  Output: {out_path}")
    print("=" * 60)
    print(f"  Python: {interpreter_flags['python_version']}")
    print(f"  Platform: {interpreter_flags['platform']}")
    print(f"  Executable: {interpreter_flags['executable']}")
    print(f"  Free-threaded: {interpreter_flags['free_threaded']} — {interpreter_flags['free_threaded_reason']}")
    print(f"  JIT available: {interpreter_flags['jit_available']} — {interpreter_flags['jit_reason']}")
    print(f"  psutil: {_HAS_PSUTIL}")
    print(f"  selectolax: {_HAS_SELECTOLAX}")
    print(f"  bs4: {_HAS_BS4}")
    print()

    rss_start_kb = _rss_kb()
    rss_psutil_start = _rss_psutil()
    errors = 0

    for name, bench_fn in BENCHMARKS:
        print(f"  Running {name} ...", end=" ", flush=True)
        try:
            gc.collect()
            result = bench_fn()
            record = _build_record(
                name=name,
                bench_result=result,
                interpreter_flags=interpreter_flags,
                rss_start_kb=rss_start_kb,
                rss_psutil_start=rss_psutil_start,
                quick=args.quick,
            )
            _write_jsonl(record, out_path)
            status = result.get("status", "unknown")
            print(status)
            if status == "fail":
                errors += 1
        except Exception as exc:
            print(f"ERROR {exc}")
            errors += 1
            record = _build_record(
                name=name,
                bench_result={"status": "fail", "error": str(exc)},
                interpreter_flags=interpreter_flags,
                rss_start_kb=rss_start_kb,
                rss_psutil_start=rss_psutil_start,
                quick=args.quick,
            )
            _write_jsonl(record, out_path)

    rss_end_kb = _rss_kb()
    rss_psutil_end = _rss_psutil()

    print()
    print(f"  Results → {out_path}")
    print(f"  RSS start={rss_start_kb}KB  end={rss_end_kb}KB")
    if _HAS_PSUTIL:
        print(f"  psutil RSS start={rss_psutil_start:.2f}MiB  end={rss_psutil_end:.2f}MiB")

    if errors:
        print(f"\n  {errors} benchmark(s) failed — see JSONL for details")
        return 65
    return 0


if __name__ == "__main__":
    sys.exit(main())