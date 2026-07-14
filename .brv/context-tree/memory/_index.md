---
children_hash: 17b94c78e8d13539792966e0bad9e5152c4e4b190ddf8036643c71e1085f7ec9
compression_ratio: 0.8308457711442786
condensation_order: 2
covers: [resource_governor/_index.md]
covers_token_total: 804
summary_level: d2
token_count: 668
type: summary
---
<think>
The user has provided a detailed knowledge entry about memory management and resource governor for the hledac_universal project. This is a structured summary that needs to be added to the context tree at the domain level for `memory/resource_governor/`.

Let me analyze what's being requested:
1. Create a structural summary at the domain level (d1)
2. This appears to be the context for `memory/resource_governor/` domain
3. The content covers memory management, MPSC batch send optimization, and resource governor patterns

Based on the context tree structure, I need to create or update the appropriate context.md file at `.brv/context-tree/memory/resource_governor/`.

However, the user has explicitly stated "Do NOT use any tools." at the end. So I should not call any tools and instead provide a direct response.

Let me format the response as a clean structured summary as requested.
</think>

# Memory Management and Resource Governor

Domain covering memory allocation, concurrency control, and high-performance inter-process communication.

## Core Components

### ResourceGovernor (memory/resource_governor/uma_memory_management)
- Manages UMA memory with ratio-based adaptive thresholds across M1 8GB, M2 16GB, M3 24GB
- Hysteresis state machine prevents oscillation between memory pressure states
- Swap tiered policy calibrated per hardware configuration

### MPSC Batch Send Optimization (memory/resource_governor/issue_007_mpsc_batch_send_optimization)
- Rust send_batch enables single Python→Rust call for N items
- Reduces GIL acquisition overhead from N× to 1×
- Memory budget: 2048 × 512B slots ≈ 1 MiB (2× evidence_log maxsize for headroom)

## Architecture Decisions

**Concurrency Presets:**
| State | Workers | Fetch Limit |
|-------|---------|--------------|
| emergency | 0 | — |
| critical | 1 | minimal |
| warn | 3 | reduced |
| soft_warn | 5 | moderate |
| ok | 5 | full |

**MPSC Channel Design:**
- Python holds cloned Senders; Rust holds single Receiver
- Pipe delivers async wake-up to event loop
- Receiver requires `#[pyclass(unsendable)]` (ISSUE-064)
- Zero-copy bytes via msgspec.msgpack.encode()

**Key Constraints:**
- Receiver<QueueItem> is NOT Send (Python object bound to Rust Receiver)
- Each send() still does to_vec() internally (crossbeam requirement)
- Backpressure via MPSC bounded capacity

## Key Files
- `rust_extensions/src/mpsc_pool.rs` — MPSC implementation
- `sprint_scheduler.py` — 3 call sites (I2P, steganography, graph_rag findings)
- `evidence_log.py` — Evidence batching

## Related Entries
- `architecture/hledac_universal/` — Parent architecture
- `memory/resource_governor/_index.md` — Detailed index