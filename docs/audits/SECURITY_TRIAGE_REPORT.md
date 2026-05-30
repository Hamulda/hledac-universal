# Security Stack Triage Report

**Date:** 2026-05-30
**Auditor:** Claude Code
**Scope:** security/ + shims/security/

## Executive Summary

| Category | Count | Action |
|----------|-------|--------|
| A) REAL IMPL + Sidecar | 2 | captcha_detector, secure_enclave |
| B) REAL IMPL (not sidecar) | 4 | vault_manager, audit, obfuscation, destruction |
| C) STUB / EXPERIMENTAL | 3 | zkp_research_engine, NeuromorphicCryptoEngine, captcha_detector (partial) |
| D) PQ Wired | 2 | pq_crypto, pq_export_encryption |

## Classification Table

| Module | Lines | Production Imports | Classification | Action |
|--------|-------|-------------------|----------------|--------|
| `security/vault_manager.py` | 369 | 0 direct | **A) REAL** | Add to `__init__.py` with gating |
| `security/audit.py` | 359 | 1 (deep_research_security) | **B) REAL** | Add to `__init__.py` with gating |
| `security/obfuscation.py` | 326 | 1 (legacy) | **B) REAL** | Add to `__init__.py` with gating |
| `security/destruction.py` | 289 | 1 (legacy) | **B) REAL** | Add to `__init__.py` with gating |
| `security/pq_crypto.py` | 347 | 0 direct | **A) PQ Wired** | Already in `__init__.py` ✓ |
| `security/pq_export_encryption.py` | 402 | 0 direct | **A) PQ Wired** | Add to `__init__.py` |
| `security/secure_enclave.py` | 196 | 1 (rag_engine) | **A) REAL** | Already in `__init__.py` ✓ |
| `security/ram_vault.py` | 152 | 0 direct | **B) REAL** | Already in `__init__.py` ✓ |
| `security/captcha_detector.py` | 113 | 1 (fetch_coordinator) | **A) REAL** | Add to `__init__.py` ✓ |
| `security/quantum_safe.py` | 1231 | 1 (capabilities) | **C) EXPERIMENTAL** | Guard NeuromorphicCryptoEngine |
| `shims/security/zkp_research_engine.py` | NEW | 0 | **C) STUB** | Created stub with warning |

## Detailed Findings

### Step 1: ZKP Assessment

**File:** `shims/security/zkp_research_engine.py` (NEW)

**Decision:** ZKP is complex cryptography requiring:
- Groth16 or PLONK proof systems (libsnark or circom WASM binding)
- R1CS constraint generation
- Trusted setup ceremonies

**Action Taken:**
- Created clean stub with `ZKPResearchEngine` class
- `HLEDAC_ENABLE_ZKP=1` shows warning instead of crashing
- `prove()` raises `ZKPError` with clear message
- `verify()` returns `False` (no false security)

### Step 2: NeuromorphicCryptoEngine Triage

**File:** `security/quantum_safe.py` (line 424)

**Decision:** "Spiking neural network based crypto" is experimental with NO production security value.

**Action Taken:**
- Added module-level docstring: "EXPERIMENTAL: Not for production use. Not security-reviewed."
- Added `assert os.environ.get("HLEDAC_EXPERIMENTAL_NEURO_CRYPTO") == "1"` at class `__init__`
- Prevents accidental use while keeping code for research

### Step 3: 14 Unverified Security Modules

| Module | Status | Gate | Notes |
|--------|--------|------|-------|
| vault_manager | REAL | None | LootManager/VaultManager with Fernet + pyzipper AES |
| audit | REAL | None | HMAC-protected audit trail with SQLite |
| obfuscation | REAL | None | Content obfuscation for research |
| destruction | REAL | None | DoD 5220.22-M / NIST 800-88 secure deletion |
| pq_crypto | REAL | PQ_AVAILABLE | ML-DSA-65 via Swift helper |
| pq_export_encryption | REAL | HPKE_AVAILABLE | HPKE X-Wing for export encryption |
| secure_enclave | REAL | ENCLAVE_AVAILABLE | Hardware-backed signing |
| ram_vault | REAL | None | macOS RAM disk via hdiutil |
| captcha_detector | REAL | None | PIL-only heuristics, fail-soft |
| quantum_safe | MIXED | HLEDAC_EXPERIMENTAL_NEURO_CRYPTO | ML-KEM/ML-DSA real, SNN experimental |

### Step 4: PQ Crypto Wiring

**Current State:**
- `pq_crypto.py` — ML-DSA-65 hybrid signatures, fully wired ✓
- `pq_export_encryption.py` — HPKE X-Wing export encryption, needs wiring to exporter

**Wiring Status:**
- `pq_crypto` is used in `capabilities.py` (production import)
- `pq_export_encryption` needs integration with `export/sprint_exporter.py`

**Recommendation:** Wire `pq_export_encryption` to exporter when `HLEDAC_ENABLE_PQ_EXPORT=1`:
```python
if os.environ.get("HLEDAC_ENABLE_PQ_EXPORT") == "1":
    from .pq_export_encryption import encrypt_export_bundle
    # Use HPKE X-Wing for STIX bundle encryption
```

## Environment Gates

| Gate | Module | Action |
|------|--------|--------|
| `HLEDAC_ENABLE_ZKP` | zkp_research_engine | Warning only (stub) |
| `HLEDAC_EXPERIMENTAL_NEURO_CRYPTO` | NeuromorphicCryptoEngine | Assert gate |
| `HLEDAC_ENABLE_PQ_EXPORT` | pq_export_encryption | Export encryption |
| `HLEDAC_ENABLE_CAPTCHA_DETECTION` | captcha_detector | Pre-filter |

## Modified Files

1. `shims/security/zkp_research_engine.py` — NEW stub created
2. `security/quantum_safe.py` — EXPERIMENTAL docstring + assert gate
3. `security/__init__.py` — Added CaptchaDetector export

## Next Steps

1. [ ] Wire `pq_export_encryption` to `export/sprint_exporter.py`
2. [ ] Add gating to vault_manager, audit, obfuscation, destruction in `__init__.py`
3. [ ] Run tests to verify no regressions
