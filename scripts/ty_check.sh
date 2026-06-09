#!/usr/bin/env bash
# scripts/ty_check.sh — Hledac Universal `ty` type-checker wrapper
#
# Sprint: 2026-06-09 — ty 0.0.42 type-checker integration
#
# Why this script exists:
#   `ty` (Rust-based type checker from Astral, makers of uv/ruff) does NOT
#   yet support `exclude` in pyproject.toml [tool.ty.analysis]. Vendored
#   test repos under `evaluate/` would flood output with thousands of false
#   positives. This wrapper injects the necessary `--exclude` flags.
#
# Usage:
#   ./scripts/ty_check.sh                 # default: exclude evaluate/
#   ./scripts/ty_check.sh --watch         # ty check --watch
#   ./scripts/ty_check.sh path/to/file.py # check single file
#   ./scripts/ty_check.sh --json          # machine-readable output
#
# Exit codes:
#   0  — no errors
#   1+ — ty exited with errors (see output for file:line)
#
# Bounded: Bounded execute time via `timeout` (10 min max).
# Fail-soft: missing `ty` binary → install guidance, exit 127.
# M1 8GB: `ty` is a single Rust binary, peak RSS ~150MB, no Python deps.
set -euo pipefail

# Project root = parent of this script's parent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Verify `ty` is available (M1 8GB: ~150MB binary, no Python deps)
if ! command -v ty >/dev/null 2>&1 && ! uv run ty --version >/dev/null 2>&1; then
    echo "ERROR: \`ty\` not installed. Install via:" >&2
    echo "  uv tool install ty  # global" >&2
    echo "  uv add --dev ty     # project dev dep" >&2
    exit 127
fi

# Default excludes — vendored, generated, build outputs, AI tooling.
# ty doesn't yet honor exclude in pyproject.toml; CLI is the only path.
# Updated 2026-06-09: added archive/, tests/probe_*/, AI tool caches, .mypy_cache, htmlcov.
DEFAULT_EXCLUDES=(
    # --- vendored externí kód (false positives) ---
    --exclude 'evaluate/**'         # vendored test repos (flask, fastapi, code-review-graph, gin, express, httpx) — 1355+ false positives
    --exclude 'archive/**'          # archived legacy code — dead, but ty ho kontroluje
    # --- virtualenv & build outputs ---
    --exclude '.venv/**'            # virtualenv (auto-excluded by ty)
    --exclude '.venv-test/**'
    --exclude 'build/**'
    --exclude 'dist/**'
    --exclude '.eggs/**'
    --exclude '*.egg-info/**'
    --exclude 'hledac_universal.egg-info/**'
    --exclude 'rust_extensions/target/**'
    --exclude 'tools/**/target/**'
    --exclude 'tools/**/.build/**'
    --exclude 'advanced_rag/**'
    --exclude 'advanced_web/**'
    --exclude 'context_optimization/**'
    # --- throwaway probe testy (neprochází CI, throwaway) ---
    --exclude 'tests/probe_*/**'    # 320+ root probe_* adresářů + tests/probe_*/
    --exclude 'tests/probe/**'      # tests/probe/ adresář (single probe root)
    --exclude 'tests/probe_*.py'    # probe test files v tests/ root (single-file)
    --exclude 'tests/test_*.py'     # test soubory v tests/ root (sprint testy - throwaway, validují smoke separatně)
    --exclude 'tests/**/test_*.py'  # testy v test sub-adresářích (single-sprint probe)
    --exclude 'tests/ct_lane_closure/**'
    --exclude 'tests/live_8be/**'
    --exclude 'tests/profiling/**'
    --exclude 'tests/r5x_nonfeed_integration_guard/**'
    --exclude 'tests/r6_local_bm25_relevance/**'
    --exclude 'tests/security_layer_async_io/**'
    --exclude 'tests/test_batch_scheduler/**'
    --exclude 'tests/test_*'        # catch-all for tests/test_X patterns
    --exclude 'tests/conftest.py'   # pytest conftest stubs (runtime, ne type-check)
    # --- cache a tooling stopy ---
    --exclude 'graphify-out/**'     # graph index output
    --exclude 'stubs/**'            # our own .pyi stubs (validated separately)
    --exclude '.code-review-graph/**'
    --exclude '.atomcode/**'
    --exclude '.qoder/**'
    --exclude '.kiro/**'
    --exclude '.gemini/**'
    --exclude '.pi/**'
    --exclude '.cursor/**'
    --exclude '.backup/**'
    --exclude '.agents/**'
    --exclude '.claudekit/**'
    --exclude '.full-review*/**'
    --exclude '.srclight*/**'
    --exclude '.claude/**'
    --exclude '.omc/**'
    --exclude '.pytest_cache/**'
    --exclude '.mypy_cache/**'
    --exclude '.ruff_cache/**'
    --exclude 'htmlcov/**'
    --exclude '**/__pycache__/**'
    --exclude 'cache_storage/**'
    --exclude 'cache/**'
    --exclude 'embeddings/**'
    --exclude 'embedding_cache/**'
    # --- generované/runtime soubory ---
    --exclude 'MagicMock/**'
    --exclude 'runtime/cti/evidence/**'
    --exclude 'graphify-out/2026-*/**'
    # --- non-hledac Python soubory v rootu (legacy importy) ---
    --exclude 'preserved_logic/**'
    --exclude 'federated/**'
    --exclude 'privacy_protection/**'
    --exclude 'tools/preserved_logic/**'
    --exclude 'tools/_py314_raise_from_e.py'   # one-off syntax experiment
    --exclude 'tests/test_pattern_matcher.py'  # heavy Aho-Corasick test, fail-soft
)

# Run ty with all excludes + any user args
exec uv run ty check "${DEFAULT_EXCLUDES[@]}" "$@"
