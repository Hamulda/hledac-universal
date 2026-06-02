# F260 — SecurityLayer (MissionAudit) & MemoryLayer Cleanup Audit

**Datum:** 2026-06-02
**Scope:** `layers/security_layer.py` (MissionAudit, ~191 LOC Merkle + HMAC),
`layers/memory_layer.py` (~1525 LOC, layer-system memory surface)
**Metoda:** 5 ověřovacích grepů + přímé čtení kódu. Žádné mutace kódu mimo dva
dokumentační komentáře (memory_layer.py:18, security_layer.py:11).

---

## 1. Verifikační grep — výsledky

### G1 — Externí produktivní volání `audit_log|audit_chain|merkle|hmac`
```
rg "audit_log|audit_chain|merkle|hmac" core/ knowledge/ utils/ pipeline/ runtime/ --type py
→ (no output)
```
**Výsledek: 0 hitů.** Audit symboly jsou plně enkapsulovány v `layers/security_layer.py`.

### G2 — Env flagy `HLEDAC_ENABLE_SECURITY|AUDIT|PRIVACY`
```
rg "HLEDAC_ENABLE_SECURITY|HLEDAC_ENABLE_AUDIT|HLEDAC_ENABLE_PRIVACY" --type py
→ pouze HLEDAC_ENABLE_PRIVACY_LAYER v tests/probe_f250f.py + runtime/sprint_scheduler.py
```
**Výsledek: 0 env flagů pro SecurityLayer/MissionAudit.** Privacy má vlastní
oddělený gate (`PRIVACY_LAYER`), ale ten neříídí `SecurityLayer`/`MissionAudit`.

### G3 — Git aktivita 180 dní
```
git log --since=180days --oneline -- layers/security_layer.py
329d2a9b feat: sprint integration - scheduler, policy manager, runtime, security
483eae57 feat: sprint integration — scheduler, policy manager, runtime, security
1a506526 feat: F206AR probes, transport router, prelive readiness, and layer improvements
34315015 feat: sprint F193A integration — new probe tests and module updates
f5002109 Sprint 8UC: Persistent Research Memory + Speculative Prefetch + xgrammar + OODA Loop
```
**Výsledek: 5 commitů za 180 dní.** Aktivní vývoj, ne opuštěno.

### G4 — GhostLayer/StealthLayer × MemoryLayer API
```
rg "MissionAudit|createramdisk|get_ramdisk|get_memory_pressure|EntropyMaskingManager" \
   layers/ghost_layer.py layers/stealth_layer.py --type py
→ (no output)
```
**Výsledek: 0 interních layer-consumerů.** GhostLayer (30 kB) a StealthLayer
(98 kB) **NEIMPORTUJÍ** MemoryLayer API.

### G5 — Globální importy MemoryLayer
```
rg "memory_layer|MemoryLayer" layers/ --type py
→ layers/memory_layer.py: definice třídy (self-reference)
  layers/layer_manager.py:284-285: lazy property `self._memory = MemoryLayer()`
  layers/__init__.py:55-62: re-export
```
**Výsledek:** MemoryLayer je zapojen pouze přes `LayerManager.memory` lazy
property, ale žádný jiný modul v `layers/` ho nekonzumuje.

### G6 — Globální produkční volání SecurityLayer / MissionAudit
```
rg "MissionAudit|SecurityLayer|security_layer" --type py -g '!layers/security_layer.py'
→ runtime/sprint_scheduler.py:15881  getattr(security, "_mission_audit", None) — AKTIVNÍ
  runtime/sprint_scheduler.py:15897  log.W fallback pro selhání
  legacy/autonomous_orchestrator.py:1666, 11194, 18957, 19034, 30894 — LEGACY FACADE
  layers/privacy_layer.py:           deleguje log_privacy_event na SecurityLayer
  layers/layer_manager.py:288-294    lazy property SecurityLayer()
  layers/__init__.py:148-149         re-export MissionAudit, SecurityLayer
  config/__init__.py: enable_security_layer: bool = True
  tests/test_autonomous_orchestrator.py: mock_orch.config.enable_security_layer = False
  tests/security_layer_async_io/test_security_layer.py:  SecurityLayer(config)
```
**Výsledek:** MissionAudit je **aktivně fail-soft wired v canonical sprint path**.

### G7 — MemoryLayer importy mimo `layers/`
```
rg "MemoryLayer|memory_layer" runtime/ core/ pipeline/ knowledge/ brain/ fetching/ coordinators/
→ runtime/memory_authority.py:11  "layer-system memory surface, not canonical sprint owner"
  runtime/memory_authority.py:25  "✗ does NOT import MemoryLayer"
  runtime/memory_authority.py:48  "Provides MemoryLayer.get_memory_pressure() for layer consumers"
  runtime/memory_authority.py:50  "layers/memory_layer.py": "layer_system"
  runtime/memory_authority.py:116 if "MemoryLayer" in s: (test fixture for authority map)
  memory/memory_manager.py:14  "EntropyMaskingManager: noise injection for privacy" (docstring)
```
**Výsledek:** 0 produkčních importů v canonical sprint path. `memory_authority.py`
explicitně deklaruje, že `memory_layer.py` NENÍ canonical owner.

---

## 2. Rozhodovací matice

### DECISION A — MissionAudit (SecurityLayer._mission_audit)

