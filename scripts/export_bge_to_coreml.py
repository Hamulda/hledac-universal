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

from pathlib import Path

try:
    import coremltools as ct
    from coremltools import ComputeUnit
except ImportError as e:
    raise RuntimeError(f"coremltools not available: {e}") from e

# Model paths — use HuggingFace Hub cache or local
DEFAULT_MODEL_ID = "BAAI/bge-small-en-v1.5"
OUTPUT_NAME = "bge-small-ane"

# Inline MODELS_ROOT — utils/paths.py doesn't exist yet
_MODELS_ROOT = Path.home() / ".hledac" / "models"


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


import psutil


def _check_ram_before_export() -> None:
    """F218A: Verify sufficient RAM before export to avoid crash on M1 8GB."""
    ram = psutil.virtual_memory()
    if ram.percent > 75.0:
        print(f"WARNING: RAM usage {ram.percent:.1f}% > 75%")
        print("Export may fail. Close other applications and retry.")
        print(f"Available: {ram.available // (1024**3)} GB")
        response = input("Continue anyway? [y/N]: ")
        if response.lower() != "y":
            sys.exit(0)


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
    dry_run: bool = False,
) -> Path | dict:
    """
    Export BGE ONNX model to CoreML mlpackage.

    Args:
        model_id: HuggingFace model ID for BGE model
        output_path: Output path for mlpackage (default: MODELS_ROOT / bge-small-en-v1.5.mlpackage)
        compute_units: CoreML compute units (ALL = ANE + GPU + CPU)
        quantize: Apply quantization to reduce size
        dry_run: If True, validate model loads correctly and print config summary.
                 Does NOT save the mlmodel. Returns dict with validation results.

    Returns:
        Path to exported mlpackage (dry_run=False), or dict with validation results (dry_run=True)
    """
    from sentence_transformers import SentenceTransformer

    # Resolve output path
    if output_path is None:
        output_path = _MODELS_ROOT / f"{OUTPUT_NAME}.mlpackage"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[EXPORT] Loading FastEmbed model: {model_id}")
    print(f"[EXPORT] Output: {output_path}")
    print(f"[EXPORT] Compute units: {compute_units}")

    # Load via sentence-transformers (FastEmbed backend)
    model = SentenceTransformer(model_id)

    print(f"[EXPORT] Model loaded. Embedding dim: {model.get_embedding_dimension()}")
    embedding_dim = model.get_embedding_dimension()

    # dry_run: validate config only, no save
    if dry_run:
        result = {
            "status": "ok",
            "model_id": model_id,
            "embedding_dim": embedding_dim,
            "compute_units": str(compute_units),
            "quantize": quantize,
            "output_path": str(output_path),
        }
        print(f"[DRY-RUN] Config validated — model_id={model_id}")
        print(f"[DRY-RUN] embedding_dim={embedding_dim}, compute_units={compute_units}")
        print(f"[DRY-RUN] quantize={quantize}, output_path={output_path}")
        print("[DRY-RUN] No mlmodel saved (dry_run=True)")
        return result

    # Convert to CoreML — requires TorchScript tracing in coremltools 9.0
    import torch

    print("[EXPORT] Tracing model with dummy input...")
    dummy_text = ["test text"]
    model.eval()
    model.to("cpu")
    # Detach all parameters before tracing
    for p in model.parameters():
        p.requires_grad_(False)
        p.grad = None
    with torch.no_grad():
        features = model.tokenizer(dummy_text, return_tensors="pt", padding=True, truncation=True, max_length=512)
        plain_dict = {k: v.clone().detach() for k, v in features.items() if k in ("input_ids", "attention_mask")}

        class TracedTransformer(torch.nn.Module):
            def __init__(self, transformer):
                super().__init__()
                self.transformer = transformer

            def forward(self, x):
                return self.transformer(x)

        traced_mod = TracedTransformer(model[0])
        traced = torch.jit.trace(traced_mod, (plain_dict,), strict=False)

    print("[EXPORT] Converting traced model to CoreML...")
    mlmodel = ct.convert(
        traced,
        compute_units=compute_units,
        source="pytorch",
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
    import argparse

    # === F218A: RAM pre-check before export ===
    _check_ram_before_export()

    parser = argparse.ArgumentParser(description="Export BGE to CoreML")
    parser.add_argument("--dry-run", action="store_true", help="Validate config only, do not save mlmodel")
    args = parser.parse_args()

    print("=== BGE → CoreML Export ===")
    print(f"coremltools version: {ct.__version__}")

    # Check for existing export
    output_path = _MODELS_ROOT / f"{OUTPUT_NAME}.mlpackage"
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[EXPORT] Already exists: {output_path} ({size_mb:.1f}MB)")
        print("[EXPORT] Skipping — delete .mlpackage to re-export")
        return 0

    try:
        result = export_bge_to_coreml(dry_run=args.dry_run)
        if args.dry_run:
            print(f"\n[DRY-RUN] Validation complete: {result}")
        else:
            print(f"\n[SUCCESS] CoreML model exported to:\n  {result}")
        return 0
    except Exception as e:
        print(f"\n[FAILED] Export failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
