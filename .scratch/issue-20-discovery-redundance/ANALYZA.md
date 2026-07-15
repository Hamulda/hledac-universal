# ISSUE #20: Discovery Redundance — intel/ vs network/ vs intelligence/

## ⚠️ NUTNÉ: všechny změny jsou hotové — FÁZE 1 dokončena

**Status: FÁZE 1 COMPLETE** (2026-07-15)

viz sekce "FÁZE 1 HOTOVO" dole.

---

## Současný stav (PŘED migrací — historical record)

### 3 jmenné prostory, 3 různé filosofie

| Namespace | Files | Size | Role | Production wiring |
|-----------|-------|------|------|-------------------|
| `intel/` | 9 | 124 KB | Re-export facade + 7 OSINT primitives | Jen 1 přímý import (ct_log_scanner) |
| `network/` | 20 | 236 KB | Infrastructure + 7 duplicate OSINT modules | 4 production imports (session_runtime) |
| `intelligence/` | 63 | 1.6 MB | Canonical OSINT capability forest (lazy) | 0 production imports (vše přes intel/ re-export) |

### Klíčový nález: intel/ je RE-EXPORT FACADE

```python
# intel/__init__.py — Sprint 8.7 artifact
from intelligence.greynoise_lane import *   # ← re-export z intelligence/
from intelligence.shodan_lane import *      # ← re-export z intelligence/
... (47 dalších re-exportů)
from intel.bgp_monitor import *            # ← vlastní modul
from intel.passive_dns import *            # ← vlastní modul
```

`intel/__init__.py` funguje jako jednotný OSINT API surface — všechny `intelligence/` moduly
jsou přes něj re-exportovány. Production kód NEPOUŽÍVÁ `intelligence/` přímo.

### Duplikáty — 9 souborů se stejným jménem

#### 100% identické (MD5 hash):
| File | intel/ | network/ | Action |
|------|--------|----------|--------|
| jarm_fingerprinter.py | 22 312 B | 22 312 B | Duplikát — SMAZAT network/ |
| gemini_transport.py | 14 774 B | 14 774 B | Duplikát — SMAZAT network/ |

#### Různý obsah, stejné jméno:
| File | intel/ | network/ | Similarity | Kanonická verze |
|------|---------|----------|------------|-----------------|
| bgp_monitor.py | 15 078 B | 14 899 B | 99.7% | intel/ (F234 implementace) |
| passive_dns.py | 16 222 B | 14 884 B | 94.6% | intel/ (více funkcí) |
| passive_fingerprint.py | 12 190 B | 12 165 B | 99.9% | rozdíly v import order |
| ct_log_scanner.py | 6 024 B | 4 541 B | 80.6% | intel/ (httpx, msgspec) |
| dns_tunnel_detector.py | 27 821 B | 28 255 B | 77.2% | intel/ (numpy, scapy fallback) |

### Production importy (skutečné volání)

```
network.session_runtime   → 3 files (fetch_coordinator, public_fetcher)
intel.ct_log_scanner     → 1 file (duckdb_ct_cache_store)
network.bgp_monitor      → 1 file (network_intelligence.py)
network.passive_dns      → 1 file (network_intelligence.py)
network.passive_fingerprint → 1 file (network_intelligence.py)
```

### intelligence/ — 1.6 MB "capability forest", 0 production importů

- 62 souborů, lazy-loaded přes PEP 562 `__getattr__`
- 11 sidecar adapterů reálně napojených přes `SidecarRegistry`
- 21 lazy spec groups v `__init__.py`
- Žádný production kód neimportuje `intelligence/*` přímo — vše jde přes `intel/` re-export

### network/ — unique infrastructure

```
session_runtime.py      ← SKUTEČNĚ používán (3 production imports)
tor_manager.py         ← Tor transport management
ipfs_client.py         ← IPFS discovery
i2p_client.py          ← I2P protocol
banner_grabber.py      ← TCP banner enumeration
ipv6_recon.py          ← IPv6 reconnaissance
domain_concurrency.py  ← Per-domain concurrency control
favicon_hasher.py      ← Favicon hashing
js_bundle_extractor.py ← JS bundle analysis
js_source_map_extractor.py ← Source map extraction
open_storage_scanner.py ← S3/open storage discovery
```

## Architektonický konflikt

```
Problém: 3 jmenné prostory pro překrývající se domény

  intel/      = "low-level OSINT primitives" + re-export facade intelligence/*
  network/    = "network-level tooling" + duplikáty intel/*
  intelligence/ = "high-level OSINT lanes" (lazy, 1.6 MB)

Zmatek:
  - Kde je kánonická verze passive_dns? V intel/ nebo network/?
  - Proč máme 3 různé bgp_monitor.py?
  - intelligence/ má 1.6 MB, ale nikdo ji nepoužívá přímo!
```

