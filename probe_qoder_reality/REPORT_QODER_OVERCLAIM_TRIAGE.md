# QODER Overclaim Triage Report — F206AM

**Matrix:** `probe_qoder_reality/qoder_reality_matrix.json`
**Scanned:** 88 docs, 518 modules, 122 overclaims
**Verdict breakdown:** CANONICAL=1 · ACTIVE_RUNTIME=11 · ACTIVE_CAPABILITY=139 · TEST_ONLY=184 · DEPRECATED=17 · LEGACY=6 · DONOR=3 · DONOR_OR_OPTIONAL=28 · STORAGE_AUTHORITY=38 · SECURITY_CRITICAL=30 · TRANSPORT_AUTHORITY=20

---

## Severity Summary

| Severity | Count | Notes |
|----------|-------|-------|
| **HIGH** | 4 | layers/stealth_layer.py ×2, benchmarks/benchmark_pipeline.py ×2 |
| **MEDIUM** | 17 | TEST_ONLY × 15, DEPRECATED × 2 |
| **LOW** | 101 | DEPRECATED/DONOR/DONOR_OR_OPTIONAL/LEGACY × 101 |

---

## Overclaim Taxonomy (6 Groups)

All 122 overclaims follow a single structural pattern: **doc uses assertive runtime language (canonical/production/wired/active runtime) but referenced module has a non-assertive verdict.**

### Group 1 — Canonical Overclaims (84 overclaims, 45 docs)
Doc uses "canonical" for a module that is not CANONICAL_OWNER.

- **Actual verdicts:** DEPRECATED (22), TEST_ONLY (31), LEGACY (7), DONOR (8), DONOR_OR_OPTIONAL (16)
- **Claim pattern:** `Uses 'canonical' language but module is <VERDICT>`
- **Affected modules (top hits):** `autonomous_orchestrator.py` (DEPRECATED, 9 docs), `REAL_ARCHITECTURE.md` (DEPRECATED, 4 docs), `layers/stealth_layer.py` (DONOR_OR_OPTIONAL, 3 docs), `orchestrator/global_scheduler.py` (LEGACY, 3 docs)

**Suggested wording change (per pattern):**
- DEPRECATED module: replace "canonical" → "deprecated (superseded by <SUCCESSOR>)"
- TEST_ONLY module: replace "canonical" → "test/benchmark (not production)"
- LEGACY module: replace "canonical" → "legacy"
- DONOR/DONOR_OR_OPTIONAL module: replace "canonical" → "optional donor"
- `autonomous_orchestrator.py` specifically: already deprecated, docs should reference `core/__main__.py::run_sprint()` as canonical entry point

---

### Group 2 — Production Overclaims (36 overclaims, 22 docs)
Doc uses "production" for a module that is not in the production write path.

- **Actual verdicts:** DEPRECATED (9), TEST_ONLY (21), LEGACY (2), DONOR_OR_OPTIONAL (4)
- **Claim pattern:** `Uses 'production' language but module is <VERDICT>`

**Suggested wording change (per pattern):**
- TEST_ONLY module: replace "production" → "test harness" or "benchmark harness"
- DEPRECATED module: replace "production" → "deprecated (superseded)"
- LEGACY/DONOR_OR_OPTIONAL: replace "production" → "optional donor"

---

### Group 3 — Wired Overclaims (8 overclaims, 5 docs)
Doc uses "wired" for a module that is not wired into the active pipeline.

- **Actual verdicts:** TEST_ONLY (4), DEPRECATED (2), DONOR_OR_OPTIONAL (1), DONOR (1)
- **Claim pattern:** `Uses 'wired' language but module is <VERDICT>`
- **Affected docs:** `Deployment and Operations.md` (wired+DEPRECATED/DONOR ×3), `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Capability Probes (6b-7i).md` (wired+TEST_ONLY), `Utilities and Helpers/MLX Integration.md` (wired+TEST_ONLY ×2)

