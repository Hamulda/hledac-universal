# Feature Flag System: Taxonomy, Dependencies, Validation & Presets

> **Sprint:** F-FLAG-1 (analysis only — no code changes)
> **Author:** Claude (Opus 4.8) — flag system audit
> **Scope:** `~/PycharmProjects/Hledac/hledac/universal/`
> **Status:** ANALYSIS — proposed, not yet implemented

---

## Executive Summary

Projekt má **45+ user-facing flagů** (HLEDAC_ENABLE_*) plus **86+ library-availability flagů** (*_AVAILABLE) a desítky konfiguračních env var. Aktuálně:

- **Žádná taxonomická struktura** — flat namespace v `CLAUDE.md`
- **2 nezávislé resolver patterny** vedle sebe: `utils/feature_flags.py` (canonical, 1 feature) × `os.environ.get("HLEDAC_...") == "1"` (ad-hoc, 45+ feature)
- **Žádná fail-fast validace** — chybné kombinace flagů se projeví až za běhu (silent no-op, crash, OOM)
- **13+ flagů v kódu nezdokumentovaných v CLAUDE.md** (drift)
- **5+ skrytých závislostí** (DSPY→LLM, HERMES_SYNTHESIS→LLM, GRAPH_RAG→LLM+RAM, BGP_PDNS→BGP, MOBILECLIP→RAM)
- **2 alias kolize** (SYNTHESIS ↔ HERMES_SYNTHESIS, TOR vs TOR_PROXY_URL)

Doporučení: zavést `utils/feature_flags.py` jako **jediný resolver**, přidat **declarative registry s dependency rules** a **fail-fast validation hook** v `__main__.py::main()` před `run_sprint()`.

---

## 1. Aktuální stav — inventář

### 1.1 Tři odlišné vrstvy flag systému

| Vrstva | Příklad | Počet | Účel | Resolution pattern |
|--------|---------|-------|------|---------------------|
| **L1 User flags** | `HLEDAC_ENABLE_LLM=1` | 58 unikátních v kódu (45 zdokumentovaných v CLAUDE.md) | Opt-in/out feature | Ad-hoc `os.environ.get(...)` (45×) + canonical `is_deep_research_enabled()` (1×) |
| **L2 Library availability** | `MLX_AVAILABLE` | 86 v `.flags_baseline.json` | Detekce nainstalované knihovny | `try: import X; X_AVAILABLE = True` (module-level) |
| **L3 Config values** | `HLEDAC_LANCEDB_QUANTIZE`, `HLEDAC_RESEARCH_MODE` | ~15 | Numerické/textové konfigurace | Přímé čtení, parsování |
| **L4 External service** | `SHODAN_API_KEY`, `TOR_PROXY_URL` | ~8 | API klíče, proxy URL | Přímé čtení, validace při použití |
| **L5 Capability derivations** | `is_tor_available()` | ~10 v `capabilities.py` | Agregace L2+L1 do logických capabilities | Funkce vracející bool |

### 1.2 L1 flagy (HLEDAC_ENABLE_*) — kompletní seznam z kódu

**Dokumentované v CLAUDE.md (45):**

| Flag | Skupina | M1 RAM impact |
|------|---------|---------------|
| ACADEMIC | intelligence_apis | +80MB |
| ALT_PROTOCOLS | dark_surface | +60MB |
| BANNER_GRAB | network | nízký |
| BGP | network | nízký |
| BGP_PDNS | network | nízký |
| CAPTCHA_DETECTION | stealth | nízký |
| CENSYS | intelligence_apis | nízký |
| COMMONCRAWL | intelligence_apis | nízký |
| CONTENT_LAYER | brain | nízký |
| CURL_CFFI | network | +50MB |
| DARK_PIVOTS | dark_surface | střední |
| DHT | dark_surface | +100MB |
| DIGITAL_GHOST | forensics | nízký |
| DSPY | brain | +200MB |
| FEDIVERSE | dark_surface | +50MB |
| GOPHER | network | nízký |
| GRAPH_ANALYSIS | storage | +50MB |
| GRAPH_RAG | brain | +300MB (embeddings) |
| GREYNOISE | intelligence_apis | nízký |
| HEAVY_BROWSER | network | +1.5GB (Playwright) |
| HERMES_SYNTHESIS | brain | sdílí LLM |
| HTTPX_H2 | network | nízký |
| HYPOTHESIS | brain | sdílí LLM |
| I2P | dark_surface | střední |
| IMAGE_OSINT | forensics | nízký |
| IPFS | dark_surface | nízký |
| LAYERS | system | nízký |
| LEAKSENTINEL | intelligence_apis | +30MB |
| LLM | brain | +2.2GB (Hermes3 4bit) |
| NODRIVER | network | +400MB |
| NYM | dark_surface | střední |
| PRIVACY_LAYER | system | nízký |
| PROVIDERLESS_DISCOVERY | network | nízký |
| RESEARCH_LAYER | system | nízký |
| SHODAN | intelligence_apis | nízký |
| SOCIAL | network | nízký |
| STEALTH_LAYER | stealth | +50MB |
| STEGANOGRAPHY | forensics | nízký |
| SYNTHESIS | brain | DEPRECATED alias |
| TEMPORAL_STORE | storage | +50MB |
| TI_FEEDS | intelligence_apis | nízký |
| TOR | dark_surface | střední |
| ZERO_ATTRIBUTION | stealth | nízký |
| ZKP | dark_surface | nízký |
| ACADEMIC | intelligence_apis | (dup) |

