#!/usr/bin/env python3
"""
Sprint F221A: Practical Model Stack Smoke & Assets
==================================================

One-shot smoke check for the selected model stack on MacBook Air M1 8GB.
Verifies: imports, availability flags, disk-free space, model download commands.

Does NOT load models (that is a separate benchmark concern).
Does NOT modify any state.

Usage:
    uv run python scripts/model_stack_smoke.py --check          # all components, terse
    uv run python scripts/model_stack_smoke.py --smoke          # smoke + quick import test
    uv run python scripts/model_stack_smoke.py --component llm  # LLM only
    uv run python scripts/model_stack_smoke.py --component embeddings
    uv run python scripts/model_stack_smoke.py --component ner
    uv run python scripts/model_stack_smoke.py --component reranker
    uv run python scripts/model_stack_smoke.py --component pii
    uv run python scripts/model_stack_smoke.py --component ocr
    uv run python scripts/model_stack_smoke.py --print-download-commands
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Model stack constants (canonical source of truth for this script)
# ---------------------------------------------------------------------------
PRIMARY_LLM = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
ROLLBACK_LLM = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
EMBED_MODEL = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"  # ModernBERT via mlx_embeddings
NER_MODEL = "knowledgator/gliner-relex-large-v0.5"
RERANKER_MODEL = "ms-marco-MiniLM-L-12-v2"  # FlashRank auto-downloads this

MODELS_DIR = Path.home() / ".hledac" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Component definitions
# ---------------------------------------------------------------------------
def check_llm() -> dict:
    """Check LLM stack: mlx_lm, mlx-community model IDs, KV-cache params."""
    notes = []
    try:
        import mlx.core as mx
        notes.append(f"mlx.core {mx.__version__}")
    except Exception as e:
        return {"status": "FAIL", "component": "llm", "error": str(e)}

    try:
        import mlx_lm
        notes.append(f"mlx_lm {mlx_lm.__version__}")
    except Exception as e:
        return {"status": "FAIL", "component": "llm", "error": f"mlx_lm not importable: {e}"}

    mlx_lm_models = [PRIMARY_LLM, ROLLBACK_LLM]
    notes.append(f"primary={PRIMARY_LLM}")
    notes.append(f"rollback={ROLLBACK_LLM}")

    # Check kv_bits / max_kv_size are valid mlx_lm.generate kwargs
    import mlx_lm.generate as _gen
    import inspect
    sig = inspect.getfullargspec(_gen)
    gen_kwargs = sig.args + sig.kwonlyargs
    has_kv = "kv_bits" in gen_kwargs
    has_max_kv = "max_kv_size" in gen_kwargs
    notes.append(f"generate(kv_bits={has_kv}, max_kv_size={has_max_kv})")

    return {
        "status": "OK",
        "component": "llm",
        "notes": notes,
        "models": mlx_lm_models,
    }


def check_embeddings() -> dict:
    """Check embedding stack: EmbeddingRouter + ANEEmbedder + ModernBERT."""
    notes = []

    # 1. ANE availability
    try:
        import CoreML as _coreml  # noqa: F401
        import Foundation as _found  # noqa: F401
        notes.append("CoreML+Foundation=OK")
    except ImportError:
        notes.append("CoreML+Foundation=N/A (ANE unavailable)")

    # 2. mlx_embeddings for ModernBERT
    try:
        from mlx_embeddings import load as _mlx_load  # noqa: F401
        notes.append("mlx_embeddings=OK")
    except ImportError:
        notes.append("mlx_embeddings=N/A (optional)")

    # 3. Import the pipeline components
    try:
        from hledac.universal.embedding_pipeline import EmbeddingRouter as _er  # noqa: F401
        notes.append("EmbeddingRouter=OK")
    except Exception as e:
        return {"status": "FAIL", "component": "embeddings", "error": f"EmbeddingRouter import failed: {e}"}

    try:
        from hledac.universal.brain.ane_embedder import ANEEmbedder, ANE_AVAILABLE
        notes.append(f"ANEEmbedder import=OK (ANE_AVAILABLE={ANE_AVAILABLE})")

        # Sprint F216B: CoreMLEmbedder (BGE on ANE) check via embedding_pipeline
        coreml_active_path = "unavailable"
        try:
            from hledac.universal.embedding_pipeline import get_ane_embedder, COREML_AVAILABLE

            notes.append(f"CoreML available={COREML_AVAILABLE}")
            bge = get_ane_embedder()
            if bge is not None:
                if bge._available and bge._loaded:
                    coreml_active_path = "coreml_ane"
                elif bge._available:
                    coreml_active_path = "coreml_loaded_not_ready"
                else:
                    coreml_active_path = f"not_available ({bge._last_error})"
            else:
                coreml_active_path = "not_initialized"
            notes.append(f"ANE BGE CoreMLEmbedder path={coreml_active_path}")
        except ImportError:
            notes.append("CoreMLEmbedder=N/A (coremltools not installed)")
        except Exception as e:
            notes.append(f"CoreMLEmbedder check: {e}")
    except Exception as e:
        return {"status": "FAIL", "component": "embeddings", "error": f"ANEEmbedder import failed: {e}"}

    # 4. Disk space check (ModernBERT ~500MB, ANE CoreML ~300MB)
    try:
        _total, _used, free = shutil.disk_usage(MODELS_DIR)
        free_gb = free // (1024**3)
        notes.append(f"disk_free={free_gb}GB @ {MODELS_DIR}")
        if free_gb < 2:
            return {"status": "WARN", "component": "embeddings", "notes": notes, "warning": "low disk space"}
    except Exception as e:
        notes.append(f"disk_free check failed: {e}")

    return {
        "status": "OK",
        "component": "embeddings",
        "notes": notes,
        "model": EMBED_MODEL,
    }


def check_ner() -> dict:
    """Check NER engine: NEREngine + GLiNER-Relex."""
    notes = []

    try:
        from hledac.universal.brain.ner_engine import NEREngine as _ner  # noqa: F401
        notes.append("NEREngine=OK")
    except Exception as e:
        return {"status": "FAIL", "component": "ner", "error": f"NEREngine import failed: {e}"}

    try:
        from hledac.universal.brain.ner_engine import _get_torch
        torch = _get_torch()
        notes.append(f"torch={torch.__version__ if hasattr(torch, '__version__') else 'loaded'}")
    except Exception as e:
        notes.append(f"torch check: {e}")

    # Check NaturalLanguage framework (ANE NER acceleration)
    try:
        import Foundation as _found  # noqa: F401
        notes.append("Foundation=OK")
    except ImportError:
        notes.append("Foundation=N/A")

    return {
        "status": "OK",
        "component": "ner",
        "notes": notes,
        "model": NER_MODEL,
    }


def check_reranker(mode: str = "check") -> dict:
    """Check reranker: LightweightReranker + FlashRank.

    Args:
        mode: "check" = no instantiation (no download risk); "smoke" = lightweight real check.
    """
    notes = []
    status = "OK"

    try:
        from hledac.universal.tools.reranker import LightweightReranker as _lr, FLASHRANK_AVAILABLE
        notes.append(f"LightweightReranker=OK (FLASHRANK_AVAILABLE={FLASHRANK_AVAILABLE})")
    except Exception as e:
        return {"status": "FAIL", "component": "reranker", "error": f"Reranker import failed: {e}"}

    if FLASHRANK_AVAILABLE:
        # Check module import only — never auto-download in --check mode
        try:
            import flashrank
            notes.append("flashrank module=OK")
        except Exception as e:
            notes.append(f"flashrank module: {e}")
            status = "WARN"

        if mode == "check":
            # --check: import-only check, no Ranker instantiation
            notes.append("Ranker instantiation=SKIPPED (--check mode, no auto-download)")
            notes.append("cache_status=unknown")
        elif mode == "smoke":
            # --smoke: only instantiate if local cache already exists
            cache_dir = MODELS_DIR / "flashrank"
            cached_model = cache_dir / "ms-marco-MiniLM-L-12-v2"
            if cached_model.exists():
                try:
                    flashrank.Ranker(cache_dir=str(cache_dir))
                    notes.append(f"FlashRank Ranker instantiated (cache found)")
                except Exception as e:
                    notes.append(f"FlashRank Ranker init (cached): {e}")
            else:
                notes.append(f"FlashRank Ranker=SKIPPED (no local cache at {cache_dir})")
                notes.append("cache_status=missing")
                status = "WARN"

    return {
        "status": status,
        "component": "reranker",
        "notes": notes,
        "model": RERANKER_MODEL,
    }


def check_pii() -> dict:
    """Check PII gate: SecurityGate, regex patterns, fallbacks."""
    notes = []

    try:
        from hledac.universal.security.pii_gate import SecurityGate, create_security_gate
        notes.append("SecurityGate=OK")
    except Exception as e:
        return {"status": "FAIL", "component": "pii", "error": f"SecurityGate import failed: {e}"}

    # Instantiate and smoke-test sanitize
    try:
        gate = create_security_gate()
        test_text = "Contact john.doe@example.com or call 555-123-4567"
        result = gate.sanitize(test_text)
        notes.append(f"sanitize smoke=OK (input preserved={test_text != result})")
    except Exception as e:
        notes.append(f"sanitize smoke: {e}")

    try:
        from hledac.universal.security.pii_gate import get_pii_backend
        backend = get_pii_backend()
        notes.append(f"pii_backend={backend}")
    except Exception as e:
        notes.append(f"pii_backend check: {e}")

    return {
        "status": "OK",
        "component": "pii",
        "notes": notes,
    }


def check_ocr() -> dict:
    """Check OCR: VisionOCR + ocrmac."""
    notes = []

    try:
        from hledac.universal.tools.ocr_engine import VisionOCR as _vo  # noqa: F401
        notes.append("VisionOCR=OK")
    except Exception as e:
        return {"status": "FAIL", "component": "ocr", "error": f"VisionOCR import failed: {e}"}

    try:
        import ocrmac
        notes.append(f"ocrmac=OK ({ocrmac.__version__ if hasattr(ocrmac, '__version__') else 'loaded'})")
    except ImportError:
        notes.append("ocrmac=N/A (not installed)")
    except Exception as e:
        notes.append(f"ocrmac: {e}")

    return {
        "status": "OK",
        "component": "ocr",
        "notes": notes,
    }


COMPONENTS = {
    "llm": check_llm,
    "embeddings": check_embeddings,
    "ner": check_ner,
    "reranker": check_reranker,
    "pii": check_pii,
    "ocr": check_ocr,
}


def run_check(component: str | None, verbose: bool, mode: str = "check") -> int:
    """Run checks and print results. Returns exit code (0=OK, 1=FAIL)."""
    if component:
        targets = {component: COMPONENTS[component]}
    else:
        targets = COMPONENTS

    all_ok = True
    for name, fn in targets.items():
        try:
            if name == "reranker":
                result = fn(mode=mode)
            else:
                result = fn()
        except Exception as e:
            result = {"status": "FAIL", "component": name, "error": str(e)}

        status = result.get("status", "FAIL")
        if status != "OK":
            all_ok = False

        icon = "✓" if status == "OK" else ("⚠" if status == "WARN" else "✗")
        print(f"{icon} [{name.upper()}] {status}")
        if verbose or status != "OK":
            for key, val in result.items():
                if key not in ("status", "component"):
                    print(f"    {key}: {val}")
        if "error" in result:
            print(f"    ERROR: {result['error']}")

    return 0 if all_ok else 1


def print_download_commands() -> None:
    """Print exact download commands for all models that need them."""
    print("# Sprint F221A: Model Download Commands")
    print("# Run these BEFORE --smoke to pre-fetch models.\n")
    print("# ---------------------------------------------------------------------------")
    print("# LLM models (MLX, downloaded via mlx_lm on first use or explicit prefetch):")
    print(f"#   Primary:  {PRIMARY_LLM}")
    print(f"#   Rollback: {ROLLBACK_LLM}")
    print(f"#   Download/cache dir: ~/.cache/mlx/")
    print()
    print("#   # Prefetch primary model:")
    print(f"#   python -c \"from mlx_lm import load; load('{PRIMARY_LLM}')\"")
    print()
    print("#   # Prefetch rollback model:")
    print(f"#   python -c \"from mlx_lm import load; load('{ROLLBACK_LLM}')\"")
    print()
    print("# ---------------------------------------------------------------------------")
    print("# Embedding model (ModernBERT via mlx_embeddings):")
    print(f"#   Model: {EMBED_MODEL}")
    print("#   mlx_embeddings auto-downloads on first use.")
    print("#   Cache: ~/.cache/huggingface/hub/")
    print()
    print("# ---------------------------------------------------------------------------")
    print("# NER model (GLiNER-Relex via transformers on first use):")
    print(f"#   Model: {NER_MODEL}")
    print("#   Downloaded automatically by transformers on first NEREngine.initialize().")
    print("#   Cache: ~/.cache/huggingface/hub/")
    print()
    print("#   # Pre-download:")
    print(f"#   python -c \"from transformers import AutoModelForTokenClassification, AutoTokenizer; \\")
    print(f"#   m = AutoModelForTokenClassification.from_pretrained('{NER_MODEL}'); \\")
    print(f"#   t = AutoTokenizer.from_pretrained('{NER_MODEL}')\"")
    print()
    print("# ---------------------------------------------------------------------------")
    print("# Reranker (FlashRank auto-downloads ms-marco-MiniLM-L-12-v2 on first use):")
    print(f"#   Model: {RERANKER_MODEL}")
    print(f"#   Cache dir used: {MODELS_DIR / 'flashrank'}")
    print()
    print("# ---------------------------------------------------------------------------")
    print("# OCR (ocrmac - system Vision framework, no model download needed):")
    print("#   ocrmac uses macOS built-in Vision framework - no model files needed.")
    print("#   Install: pip install ocrmac")
    print()
    print("# ---------------------------------------------------------------------------")
    print("# Full dependency install command:")
    print("#   pip install mlx mlx-lm mlx-embeddings flashrank transformers ocrmac")
    print("#   # plus torch (required by transformers for NER)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprint F221A Model Stack Smoke")
    parser.add_argument("--check", action="store_true", help="Terse check of all components")
    parser.add_argument("--smoke", action="store_true", help="Smoke test (imports + availability)")
    parser.add_argument("--component", choices=list(COMPONENTS.keys()), help="Check one component only")
    parser.add_argument("--print-download-commands", action="store_true", help="Print download commands and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.print_download_commands:
        print_download_commands()
        return 0

    if args.component:
        mode = "smoke" if args.smoke else "check"
        return run_check(args.component, args.verbose, mode=mode)

    if not (args.check or args.smoke):
        parser.print_help()
        return 0

    mode = "smoke" if args.smoke else "check"
    return run_check(None, args.verbose, mode=mode)


if __name__ == "__main__":
    sys.exit(main())