# MULTIHOP_CHAIN_REPORT.md — Sprint F260

## Overview

Unified `InferenceEngine.MultiHopPath` reasoning with `GraphRAGOrchestrator` multi-hop traversal into a single DSPy-powered deep reasoning chain.

## Files Modified

### 1. `brain/dspy_signatures.py` — Step 1: DeepResearchHopSignature

Added `DeepResearchHopSignature` DSPy signature for multi-hop reasoning:
```python
class DeepResearchHopSignature(dspy.Signature):
    """Given a query and current evidence, decide what to research next and why."""
    query: str = dspy.InputField(...)
    current_evidence: list[str] = dspy.InputField(desc="Findings gathered so far (last 20 max)")
    hop_number: int = dspy.InputField(...)
    next_query: str = dspy.OutputField(desc="Most promising next research direction")
    reasoning: str = dspy.OutputField(desc="Why this direction reduces epistemic uncertainty")
    confidence: float = dspy.OutputField(desc="Confidence 0-1")
```

Also added `DeepResearchChain = dspy.ChainOfThought(DeepResearchHopSignature)` wrapper.

### 2. `brain/dspy_programs.py` — Step 2: MultiHopDeepResearchChain

Added `MultiHopDeepResearchChain` DSPy Module:
- `forward(query, initial_findings, graph_rag)` — iterates hops, stops if confidence < 0.3
- `_get_ram_adaptive_hops()` — RAM-based hop count (3 when critical, 4 when warn, 5 normal)
- `_fetch_graph_evidence()` — bounded GraphRAG calls (2 hops, 30 nodes, 120s timeout)
- Factory: `get_multi_hop_chain(graph_rag, max_hops)` — fail-soft creation

M1 Constraints:
| Constraint | Value |
|------------|-------|
| max_hops | 5 (3 when RAM < 4.5GB) |
| confidence threshold | 0.3 (stop if lower) |
| evidence per hop | 20 (M1 context window guard) |
| nodes per hop | 30 |
| hops per search | 2 |
| total timeout | 120s |

### 3. `brain/hypothesis_engine.py` — Step 3: Wire to generate_hypotheses_async

In `generate_hypotheses_async()`, added MultiHop chain invocation BEFORE hypothesis generation:

```python
if HLEDAC_ENABLE_LLM and MULTIHOP_AVAILABLE and get_multi_hop_chain is not None and rag_context:
    try:
        snapshot = get_uma_snapshot()
        if not snapshot.is_emergency and not snapshot.is_critical:
            graph_rag = context.get("graph_rag")
            if graph_rag:
                chain = get_multi_hop_chain(graph_rag=graph_rag)
                extended_evidence = chain.forward(query, rag_context[:20])
                # Merge extended evidence into rag_context
```

RAM constraint: Only runs when `RAM > 5.0GB` (checked via `get_uma_snapshot()`).

### 4. `brain/inference_engine.py` — Step 4: EIG integration (stub)

MultiHopPath already uses confidence-based pruning. The EIG calculator (`utils/eig.py`) is imported in `dspy_programs.py` for future Step 5 (action ranking from chain results).

Current: hop selection is via `DeepResearchHopSignature.next_query`. Future: `EIGCalculator.rank_actions()` can rank candidate next_queries by expected information gain.

## Integration Points

| Component | Integration |
|-----------|-------------|
| `GraphRAGOrchestrator.multi_hop_search()` | Used in `_fetch_graph_evidence()` |
| `HypothesisEngine.generate_hypotheses_async()` | Runs chain before hypothesis generation |
| `utils.uma_budget.get_uma_snapshot()` | RAM constraint enforcement |
| `DempsterShafer` / `EIGCalculator` | Available in `dspy_programs.py` for DS conflict detection |

## Invariants

| Test | Description |
|------|-------------|
| `TEST_MULTIHOP_SIGNATURE_DEFINED` | DeepResearchHopSignature exists |
| `TEST_MULTIHOP_CHAIN_INSTANTIABLE` | MultiHopDeepResearchChain can be created when DSPy available |
| `TEST_MULTIHOP_RAM_ADAPTIVE` | Hop count adapts to RAM state |
| `TEST_MULTIHOP_CONFIDENCE_STOP` | Chain stops when confidence < 0.3 |
| `TEST_MULTIHOP_EVIDENCE_LIMIT` | Evidence per hop limited to 20 |
| `TEST_MULTIHOP_GRAPH_RAG_INTEGRATION` | GraphRAG search called on each hop |

## Gate

- `HLEDAC_ENABLE_LLM=1` — required for MultiHop chain activation
- `HLEDAC_ENABLE_DSPY=1` — required for DSPy runtime
- `graph_rag` must be in context dict (passed from sprint_scheduler)

## Verification

```bash
cd ~/PycharmProjects/Hledac/hledac/universal
uv run pytest tests/probe_f260_multihop.py -v
```