**Nezdokumentované v CLAUDE.md (13+):**

| Flag | Kde se používá | Drift status |
|------|----------------|--------------|
| `HLEDAC_ENABLE_ADVANCED_` | (broken ref, zřejmě typo) | 🗑️ dead |
| `HLEDAC_ENABLE_ADVANCED_RAG` | neidentifikováno v kódu | ⚠️ drift |
| `HLEDAC_ENABLE_ADVANCED_STEALTH` | neidentifikováno v kódu | ⚠️ drift |
| `HLEDAC_ENABLE_CB_PERSISTENCE` | circuit-breaker persistence | 🆕 undocumented |
| `HLEDAC_ENABLE_DSPY_` | (broken ref, zřejmě typo) | 🗑️ dead |
| `HLEDAC_ENABLE_EVIDENCE_ANALYZER` | forensics | 🆕 undocumented |
| `HLEDAC_ENABLE_FEDERATED_HYBRID` | federated/bridge.py | 🆕 undocumented |
| `HLEDAC_ENABLE_FEDERATED_P2P` | federated/sidecar_adapter.py | 🆕 undocumented |
| `HLEDAC_ENABLE_GRAPH_PATHS` | graph/quantum_pathfinder.py | 🆕 undocumented |
| `HLEDAC_ENABLE_MOBILECLIP` | multimodal/fusion.py:9 | 🆕 undocumented |
| `HLEDAC_ENABLE_MY_SIDECAR` | example v project CLAUDE.md (sample, neimplementováno) | 📝 example |
| `HLEDAC_ENABLE_PQ_EXPORT` | federated/post_quantum.py | 🆕 undocumented |
| `HLEDAC_ENABLE_RL` | rl/ (Sprint F223K) | 🆕 undocumented |
| `HLEDAC_ENABLE_STRUCTURED` | neidentifikováno v kódu | ⚠️ drift |
| `HLEDAC_ENABLE_X` | (broken ref, zřejmě placeholder) | 🗑️ dead |

**Akce:** Přidat 7 chybějících do CLAUDE.md (CB_PERSISTENCE, EVIDENCE_ANALYZER, FEDERATED_HYBRID, FEDERATED_P2P, GRAPH_PATHS, MOBILECLIP, PQ_EXPORT, RL). Smazat 3 mrtvé (ADVANCED_, DSPY_, X). Ověřit ADVANCED_RAG, ADVANCED_STEALTH, STRUCTURED.

---

## 2. Hierarchická flag taxonomie (5 skupin)

### 2.1 Návrh skupin

```
HLEDAC_ENABLE_*
├── network/              (HTTP, transport, fetch)
│   ├── curl_cffi         (JA3 fingerprinting)
│   ├── httpx_h2          (HTTP/2 support)
│   ├── tor               (Tor SOCKS5)
│   ├── i2p               (I2P SAM bridge)
│   ├── nym               (Nym mixnet)
│   ├── gopher            (alt protocol)
│   ├── alt_protocols     (Finger, Whois, etc.)
│   ├── ipfs              (IPFS gateway)
│   ├── dht               (DHT discovery)
│   ├── fediverse         (Mastodon/ActivityPub)
│   ├── social            (Twitter/X, Reddit)
│   ├── banner_grab       (TCP banners)
│   ├── captcha_detection (CAPTCHA solving)
│   ├── providerless_disc (Cascade DDG→Historical)
│   ├── nodriver          (headless browser)
│   └── heavy_browser     (Playwright, M1 RAM critical)
│
├── brain/                (LLM, inference, RAG)
│   ├── llm               (Hermes3 4bit, M1 2.2GB)
│   ├── dspy              (compiled programs, needs llm)
│   ├── hermes_synthesis  (F260, needs llm)
│   ├── hypothesis        (pivot planner, needs llm)
│   ├── content_layer     (semantic analysis)
│   ├── graph_rag         (graph embeddings, needs llm)
│   └── evidence_analyzer (forensic reasoning)
│
├── storage/              (DuckDB, LMDB, graph, persistence)
│   ├── graph_analysis    (Leiden community detection)
│   ├── graph_paths       (quantum pathfinder, needs graph_analysis)
│   ├── temporal_store    (CT archive timelines)
│   ├── lancedb_quantize  (IVF-PQ config, not boolean)
│   └── cb_persistence    (circuit breaker state)
│
├── dark_surface/         (Tor, I2P, IPFS, federated, ZKP)
│   ├── dark_pivots       (orchestrated pivots)
│   ├── tor               (see network/tor)
│   ├── i2p               (see network/i2p)
│   ├── nym               (see network/nym)
│   ├── ipfs              (see network/ipfs)
│   ├── dht               (see network/dht)
│   ├── fediverse         (see network/fediverse)
│   ├── gopher            (see network/gopher)
│   ├── alt_protocols     (see network/alt_protocols)
│   ├── federated         (sidecar activation)
│   ├── federated_hybrid  (P2P + bridge, needs federated)
│   ├── federated_p2p     (pure P2P, alternative to hybrid)
│   ├── pq_export         (post-quantum crypto export)
│   └── zkp               (zero-knowledge proofs, needs oqs)
│
├── intelligence_apis/    (External OSINT providers)
│   ├── shodan            (device/IP fingerprints)
│   ├── greynoise         (mass scanner detection)
│   ├── censys            (cert transparency, TLS)
│   ├── commoncrawl       (web archive search)
│   ├── ti_feeds          (threat intel feeds)
│   ├── academic          (arXiv, PubMed)
│   ├── leaksentinel      (paste/GitHub/breach signals)
│   └── research_layer    (consolidated research APIs)
│
├── forensics/            (Evidence extraction, steg)
│   ├── image_osint       (EXIF, GPS, OCR)
│   ├── steganography     (LSB detection)
│   ├── digital_ghost     (anti-forensics detection)
│   └── evidence_analyzer (consolidated forensics)
│
├── stealth/              (Anonymity, anti-detection)
│   ├── stealth_layer     (UA rotation, jitter)
│   ├── advanced_stealth  (JA3 + JA4)
│   ├── captcha_detection (see network/)
│   ├── zero_attribution  (no breadcrumbs)
│   └── privacy_layer     (GDPR compliance)
│
└── system/               (Cross-cutting)
    ├── layers            (security layer manager)
    ├── synthesis         (DEPRECATED → use brain/hermes_synthesis)
    └── rl                (Sprint F223K RL feedback)
```

