# =============================================================================
# Hledac Universal — Build & Run Makefile
# F350M-R | Python 3.14 | macOS ARM64
# =============================================================================
#
# Usage:
#   make help              — show this help
#   make probe             — run all probes (hermetic, no external deps)
#   make probe-ci          — CI probe run (fail-fast)
#   make benchmark         — run all benchmarks
#   make audit             — run all audit tools
#   make audit-ci          — CI audit run (BLE001 + guards)
#   make smoke             — run smoke tests
#   make test              — pytest suite
#   make test-ci           — CI pytest (cov + probe skip)
#   make sprint-gate       — run sprint preflight gates
#   make scripts           — run one-shot scripts
#
# CI Entry Point: Only Makefile targets consumed by .github/workflows/
#

PYTHON  := uv run --no-sync python
PYTEST  := uv run --no-sync pytest

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help:
	@echo "Hledac Universal — Available targets"
	@echo ""
	@echo "=== CI (primary entry point for GitHub Actions) ==="
	@echo "  make probe-ci       CI probe run — fail-fast"
	@echo "  make audit-ci       CI audit — BLE001 + guards"
	@echo "  make test-ci        CI pytest — full suite, skip probes"
	@echo ""
	@echo "=== Development ==="
	@echo "  make probe          Run all probes (hermetic)"
	@echo "  make benchmark       Run all benchmarks"
	@echo "  make audit          Run all audit tools"
	@echo "  make smoke          Run smoke tests"
	@echo "  make test           Run pytest suite"
	@echo "  make sprint-gate    Run sprint preflight gates"
	@echo "  make scripts        Run one-shot dev scripts"
	@echo ""
	@echo "=== Individual ==="
	@echo "  make ble-audit      BLE001 bare-except audit"
	@echo "  make ci-guard       Root scripts guard"
	@echo "  make flag-smoke     Flag smoke runner"
	@echo "  make probe-q1       Q1 arch rules probe tests"

# =============================================================================
# CI Targets — consumed by .github/workflows/
# =============================================================================

.PHONY: probe-ci
probe-ci:
	@echo "[CI] Running probes..."
	$(PYTHON) -m tools.probe.probe_f214int_interpreter_pool || exit 1
	$(PYTHON) -m tools.probe.probe_f214m_execution_optimizer_backpressure || exit 1
	$(PYTHON) -m tools.probe.probe_f214opt314_runtime_optimizations || exit 1
	$(PYTHON) -m tools.probe.probe_f214r_annotationlib_introspection || exit 1
	$(PYTHON) -m tools.probe.probe_f214t_tstring_safe_renderer || exit 1
	$(PYTHON) -m tools.probe.probe_f214zstd2_transient_artifacts || exit 1
	$(PYTHON) -m tools.probe.probe_r0_nonfeed_reality_lock || exit 1
	$(PYTHON) -m tools.probe.probe_f214h_content_miner_backpressure || exit 1
	$(PYTEST) tests/probe_q1_arch_rules/ -x --timeout=30 -q || exit 1
	$(PYTHON) -m ruff_ext --ci || exit 1

.PHONY: audit-ci
audit-ci:
	@echo "[CI] Running audits..."
	$(PYTHON) -m tools.audit.ble_audit --ci
	$(PYTHON) -m tools.audit.ci_root_scripts_guard
	$(PYTHON) -m tools.audit.ci_tst001_guard

.PHONY: test-ci
test-ci:
	$(PYTEST) tests/ \
		--cov=hledac.universal \
		--cov-fail-under=70 \
		--ignore="tests/probe_" \
		--ignore="evaluate/" \
		-m "not mlx and not live and not integration and not parity" \
		-x --timeout=30 -q

# =============================================================================
# Probe (hermetic tests for sprint internals)
# =============================================================================

.PHONY: probe
probe:
	@echo "[probe] Running all probes..."
	$(PYTHON) -m tools.probe.probe_f214int_interpreter_pool
	$(PYTHON) -m tools.probe.probe_f214m_execution_optimizer_backpressure
	$(PYTHON) -m tools.probe.probe_f214opt314_runtime_optimizations
	$(PYTHON) -m tools.probe.probe_f214r_annotationlib_introspection
	$(PYTHON) -m tools.probe.probe_f214t_tstring_safe_renderer
	$(PYTHON) -m tools.probe.probe_f214zstd2_transient_artifacts
	$(PYTHON) -m tools.probe.probe_r0_nonfeed_reality_lock
	$(PYTHON) -m tools.probe.probe_f214h_content_miner_backpressure
	$(PYTEST) tests/probe_q1_arch_rules/ --timeout=30 -q

.PHONY: probe-q1
probe-q1:
	@echo "[probe-q1] Running Q1 arch rules probe..."
	$(PYTEST) tests/probe_q1_arch_rules/ -x --timeout=30 -v

# =============================================================================
# Benchmark
# =============================================================================

.PHONY: benchmark
benchmark:
	@echo "[benchmark] Running benchmarks..."
	$(PYTHON) -m tools.benchmark.bench_f214_python314_runtime
	$(PYTHON) -m tools.benchmark.bench_gc_314_runtime
	$(PYTHON) -m tools.benchmark.bench_m1_runtime_gates
	$(PYTHON) -m tools.benchmark.bench_py314_jit

# =============================================================================
# Audit
# =============================================================================

