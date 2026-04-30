# Secure Enclave Helper

A minimal macOS Swift command-line helper that exposes Secure Enclave signing operations to Python via JSON.

## Purpose

This helper provides cryptographic signing capabilities using Apple Silicon Secure Enclave. It is designed as a fail-soft, optional component for the Hledac OSINT orchestrator.

**Scope:**
- Signs pre-hashed digests only (SHA-256, 64 hex chars)
- Manages Secure Enclave keys via Keychain
- Returns JSON responses for Python consumption

**Out of scope:**
- Raw OSINT chunk processing
- NLP, parsing, or data analysis
- Persistent storage of any kind
- Network operations

## Building

```bash
cd tools/secure_enclave_helper
swift build --configuration release
```

The binary will be at `.build/release/secure-enclave-helper`.

## Commands

### status

Check Secure Enclave availability and helper version.

```bash
secure-enclave-helper status
```

```json
{
  "ok": true,
  "data": {
    "secure_enclave_available": "true",
    "os_version": "14.0.0",
    "hardware_model": "Apple M1",
    "helper_version": "1.0.0"
  }
}
```

### ensure-signing-key

Create the signing key in Secure Enclave if it doesn't exist.

```bash
secure-enclave-helper ensure-signing-key --key-id com.hledac.sprint.signing.v1
```

```json
{
  "ok": true,
  "data": {
    "key_id": "com.hledac.sprint.signing.v1",
    "status": "ready"
  }
}
```

### public-key

Get the public key for a key ID (stable across calls).

```bash
secure-enclave-helper public-key --key-id com.hledac.sprint.signing.v1
```

```json
{
  "ok": true,
  "data": {
    "key_id": "com.hledac.sprint.signing.v1",
    "public_key_hex": "0450a3b...",
    "public_key_pem": "-----BEGIN PUBLIC KEY-----\n..."
  }
}
```

### sign-digest

Sign a SHA-256 digest (64 hex characters).

```bash
secure-enclave-helper sign-digest \
  --key-id com.hledac.sprint.signing.v1 \
  --digest-hex a1b2c3d4e5f6...
```

```json
{
  "ok": true,
  "data": {
    "key_id": "com.hledac.sprint.signing.v1",
    "signature_hex": "30460221...",
    "algorithm": "ecdsa-sha256-p256"
  }
}
```

### delete-key

Delete a signing key (for testing/reset).

```bash
secure-enclave-helper delete-key --key-id com.hledac.sprint.signing.v1
```

## Error Responses

```json
{
  "ok": false,
  "error_code": "INVALID_DIGEST_HEX",
  "message": "Digest must be 64 hex characters (SHA-256)"
}
```

Error codes:
- `SECURE_ENCLAVE_NOT_AVAILABLE` - Hardware does not support Secure Enclave
- `KEY_NOT_FOUND` - Key does not exist in Keychain
- `KEY_GENERATION_FAILED` - Failed to create key in Secure Enclave
- `INVALID_DIGEST_HEX` - Digest is not 64 hex characters
- `SIGNING_FAILED` - Signature operation failed
- `MISSING_KEY_ID` - Required --key-id argument not provided
- `TIMEOUT` - Helper exceeded timeout (see below)

## Timeout Behavior

The helper has a **10-second timeout** (`HELPER_TIMEOUT_SECONDS`). If a command hangs:

1. Python adapter should use `subprocess.run()` with `timeout=15` (buffer beyond helper timeout)
2. If timeout fires, helper exits with code **124**
3. Python should treat this as `SECURE_ENCLAVE_TIMEOUT` and fail-soft

```python
import subprocess

result = subprocess.run(
    ["secure-enclave-helper", "sign-digest", "--key-id", key_id, "--digest-hex", digest],
    capture_output=True,
    text=True,
    timeout=15
)
if result.returncode == 124:
    raise TimeoutError("Secure Enclave helper timed out")
```

## Security Properties

- **Non-exportable key**: Private key cannot be extracted from Secure Enclave
- **Device-only**: Key cannot be used on another machine
- **No biometric prompt**: Uses `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` access control
- **Isolated execution**: No network access, no file system access outside Keychain

## macOS 26+ ML-DSA/ML-KEM Support

**TODO**: Add post-quantum cryptographic support for macOS 26+.

```swift
// Placeholder for future post-quantum support
#if canImport(CryptoKitML)
enum MLKEMKeyExchange {
    // Implement ML-KEM-768 key encapsulation
}
#endif
```

This is a **feature flag** - the helper works without it on current macOS.

## Architecture

```
Python Adapter              Swift Helper
     |                          |
     |-- status --------------->
     |                          |
     |<-------------------------|
     |  {"ok": true, ...}      |
     |                          |
     |-- sign-digest ----------->
     |   (hex-encoded digest)   |
     |                          |
     |<-------------------------|
     |  {"ok": true, ...}       |
     |  (signature hex)         |
```

## Testing

```bash
# Build
swift build --configuration release

# Test status
.build/release/secure-enclave-helper status

# Test with invalid digest
.build/release/secure-enclave-helper sign-digest \
  --key-id test.key \
  --digest-hex invalid

# Test with valid digest (create key first)
.build/release/secure-enclave-helper ensure-signing-key --key-id test.key
.build/release/secure-enclave-helper sign-digest \
  --key-id test.key \
  --digest-hex $(openssl rand -hex 32)
```

## Integration with Python

See `security/secure_enclave.py` for the Python adapter that calls this helper.
