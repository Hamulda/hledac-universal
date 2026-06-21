# Rust vs Python benchmark — hledac-rust-extensions

_Generated 2026-06-20 11:33 UTC on Python 3.14.5, arm64 (Darwin)_

Median of 5 timed runs after 1 warm-up. Lower is better.

| Workload | Python (ms) | Rust (ms) | Speedup | Hits (Py / Rust) |
|---|---:|---:|---:|:---:|
| AhoCorasick — 20 patterns × 10,000 texts | 5.72 | 3.35 | 1.7× | 22000 / 18000 |
| BloomFilter — add 100,000 + check 10,000 URLs | 1131.08 | 8.22 | 137.7× | 10000 / 10000 |
| RollingHash — hash 10,000 URLs (window=8) | 5.63 | 6.06 | 0.9× | 0 / 18039786061196316 |

## Workload details

### AhoCorasick
- **Workload**: 20 patterns × 10,000 texts
- **Python backend**: present
- **Rust backend**: 3.349 ms (median)
- **Speedup**: 1.7×

### BloomFilter
- **Workload**: add 100,000 + check 10,000 URLs
- **Python backend**: present
- **Rust backend**: 8.217 ms (median)
- **Speedup**: 137.7×

### RollingHash
- **Workload**: hash 10,000 URLs (window=8)
- **Python backend**: present
- **Rust backend**: 6.063 ms (median)
- **Speedup**: 0.9×
