# Capability Audit: brain/ — Inference & Reasoning Stack

**Date:** 2026-05-30
**Scope:** brain/ + rl/ directories
**Status:** COMPLETE

---

## Sekce 1: LLM Inference Stack

### 1.1 Primary Inference Engine

| File | Lines | Model | Backend | Status |
|------|-------|-------|---------|--------|
| `hermes3_engine.py` | ~800 | DeepHermes-3-Llama-3-3B-Preview-4bit | mlx_lm | **CANONICAL** |
| `inference_engine.py` | ~600 | N/A (rule-based) | custom | active |
| `model_engine.py` | ~400 | configurable | mlx_lm/ollama | facade |

### 1.2 Hermes3 Engine Capabilities

```
class Hermes3Engine:
  - ChatML formatting
  - Batch inference (async)
  - Structured generation (Pydantic)
  - KV-cache for prompt prefix
  - GPU memory monitoring
  - Timeout: 120s default (env: HERMES_TIMEOUT_S)
```

### 1.3 Supported Backends

| Backend | Implementation | File |
|---------|---------------|------|
| MLX (Apple Silicon) | mlx_lm.load() | hermes3_engine.py |
| Ollama | ollama.generate() | model_engine.py |
| llama.cpp | llama_cpp | fallback |

---

## Sekce 2: DSPy Signatures Inventory

### 2.1 Complete Signature List

**brain/dspy_signatures.py (5 signatures):**

| Signature | Input Fields | Output Fields | Purpose |
|-----------|-------------|---------------|---------|
| `AnalysisSignature` | query | entities, gaps, sources | OSINT analysis, gap identification |
| `ExtractionSignature` | content | entities | Entity/relation extraction |
| `SummarizationSignature` | findings | summary, confidence, contested | Summarization with confidence |
| `DarkQuerySignature` | context | dark_queries | Dark surface query generation |
| `HypothesisSignature` | findings, context | hypotheses | Hypothesis generation |

**brain/dspy_service.py (3 signatures):**

| Signature | Input Fields | Output Fields | Purpose |
|-----------|-------------|---------------|---------|
| `QueryExpandSignature` | query | expanded_queries | Query expansion |
| `RelevanceScoreSignature` | findings, min_score | scored_findings | Finding relevance scoring |
| `PivotSuggestSignature` | findings, context | pivots | Pivot suggestion |

**brain/dspy_optimizer.py (1 signature):**

| Signature | Purpose |
|-----------|---------|
| `OSINTAnalyze` | Metric signature for optimizer |

**Total: 9 DSPy Signatures**

### 2.2 DSPy Programs

| Program | Signature | Type | Lines |
|---------|-----------|------|-------|
| `DarkQueryProgram` | DarkQuerySignature | single-hop | ~50 |
| `HypothesisGeneratorProgram` | HypothesisSignature | single-hop | ~50 |
| `HypothesisRankProgram` | HypothesisRankerSignature | single-hop | ~50 |

### 2.3 DSPy Optimizer

| Property | Value |
|----------|-------|
| Optimizer | **MIPROv2** (not BootstrapFewShot) |
| Metric | `osint_metric()` |
| num_candidates | 2 |
| num_trials | 2 |
| Memory guard | enabled |
| Thermal guard | enabled |

---

## Sekce 3: Reasoning Capabilities

### 3.1 Dempster-Shafer Evidence Fusion

**File:** `brain/evidence_fusion.py`

```python
class DempsterShafer:
  def __init__(self, hypotheses: set[str])
  def add_hypothesis(self, hypothesis: str)
  def add_evidence(self, hypothesis: str, mass: float, source_weight: float)
  def belief(self, hypothesis: str) -> float
  def plausibility(self, hypothesis: str) -> float
  def conflict_mass(self) -> float
  def detect_contradiction(self, threshold: float = 0.5) -> bool
```

**Usage:** `hypothesis_engine.py` imports and uses DempsterShafer for contradiction detection.

### 3.2 Beta-Binomial Confidence

**File:** `brain/confidence_utils.py`

```python
class BetaBinomial:
  def add_support(self, weight: float)
  def add_contradict(self, weight: float)
  def belief(self) -> float  # posterior mean
  def conflict(self) -> float
```

### 3.3 Insight Engine

**File:** `brain/insight_engine.py`

