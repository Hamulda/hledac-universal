# security-quantum-crypto

**Type:** Security Layer  
**Path:** `security/quantum_resistant_crypto.py`  
**Status:** current

## Purpose

Post-quantum cryptographic primitives for long-term secrecy. CRYSTALS-Dilithium, CRYSTALS-Kyber, SPHINCS+.

## Key Functions

| Function | Purpose |
|----------|---------|
| `generate_keypair()` | Generate PQC keypair |
| `encapsulate(public_key)` | KEM encapsulation |
| `decapsulate(ciphertext)` | KEM decapsulation |
| `sign(message)` | Dilithium signature |
| `verify(message, signature)` | Verify signature |

## Invariants

- [SQC-1] Default: Dilithium3 for signatures, Kyber768 for KEM
- [SQC-2] Hybrid mode: combine classical + PQC
- [SQC-3] Key sizes: Dilithium3 ~4KB pubkey, Kyber768 ~1KB pubkey

## Dependencies

- `pqcrypto` or Rust `pqcrypto` bindings
