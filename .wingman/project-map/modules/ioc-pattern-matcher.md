# IoC Pattern Matcher

## Metadata

- **Entry Path:** modules/ioc-pattern-matcher
- **Status:** current
- **Source:** knowledge/ioc_pattern_matcher.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Fast pattern-based IoC extraction for hot patterns: BTC, XMR, Onion, email, URL.

## Source Paths

- `knowledge/ioc_pattern_matcher.py`
- `rust_extensions/src/consistency_verifier.rs`

## Pattern Types

| Type | Confidence | Notes |
|------|------------|-------|
| EMAIL | 0.95 (with TLD) | |
| BTC_ADDRESS | 0.9 (bc1), 0.85 (Legacy) | |
| ETH_ADDRESS | 0.9 (42 chars) | |
| PRIVATE_KEY | 0.99 | Critical finding |
| PASSWORD | 0.99 | Critical finding |
| API_KEY_GENERIC | varies | Critical finding |
| AWS_KEY | varies | Critical finding |
| GOOGLE_KEY | varies | Critical finding |
| STRIPE_KEY | varies | Critical finding |

## Architecture

- Python `re` for regex (BTC, XMR, Onion variable-length)
- Ready for `pyahocorasick` when available for literal multi-pattern

## Bounds

| Limit | Value |
|-------|-------|
| MAX_TEXT_SIZE | 10MB |
| MAX_TEXT_BYTES | 1MB per match |

## Dual Engine

| Engine | Method | Use Case |
|--------|--------|----------|
| Rust regex | `rust.ioc.extract_iocs_flat()` | Fast, clearnet |
| Brain NER | `brain.ner_engine.extract_iocs_from_text()` | Free text, forums |

## Related Entries

- modules/ioc-processor
- features/ioc-extraction
