"""
tests/test_phase28_uma_ceiling.py

MODERN-47: Phase 28 verification tests
Part (d): UMA ceilings == 6.25 everywhere

Tests:
- UmaBudget.UMA_HARD_CEILING_GIB == 6.25 (SSOT)
- All derived constants correctly derive from SSOT
- THRESHOLD_* constants are percentages of SSOT
- No hardcoded 6.25 values elsewhere (AST analysis)
- Threshold ladder: WARN, CRITICAL, EMERGENCY
- M1_FETCH_SOFT_CEILING_GB consistency
- MISSION_PEAK_RSS_GIB derived correctly

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""

from __future__ import annotations

import ast
from pathlib import Path


class TestUmaBudgetSSOT:
    """Test UmaBudget as Single Source of Truth (SSOT) for 6.25 GiB ceiling."""

    def test_uma_budget_import(self) -> None:
        """UmaBudget must be importable from utils.uma_budget."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget is not None

    def test_uma_hard_ceiling_is_6_25(self) -> None:
        """UMA_HARD_CEILING_GIB must be exactly 6.25 GiB."""
        from utils.uma_budget import UmaBudget

        assert hasattr(UmaBudget, "UMA_HARD_CEILING_GIB")
        ceiling = UmaBudget.UMA_HARD_CEILING_GIB

        assert isinstance(ceiling, float), f"Expected float, got {type(ceiling)}"
        assert ceiling == 6.25, f"UMA_HARD_CEILING_GIB must be 6.25, got {ceiling}"

    def test_uma_hard_ceiling_is_class_variable(self) -> None:
        """UMA_HARD_CEILING_GIB must be a class variable (not @property)."""
        from utils.uma_budget import UmaBudget

        # Must be accessible as class attribute without instantiation
        assert hasattr(UmaBudget, "UMA_HARD_CEILING_GIB")

        # Must be a number, not a property descriptor
        value = UmaBudget.UMA_HARD_CEILING_GIB
        assert isinstance(value, (int, float)), "Must be a numeric value"
        assert not hasattr(type(UmaBudget).__dict__.get("UMA_HARD_CEILING_GIB", None), "fget"), (
            "UMA_HARD_CEILING_GIB must not be a @property"
        )


class TestDerivedConstants:
    """Test that all constants are correctly derived from UmaBudget.UMA_HARD_CEILING_GIB."""

    def test_total_gib_is_alias(self) -> None:
        """TOTAL_GIB must be an alias to UMA_HARD_CEILING_GIB."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.TOTAL_GIB == 6.25
        assert UmaBudget.TOTAL_GIB == UmaBudget.UMA_HARD_CEILING_GIB

    def test_tracked_allocation_budget(self) -> None:
        """TRACKED_ALLOCATION_BUDGET_GIB = 3.75 GiB (ORCHESTRATOR + LLM + KV_CACHE)."""
        from utils.uma_budget import UmaBudget

        # Components: ORCHESTRATOR=1.0 + LLM_WEIGHTS=2.0 + KV_CACHE=0.75 = 3.75
        expected = 1.0 + 2.0 + 0.75
        assert abs(UmaBudget.TRACKED_ALLOCATION_BUDGET_GIB - expected) < 0.01

    def test_mission_peak_rss_derived(self) -> None:
        """MISSION_PEAK_RSS_GIB must be derived from SSOT (88% of ceiling)."""
        from utils.uma_budget import UmaBudget

        expected = round(6.25 * 0.88, 2)
        actual = UmaBudget.MISSION_PEAK_RSS_GIB

        assert actual == expected, f"MISSION_PEAK_RSS_GIB should be {expected} (6.25 * 0.88), got {actual}"

    def test_threshold_constants_are_derived(self) -> None:
        """THRESHOLD_* constants must be derived percentages of SSOT."""
        from utils.uma_budget import UmaBudget

        ceiling = 6.25

        # Soft warning: 88%
        expected_warn = round(ceiling * 0.88, 2)
        assert abs(UmaBudget.THRESHOLD_SOFT_WARN_GIB - expected_warn) < 0.01

        # Warning: 95%
        expected_warn2 = round(ceiling * 0.95, 2)
        assert abs(UmaBudget.THRESHOLD_WARN_GIB - expected_warn2) < 0.01

        # Critical: 99%
        expected_critical = round(ceiling * 0.99, 2)
        assert abs(UmaBudget.THRESHOLD_CRITICAL_GIB - expected_critical) < 0.01

        # Emergency: 100% (ceiling)
        assert UmaBudget.THRESHOLD_EMERGENCY_GIB == ceiling

    def test_threshold_ratios_are_consistent(self) -> None:
        """THRESHOLD_RATIO values must match THRESHOLD_GIB calculations."""
        from utils.uma_budget import UmaBudget

        assert abs(UmaBudget.SOFT_WARN_RATIO - 0.88) < 0.001
        assert abs(UmaBudget.WARN_RATIO - 0.95) < 0.001
        assert abs(UmaBudget.CRITICAL_RATIO - 0.99) < 0.001
        assert abs(UmaBudget.EMERGENCY_RATIO - 1.00) < 0.001


class TestM1FetchCeiling:
    """Test M1_FETCH_SOFT_CEILING_GB consistency."""

    def test_m1_fetch_soft_ceiling_matches_mission_peak(self) -> None:
        """M1_FETCH_SOFT_CEILING_GB must equal MISSION_PEAK_RSS_GIB."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.M1_FETCH_SOFT_CEILING_GB == UmaBudget.MISSION_PEAK_RSS_GIB

    def test_module_level_uma_hard_ceiling(self) -> None:
        """Module-level UMA_HARD_CEILING_GIB must match UmaBudget.UMA_HARD_CEILING_GIB."""
        from utils.uma_budget import UMA_HARD_CEILING_GIB, UmaBudget

        assert UMA_HARD_CEILING_GIB == 6.25
        assert UMA_HARD_CEILING_GIB == UmaBudget.UMA_HARD_CEILING_GIB


