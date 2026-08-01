# ISSUE [ARCH-SRP-001]: God Object — ModelManager / Brain Module SRP Violation

## Analýza současného stavu

### 1. Hlavní problémové soubory

| Soubor | Velikost | Metod | problém |
|--------|----------|-------|---------|
| `brain/deephermes3_engine.py` | 211 KB | 60+ | **PRIMÁRNÍ GOD OBJECT** — mixuje inference + batch + KV cache + warmup + prompt composition |
| `brain/moe_router.py` | 769 řádků | 30+ | Samostatný, ale tight-coupled na expert model management |
| `brain/model_manager.py` | 50 KB | 50+ | Model lifecycle, ale závislost na MLX internals |
| `brain/batch_scheduler.py` | 475 řádků | 13 | **UŽ EXTRAHOVÁN** — BatchScheduler oddělený, vnořený v DeepHermes3Engine |

### 2. DeepHermes3Engine — 8 odpovědností v 1 třídě

```
DeepHermes3Engine
├── BATCH_SCHEDULING (11 methods)
│   ├── _ensure_batch_worker, _shutdown_batch_worker
│   ├── batch_processor, _submit_structured_batch
│   ├── _batch_worker, _collect_batch
│   ├── _is_batch_safe, _process_batch, _process_structured_batch
│   └── _execute_structured_batch
│
├── MODEL_LIFECYCLE (16 methods)
│   ├── init_model_breaker, model, tokenizer
│   ├── _ensure_model_loaded, _compile_model_warmup
│   ├── initialize, _init_draft_model
│   └── _load_model_async, _begin_model_unload...
│
├── INFERENCE (15 methods)
│   ├── generate, generate_stream, generate_structured
│   ├── _do_generate, _prep_generate, _build_generate_kwargs
│   └── _decode_token, _stream_tokens...
│
├── KV_CACHE (27 methods)
│   ├── _prefill_warmup_caches, _prefill_system_cache
│   ├── _get_prefix_cache, _get_session_cache
│   ├── _store_session_cache, _get_kv_cache_kwargs
│   └── 17 dalších...
│
├── WARMUP (6 methods)
│   ├── _prefill, _do_prefill, _compute_warmup_hash
│   └── _execute_warmup_generation, _run_warmup_via_worker...
│
├── MEMORY_MANAGEMENT (2 methods + gc refs)
│   ├── _get_gpu_memory, _handle_metal_pressure
│   └── implicit gc.collect() calls throughout
│
├── PROMPT_COMPOSITION (3 methods)
│   ├── _get_prompt_bandit
│   ├── _compute_system_prompt_hash
│   └── _format_chatml ← DUPLICITNÍ s _prompts.py!
│
└── TELEMETRY (3 methods)
    └── _record_flush_interval_telemetry, get_lora_stats, get_inference_stats
```

### 3. Kritické zjištění: `_prompts.py` existuje, ale není používán!

```python
# brain/_prompts.py (323 lines) — POSKYTUJE:
class PromptRole(Enum): ...
class PromptTemplate: ...  # frozen dataclass
class PromptFormatter(Protocol): ...
class ChatMLPromptFormatter: ...  # implementace

# ALE:
# deephermes3_engine.py → NEPOUŽÍVÁ _prompts.py!
# moe_router.py → importuje _prompts ale má vlastní _format_* metody
```

### 4. MoERouter — duplicitní prompt composition

```python
# moe_router.py má vlastní:
def _format_expert_prompt(...) -> str
def _format_expert_block(...) -> str  
def _format_synthesis_input(...) -> str
def _fallback_synthesis(...) -> str

# A PAK deephermes3_engine.py má:
def _format_chatml(...) -> str  # DUPLICITA!
```

### 5. BatchScheduler — úspěšný případ extrakce

`brain/batch_scheduler.py` (475 lines) byl již extrahován z DeepHermes3Engine.
**Problém**: Je stále vnořený v DeepHermes3Engine module, ale fyzicky oddělený.

---

## Navrhovaná architektura (Cutting-Edge Solution)

