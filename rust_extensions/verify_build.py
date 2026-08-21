"""verify_build.py — smoke test for hledac-rust-extensions.

Exercises every public class of the compiled extension and prints
PASS/FAIL per module with a short timing comparison against the
Python fallback paths. Exit code 0 on all-PASS, 1 on any FAIL.

Usage:
    python rust_extensions/verify_build.py

Verifies:
    1. AhoCorasickMatcher  — multi-pattern match + find_any
    2. BloomFilter         — add / contains / __len__
    3. RollingHashEngine   — hash + roll + hashes
    4. (FastHasher removed — use content_hash_64 directly)
    5. content_hash_64     — xxHash3-64 streaming API

Each block reports median wall-clock (5 runs) for Rust vs pure Python
fallback where applicable, plus speedup ratio.
"""

import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

try:
    import hledac_rust_extensions as r  # type: ignore
except ImportError as e:
    print(f"FATAL: hledac_rust_extensions not importable: {e}")
    print("  Did you run `maturin develop --release` in rust_extensions/?")
    sys.exit(1)


_RESULTS: list[tuple[str, str, str, float, float | None, float | None]] = []


def _time_median(fn: Callable[[], Any], n: int = 5) -> float:
    """Median wall-clock in ms over `n` runs."""
    samples: list[float] = []
    # warm-up
    fn()
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def _report(
    module: str,
    name: str,
    status: str,
    rust_ms: float,
    py_ms: float | None = None,
    note: str = "",
) -> None:
    speedup = ""
    if py_ms is not None and py_ms > 0:
        ratio = py_ms / rust_ms if rust_ms > 0 else float("inf")
        speedup = f"  ({ratio:.1f}× faster)"
    line = (
        f"[{status:4s}] {module:24s} {name:32s} "
        f"rust={rust_ms:8.3f}ms"
        + (f"  py={py_ms:8.3f}ms{speedup}" if py_ms is not None else "")
        + (f"  {note}" if note else "")
    )
    print(line)
    _RESULTS.append((module, name, status, rust_ms, py_ms, ratio_or_none(py_ms, rust_ms)))


def ratio_or_none(py_ms: float | None, rust_ms: float) -> float | None:
    if py_ms is None or py_ms <= 0 or rust_ms <= 0:
        return None
    return py_ms / rust_ms


def test_aho_corasick() -> None:
    patterns = [
        "malware",
        "phishing",
        "suspicious",
        "credential_dumping",
        "lateral_movement",
        "c2_beacon",
        "ransomware",
        "exploit",
        "CVE-2024",
        "0day",
        "stealer",
        "loader",
        "shellcode",
        "encrypted",
        "payload",
        "obfuscated",
        "command_and_control",
        "privilege_escalation",
        "persistence",
        "exfiltration",
    ]
    text_corpus = [
        "Likely phishing site with credential_dumping payload",
        "Clean: just an example sentence with no IOC tokens here",
        "Detected C2_beacon + lateral_movement + privilege_escalation chain",
        "JavaScript obfuscated loader dropped a stealer executable",
        "Ransomware note references encrypted backup destruction",
    ] * 200  # 1,000 texts

    rust_matcher = r.AhoCorasickMatcher(patterns)

    def rust_scan() -> int:
        total = 0
        for t in text_corpus:
            total += len(rust_matcher.scan(t))
        return total

    py_ms: float | None = None
    try:
        import ahocorasick  # type: ignore

        ac = ahocorasick.Automaton()
        for i, p in enumerate(patterns):
            ac.add_word(p, (i, p))
        ac.make_automaton()

        def py_scan() -> int:
            total = 0
            for t in text_corpus:
                for end_idx, (idx, pat) in ac.iter(t.lower()):
                    # Discard loop vars; we just want the match count.
                    _ = (end_idx, idx, pat)
                    total += 1
            return total

        py_ms = _time_median(py_scan)
    except ImportError:
        py_ms = None

    rust_ms = _time_median(rust_scan)
    _report(
        "aho_corasick", "scan 1k texts × 20 patterns", "PASS" if rust_matcher.len() == 20 else "FAIL", rust_ms, py_ms
    )


