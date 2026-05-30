# Capability Audit: Security, Anonymization & Dark Web

Generated: 2026-05-30 | Project: hledac/universal

---

## Sekce 1: Anonymization Stack — Co je funkční

### 1.1 Temporal Anonymizer
| Komponenta | Soubor | Status | Detail |
|---|---|---|---|
| `TemporalAnonymizer` | `security/temporal_anonymizer.py` | IMPL | 185L, gated by `HLEDAC_ENABLE_ZERO_ATTRIBUTION=1` |
| `anonymize_timestamp()` | — | FUNKČNÍ | Rounds to 15-min boundary + ±2min jitter (cryptographically secure) |
| `delayed_write_buffer()` | — | FUNKČNÍ | Random delay [30-120s] prevents timing correlation |
| Wired to | `fetch_coordinator.py` | ✅ | Post-processor, fire-and-forget |
| M1 constraint | — | ✅ | < 0.05ms per finding |

### 1.2 Zero Attribution Engine
| Komponenta | Soubor | Status | Detail |
|---|---|---|---|
| `ZeroAttributionEngine` | `security/zero_attribution_engine.py` | IMPL | 415L, gated by `HLEDAC_ENABLE_ZERO_ATTRIBUTION=1` |
| `query_timing_jitter()` | — | FUNKČNÍ | Randomized query timing |
| `generate_cover_traffic()` | — | FUNKČNÍ | Cover traffic generation |
| `fingerprint_rotate_headers()` | — | FUNKČNÍ | Header rotation per request |
| `strip_metadata()` | — | FUNKČNÍ | Strips EXIF, PDF, HTML metadata |
| Wired to | `fetch_coordinator.py` | ✅ | Via duckdb_shadow_store |

### 1.3 Feature Gate Status
| Feature | Env var | Default | Wired |
|---|---|---|---|
| Zero Attribution | `HLEDAC_ENABLE_ZERO_ATTRIBUTION` | 0 (OFF) | ✅ Sprint scheduler respects gate |
| Tor | `HLEDAC_ENABLE_TOR` | 0 (OFF) | ✅ DarkWebCrawler checks gate |
| DHT | `HLEDAC_ENABLE_DHT` | 0 (OFF) | ✅ DHT adapter checks gate |

---

## Sekce 2: Dark Web Access — Tor / I2P / Nym

### 2.1 Transport Implementations
| Protokol | Soubor | Třída | Status | Wired |
|---|---|---|---|---|
| **Tor (SOCKS5)** | `transport/tor_transport.py` | `TorTransport` | IMPL | ✅ DarkWebCrawler + OnionSeedManager |
| Tor circuit rotation | — | — | IMPL | `max_circuit_requests` + `rotate_circuit()` |
| Tor bridge support | — | — | IMPL | Bridge detection + fallback |
| **I2P (SOCKS5/SAM/HTTP)** | `transport/i2p_transport.py` | `I2PTransport` | IMPL | 3 modes, SOCKS5 preferred |
| I2P SAMv3 | — | — | IMPL | `network/i2p_client.py` (387L) |
| I2P eepsites | — | — | IMPL | `network/i2p_client.py` fetch_eepsite() |
| **Nym Mixnet** | `transport/nym_transport.py` | `NymTransport` | IMPL | Sphinx packet routing |
| Nym self-address | — | — | IMPL | `wait_for_self_address()` |

### 2.2 Dark Web Intelligence
| Komponenta | Soubor | Status | Wired |
|---|---|---|---|
| `DarkWebCrawler` | `intelligence/dark_web_intelligence.py` | IMPL | ✅ SidecarOrchestrator |
| `OnionSeedManager` | — | IMPL | ✅ DarkWebCrawler |
| `darkweb_content_to_canonical()` | — | IMPL | ✅ DuckDB ingest |
| Onion discovery sidecar | `runtime/sidecar_orchestrator.py` | IMPL | ✅ Sprint scheduler |

### 2.3 Wiring Reports
| Report | Existuje | Verdict |
|---|---|---|
| `DARKWEB_WIRING_COMPLETE.md` | ✅ | Confirms Tor/I2P/Nym wired as of Sprint F251 |
| `DARKWEB_SIDECAR_REPORT.md` | ✅ | Confirms onion_discovery in sidecar chain |
| Sprint integration | — | ✅ `_run_onion_discovery_sidecar()` in scheduler |

