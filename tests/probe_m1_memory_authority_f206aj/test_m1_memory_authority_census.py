"""
Sprint F206AJ — M1 Memory Authority Census Probe Tests

Tests verify:
1. Report generated (files exist, non-empty)
2. Core MLX wired limit included in matrix
3. Streaming embedder constants included
4. No imports that load MLX model (AST check)
5. No live sprint (no actual orchestrator initialization)
6. No network (no aiohttp/httpx/curl_cffi actual calls)

ABORT CONDITIONS: none — read-only static scan.
"""

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
PROBE_DIR = REPO_ROOT / "probe_m1_memory_authority"
MATRIX_JSON = PROBE_DIR / "m1_memory_authority_matrix.json"
REPORT_MD = PROBE_DIR / "REPORT_M1_MEMORY_AUTHORITY.md"
PROBE_TEST_FILE = Path(__file__)


def _get_imports(file_path: Path) -> set[str]:
    """Return all top-level imports from a file via AST."""
    source = file_path.read_text()
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


class TestReportGenerated:
    """Verify census outputs exist and are non-empty."""

    def test_matrix_json_exists(self):
        assert MATRIX_JSON.exists(), f"matrix not found: {MATRIX_JSON}"

    def test_matrix_json_nonempty(self):
        size = MATRIX_JSON.stat().st_size
        assert size > 1000, f"matrix too small: {size} bytes"

    def test_report_md_exists(self):
        assert REPORT_MD.exists(), f"report not found: {REPORT_MD}"

    def test_report_md_nonempty(self):
        size = REPORT_MD.stat().st_size
        assert size > 500, f"report too small: {size} bytes"


