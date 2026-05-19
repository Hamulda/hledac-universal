# DISCOVERY OFFLINE REPLAY/AUDIT

**Audit date:** 2026-05-19
**Scope:** `discovery/`, `fetching/`, `pipeline/`, `tests/probe_*`, `docs/`
**Goal:**Zjistit, zda existuje společný offline replay/cache mechanismus, nebo jen individuální fixture/mirror patterny.

---

## Finding 0 — NENÍ ŽÁDNÝ SPOLEČNÝ MECHANISMUS

Žádný sdílený `OfflineCache`, `CassetteManager`, `ReplayFramework` neexistuje.
Každý adapter si buduje vlastní ad-hoc strategii.

---

## Finding 1 — Sdílené prvky napříč adaptéry

### 1a — ProviderStats registry (všechny adaptéry)

`discovery/provider_stats.py` — centralizovaný záznam úspěchu/selhání/timeoutu.
Používá ho `DiscoveryPlanner._registry` přes `record_success`, `record_failure`, `record_timeout`.
**Není cache/replay** — jen statistika. Data se nikam neukládají (jen in-memory okamžik).

### 1b — Circl PDNS cooldown (jeden adapter)

`discovery/circl_pdns_adapter.py` — modulový dict `_pdns_cooldown: dict[str, tuple[float, str]]`
s funkcemi `_enter_cooldown`, `_check_cooldown`, `_clear_cooldown`. MAX cooldown keys = 512.

Mechanismus: při provider failure → domain do cooldown listu → další dotaz na stejný domain
je potlačen (vrací prázdný výsledek s `cooldown_active=True`). LRU eviction starých domain.

```python
# discovery/circl_pdns_adapter.py:122-134
cooldown_now = time.monotonic()
in_cooldown, _, _ = _check_cooldown(domain_norm, cooldown_now)
if in_cooldown:
    return [], CooldownReport(cooldown_active=True, ...)
```

**Offline replay:** NE

### 1c — crtsh file cache (jeden adapter)

`discovery/crtsh_adapter.py` — CT log cache na disk (`~/.cache/hledac/crtsh/*.json`).
`CTProviderStatusReport` obsahuje: `ct_cache_used`, `ct_cache_stale`, `ct_cache_age_s`.

Mechanismus: response je uložena do souboru, při dalším dotazu na stejný domain+key
je načtena z cache. Stale cache je preferována během cooldown (F219E cooldown states).

```python
# crtsh_adapter.py CTProviderStatus fields (z .bak_F234D_PARALLEL_CT_PROVIDER)
ct_cache_used: bool = False
ct_cache_stale: bool = False
ct_cache_age_s: float = 0.0
cooldown_active: bool = False
cooldown_reason: Optional[str] = None
cooldown_remaining_s: float = 0.0
```

**Offline replay:** částečně (stale cache fallback), ale není to replay — je to cache.

---

## Finding 2 — Žádná offline fixture v testech discovery adaptérů

`tests/probe_e2e_signal_fixture/test_e2e_signal_fixture.py` — benchmark/fixture soubor,
ale je v `benchmarks/`, ne v `tests/probe_*` a není discovery-specific.
Je to E2E signal fixture pro HTTP fetch, ne pro discovery adaptéry.

Žádný z `tests/probe_8ab/conftest.py`, `tests/probe_8ac/test_sprint_8ac.py`,
`tests/probe_8ag/test_sprint_8ag.py` **nepoužívá offline cassette/fixture pro discovery**.
Všechny používají `pytest.fixture` pro in-memory mock objekty, ne pro persisted replay data.

---

## Finding 3 — Probe testy bez offline replay

Z `tests/probe_*` grep:
- Žádný test neimportuje `vcrpy`, `responses`, `pytest-recording`, ani podobný framework
- Žádný test nemá `.jsonl` cassette soubory
- Žádný test nemá `@pytest.mark.vcr` ani ekvivalent

---

## Finding 4 — Source adapter pattern (ti_feed_adapter)

`discovery/ti_feed_adapter.py` má `SourceAdapter` ABC + 4 implementace:
`NvdApiAdapter`, `CisaKevAdapter`, `WaybackArchiveAdapter` — ale žádná nemá
offline cache ani replay.

---

## MATRIX

| Adapter | Cache existuje? | Offline fixture test? | Cooldown? | Rate limit? | Replayable? | Source of truth |
|---|---|---|---|---|---|---|
| `circl_pdns_adapter` | ❌ (jen cooldown dict) | ❌ | ✅ module-level dict | ❌ | ❌ | live provider |
| `crtsh_adapter` | ✅ file cache (~/.cache) | ❌ | ✅ cooldown states | ❌ | ❌ (stale cache fallback) | live provider |
| `duckduckgo_adapter` | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `wayback_cdx_adapter` | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `ti_feed_adapter` (sub-adapters) | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `rss_atom_adapter` | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `cascade` | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `discovery_planner` | ❌ | ❌ | ❌ | ❌ | ❌ | live provider |
| `provider_stats` | ❌ (stat only) | ❌ | ❌ | ❌ | ❌ | in-memory |

**Verdikt:** Žádný adapter nemá offline replay. Dva mají cooldown. Jeden má file cache.

---

## DESIGN NÁVRH — Lightweight Offline Replay Framework

### Cíl
- Žádné nové heavy závislosti (žádné `vcrpy`, `responses`, `duckdb`, atd.)
- JSONL response cassette per adapter
- Opt-in via env var `HLEDAC_DISCOVERY_REPLAY=1`
- **Nikdy default pro live run** — pouze pro testování/hermetic probe
- M1-friendly bounded file cache

### Architektura

