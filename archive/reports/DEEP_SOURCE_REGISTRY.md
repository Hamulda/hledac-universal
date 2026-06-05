# DeepSourceRegistry — Sprint F270

Curated, self-updating registry of beyond-surface OSINT sources for the
Hledac Universal orchestrator. Phase 2.7 of the `UnifiedResearchEngine`
pipeline.

---

## Goal

Provide a curated catalog of OSINT-relevant sources **BEYOND the indexed
web** — dark web (`.onion` / `.i2p`), archives, paste sites, academic
mirrors, code-intelligence search engines, leak DBs, P2P gateways. The
registry is self-updating via async HEAD probes, persists `last_verified`
timestamps through LMDB, and exposes a transport-aware filter so the
orchestrator can ask *"what can I reach right now?"* given the current
transport capabilities.

---

## Architecture

```
discovery/deep_source_registry.py   ← registry + curated catalog (BLAKE2b IDs)
        ↑
enhanced_research.py                ← discover_deep_sources() helper
        ↑                              UnifiedResearchEngine._task_source_discovery()
        │
core/__main__.py                    ← --list-sources [--tier ...] CLI flag
        ↓
runtime/sprint_scheduler.py         ← (future) Phase 2.7 invocation site
        ↓
LMDB (1 MiB)                        ← last_verified persistence
```

### M1 8GB UMA invariants

| Invariant | Value | Why |
|---|---|---|
| `MAX_SOURCES_IN_REGISTRY` | 200 | Hard cap on curated entries. |
| `LMDB_MAP_SIZE` | 1 MiB | One small JSON blob per source. |
| `MAX_CONCURRENT_HEAD` | 10 | Bounded parallel HEAD probes. |
| `HEAD_TIMEOUT_S` | 5.0 | Per-probe timeout, M1-safe. |
| `writemap` / `sync` | `False` / `False` | Prevents SSD page-cache thrashing. |
| `max_dbs` | 2 | One main DB + `deep_sources` sub-DB. Prevents `MDB_DBS_FULL`. |
| Sub-DB handle | cached | Opened once, reused for env lifetime. |
| `b"deep_sources"` (bytes) | sub-DB name | lmdb accepts `str \| bytes`. |

### Authoritative seam

- `DeepSource` — frozen, slots-only dataclass; `__post_init__` enforces:
  - `reliability ∈ [0.0, 1.0]`
  - `.onion` URL ⇒ `transport_required="tor"`
  - `.i2p` / `.b32.i2p` URL ⇒ `transport_required="i2p"`
- `DeepSourceRegistry` — in-memory catalog with opt-in LMDB overlay.
- `discover_deep_sources()` — helper that returns a `list[CanonicalFinding]`
  with `source_type="source_discovery"`.

### Source classification

Five tiers × four transports × seven data types:

| Tier | Examples | Transport |
|---|---|---|
| `surface` | crt.sh, GitHub code search, IPFS gateways | `none` / `direct` |
| `dark` | Ahmia `.onion`, BBC `.onion`, IntelX `.onion` | `tor` |
| `archive` | Wayback CDX, CommonCrawl index | `none` |
| `p2p` | IPFS public gateways, DHT bootstrap | `none` |
| `academic` | Semantic Scholar, arXiv, CrossRef | `none` |

| Data type | Examples |
|---|---|
| `ct_logs` | crt.sh, CertStream, Google CT |
| `passive_dns` | SecurityTrails, CIRCL pDNS |
| `archive` / `repo` | Wayback, CommonCrawl, grep.app, Sourcegraph |
| `paste` | Pastebin, Ghostbin, Privatebin |
| `academic` | arXiv, OpenAlex, PubMed |
| `forum` | Ahmia, dark.fail mirrors |
| `leak_db` | HIBP, DeHashed, IntelX |

---

## Curated catalog (63 sources, growing)

### CT Log APIs (7)
- `crt.sh` — https://crt.sh/
- `CertStream` — https://certstream.calidog.io/
- `Facebook CT`, `Google Pilot CT`, `Google Argon CT`,
  `Cloudflare Nimbus`, `Let's Encrypt Oak`

### Passive DNS (5)
- SecurityTrails, CIRCL pDNS, Robtex, MnemonicPDNS, DNSlytics

### Archive sources (5)
- Wayback CDX, CommonCrawl Index, archive.is, CachedView, Mementos Web

### Paste sites (5)
- Pastebin, Ghostbin, Privatebin (known instance), Throwbin, Rentry

### Academic (6)
- Semantic Scholar, arXiv API, CORE.ac.uk, CrossRef REST, OpenAlex, PubMed

### Code intelligence (6)
- grep.app, Sourcegraph public, GitHub code search, GitLab, Codeberg, searchcode

### Leak intelligence (5)
- HIBP, DeHashed (gated), LeakCheck, IntelligenceX, Leak-Lookup

### Dark web clearnet mirrors (5)
- Ahmia.fi, dark.fail, onion.live, DarkOwl Vision, TorBot

### P2P (5)
- IPFS public gateways (ipfs.io, dweb.link, cf-ipfs, nftstorage), DHT bootstrap

### Dark web (real .onion, 10)
- Ahmia, dark.fail, ProtonMail, BBC, NYT, Facebook, Dread, Hidden Wiki,
  Tor Metrics, IntelX

### I2P eepsites (4)
- I2P Project, I2P Pastebin, I2P Bug Tracker, I2P Namecoin (mirror)

Total: **63 curated sources** (≥ 50 required by spec). All `.onion` URLs
require `tor` transport; all `.i2p` URLs require `i2p` transport — enforced
at `DeepSource.__post_init__` and verified by tests.

