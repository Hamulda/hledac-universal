# Documentation Review Findings — Sprint F195 (03B)

## Scope
Modified files from sprint F195 integration in `hledac/universal/`:
`autonomous_orchestrator.py`, `brain/model_manager.py`, `brain/prompt_bandit.py`, `brain/prompt_cache.py`, `coordinators/fetch_coordinator.py`, `coordinators/memory_coordinator.py`, `intelligence/*`, `layers/*`, `legacy/*`, `security/*`, `tools/*`, `utils/*`

---

## Executive Summary

**Overall Documentation Health: INCOMPLETE**

| Dimension | Status | Severity |
|-----------|--------|----------|
| Inline documentation | Partially complete | Medium |
| API documentation | Minimal | High |
| Architecture documentation | GHOST_INVARIANTS.md adequate; REAL_ARCHITECTURE.md accurate | Medium |
| README completeness | No README in scope | Medium |
| Accuracy vs implementation | Several critical gaps | Critical |
| Changelog/migration guides | None found | High |

---

## 1. Inline Documentation

### 1.1 [CRITICAL] `autonomous_orchestrator.py` — Facade Confusion Unresolved

**Finding:** The facade module has extensive docstring explaining its NON-canonical status, but this creates a fundamental documentation paradox: the module is documented as "NOT canonical" while simultaneously being imported by `__init__.py` and tests.

**What is missing:**
- The `__donor_capability_list__` is a plain list, not actual documentation
- No migration path is documented (just a deprecation warning)
- The authority chain in docstring is correct but the module still exports 65+ names

**Specific recommendation:**
```markdown
Add a MIGRATION_STATUS table:
| Symbol | Status | Migrate To | Blocking Issue |
|--------|--------|------------|----------------|
| FullyAutonomousOrchestrator | DEPRECATED | runtime/sprint_scheduler.py | smoke_runner.py, 8 probe tests |
```

**Severity:** Critical

---

### 1.2 [HIGH] `coordinators/fetch_coordinator.py` — Circuit Breaker Write Path Undocumented

**Finding:** The Sprint F195C circuit breaker implementation (`_record_domain_failure()`, `get_blocked_domains()`) is implemented but:
1. No docstring explains the circuit breaker algorithm (exponential backoff formula)
2. The constants `_failure_threshold = 3` and `_cooldown_seconds = 60` are magic numbers
3. The `get_blocked_domains()` method at line 411-414 has dead code after `return` statement (line 416-419)

**Specific recommendation:**
```python
async def _record_domain_failure(self, domain: str) -> None:
    """
    Record domain failure and apply circuit breaker if threshold exceeded.

    Circuit breaker algorithm:
    - Threshold: 3 consecutive failures trigger blocking
    - Backoff: min(60 * 2^(failures-3), 3600) seconds (exponential, capped at 1h)
    - Unblock: automatic after backoff expires

    Args:
        domain: FQDN that failed
    """
```

**Severity:** High

---

### 1.3 [MEDIUM] `brain/model_manager.py` — Memory Guard Logic Obscured

**Finding:** The `_MODEL_SIZES_GB` dictionary documents model sizes, but the threshold calculation at line 72:
```python
threshold = _model_max_rss_gb - model_size
```
is not explained. The comment says "M1 8GB" but the actual math (5.5 - 2.0 = 3.5GB) is unexplained.

**What is missing:**
- Why `_model_max_rss_gb = 5.5` (not 6.0 or 7.0)?
- What happens if multiple models are loaded simultaneously?
- The "1-model-at-a-time" claim is in the module docstring but not enforced in code

**Severity:** Medium

---

### 1.4 [MEDIUM] `brain/prompt_cache.py` — Trigram Embedding Rationale Missing

**Finding:** The trigram-based embedding (lines 71-83) uses a 256-dimensional space but:
1. No explanation why 256 dimensions
2. No explanation why character trigrams (not word n-grams)
3. The cosine similarity threshold for cache hits is not documented

**Severity:** Medium

---

