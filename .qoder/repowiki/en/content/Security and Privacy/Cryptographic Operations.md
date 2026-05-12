# Cryptographic Operations

<cite>
**Referenced Files in This Document**
- [encryption.py](file://hledac/universal/security/encryption.py)
- [encryption.py](file://hledac/universal/utils/encryption.py)
- [key_manager.py](file://hledac/universal/security/key_manager.py)
- [pq_crypto.py](file://hledac/universal/security/pq_crypto.py)
- [pq_crypto_swift.py](file://hledac/universal/security/pq_crypto_swift.py)
- [pq_export_encryption.py](file://hledac/universal/security/pq_export_encryption.py)
- [pq_export_encryption_swift.py](file://hledac/universal/security/pq_export_encryption_swift.py)
- [quantum_safe.py](file://hledac/universal/security/quantum_safe.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document describes cryptographic operations in Hledac Universal with a focus on:
- AES-GCM encryption/decryption for sensitive data
- Key management and rotation
- Post-quantum cryptography (PQC) integrations for signatures and export-grade encryption
- Cross-platform compatibility and Apple Silicon optimization
- Practical secure data handling, key rotation procedures, and best practices
- Security compliance considerations

## Project Structure
The cryptographic stack is organized around modular, protocol-driven components:
- Symmetric encryption utilities for general-purpose secure storage
- Key management with versioning and HKDF-based derivation
- Post-quantum signature support (ML-DSA) and export encryption (HPKE X-Wing)
- A quantum-safe vault integrating classical and neuromorphic primitives
- Swift-backed backends for macOS 26+ hardware acceleration

```mermaid
graph TB
subgraph "Symmetric Crypto"
UEnc["utils/encryption.py<br/>AES-256-GCM"]
SecEnc["security/encryption.py<br/>AES-GCM"]
end
subgraph "Key Management"
KM["security/key_manager.py<br/>HKDF, LMDB, rotation"]
end
subgraph "Post-Quantum Signatures"
PQProto["security/pq_crypto.py<br/>Protocols & Enums"]
PQSwift["security/pq_crypto_swift.py<br/>Swift helper bridge"]
end
subgraph "Export Encryption (HPKE)"
HPKEProto["security/pq_export_encryption.py<br/>Protocols & Envelope"]
HPKESwift["security/pq_export_encryption_swift.py<br/>Swift helper bridge"]
end
subgraph "Quantum-Safe Vault"
QS["security/quantum_safe.py<br/>Vault, SNN, Entropy"]
end
UEnc --> KM
SecEnc --> KM
PQProto --> PQSwift
HPKEProto --> HPKESwift
QS --> KM
QS --> PQProto
QS --> HPKEProto
```

**Diagram sources**
- [encryption.py:36-164](file://hledac/universal/utils/encryption.py#L36-L164)
- [encryption.py:6-23](file://hledac/universal/security/encryption.py#L6-L23)
- [key_manager.py:53-175](file://hledac/universal/security/key_manager.py#L53-L175)
- [pq_crypto.py:96-263](file://hledac/universal/security/pq_crypto.py#L96-L263)
- [pq_crypto_swift.py:177-324](file://hledac/universal/security/pq_crypto_swift.py#L177-L324)
- [pq_export_encryption.py:173-479](file://hledac/universal/security/pq_export_encryption.py#L173-L479)
- [pq_export_encryption_swift.py:175-404](file://hledac/universal/security/pq_export_encryption_swift.py#L175-L404)
- [quantum_safe.py:405-752](file://hledac/universal/security/quantum_safe.py#L405-L752)

**Section sources**
- [encryption.py:1-164](file://hledac/universal/utils/encryption.py#L1-L164)
- [encryption.py:1-23](file://hledac/universal/security/encryption.py#L1-L23)
- [key_manager.py:1-175](file://hledac/universal/security/key_manager.py#L1-L175)
- [pq_crypto.py:1-263](file://hledac/universal/security/pq_crypto.py#L1-L263)
- [pq_crypto_swift.py:1-324](file://hledac/universal/security/pq_crypto_swift.py#L1-L324)
- [pq_export_encryption.py:1-479](file://hledac/universal/security/pq_export_encryption.py#L1-L479)
- [pq_export_encryption_swift.py:1-404](file://hledac/universal/security/pq_export_encryption_swift.py#L1-L404)
- [quantum_safe.py:1-1173](file://hledac/universal/security/quantum_safe.py#L1-L1173)

## Core Components
- AES-GCM encryption/decryption
  - Utilities for AES-256-GCM with random nonces and authentication tags
  - Two implementations: a lightweight class for general storage and a minimal function-based implementation
- Key management
  - Master key versioning, HKDF-derived bucket keys, LMDB-backed persistence, and controlled memory locking
  - Rotation with optional migration semantics
- Post-quantum signatures (ML-DSA)
  - Protocol-driven design with a null backend for fail-soft environments
  - Swift-backed backend for macOS 26+ using a helper tool
- Export encryption (HPKE X-Wing)
  - Protocol-driven HPKE export with envelope metadata and status tracking
  - Swift-backed backend for macOS 26+ with persistent keychain integration
- Quantum-safe vault and neuromorphic crypto
  - Vault composition of classical and PQC primitives
  - SNN-based encryption/decryption with entropy pooling and M1 8GB optimizations

**Section sources**
- [encryption.py:36-164](file://hledac/universal/utils/encryption.py#L36-L164)
- [encryption.py:6-23](file://hledac/universal/security/encryption.py#L6-L23)
- [key_manager.py:53-175](file://hledac/universal/security/key_manager.py#L53-L175)
- [pq_crypto.py:96-263](file://hledac/universal/security/pq_crypto.py#L96-L263)
- [pq_crypto_swift.py:177-324](file://hledac/universal/security/pq_crypto_swift.py#L177-L324)
- [pq_export_encryption.py:173-479](file://hledac/universal/security/pq_export_encryption.py#L173-L479)
- [pq_export_encryption_swift.py:175-404](file://hledac/universal/security/pq_export_encryption_swift.py#L175-L404)
- [quantum_safe.py:405-752](file://hledac/universal/security/quantum_safe.py#L405-L752)

## Architecture Overview
The system separates concerns across protocols, backends, and helpers:
- Protocols define interfaces and data contracts
- Backends implement platform-specific logic
- Swift helper bridges provide macOS 26+ acceleration and keychain integration
- Vault composes primitives for end-to-end security

```mermaid
classDiagram
class PostQuantumBackend {
+name : str
+is_available() bool
+pq_status() PQStatus
+ensure_mldsa_key(key_id, level) bool
+sign_mldsa_digest(key_id, digest, level) PQSignature
+verify_mldsa_signature(digest, signature, public_key, level) bool
}
class SwiftPostQuantumBackend {
+is_available(force_refresh) bool
+pq_status() PQStatus
+ensure_mldsa_key(key_id, level) bool
+sign_mldsa_digest(key_id, digest, level) PQSignature
+verify_mldsa_signature(digest, signature, public_key, level) bool
+name : str
}
class PostQuantumExportBackend {
+name : str
+is_available() bool
+hpke_status() HPKEStatus
+generate_recipient_key(key_id) (pub, key_id, fp) | None
+encrypt_hpke(plaintext, aad, recipient_pub, recipient_key_id) ExportEncryptionEnvelope | None
+decrypt_hpke(envelope, placeholder, test_material) bytes | None
}
class HPKEExportBackend {
+is_available(force_refresh) bool
+hpke_status() HPKEStatus
+generate_recipient_key(key_id) (pub, key_id, fp) | None
+encrypt_hpke(plaintext, aad, recipient_pub, recipient_key_id) ExportEncryptionEnvelope
+decrypt_hpke(envelope, placeholder, test_material) bytes
+name : str
}
class KeyManager {
+get_master_key(version) (key, salt, version)
+get_bucket_key(bucket_id, version) (key, version)
+rotate_master_key(migrate) void
}
class DataEncryption {
+encrypt(plaintext) EncryptionResult
+decrypt(result) DecryptionResult
+generate_key_b64() str
}
PostQuantumBackend <|.. SwiftPostQuantumBackend
PostQuantumExportBackend <|.. HPKEExportBackend
```

**Diagram sources**
- [pq_crypto.py:96-263](file://hledac/universal/security/pq_crypto.py#L96-L263)
- [pq_crypto_swift.py:177-324](file://hledac/universal/security/pq_crypto_swift.py#L177-L324)
- [pq_export_encryption.py:173-479](file://hledac/universal/security/pq_export_encryption.py#L173-L479)
- [pq_export_encryption_swift.py:175-404](file://hledac/universal/security/pq_export_encryption_swift.py#L175-L404)
- [key_manager.py:53-175](file://hledac/universal/security/key_manager.py#L53-L175)
- [encryption.py:36-164](file://hledac/universal/utils/encryption.py#L36-L164)

## Detailed Component Analysis

### AES-GCM Encryption Utilities
- Purpose: Provide AES-256-GCM encryption/decryption for sensitive data with authenticated encryption and random nonces.
- Key characteristics:
  - Nonce size: 12 bytes; Tag size: 16 bytes
  - Additional associated data support for binding external metadata
  - Environment-driven key provisioning for session-scoped encryption
- Usage patterns:
  - General-purpose storage encryption via the class-based utility
  - Minimal function-based API for quick symmetric operations

```mermaid
sequenceDiagram
participant Caller as "Caller"
participant DE as "DataEncryption.encrypt()"
participant AES as "AES-GCM Cipher"
Caller->>DE : "encrypt(plaintext)"
DE->>DE : "_get_key_from_env() or _generate_key()"
DE->>AES : "Cipher(AES, GCM(nonce))"
AES-->>DE : "encryptor"
DE->>AES : "encryptor.update(plaintext)"
AES-->>DE : "ciphertext + tag"
DE-->>Caller : "EncryptionResult{ciphertext, nonce, tag}"
```

**Diagram sources**
- [encryption.py:69-116](file://hledac/universal/utils/encryption.py#L69-L116)

**Section sources**
- [encryption.py:36-164](file://hledac/universal/utils/encryption.py#L36-L164)
- [encryption.py:6-23](file://hledac/universal/security/encryption.py#L6-L23)

### Key Management and Rotation
- Purpose: Securely manage master keys, derive per-bucket keys, and rotate keys with optional migration.
- Key characteristics:
  - Master key versioning with automatic generation
  - HKDF-based derivation keyed by bucket ID and version
  - LMDB-backed persistence with configurable map size
  - Memory locking for master key buffers to reduce swap exposure
  - Async-safe operations with internal locks
- Rotation procedure:
  - Generate new master key and salt
  - Optionally retain previous versions for reading migrated data
  - Invalidate access to older versions unless explicitly requested

```mermaid
flowchart TD
Start([Start]) --> Load["Load master keys from LMDB"]
Load --> HasKeys{"Any keys found?"}
HasKeys --> |No| Gen["Generate new master key (version N+1)"]
HasKeys --> |Yes| Choose["Resolve requested version or latest"]
Gen --> Save["Persist key and salt to LMDB"]
Save --> Derive["Derive bucket key via HKDF"]
Choose --> Derive
Derive --> Rotate{"Rotate requested?"}
Rotate --> |Yes| NewVer["Generate new master key (version N+1)"]
Rotate --> |No| End([End])
NewVer --> Migrate{"Migrate old keys?"}
Migrate --> |Yes| Keep["Keep old keys for reading"]
Migrate --> |No| Drop["Drop old keys (unreadable)"]
Keep --> End
Drop --> End
```

**Diagram sources**
- [key_manager.py:73-175](file://hledac/universal/security/key_manager.py#L73-L175)

**Section sources**
- [key_manager.py:53-175](file://hledac/universal/security/key_manager.py#L53-L175)

### Post-Quantum Signatures (ML-DSA)
- Purpose: Provide hybrid signatures combining ECDSA-P256 (required) and ML-DSA-65 (optional on macOS 26+).
- Key characteristics:
  - Protocol defines backend interface and status reporting
  - Null backend ensures fail-soft behavior when unavailable
  - Swift-backed backend integrates with a helper tool for signing and verification
  - Status caching with short TTL for performance
- Integration:
  - Backend selection based on environment and availability
  - Signing performed over canonical batch digests
  - Verification supports both required and optional signatures

```mermaid
sequenceDiagram
participant App as "Application"
participant Factory as "create_post_quantum_backend()"
participant Swift as "SwiftPostQuantumBackend"
participant Helper as "secure-enclave-helper"
App->>Factory : "enabled=True, key_id=..."
Factory->>Swift : "try load Swift backend"
Swift->>Helper : "pq-status"
Helper-->>Swift : "status ok?"
Swift-->>Factory : "backend, status"
Factory-->>App : "(backend, status)"
App->>Swift : "ensure_mldsa_key(key_id)"
Swift->>Helper : "ensure-mldsa-key --key-id"
Helper-->>Swift : "ok"
Swift-->>App : "True"
App->>Swift : "sign_mldsa_digest(key_id, digest)"
Swift->>Helper : "mldsa-sign-digest --key-id --digest-hex"
Helper-->>Swift : "signature_hex"
Swift-->>App : "PQSignature"
```

**Diagram sources**
- [pq_crypto.py:208-263](file://hledac/universal/security/pq_crypto.py#L208-L263)
- [pq_crypto_swift.py:191-324](file://hledac/universal/security/pq_crypto_swift.py#L191-L324)

**Section sources**
- [pq_crypto.py:1-263](file://hledac/universal/security/pq_crypto.py#L1-L263)
- [pq_crypto_swift.py:1-324](file://hledac/universal/security/pq_crypto_swift.py#L1-L324)

### Export Encryption (HPKE X-Wing)
- Purpose: Provide export-grade encryption using HPKE with X-Wing ML-KEM-768/X25519 for macOS 26+.
- Key characteristics:
  - Envelope carries encapsulated key, AAD hash, ciphertext, and recipient metadata
  - Policy-driven behavior: required, preferred, or unencrypted-only
  - Persistent keychain integration for production decryption
  - Test-only ephemeral keys for local roundtrips
- Integration:
  - Swift-backed backend for encryption/decryption and key generation
  - Status caching and robust error handling with fail-soft semantics

```mermaid
sequenceDiagram
participant App as "Application"
participant Factory as "create_export_backend()"
participant HPKE as "HPKEExportBackend"
participant Helper as "secure-enclave-helper"
App->>Factory : "enabled=True, key_id=..."
Factory->>HPKE : "try load HPKE backend"
HPKE->>Helper : "hpke-status"
Helper-->>HPKE : "available=true, pq=true"
HPKE-->>Factory : "backend, status"
Factory-->>App : "(backend, status)"
App->>HPKE : "generate_recipient_key(key_id)"
HPKE->>Helper : "hpke-generate-recipient-key --key-id"
Helper-->>HPKE : "public_key_b64, key_id, fingerprint"
HPKE-->>App : "(pub, key_id, fp)"
App->>HPKE : "encrypt_hpke(plaintext, aad, pub)"
HPKE->>Helper : "hpke-encrypt --plaintext-b64 --aad-b64 --recipient-key-b64"
Helper-->>HPKE : "encapsulated_key_b64, ciphertext_b64"
HPKE-->>App : "ExportEncryptionEnvelope"
```

**Diagram sources**
- [pq_export_encryption.py:304-422](file://hledac/universal/security/pq_export_encryption.py#L304-L422)
- [pq_export_encryption_swift.py:191-338](file://hledac/universal/security/pq_export_encryption_swift.py#L191-L338)

**Section sources**
- [pq_export_encryption.py:1-479](file://hledac/universal/security/pq_export_encryption.py#L1-L479)
- [pq_export_encryption_swift.py:1-404](file://hledac/universal/security/pq_export_encryption_swift.py#L1-L404)

### Quantum-Safe Vault and Neuromorphic Crypto
- Purpose: Compose classical and post-quantum primitives into a unified vault; integrate neuromorphic computing for encryption/signatures.
- Key characteristics:
  - Vault supports ML-KEM/ML-DSA and integrates SNN-based encryption
  - Entropy pool for randomness and reseeding
  - Lazy initialization and cleanup for M1 8GB memory optimization
  - Neural signature generation and verification
- Implementation highlights:
  - SNN-based keystream generation and XOR encryption
  - Neural signature derived from network activations
  - Cleanup routines to release memory-heavy components

```mermaid
classDiagram
class QuantumSafeVault {
+initialize() void
+encrypt(plaintext, aad) EncryptedContainer
+decrypt(container) bytes
+encrypt_with_snn(data, key_id) SNNEncryptedContainer
+decrypt_with_snn(container) bytes
+generate_signature(data, key_id) bytes
+verify_signature(data, signature, key_id) bool
}
class NeuromorphicCryptoEngine {
+initialize() bool
+encrypt(data, key_id) SNNEncryptedContainer
+decrypt(container) bytes
+generate_signature(data, key_id) bytes
+verify_signature(data, signature, key_id) bool
+get_entropy_pool() EntropyPool
+cleanup() void
}
class EntropyPool {
+add_entropy(source, bytes) void
+extract_entropy(length) bytes
+get_entropy_estimate() float
}
QuantumSafeVault --> NeuromorphicCryptoEngine : "uses"
QuantumSafeVault --> EntropyPool : "uses"
```

**Diagram sources**
- [quantum_safe.py:405-752](file://hledac/universal/security/quantum_safe.py#L405-L752)
- [quantum_safe.py:46-133](file://hledac/universal/security/quantum_safe.py#L46-L133)

**Section sources**
- [quantum_safe.py:1-1173](file://hledac/universal/security/quantum_safe.py#L1-L1173)

## Dependency Analysis
- Internal dependencies:
  - Swift backends depend on protocol definitions in their respective core modules
  - Key manager depends on cryptography primitives and LMDB
  - Vault composes key manager, PQC, and HPKE components
- External dependencies:
  - cryptography library for AES-GCM, HKDF, and hashing
  - Swift helper tool for macOS 26+ ML-DSA and HPKE operations
  - LMDB for durable key storage

```mermaid
graph LR
Crypto["cryptography"] --> SecEnc["security/encryption.py"]
Crypto --> UEnc["utils/encryption.py"]
Crypto --> KM["security/key_manager.py"]
Swift["secure-enclave-helper"] --> PQSwift["security/pq_crypto_swift.py"]
Swift --> HPKESwift["security/pq_export_encryption_swift.py"]
LMDB["LMDB"] --> KM
PQProto["security/pq_crypto.py"] --> PQSwift
HPKEProto["security/pq_export_encryption.py"] --> HPKESwift
KM --> QS["security/quantum_safe.py"]
PQProto --> QS
HPKEProto --> QS
```

**Diagram sources**
- [encryption.py:1-23](file://hledac/universal/security/encryption.py#L1-L23)
- [encryption.py:1-164](file://hledac/universal/utils/encryption.py#L1-L164)
- [key_manager.py:1-175](file://hledac/universal/security/key_manager.py#L1-L175)
- [pq_crypto_swift.py:1-324](file://hledac/universal/security/pq_crypto_swift.py#L1-L324)
- [pq_export_encryption_swift.py:1-404](file://hledac/universal/security/pq_export_encryption_swift.py#L1-L404)
- [quantum_safe.py:1-1173](file://hledac/universal/security/quantum_safe.py#L1-L1173)

**Section sources**
- [encryption.py:1-23](file://hledac/universal/security/encryption.py#L1-L23)
- [encryption.py:1-164](file://hledac/universal/utils/encryption.py#L1-L164)
- [key_manager.py:1-175](file://hledac/universal/security/key_manager.py#L1-L175)
- [pq_crypto_swift.py:1-324](file://hledac/universal/security/pq_crypto_swift.py#L1-L324)
- [pq_export_encryption_swift.py:1-404](file://hledac/universal/security/pq_export_encryption_swift.py#L1-L404)
- [quantum_safe.py:1-1173](file://hledac/universal/security/quantum_safe.py#L1-L1173)

## Performance Considerations
- Apple Silicon optimization:
  - Swift-backed backends leverage macOS 26+ hardware acceleration and CryptoKit
  - Status caching reduces repeated helper invocations
  - Lazy initialization and cleanup minimize memory footprint on M1 8GB systems
- Cross-platform compatibility:
  - Null backends ensure graceful degradation on unsupported platforms
  - Environment-based key provisioning avoids hardcoding secrets
- Operational tips:
  - Prefer HKDF-derived bucket keys for deterministic, scalable key derivation
  - Use AES-GCM with authenticated associated data for integrity binding
  - Cache backend instances to avoid repeated initialization overhead

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- AES-GCM failures:
  - Missing cryptography library: ensure installation and availability
  - Invalid base64 inputs or corrupted tags: verify encoding and integrity
- Key management issues:
  - LMDB map size too small: adjust via environment-provided helpers
  - Missing master key version: rotate or restore previous version
- PQC backend problems:
  - Helper missing or not executable: verify path resolution and permissions
  - ML-DSA unavailable: confirm macOS version and feature flags
- HPKE export issues:
  - PQ export unavailable: check policy and backend status
  - Decryption persistence unsupported: ensure recipient key ID and keychain access
- Quantum-safe vault:
  - Initialization failures: confirm entropy sources and memory constraints
  - Cleanup required after heavy operations to free SNN weights and pools

**Section sources**
- [encryption.py:98-115](file://hledac/universal/utils/encryption.py#L98-L115)
- [key_manager.py:1-175](file://hledac/universal/security/key_manager.py#L1-L175)
- [pq_crypto_swift.py:84-114](file://hledac/universal/security/pq_crypto_swift.py#L84-L114)
- [pq_export_encryption_swift.py:82-112](file://hledac/universal/security/pq_export_encryption_swift.py#L82-L112)
- [quantum_safe.py:433-460](file://hledac/universal/security/quantum_safe.py#L433-L460)

## Conclusion
Hledac Universal’s cryptographic stack combines classical AES-GCM, robust key management, and forward-looking post-quantum primitives. The design emphasizes:
- Fail-soft availability with null backends
- macOS 26+ acceleration via Swift helper bridges
- Production-safe envelopes and persistent keychain integration
- Scalable key derivation and rotation
- Experimental neuromorphic cryptography with memory-conscious operations

These components enable secure, compliant, and future-ready handling of sensitive data across diverse environments.