### 2.2 Implementační model

Navrhuji rozšířit `utils/feature_flags.py` o **declarative registry** s taxonomickými metadaty:

```python
# utils/feature_flags.py (rozšíření)

@dataclass(frozen=True)
class FlagSpec:
    """Canonical specification of a single HLEDAC_ENABLE_* feature flag."""
    name: str
    group: str                # network/brain/storage/dark_surface/intelligence_apis/forensics/stealth/system
    default: bool = False
    m1_ram_mb: int = 0        # estimated RSS impact
    requires: frozenset[str] = field(default_factory=frozenset)  # other flags
    conflicts: frozenset[str] = field(default_factory=frozenset)  # mutually exclusive
    deprecated_by: str | None = None
    legacy_alias: str | None = None
    doc: str = ""

# ─── Registry (the single source of truth) ──────────────────────────
FLAG_REGISTRY: dict[str, FlagSpec] = {
    # ─── network/ ───
    "HLEDAC_ENABLE_CURL_CFFI": FlagSpec(
        name="HLEDAC_ENABLE_CURL_CFFI",
        group="network", m1_ram_mb=50,
        doc="curl_cffi HTTP transport (JA3 fingerprinting)",
    ),
    "HLEDAC_ENABLE_HTTPX_H2": FlagSpec(
        name="HLEDAC_ENABLE_HTTPX_H2",
        group="network", m1_ram_mb=10,
        conflicts=frozenset({"HLEDAC_ENABLE_CURL_CFFI"}),
        doc="httpx HTTP/2 backend (conflicts with curl_cffi)",
    ),
    "HLEDAC_ENABLE_HEAVY_BROWSER": FlagSpec(
        name="HLEDAC_ENABLE_HEAVY_BROWSER",
        group="network", m1_ram_mb=1500,
        conflicts=frozenset({"HLEDAC_ENABLE_NODRIVER"}),
        doc="Playwright (M1 RAM critical, 1.5GB+ headroom)",
    ),
    "HLEDAC_ENABLE_NODRIVER": FlagSpec(
        name="HLEDAC_ENABLE_NODRIVER",
        group="network", m1_ram_mb=400,
        conflicts=frozenset({"HLEDAC_ENABLE_HEAVY_BROWSER"}),
        doc="nodriver headless browser (Chrome binary required)",
    ),
    # ... atd. pro všech 58 flagů
}

def is_flag_enabled(name: str) -> bool:
    """Canonical resolver. Replaces ad-hoc os.environ.get(...) pattern."""
    spec = FLAG_REGISTRY.get(name)
    if spec is None:
        raise UnknownFlagError(name)
    if spec.legacy_alias:
        return _env_truthy(name) or _env_truthy(spec.legacy_alias)
    return _env_truthy(name)
```

**Dopad:** `sprint_scheduler.py:6177`, `public_fetcher.py:1663`, `sprint_scheduler.py:23241` aj. (60+ site) přepsat z `os.environ.get("HLEDAC_X", "0") == "1"` na `is_flag_enabled("HLEDAC_X")`. **Audit-friendly**, **IDE-navigable**, **validatable**.

### 2.3 Discovery helper

```python
def list_flags(group: str | None = None) -> list[FlagSpec]:
    """List all known flags, optionally filtered by group."""
    if group is None:
        return list(FLAG_REGISTRY.values())
    return [s for s in FLAG_REGISTRY.values() if s.group == group]

def estimate_m1_ram_pressure(active: set[str]) -> int:
    """Sum M1 RSS impact of currently active flags. Used by M1ResourceGovernor."""
    return sum(FLAG_REGISTRY[f].m1_ram_mb for f in active if f in FLAG_REGISTRY)
```

---

## 3. Undocumented flag dependencies (5+ nalezeno)

### 3.1 Implication rules (A requires B)

