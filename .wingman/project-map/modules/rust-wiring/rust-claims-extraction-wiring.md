# rust-claims-extraction-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/claims_extraction_wiring.py`  
**Status:** current

## Purpose

Rust-accelerated claim extraction from unstructured text. Extracts factual claims for knowledge graph population.

## Key Functions

| Function | Purpose |
|----------|---------|
| `extract_claims(text)` | Extract claims as structured JSON |
| `normalize_claim(claim)` | Normalize claim text |
| `score_claim(claim)` | Score claim confidence |

## Invariants

- [RCE-1] Claims extracted as JSON for zero-copy transfer
- [RCE-2] Max claim length: 500 chars
- [RCE-3] Confidence threshold: 0.7 for storage