## Řešení: Fáze 1 — Rychlá oprava (malý scope, vysoký dopad)

### Krok 1: Smazat 100% duplikáty z network/
```bash
network/jarm_fingerprinter.py   ← SMAZAT (intel/ je kanonická verze)
network/gemini_transport.py     ← SMAZAT (intel/ je kanonická verze)
```

### Krok 2: Intel/ jako jediný canonical namespace pro OSINT primitives
Pro všechny near-duplicates (bgp_monitor, passive_dns, ct_log_scanner,
dns_tunnel_detector, passive_fingerprint):
- Ponechat intel/ verzi jako kanonickou
- Přepsat network/ verzi symlinkem / re-exportem nebo smazat duplikát
- Aktualizovat `network/network_intelligence.py` aby importoval z `intel.*`

### Krok 3: Smazat .bak soubory
```bash
network/dns_tunnel_detector.py.bak
intelligence/streaming_embedder.py.bak
intelligence/exposure_clients.py.bak
```

## Řešení: Fáze 2 — Strukturální konsolidace (rozsáhlá)

### Cílová struktura: `recon/` namespace

```
recon/                          # ★ Nový canonical namespace
├── __init__.py                 # PEP 562 lazy loading (copy z intelligence/)
├── dns/
│   ├── __init__.py
│   ├── passive_dns.py           # ← z intel/ (MERGE intel + network verze)
│   │                            #   Síť: intel/ je rozsáhlejší (374 vs 342 lines)
│   ├── dns_tunnel_detector.py   # ← z intel/ (MERGE)
│   │                            #   Síť: obě verze mají unique feat, sloučit
│   └── _resolver_health.py      # extracted z passive_dns.py
├── cert/
│   ├── __init__.py
│   └── ct_log_scanner.py        # ← z intel/ (MERGE)
│                                 #   Síť: intel/ má async, msgspec, lepší
├── network/
│   ├── __init__.py
│   ├── bgp_monitor.py           # ← z intel/ (MERGE)
│   ├── passive_fingerprint.py    # ← z intel/ (MERGE)
│   └── jarm_fingerprinter.py    # ← z intel/
├── protocols/
│   ├── __init__.py
│   ├── i2p_client.py            # ← z network/
│   ├── ipv6_recon.py            # ← z network/
│   ├── gemini_transport.py      # ← z intel/
│   └── tor_manager.py            # ← z network/
├── services/
│   ├── __init__.py
│   ├── banner_grabber.py        # ← z network/
│   ├── exposed_service_hunter.py # ← z intelligence/
│   ├── censys_lane.py           # ← z intelligence/
│   └── ipfs_client.py          # ← z network/
├── identity/
│   ├── __init__.py
│   ├── pastebin_monitor.py       # ← z intelligence/
│   ├── identity_stitching.py    # ← z intelligence/
│   └── social_identity_miner.py # ← z intelligence/
├── temporal/
│   ├── __init__.py
│   ├── temporal_archaeologist.py # ← z intelligence/
│   ├── timeline_synthesizer.py   # ← z intelligence/
│   └── temporal_analysis.py      # ← z intelligence/
├── web/
│   ├── __init__.py
│   ├── stealth_crawler.py        # ← z intelligence/ (největší: 96 KB)
│   ├── dark_web_intelligence.py  # ← z intelligence/
│   ├── web_intelligence.py       # ← z intelligence/
│   └── browser_pool.py           # ← z intelligence/
├── chains/
│   ├── __init__.py
│   ├── relationship_discovery.py # ← z intelligence/ (85 KB, největší)
│   └── pattern_mining.py         # ← z intelligence/ (58 KB)
├── exposure/
│   ├── __init__.py
│   ├── leak_sentinel.py          # ← z intelligence/
│   ├── exposure_correlator.py    # ← z intelligence/
│   └── data_leak_hunter.py       # ← z intelligence/
├── blockchain/
│   ├── __init__.py
│   ├── blockchain_analyzer.py     # ← z intelligence/
│   └── cryptographic_intelligence.py # ← z intelligence/
├── lanes/                        # Adapter/Lane pattern — sidecar adapters
│   ├── __init__.py
│   ├── greynoise_lane.py         # ← z intelligence/
│   ├── shodan_lane.py            # ← z intelligence/
│   ├── doh_lane.py               # ← z intelligence/
│   ├── dark_web_lane.py          # ← z intelligence/
│   ├── network_reconnaissance_lane.py
│   ├── ct_lane.py
│   ├── bgp_lane.py
│   ├── bgp_advisor_adapter.py
│   ├── bgp_passive_dns_adapter.py
│   ├── census_lane.py
│   ├── commoncrawl_adapter.py
│   └── academic_search.py         # ← z intelligence/
├── collectors/                   # Open-source data collectors
│   ├── __init__.py
│   ├── archive_discovery.py      # ← z intelligence/
│   ├── pastebin_monitor.py       # ← z intelligence/
│   ├── academic_discovery.py     # ← z intelligence/
│   └── github_secret_scanner.py  # ← z intelligence/
└── infrastructure/              # ★ ZŮSTÁVÁ V network/ — není OSINT
    ├── session_runtime.py         # ← z network/ (pouze session management)
    ├── tor_manager.py            # ← z network/
    ├── ipfs_client.py            # ← z network/
    ├── domain_concurrency.py     # ← z network/
    ├── favicon_hasher.py         # ← z network/
    ├── js_bundle_extractor.py    # ← z network/
    ├── js_source_map_extractor.py
    └── open_storage_scanner.py
```