| # | Feature flag | Required flags | Evidence | Severity |
|---|--------------|----------------|----------|----------|
| 1 | `HLEDAC_ENABLE_DSPY` | `HLEDAC_ENABLE_LLM` | `brain/dspy_service.py:35,81` — DSPy a Hermes3LM mají nezávislé gates; pokud DSPY=1 ale LLM=0, vrátí None | **HIGH** (silent no-op) |
| 2 | `HLEDAC_ENABLE_HERMES_SYNTHESIS` | `HLEDAC_ENABLE_LLM` | `sprint_scheduler.py:22096` — "HLEDAC_ENABLE_LLM=1 (same as synthesis)" | **HIGH** (loads Hermes twice without LLM) |
| 3 | `HLEDAC_ENABLE_HYPOTHESIS` | `HLEDAC_ENABLE_LLM` | `sprint_scheduler.py:23806` — "Gate: HLEDAC_ENABLE_HYPOTHESIS=1 and RAM < 70%" (implicit Hermes load) | **HIGH** |
| 4 | `HLEDAC_ENABLE_GRAPH_RAG` | `HLEDAC_ENABLE_LLM` + `HLEDAC_ENABLE_GRAPH_ANALYSIS` + RAM<5GB | `sprint_scheduler.py:29590` — "Gate: HLEDAC_ENABLE_GRAPH_RAG=1 + RAM check < 5.0GB" | **CRITICAL** (embeddings need Hermes; graph needs DuckPGQ) |
| 5 | `HLEDAC_ENABLE_BGP_PDNS` | `HLEDAC_ENABLE_BGP` | BGP_PDNS je sub-feature BGP enrichment, bez BGP parent nefunguje | **MEDIUM** |
| 6 | `HLEDAC_ENABLE_FEDERATED_HYBRID` | `HLEDAC_ENABLE_FEDERATED` | Hybrid mód = federated + bridge; bez parent gateway | **MEDIUM** |
| 7 | `HLEDAC_ENABLE_GRAPH_PATHS` | `HLEDAC_ENABLE_GRAPH_ANALYSIS` | Quantum pathfinder čte z DuckPGQ; bez graph analysis žádná data | **MEDIUM** |
| 8 | `HLEDAC_ENABLE_PQ_EXPORT` | `HLEDAC_ENABLE_FEDERATED` | Post-quantum export smysl dává jen v federated kontextu | **LOW** |
| 9 | `HLEDAC_ENABLE_EVIDENCE_ANALYZER` | `HLEDAC_ENABLE_IMAGE_OSINT` OR `HLEDAC_ENABLE_STEGANOGRAPHY` | Evidence analyzer agreguje z těchto dvou | **LOW** |
| 10 | `HLEDAC_ENABLE_MOBILECLIP` | RAM<5GB (M1) | `multimodal/fusion.py:8` — "matching the M1 RAM budget" | **HIGH** (M1 OOM) |

### 3.2 Mutual exclusion (A conflicts B)

| # | Pair | Reason | Evidence |
|---|------|--------|----------|
| 1 | `HEAVY_BROWSER` ↔ `NODRIVER` | Chrome binary conflict (oba instalují chromium) | `public_fetcher.py:1663-1664` |
| 2 | `CURL_CFFI` ↔ `HTTPX_H2` | Různé HTTP backends, ne obojí najednou | `e2e_sprint_probe.py:388` |
| 3 | `FEDERATED_HYBRID` ↔ `FEDERATED_P2P` | Hybrid=P2P+bridge, P2P=čistý P2P; kontradikce | `federated/__init__.py` |
| 4 | `SYNTHESIS` ↔ `HERMES_SYNTHESIS` | SYNTHESIS je deprecated alias | `utils/feature_flags.py:54` |
| 5 | `TOR` vyžaduje `TOR_PROXY_URL` | Bez běžícího tor_manageru je TOR no-op | `network/tor_manager.py` |
| 6 | `I2P` vyžaduje `I2P_PROXY_URL` | Stejný pattern jako TOR | `.env.example` |

### 3.3 Resource gates (A requires hardware)

| Flag | Hard requirement | Failure mode |
|------|------------------|--------------|
| `HLEDAC_ENABLE_LLM` | MLX nainstalován + RAM ≥ 2.5GB | Hermes load crashne |
| `HLEDAC_ENABLE_NODRIVER` | Chrome/Chromium binárka | ImportError při startu |
| `HLEDAC_ENABLE_HEAVY_BROWSER` | Playwright + RAM ≥ 6GB | OOM kill |
| `HLEDAC_ENABLE_TOR` | tor daemon běží | ConnectionError při fetch |
| `HLEDAC_ENABLE_I2P` | i2pd běží | ConnectionError |
| `HLEDAC_ENABLE_ZKP` | liboqs (OQS) nainstalován | ImportError |

### 3.4 Compound budget rules (multi-flag M1 limit)

Když je aktivních více flagů současně, **součet RAM přesahuje 6.25GB M1 budget**:

