# Phase 4: CI/CD Pipeline & Operational Practices Review

## Executive Summary

**Status**: SIGNIFICANT GAPS

The project lacks formal CI/CD infrastructure, automated deployment pipelines, and comprehensive operational monitoring. Sprint F195C circuit breaker and M1 8GB memory constraints are inadequately addressed in operational practices. The focus has been on code correctness with insufficient attention to deployment, observability, and incident response.

---

## 1. CI/CD Pipeline Assessment

### 1.1 Build Automation

| Aspect | Finding | Severity |
|--------|---------|----------|
| CI Configuration | **No CI/CD found** - no GitHub Actions, GitLab CI, Jenkins, or equivalent | CRITICAL |
| Automated Tests | Manual pytest execution via PHASE_GATES.py tiering | HIGH |
| Pre-commit Hooks | `pre_commit_guard.py` only blocks "None" files - no lint/security checks | HIGH |
| Artifact Management | No binary artifact registry or versioned releases | MEDIUM |

**Evidence**:
- No `.github/workflows/` directory
- No `.gitlab-ci.yml`, `Jenkinsfile`, or equivalent
- `scripts/pre_commit_guard.py` only checks for "None" filename pattern
- Test execution documented in `PHASE_GATES.py` is manual

**Risk**: Without automated CI, code quality gates depend entirely on developer discipline. Sprint F195C changes (circuit breaker, MLX cache) have no regression protection.

### 1.2 Test Gates

| Gate | Duration | Location | Status |
|------|----------|----------|--------|
| probe_gate | <1s | `tests/probe_*/` | Defined in PHASE_GATES.py |
| ao_canary | ~5-10s | `tests/test_ao_canary.py` | Defined in PHASE_GATES.py |
| phase_gate | 10-60s | `tests/test_sprint*.py` | Manual execution |
| manual_only | Minutes | `tests/e2e_*.py` | Explicit opt-in |
| mega_suite | 10+ min | `tests/test_autonomous_orchestrator.py` (22k lines) | Never as default |

**Critical Gap**: No automated memory constraint validation in any test gate. M1 8GB thresholds are documented but not enforced.

**Recommendation**: Add probe gate for M1 memory validation:
```bash
# Example: memory_pressure_smoke.py
import psutil
assert psutil.virtual_memory().available > 1_500_000_000, "Insufficient headroom for M1 8GB"
```

---

## 2. Deployment Strategy

### 2.1 Current State

| Aspect | Finding | Severity |
|--------|---------|----------|
| Containerization | None - no Dockerfile or docker-compose | HIGH |
| Blue-Green Deploy | Not implemented | HIGH |
| Canary Deploy | Not implemented | HIGH |
| Rollback Capability | Not implemented | HIGH |
| Zero-Downtime | Not implemented | HIGH |

**Evidence**:
- No Dockerfile in repository
- `smoke_runner.py` provides pre-deployment smoke test but manual
- `mount_ramdisk.sh` handles scratch space but manual execution

**Operational Risk**: CRITICAL - Production deployments require manual process with no safety nets.

### 2.2 Deployment Entry Points

| Entry Point | Location | Authority |
|-------------|----------|----------|
| Canonical | `core.__main__:run_sprint()` | YES - documented |
| Smoke Runner | `smoke_runner.py` | DIAGNOSTIC ONLY - clearly documented |
| _run_sprint_mode | `__main__._run_sprint_mode()` | ALTERNATE - documented |

The codebase correctly identifies canonical vs. diagnostic paths. However, no deployment mechanism enforces canonical path usage.

---

## 3. Infrastructure as Code

### 3.1 Configuration Management

| Aspect | Finding | Severity |
|--------|---------|----------|
| Version Control | Config files tracked in git | LOW |
| IaC Tooling | None - no Terraform, CloudFormation, Pulumi | HIGH |
| Secret Management | .env mentioned in .gitignore but no vault | HIGH |
| Environment Parity | No enforcement between dev/staging/prod | HIGH |

**Evidence**:
- `tests/live_8be/searxng_local/settings.yml` - settings checked in
- `proxies.json` loaded from `DB_ROOT/config/proxies.json`
- Cache roots configured via environment variables (HLEDAC_CACHE_ROOT, HF_HOME, etc.)

### 3.2 IaC Gaps

- No infrastructure definition for MLX model download
- No RAM disk provisioning automation beyond mount script
- No network topology definition for Tor/SOCKS proxies

---

## 4. Monitoring & Observability

### 4.1 Current Monitoring

| Component | Implementation | Observability |
|-----------|----------------|---------------|
| Memory (UMA) | `utils/uma_budget.py` | `get_uma_snapshot()`, `get_uma_pressure_level()` |
| MLX Cache | `utils/mlx_cache.py` | `clear_mlx_cache()`, semaphore for concurrency |
| Circuit Breaker | `coordinators/fetch_coordinator.py` | Debug logs only |
| Flow Trace | `utils/flow_trace.py` | `trace_fetch_start/end()` |
| Telemetry | `_telemetry` dict in FetchCoordinator | In-memory only |