| Capability | Method | Status |
|------------|--------|--------|
| Pattern recognition | `_recognize_patterns()` | active |
| Anomaly detection | `_detect_anomalies()` | active |
| **Contradiction detection** | `_find_contradictions()` | active |
| **Gap identification** | `_identify_gaps()` | active |
| Hypothesis generation | `_generate_hypotheses()` | active |
| Serendipity engineering | `_engineer_serendipity()` | active |

### 3.4 Multi-Hop Inference

**File:** `brain/inference_engine.py`

```python
class MultiHopPath:
  - Represents full inference chain
  - Compound confidence calculation
  - Cycle detection
  - Final score computation
```

### 3.5 Hypothesis Engine

**File:** `brain/hypothesis_engine.py` (5319 lines)

| Capability | Implementation |
|------------|----------------|
| Abductive reasoning | Hypothesis generation from observations |
| Multi-hypothesis tracking | Bounded hypothesis space |
| Dempster-Shafer | Via evidence_fusion.DempsterShafer |
| Contradiction detection | conflict_mass() > threshold |
| Streaming evaluation | Async batch processing |

---

## Sekce 4: RL Policy Layer

### 4.1 QMIX Implementation

**File:** `rl/qmix.py`

| Component | Description |
|-----------|-------------|
| `QMixer` | Central mixing network (hypernet) |
| `QNetwork` | Per-agent Q-network |
| `QMIXAgent` | Epsilon-greedy policy |
| `QMIXJointTrainer` | Joint gradient updates |
| Backend | **MLX** (Apple Silicon native) |

### 4.2 Policy Manager

**File:** `rl/sprint_policy_manager.py`

| Property | Value |
|----------|-------|
| Enabled | **False by default** |
| Env var | `HLEDAC_DISABLE_RL != "1"` |
| Algorithm | QMIX + epsilon-greedy fallback |
| Persistence | JSON to `rl/.sprint_policy_state.json` |
| Reward fields | total_reward, sprint_rewards |

---

## Sekce 5: Gap Analysis — What We Have vs. What We Need

### 5.1 COMPLETE

| Capability | Implementation | File |
|------------|----------------|------|
| LLM inference (local) | Hermes3Engine + mlx_lm | hermes3_engine.py |
| DSPy signatures | 9 signatures defined | dspy_signatures.py, dspy_service.py |
| DSPy programs | 3 programs | dspy_programs.py |
| DSPy optimizer | MIPROv2 | dspy_optimizer.py |
| Dempster-Shafer | DempsterShafer class | evidence_fusion.py |
| Beta-Binomial confidence | BetaBinomial class | confidence_utils.py |
| Contradiction detection | InsightEngine + DempsterShafer | insight_engine.py, evidence_fusion.py |
| Gap identification | InsightEngine._identify_gaps() | insight_engine.py |
| Multi-hop inference | MultiHopPath class | inference_engine.py |
| RL policy | QMIX + epsilon-greedy | rl/qmix.py, sprint_policy_manager.py |

### 5.2 MISSING — Required Capabilities

| Capability | Status | Priority |
|------------|--------|----------|
| **Multi-hop DeepResearchChain (5+ hops)** | Partial — InferenceEngine has multi-hop but no DSPy Module wrapper | HIGH |
| **EpistemicGapDetector signature** | MISSING — gap analysis exists in InsightEngine but no DSPy signature | HIGH |
| **ContradictionResolver signature** | MISSING — contradiction detection exists but no resolution DSPy module | HIGH |
| **AcademicEvidenceExtractor signature** | MISSING | MEDIUM |
| **SocialSignalExtractor signature** | MISSING | MEDIUM |
| **Research session memory** | MISSING — no cross-sprint gap memory | HIGH |
| **DSPy BootstrapFewShot optimizer** | Uses MIPROv2 instead | LOW (MIPROv2 is superior) |
| **Dempster-Shafer ↔ DSPy bridge** | MISSING — DempsterShafer not integrated into DSPy programs | HIGH |

### 5.3 Partial Implementation (Needs Completion)

| Capability | Current State | Gap |
|------------|---------------|-----|
| Hypothesis generation | hypothesis_engine.py + DSPy | No active Dempster-Shafer feedback loop |
| Query expansion | dspy_service.QueryExpandSignature | Not connected to evidence fusion |
| Dark query generation | dspy_programs.DarkQueryProgram | Single-hop only, no multi-hop reasoning |

---

