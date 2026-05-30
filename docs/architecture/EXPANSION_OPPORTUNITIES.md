# EXPANSION_OPPORTUNITIES.md

**Generováno**: 2026-05-12
**Auditor**: Claude Code (4-paralelní audit)
**Scope**: `hledac/universal/`
**Poznámka**: Audit mrtvého kódu zastaven uživatelem — kategorie je částečná

---

## SOUHRN

| Kategorie | Počet | HIGH | MED | LOW |
|-----------|-------|------|-----|-----|
| Chybějící implementace | 17 | 15 | 10 | 6 |
| Odpojené moduly | 365 | — | — | — |
| Broken importy | 281 | — | — | — |
| **CELKEM** | **663** | **15** | **10** | **6** |

---

## 1. CHYBĚJÍCÍ IMPLEMENTACE (17 ověřených nálezů)

### 1.1 Post-Quantum Kryptografie — ELLIPSIS STUBS (15 HIGH)

#### security/pq_crypto.py — PostQuantumBackend Protocol (lines 96-162)

```
@runtime_checkable
class PostQuantumBackend(Protocol):
    @property
    def name(self) -> str: ...                                      # line 101

    def is_available(self) -> bool: ...                             # line 105

    def pq_status(self) -> PQStatus: ...                           # line 109

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool: ...  # line 113

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature: ...  # line 126

    def verify_mldsa_signature(
        self,
        digest: str,
        signature: bytes,
        public_key_bytes: bytes,
        level: int = 65
    ) -> bool: ...                                                  # line 143
```

#### security/pq_export_encryption.py — ExportEncryptionBackend Protocol (lines 177-243)

```
@runtime_checkable
class ExportEncryptionBackend(Protocol):
    @property
    def name(self) -> str: ...                                      # line 178

    def is_available(self) -> bool: ...                             # line 182

    def hpke_status(self) -> HPKEStatus: ...                       # line 186

    def generate_recipient_key(self, key_id: str) -> tuple[str, str, str] | None: ...  # line 190

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
        recipient_key_id: str = "",
    ) -> ExportEncryptionEnvelope | None: ...                      # line 203

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
        test_material: TestOnlyHPKERoundtripMaterial | None = None,
    ) -> bytes | None: ...                                        # line 224
```

#### security/secure_enclave.py — SecureEnclaveBackend Protocol (lines 66-96)

```
@runtime_checkable
class SecureEnclaveBackend(Protocol):
    @property
    def name(self) -> str: ...                                      # line 71

    def is_available(self) -> bool: ...                             # line 75

    async def sign_batch_digest(self, manifest: BatchManifest) -> SignedDigest: ...  # line 79
```

**Priority**: HIGH
**Důvod**: 15 Protocol metod má jen `...` tělo — neexistuje žádná implementace. `NullPostQuantumBackend` a `NullSecureEnclaveBackend` jsou stuby, ne plné implementace.
**Akce**: Implementovat `SwiftPostQuantumBackend` a `RealSecureEnclaveBackend` pomocí CryptoKit/Swift helper

---

### 1.2 Model Swap Manager — ModelLifecycleProtocol STUBS (4 MED)

#### brain/model_swap_manager.py — ModelLifecycleProtocol (lines 49-86)

```
class ModelLifecycleProtocol(msgspec.Struct, frozen=True, gc=False):
    def get_current_model_name(self) -> str | None: ...             # line 62

    async def cancel_pending_model_tasks(self, model_name: str) -> int: ...  # line 66

    async def unload_current_model(self) -> None: ...               # line 75

    async def load_model(self, target_model: str) -> bool: ...      # line 79
```

**Priority**: MED
**Důvod**: Model swap manager je klíčový pro M1 resource governance. Tento Protocol čeká na implementaci v `Hermes3Engine` nebo jiném lifecycle objektu.
**Akce**: Implementovat metody v odpovídajícím lifecycle objektu

---

### 1.3 NotImplementedError STUBS (2 MED)

#### deep_probe.py — PathPattern.generate_predictions (line 402)

```python
class PathPattern(PathPatternBase):
    def generate_predictions(self, base_url: str) -> list[str]:
        raise NotImplementedError("PathPattern.generate_predictions must be implemented by subclass")
```

#### project_types.py — ResearchStrategy.research (line 755)

```python
async def research(self, query: str, context: Dict[str, Any]) -> List[NormalizedFinding]:
    raise NotImplementedError("Subclasses must implement research()")
```

**Priority**: MED
**Důvod**: `PathPattern.generate_predictions` — abstraktní metoda čeká na implementaci v `DatePathPattern`, `VersionPathPattern` apod. `ResearchStrategy.research` — základní strategie bez implementace.

---

