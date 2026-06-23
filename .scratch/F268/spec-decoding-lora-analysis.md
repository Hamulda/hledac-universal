# F268 — Speculative Decoding + LoRA Fine-tuning Analysis

**Datum:** 2026-06-23
**Sprint:** F268
**Platform:** MacBook Air M1 8GB UMA, Python 3.14, MLX

---

## 1. Speculative Decoding — Aktuální Stav

### 1.1 Co je již implementováno

#### Draft Model Init (`deephermes3_engine.py:1355-1427`)

```python
async def _init_draft_model(self) → None:
    # Detekce podpory: mlx_lm.stream_generate má draft_model param?
    self._supports_draft = (
        hasattr(mlx_lm, 'stream_generate')
        and 'draft_model' in inspect.signature(mlx_lm.stream_generate).parameters
    )

    # Metal active memory threshold (ne systémová RAM!)
    if metal_active_gib >= 2.5: _speculative_enabled = False
    elif metal_active_gib > 2.0:  # 1B draft (Llama-3.2-1B-Instruct-4bit ≈700MB)
        _draft_model_name = "mlx-community/Llama-3.2-1B-Instruct-4bit"
        _speculative_enabled = True; _num_draft_tokens = 4
    elif metal_active_gib > 1.5:  # 0.5B fallback (Qwen2-0.5B-Instruct-4bit ≈350MB)
        _draft_model_name = "mlx-community/Qwen2-0.5B-Instruct-4bit"
        _speculative_enabled = True; _num_draft_tokens = 4
    else: _speculative_enabled = False

    # Load draft model
    _draft_model_obj, _draft_tokenizer = await asyncio.to_thread(
        load, _draft_model_name, tokenizer_config={"trust_remote_code": True}
    )
```

**Klíčové body:**
- Memory guard: měří `mx.get_active_memory()` (Metal/GPU), ne systémovou RAM
- Threshold 2.0 GiB pro 1B model, 1.5 GiB pro 0.5B model
- Model se loaduje lazy přes `asyncio.to_thread` — M1 safe
- Draft model navazuje na stejný tokenizer (Llama family compatibility)

#### Generování s Draft Modelem

**Přímá cesta** (`_run_inference`, line 2361):
```python
generate_kwargs["draft_model"] = self._draft_model_obj
generate_kwargs["num_draft_tokens"] = self._num_draft_tokens
response = mlx_generate(**generate_kwargs)  # mlx_lm.generate()
```

**Stream cesta** (`_stream_tokens`, line 2965-2967):
```python
if self._speculative_enabled and self._draft_model_obj is not None:
    stream_kwargs["draft_model"] = self._draft_model_obj
    stream_kwargs["num_draft_tokens"] = self._num_draft_tokens
for chunk in stream_generate(self._model, self._tokenizer, prompt=formatted_prompt, **stream_kwargs):
```

**Batch executor** (`MLXBatchedExecutor`) — propaguje draft model skrze `generate_kwargs`.

### 1.2 Gaps (Co chybí nebo nefunguje)

#### GAP-1: `hledac.speculative_decoding` neexistuje

`execution_coordinator.py:774` se snaží importovat:
```python
from hledac.speculative_decoding.speculative_engine import (
    DecodingMode, SpeculationConfig, SpeculativeEngine,
)
```

Tento modul **neexistuje**. `generate_with_speculative_decoding()` vždy spadne do `ImportError` → vrací `{'success': False, 'error': 'SpeculativeEngine not available', 'fallback': True}`.

**Důsledek:** `ExecutionCoordinator.generate_with_speculative_decoding()` je mrtvý kód.

**Avšak:** Tato API není volána z žádného aktivního místa — `deephermes3_engine.py` má vlastní integrovaný speculative decoding přes `mlx_lm.generate(draft_model=...)`. To je správná cesta.

#### GAP-2: `_init_draft_model` voláno pouze z `initialize()`

`deephermes3_engine.py:1340`:
```python
await self._init_draft_model()
```

Model se inituje pouze při první inference, ne při `load_model()`. To může být problematické pokud:
1. Model je unloadnut a znovu loadnut
2. Draft model state není persistován