## Sekce 6: Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    LLM Inference                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────┐ │
│  │ Hermes3Engine   │  │ ModelEngine     │  │ Inference  │ │
│  │ (mlx_lm)        │  │ (ollama fallback)│  │ Engine     │ │
│  └────────┬────────┘  └────────┬────────┘  └─────┬──────┘ │
└───────────┼─────────────────────┼────────────────┼────────┘
            │                     │                │
            ▼                     ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    DSPy Layer                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Signatures   │  │ Programs     │  │ Optimizer (MIPROv2)│ │
│  │ (9 total)    │  │ (3 programs) │  │                  │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
            │                     │                │
            ▼                     ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Reasoning Layer                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Dempster-    │  │ BetaBinomial │  │ InsightEngine    │ │
│  │ Shafer       │  │ Confidence   │  │ (contradictions, │ │
│  │              │  │              │  │  gaps)           │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
            │                     │                │
            ▼                     ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Hypothesis Engine                         │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ 5319 lines — abductive reasoning, hypothesis space   │ │
│  │ Dempster-Shafer integration, contradiction detection  │ │
│  └──────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│                    RL Policy (QMIX)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ QMixer      │  │ QNetwork     │  │ SprintPolicyMgr   │ │
│  │ (MLX)       │  │ (MLX)       │  │ (disabled by def) │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Sekce 7: Recommendations

### HIGH Priority

1. **EpistemicGapDetector DSPy Signature**
   - Define signature with input: findings, gaps, context
   - Output: prioritized gap list with evidence requirements
   - Connect to InsightEngine._identify_gaps()

2. **ContradictionResolver DSPy Module**
   - Extend InsightEngine contradiction detection into DSPy program
   - Input: contradictory findings with conflict mass
   - Output: resolution strategy, confidence-adjusted evidence

3. **Dempster-Shafer ↔ DSPy Bridge**
   - Integrate DempsterShafer into hypothesis_engine DSPy flow
   - Use conflict_mass() as confidence penalty in DSPy metric
   - Enable evidence updating based on DSPy predictions

4. **Research Session Memory**
   - Implement cross-sprint gap tracking
   - Store identified gaps in LMDB
   - Query gaps at sprint initialization

### MEDIUM Priority

5. **AcademicEvidenceExtractor Signature**
   - For academic/academic profile lanes
   - Extract: citations, peer-review status, venue credibility

6. **SocialSignalExtractor Signature**
   - For social media signal correlation
   - Extract: engagement metrics, network centrality, authenticity signals

7. **Multi-hop DeepResearchChain DSPy Module**
   - Wrap InferenceEngine.MultiHopPath in DSPy Module
   - Enable 5+ hop reasoning chains via DSPy
   - Connect to hypothesis_engine for hypothesis testing

---

## Appendix: File Inventory

| File | Lines | Type | DSPy | DS | MLX |
|------|-------|------|------|----|-----|
| hypothesis_engine.py | 5319 | hypothesis engine | yes | yes | no |
| hermes3_engine.py | ~800 | LLM engine | yes | no | yes |
| inference_engine.py | ~600 | reasoning | no | no | no |
| insight_engine.py | ~400 | synthesis | no | no | no |
| synthesis_runner.py | ~500 | synthesis | yes | no | no |
| dspy_service.py | ~300 | DSPy service | yes | no | no |
| dspy_signatures.py | ~200 | signatures | yes | no | no |
| dspy_programs.py | ~200 | programs | yes | no | no |
| dspy_optimizer.py | ~300 | optimizer | yes | no | no |
| evidence_fusion.py | ~200 | DS theory | no | yes | no |
| confidence_utils.py | ~150 | confidence | no | yes | no |
| rl/qmix.py | ~500 | RL | no | no | yes |
| rl/sprint_policy_manager.py | ~400 | RL policy | no | no | no |

**Legend:** DSPy = uses DSPy, DS = Dempster-Shafer, MLX = uses MLX

---

---

## Sekce 8: Extended Cross-Module Analysis

### 8.1 EIG — Expected Information Gain Calculator

**File:** `utils/eig.py`

```python
class EIGCalculator:
  EIG_THRESHOLD = 0.1
  def compute_eig(self, hypothesis_set: list, action: dict) -> float
  def _entropy(self, hypothesis_set: list) -> float
  def _expected_entropy_after_action(self, hypothesis_set: list, action: dict) -> float
  def rank_actions(self, hypothesis_set: list, candidates: list[dict]) -> list[tuple]
```