### Cílová architektura

```
brain/
├── _prompts.py                    # ✅ EXISTUJE — PromptBuilder základ
│   ├── PromptRole, PromptTemplate, PromptFormatter Protocol
│   └── ChatMLPromptFormatter
│
├── _inference/                    # 📦 NOVY SUBPAKET — LLM inference čisté
│   ├── __init__.py
│   ├── _engine.py                 # LLMEngine Protocol
│   ├── _deephermes.py             # DeepHermes3Adapter (nyní extract z deephermes3_engine)
│   └── _stream.py                 # Stream handling
│
├── _batch/                       # 📦 NOVY SUBPAKET — batch scheduling
│   ├── __init__.py
│   └── batch_scheduler.py         # EXISTUJE, přesunout sem
│
├── _hypothesis/                  # 📦 EXISTUJICI — HypothesisEngine
│   └── research_hypothesis_engine.py  # 1968 lines
│
├── moe_router.py                 # 🔄 REFACTOR — použít _prompts.py
│
├── model_manager.py               # 🔄 REFACTOR — composice přes protokoly
│
└── brain_coordinator.py          # 📦 NOVY — orchestrace

brain/__init__.py                  # 🔄 FACADE — jednotný přístup
```

### Definované ČISTÉ ROZHRANÍ (Protocoly)

```python
# brain/_inference/_engine.py

class LLMEngine(Protocol):
    """Protocol pro čistou LLM inference — žádná prompt composition."""
    
    async def generate(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
    ) -> str:
        ...
    
    async def generate_stream(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        system_msg: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        ...
    
    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        priority: float = 1.0,
    ) -> T:
        ...


class PromptBuilder(Protocol):
    """Protocol pro prompt composition — pouze formátování."""
    
    def format_chatml(
        self,
        system_msg: str | None,
        user_msg: str,
        history: list[ChatMLMessage] | None = None,
    ) -> str:
        ...
    
    def format_dspy(
        self,
        messages: Sequence[PromptTemplate],
    ) -> str:
        ...
    
    def extract_thinking(
        self,
        response: str,
    ) -> dict[str, str]:
        ...


class HypothesisEngine(Protocol):
    """Protocol pro hypothesis-driven pivot planning."""
    
    async def generate_hypotheses(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> list[Hypothesis]:
        ...
    
    async def verify_hypothesis(
        self,
        hypothesis: Hypothesis,
    ) -> AdversarialReport:
        ...
```

### Klíčové změny

#### 1. DeepHermes3Engine → DeepHermes3Adapter (Adapter pattern)

```python
# brain/_inference/_deephermes.py

class DeepHermes3Adapter:
    """
    Adapter wrapping DeepHermes3Engine.
    Extrahuje inference z god object a deleguje na:
    - Prompt composition → PromptBuilder (injected)
    - Batch scheduling → BatchScheduler (injected)
    """
    
    def __init__(
        self,
        prompt_builder: PromptBuilder,
        batch_scheduler: BatchScheduler | None = None,
    ):
        self._prompt_builder = prompt_builder
        self._batch_scheduler = batch_scheduler
        self._engine = DeepHermes3Engine(...)
    
    async def generate(self, prompt: str, ...) -> str:
        # Use prompt_builder for composition
        formatted = self._prompt_builder.format_chatml(...)
        return await self._engine.generate(formatted, ...)
```

#### 2. MoERouter refactoring

```python
# brain/moe_router.py — použít existující _prompts.py

class MoERouter:
    def __init__(
        self,
        prompt_builder: PromptBuilder,  # INJECT — žádné vlastní format metody
        llm_engine: LLMEngine,
    ):
        self._prompt_builder = prompt_builder
        self._llm_engine = llm_engine
    
    async def _generate_with_expert(self, expert_name: str, ...) -> str:
        # Use injected prompt_builder
        prompt = self._prompt_builder.format(
            role=PromptRole.EVIDENCE,
            template=self._expert_templates[expert_name],
            ...
        )
        return await self._llm_engine.generate(prompt, ...)
```