**Ověření:**
```python
# deephermes3_engine.py:1258-1273 — evict_model_cache
@classmethod
def evict_model_cache(cls) -> None:
    """Evict model from Metal cache — called by model_manager."""
    # Resetuje _model, _tokenizer, _draft_model_obj, _draft_model_name?
```

Kontrola: po `evict_model_cache` se draft model reinicializuje při dalším `initialize()`.

#### GAP-3: Speculative decode telemetry

Neexistuje sledování:
- Acceptance rate (přijaté vs. rejetované draft tokeny)
- Speedup factor
- Počet speculative decode invocation

### 1.3 Co funguje správně

| Komponenta | Status | Detail |
|---|---|---|
| Draft model detection | ✅ | `mlx_lm.stream_generate` param check |
| Memory guard (Metal) | ✅ | `mx.get_active_memory()` threshold |
| Draft model init | ✅ | Lazy load přes `asyncio.to_thread` |
| `generate()` path | ✅ | `draft_model` v `_build_generate_kwargs()` |
| `stream_generate()` path | ✅ | `draft_model` v `stream_kwargs` |
| Batch executor path | ✅ | Propagováno přes `generate_kwargs` |

### 1.4 Verifikace — zda draft model skutečně běží

```bash
# Spustit sprint s MLX debug logy
HLAMD=debug python -m hledac.universal --sprint "test" --duration 60
# Hledat v logu:
# [SPEC] Draft model loaded: mlx-community/Llama-3.2-1B-Instruct-4bit
# [SPEC] Insufficient Metal memory — speculative decoding disabled
```

Nebo pomocí telemetry:
```python
engine._speculative_enabled  # True/False
engine._draft_model_name      # "mlx-community/Llama-3.2-1B-Instruct-4bit" nebo None
engine._supports_draft         # True/False
```

---

## 2. QLoRA Fine-tuning — MLX Built-in

### 2.1 Aktuální stav

**QLoRA je vestavěná v `mlx_lm` — nepotřebujete samostatnou knihovnu.**

```bash
# Aktualizace mlx-lm
pip install -U mlx-lm

# Spuštění QLoRA trénování
mlx_lm.lora \
  --model mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit \
  --data ./vasi_slozka_s_daty \
  --train \
  --batch-size 4 \
  --iters 1000
```

`mlx_lm.lora` automaticky rozpozná 4bit model, zmrazí jeho váhy a vytvoří trénovatelné LoRA adaptéry.

### 2.2 Co je QLoRA

QLoRA = Quantized LoRA — kombinace:
- **4bit kvantizace** base modelu (již máme: `DeepHermes-3-Llama-3-3B-Preview-4bit`)
- **Low-rank adaptation** na attention vrstvách
- M1 8GB: ~100-300MB pro adapter + ~2GB pro base model = ~2.3GB celkem

### 2.3 M1 8GB Limity pro QLoRA

| Parametr | Hodnota | Důvod |
|---|---|---|
| Max rank | 32-64 | Memory constraint |
| Trainable params | ~0.5-2M | 8GB UMA ceiling |
| Batch size | 1-4 | VRAM limit |
| Training steps | 100-1000 | Ephemeral adapter |
| Adapter size | ~100-300MB | LoRA weights + optimizer state |

### 2.4 ChatML Formátování (Kritické!)

DeepHermes-3 používá ChatML syntaxi. Trénovací data v `train.jsonl` musí dodržovat:

```
<|im_start|>user
{vstupni_query}<|im_end|>
<|im_start|>assistant
{akceptovany_vysledek}<|im_end|>
```

Bez správného formátu model ztratí schopnost tool calling a structured output.

### 2.5 Pipeline Integration Points

1. **Offline training** — historie sprintů z DuckDB → `train.jsonl` → `mlx_lm.lora`
2. **Per-sprint adaptation** — lightweight online na začátku sprintu ( riziko: paměť)
3. **Cross-sprint model** — persistovaný adapter pro typické query patterns

### 2.6 API v mlx_lm