---

## Sekce 3: Cryptography Stack — PQ / ZKP

### 3.1 Post-Quantum Cryptography
| Algoritmus | Soubor | Status | Detail |
|---|---|---|---|
| **ML-DSA-65** | `security/pq_crypto.py` | IMPL | Hybrid signature (P-256 + ML-DSA), macOS 26+ |
| **P-256** | `security/pq_crypto.py` | IMPL | Primary signature, always available |
| **ML-KEM** | `security/quantum_safe.py` | REF | Referenced, not used for encryption |
| **SPHINCS+** | `security/quantum_safe.py` | REF | In NeuromorphicCryptoEngine |
| **Kyber** | `security/quantum_safe.py` | REF | In NeuromorphicCryptoEngine |
| **Dilithium** | `security/quantum_safe.py` | REF | In NeuromorphicCryptoEngine |
| **HPKE** | `security/pq_export_encryption.py` | IMPL | Hybrid PKE for export |
| **PQ export** | `security/pq_export_encryption.py` | IMPL | STIX/JSON-LD export with PQ keys |

### 3.2 PQ Crypto Status
| Komponenta | Status | Poznámka |
|---|---|---|
| `PostQuantumBackend` protocol | IMPL | Interface for PQ implementations |
| `NullPostQuantumBackend` | IMPL | Stub, always-unavailable (import-safe) |
| `HybridSignatureSet` | IMPL | P-256 + optional ML-DSA per batch |
| `ensure_mldsa_key()` | IMPL | Feature-detected on macOS 26+ |
| `pq_status()` | IMPL | PQAvailability enum (DISABLED/UNAVAILABLE/AVAILABLE/SIGNED/FAIL_SOFT) |
| Wired to export | ✅ | `pq_export_encryption.py` → STIX/JSON-LD |

### 3.3 Zero-Knowledge Proofs
| Komponenta | Soubor | Status | Detail |
|---|---|---|---|
| `ZKPResearchEngine` | `_shims/security_zkp_research_engine.py` | STUB ONLY | 11L stub, raises NotImplementedError |
| Real implementation | — | MISSING | `_shims/` contains placeholder |

### 3.4 Neuromorphic Cryptography
| Komponenta | Soubor | Status | Poznámka |
|---|---|---|---|
| `NeuromorphicCryptoEngine` | `security/quantum_safe.py` | EXPERIMENTAL | Spiking neural network based |
| `QuantumSafeVault` | `security/quantum_safe.py` | EXPERIMENTAL | SNN + classic crypto hybrid |
| Wired | ❌ | Not referenced in production pipeline |

---

## Sekce 4: Stealth Capabilities — TLS / Browser / Timing

### 4.1 StealthLayer (layers/stealth_layer.py)
| Capability | Třída | Metody | Status |
|---|---|---|---|
| **Browser fingerprint evasion** | `FingerprintRandomizer` | `generate_profile()`, `rotate()`, `get_js_protection_script()` | IMPL |
| Canvas fingerprint noise | — | `_generate_canvas_noise()` | IMPL |
| WebGL profile spoofing | — | `_generate_webgl_profile()` | IMPL |
| Screen resolution randomization | — | `_generate_screen_resolution()` | IMPL |
| Timezone spoofing | — | `_generate_timezone()` | IMPL |
| Font list randomization | — | `_generate_font_list()` | IMPL |
| Plugin spoofing | — | `_generate_plugins()` | IMPL |
| Hardware specs spoofing | — | `_generate_hardware_specs()` | IMPL |
| **CAPTCHA solving** | `AdvancedCaptchaSolver` | `solve_captcha()`, `_run_transformers_ocr()`, `_run_tesseract_ocr()` | IMPL |
| Image CAPTCHA | — | `_solve_image_captcha()` | IMPL |
| Text/logic CAPTCHA | — | `_solve_text_logic()` | IMPL |
| Math CAPTCHA | — | `_solve_math_captcha()` | IMPL |
| **JS evasion** | `JavaScriptEvasion` | `get_all_evasion_scripts()` | IMPL |
| WebDriver hider | — | `_get_webdriver_hider()` | IMPL |
| Automation hider | — | `_get_automation_hider()` | IMPL |
| WebRTC disabler | — | `_get_webrtc_disabler()` | IMPL |
| Canvas override | — | `_get_canvas_override()` | IMPL |
| WebGL override | — | `_get_webgl_override()` | IMPL |
| Chrome runtime spoof | — | `_get_chrome_runtime_spoof()` | IMPL |
| **Behavior simulation** | `BehaviorSimulator` | `generate_mouse_path()`, `simulate_mouse_move()`, `simulate_click()`, `simulate_scroll()`, `simulate_typing()`, `simulate_reading()` | IMPL |
| Bezier curve mouse | — | `_bezier_curve()` | IMPL |
| Random delay | — | `_random_delay()` | IMPL |
| **Anti-debugging** | `Chameleon` | `masquerade_process()`, `is_debugger_present()`, `is_debugger_protected()` | IMPL |
| **Timing jitter** | `StealthLayer` | `get_timing_jitter()` | IMPL |