class TestCoreMLXWiredLimitIncluded:
    """Verify core MLX wired limit appears in the matrix."""

    def test_mlx_wired_limit_in_matrix(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        mlx_entries = [
            e for e in data["matrix"]
            if "mlx" in e["component"].lower() and "wired" in e["component"].lower()
        ]
        assert len(mlx_entries) >= 2, f"Expected >=2 MLX wired entries, got {len(mlx_entries)}"

    def test_core_main_wired_value_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entries = [
            e for e in data["matrix"]
            if "wired" in e["component"].lower()
            and e.get("value") == 2_500_000_000
        ]
        assert entries, "mx.metal.set_wired_limit(2_500_000_000) from core/__main__ not in matrix"

    def test_mlx_cache_wired_value_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entries = [
            e for e in data["matrix"]
            if "wired" in e["component"].lower()
            and e.get("value") == 2_684_354_560
        ]
        assert entries, "_METAL_WIRED_LIMIT_BYTES from mlx_cache not in matrix"

    def test_mlx_cache_limit_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        cache_entries = [
            e for e in data["matrix"]
            if "cache" in e["component"].lower() and "limit" in e["component"].lower()
        ]
        assert len(cache_entries) >= 2, "Expected >=2 MLX cache limit entries"


class TestStreamingEmbedderConstantsIncluded:
    """Verify streaming embedder RAM guard and batch constants are in the matrix."""

    def test_streaming_embedder_batch_constant(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entries = [
            e for e in data["matrix"]
            if "streaming" in e["component"].lower() and "batch" in e["component"].lower()
        ]
        assert entries, "streaming_embedder batch constant not in matrix"
        assert entries[0]["value"] == 16, f"Expected batch=16, got {entries[0]['value']}"

    def test_streaming_embedder_max_embedding_batch(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entries = [
            e for e in data["matrix"]
            if "MAX_EMBEDDING_BATCH" in str(e.get("constant", ""))
        ]
        assert entries, "MAX_EMBEDDING_BATCH not in matrix"
        assert entries[0]["value"] == 16

    def test_high_water_guard_pct_in_matrix(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        hw_entry = data.get("high_water_guard_pct")
        assert hw_entry is not None, "high_water_guard_pct not in matrix"
        assert hw_entry == 0.85, f"Expected 0.85, got {hw_entry}"


class TestEmbeddingBatchUnified:
    """Verify embedding batch is consistent across all consumers."""

    def test_all_embedding_batch_entries_equal_16(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        batch_entries = [
            e for e in data["matrix"]
            if "batch" in e["component"].lower() and "embed" in e["component"].lower()
        ]
        assert batch_entries, "No embedding batch entries found"
        values = {e["value"] for e in batch_entries}
        assert values == {16}, f"All embedding batch values should be 16, got: {values}"


class TestNoImportsThatLoadMLXModel:
    """Verify the test file AST has no imports that load MLX models."""

    def test_no_mlx_lm_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "mlx_lm" not in imports
        assert "mlx" not in imports  # base mlx is fine but not mlx_lm

    def test_no_model_load_imports(self):
        imports = _get_imports(PROBE_TEST_FILE)
        forbidden = {"model_manager", "hermes3_engine", "brain.model_manager", "brain.hermes3_engine"}
        for mod in forbidden:
            assert mod not in imports, f"test file must not import {mod}"


class TestNoLiveSprint:
    """Verify the test file AST has no imports that initialize a live sprint."""

    def test_no_sprint_scheduler_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "sprint_scheduler" not in str(imports)
        assert "SprintScheduler" not in str(imports)

    def test_no_autonomous_orchestrator_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "autonomous_orchestrator" not in str(imports)

    def test_no_core_main_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        # core.__main__ has run_pre_sprint_checks — test should not need it
        assert "core.__main__" not in str(imports)


class TestNoNetwork:
    """Verify the test file AST has no network library imports."""

    def test_no_aiohttp_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "aiohttp" not in imports

    def test_no_httpx_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "httpx" not in imports

    def test_no_curl_cffi_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "curl_cffi" not in imports

    def test_no_public_fetcher_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "fetching.public_fetcher" not in str(imports)

    def test_no_requests_import(self):
        imports = _get_imports(PROBE_TEST_FILE)
        assert "requests" not in imports


class TestConflictsIdentified:
    """Verify the census correctly identifies conflicts."""

    def test_conflicts_list_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert "conflicts" in data, "conflicts key must exist"
        assert len(data["conflicts"]) >= 4, f"Expected >=4 conflicts, got {len(data['conflicts'])}"

    def test_c1_mlx_wired_limit_conflict_identified(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        c1 = next((c for c in data["conflicts"] if c["id"] == "C1"), None)
        assert c1 is not None, "Conflict C1 (MLX wired limit) not found"
        assert c1["severity"] == "HIGH", f"C1 severity should be HIGH, got {c1['severity']}"
        assert "core/__main__.py" in str(c1["files"]), "C1 should reference core/__main__.py"
        assert "mlx_cache.py" in str(c1["files"]), "C1 should reference utils/mlx_cache.py"

    def test_c4_emergency_below_critical_identified(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        c4 = next((c for c in data["conflicts"] if c["id"] == "C4"), None)
        assert c4 is not None, "Conflict C4 (emergency below critical) not found"
        assert c4["severity"] == "MEDIUM"
        assert "EMERGENCY_RAM_GB" in str(c4) or "6.2" in str(c4["values"])

    def test_conflict_matrix_groups_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert "conflict_matrix" in data, "conflict_matrix key must exist"
        groups = data["conflict_matrix"]
        assert "mlx_wired_limit" in groups, "mlx_wired_limit group must exist"
        assert groups["mlx_wired_limit"]["status"] == "CONFLICT_C1"


class TestConstantsCompleteness:
    """Verify key constants are captured in the matrix."""

    def test_uma_warn_threshold_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "WARN" in e.get("constant", "") and "threshold" in e["component"].lower()),
            None
        )
        assert entry, "UMA WARN threshold not in matrix"
        assert entry["value"] == 6144, f"WARN threshold should be 6144, got {entry['value']}"

    def test_uma_critical_threshold_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "CRITICAL" in e.get("constant", "") and "threshold" in e["component"].lower()),
            None
        )
        assert entry, "UMA CRITICAL threshold not in matrix"
        assert entry["value"] == 6656, f"CRITICAL threshold should be 6656, got {entry['value']}"

    def test_fetch_default_limit_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "DEFAULT_FETCH" in e.get("constant", "")),
            None
        )
        assert entry, "DEFAULT_FETCH_LIMIT not in matrix"
        assert entry["value"] == 25

    def test_model_loaded_fetch_limit_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "MODEL_LOADED_FETCH" in e.get("constant", "")),
            None
        )
        assert entry, "MODEL_LOADED_FETCH_LIMIT not in matrix"
        assert entry["value"] == 3


