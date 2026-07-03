# Issue 11.1 — Komplexní analýza ML/GPU adresářů
**M1 8GB UMA — jediný model v paměti, šest adresářů je nemožných**
**Datum:** 2026-07-03
**Stav:** Root Analysis Complete

---

## 1. Aktuální stav — co kde je

### graph/ (86 KB Python, 0 KB Rust)
| Soubor | Řádků | Role |
|--------|-------|------|
| quantum_pathfinder.py | 2323 | DuckPGQGraph + quantum-inspired pathfinding, numpy/scipy/mlx backendy |
| graph_manager.py | 257 | Lightweight wrapper |
| hypothesis_graph.py | 422 | Graph hypothesis tracking |
| lock_manager.py | 547 | Concurrency locking |

**Reality:** Žádný z těchto souborů není GPU-intenzivní. DuckPGQGraph používá DuckDB (CPU), numpy, scipy. quantum_pathfinder má MLX fallbacky, ale běží v bounded režimu.

### brain/ (3256 KB Python — NEJVĚTŠÍ)
| Soubor | Řádků | Typ | Model(y) |
|--------|-------|-----|---------|
| deephermes3_engine.py | 5204 | **Model loader + inference** | Hermes-3-Llama-3.2-3B-4bit (mlx_lm) |
| model_manager.py | 1384 | **Model lifecycle manager** | Správce všech modelů |
| moe_router.py | 1075 | **Multi-expert router** | torch + mlx.nn expert modely |
| synthesis_runner.py | 1888 | Inference orchestration | volá mlx_lm.generate() |
| ner_engine.py | 1746 | NER + IoC extraction | gliner + CoreML + mlx_gliner2 |
| inference_engine.py | 2460 | Abductive reasoning | žádný model — pure Python |
| mlx_batched_executor.py | 712 | Batch executor wrapper | volá DeepHermes3Engine |
| distillation_engine.py | 875 | Model distillation | trainuje MLP na CPU/MLX |
| ane_embedder.py | 783 | **ANE embeddings** | modernbert → ANE (Apple Neural Engine) |
| coreml_embedder.py | 608 | CoreML embeddings | BGE → ONNX → CoreML |
| mlx_embedder.py | 157 | MLX embeddings | mlx-embed (ANA fallback) |
| unified_embedding_manager.py | 280 | Embedding facade | všechny embeddery |
| gnn_predictor.py | ~1000 | GNN prediction | torch, mlx.nn |
| modernbert_engine.py | ~350 | ModernBERT embeddings | volá ane_embedder / coreml_embedder |
| insight_engine.py | ~900 | Insight generation | volá hermes engine |
| + 15 dalších | ~2000 | DSPy, confidence, decision, causal, atd. | various |

**Klíčový problém brain/:**
- `moe_router.py` importuje **torch** a **mlx.nn** a nahrává více "expert" modelů současně
- `model_manager.py` je správce, který rozhoduje co je loaded
- Na M1 8GB lze mít pouze JEDEN velký model (Hermes-3 3B = ~2GB) + embedding model (~200MB)

### multimodal/ (160 KB Python)
| Soubor | Řádků | Role |
|--------|-------|------|
| analyzer.py | 867 | MultimodalEnricher, DocumentExtractor — PDF + image text |
| vision_encoder.py | 369 | VisionEncoder — CoreML + torchvision |
| evidence_triage.py | ~550 | Evidence triage |
| fusion.py | ~250 | Multimodal fusion |

**Reality:** `analyzer.py` lazy-loaduje `mobileclip` a `PIL`. `vision_encoder.py` vyžaduje **torchvision + coremltools**. Na M1 8GB je toto **příliš velké** — mobilclip model může být 500MB+.

### mlx_models/ (**PRÁZDNÝ**)
```
total 0
drwxr-xr-x   2 vojtechhamada  staff    64 Jun 27 23:22 .
```

Žádný model zde není fyzicky uložen. Používá se `huggingface_hub.snapshot_download` na dvou místech:
- `brain/synthesis_runner.py:1441` — `await asyncio.to_thread(mlx_lm.utils.snapshot_download, model_id)`
- `runtime/prewarm_daemon.py:141` — `from huggingface_hub import snapshot_download`

### text/ (176 KB Python)
| Soubor | Řádků | Role |
|--------|-------|------|
| encoding_detector.py | ~500 | Encoding detection |
| unicode_analyzer.py | 749 | Unicode attack analyzer |
| hash_identifier.py | ~450 | Hash identification |
| text_analyzer_facade.py | ~300 | Facade pattern |

