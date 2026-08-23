# Utilities Index

## Entry Count

15 utilities

## All Utilities

| Name | Path | Status | Purpose |
|------|------|--------|---------|
| paths | utilities/paths.md | current | Path configuration |
| resource-allocator | utilities/resource-allocator.md | current | Resource allocation |
| evidence-writer | utilities/evidence-writer.md | current | Evidence persistence |
| env-config | utilities/env-config.md | current | Environment config |
| async-helpers | utilities/async-helpers.md | current | Async utilities |
| optional-imports | utilities/optional-imports.md | current | Optional imports |
| bounded-collections | utilities/bounded-collections.md | current | Bounded data structures |
| async-cache | utilities/async-cache.md | **NEW** | Async cache |
| adaptive-cache | utilities/adaptive-cache.md | **NEW** | Adaptive cache policy |
| asyncx-core | utilities/asyncx-core.md | **NEW** | AsyncX core |
| thread-pool-utils | utilities/thread-pool-utils.md | **NEW** | Thread pools |
| rayon-pool-utils | utilities/rayon-pool-utils.md | **NEW** | Rayon parallelism |
| intelligent-cache | utilities/intelligent-cache.md | **NEW** | Smart caching |
| memory-tier-utils | utilities/memory-tier-utils.md | **NEW** | Memory tiers |
| mlx-prompt-cache | utilities/mlx-prompt-cache.md | **NEW** | Prompt caching |

## Cache Utilities

| Name | Best For |
|------|----------|
| async-cache | Simple async TTL cache |
| adaptive-cache | Mixed access patterns |
| intelligent-cache | Workload-aware eviction |
| mlx-prompt-cache | MLX prompt reuse |

## Async Utilities

| Name | Purpose |
|------|---------|
| async-helpers | General async helpers |
| asyncx-core | Task groups, cancellation |
| thread-pool-utils | CPU-bound work |
| rayon-pool-utils | Data parallelism |

## M1-Specific

| Name | Purpose |
|------|---------|
| memory-tier-utils | UMA tier management |
| resource-allocator | M1 budget enforcement |
| mlx-prompt-cache | Metal cache optimization |
