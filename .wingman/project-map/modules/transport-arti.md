# transport-arti

**Type:** Transport Layer  
**Path:** `transport/arti_transport.py`  
**Status:** current

## Purpose

Arti (Rust Tor implementation) transport. Lighter weight than Stem-based Tor, native Rust FFI.

## Key Functions

| Function | Purpose |
|----------|---------|
| `ArtiTransport` | Main class |
| `connect()` | Connect to Tor network |
| `fetch_circuit()` | Fetch via Arti circuit |

## Invariants

- [TA-1] Uses `arti_pyo3` Rust bindings
- [TA-2] Circuit pooling: max 5 concurrent circuits
- [TA-3] Fallback: if Arti unavailable → TorStem fallback

## M1 Memory Notes

Arti embedded in Rust extension. ~30MB static + runtime circuits.