**Integration:** Uses `DempsterShafer` for bandit_arms, computes information gain for action selection.

### 8.2 Tree of Thoughts (ToT) Integration

**File:** `tot_integration.py`

| Component | Description |
|-----------|-------------|
| `TotIntegrationLayer` | Unified ToT interface, autonomous activation |
| `TotConfig` | Configuration with memory/thermal guards |
| `TotResult` | ToT reasoning result |
| `should_activate_tot()` | Complexity-based activation decision |
| `analyze_complexity()` | Query complexity analysis |

**Features:**
- Czech language boost for ToT activation
- Memory pressure detection
- Hybrid ToT+MoE mode
- Lazy import to avoid heavy loading

### 8.3 Graph RAG Orchestrator

**File:** `knowledge/graph_rag.py`

| Component | Description |
|-----------|-------------|
| `GraphRAGOrchestrator` | Multi-hop graph traversal |
| `CentralityScores` | Node centrality metrics |
| `Community` | Detected graph communities |
| `GraphContradiction` | Contradiction in graph |

**Capabilities:**
- Multi-hop search (Hop 1..N)
- Path scoring based on credibility
- Semantic search + graph traversal
- MLX embedder (shared singleton)

### 8.4 Quantum-Inspired PathFinder

**File:** `graph/quantum_pathfinder.py`

| Component | Description |
|-----------|-------------|
| `QuantumInspiredPathFinder` | MLX-accelerated pathfinding |
| `QuantumPathConfig` | Config: max_steps, coin_type, use_mlx |
| Algorithms | Quantum random walks, Grover amplification |

**MLX Features:**
- Hadamard/Grover coin operators
- Sparse COO adjacency matrix
- M1 8GB optimized

### 8.5 Distillation Engine

**File:** `brain/distillation_engine.py`

```python
class DistillationEngine:
  async def initialize(embedding_model)
  async def add_example(example: DistillationExample)
  async def train(n_epochs)
  async def score_chain(query, chain) -> float
  def _heuristic_score(reasoning_chain) -> float
```

**Components:**
- `CriticMLP` — MLX neural network for quality scoring
- `DistillationExample` — Training examples
- SQLite-backed training data

### 8.6 Multimodal Coordinator

**File:** `coordinators/multimodal_coordinator.py`

| Component | Description |
|-----------|-------------|
| `MLXMultimodalEncoder` | Vision, Audio, Text encoders |
| `ModalityType` | DOCUMENT, IMAGE, AUDIO, VIDEO, TEXT |
| `FusedRepresentation` | Cross-modal fusion |
| `ContrastiveExample` | Contrastive learning examples |

**MLX Features:**
- M1-optimized vision encoding
- Audio spectral encoding
- Text tokenization

### 8.7 Hypothesis Generator Module

**File:** `hypothesis/hypothesisgenerator.py`

```python
class HypothesisGenerator:
  def __init__(self, graph: DuckPGQGraph)
  def generate(query, context) -> list[ResearchHypothesis]

class ResearchHypothesis:
  # IOC extraction: IPs, domains, hashes, emails
```

**Methods:**
- `_heuristic_generate()` — rule-based
- `_dspy_generate()` — DSPy-powered
- IOC extraction from payload

### 8.8 Hypothesis Builder Export

**File:** `export/hypothesis_builder.py`

```python
class HypothesisBuilder:
  def engine() -> HypothesisEngine  # canonical
  async def run_hypothesis_generation(findings, sprint_id)
  def _to_stix_bundle(hypotheses) -> dict
```

**Features:**
- Uses brain/hypothesis_engine.py (canonical)
- STIX bundle export
- RAM guard before execution

### 8.9 DuckDB Canonical Store

**File:** `knowledge/duckdb_store.py`

| Table | Purpose |
|-------|---------|
| `sprint_delta` | Per-sprint metrics |
| `shadow_findings` | Finding-level records |
| `IOCGraph` | Truth graph for IOC storage |

**Canonical Write Path:**
```
async_ingest_findings_batch()
  → async_record_canonical_findings_batch()
    → WALManager.append() [crash safety]
    → DuckDB insert
```

---

## Sekce 9: Complete Cross-Module Dependency Map