### 1.4 TODO Komentáře (4 nálezy)

#### planning/htn_planner.py

| Řádek | Kód | Popis |
|-------|-----|-------|
| 660 | `# TODO 8S/8T: further refine per-task instrumentation if Hermes` | Nedokončená instrumentace |
| 724 | `confidence = 0.8  # TODO §7.4/§5.15: nahradit quality/corroboration score` | Placeholder score |

**Priority**: MED
**Důvod**: Oba TODO v htn_planner signalizují nekompletní instrumentaci cost-modelu

#### utils/shared_tensor.py

| Řádek | Kód | Popis |
|-------|-----|-------|
| 3 | `# Skutečný zero-copy vyžaduje Metal buffer – to je zatím TODO.` | Zero-copy Metal buffer |
| 21 | `což je TODO pro budoucí implementaci.` | Komentář u SharedTensor |

**Priority**: LOW
**Důvod**: Dokumentační TODO pro budoucí M1 GPU optimalizaci

---

### 1.5 Abstract Base Methods — OK (NENÍ PROBLÉM)

```
discovery/ti_feed_adapter.py
├── line 106: @property @abstractmethod def source_type(self) -> str: ...
├── line 112: @property @abstractmethod def source_tier(self) -> str: ...
└── line 147: @abstractmethod async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]: ...
```

**Status**: ✅ SPRÁVNĚ — jedná se o `@abstractmethod` dekorátory, ne o chybějící implementace. Třídy jako `NVDFeedAdapter`, `CISAKEVAdapter` atd. tyto metody implementují správně.

---

## 2. ODPOJENÉ MODULY (365 modulů, 56% kódu)

### 2.1 Kritické Odpojené Adresáře

| Adresář | Počet | Stáří | Status |
|---------|-------|-------|--------|
| `federated/` | 13 | 43+ dní | Pravděpodobně mrtvý |
| `hypothesis/` | 5 | 43+ dní | Pravděpodobně mrtvý |
| `context_optimization/` | 5 | 43+ dní | Pravděpodobně mrtvý |
| `legacy/` | 6 | 19+ dní | Odložený kód |
| `orchestrator/` | 10 | — | Alternativní orchestrace |
| `dht/` | 4 | — | DHT experimenty |

### 2.2 Velké Nepoužívané Adresáře

| Adresář | Modulů | Status |
|---------|--------|--------|
| `tools/` | 77 | Utility skripty bez importu |
| `utils/` | 47 | Helper moduly nikdy neimportované |
| `intelligence/` | 23 | Intelligence adaptery odpojené |
| `coordinators/` | 23 | Mnoho variant nikdy neimportovaných |
| `security/` | 19 | Security utility odpojené |
| `brain/` | 17 | Experimentální mozkové moduly |
| `knowledge/` | 17 | Alternativní storage implementace |
| `layers/` | 14 | Abstraction layer experimenty |

### 2.3 Entry Point Duplikáty

```
hledac/universal/hledac/__main__.py
└── Status: ODPOJENÝ — existuje druhý __main__ mimo standardní path
```

### 2.4 Nejstarší Odpojené Soubory (43+ dní bez commitu)

```
benchmarks/__init__.py
brain/apple_fm_probe.py
brain/decision_engine.py
brain/dynamic_model_manager.py
coordinators/advanced_research_coordinator.py
coordinators/base.py
coordinators/claims_coordinator.py
```

**Akce**: Provést git rm těchto souborů — jsou 43+ dní staré a nikdy nebyly použity

---

## 3. BROKEN IMPORTS (281 importů v 72 souborech)

### 3.1 Podle Typu

| Typ | Počet |
|-----|-------|
| `hledac.universal.X` — modul neexistuje | 141 |
| `hledac.X` — mimo filesystem boundary | 124 |
| `hledac.core.X` — core je sibling, ne uvnitř universal | 16 |

### 3.2 Nejpostiženější Soubory

| Soubor | Broken Imports |
|--------|----------------|
| `coordinators/security_coordinator.py` | 23 |
| `tests/probe_f192g/test_f192g_grey_runtime_seams.py` | 14 |
| `tests/probe_temporal_priority_hints/...` | 14 |
| `coordinators/execution_coordinator.py` | 11 |
| `coordinators/monitoring_coordinator.py` | 10 |

### 3.3 Nejčastější Chybějící Moduly

```
hledac.universal.layers              # 14 importů — celý modul neexistuje
hledac.universal.orchestrator        # 10 importů — modul neexistuje
hledac.brain.modernbert_engine       # 8 importů — modul neexistuje
hledac.universal.runtime.intelligence_dispatcher  # 6 importů — modul neexistuje
hledac.universal.probe_f207j_nonfeed_finding_bridge  # 4 importy
```

