---
children_hash: 9042a72c6b722b7211e581b6412a0c2615701a078c11c15a0d0039f47dfc77f5
compression_ratio: 0.7109375
condensation_order: 1
covers: [context.md, issue_007_mpsc_batch_send_optimization.md, uma_memory_management.md]
covers_token_total: 1024
summary_level: d1
token_count: 728
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about resource_governor. I need to:

1. Preserve key facts, architectural decisions, and relationships
2. Reference entry names for drill-down
3. Target ~20-30% compression
4. Output clean structured markdown
5. NOT wrap in code blocks or XML tags
6. NOT use any tools

Let me analyze the content:

**Context Overview:** This domain covers memory/resource management including:
- ResourceGovernor with UMA memory management
- MPSC batch send optimization (ISSUE-007)
- Memory thresholds and concurrency control

**Key components:**
1. issue_007_mpsc_batch_send_optimization.md - MPSC optimization details
2. uma_memory_management.md - Memory management system
3. context.md - Overview

**Key facts to preserve:**
- MPSC capacity: 2048 slots × 512B = ~1 MiB budget
- Concurrency presets: emergency=0, critical=1, warn=3, soft_warn=5, ok=5
- Hysteresis state machine prevents oscillation
- Batch send reduces GIL from N× to 1×
- #[pyclass(unsendable)] required for Receiver

Let me create a concise structural summary.
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