| Kombinatione | Estimated RSS | Status |
|--------------|---------------|--------|
| `LLM + HYPOTHESIS + HERMES_SYNTHESIS` | 2.2 + 0.2 + 0.2 = 2.6GB | OK (single Hermes model) |
| `LLM + DSPY + GRAPH_RAG` | 2.2 + 0.2 + 0.3 = 2.7GB | OK (DSPy + Hermes3 share) |
| `LLM + NODRIVER + GRAPH_RAG` | 2.2 + 0.4 + 0.3 = 2.9GB | ⚠️ warning |
| `LLM + HEAVY_BROWSER + GRAPH_RAG` | 2.2 + 1.5 + 0.3 = 4.0GB | ⚠️ warning |
| `LLM + HEAVY_BROWSER + NODRIVER` | 2.2 + 1.5 + 0.4 = 4.1GB | 🚫 **BLOCK** (conflicts) |
| `LLM + HYPOTHESIS + DSPY + GRAPH_RAG + MOBILECLIP` | 2.2 + 0.2 + 0.2 + 0.3 + 0.5 = 3.4GB | ⚠️ warning |
| `LLM + ALL_DARK_SURFACE` | 2.2 + 0.5 = 2.7GB | OK |
| `LLM + ALL_INTELLIGENCE_APIS` | 2.2 + 0.1 = 2.3GB | OK (rate-limited) |

---

## 4. Fail-Fast validation na startu

### 4.1 Návrh API

```python
# utils/feature_flags.py (rozšíření)

class FlagValidationError(RuntimeError):
    """Raised at startup when flag combination is invalid."""
    def __init__(self, errors: list[str], warnings: list[str]):
        self.errors = errors
        self.warnings = warnings
        msg = f"Feature flag validation failed:\n" + "\n".join(f"  ❌ {e}" for e in errors)
        if warnings:
            msg += "\n\nWarnings:\n" + "\n".join(f"  ⚠️  {w}" for w in warnings)
        super().__init__(msg)


def validate_flag_combo(active_flags: set[str]) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for the given set of active flags.

    Errors → fail-fast (raise FlagValidationError)
    Warnings → log warning, proceed
    """
    errors: list[str] = []
    warnings: list[str] = []

    for flag_name in active_flags:
        spec = FLAG_REGISTRY.get(flag_name)
        if spec is None:
            errors.append(f"Unknown flag: {flag_name!r} (not in FLAG_REGISTRY)")
            continue

        # 1. Check requires
        for req in spec.requires:
            if req not in active_flags:
                errors.append(
                    f"{flag_name} requires {req} (currently disabled). "
                    f"Enable it: export {req}=1"
                )

        # 2. Check conflicts (mutual exclusion)
        for conflict in spec.conflicts:
            if conflict in active_flags:
                errors.append(
                    f"{flag_name} conflicts with {conflict} (mutual exclusion). "
                    f"Disable one of them."
                )

    # 3. Pairwise mutual exclusion (symmetric)
    for a_name, a_spec in FLAG_REGISTRY.items():
        if a_name not in active_flags:
            continue
        for b in a_spec.conflicts:
            if b in active_flags:
                # avoid double-reporting
                pass  # already reported in (1)

    # 4. Resource gates
    total_ram = estimate_m1_ram_pressure(active_flags)
    if total_ram > 6_250:  # 6.25GB M1 ceiling
        errors.append(
            f"Combined M1 RAM impact {total_ram}MB exceeds 6.25GB ceiling. "
            f"Reduce active flags or run on bigger host."
        )
    elif total_ram > 5_500:  # 5.5GB soft warning
        warnings.append(
            f"Combined M1 RAM impact {total_ram}MB > 5.5GB soft ceiling. "
            f"Expect reduced performance."
        )

    # 5. LLM model share detection (Hermes3 should not load twice)
    llm_loaders = active_flags & {
        "HLEDAC_ENABLE_LLM",
        "HLEDAC_ENABLE_HERMES_SYNTHESIS",
        "HLEDAC_ENABLE_DSPY",
        "HLEDAC_ENABLE_HYPOTHESIS",
        "HLEDAC_ENABLE_GRAPH_RAG",
    }
    if len(llm_loaders) > 1 and "HLEDAC_ENABLE_LLM" not in active_flags:
        warnings.append(
            f"LLM-dependent flags {sorted(llm_loaders)} active but "
            f"HLEDAC_ENABLE_LLM=0 → Hermes3 will not load. "
            f"These flags will be silent no-ops."
        )

    return errors, warnings
```

### 4.2 Wire-up v `__main__.py`

Navrhuji přidat **validation hook** úplně na začátek `main()` (před `run_sprint()`), aby fail-fast zachytil chyby dřív, než se spustí cokoliv drahého:

```python
# core/__main__.py (návrh úpravy)
from utils.feature_flags import (
    is_flag_enabled, validate_flag_combo, FlagValidationError,
    FLAG_REGISTRY, list_flags,
)

def _collect_active_flags() -> set[str]:
    """Scan process env for HLEDAC_ENABLE_*=1."""
    return {
        name for name in FLAG_REGISTRY
        if is_flag_enabled(name)
    }

def main() -> int:
    # ─── Phase 0: Flag validation (fail-fast) ────────────────────────
    active = _collect_active_flags()
    errors, warnings = validate_flag_combo(active)
    for w in warnings:
        log.warning("[flags] %s", w)
    if errors:
        # print + exit 2, NOT raise — keeps CI happy
        print(FlagValidationError(errors, warnings), file=sys.stderr)
        return 2
    log.info("[flags] %d active flags validated OK", len(active))

    # ... existing main() body
```

### 4.3 Test surface