**Suggested wording change:**
- TEST_ONLY: replace "wired" → "benchmarked by" or "validated by"
- DEPRECATED: replace "wired" → "was wired (deprecated)"
- DONOR_OR_OPTIONAL: replace "wired" → "available via optional donor"

---

### Group 4 — Active Runtime Overclaims (4 overclaims, 2 docs)
Doc uses "active runtime" for a module that is LEGACY or DONOR.

- **Claim pattern:** `Uses 'active runtime' language but module is <VERDICT>`
- **Affected docs:** `Runtime Management/Sprint Lifecycle Management.md`
- **Affected modules:** `orchestrator/phase_controller.py` (LEGACY), `runtime/windup_engine.py` (DONOR)

**Suggested wording change:**
- LEGACY: replace "active runtime" → "legacy (superseded by SprintScheduler)"
- DONOR: replace "active runtime" → "optional donor advisory"

---

### Group 5 — Security Overclaims (0 in HIGH/MEDIUM)
No HIGH/MEDIUM security overclaims. All 30 SECURITY_CRITICAL verdicts in the matrix are correctly documented.

---

### Group 6 — Storage/Write-Path Overclaims (0 in HIGH/MEDIUM)
No HIGH/MEDIUM storage/write-path overclaims. STORAGE_AUTHORITY verdicts are correctly scoped.

---

## Top 20 Doc Patches

Sorted: HIGH first, then MEDIUM by affected_modules_count descending.

### Patches 1–4: HIGH Severity

**Patch 1** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md` | `canonical` → `DONOR_OR_OPTIONAL` | `layers/stealth_layer.py` | severity=HIGH | should_patch_now=true

> **Affected modules:** 15 (layers/stealth_layer.py, layers/temporal_signal_layer.py, layers/temporal_signal_store.py, layers/temporal_signal_runtime.py, and 11 more)
>
> **Current:** Describes stealth_layer and temporal_signal_* as canonical probe infrastructure.
> **Should say:** "Stealth and temporal-signal layers are optional donor components — not wired into the production pipeline."
> **Why:** DONOR_OR_OPTIONAL verdict means these are opt-in, not canonical. Misleading for new engineers.

---

**Patch 2** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Domain Probes.md` | `production` → `DONOR_OR_OPTIONAL` | `layers/stealth_layer.py` | severity=HIGH | should_patch_now=true

> Same doc as Patch 1, second claim. Both canonical and production language used.
> **Should say:** "Stealth/temporal-signal are optional donor probe surfaces — not production-critical."
> **Why:** High confusion risk; stealth is often assumed to be always-on.

---

**Patch 3** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Benchmark and Performance Probes.md` | `canonical` → `TEST_ONLY` | `benchmarks/benchmark_pipeline.py` | severity=HIGH | should_patch_now=true

> **Affected modules:** 12 (benchmark_pipeline.py, benchmark_sprint_probe.py, e2e_canonical_benchmark.py, e2e_compare.py, research_effectiveness.py, and 7 more)
>
> **Current:** Describes benchmarks as canonical performance measurement.
> **Should say:** "Benchmark suite is a test-only harness — not part of production measurement."
> **Why:** 12 test/benchmark files grouped under "canonical" is misleading for capacity planning.

---

**Patch 4** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Benchmark and Performance Probes.md` | `wired` → `TEST_ONLY` | `benchmarks/benchmark_pipeline.py` | severity=HIGH | should_patch_now=true

> Same doc as Patch 3, second claim.
> **Should say:** "Benchmark probes are driven by the benchmark harness — not wired into live pipelines."
> **Why:** HIGH affected_modules_count=12 confirms broad mischaracterization.

---

### Patches 5–14: MEDIUM Severity (selected highest-impact)

**Patch 5** | `Brain Engines/Hermes3 Engine.md` | `canonical` → `TEST_ONLY` | `tests/test_sprint75/test_speculative_decoding.py` | severity=MEDIUM | affected=4 | should_patch_now=true

> **Current:** Mentions speculative decoding as canonical Hermes3 capability.
> **Should say:** "Speculative decoding is validated by the test harness (tests/test_sprint75/) — not a production feature."
> **Why:** Hermes3 engine is ACTIVE_CAPABILITY; speculative decoding test coverage ≠ production feature.

