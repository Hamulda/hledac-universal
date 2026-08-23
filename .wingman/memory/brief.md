# Hledac Universal - Memory Brief

**Project:** OSINT Orchestrator (`autonomous_orchestrator.py`)  
**Last Updated:** 2026-08-20  
**Status:** Active development

## Architecture Overview

Asynchronní autonomní OSINT orchestrátor pro MacBook Air M1 8GB. MLX-native inference s Hermes-3-Llama-3.2-3B.

## Key Modules (75 total)

### Core
- `pipeline_orchestrator` - Sprint pipeline orchestration
- `execution_coordinator` - Task execution management
- `memory_coordinator` - M1 memory management
- `intel_coordinator` - Intelligence source coordination

### Inference
- `hermes_model_cache` - MLX model caching
- `mlx_kv_cache_share` - KV cache management
- `context_compressor` - Prompt compression (LLMlingua)

### Storage Trinity
| Layer | Tech | Path |
|-------|------|------|
| Canonical | DuckDB | `duckdb_store.py` |
| Metadata | LMDB | `lmdb_unified.py` |
| RAG | LanceDB | `duckdb_vector_store.py` |

### Rust FFI (9 wiring modules)
- IOC dedup, Bloom filter, SIMD similarity
- Circuit breaker, Graph analytics, AIMD
- Claims extraction, Text norm, URL engine

### Transport Layer (6 modules)
- `transport_tor` - Tor anonymity
- `transport_i2p` - I2P tunnels  
- `transport_arti` - Arti Rust
- `transport_nym` - Nym mixnet
- `transport_session_pool` - Session reuse
- `transport_circuit_breaker` - Failure isolation

### Security (6 modules)
- `stealth_engine` - Browser fingerprinting
- `captcha_solver` - CAPTCHA bypass
- `quantum_crypto` - Post-quantum (Dilithium/Kyber)
- `pii_gate` - PII redaction
- `vault_manager` - Encrypted secrets
- `ephemeral_wipe` - Secure deletion

### Recon Lanes (6 OSINT sources)
- `shodan_lane` - Device fingerprints
- `greynoise_lane` - Threat intel
- `dark_web_lane` - .onion reconnaissance
- `ct_log_scanner` - Certificate transparency
- `wayback_cdx` - Historical URLs
- `github_secret_scanner` - Secret detection

### Network Intelligence (3 modules)
- `passive_dns` - Historical DNS
- `bgp_monitor` - ASN/routing
- `dns_tunnel_detector` - Exfiltration detection

### Multimodal (3 modules)
- `media_engine` - Media orchestration
- `vision_encoder` - Image understanding
- `evidence_triage` - Priority scoring

## Critical Invariants (M1 8GB)

1. `asyncio.gather` always with `return_exceptions=True`
2. `mx.eval([])` before `mx.metal.clear_cache()`
3. DuckDB write: `async_ingest_findings_batch()` only
4. LMDB bulk: `putmulti()` not per-item loop
5. URL dedup: `RotatingBloomFilter` not `Set[str]`
6. Metal cache: `min(max(available*0.2, 512MiB), 1.5GiB)`
7. Never `--disable-gpu` on M1

## Storage Paths

```
~/.hledac/
├── findings.duckdb      # Canonical findings
├── metadata.lmdb       # Entity/claim metadata
├── embeddings.lmdb     # RAG embeddings
└── vault.lmdb          # Encrypted secrets
```

## Project Map

- **Total entries:** 148
- **Features:** 17
- **Modules:** 75
- **Utilities:** 15
- **Patterns:** 9
- **Domains:** 7

See `.wingman/project-map/index.md` for full catalog.

## Dependencies

| Package | Purpose |
|---------|---------|
| mlx, mlx-lm | LLM inference |
| duckdb | Analytics DB |
| lmdb | KV store |
| lanceDB | ANN vectors |
| curl_cffi | Stealth HTTP |
| pybloom_live | Bloom filters |
| psutil | Memory monitoring |
| llmlingua | Prompt compression |

## Entry Points

```bash
python -m hledac.universal --sprint "QUERY" [--duration SECS]
```

## Testing

```bash
pytest tests/ -x --timeout=30 -q
```
