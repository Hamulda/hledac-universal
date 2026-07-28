"""rust_vs_python_benchmark.py — Python vs Rust side-by-side timings.

Compares the three hot-path classes of `hledac-rust-extensions` against
their pure-Python fallbacks, then writes a Markdown table to
`benchmarks/rust_vs_python_results.md`.

Workloads (per task spec):
    1. Pattern matcher — 20 patterns × 10,000 texts
    2. Bloom filter    — add 100,000 URLs + check 10,000
    3. Rolling hash    — hash 10,000 URLs

Usage:
    python benchmarks/rust_vs_python_benchmark.py
"""


import platform
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Add repo root to sys.path so we can `import utils.…` and `import tools.…`
# from inside the benchmarks/ directory.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Import — abort if extension missing.
# ---------------------------------------------------------------------------
try:
    import hledac_rust_extensions as r  # type: ignore
except ImportError as e:
    print(f"FATAL: {e}")
    print("  Run `maturin develop --release` in rust_extensions/ first.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Timing helper — median of N runs after one warm-up.
# ---------------------------------------------------------------------------

def _median_ms(fn: Callable[[], Any], runs: int = 5) -> float:
    fn()  # warm-up
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


# ---------------------------------------------------------------------------
# Workload 1 — AhoCorasick (20 patterns × 10,000 texts)
# ---------------------------------------------------------------------------

PATTERNS: list[str] = [
    "malware", "phishing", "suspicious", "credential_dumping",
    "lateral_movement", "c2_beacon", "ransomware", "exploit",
    "CVE-2024", "0day", "stealer", "loader", "shellcode",
    "encrypted", "payload", "obfuscated", "command_and_control",
    "privilege_escalation", "persistence", "exfiltration",
]

TEXT_PARTS = [
    "Likely phishing site with credential_dumping payload",
    "Clean: just an example sentence with no IOC tokens here",
    "Detected C2_beacon + lateral_movement + privilege_escalation chain",
    "JavaScript obfuscated loader dropped a stealer executable",
    "Ransomware note references encrypted backup destruction",
]


def build_text_corpus(n: int = 10_000) -> list[str]:
    return [TEXT_PARTS[i % len(TEXT_PARTS)] for i in range(n)]


def bench_aho_corasick() -> dict[str, Any]:
    corpus = build_text_corpus(10_000)

    # ---- Rust ----
    rust_matcher = r.AhoCorasickMatcher(PATTERNS)
    def rust_scan() -> int:
        total = 0
        for t in corpus:
            total += len(rust_matcher.scan(t))
        return total

    rust_ms = _median_ms(rust_scan)
    rust_hits = rust_scan()

    # ---- Python (pyahocorasick) ----
    py_ms: float | None = None
    py_hits: int | None = None
    try:
        import ahocorasick  # type: ignore
        ac = ahocorasick.Automaton()
        for i, p in enumerate(PATTERNS):
            ac.add_word(p, (i, p))
        ac.make_automaton()
        def py_scan() -> int:
            total = 0
            for t in corpus:
                for _match in ac.iter(t.lower()):
                    _ = _match  # payload ignored — count only
                    total += 1
            return total
        py_ms = _median_ms(py_scan)
        py_hits = py_scan()
    except ImportError:
        pass

    return {
        "name": "AhoCorasick",
        "workload": "20 patterns × 10,000 texts",
        "rust_ms": rust_ms,
        "py_ms": py_ms,
        "rust_hits": rust_hits,
        "py_hits": py_hits,
        "speedup": (py_ms / rust_ms) if (py_ms and rust_ms > 0) else None,
    }


# ---------------------------------------------------------------------------
# Workload 2 — Bloom filter (add 100k + check 10k)
# ---------------------------------------------------------------------------

def build_url_pool(n: int) -> list[str]:
    return [f"https://example{i}.com/path/{i:08d}?q={i}" for i in range(n)]


def bench_bloom_filter() -> dict[str, Any]:
    add_urls = build_url_pool(100_000)
    check_urls = add_urls[:5_000] + build_url_pool(110_000, )[5_000:10_000]  # 5k present + 5k absent

    # ---- Rust ----
    def rust_full() -> int:
        bf = r.BloomFilter(200_000, 0.001)
        for u in add_urls:
            bf.add(u)
        hits = 0
        for u in check_urls:
            if u in bf:
                hits += 1
        return hits

    rust_ms = _median_ms(rust_full)
    rust_hits = rust_full()

    # ---- Python (utils.bloom_filter.BloomFilter) ----
    py_ms: float | None = None
    py_hits: int | None = None
    try:
        from hledac.universal.utils.bloom_filter import BloomFilter as PyBF  # type: ignore

        def py_full() -> int:
            bf = PyBF(max_elements=200_000, error_rate=0.001)
            for u in add_urls:
                bf.add(u)
            hits = 0
            for u in check_urls:
                if u in bf:
                    hits += 1
            return hits

        py_ms = _median_ms(py_full)
        py_hits = py_full()
    except ImportError:
        pass

    return {
        "name": "BloomFilter",
        "workload": "add 100,000 + check 10,000 URLs",
        "rust_ms": rust_ms,
        "py_ms": py_ms,
        "rust_hits": rust_hits,
        "py_hits": py_hits,
        "speedup": (py_ms / rust_ms) if (py_ms and rust_ms > 0) else None,
    }


# ---------------------------------------------------------------------------
# Workload 3 — Rolling hash (10,000 URLs)
# ---------------------------------------------------------------------------

def bench_rolling_hash() -> dict[str, Any]:
    urls = build_url_pool(10_000)
    data_list = [u.encode() for u in urls]

    # ---- Rust ----
    rh = r.RollingHashEngine(base=256, modulus=2**61 - 1, window_size=8)
    def rust_hash() -> int:
        total = 0
        for d in data_list:
            total ^= rh.hash(d)
        return total

    rust_ms = _median_ms(rust_hash)
    rust_hits = rust_hash()

    # ---- Python (tools.rolling_hash_engine.RollingHashPython) ----
    py_ms: float | None = None
    py_hits: int | None = None
    try:
        from hledac.universal.tools.rolling_hash_engine import RollingHashPython  # type: ignore
        py_rh = RollingHashPython(base=256, modulus=2**61 - 1)
        def py_hash() -> int:
            total = 0
            for d in data_list:
                total ^= py_rh.hash(d[:8])
            return total
        py_ms = _median_ms(py_hash)
        py_hits = py_hash()
    except ImportError:
        pass

    return {
        "name": "RollingHash",
        "workload": "hash 10,000 URLs (window=8)",
        "rust_ms": rust_ms,
        "py_ms": py_ms,
        "rust_hits": rust_hits,
        "py_hits": py_hits,
        "speedup": (py_ms / rust_ms) if (py_ms and rust_ms > 0) else None,
    }


# ---------------------------------------------------------------------------
# Markdown report writer
# ---------------------------------------------------------------------------

def render_md(results: list[dict[str, Any]], target_dir: Path) -> Path:
    py_info = platform.python_version()
    target_path = target_dir / "rust_vs_python_results.md"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Rust vs Python benchmark — hledac-rust-extensions",
        "",
        f"_Generated {now} on Python {py_info}, {platform.machine()} ({platform.system()})_",
        "",
        "Median of 5 timed runs after 1 warm-up. Lower is better.",
        "",
        "| Workload | Python (ms) | Rust (ms) | Speedup | Hits (Py / Rust) |",
        "|---|---:|---:|---:|:---:|",
    ]
    for r_ in results:
        py = f"{r_['py_ms']:.2f}" if r_["py_ms"] is not None else "n/a"
        rs = f"{r_['rust_ms']:.2f}"
        sp = f"{r_['speedup']:.1f}×" if r_["speedup"] is not None else "n/a"
        hits = (
            f"{r_['py_hits']} / {r_['rust_hits']}"
            if r_["py_hits"] is not None
            else f"— / {r_['rust_hits']}"
        )
        lines.append(f"| {r_['name']} — {r_['workload']} | {py} | {rs} | {sp} | {hits} |")
    lines.append("")
    lines.append("## Workload details")
    lines.append("")
    for r_ in results:
        lines.append(f"### {r_['name']}")
        lines.append(f"- **Workload**: {r_['workload']}")
        lines.append("- **Python backend**: "
                     + ("present" if r_["py_ms"] is not None else "fallback unavailable in this env"))
        lines.append(f"- **Rust backend**: {r_['rust_ms']:.3f} ms (median)")
        if r_["py_ms"] is not None:
            lines.append(f"- **Speedup**: {r_['speedup']:.1f}×")
        lines.append("")

    target_path.write_text("\n".join(lines))
    return target_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"hledac-rust-extensions benchmark — Python {platform.python_version()}, {platform.machine()}")
    print(f"Module: {r.__file__}")
    print()

    results: list[dict[str, Any]] = []
    for bench in (bench_aho_corasick, bench_bloom_filter, bench_rolling_hash):
        r_ = bench()
        results.append(r_)
        py = f"{r_['py_ms']:8.2f} ms" if r_["py_ms"] is not None else "    n/a"
        sp = f"{r_['speedup']:5.1f}×" if r_["speedup"] is not None else "   n/a"
        print(f"  {r_['name']:14s}  rust={r_['rust_ms']:8.2f} ms  py={py}  speedup={sp}")

    target_dir = Path(__file__).resolve().parent
    md = render_md(results, target_dir)
    print()
    print(f"Markdown report: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
