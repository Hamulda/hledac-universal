# HypothesisGenerator Specification — Sprint F202G

## Overview
`hypothesis/hypothesisgenerator.py` — bounded heuristic hypothesis generation from sprint findings. Fail-soft: always returns >= 1 hypothesis, even if DSPy is unavailable.

## Types

### ResearchHypothesis
```python
@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis_text: str           # Natural language hypothesis
    confidence: float              # 0.0-1.0
    pivot_seeds: tuple[str, ...]   # New seeds to explore
    supporting_findings: tuple[str, ...]  # Evidence/finding_ids
    hypothesis_type: str          # entity_expansion | temporal | lateral | adversarial
```

### HypothesisGenerator
```python
class HypothesisGenerator:
    def __init__(self, graph: Optional[DuckPGQGraph] = None) -> None: ...
    def generate(
        self,
        findings: list[Any],
        current_seeds: list[str],
        sprint_depth: int = 1,
    ) -> list[ResearchHypothesis]: ...
```

## Invariants

| # | Name | Value |
|---|------|-------|
| 1 | MAX_HYPOTHESES | 10 per generate() call |
| 2 | MAX_SEEDS_PER_HYPOTHESIS | 5 |
| 3 | Fail-soft | Returns placeholder hypothesis when no findings/seeds |
| 4 | DSPy gate | HLEDAC_ENABLE_DSPY env var required |
| 5 | Type availability | ResearchHypothesis exported from hypothesis/ |

## Hypothesis Types

| Type | Trigger | Example |
|------|---------|---------|
| entity_expansion | IP IOC | "explore adjacent /24 subnet" |
| entity_expansion | domain IOC | "check related domains under parent TLD" |
| entity_expansion | seed anchor | "derive related domain/IP patterns from seed" |
| temporal | sprint_depth > 1 | "cross-reference WHOIS registration timeline" |
| lateral | file hash IOC | "find other artifacts sharing the same hash" |
| adversarial | email IOC | "search paste sites and breach feeds for credentials" |

## DSPy Integration

DSPy path activated when:
- `HLEDAC_ENABLE_DSPY=1` env var is set
- `_load_dspy_program()` can load compiled `HypothesisGeneratorProgram`
- Compiled program at `HLEDAC_DSPY_DIR / "hypothesis_generator.json"`

When DSPy is unavailable or fails, falls back to `_heuristic_generate()`.

## Exports

```python
from hypothesis.hypothesisgenerator import HypothesisGenerator, ResearchHypothesis
from hypothesis import HypothesisGenerator, ResearchHypothesis  # via __init__.py
```

## Test Coverage

- 27/27 tests in `probe_f202g/test_hypothesis_pivot_planner.py` passing
- Integration: `from hypothesis.hypothesisgenerator import HypothesisGenerator; gen.generate(findings, seeds, depth)` returns list[ResearchHypothesis]
- Edge: empty findings + no seeds → returns placeholder hypothesis (confidence=0.1)
- Bounds: never returns more than MAX_HYPOTHESES=10
