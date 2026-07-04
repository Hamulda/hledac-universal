# Kompatibilita — `shims/`, `_shims/`, `stubs/`

## Přehled

Projekt obsahuje tři adresáře pro různé účely kompatibility. Nejedná se o duplikáty — každý plní jinou roli.

---

## `shims/` — External Library Compatibility (OPTIONAL)

**Účel:** Wrappers pro **volitelné** knihovny třetích stran, které nejsou v core závislostech.

**Struktura:**
```
shims/
└── security/
    └── zkp_research_engine.py   # ZKP stub (real ZKP vyžaduje libsnark/circom)
```

**Charakteristika:**
- Public API pro volitelné featury (`HLEDAC_ENABLE_ZKP=1` gating)
- Obsahuje stub implementace, které **nejsou** plnohodnotné
- Importuje se přímo do security modulu
- Kód je **nefunkční placeholder** — varuje při aktivaci featury

**Příklad:** `zkp_research_engine.py` — ZKP vyžaduje libsnark/circom WASM trusted setup, takže je stub.

---

## `_shims/` — Internal Phase-Out Bridging (CIRCULAR IMPORT WORKAROUND)

**Účel:** Privátní shimy pro **interní** moduly, které mají cirkulární import problémy v `hledac/` namespace řetězci.

**Struktura:**
```
_shims/
├── __init__.py                 # Re-exports z modulu
├── core_resilience.py          # → core/resilience.py
├── core_watchdog.py            # → core/watchdog.py
├── core_http.py                # → core/http_client.py
├── core_mlx_embeddings.py      # → core/mlx_embeddings.py (F265-5.2)
├── core_unified_ai_orchestrator.py
├── cortex_director.py
└── security_*.py               # → security/*.py
```

**Charakteristika:**
- Privátní prefix (`_shims`) — **nepoužívat v novém kódu**
- Obchází cirkulární importy v `hledac.core.*` namespace
- Obsahuje **plnohodnotný kód** — re-exportuje z originálního modulu
- F265-5.2: `core_mlx_embeddings.py` proxuje přes `sys.modules` check pro různé import path varianty
- Aktivně používáno v `coordinators/`, `context_optimization/`, `intelligence/`

**Proč existuje:**
```
hledac/__init__.py
  └─> hledac.core.__init__.py
        └─> (circular) hledac/__init__.py

_shims/ bypasses: hledac.universal._shims -> universal/core/*.py
```

---

## `stubs/` — Type Stub Definitions (.pyi)

**Účel:** Type stub definice pro **statickou analýzu** — `pyright`, `mypy`, `ty`.

**Struktura:**
```
stubs/
├── CoreML/                     # Apple CoreML framework
│   ├── __init__.pyi
│   ├── _internal.pyi
│   └── _types.pyi
├── curl_cffi/                  # curl_cffi HTTP library
│   ├── __init__.pyi
│   ├── aiohttp.pyi
│   └── requests.pyi
├── Foundation/                  # Apple Foundation framework
├── hledac_rust_extensions/      # PyO3 Rust extension (31k+ lines)
│   └── __init__.pyi            # Full API surface mirroring rust_extensions/
├── mlx_vlm/                     # MLX VLM bindings
│   ├── __init__.pyi
│   └── generate/
├── NaturalLanguage/             # Apple NLP framework
├── nodriver.pyi                 # WebDriver-free Chrome
├── ocrmac/                      # macOS OCR
├── oqs/                         # Open Quantum Safe
├── Vision/                      # Apple Vision framework
├── wasmtime/                    # WASM runtime
└── zstd/                        # Zstandard compression
```

**Charakteristika:**
- Čistě **type hints** — `.pyi` soubory nejsou za runtime importovány
- Musí se shodovat s aktuální verzí knihovny
- Pro PyO3 extension: synchronizace s `rust_extensions/src/lib.rs`
- Rule of thumb: nový `#[pyclass]` v Rust → přidat symbol do `stubs/hledac_rust_extensions/__init__.pyi`

---

## Srovnání

| Aspekt | `shims/` | `_shims/` | `stubs/` |
|--------|----------|-----------|----------|
| **Účel** | External optional deps | Internal circular imports | Type checking |
| **Typ** | Stub (placeholder) | Proxy (real code) | `.pyi` definitions |
| **Import za runtime** | Ano | Ano | Ne |
| **Veřejné** | Ano | Ne (`_` prefix) | Ano |
| **Pro koho** | Uživatelé s volitelnými featy | Interní kód | Type checkery |
| **Příklad** | ZKP stub | `core_mlx_embeddings` | `hledac_rust_extensions` |

---

## Pravidla pro nový kód

1. **Nová external závislost** → `shims/` pokud je volitelná/placeholder
2. **Circulární import interního modulu** → `_shims/` (dočasné řešení, refaktorovat preferováno)
3. **Type definitions pro externí lib** → `stubs/<lib_name>/`
4. **Nový PyO3 symbol** → `stubs/hledac_rust_extensions/__init__.pyi`

---

## Maintenance

- `_shims/` — při refaktoru circulárních importů zrušit a nahradit přímým importem
- `stubs/` — aktualizovat při upgradu knihoven (verze musí souhlasit)
- `shims/` — review při aktivaci featury (varování = stub není implementace)