.PHONY: audit
audit:
	@echo "[audit] Running all audits..."
	$(PYTHON) -m tools.audit.async_compat_audit
	$(PYTHON) -m tools.audit.audit_eager_imports
	$(PYTHON) -m tools.audit.audit_flags
	$(PYTHON) -m tools.audit.audit_reality_index
	$(PYTHON) -m tools.audit.audit_try_except
	$(PYTHON) -m tools.audit.bounded_queue_audit
	$(PYTHON) -m tools.audit.windup_authority_audit
	$(PYTHON) -m tools.audit.check_dependency_profiles
	$(PYTHON) -m tools.audit.ble_audit
	$(PYTHON) -m tools.audit.ci_root_scripts_guard
	$(PYTHON) -m tools.audit.ci_tst001_guard
	$(PYTHON) -m tools.audit.codehealth_guard
	$(PYTHON) -m tools.audit.live_kpi_extraction_guard
	$(PYTHON) -m tools.audit.live_measurement_extraction_guard

# =============================================================================
# Smoke
# =============================================================================

.PHONY: smoke
smoke:
	@echo "[smoke] Running smoke tests..."
	$(PYTHON) -m tools.smoke.flag_smoke_runner
	$(PYTHON) -m tools.scripts.model_stack_smoke
	$(PYTHON) -m tools.scripts.smoke_llm_candidate

# =============================================================================
# Sprint Gates (preflight readiness checks)
# =============================================================================

.PHONY: sprint-gate
sprint-gate:
	@echo "[sprint-gate] Running preflight gates..."
	$(PYTHON) -m tools.sprint_gate.core_readiness_gate
	$(PYTHON) -m tools.sprint_gate.cp314_wheel_gate
	$(PYTHON) -m tools.sprint_gate.capability_kpi_dashboard
	$(PYTHON) -m tools.sprint_gate.f231_artifact_inventory
	$(PYTHON) -m tools.sprint_gate.f234_nonfeed_diagnostic_preflight
	$(PYTHON) -m tools.sprint_gate.final_prelive_readiness
	$(PYTHON) -m tools.sprint_gate.prelive_decision_gate
	$(PYTHON) -m tools.sprint_gate.prelive_one_button_gate
	$(PYTHON) -m tools.sprint_gate.qoder_reality_check
	$(PYTHON) -m tools.sprint_gate.replay_research_loop
	$(PYTHON) -m tools.sprint_gate.research_quality_score
	$(PYTHON) -m tools.sprint_gate.runtime_authority_probe
	$(PYTHON) -m tools.sprint_gate.live_artifact_triage
	$(PYTHON) -m tools.sprint_gate.live_kpi_responsibility_index
	$(PYTHON) -m tools.sprint_gate.live_memory_preflight
	$(PYTHON) -m tools.sprint_gate.live_multisource_validator
	$(PYTHON) -m tools.sprint_gate.live_result_sanity
	$(PYTHON) -m tools.sprint_gate.prelive_artifact_cockpit
	$(PYTHON) -m tools.sprint_gate.prelive_artifact_pack

# =============================================================================
# Scripts (one-shot dev tools)
# =============================================================================

.PHONY: scripts
scripts:
	@echo "[scripts] Running one-shot scripts..."
	$(PYTHON) -m tools.scripts.ci_health_check
	$(PYTHON) -m tools.scripts.smoke_llm_candidate
	$(PYTHON) -m tools.scripts.model_stack_smoke
	$(PYTHON) -m tools.scripts.verify_imports
	$(PYTHON) -m tools.scripts.embedding_backend_check
	$(PYTHON) -m tools.scripts.check_msgspec_migration
	$(PYTHON) -m tools.scripts.check_dspy
	$(PYTHON) -m tools.scripts.check_torrc
	$(PYTHON) -m tools.scripts.tor_health_check
	$(PYTHON) -m tools.scripts.compile_dspy_programs
	$(PYTHON) -m tools.scripts.dspy_compile
	$(PYTHON) -m tools.scripts.export_bge_to_coreml
	$(PYTHON) -m tools.scripts.extract_nonfeed_seeds
	$(PYTHON) -m tools.scripts.benchmark_rust_vs_python
	$(PYTHON) -m tools.scripts.score_corroboration
	$(PYTHON) -m tools.scripts.pre_commit_guard

# =============================================================================
# Test
# =============================================================================

.PHONY: test
test:
	$(PYTEST) tests/ -x --timeout=30 -q

# =============================================================================
# Individual tool shortcuts
# =============================================================================

.PHONY: ble-audit
ble-audit:
	$(PYTHON) -m tools.audit.ble_audit

.PHONY: ci-guard
ci-guard:
	$(PYTHON) -m tools.audit.ci_root_scripts_guard

.PHONY: flag-smoke
flag-smoke:
	$(PYTHON) -m tools.smoke.flag_smoke_runner

.PHONY: live-preflight
live-preflight:
	$(PYTHON) -m tools.sprint_gate.live_memory_preflight
	$(PYTHON) -m tools.sprint_gate.live_kpi_responsibility_index

.PHONY: prelive
prelive:
	$(PYTHON) -m tools.sprint_gate.prelive_artifact_cockpit
	$(PYTHON) -m tools.sprint_gate.prelive_artifact_pack
	$(PYTHON) -m tools.sprint_gate.live_artifact_triage
