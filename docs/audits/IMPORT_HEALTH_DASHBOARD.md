# Import Health Dashboard

**Generated:** 2026-05-24
**Source:** broken_imports.json

## Summary

| Metric | Value |
|--------|-------|
| Total broken imports (before) | 281 (91 KB) |
| **Total broken imports (after)** | **40 (14.3 KB)** |
| Target size | < 50 KB |
| Core imports pass | ✅ 3/3 |

## Reduction: 91 KB → 14.3 KB (84% reduction)

## Permanently Shimmed Items (241 total)

### Deleted in F196A Sprint (cleanup)
| Item | Count | Rationale |
|------|-------|-----------|
| `hledac.universal.layers.*` | 16 | Layers system removed |
| `hledac.universal.rl.marl_coordinator.*` | 8 | MARLCoordinator deleted |
| `hledac.universal.runtime.memory_watchdog.*` | 7 | MemoryWatchdog deleted |
| `hledac.universal.transport.TransportContext` | 6 | TransportContext removed |
| `hledac.universal.runtime.intelligence_dispatcher.*` | 4 | IntelligenceDispatcher deleted |
| `hledac.universal.FETCH_SEMAPHORE` | 3 | Removed |
| `hledac.universal.orchestrator.*` | 3 | Refactored to autonomous_orchestrator |

### Never Existed (ghost modules)
| Prefix | Count | Rationale |
|--------|-------|-----------|
| `hledac.cortex.*` | 4 | Cortex module never existed |
| `hledac.speculative_decoding.*` | 3 | Never implemented |
| `hledac.tools.preserved_logic.*` | 3 | Never existed |
| `hledac.outdated.*` | 2 | Cleaned up |
| `hledac.advanced_rag.*` | 1 | Never existed |
| `hledac.stealth_osint.*` | 4 | Never existed |
| `hledac.stealth_web_v2.*` | 4 | Never existed |
| `hledac.supreme.*` | 3 | Never existed |
| `hledac.ultra_context.*` | 4 | Never existed |

### Moved/Refactored (outside universal/)
| Module | Count | Rationale |
|--------|-------|-----------|
| `hledac.core.*` | ~40 | All hledac/core/* modules are outside universal/ |
| `hledac.security.*` (non-universal) | 6 | Security modules in hledac/security/ not hledac/universal/security/ |

### Legacy Test Files
| File | Count | Rationale |
|------|-------|-----------|
| `tests/test_sprint_f193a_legacy_boundary.py` | 6 | Legacy boundary tests |
| `tests/test_sprint54.py` | 1 | Old sprint test |
| `tests/test_sprint62c.py` | 1 | Old sprint test |
| `tests/test_sprint64_transport_resolver.py` | 2 | Old sprint test |
| `tests/probe_f192g/*` | 4 | F192G probe tests |

## CI Health Checks

```bash
# Core imports must pass (use uv run for proper environment)
uv run python -c "from hledac.universal.runtime.sprint_scheduler import SprintScheduler; print('OK')"
uv run python -c "from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore; print('OK')"
uv run python -c "from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator; print('OK')"
```

All 3 pass ✅

Or use the CI script:
```bash
python scripts/ci_health_check.py
```

## Files Created

| File | Purpose |
|------|---------|
| `PERMANENTLY_SHIMMED.md` | Full documentation of 241 shimmed items |
| `IMPORT_HEALTH_DASHBOARD.md` | This dashboard |
| `import_categorization.json` | Categorization data |
| `analyze_imports.py` | Categorization script |
| `scripts/ci_health_check.py` | CI health check script |

## TOML Fixes Applied

Fixed `pyproject.toml`:
- Removed duplicate `]` closing osint-html array (lines 117-118)
- Removed duplicate `]` closing pq array (line 124)

## Next Steps

1. [x] broken_imports.json < 50 KB ✅ (14.3 KB)
2. [x] 3 core imports pass ✅
3. [ ] Fix remaining 40 wrong_internal_path items (optional, not blocking)
4. [ ] Add CI health check to Makefile or pytest conftest