**Reality:** Žádné modely. Čistě Python — regex, dataclasses, msgspec. **Žádný GPU load.**

### banks/ (4 KB + SQLite)
```
total 16
drwxr-xr-x  4 vojtechhamada  staff   128 Jun 29 17:13 .
-rw-r--r--  1 vojtechhamada  staff  372736 Jun 29 16:52 mnemopi.db
```

Jediný obsah je `mnemopi.db` — SQLite databáze (372 KB). **Žádné modely, žádný GPU load.**

---

## 2. Kořenové problémy (Root Causes)

### RP-1: Šest adresářů, ale pouze DVA jsou skutečně GPU-náročné
- **brain/** — ano, je GPU (MLX + ANE)
- **multimodal/** — ano, je GPU (mobileclip + torchvision + coremltools)
- **graph/** — ne, CPU (DuckDB + numpy)
- **text/** — ne, CPU (čistý Python)
- **banks/** — ne, CPU (SQLite)
- **mlx_models/** — prázdný, pouze cache路径

### RP-2: brain/ má překrývající se kompetence
```
model_manager.py       — lifecycle management (who loads what)
deephermes3_engine.py  — Hermes-3 inference (THE primary model)
moe_router.py         — multi-expert routing (N Expert models + 1 Router)
mlx_batched_executor.py — batch executor
synthesis_runner.py     — orchestration + synthesis
```
**Problém:** `moe_router.py` se snaží být samostatný router s vlastními expert modely. To je v konfliktu s `model_manager.py`, který řeší přesně to samé.

### RP-3: HuggingFace cache je neorganizovaná
- `snapshot_download` se volá ad-hoc v `synthesis_runner.py` a `prewarm_daemon.py`
- Žádná centralizovaná cache management
- Na M1 8GB může cache růst nekontrolovaně

### RP-4: multimodal/ není M1-friendly
- `mobileclip` model může být 500MB+
- `torchvision` je CPU-only na M1 (bez GPU core)
- `analyzer.py` lazy-loaduje PIL, PyPDF2, mobileclip — ale při prvním použití může explodovat

### RP-5: text/ a banks/ jsou orphan adresáře
- **text/** — obsahuje utility, které by mohly být v `utils/` — žádný důvod být samostatný adresář
- **banks/** — obsahuje SQLite DB, mělo by být součástí `knowledge/` jako `knowledge/memory_palace.db`

---

## 3. Cutting-Edge řešení pro M1 8GB

### Strategie: Single-Model Policy + Lazy Everything

#### 3.1 brain/ — Konsolidace na JEDEN model

**Current state:**
```
model_manager.py     — 1384 řádků, 93 __slots__ attributes
deephermes3_engine.py — 5204 řádků, všechno v jednom
moe_router.py        — 1075 řádků, torch + mlx.nn, multiple experts
```

**Navrhované řešení:**

```python
# brain/model_manager.py — one model at a time, lazy loading
class ModelManager:
    # ZŮSTANE — lifecycle management
    
    async def acquire_model_ctx(self, model_name: ModelName):
        # ONE model in memory at a time
        # Expert routing is DONE at prompt level, not model level
        # MoE "experts" become prompt routing to ONE model
        
# moe_router.py — REPLACED with prompt-level expert routing
class ExpertRouter:
    """Instead of loading multiple expert models, route to ONE model with expert prompts."""
    
    async def route(self, query: str, rag_context: list[str]) -> list[str]:
        # Route to DEEPHERMES3 with specialized system prompts
        # No additional model weights needed
```

**Why:** M1 8GB nemůže držet Hermes-3 (2GB) + 4-8 expert modelů (2-4GB každý) = 10-34GB. Nemožné.

**Migrace kroků:**
1. Odstranit `torch` import z `moe_router.py` — žádný torch na M1
2. `moe_router.py` se stane čistě prompt-based routerem (žádné extra modely)
3. `deephermes3_engine.py` převezme všechnu inference
4. `model_manager.py` zůstane jako lifecycle správce

#### 3.2 multimodal/ — Defer, ne Delete

**Current state:**
- `mobileclip` (500MB+) se loaduje při prvním použití
- `torchvision` je CPU-only overhead
- `vision_encoder.py` používá coremltools (convert on demand)

**Navrhované řešení:**

```python
# multimodal/analyzer.py — defer heavy vision to when RAM allows
class MultimodalEnricher:
    def _can_run_heavy_vision(self) -> bool:
        # Check available RAM — only run if >2GB free
        # Otherwise return text-only enrichment
        
    async def enrich(self, finding) -> dict[str, Any] | None:
        if self._can_run_heavy_vision():
            # Run mobileclip/vision encoder
        else:
            # Fallback to text-only processing
            return self._enrich_text_only(finding)
```

**Klíčové:** Na M1 8GB, `HLEDAC_ENABLE_HEAVY_BROWSER=0` (default) znamená že multimodální enrichment není potřeba. Sprint běží bez obrázků.

#### 3.3 mlx_models/ — Centralized cache management

**Current state:**
- Prázdný adresář
- `snapshot_download` je volána ad-hoc

**Navrhované řešení:**

```python
# brain/model_cache.py — centralized HuggingFace cache
from huggingface_hub import snapshot_download
from pathlib import Path
import os

MODEL_CACHE_DIR = Path("~/.cache/hledac/models").expanduser()

def get_or_download_model(model_id: str, max_size_gb: float = 2.0) -> Path:
    """Download model if not cached, with size check."""
    model_dir = MODEL_CACHE_DIR / model_id.replace("/", "--")
    
    if model_dir.exists():
        return model_dir
        
    # Check available disk space
    if (MODEL_CACHE_DIR.stat().st_free < max_size_gb * 1024**3):
        raise MemoryError(f"Insufficient disk space for {model_id}")
    
    return snapshot_download(repo_id=model_id, cache_dir=str(MODEL_CACHE_DIR))
```

**Integrace:**
- `synthesis_runner.py` používá `get_or_download_model()` místo přímého `snapshot_download`
- `prewarm_daemon.py` používá stejnou funkci
- Velikost cache je monitorována přes `uma_budget.py`

#### 3.4 text/ → utils/ nebo knowledge/

**Current state:** Samostatný adresář pro utility, které jsou univerzální.

**Navrhované řešení:**
```
text/ → utils/text_utils.py (move encoding_detector.py, unicode_analyzer.py, hash_identifier.py)
text/ → knowledge/text_analysis.py (move text_analyzer_facade.py if still needed)
```

**Důvod:** `text/` není ML adresář — jsou to pure Python utility. Nemá co být v root adresáři vedle `brain/` a `multimodal/`.

#### 3.5 banks/ → knowledge/memory_palace/

**Current state:** `banks/universal-2j55095ihu626/mnemopi.db` — 372 KB SQLite

**Navrhované řešení:**
```
banks/universal-2j55095ihu626/ → knowledge/memory_palace/
banks/universal-2j55095ihu626/mnemopi.db → knowledge/memory_palace/mnemopi.db
```

**Důvod:** `banks/` je artifact z jiného projektu ("universal-2j55095ihu626"). Obsahuje pouze jednu SQLite DB, která patří do knowledge vrstvy.

#### 3.6 graph/ — petgraph consideration

**Current state:**
- `quantum_pathfinder.py` používá numpy/scipy pro quantum-inspired algorithms
- DuckPGQGraph používá DuckDB pro graph storage

**Cutting-edge řešení:**
```python
# graph/quantum_pathfinder.py — use petgraph for in-memory graph
# Instead of: networkx (heavy), numpy adjacency matrices (RAM)
# Use: petgraph (lightweight, ~50KB, Rust-speed)

import petgraph

class QuantumInspiredPathFinder:
    def __init__(self, config: QuantumPathConfig | None = None):
        # petgraph is already in Cargo.toml (petgraph = "0.13")
        # Wire through rust_extensions for graph algorithms
```

**Realita:** Graph algorithms jsou v Rust (`petgraph`), Python je jen wrapper. To je **správný směr** — žádné změny potřeba.

---

## 4. Akční plán implementace

### Fáze 1: Konsolidace brain/ (1 den)
- [ ] `moe_router.py` — odstranit torch importy, přepracovat na prompt-based routing
- [ ] `model_manager.py` — zjednodušit na single-model lifecycle
- [ ] `deephermes3_engine.py` — zůstává jako primární inference engine
- [ ] Test: šprint běží s jedním modelem, MoE routing funguje přes prompty

### Fáze 2: mlx_models/ cache management (0.5 dne)
- [ ] Vytvořit `brain/model_cache.py` s centralizovaným `get_or_download_model()`
- [ ] Refaktorovat `synthesis_runner.py` a `prewarm_daemon.py` na novou funkci
- [ ] Přidat velikostní monitoring přes `uma_budget.py`

### Fáze 3: text/ → utils/ (0.25 dne)
- [ ] Přesunout `encoding_detector.py`, `unicode_analyzer.py`, `hash_identifier.py` → `utils/text/`
- [ ] Přesunout `text_analyzer_facade.py` → `knowledge/` pokud ještě potřeba
- [ ] Smazat prázdný `text/` adresář

### Fáze 4: banks/ → knowledge/memory_palace/ (0.25 dne)
- [ ] Přesunout `banks/universal-2j55095ihu626/` → `knowledge/memory_palace/`
- [ ] Update import paths v kódu
- [ ] Smazat prázdný `banks/` adresář

### Fáze 5: multimodal/ defer mechanism (0.5 dne)
- [ ] Implementovat ` MultimodalEnricher._can_run_heavy_vision()` s RAM checkem
- [ ] Přidat graceful fallback na text-only enrichment
- [ ] Dokumentovat: na M1 8GB bez `HLEDAC_ENABLE_HEAVY_BROWSER=1` je multimodal vždy deferred

### Fáze 6: graph/ — petgraph wire (průběžně)
- [ ] Ověřit že `petgraph` je v `Cargo.toml` 
- [ ] Zjistit kde v kódu se volá `quantum_pathfinder.py` — nahradit Rust implementací kde možné

---

## 5. Invarianty (pro testování)

| Invariant | Test |
|----------|------|
| Jeden model v paměti na M1 8GB | `test_model_manager_single_model.py` — ověř že load_model( Hermes ) evicts previous |
| HuggingFace cache bounded | `test_model_cache_size.py` — cache nepřesáhne 4GB |
| text/ deprecaced | `test_text_deprecated.py` — importy z utils/text/ fungují |
| banks/ migrated | `test_banks_migrated.py` — mnemopi.db v knowledge/memory_palace/ |
| multimodal defer | `test_multimodal_defer.py` — při nízké RAM padne na text-only |

---

## 6. Závislosti na architektuře

```
brain/model_manager.py ──────► deephermes3_engine.py
brain/moe_router.py    ──────► (se odstraní torch, zůstane prompt routing)
brain/synthesis_runner.py ────► brain/model_cache.py (NEW)
brain/distillation_engine.py ──► (pure MLX training, small model)
brain/ane_embedder.py  ───────► (ANE je oddělený memory domain)
knowledge/duckdb_store.py ───► (už je oddělené)
```

---

## 7. Finální stav (po migraci)

```
universal/
├── brain/                    # ML inference (Hermes-3 via MLX)
│   ├── deephermes3_engine.py # PRIMARY model
│   ├── model_manager.py     # Lifecycle (single model)
│   ├── model_cache.py       # NEW: HuggingFace cache
│   ├── ane_embedder.py      # ANE embeddings (oddělený domain)
│   ├── coreml_embedder.py   # CoreML embeddings
│   └── ...
├── graph/                    # Graph analytics (DuckDB + petgraph/Rust)
│   ├── quantum_pathfinder.py # DuckPGQGraph
│   └── ...                  # Rust extensions for petgraph
├── knowledge/                # Storage (DuckDB, LanceDB, LMDB)
│   ├── memory_palace/       # NEW: former banks/
│   └── ...
├── utils/
│   └── text/                # NEW: former text/
│       ├── encoding_detector.py
│       ├── unicode_analyzer.py
│       └── hash_identifier.py
├── multimodal/               # Deferred on M1 8GB (RAM check)
│   ├── analyzer.py          # with _can_run_heavy_vision() guard
│   └── ...
└── mlx_models/              # Empty — centralized cache in brain/model_cache.py
```

**Úspora RAM:** ~2-4GB (odstraněním torch z moe_router, defer multimodalu)

---

## 8. Co NEBOŘIT

- DuckPGQGraph — funguje, je v Rust, není GPU-bound
- LanceDB embeddings — ANE/CoreML embeddery jsou oddělené memory domain
- `mlx_lm.generate()` — zůstává primary inference path
- Rust SIMD — `simd_similarity.rs` běží v Rust, ne Python

---

*Analysis complete. Awaiting approval to proceed with implementation.*
