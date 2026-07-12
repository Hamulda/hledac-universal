---
title: Hashing Facade ISSUE-2
summary: 'ISSUE #2: Created centralized hashing facade utils/hashing.py with xxh3_64_hex/batch_xxh3_64_hex/sha256_hex/blake3_64_hex. Hot paths migrated from hashlib (7 in deduplication.py, 1 blake2b in url_dedup.py). Crypto-grade kept for forensics/security/stealth/vault. Expected ~10x speedup via Rust NEON SIMD.'
tags: []
related: [facts/project/xxhash_rust_implementation.md]
keywords: []
createdAt: '2026-07-12T13:32:16.321Z'
updatedAt: '2026-07-12T13:32:16.321Z'
---
## Reason
Document ISSUE #2 completion: hashlib bottleneck fixed with xxh3 facade

## Raw Concept
**Task:**
ISSUE #2: Fix hashlib bottleneck with centralized xxh3 hashing facade

**Changes:**
- Created utils/hashing.py with xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, blake3_64_hex
- Migrated 7 hashlib calls in utils/deduplication.py to xxh3
- Migrated 1 blake2b call in tools/url_dedup.py to xxh3
- Preserved crypto-grade hashing for forensics/security/stealth/vault

**Files:**
- utils/hashing.py
- utils/deduplication.py
- tools/url_dedup.py
- forensics/metadata_extractor.py

**Flow:**
import facade -> call xxh3_64_hex -> Rust NEON SIMD -> 64-bit hex output

**Timestamp:** 2026-07-12

## Narrative
### Structure
Centralized facade at utils/hashing.py provides 4 hash functions. xxh3_64_hex for single items, batch_xxh3_64_hex for bulk operations, sha256_hex and blake3_64_hex for compatibility.

### Dependencies
Depends on Rust xxhash implementation with NEON SIMD support for performance

### Highlights
Hot paths migrated: deduplication.py (7x), url_dedup.py (1x). Crypto-grade hashing unchanged for security-sensitive operations.

## Facts
- **sprint_issue**: ISSUE #2 hashlib bottleneck completed [project]
- **hashing_facade_file**: Centralized hashing facade created at utils/hashing.py [project]
- **hashing_functions**: Facade provides: xxh3_64_hex, batch_xxh3_64_hex, sha256_hex, blake3_64_hex [project]
- **deduplication_migration**: utils/deduplication.py migrated 7 hashlib calls to xxh3 [project]
- **url_dedup_migration**: tools/url_dedup.py migrated 1 blake2b call to xxh3 [project]
- **crypto_grade_preserved**: Crypto-grade hashing kept for: forensics/metadata_extractor.py, security_layer.py, stealth_layer.py, vault IDs [project]
- **performance_gain**: Expected performance gain: ~10x via Rust NEON SIMD vs Python GIL [project]
