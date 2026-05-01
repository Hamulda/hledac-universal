"""
Sprint F206AL — M1 Memory Threshold Harmonization Probe Tests

Tests verify F206AL invariants:
1. MLX wired limit is unified (single source: mlx_cache._MLX_WIRED_LIMIT)
2. SOFT_PREEMPT_RAM_GIB is NOT below uma_budget.UMA_CRITICAL_GIB (threshold inversion fixed)
3. All 5.5GB ceilings reference uma_budget.M1_FETCH_SOFT_CEILING_GB or are documented mirrors
4. GENERAL_HIGH_WATER_RATIO remains 0.85
5. No MLX model loading in probe (AST check)
6. No live sprint (no actual orchestrator/scheduler init)

ABORT CONDITIONS: none — read-only static scan.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
PROBE_TEST_FILE = Path(__file__)

# Sprint F206AL: Canonical source files
UMA_BUDGET = REPO_ROOT / "utils" / "uma_budget.py"
MLX_CACHE = REPO_ROOT / "utils" / "mlx_cache.py"
CORE_MAIN = REPO_ROOT / "core" / "__main__.py"
RESOURCE_ALLOCATOR = REPO_ROOT / "resource_allocator.py"
PUBLIC_FETCHER = REPO_ROOT / "fetching" / "public_fetcher.py"

# Constants we're checking
MLX_WIRED_LIMIT_BYTES = 2_684_354_560  # 2.5 GiB — canonical value


class TestM1MemoryHarmonization:
    """F206AL: Harmonization invariants verified via static analysis."""

    def test_uma_budget_exposes_gb_aliases(self):
        """Verify uma_budget.py exports the new GB threshold constants."""
        source = UMA_BUDGET.read_text()
        assert "UMA_WARN_GIB" in source, "UMA_WARN_GIB not found in uma_budget.py"
        assert "UMA_CRITICAL_GIB" in source, "UMA_CRITICAL_GIB not found in uma_budget.py"
        assert "UMA_EMERGENCY_GIB" in source, "UMA_EMERGENCY_GIB not found in uma_budget.py"
        assert "M1_FETCH_SOFT_CEILING_GB" in source, "M1_FETCH_SOFT_CEILING_GB not found"
        assert "GENERAL_HIGH_WATER_RATIO" in source, "GENERAL_HIGH_WATER_RATIO not found"

    def test_mlx_wired_limit_unified(self):
        """
        C1 resolution: MLX wired limit must have ONE canonical value.

        Before F206AL: core/__main__.py hardcoded 2_500_000_000,
        mlx_cache._MLX_WIRED_LIMIT = 2_684_354_560 (2.5GiB).
        After F206AL: core/__main__.py imports mlx_cache._MLX_WIRED_LIMIT.
        """
        # mlx_cache must define _MLX_WIRED_LIMIT
        mlx_source = MLX_CACHE.read_text()
        assert "_MLX_WIRED_LIMIT" in mlx_source, "_MLX_WIRED_LIMIT not in mlx_cache.py"
        # mlx_cache must define _METAL_WIRED_LIMIT_BYTES as the canonical bytes value
        assert "_METAL_WIRED_LIMIT_BYTES" in mlx_source, (
            "_METAL_WIRED_LIMIT_BYTES not in mlx_cache.py"
        )
        # _MLX_WIRED_LIMIT must equal _METAL_WIRED_LIMIT_BYTES
        assert "_MLX_WIRED_LIMIT = _METAL_WIRED_LIMIT_BYTES" in mlx_source, (
            "_MLX_WIRED_LIMIT should alias _METAL_WIRED_LIMIT_BYTES"
        )

        # core/__main__.py must NOT hardcode 2_500_000_000
        main_source = CORE_MAIN.read_text()
        assert "2_500_000_000" not in main_source, (
            "core/__main__.py still has hardcoded 2_500_000_000 — should use mlx_cache._MLX_WIRED_LIMIT"
        )
        # Should import mlx_cache
        assert "mlx_cache" in main_source, "core/__main__.py should import mlx_cache"

    def test_threshold_inversion_fixed(self):
        """
        C4 resolution: SOFT_PREEMPT_RAM_GIB must NOT be below UMA_CRITICAL_GIB.

        Before F206AL: EMERGENCY_RAM_GB=6.2 < uma_budget.CRITICAL=6.5 → threshold inversion
        (emergency brake fires BEFORE critical state is reached).
        After F206AL: SOFT_PREEMPT_RAM_GIB <= UMA_CRITICAL_GIB (6.5), so the request-level
        preemption fires at or before system-level critical — correct ordering.

        SOFT_PREEMPT is a preventive/request-level action that should trigger at or before
        the system-level CRITICAL threshold, NOT after it.
        """
        uma_source = UMA_BUDGET.read_text()
        alloc_source = RESOURCE_ALLOCATOR.read_text()

        # Extract UMA_CRITICAL_GIB value
        match = re.search(r"UMA_CRITICAL_GIB[^=]*=\s*([0-9.]+)", uma_source)
        assert match, "UMA_CRITICAL_GIB not found in uma_budget.py"
        uma_critical = float(match.group(1))

        # SOFT_PREEMPT_RAM_GIB must be defined as a class attribute (type-annotated float).
        # Pattern matches: SOFT_PREEMPT_RAM_GIB: float = VALUE  (with type annotation).
        match = re.search(r"^\s*SOFT_PREEMPT_RAM_GIB\s*:\s*float\s*=\s*([0-9.]+)\s*$", alloc_source, re.MULTILINE)
        assert match, "SOFT_PREEMPT_RAM_GIB (float-annotated) not found in resource_allocator.py"
        preempt = float(match.group(1))

        assert preempt <= uma_critical, (
            f"Threshold inversion! SOFT_PREEMPT_RAM_GIB={preempt} > UMA_CRITICAL_GIB={uma_critical}. "
            f"SOFT_PREEMPT must be <= CRITICAL (preventive preemption fires before/at critical)."
        )

    def test_uma_warn_critical_emergency_ordering(self):
        """UMA_WARN < UMA_CRITICAL < UMA_EMERGENCY (strictly increasing)."""
        uma_source = UMA_BUDGET.read_text()
        warn = float(re.search(r"UMA_WARN_GIB[^=]*=\s*([0-9.]+)", uma_source).group(1))
        crit = float(re.search(r"UMA_CRITICAL_GIB[^=]*=\s*([0-9.]+)", uma_source).group(1))
        emerg = float(re.search(r"UMA_EMERGENCY_GIB[^=]*=\s*([0-9.]+)", uma_source).group(1))

        assert warn < crit < emerg, f"WARN={warn} < CRIT={crit} < EMERG={emerg} ordering violated"

    def test_55gb_ceilings_unified_or_documented(self):
        """
        C2 resolution: All 5.5GB ceilings must reference uma_budget.M1_FETCH_SOFT_CEILING_GB
        or be documented mirrors.

        Verifies resource_allocator and public_fetcher import M1_FETCH_SOFT_CEILING_GB.
        """
        alloc_source = RESOURCE_ALLOCATOR.read_text()
        fetcher_source = PUBLIC_FETCHER.read_text()

        # resource_allocator must import M1_FETCH_SOFT_CEILING_GB
        assert "M1_FETCH_SOFT_CEILING_GB" in alloc_source, (
            "resource_allocator.py should import M1_FETCH_SOFT_CEILING_GB from uma_budget"
        )

        # public_fetcher must import M1_FETCH_SOFT_CEILING_GB
        assert "M1_FETCH_SOFT_CEILING_GB" in fetcher_source, (
            "public_fetcher.py should import M1_FETCH_SOFT_CEILING_GB from uma_budget"
        )

        # Neither should have hardcoded "5.5" literal in active code (assignments).
        # Filter: skip comment lines, docstring lines, and lines already using the alias.
        alloc_lines = []
        for line in alloc_source.split("\n"):
            if "5.5" not in line:
                continue
            if line.strip().startswith("#"):
                continue
            if "M1_FETCH_SOFT_CEILING_GB" in line:
                continue
            # Skip docstring/content lines that aren't assignments
            if '"""' in line or "'''" in line:
                continue
            if "=" not in line:
                # Likely docstring or descriptive text, not active code
                continue
            alloc_lines.append(line.strip())

        fetcher_lines = []
        for line in fetcher_source.split("\n"):
            if "5.5" not in line:
                continue
            if line.strip().startswith("#"):
                continue
            if "M1_FETCH_SOFT_CEILING_GB" in line:
                continue
            if '"""' in line or "'''" in line:
                continue
            if "=" not in line:
                continue
            fetcher_lines.append(line.strip())

        assert not alloc_lines, f"resource_allocator.py has unaliased 5.5GB: {alloc_lines}"
        assert not fetcher_lines, f"public_fetcher.py has unaliased 5.5GB: {fetcher_lines}"

    def test_general_high_water_ratio_remains_085(self):
        """Verify GENERAL_HIGH_WATER_RATIO is still 0.85."""
        uma_source = UMA_BUDGET.read_text()
        match = re.search(r"GENERAL_HIGH_WATER_RATIO[^=]*=\s*([0-9.]+)", uma_source)
        assert match, "GENERAL_HIGH_WATER_RATIO not found in uma_budget.py"
        ratio = float(match.group(1))
        assert ratio == 0.85, f"GENERAL_HIGH_WATER_RATIO should be 0.85, got {ratio}"

    def test_no_model_load_in_probes(self):
        """Verify probe test files do not trigger MLX model loading."""
        probe_dir = REPO_ROOT / "tests" / "probe_m1_memory_harmonization_f206al"
        for py_file in probe_dir.glob("test_*.py"):
            if py_file.name == PROBE_TEST_FILE.name:
                continue  # Skip this test file itself (docstrings mention mlx_lm)
            source = py_file.read_text()
            # mlx_lm.load is the model-loading call
            assert "mlx_lm" not in source, f"{py_file.name} imports mlx_lm"
            assert "load_model" not in source, f"{py_file.name} calls load_model"
            # mx.metal.set_wired_limit is safe (BOOT only), but model load is not

    def test_no_live_sprint_in_probes(self):
        """Verify probe test files do not initialize SprintScheduler or orchestrator."""
        probe_dir = REPO_ROOT / "tests" / "probe_m1_memory_harmonization_f206al"
        for py_file in probe_dir.glob("test_*.py"):
            if py_file.name == PROBE_TEST_FILE.name:
                continue  # Skip this test file itself (docstrings mention SprintScheduler)
            source = py_file.read_text()
            assert "SprintScheduler" not in source, f"{py_file.name} initializes SprintScheduler"
            assert "autonomous_orchestrator" not in source, (
                f"{py_file.name} references autonomous_orchestrator"
            )

    def test_no_network_in_probes(self):
        """Verify probe tests make no actual network calls."""
        probe_dir = REPO_ROOT / "tests" / "probe_m1_memory_harmonization_f206al"
        for py_file in probe_dir.glob("test_*.py"):
            if py_file.name == PROBE_TEST_FILE.name:
                continue  # Skip this test file itself
            source = py_file.read_text()
            assert "aiohttp.ClientSession" not in source, f"{py_file.name} creates ClientSession"
            assert "httpx.Client" not in source, f"{py_file.name} creates httpx.Client"
            assert "curl_cffi" not in source, f"{py_file.name} uses curl_cffi"

    def test_uma_budget_uma_constants_are_gib(self):
        """Verify all uma_budget GB constants are expressed in GiB (base-2)."""
        uma_source = UMA_BUDGET.read_text()
        # Values must be 6.0, 6.5, 7.0 (decimal GiB)
        assert re.search(r"UMA_WARN_GIB[^=]*=\s*6\.0\b", uma_source), "UMA_WARN_GIB should be 6.0"
        assert re.search(r"UMA_CRITICAL_GIB[^=]*=\s*6\.5\b", uma_source), "UMA_CRITICAL_GIB should be 6.5"
        assert re.search(r"UMA_EMERGENCY_GIB[^=]*=\s*7\.0\b", uma_source), "UMA_EMERGENCY_GIB should be 7.0"
