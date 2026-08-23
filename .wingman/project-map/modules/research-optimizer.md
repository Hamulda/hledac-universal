# Research Optimizer

## Metadata

- **Entry Path:** modules/research-optimizer
- **Status:** current
- **Source:** coordinators/research_optimizer.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Adaptive research pipeline optimizer using cost models and adaptive strategy selection.

## Source Paths

- `coordinators/research_optimizer.py`
- `coordinators/research_coordinator.py`

## Use When

- Optimizing research query strategies
- Adapting to resource constraints
- Dynamic pipeline configuration

## Do Not Use When

- Simple sequential research
- Fixed pipeline requirements

## Key Components

- `ResearchOptimizer`: Main optimizer class
- `AdaptiveCostModel`: Cost estimation model
- `OptimizationConfig`: Configuration dataclass

## API

```python
from coordinators.research_optimizer import ResearchOptimizer, OptimizationConfig

config = OptimizationConfig(...)
optimizer = ResearchOptimizer(config)
result = await optimizer.execute(initial_queries)
```

## Related Entries

- modules/research-coordinator
- features/sprint-pipeline
