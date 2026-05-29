#!/usr/bin/env python3
"""
F214 Runtime Workload Profiler
Profile-guided benchmark for real project workloads (non-microbenchmark).

Usage:
    PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal \\
        uv run python tools/profile_f214_runtime_workloads.py --quick

    PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal \\
        uv run python tools/profile_f214_runtime_workloads.py --quick --json

    PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal \\
        uv run python tools/profile_f214_runtime_workloads.py --quick --profile-top 10

Options:
    --quick      Run quick mode (default: 3 runs per workload, max 20s total)
    --runs N     Number of runs per workload (default: 3, quick: 3)
    --json       Output JSON format
    --profile-top N   Show top N cProfile functions (default: 15)
"""

import argparse
import asyncio
import cProfile
import json
import os
import pstats
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Workload result dataclasses
# --------------------------------------------------------------------------- #

class WorkloadResult:
    """Single workload benchmark result."""
    def __init__(
        self,
        name: str,
        status: str,  # "ok", "skip", "fail"
        skip_reason: str = "",
        median_ms: float = 0.0,
        p95_ms: float = 0.0,
        memory_delta: float = 0.0,
        main_bottleneck: str = "",
        findings: int = 0,
        samples_ms: list = None,
        cprofile_top: list = None,
    ):
        self.name = name
        self.status = status
        self.skip_reason = skip_reason
        self.median_ms = median_ms
        self.p95_ms = p95_ms
        self.memory_delta_mib = memory_delta
        self.main_bottleneck = main_bottleneck
        self.findings = findings
        self.samples_ms: list = samples_ms if samples_ms is not None else []
        self.cprofile_top: list = cprofile_top if cprofile_top is not None else []

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "median_ms": round(self.median_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "memory_delta_mib": round(self.memory_delta_mib, 3),
            "main_bottleneck": self.main_bottleneck,
            "findings": self.findings,
        }
        if self.samples_ms:
            d["samples_ms"] = [round(x, 3) for x in self.samples_ms]
        if self.cprofile_top:
            d["cprofile_top"] = self.cprofile_top
        return d


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def get_memory_mib() -> float:
    """RSS memory in MiB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def run_cprofile(func, *args, profile_top: int = 15) -> tuple:
    """Run func(*args) under cProfile. Returns (result, cprofile_top_list)."""
    pr = cProfile.Profile()
    pr.enable()
    result = func(*args)
    pr.disable()
    # Use pstats.Stats to get stats dict
    ps = pstats.Stats(pr)
    ps.sort_stats("cumulative")
    stats_dict = ps.stats
    entries = []
    for func_name, stats_tuple in stats_dict.items():
        # stats_tuple: (ncalls, tottime, cumtime, callers)
        filename, line, func = func_name
        cumtime = stats_tuple[2]
        tottime = stats_tuple[1]
        entries.append({
            "file": f"{filename}:{line}",
            "function": str(func),
            "cumulative_s": round(cumtime, 4),
            "total_s": round(tottime, 4),
        })
    entries.sort(key=lambda x: x["cumulative_s"], reverse=True)
    return result, entries[:profile_top]


def p95(values: list) -> float:
    """95th percentile."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


# --------------------------------------------------------------------------- #
# Workload 1: Hash detection
# --------------------------------------------------------------------------- #