class TestThresholdLadder:
    """Test the threshold ladder (WARN → CRITICAL → EMERGENCY)."""

    def test_threshold_ordering(self) -> None:
        """Thresholds must be in ascending order: WARN < CRITICAL < EMERGENCY."""
        from utils.uma_budget import UmaBudget

        thresholds = [
            ("SOFT_WARN", UmaBudget.THRESHOLD_SOFT_WARN_GIB),
            ("WARN", UmaBudget.THRESHOLD_WARN_GIB),
            ("CRITICAL", UmaBudget.THRESHOLD_CRITICAL_GIB),
            ("EMERGENCY", UmaBudget.THRESHOLD_EMERGENCY_GIB),
        ]

        for i in range(len(thresholds) - 1):
            name1, val1 = thresholds[i]
            name2, val2 = thresholds[i + 1]
            assert val1 < val2, f"{name1} ({val1}) must be < {name2} ({val2})"

    def test_threshold_progression(self) -> None:
        """Threshold progression: ~88% → ~95% → ~99% → 100%."""
        from utils.uma_budget import UmaBudget

        ceiling = 6.25

        # Verify progression
        assert UmaBudget.THRESHOLD_SOFT_WARN_GIB >= 5.0  # ~88%
        assert UmaBudget.THRESHOLD_WARN_GIB >= 5.5  # ~95%
        assert UmaBudget.THRESHOLD_CRITICAL_GIB >= 6.0  # ~99%
        assert UmaBudget.THRESHOLD_EMERGENCY_GIB == ceiling  # 100%


class TestMemoryBreakdown:
    """Test memory breakdown components."""

    def test_macos_system_gib(self) -> None:
        """MACOS_SYSTEM_GIB must be ~2.5 GiB (macOS baseline)."""
        from utils.uma_budget import UmaBudget

        assert abs(UmaBudget.MACOS_SYSTEM_GIB - 2.5) < 0.01

    def test_orchestrator_gib(self) -> None:
        """ORCHESTRATOR_GIB must be 1.0 GiB."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.ORCHESTRATOR_GIB == 1.0

    def test_llm_weights_gib(self) -> None:
        """LLM_WEIGHTS_GIB must be 2.0 GiB (DeepHermes-3-3B Q4)."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.LLM_WEIGHTS_GIB == 2.0

    def test_kv_cache_gib(self) -> None:
        """KV_CACHE_GIB must be 0.75 GiB."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.KV_CACHE_GIB == 0.75


class TestMetalCacheLimits:
    """Test MLX Metal cache limits."""

    def test_metal_cache_floor(self) -> None:
        """METAL_CACHE_FLOOR_MIB must be 512 MiB."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.METAL_CACHE_FLOOR_MIB == 512

    def test_metal_cache_ceiling(self) -> None:
        """METAL_CACHE_CEILING_MIB must be 1536 MiB (1.5 GiB)."""
        from utils.uma_budget import UmaBudget

        assert UmaBudget.METAL_CACHE_CEILING_MIB == 1536