```python
# tests/test_flag_validation.py (návrh)

class TestFlagValidation:
    def test_dspy_without_llm_is_error(self):
        errors, _ = validate_flag_combo({"HLEDAC_ENABLE_DSPY"})
        assert any("requires HLEDAC_ENABLE_LLM" in e for e in errors)

    def test_heavy_browser_conflicts_nodriver(self):
        errors, _ = validate_flag_combo({
            "HLEDAC_ENABLE_HEAVY_BROWSER",
            "HLEDAC_ENABLE_NODRIVER",
        })
        assert any("mutual exclusion" in e for e in errors)

    def test_curl_cffi_conflicts_httpx_h2(self):
        errors, _ = validate_flag_combo({
            "HLEDAC_ENABLE_CURL_CFFI",
            "HLEDAC_ENABLE_HTTPX_H2",
        })
        assert any("mutual exclusion" in e for e in errors)

    def test_m1_budget_breach_is_error(self):
        flags = {"HLEDAC_ENABLE_LLM", "HLEDAC_ENABLE_HEAVY_BROWSER",
                 "HLEDAC_ENABLE_NODRIVER", "HLEDAC_ENABLE_GRAPH_RAG",
                 "HLEDAC_ENABLE_DSPY", "HLEDAC_ENABLE_HERMES_SYNTHESIS"}
        errors, _ = validate_flag_combo(flags)
        assert any("6.25GB ceiling" in e for e in errors)

    def test_llm_dependent_silent_noop_warns(self):
        # DSPY=1, LLM=0 → warn user that DSPy will be no-op
        errors, warnings = validate_flag_combo({"HLEDAC_ENABLE_DSPY"})
        # actually this is an ERROR (require) not a warn
        assert any("requires" in e for e in errors)

    def test_known_clean_combo_passes(self):
        # RECON preset (no LLM)
        errors, warnings = validate_flag_combo({
            "HLEDAC_ENABLE_TOR", "HLEDAC_ENABLE_I2P", "HLEDAC_ENABLE_IPFS",
            "HLEDAC_ENABLE_DARK_PIVOTS", "HLEDAC_ENABLE_NODRIVER",
            "HLEDAC_ENABLE_STEALTH_LAYER",
        })
        assert errors == []
```

---

## 5. Flag presets (profily)

### 5.1 Návrh 5 presetů

| Preset | Scope | Aktivní flagy | RAM est. | M1 safe? |
|--------|-------|---------------|----------|----------|
| **MINIMAL** | CI, unit testy, no network | (žádné) | ~2GB | ✅ |
| **OSINT** | Výchozí operátor, public web | TI_FEEDS, IMAGE_OSINT, STEGANOGRAPHY, LEAKSENTINEL, CENSYS, SHODAN, GREYNOISE, COMMONCRAWL, PROVIDERLESS_DISCOVERY, TEMPORAL_STORE | ~2.5GB | ✅ |
| **RECON** | Darksurface + stealth, no LLM | DARK_PIVOTS, TOR, I2P, IPFS, DHT, FEDIVERSE, GOPHER, ALT_PROTOCOLS, NYM, STEALTH_LAYER, NODRIVER, ADVANCED_STEALTH, ZERO_ATTRIBUTION, PRIVACY_LAYER, BGP, BGP_PDNS, BANNER_GRAB | ~3GB | ✅ |
| **RESEARCH** | LLM + graph, no darksurface | LLM, DSPY, HYPOTHESIS, HERMES_SYNTHESIS, GRAPH_RAG, GRAPH_ANALYSIS, GRAPH_PATHS, CONTENT_LAYER, EVIDENCE_ANALYZER, ACADEMIC, RESEARCH_LAYER, DIGITAL_GHOST, HYPOTHESIS | ~4.5GB | ✅ |
| **FULL** | Vše, M1 unsafe | Všechny HLEDAC_ENABLE_*=1 | ~7GB+ | 🚫 M1 unsafe |

### 5.2 Implementační model

