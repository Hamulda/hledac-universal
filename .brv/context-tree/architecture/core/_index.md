---
children_hash: 7f2a6f1d1199e4eaa95267fab0bd5b86303f0c1e60e5c2d6960d9522d803d196
compression_ratio: 0.4770408163265306
condensation_order: 1
covers: [context.md, lock_registry_architecture.md]
covers_token_total: 392
summary_level: d1
token_count: 187
type: summary
---
<think>
The user wants me to create a concise structural summary at level d1 (domain level). I need to aggressively compress while preserving key facts about the lock registry architecture.
</think>

# core

Deadlock-free lock registry using 8-category ascending acquisition order defined in `core/locks.py`. LockCategory enum (METRICS→CACHE→CONFIG→NETWORK→CURSOR→GRAPH→WAL→MPC) ensures constant amortized acquisition time by preventing cyclic dependencies. AsyncLockDCLP implements lazy initialization—`asyncio.Lock()` created only after `threading.Lock()` check, avoiding asyncio import at module load (ISSUE-014 CRITICAL invariant). All locks within a group must share same category; use Rust AtomicCounter (issue #5) for high-frequency counters.