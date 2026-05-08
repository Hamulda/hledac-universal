"""M1 MLX inference baseline probes — measures MLX Metal performance."""
from __future__ import annotations

import logging
import os
import pathlib
import time
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
RESULTS_DIR = PROJECT_ROOT / "tests" / "probe_bench_g"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "baseline_results.json"

# --- probe config ---
TINY_PROMPTS = [
    "a",
    "bb",
    "ccc",
]
MAX_TOKENS_SMOKE = 8
WARMUP_RUNS = 1
MEASURE_RUNS = 2

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _now_ms() -> float:
    return time.monotonic() * 1000.0


def _timed_ms(fn) -> tuple[float, Any]:
    t0 = _now_ms()
    result = fn()
    return _now_ms() - t0, result


# -----------------------------------------------------------------------------
# Probes
# -----------------------------------------------------------------------------


def probe_mlx_import() -> dict[str, Any]:
    """Import mlx.core and verify it is not None."""
    try:
        import mlx.core as mx

        assert mx is not None
        return {"ok": True, "mlx_version": getattr(mx, "__version__", "unknown")}
    except Exception as e:
        logger.warning("probe_mlx_import failed: %s", e)
        return {"error": str(e)}


def probe_mlx_lm_import() -> dict[str, Any]:
    """Import mlx_lm and verify it has a `load` attribute."""
    try:
        import mlx_lm

        assert hasattr(mlx_lm, "load"), "mlx_lm has no 'load' attribute"
        return {"ok": True, "mlx_lm_version": getattr(mlx_lm, "__version__", "unknown")}
    except Exception as e:
        logger.warning("probe_mlx_lm_import failed: %s", e)
        return {"error": str(e)}


def probe_metal_memory_surface() -> dict[str, Any]:
    """Return active Metal memory in bytes via mx.metal.get_active_memory()."""
    try:
        import mlx.core as mx

        # get_active_memory may raise if Metal is not available
        mem_bytes = mx.get_active_memory()
        assert isinstance(mem_bytes, int), f"expected int, got {type(mem_bytes)}"
        assert mem_bytes >= 0, f"expected >= 0, got {mem_bytes}"
        return {"ok": True, "metal_memory_bytes": mem_bytes}
    except Exception as e:
        logger.warning("probe_metal_memory_surface failed: %s", e)
        return {"error": str(e)}


def probe_tiny_array_ops() -> dict[str, Any]:
    """Create small mx.arrays, run add/matmul, measure latency in ms."""
    try:
        import mlx.core as mx

        a = mx.array([[1.0, 2.0], [3.0, 4.0]])
        b = mx.array([[5.0, 6.0], [7.0, 8.0]])

        lat_add, _ = _timed_ms(lambda: a + b)
        lat_matmul, _ = _timed_ms(lambda: mx.matmul(a, b))

        return {
            "ok": True,
            "add_latency_ms": round(lat_add, 3),
            "matmul_latency_ms": round(lat_matmul, 3),
        }
    except Exception as e:
        logger.warning("probe_tiny_array_ops failed: %s", e)
        return {"error": str(e)}


def probe_cached_model_path() -> dict[str, Any]:
    """Verify existence of a HuggingFace cache directory or project-local cache."""
    try:
        hf_cache = pathlib.Path(os.path.expanduser("~/.cache/huggingface/"))
        project_cache = PROJECT_ROOT / ".model_cache"
        project_cache_exists = project_cache.exists()

        # check HF cache for models directory
        hf_models_exists = (hf_cache / "models").exists()

        # prefer project-local if present
        if project_cache_exists:
            model_path = str(project_cache)
        elif hf_models_exists:
            model_path = str(hf_cache / "models")
        else:
            model_path = None

        return {
            "ok": True,
            "model_path": model_path,
            "project_cache_exists": project_cache_exists,
            "hf_models_exists": hf_models_exists,
        }
    except Exception as e:
        logger.warning("probe_cached_model_path failed: %s", e)
        return {"error": str(e)}


def _load_tiny_model() -> Any:
    """Load the smallest available mlx model for benchmarking."""
    import mlx_lm

    model_name = "mlx-community/Qwen2-0.5B"
    result = mlx_lm.load(model_name)
    model: Any = result[0]
    return model


def probe_model_load_latency() -> dict[str, Any]:
    """Measure model load latency in ms using mlx_lm.load()."""
    try:
        import mlx.core as mx

        # warm up mx (Metal lazy evaluation)
        mx.eval(mx.array([1.0]))

        lat_load, model = _timed_ms(_load_tiny_model)
        mx.eval(model.parameters())

        return {
            "ok": True,
            "model_load_latency_ms": round(lat_load, 3),
        }
    except Exception as e:
        logger.warning("probe_model_load_latency failed: %s", e)
        return {"error": str(e)}


def probe_first_token_latency() -> dict[str, Any]:
    """Measure time to first token after model load."""
    try:
        import mlx_lm

        model_name = "mlx-community/Qwen2-0.5B"
        result = mlx_lm.load(model_name)
        model: Any = result[0]
        tokenizer: Any = result[1]

        mlx_tokens = tokenizer.encode("a")

        t0 = _now_ms()
        mlx_lm.generate(model, tokenizer, mlx_tokens, max_tokens=1)
        first_token_time = _now_ms() - t0

        return {
            "ok": True,
            "first_token_latency_ms": round(first_token_time, 3),
        }
    except Exception as e:
        logger.warning("probe_first_token_latency failed: %s", e)
        return {"error": str(e)}


def probe_cache_clear_latency() -> dict[str, Any]:
    """Measure mx.metal.clear_cache() latency in ms."""
    try:
        import mlx.core as mx

        # warm up Metal first
        mx.eval(mx.array([1.0]))

        lat_clear, _ = _timed_ms(lambda: mx.clear_cache())

        return {
            "ok": True,
            "cache_clear_latency_ms": round(lat_clear, 3),
        }
    except Exception as e:
        logger.warning("probe_cache_clear_latency failed: %s", e)
        return {"error": str(e)}


# -----------------------------------------------------------------------------
# Baseline runner
# -----------------------------------------------------------------------------


def run_baseline() -> dict[str, Any]:
    """Run all probe_* functions, aggregate results, print a report."""
    probes = [
        ("probe_mlx_import", probe_mlx_import),
        ("probe_mlx_lm_import", probe_mlx_lm_import),
        ("probe_metal_memory_surface", probe_metal_memory_surface),
        ("probe_tiny_array_ops", probe_tiny_array_ops),
        ("probe_cached_model_path", probe_cached_model_path),
        ("probe_model_load_latency", probe_model_load_latency),
        ("probe_first_token_latency", probe_first_token_latency),
        ("probe_cache_clear_latency", probe_cache_clear_latency),
    ]

    results: dict[str, Any] = {}
    for name, fn in probes:
        results[name] = fn()

    # Pretty print
    print("\n=== M1 MLX Inference Baseline ===")
    for name, res in results.items():
        status = "OK" if res.get("ok") else f"ERROR: {res.get('error', 'unknown')}"
        print(f"  {name}: {status}")

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    run_baseline()