```python
# utils/feature_flags.py (rozšíření)

@dataclass(frozen=True)
class FlagPreset:
    """Named collection of flags that activate together."""
    name: str
    description: str
    flags: frozenset[str]
    m1_safe: bool
    requires_advanced_host: bool = False

PRESETS: dict[str, FlagPreset] = {
    "minimal": FlagPreset(
        name="minimal",
        description="CI/test profile, no network, no LLM",
        flags=frozenset(),
        m1_safe=True,
    ),
    "osint": FlagPreset(
        name="osint",
        description="Default operator profile: public OSINT APIs",
        flags=frozenset({
            "HLEDAC_ENABLE_TI_FEEDS",
            "HLEDAC_ENABLE_IMAGE_OSINT",
            "HLEDAC_ENABLE_STEGANOGRAPHY",
            "HLEDAC_ENABLE_LEAKSENTINEL",
            "HLEDAC_ENABLE_CENSYS",
            "HLEDAC_ENABLE_SHODAN",
            "HLEDAC_ENABLE_GREYNOISE",
            "HLEDAC_ENABLE_COMMONCRAWL",
            "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY",
            "HLEDAC_ENABLE_TEMPORAL_STORE",
        }),
        m1_safe=True,
    ),
    "recon": FlagPreset(
        name="recon",
        description="Darksurface + stealth, no LLM",
        flags=frozenset({
            "HLEDAC_ENABLE_DARK_PIVOTS",
            "HLEDAC_ENABLE_TOR", "HLEDAC_ENABLE_I2P", "HLEDAC_ENABLE_NYM",
            "HLEDAC_ENABLE_IPFS", "HLEDAC_ENABLE_DHT", "HLEDAC_ENABLE_FEDIVERSE",
            "HLEDAC_ENABLE_GOPHER", "HLEDAC_ENABLE_ALT_PROTOCOLS",
            "HLEDAC_ENABLE_STEALTH_LAYER", "HLEDAC_ENABLE_NODRIVER",
            "HLEDAC_ENABLE_ZERO_ATTRIBUTION", "HLEDAC_ENABLE_PRIVACY_LAYER",
            "HLEDAC_ENABLE_BGP", "HLEDAC_ENABLE_BGP_PDNS",
            "HLEDAC_ENABLE_BANNER_GRAB",
        }),
        m1_safe=True,
    ),
    "research": FlagPreset(
        name="research",
        description="LLM + graph + research APIs, no darksurface",
        flags=frozenset({
            "HLEDAC_ENABLE_LLM", "HLEDAC_ENABLE_DSPY",
            "HLEDAC_ENABLE_HYPOTHESIS", "HLEDAC_ENABLE_HERMES_SYNTHESIS",
            "HLEDAC_ENABLE_GRAPH_RAG", "HLEDAC_ENABLE_GRAPH_ANALYSIS",
            "HLEDAC_ENABLE_GRAPH_PATHS", "HLEDAC_ENABLE_CONTENT_LAYER",
            "HLEDAC_ENABLE_EVIDENCE_ANALYZER",
            "HLEDAC_ENABLE_ACADEMIC", "HLEDAC_ENABLE_RESEARCH_LAYER",
            "HLEDAC_ENABLE_DIGITAL_GHOST", "HLEDAC_ENABLE_DEEP_RESEARCH",
        }),
        m1_safe=True,
    ),
    "full": FlagPreset(
        name="full",
        description="ALL flags enabled — M1 unsafe, dev workstation only",
        flags=frozenset(FLAG_REGISTRY.keys()),
        m1_safe=False,
        requires_advanced_host=True,
    ),
}

def apply_preset(preset_name: str) -> None:
    """Set all flags in the preset to '1' in os.environ. Idempotent."""
    preset = PRESETS.get(preset_name)
    if preset is None:
        raise ValueError(f"Unknown preset: {preset_name!r}. "
                         f"Available: {sorted(PRESETS.keys())}")
    for flag in preset.flags:
        os.environ[flag] = "1"
    if not preset.m1_safe:
        import warnings
        warnings.warn(
            f"Preset {preset_name!r} is NOT M1 8GB safe. "
            f"Expect OOM kills. Use a workstation with ≥16GB RAM.",
            ResourceWarning,
            stacklevel=2,
        )
```

### 5.3 CLI surface

Navrhuji přidat `--preset` flag do `__main__.py`:

```python
# core/__main__.py (návrh)
parser.add_argument(
    "--preset",
    choices=list(PRESETS.keys()),
    help="Activate a flag preset. Overrides individual HLEDAC_ENABLE_* env vars.",
)
parser.add_argument(
    "--list-presets",
    action="store_true",
    help="List available presets and exit.",
)
```

**Použití:**
```bash
# Místo ručního nastavování 10+ env vars
python -m hledac.universal --preset research --sprint "Find..."

# Inspekce presetu
python -m hledac.universal --list-presets
# minimal   CI/test profile, no network, no LLM           (M1 safe)
# osint     Default operator: public OSINT APIs           (M1 safe)
# recon     Darksurface + stealth, no LLM                 (M1 safe)
# research  LLM + graph + research APIs                   (M1 safe)
# full      ALL flags enabled — M1 unsafe                 (dev workstation)
```

### 5.4 `.env` shorthand

Pro pohodlí v development módu přidat do `.env.example`:

```bash
# ─── Preset (alternative to setting individual flags) ───
# Uncomment ONE of:
#HLEDAC_PRESET=minimal
#HLEDAC_PRESET=osint
#HLEDAC_PRESET=recon
#HLEDAC_PRESET=research
#HLEDAC_PRESET=full
```

### 5.5 Composer (kombinace presetů)

Pro pokročilé uživatele umožnit sloučení:

```python
def compose(*preset_names: str) -> frozenset[str]:
    """Merge multiple presets into a single flag set.
    
    Validates: requires/conflicts across composed presets.
    """
    flags: set[str] = set()
    for name in preset_names:
        flags |= PRESETS[name].flags
    # Run validation on the merged set
    errors, warnings = validate_flag_combo(flags)
    if errors:
        raise FlagValidationError(errors, warnings)
    return frozenset(flags)

# Usage:
#   compose("osint", "recon")  → OSINT APIs + darksurface
#   compose("research", "recon")  → LLM + darksurface (⚠️ warning)
```

---

## 6. Implementation Roadmap

### Phase 1: Inventarizace & cleanup (1 sprint, ~2 dny)