### 1.5 [MEDIUM] `security/deep_research_security.py` — Custom Crypto Audit Trail Missing

**Finding:** The module imports `quantum_safe.py` which contains SpikingNeuralNetwork custom crypto (flagged as HIGH risk in Phase 2 security review), but there is no mention of this risk in the docstring.

**What is missing:**
- Security review notes referencing the Phase 2 findings
- Explicit warning about `SpikingNeuralNetwork` custom crypto
- Guidance on when NOT to use this module

**Severity:** Medium

---

### 1.6 [MEDIUM] `security/digital_ghost_detector.py` — Reference to Undocumented Source

**Finding:** The docstring references `next_gen_enhancements.py` comments as the source of requirements, but this file is not documented anywhere and appears to be an internal comment chain being promoted to documentation.

**Specific recommendation:**
Either:
1. Remove references to `next_gen_enhancements.py` 
2. Or add it to the architecture docs with actual file location

**Severity:** Medium

---

### 1.7 [LOW] `utils/simple_bottleneck_profiler.py` — Phase Number Inconsistency

**Finding:** The docstring says "Phase 11.5 Task 3" but the sprint system uses F195 naming convention. This appears to be legacy documentation that was not updated during sprint renumbering.

**Severity:** Low

---

## 2. API Documentation

### 2.1 [HIGH] `coordinators/fetch_coordinator.py` — No Request/Response Schemas

**Finding:** The `FetchCoordinator` class has no API documentation for:
- `step()` method signature and return type
- `start()` async initialization contract
- `_resolve_host_ips()` public/private contract
- Telemetry dictionary shape

**What is missing:**
```python
async def step(self, num_items: int = 5) -> FetchStepResult:
    """
    Execute one fetch coordination step.

    Args:
        num_items: Maximum URLs to process (default 5, max bounded by config)

    Returns:
        FetchStepResult with:
        - evidence_ids: list[str] — bounded to MAX_EVIDENCE_IDS_PER_STEP
        - urls_fetched: int
        - stop_signal: bool
        - stop_reason: str | None

    Raises:
        Nothing — always returns FetchStepResult (fail-safe)
    """
```

**Severity:** High

---

### 2.2 [HIGH] `utils/uma_budget.py` — Snapshot Schema Undocumented

**Finding:** `get_uma_snapshot()` returns a dict but the schema is undocumented. The `is_uma_warn()` / `is_uma_critical()` / `is_uma_emergency()` functions have inconsistent documentation.

**What is missing:**
- Return type annotation says `dict` but actual keys are undocumented
- The `is_uma_emergency()` function is listed in `__all__` but not in the module docstring API section
- `format_uma_budget_report()` output format not specified

**Severity:** High

---

### 2.3 [MEDIUM] `brain/model_manager.py` — Public API Unclear

**Finding:** The module has internal functions (`_check_rss_before_load`, `_verify_rss_after_unload`) but it's unclear which are public API vs internal.

**Specific recommendation:**
Add `__all__` export list to clearly delineate public API.

**Severity:** Medium

---

## 3. Architecture Documentation

### 3.1 [MEDIUM] GHOST_INVARIANTS.md — Missing Sprint F195C Updates

**Finding:** GHOST_INVARIANTS.md was last updated for "Sprint F195B (2026-04-22)" but Sprint F195C added:
- Circuit breaker write path in FetchCoordinator
- `is_uma_emergency()` function export
- `get_blocked_domains()` method

**What is missing:**
- Section "Sprint F195C: Circuit Breaker" with invariants
- Update the "Last updated" timestamp

**Severity:** Medium

---

### 3.2 [MEDIUM] REAL_ARCHITECTURE.md — Dead Code Paths Not Flagged

**Finding:** REAL_ARCHITECTURE.md correctly identifies dormant modules but does not flag the dead code path in `get_blocked_domains()` (line 416-419 after return statement in `FetchCoordinator`).

**Specific recommendation:**
Add a "Dead Code发现了" section or annotate the module table with:
```markdown
| coordinators/fetch_coordinator.py | Circuit breaker write path dead code at line 416-419 |
```

