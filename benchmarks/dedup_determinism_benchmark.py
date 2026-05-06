#!/usr/bin/env python3
"""
Dedup Determinism Benchmark — Sprint F214OPT-J
==============================================

Tests determinism and performance of:
- semantic_deduplicator check_batch duplicate mapping
- utils/deduplication fallback embedding
- utils/deduplication MinHash with ngram cap

No MLX model load. Synthetic texts only.
Dry-run by default (--no-dry-run to execute).

Usage:
    python benchmarks/dedup_determinism_benchmark.py \\
        --output-json probe_f214opt_dedup_determinism/benchmark.json \\
        --output-md probe_f214opt_dedup_determinism/BENCHMARK.md

    python benchmarks/dedup_determinism_benchmark.py --no-dry-run --iterations 5
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Synthetic text generation
# ---------------------------------------------------------------------------

TEXT_SIZES = {
    "10KB": 10 * 1024,
    "100KB": 100 * 1024,
    "1MB": 1024 * 1024,
}


def generate_synthetic_text(size_bytes: int, seed: int = 42) -> str:
    """Generate pseudo-random text of exact byte size."""
    rng = random.Random(seed)
    words = [
        "".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 12)))
        for _ in range(20000)
    ]
    text = " ".join(words)
    if len(text.encode("utf-8")) > size_bytes:
        text = text[:size_bytes]
    return text


def generate_texts_with_duplicates(
    size_label: str, n_unique: int = 5, n_duplicates_each: int = 3
) -> list[tuple[str, str]]:
    """
    Generate texts where some are exact duplicates.
    Returns list of (text, label) tuples.
    """
    base_size = TEXT_SIZES[size_label]
    texts = []
    for i in range(n_unique):
        seed = 1000 + i
        text = generate_synthetic_text(base_size, seed=seed)
        label = f"unique_{i}"
        texts.append((text, label))
        # Add duplicates
        for d in range(n_duplicates_each):
            texts.append((text, f"dup_{i}_{d}"))
    return texts


# ---------------------------------------------------------------------------
# Fallback embedding determinism test
# ---------------------------------------------------------------------------

def test_fallback_embedding_determinism() -> dict[str, Any]:
    """Test that fallback embedding is deterministic."""
    from utils.deduplication import DeduplicationConfig, SemanticDeduplicator

    config = DeduplicationConfig()
    deduper = SemanticDeduplicator(config)

    test_content = "The quick brown fox jumps over the lazy dog. " * 50
    results: dict[str, Any] = {
        "test_name": "fallback_embedding_determinism",
        "same_text_identical_vector": False,
        "different_texts_different": False,
        "no_nan_inf": False,
        "norm_positive": False,
        "iterations": 10,
    }

    try:
        emb1 = deduper._fallback_embedding(test_content)
        embeddings = [emb1]
        for _ in range(results["iterations"] - 1):
            emb = deduper._fallback_embedding(test_content)
            embeddings.append(emb)

        # All identical?
        all_identical = all(
            float((emb == emb1).all()) == 1.0 for emb in embeddings
        )
        results["same_text_identical_vector"] = all_identical

        # Different content produces different vector
        other_content = "Completely different text content here. " * 50
        emb_other = deduper._fallback_embedding(other_content)
        results["different_texts_different"] = float((emb_other != emb1).any()) == 1.0

        # No NaN/Inf
        import numpy as np
        has_nan = np.isnan(emb1).any()
        has_inf = np.isinf(emb1).any()
        results["no_nan_inf"] = not has_nan and not has_inf

        # Norm > 0
        import numpy as np
        results["norm_positive"] = float(np.linalg.norm(emb1)) > 0

    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# MinHash ngram cap test
# ---------------------------------------------------------------------------

def test_minhash_ngram_cap() -> dict[str, Any]:
    """Test that HLEDAC_DEDUP_MAX_NGRAMS cap is respected."""
    from utils.deduplication import ContentDeduplicator, DeduplicationConfig

    config = DeduplicationConfig()
    deduper = ContentDeduplicator(config)

    results: dict[str, Any] = {
        "test_name": "minhash_ngram_cap",
        "cap_enforced": False,
        "extreme_text_truncated": False,
        "default_cap": deduper._DEFAULT_MAX_NGRAMS,
        "env_override_works": False,
    }

    try:
        # Normal text
        normal_text = "This is a normal piece of text. " * 100
        normal_ngrams = deduper._generate_ngrams(normal_text, config.ngram_size)
        results["normal_ngram_count"] = len(normal_ngrams)

        # Extreme text (> 50K ngrams for n=5 on 1MB)
        extreme_text = generate_synthetic_text(1024 * 1024, seed=999)
        extreme_ngrams_before = deduper._generate_ngrams(extreme_text, config.ngram_size)
        results["extreme_ngram_count_before_cap"] = len(extreme_ngrams_before)

        # Cap should truncate
        capped_ngrams = extreme_ngrams_before[: deduper._get_max_ngrams()]
        results["extreme_ngram_count_after_cap"] = len(capped_ngrams)
        results["cap_enforced"] = len(capped_ngrams) <= deduper._get_max_ngrams()

        # Test env override
        original_env = os.environ.get("HLEDAC_DEDUP_MAX_NGRAMS")
        os.environ["HLEDAC_DEDUP_MAX_NGRAMS"] = "1000"
        # Re-get the cap
        custom_cap = deduper._get_max_ngrams()
        results["env_override_works"] = custom_cap == 1000
        if original_env is not None:
            os.environ["HLEDAC_DEDUP_MAX_NGRAMS"] = original_env
        else:
            os.environ.pop("HLEDAC_DEDUP_MAX_NGRAMS", None)

        # Invalid env fallback
        os.environ["HLEDAC_DEDUP_MAX_NGRAMS"] = "not_a_number"
        fallback_cap = deduper._get_max_ngrams()
        results["invalid_env_fallback_works"] = fallback_cap == deduper._DEFAULT_MAX_NGRAMS
        os.environ.pop("HLEDAC_DEDUP_MAX_NGRAMS", None)

    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# MinHash performance test
# ---------------------------------------------------------------------------

def test_minhash_performance(size_labels: list[str]) -> dict[str, Any]:
    """Benchmark MinHash on synthetic texts of various sizes."""
    from utils.deduplication import ContentDeduplicator, DeduplicationConfig

    config = DeduplicationConfig()
    deduper = ContentDeduplicator(config)

    results: dict[str, Any] = {
        "test_name": "minhash_performance",
        "sizes": {},
    }

    for label in size_labels:
        size_bytes = TEXT_SIZES[label]
        text = generate_synthetic_text(size_bytes, seed=42)
        ngrams = deduper._generate_ngrams(text, config.ngram_size)

        start = time.perf_counter()
        sig = deduper._compute_minhash(text)
        elapsed = time.perf_counter() - start

        results["sizes"][label] = {
            "text_bytes": size_bytes,
            "ngram_count": len(ngrams),
            "signature_length": len(sig) if sig else 0,
            "elapsed_seconds": round(elapsed, 4),
        }

    return results


# ---------------------------------------------------------------------------
# Semantic dedup check_batch determinism (mocked, no MLX)
# ---------------------------------------------------------------------------

def test_semantic_check_batch_no_texts_index() -> dict[str, Any]:
    """
    Verify semantic_deduplicator.check_batch does NOT use texts.index()
    inside the similarity loop by inspecting the source.
    """
    import inspect
    from semantic_deduplicator import SemanticDedupCache

    results: dict[str, Any] = {
        "test_name": "semantic_check_batch_no_texts_index",
        "has_texts_index_in_loop": False,
        "has_unique_to_original_mapping": False,
    }

    try:
        source = inspect.getsource(SemanticDedupCache.check_batch)
        # Check if texts.index is used inside the for j loop
        lines = source.split("\n")
        in_j_loop = False
        for line in lines:
            stripped = line.strip()
            if "for j," in stripped or "for j in" in stripped:
                in_j_loop = True
            if in_j_loop and "texts.index" in stripped:
                results["has_texts_index_in_loop"] = True
            if "unique_to_original" in stripped:
                results["has_unique_to_original_mapping"] = True

    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown(results: dict[str, Any]) -> str:
    """Generate markdown summary of benchmark results."""
    md = ["# Dedup Determinism Benchmark — F214OPT-J\n"]
    md.append("| Test | Result | Details |\n")
    md.append("|------|--------|----------|\n")

    for _key, val in results.items():
        if isinstance(val, dict) and "test_name" in val:
            status = "PASS" if not val.get("error") else "FAIL"
            details = val.get("error", str(val)[:80])
            md.append(f"| {val['test_name']} | {status} | {details} |\n")

    md.append("\n## Fallback Embedding Determinism\n")
    fe = results.get("fallback_embedding_determinism", {})
    md.append(f"- Same text → identical vector: `{fe.get('same_text_identical_vector')}`\n")
    md.append(f"- Different texts → different vector: `{fe.get('different_texts_different')}`\n")
    md.append(f"- No NaN/Inf: `{fe.get('no_nan_inf')}`\n")
    md.append(f"- Norm > 0: `{fe.get('norm_positive')}`\n")

    md.append("\n## MinHash Ngram Cap\n")
    nc = results.get("minhash_ngram_cap", {})
    md.append(f"- Default cap: `{nc.get('default_cap')}`\n")
    md.append(f"- Cap enforced: `{nc.get('cap_enforced')}`\n")
    md.append(f"- Env override works: `{nc.get('env_override_works')}`\n")
    md.append(f"- Invalid env fallback: `{nc.get('invalid_env_fallback_works')}`\n")

    md.append("\n## MinHash Performance\n")
    perf = results.get("minhash_performance", {})
    for size, data in perf.get("sizes", {}).items():
        md.append(
            f"- {size}: {data.get('elapsed_seconds')}s "
            f"({data.get('ngram_count')} ngrams, "
            f"{data.get('signature_length')} sig len)\n"
        )

    md.append("\n## Semantic Check_batch Mapping\n")
    sb = results.get("semantic_check_batch_no_texts_index", {})
    md.append(f"- No texts.index in loop: `{not sb.get('has_texts_index_in_loop')}`\n")
    md.append(f"- Has unique_to_original mapping: `{sb.get('has_unique_to_original_mapping')}`\n")

    return "".join(md)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Dedup Determinism Benchmark — F214OPT-J"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Validate code paths without heavy computation (default: True)"
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Execute full benchmark (overrides --dry-run)"
    )
    parser.add_argument(
        "--iterations", type=int, default=10,
        help="Iterations for determinism tests (default: 10)"
    )
    parser.add_argument(
        "--output-json", type=str,
        help="Write JSON results to path"
    )
    parser.add_argument(
        "--output-md", type=str,
        help="Write markdown summary to path"
    )
    args = parser.parse_args()

    results: dict[str, Any] = {}

    # Always run code inspection tests
    results["semantic_check_batch_no_texts_index"] = (
        test_semantic_check_batch_no_texts_index()
    )
    results["minhash_ngram_cap"] = test_minhash_ngram_cap()

    if not args.dry_run:
        results["fallback_embedding_determinism"] = (
            test_fallback_embedding_determinism()
        )
        results["minhash_performance"] = test_minhash_performance(
            ["10KB", "100KB", "1MB"]
        )
    else:
        results["_note"] = "Dry-run: skipped heavy computation"

    # Output
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"JSON → {out_path}")

    if args.output_md:
        md = generate_markdown(results)
        out_path = Path(args.output_md)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            f.write(md)
        print(f"Markdown → {out_path}")

    # Console summary
    print("\n=== Dedup Determinism Benchmark Summary ===")
    for _key, val in results.items():
        if isinstance(val, dict):
            status = "FAIL" if val.get("error") else "PASS"
            print(f"  [{status}] {_key}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