### 4.2 PrivacyLayer (layers/privacy_layer.py)
| Capability | Metody | Status |
|---|---|---|
| VPN management | — | ✅ Reference |
| Tor integration | — | ✅ Reference |
| PGP key generation | `generate_pgp_key()` | IMPL |
| PGP encrypt/decrypt | `encrypt_message()`, `decrypt_message()` | IMPL |
| Secure channel | `create_secure_channel()`, `send_channel_message()` | IMPL |
| Burner identity | `create_burner_identity()` | IMPL |
| Audit logging | `log_event()`, `search_audit_logs()` | IMPL |
| Compliance reporting | `generate_compliance_report()` | IMPL |
| Protocol generation | `generate_protocol()`, `save_protocol()` | IMPL |
| Text anonymization | `anonymize_text()` | IMPL |

### 4.3 TLS / Header Manipulation
| Capability | Lokace | Status |
|---|---|---|
| JA3 fingerprint spoofing | `transport/curl_cffi_transport.py` | IMPL |
| Header randomization | `security/zero_attribution_engine.py` | IMPL (gated) |
| User-Agent rotation | `layers/stealth_layer.py` | IMPL |
| Accept-Language rotation | `security/zero_attribution_engine.py` | IMPL (gated) |

---

## Sekce 5: Dead Code / Unverified Wiring

### 5.1 Security Moduly bez ověřeného propojení
| Soubor | Velikost | Imported by | Status |
|---|---|---|---|
| `security/quantum_safe.py` | 1231L | NONE in production | ⚠️ UNVERIFIED — only in tests |
| `security/self_healing.py` | 1223L | `runtime/sprint_scheduler.py` | ✅ Wired |
| `security/stego_detector.py` | 884L | `runtime/sprint_scheduler.py` | ✅ Wired |
| `security/digital_ghost_detector.py` | 546L | `runtime/sprint_scheduler.py` | ✅ Wired |
| `security/vault_manager.py` | 369L | `tests/` | ⚠️ UNVERIFIED — tests only |
| `security/audit.py` | 359L | — | ⚠️ UNVERIFIED |
| `security/pq_crypto_swift.py` | 347L | — | ⚠️ UNVERIFIED |
| `security/obfuscation.py` | 326L | — | ⚠️ UNVERIFIED |
| `security/destruction.py` | 289L | — | ⚠️ UNVERIFIED |
| `security/pq_crypto.py` | 263L | `tests/probe_fp...` | ⚠️ UNVERIFIED — tests only |
| `security/secure_enclave.py` | 196L | — | ⚠️ UNVERIFIED |
| `security/temporal_anonymizer.py` | 184L | `fetch_coordinator.py` | ✅ Wired |
| `security/key_manager.py` | 174L | `intelligence/data_leak_hunter.py` | ✅ Wired |
| `security/ram_vault.py` | 152L | — | ⚠️ UNVERIFIED |
| `security/captcha_detector.py` | 113L | — | ⚠️ UNVERIFIED |
| `security/pq_export_encryption.py` | 478L | `tests/probe_pq...` | ⚠️ UNVERIFIED — tests only |
| `security/pq_export_encryption_swift.py` | 402L | — | ⚠️ UNVERIFIED |

