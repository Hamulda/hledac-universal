# Graph Pivot Prioritization Audit

**Sprint**: F23x
**Date**: 2026-05-19
**Scope**: `runtime/pivot_planner.py`, `runtime/graph_accumulator.py`, `discovery/source_registry.py`, `knowledge/graph_service.py`, `runtime/pivot_executor.py`, `runtime/sprint_scheduler.py`, `graph/quantum_pathfinder.py`

---

## 1. Flow: Finding → Graph Relation → Pivot Task → Priority → Consumed

```
finding accepted (confidence, source_type, finding_id)
        │
        ▼
SprintGraphAccumulator.accumulate_findings()
        │  rows: (finding_id, source_type, confidence, sprint_id)
        ▼
graph_service.upsert_ioc_batch()         ← DuckPGQ, NOT used for pivot scoring
        │
        ▼
PivotPlanner.plan_pivots(findings, graph_stats=None)      ← advisory only
        │  _score_pivot_domain(graph_stats)  ← novelty check: domain in graph → no bonus
        │  _score_pivot_graph(graph_stats)   ← degree + connected_iocs check
        │  _cheap_score_finding()            ← confidence + signal_facets + source_type boost
        │  hypothesis_feedback penalty (F203G)
        │
        ▼
pivots.sort(key=lambda p: p.expected_value, reverse=True)   ← true priority sort
        │
        ▼
scheduler._planned_pivots = pivots        ← stored, advisory only
        │
        ▼
PivotExecutor.execute_top_pivots(pivots)  ← consumes top-N per config
        │
        ▼
enqueue_pivot(ioc_value, ioc_type, confidence, degree, task_type)
        │
        ▼
asyncio.PriorityQueue(priority=-effective)   ← negative = higher priority first
        │
        ▼
_drain_pivot_queue(max_tasks=5)    ← per ACTIVE cycle, up to 5 tasks
```

---

## 2. Priority Score — Exists?

**Yes.** `Pivot` has `expected_value: float [0.0, 1.0]` (pivot_planner.py:97).

Sort order: `pivots.sort(key=lambda p: p.expected_value, reverse=True)` (line 597).

Queue insertion: `priority = -effective` (sprint_scheduler.py:10793) — **negative so min-heap returns highest priority first**.

---

## 3. Does Pivot Prioritization Use Graph Signal?

**Partially, but only at plan time — not at queue consumption.**

### Plan-time scoring (pivot_planner.py)

| Signal | Used? | Where |
|--------|-------|-------|
| Finding confidence | ✅ | `_cheap_score_finding()` line 226 |
| Signal facets | ✅ | `_score_pivot_domain()` line 264 |
| Source type quality boost | ✅ | high_quality_sources set line 238 |
| Domain novelty in graph | ✅ | `_score_pivot_domain()` line 258: `domain not in existing_domains → +0.2` |
| Node degree in graph | ✅ | `_score_pivot_graph()` line 331: `node_degree > 5 → +0.15` |
| Connected IOC flag | ✅ | `_score_pivot_graph()` line 327: `ioc_value not in connected_iocs → +0.2` |
| Hypothesis feedback penalty | ✅ | F203G penalty multiplier line 631 |
| Mission intent boost (F225D) | ✅ | `score_pivot_for_mission()` line 362 |
| **Graph centrality (PageRank, betweenness)** | ❌ | NOT used — only raw degree count |
| **Previous sprint memory** | ❌ | NOT used |
| **Cross-sprint IOC frequency** | ❌ | NOT used |
| **DuckPGQ query results** | ❌ | NOT used |

### Query-level pivots (F216F, `generate_pivot_candidates_from_query`)

**Graph signal is entirely absent.** Hardcoded `expected_value` constants:
- Root domain: 0.9
- WWW variant: 0.7  
- Archive: 0.5
- IP reverse DNS: 0.7
- Graph: 0.5

No `graph_stats` parameter passed. No novelty check.

---

## 4. Source Confidence in Pivot Scoring?

**Partial.** `_score_pivot_domain` uses `confidence * 0.6` as base. Source type quality boost gives +0.1 for `ct_log`, `certificate`, `cisa_kev`, `threatfox_ioc`, `public`, `deep_probe`, `forensics`, `multimodal`.

However, `discovery/source_registry.py:72` has `source_quality_score()` which is **NOT called by pivot_planner** — only by `ti_feed_adapter` and `acquisition_strategy`.

---

## 5. Noise Risk by Pivot Type