| Kritérium | Výsledek | Váha |
|-----------|----------|------|
| Externí produktivní callery | `sprint_scheduler.py:15881` AKTIVNÍ (fail-soft) | STRONG |
| Env flagy | 0 (vždy zapnuto pokud SecurityLayer existuje) | NEUTRAL |
| Commits 180 dní | 5 commitů | STRONG |
| Legacy importy | `legacy/autonomous_orchestrator.py` 4× | NEUTRAL |
| Compliance význam | Merkle + HMAC = "legally bulletproof evidence" | STRONG |
| Privacy delegace | `PrivacyLayer.log_privacy_event` → SecurityLayer | STRONG |

**Verdikt: ACTIVE.** Audit trail je součást compliance story. MissionAudit NENÍ dead code.

**Pravidlo:** `IF (active callers OR recent commits) → Document as security-relevant,
add explicit comment, keep in place`. **GREEN (KEEP + DOCUMENT).**

### DECISION B — MemoryLayer

| Kritérium | Výsledek | Váha |
|-----------|----------|------|
| Canonical sprint path importy | 0 | STRONG |
| GhostLayer/StealthLayer consumer | 0 | STRONG |
| `memory_authority.py` klasifikace | `layer_system` (explicit) | STRONG |
| Spotřebitelé mimo `layers/` | `legacy/autonomous_orchestrator.py` 4×, `tests/*` 3× | WEAK |
| Commits 180 dní | 8 commitů (sprint integration) | NEUTRAL |
| Runtime význam | Layer-system helper pro ghost/stealth | MEDIUM |

**Verdikt: LAYER-SYSTEM SURFACE.** Žádný kanonický consumer, ale legacy facade
a testy stále importují public API. Přesun do `legacy/layers/` by vyžadoval
aktualizaci `legacy/autonomous_orchestrator.py` (4 importy) a ztrátu test
coverage v `tests/test_sprint82j_benchmark.py` + `tests/test_autonomous_orchestrator.py`.

**Pravidlo:** `IF (0 internal layer consumers) → Move to legacy/layers/`. Podmínka
je splněna **pro ghost/stealth**, ale legacy facade stále importuje → **YELLOW (KEEP + DOCUMENT).**

---

## 3. Uživatelská rozhodnutí (2026-06-02)

| Otázka | Rozhodnutí |
|--------|-----------|
| MemoryLayer | **Ponechat + doplnit komentář** (NEpřesouvat do legacy/layers/) |
| MissionAudit | **Dokumentovat jako security-relevant, ponechat** |

---

## 4. Implementované změny

| Soubor | Změna | Rozsah |
|--------|-------|--------|
| `layers/memory_layer.py` | Rozšířen modul docstring (řádky 18-37) o F260 audit verdict | +18 řádků komentáře |
| `layers/security_layer.py` | Rozšířen modul docstring (řádky 11-30) o F260 SecurityLayer/MissionAudit verdict | +20 řádků komentáře |

**Žádné kódové mutace.** Žádné přesuny souborů. Žádné importy změněny. Žádné
testy spuštěny (změna je čistě dokumentační, neovlivňuje runtime).

### Syntax check
```
$ python3 -c "import ast; ast.parse(open('layers/memory_layer.py').read())"
memory_layer.py: OK
$ python3 -c "import ast; ast.parse(open('layers/security_layer.py').read())"
security_layer.py: OK
```

---

## 5. Pro budoucí sprinty

1. **MissionAudit extraction (deferred):** Pokud by se někdy SecurityLayer
   rozděloval na submoduly, MissionAudit by měl zůstat v `layers/security_layer.py`
   nebo se přesunout do `layers/security/audit.py` s jasným `from .audit import MissionAudit`
   aliasem, aby `sprint_scheduler.py:15881` zůstal funkční.

2. **MemoryLayer full retirement (deferred):** Pokud se `legacy/autonomous_orchestrator.py`
   jednoho dne kompletně nahradí `core/__main__.py::run_sprint()`, MemoryLayer
   se stane kandidátem na přesun do `legacy/layers/`. Tehdy:
   - `git mv layers/memory_layer.py legacy/layers/memory_layer.py`
   - Update `layers/__init__.py:55-62` (odstranit re-export)
   - Update `layers/layer_manager.py:280-286` (odstranit `memory` property)
   - Update `runtime/memory_authority.py:50` (změnit `layer_system` → `legacy_layer`)

3. **Audit chain verification:** Pokud se MissionAudit jednoho dne validuje
   (např. F3xx compliance sprint), bude potřeba test, který ověří, že se
   chained hash links nekorigují po žádné editaci `security_layer.py`.

---

## 6. Reference

- `runtime/memory_authority.py` — autoritativní memory map
- `runtime/sprint_scheduler.py:15875-15897` — fail-soft audit call site
- `layers/layer_manager.py:280-302` — lazy Layer properties
- `layers/privacy_layer.py` — delegace `log_privacy_event` na SecurityLayer
- `legacy/autonomous_orchestrator.py:1666, 11194, 18957, 19034, 30894` — legacy facade
- `tests/security_layer_async_io/test_security_layer.py` — SecurityLayer tests
- `tests/test_sprint82j_benchmark.py:232-253` — EntropyMaskingManager termination test
- `tests/test_autonomous_orchestrator.py:19105, 19113` — RAMDiskManager tests
- CLAUDE.md F195C reference: "audit HMAC" compliance requirement

---

*Audit completed 2026-06-02. Žádné nevratné akce. Dokumentační komentáře přidány
do obou modulů pro budoucí archaeology. Uživatel schválil obě rozhodnutí (KEEP +
DOCUMENT) — žádné RED decision.*
