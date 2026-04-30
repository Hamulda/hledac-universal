# REAL HPKE X-Wing Report — Sprint F206AC

**Date:** 2026-04-30
**macOS:** 26.4.1
**Swift:** 6.2.3 (swiftlang-6.2.3.3.21 clang-1700.6.3.2)
**SDK:** macOS 26.2
**CommandLineTools:** Active

---

## Environment Verification

| Check | Result |
|-------|--------|
| sw_vers -productVersion | 26.4.1 |
| swift --version | Apple Swift version 6.2.3 |
| xcrun --sdk macosx --show-sdk-version | 26.2 |
| xcode-select -p | /Library/Developer/CommandLineTools |

**Conclusion:** macOS 26.4.1 ≥ 26.0 — HPKE X-Wing CryptoKit API available.

---

## HPKE X-Wing Roundtrip Test

**File:** `/tmp/hpke_xwing_real_probe.swift`

**Test 1 — Keypair Generation**
```
✓ XWingMLKEM768X25519.PrivateKey.generate() — succeeded
  PublicKey.rawRepresentation: 1216 bytes
```

**Test 2 — HPKE Encap/Seal**
```
✓ HPKE.Sender(recipientKey:ciphersuite:info:) — succeeded
  encapsulatedKey: 1120 bytes
✓ sender.seal(plaintext, authenticating:) — succeeded
  ciphertext: 30 bytes (for "Hello, X-Wing!")
```

**Test 3 — HPKE Open/Recipient**
```
✓ HPKE.Recipient(privateKey:ciphersuite:info:encapsulatedKey:) — succeeded
✓ recipient.open(ciphertext, authenticating:) — succeeded
  decrypted: "Hello, X-Wing!"
```

**ROUNDTRIP: ✓ PASSED**

---

## Key Export/Import Roundtrip Test

**File:** `/tmp/hpke_key_export_test.swift`

| Key Type | Export Method | Size | Import Method | Result |
|----------|--------------|------|---------------|--------|
| PrivateKey | `integrityCheckedRepresentation` | 64 bytes | `PrivateKey(integrityCheckedRepresentation:)` | ✓ Roundtrip OK |
| PublicKey | `rawRepresentation` | 1216 bytes | `PublicKey(rawRepresentation:)` | ✓ Roundtrip OK |

**Conclusion:** Private key persistence via Keychain IS supported using `integrityCheckedRepresentation`.

---

## API Surface (CryptoKit HPKE X-Wing)

```
XWingMLKEM768X25519.PrivateKey
  .generate() -> PrivateKey
  .publicKey -> PublicKey
  .integrityCheckedRepresentation -> Data (64 bytes)

XWingMLKEM768X25519.PublicKey
  .rawRepresentation -> Data (1216 bytes)
  init(rawRepresentation: Data) throws

HPKE.Ciphersuite
  .XWingMLKEM768X25519_SHA256_AES_GCM_256

HPKE.Sender
  init(recipientKey: PK, ciphersuite: HPKE.Ciphersuite, info: Data) throws
  var encapsulatedKey: Data  (1120 bytes)
  func seal(_ plaintext: Data, authenticating aad: Data) throws -> Data

HPKE.Recipient
  init(privateKey: XWingMLKEM768X25519.PrivateKey, ciphersuite: HPKE.Ciphersuite, info: Data, encapsulatedKey: Data) throws
  func open(_ ciphertext: Data, authenticating aad: Data) throws -> Data
```

---

## Placeholder Audit Map

| Function | Type | Status |
|----------|------|--------|
| `hmacSign()` | Classical HMAC-SHA256 | RETAINED — classical compat only, never PQ path |
| `aesGCMEncrypt()` | Placeholder AES-GCM | REMOVED from HPKE path |
| `aesGCMDecrypt()` | Placeholder AES-GCM | REMOVED from HPKE path |
| `deriveX25519PublicKey()` | Placeholder X25519 | REMOVED from HPKE path |
| `deriveX25519SharedSecret()` | Placeholder X25519 | REMOVED from HPKE path |
| `deriveAESKey()` | Classical HKDF-SHA256 | RETAINED — used in Secure Enclave P-256 path |
| `hpkeStatus()` placeholder path | X25519+AES-GCM | REPLACED — real CryptoKit X-Wing |
| `hpkeGenerateRecipientKey()` placeholder | SecRandomCopyBytes | REPLACED — XWingMLKEM768X25519.PrivateKey.generate() |
| `hpkeEncrypt()` placeholder | X25519+AES-GCM | REPLACED — HPKE.Sender/seal() |
| `hpkeDecrypt()` placeholder | X25519+AES-GCM | REPLACED — HPKE.Recipient/open() |
| `checkHPKEAvailability()` | Compile-time check | REPLACED — actual CryptoKit probing |

