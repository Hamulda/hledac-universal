"""F3.3 / Issue #47: Verify benchmark_coordinator archiving (2026-07-05).

benchmark_coordinator moved from _deprecated/ to archive/coordinators_deprecated_2026_07_05/
Superseded by: benchmarks/ scripts (benchmarks/live_measurement_kpi.py, etc.)
"""

import warnings

import pytest
from _core import aclose


class TestF33BenchmarkDeprecation:
    """benchmark_coordinator archived to archive/ on 2026-07-05."""

    def test_import_via_top_level_raises_import_error(self):
        """`from hledac.universal.coordinators.benchmark_coordinator import X` raises ImportError."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(ImportError, match="archived"):
                import hledac.universal.coordinators.benchmark_coordinator as _  # noqa: F401

    def test_shim_in_coordinators(self):
        """The benchmark_coordinator.py shim exists in coordinators/ with ImportError."""
        from pathlib import Path

        shim = Path(__file__).parent.parent / "coordinators" / "benchmark_coordinator.py"
        assert shim.exists(), f"shim missing from coordinators/: {shim}"
        content = shim.read_text()
        assert "raise ImportError" in content, "shim should raise ImportError"

    def test_no_real_module_in_archive(self):
        """The original benchmark_coordinator.py was NEVER in archive (no-op deprecation)."""
        from pathlib import Path

        archive_dir = Path(__file__).parent.parent / "archive" / "coordinators_deprecated_2026_07_05"
        real_module = archive_dir / "benchmark_coordinator.py"
        # benchmark_coordinator never had a real implementation - only a deprecated shim
        if real_module.exists():
            assert real_module.stat().st_size < 1000, "benchmark_coordinator was always a shim"

    def test_deprecated_dir_clean(self):
        """coordinators/_deprecated/ does not exist or is empty (benchmark files never existed)."""
        from pathlib import Path

        dep_dir = Path(__file__).parent.parent / "coordinators" / "_deprecated"
        # _deprecated/ directory was never created for benchmark_coordinator
        if not dep_dir.exists():
            return
        files = [
            f.name for f in dep_dir.iterdir()
            if f.is_file() and f.name not in ("__init__.py", "__pycache__")
        ]
        assert files == [], f"_deprecated/ should be empty, found: {files}"

    def test_top_level_alias_raises(self):
        """`coordinators.benchmark_coordinator` raises ImportError pointing to archive."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(ImportError):
                import hledac.universal.coordinators.benchmark_coordinator as _  # noqa: F401
