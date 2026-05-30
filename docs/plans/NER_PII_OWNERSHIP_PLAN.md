# NER / PII Ownership Plan — Sprint F218C

## Canonical Owners

| Component | File | Class/Function | Role | Status |
|-----------|------|----------------|------|--------|
| NER/RE Engine | `brain/ner_engine.py` | `NEREngine` | Joint NER + Relation Extraction | **Canonical Active** |
| PII/Privacy Gate | `security/pii_gate.py` | `SecurityGate` | Regex-based PII detection + sanitization | **Canonical Active** |
| Regex Fallback Extractor | `utils/entity_extractor.py` | `EntityExtractor` | Fast IOC extraction (email, crypto, IPs) | Alternative / Fallback |
| Entity Signal Extractor | `intelligence/entity_signal_extractor.py` | `EntitySignalExtractor` | Deterministic extraction from CanonicalFindings | Bounded advisory |
| Entity Linker | `knowledge/entity_linker.py` | `EntityLinker` | Wikidata-based linking + optional GLiNER | Optional, no default model |
| Aho-Corasick Extractor | `utils/aho_extractor.py` | `AhoExtractor` | Shadow pattern scanning | Shadow-only |
| Pattern Matcher | `patterns/pattern_matcher.py` | `PatternMatcher` | OSINT literal pack, regex IOC | Singleton, no ML |
| Leak Sentinel | `intelligence/leak_sentinel.py` | `LeakSentinelAdapter` | Paste/GitHub/breach → redacted CanonicalFinding | Bounded, uses `fallback_sanitize()` |

---

## Component Details

### 1. Canonical NER/RE Owner: `brain/ner_engine.py::NEREngine`

**Model:** `knowledgator/gliner-relex-large-v0.5` (GLiNER-Relex, joint NER + RE)

**Backend:** `gliner-pytorch` (CPU-only inference)

**Active features:**
- Lazy loading — model loads on first use, not at import
- `predict()` — single-text NER
- `predict_with_relations()` — joint NER + Relation Extraction via `gliner-relex`
- `predict_batch()` / `predict_batch_async()` — batch inference
- `predict_strict()` / `predict_batch_strict()` — memory-isolated subprocess mode
- `predict_async()` — async wrapper (ANE-first on Apple Silicon)
- `unload()` — explicit model unload

**Fallback chain (documented, inactive unless explicitly enabled):**
1. `NLTagger` (NaturalLanguage.framework, ANE) — `_nl_available` detection at import time
2. `CoreML` (`ner.mlmodel` at `~/.hledac/models/`) — lazy loaded via `_load_coreml_model()`
3. `GLiNER` (CPU, default) — always available

**Config:**
- `config.py::GLINER_MODEL = "knowledgator/gliner-x-base"` (base model, NOT Relex)
- `NEREngine.__init__` default: `"knowledgator/gliner-relex-large-v0.5"` (hardcoded, overrides config)

**What NEREngine is NOT authority for:**
- PII sanitization (→ `SecurityGate`)
- Vault/export encryption (→ `vault_manager.py`)
- Steganography detection (→ `stego_detector.py`)
- Content blocking/rejection decisions
- Runtime memory/budget management
- Media processing

---

### 2. Canonical PII/Privacy Owner: `security/pii_gate.py::SecurityGate`

**Backend:** Pure regex — no ML models, no transformers, no torch

**Active features:**
- `sanitize()` — detect + mask PII with configurable `mask_char`
- `analyze_risk()` — risk scoring based on PII density
- `fallback_sanitize()` — **always-on mandatory safety net** (never returns raw PII)
- `quick_sanitize()` — convenience function with lazy singleton

**PII Categories (regex-based):**
`EMAIL`, `PHONE`, `SSN`, `CREDIT_CARD`, `IP_ADDRESS`, `URL`, `USERNAME`, `DATE`, `PASSPORT`, `DRIVER_LICENSE`, `ADDRESS`

**International extensions in `fallback_sanitize()`:**
`IBAN`, `EU_VAT`, `E164_PHONE`, `UK_NINO`, `CZ_RODNE_CISLO`

**Fallback chain:** N/A — always regex-based

**Config:** No model config; `threshold` and `mask_char` are runtime parameters only

**What SecurityGate is NOT authority for:**
- NER/Relation Extraction (→ `NEREngine`)
- Vault/export encryption (→ `vault_manager.py`)
- Content blocking/rejection (early gate = detection only)
- Runtime memory/budget management
- Media processing or augmentation

---

### 3. Regex Fallback: `utils/entity_extractor.py::EntityExtractor`

**Labeled as:** "ALTERNATIVA" — alternative to `NEREngine`