1. Přidat 7 chybějících flagů do `CLAUDE.md` (CB_PERSISTENCE, EVIDENCE_ANALYZER, FEDERATED_HYBRID, FEDERATED_P2P, GRAPH_PATHS, MOBILECLIP, PQ_EXPORT, RL)
2. Smazat 3 mrtvé reference (HLEDAC_ENABLE_ADVANCED_, HLEDAC_ENABLE_DSPY_, HLEDAC_ENABLE_X)
3. Ověřit existenci HLEDAC_ENABLE_ADVANCED_RAG, ADVANCED_STEALTH, STRUCTURED — buď implementovat, nebo smazat
4. Aktualizovat `.flags_baseline.json` (audit_flags.py auto-update)

**Acceptance:** `python tools/audit_flags.py` prochází, `CLAUDE.md` flag list matches `grep -EoH` count.

### Phase 2: Declarative registry (1 sprint, ~3 dny)

1. Rozšířit `utils/feature_flags.py` o `FlagSpec` dataclass + `FLAG_REGISTRY`
2. Přidat `is_flag_enabled(name)` jako canonical resolver
3. Postupně migrovat ad-hoc `os.environ.get(...)` site (60+) na `is_flag_enabled(...)`
4. Zachovat zpětnou kompatibilitu (oba patterny fungují)

**Acceptance:** `python -c "from utils.feature_flags import FLAG_REGISTRY; print(len(FLAG_REGISTRY))"` → 58+

### Phase 3: Validation & presets (1 sprint, ~3 dny)

1. Implementovat `validate_flag_combo()` s requires/conflicts/budget rules
2. Wire-up v `__main__.py::main()` Phase 0 (před `run_sprint()`)
3. Implementovat `FlagPreset` + 5 presetů
4. Přidat `--preset` + `--list-presets` CLI flagy
5. Test surface: 12+ testů v `tests/test_flag_validation.py`

**Acceptance:**
- `python -m hledac.universal --preset research --sprint "..."` funguje
- `python -m hledac.universal --preset full` warní M1 unsafe
- Spuštění s konfliktními flagy exit 2 s konkrétní chybovou hláškou

### Phase 4: Migration & enforcement (ongoing)

1. `tools/audit_flags.py` rozšířit o kontrolu requires/conflicts
2. Přidat `tools/flag_presets_smoke.py` — smoke test všech 5 presetů
3. CI gate: `pytest tests/test_flag_validation.py -q` v `pytest tests/ -x`

---

## 7. Open Questions

1. **Backwards compat pro `SYNTHESIS` alias?** Aktuálně `HLEDAC_ENABLE_SYNTHESIS` je v CLAUDE.md jako deprecated → `HERMES_SYNTHESIS`. Plánujeme plnou deprecation v Sprint F-FLAG-3, ale do té doby musí `is_flag_enabled("HLEDAC_ENABLE_SYNTHESIS")` fungovat a varovat.

2. **M1 budget hard cap — co když preset přesáhne?** Chování: **warn + proceed** (uživatel výslovně zvolil preset) vs **fail-fast** (M1 8GB je hardware limit). Doporučuji: pro `full` preset **warn + proceed**, pro individuální kombinaci **fail-fast** pokud > 6.25GB.

3. **Jak řešit `HLEDAC_ENABLE_RL` vs `HLEDAC_DISABLE_RL`?** V `.env.example` jsou oba — `ENABLE_RL_FEEDBACK=false` a `HLEDAC_DISABLE_RL=` (opt-out). **Doporučení:** sjednotit na `HLEDAC_ENABLE_RL` (opt-in), ponechat `HLEDAC_DISABLE_RL` jako legacy alias s deprecation warning.

4. **Federated presets?** RECON zahrnuje DHT/Fediverse/Gopher, ale **ne** federated (peer-to-peer). Plánujeme samostatný preset `federated` (P2P+bridge+IPFS+DHT)?

5. **Validation hook v `tools/flag_smoke_runner.py`?** Aktuálně flag_smoke_runner testuje jen "is flag set + module import". Rozšířit o "is flag combo valid"?

---

## 8. Souhrn

| Oblast | Doporučení | Effort | Dopad |
|--------|-----------|--------|-------|
| **Taxonomie** | 5-skupinová struktura v CLAUDE.md + `FlagSpec` registry | M | Velký (discovery, IDE nav) |
| **Dependencies** | 10 implication + 6 conflict + 6 resource + 8 budget rules | M | Velký (prevence OOM/crash) |
| **Validace** | `validate_flag_combo()` + Phase 0 hook v `__main__.py` | S | HIGH (fail-fast) |
| **Presets** | 5 presetů (minimal/osint/recon/research/full) + `--preset` CLI | M | Velký (UX) |

**Doporučená implementační sekvence:** Phase 1 (inventarizace) → Phase 3 (validation) → Phase 2 (registry migration) → Phase 4 (enforcement). Phase 3 první, protože okamžitě chrání před OOM a conflict, i když registry ještě není hotový.

**Acceptance kritéria pro celý sprint F-FLAG:**
- Všech 58 flagů v `FLAG_REGISTRY`
- `--list-presets` vypíše 5 presetů
- Spuštění s `HLEDAC_ENABLE_HEAVY_BROWSER=1 HLEDAC_ENABLE_NODRIVER=1` → exit 2 s chybovou hláškou
- Spuštění s `--preset full` → warn o M1, ale pokračuje
- `python tools/audit_flags.py` + `python tools/flag_smoke_runner.py` procházejí
- 12+ testů v `tests/test_flag_validation.py`

---

*Last updated: 2026-06-06 — analysis only, pending user approval before implementation.*