---

**Patch 6** | `Brain Engines/Hermes3 Engine.md` | `production` → `TEST_ONLY` | `tests/test_sprint75/test_speculative_decoding.py` | severity=MEDIUM | affected=4 | should_patch_now=true

> Same doc, second claim. Remove both canonical and production claims simultaneously.

---

**Patch 7** | `Core Architecture/Design Patterns and Architectural Principles.md` | `canonical` → `DONOR_OR_OPTIONAL` | `infrastructure/plugin_manager.py` | severity=MEDIUM | affected=4 | should_patch_now=true

> **Current:** plugin_manager listed as canonical architectural pattern.
> **Should say:** "Plugin manager is an optional donor extension point."
> **Why:** infrastructure/plugin_manager has DONOR_OR_OPTIONAL verdict; not wired by default.

---

**Patch 8** | `Project Overview/Introduction.md` | `canonical` → `DEPRECATED` | `REAL_ARCHITECTURE.md` | severity=MEDIUM | affected=4 | should_patch_now=true

> **Current:** References REAL_ARCHITECTURE.md as canonical architecture doc.
> **Should say:** "REAL_ARCHITECTURE.md is deprecated — see the active architecture docs under Core Architecture/."
> **Why:** REAL_ARCHITECTURE.md is DEPRECATED (confusing for onboarding).

---

**Patch 9** | `Project Overview/Introduction.md` | `production` → `DEPRECATED` | `REAL_ARCHITECTURE.md` | severity=MEDIUM | affected=4 | should_patch_now=true

> Same doc, second claim.

---

**Patch 10** | `Testing and Quality Assurance/Integration and End-to-End Testing.md` | `canonical` → `TEST_ONLY` | `tests/conftest.py` | severity=MEDIUM | affected=9 | should_patch_now=true

> **Current:** conftest.py referenced as canonical test infrastructure.
> **Should say:** "conftest.py is a test-only pytest harness — not part of production code."
> **Why:** tests/conftest.py is TEST_ONLY with 9 affected entries.

---

**Patch 11** | `Testing and Quality Assurance/Probe Testing System/Test Execution and Orchestration.md` | `canonical` → `TEST_ONLY` | `tests/conftest.py` | severity=MEDIUM | affected=6 | should_patch_now=true

> Same pattern as Patch 10; different doc.

---

**Patch 12** | `Testing and Quality Assurance/Testing and Quality Assurance.md` | `canonical` → `TEST_ONLY` | `tests/conftest.py` | severity=MEDIUM | affected=9 | should_patch_now=true

> Same pattern; third doc claiming conftest.py as canonical.

---

**Patch 13** | `Knowledge Layer/DuckDB Shadow Store.md` | `canonical` → `TEST_ONLY` | `tests/test_sprint8ao_duckdb_sidecar.py` | severity=MEDIUM | affected=4 | should_patch_now=true

> **Current:** Describes DuckDB sidecar test as canonical storage.
> **Should say:** "DuckDB sidecar is validated by the test harness — production write path is duckdb_store.py."
> **Why:** canonical write path is duckdb_store.py, not the test sidecar.

---

**Patch 14** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Production Readiness Probes (8a-8z).md` | `canonical` → `TEST_ONLY` | `tests/probe_8b/test_sprint_8b.py` | severity=MEDIUM | affected=5 | should_patch_now=true

> **Current:** Describes 8a-8z probes as production readiness gates.
> **Should say:** "8a-8z probes validate sprint readiness in the test harness."
> **Why:** probe files are TEST_ONLY, not production gates.

---

### Patches 15–20: MEDIUM Severity (remaining)

**Patch 15** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Production Readiness Probes (8a-8z).md` | `production` → `TEST_ONLY` | `tests/probe_8b/test_sprint_8b.py` | severity=MEDIUM | affected=5 | should_patch_now=true

---

