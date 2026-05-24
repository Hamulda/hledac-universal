#!/usr/bin/env python3
"""
Export BAAI/bge-small-en-v1.5 ONNX model to CoreML mlpackage format.

Usage:
    python scripts/export_bge_to_coreml.py

Requires:
    - macOS (uses coremltools)
    - FastEmbed ONNX model downloaded (~100MB)
    - coremltools package

Compute units: ct.ComputeUnit.ALL (ANE + GPU + CPU)

Runtime condition: script only runs on macOS (sys.platform check).
"""

from __future__ import annotations

import sys

# Runtime platform check — only run on macOS
if sys.platform != "darwin":
    raise RuntimeError("CoreML export only supported on macOS")

import json
from pathlib import Path

try:
    import coremltools as ct
    from coremltools import ComputeUnit
except ImportError as e:
    raise RuntimeError(f"coremltools not available: {e}") from e

# Model paths — use HuggingFace Hub cache or local
DEFAULT_MODEL_ID = "BAAI/bge-small-en-v1.5"
OUTPUT_NAME = "bge-small-en-v1.5"


def _get_hf_cache_path(model_id: str) -> Path:
    """Get HuggingFace Hub cache path for model."""
    # HF cache default: ~/.cache/huggingface/hub/
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    # FastEmbed downloads to subdirectory named by model_id hash
    for d in hf_cache.iterdir():
        if d.is_dir() and model_id in d.name:
            # Look for ONNX files
            onnx_files = list(d.rglob("*.onnx"))
            if onnx_files:
                return onnx_files[0].parent
    return None


def _find_onnx_model() -> Path | None:
    """Locate downloaded ONNX model from FastEmbed."""
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if not hf_cache.exists():
        return None
    # FastEmbed stores ONNX in subdirectories with hashed names
    for d in hf_cache.iterdir():
        if not d.is_dir():
            continue
        onnx_files = list(d.rglob("*.onnx"))
        for f in onnx_files:
            if "bge" in f.name.lower() or "bge" in f.stem.lower():
                return f
    return None


def _find_local_onnx() -> Path | None:
    """Check local models directory for existing ONNX."""
    # Check common local paths
    candidates = [
        Path("~/.hledac/models/bge-small-en-v1.5.onnx").expanduser(),
        Path("models/bge-small-en-v1.5.onnx"),
        Path("/tmp/bge-small-en-v1.5.onnx"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def export_bge_to_coreml(
    model_id: str = DEFAULT_MODEL_ID,
    output_path: Path | None = None,
    compute_units: ComputeUnit = ComputeUnit.ALL,
    quantize: bool = False,
) -> Path:
    """
    Export BGE ONNX model to CoreML mlpackage.

    Args:
        model_id: HuggingFace model ID for BGE model
        output_path: Output path for mlpackage (default: MODELS_ROOT / bge-small-en-v1.5.mlpackage)
        compute_units: CoreML compute units (ALL = ANE + GPU + CPU)
        quantize: Apply quantization to reduce size

    Returns:
        Path to exported mlpackage
    """
    from sentence_transformers import SentenceTransformer

    # Resolve output path
    from hledac.universal.utils.paths import MODELS_ROOT

    if output_path is None:
        output_path = MODELS_ROOT / f"{OUTPUT_NAME}.mlpackage"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[EXPORT] Loading FastEmbed model: {model_id}")
    print(f"[EXPORT] Output: {output_path}")
    print(f"[EXPORT] Compute units: {compute_units}")

    # Load via sentence-transformers (FastEmbed backend)
    model = SentenceTransformer(model_id)

    print(f"[EXPORT] Model loaded. Embedding dim: {model.get_sentence_embedding_dimension()}")

    # Convert to CoreML
    # coremltools.convert() accepts a HuggingFace model or local path
    mlmodel = ct.convert(
        model,
        compute_units=compute_units,
        pass_pipeline_tokens_to_itself=True,
    )

    # Quantize if requested (reduces size ~4x, may affect quality)
    if quantize:
        print("[EXPORT] Quantizing to float16...")
        mlmodel = ct.models.quantization.quantize_weights(mlmodel, nbits=16)

    # Save
    print(f"[EXPORT] Saving to {output_path}...")
    mlmodel.save(str(output_path))

    # Verify
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[EXPORT] SUCCESS: {output_path} ({size_mb:.1f}MB)")
    else:
        raise RuntimeError(f"Export failed: {output_path} not created")

    return output_path


def main() -> int:
    """CLI entry point."""
    print("=== BGE → CoreML Export ===")
    print(f"coremltools version: {ct.__version__}")

    # Check for existing export
    from hledac.universal.utils.paths import MODELS_ROOT

    output_path = MODELS_ROOT / f"{OUTPUT_NAME}.mlpackage"
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[EXPORT] Already exists: {output_path} ({size_mb:.1f}MB)")
        print("[EXPORT] Skipping — delete .mlpackage to re-export")
        return 0

    try:
        path = export_bge_to_coreml()
        print(f"\n[SUCCESS] CoreML model exported to:\n  {path}")
        return 0
    except Exception as e:
        print(f"\n[FAILED] Export failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())