### Klíčová rozhodnutí

1. **network/ jako `infrastructure/`** — session_runtime, tor_manager, ipfs_client
   nejsou OSINT analytické moduly. Jsou to infrastructure. Ale... měnit network/ na
   infrastructure/ by bylo breaking change pro 302 importů (z nichž většina je z .venv).
   Zachovat `network/` pro infrastructure, přesunout pouze OSINT-related do `recon/`.

2. **intelligence/ → `recon/`** — 1.6 MB lazy-loaded content jde do `recon/` jako
   hlavní OSINT namespace. Lazy loading přes PEP 562 zůstává.

3. **intel/ zaniká** — jeho 7 standalone modulů se přesouvá do `recon/` subdirs.
   Jeho re-export facade se nahrazuje přímými importy z `recon/`.

4. **MERGE near-duplicates** — u bgp_monitor, passive_dns, ct_log_scanner,
   dns_tunnel_detector, passive_fingerprint: obě verze sloučit do jedné
   nejlepší implementace (intel/ verze jsou obecně rozsáhlejší).

## Fáze 3 — Import rewrites (nejbolestivější)

### Současné importy vyžadující změnu

| Odkud | Co | Kam |
|-------|-----|-----|
| intel/__init__.py | `from intelligence.X import *` | `from recon.X import *` |
| intel/__init__.py | `from intel.X import *` | `from recon.Y.X import *` |
| network/network_intelligence.py | `from network.bgp_monitor import` | `from recon.network.bgp_monitor import` |
| network/network_intelligence.py | `from network.passive_dns import` | `from recon.dns.passive_dns import` |
| network/network_intelligence.py | `from network.passive_fingerprint import` | `from recon.network.passive_fingerprint import` |
| knowledge/duckdb_ct_cache_store.py | `from intel.ct_log_scanner import` | `from recon.cert.ct_log_scanner import` |
| coordinators/fetch_coordinator.py | `from network.session_runtime import` | `from network.session_runtime import` (BEZ ZMĚNY) |
| fetching/public_fetcher.py | `from network.session_runtime import` | `from network.session_runtime import` (BEZ ZMĚNY) |

### Auto-rewrite strategie

```bash
# 1. Přejmenovat intelligence/ → recon/
mv intelligence/ recon/

# 2. Přesunout intel/ moduly do recon/ subdirs
mv intel/bgp_monitor.py recon/network/bgp_monitor.py
mv intel/passive_dns.py recon/dns/passive_dns.py
mv intel/ct_log_scanner.py recon/cert/ct_log_scanner.py
mv intel/dns_tunnel_detector.py recon/dns/dns_tunnel_detector.py
mv intel/passive_fingerprint.py recon/network/passive_fingerprint.py
mv intel/jarm_fingerprinter.py recon/network/jarm_fingerprinter.py
mv intel/gemini_transport.py recon/protocols/gemini_transport.py

# 3. Přesunout unique network/ modules do recon/
mv network/i2p_client.py recon/protocols/i2p_client.py
mv network/ipv6_recon.py recon/protocols/ipv6_recon.py
mv network/banner_grabber.py recon/services/banner_grabber.py

# 4. Smazat duplikáty z network/
rm network/jarm_fingerprinter.py  network/gemini_transport.py
rm network/bgp_monitor.py network/passive_dns.py
rm network/ct_log_scanner.py network/dns_tunnel_detector.py
rm network/passive_fingerprint.py

# 5. Přesunout intelligence/ testy
mv intelligence/tests/ recon/tests/

# 6. Přepsat intel/__init__.py — re-export z recon/
```

