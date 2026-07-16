+++
title = "knowledge/explainer/"
weight = 6
description = "<think> Let me analyze this module carefully:  1. **Module name**: `knowledge/explainer` 2. **Purpose**: Based on the symbols and their descriptions, this module provides functionality to explain pred..."

[extra]
tier = 2
file_count = 3
total_lines = 207
languages = "Python"
parent_path = "knowledge"
+++

<think>
Let me analyze this module carefully:

1. **Module name**: `knowledge/explainer`
2. **Purpose**: Based on the symbols and their descriptions, this module provides functionality to explain predictions in graph neural networks - it explains predictions for given nodes and explains paths between nodes.

3. **Key components**:
 - Two main classes: `DeepExplainer` (GNN-based explanation) and `FastExplainer` (delta evidence-based path explanation)
 - Key functions: `explain` (explains prediction for a node), `explain_path` (explains path between nodes)
 - Total of 207 lines across 3 files - relatively small/compact module

4. **Architecture**:
 - No outgoing dependencies detected
 - No incoming dependencies detected
 - Tier 2 module
 - This is interesting - it has no dependencies at all, which is unusual

5. **Fan-in**: The `__init__` function is referenced in 641 files! This is extremely high fan-in, meaning changes to this module would have a massive blast radius.

6. **Symbol density**: 12 functions, 2 classes, 9 methods, 3 constants across 207 lines - quite dense with implementation logic

Let me write a module overview that:
- Starts with what the module DOES (not "the module consists of...")
- Describes purpose, responsibilities, architectural role
- Notes the massive fan-in (641 references!)
- Notes the compact size (207 lines, 3 files)
- No speculation about design intent
- 4-8 sentences
- Varied sentence structure
</think>