### 4.2 Circuit Breaker Observability Gap

**Severity**: HIGH

The circuit breaker (`get_blocked_domains()`) is:
- Logged at WARNING level when domain blocked
- Traced via `trace_fetch_end(url, "circuit_breaker", "circuit_open", 0.0)`
- **NOT exposed to external monitoring** - no metrics export, no Prometheus counters, no alerts

**Current state in `fetch_coordinator.py:411-419`**:
```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}
    # NOTE: Lines 416-419 are DEAD CODE - unreachable after return
```

**Recommendation**: Add circuit breaker metrics:
```python
# Add to FetchCoordinator
self._circuit_breaker_metrics = {
    'total_blocks': 0,
    'active_blocks': 0,
    'total_unblocks': 0,
}
```

### 4.3 MLX Cache Effectiveness Monitoring

**Severity**: HIGH

Current MLX cache (`utils/mlx_cache.py`):
- LRU cache with max 2 models
- Semaphore limits to 1 concurrent inference
- No visibility into cache hit/miss rates
- No wire/cache memory distinction in metrics

**Evidence** from `utils/mlx_cache.py:19-20`:
```python
_MLX_CACHE: OrderedDict[str, Tuple[Any, Any]] = OrderedDict()
_MLX_CACHE_MAX = 2
```

**Recommendation**: Add cache metrics to `mlx_cache.py`:
```python
_CACHE_HITS = 0
_CACHE_MISSES = 0

def get_cache_stats() -> dict:
    return {'hits': _CACHE_HITS, 'misses': _CACHE_MISSES, 'hit_rate': ...}
```

### 4.4 M1 8GB Memory Constraint Monitoring

**Severity**: HIGH

`utils/uma_budget.py` defines thresholds:
```python
_WARN_THRESHOLD_MB = 6_144   # 6.0 GB
_CRITICAL_THRESHOLD_MB = 6_656  # 6.5 GB
_EMERGENCY_THRESHOLD_MB = 7_168  # 7.0 GB
```

**Gaps**:
- No persistent storage of memory pressure events
- No alerting when approaching thresholds
- `mx.eval([])` barrier before `clear_cache()` not enforced in all paths
- Memory pressure callbacks not documented in GHOST_INVARIANTS.md

---

## 5. Incident Response

### 5.1 Emergency Purge - CRITICAL UNTESTED PATH

**Severity**: CRITICAL

`emergency_purge()` in `security/deep_research_security.py:246-248`:
- **No tests exist** for this critical security path
- Prior review flagged audit log deletion intent (compliance concern)
- No runbook or documented procedure for emergency scenarios

**Evidence from prior review**:
> "Audit Log Deletion in Emergency Purge" - Comment indicates intent to delete audit logs in emergency purge. Compliance violation and forensic evidence destruction risk.

### 5.2 Race Condition - _lightpanda_pool_started

**Severity**: HIGH

From `coordinators/fetch_coordinator.py:669-672`:
- Boolean guard not protected by lock in async context
- Completely untested concurrent scenario
- Could cause pool initialization race

### 5.3 Runbooks & Documentation

| Procedure | Status |
|-----------|--------|
| Circuit Breaker Recovery | Not documented |
| Memory Pressure Response | Not documented |
| MLX OOM Recovery | Not documented |
| emergency_purge Execution | Not documented |

---

## 6. Environment Management

### 6.1 Cache Root Configuration

**Location**: `tests/PHASE_GATES.py:pytest_configure()`

```python
# Sets before any imports:
HF_HOME, HF_HUB_CACHE, HF_DATASETS_CACHE, TRANSFORMERS_CACHE,
PYTORCH_TRANSFORERS_CACHE, PYTORCH_PRETRAINED_BERT_CACHE, TORCH_HOME,
XDG_CACHE_HOME, SENTENCE_TRANSFORMERS_HOME
```

**Gap**: Production environment configuration not documented. Cache root selection logic (`GHOST_RAMDISK` vs `HLEDAC_RUNTIME_ROOT`) is test-focused.

### 6.2 Environment Variable Schema

| Variable | Purpose | Validation |
|----------|---------|------------|
| HLEDAC_CACHE_ROOT | Primary cache location | None |
| HLEDAC_RUNTIME_ROOT | Runtime root | Fallback to ~/.hledac_fallback_ramdisk |
| GHOST_RAMDISK | RAM disk path | Idempotent mount |
| HF_HOME | HuggingFace cache | Set by PHASE_GATES |

**Gap**: No schema validation. No documentation of required vs. optional variables.

---

## 7. M1 8GB Operational Considerations

### 7.1 Memory Pressure Response