```
tools/
└── discovery_replay.py          # Nový modul — jediný nový soubor
    ├── _get_cassette_dir(adapter) → Path  # replay/{adapter}/
    ├── cassette_path(adapter, key) → Path  # replay/{adapter}/{key_hash[:2]}/{key}.jsonl
    ├── read_cassette(adapter, key) → ResponseData | None
    ├── write_cassette(adapter, key, response) → None  # atomic write, size guard
    └── replay_enabled() → bool
```

**Path formula:** `replay/{adapter_name}/{key_hash[:2]}/{key}.jsonl`
Příklad: `replay/circl_pdns/ab/evil.com.jsonl`
Hash prefix (2 chars) zabraňuje too many files per directory.

### JSONL Cassette Schema

```jsonl
{"ts": 1716134400.0, "key": "example.com", "response": {...}, "ttl_s": 86400}
{"ts": 1716134400.0, "key": "threatfox", "response": {...}, "ttl_s": 3600}
```

### Integrace (příklad pro ti_feed_adapter)

```python
# V SourceAdapter._fetch()
from tools.discovery_replay import read_cassette, write_cassette

def _fetch(self, query: str) -> list[SourceFinding]:
    if replay_enabled():
        cached = read_cassette(self.name, query)
        if cached:
            return cached["response"]

    result = await self._do_fetch(query)  # live

    if result and replay_enabled():
        write_cassette(self.name, query, result)

    return result
```

### Boundy (M1-friendly)

| Parametr | Hodnota | M1-friendly? |
|---|---|---|
| Max cassette file size | 1 MB | Yes (enforced at write-time) |
| Max total cassettes | 512 (subdirs) | Borderline — 512MB worst-case disk |
| TTL default | 24h | Yes |
| Auto-cleanup | na startu | Yes |

**512 x 1MB = 512MB worst-case.** Acceptable pro opt-in test mechanism, ne live run.

### Env var ovládání

```bash
HLEDAC_DISCOVERY_REPLAY=1        # zapne replay pro všechny adaptéry
HLEDAC_REPLAY_TTL=3600           # 1h TTL místo 24h
HLEDAC_REPLAY_DIR=/tmp/replay     # custom cassette dir (default: .hledac/replay)
```

**Atomic write:** `tempfile.NamedTemporaryFile(delete=False)` + `os.replace()`
(platform-native atomic rename, Windows-compatible).

**Size guard:** `CassetteSizeExceeded` raised if payload > 1MB — caller fails softly.

### Co NENÍ součástí návrhu

- Rate limiting — řeší `host_policies.py` a per-adapter cooldown
- Mock fixtures v testech — stávající `pytest.fixture` + `MagicMock` je dostatečný
- Live run cache — návrh je čistě pro offline replay/test, ne pro production caching
- Sdílená cooldown infra — `circl_pdns_adapter` cooldown zůstává oddělený

### Proč neexistuje společný mechanismus

Historický důvod: každý adapter vznikal izolovaně. discovery_planner dělá
dispatch a statistiku, ale nemá cache vrstvu. Sdílená cache by znamenala
závislost na jednom úložišti (LMDB/file) napříč všemi adaptéry, což by
zvýšilo coupling. Současný stav — každý adapter si řeší své boundary
independně — je záměrný (i když neoptimální).

---

## DESIGN CAVEATS (post-review)

Následující body je třeba vyřešit před implementací `tools/discovery_replay.py`:

### CRITICAL — Key collision v `cassette_path()`

Návrh používá flat namespace pro cassette soubory. Pokud
`cassette_path("circl_pdns", "evil.com")` a `cassette_path("crtsh", "evil.com")`
mapují na stejný soubor, dojde ke kolizi a corrupt JSONL.

**Oprava:** `cassette_path(adapter, key)` → `Path("replay/{adapter}/{key_hash[:2]}/{key}.jsonl")`

### HIGH — JSONL corruption on partial write

`write_cassette()` appenduje do sdíleného souboru. Pokud je proces interrupted
mid-write, JSONL parser přeskočí všechny následující platné řádky po corrupted line.

**Oprava:** Atomic write — `tempfile.NamedTemporaryFile` + `os.replace()` rename.

### MEDIUM — 1MB bound není enforceable at write time

Návrh specifikuje 1MB max, ale ne chování při překročení. Možnosti:
- Truncate (ztrácí integritu)
- Reject (caller musí catch `CassetteSizeExceeded`)
- Sidecar `.raw` file

**Oprava:** `if len(json_line) > 1_000_000: raise CassetteSizeExceeded(...)`

### MEDIUM — No race condition protection

Parallel test execution (`pytest -n auto`) může simultánně zapisovat do stejného
cassette file. `open(..., "a")` není atomic na všech filesystems.

**Oprava:** `fcntl.flock()` nebo portable file locking.

---

## SOUVISLOSTI

- `docs/DEPENDENCY_HYGIENE.md` — kontrola dependencies (není relevantní pro cache)
- `docs/LOCAL_OSINT_CAPABILITY_MATRIX.md` — capability matrix (offline replay není zmíněn)
- `tools/url_dedup.py` — RotatingBloomFilter pro URL dedup, nesouvisí
- `tools/lmdb_kv.py` — LMDB KV store, není použito pro discovery cache

---

## ZÁVĚR

**Žádný společný offline replay/cache mechanismus neexistuje.**
Pouze dva adaptéry mají jakousikache/cooldown strategii:
- `circl_pdns_adapter` — modul-level cooldown dict (in-memory, LRU bounded 512)
- `crtsh_adapter` — file-based stale cache s cooldown states

Všechny ostatní adaptéry jsou čistě online. Design návrh výše poskytuje
minimální, non-invasive řešení bez nových závislostí.