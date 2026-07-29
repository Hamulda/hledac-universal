# ISSUE #38: WebSocket/UDS Layer Protocol - Layers Reorganization (Částečně implementováno)

### Provedené změny

#### 1. Demo funkce přesunuty do `layers/examples/`

**Demo funkce extrahovány z produkčního kódu:**
- `demo_connected_coordination()` - přesunuta z `hive_coordination.py:383`
- `demo_smart_spawned_integration()` - přesunuta z `smart_coordination.py:281`

**Nová struktura:**
```
layers/examples/
├── __init__.py      # Public API exports
└── demos.py         # Demo implementations
```

**Soubory aktualizovány:**
- `layers/__init__.py` - přidány exporty `demo_connected_coordination`, `demo_smart_spawned_integration`, `run_all_demos`
- `layers/hive_coordination.py` - odstraněn `if __name__ == '__main__':` block
- `layers/smart_coordination.py` - odstraněn `if __name__ == '__main__':` block

---

## Návrh dlouhodobé reorganizace

### Cílová struktura

```
layers/
├── security/              # Privacy, Ghost, Security
│   ├── __init__.py
│   ├── security_layer.py
│   ├── ghost_layer.py
│   └── privacy_layer.py
│
├── signals/              # Temporal, Memory
│   ├── __init__.py
│   ├── temporal_signal_layer.py
│   ├── temporal_signal_runtime.py
│   ├── temporal_signal_store.py
│   └── memory_layer.py
│
├── coordination/          # Hive, Smart, Communication
│   ├── __init__.py
│   ├── communication_layer.py
│   ├── hive_coordination.py
│   └── smart_coordination.py
│
├── content/              # Content cleaning
│   ├── __init__.py
│   └── content_layer.py
│
├── research/             # Deep research
│   ├── __init__.py
│   └── research_layer.py
│
├── stealth/              # Stealth + strategies + UA
│   ├── __init__.py
│   ├── stealth_layer.py
│   ├── stealth_strategies.py
│   └── ua_rotator.py
│
├── protocol/             # Layer protocol + manager
│   ├── __init__.py
│   ├── layer_protocol.py
│   └── layer_manager.py
│
└── examples/            # Demo functions (IMPLEMENTOVÁNO)
    ├── __init__.py
    └── demos.py
```

### Analýza současného stavu

| Modul | Status | Velikost | Poznámka |
|-------|--------|----------|-----------|
| `hive_coordination.py` | **DEPRECATED** | 26KB | Integrováno do `coordination_layer.py`, obsahuje demo |
| `smart_coordination.py` | **DEPRECATED** | 20KB | Integrováno do `coordination_layer.py`, obsahuje demo |
| `layer_manager.py` | **DEPRECATED** | 29KB | Dormant, používán pouze legacy kódem |
| `layer_protocol.py` | AKTIVNÍ | 12KB | Obsahuje UDS server (`create_uds_server`, `uds_fetch`) |
| `communication_layer.py` | AKTIVNÍ | 34KB | Agent messaging, model bridge |
| `content_layer.py` | AKTIVNÍ | 20KB | HTML cleaning, Markdown |
| `ghost_layer.py` | AKTIVNÍ | 27KB | GhostDirector integration |
| `memory_layer.py` | AKTIVNÍ | 54KB | M1 memory management, _StealthMemoryManager, _ThermalSampler |
| `privacy_layer.py` | AKTIVNÍ | 17KB | VPN/Tor, PGP |
| `research_layer.py` | AKTIVNÍ | 13KB | Deep research |
| `security_layer.py` | AKTIVNÍ | 37KB | Cryptography, obfuscation, MissionAudit |
| `stealth_layer.py` | AKTIVNÍ | 88KB | Stealth browsing, CAPTCHA |
| `stealth_strategies.py` | PODPŮRNÝ | 23KB | Strategy protocol + implementations |
| `temporal_signal_layer.py` | AKTIVNÍ | 21KB | Temporal intelligence |
| `temporal_signal_runtime.py` | PODPŮRNÝ | 11KB | Runtime utilities |
| `temporal_signal_store.py` | PODPŮRNÝ | 5KB | DuckDB store |
| `ua_rotator.py` | PODPŮRNÝ | 16KB | User-Agent rotation |

### Deprecation status (dokumentované v kódu)

1. **hive_coordination.py** - `DEPRECATED: This module is now integrated into coordination_layer.py`
2. **smart_coordination.py** - `DEPRECATED: This module is now integrated into coordination_layer.py`
3. **layer_manager.py** - `deprecated: This class is DEPRECATED and DORMANT`

### Problémy k řešení

1. ~~**BUG v hive_coordination.py:97** - `sqlite3.OperationalError: duplicate column name: new_topology`~~
   - ~~V konstruktoru `ConnectedCoordinationSystem.__init__()` se vytváří tabulka s duplicitním sloupcem `new_topology`~~
   - ~~Tento bug se nikdy neprojevil, protože kód byl v `if __name__ == '__main__':` bloku~~
   - ✅ **OPRAVENO** (2026-07-15): Odstraněn duplicitní sloupec, opravena struktura tabulky

2. **BUG v hive_coordination.py** - `sqlite3.ProgrammingError: Cannot operate on a closed database`
   - `with closing(self.memory_db)` uzavřel connection po init bloku
   - ✅ **OPRAVENO** (2026-07-15): Odstraněn `with closing()` wrapper, connection zůstává otevřená

3. **BUG v hive_coordination.py** - `sqlite3.OperationalError: table topology_history has no column named old_topology`
   - Tabulka `topology_history` neměla sloupec `old_topology` ale INSERT ho používal
   - ✅ **OPRAVENO** (2026-07-15): Přidán sloupec `old_topology` do CREATE TABLE

4. **Duplicitní AdvancedCaptchaSolver** - existuje v `stealth_layer.py` i `security/captcha_solver.py`
   - Záměrně dvě paralelní implementace (F360 CAPTCHA SOLVER OVERLAP NOTE)
   - `AdvancedCaptchaSolver` = stealth vrstva (lazy, HLEDAC_ENABLE_STEALTH_LAYER=1)
   - `VisionCaptchaSolver` = security vrstva (primary při HLEDAC_ENABLE_CAPTCHA_LOCAL=1)

3. **Vnitřní třídy v memory_layer.py** - `_StealthMemoryManager`, `_ThermalSampler` nejsou dokumentovány jako veřejné API
4. **Koordinační moduly** - hive a smart coordination jsou deprecated, ale stále v aktivním kódu

### Doporučené kroky

1. **Okamžité (bezpečné):**
   - [x] Demo funkce přesunuty do `layers/examples/` (PROVEDENO)
   - [ ] Přidat deprecation warnings do `hive_coordination` a `smart_coordination`

2. **Krátkodobé (2-4 sprinty):**
   - [ ] Sloučit `temporal_signal_runtime.py` do `temporal_signal_layer.py`
   - [ ] Extrahovat `AdvancedCaptchaSolver` z `stealth_layer.py` do samostatného modulu
   - [ ] Zdokumentovat vnitřní třídy v `memory_layer.py`

3. **Dlouhodobé (architektura):**
   - [ ] Přesunout soubory do podsložek podle funkce
   - [ ] Aktualizovat všechny import cesty v projektu
   - [ ] Odstranit deprecated moduly po přechodném období

### Poznámky k M1 8GB

Při reorganizaci je třeba zachovat:
- Lazy importy pro MLX, Metal, atd.
- Asyncio-native kód bez `time.sleep()`
- Fail-safe pattern pro všechny operace