**Current Implementation**:
- `UmaWatchdog` in `utils/uma_budget.py` provides callbacks
- Pressure levels: normal → warn → critical → emergency
- No automatic action tied to pressure levels

**Recommendation**:
```python
# In uma_budget.py, add automatic actions:
if pressure_level == 'critical':
    trigger_mlx_cache_clear()
    reduce_fetch_concurrency()
elif pressure_level == 'emergency':
    trigger_aggressive_cleanup()
    alert_oncall()
```

### 7.2 MLX Cache Cleanup Enforcement

**Invariant from GHOST_INVARIANTS.md**:
```markdown
### Cleanup order: GC → eval barrier → clear_cache
The canonical MLX cleanup sequence (via `mlx_cleanup_sync()`):
1. `gc.collect()` — release Python refs to MLX objects
2. `mx.eval([])` — GPU queue drain barrier
3. `mx.metal.clear_cache()` — Metal cache release
```

**Gap**: No enforcement in CI. Tests don't verify cleanup order.

---

## 8. Summary of Findings

### Critical Issues (Must Address)

| ID | Category | Finding | Impact |
|----|----------|---------|--------|
| C1 | CI/CD | No automated CI pipeline | No regression protection for F195C |
| C2 | Monitoring | Circuit breaker has no metrics export | No observability into blocking behavior |
| C3 | Monitoring | MLX cache has no hit/miss metrics | Cannot measure cache effectiveness |
| C4 | Incident | emergency_purge() completely untested | Critical security path unvalidated |
| C5 | Deployment | No rollback capability | Cannot recover from failed deploy |

### High Priority Issues

| ID | Category | Finding | Impact |
|----|----------|---------|--------|
| H1 | CI/CD | No containerization | Manual deployment process |
| H2 | CI/CD | Pre-commit only blocks "None" files | No lint/security gates |
| H3 | Monitoring | Memory pressure has no auto-actions | Depends on manual intervention |
| H4 | IaC | No infrastructure definition | Environment drift likely |
| H5 | Ops | Race condition _lightpanda_pool_started untested | Potential crashes under load |

### Medium Priority Issues

| ID | Category | Finding | Impact |
|----|----------|---------|--------|
| M1 | Monitoring | Flow trace not integrated with external monitoring | Limited debugging capability |
| M2 | Env | No environment parity enforcement | Dev/prod gaps |
| M3 | Docs | No runbooks for circuit breaker recovery | Slower incident response |
| M4 | Memory | mx.eval([]) barrier not enforced in CI | Cleanup may be ineffective |

---

## 9. Recommendations

### Immediate Actions (This Sprint)

1. **Add circuit breaker metrics** to `FetchCoordinator._telemetry`:
   - `circuit_breaker.blocks`, `circuit_breaker.unblocks`, `circuit_breaker.active`
   - Export via existing flow_trace mechanism

2. **Document M1 8GB operational runbook**:
   - Memory thresholds and expected behavior
   - MLX cache cleanup sequence
   - Circuit breaker recovery steps

3. **Add memory smoke test to probe_gate**:
   ```python
   # tests/probe_gate/test_m1_memory.py
   def test_memory_headroom():
       mem = psutil.virtual_memory()
       assert mem.available > 1_500_000_000, "M1 8GB requires 1.5GB headroom"
   ```

### Short-term (Next Sprint)

4. **Add MLX cache metrics** to `mlx_cache.py`:
   - Cache hits/misses counter
   - Memory used by cache
   - Model load latency

5. **Add emergency_purge tests** (CRITICAL - currently zero coverage)

6. **Enhance pre-commit guard** to run linting and basic checks

### Long-term (Architecture)

7. **Implement CI/CD pipeline** (GitHub Actions recommended):
   - Probe gate on every PR
   - Phase gate on merge to main
   - Memory-constrained test runner for M1

8. **Add containerization** for production deployment:
   - Dockerfile with proper memory limits
   - docker-compose for local development

9. **Implement metrics export**:
   - Prometheus counters for circuit breaker
   - Memory pressure alerting
   - MLX cache hit rate dashboard

---

## 10. Verification Commands

To validate current state:

```bash
# Check for CI configuration
ls -la .github/workflows/ 2>/dev/null || echo "NO CI CONFIG FOUND"

# Run probe gate (should be <1s)
pytest tests/probe_*/ -m probe_gate -q --collect-only

# Run memory validation
python -c "import psutil; m=psutil.virtual_memory(); print(f'Available: {m.available/1e9:.1f}GB')"

# Verify circuit breaker exists
grep -n "get_blocked_domains" coordinators/fetch_coordinator.py

# Check emergency_purge test coverage
grep -r "emergency_purge" tests/ || echo "NO TESTS FOR emergency_purge"
```

---

*Review completed: 2026-04-23*
*Phase: 04B - DevOps & Operational Practices*
*Reviewer: Claude Code (autonomous agent)*
