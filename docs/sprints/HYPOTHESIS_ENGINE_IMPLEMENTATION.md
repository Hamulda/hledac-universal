# Hypothesis Engine Implementation — Sprint F259

> Causal Graph Reasoning Engine for Hledac Universal OSINT Platform

---

## Overview

The Hypothesis Engine implements automated causal reasoning over discovered evidence, identifying connections that no human analyst would notice across thousands of data points. Unlike ordinary LLMs that summarize, this engine **REASONS** — finding causal chains, contradictions, and hidden connections.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HypothesisEngine                                    │
│                                                                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │ CausalEngine │───▶│HypothesisGraph│───▶│ DSPy/Hermes3 │───▶│  STIX 2.1   │  │
│  │             │    │   (NetworkX)  │    │  Signatures  │    │   Export    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│         │                  │                   │                            │
│         ▼                  ▼                   ▼                            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                     │
│  │  Entity     │    │  Hidden     │    │  Causal     │                     │
│  │  Extraction │    │  Bridges    │    │  Chains     │                     │
│  └─────────────┘    └─────────────┘    └─────────────┘                     │
│         │                  │                   │                            │
│         ▼                  ▼                   ▼                            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                     │
│  │  Temporal   │    │  Anomalous   │    │  Contradic- │                     │
│  │  Sequences  │    │  Clusters   │    │  tions      │                     │
│  └─────────────┘    └─────────────┘    └─────────────┘                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. CausalEngine (`brain/causal_engine.py`)

Core reasoning loop implementing 6-step hypothesis generation pipeline.

#### Step 1: Entity Extraction

**Algorithm:** Regex-based IOC extraction with deduplication

```
Input: List[CanonicalFinding]
  │
  ├─▶ Extract IPs (regex: \b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b)
  ├─▶ Extract domains (regex: \b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b)
  ├─▶ Extract emails (regex: [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})
  ├─▶ Extract URLs (regex: https?://[^\s]+)
  │
  ▼
Output: List[Entity] with deduplication by value
```

**M1 optimization:**
- MAX_ENTITIES = 5000 (hard cap)
- MAX_FINDINGS = 50,000 (input cap)
- Source findings merged (max 100 per entity)

#### Step 2: Temporal Ordering

**Algorithm:** Sort by timestamp, group by proximity gap

```
Entities sorted by last_seen → chronological order
  │
  ├─▶ Gap threshold: 3600 seconds (1 hour)
  ├─▶ Entities within gap → same sequence
  └─▶ Sequence needs minimum 2 entities
```

**Output:** List[TemporalSequence] — ordered entity chains with timestamps

#### Step 3: Co-occurrence Analysis

**Algorithm:** Numpy-based co-occurrence matrix with float16 for RAM savings

```
                    Entity A  Entity B  Entity C
Entity A              0         5         2
Entity B              5         0         3
Entity C              2         3         0

co_occurrence[i,j] = count of findings containing both entity i and j
```

**M1 optimizations:**
- float16 dtype (50% RAM vs float32)
- MAX_CO_OCCURRENCE_MATRIX_SIZE = 2000 entities
- Bounded computation (skipped if too many entities)

#### Step 4: Anomaly Detection

**Algorithm:** Cross-domain source analysis

```
For each entity:
  │
  ├─▶ Identify source domains:
  │     - dark_web: "dark", "tor", "i2p" in source
  │     - paste: "paste", "bin" in source
  │     - cert_log: "cert", "ct", "transparency" in source
  │     - code_repo: "github", "gitlab" in source
  │
  └─▶ If ≥3 distinct domains → ANOMALY
```

**Output:** List[AnomalySignal] with cross-domain mixing scores

#### Step 5: Causal Chain Generation

**Algorithm:** Multi-source hypothesis generation with confidence scoring

```
Input: entity relationships from co-occurrence + temporal sequences
  │
  ├─▶ Build unique entity pairs
  ├─▶ For each pair:
  │     │
  │     ├─▶ Calculate confidence:
  │     │     confidence = (
  │     │       0.25 × source_factor +        # normalized source count
  │     │       0.25 × diversity_factor +    # source type diversity
  │     │       0.25 × co_occurrence_factor + # co-occurrence score
  │     │       0.25 × temporal_factor       # 1.0 if temporal, 0.3 otherwise
  │     │     )
  │     │
  │     └─▶ Generate statement via Hermes3 or template
  │
  └─▶ Sort by confidence, cap at MAX_HYPOTHESES (200)
```

