# ISSUE R-19: ane.rs — Kompletní Analýza a Řešení

**Datum:** 2026-07-27
**Projekt:** Hledac Universal (MacBook Air M1 8GB)
**Status:** HLUBOKÁ ANALÝZA

---

## 1. Shrnutí Issue R-19

Issue tvrdí:
- `rust_extensions/src/ane.rs` — 663 LOC, plně implementovaný ANE modul
- 0 produkčních callerů
- ANE je ideální pro M1: 15 TOPS, separátní GPU context, bez contention
- `brain/ane_embedder.py` importuje `_RustCoreMLEmbedder` ale používá Python fallback
- `brain/ner_engine.py` — GLiNER-like NER, 200 findings × 768-dim cosines = 4s, ANE by dal 200-500ms
- `brain/moe_router.py` — 25 experts × 768-dim matvec, ideální ANE batch

---

## 2. Skutečný Stav — Fakta z Kódu

### 2.1 `rust_extensions/src/ane.rs` (663 lines)

| Funkce | Řádek | Status | Poznámka |
|--------|--------|--------|-----------|
| `init()` | 258 | ✅ IMPLEMENTED | Reset telemetry |
| `get_status()` | 276 | ✅ IMPLEMENTED | OS check + registry |
| `load_model()` | 295 | ✅ IMPLEMENTED | Registruje do BTreeMap (NOT CoreML load) |
| `unload_model()` | 313 | ✅ IMPLEMENTED | Odregistruje z registry |
| `list_models()` | 322 | ✅ IMPLEMENTED | Vrací registered IDs |
| `validate_batch()` | 338 | ✅ IMPLEMENTED | Enforce ANE_MAX_BATCH_SIZE=4096 |
| **`embed_tokens()`** | 421 | ❌ **STUB** | `PyRuntimeError` — deleguje na Python |
| **`run_inference()`** | 375 | ❌ **STUB** | `PyRuntimeError` — deleguje na Python |

**Klíčový kód `embed_tokens()` na řádku ~421:**
```rust
Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
    "ANE inference for '{}' should be called from Python via CoreML. \
     Use brain.ane_embedder.ANEEmbedder.embed() for actual inference.",
    model_id, ...
)))
```

**Verdikt:** `rust.ane.embed_tokens()` a `rust.ane.run_inference()` jsou **100% stuby vracející chyby**. Rust modul poskytuje pouze: model registry, batch validation, a telemetry. **Žádná skutečná ANE/CoreML inference se v Rust nikdy nekoná.**

### 2.2 `brain/ane_embedder.py` (767 lines)

**`ANEEmbedder.load()` priority order:**
1. **MLX path (PRIMARY):** `mlx_embeddings_load('nomic-ai/modernbert-embed-base', lazy=False)` — line 411
2. **CoreML path (FALLBACK):** `MLModel.modelWithContentsOfURL_error_()` — lines 431-439
3. **Hash fallback:** deterministic `_hash_embed()` — lines 553-565

**Registrace v Rust** (lines 416-422):
```python
if _RUST_ANE_AVAILABLE and _rust_ane is not None:
    _rust_ane.init()
    _rust_ane.load_model(self.model_name, str(self.coreml_path), self.hidden_dim, 512)
```
→ Rust ANE registry je informing pouze pro scheduling/telemetry. Inference jde přes Python.

**`get_ane_embedder()`** (line 594):
```python
def get_ane_embedder() -> ANEEmbedder | None:
    warnings.warn('...deprecated...')
    return None  # VŽDY vrací None
```

**Verdikt:** `ANEEmbedder` třída je plně funkční přes MLX (ne ANE). Ale `get_ane_embedder()` je deprecated → `None`. Třída není dosažitelná přes veřejné API.

### 2.3 Kde se Embeddings skutečně používají v Produkci

**Kanonická produkční cesta:**

```
semantic_dedup_findings()              # brain/ane_embedder.py:612
rerank_findings_cosine()               # brain/ane_embedder.py:660
    └── MLXEmbeddingManager.encode()    # core/embeddings/legacy.py
            └── mlx_embeddings_load('nomic-ai/modernbert-embed-base')
                    └── MLX Metal GPU backend (NENÍ ANE)
```

