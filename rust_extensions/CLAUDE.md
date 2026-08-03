# rust_extensions — PyO3 Native Extensions pro Hledac

## Modul
Python/Rust extension via PyO3 (`cdylib`), build via `maturin develop` / `maturin build`.

## Build
```bash
cd rust_extensions

# Standard build (default: core + data) — DuckDB wired
# ⚠️ M1/M2/M3: RUSTFLAGS required for NEON SIMD — maturin ignores Cargo.toml
# [target.aarch64-apple-darwin] rustflags (cargo:rustc-cfg=neon_available via build.rs
# is still set, but +neon compiler flag must come from RUSTFLAGS env var).
RUSTFLAGS="-C target-feature=+neon,+crypto" maturin develop

# Fast dev build (bez DuckDB, bez Metal) — ~5× rychlejší
# ⚠️ M1/M2/M3: stejné RUSTFLAGS pro NEON SIMD
RUSTFLAGS="-C target-feature=+neon,+crypto" maturin develop --no-default-features --features ""

# Full build (vše) — pouze CI
# ⚠️ M1/M2/M3: stejné RUSTFLAGS pro NEON SIMD
RUSTFLAGS="-C target-feature=+neon,+crypto" maturin develop --features "full"

# Release wheel
RUSTFLAGS="-C target-feature=+neon,+crypto" maturin build --release
RUSTFLAGS="-C target-feature=+neon,+crypto" maturin build --release --features "full"
```

**Proč RUSTFLAGS env var:** Maturin při `maturin develop` / `maturin build` sestavuje vlastní
`cargo` příkaz a Cargo.toml `[target.aarch64-apple-darwin]` rustflags jsou ignorovány.
`build.rs` správně nastavuje `cargo:rustc-cfg=neon_available` (detekce přes
`CARGO_CFG_TARGET_ARCH`), ale bez `+neon` v target-features compiler pouze
používá skalární fallback — žádné SIMD instrukce se neemitují.

## Feature flags (D3 fix)

| Příkaz | Kompiluje | Rychlost |
|--------|-----------|----------|
| `--no-default-features --features ""` | Pouze core moduly | ~5× rychlejší |
| `default` (bez flagů) | core + data | Standard |
| `--features "tls13"` | core + data + tls13 | +~2MB compile |
| `--features "pdf"` | core + data + pdf | +~3MB compile |
| `--features "full"` | Všechny moduly | ~3× pomalejší |

### tls13 — TLS 1.3 JA4 fingerprinting + ECH

```bash
maturin develop --features "tls13"
```

Přidává:
- `rust.tls.ja4_from_client_hello()` — JA4 z ClientHello hex/binary
- `rust.tls.connect_and_ja4()` — TLS spojení + JA4 fingerprint
- `rust.tls.batch_ja4()` — Paralelní batch fingerprinting

M1 8GB: ~2MB compile, ~50KB per connection.

### pdf — PDF text extraction + IOC extraction

```bash
maturin develop --features "pdf"
```

Přidává:
- `rust.pdf.extract_text(path)` — Extrahuje plain text z PDF souboru
- `rust.pdf.extract_text_from_bytes(data)` — Extrahuje text z PDF v paměti
- `rust.pdf.extract_text_and_iocs_from_bytes(data)` — **Single-pass** text + IOCs (2× rychlejší než postupné volání)
- `rust.pdf.extract_text_and_iocs(path)` — Single-pass file verze
- `rust.pdf.extract_iocs(path)` — Extrahuje IOCs z PDF (text → IOC regex)
- `rust.pdf.extract_iocs_from_bytes(data)` — IOC extrakce z PDF bytes

Dependencies: `lopdf = "0.34"` (pure Rust, no C deps)
M1 8GB: ~3MB compile, bounded (100MB max PDF, 10k pages max)
Python fallback: PyMuPDF v `content_miner.py` a `document_metadata_extractor.py`

> **D6 (2026-07-16):** `ml` feature odstraněna — `metal` crate (~45s compile, ~3MB dylib) nebyl v produkci využíván. `gpu_batch_keyword_scan()` v `metal_pattern_matcher` nebyl nikdy volán. CPU fallback přes Aho-Corasick + rayon je dostatečný pro všechny workloady. Viz CLAUDE.md root sekce D6.

Kompilace: `opt-level = 1` v `[profile.dev]` (3-5× rychlejší incremental builds na M1).

## R22: ANE + Metal + Accelerate Moduly (2026-07-26)

### ane — Apple Neural Engine Bindings (feature: `ane`)

```bash
maturin develop --features "ane"
```

Modul pro Apple Neural Engine (ANE) akceleraci ML inference.

Funkce:
- `rust.ane.init()` — Inicializace ANE subsystemu
- `rust.ane.load_model(model_id, model_path, hidden_dim, max_seq_len)` — Registrace CoreML modelu
- `rust.ane.validate_batch(batch_size, seq_len, max_seq_len)` — Validace batch dimenzí
- `rust.ane.get_telemetry()` — Telemetrie (embed_calls, embed_tokens, cache_hits)
- `rust.ane.is_ane_available()` — Kontrola dostupnosti na Apple Silicon

