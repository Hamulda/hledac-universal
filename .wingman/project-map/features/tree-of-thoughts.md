# Tree-of-Thoughts Reasoning

## Metadata

- **Entry Path:** features/tree-of-thoughts
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** feature

## Summary

Exploratory reasoning via branching thought paths with self-reflection checkpoints.

## Source Paths

- `coordinators/meta_reasoning_coordinator.py`
- `coordinators/refactor_tot.py`
- `graph/hypothesis_graph.py`

## How It Works

1. Initial query → multiple reasoning branches
2. Each branch explores different angles
3. Self-reflection checkpoints validate branches
4. Promising branches expand, weak ones prune
5. Final synthesis from validated paths

## Use Cases

- Complex OSINT queries with multiple hypotheses
- Multi-source correlation
- Adversarial scenario exploration

## Constraints

- Token budget via context compressor
- Timeout via sprint duration
- Branch limit via bounded collections

## Related Entries

- modules/meta-reasoning-coordinator
- features/sprint-pipeline