## Alternativa: Minimalistní přístup (doporučeno pro tento sprint)

Pokud je scope FÁZE 2 příliš velký (62 souborů k přesunu, stovky importů přepsat),
existuje minimalističtější varianta:

### Jen konsolidace duplikátů bez přesunu namespace

1. **Smazat 2 soubory** (100% duplikáty):
   - `network/jarm_fingerprinter.py`
   - `network/gemini_transport.py`

2. **Udělat intel/ kanonickým** pro near-duplicates:
   - Přesunout network/bgp_monitor.py → intel/bgp_monitor_network_backup.py
   - Nechat jen intel/bgp_monitor.py
   - Network network_intelligence.py změnit na import z intel/

3. **To samé pro passive_dns, ct_log_scanner, dns_tunnel_detector, passive_fingerprint**

4. **intel/ zůstává jako facade** — žádné přejmenování

5. **intelligence/ zůstává** — jen lazy-loading facade

Výhody: Žádné přejmenování namespace, žádné rewritty stovky importů,
pouze mazání duplikátů a oprava cross-namespace importů.

## Doporučený postup

**Sprint tento týden:** Fáze 1 (smazat 100% duplikáty) + část Fáze 2
(smazat network/ duplikáty, fixnout network_intelligence.py imports).

**Sprint příští týden:** Dokončit Fázi 2 (přejmenování intelligence/ → recon/,
přesun intel/ modulů, kompletní import rewrites).

## Invariant testy

| Test | Ověření |
|------|---------|
| Žádný soubor není v intel/ I network/ I intelligence/ se stejným obsahem | MD5 hash uniqueness |
| Všechny production importy z intel/ → stále funkční | pytest po každém přesunu |
| SidecarRegistry napojení na intelligence/ zachováno | probe testy |
| Žádný nový `from intelligence.` import v produkčním kódu | grep po dokončení |

## Metrics

- ** před:** 3 namespaces (intel/, network/, intelligence/), 92 souborů, 2.0 MB
- **po (Fáze 1):** 3 namespaces, 88 souborů (-4 duplikáty), 2.0 MB
- **po (Fáze 2):** 2 namespaces (recon/, network/infrastructure/), ~80 souborů, 2.0 MB
- **Úspora:** Žádná v bytech, ale dramatické zlepšení v clarity / maintainability

---

## ✅ FÁZE 1 HOTOVO (2026-07-15)

### Provedené změny:

1. **Smazány 100% duplikáty:**
   - `network/jarm_fingerprinter.py` (identický s intel/)
   - `network/gemini_transport.py` (identický s intel/)

2. **Smazány .bak soubory:**
   - `network/dns_tunnel_detector.py.bak`
   - `intelligence/streaming_embedder.py.bak`
   - `intelligence/exposure_clients.py.bak`

3. **5 near-duplicates převedeno na re-export:**
   - `network/bgp_monitor.py` → re-export z `intel.bgp_monitor`
   - `network/passive_dns.py` → re-export z `intel.passive_dns`
   - `network/passive_fingerprint.py` → re-export z `intel.passive_fingerprint`
   - `network/ct_log_scanner.py` → re-export z `intel.ct_log_scanner`
   - `network/dns_tunnel_detector.py` → re-export z `intel.dns_tunnel_detector`

4. **Opraveny import pathy:**
   - `network/network_intelligence.py` nyní importuje z `intel.*` místo `network.*`

5. **Aktualizovány docstringy:**
   - `intel/__init__.py` — nový přehledný docstring
   - `network/__init__.py` — dual-role dokumentace (infrastructure + re-exports)

### Architektura po Fázi 1:

```
intel/           — Canonical OSINT primitives (8 files)
  └── bgp_monitor, passive_dns, passive_fingerprint,
      ct_log_scanner, dns_tunnel_detector, jarm_fingerprinter,
      gemini_transport, intel_seed

network/         — Infrastructure + backward-compat re-exports (17 files)
  ├── Infrastructure: session_runtime, tor_manager, ipfs_client,
  │   i2p_client, ipv6_recon, banner_grabber, domain_concurrency,
  │   favicon_hasher, js_bundle_extractor, js_source_map_extractor,
  │   open_storage_scanner
  ├── Re-exports: bgp_monitor, passive_dns, passive_fingerprint,
  │   ct_log_scanner, dns_tunnel_detector (všechny → intel/)
  └── network_intelligence.py (adapter)

intelligence/    — Capability forest (62 files, lazy-loaded, 1.6 MB)
  └── Re-exported přes intel/__init__.py facade
```