#### 3. BrainCoordinator (nový)

```python
# brain/brain_coordinator.py

class BrainCoordinator:
    """
    Jednotný vstupní bod pro brain modul.
    Composituje: LLMEngine + PromptBuilder + HypothesisEngine + BatchScheduler
    """
    
    def __init__(
        self,
        llm_engine: LLMEngine,
        prompt_builder: PromptBuilder,
        hypothesis_engine: HypothesisEngine | None = None,
    ):
        self._llm = llm_engine
        self._prompt = prompt_builder
        self._hypothesis = hypothesis_engine
    
    async def think(self, query: str, context: dict | None = None) -> str:
        """Hlavní inference entry point."""
        # 1. Optional: generate hypotheses
        # 2. Compose prompt via PromptBuilder
        # 3. Delegate to LLMEngine
        # 4. Return result
```

---

## IMPLEMENTAČNÍ PLÁN

### Fáze 1: Základy — Protocoly a Composition

**Target soubory:**
- `brain/_inference/__init__.py` (nový)
- `brain/_inference/_engine.py` (nový — LLMEngine Protocol)
- `brain/_prompts.py` (modifikace — přidat missing metody pokud jsou)

**Kroky:**
1. Definovat `LLMEngine` Protocol v `_inference/_engine.py`
2. Definovat `PromptBuilder` Protocol (rozšířit existující v `_prompts.py`)
3. Vytvořit `DeepHermes3Adapter` který deleguje na injektovaný PromptBuilder
4. Aktualizovat `brain/__init__.py` pro nové uspořádání

### Fáze 2: Batch Scheduler přesun

**Target soubory:**
- `brain/_batch/__init__.py` (nový)
- `brain/_batch/batch_scheduler.py` (přesun z `brain/batch_scheduler.py`)

**Kroky:**
1. Vytvořit `brain/_batch/` adresář
2. Přesunout `batch_scheduler.py`
3. Aktualizovat importy v `brain/__init__.py`

### Fáze 3: MoERouter refactoring

**Target soubory:**
- `brain/moe_router.py` (modifikace)

**Kroky:**
1. Injektovat `PromptBuilder` do MoERouter
2. Odstranit duplicitní `_format_*` metody
3. Použít existující `ChatMLPromptFormatter` z `_prompts.py`

### Fáze 4: Model Manager composice

**Target soubory:**
- `brain/model_manager.py` (modifikace)

**Kroky:**
1. Přidat Protocol pro model lifecycle management
2. Extrahovat MLX-specific implementace za Protocol
3. Použít composition pattern (jak navrhuje lesson 7725)

### Fáze 5: BrainCoordinator

**Target soubory:**
- `brain/brain_coordinator.py` (nový)

**Kroky:**
1. Vytvořit `BrainCoordinator` jako fasádu
2. Zaintegrovat všechny komponenty
3. Aktualizovat call sites

---

## INVARIANTY (testy)

| # | Test | Ověření |
|---|------|---------|
| 1 | `LLMEngine.generate` nevolá žádné `format` metody | grep "format" v generate těle |
| 2 | `PromptBuilder` nemá žádné `generate` metody | grep "generate" v PromptBuilder |
| 3 | `BrainCoordinator` má přesně jednu závislost na každé komponentě | `__init__` signature |
| 4 | Žádný circular import mezi `_inference`, `_batch`, `_hypothesis` | import graph |
| 5 | `moe_router.py` používá `_prompts.py` | import check |

---

## M1 8GB CONSTRAINTS

- Nové Protocol třídy přidávají ~0 RAM overhead (pouze reference)
- Composition pattern umožňuje lazy loading jednotlivých komponent
- Batch scheduler je lazy initialized
- Přesun do subpackage nemění runtime paměťovou stopu

---

## Python 3.14+ Best Practices

- `Protocol` (PEP 544) pro type-safe dependency injection
- `frozen=True` dataclasses pro immutable prompt templates
- `slots=True` pro snížení memory footprint
- `enum.Enum` s `auto()` pro PromptRole
- Async context managers pro resource management