**Method:** Deterministic regex — emails, crypto addresses, API keys, IPs, onion links

**No model loading** — purely regex-based

**Role:** Fast but less accurate than GLiNER. Used when NEREngine is unavailable or as pre-filter.

---

### 4. Inactive / Benchmark-Only Paths

| Path | File | Reason Deferred |
|------|------|----------------|
| `NLTagger` ANE NER | `brain/ner_engine.py` | `_nl_available` detected at import; falls back to GLiNER in `predict_async()` unless ANE explicitly enabled |
| `CoreML` NER | `brain/ner_engine.py` | Requires `ner.mlmodel` at `~/.hledac/models/`; lazy loaded only if file exists |
| CoreML ANE benchmark | `benchmarks/coreml_ane_capability.py` | Benchmark harness only — NOT production |
| Apple `apple_fm_probe` NLTagger | Not in production path | Documentation-only / benchmark |

**Policy:** Do NOT activate CoreML/NLTagger paths in production without explicit Sprint change. These are documented for future consideration.

---

### 5. Future Candidate Policy

| Candidate | Source | Status |
|-----------|--------|--------|
| `GLiNER2-PII` | Multiple papers | Not evaluated — future sprint candidate |
| `UniversalNER` | Nebra/UniversalNER | Not evaluated — future sprint candidate |
| `Instructor-NER` | InstructorEmbedding | Not evaluated — future sprint candidate |
| CoreML NLTagger | Apple NaturalLanguage | Deferred — requires model export + ANE validation |
| ANE-only NLTagger | NaturalLanguage.framework | Deferred — benefits unclear vs GLiNER CPU |

These are out-of-scope for F218C. Any activation requires a dedicated Sprint change proposal with benchmark evidence.

---

## Diagnostic Helpers

### `brain/ner_engine.py::get_ner_backend()`

**Signature:**
```python
def get_ner_backend() -> str:
```

**Returns:**
- `"gliner-relex"` — when GLiNER model is loaded
- `"nltagger"` — when ANE NaturalLanguage available and engine initialized
- `"coreml"` — when CoreML NER model loaded
- `"unavailable"` — when no backend available / engine not initialized

**Does NOT load models** — read-only snapshot of current backend state.

### `brain/ner_engine.py::get_extraction_status()`

**Signature:**
```python
def get_extraction_status() -> dict:
```

**Returns:**
```python
{
    "ner_backend": str,           # get_ner_backend() result
    "ner_loaded": bool,           # _default_engine._model is not None
    "pii_backend": "regex",       # always "regex"
    "coreml_ner_inactive": bool,  # True (CoreML path documented inactive)
    "nltagger_inactive": bool,    # not _nl_available
    "relex_model": "knowledgator/gliner-relex-large-v0.5",
    "config_model": "knowledgator/gliner-x-base",
}
```

### `security/pii_gate.py::get_pii_backend()`

**Signature:**
```python
def get_pii_backend() -> str:
```

**Returns:** `"regex"` — always regex-based (no ML models in this module)

---

## Ownership Boundaries

### NEREngine is authority for:
- Named Entity Recognition (persons, organizations, locations, etc.)
- Joint NER + Relation Extraction (`gliner-relex` model)
- Entity extraction from texts / findings
- Entity co-occurrence and summary building

### NEREngine is NOT authority for:
- PII detection / sanitization → `SecurityGate`
- Vault/export operations → `vault_manager.py`
- Steganography detection → `stego_detector.py`
- Content blocking/rejection
- Runtime memory/budget management
- Media processing

### SecurityGate is authority for:
- PII detection via regex (email, phone, SSN, etc.)
- Text sanitization with masking
- Risk scoring based on PII density
- Always-on `fallback_sanitize()` mandatory safety net

### SecurityGate is NOT authority for:
- NER/Relation Extraction → `NEREngine`
- Vault/export encryption → `vault_manager.py`
- Content blocking/rejection decisions
- Runtime memory/budget management
- Media processing

---

## Architecture Notes

- **No model is loaded at import time** for either canonical owner
- **Diagnostic helpers** (`get_ner_backend`, `get_extraction_status`, `get_pii_backend`) are read-only and do not trigger model loading
- **Singleton pattern** via `get_ner_engine()` / `quick_sanitize()` with explicit `reset_ner_engine()` / `unload()` for memory management
- **M1 8GB safe** — no ANE/NLTagger forced active; GLiNER CPU-only by default
- **Config drift warning:** `config.py::GLINER_MODEL` is `gliner-x-base` (base) but `NEREngine` default is `gliner-relex-large-v0.5` (Relex). This is a known discrepancy — Relex is the actual active model.