Architektura: Model registry s max 2 modely současně (ANE HW limit). Skutečná inference přes Python/CorML, Rust poskytuje pouze registry a validaci.

### metal — Metal GPU Compute (feature: `metal`)

```bash
maturin develop --features "metal"
```

GPU batch matmul pro MoE router + SILICON-01 opportunistic hash cracking.

Funkce (metal_compute):
- `rust.metal.init()` — Inicializace Metal GPU
- `rust.metal.batch_matmul(...)` — Batch maticové násobení
- `rust.metal.batch_matvec(...)` — Batch maticový vektor součin
- `rust.metal.get_telemetry()` — Telemetrie
- `rust.metal.clear_cache()` — Uvolnění GPU paměti

Funkce (MetalHashCracker — SILICON-01):
- `rust.MetalHashCracker()` — Inicializace GPU hash crackeru
- `cracker.is_available` — True pokud Metal GPU dostupné
- `cracker.device_name` — Název zařízení (např. "Apple M1")
- `cracker.crack_md5(target_hex, wordlist) -> Optional[str]` — MD5 dictionary attack (GPU → CPU fallback)
- `cracker.crack_sha256(target_hex, wordlist) -> Optional[str]` — SHA-256 dictionary attack
- `cracker.crack_batch_md5(targets, wordlist) -> Dict[str, Optional[str]]` — Batch cracking
- `cracker.get_stats() -> Dict` — Telemetrie (gpu_attempts, cpu_fallbacks, atd.)
- `cracker.clear_cache()` — Uvolnění GPU bufferů

M1 8GB: GPU buffery bounded na 64 MB, total guard 256 MB.
CPU fallback: Rayon + NEON (vždy dostupný, bez Metal crate).

### accelerate — Accelerate/vDSP FFI (always compiled)

```bash
maturin develop  # bez额外的feature flag
```

FFI binding na Apple Accelerate framework (vDSP) pro rychlé cosine similarity výpočty.

Funkce:
- `rust.accelerate.init()` — Inicializace Accelerate subsystemu
- `rust.accelerate.cosine_similarity(a, b, normalize)` — Cosine similarity dvou vektorů
- `rust.accelerate.batch_cosine_similarity(queries, candidates, Q, N, dim, normalize)` — Batch cosine similarity
- `rust.accelerate.batch_normalize(vectors, batch, dim)` — L2 normalizace dávky vektorů
- `rust.accelerate.get_telemetry()` — Telemetrie (cosine_calls, cosine_pairs)

NER integrace: `brain/ner_engine.py` → entity cosine comparison → Rust accelerate batch cosine.

### Feature Flags Souhrn

| Feature | Kompiluje | Závislost |
|---------|-----------|------------|
| `ane` | ANE model registry | Žádná (CoreML na Python straně) |
| `metal` | Metal GPU batch matmul | Žádná (stub + CPU fallback) |
| accelerate | vDSP FFI | Accelerate.framework (systémový) |

M1 8GB: Všechny moduly jsou bounded — žádná neomezená alokace.

### office — Office document text extraction (.docx, .xlsx, .pptx)

```bash
maturin develop --features "office"
```

Přidává:
- `rust.office.extract_text(path, format)` — Extrahuje plain text z Office dokumentu
- `rust.office.extract_text_from_bytes(data, format)` — Extrahuje text z Office v paměti
- `rust.office.extract_iocs(path, format)` — Extrahuje IOCs z Office dokumentu
- `rust.office.extract_iocs_from_bytes(data, format)` — IOC extrakce z Office bytes

Dependencies: `docx-rs = "0.15"` + `calamine = "0.26"` (pure Rust, no C deps)
M1 8GB: ~5MB compile, bounded (100MB max file size)
Python fallback: python-docx + openpyxl v `content_miner.py`

## Struktura
```
rust_extensions/
├── pyproject.toml          # maturin konfigurace
├── Cargo.toml              # Rust dependencies
├── src/
│   ├── lib.rs              # #[pymodule] = hledac_rust_extensions
│   └── ...
└── hledac_rust_extensions/ # Python package (pro maturin python-source=".")
    ├── __init__.py
    └── hledac_rust_extensions.pyi
```

## Důležité
- PyO3 cdylib = `module-name = "hledac_rust_extensions"` v pyproject.toml
- Python package jméno = `hledac_rust_extensions` (PODTRŽÍTKO, ne hyphen)
- `extension-module` feature = nutný pro cdylib build (od PyO3 0.21)
- ISSUE-014: Non-abi3 native wheel — builds cp314-specific .so s plným Python C API (ne stable ABI subset)

## Maturin Cheatsheet
| Příkaz | Účel |
|--------|------|
| `maturin develop` | Build + install do dev Python (RY)
| `maturin build` | Wheel do `target/maturin/` |
| `maturin build --release` | Release build |
| `maturin list-python` | Dostupné Python interpretry |