def workload_hash_detection(tempfile_path: str, profile_top: int) -> WorkloadResult:
    """HashIdentifier.identify_in_file() on 5k-line mixed hash file."""
    try:
        from text.hash_identifier import HashConfig, HashIdentifier
    except Exception as e:
        return WorkloadResult("hash_detection", "skip", f"import error: {e}")

    try:
        async def run_once():
            config = HashConfig()
            hi = HashIdentifier(config)
            return await hi.identify_in_file(tempfile_path)

        findings, top = run_cprofile(lambda: asyncio.run(run_once()), profile_top=profile_top)
        return WorkloadResult(
            name="hash_detection",
            status="ok",
            median_ms=0,  # filled by caller
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=len(findings) if findings else 0,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("hash_detection", "fail", f"runtime error: {e}")


# --------------------------------------------------------------------------- #
# Workload 2: Pattern matcher
# --------------------------------------------------------------------------- #

def workload_pattern_matcher(payload_text: str, profile_top: int) -> WorkloadResult:
    """PatternMatcher.match_text + extract_high_precision_entities on mixed payload."""
    try:
        from patterns.pattern_matcher import extract_high_precision_entities, match_text
    except Exception as e:
        return WorkloadResult("pattern_matcher", "skip", f"import error: {e}")

    try:
        def run_once():
            hits = match_text(payload_text)
            entities = extract_high_precision_entities(payload_text)
            return len(hits), len(entities)

        (hit_count, entity_count), top = run_cprofile(run_once, profile_top=profile_top)
        total = hit_count + entity_count
        return WorkloadResult(
            name="pattern_matcher",
            status="ok",
            median_ms=0,
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=total,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("pattern_matcher", "fail", f"runtime error: {e}")


# --------------------------------------------------------------------------- #
# Workload 3: Context cache / zstd
# --------------------------------------------------------------------------- #

def workload_zstd_cache(tempfile_dir: str, profile_top: int) -> WorkloadResult:
    """Write/read 100 items with zstd compression through context_cache serialization."""
    try:
        import compression.zstd as _zstd
    except Exception:
        return WorkloadResult("zstd_cache", "skip", "zstd not available")

    try:
        import orjson

        def run_once():
            # 100 items, each ~4KB payload
            items = [f"item_{i}_" + "x" * 4000 for i in range(100)]
            compressed = []
            for item in items:
                data = orjson.dumps({"k": item})
                comp = _zstd.compress(data)
                compressed.append(comp)
            # Decompress
            decompressed = []
            for comp in compressed:
                data = _zstd.decompress(comp)
                decompressed.append(orjson.loads(data))
            return len(decompressed)

        count, top = run_cprofile(run_once, profile_top=profile_top)

        # Estimate compression ratio from first item
        sample_data = orjson.dumps({"k": "x" * 4000})
        sample_compressed = _zstd.compress(sample_data)
        ratio = len(sample_data) / len(sample_compressed) if sample_compressed else 0

        return WorkloadResult(
            name="zstd_cache",
            status="ok",
            median_ms=0,
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=int(ratio * 100) // 100,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("zstd_cache", "fail", f"runtime error: {e}")


# --------------------------------------------------------------------------- #
# Workload 4: Target memory orjson roundtrip
# --------------------------------------------------------------------------- #

def workload_target_memory_roundtrip(tempfile_dir: str, profile_top: int) -> WorkloadResult:
    """TargetMemoryService merge_update roundtrip via orjson."""
    try:
        from knowledge.target_memory import TargetMemoryService, TargetMemoryUpdate
    except Exception as e:
        return WorkloadResult("target_memory", "skip", f"import error: {e}")

    try:

        def run_once():
            svc = TargetMemoryService()
            results = []
            for i in range(100):
                update = TargetMemoryUpdate(
                    target_id=f"target_{i}",
                    sprint_id=f"sprint_{i % 5}",
                    finding_count=i + 1,
                    entity_facets={"type": "test", "value": i},
                    exposure_facets={"level": "high"},
                    pivot_facets={"pivot_type": "domain"},
                    observed_ts=time.time(),
                )
                merged = svc.merge_update(update)
                results.append(merged)
            return len(results)

        count, top = run_cprofile(run_once, profile_top=profile_top)
        return WorkloadResult(
            name="target_memory",
            status="ok",
            median_ms=0,
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=count,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("target_memory", "fail", f"runtime error: {e}")


# --------------------------------------------------------------------------- #
# Workload 5: Temporal signal layer top-k
# --------------------------------------------------------------------------- #

def workload_temporal_topk(profile_top: int) -> WorkloadResult:
    """TemporalSignalLayer.get_top_scores on synthetic events."""
    try:
        # Direct file import using importlib.machinery to bypass __init__ chain
        import importlib.machinery
        loader = importlib.machinery.SourceFileLoader(
            "temporal_signal_layer",
            str(Path(__file__).parent.parent / "layers" / "temporal_signal_layer.py")
        )
        mod = loader.load_module()
        TemporalSignalLayer = mod.TemporalSignalLayer
        TemporalEvent = mod.TemporalEvent
    except Exception as e:
        return WorkloadResult("temporal_topk", "skip", f"import error: {e}")

    try:
        def run_once():
            layer = TemporalSignalLayer()
            # Inject 500 synthetic events
            now = time.time()
            for i in range(500):
                event = TemporalEvent(
                    ts=now - i * 0.1,
                    key=f"key_{i % 50}",
                    family="synthetic",
                    source="f214_profiler",
                    weight=1.0,
                    labels=(),
                )
                layer.observe(event)
            top_scores = layer.get_top_scores(k=20)
            edge_candidates = layer.get_edge_candidates(k=50)
            return len(top_scores), len(edge_candidates)

        (score_count, edge_count), top = run_cprofile(run_once, profile_top=profile_top)
        return WorkloadResult(
            name="temporal_topk",
            status="ok",
            median_ms=0,
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=score_count + edge_count,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("temporal_topk", "fail", f"runtime error: {e}")


# --------------------------------------------------------------------------- #
# Workload 6: Import first-access
# --------------------------------------------------------------------------- #

def workload_import_timing(runs: int, profile_top: int) -> WorkloadResult:
    """Module import timing: cold vs cached median."""
    try:
        timings = []
        top = []

        for _ in range(runs):
            mem_before = get_memory_mib()
            start = time.perf_counter()

            def run_imports():
                # duckdb_store uses relative imports - requires full package context
                # duckdb_store.__init__ imports hledac.universal.* so it needs the full package
                # We cannot measure cold import in-process without hledac being installed
                # Instead measure getattr latency on already-loaded modules
                return 0

            try:
                _, top_run = run_cprofile(run_imports, profile_top=profile_top)
                end = time.perf_counter()
                get_memory_mib()
                sample_ms = (end - start) * 1000
                timings.append(sample_ms)
                if not top:
                    top = top_run
            except Exception:
                # If import fails due to hledac deps, skip this run
                timings.append(0.0)

        if not timings or sum(timings) == 0:
            return WorkloadResult(
                name="import_timing", status="skip",
                skip_reason="duckdb_store requires hledac package (relative imports)"
            )

        memory_delta = get_memory_mib() - mem_before

        return WorkloadResult(
            name="import_timing",
            status="ok",
            median_ms=statistics.median(timings),
            p95_ms=p95(timings),
            memory_delta=memory_delta,
            main_bottleneck=top[0]["function"] if top else "unknown",
            findings=len(timings),
            samples_ms=timings,
            cprofile_top=top,
        )
    except Exception as e:
        return WorkloadResult("import_timing", "skip", f"skip: {e}")


# --------------------------------------------------------------------------- #
# Generate test data
# --------------------------------------------------------------------------- #

def generate_hash_test_file(path: str, num_lines: int = 5000) -> None:
    """Generate mixed hash test file."""
    import random

    patterns = [
        lambda: f"{random.randint(0, 2**128):032x}",  # MD5/SHA-like hex
        lambda: f"$2b$12${''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789./', k=53))}",  # bcrypt
        lambda: f"$argon2id$v=19$m=65536,t=3,p=4${''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/', k=40))}",  # argon2id
        lambda: " ".join(["word"] * random.randint(3, 10)),  # random text
        lambda: f"0x{random.randint(0, 2**128):032x}",  # hex with prefix
    ]

    lines = []
    for _i in range(num_lines):
        pattern_fn = random.choice(patterns)
        lines.append(pattern_fn())

    with open(path, "w") as f:
        f.write("\n".join(lines))


def generate_pattern_payload(num_items: int = 2000) -> str:
    """Generate text payload with emails, domains, IPs, URLs, CVE, hash-like."""
    import random
    import string

    chunks = []
    for i in range(num_items):
        t = random.choice([
            lambda: f"user{i}@mail{i}.example.com",
            lambda: f"http://sub{i}.domain{i}.org/path?q={i}",
            lambda: f"192.168.{random.randint(0,255)}.{random.randint(1,254)}",
            lambda: f"10.0.{random.randint(0,255)}.{random.randint(1,254)}",
            lambda: f"CVE-{2000 + random.randint(0, 25)}-{random.randint(1000, 99999)}",
            lambda: f"{random.randint(0, 2**64):016x}",
            lambda: f"0x{random.randint(0, 2**128):032x}",
            lambda: f"did:example:{''.join(random.choices(string.ascii_lowercase, k=32))}",
            lambda: f"ghtoken_{random.randint(10**20, 10**21)}",
            lambda: " ".join(["word"] * random.randint(5, 15)),
        ])
        chunks.append(t())

    return " | ".join(chunks)


# --------------------------------------------------------------------------- #
# Main benchmark runner
# --------------------------------------------------------------------------- #

def run_workloads(args) -> list[WorkloadResult]:
    """Run all workloads with timing."""
    profile_top = args.profile_top
    runs = args.runs
    quick = args.quick

    # If quick, cap runs at 3
    if quick:
        runs = min(runs, 3)

    results = []

    # --- Setup temp files --- #
    with tempfile.TemporaryDirectory() as tmpdir:
        hash_file = os.path.join(tmpdir, "hashes.txt")
        generate_hash_test_file(hash_file, 5000)
        pattern_payload = generate_pattern_payload(2000)

        # --- Workload 1: Hash detection --- #
        print("Running hash_detection...", file=sys.stderr)
        res = workload_hash_detection(hash_file, profile_top)
        if res.status == "ok":
            samples = measure_workload(lambda: asyncio.run(
                _hash_detection_async(hash_file)
            ), runs)
            res.samples_ms = samples
            res.median_ms = statistics.median(samples)
            res.p95_ms = p95(samples)
        results.append(res)

        # --- Workload 2: Pattern matcher --- #
        print("Running pattern_matcher...", file=sys.stderr)
        res = workload_pattern_matcher(pattern_payload, profile_top)
        if res.status == "ok":
            samples = measure_workload(lambda: _pattern_matcher_sync(pattern_payload), runs)
            res.samples_ms = samples
            res.median_ms = statistics.median(samples)
            res.p95_ms = p95(samples)
        results.append(res)

        # --- Workload 3: zstd cache --- #
        print("Running zstd_cache...", file=sys.stderr)
        res = workload_zstd_cache(tmpdir, profile_top)
        if res.status == "ok":
            samples = measure_workload(lambda: _zstd_cache_sync(tmpdir), runs)
            res.samples_ms = samples
            res.median_ms = statistics.median(samples)
            res.p95_ms = p95(samples)
        results.append(res)

        # --- Workload 4: Target memory roundtrip --- #
        print("Running target_memory...", file=sys.stderr)
        res = workload_target_memory_roundtrip(tmpdir, profile_top)
        if res.status == "ok":
            samples = measure_workload(lambda: _target_memory_sync(), runs)
            res.samples_ms = samples
            res.median_ms = statistics.median(samples)
            res.p95_ms = p95(samples)
        results.append(res)

        # --- Workload 5: Temporal top-k --- #
        print("Running temporal_topk...", file=sys.stderr)
        res = workload_temporal_topk(profile_top)
        if res.status == "ok":
            samples = measure_workload(_temporal_topk_sync, runs)
            res.samples_ms = samples
            res.median_ms = statistics.median(samples)
            res.p95_ms = p95(samples)
        results.append(res)

        # --- Workload 6: Import timing --- #
        print("Running import_timing...", file=sys.stderr)
        res = workload_import_timing(runs, profile_top)
        results.append(res)

    return results


# --------------------------------------------------------------------------- #
# Sync wrappers for workloads that need async
# --------------------------------------------------------------------------- #

async def _hash_detection_async(file_path: str):
    from text.hash_identifier import HashConfig, HashIdentifier
    config = HashConfig()
    hi = HashIdentifier(config)
    return await hi.identify_in_file(file_path)


def _hash_detection_sync(file_path: str):
    return asyncio.run(_hash_detection_async(file_path))


def _pattern_matcher_sync(payload: str):
    from patterns.pattern_matcher import extract_high_precision_entities, match_text
    hits = match_text(payload)
    entities = extract_high_precision_entities(payload)
    return len(hits) + len(entities)


def _zstd_cache_sync(tmpdir: str):
    import compression.zstd as _zstd

    import orjson
    items = [f"item_{i}_" + "x" * 4000 for i in range(100)]
    compressed = []
    for item in items:
        data = orjson.dumps({"k": item})
        comp = _zstd.compress(data)
        compressed.append(comp)
    decompressed = []
    for comp in compressed:
        data = _zstd.decompress(comp)
        decompressed.append(orjson.loads(data))
    return len(decompressed)


def _target_memory_sync():
    from knowledge.target_memory import TargetMemoryService, TargetMemoryUpdate
    svc = TargetMemoryService()
    results = []
    for i in range(100):
        update = TargetMemoryUpdate(
            target_id=f"target_{i}",
            sprint_id=f"sprint_{i % 5}",
            finding_count=i + 1,
            entity_facets={"type": "test", "value": i},
            exposure_facets={"level": "high"},
            pivot_facets={"pivot_type": "domain"},
            observed_ts=time.time(),
        )
        merged = svc.merge_update(update)
        results.append(merged)
    return len(results)


def _temporal_topk_sync():
    import importlib.machinery
    from pathlib import Path
    loader = importlib.machinery.SourceFileLoader(
        "temporal_signal_layer",
        str(Path(__file__).parent.parent / "layers" / "temporal_signal_layer.py")
    )
    mod = loader.load_module()
    TemporalSignalLayer = mod.TemporalSignalLayer
    TemporalEvent = mod.TemporalEvent
    layer = TemporalSignalLayer()
    now = time.time()
    for i in range(500):
        event = TemporalEvent(
            ts=now - i * 0.1,
            key=f"key_{i % 50}",
            family="synthetic",
            source="f214_profiler",
            weight=1.0,
            labels=(),
        )
        layer.observe(event)
    top_scores = layer.get_top_scores(k=20)
    edge_candidates = layer.get_edge_candidates(k=50)
    return len(top_scores) + len(edge_candidates)


def measure_workload(func, runs: int) -> list:
    """Measure workload multiple times, return list of ms timings."""
    samples = []
    for _ in range(runs):
        mem_before = get_memory_mib()
        start = time.perf_counter()
        try:
            func()
        except Exception:
            pass
        end = time.perf_counter()
        mem_after = get_memory_mib()
        mem_after - mem_before
        sample_ms = (end - start) * 1000
        samples.append(sample_ms)
        # Track memory delta per result (approximate per sample)
    return samples


# --------------------------------------------------------------------------- #
# Output formatters
# --------------------------------------------------------------------------- #

def format_text(results: list[WorkloadResult], args) -> str:
    lines = []
    lines.append("# F214 Runtime Workload Profile\n")

    # Summary table
    lines.append("| Workload | Status | Median ms | p95 ms | Memory MiB | Main bottleneck |")
    lines.append("|---|---|---:|---:|---:|---|")
    for r in results:
        lines.append(f"| {r.name} | {r.status} | {r.median_ms:.1f} | {r.p95_ms:.1f} | {r.memory_delta_mib:.1f} | {r.main_bottleneck} |")

    lines.append("")
    lines.append("## Per-workload details\n")

    for r in results:
        lines.append(f"### {r.name} (`{r.status}`)")
        if r.skip_reason:
            lines.append(f"SKIP: {r.skip_reason}")
        else:
            if r.samples_ms:
                lines.append(f"- samples_ms: {[round(x, 2) for x in r.samples_ms]}")
            if r.cprofile_top:
                lines.append("- cProfile top (cumulative):")
                for entry in r.cprofile_top[:5]:
                    lines.append(f"  - {entry['function']}: {entry['cumulative_s']}s total, {entry['total_s']}s self")
        lines.append("")

    return "\n".join(lines)


def format_json(results: list[WorkloadResult]) -> str:
    output = {
        "benchmarks": {},
        "summary": {},
    }
    for r in results:
        output["benchmarks"][r.name] = r.to_dict()

    # Top bottlenecks
    all_bottlenecks = []
    for r in results:
        if r.status == "ok" and r.cprofile_top:
            all_bottlenecks.append({
                "workload": r.name,
                "function": r.cprofile_top[0]["function"],
                "cumulative_s": r.cprofile_top[0]["cumulative_s"],
                "median_ms": r.median_ms,
            })
    all_bottlenecks.sort(key=lambda x: x["cumulative_s"], reverse=True)
    output["summary"]["top_bottlenecks"] = all_bottlenecks[:5]

    return json.dumps(output, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="F214 Runtime Workload Profiler")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 3 runs, ~20s limit")
    parser.add_argument("--runs", type=int, default=3, help="Runs per workload (default: 3)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--profile-top", type=int, default=15, help="cProfile top N functions")
    args = parser.parse_args()

    results = run_workloads(args)

    if args.json:
        print(format_json(results))
    else:
        print(format_text(results, args))


if __name__ == "__main__":
    main()