| Pivot Type | Noise Risk | Reason |
|-----------|------------|--------|
| `archive` | **HIGH** | Wayback/commoncrawl can return 1000s of results for popular domains. Base score = `confidence * 0.4`. No dedup against prior sprints. |
| `domain` (from URL) | MEDIUM | Extracts domain from URLs. Many URLs → many duplicate domain pivots. Dedup by `(pivot_type, ioc_type, ioc_value)` mitigates somewhat. |
| `leak` (email) | MEDIUM | Breach aggregators can return massive leak dumps. But bounded by source. |
| `graph` (IP) | LOW | Reverse DNS + graph traversal. Limited blast radius. |
| `identity` | LOW | Profile resolution. Small surface. |
| `graph` (hash) | **HIGH** | VirusTotal/MalwareBazaar can return large malware catalogues. No result cap in pivot executor. |

---

## 6. Key Findings

### FINDING 1: Queue consumption is FIFO within priority bands
`_drain_pivot_queue()` processes `max_tasks=5` per ACTIVE cycle. Pivots from the same `expected_value` score come out in enqueue order (asyncio PriorityQueue tie-break is insertion order). **No temporal decay, no degree-normalization.**

### FINDING 2: Query-level pivots ignore graph signal completely
`generate_pivot_candidates_from_query()` (F216F) creates pivots with hardcoded scores and no graph context. When the planner runs without findings (e.g., cold start), `graph_stats` is `{}` or not passed at all.

### FINDING 3: Graph centrality APIs exist but aren't used
`knowledge/graph_service.py` has `CentralityScores` with `pagerank`, `betweenness`, `closeness`, `eigenvector`. Only raw `degree` (count of edges) is used in `_score_pivot_graph`. No PageRank, betweenness, or eigenvector.

### FINDING 4: No cross-sprint memory in pivot scoring
The graph accumulates IOC nodes across sprints (`sprint_id` field in upsert), but pivot scoring only sees current-sprint `graph_stats`. A node seen across 10 prior sprints gets the same novelty bonus as one seen in 1 prior sprint.

### FINDING 5: Pivot executor is read-only advisory
`pivot_executor.py` receives pivots from `planned_pivots` but does not feed results back into graph or pivot scoring. No closed loop.

---

## 7. Recommended First Safe Heuristic

**Add domain-degree-weighted novelty boost to `_score_pivot_domain()`:**

```python
def _score_pivot_domain(domain, confidence, envelope, graph_stats):
    score = confidence * 0.6

    # Existing novelty check
    existing_domains = graph_stats.get("domains", [])
    if domain not in existing_domains:
        score += 0.2

    # NEW: degree-weighted novelty — high-degree parent domains are less interesting
    node_degree = graph_stats.get("node_degrees", {}).get(domain, 0)
    degree_penalty = min(0.15, node_degree * 0.01)  # -0.01 per incident, cap at -0.15
    score -= degree_penalty

    # NEW: cross-sprint frequency bonus (proxy via domain in graph at all)
    # If domain exists in graph, it has appeared in prior sprints — reduce novelty
    if domain in existing_domains:
        score -= 0.05  # Already pivoted — slightly deprioritize

    return min(1.0, max(0.0, score))
```

**Why safe:**
- Only modifies scoring weight, no new queue structure
- Uses existing `node_degrees` API already in `graph_stats`
- Backward-compatible — only adjusts score within existing bounds
- Reduces noise from high-degree domains (common.com, cloudfront.net) that fill the queue

**Expected impact:** Archive/domain pivots on high-degree nodes (CDNs, registrars) get deprioritized; novel low-degree domains rise in queue.

---

## 8. Files Reviewed

| File | Key Role |
|------|----------|
| `runtime/pivot_planner.py` | Pivot generation + scoring, F202G, F216F, F225D |
| `runtime/pivot_executor.py` | Consumes planned pivots, F204C |
| `runtime/sprint_scheduler.py` | `_get_graph_signal()`, `_drain_pivot_queue()`, `enqueue_pivot()` |
| `runtime/graph_accumulator.py` | IOC → graph accumulation adapter (F232I) |
| `knowledge/graph_service.py` | `CentralityScores`, `upsert_ioc_batch()`, `graph_stats()` |
| `discovery/source_registry.py` | `source_quality_score()` — NOT used by pivot_planner |
| `graph/quantum_pathfinder.py` | Does not exist (confirmed) |
| `runtime/sprint_advisory_runner.py` | Passes `graph_signal` to analyst workbench, not to pivot planner |