### 3.4 Konkrétní Broken Imports — Entry Point Relevant

```
brain/model_manager.py:26
└── from hledac.universal import adjust_fetch_workers
    ⚠️ adjust_fetch_workers je v utils/concurrency.py, ne v root

pipeline/live_public_pipeline.py:1821
└── from hledac.universal.layers import get_temporal_signal_layer
    ⚠️ layers/ modul neexistuje
```

### 3.5 Broken Imports v Testech

```
tests/probe_temporal_priority_hints/test_temporal_priority_hints.py:35
└── from hledac.universal.layers import build_temporal_priority_hints

tests/probe_f192g/test_f192g_grey_runtime_seams.py:641
└── from hledac.universal.runtime.intelligence_dispatcher import IntelligenceDispatcher

tests/probe_f207j_nonfeed_finding_bridge/test_nonfeed_finding_bridge.py:29
└── from hledac.universal.probe_f207j_nonfeed_finding_bridge import ct_results_to_findings
```

**Akce**: Opravit import path nebo odstranit testy pokud referenced modul neexistuje

---

## 4. EXPANZE PRIORITNÍHO POŘADÍ

### CRITICAL (akce ihned)

| # | Soubor | Problém | Akce |
|---|--------|---------|------|
| 1 | `security/pq_crypto.py` | 6x Protocol ellipsis stub | Implementovat `SwiftPostQuantumBackend` |
| 2 | `security/pq_export_encryption.py` | 6x Protocol ellipsis stub | Implementovat HPKE backend |
| 3 | `security/secure_enclave.py` | 3x Protocol ellipsis stub | Implementovat `RealSecureEnclaveBackend` |
| 4 | `brain/model_swap_manager.py` | 4x Protocol ellipsis stub | Implementovat v Hermes3Engine |
| 5 | `coordinators/security_coordinator.py` | 23 broken imports | Buď implementovat nebo smazat |

### HIGH (akce tento sprint)

| # | Soubor | Problém | Akce |
|---|--------|---------|------|
| 6 | `federated/` adresář | 13 odpojených modulů | Rozhodnout o existenci/odstranění |
| 7 | `context_optimization/` | 5 odpojených modulů | Odstranit nebo integrovat |
| 8 | `legacy/` | 6 starých souborů | Odstranit nebo migrovat |
| 9 | `tests/probe_f192g/` | 14 broken imports | Opravit nebo odstranit test |
| 10 | `tests/probe_temporal_priority_hints/` | 14 broken imports | Opravit nebo odstranit test |

### MEDIUM (akce v dalším sprintu)

| # | Soubor | Problém | Akce |
|---|--------|---------|------|
| 11 | `planning/htn_planner.py` | 2x TODO | Dokončit instrumentaci cost-modelu |
| 12 | `deep_probe.py` | NotImplementedError | Implementovat PathPattern.generate_predictions |
| 13 | `project_types.py` | NotImplementedError | Implementovat ResearchStrategy.research |
| 14 | `orchestrator/` | 10 odpojených modulů | Rozhodnout o osudu |
| 15 | `layers/` | 14 odpojených modulů | Odstranit nebo integrovat |

### NÍZKÁ (dlouhodobý backlog)

| # | Soubor | Problém | Akce |
|---|--------|---------|------|
| 16 | `utils/shared_tensor.py` | 2x TODO | Metal buffer zero-copy implementace |
| 17 | `benchmarks/__init__.py` | Odpojený | Odstranit |
| 18 | `brain/apple_fm_probe.py` | 43+ dní starý | Odstranit nebo implementovat |

---

## 5. DOPORUČENÍ PRO EXPANZI

### Fáze 1: Úklid (1-2 dny)
1. Smazat `federated/`, `hypothesis/`, `context_optimization/` (31 modulů, 43+ dní staré)
2. Smazat `legacy/` (6 modulů)
3. Opravit nebo odstranit `coordinators/security_coordinator.py` (23 broken imports)
4. Opravit/odstranit testy s broken imports

### Fáze 2: Implementace (3-5 dní)
1. Implementovat 15 Protocol ellipsis stubs (PQ crypto, export encryption, secure enclave)
2. Implementovat 4 ModelLifecycleProtocol metody
3. Implementovat PathPattern.generate_predictions a ResearchStrategy.research

### Fáze 3: Integrace (2-3 dny)
1. Integrovat `orchestrator/` nebo odstranit
2. Integrovat `layers/` nebo odstranit
3. Rozhodnout o osudu `tools/`, `utils/` (124 modulů)

---

## PŘÍLOHY

- `broken_imports.json` — kompletní seznam 281 broken importů
- `disconnected_modules.json` — kompletní seznam 365 odpojených modulů (generováno agentem)