**Severity:** Medium

---

### 3.3 [LOW] GHOST_INVARIANTS.md — Async Hygiene Rule #3 Misleading

**Finding:** Rule #3 says "use `async_getaddrinfo()` instead of `socket.getaddrinfo`" but:
1. `FetchCoordinator._resolve_host_ips()` is explicitly marked synchronous
2. The rule doesn't mention that DNS resolution IS permitted via `asyncio.to_thread` for the sync variant

**Specific recommendation:**
Clarify: "For synchronous contexts (e.g., `FetchCoordinator._resolve_host_ips()`), use `asyncio.to_thread` with `socket.getaddrinfo`. For async contexts, use `async_getaddrinfo()`."

**Severity:** Low

---

## 4. README Completeness

### 4.1 [HIGH] No README in Scope Directory

**Finding:** There is no README.md in `hledac/universal/` covering:
- Setup instructions
- Development workflow
- Sprint naming convention (F###)
- How to run tests
- M1 8GB memory constraints

**What exists:**
- `AGENTS.md` (project-level)
- `GHOST_INVARIANTS.md` (invariant reference)
- `REAL_ARCHITECTURE.md` (architecture truth)

**Recommendation:**
Create `hledac/universal/README.md` with at minimum:
1. Quick start (3 steps)
2. Sprint naming convention table
3. Memory constraints summary
4. Link to GHOST_INVARIANTS.md and REAL_ARCHITECTURE.md

**Severity:** High

---

## 5. Accuracy Against Implementation

### 5.1 [CRITICAL] `FetchCoordinator.get_blocked_domains()` Dead Code

**Finding:** Lines 411-419 in `fetch_coordinator.py`:
```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    # Exponential backoff retry (Fix 2)  # <- DEAD CODE
    self._base_retry_delay = 1.0         # <- DEAD CODE
    self._max_retries = 3                # <- DEAD CODE
    self._max_backoff_delay = 30.0       # <- DEAD CODE
```

This dead code contradicts the GHOST_INVARIANTS.md rule about "bare `except` forbidden" (Rule #5) — this is dead code that should be removed, not a documentation issue but it indicates documentation didn't catch this.

**Severity:** Critical (but is code, not documentation)

---

### 5.2 [HIGH] `autonomous_orchestrator.py` — Authority Chain Documentation Wrong

**Finding:** The docstring claims:
```
.. authority_chain::
    autonomous_orchestrator.py (THIS FACADE, NON_CANONICAL)
        → legacy/autonomous_orchestrator.py (IMPLEMENTATION TRUTH)
```

But `legacy/autonomous_orchestrator.py` itself delegates to `core/__main__.py::run_sprint()` (the ACTUAL canonical owner per REAL_ARCHITECTURE.md). The authority chain in the docstring is incomplete.

**Specific recommendation:**
Update the authority chain:
```
autonomous_orchestrator.py (THIS FACADE, NON_CANONICAL)
    → legacy/autonomous_orchestrator.py (LEGACY TRUTH)
    → core.__main__.run_sprint() (PRODUCTION CANONICAL OWNER)
    → runtime.sprint_scheduler.SprintScheduler (PRODUCTION ORCHESTRATOR)
```

**Severity:** High

---

### 5.3 [MEDIUM] `utils/mlx_cache.py` — Cache Limit Values Inconsistent

**Finding:** GHOST_INVARIANTS.md states:
```
Metal cache limit is 2.5 GiB
mx.metal.set_cache_limit(2_684_354_560)  # = 2_500_000_000 approximately
```

But `utils/mlx_cache.py` does not contain these constants. The actual values are in `brain/model_manager.py` or imported from elsewhere. This creates a documentation-implementation gap.

**Specific recommendation:**
Add to `utils/mlx_cache.py`:
```python
# Sprint 8T: MLX Metal Memory Limits (M1 8GB UMA)
# GHOST_INVARIANT: These values MUST match GHOST_INVARIANTS.md
_MLX_CACHE_LIMIT = 2_684_354_560  # 2.5 GiB - Metal cache
_MLX_WIRED_LIMIT = 2_684_354_560  # 2.5 GiB - Wired memory
```

**Severity:** Medium

---

### 5.4 [MEDIUM] `utils/uma_budget.py` — `is_uma_emergency()` Missing from Module Docstring

**Finding:** The module docstring API section (lines 21-27) lists:
- `is_uma_critical()` — documented
- `is_uma_warn()` — documented
- `is_uma_emergency()` — NOT in the API list but IS in `__all__`

**Specific recommendation:**
Add `is_uma_emergency()` to the module docstring API section.

**Severity:** Medium

---

## 6. Changelog / Migration Guides

### 6.1 [HIGH] No Migration Guide for Sprint F195

**Finding:** Sprint F195 made significant changes:
1. `autonomous_orchestrator.py` became a facade (breaking change for direct importers)
2. Circuit breaker added to `FetchCoordinator`
3. `is_uma_emergency()` function added

There is no `CHANGELOG.md` or migration guide documenting:
- What changed
- What imports broke
- How to migrate
- When the facade will be removed

**Recommendation:**
Create `SPRINT_F195_MIGRATION.md` covering:
| Change | Before | After | Migration |
|--------|--------|-------|-----------|
| autonomous_orchestrator.py | Direct imports | Import from legacy/ or runtime/ | Update imports to `runtime.sprint_scheduler` |
| FetchCoordinator circuit breaker | No blocking | Domain blocking after 3 failures | No action needed, transparent |
| is_uma_emergency() | Did not exist | Added | Use for emergency threshold |

**Severity:** High

---

## Summary Table

| ID | Severity | Area | Finding |
|----|----------|------|---------|
| 1.1 | Critical | Inline | Facade documentation paradox — module documented as NON-canonical but still exported |
| 1.2 | High | Inline | Circuit breaker algorithm undocumented in FetchCoordinator |
| 2.1 | High | API | FetchCoordinator.step() has no request/response schema |
| 2.2 | High | API | uma_budget.py snapshot dict schema undocumented |
| 3.1 | Medium | Arch | GHOST_INVARIANTS.md not updated for F195C |
| 4.1 | High | README | No README.md in scope directory |
| 5.1 | Critical | Accuracy | get_blocked_domains() dead code (code bug, docs didn't catch) |
| 5.2 | High | Accuracy | autonomous_orchestrator authority chain incomplete |
| 6.1 | High | Changelog | No migration guide for F195 breaking changes |

---

## Recommendations (Priority Order)

1. **[CRITICAL]** Fix `get_blocked_domains()` dead code — remove lines 416-419
2. **[CRITICAL]** Update `autonomous_orchestrator.py` authority chain documentation
3. **[HIGH]** Add circuit breaker algorithm docstring to `FetchCoordinator._record_domain_failure()`
4. **[HIGH]** Create `SPRINT_F195_MIGRATION.md`
5. **[HIGH]** Add request/response schema for `FetchCoordinator.step()`
6. **[HIGH]** Document `uma_budget.py` snapshot dict schema
7. **[HIGH]** Update GHOST_INVARIANTS.md with F195C circuit breaker invariants
8. **[HIGH]** Create `hledac/universal/README.md`
9. **[MEDIUM]** Add `__all__` to `brain/model_manager.py`
10. **[MEDIUM]** Add `is_uma_emergency()` to `uma_budget.py` module docstring API list
11. **[MEDIUM]** Add MLX cache limit constants to `utils/mlx_cache.py` matching GHOST_INVARIANTS.md
12. **[MEDIUM]** Add security warning to `deep_research_security.py` about custom crypto
13. **[LOW]** Clarify async hygiene Rule #3 in GHOST_INVARIANTS.md about DNS resolution
14. **[LOW]** Remove "Phase 11.5" reference in `simple_bottleneck_profiler.py`

---

*Review completed: 2026-04-23*
*Reviewer: Documentation Architecture Review (03B)*
*Files reviewed: 18 files across sprint F195 scope*