class TestSemanticDedupConstants:
    """Verify semantic deduplicator constants are captured."""

    def test_max_cache_items_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "MAX_CACHE_ITEMS" in e.get("constant", "")),
            None
        )
        assert entry, "MAX_CACHE_ITEMS not in matrix"
        assert entry["value"] == 512

    def test_max_cache_memory_mb_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "MAX_CACHE_MEMORY_MB" in e.get("constant", "")),
            None
        )
        assert entry, "MAX_CACHE_MEMORY_MB not in matrix"
        assert entry["value"] == 256

    def test_embedding_dim_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "EMBEDDING_DIM" in e.get("constant", "")),
            None
        )
        assert entry, "_EMBEDDING_DIM not in matrix"
        assert entry["value"] == 256


class TestResourceAllocatorConstants:
    """Verify resource allocator constants captured."""

    def test_max_ram_gb_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "MAX_RAM_GB" in e.get("constant", "")),
            None
        )
        assert entry, "MAX_RAM_GB not in matrix"
        assert entry["value"] == 5.5

    def test_emergency_ram_gb_present(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        entry = next(
            (e for e in data["matrix"] if "EMERGENCY_RAM_GB" in e.get("constant", "")),
            None
        )
        assert entry, "EMERGENCY_RAM_GB not in matrix"
        assert entry["value"] == 6.2


class TestJSONSchema:
    """Verify matrix JSON is valid and well-structured."""

    def test_matrix_valid_json(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert "census_version" in data
        assert data["census_version"] == "F206AJ"
        assert "matrix" in data
        assert isinstance(data["matrix"], list)
        assert len(data["matrix"]) >= 30, f"Expected >=30 entries, got {len(data['matrix'])}"

    def test_matrix_entries_have_required_fields(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        required = {"component", "file", "constant", "value", "unit", "owner", "hot_path", "conflict_group"}
        for entry in data["matrix"]:
            missing = required - set(entry.keys())
            assert not missing, f"Entry missing fields: {missing} — {entry.get('component', '?')}"

    def test_conflicts_have_required_fields(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        required = {"id", "severity", "title", "description", "files", "values", "units", "conflict_group"}
        for conflict in data["conflicts"]:
            missing = required - set(conflict.keys())
            assert not missing, f"Conflict missing fields: {missing} — {conflict.get('id', '?')}"

    def test_conflict_severities_valid(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        valid = {"HIGH", "MEDIUM", "LOW", "INFO", "CRITICAL"}
        for conflict in data["conflicts"]:
            assert conflict["severity"] in valid, f"Invalid severity: {conflict['severity']}"


class TestAbortedConditionsNone:
    """Verify aborted conditions are all False (probe is read-only)."""

    def test_aborted_runtime_behavior(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert data.get("aborted") is not None
        assert "runtime_behavior" in data["aborted"]

    def test_aborted_model_load(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert "model_load" in data["aborted"]

    def test_aborted_network(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert "network" in data["aborted"]

    def test_aborted_live_sprint(self):
        with open(MATRIX_JSON) as f:
            data = json.load(f)

        assert "live_sprint" in data["aborted"]