# Qoder Doc Patch Report — Sprint F206AP

## Overview
Patched HIGH severity Qoder documentation overclaims in two files.

## Files Patched

### 1. Specialized Domain Probes.md
- **Patches applied:** 2 HIGH severity (canonical + production overclaims)
- **Modules affected:** `layers/stealth_layer.py`, `layers/temporal_signal_layer.py`, `layers/temporal_signal_store.py`, `layers/temporal_signal_runtime.py`
- **Verdict:** DONOR_OR_OPTIONAL
- **Changes:**
  - Removed "canonical probe infrastructure" language for stealth/temporal-signal layers
  - Removed "production probe surfaces" language
  - Added Reality status blocks to Introduction, Project Structure, Core Components, and Architecture Overview sections

### 2. Benchmark and Performance Probes.md
- **Patches applied:** 2 HIGH severity (canonical + wired overclaims)
- **Modules affected:** `benchmarks/benchmark_pipeline.py`, `benchmarks/benchmark_sprint_probe.py`, `benchmarks/e2e_canonical_benchmark.py`, `benchmarks/e2e_compare.py`, `benchmarks/research_effectiveness.py`
- **Verdict:** TEST_ONLY
- **Changes:**
  - Removed "canonical performance measurement" language
  - Removed "wired into live pipelines" language
  - Added Reality status blocks to Introduction and Architecture Overview sections

## Reality Status Blocks Added
- 4 Reality status blocks total (2 per patched file)
- Format: Runtime verdict, Canonical hot path, Production write path, Test/benchmark role

## Scope Limitations
- Only 2 of 45 affected docs patched
- Only top 4 HIGH severity patches applied
- 17 MEDIUM and 101 LOW severity overclaims remain
- No production Python/Swift files modified
- No runtime behavior changes

## Verification
```bash
rtk proxy python -m pytest -q tests/probe_qoder_doc_patch_f206ap
```
