# Hledac Universal — Runtime Data Locations

## Overview

All runtime data lives under `hledac/universal/runtime/` (gitignored).
No external XDG directories are used — everything is self-contained.

## Runtime Directory Structure

```
hledac/universal/
└── runtime/               ← gitignored, created at import
    ├── cti/                ← CTI_EXPORT_DIR — STIX CTI bundle exports
    ├── state/              ← RUNTIME_STATE — sprint state and reports
    ├── embeddings/         ← EMBEDDING_CACHE — vector embeddings cache
    └── benchmarks/          ← BENCHMARK_CACHE — benchmark results
```

## Path Constants (paths.py)

| Constant | Path |
|----------|------|
| `CTI_EXPORT_DIR` | `runtime/cti/` |
| `RUNTIME_STATE` | `runtime/state/` |
| `EMBEDDING_CACHE` | `runtime/embeddings/` |
| `BENCHMARK_CACHE` | `runtime/benchmarks/` |

## Environment Variable Overrides

```bash
# Override CTI export directory (downstream backward compat)
export GHOST_EXPORT_DIR=/custom/path
```

## Clearing Runtime State

```bash
# Clear all runtime data
rm -rf hledac/universal/runtime/

# Clear only CTI exports
rm -rf hledac/universal/runtime/cti/ghost_cti_*.stix.json
```

## Implementation

Path constants are defined in `paths.py` and initialized at import time.
All directories are created with `mkdir(parents=True, exist_ok=True)`.