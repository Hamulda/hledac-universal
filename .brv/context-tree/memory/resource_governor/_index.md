---
children_hash: 4df2f200e001474b94914300c51115828254d69f5da6aed524e38012f4be8be8
compression_ratio: 0.48739495798319327
condensation_order: 1
covers: [context.md, uma_memory_management.md]
covers_token_total: 357
summary_level: d1
token_count: 174
type: summary
---
<think>
The user wants me to create a more concise structural summary of the resource_governor topic. I need to aggressively compress while keeping only the most critical facts and relationships.
</think>

# Resource Governor

Memory management via ratio-based thresholds with hysteresis state machine across M1 8GB (6.25GB), M2 16GB, M3 24GB configurations.

**Concurrency presets:** emergency=0, critical=1, warn=3, soft_warn=5, ok=5 workers

**Key components:** Dual-channel TTL cache, swap tiered policy, hysteresis state machine preventing oscillation

**Flow:** memory_check → hysteresis_state → concurrency_adjust → action_take

See `uma_memory_management.md` for implementation details.