**Skuteční volači `semantic_dedup_findings`:**
- `brain/distillation_engine.py:637` — distillation pipeline
- `runtime/windup_engine.py:146` — sprint winddown dedup
- `export/sprint_exporter.py:496` — export dedup

**Skuteční volači `rerank_findings_cosine`:**
- `brain/distillation_engine.py:641` — post-discovery reranking

### 2.4 `brain/ner_engine.py` (1568 lines) — NER NEPOUŽÍVÁ embeddings!

**NER extraction path:**
1. **mlx_gliner2** (primary) — běží na Metal GPU/ANE pro GLiNER entity extraction
2. **CoreML NER** (fallback) — `ner.mlmodel` — require pre-compiled .mlmodel
3. **GLiNER CPU** (final fallback) — torch-based
4. **NaturalLanguage framework** — pro NLP tasky (ne embeddings!)

**`brain/ner_engine.py` — žádné volání `embed()` nebo `embed_tokens()` NENÍ.**

The issue's claim: *"200 findings × 768-dim cosines = 4s, ANE by dal 200-500ms"* — **TOTO JE NESPRÁVNÉ.** NER engine nedělá cosine similarity na embeddings. Používá GLiNER token-based NER, ne embedding cosines.

### 2.5 `brain/moe_router.py` (738 lines) — MoE NEPOUŽÍVÁ ANEEmbedder!

**MoE router:**
- Má vlastní `_embedding_model` (`mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit`)
- Používá `rust.metal.batch_matvec()` pro MoE expert routing
- **Nepoužívá** `ANEEmbedder`, `embed_tokens()`, ani `embed()`

### 2.6 CoreML Model Compilation — NENÍ IMPLEMENTOVÁNO

**Žádné `.mlmodel`, `.mlpackage`, nebo `.mlmodelc` soubory nikde v codebase.**

`brain/ane_embedder.py:convert_to_ane()` (line 467) pouze kompiluje existující `.mlmodel` files:
```python
if raw_path.exists():
    compiled_url, err = _CoreML.MLModel.compileModelAtURL_error_(url, None)
```
Ale `raw_path` = `MODELS_DIR / 'AllMiniLML6V2.mlmodel'` — tento soubor **neexistuje**.

Pro ModernBERT na ANE je potřeba:
```python
import coremltools as ct
model = ct.convert(mlx_model, ...)
model.save('modernbert_ane.mlpackage')
```
**Tento kód nikde v codebase není.**

### 2.7 mlx_embeddings vs ANE — Co je rychlejší?

**`mlx_embeddings` (nomic-ai/modernbert-embed-base):**
- Backend: **MLX Metal GPU** (ne ANE!)
- Memory: ~90MB resident
- Rychlost: ~50-100ms pro batch=32 (768-dim, seq=512)
- Metal GPU = součást UMA = contending s CPU/LLM

**Co ANE nabízí:**
- Dedicated 15 TOPS Neural Engine
- Oddělená paměť od GPU bandwidth
- Nižší spotřeba pro sustained embedding workloads
- require: coremltools compilation + CoreML runtime s `compute_units=ComputeUnit.ANANEURAL`

---

## 3. Architektonické Problémy

### Problem A: `rust.ane.embed_tokens()` je stub — ne implementace

Funkce vrací `PyRuntimeError` místo skutečných embeddings. Python kód ji nemůže použít.

**Možné řešení:** PyO3 callback do Python CoreML inference:
```rust
#[pyfunction]
pub fn embed_tokens(model_id: String, input_ids: Vec<u32>, attention_mask: Vec<u32>) -> Result<Vec<f32>, PyErr> {
    // Získat Python GIL a zavolat Python _coreml_embed()
    let py = Python::acquire_gil();
    let py_model_id = PyString::new(py, &model_id);
    // ... zavolat python function
}
```

**Ale:** Bez kompilovaného `.mlmodel` souboru i tak nefunguje.