```python
from mlx_lm import lora

# Training
lora.train(
    model="mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
    train_data="./data",
    batch_size=4,
    iters=1000,
    rank=16,          # LoRA rank
    alpha=32,         # scaling factor
    num_layers=16,    # omezit vrstvy pro M1 8GB
)

# Inference s adapterem
from mlx_lm import load, generate
model, tokenizer = load(
    "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
    adapter_path="./lora_adapter.npz"
)
generate(model, tokenizer, prompt)
```

---

## 3. Cutting-Edge Řešení

### 3.1 Speculative Decoding — Recommended Fixes

#### P0: Vyčistit mrtvý kód `ExecutionCoordinator`

`execution_coordinator.py:746-841` — `generate_with_speculative_decoding()` importuje neexistující modul. Dvě možnosti:

**Option A (doporučeno):** Odstranit metodu — `deephermes3_engine` už má správnou implementaci přes `draft_model` param.

**Option B:** Implementovat `hledac.speculative_decoding.speculative_engine` jako thin wrapper kolem `mlx_lm.generate` s `draft_model`.

#### P1: Draft Model Telemetry

Přidat metriky pro monitoring speculative decode efektivity:

```python
# V DeepHermes3Engine
self._spec_stats = {
    'total_speculative_calls': 0,
    'total_accepted_tokens': 0,
    'total_rejected_tokens': 0,
    'total_draft_time_ms': 0,
}

# Po každém generate() — pokud speculative
if self._speculative_enabled:
    # mlx_lm.generate vrací (response, {accepted_tokens, rejected_tokens, ...})
    # NEBO počítáme z result
    self._spec_stats['total_speculative_calls'] += 1
```

#### P2: Dynamic `num_draft_tokens`

Adaptivní počet draft tokenů podle acceptance rate:
- High acceptance (>0.8) → zvýšit `num_draft_tokens` (až 8)
- Low acceptance (<0.4) → snížit na 2-4

```python
# V _build_generate_kwargs
if self._spec_stats['total_speculative_calls'] > 10:
    rate = accepted / (accepted + rejected)
    if rate > 0.8: num_draft = min(8, self._num_draft_tokens + 2)
    elif rate < 0.4: num_draft = max(2, self._num_draft_tokens - 1)
    else: num_draft = self._num_draft_tokens
```

### 3.2 QLoRA Fine-tuning — Implementation Path

#### Fáze 1: QLoRA Infrastructure (1 sprint)
```python
# training/qlora_trainer.py
# - mlx_lm.lora.train() wrapper
# - Data: historical query→pivot pairs z DuckDB → train.jsonl
# - LoRA rank: 16-32 pro M1 8GB
# - Training: 100-1000 steps, batch=4, lr=1e-3

# Brain/hypothesis_engine.py — přidat optional QLoRA path
# - Load trained adapter via mlx_lm.load(adapter_path=...)
# - Use trained adapter for expand_query() inference
```

#### Fáze 2: Production Integration (1 sprint)
```python
# brain/query_expansion_lora.py
# - Load adapter from disk
# - Apply during hypothesis generation
# - Optional: continued online adaptation
```

### 3.3 M1 8GB Specific Optimalizace

#### Pro Speculative Decoding:
1. **Snížit `num_draft_tokens` na 2-4** — Méně spekulací = méně rejectů = lepší throughput
2. **Používat Llama-3.2-1B** (700MB) než Qwen2-0.5B — Lepší acceptance rate (stejná rodina jako Hermes-3 3B)
3. **Prefix cache reuse** — System prompt cache platí i pro draft model

#### Pro LoRA:
1. **Quantized base model** — 4bit quantized base (již používáme)
2. **Rank 16-32** — Dostatečná expresivita, malá paměť
3. **Single-task fine-tuning** — Jeden adapter per use-case (query expansion), ne multi-task

---

## 4. Architektura — Souhrn

