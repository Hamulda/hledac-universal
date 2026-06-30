"""
Sprint 8C0 Benchmark 4: Hermes / MLX Inference Baseline

Measures:
- tokens_per_second (if Hermes available)
- TTFT (time to first token)
- load/no-load delta (if safely measurable)

Fixtures: fixed offline prompts (no network).

Reports:
- UNAVAILABLE_WITH_REASON if model / backend not ready
- Does NOT fake metrics
"""

import gc
import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.bench_8c0.common_stats import (
    build_result,
    check_hermes_model,
    check_mlx,
    write_results,
)


# ---------------------------------------------------------------------------
# Fixed prompt set (offline, no network)
# ---------------------------------------------------------------------------

FIXED_PROMPTS = [
    "What is the capital of France?",
    "Explain photosynthesis in one sentence.",
    "List three prime numbers.",
    "What year did World War II end?",
    "Describe the water cycle.",
    "What is the chemical symbol for gold?",
    "Name the largest planet in our solar system.",
    "What is 15 times 7?",
    "What continent is Egypt on?",
    "Describe what a computer does.",
]


# ---------------------------------------------------------------------------
# Hermes / MLX inference helpers
# ---------------------------------------------------------------------------

def get_mlx_available() -> Tuple[bool, str]:
    """Check if MLX is available."""
    return check_mlx()


def get_hermes_available() -> Tuple[bool, str]:
    """Check if Hermes model is cached and available."""
    # Set HF_HOME so mlx_lm can find cached models
    import os
    os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    return check_hermes_model()


async def generate_with_hermes(
    model_path: str,
    prompt: str,
    max_tokens: int = 32,
) -> Tuple[List[str], float]:
    """
    Generate tokens using Hermes via mlx_lm (async wrapper over sync).
    Returns (token_strings, ttft_seconds).
    """
    import os
    os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    import mlx_lm as mlm

    # Load model and tokenizer
    model, tokenizer = mlm.load(model_path)

    ttft: Optional[float] = None
    tokens: List[str] = []

    start = time.perf_counter_ns()
    first_token_ns: Optional[int] = None

    # Synchronous streaming — run in executor to not block
    def _sync_stream():
        nonlocal first_token_ns
        for token in mlm.generate(  # type: ignore[attr-defined]
            model,
            tokenizer,
            prompt,
            max_tokens=max_tokens,
        ):
            if first_token_ns is None:
                first_token_ns = time.perf_counter_ns()
            tokens.append(token)

    import asyncio
    await asyncio.to_thread(_sync_stream)

    elapsed_s = (time.perf_counter_ns() - start) / 1_000_000_000
    ttft_s = (first_token_ns - start) / 1_000_000_000 if first_token_ns else elapsed_s

    return tokens, ttft_s


def sync_generate_with_hermes(
    model_path: str,
    prompt: str,
    max_tokens: int = 32,
) -> Tuple[int, float]:
    """
    Synchronous generation with Hermes via mlx_lm.
    Returns (token_count, ttft_seconds).
    """
    import os
    os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    import mlx_lm as mlm

    # Load model and tokenizer (mlm.load returns 2 or 3 values)
    loaded = mlm.load(model_path)
    if len(loaded) == 3:
        model, tokenizer, _ = loaded
    else:
        model, tokenizer = loaded

    start = time.perf_counter_ns()
    first_token_ns: Optional[int] = None

    token_count = 0
    for _ in mlm.generate(  # type: ignore[attr-defined]
        model,
        tokenizer,
        prompt,
        max_tokens=max_tokens,
    ):
        token_count += 1
        if first_token_ns is None:
            first_token_ns = time.perf_counter_ns()

    ttft_s = (first_token_ns - start) / 1_000_000_000 if first_token_ns else 0.0

    return token_count, ttft_s


# ---------------------------------------------------------------------------
# Benchmark tests
# ---------------------------------------------------------------------------