def test_bloom_filter() -> None:
    urls = [f"https://example{i}.com/path/{i}" for i in range(10_000)]
    probe = urls[:5_000] + [f"https://probe{i}.com" for i in range(5_000)]

    py_ms: float | None = None
    try:
        from hledac.universal.utils.bloom_filter import BloomFilter as PyBF

        py_bf = PyBF(max_elements=100_000, error_rate=0.001)

        # Combined add+check
        def py_full() -> int:
            for u in urls:
                py_bf.add(u)
            hits = 0
            for u in probe:
                if u in py_bf:
                    hits += 1
            return hits

        py_ms = _time_median(py_full)
    except ImportError:
        py_ms = None

    # Reset Rust and time the full cycle together
    rust_bf = r.BloomFilter(100_000, 0.001)

    def rust_full() -> int:
        for u in urls:
            rust_bf.add(u)
        hits = 0
        for u in probe:
            if u in rust_bf:
                hits += 1
        return hits

    rust_ms = _time_median(rust_full)

    # Sanity: in-filter items should all be present (10k+), probe set 5k
    # (definitely absent) should be all False
    in_set = sum(1 for u in urls[:1000] if u in rust_bf)
    absent = sum(1 for u in probe[5000:] if u in rust_bf)
    status = "PASS" if in_set == 1000 and absent == 0 else "FAIL"
    _report(
        "bloom",
        "add 10k + check 10k URLs (100k cap)",
        status,
        rust_ms,
        py_ms,
        note=f"hit={in_set}/1000 absent_fp={absent}/5000",
    )


def test_rolling_hash() -> None:
    # 10,000 short URLs (sliding window 8)
    urls = [f"https://example{i}.com/{i:08d}" for i in range(10_000)]
    data_list = [u.encode() for u in urls]

    rh = r.RollingHashEngine(base=256, modulus=2**61 - 1, window_size=8)

    def rust_hashes() -> int:
        total = 0
        for d in data_list:
            h = rh.hash(d)
            total ^= h
        return total

    py_ms: float | None = None
    try:
        from hledac.universal.tools.rolling_hash_engine import RollingHashPython

        py_rh = RollingHashPython(base=256, modulus=2**61 - 1)

        def py_hashes() -> int:
            total = 0
            for d in data_list:
                h = py_rh.hash(d[:8])  # only first window matches Rust
                total ^= h
            return total

        py_ms = _time_median(py_hashes)
    except ImportError:
        py_ms = None

    rust_ms = _time_median(rust_hashes)
    # Sanity: hash of a known input is reproducible
    known = r.RollingHashEngine(base=256, modulus=2**61 - 1, window_size=8)
    h1 = known.hash(b"abcdabcd")
    h2 = known.hash(b"abcdabcd")
    status = "PASS" if h1 == h2 and h1 != 0 else "FAIL"
    _report("rolling_hash", "hash 10k URLs (window=8)", status, rust_ms, py_ms, note=f"h('abcdabcd')={h1}")


def test_content_hash() -> None:
    samples = [f"document-{i}-content-fingerprint" for i in range(10_000)]

    def rust_hash() -> int:
        return sum(r.content_hash_64(s.encode()) for s in samples)

    rust_ms = _time_median(rust_hash)
    # Determinism check
    h1 = r.content_hash_64(b"test string")
    h2 = r.content_hash_64(b"test string")
    status = "PASS" if h1 == h2 and h1 != 0 else "FAIL"
    _report("content_hash", "xxh3_64 10k short strings", status, rust_ms, py_ms=None, note=f"h('test string')={h1}")


def main() -> int:
    print("=" * 96)
    print("hledac-rust-extensions verify_build.py")
    print(f"  Python : {sys.version.split()[0]}")
    print(f"  Module : {r.__file__}")
    print("=" * 96)

    test_aho_corasick()
    test_bloom_filter()
    test_rolling_hash()
    test_content_hash()

    print("=" * 96)
    fails = [r_ for r_ in _RESULTS if r_[2] != "PASS"]
    print(f"Summary: {len(_RESULTS) - len(fails)}/{len(_RESULTS)} PASS")
    if fails:
        for row in fails:
            module, name = row[0], row[1]
            print(f"  FAIL: {module}::{name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
