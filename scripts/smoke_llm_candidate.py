#!/usr/bin/env python3
"""
Sprint F217C: Smoke script for LLM candidate resolution.

Usage:
    uv run python scripts/smoke_llm_candidate.py --candidate default
    uv run python scripts/smoke_llm_candidate.py --candidate deephermes
    uv run python scripts/smoke_llm_candidate.py --candidate hermes

Prints resolved model ID.
Attempts one tiny generation only if model is locally available.
If missing locally, prints clear missing model message.
Does NOT download models.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add Hledac project root to sys.path so 'hledac' namespace package resolves.
# universal/scripts/ → universal/ → hledac/ → Hledac/ (project root)
# The hledac/ directory is a namespace package; its parent (Hledac/) is what we need.
_project_root = Path(__file__).parent.parent  # → universal/
_hledac_root = _project_root.parent  # → hledac/ (namespace package dir)
_project_root_of_hledac = _hledac_root.parent  # → Hledac/ (project root with pyproject.toml)
sys.path.insert(0, str(_project_root_of_hledac))


def check_model_locally(model_id: str) -> bool:
    """Check if model is cached locally in mlx_lm cache."""
    cache_paths = [
        Path.home() / ".cache" / "mlx_lm" / model_id.split("/", 1)[1],
        Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_id.replace('/', '--')}",
    ]
    for p in cache_paths:
        if p.exists():
            return True
    return False


def tiny_generate(model_id: str) -> dict:
    """Attempt a single tiny generation, no download."""
    if not check_model_locally(model_id):
        return {
            "status": "missing_local_model",
            "model_id": model_id,
            "message": f"Model not cached locally at ~/.cache/mlx/ or ~/.cache/huggingface/. "
                       f"Download to run generation: `mlx_lm.download('{model_id}')`",
        }

    try:
        import mlx_lm
    except ImportError:
        return {
            "status": "error",
            "model_id": model_id,
            "message": "mlx_lm not available — cannot generate",
        }

    try:
        prompt = "Return only valid JSON: {\"status\":\"ok\"}"
        response = mlx_lm.generate(
            model_id,
            prompt,
            max_tokens=32,
            temp=0.0,
        )
        return {
            "status": "ok",
            "model_id": model_id,
            "response": response.strip(),
        }
    except Exception as e:
        return {
            "status": "error",
            "model_id": model_id,
            "message": str(e),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test LLM candidate resolution")
    parser.add_argument(
        "--candidate",
        default="default",
        choices=["default", "deephermes", "hermes", "nanbeige", "smollm3", "all"],
        help="Candidate key to resolve",
    )
    args = parser.parse_args()

    from hledac.universal.brain.llm_candidate_registry import (
        get_primary_reasoner_candidate,
        get_fallback_reasoner_candidate,
        LLM_CANDIDATES,
    )

    if args.candidate == "default":
        primary = get_primary_reasoner_candidate()
        print(f"Primary reasoner: {primary.model_key} -> {primary.model_id}")
        result = tiny_generate(primary.model_id)
        print(json.dumps(result, indent=2))
    elif args.candidate == "all":
        for key in LLM_CANDIDATES:
            cand = LLM_CANDIDATES[key]
            print(f"  {key}: {cand['model_id']} (default={cand['is_current_default']})")
    else:
        cand = LLM_CANDIDATES.get(args.candidate)
        if not cand:
            print(f"Unknown candidate: {args.candidate}", file=sys.stderr)
            sys.exit(1)
        print(f"Candidate '{args.candidate}': {cand['model_id']}")
        result = tiny_generate(cand["model_id"])
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()