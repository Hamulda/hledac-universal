"""benchmarks/bench_rust_vs_python.py — Side-by-side benchmark of Python fallback vs Rust extension.

Measures wall-clock time of the three hot paths we ship native bindings for:

    1. Aho-Corasick     — 10 000 pattern matches
    2. Bloom filter     — 100 000 URL hash operations (50 000 add + 50 000 check)
    3. Rolling hash     — 1 MiB input, 8-byte sliding window

Each operation is run three times (warm-up + 2 measured) on the median
to absorb GC / page-in noise. The Rust path is skipped gracefully if
``hledac_rust_extensions`` is not importable — the script then serves as
a regression baseline for the pure-Python fallback only.

Output table is fixed-width, human-readable, and easy to grep:

    operation    | python_ms | rust_ms | speedup
    -------------+-----------+---------+--------
    aho_corasick |   1234.56 |   56.78 |  21.7x

Total wall-clock must stay below 30 s (CI budget). The script exits 0
on success, 1 if Rust is unexpectedly missing (CI should run with
Rust available) or if the timeout is exceeded.

Usage:
    uv run python benchmarks/bench_rust_vs_python.py
    python -m benchmarks.bench_rust_vs_python
"""


import statistics
import sys
import time
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Constants — bounded inputs, deterministic seed for reproducibility
# ---------------------------------------------------------------------------
SEED = 0xC0FFEE
N_PATTERNS = 10_000            # aho-corasick pattern count
N_TEXTS = 1_000                # aho-corasick text corpus size
N_URLS = 100_000               # bloom filter workload size
ROLLING_INPUT_BYTES = 1 << 20  # 1 MiB
ROLLING_WINDOW = 8
BUDGET_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Input generation (deterministic)
# ---------------------------------------------------------------------------

def _gen_aho_inputs() -> tuple[list[str], list[str]]:
    """Generate patterns + text corpus. Uses fixed seed for reproducibility."""
    import random
    rng = random.Random(SEED)
    patterns = [f"pat_{i:05d}_token" for i in range(N_PATTERNS)]
    texts: list[str] = []
    for _ in range(N_TEXTS):
        # 200-char text containing on average 3 patterns
        body = " ".join(rng.choice(patterns[:1000]) for _ in range(3))
        texts.append(body + " filler text " * 5)
    return patterns, texts


