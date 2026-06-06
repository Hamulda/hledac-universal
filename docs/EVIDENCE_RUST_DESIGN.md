# evidence_log.py → evidence_rs (Rust) — design

**Scope:** nahradit hash/normalizaci/serializaci/dedup z `evidence_log.py` Rust modulem v `rust_extensions/`.
**Auditovaný soubor:** `hledac/universal/evidence_log.py` (2178 L, 2 třídy: `EvidenceEvent` L114, `EvidenceLog` L224).

---

## 1. Hash místa (audit)

| # | L | Hash | Vstup | Použití |
|---|---|------|-------|---------|
| 1 | 158 | `sha256(json_str.encode())` | JSON serializace `data` (`sort_keys=True, separators=(',',':'))` | `content_hash` (16-hex prefix L523) |
| 2 | 290 | `sha256(f"GENESIS:{run_id}".encode())` | run_id string | `_genesis_hash` (per-log singleton) |
| 3 | 599 | `sha256(f"{_chain_head}:{content_hash}:{event_id}".encode())` | chain triplet | `event.chain_hash` |
| 4 | 664 | dtto (recompute path) | chain triplet | verifikace |
| 5 | 1561 | dtto | chain triplet | `verify_all` |
| 6 | 523 | `sha256(value.encode())[:16]` | `value: str` | 16-hex short id |

**Pattern:** SHA-256 nad UTF-8, 6 site vše přes `hashlib.sha256(...).hexdigest()`. Chain-input deterministický (sorted keys, ASCII). Žádný MD5/SHA1/custom.

## 2. Normalizace (audit)

| Místo | Operace | Kontext |
|-------|---------|---------|
| L1314, L1343, L403 | `.strip()` | parsování JSONL řádků (whitespace trim) |
| L1213 | `.upper()` | display formatting event_type v reportu |
| L215 | `isinstance(timestamp, str)` | defensive parse |
| L597 | `chain_input = f"{head}:{hash}:{id}"` | strukturovaná serializace přes `:` delimiter |

**Chybí:** lowercase/uppercase normalizace pro IoC hodnoty (domény, IP, hash, email) — `evidence_log.py` je **event log**, ne IoC extractor. Normalizace IoC se děje v `intel/*` a `coordinators/semantic_deduplicator.py`. Navrhuji `normalize_ioc()` přidat jako utilitu pro **obecný reuse** (volá se z `rust_extensions::ioc_extract` chain), ale **nepřepisovat** `evidence_log.py` normalizační kód.

## 3. Dedup logika (audit)

| Místo | Algoritmus | Struktura |
|-------|------------|-----------|
| L2096–2103 | `seen: set[str]`, key = `a[:60]` (first 60 chars) | exaktní substring prefix match pro `top_retro_actions` |
| L2163 | `visited: set[str]`, key = `event_id` | exaktní ID match v `get_chain()` |

**Verdikt:** `evidence_log.py` dedup je **exact match na normalizovaném klíči**, ne fuzzy/hash. Dedup přes **content hash** dělá `RotatingBloomFilter` v `rust_extensions/bloom.rs` — to je správné místo pro `is_duplicate()`. `evidence_log.py` nehashuje pro dedup, jen pro chain integrity.

## 4. Serializace/deserializace (audit)

| Místo | Formát | Cíl |
|-------|--------|-----|
| L157, L221, L409, L476 | `json.dumps(..., sort_keys=True, separators=(',',':'))` | JSONL persist (NDJSON), content_hash input |
| L406, L1316, L1346 | `json.loads(line)` | JSONL read |
| L617 | `line.encode('utf-8') + b'\n'` | bytes write to file |
| L481 | `async with self._db.begin() as conn:` | **SQLite** (NE LMDB!) — `_db` je `aiosqlite` connection |

**Důležitý fakt:** `evidence_log.py` **nepoužívá LMDB**. Backend je:
- `_persist_file: Path` → NDJSON append-only (write path)
- `_db: aiosqlite.Connection` → SQLite index (read/query path)

→ **Cílový drop-in je SQLite + NDJSON, ne LMDB.** Zero-copy `rkyv` přes mmap je atraktivní pro **NDJSON ring buffer** v režimu read-only export (viz §7).

## 5. Návrh: `evidence_rs` modul

### 5.1 API kontrakt (pyi)

```python
class IocType(str, Enum):
    DOMAIN = "domain"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    URL = "url"
    EMAIL = "email"
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    UNKNOWN = "unknown"

def normalize_ioc(raw: str, ioc_type: IocType) -> str
def hash_finding(finding: CanonicalFinding) -> bytes   # BLAKE3 32B
def is_duplicate(hash32: bytes, bloom: RotatingBloomFilter) -> bool
def serialize_finding(finding: CanonicalFinding) -> bytes   # rkyv 0.8 archív
def deserialize_finding(arch: bytes) -> CanonicalFinding     # zero-copy view
def chain_hash(prev: bytes, content: bytes, event_id: str) -> bytes   # BLAKE3 32B
```

### 5.2 Normalizační pravidla (shodná s projektem)

| IoC | Pravidlo |
|-----|---------|
| `domain` | `lower()`, strip, strip trailing `.`, IDN `idna` encode, strip `www.` (preserve) |
| `ipv4` | `ipaddress.IPv4Address(...)` round-trip (zero-pad reject; standardizovaný text) |
| `ipv6` | `ipaddress.IPv6Address(...).compressed` |
| `url` | `lower(scheme+host)`, strip fragment, sort query params, IDN host, strip default port |
| `email` | `lower()`, strip, `email.utils.parseaddr` validace, domena IDN encode |
| `md5/sha1/sha256` | `lower()`, strip, hex-validace (`^[0-9a-f]{n}$`) |
| `unknown` | `strip()` only |

### 5.3 Hash strategie

- **`BLAKE3`** (32 B / 256 bit) — 5-10× rychlejší než SHA-256 v SW, hardend pro non-chain use.
- **Chain hash** (`chain_hash`): záměrně ponechán jako **BLAKE3 keyed** s `prev` jako key prefixem → deterministický jako SHA-256, ale levnější. **Kompatibilita:** hash output 32B (vs 64 hex char u SHA-256) — vyžaduje migration okna. **Doporučení:** paralelní běh 1 sprint = emit oba (`chain_hash_sha256` legacy + `chain_hash_blake3` new), read preferuje blake3.
- **`content_hash` (16 hex):** trvá `blake3(value)[:8].hex()` (8 B = 16 hex char), drop-in kompatibilní s L523.

### 5.4 Dedup přes RotatingBloomFilter

`evidence_log.py` chain_hash nepoužívá Bloom. **Nový use case:** `is_duplicate()` se volá ze `semantic_deduplicator` / `findings_dedup` (cross-sprint). `RotatingBloomFilter` už existuje v `rust_extensions/bloom.rs` (F195C/F197B) — `evidence_rs::is_duplicate` je **thin wrapper** nad ním.

## 6. rkyv zero-copy bonus

**Trade-off:** `evidence_log.py` persistuje do NDJSON (human-readable, debug-friendly, append-only). Migrace na `rkyv` (binární archiv) by **zabila debuggovatelnost** a vyžadovala dual-read okno. **Doporučení:** `rkyv` **NE** pro `evidence_log.py` write path. Místo toho:

- **Aplikuj rkyv** v `findings/` write pathu (cross-sprint) — tam je objem >10× větší a overhead JSON parse je měřitelný.
- **Pro `evidence_log.py`:** `rkyv::check_archived_value` pouze jako **integrity check** při loadování NDJSON řádku (validuje struct layout aniž by serializoval).

```rust
// rkyv::check_archived_value::<EvidenceEvent, Error>(bytes)?  // validates bytes match schema
```

## 7. Wire-up plán

| Krok | Edit | Invariant |
|------|------|-----------|
| 1 | `rust_extensions/Cargo.toml` +deps: `blake3`, `idna`, `rkyv`, `smol_str` | bounded, fail-safe |
| 2 | `rust_extensions/src/evidence_rs.rs` (new) | jedno `#[pymodule]` per modul |
| 3 | `rust_extensions/src/lib.rs` → `m.add_submodule(evidence_rs)?` | ne-reentrant |
| 4 | `evidence_log.py` L597–599 → fallback `try: chain_hash = evidence_rs.chain_hash(...).hex() except Exception: hashlib.sha256(...)` | **SHA-256 jako fallback** pro determinismus i bez Rust |
| 5 | `tests/probe_evidence_rs.py` — 12 testů: normalize (×4 IoC typy), hash determinismus, BLAKE3 ≠ SHA-256, is_duplicate, serialize roundtrip, rkyv check | Always-on |
| 6 | `smoke_runner.py` projde (32-char BLAKE3 hex dual-write) | forward-compat |

---

## Invarianty

| ID | Test | Fail-safe |
|----|------|-----------|
| INV-1 | `normalize_ioc("FOO.COM.", DOMAIN) == "foo.com"` | vrátí `raw.lower().strip()` při chybě |
| INV-2 | `blake3(b"x")` deterministický přes 1000 iter | vrátí `hashlib.sha256` fallback |
| INV-3 | `chain_hash(prev, c, id)` s SHA-256 dual-write, legacy verze stále ověřitelná | vždy dual-emit |
| INV-4 | `is_duplicate` nikdy nevyhodí (RotatingBloom je noexcept) | vrátí `false` |
| INV-5 | `serialize_finding` roundtrip přes 100k iterací | `rkyv` schema panic → JSON fallback |
| INV-6 | žádný `unwrap()` v Rust kódu v `#[pymethod]` path | `PyResult<T>` všude |
| INV-7 | bounded: `MAX_NORMALIZE_LEN = 4096` (přes `.chars().take(4096)`) | trunc s varování |
| INV-8 | bounded: `MAX_EVENT_PAYLOAD_BYTES = 1_048_576` (1 MB) | reject s `ValueError` |