The explainer module provides interpretability capabilities for graph neural network predictions by offering two complementary explanation strategies: a deep GNN-based explainer that generates detailed per-node prediction explanations with important edges and features, and a fast delta-based path explainer that quickly scores edge importance by measuring score drops when edges are removed. Despite having zero outgoing dependencies, this module achieves the highest fan-in across the entire codebase—its initialization entry point is referenced by over 640 other files—making it a critical interface point that would have an enormous blast radius if changed. The module is notably compact at just 207 lines across three files, maintaining high symbol density with two core explainer classes, twelve utility functions, and lazy-loading infrastructure via PEP 562. This lightweight design positions the module as a leaf in the dependency graph with no internal coupling requirements, yet it remains one of the most frequently imported components in the system.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `deep.py` (89 lines)
- `fast.py` (66 lines)
- `__init__.py` (52 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__init__</code> (Function) in deep.py — referenced in 641 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _body_hash.py, _buffer.py +592 more</li></ul>
</li>
<li>
<p><code>__getattr__</code> (Function) in __init__.py — referenced in 113 files</p>
<details><summary>PEP 562 lazy module attribute access.</summary>
<div class="doc-comment">
<p>PEP 562 lazy module attribute access.</p>
<p>Whitelist-only: only names in _LAZY_WHITELIST can be imported.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _domain_protocol.py, _lazy_imports.py, _telemetry_setup.py, academic_discovery.py, academic_search.py +83 more</li></ul>
</li>
<li>
<p><code>explain</code> (Function) in deep.py — referenced in 13 files</p>
<details><summary>Vysvětlí predikci pro daný uzel.</summary>
<div class="doc-comment">
<p>Vysvětlí predikci pro daný uzel.</p>
<p>Vrací slovník s důležitými hranami a případně důležitými features.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition_strategy.py, autonomous_analyzer.py, cost_model.py, evidence_chain.py +7 more</li></ul>
</li>
<li>
<p><code>__dir__</code> (Function) in __init__.py — referenced in 7 files</p>
<ul><li class="ref-list">Referenced by: test_f_a2_lazy_intelligence.py</li></ul>
</li>
<li>
<p><code>explain_path</code> (Function) in fast.py — referenced in 5 files</p>
<details><summary>Vysvětlí cestu mezi uzly – vrátí seznam hran (source, target) s vahami důležitosti.</summary></details>
<ul><li class="ref-list">Referenced by: adversarial.py, explainer.py, inference_engine.py, test_sprint60.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (12)</summary>
<ul>
<li><code>_fallback_explain</code> (deep.py) — <span class="doc-comment-inline">Fallback GNN explainer s gradient-based mask.</span></li>
<li><code>explain_path</code> (fast.py) — <span class="doc-comment-inline">Vysvětlí cestu mezi uzly – vrátí seznam hran (source, target) s vahami důležitosti.</span></li>
<li><code>explain</code> (deep.py)
<details><summary>Vysvětlí predikci pro daný uzel.</summary>
<div class="doc-comment">
<p>Vysvětlí predikci pro daný uzel.</p>
<p>Vrací slovník s důležitými hranami a případně důležitými features.</p>
</div>
</details>
</li>
<li><code>__getattr__</code> (__init__.py)
<details><summary>PEP 562 lazy module attribute access.</summary>
<div class="doc-comment">
<p>PEP 562 lazy module attribute access.</p>
<p>Whitelist-only: only names in _LAZY_WHITELIST can be imported.</p>
</div>
</details>
</li>
<li><code>_delta_for_edge</code> (fast.py) — <span class="doc-comment-inline">Spočítá důležitost hrany jako pokles skóre po jejím odstranění.</span></li>
<li><code>loss_fn</code> (deep.py)</li>
<li><code>_score_path</code> (fast.py) — <span class="doc-comment-inline">Ohodnotí cestu – čím kratší, tím lepší. Vrací skóre 0..1.</span></li>
<li><code>_score_path_without_edge</code> (fast.py) — <span class="doc-comment-inline">Jako _score_path, ale zakáže danou hranu.</span></li>
<li><code>__init__</code> (fast.py)</li>
<li><code>__init__</code> (deep.py)</li>
<li><code>_extract_subgraph</code> (deep.py) — <span class="doc-comment-inline">Extrahuje subgraf – využívá RelationshipDiscoveryEngine.</span></li>
<li><code>__dir__</code> (__init__.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (2)</summary>
<ul>
<li><code>DeepExplainer</code> (deep.py) — <span class="doc-comment-inline">Deep explainer pro vysvětlení predikcí pomocí GNN.</span></li>
<li><code>FastExplainer</code> (fast.py) — <span class="doc-comment-inline">Fast explainer pro vysvětlení cest v grafu pomocí delta evidence.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (9)</summary>
<ul>
<li><code>_fallback_explain</code> (deep.py) — <span class="doc-comment-inline">Fallback GNN explainer s gradient-based mask.</span></li>
<li><code>explain_path</code> (fast.py) — <span class="doc-comment-inline">Vysvětlí cestu mezi uzly – vrátí seznam hran (source, target) s vahami důležitosti.</span></li>
<li><code>explain</code> (deep.py)
<details><summary>Vysvětlí predikci pro daný uzel.</summary>
<div class="doc-comment">
<p>Vysvětlí predikci pro daný uzel.</p>
<p>Vrací slovník s důležitými hranami a případně důležitými features.</p>
</div>
</details>
</li>
<li><code>_delta_for_edge</code> (fast.py) — <span class="doc-comment-inline">Spočítá důležitost hrany jako pokles skóre po jejím odstranění.</span></li>
<li><code>_score_path</code> (fast.py) — <span class="doc-comment-inline">Ohodnotí cestu – čím kratší, tím lepší. Vrací skóre 0..1.</span></li>
<li><code>_score_path_without_edge</code> (fast.py) — <span class="doc-comment-inline">Jako _score_path, ale zakáže danou hranu.</span></li>
<li><code>__init__</code> (fast.py)</li>
<li><code>__init__</code> (deep.py)</li>
<li><code>_extract_subgraph</code> (deep.py) — <span class="doc-comment-inline">Extrahuje subgraf – využívá RelationshipDiscoveryEngine.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (3)</summary>
<ul>
<li><code>_LAZY_ATTRS</code> (__init__.py)</li>
<li><code>_MLX_AVAILABLE</code> (deep.py)</li>
<li><code>_LAZY_WHITELIST</code> (__init__.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 207 |
| Avg lines/file | 69 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