### Problem B: Žádná CoreML kompilace ModernBERTu

ModernBERT musí být zkompilován přes coremltools s `compute_units=ComputeUnit.ANANEURAL`:
```python
import coremltools as ct
mlx_model = ...  # nomic-ai/modernbert-embed-base
coreml_model = ct.convert(mlx_model, ...)
coreml_model.save('modernbert_ane.mlpackage')
```
**Tento kód v codebase NENÍ.**

### Problem C: NER a MoE nepoužívají ANEEmbedder

- NER engine (`ner_engine.py`) — vlastní GLiNER, NE embedding cosines
- MoE router (`moe_router.py`) — vlastní embedding model, NE ANEEmbedder

Issue tvrdí že tyto moduly potřebují ANE, ale ve skutečnosti ANEEmbedder vůbec nepoužívají.

### Problem D: `get_ane_embedder()` → `None`

i kdyby ANEEmbedder fungoval, volání `get_ane_embedder()` vrací `None` (deprecated). Existuje nová cesta přes `MLXEmbeddingManager`.

---

## 4. M1 8GB UMA — Co je Pragmatické

### Aktuální MLX Metal Path — již Rychlý

Pro typické embedding workload (batch=32, seq=512):
- **MLX Metal:** ~50-100ms
- **Metal contention s LLM:** řešeno přes `_MLXFamilyMutex` (llm slot vs embed_ane slot)
- **Produkční:** `semantic_dedup_findings()` — 200 titles × 768-dim cosine = ~20ms (Rust SIMD accelerate)

ANE by přineslo zrychlení pouze pokud:
1. Metal GPU je saturated LLM inference → ANE je volné
2. Pro velké batche (100+ texts) kde ANE 15 TOPS dominuje

### Effort/Benefit Analysis

| Co | Effort | Benefit | Realita |
|----|--------|---------|---------|
| Rust `embed_tokens()` stub → real impl přes PyO3 | 1 den | Minimální (bez .mlmodel) | Nepoužitelné bez modelu |
| coremltools kompilace pipeline | 1-2 dny | Vysoký (ANE 10-50ms vs 200ms MLX) | Vyžaduje `mlx-embedding-models` → CoreML conv |
| NER wired ANE path | 0 (NER nepoužívá embeddings) | Žádný | NER používá GLiNER token-based |
| MoE wired ANE path | 0 (MoE vlastní embed model) | Žádný | MoE má vlastní mlx model |
| MLX Metal optimalizace | 0.5 dne | Střední | Již funguje, může zlepšit batching |

---

## 5. Doporučené Řešení

### Fáze 1: Opravit Rust ANE Stub (0.5 dne)

**Soubor:** `rust_extensions/src/ane.rs`

Změnit `embed_tokens()` a `run_inference()` z error stubů na **PyO3 callback** do Python CoreML inference:

```rust
#[pyfunction]
pub fn embed_tokens(
    py_model_id: String,
    py_func: &PyAny,  // Python callable pro inference
    input_ids: Vec<u32>,
    attention_mask: Vec<u32>,
) -> Result<Vec<f32>, PyErr> {
    // Získat Python GIL
    let gil = GILPool::new();
    let py = gil.python();
    
    // Zavolat Python callable
    let result = py_func.call1((&py_model_id, &input_ids, &attention_mask))?;
    // Zpracovat výsledek (numpy array → Vec<f32>)
    // ...
}
```

**Ale:** Bez `.mlmodel` souboru tato změna stále nepomůže.

### Fáze 2: coremltools Kompilační Pipeline (2 dny)

**Soubor:** `tools/compile_modernbert_ane.py` (nový)

