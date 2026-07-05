"""F3.3 / Issue #47: Verify benchmark_coordinator archiving (2026-07-05).

benchmark_coordinator moved from _deprecated/ to archive/coordinators_deprecated_2026_07_05/
Superseded by: benchmarks/ scripts (benchmarks/live_measurement_kpi.py, etc.)
"""

import warnings

import pytest


class TestF33BenchmarkDeprecation:
    """benchmark_coordinator archived to archive/ on 2026-07-05."""

    def test_import_via_top_level_raises_import_error(self):
        """`from hledac.universal.coordinators.benchmark_coordinator import X` raises ImportError."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(ImportError, match="archived"):
                import hledac.universal.coordinators.benchmark_coordinator as _  # noqa: F401

    def test_shim_archived(self):
        """The shim file was moved to archive/coordinators_deprecated_2026_07_05/."""
        from pathlib import Path

        archive_dir = Path(__file__).parent.parent / "archive" / "coordinators_deprecated_2026_07_05"
        shim = archive_dir / "benchmark_coordinator_shim.py"
        assert shim.exists(), f"shim missing from archive: {shim}"
        assert shim.stat().st_size > 1000, "shim too small"

    def test_real_module_archived(self):
        """The original benchmark_coordinator.py was moved to archive/."""
        from pathlib import Path

        archive_dir = Path(__file__).parent.parent / "archive" / "coordinators_deprecated_2026_07_05"
        real_module = archive_dir / "benchmark_coordinator.py"
        assert real_module.exists(), f"real module missing from archive: {real_module}"
        # The original was ~29 KB.
        assert real_module.stat().st_size > 15000, "real module too small — likely stripped"

    def test_deprecated_dir_clean(self):
        """coordinators/_deprecated/ contains only __init__.py (no benchmark files)."""
        from pathlib import Path

        dep_dir = Path(__file__).parent.parent / "coordinators" / "_deprecated"
        files = [
            f.name for f in dep_dir.iterdir()
            if f.is_file() and f.name not in ("__init__.py", "__pycache__")
        ]
        assert files == [], f"_deprecated/ should contain only __init__.py, found: {files}"

    def test_top_level_alias_raises(self):
        """`coordinators.benchmark_coordinator` raises ImportError pointing to archive."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(ImportError):
                import hledac.universal.coordinators.benchmark_coordinator as _  # noqa: F401
