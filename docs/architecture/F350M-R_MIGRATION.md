# F350M-R Migration Dashboard

## Overview

**Issue**: F350M-R — Migration to `recon/` and canonical paths

**Problem**: Dual-path architecture with broken Rust probe pattern caused failures. `core.rust_backend` import failed because `rust.ioc` is a domain object, not a module.

**Solution**: 
1. Replace broken imports with `get_accel()` facade (lazy, single probe)
2. Consolidate to canonical paths: `recon/` for OSINT, `knowledge/` for data processing
3. Keep facade layers for backward compatibility with `DeprecationWarning`

## Architecture

```
ARCHITECTURE (post-F350M-R):
  recon/           — Canonical OSINT namespace (capability forest + primitives)
  network/         — Network primitives (passive_dns, bgp_monitor, etc.)
  knowledge/       — Data processing (ioc_processor, duckdb_store, etc.)
  
DEPRECATED FACADES (emit DeprecationWarning):
  intel/          → recon/ + network/ (stub-free __getattr__ pattern)
  forensics/      → knowledge/ (re-exports with warnings)
```

## Migration Status

| Module | Canonical Path | Status | Priority |
|--------|---------------|--------|----------|
| `forensics.ioc_extractor` | `knowledge.ioc_processor` | ✅ Complete | Low |
| `intel.*` (all 26 stubs) | `recon.*` / `network.*` | ✅ Complete (A5) | Low |

## Completed Modules

### forensics/ioc_extractor.py ✅

**Status**: Complete

**Migration**:
```python
# Before (deprecated)
from hledac.universal.forensics.ioc_extractor import fast_ioc_extract

# After (canonical)
from hledac.universal.knowledge.ioc_processor import fast_ioc_extract
```

**Canonical Hot Path** (bypasses Python entirely):
```python
# knowledge/duckdb_store.py uses:
from hledac_rust_extensions import batch_ioc_extract_unified
```

### intel/__init__.py (stub-free facade) ✅

**Status**: Complete

**Pattern**: `__getattr__` with redirect map — no physical stub files

**Migration** (search+replace):
```
from hledac.universal.intel.X       → from hledac.universal.recon.X
from hledac.universal.intel.bgp_*   → from hledac.universal.network.bgp_*
from hledac.universal.intel.passive_* → from hledac.universal.network.passive_*
```

## F350M-R Facade Pattern

For future deprecations, use this pattern:

```python
"""Module — DEPRECATED (F350M-R).
=============================================================================
Migration: import from 'canonical.path' instead.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "module.path is deprecated — import from 'canonical.path' instead.",
    DeprecationWarning,
    stacklevel=1,
)

# Re-export everything
from canonical.path import *  # noqa: F401,F403
```

## Hot vs Cold Path

| Path | Use Case | Canonical |
|------|----------|-----------|
| Hot | DuckDB batch writes | `hledac_rust_extensions.batch_ioc_extract_unified` |
| Cold | Forensic analysis | `knowledge.ioc_processor.fast_ioc_extract` |

## References

- **intel/__init__.py** — Stub-free facade pattern
- **knowledge/ioc_processor.py** — Canonical IOC processing
- **core/rust_backend/** — FFI circuit breaker + lazy Rust probe

---

*Last updated: 2026-08-06*

## F350M-R A5: Stub Files Removed (2026-08-06)

**Change**: 26 physical stub files in `intel/` replaced with 1-line placeholders (~99% code reduction)

**Before**:
- 26 stub files × ~5-8 lines each = ~169 lines
- Each file: `from recon.X import *` with comment

**After**:
- 26 stub files reduced to 1-line placeholders
- `intel/__init__.py` handles all redirects via `_RECON_MAP` (63 entries)

**Removed stubs**:
```
academic_search, archive_discovery, bgp_advisor_adapter, confidence_policy,
ct_log_client, data_leak_hunter, doh_lane, entity_signal_extractor,
exposure_clients, github_secret_scanner, identity_stitching_canonical,
intel_seed, kill_chain_tagger, leak_sentinel, network_reconnaissance,
passive_fingerprint, pastebin_monitor, pattern_mining_canonical,
relationship_discovery, shodan_wrapper, social_identity_miner, stealth_crawler,
streaming_embedder, wayback_cdx_deep_adapter, wayback_diff_miner, whois_service
```

**3 missing redirects added to _RECON_MAP**:
- `shodan_wrapper` → `recon.shodan_wrapper`
- `whois_service` → `recon.whois_service`
- `streaming_embedder` → `recon.streaming_embedder`