```
brain/deephermes3_engine.py
├── _init_draft_model()        ← Draft model init (Speculative Decoding)
│   ├── metal_active_gib check
│   ├── mlx_lm.load(draft_model)
│   └── _draft_model_obj, _draft_tokenizer
│
├── _build_generate_kwargs()    ← draft_model v generate() path
│   └── generate_kwargs["draft_model"] = _draft_model_obj
│
├── _stream_tokens()            ← draft_model v stream path
│   └── stream_kwargs["draft_model"] = _draft_model_obj
│
├── _mlx_batcher               ← MLXBatchedExecutor (P0-2)
│   └── propagueje draft_model přes generate_kwargs
│
└── _mlx_worker_thread         ← MLXWorkerThread (P0-3)
    └── inference na dedicated thread

coordinators/execution_coordinator.py
└── (mrtvý kód ODSTRANĚN v T1.1)
    └── Speculative decoding žije v deephermes3_engine.py přes mlx_lm.generate(draft_model=...)
```

```
brain/hypothesis_engine.py
├── PivotPlanner
│   ├── generate_pivots()
│   ├── generate_dark_surface_queries()
│   └── expand_query() ← CANDIDATE PRO QLoRA
│
training/qlora_trainer.py      ← NEW (F269)
├── train_qlora()
├── prepare_training_data()     # DuckDB → ChatML JSONL
└── apply_adapter_to_model()

training/query_expansion_lora.py ← NEW (F269)
├── QueryDataset
├── train_lora_adapter()
└── evaluate_adapter()
```

---

## 5. Akční Plán

### Fáze 1: Speculative Decoding Cleanup — COMPLETED ✅

| Task | Soubor | Status |
|---|---|---|
| T1.1 | `execution_coordinator.py` | ✅ ODSTRANĚNO (210 řádků) |
| T1.2 | `deephermes3_engine.py` | ✅ PŘIDÁNO (`_spec_stats`) |
| T1.3 | `tests/` | Přeskočeno (není v scope) |

### Fáze 2: QLoRA Infrastructure (1 sprint)

**QLoRA je vestavěná v `mlx_lm` — žádná extra dependency není potřeba.**

| Task | Soubor | Akce |
|---|---|---|
| T2.1 | `training/qlora_trainer.py` | Nový modul — mlx_lm.lora.train() wrapper |
| T2.2 | DuckDB → JSONL | Export historie query→pivots v ChatML formátu |
| T2.3 | `brain/hypothesis_engine.py` | Integrace QLoRA pro `expand_query()` |

### Fáze 3: QLoRA Production (1 sprint)

| Task | Soubor | Akce |
|---|---|---|
| T3.1 | `training/query_expansion_lora.py` | Training loop + evaluation |
| T3.2 | Per-sprint adapter loading | Load trained adapter do Hermes3 |
| T3.3 | Online adaptation | Optional lightweight per-sprint fine-tune |

---

## 6. M1 8GB Memory Budget — Finální Čísla

```
Celkový budget: ~6.25GB

S aktuálním Hermes-3-3B (≈2GB Metal):
├── Base model (Hermes-3-3B-4bit):     ~2.0 GB
├── KV cache (max_kv_size=8192):       ~0.75 GB  ( Metalscached)
├── mlx_lm runtime overhead:            ~0.25 GB
├── Sprint orchestrátor:               ~1.0 GB
├── macOS + systém:                    ~2.5 GB
└── Rezerva:                            ~0.75 GB

S draft modelem (Llama-3.2-1B ≈700MB):
+ Draft model weights:                  +0.7 GB
+ Draft KV cache:                      +0.1 GB
= Celkem:                               ~3.6 GB Metal
Volný rezerva:                          ~2.65 GB

S LoRA adapterem (rank 16, ≈100MB):
+ LoRA adapter:                        +0.1 GB
= Celkem:                              ~3.7 GB Metal
Volný rezerva:                          ~2.55 GB
```

**Závěr:** M1 8GB unesie:
- Hermes-3-3B alone
- Hermes-3-3B + draft model (1B nebo 0.5B)
- Hermes-3-3B + draft model + LoRA adapter

Ale NIKDY ne současně všechny tři modely v RAM. LoRA a draft se musí střídat nebo LoRA musí být applied na base bez extra copy (mlx_lora podporuje in-place apply).