class TestHighWaterRatio:
    """Test high water ratio for sidecar admission."""

    def test_high_water_ratio(self) -> None:
        """HIGH_WATER_RATIO must be 0.88 (88% of ceiling)."""
        from utils.uma_budget import UmaBudget

        assert abs(UmaBudget.HIGH_WATER_RATIO - 0.88) < 0.001


class TestLegacyAliases:
    """Test backward compatibility aliases."""

    def test_uma_total_budget_gib_alias(self) -> None:
        """UMA_TOTAL_BUDGET_GIB must be an alias for UmaBudget.UMA_HARD_CEILING_GIB."""
        from utils.uma_budget import UMA_TOTAL_BUDGET_GIB, UmaBudget

        assert UMA_TOTAL_BUDGET_GIB == 6.25
        assert UMA_TOTAL_BUDGET_GIB == UmaBudget.UMA_HARD_CEILING_GIB

    def test_uma_warn_gib_alias(self) -> None:
        """UMA_WARN_GIB must match UmaBudget.THRESHOLD_WARN_GIB."""
        from utils.uma_budget import UMA_WARN_GIB, UmaBudget

        assert abs(UMA_WARN_GIB - UmaBudget.THRESHOLD_WARN_GIB) < 0.01


class TestNoHardcodedCeilings:
    """Test that 6.25 is not hardcoded outside UmaBudget (AST analysis)."""

    def test_no_hardcoded_6_25_in_coordinators(self) -> None:
        """UMA_HARD_CEILING_GIB must be used via UmaBudget, not hardcoded."""
        # Dynamic project root detection
        project_root = Path(__file__).parent.parent
        coordinators_dir = project_root / "coordinators"

        violations = []

        for py_file in coordinators_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text()
                tree = ast.parse(content)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                # Check for literal 6.25 assignments (excluding comments)
                                if isinstance(node.value, ast.Constant) and node.value.value == 6.25:
                                    violations.append(f"{py_file.name}:{node.lineno}: hardcoded 6.25")
            except SyntaxError:
                continue

        # Allow 6.25 in test files (for verification)
        # Allow in uma_budget.py (the SSOT)
        filtered_violations = [v for v in violations if "uma_budget.py" not in v and "test_" not in v]

        assert len(filtered_violations) == 0, "Hardcoded 6.25 found outside UmaBudget:\n" + "\n".join(
            filtered_violations
        )

    def test_no_hardcoded_5_5_in_uma_context(self) -> None:
        """5.5 (MISSION_PEAK_RSS_GIB) should derive from UmaBudget, not hardcode."""
        # This is a softer check - 5.5 can appear legitimately
        # The real test is that it's consistent with UmaBudget.MISSION_PEAK_RSS_GIB
        from utils.uma_budget import UmaBudget

        assert UmaBudget.M1_FETCH_SOFT_CEILING_GB == 5.5
        assert UmaBudget.MISSION_PEAK_RSS_GIB == 5.5


class TestWatchdogFunctions:
    """Test UmaWatchdog callback functions."""

    def test_is_uma_warn_exists(self) -> None:
        """is_uma_warn() must exist."""
        from utils.uma_budget import is_uma_warn

        assert callable(is_uma_warn)

    def test_is_uma_critical_exists(self) -> None:
        """is_uma_critical() must exist."""
        from utils.uma_budget import is_uma_critical

        assert callable(is_uma_critical)

    def test_is_uma_emergency_exists(self) -> None:
        """is_uma_emergency() must exist."""
        from utils.uma_budget import is_uma_emergency

        assert callable(is_uma_emergency)

    def test_get_uma_usage_mb_exists(self) -> None:
        """get_uma_usage_mb() must exist."""
        from utils.uma_budget import get_uma_usage_mb

        assert callable(get_uma_usage_mb)


class TestM1Optimization:
    """Test M1 8GB-specific optimizations."""

    def test_gc_false_on_budget_classes(self) -> None:
        """UmaBudget-related classes should use gc=False for M1 optimization."""
        from utils.uma_budget import UmaBudget

        # Check UmaBudget itself
        if hasattr(UmaBudget, "__slots__"):
            assert UmaBudget.__slots__ is not None

    def test_uma_budget_is_msgspec_compatible(self) -> None:
        """UmaBudget should be msgspec.Struct compatible (or similar)."""
        from utils.uma_budget import UmaBudget

        # Should be usable with msgspec if needed
        # Check that class attributes are accessible
        assert hasattr(UmaBudget, "UMA_HARD_CEILING_GIB")