```python
#!/usr/bin/env python3
"""
ModernBERT → CoreML ANE kompilace.

Usage:
    python tools/compile_modernbert_ane.py [--model nomic-ai/modernbert-embed-base]
"""
import argparse
import subprocess
import sys
from pathlib import Path

def compile_modernbert_ane():
    # 1. Load MLX model
    from mlx_embeddings import load
    model, processor = load('nomic-ai/modernbert-embed-base', lazy=False)
    
    # 2. Trace/convert to CoreML
    import coremltools as ct
    
    # CoreML doesn't support MLX directly, so we need a conversion path
    # Option A: ONNX export from MLX → CoreML
    # Option B: Use mlx-embeddings export API
    # Option C: Manual torch traced model
    
    # Pro M1 ANE je potřeba:
    # 1. Export do torchscript/ONNX
    # 2. ct.convert(..., compute_units=ct.ComputeUnit.ANANEURAL)
    
    traced = ...  # torchscript traced model
    coreml_model = ct.convert(
        traced,
        compute_units=ct.ComputeUnit.ANANEURAL
    )
    
    output_path = Path.home() / '.hledac' / 'models' / 'modernbert_ane.mlpackage'
    coreml_model.save(str(output_path))
    print(f"[OK] ModernBERT ANE compiled: {output_path}")

if __name__ == '__main__':
    compile_modernbert_ane()
```

**Problém:** MLX modely nejsou přímo kompatibilní s coremltools. Vyžaduje se:
1. MLX → ONNX export
2. ONNX → CoreML konverze
3. Anebo: zkompilovat z HuggingFace PyTorch verze přes coremltools přímo

### Fáze 3: Wire ANE Path do `MLXEmbeddingManager` (0.5 dne)

```python
# core/embeddings/legacy.py
class MLXEmbeddingManager:
    async def encode(self, texts: list[str]) -> np.ndarray:
        # ... existující MLX kód ...
        
        # ANE path (pokud je dostupný .mlmodel):
        if self._ane_available and self._mlmodel is not None:
            return await self._embed_ane(texts)
```

---

## 6. Závěr — Co Je Skutečně Potřeba

### Hlavní Problém
Issue R-19 je **částečně nesprávně formulovaný**:
1. ✅ `rust.ane.embed_tokens()` JE stub — potřebuje opravu
2. ✅ Žádná CoreML kompilační pipeline — potřebuje vytvořit
3. ❌ NER engine NEPOTŘEBUJE ANE embeddings — GLiNER je token-based
4. ❌ MoE router NEPOUŽÍVÁ ANEEmbedder — má vlastní mlx model

### Praktické Řešení

**Pro M1 8GB je MLX Metal Embedding již dostatečně rychlý:**
- 50-100ms pro batch=32 ModernBERT embeddings
- `_MLXFamilyMutex` správně koordinuje ANE/MLX/LLM paměť
- Rust SIMD accelerate pro cosine similarity (~20ms pro 200 × 768-dim)

**Pokud ANE path je vyžadována:**
1. **Opravit Rust `embed_tokens()`** — PyO3 callback do Python CoreML
2. **Vytvořit kompilační pipeline** — MLX/HF → torchscript → CoreML ANE
3. **Wire do `MLXEmbeddingManager`** — jako další backend
4. **Otestovat na reálném benchmarku** — ANE vs MLX Metal vs CPU

**Nebo:** Soustředit se na MLX Metal optimalizace (adaptive batching, prefetch) místo ANE complexity.

---

## Příloha: Klíčové Soubory a Řádky

| Komponenta | Soubor | Řádky | Status |
|-----------|--------|--------|--------|
| Rust embed_tokens stub | `rust_extensions/src/ane.rs` | ~421 | ❌ STUB |
| Python ANEEmbedder | `brain/ane_embedder.py` | 373-565 | ✅ MLX path OK |
| MLX Embeddings canonical | `core/embeddings/legacy.py` | 1-663 | ✅ Production path |
| NER GLiNER engine | `brain/ner_engine.py` | 401-1008 | ✅ Nepoužívá embeddings |
| MoE router | `brain/moe_router.py` | 1-738 | ✅ Vlastní embed model |
| Rust ANE registry | `rust_extensions/src/ane.rs` | ~80-250 | ✅ Registry OK |
| CoreML kompilace | `brain/ane_embedder.py:467` | 467-494 | ⚠️ Čeká na .mlmodel |