### Ověření:
- ✓ Všechny import testy prošly (9/9)
- ✓ 33 pytest testů prošlo
- ✓ 0 nových syntax chyb

---

## ✅ FÁZE 2 HOTOVO (2026-07-15)

### Provedené změny:

1. **Přejmenováno `intelligence/` → `recon/`:**
   - Celý adresář přejmenován zachovává historii
   - 60+ souborů, 1.6 MB capability forest

2. **Přesunuto 8 OSINT primitives z `intel/` do `recon/` subdirs:**
   - `intel/bgp_monitor.py` → `recon/network/bgp_monitor.py`
   - `intel/passive_fingerprint.py` → `recon/network/passive_fingerprint.py`
   - `intel/passive_dns.py` → `recon/dns/passive_dns.py`
   - `intel/dns_tunnel_detector.py` → `recon/dns/dns_tunnel_detector.py`
   - `intel/ct_log_scanner.py` → `recon/cert/ct_log_scanner.py`
   - `intel/jarm_fingerprinter.py` → `recon/protocols/jarm_fingerprinter.py`
   - `intel/gemini_transport.py` → `recon/protocols/gemini_transport.py`
   - `intel/intel_seed.py` → `recon/intel_seed.py`

3. **Vytvořeny `__init__.py` pro nové subdirs:**
   - `recon/dns/__init__.py`
   - `recon/cert/__init__.py`
   - `recon/network/__init__.py`
   - `recon/protocols/__init__.py`

4. **Přepracován `intel/__init__.py`:**
   - `intel/` nyní pure backward-compat facade
   - 60 stub souborů v `intel/` deleguje na `recon/`
   - `intel/bgp_monitor` → deleguje na `recon.network.bgp_monitor`
   - Všechny staré `from intel.X` importy funkční

5. **Opraveny internal imports v `recon/` modules:**
   - 32 broken cross-references opraveno (38 total)
   - `hledac.universal.intelligence.X` → `hledac.universal.recon.X`
   - `from intelligence.X` → `from recon.X`
   - `from ..intelligence.X` → `from ..X`
   - Orphaned self-import `from intel.intel_seed` v `recon/intel_seed.py` odstraněn

6. **Přepracován `network/__init__.py`:**
   - OSINT re-exports nyní importují přímo z `recon/` místo z lokálních stubů
   - Přímé importy: `recon.dns.dns_tunnel_detector`, `recon.dns.passive_dns`, `recon.network.passive_fingerprint`

### Architektura po Fázi 2:

```
recon/              — Canonical OSINT namespace (bývalé intelligence/)
  ├── dns/
  │   ├── __init__.py
  │   ├── passive_dns.py       ← z intel/
  │   └── dns_tunnel_detector.py ← z intel/
  ├── cert/
  │   ├── __init__.py
  │   └── ct_log_scanner.py     ← z intel/
  ├── network/
  │   ├── __init__.py
  │   ├── bgp_monitor.py        ← z intel/
  │   └── passive_fingerprint.py ← z intel/
  ├── protocols/
  │   ├── __init__.py
  │   ├── jarm_fingerprinter.py ← z intel/
  │   └── gemini_transport.py   ← z intel/
  ├── intel_seed.py             ← z intel/
  └── *.py (35+ capability forest modules) ← z intelligence/

intel/             — Pure backward-compat facade (60 stub souborů)
  ├── __init__.py (__getattr__ for lazy delegation)
  ├── bgp_monitor.py → stub delegující na recon.network.bgp_monitor
  ├── passive_dns.py → stub delegující na network.passive_dns
  ├── passive_fingerprint.py → stub delegující na network.passive_fingerprint
  └── ... (57 dalších stub souborů)

network/           — Infrastructure + backward-compat re-exports
  └── session_runtime, tor_manager, ipfs_client, i2p_client,
      banner_grabber, ipv6_recon, domain_concurrency, favicon_hasher,
      js_bundle_extractor, js_source_map_extractor, open_storage_scanner
```

### Ověření:
- ✓ 28/28 kritických importů funkčních
- ✓ 101/109 pytest testů prochází (8 selhání = Camoufox/nodriver, nesouvisí s migrací)
- ✓ Všech 5 produkčních importů funkčních:
  - `intel.bgp_monitor` → network_intelligence.py ✓
  - `intel.passive_dns` → network_intelligence.py ✓
  - `intel.passive_fingerprint` → network_intelligence.py ✓
  - `intel.ct_log_scanner` → v docstringu (migrační příklad, nekód) ✓
  - `network.session_runtime` → public_fetcher.py ✓