**DSPy fallback:** If DSPy available, uses `CausalHypothesisSignature` for structured generation. Otherwise, direct Hermes3 prompting with template fallback.

#### Step 6: Contradiction Detection

**Algorithm:** Find conflicting attributes across findings

```
For each entity with ≥2 source findings:
  │
  └─▶ Compare attributes:
        - Temporal: earlier vs later occurrence
        - Attribute values: conflicting data points
        - Source reliability: inconsistent confidence
```

**Output:** List[Contradiction] with severity scores

### 2. HypothesisGraph (`graph/hypothesis_graph.py`)

NetworkX-based directed graph for hypothesis reasoning.

#### Node Structure

```
Entity Node:
  - node_id: unique identifier
  - entity_type: ip | domain | person | org | email | url
```

#### Edge Structure

```
HypothesisEdge:
  - source: entity A
  - target: entity B
  - hypothesis_type: causal | correlative | temporal | identity
  - statement: human-readable hypothesis
  - confidence: 0.0 - 1.0
  - supporting_sources: tuple of source types
  - temporal_sequence: event order
```

#### Hidden Bridge Detection

**Algorithm:** Betweenness centrality analysis

```
1. Compute betweenness centrality for all nodes
   - For graphs > 1000 nodes: use k=100 sample
   - Normalized values [0, 1]

2. Filter nodes with betweenness ≥ MIN_BETWEENNESS_THRESHOLD (0.01)

3. Sort by betweenness descending, return top_k (default: 20)

4. For each bridge:
   - Identify connected clusters
   - Report edge count
```

**Hidden Bridge:** A node that connects otherwise disconnected clusters, suggesting hidden relationships.

#### Anomalous Cluster Detection

**Algorithm:** Shannon entropy of entity type distribution

```
1. Get connected components (undirected)

2. For each cluster (size ≥ min_cluster_size):
   │
   ├─▶ Count entity types
   │
   ├─▶ Compute Shannon entropy:
   │     H = -Σ(p × log2(p))
   │     where p = count_of_type / total_entities
   │
   ├─▶ Normalize: mix_score = H / log2(num_types)
   │
   └─▶ If mix_score ≥ domain_mix_threshold (0.6) → ANOMALOUS
```

**Anomalous Cluster:** A cluster with high domain mixing (e.g., IP + domain + person + org in same component), suggesting hidden connections.

### 3. STIX 2.1 Export

```json
{
  "type": "bundle",
  "id": "bundle--{uuid}",
  "spec_version": "2.1",
  "objects": [
    {
      "type": "identity",
      "id": "identity--{uuid}",
      "name": "entity_value",
      "identity_class": "entity_type"
    },
    {
      "type": "relationship",
      "id": "relationship--{uuid}",
      "source_ref": "identity--{hash}",
      "target_ref": "identity--{hash}",
      "relationship_type": "causes|related-to|preceded-by|same-as",
      "description": "hypothesis statement",
      "confidence": 85
    }
  ]
}
```

## Confidence Scoring

### Formula

```
confidence = (
    0.25 × min(source_count / 10, 1.0) +
    0.25 × min(source_diversity / 5, 1.0) +
    0.25 × min(co_occurrence_score / 5, 1.0) +
    0.25 × (1.0 if temporal_consistent else 0.3)
)
```

### Factors

| Factor | Weight | Description |
|--------|--------|-------------|
| source_count | 25% | Number of findings supporting hypothesis |
| source_diversity | 25% | Number of distinct source types |
| co_occurrence_score | 25% | Strength of entity co-occurrence |
| temporal_consistent | 25% | Entities appear in same temporal sequence |

### Interpretation

| Confidence | Meaning |
|-------------|---------|
| 0.80 - 1.00 | Strong hypothesis, multiple independent sources |
| 0.60 - 0.79 | Moderate hypothesis, some support |
| 0.40 - 0.59 | Weak hypothesis, limited evidence |
| 0.00 - 0.39 | Low confidence, needs more data |

## M1 8GB Constraints

| Limit | Value | Reason |
|-------|-------|--------|
| MAX_NODES | 10,000 | Graph size cap |
| MAX_EDGES | 50,000 | Edge storage cap |
| MAX_ENTITIES | 5,000 | Entity extraction cap |
| MAX_FINDINGS | 50,000 | Input processing cap |
| MAX_HYPOTHESES | 200 | Output cap |
| CO_OCCURRENCE_FP16 | True | 50% RAM savings |
| MAX_CO_OCCURRENCE_MATRIX | 2,000 | Matrix size cap |
| RAM_THRESHOLD | 70% | Don't run if RAM > 70% |

