"""
Common statistics helpers for Sprint 8C0 benchmark suite.

Provides:
- Warmup / repeat / percentile utilities
- JSON export with deterministic schema
- Shared fixtures loading
"""

import gc
import json
import time
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

BENCHMARK_RESULT_SCHEMA = {
    "benchmark": str,       # e.g. "e2e_baseline", "html_parse"
    "status": str,          # "PASS", "FAIL", "MISSING_DEP", "UNAVAILABLE_WITH_REASON"
    "reason": str | None, # why unavailable / missing dep
    "n": int,               # number of measured runs
    "warmup": int,          # warmup runs performed
    "min": float,
    "median": float,
    "p95": float,
    "max": float,
    "unit": str,
    "fixtures": list[str],   # source files used
    "seed": int | None,  # deterministic seed used
    "timestamp": str,
    "extra": dict[str, Any], # benchmark-specific extra fields
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def compute_percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile of a list of values (0.0 <= p <= 1.0)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def warmup_and_measure(
    fn: Callable[[], Any],
    warmup: int = 2,
    repeats: int = 5,
    gc_between: bool = True,
) -> tuple[list[float], float]:
    """
    Run `fn` `warmup` times for warmup, then `repeats` times collecting
    wall-clock durations. Returns (durations, median).

    Does NOT call gc.collect() between warmup runs by default.
    Set gc_between=True to force GC between measured runs.
    """
    # Warmup phase
    for _ in range(warmup):
        fn()

    # Measurement phase
    durations: list[float] = []
    for _ in range(repeats):
        if gc_between:
            gc.collect()
        start = time.perf_counter_ns()
        fn()
        elapsed_ns = time.perf_counter_ns() - start
        durations.append(elapsed_ns / 1_000_000)  # ms

    return durations, median(durations)


def build_result(
    benchmark: str,
    durations_ms: list[float],
    warmup: int,
    unit: str,
    fixtures: list[str],
    status: str = "PASS",
    reason: str | None = None,
    seed: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a machine-readable benchmark result dict."""
    d = {
        "benchmark": benchmark,
        "status": status,
        "reason": reason,
        "n": len(durations_ms),
        "warmup": warmup,
        "min": round(min(durations_ms), 3) if durations_ms else 0.0,
        "median": round(median(durations_ms), 3) if durations_ms else 0.0,
        "p95": round(compute_percentile(durations_ms, 0.95), 3) if durations_ms else 0.0,
        "max": round(max(durations_ms), 3) if durations_ms else 0.0,
        "unit": unit,
        "fixtures": fixtures,
        "seed": seed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "extra": extra or {},
    }
    return d


def write_results(results: list[dict[str, Any]], output_path: Path) -> None:
    """Append all results to a JSONL output file (one JSON object per line)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def load_fixture_manifest() -> dict[str, list[str]]:
    """Load the fixture manifest created during KROK 2."""
    path = Path(__file__).parent.parent.parent / "tests" / "probe_8c0" / "fixture_manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"html": [], "json": [], "warc": [], "text": []}


def load_html_fixtures(limit: int = 20) -> list[tuple[str, str]]:
    """
    Load real HTML fixtures from the project.
    Returns list of (file_path, content).
    """
    manifest = load_fixture_manifest()
    fixtures: list[tuple[str, str]] = []
    for fp in manifest.get("html", [])[:limit]:
        try:
            content = Path(fp).read_text(errors="ignore")
            fixtures.append((fp, content))
        except Exception:
            pass
    return fixtures


def load_json_fixtures(limit: int = 20) -> list[tuple[str, dict[str, Any]]]:
    """
    Load real JSON fixtures from the project.
    Returns list of (file_path, parsed_dict).
    """
    manifest = load_fixture_manifest()
    fixtures: list[tuple[str, dict[str, Any]]] = []
    for fp in manifest.get("json", [])[:limit]:
        try:
            data = json.loads(Path(fp).read_text(errors="ignore"))
            fixtures.append((fp, data))
        except Exception:
            pass
    return fixtures


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def check_selectolax() -> bool:
    """Check if selectolax parser is available."""
    try:
        from selectolax.parser import HTMLParser as _  # noqa: F401
        return True
    except ImportError:
        return False


def check_uvloop() -> bool:
    """Check if uvloop is installed and can be used."""
    try:
        import uvloop as _  # noqa: F401
        return True
    except ImportError:
        return False


def check_mlx() -> tuple[bool, str]:
    """
    Check if MLX and mlx_lm are available for inference.
    Returns (available, reason_if_not).
    """
    try:
        import mlx.core as _  # noqa: F401
    except ImportError:
        return False, "MLX not installed"

    try:
        import mlx_lm as _  # noqa: F401
    except ImportError:
        return False, "mlx_lm not installed"

    return True, ""


def check_hermes_model() -> tuple[bool, str]:
    """
    Check if Hermes model is available.
    Returns (available, model_path_or_reason).
    """
    available, reason = check_mlx()
    if not available:
        return False, reason

    import os
    hf_home = os.path.expanduser("~/.cache/huggingface/hub/models--mlx-community--Hermes-3-Llama-3.2-3B-4bit")
    if Path(hf_home).exists():
        return True, "mlx-community/Hermes-3-Llama-3.2-3B-4bit"

    # Check common model paths
    model_paths = [
        Path.home() / ".cache" / "mlx_lm" / "Hermes-3-Llama-3.2-3B-4bit",
        Path.home() / ".cache" / "mlx_lm" / "hermes-3-llama-3.2-3b-4bit",
        Path.home() / ".cache" / "lm-hub" / "Hermes-3-Llama-3.2-3B-4bit",
    ]
    for mp in model_paths:
        if mp.exists():
            return True, str(mp)
    return False, "Hermes model not found in common cache paths"
