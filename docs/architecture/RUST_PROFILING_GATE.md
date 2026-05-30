# RUST_PROFILING_GATE

## Decision Criteria

| Condition | Action |
|-----------|--------|
| `correlate_passive_fingerprints` > 100ms for 500 findings | Proceed with Rust |
| `correlate_passive_fingerprints` < 20ms for 500 findings | Cancel Rust — not worth maintenance overhead |
| > 200ms CPU time on hot path (M1 8GB) | Strong Rust justification |
| MLX inference is 500ms–3000ms per call | Rust only helps if CPU path > 200ms |

## M1 8GB Context

Real bottlenecks (in priority order):
1. MLX inference: 500ms–3s per call — Rust does NOT help
2. Network I/O: 5–30s total sprint — Rust does NOT help
3. CPU hot path: only worth Rust if > 200ms cumulative

## Profiling Locations

### `passive_fingerprint.py`

#### `correlate_passive_fingerprints` (sync, Rust candidate)
- **File**: `intelligence/passive_fingerprint.py`, lines ~1129–1185
- **Measurement points**:
  - `_GLOBAL_STATS["correlate_extract_ms"]` — O(n×m) loop over findings × patterns
  - `_GLOBAL_STATS["correlate_canonical_ms"]` — CanonicalFinding construction
  - `_GLOBAL_STATS["correlate_total_ms"]` — full function
- **Key loop**: `for finding in findings[:MAX_FINGERPRINT_FINDINGS]` → calls `extract_fingerprints()`
- **Patterns**: 170+ compiled `re.compile()` in `SERVER_HEADER_PATTERNS` + `TLS_CERT_PATTERNS`
- **Score**: STRONG_RUST_CANDIDATE — O(n×m) linear scan, regex-heavy, zero async/MLX/network

#### `_extract_tech_stack_findings` (sync, Rust candidate)
- **File**: `intelligence/passive_fingerprint.py`, lines ~1386–1503
- **Measurement point**: `_GLOBAL_STATS["extract_tech_stack_loop_ms"]`
- **Key loop**: `for finding in findings[:MAX_TECH_STACK_FINDINGS * 2]` → O(n×_TECH_STACK_PATTERNS)
- **Patterns**: 200+ `re.compile()` in `_TECH_STACK_PATTERNS` (HTML/url_marker scan)
- **Score**: STRONG_RUST_CANDIDATE — same pattern as above, pure CPU, no I/O

### `pattern_mining.py`

**Classification**: POOR_RUST_CANDIDATE

| Metric | Value |
|--------|-------|
| Total lines | 2032 |
| async defs | 4 (100% MLX wrappers — must stay Python) |
| await calls | only in MLX async wrappers |
| re.compile | 0 (no regex in this file) |
| MLX references | yes (Mamba2, FFT — cannot move to Rust) |
| DuckDB references | 0 |
| Network references | 0 |
| numpy references | 54 (compute-heavy but GIL-released during numpy ops) |

**Async defs (must stay Python)**:
- `_get_mamba_model()` — loads MLX model
- `forecast_mamba2()` — MLX inference
- `_mamba_forecast_async()` — MLX wrapper
- `_run_mlx_forecast_background()` — MLX background task

**Compute-heavy (Rust candidate but numpy GIL-released)**:
- `_correlation_numpy()` — numpy correlation matrix
- `_correlation_mlx()` — MLX correlation (falls back to numpy)
- `_extract_pattern_features()` — numpy ndarray ops
- `_gini_coefficient()` — pure Python math
- `_detect_cycles()` — graph cycle detection

**No profiling instrumentation needed** — MLX inference dominates, Rust would not help.

## Files Not Requiring Profiling

| File | Reason |
|------|--------|
| `pattern_mining.py` | MLX is the bottleneck; async-only MLX wrappers must stay Python |
| `ioc_extractor.py` | Pure Python fallback; Rust linking failed; not in hot path |

## Rust Scoring Summary

| File | Rust Score | Reason |
|------|-----------|--------|
| `passive_fingerprint.py` (LAYER A) | STRONG | O(n×m) regex, 170+ patterns, zero I/O/MLX |
| `pattern_mining.py` | POOR | 54% async MLX wrappers, numpy GIL-released |

## Next Steps

1. Run probe tests to capture baseline timings
2. If `correlate_total_ms` > 100ms for 500 findings → implement Rust layer using Aho-Corasick
3. If `correlate_total_ms` < 20ms → document as "Rust not needed, Python regex is sufficient"
4. pattern_mining.py: no action — MLX is the actual bottleneck