```
┌─────────────────────────────────────────────────────────────────────┐
│ Entry Points │
│  __main__.py → core/__main__.py → SprintScheduler.run()            │
└─────────────────────────────────────────────────────────────────────┘
 │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Canonical Write Layer                             │
│  duckdb_store.py → async_ingest_findings_batch()                    │
│  knowledge/graph_rag.py → multi_hop_search()                        │
└─────────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  brain/          │ │  knowledge/     │ │  graph/         │
│  hermes3_engine │ │  graph_rag      │ │  quantum_path   │
│  hypothesis_eng  │ │  duckdb_store   │ │  finder         │
│  insight_engine  │ │                 │ │                 │
│  dspy_*          │ │                 │ │                 │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         ▼ ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Evidence Fusion Layer                             │
│  evidence_fusion.py → DempsterShafer │
│  confidence_utils.py → BetaBinomial                                  │
│  utils/eig.py → EIGCalculator │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    RL Policy Layer                                   │
│  rl/qmix.py → QMIXAgent, QMixer (MLX)                               │
│  rl/sprint_policy_manager.py → SprintPolicyManager                  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Export Layer │
│  export/hypothesis_builder.py → STIX bundle │
│  export/sprint_exporter.py → JSON/CSV export                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Sekce 10: Additional MLX Usage Patterns

###10.1 MLX Users Across Project

| File | MLX Usage |
|------|-----------|
| `brain/prompt_cache.py` | KV-cache management |
| `brain/prompt_bandit.py` | Multi-armed bandit |
| `brain/moe_router.py` | Mixture of Experts routing |
| `brain/gnn_predictor.py` | Graph neural network |
| `brain/distillation_engine.py` | CriticMLP training |
| `brain/modernbert_engine.py` | Embeddings |
| `brain/apple_fm_probe.py` | Apple Foundation Model |
| `rl/qmix.py` | QMIX neural networks |
| `rl/replay_buffer.py` | Experience replay |
| `graph/quantum_pathfinder.py` | Quantum random walks |
| `knowledge/graph_rag.py` | Embedder (shared) |
| `prefetch/prefetch_oracle.py` | ML-based prefetch |
| `prefetch/ssm_reranker.py` | Reranking model |
| `research/task_prioritizer.py` | Priority scoring |
| `research/spike_priority.py` | Spike detection |
| `core/mlx_embeddings.py` | Core embedding manager |
| `core/resource_governor.py` | M1 resource monitoring |

### 10.2 Total MLX Usage: 22 files

---

## Sekce 11: Complete File Inventory (Extended)

| File | Lines | Type | DSPy | DS | MLX | EIG | ToT |
|------|-------|------|------|-----|-----|-----|-----|
| hypothesis_engine.py | 5319 | hypothesis | yes | yes | no | no | no |
| hermes3_engine.py | ~800 | LLM | yes | no | yes | no | no |
| inference_engine.py | ~600 | reasoning | no | no | no | no | no |
| insight_engine.py | ~400 | synthesis | no | no | no | no | no |
| synthesis_runner.py | ~500 | synthesis | yes | no | no | no | no |
| dspy_service.py | ~300 | DSPy | yes | no | no | no | no |
| dspy_signatures.py | ~200 | signatures | yes | no | no | no | no |
| dspy_programs.py | ~200 | programs | yes | no | no | no | no |
| dspy_optimizer.py | ~300 | optimizer | yes | no | no | no | no |
| evidence_fusion.py | ~200 | DS theory | no | yes | no | no | no |
| confidence_utils.py | ~150 | confidence | no | yes | no | no | no |
| rl/qmix.py | ~500 | RL | no | no | yes | no | no |
| rl/sprint_policy_manager.py | ~400 | RL policy | no | no | no | no | no |
| utils/eig.py | ~150 | EIG | no | yes | no | yes | no |
| tot_integration.py | ~500 | ToT | no | no | no | no | yes |
| graph_rag.py | ~600 | graph RAG | no | no | yes | no | no |
| quantum_pathfinder.py | ~400 | quantum | no | no | yes | no | no |
| distillation_engine.py | ~400 | distillation | no | no | yes | no | no |
| multimodal_coordinator.py | ~400 | multimodal | no | no | yes | no | no |
| hypothesisgenerator.py | ~300 | hypothesis | yes | no | no | no | no |
| export/hypothesis_builder.py | ~300 | export | no | yes | no | no | no |
| duckdb_store.py | ~800 | storage | no | no | no | no | no |

**Legend:** DSPy, DS=Dempster-Shafer, MLX, EIG=Expected Information Gain, ToT=Tree of Thoughts

---

## Sekce 12: Final Gap Analysis (Updated)

### 12.1 FULLY IMPLEMENTED

| Capability | File | Status |
|------------|------|--------|
| LLM inference (Hermes3) | hermes3_engine.py | CANONICAL |
| DSPy signatures (9) | dspy_*.py | active |
| DSPy programs (3) | dspy_programs.py | active |
| DSPy optimizer (MIPROv2) | dspy_optimizer.py | active |
| Dempster-Shafer | evidence_fusion.py | active |
| Beta-Binomial confidence | confidence_utils.py | active |
| EIG calculator | utils/eig.py | active |
| ToT integration | tot_integration.py | active |
| Graph RAG | graph_rag.py | active |
| Quantum pathfinder | quantum_pathfinder.py | active |
| Distillation engine | distillation_engine.py | active |
| Multimodal encoder | multimodal_coordinator.py | active |
| QMIX RL | rl/qmix.py | MLX native |
| Multi-hop inference | inference_engine.py | active |
| Contradiction detection | insight_engine.py + evidence_fusion.py | active |
| Gap identification | insight_engine.py | active |
| Hypothesis generation | hypothesis_engine.py | canonical |
| STIX export | export/hypothesis_builder.py | active |

### 12.2 MISSING — HIGH Priority

| Capability | Why Missing | Implementation Path |
|-----------|-------------|---------------------|
| **EpistemicGapDetector DSPy signature** | InsightEngine has gaps but no DSPy wrapper | Create DSPy signature from _identify_gaps() |
| **ContradictionResolver DSPy module** | Detection exists, resolution doesn't | Extend GraphContradiction + DSPy program |
| **Dempster-Shafer ↔ DSPy bridge** | DS not in DSPy metric | Use conflict_mass() in osint_metric() |
| **Research session memory** | No cross-sprint gap persistence | LMDB store for gaps |
| **Multi-hop DeepResearchChain (5+ hops)** | InferenceEngine exists, no DSPy Module | Wrap MultiHopPath in dspy.Module |

### 12.3 MISSING — MEDIUM Priority

| Capability | Why Missing | Implementation Path |
|-----------|-------------|---------------------|
| **AcademicEvidenceExtractor** | No academic profile support | New DSPy signature |
| **SocialSignalExtractor** | No social media signals | New DSPy signature |
| **ToT ↔ QMIX integration** | ToT and RL are separate | Connect ToT decisions to RL policy |
| **Quantum ↔ Dempster-Shafer** | Separate algorithms | Use quantum walks for evidence gathering |

### 12.4 PARTIAL — Needs Integration

| Capability | Current | Gap |
|------------|---------|-----|
| Hypothesis engine → DSPy | hypothesis_engine.py uses DSPy programs | No DS feedback loop |
| Query expansion → evidence fusion | dspy_service.QueryExpandSignature | Not connected to DS |
| Dark queries → multi-hop | dspy_programs.DarkQueryProgram | Single-hop only |
| Graph RAG → contradiction | graph_rag.GraphContradiction | Not wired to DS |
| EIG → RL policy | EIGCalculator uses DS | Not connected to QMIX |

---

## Sekce 13: Integration Roadmap

### Phase 1: DSPy ↔ Dempster-Shafer Bridge (HIGH)
```
hypothesis_engine.py
 │ uses
  ▼
evidence_fusion.DempsterShafer ──► conflict_mass()
 ▲ │
  │ use in ▼
  └──────────────────────── osint_metric() [dspy_optimizer.py]
```

### Phase 2: Epistemic Gap Memory (HIGH)
```
InsightEngine._identify_gaps()
  │ stores
  ▼
LMDB gap_store ──► query at sprint init
```

### Phase 3: Multi-hop DSPy Module (HIGH)
```
InferenceEngine.MultiHopPath
  │ wrap
  ▼
dspy.Module(DeepResearchChain) ──► 5+ hop reasoning
```

### Phase 4: ToT ↔ RL Integration (MEDIUM)
```
TotIntegrationLayer.should_activate_tot()
  │ informs
  ▼
SprintPolicyManager ──► QMIX action selection
```

### Phase 5: Academic/Social Signatures (MEDIUM)
```
New signatures in dspy_signatures.py:
  - AcademicEvidenceExtractor
  - SocialSignalExtractor
```

---

*Generated: 2026-05-30*
*Extended: 2026-05-30 (cross-module analysis)*