**Patch 16** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Capability Probes (6b-7i).md` | `canonical` → `TEST_ONLY` | `tests/probe_6b/test_apple_fm_probe.py` | severity=MEDIUM | affected=10 | should_patch_now=true

> **Current:** Describes 6b-7i probes as canonical capability coverage.
> **Should say:** "6b-7i probes are test-only capability validation — M1-specific probes not wired in standard runs."

---

**Patch 17** | `Testing and Quality Assurance/Probe Testing System/Probe Categories and Classification/Specialized Capability Probes (6b-7i).md` | `wired` → `TEST_ONLY` | `tests/probe_6b/test_apple_fm_probe.py` | severity=MEDIUM | affected=10 | should_patch_now=true

---

**Patch 18** | `Core Architecture/Authority Model and Entry Points/Boot Hygiene and Teardown Management.md` | `canonical` → `TEST_ONLY` | `tests/test_sprint8an_hygiene.py` | severity=MEDIUM | affected=5 | should_patch_now=true

> **Current:** Describes hygiene tests as canonical boot hygiene.
> **Should say:** "Boot hygiene is validated by the test harness (tests/test_sprint8an_hygiene.py, tests/probe_8ai/)."

---

**Patch 19** | `Utilities and Helpers/MLX Integration.md` | `canonical` → `TEST_ONLY` | `benchmarks/m1_embedding_streaming.py` | severity=MEDIUM | affected=6 | should_patch_now=true

> **Current:** Describes m1_embedding_streaming as canonical MLX streaming.
> **Should say:** "MLX embedding streaming is benchmarked in benchmarks/m1_embedding_streaming.py — not a production streaming path."

---

**Patch 20** | `Utilities and Helpers/MLX Integration.md` | `wired` → `TEST_ONLY` | `benchmarks/m1_embedding_streaming.py` | severity=MEDIUM | affected=6 | should_patch_now=true

---

## LOW Severity — Batch Fix Recommendation

The remaining 101 LOW overclaims follow the same patterns (canonical/production for DEPRECATED/DONOR/DONOR_OR_OPTIONAL modules). They should be fixed but are lower priority.

**Batch fix strategy by module:** Fix `autonomous_orchestrator.py` references (DEPRECATED, appears in 9 docs) and `REAL_ARCHITECTURE.md` references (DEPRECATED, appears in 4 docs) as a single pass — these 2 modules alone clear ~13 of the 101 LOW overclaims.

**Remaining LOW → MEDIUM candidates (review before patching):**
- `Deployment and Operations.md` — 12 overclaims (DEPRECATED/DONOR × 3 patterns × 4 verdicts each = 12)
- `API Reference/Core APIs.md` — 6 overclaims (autonomous_orchestrator + orchestrator_integration)
- `Project Overview/Architecture Overview.md` — 6 overclaims
- `Project Overview/Project Overview.md` — 6 overclaims
- `Project Overview/Core Features.md` — 4 overclaims

---

## Wording Change Reference Table

| Current claim | Actual verdict | Replace with |
|---|---|---|
| "canonical" | DEPRECATED | "deprecated (superseded)" |
| "canonical" | TEST_ONLY | "test/benchmark harness" |
| "canonical" | LEGACY | "legacy" |
| "canonical" | DONOR | "optional donor" |
| "canonical" | DONOR_OR_OPTIONAL | "optional donor" |
| "production" | DEPRECATED | "deprecated (superseded)" |
| "production" | TEST_ONLY | "test harness" |
| "production" | LEGACY | "legacy advisory" |
| "production" | DONOR_OR_OPTIONAL | "optional donor" |
| "wired" | TEST_ONLY | "benchmarked by" |
| "wired" | DEPRECATED | "was wired (deprecated)" |
| "wired" | DONOR_OR_OPTIONAL | "available via optional donor" |
| "active runtime" | LEGACY | "legacy (superseded)" |
| "active runtime" | DONOR | "optional donor advisory" |

---

## Test Coverage

- `tests/probe_qoder_reality_f206ag/test_qoder_reality.py` — reads matrix, validates schema, enforces output cap, no production imports, no network calls