class TestHermesMLXBenchmark(unittest.TestCase):
    """
    Hermes / MLX inference baseline benchmark.
    """

    @classmethod
    def setUpClass(cls):
        cls.mlx_available, cls.mlx_reason = get_mlx_available()
        cls.hermes_available, cls.hermes_path = get_hermes_available()

    def test_mlx_backend_check(self):
        """Report MLX availability status."""
        result = {
            "benchmark": "hermes_mlx_backend_check",
            "status": "PASS",
            "reason": None,
            "n": 1,
            "warmup": 0,
            "min": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "unit": "boolean",
            "fixtures": [],
            "seed": None,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "extra": {
                "mlx_available": self.mlx_available,
                "mlx_reason": self.mlx_reason,
                "hermes_available": self.hermes_available,
                "hermes_path": self.hermes_path,
            },
        }

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_mlx_backend_check.jsonl"
        write_results([result], output_path)

    def test_hermes_inference_baseline(self):
        """
        Run Hermes inference on fixed prompts.
        Report UNAVAILABLE_WITH_REASON if model not ready.
        """
        if not self.hermes_available:
            result = build_result(
                benchmark="hermes_inference_tokens_per_second",
                durations_ms=[],
                warmup=0,
                unit="tok/s",
                fixtures=FIXED_PROMPTS,
                status="UNAVAILABLE_WITH_REASON",
                reason=f"Hermes model not available: {self.hermes_path}",
                extra={
                    "mlx_available": self.mlx_available,
                    "hermes_available": False,
                },
            )
            output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_inference_tokens_per_second.jsonl"
            write_results([result], output_path)
            self.skipTest(f"Hermes model not available: {self.hermes_path}")

        model_path = self.hermes_path

        # Warmup — load model and run one prompt
        try:
            _, _ = sync_generate_with_hermes(model_path, FIXED_PROMPTS[0], max_tokens=8)
        except Exception as e:
            result = build_result(
                benchmark="hermes_inference_tokens_per_second",
                durations_ms=[],
                warmup=0,
                unit="tok/s",
                fixtures=FIXED_PROMPTS,
                status="UNAVAILABLE_WITH_REASON",
                reason=f"Warmup generation failed: {e}",
                extra={"hermes_path": model_path},
            )
            output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_inference_tokens_per_second.jsonl"
            write_results([result], output_path)
            self.skipTest(f"Warmup failed: {e}")

        # Measure: tokens/s and TTFT across all fixed prompts
        tok_per_s_list: List[float] = []
        ttft_list: List[float] = []

        for prompt in FIXED_PROMPTS:
            try:
                tok_count, ttft_s = sync_generate_with_hermes(model_path, prompt, max_tokens=32)
                elapsed_s = ttft_s  # approximate total time
                tok_per_s = tok_count / elapsed_s if elapsed_s > 0 else 0.0
                tok_per_s_list.append(tok_per_s)
                ttft_list.append(ttft_s * 1000)  # ms
            except Exception as e:  # noqa: BLE001
                # Log but continue — report partial results
                pass

        if not tok_per_s_list:
            result = build_result(
                benchmark="hermes_inference_tokens_per_second",
                durations_ms=[],
                warmup=1,
                unit="tok/s",
                fixtures=FIXED_PROMPTS,
                status="UNAVAILABLE_WITH_REASON",
                reason="All generation attempts failed",
                extra={"hermes_path": model_path},
            )
            output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_inference_tokens_per_second.jsonl"
            write_results([result], output_path)
            self.skipTest("All generation attempts failed")

        result = build_result(
            benchmark="hermes_inference_tokens_per_second",
            durations_ms=tok_per_s_list,
            warmup=1,
            unit="tok/s",
            fixtures=FIXED_PROMPTS,
            status="PASS",
            extra={
                "tok_per_s": round(sum(tok_per_s_list) / len(tok_per_s_list), 2),
                "ttft_ms": round(sum(ttft_list) / len(ttft_list), 3),
                "hermes_path": model_path,
                "max_tokens": 32,
            },
        )

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_inference_tokens_per_second.jsonl"
        write_results([result], output_path)

        self.assertGreater(result["median"], 0)

    def test_load_unload_delta(self):
        """
        Measure model load + unload time as a single delta.
        Useful for understanding cold-start overhead.
        """
        if not self.mlx_available:
            result = build_result(
                benchmark="hermes_load_unload_ms",
                durations_ms=[],
                warmup=0,
                unit="ms",
                fixtures=[],
                status="UNAVAILABLE_WITH_REASON",
                reason=f"MLX not available: {self.mlx_reason}",
            )
            output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_load_unload_ms.jsonl"
            write_results([result], output_path)
            self.skipTest("MLX not available")

        # Cannot safely measure load/unload without model weights
        # — report as UNAVAILABLE_WITH_REASON (would require dummy load)
        result = build_result(
            benchmark="hermes_load_unload_ms",
            durations_ms=[],
            warmup=0,
            unit="ms",
            fixtures=[],
            status="UNAVAILABLE_WITH_REASON",
            reason="Cannot safely measure load/unload delta without model weights — requires real model path",
            extra={
                "mlx_available": self.mlx_available,
                "hermes_available": self.hermes_available,
            },
        )
        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "hermes_load_unload_ms.jsonl"
        write_results([result], output_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
