# DSPy Optimization Map

## Sprint F234 — Dormant Module Activation

**Module:** `brain/dspy_optimizer.py`
**Purpose:** Offline MIPROv2 prompt optimization for OSINT synthesis prompts.
**Status:** Activated — wired to `synthesis_runner.py` as background optimizer.

---

## Persistence

**Location:** `~/.hledac/dspy_cache.json`

**JSON Schema:**
```json
{
  "prompts": {
    "analysis:medium": "<optimized instruction string>",
    "summarization:medium": "<optimized instruction string>",
    "extraction:medium": "<optimized instruction string>"
  },
  "versions": {
    "analysis:medium": [
      {
        "version": 1,
        "prompt": "<instruction>",
        "trained_at": 1234567890.0,
        "examples": 42
      }
    ]
  },
  "current": {
    "analysis:medium": 1
  }
}
```

- **Max versions per task:** 10 (oldest pruned on save)
- **Load:** `_load_cache()` called on `DSPyOptimizer.__init__`
- **Save:** `_save_cache()` called after each successful optimization run

---

## Trained Task Keys

| Key | Description |
|-----|-------------|
| `analysis:medium` | OSINT analysis prompt — entities, gaps, sources, verification |
| `summarization:medium` | Finding summarization — facts, contested info, credibility |
| `extraction:medium` | Entity/relation extraction — people, orgs, dates, claims |

Only **medium complexity** is trained. Low/high complexity falls through to default templates.

---

## Integration Points

### 1. Initialization (`synthesis_runner.py`)
```
_get_dspy_optimizer() → DSPyOptimizer(brain_manager=None)
  → asyncio.create_task(optimizer.start(), name="dspy_optimizer")
```
- Lazy singleton — initialized on first `synthesize_findings()` call
- Background `_optimize_loop()` runs every 24h
- Task tracked in `_bg_tasks` set (if available on brain manager)

### 2. Prompt Retrieval (`synthesis_runner.synthesize_findings()`)
```
DSPyOptimizer.get_prompt('analysis', {'complexity': 'medium'})
  → _optimized_prompts[key] if available
  → _default_prompt(task) as fallback
```
Called at synthesis start, before bandit arm selection. Result set via `set_custom_prompt()`.

### 3. Cache Fallback (`synthesis_runner._get_dspy_prompts()`)
```
load_optimized_prompts() from dspy_optimizer module
  → reads ~/.hledac/dspy_cache.json directly
  → used if optimizer not yet initialized
```

---

## Guards & Circuit Breaker

| Guard | Threshold | Behavior |
|-------|-----------|----------|
| CPU | >15% | Skip optimization cycle |
| RAM available | <4GB | Skip optimization cycle |
| Battery | <80% and unplugged | Skip optimization cycle |
| Thermal | HOT or CRITICAL | Skip optimization cycle |
| Thermal trend | Rising 3 consecutive | Skip optimization cycle |
| pytest active | `sys.modules` | Skip optimization cycle |

**Circuit Breaker:**
- 3 consecutive failures → 1h blackout (`_circuit_open_until`)
- Resets on successful optimization

---

## Evidence Log Seam

**Status:** STALE — `_brain._orch._evidence_log` path does not resolve in current architecture.

`DSPyOptimizer._run_optimization()` tries to read from:
```python
recent = self._brain._orch._evidence_log.get_recent_events(1000)
```

This requires `brain._orch._evidence_log` to exist. The active evidence log is at `evidence_log.py:EvidenceLog`, but is NOT attached to `brain._orch`.

**Impact:** Training examples always empty → optimization never actually runs → only default prompts used.

**Fix needed:** Wire active `EvidenceLog` instance to `brain._orch` or override `brain_manager` reference in `DSPyOptimizer` to point to the actual evidence log accessor.

---

## DSPy Dependency

- **Import:** guarded with `try/except` — fails silently if `dspy` not installed
- **Runtime dependency:** `dspy`, `dspy.teleprompt` (MIPROv2)
- **Model:**假设 Hermes server at `http://localhost:8080/v1` (OpenAI-compatible)
- **Not in requirements.txt** — must be installed separately

---

## Bandit Interaction

DSPy optimized prompts and bandit arm selection are independent:
- DSPy optimizes the **base instruction** (task template)
- Bandit selects **prompt modifier** (arm suffix: adversarial/temporal/technical/contextual)
- Both composed: `prompt + bandit_modifier`

---

## Default Prompt Templates

When no optimized prompt is available:

```
analysis:    "You are an OSINT analyst. Analyze this query and identify:
               1. Key entities (people, organizations, locations)
               2. Information gaps
               3. Recommended sources
               4. Potential verification challenges"

summarization: "Summarize the following OSINT findings:
                 - Focus on verified facts
                 - Note contested information
                 - Include source credibility assessment"

extraction:    "Extract entities and relationships from this OSINT content:
                - People, organizations, locations
                - Dates and temporal relationships
                - Claims and their sources
                - Contradictions or uncertainties"
```

---

## Metric (MIPROv2 Training)

```python
def _osint_metric(example, pred):
    answer = str(pred.answer)
    if len(answer) < 50:
        return 0.0
    try:
        data = json.loads(answer)
        fields = data.keys() if isinstance(data, dict) else []
        field_bonus = min(1.0, len(fields) / 3)
        return 0.7 + 0.3 * field_bonus  # 0.7-1.0 for valid JSON
    except json.JSONDecodeError:
        return 0.3 if len(answer) > 100 else 0.0
```

Rewards structured JSON with 3+ fields. Penalizes non-JSON responses.

---

## Sprint F224F — FOCA Confidence Modifier

**Feature Signal:** `foca_confidence_modifier` from `forensics/enrichment_service.py`

**Source:** `ForensicsEnricher._score_foca_findings(enrichment)` → float in [0.0, 0.3]

**Integration:** Injected into enrichment dict at `enrich()` return, key `foca_confidence_modifier`

### FOCA Scoring Signals

| Signal | Modifier | Source |
|--------|----------|--------|
| PPTX macro URLs detected | +0.10 | C2 infrastructure indicator |
| PPTX has_macros | +0.05 | Malicious macro presence |
| PPTX hidden slides | +0.05 | Obfuscation indicator |
| PPTX template path | +0.05 | Forensic tracking signal |
| Email originating IP | +0.10 | Traceable infrastructure |
| Email DKIM/SPF | +0.05 | Authentication signal |
| Email attachments | +0.05 per attachment | IOCs |
| CAD autocad_version | +0.10 | Specific version identifiable |
| CAD coordinate_extents | +0.05 | Geolocation possible |

**Max cap:** 0.3 (cumulative signals can exceed this, but modifier is clamped)

### DSPy Training Signal Usage

FOCA modifier should be used as an **additional feature** in MIPROv2 training:

```
FOCA modifier signals:
- document_has_c2_urls: 0.1 if macro_urls detected
- document_has_macros: 0.05 if has_macros
- document_obfuscation: 0.05 if hidden_slides
- document_forensic_trace: 0.05 if template_path
- infrastructure_traceable: 0.1 if originating_ip
- document_ioc_density: 0.05 * attachment_count
```

These can be encoded as binary or scalar features appended to the finding metadata, used by the metric function to reward findings with high FOCA forensic signals.