## Integration Points

### Sprint Scheduler Integration

```
_run_export()
  │
  ├─▶ render_md → diagnostic.md
  ├─▶ render_jsonld → report.jsonld
  ├─▶ render_stix → stix.json
  ├─▶ _run_cti_export → CTI STIX bundle
  │
  └─▶ _run_hypothesis_export()     ← NEW: Sprint F259
        │
        ├─▶ CausalEngine.generate_hypotheses()
        ├─▶ HypothesisGraph.build()
        ├─▶ find_hidden_bridges()
        ├─▶ detect_anomalous_clusters()
        └─▶ Export STIX hypothesis bundle
```

### Feature Gate

```
HLEDAC_ENABLE_HYPOTHESIS=1
  └── Must be set to enable hypothesis generation

RAM check:
  └── psutil.virtual_memory().percent < 70%
      └── Skip if RAM > 70%
```

## DSPy Signatures

When DSPy is available, these signatures provide structured hypothesis generation:

### CausalHypothesisSignature

```
Input:
  entity_a: str       — Source entity
  relationship: str   — causes | correlates | precedes
  entity_b: str       — Target entity

Output:
  hypothesis_text: str  — Formal hypothesis statement
  confidence: float     — 0.0-1.0 confidence
  reasoning: str         — Brief justification
```

### ContradictionSignature

```
Input:
  finding_a: str  — Finding A content
  finding_b: str  — Finding B content

Output:
  contradicts: bool   — True if contradictory
  explanation: str   — Why they contradict
  severity: float    — 0.0-1.0 severity
```

### HiddenConnectionSignature

```
Input:
  entity_a: str  — Entity A
  entity_b: str  — Entity B

Output:
  connection_type: str  — Type of connection (if any)
  confidence: float     — 0.0-1.0 confidence
  explanation: str      — How they might be connected
```

## Usage

### Enable Hypothesis Generation

```bash
export HLEDAC_ENABLE_HYPOTHESIS=1
python -m hledac.universal --sprint "target query"
```

### Programmatic Usage

```python
from brain.causal_engine import CausalEngine
from graph.hypothesis_graph import HypothesisGraph

# Initialize
causal_engine = CausalEngine()
hypothesis_graph = HypothesisGraph()

# Generate hypotheses
hypotheses = await causal_engine.generate_hypotheses(findings)

# Build graph
for hyp in hypotheses:
    hypothesis_graph.add_hypothesis_edge(hyp)

# Find hidden bridges
bridges = hypothesis_graph.find_hidden_bridges()

# Detect anomalous clusters
anomalies = hypothesis_graph.detect_anomalous_clusters()

# Export to STIX
stix_bundle = hypothesis_graph.to_stix_bundle()
```

### Via HypothesisBuilder

```python
from export.hypothesis_builder import run_hypothesis_if_enabled

result = await run_hypothesis_if_enabled(
    findings=findings,
    sprint_id="sprint_abc",
    output_dir="/path/to/export",
)

print(f"Generated {result.hypotheses_generated} hypotheses")
print(f"Found {result.hidden_bridges} hidden bridges")
print(f"Detected {result.anomalies_detected} anomalies")
```

## Failure Modes

| Mode | Handling |
|------|----------|
| DSPy unavailable | Fallback to Hermes3 direct prompting |
| numpy unavailable | Skip co-occurrence matrix, use temporal only |
| NetworkX unavailable | Return empty results, log warning |
| RAM > 70% | Skip entirely, log info |
| Too many entities | Cap at MAX_ENTITIES, skip overflow |
| Too many findings | Cap at MAX_FINDINGS, process subset |

## Benchmarking

```
Performance targets (M1 8GB):
  - Entity extraction: ~1000 findings/second
  - Co-occurrence matrix: < 100ms for 1000 entities
  - Graph operations: < 500ms for 10k nodes
  - Full pipeline: < 30s for 50k findings
```

## See Also

- `brain/causal_engine.py` — Core reasoning engine
- `graph/hypothesis_graph.py` — Graph-based reasoning
- `export/hypothesis_builder.py` — Export integration
- `runtime/sprint_scheduler.py` — Sprint integration (_run_hypothesis_export)