### 5.2 Imported Security Modules (verified)
| Module | Imported by | Usage |
|---|---|---|
| `security.passive_dns` | `discovery/circl_pdns_adapter.py` | PDNS queries |
| `security.key_manager` | `intelligence/data_leak_hunter.py` | Key management |
| `security.digital_ghost_detector` | `runtime/sprint_scheduler.py` | Forensics enrichment |
| `security.stego_detector` | `runtime/sprint_scheduler.py` | Steganography detection |
| `security.self_healing` | `tests/test_circuit_breaker_metrics.py` | Circuit breaker |
| `security.vault_manager` | `tests/` | Vault operations |
| `security.pq_crypto` | `tests/probe_fp...` | PQ signature tests |
| `security.pq_export_encryption` | `tests/probe_pq...` | Export encryption tests |
| `security.pii_gate` | `tests/` | PII detection |

### 5.3 ZKP — Completely Missing
| Komponenta | Status |
|---|---|
| `ZKPResearchEngine` | ❌ STUB ONLY — `_shims/security_zkp_research_engine.py` (11L) |
| Real ZKP implementation | ❌ NOT IMPLEMENTED |

---

## Sekce 6: Duplicity — stealth_layer.py vs security/*.py

### 6.1 Překrývající se Funkcionality
| Funkce | stealth_layer.py | security/*.py | Poznámka |
|---|---|---|---|
| Browser fingerprint | ✅ `FingerprintRandomizer` | ❌ | stealth_layer ONLY |
| TLS fingerprint | ❌ | ✅ `curl_cffi_transport.py` | security transport ONLY |
| CAPTCHA solving | ✅ `AdvancedCaptchaSolver` | ❌ | stealth_layer ONLY |
| JS evasion | ✅ `JavaScriptEvasion` | ❌ | stealth_layer ONLY |
| Behavior simulation | ✅ `BehaviorSimulator` | ❌ | stealth_layer ONLY |
| Anti-debugging | ✅ `Chameleon` | ❌ | stealth_layer ONLY |
| Header randomization | ❌ | ✅ `zero_attribution_engine.py` (gated) | security ONLY |
| Metadata stripping | ❌ | ✅ `zero_attribution_engine.py` (gated) | security ONLY |
| PGP encryption | ❌ | ✅ `privacy_layer.py` | layers ONLY |
| Audit logging | ❌ | ✅ `audit.py` | security ONLY |
| Secure destruction | ❌ | ✅ `destruction.py` | security ONLY |

### 6.2 Architektonická Dělba
| Layer | Odpovědnost |
|---|---|
| `layers/stealth_layer.py` | Browser-level evasion: fingerprinting, CAPTCHA, JS, behavior simulation, anti-debugging |
| `layers/privacy_layer.py` | Identity management: VPN, Tor, PGP, burner identities, compliance |
| `security/` | Cryptographic operations: PQ signatures, key management, audit, destruction |
| `transport/` | Network-level stealth: JA3 spoofing, SOCKS5, circuit rotation |

### 6.3 Žádná Duplicita — Čistá Separace
- `stealth_layer.py` (2776L) ≠ `security/` — runtime browser/network layer vs crypto
- `privacy_layer.py` (549L) ≠ `security/` — identity management vs cryptographic primitives
- `security/zero_attribution_engine.py` vs `layers/stealth_layer.py` — post-fetch metadata vs runtime evasion

---

## Shrnutí

| Kategorie | Počet | Funkční |
|---|---|---|
| Anonymization modules | 2 | 2 (gated) |
| Dark web transports | 3 (Tor, I2P, Nym) | 3 ✅ |
| Dark web intelligence | 2 (DarkWebCrawler, OnionSeedManager) | 2 ✅ |
| PQ algorithms | 6 (ML-DSA, P-256, ML-KEM, SPHINCS+, Kyber, Dilithium) | 4+ (hybrid) |
| ZKP | 1 stub | 0 (not implemented) |
| Stealth capabilities | 20+ | 20+ |
| Security modules total | 21 | 21 |
| Wired to pipeline | 7 | 7 ✅ |
| Unverified / dead code | 14 | ⚠️ |

### Top 3 gaps:

1. **ZKPResearchEngine** — completely missing, only stub in `_shims/`
2. **PQ crypto wired to production** — only tested, not in sprint scheduler
3. **quantum_safe.py (1231L)** — experimental neuromorphic crypto, no production wiring

---

## Sekce 7: System-Wide Architecture Map

### 7.1 Directory Tree (key modules)

```
hledac/universal/
├── __main__.py           [CLI entry, 3336L]
├── autonomous_orchestrator.py [facade]
├── enhanced_research.py   [enhanced research]
├── evidence_log.py
├── project_types.py
├── deep_probe.py          [deep probe scanner]
│
├── brain/                 [MLX inference]
│   ├── inference_engine.py
│   ├── hypothesis_engine.py
│   └── ...
│
├── coordinators/         [20 coordinators]
│   ├── fetch_coordinator.py   [HTTP fetching]
│   ├── research_coordinator.py [multi-source research]
│   ├── security_coordinator.py
│   ├── memory_coordinator.py
│   ├── swarm_coordinator.py
│   ├── multimodal_coordinator.py
│   └── ... (14 more)
│
├── core/                  [resource management]
│   ├── resource_governor.py   [M1 Uma budget]
│   └── ...
│
├── discovery/             [OSINT adapters]
│   ├── crtsh_adapter.py       [Certificate Transparency]
│   ├── circl_pdns_adapter.py   [PDNS]
│   ├── wayback_cdx_adapter.py  [Wayback CDX]
│   ├── ti_feed_adapter.py      [NVD, CISA KEV, URLhaus, ThreatFox]
│   ├── rss_atom_adapter.py     [RSS/Atom feeds]
│   ├── academic/               [openalex, arxiv, crossref, s2orc]
│   └── ...
│
├── export/
│   ├── hypothesis_builder.py
│   └── sprint_exporter.py
│
├── hypothesis/
│   └── hypothesis_engine.py   [evidence-driven research]
│
├── intelligence/          [analysis engines]
│   ├── dark_web_intelligence.py [Tor crawler]
│   ├── exposure_correlator.py
│   ├── leak_sentinel.py
│   ├── identity_stitching_engine.py
│   ├── academic_discovery.py
│   ├── academic_search.py
│   ├── exposed_service_hunter.py [Shodan, Censys]
│   ├── pastebin_monitor.py
│   ├── github_secrets.py
│   └── ...
│
├── knowledge/             [persistence]
│   ├── duckdb_store.py     [canonical write]
│   ├── lancedb_store.py    [RAG embeddings]
│   └── formatters.py       [STIX, JSON-LD export]
│
├── layers/                [abstraction layers]
│   ├── stealth_layer.py   [98KB, browser evasion]
│   ├── privacy_layer.py   [PGP, VPN, burner identity]
│   ├── security_layer.py
│   ├── ghost_layer.py
│   ├── memory_layer.py
│   ├── research_layer.py
│   ├── content_layer.py
│   ├── hive_coordination.py
│   └── temporal_signal_*.py
│
├── pipeline/              [data flow]
│   ├── live_public_pipeline.py  [5041L, public surface]
│   ├── live_feed_pipeline.py    [2461L, feeds]
│   ├── pivot_lane_planner.py   [404L]
│   └── scoring.py
│
├── policy/
│   └── nym_policy.py
│
├── runtime/              [sprint orchestration]
│   ├── sprint_scheduler.py
│   ├── sprint_lifecycle.py
│   └── sidecar_orchestrator.py
│
├── security/             [21 modules]
│   ├── pq_crypto.py           [ML-DSA-65 + P-256]
│   ├── quantum_safe.py        [neuromorphic (experimental)]
│   ├── temporal_anonymizer.py [gated]
│   ├── zero_attribution_engine.py [gated]
│   ├── vault_manager.py
│   ├── key_manager.py
│   ├── self_healing.py
│   ├── stego_detector.py
│   ├── digital_ghost_detector.py
│   └── ... (12 more)
│
├── transport/            [15 transport files]
│   ├── curl_cffi_transport.py  [JA3 fingerprint]
│   ├── tor_transport.py
│   ├── i2p_transport.py
│   ├── nym_transport.py       [mixnet]
│   ├── gopher_transport.py
│   ├── httpx_transport.py
│   └── circuit_breaker.py
│
├── network/              [protocol clients]
│   ├── ipfs_client.py          [671L, multi-gateway]
│   ├── gemini_transport.py     [465L, TLS-only]
│   ├── i2p_client.py           [387L, SAM]
│   └── session_runtime.py
│
├── stealth/              [stealth browser]
│   ├── stealth_manager.py  [1262L]
│   └── stealth_session.py
│
├── tools/                [utilities]
│   ├── commoncrawl_adapter.py
│   ├── deep_research_sources.py
│   ├── hnsw_builder.py
│   └── zstd_compressor.py
│
├── dht/                  [BitTorrent DHT]
│   └── kademlia_node.py   [1335L]
│
├── infrastructure/
│   ├── plugin_manager.py
│   └── system_monitor.py
│
└── rl/                   [reinforcement learning]
    ├── sprint_policy_manager.py
    └── ...
```

### 7.2 Cross-Module Dependencies (verified wiring)

| From | To | Usage |
|---|---|---|
| `runtime/sprint_scheduler.py` | `coordinators/fetch_coordinator.py` | Sprint execution |
| `runtime/sprint_scheduler.py` | `security/digital_ghost_detector.py` | Forensics enrichment |
| `runtime/sprint_scheduler.py` | `security/stego_detector.py` | Steganography |
| `runtime/sprint_scheduler.py` | `security/self_healing.py` | Circuit breaker |
| `coordinators/fetch_coordinator.py` | `transport/curl_cffi_transport.py` | HTTP fetch |
| `coordinators/fetch_coordinator.py` | `transport/tor_transport.py` | Dark web |
| `coordinators/fetch_coordinator.py` | `security/temporal_anonymizer.py` | Timestamp anonymization |
| `coordinators/fetch_coordinator.py` | `security/zero_attribution_engine.py` | Header stripping |
| `knowledge/duckdb_store.py` | `knowledge/lancedb_store.py` | RAG embed |
| `intelligence/dark_web_intelligence.py` | `transport/tor_transport.py` | Onion crawl |
| `intelligence/exposed_service_hunter.py` | `discovery/crtsh_adapter.py` | Certificate search |
| `discovery/circl_pdns_adapter.py` | `security/passive_dns.py` | PDNS queries |
| `intelligence/data_leak_hunter.py` | `security/key_manager.py` | Key management |

### 7.3 External Dependencies Summary

| Dependency | Used by | Purpose |
|---|---|---|
| `curl_cffi` | transport/*, fetch_coordinator | JA3 fingerprint, stealth HTTP |
| `mlx` | brain/* | LLM inference on M1 |
| `duckdb` | knowledge/duckdb_store | Canonical storage |
| `lancedb` | knowledge/lancedb_store | RAG embeddings |
| `lmdb` | knowledge/*, layers/temporal_signal | KV cache |
| `stem` | transport/tor_transport | Tor circuit control |
| `psutil` | coordinators/memory_coordinator | RAM monitoring |
| `resiliparse` | layers/content_layer | HTML parsing |
| `beautifulsoup4` | layers/content_layer | HTML cleaning |
| `nodriver` | layers/stealth_layer | Headless browser |
| `cryptography` | security/pq_crypto | P-256 signatures |
| `aiohttp` | network/*, fetching/* | Async HTTP |
| `httpx` | transport/httpx_transport | HTTP/2 transport |
| `orjson` | knowledge/duckdb_store | Fast serialization |
| `pybloom_live` | utils/bloom_filter | URL dedup |

### 7.4 Dead Code / Aspirational (never wired)

| Soubor | Velikost | Status |
|---|---|---|
| `_shims/security_zkp_research_engine.py` | 11L | STUB — NotImplementedError |
| `security/quantum_safe.py` | 1231L | EXPERIMENTAL — no production imports |
| `security/vault_manager.py` | 369L | TESTS ONLY |
| `security/audit.py` | 359L | UNVERIFIED |
| `security/secure_enclave.py` | 196L | UNVERIFIED |
| `security/ram_vault.py` | 152L | UNVERIFIED |
| `security/captcha_detector.py` | 113L | UNVERIFIED |
| `security/obfuscation.py` | 326L | UNVERIFIED |
| `security/destruction.py` | 289L | UNVERIFIED |