# Rust vs Python benchmark — hledac-rust-extensions

_Generated 2026-06-01 10:38 UTC on Python 3.14.5, arm64 (Darwin)_

Median of 5 timed runs after 1 warm-up. Lower is better.

| Workload | Python (ms) | Rust (ms) | Speedup | Hits (Py / Rust) |
|---|---:|---:|---:|:---:|
| AhoCorasick — 20 patterns × 10,000 texts | 5.22 | 3.01 | 1.7× | 22000 / 18000 |
| BloomFilter — add 100,000 + check 10,000 URLs | 942.92 | 13.78 | 68.4× | 10000 / 10000 |
| RollingHash — hash 10,000 URLs (window=8) | 5.56 | 5.63 | 1.0× | 0 / 18039786061196316 |

## Workload details

### AhoCorasick
- **Workload**: 20 patterns × 10,000 texts
- **Python backend**: present
- **Rust backend**: 3.008 ms (median)
- **Speedup**: 1.7×

### BloomFilter
- **Workload**: add 100,000 + check 10,000 URLs
- **Python backend**: present
- **Rust backend**: 13.777 ms (median)
- **Speedup**: 68.4×

### RollingHash
- **Workload**: hash 10,000 URLs (window=8)
- **Python backend**: present
- **Rust backend**: 5.631 ms (median)
- **Speedup**: 1.0×