---

## HPKE Commands — New JSON Schema

### hpke-status (success)
```json
{
  "ok": true,
  "available": true,
  "pq": true,
  "mode": "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
  "backend": "cryptokit",
  "secure_enclave_claimed": false
}
```

### hpke-status (unavailable)
```json
{
  "ok": false,
  "available": false,
  "pq": false,
  "error_code": "PQ_HPKE_NOT_AVAILABLE",
  "message": "macOS 26+ CryptoKit X-Wing HPKE required"
}
```

### hpke-generate-recipient-key (success)
```json
{
  "ok": true,
  "data": {
    "key_id": "com.hledac.pq.export.v1",
    "public_key_b64": "<XWingMLKEM768X25519.PublicKey rawRepresentation, base64>",
    "private_key_exported": "true",
    "private_key_exported_for_local_test": true,
    "persistence": false,
    "algorithm": "xwing-mlkem768x25519",
    "pq": "true",
    "status": "ready"
  }
}
```

### hpke-encrypt (success)
```json
{
  "ok": true,
  "data": {
    "encapsulated_key_b64": "<1120 bytes base64>",
    "ciphertext_b64": "<ciphertext base64>",
    "mode": "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
    "pq": "true"
  }
}
```

### hpke-decrypt (success)
```json
{
  "ok": true,
  "data": {
    "plaintext_b64": "<decrypted base64>",
    "algorithm": "xwing-mlkem768x25519",
    "pq": "true"
  }
}
```

---

## Classical Compat (Non-PQ) Path

`hmacSign()` and `deriveAESKey()` are retained but:
- Moved to CLASSICAL_COMPAT section with clear naming
- Never used by any hpke-* command
- Return `pq=false`, `backend=classical_compat`, `mode=CLASSICAL-X25519-AES-GCM-HMAC-COMPAT`
- Labeled with `CLASSICAL_COMPAT` comment

---

## Implementation Notes

1. **info parameter**: HPKE requires same `info` at both sender and recipient. Added `--info-b64` parameter to `hpke-encrypt` and `hpke-decrypt` commands. Default info: `"hledac.hpke.export.v1"`.

2. **Private key storage**: Store `integrityCheckedRepresentation` (64 bytes) in Keychain for persistence. On decrypt, retrieve and reconstruct with `XWingMLKEM768X25519.PrivateKey(integrityCheckedRepresentation:)`.

3. **Public key import**: Use `XWingMLKEM768X25519.PublicKey(rawRepresentation:)` to import public keys for encryption.

4. **AAD handling**: AAD is passed directly to `seal()` and `open()`. The AAD hash computation remains in Python layer for envelope integrity.

5. **CIPHERSUITE**: `HPKE.Ciphersuite.XWingMLKEM768X25519_SHA256_AES_GCM_256` — constant, no runtime selection needed.

---

## Files Modified

| File | Change |
|------|--------|
| `tools/secure_enclave_helper/Sources/Commands.swift` | Real CryptoKit HPKE X-Wing for all hpke-* commands |
| `tools/secure_enclave_helper/Sources/main.swift` | Added `--info-b64` param to hpke-encrypt/decrypt |
| `security/pq_export_encryption_swift.py` | PQ=true validation, truthful status parsing |
| `security/pq_export_encryption.py` | Mode string updated to match new schema |
| `tests/probe_real_cryptokit_pq_f206ab/` | New hermetic + integration tests |

---

## ABORT Conditions — Verified Clear

- [x] No placeholder/classical labeled as pq=true
- [x] No placeholder labeled as HPKE/X-Wing/ML-KEM/PQ
- [x] No PQ fallback to classical without truthful degradation
- [x] No Python import-time helper spawn
- [x] ML-DSA implementation unmodified
- [x] No live network, no Touch ID, no per-chunk operations