---

## API

### `DeepSourceRegistry`

```python
from hledac.universal.discovery.deep_source_registry import DeepSourceRegistry

reg = DeepSourceRegistry()                  # 0 I/O
reg.attach_lmdb("/path/to/lmdb")            # opt-in persistence
hydrated = reg.hydrate_from_lmdb()          # overlay last_verified

# Filters (sync, read-only)
reg.get_sources(tier="dark")                                    # → list[DeepSource]
reg.get_sources(transport="tor", data_type="forum")             # AND-combined
reg.get_available_sources({"direct", "tor"})                    # transport-aware

# Async verification (bounded concurrency, fail-soft)
ok = await reg.verify_source(source_id)                         # HEAD + LMDB persist
results = await reg.verify_all()                                # {sid: ok}
```

### `discover_deep_sources()` helper

```python
from hledac.universal.enhanced_research import discover_deep_sources

findings = discover_deep_sources(
    query="ransomware leak site",
    transport_capabilities={"direct", "tor"},
    max_results=20,
    tier=None,
)
# → list[CanonicalFinding] with source_type="source_discovery"
```

Relevance scoring (substring match, case-insensitive):
- 60% name match
- 30% URL match
- 10% base reliability

Output `payload_text` includes `name`, `base_url`, `tier`, `transport`,
`data_type`, `reliability`, `last_verified`, `relevance_score`.

### `UnifiedResearchEngine._task_source_discovery()`

New `Phase 2.7` task method on `UnifiedResearchEngine`. Returns:

```python
{
    "query": str,
    "findings": list[CanonicalFinding],   # source_type="source_discovery"
    "count": int,
    "tier": str | None,                    # optional tier filter from context
}
```

The task honors context overrides for `tier`, `transport_capabilities`,
and `max_results` (capped 1..100). Always completes (fail-soft on
every error).

### CLI

```bash
python -m hledac.universal.core --list-sources
python -m hledac.universal.core --list-sources --tier dark
python -m hychac.universal.core --list-sources --tier academic
```

Prints a formatted table of available sources and exits.

---

## LMDB persistence

Per-source `last_verified` timestamps are persisted as 1-tuple JSON
blobs in a named sub-DB:

```
key:   <16-char hex source_id>
value: {"last_verified": 1700000000.0}
```

Bugfixes (Sprint F270) applied:
- `max_dbs=2` on `lmdb.open()` (prevents `MDB_DBS_FULL`).
- Sub-DB handle cached in `self._db` (avoids repeated `open_db()` calls).
- `registry.close()` between instances in tests (LMDB single-writer).

The `paths.open_lmdb()` helper (Sprint 8AG §1.4) provides single-retry
lock recovery, `writemap=False`, `sync=False`.

---

## Test coverage

`tests/probe_deep_source_registry.py` — **28 hermetic tests**, no network.

```
$ python tests/probe_deep_source_registry.py
PASS  test_all_i2p_sources_require_i2p_transport
PASS  test_all_onion_sources_require_tor_transport
PASS  test_compute_source_id_collisions_unlikely
PASS  test_compute_source_id_is_deterministic
PASS  test_curated_sources_well_formed
PASS  test_deep_source_validation_reliability_bounds
PASS  test_deep_source_validation_url_consistency
PASS  test_discover_deep_sources_empty_query_returns_empty
PASS  test_discover_deep_sources_returns_canonical_findings
PASS  test_get_available_empty_capabilities
PASS  test_get_available_no_transport_required_passes
PASS  test_get_available_with_tor_capability
PASS  test_get_sources_combined_filters
PASS  test_get_sources_filter_by_data_type
PASS  test_get_sources_filter_by_tier
PASS  test_get_sources_filter_by_transport
PASS  test_hydrate_without_lmdb_returns_zero
PASS  test_lmdb_attach_idempotent
PASS  test_lmdb_db_name_is_bytes
PASS  test_lmdb_map_size_constant
PASS  test_lmdb_persistence_roundtrip
PASS  test_max_concurrent_head_constant
PASS  test_max_sources_in_registry_constant
PASS  test_registry_loads_without_network
PASS  test_source_count_minimum_50
PASS  test_verify_source_404_treated_as_reachable
PASS  test_verify_source_5xx_returns_false
PASS  test_verify_source_success_persists_timestamp

28 passed, 0 failed out of 28
```

Head-probe tests mock `aiohttp.ClientSession` so no real network is
touched. `discover_deep_sources` integration tests are run inside the
same standalone runner and exercise the canonical write path.

---

## Files touched

| File | Change | Lines |
|---|---|---|
| `discovery/deep_source_registry.py` | **NEW** | ~600 |
| `enhanced_research.py` | Add `discover_deep_sources()` + `_task_source_discovery()` | +200 |
| `core/__main__.py` | Add `--list-sources`, `--tier` flags + handler | +35 |
| `tests/probe_deep_source_registry.py` | **NEW** | ~520 |

---

## Future work

- Wire `_task_source_discovery()` into `runtime/sprint_scheduler.py` as
  Phase 2.7 advisory output (finds candidate sources per sprint topic).
- Periodic `verify_all()` job (24h cadence) to refresh `last_verified`.
- Extend curated catalog beyond 63 with user-submitted sources (gated by
  reliability ≥ 0.7).
- Add `transport_capabilities` autodetection via `TransportResolver` for
  real production integration (currently best-effort).
- Source-type-specific URL builders (e.g. crt.sh query string templates).

---

*Sprint F270 — completed 2026-06-05. All invariants verified, 28/28 tests
passing, no network calls in tests.*