def _gen_bloom_inputs() -> tuple[list[str], list[str]]:
    """Generate URLs to add + URLs to check (50% hits, 50% misses)."""
    import random
    rng = random.Random(SEED)
    add = [f"https://example{i:05d}.com/path?q={i}" for i in range(N_URLS // 2)]
    check = add + [f"https://probe{i:05d}.net/" for i in range(N_URLS // 2)]
    rng.shuffle(check)
    return add, check


def _gen_rolling_input() -> bytes:
    """1 MiB deterministic byte stream."""
    import random
    rng = random.Random(SEED)
    return bytes(rng.getrandbits(8) for _ in range(ROLLING_INPUT_BYTES))


# ---------------------------------------------------------------------------
# Pure-Python fallback implementations
# ---------------------------------------------------------------------------

def _python_aho_corasick(patterns: list[str], texts: list[str]) -> int:
    """O(patterns * text_length) Python scan — for benchmarking the fallback."""
    pat_set = set(patterns)
    hits = 0
    for t in texts:
        # Naïve substring scan: for each pattern, count occurrences.
        # We do an O(|text|) scan per pattern; this is the slow path.
        for p in pat_set:
            if p in t:
                hits += 1
    return hits


def _python_bloom(add: list[str], check: list[str]) -> int:
    """Pure-Python Bloom filter (no FNV-1a trick, just hashlib.sha256 % mod)."""
    capacity = max(len(add) * 2, 1024)
    bits = bytearray(capacity // 8)
    k = 7  # hash functions
    hits = 0
    for url in add:
        h = hash(url)
        for i in range(k):
            idx = (h + i * h) % (len(bits) * 8)
            bits[idx // 8] |= 1 << (idx % 8)
    for url in check:
        h = hash(url)
        present = True
        for i in range(k):
            idx = (h + i * h) % (len(bits) * 8)
            if not (bits[idx // 8] & (1 << (idx % 8))):
                present = False
                break
        if present:
            hits += 1
    return hits


def _python_rolling(data: bytes, window: int) -> int:
    """Pure-Python rolling hash (Rabin-Karp, Mersenne prime modulus)."""
    base, modulus = 256, (1 << 61) - 1
    if len(data) < window:
        return 0
    # Power precomputation
    power = pow(base, window - 1, modulus)
    # Initial window
    h = 0
    for i in range(window):
        h = (h * base + data[i]) % modulus
    total = h
    for i in range(window, len(data)):
        h = ((h - data[i - window] * power) % modulus * base + data[i]) % modulus
        total ^= h
    return total


# ---------------------------------------------------------------------------
# Rust implementations (use the installed extension)
# ---------------------------------------------------------------------------

def _rust_aho_corasick(patterns: list[str], texts: list[str]) -> int:
    import hledac_rust_extensions as r
    m = r.AhoCorasickMatcher(patterns)
    return sum(len(m.scan(t)) for t in texts)


def _rust_bloom(add: list[str], check: list[str]) -> int:
    import hledac_rust_extensions as r
    bf = r.BloomFilter(capacity=len(add) * 2, fp_rate=0.01)
    for u in add:
        bf.add(u)
    return sum(1 for u in check if u in bf)


def _rust_rolling(data: bytes, window: int) -> int:
    import hledac_rust_extensions as r
    rh = r.RollingHashEngine(base=256, modulus=(1 << 61) - 1, window_size=window)
    hashes = rh.hashes(data)
    out = 0
    for h in hashes:
        out ^= h
    return out


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _time_median_ms(fn: Callable[[], object], runs: int = 3) -> float:
    """Median wall-clock in ms over `runs`. Includes 1 warm-up pass."""
    fn()  # warm-up
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def _row(name: str, py_ms: float, rust_ms: float | None) -> str:
    speedup = ""
    if rust_ms is not None and rust_ms > 0:
        ratio = py_ms / rust_ms
        speedup = f"{ratio:5.1f}x"
    py_str = f"{py_ms:9.2f}"
    rust_str = f"{rust_ms:7.2f}" if rust_ms is not None else "    N/A"
    return f"{name:14s}|{py_str}  |{rust_str}  | {speedup}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("hledac-rust-extensions — benchmarks/bench_rust_vs_python.py")
    print(f"  Python : {sys.version.split()[0]}")
    print("=" * 70)

    deadline = time.monotonic() + BUDGET_SECONDS

    # 1. Aho-Corasick
    print("\n[1/3] Aho-Corasick: 10 000 patterns × 1 000 texts")
    patterns, texts = _gen_aho_inputs()
    py_ms = _time_median_ms(lambda: _python_aho_corasick(patterns, texts))
    rust_ms: float | None = None
    try:
        rust_ms = _time_median_ms(lambda: _rust_aho_corasick(patterns, texts))
    except ImportError:
        print("  WARNING: hledac_rust_extensions not importable — Rust column empty")

    # 2. Bloom filter
    print("[2/3] Bloom filter: 50 000 add + 50 000 check (100 000 cap)")
    add, check = _gen_bloom_inputs()
    py_ms_b = _time_median_ms(lambda: _python_bloom(add, check))
    rust_ms_b: float | None = None
    try:
        rust_ms_b = _time_median_ms(lambda: _rust_bloom(add, check))
    except ImportError:
        pass

    # 3. Rolling hash
    print(f"[3/3] Rolling hash: 1 MiB input, window={ROLLING_WINDOW}")
    data = _gen_rolling_input()
    py_ms_r = _time_median_ms(lambda: _python_rolling(data, ROLLING_WINDOW))
    rust_ms_r: float | None = None
    try:
        rust_ms_r = _time_median_ms(lambda: _rust_rolling(data, ROLLING_WINDOW))
    except ImportError:
        pass

    # ---- Output table ----
    print()
    print(f"{'operation':14s}| {'python_ms':9s}  | {'rust_ms':7s}  | speedup")
    print("-" * 14 + "+" + "-" * 11 + "  +" + "-" * 9 + "  +" + "-" * 7)
    print(_row("aho_corasick", py_ms, rust_ms))
    print(_row("bloom", py_ms_b, rust_ms_b))
    print(_row("rolling_hash", py_ms_r, rust_ms_r))

    elapsed = BUDGET_SECONDS - (deadline - time.monotonic())
    print()
    print(f"Total wall-clock: {elapsed:.2f}s (budget: {BUDGET_SECONDS:.0f}s)")

    if elapsed > BUDGET_SECONDS:
        print(f"FAIL: exceeded {BUDGET_SECONDS:.0f}s budget")
        return 1
    if rust_ms is None and rust_ms_b is None and rust_ms_r is None:
        print("FAIL: hledac_rust_extensions not importable — CI must run with Rust built")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
